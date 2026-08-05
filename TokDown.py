#!/usr/bin/env bash
""":"
exec "$(dirname "$0")/TokDown_Venv/bin/python3" "$0" "$@"
":"""

"""
TokDown

Downloads TikTok videos, slideshow/photo posts, and full account URLs.
Videos go through yt-dlp (no browser automation needed — yt-dlp handles
TikTok profile URLs as playlists natively). Slideshow/photo posts have no
video stream at all, so yt-dlp can't handle them properly — those are
fetched through Camoufox instead and either saved as raw images + audio,
or stitched into an mp4 slideshow (ffmpeg), per the "Image only" checkbox.

Requirements:
    pip install yt-dlp curl-cffi camoufox[geoip]
    python3 -m camoufox fetch

curl-cffi backs yt-dlp's --impersonate flag, which TikTok's extractor
needs to avoid "attempting impersonation, but no impersonate target is
available" failures.

Also needs ffmpeg on PATH (a binary, not a pip package) to stitch
slideshow images into video — on Windows, e.g. `winget install ffmpeg`.
Fetching/downloading slideshow assets themselves needs no extra tooling
beyond the above: image/audio downloads use Python's stdlib
urllib.request, which works the same on every OS.

Configuration:
    Downloads are sandboxed to a TikToks/ folder next to this script by
    default. Changing the folder via the GUI's Browse button is
    remembered for next launch (saved to TokDown_last_folder.txt); the
    OUTPUT_FOLDER env var overrides both if set.

    A TokDown_Profile/ folder is also created next to this script — a
    persistent Camoufox browser profile used for slideshow fetching. This
    matters: a fresh, randomized browser fingerprint on every launch reads
    as bot traffic to TikTok and gets captcha-walled, even on posts never
    touched before; a persistent profile keeps one consistent identity
    across runs instead. Don't commit this folder to version control —
    it holds real browser profile data (add it to .gitignore).

    Every error/warning, and any crash, is written to
    TokDown_error_log.txt next to this script (reset fresh each launch)
    — attach that file as-is to a GitHub issue when reporting a problem.

    On Windows, TokDown always re-launches itself under pythonw.exe, so no
    console/terminal window ever opens alongside the GUI — if TokDown ever
    fails to start with no error visible, check TokDown_error_log.txt
    rather than expecting console output.
"""

import datetime
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import traceback
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

# On Unix, the bash-polyglot header above already re-execs into
# TokDown_Venv's own python before any of this runs. Windows has no
# shebang-line execution, so if this got started under some other
# interpreter — double-clicked, or run as `python TokDown.py` with
# whatever Python is on PATH — do the equivalent here instead, before
# any third-party imports below (which would otherwise either fail or,
# worse, silently succeed against the wrong versions of things — see
# curl-cffi's version pinning below for why that's a real problem here).
#
# On win32 this ALSO re-execs into pythonw.exe specifically, not
# python.exe, even if already running inside TokDown_Venv — regardless of
# how this got started (a plain python.org install, the Microsoft Store's
# python.exe app-execution-alias hosted in Windows Terminal, a .bat
# double-click, ...), python.exe is a console-subsystem binary and always
# has *some* console window attached, however it got there. Hiding that
# window after the fact (an earlier approach) proved unreliable — with
# Windows Terminal as the host it came back minimized instead of hidden.
# pythonw.exe is a different binary, linked /SUBSYSTEM:WINDOWS, so
# Windows never allocates it a console in the first place — nothing to
# hide or fail to hide. Every normal Python install (Store or python.org)
# ships both exes side by side, so this needs no extra setup.
_script_dir = Path(__file__).resolve().parent
_venv_dir = _script_dir / "TokDown_Venv"
if sys.platform == "win32":
    _target_python = _venv_dir / "Scripts" / "pythonw.exe"
    _already_there = Path(sys.executable).resolve() == _target_python.resolve()
else:
    _target_python = _venv_dir / "bin" / "python3"
    _already_there = Path(sys.prefix).resolve() == _venv_dir.resolve()
if not _already_there:
    if not _target_python.exists():
        print(f"TokDown_Venv not found at {_target_python}")
        print("Run setup.sh (Unix) or setup.bat (Windows) first.")
        sys.exit(1)
    os.execv(str(_target_python), [str(_target_python), str(_script_dir / "TokDown.py"), *sys.argv[1:]])

try:
    from camoufox.sync_api import Camoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    Camoufox = None
    CAMOUFOX_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent

# Remembers the last folder chosen via the GUI's Browse button, so it
# doesn't reset back to the default every time TokDown is relaunched.
LAST_FOLDER_FILE = SCRIPT_DIR / ".TokDown_last_folder.txt"


def _load_last_output_folder():
    try:
        saved = LAST_FOLDER_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return Path(saved)
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _save_last_output_folder(path):
    try:
        LAST_FOLDER_FILE.write_text(str(path), encoding="utf-8")
    except OSError:
        pass


# Precedence: OUTPUT_FOLDER env var > last folder remembered from a
# previous run > the default "TikToks" folder next to this script.
BASE_OUTPUT_FOLDER = Path(
    os.environ.get("OUTPUT_FOLDER")
    or _load_last_output_folder()
    or (SCRIPT_DIR / "TikToks")
)
_ORIGINAL_OUTPUT_FOLDER = BASE_OUTPUT_FOLDER
BASE_OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)
CAMOUFOX_PROFILE = SCRIPT_DIR / "TokDown_Profile"

# yt-dlp's console-script exe lives inside TokDown_Venv (that's where it
# was pip-installed), but re-exec'ing into the venv's python above does
# NOT "activate" it — PATH still points wherever the OS/shell had it
# pointed before TokDown ever ran. shutil.which("yt-dlp") therefore often
# can't see it and falls back to a bare "yt-dlp" string, which Windows'
# subprocess.Popen cannot resolve on its own (WinError 2: cannot find the
# file specified). Look inside the venv first — we know exactly where it
# is — and only fall back to a PATH search / bare name if that's missing
# (e.g. someone runs this outside of TokDown_Venv entirely).
_venv_ytdlp = _venv_dir / ("Scripts/yt-dlp.exe" if sys.platform == "win32" else "bin/yt-dlp")
YTDLP_EXE = str(_venv_ytdlp) if _venv_ytdlp.exists() else (shutil.which("yt-dlp") or "yt-dlp")
IMPERSONATE_TARGET = "chrome"

# Now that this whole app runs under pythonw.exe on Windows (see the
# re-exec block above), there's no console for child processes to share —
# without this flag, every yt-dlp/ffmpeg/ffprobe call would pop its own
# brand-new, briefly-visible console window. subprocess.CREATE_NO_WINDOW
# only exists on Windows; elsewhere this is just 0 (no-op flag).
SUBPROCESS_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Every error/warning the app logs, plus any actual crash (unhandled
# exception — main thread, a background thread, or a Tk GUI callback),
# gets written here. Reset fresh each launch so a bug report is just this
# one session, not old runs mixed in — meant to be attached as-is to a
# GitHub issue.
ERROR_LOG_FILE = SCRIPT_DIR / "TokDown_error_log.txt"


def _write_error_log(text):
    """Never raises — logging a problem must not be able to cause a new one."""
    try:
        with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def _start_error_log():
    try:
        with open(ERROR_LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"TokDown error log — {datetime.datetime.now().isoformat(timespec='seconds')}\n")
            f.write(f"Python {sys.version.split()[0]} on {sys.platform}\n")
            f.write("=" * 70 + "\n")
    except OSError:
        pass


def _install_crash_logging():
    """Catches what the app's own try/except blocks don't: a crash in a
    background thread, or in main() before the GUI even exists, would
    otherwise just print to a console window that most users double-
    clicking this script will never see."""
    def handle_exception(exc_type, exc_value, exc_tb):
        _write_error_log(
            "[UNHANDLED — main thread]\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def handle_thread_exception(args):
        _write_error_log(
            f"[UNHANDLED — thread {args.thread.name}]\n"
            + "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback))
        )
        threading.__excepthook__(args)

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception

# ============================================================================
# DEPENDENCY CHECKS
# ============================================================================
def check_curl_cffi(log_func):
    """curl_cffi is what actually backs --impersonate. Without it yt-dlp
    can't fulfil the impersonation request at all."""
    try:
        import curl_cffi  # noqa: F401
        return True
    except ImportError:
        log_func("❌ Missing 'curl-cffi' — required for --impersonate.", "error")
        log_func("   Install with: pip install curl-cffi", "error")
        return False


def check_camoufox(log_func):
    """Camoufox (stealth Firefox) is what fetches real image URLs for
    slideshow/photo posts — yt-dlp's TikTok extractor doesn't parse those
    at all, so without this, slideshow posts just download as audio-only."""
    if not CAMOUFOX_AVAILABLE:
        log_func("❌ Missing 'camoufox' — required for slideshow/photo post support.", "error")
        log_func("   Install with: pip install camoufox[geoip]", "error")
        log_func("   Then run:     python3 -m camoufox fetch", "error")
        return False
    return True


def check_ffmpeg(log_func):
    """ffmpeg is a binary, not a pip package — stitching slideshow images
    into an mp4 needs it on PATH."""
    if not shutil.which("ffmpeg"):
        log_func("❌ Missing 'ffmpeg' — required to stitch slideshow images into video.", "error")
        log_func("   Install it and make sure it's on PATH (e.g. apt/brew install ffmpeg,", "error")
        log_func("   or on Windows: winget install ffmpeg).", "error")
        return False
    return True

# ============================================================================
# URL HELPERS
# ============================================================================
def _is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url

# ============================================================================
# OUTPUT FILTERING (yt-dlp stdout parsing)
# ============================================================================
_SUPPRESS_PREFIXES = (
    "[info]",
    "[debug]",
    "[Cookies]",
    "[TLSFingerprint]",
    "[Metadata]",
    "[thumbnail]",
)

# Progress line: [download] 45.3% of 1.23GiB at 2.34MiB/s ETA 00:45
_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+[\d.~]+\S*\s+at\s+([\d.]+)(KiB/s|MiB/s|GiB/s)\s+ETA\s+(\S+)"
)
# Batch item counter: [download] Downloading item 3 of 47
_BATCH_RE = re.compile(r"\[download\] Downloading item (\d+) of (\d+)")
# Destination line: [download] Destination: Some Video Title.mp4
_DEST_RE = re.compile(r"\[download\] Destination:\s+(.+)")
# Per-item extraction line: [TikTok] Extracting URL: https://www.tiktok.com/@x/video/123
# (playlist-level "Extracting URL" lines for the account itself don't contain
# "/video/" and are deliberately not matched as a retryable item below)
_EXTRACT_URL_RE = re.compile(r"Extracting URL:\s+(\S+)")


def _should_log(line: str) -> bool:
    """Return True if this yt-dlp output line should appear in the log
    area. Progress % lines never reach this — _run_ytdlp_batch's main
    loop already intercepts and drops them before falling through here."""
    for prefix in _SUPPRESS_PREFIXES:
        if line.startswith(prefix):
            low = line.lower()
            return "error" in low or "warning" in low
    return True

# ============================================================================
# DOWNLOAD ENGINE
# ============================================================================
# Retry tuning: each retry pass raises the sleep-interval window, since the
# "Unexpected response from webpage request" errors look like TikTok
# probabilistically rate-limiting/challenging individual requests rather
# than anything wrong with the request itself — spacing requests out
# further is the only real lever available.
MAX_RETRIES = 2
BASE_SLEEP_MIN, BASE_SLEEP_MAX = 3, 9
RETRY_SLEEP_STEP = 5

# ── Slideshow/photo post handling ──────────────────────────────────────
# yt-dlp can't reliably tell a slideshow post from a video (see
# download_tiktok()'s docstring), so account/profile URLs are pre-checked
# via TikTok's own item_list endpoint (through Camoufox) to split them
# into real video_urls / slideshow_urls before yt-dlp ever runs.
_PHOTO_PERMALINK_RE = re.compile(r"tiktok\.com/@([^/]+)/photo/(\d+)")
_VIDEO_PERMALINK_RE = re.compile(r"tiktok\.com/@([^/]+)/video/(\d+)")
# Safety cap on how many item_list pages (via scroll) to fetch per account
# during the pre-check — generous, but not unbounded; if hit, a warning is
# logged rather than silently missing posts.
ACCOUNT_PRECHECK_MAX_PAGES = 50

# Each slideshow image is shown this long, sliding into the next over
# SLIDESHOW_XFADE_DURATION seconds (ffmpeg xfade filter).
SLIDESHOW_IMAGE_DURATION = 5.0
SLIDESHOW_XFADE_DURATION = 0.5
SLIDESHOW_XFADE_TRANSITION = "slideleft"
SLIDESHOW_FPS = 30
CAMOUFOX_ITEM_DETAIL_TIMEOUT_MS = 30000


def _run_ytdlp_batch(
    urls,
    log,
    effective_out,
    sleep_min,
    sleep_max,
    stop_event=None,
    process_started_callback=None,
    write_thumbnails=False,
):
    """
    Runs a single yt-dlp pass over `urls`. Returns (returncode, failed_urls,
    stopped) where failed_urls is the list of individual video URLs yt-dlp
    logged an ERROR for (not the whole batch — --ignore-errors keeps it
    going), so the caller can retry just those specific links.
    """
    batch_file = BASE_OUTPUT_FOLDER / "queue_tiktok.txt"
    with open(batch_file, "w") as f:
        for url in urls:
            f.write(url + "\n")

    cmd = [
        YTDLP_EXE,
        "--batch-file", str(batch_file),
        "--output", str(effective_out / "%(uploader)s/%(title)s.%(ext)s"),
        "--no-overwrites",       # skip files that already exist
        "--continue",            # resume partially downloaded files
        "--ignore-errors",       # one bad URL in a batch shouldn't kill the rest
        "--impersonate", IMPERSONATE_TARGET,
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--retries", "10",
        "--fragment-retries", "10",
        "--sleep-interval", str(sleep_min),
        "--max-sleep-interval", str(sleep_max),
        "--progress",
        "--newline",             # one progress update per line (parseable)
    ]
    if write_thumbnails:
        # Only used for unclassified account/profile URLs that might
        # contain slideshow posts (see download_tiktok()'s docstring) —
        # yt-dlp can't extract a slideshow's actual video, so its
        # thumbnail is the only visual it can salvage. Confirmed video
        # URLs never set this; nobody wants a stray thumbnail file next
        # to a video they already have.
        cmd.append("--write-all-thumbnails")

    process = None
    failed_urls = []
    stopped = False
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )
        if process_started_callback:
            process_started_callback(process)

        current_file = ""
        current_item_url = ""
        for line in process.stdout:
            line_s = line.rstrip()
            if not line_s:
                continue

            # ── Stop requested ───────────────────────────────────────
            if stop_event and stop_event.is_set():
                process.kill()
                log("⏹ Download stopped by user.", "warning")
                stopped = True
                break

            # ── Which item is currently being extracted ──────────────
            # Only track per-video URLs (not the account/playlist-level
            # "Extracting URL" line), so failures map to a single link.
            m = _EXTRACT_URL_RE.search(line_s)
            if m and "/video/" in m.group(1):
                current_item_url = m.group(1)
                # fall through — still worth logging this line below

            # ── Batch item counter (log once per video) ─────────────
            m = _BATCH_RE.search(line_s)
            if m:
                n, total = m.group(1), m.group(2)
                log(f" 📦 Item {n} of {total}")
                continue

            # ── Progress line — too noisy for the log, drop it ──────
            if _PROGRESS_RE.search(line_s):
                continue

            # ── New file destination ─────────────────────────────────
            m = _DEST_RE.search(line_s)
            if m:
                current_file = Path(m.group(1).strip()).name
                log(f" ⬇️ {current_file}")
                continue

            # ── Flag the specific failure mode we're fixing for ─────
            if "no impersonate target is available" in line_s.lower():
                log(" ⚠️ Impersonation target unavailable — is curl-cffi installed?", "warning")

            # ── Per-item error: note the link down for retry ────────
            if line_s.startswith("ERROR:"):
                if current_item_url:
                    failed_urls.append(current_item_url)
                    log(f" ❌ Failed: {current_item_url}", "error")
                log(" " + line_s)
                continue

            # ── Everything else: log it if it isn't suppressed ──────
            if _should_log(line_s):
                log(" " + line_s)

        if not stopped:
            process.wait()
    except Exception as e:
        log(f" ❌ Subprocess error: {e}", "error")
    finally:
        if process_started_callback:
            process_started_callback(None)  # clear the stored reference
        if batch_file.exists():
            os.remove(batch_file)

    returncode = process.returncode if process is not None else 1
    # de-dupe while preserving order, in case a URL errors more than once
    failed_urls = list(dict.fromkeys(failed_urls))
    return returncode, failed_urls, stopped


def download_tiktok(
    urls,
    log_callback,
    stop_event=None,
    process_started_callback=None,
    output_dir=None,
    write_thumbnails=False,
):

    if not urls:
        return

    def log(msg, level="info"):
        log_callback(msg, level)

    log(f"▶️ Processing {len(urls)} TikTok URL(s)...")

    effective_out = Path(output_dir) if output_dir else BASE_OUTPUT_FOLDER
    effective_out.mkdir(parents=True, exist_ok=True)

    remaining = list(urls)
    sleep_min, sleep_max = BASE_SLEEP_MIN, BASE_SLEEP_MAX
    attempt = 0
    last_returncode = 0

    while remaining and attempt <= MAX_RETRIES:
        if attempt > 0:
            sleep_min += RETRY_SLEEP_STEP
            sleep_max += RETRY_SLEEP_STEP
            log("")
            log(f"🔁 Retry {attempt}/{MAX_RETRIES} for {len(remaining)} link(s) "
                f"that failed (sleep {sleep_min}-{sleep_max}s)...", "warning")

        log("📥 Starting download..." if attempt == 0 else "📥 Retrying...")
        last_returncode, remaining, stopped = _run_ytdlp_batch(
            remaining, log, effective_out, sleep_min, sleep_max,
            stop_event=stop_event,
            process_started_callback=process_started_callback,
            write_thumbnails=write_thumbnails,
        )
        if stopped:
            return  # "stopped by user" already logged
        attempt += 1

    if remaining:
        log(f"⚠️ {len(remaining)} link(s) still failed after "
            f"{MAX_RETRIES} retr{'y' if MAX_RETRIES == 1 else 'ies'}:", "error")
        for u in remaining:
            log(f"    {u}", "error")

        # Persist the list so it isn't only sitting in the scrollback —
        # useful if you want to retry these later, e.g. once TikTok's
        # rate-limiting has eased off.
        try:
            fail_log = effective_out / "tokdown_failed_links.txt"
            with open(fail_log, "a") as f:
                for u in remaining:
                    f.write(u + "\n")
            log(f" 📝 Failed links saved to {fail_log}", "warning")
        except Exception as e:
            log(f" ⚠️ Couldn't save failed-links file: {e}", "warning")
    else:
        if last_returncode == 0:
            log("✅ Downloads complete!", "success")
        else:
            log(f"⚠️ yt-dlp exited with code {last_returncode}, but no individual "
                f"link errors were caught — worth checking the log above.", "warning")

# ============================================================================
# URL CLASSIFICATION (video vs. slideshow/photo)
# ============================================================================
def _fetch_account_item_types(browser, profile_url, log):
    """
    Navigates to a TikTok profile URL and captures every item_list response
    (TikTok's own post-listing API), paging via scroll until hasMore is
    False or ACCOUNT_PRECHECK_MAX_PAGES is hit. Returns the raw list of
    itemList entries (each has "id", "author", and — if it's a slideshow —
    "imagePost"), or None if the page itself couldn't be reached at all.
    """
    page = browser.new_page()
    items = []
    try:
        with page.expect_response(
            lambda r: "/api/post/item_list/" in r.url, timeout=45000
        ) as resp_info:
            page.goto(profile_url, wait_until="load", timeout=60000)
        body = resp_info.value.json()
        items.extend(body.get("itemList", []))
        has_more = body.get("hasMore", False)

        pages_fetched = 1
        while has_more and pages_fetched < ACCOUNT_PRECHECK_MAX_PAGES:
            try:
                with page.expect_response(
                    lambda r: "/api/post/item_list/" in r.url, timeout=20000
                ) as resp_info:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                body = resp_info.value.json()
            except Exception:
                break  # no more item_list calls fired — treat as end of list
            items.extend(body.get("itemList", []))
            has_more = body.get("hasMore", False)
            pages_fetched += 1

        if has_more:
            log(f" ⚠️ {profile_url}: stopped after {len(items)} post(s) "
                f"({ACCOUNT_PRECHECK_MAX_PAGES} pages) — account has more; "
                f"some slideshow posts may be missed.", "warning")
        return items
    except Exception as e:
        log(f" ⚠️ Couldn't scan {profile_url} for slideshow posts: {e}", "warning")
        return None
    finally:
        page.close()


def classify_tiktok_urls(urls, log, stop_event=None):
    """
    Splits TikTok URLs into (video_urls, slideshow_urls, unclassified_urls).
    Direct /video/<id> or /photo/<id> pastes are classified by URL shape
    alone — no browser needed. Bare account/profile URLs are expanded via
    a Camoufox pre-check against TikTok's own item_list endpoint, since
    yt-dlp itself can't reliably tell a slideshow from a video on its own.

    unclassified_urls holds account/profile URLs that couldn't be pre-
    checked at all (camoufox missing, or the pre-check itself failed) —
    they still go to yt-dlp like video_urls, but since they might secretly
    contain slideshow posts, the caller should ask yt-dlp to also write
    thumbnails for them as a fallback. video_urls never needs that: every
    URL in it is a confirmed real video.
    """
    video_urls = []
    slideshow_urls = []
    unclassified_urls = []
    account_urls = []

    for u in urls:
        if _PHOTO_PERMALINK_RE.search(u):
            slideshow_urls.append(u)
        elif _VIDEO_PERMALINK_RE.search(u):
            video_urls.append(u)
        else:
            account_urls.append(u)

    if account_urls:
        if not check_camoufox(log):
            log(f" ⚠️ Skipping slideshow pre-check for {len(account_urls)} account "
                f"URL(s) — their posts will go through yt-dlp as normal, and any "
                f"slideshow posts among them will download as audio-only.", "warning")
            unclassified_urls.extend(account_urls)
        else:
            with Camoufox(
                headless=True, persistent_context=True,
                user_data_dir=str(CAMOUFOX_PROFILE),
            ) as browser:
                for acc_url in account_urls:
                    if stop_event and stop_event.is_set():
                        break
                    log(f" 🔎 Checking {acc_url} for slideshow posts...")
                    items = _fetch_account_item_types(browser, acc_url, log)
                    if items is None:
                        log(f" ⚠️ Falling back to yt-dlp for {acc_url} "
                            f"(pre-check failed)", "warning")
                        unclassified_urls.append(acc_url)
                        continue

                    n_slideshow = 0
                    for it in items:
                        item_id = it.get("id")
                        author = (it.get("author") or {}).get("uniqueId")
                        if not item_id or not author:
                            continue
                        if it.get("imagePost"):
                            slideshow_urls.append(f"https://www.tiktok.com/@{author}/photo/{item_id}")
                            n_slideshow += 1
                        else:
                            video_urls.append(f"https://www.tiktok.com/@{author}/video/{item_id}")
                    log(f" ✅ {acc_url}: {len(items)} post(s) found, "
                        f"{n_slideshow} slideshow, {len(items) - n_slideshow} video")
                browser.close()

    video_urls = list(dict.fromkeys(video_urls))
    slideshow_urls = list(dict.fromkeys(slideshow_urls))
    unclassified_urls = list(dict.fromkeys(unclassified_urls))
    return video_urls, slideshow_urls, unclassified_urls


def process_tiktok_urls(
    urls,
    log_callback,
    stop_event=None,
    process_started_callback=None,
    output_dir=None,
    image_only=False,
):
    """
    Top-level entry point: classifies `urls` into videos vs. slideshows,
    runs the normal yt-dlp batch for videos, then the Camoufox-based
    slideshow pipeline for photo posts.
    """
    if not urls:
        return

    def log(msg, level="info"):
        log_callback(msg, level)

    effective_out = Path(output_dir) if output_dir else BASE_OUTPUT_FOLDER
    effective_out.mkdir(parents=True, exist_ok=True)

    video_urls, slideshow_urls, unclassified_urls = classify_tiktok_urls(
        urls, log, stop_event=stop_event
    )

    if stop_event and stop_event.is_set():
        return

    if video_urls:
        download_tiktok(
            video_urls, log_callback,
            stop_event=stop_event,
            process_started_callback=process_started_callback,
            output_dir=output_dir,
        )

    if stop_event and stop_event.is_set():
        return

    if unclassified_urls:
        log("")
        log(f"📥 Downloading {len(unclassified_urls)} account/profile URL(s) that "
            f"couldn't be pre-checked (thumbnails included, in case any of their "
            f"posts turn out to be slideshows)...")
        download_tiktok(
            unclassified_urls, log_callback,
            stop_event=stop_event,
            process_started_callback=process_started_callback,
            output_dir=output_dir,
            write_thumbnails=True,
        )

    if stop_event and stop_event.is_set():
        return

    if slideshow_urls:
        log("")
        log(f"🖼️ Found {len(slideshow_urls)} slideshow/photo post(s) — "
            f"fetching real images via Camoufox...")
        _process_slideshow_urls(
            slideshow_urls, effective_out, image_only, log,
            stop_event=stop_event,
        )

# ============================================================================
# SLIDESHOW / PHOTO POST PIPELINE
# ============================================================================
# Plain HTTP headers used for downloading images/audio — TikTok's CDN URLs
# come back from item/detail already pre-signed (x-expires/x-signature in
# the query string), so a normal GET with a real-looking User-Agent and
# Referer is all that's needed. No curl_cffi/impersonation required here,
# and no OS-specific tooling — urllib.request is stdlib, same on Windows.
_ASSET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.tiktok.com/",
}


def _fetch_slideshow_item(browser, url, log):
    """
    Navigates to a slideshow's own /photo/<id> permalink and intercepts
    TikTok's /api/item/detail/ response. That request is signed
    (X-Bogus/X-Gnarly/msToken) by TikTok's own client-side JS — the real
    browser session generates it automatically, no manual signing needed.
    Returns {"id", "images": [urls], "audio_url": str|None} or None on any
    failure (never raises — one bad item must not abort the whole batch).
    """
    page = browser.new_page()
    try:
        with page.expect_response(
            lambda r: "/api/item/detail/" in r.url,
            timeout=CAMOUFOX_ITEM_DETAIL_TIMEOUT_MS,
        ) as resp_info:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        body = resp_info.value.json()

        if body.get("statusCode") != 0:
            log(f" ⚠️ {url}: item/detail returned statusCode "
                f"{body.get('statusCode')}", "warning")
            return None

        item = body.get("itemInfo", {}).get("itemStruct", {})
        raw_images = item.get("imagePost", {}).get("images", [])
        image_urls = [
            img["imageURL"]["urlList"][0]
            for img in raw_images
            if img.get("imageURL", {}).get("urlList")
        ]
        if not image_urls:
            log(f" ⚠️ {url}: no images found in imagePost", "warning")
            return None

        return {
            "id": item.get("id"),
            "images": image_urls,
            "audio_url": (item.get("music") or {}).get("playUrl") or None,
        }
    except Exception as e:
        log(f" ⚠️ Failed to fetch slideshow data for {url}: {e}", "warning")
        return None
    finally:
        page.close()


def _download_slideshow_assets(item, dest_dir, log):
    """
    Downloads every image + the background audio track (if any) to
    dest_dir via a plain HTTP GET. A bad individual image/audio URL is
    skipped (logged), not fatal to the whole item — returns whatever
    succeeded. Returns (list[Path] images, Path|None audio).
    """
    def _get(url, dest):
        req = urllib.request.Request(url, headers=_ASSET_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())

    images = []
    for i, url in enumerate(item["images"]):
        dest = dest_dir / f"img_{i:02d}.jpg"
        try:
            _get(url, dest)
            images.append(dest)
        except Exception as e:
            log(f" ⚠️ Image {i} failed to download: {e}", "warning")

    audio = None
    if item.get("audio_url"):
        dest = dest_dir / "audio.mp3"
        try:
            _get(item["audio_url"], dest)
            audio = dest
        except Exception as e:
            log(f" ⚠️ Audio track failed to download: {e}", "warning")

    return images, audio


def _probe_image_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, check=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    ).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def _build_slideshow_video(image_paths, audio_path, out_path, log):
    """
    Stitches images into an mp4: each shown for SLIDESHOW_IMAGE_DURATION
    seconds, sliding into the next over SLIDESHOW_XFADE_DURATION seconds
    (ffmpeg xfade filter), background audio muxed underneath (looped if
    shorter than the video, trimmed if longer — via -stream_loop -1 +
    -shortest, so both directions of the length mismatch are handled with
    no branching). Canvas size = the max width/height across this post's
    own images, so mixed dimensions within one post are handled safely.
    Returns True on success; out_path is left unwritten on failure.
    """
    image_paths = [Path(p) for p in image_paths]
    n = len(image_paths)
    if n == 0:
        log(" ⚠️ No images to stitch.", "warning")
        return False

    try:
        sizes = [_probe_image_size(p) for p in image_paths]
    except Exception as e:
        log(f" ⚠️ ffprobe failed to read image dimensions: {e}", "warning")
        return False
    W = max(s[0] for s in sizes)
    H = max(s[1] for s in sizes)
    W += W % 2  # yuv420p needs even dimensions
    H += H % 2

    D = SLIDESHOW_IMAGE_DURATION
    X = SLIDESHOW_XFADE_DURATION

    cmd = ["ffmpeg", "-y"]
    for i, p in enumerate(image_paths):
        dur = D if (i == n - 1 or n == 1) else (D + X)
        cmd += ["-loop", "1", "-t", str(dur), "-i", str(p)]

    audio_input_idx = None
    if audio_path:
        audio_input_idx = n
        cmd += ["-stream_loop", "-1", "-i", str(audio_path)]

    filter_parts = [
        f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format=yuv420p,fps={SLIDESHOW_FPS}[v{i}]"
        for i in range(n)
    ]

    if n == 1:
        final_label = "v0"
    else:
        prev = "v0"
        for k in range(1, n):
            out_label = f"vx{k}"
            filter_parts.append(
                f"[{prev}][v{k}]xfade=transition={SLIDESHOW_XFADE_TRANSITION}:"
                f"duration={X}:offset={k * D}[{out_label}]"
            )
            prev = out_label
        final_label = prev

    cmd += ["-filter_complex", ";".join(filter_parts), "-map", f"[{final_label}]"]
    if audio_input_idx is not None:
        cmd += ["-map", f"{audio_input_idx}:a", "-shortest"]

    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_input_idx is not None:
        cmd += ["-c:a", "aac"]
    cmd += [str(out_path)]

    result = subprocess.run(
        cmd, capture_output=True, text=True, creationflags=SUBPROCESS_CREATIONFLAGS
    )
    if result.returncode != 0:
        log(" ⚠️ ffmpeg failed to build slideshow video:", "warning")
        log(" " + result.stderr[-1500:], "warning")
        return False
    return True


def _persist_skipped_slideshows(urls, effective_out, log):
    """Mirrors download_tiktok()'s tokdown_failed_links.txt pattern, so
    slideshow posts that couldn't be fetched aren't only sitting in
    scrollback."""
    try:
        skip_log = effective_out / "tokdown_skipped_slideshows.txt"
        with open(skip_log, "a") as f:
            for u in urls:
                f.write(u + "\n")
        log(f" 📝 Skipped slideshow links saved to {skip_log}", "warning")
    except Exception as e:
        log(f" ⚠️ Couldn't save skipped-slideshows file: {e}", "warning")


def _persist_raw_assets(images, audio, dest_dir, log):
    """Copies already-downloaded images/audio (e.g. out of a temp dir
    that's about to be cleaned up) into a permanent folder — used when
    stitching can't happen or fails, so the fetch isn't wasted."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in [*images, *([audio] if audio else [])]:
        try:
            shutil.copy(f, dest_dir / f.name)
        except Exception as e:
            log(f" ⚠️ Couldn't copy {f.name} to {dest_dir}: {e}", "warning")


def _process_slideshow_urls(slideshow_urls, effective_out, image_only, log, stop_event=None):
    """
    Orchestrates the full slideshow pipeline: one Camoufox browser for the
    whole run (not one launch per item), fetch -> download -> stitch (or
    just save raw, if image_only).

    image_only=True: raw images + audio ARE the deliverable, saved
    straight to the output folder permanently.
    image_only=False: raw images + audio are only build material for the
    mp4 — downloaded to a temp dir, used to stitch, then discarded once
    the video exists. They're only copied into the output folder if
    stitching can't happen (ffmpeg missing) or fails, so a failed fetch
    is never silently lost.
    """
    if not slideshow_urls:
        return

    effective_out = Path(effective_out)
    effective_out.mkdir(parents=True, exist_ok=True)

    if not check_camoufox(log):
        log(f" ⚠️ Skipping {len(slideshow_urls)} slideshow post(s) — "
            f"camoufox not installed.", "warning")
        _persist_skipped_slideshows(slideshow_urls, effective_out, log)
        return

    ffmpeg_ok = image_only or check_ffmpeg(log)
    if not image_only and not ffmpeg_ok:
        log(" ⚠️ ffmpeg not installed — slideshow posts will be saved as "
            "raw images+audio only (no video).", "warning")

    skipped = []
    with Camoufox(
        headless=True, persistent_context=True,
        user_data_dir=str(CAMOUFOX_PROFILE),
    ) as browser:
        for url in slideshow_urls:
            if stop_event and stop_event.is_set():
                break

            m = _PHOTO_PERMALINK_RE.search(url)
            if not m:
                log(f" ⚠️ Couldn't parse author/id from {url}, skipping.", "warning")
                skipped.append(url)
                continue
            author, item_id = m.group(1), m.group(2)

            dest_dir = effective_out / author / f"{item_id}_slideshow"
            final_mp4 = effective_out / author / f"{item_id}_slideshow.mp4"
            if final_mp4.exists():
                log(f" ⏭️ {item_id}: already have {final_mp4.name}, skipping.")
                continue
            if image_only and dest_dir.exists() and any(dest_dir.iterdir()):
                log(f" ⏭️ {item_id}: already have raw images, skipping.")
                continue

            log(f" 🖼️ Fetching slideshow {item_id}...")
            item = _fetch_slideshow_item(browser, url, log)
            if item is None:
                skipped.append(url)
                continue

            if image_only:
                dest_dir.mkdir(parents=True, exist_ok=True)
                images, audio = _download_slideshow_assets(item, dest_dir, log)
                if not images:
                    log(f" ⚠️ {item_id}: no images downloaded, skipping.", "warning")
                    skipped.append(url)
                    continue
                if len(images) < len(item["images"]):
                    log(f" ⚠️ {item_id}: only {len(images)}/{len(item['images'])} "
                        f"images downloaded — continuing with the partial set.", "warning")
                log(f" ✅ {item_id}: saved {len(images)} image(s)"
                    f"{' + audio' if audio else ''} to {dest_dir}", "success")
                continue

            # Stitching mode: images/audio are only intermediate build
            # material — download to a temp dir that cleans itself up.
            with tempfile.TemporaryDirectory(prefix=f"tokdown_{item_id}_") as tmp:
                images, audio = _download_slideshow_assets(item, Path(tmp), log)
                if not images:
                    log(f" ⚠️ {item_id}: no images downloaded, skipping.", "warning")
                    skipped.append(url)
                    continue
                if len(images) < len(item["images"]):
                    log(f" ⚠️ {item_id}: only {len(images)}/{len(item['images'])} "
                        f"images downloaded — continuing with the partial set.", "warning")

                if not ffmpeg_ok:
                    _persist_raw_assets(images, audio, dest_dir, log)
                    log(f" ⚠️ {item_id}: raw images+audio saved to {dest_dir} "
                        f"(ffmpeg unavailable, no video built).", "warning")
                    continue

                final_mp4.parent.mkdir(parents=True, exist_ok=True)
                ok = _build_slideshow_video(images, audio, final_mp4, log)
                if ok:
                    log(f" ✅ {item_id}: slideshow video saved to {final_mp4}", "success")
                else:
                    _persist_raw_assets(images, audio, dest_dir, log)
                    log(f" ⚠️ {item_id}: stitching failed — raw assets saved "
                        f"to {dest_dir} for troubleshooting.", "warning")
        browser.close()

    if skipped:
        log(f" ⚠️ {len(skipped)} slideshow post(s) could not be fetched:", "warning")
        for u in skipped:
            log(f"    {u}", "warning")
        _persist_skipped_slideshows(skipped, effective_out, log)

# ============================================================================
# GUI
# ============================================================================
_LOG = "__log__"


class TokDownGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("TokDown")
        self.root.geometry("650x560")
        self.root.configure(bg="#1e1e1e")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.log_queue = queue.Queue()
        self.output_folder = BASE_OUTPUT_FOLDER
        self.image_only_var = tk.BooleanVar(value=False)

        self._current_process = None
        self._stop_requested = threading.Event()

        self.setup_ui()
        self.root.after(100, self.process_log_queue)
        threading.Thread(target=self._startup_tasks, daemon=True).start()

    # ------------------------------------------------------------------
    # Placeholder helpers
    # ------------------------------------------------------------------
    def _on_focus_in(self, event):
        current = self.url_text.get("1.0", "end-1c")
        if current == self.placeholder:
            self.url_text.delete("1.0", tk.END)
            self.url_text.tag_remove("placeholder", "1.0", "end")

    def _on_focus_out(self, event):
        current = self.url_text.get("1.0", "end-1c").strip()
        if not current:
            self.url_text.insert("1.0", self.placeholder)
            self.url_text.tag_add("placeholder", "1.0", "end")

    def _insert_pasted_text(self, text):
        """Inserts pasted text at the cursor and guarantees a trailing
        newline, so the next URL can be pasted immediately without
        pressing Enter first."""
        if not text:
            return
        self.url_text.insert("insert", text)
        if not text.endswith("\n"):
            self.url_text.insert("insert", "\n")
        self.url_text.see("insert")

    def _on_paste(self, event):
        """Overrides Ctrl+V paste entirely rather than reacting after Tk's
        default handling — reacting-after was unreliable (the default
        paste hadn't actually inserted anything yet by the time an
        after_idle callback ran to check). Grabs the CLIPBOARD selection
        directly and returns "break" to suppress Tk's own paste handling,
        so the text isn't inserted twice."""
        try:
            clip = self.url_text.clipboard_get()
        except tk.TclError:
            return "break"
        self._insert_pasted_text(clip)
        return "break"

    def _on_middle_click_paste(self, event):
        """Middle-click paste (X11) uses the PRIMARY selection, not the
        clipboard — a separate retrieval call from Ctrl+V's."""
        try:
            text = self.url_text.selection_get()
        except tk.TclError:
            return "break"
        self._insert_pasted_text(text)
        return "break"

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------
    def setup_ui(self):
        self.placeholder = "Paste TikTok video, photo post, or profile URLs here, one per line"

        tk.Label(self.root, text="Enter URLs (one per line):",
                 bg="#1e1e1e", fg="#cccccc", font=("Arial", 10, "bold")).pack(pady=(10, 0))

        self.url_text = scrolledtext.ScrolledText(
            self.root, height=8, width=70,
            bg="#252526", fg="#ffffff", insertbackground="white",
            highlightthickness=0, borderwidth=1,
            font=("Consolas", 10)
        )
        self.url_text.pack(fill=tk.X, padx=10, pady=5)

        self.url_text.insert("1.0", self.placeholder)
        self.url_text.tag_configure("placeholder", foreground="#666666")
        self.url_text.tag_add("placeholder", "1.0", "end")
        self.url_text.bind("<FocusIn>", self._on_focus_in)
        self.url_text.bind("<FocusOut>", self._on_focus_out)
        for seq in ("<<Paste>>", "<Control-v>", "<Control-V>"):
            self.url_text.bind(seq, self._on_paste)
        self.url_text.bind("<Button-2>", self._on_middle_click_paste)

        # ── 1. Destination (Where the downloads go) ──────────────────
        dest_frame = tk.Frame(self.root, bg="#1e1e1e")
        dest_frame.pack(pady=(5, 5)) # Automatically centers

        self.browse_btn = tk.Button(
            dest_frame, text="Browse...",
            bg="#444", fg="white", relief=tk.FLAT, cursor="hand2",
            command=self.browse_folder, padx=8, pady=2
        )
        self.browse_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.folder_label = tk.Label(
            dest_frame, text=f"📁 Output: {self.output_folder}",
            bg="#1e1e1e", fg="#aaaaaa", font=("Arial", 11)
        )
        self.folder_label.pack(side=tk.LEFT)

        # ── 2. Options (Image only decision) ─────────────────────────
        options_frame = tk.Frame(self.root, bg="#1e1e1e")
        options_frame.pack(pady=(0, 10)) # Automatically centers

        self.image_only_chk = tk.Checkbutton(
            options_frame, text="🖼️ Image(s) Only?",
            variable=self.image_only_var,
            bg="#1e1e1e", fg="#cccccc", activebackground="#1e1e1e",
            activeforeground="#cccccc", selectcolor="#333333",
            highlightthickness=0,
            font=("Arial", 12)
        )
        self.image_only_chk.pack()

        # ── 3. Action (Download / Stop) ──────────────────────────────
        actions_frame = tk.Frame(self.root, bg="#1e1e1e")
        actions_frame.pack(pady=(0, 10)) # Automatically centers

        btn_style = {
            "bg": "#333333", "fg": "white",
            "activebackground": "#555555", "activeforeground": "white",
            "relief": tk.FLAT, "padx": 15, "pady": 6, "cursor": "hand2",
        }

        self.download_btn = tk.Button(actions_frame, text="⏬ Download",
                                       command=self.start_download, **btn_style)
        self.download_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = tk.Button(
            actions_frame, text="⏹ Stop",
            command=self.stop_download,
            bg="#5a1a1a", fg="white",
            activebackground="#7a2a2a", activeforeground="white",
            relief=tk.FLAT, padx=15, pady=6, cursor="hand2",
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT)

        # ── Log area ──────────────────────────────────────────────────
        tk.Label(self.root, text="Download Log:", bg="#1e1e1e", fg="#cccccc",
                 font=("Arial", 10, "bold")).pack(pady=(8, 0))

        self.log_area = scrolledtext.ScrolledText(
            self.root, height=12, width=70, state=tk.DISABLED,
            bg="#1e1e1e", fg="#cccccc", highlightthickness=0, borderwidth=1,
            font=("Consolas", 9)
        )
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        self.log_area.tag_config("info", foreground="#569cd6")
        self.log_area.tag_config("warning", foreground="#d7ba7d")
        self.log_area.tag_config("error", foreground="#f44747")
        self.log_area.tag_config("success", foreground="#6a9955")

    # ------------------------------------------------------------------
    # Logging (thread-safe via queue)
    # ------------------------------------------------------------------
    def log(self, message, level="info"):
        self.log_queue.put((_LOG, message, level))
        if level in ("error", "warning"):
            _write_error_log(f"[{level.upper()}] {message}")

    def process_log_queue(self):
        while not self.log_queue.empty():
            _, message, level = self.log_queue.get()
            self.log_area.configure(state=tk.NORMAL)
            self.log_area.insert(tk.END, message + "\n", level)
            self.log_area.see(tk.END)
            self.log_area.configure(state=tk.DISABLED)

        self.root.after(100, self.process_log_queue)

    def clear_status(self):
        self.log_area.configure(state=tk.NORMAL)
        self.log_area.delete("1.0", tk.END)
        self.log_area.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Startup tasks
    # ------------------------------------------------------------------
    def _startup_tasks(self):
        self.log("🚀 TokDown ready!", "success")
        ok = check_curl_cffi(self.log)
        if not ok:
            self.log("   Downloads will likely fail without curl-cffi.", "warning")

    # ------------------------------------------------------------------
    # Browse folder
    # ------------------------------------------------------------------
    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder", initialdir=str(self.output_folder))
        if folder:
            self.output_folder = Path(folder)
            self.folder_label.config(text=f"📁 Output: {self.output_folder}")
            global BASE_OUTPUT_FOLDER
            BASE_OUTPUT_FOLDER = self.output_folder
            _save_last_output_folder(self.output_folder)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def start_download(self):
        raw = self.url_text.get("1.0", tk.END).strip()
        if raw == self.placeholder:
            raw = ""
        if not raw:
            messagebox.showwarning("No URLs", "Please enter at least one URL.")
            return

        urls = [u.strip() for u in raw.splitlines() if u.strip() and not u.startswith("#")]
        self.clear_status()
        self._stop_requested.clear()
        self.download_btn.configure(state=tk.DISABLED, text="⏳ Downloading...")
        self.stop_btn.configure(state=tk.NORMAL, text="⏹ Stop")
        self.image_only_chk.configure(state=tk.DISABLED)
        threading.Thread(target=self._process_downloads, args=(urls,), daemon=True).start()

    def stop_download(self):
        """Request the running download to stop."""
        self._stop_requested.set()
        proc = self._current_process
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self.stop_btn.configure(state=tk.DISABLED, text="⏹ Stopping…")
        self.log("⏹ Stop requested…", "warning")

    def _set_process(self, proc):
        """Store (or clear) the active Popen so stop_download can kill it."""
        self._current_process = proc

    def _process_downloads(self, urls):
        try:
            tiktok = [u for u in urls if _is_tiktok_url(u)]
            other = [u for u in urls if u not in tiktok]

            self.log(f"📋 Found {len(urls)} URL(s):")
            self.log(f" 🎵 TikTok : {len(tiktok)}")
            if other:
                self.log(f" ⚠️ Skipped (not a tiktok.com URL): {len(other)}", "warning")
                for o in other:
                    self.log(f"    {o}", "warning")
            self.log("")

            if not tiktok:
                self.log("⚠️ No TikTok URLs found!", "warning")
                return

            using_default = (self.output_folder == _ORIGINAL_OUTPUT_FOLDER)

            dl_kwargs = dict(
                stop_event=self._stop_requested,
                process_started_callback=self._set_process,
            )

            self.log("=" * 60)
            self.log("🎵 TikTok Downloads", "info")
            if using_default:
                self.log(f" 📁 Saving to: {self.output_folder} (subfoldered by account)")
            self.log("=" * 60)
            process_tiktok_urls(
                tiktok, self.log, output_dir=self.output_folder,
                image_only=self.image_only_var.get(), **dl_kwargs,
            )

            if not self._stop_requested.is_set():
                self.log("\n" + "=" * 60)
                self.log("✅ All downloads complete!", "success")
                self.log("=" * 60)

        except Exception as e:
            self.log(f"\n❌ Error: {e}", "error")
            self.log(traceback.format_exc(), "error")
        finally:
            self.root.after(0, self._reset_download_ui)

    def _reset_download_ui(self):
        """Re-enable buttons — always runs on the main thread."""
        self.download_btn.configure(state=tk.NORMAL, text="⏬ Download")
        self.stop_btn.configure(state=tk.DISABLED, text="⏹ Stop")
        self.image_only_chk.configure(state=tk.NORMAL)

    def on_closing(self):
        self.log("Closing TokDown...", "warning")
        self.root.destroy()
        os._exit(0)


def main():
    _start_error_log()
    _install_crash_logging()

    root = tk.Tk()

    def report_callback_exception(exc_type, exc_value, exc_tb):
        # Tkinter routes exceptions raised inside GUI callbacks (button
        # commands, etc.) here instead of sys.excepthook — separate hook
        # needed to catch those too.
        _write_error_log(
            "[UNHANDLED — Tk callback]\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        traceback.print_exception(exc_type, exc_value, exc_tb)

    root.report_callback_exception = report_callback_exception

    TokDownGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

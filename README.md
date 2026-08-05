# TokDown

Download TikTok videos, photo/slideshow posts, and whole accounts — with real slideshow-to-Video support.

TikTok slideshow posts (the ones made of photos instead of a video) don't
work with normal downloaders, since TikTok doesn't actually store them as a
video file at all. TokDown handles those properly: it fetches the real
images and audio, then stitches them into an MP4 for you automatically —
or, if you'd rather have the raw images and audio track separately, one
checkbox switches to that instead.

<img width="780" height="718" alt="TokDown" src="https://github.com/user-attachments/assets/d8f17642-6a56-44b5-8c99-27a0a4689f84" />

## Features

- **Videos** — paste a single video link, or a whole profile/account URL
  and every video on it gets queued up automatically.
- **Photo/slideshow posts** — fetched properly (not just downloaded as an
  audio file, which is what most tools do) and stitched into a slideshow
  video with the original background audio.
- **"Image(s) Only?" mode** — skip the video stitching and just save the
  raw images + audio track instead.
- **Remembers your last download folder** between launches.
- **Keeps a log of everything**, plus a separate error log you can attach
  to a bug report if something goes wrong.

## Requirements

- **Python 3.10+**
- **ffmpeg** — only needed for the default "stitch into a video" mode.
  Without it, slideshow posts still work, they just save as raw images +
  audio instead of a video.
- Everything else (yt-dlp, the browser TokDown uses for slideshow posts,
  etc.) gets installed automatically by the setup script below — you don't
  need to install these yourself.

## Setup

### Windows

1. **Install Python**, if you don't already have it:
   [python.org/downloads](https://www.python.org/downloads/)

   > **Important:** on the first screen of the installer, tick the box
   > that says **"Add python.exe to PATH"** before clicking Install. It's
   > easy to miss and it's off by default on some versions — if you skip
   > it, the setup script below won't be able to find Python.
   >
   > (If you already installed Python without checking that box, you
   > don't need to reinstall — just re-run the installer, choose
   > "Modify", and enable it from there.)

2. **Download this project** (green "Code" button → "Download ZIP" if
   you're not using git) and unzip it somewhere.

3. **Double-click `setup.bat`.** A black terminal window opens and does
   everything for you — creates a private Python environment for TokDown,
   installs what it needs, and downloads the browser component used for
   slideshow posts. This takes a few minutes and needs an internet
   connection. When it says "Setup complete!", you're done — press any
   key to close the window.

4. **Install ffmpeg** (optional, but recommended — see Requirements
   above):
   ```
   winget install ffmpeg
   ```
   Run that from a terminal (PowerShell or Command Prompt). If you don't
   have `winget`, it comes with modern Windows by default — search "App
   Installer" in the Start menu and update it if the command isn't found.

   > After installing, **close and reopen** any terminal windows (or just
   > restart your PC) before running TokDown — Windows won't recognize
   > the new ffmpeg install in a window that was already open.

5. **Run TokDown** by double-clicking `TokDown.py`. If double-clicking it
   doesn't open the app (some Windows setups don't have `.py` files
   associated with Python), open a terminal in the TokDown folder instead
   and run:
   ```
   TokDown_Venv\Scripts\python.exe TokDown.py
   ```

### macOS / Linux

1. Make sure Python 3 is installed (macOS and most Linux distros already
   have it — check with `python3 --version` in a terminal).

2. Download or clone this project, open a terminal in that folder, and
   run:
   ```
   bash setup.sh
   ```
   This creates a private Python environment for TokDown, installs what
   it needs, and downloads the browser component used for slideshow
   posts. Takes a few minutes.

3. Install ffmpeg (optional, but recommended — see Requirements above):
   ```
   # macOS
   brew install ffmpeg

   # Debian/Ubuntu
   sudo apt install ffmpeg

   # Fedora
   sudo dnf install ffmpeg

   # Arch
   sudo pacman -S ffmpeg
   ```

4. Run TokDown:
   ```
   ./TokDown.py
   ```

## How to use it

1. **Paste links** into the box at the top — one per line. You can mix:
   - a single video link
   - a single photo/slideshow post link
   - a full profile/account link (every post on that account gets queued)

   Tip: paste one link, and the cursor automatically drops to a new
   blank line — you can just paste the next one right away without
   pressing Enter in between.

2. **Choose where downloads go** with the **Output** field and **Browse**
   button. Defaults to a `TikToks` folder next to the app, and whatever
   you pick is remembered for next time.

3. **"Image(s) Only?"** — leave this **unchecked** (the default) to get
   slideshow posts as a proper stitched MP4 video. Check it if you'd
   rather have the raw images and audio track saved separately instead,
   with no video created.

4. Click **Download**. Progress and status show up in the **Download
   Log** at the bottom as it goes. **Stop** cancels the current run.

Everything downloaded is organized into a subfolder per account. In the
default stitching mode, a slideshow post's finished video is all you'll
see there — the raw images used to build it are cleaned up automatically
once it's done. With "Image(s) Only?" checked (or if ffmpeg isn't
installed), you'll instead find a subfolder with the individual images
and audio track.

## Troubleshooting

**"Missing 'curl-cffi'" / "Missing 'camoufox'" / "Missing 'ffmpeg'"**
Setup didn't finish, or ffmpeg wasn't installed separately (see Setup
above). Re-run `setup.bat` / `setup.sh` — it's safe to run again, it
won't redo work that's already done.

**A specific post keeps failing, or slideshow posts won't fetch at all**
TikTok occasionally shows a CAPTCHA challenge to automated tools if
they're used very heavily in a short time on the same account — this is
TikTok's own anti-bot system, not a bug. TokDown already keeps a
consistent browser identity between runs specifically to avoid this, so
it should be rare; if it happens, waiting a while before retrying that
account usually resolves it. Posts that couldn't be fetched are listed in
`tokdown_failed_links.txt` / `tokdown_skipped_slideshows.txt` in your
output folder so you can retry just those later.

**Windows: `setup.bat` says it can't find Python**
See the "Add python.exe to PATH" note in the Windows setup steps above —
that's almost always the cause.

**Windows: installed ffmpeg but TokDown still says it's missing**
Close and reopen the terminal / restart TokDown (or your PC) — see the
ffmpeg note in the Windows setup steps above.

**Something else went wrong / reporting a bug**
Check `TokDown_error_log.txt`, created next to the app and reset fresh
every time you launch it. It captures every error TokDown hits, plus the
full details of any crash. If you're filing a GitHub issue, attach this
file as-is — it's the fastest way to get a problem diagnosed without back
and forth.

## Notes

- `TokDown_Venv/` and `TokDown_Profile/` are created automatically and
  shouldn't be committed if you fork this — they're local environment and
  browser-profile data, not part of the app itself.
- `TokDown_Profile/` in particular holds real browser session data used
  to avoid the CAPTCHA issue mentioned above — don't share it.

#!/usr/bin/env bash
#
# setup.sh — TokDown setup
# ---------------------------------------------------------------
# Creates the venv, installs dependencies, downloads Camoufox,
# checks for ffmpeg, and makes the script executable.
#
# Usage:
#   bash setup.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="TokDown_Venv"

echo "=========================================="
echo " TokDown Setup"
echo "=========================================="

# 1. Create venv
if [ -d "$VENV_DIR" ]; then
    echo "[1/5] Venv '$VENV_DIR' already exists — skipping creation."
else
    echo "[1/5] Creating virtual environment in '$VENV_DIR'..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# 2. Install Python dependencies
#
# curl-cffi is pinned BELOW 0.16 on purpose: yt-dlp only supports specific
# curl_cffi releases at a time (it lagged behind 0.15.x for months after
# release), and 0.16.0 is confirmed broken with the yt-dlp version below —
# `yt-dlp --list-impersonate-targets` shows every target as "(unavailable)"
# with curl_cffi 0.16.0, which silently breaks TikTok downloads entirely
# (IMPERSONATE_TARGET is used on every request, not just slideshow posts).
# If you bump this, first confirm `--list-impersonate-targets` actually
# lists real targets (Chrome/Safari/etc, not just "(unavailable)" rows).
echo "[2/5] Installing dependencies (yt-dlp, curl-cffi, camoufox)..."
python -m pip install --upgrade pip
python -m pip install yt-dlp "curl-cffi<0.16" "camoufox[geoip]"

# 3. Download Camoufox browser
echo "[3/5] Downloading Camoufox browser..."
python -m camoufox fetch

deactivate

# 4. Check for ffmpeg (a system binary, not something pip can install —
# needed to stitch slideshow images into video; without it, slideshow
# posts still work, just saved as raw images+audio instead)
echo "[4/5] Checking for ffmpeg..."
if command -v ffmpeg >/dev/null 2>&1; then
    echo "      ✅ ffmpeg found: $(command -v ffmpeg)"
else
    echo "      ⚠️  ffmpeg not found on PATH."
    echo "      Slideshow posts will only save as raw images+audio until"
    echo "      it's installed. Install it with your package manager, e.g.:"
    echo "        Debian/Ubuntu: sudo apt install ffmpeg"
    echo "        Fedora:        sudo dnf install ffmpeg"
    echo "        Arch:          sudo pacman -S ffmpeg"
    echo "        macOS:         brew install ffmpeg"
    echo "        Windows:       winget install ffmpeg"
fi

# 5. Make the script executable
echo "[5/5] Making TokDown.py executable..."
chmod +x TokDown.py

echo "=========================================="
echo " ✅ Setup complete!"
echo "=========================================="
echo "Run TokDown with:"
echo "    ./TokDown.py"

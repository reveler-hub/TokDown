@echo off
REM setup.bat - TokDown setup for Windows
REM ---------------------------------------------------------------
REM Creates the venv, installs dependencies, downloads Camoufox,
REM and checks for ffmpeg. Windows equivalent of setup.sh.
REM
REM Usage:
REM   setup.bat

cd /d "%~dp0"
set VENV_DIR=TokDown_Venv

echo ==========================================
echo  TokDown Setup
echo ==========================================

REM 0. Find a Python to create the venv with. The official installer's
REM "Add python.exe to PATH" checkbox is easy to miss (off by default on
REM many versions) - if that happened, "python" won't be found here, but
REM the "py" launcher usually still is, since it registers itself
REM separately from that checkbox.
where python >nul 2>nul
if %errorlevel% == 0 (
    set PY_LAUNCHER=python
) else (
    where py >nul 2>nul
    if %errorlevel% == 0 (
        set PY_LAUNCHER=py -3
    ) else (
        echo Neither "python" nor "py" was found on PATH.
        echo Install Python from https://python.org - on the first
        echo installer screen, check "Add python.exe to PATH" before
        echo clicking Install.
        pause
        exit /b 1
    )
)

REM 1. Create venv
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/4] Venv "%VENV_DIR%" already exists - skipping creation.
) else (
    echo [1/4] Creating virtual environment in "%VENV_DIR%"...
    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create venv.
        pause
        exit /b 1
    )
)

REM 2. Install Python dependencies
REM curl-cffi is pinned BELOW 0.16 on purpose: yt-dlp only supports
REM specific curl_cffi releases at a time, and 0.16.0 is confirmed broken
REM with the yt-dlp version below - "yt-dlp --list-impersonate-targets"
REM shows every target as "(unavailable)" with curl_cffi 0.16.0, which
REM silently breaks TikTok downloads entirely (IMPERSONATE_TARGET is used
REM on every request, not just slideshow posts). If you bump this, first
REM confirm --list-impersonate-targets actually lists real targets
REM (Chrome/Safari/etc, not just "(unavailable)" rows).
echo [2/4] Installing dependencies (yt-dlp, curl-cffi, camoufox)...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV_DIR%\Scripts\python.exe" -m pip install yt-dlp "curl-cffi<0.16" "camoufox[geoip]"

REM 3. Download Camoufox browser
echo [3/4] Downloading Camoufox browser...
"%VENV_DIR%\Scripts\python.exe" -m camoufox fetch

REM 4. Check for ffmpeg (a system binary, not something pip can install -
REM needed to stitch slideshow images into video; without it, slideshow
REM posts still work, just saved as raw images+audio instead)
echo [4/4] Checking for ffmpeg...
where ffmpeg >nul 2>nul
if %errorlevel% == 0 (
    echo       ffmpeg found.
) else (
    echo       WARNING: ffmpeg not found on PATH.
    echo       Slideshow posts will only save as raw images+audio until
    echo       it's installed. Install it with:
    echo           winget install ffmpeg
    echo       Then close and reopen this terminal (or reboot) before
    echo       running TokDown - PATH changes don't apply to windows
    echo       that were already open when you installed it.
)

echo ==========================================
echo  Setup complete!
echo ==========================================
echo Run TokDown by double-clicking:
echo     TokDown.py
pause

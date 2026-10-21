@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
set "existingInstall=C:\LANStream-Enhanced"
if not exist "%existingInstall%\config.txt" goto missing
if not exist "%existingInstall%\ffmpeg.exe" goto missing
if not exist "%existingInstall%\ffprobe.exe" goto missing
set "PATH=%existingInstall%;%PATH%"
set "LANSTREAM_CONFIG_FILE=%existingInstall%\config.txt"
set "LANSTREAM_CACHE_DIR=%~dp0diagnostic-cache"
set "LANSTREAM_PORT=8061"
set "LANSTREAM_HOST=0.0.0.0"
set "LANSTREAM_TRANSCODER=qsv"
echo Close the installed Enhanced app before continuing. Port 8060 is not used.
echo This build uses QSV only and a separate cache. Configuration is read in place.
echo No process will be stopped automatically.
echo.
LANStream.exe
set "diagnosticResult=%errorlevel%"
echo.
pause
exit /b %diagnosticResult%
:missing
echo Existing config.txt, ffmpeg.exe and ffprobe.exe are required in C:\LANStream-Enhanced.
echo Extract this diagnostic build into a separate folder. Nothing was changed.
pause
exit /b 1

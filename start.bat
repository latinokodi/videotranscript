@echo off
REM ===========================================================================
REM  videotranscript - launcher (desktop GUI)
REM
REM  Double-click this file to start the app. It:
REM    1. checks that uv is installed,
REM    2. builds the isolated .venv on first run (one-off download),
REM    3. opens the desktop app.
REM
REM  You can also drag a video file ONTO this .bat to queue it at startup.
REM
REM  Structure note: flat labels + `goto` rather than nested parenthesised
REM  blocks, because cmd.exe loses the exit code of an `exit /b` inside a
REM  nested block.
REM ===========================================================================

setlocal
title videotranscript - local GPU transcription
cd /d "%~dp0"

if not exist "gui.py" goto :no_app
if not exist "vtgui\__init__.py" goto :no_app
if not exist "vtcore\__init__.py" goto :no_app

where uv >nul 2>nul
if errorlevel 1 goto :no_uv

REM --- First run: create the environment, otherwise go straight to the app ---
if exist ".venv\Scripts\python.exe" goto :launch

echo.
echo   First run - building the Python environment.
echo   This downloads Python, Qt and the CUDA runtime wheels once
echo   ^(roughly 2 GB^), so it can take a few minutes.
echo.
REM --no-dev: the published app needs only its runtime dependencies. The dev
REM group (pytest, ruff, vulture) belongs to the maintainer's checkout, not to
REM every machine that runs the app.
uv sync --no-dev
if errorlevel 1 goto :sync_failed
echo.
echo   Environment ready.
echo.

:launch
echo   Starting videotranscript...
echo.
REM Launch the app detached from this console, using the venv interpreter
REM directly instead of `uv run`. Two reasons:
REM   * Windows does not cascade kills, so `uv run pythonw gui.py` left uv.exe
REM     plus two pythonw.exe processes alive after this console died, holding
REM     several GB of VRAM with no window to close;
REM   * detached, there is nothing to strand. The ways out are closing the app
REM     window (which unloads the model) or ending the process, and the driver
REM     reclaims VRAM when the process dies.
start "videotranscript" ".venv\Scripts\pythonw.exe" gui.py %*

REM pythonw has no console, so a crash at startup would otherwise be silent.
timeout /t 5 /nobreak >nul
tasklist /fi "imagename eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
if errorlevel 1 goto :app_died

echo   Running. Close the app window to quit; this console can be closed.
endlocal & exit /b 0


REM --- Error paths ----------------------------------------------------------

:app_died
echo.
echo   [!] The app stopped immediately instead of opening a window.
echo       Run this in a terminal to see the actual error:
echo.
echo           uv run python gui.py
echo.
pause
endlocal & exit /b 1

:no_app
echo.
echo   [!] The app files were not found next to this launcher.
echo       Expected gui.py, vtgui\ and vtcore\ in the same folder.
echo       Keep start.bat inside the videotranscript folder.
echo.
pause
endlocal & exit /b 1

:no_uv
echo.
echo   [!] uv was not found on PATH.
echo.
echo   Install it once ^(no admin needed^), any of these works:
echo.
echo       pip install uv
echo       winget install --id=astral-sh.uv -e
echo.
echo   More options: https://docs.astral.sh/uv/getting-started/installation/
echo.
echo   Then close this window and run the launcher again.
echo.
pause
endlocal & exit /b 1

:sync_failed
echo.
echo   [!] "uv sync" failed. The reason is printed above.
echo       Check your internet connection and try again.
echo.
pause
endlocal & exit /b 1

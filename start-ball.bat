@echo off
REM ============================================================
REM  HiveWeave Desktop Ball Startup Script (spec 10 P0)
REM  Usage: start-ball.bat
REM  Single process: main thread pywebview ball + child uvicorn :4000
REM  (launcher.py D9). Add --headless to run backend only.
REM
REM  Reads HIVEWEAVE_OPENCODE_API_KEY from apps/hiveweave-py/.env
REM  Ball position memory + assistant workspace live under the data root
REM  (frozen EXE: <exe>/data, script mode: apps/hiveweave-py/data).
REM ============================================================

echo [HiveWeave] Starting desktop ball (single process: ball + backend)...

REM Kill any stale process on port 4000 (same discipline as start-backend.bat)
echo [HiveWeave] Killing stale backend on port 4000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":4000 " ^| findstr "LISTENING"') do (
    echo   killing PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

cd /d "%~dp0apps\hiveweave-py"

set PYTHONUNBUFFERED=1

REM .venv python directly (same as start-backend.bat, avoids venv activate).
REM -u + PYTHONUNBUFFERED: unbuffered stdio for the tee'd log.
.venv\Scripts\python.exe -u "%~dp0apps\desktop\launcher.py" %*

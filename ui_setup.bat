@echo off
cd /d "%~dp0"
echo ==== 1. Install uiautomation (reads text from the AI chat window) ====
.venv\Scripts\python.exe -m pip install uiautomation
echo.
echo ==== 2. Probe: make sure the AI chat window is visible, it reads in 3 seconds ====
timeout /t 3 >nul
.venv\Scripts\python.exe main.py --probe-reply
echo.
echo Screenshot this window for Claude, then run stop.bat and start_hidden.bat.
pause

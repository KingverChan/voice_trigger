@echo off
wscript "%~dp0start_hidden.vbs"
echo voice-trigger started in background. Log: voice-trigger.log
timeout /t 3 >nul

@echo off
rem 诊断 DeepSeek Harness 聊天输入框 (UIA) —— 双击运行, 结果写到同目录 uia_dump.txt
chcp 65001 >nul
cd /d "%~dp0"
echo 正在扫描 DeepSeek Harness 窗口的无障碍树...
".venv\Scripts\python.exe" "tools\dump_dsh_uia.py"
echo.
echo 结果已写到 tools\uia_dump.txt
pause

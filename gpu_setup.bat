@echo off
cd /d "%~dp0"
echo ==== 1. Check NVIDIA GPU and driver ====
nvidia-smi
if errorlevel 1 (
  echo.
  echo No NVIDIA GPU or driver found. Keep using CPU. Send a screenshot to Claude.
  pause
  exit /b 1
)
echo.
echo ==== 2. Install cuBLAS / cuDNN for the GPU, about 1GB ====
.venv\Scripts\python.exe -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
echo.
echo ==== 3. Test Whisper on the GPU ====
.venv\Scripts\python.exe main.py --gpu-check
echo.
echo ==== 4. Speed test: small / medium on CPU and GPU ====
.venv\Scripts\python.exe main.py --bench
echo.
echo Done. Screenshot the results for Claude, then run stop.bat and start_hidden.bat.
pause

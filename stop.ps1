# 停掉后台运行的 voice-trigger
$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*.exe' -and $_.CommandLine -like '*voice-trigger*main.py*' }
if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host "已停止 voice-trigger" } else { Write-Host "voice-trigger 没在运行" }

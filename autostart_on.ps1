# 开机自启: 在"启动"文件夹里放一个快捷方式, 登录后后台运行 start_hidden.vbs
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'voice-trigger.lnk'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = "$env:WINDIR\System32\wscript.exe"
$s.Arguments = '"' + (Join-Path $dir 'start_hidden.vbs') + '"'
$s.WorkingDirectory = $dir
$s.Save()
Write-Host "已设置开机自启: $lnk"
Write-Host "取消自启: 双击 autostart_off.bat"

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'voice-trigger.lnk'
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "已取消开机自启" } else { Write-Host "本来就没有设置开机自启" }

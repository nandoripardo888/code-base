@echo off

powershell -NoProfile -Command ^
"Get-CimInstance Win32_Process -Filter \"Name = 'tunnel-client.exe'\" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

@echo off
powershell -NoProfile -Command "Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""%~dp0add-firewall-rules.ps1""' -Verb RunAs"

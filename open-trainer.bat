@echo off
rem Opens the zone trainer (desktop-only teach mode, frontend/trainer.html) in its own
rem maximized window. The dev servers must already be running (start-dev.bat).
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" (
  start "" "%CHROME%" --app=http://localhost:5174/trainer.html --start-maximized
) else (
  start "" http://localhost:5174/trainer.html
)

@echo off
rem Opens the TradingView-style chart panel (desktop only, frontend/tv.html) in its own
rem maximized window. The dev servers must already be running (start-dev.bat).
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%CHROME%" (
  start "" "%CHROME%" --app=http://localhost:5174/tv.html --start-maximized
) else (
  start "" http://localhost:5174/tv.html
)

@echo off
cd /d "%~dp0"

echo Starting backend (FastAPI) on http://192.168.1.173:8001 ...
start "Backend - FastAPI" cmd /k call "%~dp0backend\start-backend.bat"

echo Starting frontend (Vite) on http://192.168.1.173:5174 ...
start "Frontend - Vite" cmd /k call "%~dp0frontend\start-frontend.bat"

echo.
echo Both servers are starting in separate windows.
echo Close those windows (or press Ctrl+C inside them) to stop the servers.
echo.
echo Opening the app in your browser in a few seconds...
timeout /t 6 /nobreak >nul
start "" http://localhost:5174
echo.
echo On your phone (same WiFi), open: http://192.168.1.173:5174
echo.
echo Press any key to close this window (the two server windows will keep running).
pause >nul

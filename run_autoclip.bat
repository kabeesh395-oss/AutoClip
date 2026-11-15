@echo off
title AutoClip AI Studio
cd /d "%~dp0"
cls
echo ======================================================================
echo    AutoClip AI - Autonomous Video Production Studio
echo ======================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at .venv\Scripts\python.exe
    echo Please install dependencies first.
    pause
    exit /b 1
)

echo Detecting local network configuration...
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    set LOCAL_IP=%%a
    goto :found_ip
)
:found_ip
set LOCAL_IP=%LOCAL_IP: =%
if "%LOCAL_IP%"=="" set LOCAL_IP=127.0.0.1

echo.
echo ======================================================================
echo   [ENGINE READY]
echo   - Local PC Web Studio:  http://localhost:8765
echo   - Phone Mobile Access:  http://%LOCAL_IP%:8765
echo   - Download Android APK: http://%LOCAL_IP%:8765/api/download-apk
echo ======================================================================
echo.
echo Opening AutoClip Studio in standalone app mode...
if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app=http://localhost:8765 --window-size=1300,880
) else if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
    start "" "C:\Program Files\Microsoft\Edge\Application\msedge.exe" --app=http://localhost:8765 --window-size=1300,880
) else (
    start http://localhost:8765
)
echo.
echo Starting AutoClip backend engine on 0.0.0.0:8765 ...
.venv\Scripts\python.exe -m uvicorn autoclip_app.app:app --host 0.0.0.0 --port 8765
pause

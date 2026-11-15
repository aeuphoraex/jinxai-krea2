@echo off
REM Restart Caddy with the dubwarz.vip repoint.
REM
REM Double-click is enough: the script re-launches itself elevated via UAC. The Caddy that
REM holds :80/:443/:2019 runs in session 0 as SYSTEM, so an unelevated taskkill is refused
REM ("Access is denied") and the old process keeps the ports — which looks like a successful
REM start with the same PID. Every step below is checked so that cannot pass silently.
setlocal EnableDelayedExpansion

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set CADDY=F:\MODAL\tools\caddy\caddy.exe
set CONFIG=F:\MODAL\tools\caddy\run_config.json
set LOG=F:\MODAL\tools\caddy

echo [1/4] Stopping any running Caddy...
taskkill /F /IM caddy.exe
timeout /t 2 /nobreak >nul
tasklist /FI "IMAGENAME eq caddy.exe" 2>nul | findstr /I "caddy.exe" >nul
if not errorlevel 1 (
    echo   ERROR: caddy.exe is still running, so :80/:443 cannot be freed.
    echo   Close it from Task Manager or reboot, then run this again.
    pause
    exit /b 1
)
echo   stopped.

echo [2/4] Checking :443 is free...
netstat -ano | findstr ":443" | findstr LISTENING >nul
if not errorlevel 1 (
    echo   ERROR: something still listens on :443:
    netstat -ano | findstr ":443" | findstr LISTENING
    pause
    exit /b 1
)
echo   free.

echo [3/4] Starting %CADDY%
if not exist "%CADDY%" (
    echo   ERROR: %CADDY% not found.
    pause
    exit /b 1
)
start "" /MIN "%CADDY%" run --config "%CONFIG%" >> "%LOG%\stdout.log" 2>> "%LOG%\stderr.log"
timeout /t 6 /nobreak >nul

echo [4/4] Verify
netstat -ano | findstr LISTENING | findstr ":443"
netstat -ano | findstr LISTENING | findstr ":2019"
powershell -NoProfile -Command "try { 'admin API: ' + (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 'http://127.0.0.1:2019/config/').StatusCode } catch { 'admin API: UNREACHABLE' }"
powershell -NoProfile -Command "try { 'https://dubwarz.vip: ' + (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 'https://dubwarz.vip/').StatusCode } catch { 'https://dubwarz.vip: ' + $_.Exception.Message }"

echo.
echo Note: this process runs in your session, so it will not come back after a reboot
echo until Caddy is registered as a service or a startup task.
pause

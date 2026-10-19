@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title tradebot
if not exist "storage\logs" mkdir "storage\logs"
rem restart backoff: 15 s -> 60 s -> 300 s; resets to 15 s after a run that lived >= 10 minutes (600 s)
set DELAY=15
:loop
call :now T0
python -m tradebot.app
set RC=%errorlevel%
call :now T1
set /a UP=T1-T0
>>"storage\logs\run.log" echo %date% %time% exitcode=%RC% uptime=%UP%s
rem exit codes: 0 = normal stop, 3 = another instance is running, 4 = config error
if "%RC%"=="0" goto end
if "%RC%"=="3" goto end
if "%RC%"=="4" goto end
if %UP% GEQ 600 set DELAY=15
echo [run.bat] crashed with code %RC% (uptime %UP% s), restarting in %DELAY% s...
timeout /t %DELAY% /nobreak >nul
if %DELAY% LSS 60 goto d60
set DELAY=300
goto loop
:d60
set DELAY=60
goto loop
:end
pause
exit /b %RC%
:now
set "%~1="
for /f "delims=" %%t in ('powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"') do set "%~1=%%t"
if not defined %~1 set "%~1=0"
exit /b 0

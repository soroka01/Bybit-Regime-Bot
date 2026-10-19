@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
title tradebot
:loop
python -m tradebot.app
set RC=%errorlevel%
rem exit codes: 0 = normal stop, 3 = another instance is running, 4 = config error
if "%RC%"=="0" goto end
if "%RC%"=="3" goto end
if "%RC%"=="4" goto end
echo [run.bat] crashed with code %RC%, restarting in 15 s...
timeout /t 15 /nobreak >nul
goto loop
:end
pause

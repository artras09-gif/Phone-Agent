@echo off
chcp 866 >nul
cd /d "%~dp0"
title PhoneAgent
setlocal enabledelayedexpansion

if not defined PY set "PY=python"
if not defined ADB_PATH if exist "%~dp0..\tools\platform-tools\adb.exe" set "ADB_PATH=%~dp0..\tools\platform-tools\adb.exe"
if not defined SCRCPY_PATH if exist "%~dp0..\tools\scrcpy-win64-v4.1\scrcpy.exe" set "SCRCPY_PATH=%~dp0..\tools\scrcpy-win64-v4.1\scrcpy.exe"

:menu
cls
echo ==============================================
echo                  PHONE AGENT
echo ==============================================
echo.
echo    1   Проверка: телефон, сеть, нейросеть
echo    2   Живая сессия в ленте
echo    3   Тематика: что смотреть, что листать
echo    4   Расписание сессий
echo    5   Служба: работать по расписанию
echo    6   Флот: все телефоны сразу
echo    7   Приложение: окно с кнопками
echo.
echo    0   Выход
echo.
set "choice="
set /p choice="Выбери пункт: "

if "%choice%"=="1" goto doctor
if "%choice%"=="2" goto session
if "%choice%"=="3" goto taste
if "%choice%"=="4" goto plan
if "%choice%"=="5" goto serve
if "%choice%"=="6" goto fleet
if "%choice%"=="7" goto ui
if "%choice%"=="0" exit
goto menu

:doctor
cls
%PY% main.py doctor
goto pausemenu

:session
cls
echo Сколько минут смотреть ленту? Enter - пусть решит сам.
set "mins="
set /p mins="Минуты: "
if "%mins%"=="" (
  %PY% main.py session tiktok --watch
) else (
  set /a secs=%mins%*60
  %PY% main.py session tiktok --duration !secs! --watch
)
goto pausemenu

:taste
cls
%PY% main.py interests --pick
goto pausemenu

:plan
cls
%PY% main.py plan
echo.
echo Добавить правило: окно начала, минуты и дни недели.
echo Пример окна: 21:00-22:00   Пример дней: пн-пт  сб,вс  каждый день
echo Enter вместо окна - ничего не менять.
set "win="
set /p win="Окно (ЧЧ:ММ-ЧЧ:ММ): "
if "%win%"=="" goto pausemenu
set "mins="
set /p mins="Сколько минут смотреть: "
set "days="
set /p days="Дни недели: "
if "%days%"=="" set "days=каждый день"
%PY% main.py plan --add "%win%" --minutes "%mins%" --days "%days%"
goto pausemenu

:fleet
cls
%PY% main.py fleet status
echo.
echo Поднять службы всех подключённых телефонов?
echo Каждый работает по своему расписанию, одновременно с другими.
echo Ctrl+C остановит все.
pause
%PY% main.py fleet
goto pausemenu

:ui
cls
echo Открываю окно приложения.
echo Закрой окно и нажми Ctrl+C здесь, чтобы вернуться в меню.
echo.
%PY% main.py ui
goto pausemenu

:serve
cls
echo Служба работает по расписанию, пока окно открыто.
echo Остановить - Ctrl+C.
echo.
%PY% main.py serve
goto pausemenu

:pausemenu
echo.
pause
goto menu

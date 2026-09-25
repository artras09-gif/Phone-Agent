@echo off
chcp 866 >nul
cd /d "%~dp0"
title PhoneAgent

if not defined PY set "PY=python"

%PY% main.py ui
if errorlevel 1 (
  echo.
  echo Не удалось запустить. Проверь, что установлен Python.
  pause
)

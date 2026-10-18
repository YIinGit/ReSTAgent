@echo off
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcut.ps1" %*
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo.
pause

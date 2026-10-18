@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

call :findpy
if not defined PYEXE goto :nopy

echo ReSTAgent starting...
echo   interpreter: %PYEXE%
%PYEXE% -m app.desktop --selftest
if errorlevel 1 goto :fail

for /f "delims=" %%P in ('%PYEXE% -c "import sys;print(sys.executable)"') do set "PYFULL=%%P"
for %%I in ("%PYFULL%") do set "PYDIR=%%~dpI"
set "PYW="
if exist "%PYDIR%pythonw.exe" set "PYW=%PYDIR%pythonw.exe"

if defined RA_DRYRUN (
  echo [dry-run] self-check passed, window not opened.
  exit /b 0
)

if defined PYW (
  echo   launching window via %PYW%
  start "" "%PYW%" -m app.desktop
) else (
  echo   pythonw.exe not found, running with console
  start "" "%PYFULL%" -m app.desktop
)
exit /b 0

:fail
echo.
echo [ERROR] Self-check failed. See the messages above.
echo.
pause
exit /b 1

:nopy
echo [ERROR] No usable Python 3.10+ found.
echo.
echo Tried: py -3.12 / py -3.13 / py -3.14 / py -3.11 / py -3.10 / python / python3 / py -3 / py
echo Run "py -0p" to inspect registered versions, or reinstall Python.
echo.
pause
exit /b 1

:findpy
set "PYEXE="
call :try "py -3.12"
call :try "py -3.13"
call :try "py -3.14"
call :try "py -3.11"
call :try "py -3.10"
call :try "python"
call :try "python3"
call :try "py -3"
call :try "py"
exit /b 0

:try
if defined PYEXE goto :eof
%~1 -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 set "PYEXE=%~1"
goto :eof

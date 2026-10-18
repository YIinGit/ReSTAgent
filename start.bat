@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   ReSTAgent - local AI manuscript review
echo ==========================================
echo.

call :findpy
if not defined PYEXE goto :nopy

echo Interpreter: %PYEXE%
%PYEXE% -m app.desktop --selftest
if errorlevel 1 goto :fail
echo.
echo Keep this window open. Close it to stop the service.
echo.
%PYEXE% -m app.server %*
echo.
echo Server stopped.
pause
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
echo.
echo Fix:
echo   1. Install Python from https://www.python.org/downloads/windows/
echo      and tick "Add python.exe to PATH".
echo   2. If "py" points to a removed version, run "py -0p" to inspect,
echo      or reinstall Python.
echo   3. If Python lives in a custom folder, add it to PATH or put the
echo      full path of python.exe into the PYEXE line below.
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

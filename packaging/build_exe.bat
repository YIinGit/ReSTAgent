@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

echo ============================================
echo   打包 ReSTAgent.exe
echo ============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (set "PYEXE=py -3") else (set "PYEXE=python")

%PYEXE% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo 未检测到 PyInstaller，正在安装（需要联网，只装一次）…
  %PYEXE% -m pip install --upgrade pyinstaller
  if errorlevel 1 (
    echo.
    echo [失败] 安装 PyInstaller 失败。请检查网络或手动执行：
    echo        pip install pyinstaller
    pause
    exit /b 1
  )
)

echo 开始打包（首次约 1-3 分钟）…
%PYEXE% -m PyInstaller packaging\ReSTAgent.spec --noconfirm --clean
if errorlevel 1 (
  echo.
  echo [失败] 打包未完成，请看上面的错误信息。
  pause
  exit /b 1
)

echo.
echo 完成：dist\ReSTAgent.exe
echo 双击即可运行；配置与数据会写到 %LOCALAPPDATA%\ReSTAgent\。
pause

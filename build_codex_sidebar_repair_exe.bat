@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHONUTF8=1"
set "PYTHON_EXE=python"

if exist "F:\python\python3.15\python.exe" (
  set "PYTHON_EXE=F:\python\python3.15\python.exe"
)

"%PYTHON_EXE%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name CodexSidebarRepair ^
  "%SCRIPT_DIR%codex_sidebar_repair_gui.py"

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

echo.
echo Build completed:
echo   %SCRIPT_DIR%dist\CodexSidebarRepair.exe

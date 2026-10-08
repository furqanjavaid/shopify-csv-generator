@echo off
cd /d "%~dp0.."
echo ========================================
echo  Sentivo Tools — Installer Builder
echo ========================================
echo.

REM Step 1 — Build .exe
echo [1/3] Building SentivoTools.exe...
python build.py
if %ERRORLEVEL% neq 0 (
    echo [FAIL] PyInstaller build failed!
    exit /b 1
)

REM Step 2 — Download Python if not present
if not exist "installer\python-3.11.9-amd64.exe" (
    echo [2/3] Downloading Python 3.11.9...
    curl.exe -L "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -o "installer\python-3.11.9-amd64.exe"
) else (
    echo [2/3] Python installer already present — skipping download
)

REM Step 3 — Build installer
echo [3/3] Building SentivoToolsSetup.exe...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "installer\sentivo_setup.iss"

if %ERRORLEVEL% == 0 (
    echo.
    echo ========================================
    echo  [OK] SUCCESS!
    echo  Output: installer\output\SentivoToolsSetup.exe
    echo ========================================
) else (
    echo.
    echo [FAIL] Inno Setup build failed!
    exit /b 1
)

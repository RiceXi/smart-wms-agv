@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
set "NODE_EXE=node.exe"
where.exe node.exe >nul 2>&1
if errorlevel 1 (
    if not exist "D:\node.exe" (
        echo Node.js 24 or newer is required. Check your PATH and try again.
        pause
        exit /b 1
    )
    set "NODE_EXE=D:\node.exe"
)
"%NODE_EXE%" src\server.js --open
set "WMS_EXIT=%errorlevel%"
if not "%WMS_EXIT%"=="0" pause
exit /b %WMS_EXIT%

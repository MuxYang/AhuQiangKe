@echo off
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"
set "PWSH_EXE="

:: 优先使用 pwsh，其次 powershell
where /q pwsh.exe
if %errorlevel%==0 set "PWSH_EXE=pwsh.exe"
if not defined PWSH_EXE (
    where /q powershell.exe
    if %errorlevel%==0 set "PWSH_EXE=powershell.exe"
)

if not defined PWSH_EXE (
    echo [FAIL] 未找到 PowerShell（pwsh 或 powershell），请安装后重试。
    exit /b 1
)

:: 绕过执行策略，调用 run.ps1（保持中文输出）
"%PWSH_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run.ps1" %*
exit /b %errorlevel%
pause
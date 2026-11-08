@echo off
setlocal
rem 以调试模式启动 Trae CN，让挂件能自动读取用量
rem 使用前请先完全退出 Trae（任务管理器里不能有 Trae CN.exe 残留）

set "TRAE=%LOCALAPPDATA%\Programs\Trae CN\Trae CN.exe"
if not exist "%TRAE%" (
    echo [错误] 未找到 Trae CN: %TRAE%
    pause
    exit /b 1
)

tasklist /FI "IMAGENAME eq Trae CN.exe" | find /I "Trae CN.exe" >nul
if not errorlevel 1 (
    echo [提示] Trae 还在运行。请先完全退出 Trae，再运行本脚本。
    echo        否则新进程只会复用已有窗口，调试端口不会打开。
    pause
    exit /b 1
)

echo 正在启动 Trae（调试端口 9229）...
start "" "%TRAE%" --inspect=9229

echo 等待调试端口就绪...
set /a TRY=0
:check
timeout /t 2 >nul
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -Uri 'http://127.0.0.1:9229/json/version' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){'PORT_OK'}}catch{'PORT_NO'}" > "%TEMP%\tw_port_check.txt"
set /p RES=<"%TEMP%\tw_port_check.txt"
if "%RES%"=="PORT_OK" goto ok
set /a TRY+=1
if %TRY% lss 12 goto check

echo.
echo [失败] 调试端口 9229 没有打开。
echo        可能原因：Trae 忽略了启动参数，或仍有残留进程。
echo        请把本窗口截图发给开发者。
pause
exit /b 1

:ok
echo.
echo [成功] 调试端口已启用，挂件会在 1 分钟内自动读取 Trae 用量。
timeout /t 4 >nul
exit /b 0

@echo off
rem Build of the official Telegram Bot API server (tdlib/telegram-bot-api)
rem static x64, for PhoneAgent: lets the bot receive files up to 2000 MB.
setlocal
set ROOT=D:\tgbuild
set CMAKE="C:\Program Files\Microsoft Visual Studio\18\Insiders\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
call "C:\Program Files\Microsoft Visual Studio\18\Insiders\VC\Auxiliary\Build\vcvars64.bat" >nul || goto :fail
set CL=/MP12
cd /d %ROOT%

echo [1/5] vcpkg %TIME%
if not exist vcpkg git clone --depth 1 https://github.com/microsoft/vcpkg.git || goto :fail
if not exist vcpkg\vcpkg.exe call vcpkg\bootstrap-vcpkg.bat -disableMetrics || goto :fail

echo [2/5] openssl zlib gperf %TIME%
vcpkg\vcpkg.exe install gperf:x64-windows-static openssl:x64-windows-static zlib:x64-windows-static --disable-metrics || goto :fail

echo [3/5] sources %TIME%
if not exist telegram-bot-api git clone --recursive --depth 1 https://github.com/tdlib/telegram-bot-api.git || goto :fail

echo [4/5] configure %TIME%
if not exist telegram-bot-api\build mkdir telegram-bot-api\build
cd telegram-bot-api\build
%CMAKE% -A x64 -DCMAKE_INSTALL_PREFIX:PATH=%ROOT%\out -DCMAKE_TOOLCHAIN_FILE:FILEPATH=%ROOT%\vcpkg\scripts\buildsystems\vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-windows-static -DCMAKE_POLICY_VERSION_MINIMUM=3.5 .. || goto :fail

echo [5/5] build %TIME%
%CMAKE% --build . --target install --config Release --parallel 8 || goto :fail

echo DONE %TIME%
dir %ROOT%\out\bin
exit /b 0

:fail
echo FAILED %TIME%
exit /b 1

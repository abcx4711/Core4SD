@echo off
rem Builds test\test_proto.exe with MinGW-w64 (mingw32-make and gcc).
rem
rem   make.bat          build test_proto.exe
rem   make.bat run      build and run all tests (test_proto and sim_e2e.py)
rem   make.bat clean    delete test_proto.exe and libspiisd.so
rem
rem mingw32-make is taken from PATH, or else from the folder that
rem   winget install BrechtSanders.WinLibs.POSIX.UCRT
rem installs it to. That folder goes into PATH for this script only, because
rem the Makefile calls gcc and g++ without a path.

setlocal
set "MINGW="
for %%i in (mingw32-make.exe) do set "MINGW=%%~dp$PATH:i"
if not defined MINGW set "MINGW=%LOCALAPPDATA%\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin\"
if not exist "%MINGW%mingw32-make.exe" (
    echo ERROR: mingw32-make.exe not found.
    echo Install MinGW-w64 with:  winget install BrechtSanders.WinLibs.POSIX.UCRT
    echo then open a new prompt, or add the folder of mingw32-make.exe to PATH.
    exit /b 1
)
set "PATH=%MINGW%;%PATH%"

set "TARGET=%*"
if "%TARGET%"=="" set "TARGET=test_proto.exe"
"%MINGW%mingw32-make.exe" -C "%~dp0." %TARGET%
exit /b %errorlevel%

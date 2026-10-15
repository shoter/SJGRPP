@echo off
rem Configure (first time) and build OpenTTD with MSVC + Ninja into <repo>\build.
rem Set VCPKG_INSTALLED to an existing vcpkg_installed directory to reuse it (saves a long vcpkg build in fresh worktrees).
rem Usage: utils\debug_harness\build.cmd [extra cmake --build args]
setlocal
set "REPO=%~dp0..\.."
set "BUILD=%REPO%\build"
if not defined VSDIR set "VSDIR=C:\Program Files\Microsoft Visual Studio\18\Community"
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1

if exist "%BUILD%\build.ninja" goto build
set "EXTRA="
if defined VCPKG_INSTALLED set "EXTRA=-DVCPKG_INSTALLED_DIR=%VCPKG_INSTALLED% -DVCPKG_MANIFEST_INSTALL=OFF"
cmake -S "%REPO%" -B "%BUILD%" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DOPTION_USE_ASSERTS=ON -DCMAKE_TOOLCHAIN_FILE="%VSDIR%\VC\vcpkg\scripts\buildsystems\vcpkg.cmake" -DVCPKG_TARGET_TRIPLET=x64-windows-static %EXTRA% || exit /b 1

:build
cmake --build "%BUILD%" --target openttd %* || exit /b 1

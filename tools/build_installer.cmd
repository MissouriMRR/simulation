@echo off
rem Rebuild the installer: Output\MRRSimulator-Setup-<version>.exe
rem
rem   tools\build_installer.cmd          everything: images, launcher, Linux payload, installer (takes a while)
rem   tools\build_installer.cmd quick    only the launcher and the installer; the Linux payload in payload\ is reused
rem                                      (right for changes to the launcher, scripts, sim_config, docs or the .iss)
rem
rem Changed anything under containers\? Use the full build: it rebuilds the images in your "mrr" distro (cached layers
rem make that quick), and the payload is made from those images.
rem
rem Inno Setup is looked for in %ISCC%, on PATH, in Program Files and in %USERPROFILE%\.dev\Inno Setup 7.
setlocal
cd /d "%~dp0.."

set "MODE=%~1"
if "%MODE%"=="" set "MODE=full"
if /i not "%MODE%"=="full" if /i not "%MODE%"=="quick" (
    echo usage: tools\build_installer.cmd [full^|quick]
    exit /b 2
)

if not defined ISCC for %%p in (ISCC.exe) do if not "%%~$PATH:p"=="" set "ISCC=%%~$PATH:p"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 7\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 7\ISCC.exe"
if not defined ISCC if exist "%USERPROFILE%\.dev\Inno Setup 7\ISCC.exe" set "ISCC=%USERPROFILE%\.dev\Inno Setup 7\ISCC.exe"
if not defined ISCC (
    echo Inno Setup's ISCC.exe was not found. Install Inno Setup 7 or set the ISCC environment variable to its path.
    exit /b 1
)

if /i "%MODE%"=="full" (
    echo.
    echo === 1/4  container images in the "mrr" distro ===
    call mrrsim.cmd images build script
    if errorlevel 1 exit /b 1
    call mrrsim.cmd images test all
    if errorlevel 1 exit /b 1

    echo.
    echo === 2/4  Linux payload ^(payload\mrr-rootfs.tar.gz^) ===
    python -B tools\build_distro.py
    if errorlevel 1 exit /b 1
)

echo.
echo === launcher ^(MRRSimulator.exe, MRRSimulatorGUI.exe^) ===
call launcher\build.cmd
if errorlevel 1 exit /b 1

echo.
echo === installer ===
if not exist payload\mrr-rootfs.tar.gz (
    echo payload\mrr-rootfs.tar.gz is missing: run the full build.
    exit /b 1
)
"%ISCC%" MRRSimulationLauncher.iss
if errorlevel 1 exit /b 1

echo.
echo Done:
dir /b Output\*.exe
exit /b 0

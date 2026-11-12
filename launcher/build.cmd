@echo off
rem Build the launcher into build\MRRSimulator\ (PyInstaller, one-folder mode):
rem   MRRSimulator.exe     command line
rem   MRRSimulatorGUI.exe  the window
rem The installer script (MRRSimulationLauncher.iss) picks that folder up.
rem Needs PyInstaller: pip install pyinstaller. Known to work with 6.22.3; the part a newer version could break is
rem MERGE in mrrsim.spec (two exes sharing one Python runtime). tests\test_build.py checks a build (opt-in).
setlocal
cd /d "%~dp0.."
rem the version number is kept in MRRSimulationLauncher.iss only; the exes get a generated copy
python tools\sync_version.py
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --distpath build --workpath build\pyinstaller-work launcher\mrrsim.spec
if errorlevel 1 exit /b 1
echo.
echo Built: %CD%\build\MRRSimulator\MRRSimulator.exe
echo Built: %CD%\build\MRRSimulator\MRRSimulatorGUI.exe

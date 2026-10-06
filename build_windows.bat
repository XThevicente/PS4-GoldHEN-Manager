@echo off
setlocal
cd /d "%~dp0"

echo ==============================================
echo   PS4 GoldHEN Manager - Build para Windows
echo ==============================================

py -3 -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto :error

py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "PS4_GoldHEN_Manager_v1.0.0" ^
  --icon "assets\ps4_goldhen_manager.ico" ^
  --add-data "assets\ps4_goldhen_manager.ico;assets" ^
  ps4_manager.py
if errorlevel 1 goto :error

echo.
echo Compilacion completada.
echo EXE: %CD%\dist\PS4_GoldHEN_Manager_v1.0.0.exe
echo El EXE usa el icono de PS4 GoldHEN Manager y --windowed: no muestra consola ni icono de Python.
pause
exit /b 0

:error
echo.
echo ERROR: la compilacion no se pudo completar.
pause
exit /b 1

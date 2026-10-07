@echo off
cd /d "%~dp0"
py -3 -m pip install pyinstaller
if errorlevel 1 exit /b 1
py -3 -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name PS4_GoldHEN_Companion_PC_v0.5.0 --paths . companion_control.py
if errorlevel 1 exit /b 1
echo EXE creado en dist\PS4_GoldHEN_Companion_PC_v0.5.0.exe
pause

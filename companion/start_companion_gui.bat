@echo off
cd /d "%~dp0"
start "" pythonw.exe companion_control.py
if errorlevel 1 pyw -3 companion_control.py

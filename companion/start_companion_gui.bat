@echo off
cd /d "%~dp0"
py -3 companion_control.py
if errorlevel 1 pause

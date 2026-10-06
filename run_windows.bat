@echo off
cd /d "%~dp0"
py -3 ps4_manager.py
if errorlevel 1 pause

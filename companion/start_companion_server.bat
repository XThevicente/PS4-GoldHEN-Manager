@echo off
cd /d "%~dp0"
title PS4 GoldHEN Companion Server
py -3 companion_server.py
if errorlevel 1 pause

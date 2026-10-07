@echo off
title PS4 GoldHEN Companion - Firewall
echo.
echo  PS4 GoldHEN Companion v0.2.1
echo  Abriendo solo los puertos locales necesarios...
echo.
net session >nul 2>&1
if errorlevel 1 (
  echo Este archivo necesita permisos de Administrador.
  echo Pulsa con boton derecho y elige "Ejecutar como administrador".
  pause
  exit /b 1
)

netsh advfirewall firewall delete rule name="PS4 GoldHEN Companion TCP 8787" >nul 2>&1
netsh advfirewall firewall delete rule name="PS4 GoldHEN Companion UDP 8786" >nul 2>&1

netsh advfirewall firewall add rule name="PS4 GoldHEN Companion TCP 8787" dir=in action=allow protocol=TCP localport=8787 profile=private
netsh advfirewall firewall add rule name="PS4 GoldHEN Companion UDP 8786" dir=in action=allow protocol=UDP localport=8786 profile=private

echo.
echo Reglas creadas para redes PRIVADAS.
echo TCP 8787 = API PS4 - PC
echo UDP 8786 = descubrimiento
echo.
pause

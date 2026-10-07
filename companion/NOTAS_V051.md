# Companion v0.5.1

Corrige el error de configuracion que permitia seleccionar una ROM, una DLL o un archivo comprimido como emulador. Windows devolvia WinError 193 al intentar ejecutar archivos no ejecutables, mientras la biblioteca los mostraba como configurados.

- El selector de Windows muestra EXE y la configuracion explica la diferencia entre juego y emulador.
- Se valida extension y cabecera PE, se rechazan DLL, EXE truncado y ROM renombrada, y se vuelve a validar al lanzar perfiles guardados de v0.5.
- Los errores Windows 193 y 216 indican que debes seleccionar un EXE compatible y comprobar que abre directamente.
- La validacion comprueba formato basico; no garantiza arquitectura, dependencias ni compatibilidad con una ROM. Windows realiza la comprobacion final al ejecutar.
- PS4 conserva los caracteres de las palabras con tildes mediante transliteracion y dibuja parentesis, corchetes y otros signos habituales.
- Se mantienen el catalogo, IDs, carpeta de ROMs, perfiles anteriores, el token de emparejamiento y los controles.

## Corregir la configuracion

1. Descarga y extrae un emulador de NES para Windows desde su fuente oficial, por ejemplo Mesen: https://www.mesen.ca/
2. Abre su EXE directamente y verifica que inicia. Prueba a abrir la ROM desde el emulador.
3. Companion → Retro Manager → Configurar emulador → sistema `nes`.
4. En ejecutable selecciona el EXE del emulador, por ejemplo `Mesen.exe`.
5. Argumentos: `["{rom}"]`. Guarda y prueba Lanzar en PC; despues prueba X desde PS4.

La ROM `.nes` permanece en la subcarpeta `nes` de la biblioteca. Si ya tenias configurado un archivo incorrecto en v0.5, debes sustituir esa ruta; el programa no puede elegir ni instalar un emulador automaticamente.

Validacion: 13 pruebas automatizadas aprobadas en Linux, incluida reproduccion de un perfil guardado que apunta a una ROM y un error WinError 193 simulado. Prueba de apertura/cierre de proceso real con Python. Sintaxis C comprobada con cabeceras OpenOrbis v0.5.4. Compilaciones Windows y PS4 pendientes al preparar estas notas; el manifiesto de entrega indica su resultado. No se ha ejecutado la ROM Street Fighter en un emulador durante estas pruebas.

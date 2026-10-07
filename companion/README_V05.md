# PS4 GoldHEN Companion v0.5.0 — Retro Manager

Versión experimental para probar en consola. Mantiene el puente HTTP v1 de la v0.4.

## Uso

1. Cierra el Companion anterior y abre el EXE v0.5.0. Inicia el servidor.
2. En Retro Manager, selecciona una carpeta con subcarpetas `nes`, `snes`, `gb`, `gbc`, `gba`, `megadrive`, `ps1`, `n64` o `homebrew`. Coloca tus ROMs en su sistema. El escaneo admite subcarpetas internas y no sigue enlaces simbólicos.
3. Configura el ejecutable de cada emulador ya instalado en el PC. Los argumentos se escriben como lista JSON: `["{rom}"]`. Para RetroArch, por ejemplo: `["-L", "C:/RetroArch/cores/tu_core.dll", "{rom}"]`. La aplicación no instala emuladores ni comprueba la compatibilidad del core con cada ROM.
4. Pulsa Escanear. Puedes buscar, filtrar, seleccionar un juego y lanzarlo en el PC.
5. Instala el PKG v0.5.0 en la PS4 y abre Companion. Se reutiliza el token de emparejamiento anterior. En una instalación limpia introduce el código del PC con el mando.
6. En PS4: OPTIONS → RETRO MANAGER. Cruceta o stick izquierdo: izquierda/derecha cambia de sistema; arriba/abajo selecciona juegos y cambia de página al llegar al borde; X solicita el lanzamiento en PC; triángulo detiene el proceso que ha iniciado Retro Manager; círculo vuelve; cuadrado actualiza el catálogo después de un nuevo escaneo en PC.

**El juego y el vídeo se ejecutan en el PC. Esta versión no transmite vídeo/audio ni controles de juego a la PS4. El mando controla los menús del cliente PS4. No hay emulación nativa integrada.**

## Biblioteca y carátulas

Extensiones: NES `.nes`; SNES `.sfc/.smc`; GB `.gb`; GBC `.gbc`; GBA `.gba`; Mega Drive `.md/.gen/.smd`; PS1 `.cue/.chd/.pbp`; N64 `.z64/.n64/.v64`; homebrew `.rom` con un emulador configurado. ZIP no se clasifica automáticamente y BIN de PS1 se omite para no duplicar las pistas de los CUE. Estas extensiones clasifican archivos; no garantizan que funcionen en un emulador concreto.

En PC se puede mostrar una carátula PNG con el mismo nombre base de la ROM, de hasta 512 × 512 y 2 MiB. El cliente PS4 muestra un catálogo de texto paginado; todavía no descarga carátulas.

La carpeta y los perfiles se guardan entre sesiones. La biblioteca se reescanea al iniciar el servidor y mediante Escanear. No se modifica el contenido de las ROMs. Máximo: 20000 entradas.

## Verificación antes de darla por probada en hardware

- Heartbeat ONLINE, mensaje, información de consola, reinicio HTTP y archivo pequeño funcionan como en v0.4.
- Catálogo con más de seis juegos: verificar todas las páginas y filtros desde PS4.
- X abre la ROM seleccionada en el PC; un segundo lanzamiento no sustituye un emulador activo.
- Triángulo detiene únicamente el proceso iniciado por Retro Manager. Si el emulador deriva la ejecución a otro proceso o a una instancia ya abierta, ese proceso derivado no se controla.
- Si falta el emulador, se ve un error; si el PC se desconecta, el menú sigue respondiendo y permite volver.
- Sin ROMs: se muestra biblioteca vacía, sin juegos de ejemplo inventados.
- Los comandos PC → PS4 se procesan al salir de los menús. El heartbeat sigue activo dentro de ellos.

Las pruebas automáticas verifican catálogo, paginación, IDs estables, configuración persistente, exclusión de enlaces, autorización HTTP, argumentos de lanzamiento sin shell, peticiones repetidas y compatibilidad de comandos/archivos v0.4. Las compilaciones no sustituyen la prueba física de vídeo, mando y ejecución en PS4.

## Desarrollo

Python 3.12+, biblioteca estándar. GUI: `python companion_control.py`. Tests desde la raíz: `python -m unittest discover -s companion/tests -v`. Las compilaciones EXE y PKG se ejecutan mediante los workflows de la rama `feature/ps4-companion-v0.5`.

El protocolo mantiene los endpoints anteriores y añade, con token Bearer, `GET /api/v1/retro/library?offset=0&system=nes`, `status`, `launch?id=<rom_id>&request=<id_unico>` y `stop`. El servidor resuelve los IDs contra su catálogo; la consola no puede enviar rutas ni comandos de shell. `launch` deduplica los últimos 128 request IDs de la sesión. Estos endpoints usan GET para conservar el cliente HTTP de OpenOrbis existente; un cliente debe usar request IDs y evitar cachés.

# Beta 1.1.0-beta.4 — Cola de transferencias y perfiles PS4/PS5

Esta rama de pruebas conserva la aplicación Tkinter existente, el administrador PS4
y Scene Hub, y añade un selector para separar las consolas.

## Qué funciona

- **PS4 · GoldHEN:** conserva los servicios originales de PS4. Las subidas y
  descargas del panel Archivos ahora se agregan a una cola FIFO, de una en una.
- **Transferencias:** nueva pestaña con origen, destino, progreso y errores.
  «Cancelar pendientes» afecta solamente a trabajos aún no iniciados.
- **PS5 · Vista previa:** panel informativo sin conexión, instalación ni
  transferencia; el programa oculta los controles de PS4 mientras está activo.
- La preferencia del perfil se guarda en la configuración local.
- La interfaz usa una paleta oscura neutra; no se han copiado componentes ni
  fuentes de PS5Upload.

## Límites importantes

- No existe **todavía** soporte funcional de PS5. No se debe utilizar el protocolo
  PS4/GoldHEN para una PS5; hace falta un adaptador independiente con pruebas.
- Los trabajos de la cola son **temporales**: no sobreviven a un reinicio.
- No se implementa **reanudación** de archivos parcialmente transferidos ni
  comparaciones hash. Si un trabajo falla, permanece marcado como error.
- El instalador RPI/HTTP PKG existente es independiente de la cola FTP.
- No se transfiere código de PS5Upload: proyecto externo GPL-3.0. Este
  código nuevo se ha escrito específicamente para el repositorio MIT actual.

## Probar en Windows

1. Descarga el artefacto de Windows del último workflow de esta rama o compila
   con `build_windows.bat`. **No sustituyas la release estable v1.0.0.**
2. En «PS4 · GoldHEN», conecta FTP e inicia dos subidas de archivos pequeños.
   Comprueba que sólo una empieza y la siguiente queda pendiente.
3. Comprueba «Cancelar pendientes», transferencias de carpetas, descargas
   y mensajes de error ante desconexión; los datos deben verificarse en PS4.
4. Cambia a «PS5 · Vista previa»: deben desaparecer los controles de PS4,
   y no debe abrirse ninguna conexión por esta selección.
5. Regresa a PS4; sus pestañas deben recuperarse sin reiniciar.

## Hoja de ruta

1. Integrar una cola persistente con reintento explícito, verificación
   tamaño/hash, reanudación real usando los mecanismos que admita cada servidor.
2. Separar FTP, paquetes, payloads y detección por adaptadores de consola.
3. Añadir un adaptador PS5 **sólo después** de validar el protocolo y un helper
   compatible, sus permisos, su autenticación y las operaciones reales.
4. Rediseñar progresivamente el panel de inicio, sin reescribir el motor de PS4.

## beta.5 — reanudación y verificación

Los archivos se envían a temporales `.goldhen.part`, y las subidas parciales sólo se reanudan si el SHA-256 de su prefijo coincide con el local. La descarga termina en un temporal local, comprueba tamaño y SHA-256 de vuelta contra el servidor, y renombra al final. Los destinos existentes bloquean la operación: **no se sobrescriben**. Para reanudar después de un fallo, vuelve a enviar el mismo archivo desde la interfaz; la cola no sobrevive al reinicio. REST y SIZE deben estar soportados por el servidor FTP; el hash remoto implica una lectura completa adicional y puede tardar mucho. Si la verificación falla, el temporal queda para revisión manual. Esta ruta no ha sido probada en PS4 física.

El adaptador AVA1 de PS5 es por ahora un límite de protocolo explícitamente desactivado; no hay emparejamiento Noise ni cliente funcional. Ningún comando de PS4 se aplica a PS5.

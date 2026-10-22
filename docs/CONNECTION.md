# Detección de PS4 y conexión FTP — beta.2

La detección de la consola y la sesión FTP tienen estados independientes. La búsqueda comprueba primero la última IP conocida y luego las redes locales. Prefiere el puerto FTP configurado y puede encontrar una consola mediante Payload, Klog, Debug o RPI cuando FTP no está disponible.

| Estado | Significado | Siguiente paso |
| --- | --- | --- |
| PS4 detectada · FTP no disponible | Otros servicios responden, pero el puerto FTP no responde. | Usa el servicio disponible. Para archivos, activa FTP en GoldHEN y pulsa **Reintentar FTP**. |
| PS4 detectada · FTP sin conectar | El puerto FTP responde, pero no hay una sesión FTP abierta. | Pulsa **Reintentar FTP**; un error de inicio de sesión conserva su detalle. |
| PS4 detectada · FTP conectado | La sesión FTP está abierta. | Ya puedes transferir archivos y utilizar las funciones que requieren FTP. |
| PS4 sin respuesta | Ninguno de los servicios comprobados responde. | Revisa la IP, la red y los servicios de GoldHEN. También puedes escribir la IP manualmente. |

Un puerto no disponible también puede deberse a la red o al cortafuegos. La aplicación no activa servicios en la PS4. La detección utiliza puertos conocidos; no autentica la identidad física de la consola.

Payload, Klog, Debug y RPI usan sus propias conexiones. Explorar archivos, subir o descargar contenido y leer la biblioteca mediante FTP siguen requiriendo una sesión FTP. RPI con el puerto abierto pero sin respuesta válida conserva su estado específico.

## Prueba en PS4 real

1. Pon PC y PS4 en la misma red. En GoldHEN, activa Payload Server y deja FTP desactivado.
2. Abre la beta y pulsa **Buscar PS4**. Debe aparecer **PS4 detectada · FTP no disponible**, con Payload disponible y sin un error genérico de conexión.
3. Intenta una operación de archivos. Debe explicar que requiere activar FTP; la consola debe seguir detectada.
4. Activa FTP en GoldHEN. Pulsa **Reintentar FTP**. Debe aparecer **FTP conectado** y cargarse la lista de archivos, sin repetir la búsqueda.
5. Repite con Klog como único servicio disponible y comprueba su lectura.
6. Si usas un puerto FTP distinto, configúralo antes de buscar y confirma que se conserva al reiniciar.
7. Con la consola apagada, comprueba que no se presenta como detectada.

Las pruebas automáticas ejecutan los callbacks reales de la aplicación con servicios de red simulados. Cubren detección sin FTP, cambio de IP, reintento, puerto personalizado, errores de inicio de sesión, consola inaccesible y respuestas tardías tras desconectar. No sustituyen estas pruebas en Windows y PS4 reales.

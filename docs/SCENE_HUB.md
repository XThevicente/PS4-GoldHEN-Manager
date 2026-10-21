# Integración de GoldHEN Scene Hub (beta v1.1.0)

Esta rama incorpora la sección **📰 Scene Hub** en la aplicación Windows, con un feed de noticias del portal, enlace al foro y versiones publicadas de PS4 GoldHEN Manager.

## Qué hace

- Lee `scene_news.json`, un feed JSON público de títulos, fechas, resumen y enlaces.
- Si el feed no está disponible, usa el sitemap y las páginas públicas del portal.
- Consulta las últimas versiones del repositorio mediante GitHub Releases Atom.
- Muestra un aviso cuando detecta una versión **estable** superior a la que está ejecutándose, sin descargar ni ejecutar nada.
- Mantiene un caché JSON de lectura para que las últimas noticias visibles se puedan consultar sin conexión.
- Las peticiones se ejecutan en un hilo trabajador y los resultados se incorporan mediante la cola de Tkinter; la interfaz de la PS4 no debe quedar bloqueada por Internet.
- El botón *Abrir comunidad* abre la web en el navegador predeterminado.

## Fuentes y mantenimiento

Web: https://itrsr62yvx.zite.so

Releases: https://github.com/XThevicente/PS4-GoldHEN-Manager/releases

Feed: https://raw.githubusercontent.com/XThevicente/PS4-GoldHEN-Manager/main/scene_news.json

La publicación de una noticia en Zite **no actualiza automáticamente el feed JSON** por sí sola. La tarea programada de noticias debe mantener sincronizado también el archivo `scene_news.json` en la rama `main`. Si el archivo no se ha creado aún o falla su actualización, el programa podrá consultar como respaldo las noticias del sitemap existente.

El feed JSON debe contener exactamente:

```json
{
  "schema": 1,
  "site": "https://itrsr62yvx.zite.so",
  "news": [
    {
      "title": "Título del artículo",
      "description": "Resumen breve",
      "section": "PS4",
      "date": "2026-10-17",
      "url": "https://itrsr62yvx.zite.so/?news=UUID"
    }
  ]
}
```

No incluir datos privados, tokens, cuentas ni URLs de descarga arbitrarias. El lector valida el dominio y la ruta de las noticias.

## Compilación y pruebas

1. Ejecuta `python -m unittest discover -s tests -p "test_*.py" -v`.
2. Ejecuta `build_windows.bat` en Windows (Python 3.12+).
3. Prueba la sección Scene Hub conectado y desconectado de Internet.
4. Comprueba que FTP, PKG, biblioteca, conexión a PS4, consola y cheats siguen operativos.

GitHub Actions genera un artefacto **v1.1.0-beta.1** independiente. Nunca sobrescribe la release estable v1.0.0. La publicación de una nueva release, tras probar la beta, es un paso manual separado.

## Limitaciones actuales

Las noticias proceden de fuentes públicas verificables y no necesitan acceder a cuentas del foro. La pestaña no permite responder o iniciar sesión: para eso abre el portal en el navegador. La detección de actualizaciones no instala nada automáticamente. La beta debe probarse en un ordenador Windows real antes de promoverla a estable.

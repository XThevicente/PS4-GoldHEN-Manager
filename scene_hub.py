"""Lectura segura de noticias del Scene Hub y de releases oficiales (sin dependencias).
Este módulo no descarga ni ejecuta software y tampoco se conecta con la PS4.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HUB_URL = "https://itrsr62yvx.zite.so"
HUB_SITEMAP = HUB_URL + "/sitemap.xml"
RELEASES_URL = "https://github.com/XThevicente/PS4-GoldHEN-Manager/releases"
RELEASES_ATOM = RELEASES_URL + ".atom"
NEWS_PATH = re.compile(r"^/noticias/[0-9a-f-]{36}\.html$")
RELEASE_PATH = re.compile(
    r"^/XThevicente/PS4-GoldHEN-Manager/releases/tag/[A-Za-z0-9._-]+$"
)
MAX_SITEMAP_BYTES = 240_000
MAX_ARTICLE_BYTES = 280_000
MAX_ATOM_BYTES = 350_000
MAX_NEWS = 10
MAX_RELEASES = 6


def _fetch(url: str, limit: int, *, timeout: int = 6) -> bytes:
    """HTTPS, redirects restringidas por el llamante y límite estricto de tamaño."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PS4GoldHENManager-SceneHub/1.1",
            "Accept": "application/xml, application/atom+xml, text/html;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        redirected = response.geturl()
        # Nunca seguir una redirección a HTTP u otro dominio inesperado.
        orig = urllib.parse.urlsplit(url)
        final = urllib.parse.urlsplit(redirected)
        if final.scheme != "https" or final.hostname != orig.hostname:
            raise ValueError("Redirección externa no permitida")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Respuesta demasiado grande")
    return data


def _approved_news_link(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    return (
        p.scheme == "https"
        and p.netloc == "itrsr62yvx.zite.so"
        and bool(NEWS_PATH.fullmatch(p.path))
        and not p.query
        and not p.fragment
    )


def _approved_release_link(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    return (
        p.scheme == "https"
        and p.netloc == "github.com"
        and bool(RELEASE_PATH.fullmatch(p.path))
        and not p.query
        and not p.fragment
    )


class _Metadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.heading = ""
        self.description = ""
        self.date = ""
        self._in_title = False
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            content = (a.get("content") or "").strip()
            if name in ("description", "og:description") and not self.description:
                self.description = content[:260]
            elif name == "article:published_time" and not self.date:
                self.date = content[:10]

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False

    def handle_data(self, text: str) -> None:
        if self._in_title:
            self.title += text
        elif self._in_h1:
            self.heading += text


def parse_sitemap(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    entries: list[str] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "loc" and node.text:
            url = node.text.strip()
            if _approved_news_link(url) and url not in entries:
                entries.append(url)
    return entries[:MAX_NEWS]


def parse_article(html_bytes: bytes, url: str) -> dict[str, str]:
    if not _approved_news_link(url):
        raise ValueError("URL de artículo no autorizada")
    metadata = _Metadata()
    metadata.feed(html_bytes.decode("utf-8", errors="replace"))
    title = (metadata.heading or metadata.title).strip()
    title = re.split(r"\s*[|·]\s*GoldHEN Scene Hub", title)[0].strip()
    if not title or len(title) < 5:
        raise ValueError("El artículo no tiene título")
    return {
        "title": title[:150],
        "description": metadata.description,
        "date": metadata.date,
        "url": url,
    }


def parse_atom(xml_bytes: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    ns = "{http://www.w3.org/2005/Atom}"
    releases: list[dict[str, str]] = []
    for entry in root.findall(ns + "entry"):
        title = (entry.findtext(ns + "title") or "").strip()[:150]
        updated = (entry.findtext(ns + "updated") or "")[:10]
        for link in entry.findall(ns + "link"):
            url = link.attrib.get("href", "")
            if link.attrib.get("rel") == "alternate" and _approved_release_link(url):
                releases.append(
                    {"title": title, "date": updated, "url": url, "tag": url.rsplit("/", 1)[-1]}
                )
                break
        if len(releases) >= MAX_RELEASES:
            break
    return releases


def version_numbers(label: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?", label, re.I)
    if not match:
        return None
    return tuple(map(int, match.groups()))


def is_update_available(current_version: str, releases: list[dict[str, str]]) -> bool:
    current = version_numbers(current_version)
    if current is None:
        return False
    # Solo considerar versiones estables (no dev, beta, alpha, rc).
    for item in releases:
        tag = str(item.get("tag", ""))
        if re.search(r"(?:dev|alpha|beta|rc)", tag, re.I):
            continue
        latest = version_numbers(tag)
        if latest is not None:
            return latest > current
    return False


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and obj.get("schema") == 1:
            return obj
    except (OSError, ValueError):
        pass
    return {}


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica para evitar un JSON truncado si Windows cierra la app.
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent), suffix=".tmp", delete=False
        ) as fh:
            temp_name = fh.name
            json.dump(data, fh, ensure_ascii=False)
        Path(temp_name).replace(path)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink(missing_ok=True)


def fetch_scene_bundle(cache_path: Path) -> dict[str, Any]:
    """Consulta fuentes públicas; recupera caché cuando la red falla.

    No confundir el índice SEO del sitio con la totalidad de artículos del foro:
    el botón 'Abrir Scene Hub' muestra siempre la sección actualizada de la web.
    """
    existing = _read_cache(cache_path)
    news: list[dict[str, str]] = []
    releases: list[dict[str, str]] = []
    problems: list[str] = []
    try:
        urls = parse_sitemap(_fetch(HUB_SITEMAP, MAX_SITEMAP_BYTES))
        for url in urls:
            try:
                news.append(parse_article(_fetch(url, MAX_ARTICLE_BYTES), url))
            except (OSError, ValueError, ET.ParseError, UnicodeError):
                continue
        if not news:
            problems.append("No se pudieron leer las noticias del índice público")
    except (OSError, ValueError, ET.ParseError, UnicodeError) as exc:
        problems.append("Noticias: " + type(exc).__name__)
    try:
        releases = parse_atom(_fetch(RELEASES_ATOM, MAX_ATOM_BYTES, timeout=8))
        if not releases:
            problems.append("GitHub no ha devuelto lanzamientos")
    except (OSError, ValueError, ET.ParseError, UnicodeError) as exc:
        problems.append("GitHub: " + type(exc).__name__)

    cache_used = False
    if not news:
        news = [x for x in existing.get("news", []) if
                isinstance(x, dict) and _approved_news_link(str(x.get("url", "")))][:MAX_NEWS]
        cache_used = cache_used or bool(news)
    if not releases:
        releases = [x for x in existing.get("releases", []) if
                    isinstance(x, dict) and _approved_release_link(str(x.get("url", "")))][:MAX_RELEASES]
        cache_used = cache_used or bool(releases)

    news.sort(key=lambda x: x.get("date", ""), reverse=True)
    result = {
        "schema": 1,
        "news": news,
        "releases": releases,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cache_used": cache_used,
        "status": " | ".join(problems) if problems else "Información actualizada",
    }
    if news or releases:
        try:
            _save_cache(cache_path, result)
        except OSError:
            pass
    return result

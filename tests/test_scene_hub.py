"""Pruebas sin red ni interfaz gráfica para el módulo de Scene Hub."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import scene_hub


class SceneHubTests(unittest.TestCase):
    def test_news_urls_are_limited_to_hub(self):
        good = "https://itrsr62yvx.zite.so/?news=f88361cd-f63e-4597-b251-f06fb67ef15a"
        self.assertTrue(scene_hub._approved_news_link(good))
        self.assertTrue(scene_hub._approved_news_link(
            "https://itrsr62yvx.zite.so/noticias/f88361cd-f63e-4597-b251-f06fb67ef15a.html"))
        for url in (
            "http://itrsr62yvx.zite.so/?news=f88361cd-f63e-4597-b251-f06fb67ef15a",
            "https://evil.example/?news=f88361cd-f63e-4597-b251-f06fb67ef15a",
            "https://itrsr62yvx.zite.so/?news=../../other",
            "https://itrsr62yvx.zite.so/?news=f88361cd-f63e-4597-b251-f06fb67ef15a&redirect=1",
            "https://itrsr62yvx.zite.so/privacidad.html",
        ):
            self.assertFalse(scene_hub._approved_news_link(url), url)

    def test_json_feed_validated_deduplicated(self):
        approved = "https://itrsr62yvx.zite.so/?news=f88361cd-f63e-4597-b251-f06fb67ef15a"
        feed = {"schema": 1, "news": [
            {"title": "Apollo Save Tool 2.4.0", "description": "Nuevo lanzamiento",
             "date": "2026-10-17", "url": approved},
            {"title": "Duplicado rechazado", "date": "2026-10-17", "url": approved},
            {"title": "Sitio malicioso", "url": "https://evil.example/other"},
            {"title": "Otro artículo", "date": "fecha-inválida",
             "url": "https://itrsr62yvx.zite.so/?news=5856c9b1-76aa-4e82-b69a-26dee9c660a4"},
        ]}
        result = scene_hub.parse_news_json(json.dumps(feed).encode())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2026-10-17")
        self.assertEqual(result[1]["date"], "")

    def test_sitemap_only_news(self):
        data = b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://itrsr62yvx.zite.so/noticias/f88361cd-f63e-4597-b251-f06fb67ef15a.html</loc></url>
        <url><loc>https://itrsr62yvx.zite.so/</loc></url>
        <url><loc>https://evil.example/noticias/f88361cd-f63e-4597-b251-f06fb67ef15a.html</loc></url>
        </urlset>'''
        result = scene_hub.parse_sitemap(data)
        self.assertEqual(len(result), 1)

    def test_article_title_and_description(self):
        html = b'''<html><head><title>GoldHEN Scene Hub | Noticias</title>
        <meta name="description" content="Contenido verificado"/></head>
        <body><h1>Apollo Save Tool 2.4.0</h1></body></html>'''
        url = "https://itrsr62yvx.zite.so/noticias/f88361cd-f63e-4597-b251-f06fb67ef15a.html"
        article = scene_hub.parse_article(html, url)
        self.assertEqual(article["title"], "Apollo Save Tool 2.4.0")
        self.assertEqual(article["description"], "Contenido verificado")

    def test_atom_releases_and_comparison(self):
        data = b'''<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>GoldHEN Manager v1.2.0</title>
        <updated>2026-10-21T13:00:00Z</updated>
        <link rel="alternate" href="https://github.com/XThevicente/PS4-GoldHEN-Manager/releases/tag/v1.2.0"/>
        </entry>
        <entry><title>Malicious</title><updated>2026-10-21T14:00:00Z</updated>
        <link rel="alternate" href="https://evil.example/release"/></entry>
        </feed>'''
        releases = scene_hub.parse_atom(data)
        self.assertEqual(len(releases), 1)
        self.assertTrue(scene_hub.is_update_available("1.1.0-beta.1", releases))
        self.assertFalse(scene_hub.is_update_available("2.0.0", releases))

    def test_reads_from_offline_cache(self):
        news_url = "https://itrsr62yvx.zite.so/?news=f88361cd-f63e-4597-b251-f06fb67ef15a"
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "scene_hub_cache.json"
            cache.write_text(json.dumps({
                "schema": 1, "news": [{"url": news_url, "title": "News"}],
                "releases": [],
            }), encoding="utf-8")
            with patch.object(scene_hub, "_fetch", side_effect=OSError("Offline")):
                bundle = scene_hub.fetch_scene_bundle(cache)
            self.assertTrue(bundle["cache_used"])
            self.assertEqual(bundle["news"][0]["title"], "News")

    def test_json_feed_used_without_article_scraping(self):
        feed = {"schema": 1, "news": [{
            "title": "Apollo Save Tool 2.4.0",
            "description": "Resumen",
            "date": "2026-10-17",
            "url": "https://itrsr62yvx.zite.so/?news=f88361cd-f63e-4597-b251-f06fb67ef15a",
        }]}
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"></feed>'''
        def fake_fetch(url, *_args, **_kwargs):
            if url == scene_hub.NEWS_JSON:
                return json.dumps(feed).encode()
            if url == scene_hub.RELEASES_ATOM:
                return atom
            raise AssertionError("No debe consultar el sitemap cuando hay feed")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(scene_hub, "_fetch", side_effect=fake_fetch):
                result = scene_hub.fetch_scene_bundle(Path(d) / "cache.json")
        self.assertEqual(result["news"][0]["title"], "Apollo Save Tool 2.4.0")


if __name__ == "__main__":
    unittest.main()

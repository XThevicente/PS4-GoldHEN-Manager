import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import scene_queue


class SceneQueueTests(unittest.TestCase):
    def example(self):
        return {"id": "download-abc_123", "name": "Apollo Save Tool",
                "url": "https://github.com/bucanero/apollo-ps4/releases",
                "platform": "PS4"}

    def test_generate_pairing_code(self):
        first = scene_queue.create_pairing_code()
        second = scene_queue.create_pairing_code()
        self.assertRegex(first, r"^[A-Za-z0-9_-]{32}$")
        self.assertNotEqual(first, second)

    def test_export_file_imports_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            path.write_text(json.dumps({"app": "GoldHEN Scene Hub",
                                        "version": 1, "items": [self.example()]}), encoding="utf-8")
            result = scene_queue.read_export_file(path)
            self.assertEqual(result[0]["name"], "Apollo Save Tool")

    def test_accept_website_api_shape(self):
        data = json.dumps({"ok": True, "items": [self.example()]}).encode()
        self.assertEqual(scene_queue.parse_queue(data)[0]["platform"], "PS4")

    def test_only_https_public_hosts(self):
        for url in ("http://github.com/a", "https://localhost/a",
                    "https://127.0.0.1/a", "https://10.0.0.5/a",
                    "https://host.example:8080/a", "file:///tmp/a",
                    "https://user:secret@example.org/a"):
            with self.subTest(url=url):
                self.assertFalse(scene_queue.valid_https_link(url))

    def test_malicious_items_are_discarded(self):
        invalid = dict(self.example(), url="https://127.0.0.1/admin")
        large_name = dict(self.example(), id="another", name="x" * 161)
        data = json.dumps({"ok": True, "items": [invalid, large_name, self.example(), self.example()]}).encode()
        self.assertEqual(scene_queue.parse_queue(data), [self.example()])

    def test_file_rejects_oversize(self):
        with self.assertRaises(scene_queue.QueueError):
            scene_queue.parse_queue(b"x" * (scene_queue.MAX_JSON_BYTES + 1))

    def test_api_requires_valid_token_before_calling_network(self):
        with patch("scene_queue.urlopen") as open_url:
            with self.assertRaises(scene_queue.QueueError):
                scene_queue.fetch_account_queue("INVALID")
            open_url.assert_not_called()


if __name__ == "__main__":
    unittest.main()

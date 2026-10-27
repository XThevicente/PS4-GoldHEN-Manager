import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xcloud4_resources import (
    EXPECTED_SHA256, EXPECTED_SIZE, RELEASE_URL, SOURCE_URL,
    XCloud4Mixin, approved_xcloud_link, verify_pkg,
)


class XCloud4Tests(unittest.TestCase):
    def test_links_are_approved_and_fixed(self):
        self.assertEqual(approved_xcloud_link("release"), RELEASE_URL)
        self.assertEqual(approved_xcloud_link("source"), SOURCE_URL)
        with self.assertRaises(ValueError):
            approved_xcloud_link("https://untrusted.invalid/download")

    def test_provenance_matches_published_release(self):
        self.assertEqual(len(EXPECTED_SHA256), 64)
        self.assertEqual(EXPECTED_SIZE, 16646144)
        self.assertTrue(RELEASE_URL.startswith("https://github.com/asp11edgr/XCloud4/releases"))

    def test_mismatched_package_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "suspicious.pkg"
            p.write_bytes(b"test-only")
            valid, digest = verify_pkg(p)
            self.assertFalse(valid)
            self.assertEqual(digest, hashlib.sha256(b"test-only").hexdigest())

    def test_invalid_path_rejected(self):
        with self.assertRaises(FileNotFoundError):
            verify_pkg(Path("__missing___xcloud.pkg"))

    def test_browser_uses_constant_not_dynamic_input(self):
        with patch("xcloud4_resources.webbrowser.open_new_tab") as opened:
            XCloud4Mixin._xcloud_open("release")
            opened.assert_called_once_with(RELEASE_URL)
            with self.assertRaises(ValueError):
                XCloud4Mixin._xcloud_open("bad")
            opened.assert_called_once()


if __name__ == "__main__":
    unittest.main()

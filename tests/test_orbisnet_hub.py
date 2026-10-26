"""OrbisNet links and search: no network and no GUI required."""
import unittest
from unittest.mock import patch

from orbisnet_hub import GAMES, official_link, find_games, OrbisNetMixin


class OrbisNetTests(unittest.TestCase):
    def test_only_fixed_https_official_links(self):
        self.assertEqual(official_link("site"), "https://orbisnet.app/")
        self.assertEqual(official_link("community"), "https://discord.com/invite/y5gRdBP3bH")
        with self.assertRaises(ValueError):
            official_link("https://evil.invalid/")

    def test_search_filter_is_case_insensitive(self):
        self.assertEqual(find_games("TEKKEN"), ("Tekken 7",))
        self.assertEqual(find_games(""), GAMES)
        self.assertEqual(find_games("no-such-game"), ())

    def test_opening_only_uses_approved_browser_destination(self):
        with patch("orbisnet_hub.webbrowser.open_new_tab") as browser:
            OrbisNetMixin._orbisnet_open("site")
            browser.assert_called_once_with("https://orbisnet.app/")
            with self.assertRaises(ValueError):
                OrbisNetMixin._orbisnet_open("evil")
            browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()

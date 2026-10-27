import ftplib
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from xbox_controller_resources import (
    CONFIG_PATH, FILES, PLUGIN_PATH, RELEASE_URL, XboxControllerMixin,
    local_ini_preview, official_link, prepare_plugins_ini, verify_artifact,
)


class XboxControllerTests(unittest.TestCase):
    def test_release_metadata(self):
        self.assertEqual(official_link("release"), RELEASE_URL)
        self.assertEqual(FILES["xbox_controller.prx"][0], 108752)
        self.assertEqual(FILES["xbox_controller_configurator.pkg"][0], 8192000)
        self.assertEqual(len(FILES["xbox_controller.prx"][1]), 64)
        with self.assertRaises(ValueError):
            official_link("https://example.invalid")

    def test_rejects_wrong_prx_file_without_remote_activity(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "xbox_controller.prx"
            path.write_bytes(b"not-the-real-prx")
            with self.assertRaisesRegex(ValueError, "Tamaño incorrecto"):
                verify_artifact(path, "xbox_controller.prx")
            with self.assertRaises(ValueError):
                verify_artifact(path, "unknown.pkg")

    def test_merge_preserves_existing_config_and_newlines(self):
        original = b";My configuration\r\n[default]\r\n/data/GoldHEN/plugins/other.prx\r\n[PLAY00001]\r\n/another.prx\r\n"
        new, changed = prepare_plugins_ini(original)
        self.assertTrue(changed)
        self.assertIn(b"/data/GoldHEN/plugins/other.prx\r\n", new)
        self.assertIn(PLUGIN_PATH.encode() + b"\r\n[PLAY00001]", new)
        self.assertEqual(prepare_plugins_ini(new), (new, False))

    def test_empty_config_and_duplicate_section(self):
        result, modified = prepare_plugins_ini(b"")
        self.assertTrue(modified)
        self.assertEqual(result, b"[default]\n" + PLUGIN_PATH.encode() + b"\n")
        with self.assertRaisesRegex(ValueError, "varias secciones"):
            prepare_plugins_ini(b"[default]\n[default]\n")
        with self.assertRaises(ValueError):
            prepare_plugins_ini(b"\x00")
        with self.assertRaises(ValueError):
            prepare_plugins_ini(b"\xff\xfe")

    def test_offline_backup_and_preview_never_upload(self):
        with tempfile.TemporaryDirectory() as d:
            ftp = Mock()
            ftp.download_file.side_effect = lambda remote, local: local.write_bytes(
                b"[default]\n/data/GoldHEN/plugins/original.prx\n")
            backup, proposed, changed = local_ini_preview(ftp, Path(d) / "plan")
            self.assertTrue(changed)
            self.assertTrue(backup.exists())
            self.assertIn(b"original.prx", backup.read_bytes())
            self.assertIn(PLUGIN_PATH.encode(), proposed.read_bytes())
            ftp.download_file.assert_called_once()
            self.assertEqual(ftp.download_file.call_args.args[0], CONFIG_PATH)
            ftp.upload_file.assert_not_called()

    def test_missing_ini_prepares_only_local_copy(self):
        with tempfile.TemporaryDirectory() as d:
            ftp = Mock()
            ftp.download_file.side_effect = ftplib.error_perm("550 Not found")
            backup, proposed, changed = local_ini_preview(ftp, Path(d) / "plan")
            self.assertIsNone(backup)
            self.assertTrue(changed)
            self.assertIn(PLUGIN_PATH.encode(), proposed.read_bytes())
            ftp.upload_file.assert_not_called()

    def test_safe_ui_links(self):
        with patch("xbox_controller_resources.webbrowser.open_new_tab") as opened:
            XboxControllerMixin._xbox_open("release")
            opened.assert_called_once_with(RELEASE_URL)
            with self.assertRaises(ValueError):
                XboxControllerMixin._xbox_open("not-approved")
            opened.assert_called_once()


if __name__ == "__main__":
    unittest.main()

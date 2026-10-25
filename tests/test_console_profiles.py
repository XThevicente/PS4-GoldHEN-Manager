import unittest

from console_profiles import PS4, PS5, get_profile, profile_from_label


class ConsoleProfileTests(unittest.TestCase):
    def test_ps4_is_only_enabled_transport(self):
        self.assertTrue(PS4.ready)
        self.assertEqual(PS4.transport, "goldhen_ftp")
        self.assertFalse(PS5.ready)
        self.assertIsNone(PS5.transport)

    def test_ps5_is_explicitly_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "vista previa"):
            PS5.require("instalar PKG")
        PS4.require("transferir archivos")

    def test_corrupt_preference_does_not_enable_ps5(self):
        self.assertIs(get_profile("???"), PS4)
        self.assertIs(profile_from_label(PS5.label), PS5)


if __name__ == "__main__":
    unittest.main()

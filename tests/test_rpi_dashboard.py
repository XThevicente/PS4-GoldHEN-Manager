"""Never construct a public or executable URL from the PS4 host setting."""
import unittest
from unittest.mock import Mock, patch

from rpi_dashboard import (
    RPI_DASHBOARD_PORT, RPI_RELEASES_URL,
    RPIDashboardMixin, local_rpi_url, private_ps4_address,
)


class URLValidationTests(unittest.TestCase):
    def test_private_ipv4_works(self):
        for ip in ("192.168.1.25", "10.30.0.5", "172.19.2.9"):
            with self.subTest(ip=ip):
                self.assertEqual(private_ps4_address(ip), ip)
        self.assertEqual(local_rpi_url(" 192.168.1.25 "), "http://192.168.1.25:12801/")
        self.assertEqual(local_rpi_url("192.168.1.25", log=True),
                         "http://192.168.1.25:12801/api/log")
        self.assertEqual(RPI_DASHBOARD_PORT, 12801)
        self.assertIn("codeberg.org", RPI_RELEASES_URL)

    def test_rejects_public_or_malicious_host(self):
        for host in (
            "8.8.8.8", "127.0.0.1", "169.254.11.7", "0.0.0.0",
            "192.0.2.1", "localhost", "ps4.local", "192.168.1.3:1234",
            "192.168.1.3/secret", "192.168.1.3#test",
            "192.168.1.3@attacker.invalid", "[::1]", "::ffff:192.168.1.3",
            "999.999.999.999", ""
        ):
            with self.subTest(host=host), self.assertRaises(ValueError):
                local_rpi_url(host)

    def test_disabled_on_user_cancellation(self):
        class App(RPIDashboardMixin):
            host_var = Mock()
        app = App()
        app.host_var.get.return_value = "192.168.1.25"
        with patch("rpi_dashboard.messagebox") as dialogs, patch(
            "rpi_dashboard.webbrowser.open_new_tab") as open_tab:
            dialogs.askyesno.return_value = False
            app.open_rpi_dashboard()
            open_tab.assert_not_called()
            dialogs.askyesno.return_value = True
            app.open_rpi_diagnostics()
            open_tab.assert_called_once_with("http://192.168.1.25:12801/api/log")

    def test_rejected_host_never_opens_browser(self):
        class App(RPIDashboardMixin):
            host_var = Mock()
        app = App()
        app.host_var.get.return_value = "https://evil.example"
        with patch("rpi_dashboard.messagebox") as dialogs, patch(
            "rpi_dashboard.webbrowser.open_new_tab") as open_tab:
            app.open_rpi_dashboard()
            dialogs.showwarning.assert_called_once()
            open_tab.assert_not_called()


if __name__ == "__main__":
    unittest.main()

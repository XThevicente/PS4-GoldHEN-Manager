"""Connection flows with real Tcl variables, mocked LAN services and no PS4 required."""
from __future__ import annotations

import os
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

# Importing the application creates its settings directories. Keep them isolated.
with tempfile.TemporaryDirectory() as settings_dir:
    with patch.dict(os.environ, {"APPDATA": settings_dir}):
        import ps4_manager as manager


HOST = "192.168.1.44"
PORTS = (manager.FTP_PORT, manager.BINLOADER_PORT, manager.KLOG_PORT,
         manager.PS4DEBUG_PORT, manager.RPI_PORT)


class HeadlessApp:
    """Run the actual application callbacks without creating a graphical window."""

    _selected_ftp_port = manager.PS4ManagerApp._selected_ftp_port
    _update_connection_summary = manager.PS4ManagerApp._update_connection_summary
    _startup_auto_connect = manager.PS4ManagerApp._startup_auto_connect
    _apply_service_state = manager.PS4ManagerApp._apply_service_state
    scan_ps4 = manager.PS4ManagerApp.scan_ps4
    connect_ps4 = manager.PS4ManagerApp.connect_ps4
    disconnect_ps4 = manager.PS4ManagerApp.disconnect_ps4
    refresh_services = manager.PS4ManagerApp.refresh_services
    require_connection = manager.PS4ManagerApp.require_connection

    def __init__(self, interpreter):
        def var(value=""):
            return tk.StringVar(master=interpreter, value=value)

        self.host_var = var(HOST)
        self.port_var = var(str(manager.FTP_PORT))
        self.status_var = var()
        self.connection_var = var()
        self.rpi_status_var = var()
        self.service_vars = {p: var() for p in PORTS}
        self.service_state = {p: False for p in PORTS}
        self.notify_connect_var = tk.BooleanVar(master=interpreter, value=False)
        self.detected_host = ""
        self._connection_epoch = 0
        self._ftp_connecting = False
        self.ftp_connect_button = Mock()
        self.remote_tree = Mock()
        self.remote_tree.get_children.return_value = ("old",)
        self.remote_items = {"old": object()}
        self.refresh_remote = Mock()
        self.stop_klog = Mock()
        self.post_status = self.status_var.set
        self.saved = {}
        self.settings = Mock(spec=manager.Settings)
        self.settings.get.side_effect = self.saved.get
        self.settings.set.side_effect = self.saved.__setitem__
        self.ftp = Mock(spec=manager.FTPClient)
        self.ftp.connected = False
        self.ftp.host = ""
        self.ftp.port = manager.FTP_PORT

        def connect(host, port):
            self.ftp.connected = True
            self.ftp.host, self.ftp.port = host, port
            return "FTP ready"

        self.ftp.connect.side_effect = connect
        self.ftp.disconnect.side_effect = lambda: setattr(self.ftp, "connected", False)

    def run_async(self, func, *, on_done=None, on_error=None):
        try:
            result = func()
        except Exception as exc:
            if on_error is None:
                raise
            on_error(exc)
        else:
            if on_done:
                on_done(result)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.app = HeadlessApp(tk.Tcl())
        self.open_ports = set()
        self.probe = self.enterContext(patch.object(
            manager, "can_connect", side_effect=lambda host, port, *_: (
                host == HOST and port in self.open_ports)))
        self.enterContext(patch.object(manager, "local_scan_prefixes", return_value=["192.168.1"]))
        self.enterContext(patch.object(manager.PS4DebugClient, "version", return_value="test"))
        self.rpi = self.enterContext(patch.object(manager.RemotePackageInstallerClient, "ready", return_value=True))
        self.dialogs = self.enterContext(patch.object(manager, "messagebox"))

    def test_each_non_ftp_service_detects_console_without_ftp_login(self):
        for port in PORTS[1:]:
            with self.subTest(port=port):
                self.app = HeadlessApp(tk.Tcl())
                self.open_ports = {port}
                self.app.scan_ps4()
                self.assertEqual(self.app.detected_host, HOST)
                self.assertTrue(self.app.service_state[port])
                self.assertFalse(self.app.service_state[manager.FTP_PORT])
                self.app.ftp.connect.assert_not_called()
                self.assertIn("PS4 detectada", self.app.connection_var.get())
                self.assertIn("FTP no disponible", self.app.connection_var.get())
                self.assertIn("Actívalo en GoldHEN", self.app.status_var.get())
                self.app.ftp_connect_button.configure.assert_called_with(text="Reintentar FTP", state="normal")
        self.dialogs.showerror.assert_not_called()

    def test_lan_fallback_finds_console_after_its_ip_changes(self):
        self.app.host_var.set("192.168.1.99")
        self.open_ports = {manager.BINLOADER_PORT}
        self.app.scan_ps4()
        self.assertEqual(self.app.host_var.get(), HOST)
        self.assertEqual(self.app.saved["host"], HOST)
        self.assertIn("PS4 detectada", self.app.connection_var.get())
        self.app.ftp.connect.assert_not_called()

    def test_ftp_can_be_enabled_and_retried_without_scanning_again(self):
        self.open_ports = {manager.BINLOADER_PORT}
        self.app.scan_ps4()
        self.open_ports.add(manager.FTP_PORT)
        self.app.connect_ps4()
        self.app.ftp.connect.assert_called_once_with(HOST, manager.FTP_PORT)
        self.app.refresh_remote.assert_called_once()
        self.assertIn("FTP conectado", self.app.connection_var.get())
        self.assertTrue(self.app.service_state[manager.FTP_PORT])
        self.assertEqual(self.app.saved["ftp_port"], manager.FTP_PORT)
        self.dialogs.showerror.assert_not_called()

    def test_ftp_discovery_still_connects_automatically(self):
        self.open_ports = {manager.FTP_PORT}
        self.app.scan_ps4()
        self.app.ftp.connect.assert_called_once_with(HOST, manager.FTP_PORT)
        self.assertIn("FTP conectado", self.app.connection_var.get())

    def test_custom_ftp_port_is_preserved_through_startup_and_service_checks(self):
        self.app.port_var.set("2122")
        self.open_ports = {2122}
        self.app._startup_auto_connect()
        self.app.ftp.connect.assert_called_once_with(HOST, 2122)
        self.assertEqual(self.app.port_var.get(), "2122")
        self.assertTrue(self.app.service_state[manager.FTP_PORT])
        self.assertEqual(self.app.service_vars[manager.FTP_PORT].get(), "● FTP :2122")

    def test_manual_ftp_failure_keeps_other_services_available(self):
        self.open_ports = {manager.BINLOADER_PORT, manager.KLOG_PORT}
        self.app.ftp.connect.side_effect = OSError("connection refused")
        self.app.connect_ps4()
        self.assertIn("PS4 detectada", self.app.connection_var.get())
        self.assertIn("FTP no disponible", self.app.status_var.get())
        self.assertTrue(self.app.service_state[manager.KLOG_PORT])
        self.assertFalse(self.app._ftp_connecting)
        self.app.remote_tree.delete.assert_called_once_with("old")
        self.assertEqual(self.app.remote_items, {})
        self.dialogs.showerror.assert_not_called()

    def test_ftp_login_error_remains_visible_when_port_is_open(self):
        self.open_ports = {manager.FTP_PORT, manager.KLOG_PORT}
        self.app.ftp.connect.side_effect = manager.ftplib.error_perm("530 Login refused")
        self.app.scan_ps4()
        self.assertIn("PS4 detectada", self.app.connection_var.get())
        self.assertIn("FTP sin conectar", self.app.connection_var.get())
        self.assertIn("530 Login refused", self.dialogs.showerror.call_args.args[1])
        self.app.ftp.connect.assert_called_once()  # No automatic retry loop.

    def test_manual_connection_to_unreachable_console_reports_ftp_error(self):
        self.app.ftp.connect.side_effect = OSError("timed out")
        self.app.connect_ps4()
        self.assertEqual(self.app.detected_host, "")
        self.assertIn("sin respuesta", self.app.connection_var.get())
        self.assertIn("timed out", self.dialogs.showerror.call_args.args[1])

    def test_no_services_does_not_invent_a_detected_console(self):
        self.app.scan_ps4()
        self.assertEqual(self.app.detected_host, "")
        self.assertIn("No se encontró", self.app.status_var.get())
        self.app.ftp.connect.assert_not_called()
        self.dialogs.showinfo.assert_called_once()

    def test_open_but_unready_rpi_keeps_its_distinct_status(self):
        self.open_ports = {manager.RPI_PORT}
        self.rpi.return_value = False
        self.app.scan_ps4()
        self.assertEqual(self.app.detected_host, HOST)
        self.assertFalse(self.app.service_state[manager.RPI_PORT])
        self.assertIn("abierto pero no responde", self.app.rpi_status_var.get())
        self.app.ftp.connect.assert_not_called()

    def test_file_operations_still_require_ftp_and_explain_how_to_retry(self):
        self.open_ports = {manager.BINLOADER_PORT}
        self.app.scan_ps4()
        self.assertFalse(self.app.require_connection())
        self.assertIn("Reintentar FTP", self.dialogs.showwarning.call_args.args[1])
        self.open_ports.add(manager.FTP_PORT)
        self.app.connect_ps4()
        self.assertTrue(self.app.require_connection())

    def test_invalid_ftp_port_does_not_start_a_network_operation(self):
        for value in ("abc", "0", "65536", "-1"):
            with self.subTest(value=value):
                self.app.port_var.set(value)
                self.app.connect_ps4()
                self.app.scan_ps4()
                self.app.refresh_services()
        self.probe.assert_not_called()
        self.app.ftp.connect.assert_not_called()

    def test_service_result_is_ignored_after_disconnecting_or_switching_host(self):
        for action in ("disconnect", "switch_host"):
            with self.subTest(action=action):
                self.app = HeadlessApp(tk.Tcl())
                self.open_ports = {manager.BINLOADER_PORT}
                pending = []
                self.app.run_async = lambda func, **callbacks: pending.append((func(), callbacks))
                self.app.refresh_services(connect_ftp=True)
                if action == "disconnect":
                    self.app.disconnect_ps4()
                else:
                    self.app.host_var.set("192.168.1.55")
                result, callbacks = pending.pop()
                callbacks["on_done"](result)
                self.assertFalse(any(self.app.service_state.values()))
                self.assertEqual(self.app.detected_host, "")
                self.app.ftp.connect.assert_not_called()

    def test_late_ftp_success_cannot_reconnect_after_disconnect(self):
        pending = []
        self.app.run_async = lambda func, **callbacks: pending.append((func, callbacks))
        self.app.connect_ps4()
        self.app.disconnect_ps4()
        func, callbacks = pending.pop()
        callbacks["on_done"](func())
        self.assertFalse(self.app.ftp.connected)
        self.assertFalse(self.app._ftp_connecting)
        self.app.refresh_remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()

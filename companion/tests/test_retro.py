import json
import os
import sys
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retro_manager import RetroManager, validate_emulator
from companion_server import CompanionService, safe_text

class RetroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"; self.data.mkdir()
        self.roms = self.root / "roms"; self.roms.mkdir()
        (self.roms / "nes").mkdir()
        for i in range(8): (self.roms / "nes" / f"Game {i}.nes").write_bytes(b"fixture")
        (self.roms / "nes" / "ignored.txt").write_text("fixture")
        self.retro = RetroManager(self.data)
        self.retro.set_root(self.roms)
        self.retro.scan()

    def test_catalog_paging_and_stable_ids(self):
        self.assertEqual(self.retro.page()["total"], 8)
        self.assertEqual(len(self.retro.page()["items"]), 6)
        self.assertEqual(len(self.retro.page(6)["items"]), 2)
        ids = [e["id"] for e in self.retro.entries]
        self.retro.scan()
        self.assertEqual(ids, [e["id"] for e in self.retro.entries])
        self.assertEqual(self.retro.page(system="snes")["total"], 0)
        self.assertNotIn("path", self.retro.page()["items"][0])

    def test_symlink_not_indexed(self):
        try: (self.roms / "nes" / "outside.nes").symlink_to(self.root / "secret.nes")
        except OSError: self.skipTest("symlinks unavailable")
        (self.root / "secret.nes").write_bytes(b"private")
        self.assertEqual(self.retro.scan(), 8)

    def test_config_persists_and_missing_emulator_rejected(self):
        with self.assertRaises(ValueError): self.retro.launch(self.retro.entries[0]["id"], "test")
        self.retro.configure_emulator("nes", sys.executable, ["{rom}"])
        other = RetroManager(self.data)
        self.assertEqual(other.config["root"], str(self.roms.resolve()))
        self.assertIn("nes", other.config["emulators"])

    def test_launch_exact_arguments_and_idempotence(self):
        self.retro.configure_emulator("nes", sys.executable, ["--rom", "{rom}", "literal & arg"])
        rom = self.retro.entries[0]
        with patch("retro_manager.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = None
            first = self.retro.launch(rom["id"], "unique")
            self.assertEqual(self.retro.launch(rom["id"], "unique"), first)
            self.assertEqual(spawn.call_count, 1)
            self.assertEqual(spawn.call_args.args[0][-2:], [rom["path"], "literal & arg"])
            self.assertFalse(spawn.call_args.kwargs["shell"])
            with self.assertRaises(ValueError): self.retro.launch(rom["id"], "another")
            self.retro.stop()
            spawn.return_value.terminate.assert_called_once()

    def test_http_auth_library_and_old_command_protocol(self):
        with patch("companion_server.app_data_dir", return_value=self.data):
            service = CompanionService("127.0.0.1", 0, 0)
        service.retro = self.retro
        service.state.token = "test-token"
        service._http = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(("127.0.0.1", 0), service._handler())
        import threading
        thread = threading.Thread(target=service._http.serve_forever, daemon=True); thread.start()
        self.addCleanup(service._http.server_close)
        self.addCleanup(service._http.shutdown)
        base = f"http://127.0.0.1:{service._http.server_port}"
        def get(path, auth=True):
            req = urllib.request.Request(base + path)
            if auth: req.add_header("Authorization", "Bearer test-token")
            with urllib.request.urlopen(req, timeout=2) as response: return json.load(response)
        with self.assertRaises(urllib.error.HTTPError) as exc: get("/api/v1/retro/library", False)
        self.assertEqual(exc.exception.code, 401)
        self.assertEqual(get("/api/v1/retro/library")["total"], 8)
        self.assertTrue(service.is_online())
        with self.assertRaises(urllib.error.HTTPError): get("/api/v1/retro/launch?id=arbitrary&request=x")
        command = service.queue_command("message", text="Test")
        self.assertEqual(get("/api/v1/poll")["command"]["id"], command["id"])
        with self.assertRaises(ValueError): service.queue_command("ping")
        self.assertTrue(get(f"/api/v1/ack?id={command['id']}&result=shown")["ok"])
        self.assertIsNone(service.state.pending_command)
        fid, _ = service.register_file("test.txt", b"test")
        req = urllib.request.Request(base + f"/api/v1/file?id={fid}", headers={"Authorization": "Bearer test-token"})
        with urllib.request.urlopen(req) as response: self.assertEqual(response.read(), b"test")

    def test_actual_local_process_lifecycle(self):
        import time
        marker = self.root / "launched.txt"
        script = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); time.sleep(30)"
        self.retro.configure_emulator("nes", sys.executable, ["-c", script, str(marker), "{rom}"])
        self.addCleanup(self.retro.stop)
        selected = self.retro.entries[0]
        self.retro.launch(selected["id"], "actual-process")
        deadline = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < deadline: time.sleep(0.02)
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(), selected["path"])
        self.assertTrue(self.retro.status()["running"])
        self.retro.stop()
        self.assertFalse(self.retro.status()["running"])

    def test_rom_is_rejected_as_emulator(self):
        with self.assertRaisesRegex(ValueError, "Has seleccionado un juego"):
            self.retro.configure_emulator("nes", self.retro.entries[0]["path"], ["{rom}"])
        self.assertNotIn("nes", self.retro.config["emulators"])

    def test_legacy_profile_is_validated_before_launch(self):
        entry = self.retro.entries[0]
        self.retro.config["emulators"]["nes"] = {"executable": entry["path"], "arguments": ["{rom}"]}
        with patch("retro_manager.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "Has seleccionado un juego"):
                self.retro.launch(entry["id"], "bad-profile")
            spawn.assert_not_called()

    def test_dll_and_archive_are_rejected(self):
        for name, message in [("core.dll", "core o biblioteca"), ("download.zip", "Extrae la descarga")]:
            file = self.root / name; file.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, message): validate_emulator(file)

    def test_renamed_rom_and_truncated_exe_are_rejected(self):
        fake = self.root / "renamed.exe"; fake.write_bytes(b"NES\x1a" + bytes(64))
        with self.assertRaisesRegex(ValueError, "cabecera EXE"): validate_emulator(fake, platform_name="nt")
        header = bytearray(64);header[:2] = b"MZ";header[60:64] = (1024).to_bytes(4, "little")
        fake.write_bytes(header)
        with self.assertRaisesRegex(ValueError, "incompleto"): validate_emulator(fake, platform_name="nt")

    def test_pe_validation_rejects_dll_characteristic(self):
        import struct
        fake = self.root / "header.exe"
        data = bytearray(128);data[:2] = b"MZ";struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\x00\x00";struct.pack_into("<H", data, 84, 2)
        struct.pack_into("<H", data, 86, 0x0002);struct.pack_into("<H", data, 88, 0x20b)
        fake.write_bytes(data)
        self.assertEqual(validate_emulator(fake, platform_name="nt"), fake.resolve())
        struct.pack_into("<H", data, 86, 0x2002);fake.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "una DLL"): validate_emulator(fake, platform_name="nt")

    def test_windows_launch_error_has_actionable_message(self):
        self.retro.configure_emulator("nes", sys.executable, ["{rom}"])
        error = OSError("bad exe");error.winerror = 193
        with patch("retro_manager.subprocess.Popen", side_effect=error):
            with self.assertRaisesRegex(ValueError, "EXE compatible con tu PC"):
                self.retro.launch(self.retro.entries[0]["id"], "bad-exe")
        self.assertFalse(self.retro.status()["running"])

    def test_ps4_text_keeps_words_and_parentheses(self):
        self.assertEqual(safe_text("aplicación válida (DEMO)"), "aplicacion valida (DEMO)")

if __name__ == "__main__": unittest.main()

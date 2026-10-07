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
from retro_manager import RetroManager
from companion_server import CompanionService

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

if __name__ == "__main__": unittest.main()

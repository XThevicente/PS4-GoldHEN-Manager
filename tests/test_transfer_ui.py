"""Headless integration of the new PS4 file-transfer UI and its FIFO runner."""
from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from console_profiles import PS4, PS5
from transfer_queue import TransferQueue
from transfer_ui import TransferMixin


class FakeApp(TransferMixin):
    def __init__(self):
        self.active_profile = PS4
        self.ftp = Mock()
        self.ftp.connected = True
        self.transfers = TransferQueue()
        self._transfer_runners = {}
        self._last_transfer_redraw = 0.0
        self.ui_queue = queue.Queue()
        self.status_var = Mock()
        self.post_progress = Mock()
        self.refresh_remote = Mock()
        self.refresh_local = Mock()
        self._refresh_transfer_view = Mock()
        self.pending = []
        self.require_connection = Mock(return_value=True)
        self.remote_items = {}
        self.remote_tree = Mock()
        self.remote_tree.selection.return_value = ()

    def run_async(self, func, *, on_done=None, on_error=None):
        self.pending.append((func, on_done, on_error))

    def work_one(self):
        func, done, error = self.pending.pop(0)
        try:
            result = func()
        except Exception as exc:
            error(exc)
        else:
            done(result)


class TransferFlowTests(unittest.TestCase):
    def test_queued_uploads_run_serially(self):
        app = FakeApp()
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "first.txt"
            b = Path(tmp) / "second.txt"
            a.write_bytes(b"abcd")
            b.write_bytes(b"efgh")

            def fake_upload(local, remote, cb):
                cb(local.stat().st_size, local.stat().st_size)

            app.ftp.upload_file.side_effect = fake_upload
            app.queue_upload_paths([a, b], "/data")
            self.assertEqual(len(app.pending), 1)
            self.assertEqual([j.state for j in app.transfers.snapshot()],
                             ["running", "pending"])
            app.work_one()
            self.assertEqual([j.state for j in app.transfers.snapshot()],
                             ["completed", "running"])
            app.work_one()
            self.assertEqual([j.state for j in app.transfers.snapshot()],
                             ["completed", "completed"])
            self.assertEqual(app.ftp.upload_file.call_count, 2)

    def test_cancel_pending_keeps_active_upload(self):
        app = FakeApp()
        with tempfile.TemporaryDirectory() as tmp:
            files = [Path(tmp) / str(i) for i in range(2)]
            for path in files:
                path.write_bytes(b"x")
            app.queue_upload_paths(files, "/data")
            app._cancel_pending_transfers()
            self.assertEqual([j.state for j in app.transfers.snapshot()],
                             ["running", "cancelled"])
            app.work_one()
            self.assertEqual(app.ftp.upload_file.call_count, 1)

    def test_ps5_never_runs_ps4_transfer(self):
        app = FakeApp()
        app.active_profile = PS5
        with self.assertRaisesRegex(RuntimeError, "vista previa"):
            app.queue_upload_paths([Path("nonexistent")], "/data")
        app.ftp.upload_file.assert_not_called()

    def test_ftp_failure_is_visible_and_queue_continues(self):
        app = FakeApp()
        with tempfile.TemporaryDirectory() as tmp:
            files = [Path(tmp) / str(i) for i in range(2)]
            for path in files:
                path.write_bytes(b"x")
            app.queue_upload_paths(files, "/data")
            app.ftp.upload_file.side_effect = [OSError("FTP disconnected"), None]
            app.work_one()
            self.assertEqual([j.state for j in app.transfers.snapshot()],
                             ["failed", "running"])
            app.work_one()
            self.assertEqual(app.transfers.snapshot()[1].state, "completed")
            self.assertIn("FTP disconnected", app.transfers.snapshot()[0].error)

    def test_remote_path_traversal_is_rejected(self):
        app = FakeApp()
        with tempfile.TemporaryDirectory() as tmp:
            app.local_path = Path(tmp)
            app.remote_tree.selection.return_value = ("item",)
            app.remote_items = {"item": SimpleNamespace(
                name="../escape", path="/user/escape", is_dir=False)}
            with patch("transfer_ui.messagebox") as dialogs:
                app.queue_download_selected()
            dialogs.showerror.assert_called_once()
            self.assertEqual(app.transfers.snapshot(), ())


if __name__ == "__main__":
    unittest.main()

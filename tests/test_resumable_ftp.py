"""Synthetic FTP storage tests — no physical PS4 or network required."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from resumable_ftp import ResumableFTP, TransferIntegrityError


class MemoryFTP:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.fail_after = None

    def ensure_dir(self, directory):
        pass

    def remote_size(self, path):
        return len(self.data[path]) if path in self.data else None

    def remote_sha256(self, path):
        self.calls.append(("sha256", path))
        return hashlib.sha256(self.data[path]).hexdigest()

    def rename(self, original, target):
        if target in self.data:
            raise FileExistsError(target)
        self.data[target] = self.data.pop(original)

    def upload_file(self, local, remote, callback, *, rest=0):
        self.calls.append(("upload", rest))
        blob = local.read_bytes()
        if rest and remote not in self.data:
            raise RuntimeError("REST con archivo inexistente")
        if rest:
            assert self.data[remote][:rest] == blob[:rest]
        if self.fail_after is not None:
            self.data[remote] = blob[:rest + self.fail_after]
            callback(self.fail_after, len(blob))
            raise ConnectionError("La red se cayó")
        self.data[remote] = blob
        callback(len(blob) - rest, len(blob))

    def download_file(self, remote, local, callback, *, rest=0):
        self.calls.append(("download", rest))
        blob = self.data[remote]
        if self.fail_after is not None:
            piece = blob[rest:rest + self.fail_after]
            with local.open("ab" if rest else "wb") as fd:
                fd.write(piece)
            callback(rest + len(piece), len(blob))
            raise ConnectionError("La red se cayó")
        with local.open("ab" if rest else "wb") as fd:
            fd.write(blob[rest:])
        callback(len(blob), len(blob))


class FTPTransferTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.local = Path(self.root.name) / "hello.bin"
        self.local.write_bytes(b"good-data-" * 2000)
        self.ftp = MemoryFTP()
        self.engine = ResumableFTP(self.ftp)
        self.events = []

    def progress(self, done, total, phase):
        self.events.append((done, total, phase))

    def test_resume_upload_verifies_prefix_full_hash_and_atomic_rename(self):
        self.ftp.fail_after = 1000
        with self.assertRaises(ConnectionError):
            self.engine.upload(self.local, "/data/hello.bin", self.progress)
        self.assertNotIn("/data/hello.bin", self.ftp.data)
        self.ftp.fail_after = None
        self.engine.upload(self.local, "/data/hello.bin", self.progress)
        self.assertEqual(self.ftp.data["/data/hello.bin"], self.local.read_bytes())
        self.assertIn(("upload", 1000), self.ftp.calls)
        self.assertNotIn("/data/hello.bin.goldhen.part", self.ftp.data)
        self.assertTrue(any("SHA-256" in x[2] for x in self.events))

    def test_changed_local_prefix_refused(self):
        self.ftp.data["/data/hello.bin.goldhen.part"] = b"bad-data"
        with self.assertRaisesRegex(TransferIntegrityError, "no coincide"):
            self.engine.upload(self.local, "/data/hello.bin", self.progress)
        self.assertNotIn("/data/hello.bin", self.ftp.data)

    def test_never_overwrite_existing_remote_file(self):
        self.ftp.data["/data/hello.bin"] = b"KEEP"
        with self.assertRaises(FileExistsError):
            self.engine.upload(self.local, "/data/hello.bin", self.progress)
        self.assertEqual(self.ftp.data["/data/hello.bin"], b"KEEP")

    def test_resume_download_with_full_hash(self):
        self.ftp.data["/user/test.bin"] = self.local.read_bytes()
        output = Path(self.root.name) / "got.bin"
        self.ftp.fail_after = 750
        with self.assertRaises(ConnectionError):
            self.engine.download("/user/test.bin", output, self.progress)
        self.assertFalse(output.exists())
        self.ftp.fail_after = None
        self.engine.download("/user/test.bin", output, self.progress)
        self.assertEqual(output.read_bytes(), self.local.read_bytes())
        self.assertIn(("download", 750), self.ftp.calls)
        self.assertFalse(output.with_name("got.bin.goldhen.part").exists())

    def test_detects_corrupted_remote_after_download_resume(self):
        self.ftp.data["/user/test.bin"] = b"AAAA"
        output = Path(self.root.name) / "got.bin"
        output.with_name("got.bin.goldhen.part").write_bytes(b"BB")
        with self.assertRaisesRegex(TransferIntegrityError, "SHA-256"):
            self.engine.download("/user/test.bin", output, self.progress)
        self.assertFalse(output.exists())

    def test_detects_corrupted_remote_upload_and_preserves_partial(self):
        class CorruptingFTP(MemoryFTP):
            def upload_file(self, local, remote, callback, *, rest=0):
                super().upload_file(local, remote, callback, rest=rest)
                self.data[remote] = b"X" * len(self.data[remote])
        self.ftp = CorruptingFTP()
        engine = ResumableFTP(self.ftp)
        with self.assertRaisesRegex(TransferIntegrityError, "SHA-256"):
            engine.upload(self.local, "/data/hello.bin", self.progress)
        self.assertNotIn("/data/hello.bin", self.ftp.data)
        self.assertIn("/data/hello.bin.goldhen.part", self.ftp.data)

    def test_zero_byte_file_and_size_only_mode(self):
        self.local.write_bytes(b"")
        engine = ResumableFTP(self.ftp, verify_sha256=False)
        engine.upload(self.local, "/data/empty.bin", self.progress)
        self.assertEqual(self.ftp.data["/data/empty.bin"], b"")


if __name__ == "__main__":
    unittest.main()

import threading
import unittest

from transfer_queue import TransferQueue


class TransferQueueTests(unittest.TestCase):
    def test_fifo_one_active_job_and_terminal_status(self):
        q = TransferQueue()
        first = q.add("upload", "photo.png", "/data")
        second = q.add("download", "/user/backup.zip", "C:/Backup")
        self.assertEqual(q.start_next().id, first.id)
        self.assertIsNone(q.start_next())
        q.progress(first.id, 40, 100, "photo.png")
        self.assertEqual(q.snapshot()[0].percent, 40)
        self.assertEqual(q.finish(first.id).state, "completed")
        self.assertEqual(q.start_next().id, second.id)
        self.assertEqual(q.finish(second.id, error="FTP disconnected").state, "failed")
        self.assertIsNone(q.start_next())

    def test_pending_cancellation_never_interrupts_active_job(self):
        q = TransferQueue()
        active = q.add("upload", "a", "/data")
        pending = q.add("upload", "b", "/data")
        self.assertEqual(q.start_next().id, active.id)
        self.assertEqual(q.cancel_pending(), 1)
        self.assertEqual(q.snapshot()[1].state, "cancelled")
        q.finish(active.id)
        self.assertIsNone(q.start_next())
        with self.assertRaises(ValueError):
            q.finish(pending.id)

    def test_validation_and_progress_bounds(self):
        q = TransferQueue()
        with self.assertRaises(ValueError):
            q.add("unknown", "x", "y")
        with self.assertRaises(ValueError):
            q.add("upload", "", "y")
        j = q.add("download", "x", "y")
        q.start_next()
        q.progress(j.id, 999, 100, "download")
        self.assertEqual(q.snapshot()[0].percent, 100)

    def test_concurrent_adds_receive_unique_identifiers(self):
        q = TransferQueue()
        threads = [threading.Thread(target=q.add, args=("upload", f"source{i}", "/data"))
                   for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len({j.id for j in q.snapshot()}), 30)


if __name__ == "__main__":
    unittest.main()

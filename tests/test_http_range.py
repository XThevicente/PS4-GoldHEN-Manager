import concurrent.futures
import http.client
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from http_range import UnsatisfiableRange, byte_range, effective_range
from ps4_manager import RangePackageServer


class RangeMathTests(unittest.TestCase):
    def test_inclusive_forward_open_ended_and_suffix_ranges(self):
        self.assertIsNone(byte_range(None, 100))
        self.assertEqual(byte_range("bytes=0-0", 100), (0, 0))
        self.assertEqual(byte_range("bytes=5-19", 100), (5, 19))
        self.assertEqual(byte_range("bytes=7-", 100), (7, 99))
        self.assertEqual(byte_range("bytes=95-200", 100), (95, 99))
        self.assertEqual(byte_range("bytes=-5", 100), (95, 99))
        self.assertEqual(byte_range("bytes=-300", 100), (0, 99))

    def test_malformed_or_unsatisfiable_ranges(self):
        for header in ("bytes=-", "bytes=-0", "bytes=100-",
                       "bytes=10-9", "bytes=0-1,4-5",
                       "items=1-2", "bytes=01-9999999999999999999999"):
            with self.subTest(header=header), self.assertRaises(UnsatisfiableRange):
                byte_range(header, 100)
        with self.assertRaises(UnsatisfiableRange):
            byte_range("bytes=0-", 0)

    def test_if_range_mismatch_falls_back_to_full_response(self):
        self.assertEqual(effective_range("bytes=1-3", '"known"', 10, '"known"'), (1, 3))
        self.assertIsNone(effective_range("bytes=1-3", '"outdated"', 10, '"known"'))
        self.assertIsNone(effective_range("bytes=1-3", "Wed, 21 Oct 2015 07:28:00 GMT", 10, '"known"'))


class RangeServerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.content = bytes(i % 256 for i in range(8193))
        self.pkg = Path(self.temp.name) / "homebrew.pkg"
        self.pkg.write_bytes(self.content)
        self.server = RangePackageServer()
        self.server.start("127.0.0.1", 0)
        self.addCleanup(self.server.stop)
        self.url = self.server.register(self.pkg)

    def request(self, method="GET", headers=None, url=None):
        req = urllib.request.Request(url or self.url, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_get_full_and_head(self):
        status, headers, payload = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(payload, self.content)
        self.assertEqual(int(headers["Content-Length"]), len(self.content))
        self.assertTrue(headers.get("ETag", "").startswith('"'))
        status, headers, payload = self.request("HEAD", {"Range": "bytes=5-9"})
        self.assertEqual(status, 200)
        self.assertEqual(payload, b"")
        self.assertEqual(int(headers["Content-Length"]), len(self.content))

    def test_range_and_if_range(self):
        status, headers, payload = self.request("GET", {"Range": "bytes=10-29"})
        self.assertEqual((status, payload), (206, self.content[10:30]))
        self.assertEqual(headers["Content-Range"], f"bytes 10-29/{len(self.content)}")
        status, headers, payload = self.request("GET", {"Range": "bytes=-3"})
        self.assertEqual((status, payload), (206, self.content[-3:]))
        etag = headers["ETag"]
        status, _, payload = self.request("GET", {
            "Range": "bytes=2-3", "If-Range": etag})
        self.assertEqual((status, payload), (206, self.content[2:4]))
        status, _, payload = self.request("GET", {
            "Range": "bytes=2-3", "If-Range": '"old-etag"'})
        self.assertEqual((status, payload), (200, self.content))

    def test_unregistered_and_invalid_ranges(self):
        for header in ("bytes=-", "bytes=-0", "bytes=8193-",
                       "bytes=0-2,4-5", "bytes=10-5"):
            with self.subTest(header=header):
                code, headers, body = self.request(headers={"Range": header})
                self.assertEqual(code, 416)
                self.assertEqual(headers["Content-Range"],
                                 f"bytes */{len(self.content)}")
                self.assertEqual(body, b"")
        code, _, _ = self.request(url=self.url + "/not-registered")
        self.assertEqual(code, 404)

    def test_two_readers_at_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(self.request, "GET", {"Range": "bytes=0-4095"})
                     for _ in range(2)]
            for result in tasks:
                code, _, body = result.result(timeout=8)
                self.assertEqual((code, body), (206, self.content[:4096]))

    def test_zero_byte_pkg_and_no_reuse_after_stop(self):
        empty = Path(self.temp.name) / "empty.pkg"
        empty.write_bytes(b"")
        url = self.server.register(empty)
        code, _, body = self.request(url=url)
        self.assertEqual((code, body), (200, b""))
        code, _, _ = self.request(headers={"Range": "bytes=0-"}, url=url)
        self.assertEqual(code, 416)


if __name__ == "__main__":
    unittest.main()

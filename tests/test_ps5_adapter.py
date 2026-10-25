import unittest
from ps5_adapter import PS5Transport, PS5State


class PS5AdapterTests(unittest.TestCase):
    def test_ps5_requires_real_authenticated_adapter(self):
        adapter = PS5Transport()
        self.assertEqual(adapter.state, PS5State.UNAVAILABLE)
        self.assertFalse(adapter.capabilities.authenticated)
        self.assertFalse(adapter.capabilities.transfers)
        for action in (lambda: adapter.connect("192.168.1.50"),
                       lambda: adapter.upload("a", "b"),
                       lambda: adapter.install_pkg("x.pkg")):
            with self.assertRaises(NotImplementedError):
                action()


if __name__ == "__main__":
    unittest.main()

import contextlib
import importlib.util
import io
import ipaddress
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("wake_ps5", Path(__file__).with_name("wake-ps5.py"))
wake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wake)


class ConfigTest(unittest.TestCase):
    def config(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.toml"
        path.write_text(text)
        return path

    def test_credentials_have_explicit_numeric_formats(self):
        for credential in ['"123456789"', '123456789', '"0x75bcd15"']:
            with self.subTest(credential=credential):
                ip, key = wake.load_config(self.config(
                    '[ps5]\nip = "192.168.1.65"\ncredential = ' + credential))
                self.assertEqual(str(ip), "192.168.1.65")
                self.assertEqual(key, 123456789)

    def test_invalid_credential_does_not_leak_its_value(self):
        secret = "SECRET_TEST_VALUE"
        output = io.StringIO()
        path = self.config('[ps5]\nip="10.10.10.65"\ncredential="' + secret + '"')
        with contextlib.redirect_stderr(output):
            self.assertEqual(wake.main(["-c", str(path)]), 1)
        self.assertNotIn(secret, output.getvalue())

    def test_check_is_offline_and_does_not_require_tailnet(self):
        path = self.config('[ps5]\nip="192.168.1.65"\ncredential="42"')
        with patch.object(wake.socket, "socket", side_effect=AssertionError("network access")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(wake.main(["--config", str(path), "--check"]), 0)


class SendTest(unittest.TestCase):
    def test_direct_unicast_without_discovery_or_response(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(0.1)
            with patch.object(wake, "PORT", receiver.getsockname()[1]):
                wake.send_wakeup(ipaddress.ip_address("127.0.0.1"), 123456789)
            data, _ = receiver.recvfrom(4096)
            self.assertEqual(data, b"WAKEUP * HTTP/1.1\nclient-type:vr\nauth-type:R\n"
                             b"model:w\napp-type:r\nuser-credential:123456789\n"
                             b"device-discovery-protocol-version:00030010\n")
            with self.assertRaises(socket.timeout):
                receiver.recvfrom(4096)


class StatusTest(unittest.TestCase):
    def test_standby_then_ready(self):
        requests = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(("127.0.0.1", 0))
            server.settimeout(1)
            def reply():
                for response in [b"HTTP/1.1 620 Server Standby\n", b"HTTP/1.1 200 Ok\n"]:
                    data, peer = server.recvfrom(4096)
                    requests.append(data)
                    server.sendto(response, peer)
            thread = threading.Thread(target=reply)
            thread.start()
            try:
                with patch.object(wake, "PORT", server.getsockname()[1]):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertTrue(wake.wait_until_ready(ipaddress.ip_address("127.0.0.1"), 1, 0))
            finally:
                thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(requests, [wake.SEARCH, wake.SEARCH])

    def test_no_reply_respects_deadline(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(("127.0.0.1", 0))
            start = time.monotonic()
            with patch.object(wake, "PORT", server.getsockname()[1]):
                self.assertFalse(wake.wait_until_ready(ipaddress.ip_address("127.0.0.1"), 0.05))
            self.assertLess(time.monotonic() - start, 0.5)

    def test_main_sends_one_wake_and_confirms_readiness(self):
        requests = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[ps5]\nip="127.0.0.1"\ncredential="123456789"')
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
                server.bind(("127.0.0.1", 0))
                server.settimeout(1)
                def reply():
                    for _ in range(2):
                        data, peer = server.recvfrom(4096)
                        requests.append(data)
                        if data.startswith(b"SRCH "):
                            server.sendto(b"HTTP/1.1 200 Ok\n", peer)
                thread = threading.Thread(target=reply)
                thread.start()
                output = io.StringIO()
                try:
                    with patch.object(wake, "PORT", server.getsockname()[1]):
                        with contextlib.redirect_stdout(output):
                            self.assertEqual(wake.main(["-c", str(path), "--timeout", "1"]), 0)
                finally:
                    thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(sum(data.startswith(b"WAKEUP ") for data in requests), 1)
            self.assertEqual(sum(data.startswith(b"SRCH ") for data in requests), 1)
            self.assertIn("PS5 is ready.", output.getvalue())
            self.assertNotIn("123456789", output.getvalue())


if __name__ == "__main__":
    unittest.main()

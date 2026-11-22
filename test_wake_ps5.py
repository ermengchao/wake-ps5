import contextlib
import importlib.util
import io
import ipaddress
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import ps5_discovery_relay as relay

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

    def test_relay_check_accepts_address_without_credential_and_stays_offline(self):
        path = self.config('[ps5]\nip="127.0.0.1"')
        with patch.object(wake.socket, "socket", side_effect=AssertionError("network access")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(wake.main(["--relay", "-c", str(path), "--check"]), 0)

    def test_relay_ip_override_does_not_require_a_config_file(self):
        missing_config = self.config('').with_name("missing.toml")
        with patch.object(wake, "DEFAULT_CONFIG", missing_config), patch.object(wake, "run_relay") as run:
            self.assertEqual(wake.main(["--relay", "--ip", "127.0.0.1", "--port", "19303"]), 0)
            run.assert_called_once_with(ipaddress.ip_address("127.0.0.1"), 19303)

    def test_wake_ip_override_keeps_the_configured_credential(self):
        path = self.config('[ps5]\nip="192.0.2.1"\ncredential="42"')
        with patch.object(wake, "send_wakeup") as send, patch.object(wake, "wait_until_ready", return_value=True):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(wake.main(["-c", str(path), "--ip", "127.0.0.1"]), 0)
            send.assert_called_once_with(ipaddress.ip_address("127.0.0.1"), 42)


class RelayTest(unittest.TestCase):
    def test_socks5_udp_association_forwards_discovery_and_reply(self):
        address = "127.0.0.1"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ps5, socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM
        ) as client:
            ps5.bind((address, 0))
            ps5.settimeout(2)
            client.settimeout(2)
            with relay.RelayServer(0, ps5.getsockname()) as server:
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
                thread.start()
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        with socket.create_connection(server.server_address, timeout=2) as control:
                            control.sendall(b"\x05\x01\x00")
                            self.assertEqual(relay.receive(control, 2), b"\x05\x00")
                            control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
                            result = relay.receive(control, 10)
                            self.assertEqual(result[:8], b"\x05\x00\x00\x01" + socket.inet_aton(address))
                            port, = struct.unpack("!H", result[8:])

                            request_header = b"\x00\x00\x00" + relay.ipv4_address(relay.BROADCAST, relay.DISCOVERY_PORT)
                            client.sendto(request_header + wake.SEARCH, (address, port))
                            request, peer = ps5.recvfrom(4096)
                            self.assertEqual(request, wake.SEARCH)
                            response = b"HTTP/1.1 620 Server Standby\n"
                            ps5.sendto(response, peer)
                            received, sender = client.recvfrom(4096)
                            self.assertEqual(sender, (address, port))
                            self.assertEqual(received, b"\x00\x00\x00" + relay.ipv4_address(*ps5.getsockname()) + response)
                finally:
                    server.shutdown()
                    thread.join(2)


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

"""Loopback-only SOCKS5 UDP relay for PS Remote Play discovery."""

import select
import socket
import socketserver
import struct


LOOPBACK = "127.0.0.1"
BROADCAST = "198.19.255.255"
DISCOVERY_PORT = 9302
DEFAULT_RELAY_PORT = 19302
ASSOCIATION_IDLE = 30
MAX_PACKET = 4096
SEARCH = b"SRCH * HTTP/1.1"


def receive(sock, length):
    data = bytearray()
    while len(data) < length:
        part = sock.recv(length - len(data))
        if not part:
            raise EOFError
        data.extend(part)
    return bytes(data)


def ipv4_address(host, port):
    return b"\x01" + socket.inet_aton(host) + struct.pack("!H", port)


def is_discovery_response(data):
    return data.startswith((b"HTTP/1.1 200 ", b"HTTP/1.1 620 "))


class DiscoveryRelay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.serve_association()
        except (EOFError, OSError):
            return

    def serve_association(self):
        control = self.request
        control.settimeout(1)
        version, count = receive(control, 2)
        if version != 5:
            return
        if 0 not in receive(control, count):
            control.sendall(b"\x05\xff")
            return
        control.sendall(b"\x05\x00")

        version, command, reserved, address_type = receive(control, 4)
        if (version, command, reserved) != (5, 3, 0):
            control.sendall(b"\x05\x07\x00" + ipv4_address("0.0.0.0", 0))
            return
        if address_type == 1:
            receive(control, 4)
        elif address_type == 3:
            receive(control, receive(control, 1)[0])
        elif address_type == 4:
            receive(control, 16)
        else:
            control.sendall(b"\x05\x08\x00" + ipv4_address("0.0.0.0", 0))
            return
        requested_port = struct.unpack("!H", receive(control, 2))[0]

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as relay, socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM
        ) as upstream:
            relay.bind((LOOPBACK, 0))
            control.sendall(b"\x05\x00\x00" + ipv4_address(*relay.getsockname()))
            upstream.connect(self.server.ps5)
            self.forward(control, relay, upstream, requested_port)

    def forward(self, control, relay, upstream, requested_port):
        request_header = b"\x00\x00\x00" + ipv4_address(BROADCAST, DISCOVERY_PORT)
        reply_header = b"\x00\x00\x00" + ipv4_address(*self.server.ps5)
        client = None
        while True:
            ready, _, _ = select.select((control, relay, upstream), (), (), ASSOCIATION_IDLE)
            if not ready or control in ready:
                return
            if relay in ready:
                data, sender = relay.recvfrom(MAX_PACKET + 1)
                payload = data[len(request_header) :]
                if (
                    len(data) <= MAX_PACKET
                    and sender[0] == LOOPBACK
                    and (requested_port == 0 or sender[1] == requested_port)
                    and (client is None or sender == client)
                    and data.startswith(request_header)
                    and payload.startswith(SEARCH)
                ):
                    client = sender
                    upstream.send(payload)
                    print(f"Forwarded discovery to {self.server.ps5[0]}:{self.server.ps5[1]}", flush=True)
            if upstream in ready:
                data = upstream.recv(MAX_PACKET + 1)
                if client is not None and len(data) <= MAX_PACKET and is_discovery_response(data):
                    relay.sendto(reply_header + data, client)
                    print(f"Returned PS5 discovery response ({len(data)} bytes)", flush=True)


class RelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, ps5):
        self.ps5 = ps5
        super().__init__((LOOPBACK, port), DiscoveryRelay)


def run_relay(address, port):
    with RelayServer(port, (str(address), DISCOVERY_PORT)) as server:
        print(f"Listening on {LOOPBACK}:{server.server_address[1]}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

#!/usr/bin/env python3
"""Send one PS5 UDP wake request from TOML and wait for the console to be ready.

Requires Python 3.11+. Does not check Tailscale or read captures.
"""
import argparse
import ipaddress
import math
from pathlib import Path
import re
import socket
import sys
import time

try:
    import tomllib
except ImportError:
    raise SystemExit("Python 3.11 or later is required to read TOML configuration.")

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.toml")
PORT = 9302
SEARCH = b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010\n"


def load_config(path):
    with Path(path).open("rb") as file:
        config = tomllib.load(file)
    ps5 = config.get("ps5")
    if not isinstance(ps5, dict):
        raise ValueError("Configuration must contain a [ps5] table.")
    value = ps5.get("ip")
    if not isinstance(value, str):
        raise ValueError("ps5.ip must be an IP address string.")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("ps5.ip must be a valid IPv4 or IPv6 address.") from None
    credential = ps5.get("credential")
    if isinstance(credential, str):
        text = credential.strip()
        if re.fullmatch(r"[0-9]+", text):
            credential = int(text, 10)
        elif re.fullmatch(r"0[xX][0-9a-fA-F]+", text):
            credential = int(text, 16)
        else:
            raise ValueError("ps5.credential must be decimal digits or hexadecimal digits prefixed with 0x.")
    if type(credential) is not int or not 0 <= credential < 2**64:
        raise ValueError("ps5.credential must be an integer between 0 and 2^64-1.")
    return address, credential


def send_wakeup(address, credential):
    packet = ("WAKEUP * HTTP/1.1\nclient-type:vr\nauth-type:R\nmodel:w\n"
              "app-type:r\nuser-credential:" + str(credential) +
              "\ndevice-discovery-protocol-version:00030010\n").encode("ascii")
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.sendto(packet, (str(address), PORT))


def wait_until_ready(address, timeout=45, interval=2):
    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
    deadline = time.monotonic() + timeout
    standby_reported = False
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.connect((str(address), PORT))
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(min(2, remaining))
            try:
                sock.send(SEARCH)
                response = sock.recv(4096).splitlines()
                status = response[0].split() if response else []
                if len(status) >= 2 and status[0] == b"HTTP/1.1":
                    if status[1] == b"200":
                        return True
                    if status[1] == b"620" and not standby_reported:
                        print("PS5 is in standby; waiting for it to become ready...", flush=True)
                        standby_reported = True
            except (socket.timeout, ConnectionRefusedError, ConnectionResetError):
                pass  # The console may briefly stop responding while booting.
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
    return False


def positive_seconds(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                        help="TOML file; defaults to config.toml next to this script")
    parser.add_argument("--check", action="store_true", help="validate configuration without sending a packet")
    parser.add_argument("--timeout", type=positive_seconds, default=45,
                        help="maximum seconds to wait for readiness (default: 45)")
    args = parser.parse_args(argv)
    try:
        address, credential = load_config(args.config)
        if args.check:
            print("Configuration is valid; no packet sent and no credential displayed.")
            return 0
        send_wakeup(address, credential)
        destination = f"[{address}]" if address.version == 6 else str(address)
        print(f"Sent one wake request to {destination}:{PORT}. Waiting up to {args.timeout:g} seconds for PS5 readiness...", flush=True)
        if wait_until_ready(address, args.timeout):
            print("PS5 is ready. You can connect with Remote Play.")
            return 0
        print(f"Timed out after {args.timeout:g} seconds; PS5 readiness was not confirmed.", file=sys.stderr)
        return 1
    except tomllib.TOMLDecodeError:
        print("Invalid TOML syntax in the configuration file.", file=sys.stderr)
    except (OSError, ValueError, UnicodeError) as error:
        print("Wake operation failed: " + str(error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

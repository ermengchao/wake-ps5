# wake-ps5

Read the PS5 IP address and wake credential from TOML, send one standard WAKEUP request over UDP port 9302, then poll until the console reports that it is ready.

Requires Python 3.11 or later. No third-party packages are needed. By default, the script reads `config.toml` next to the script. Use `--config` to select another file.

```toml
[ps5]
ip = "10.10.10.65"
credential = "123456789" # Example value; replace with your own credential.
```

`credential` is the integer value of `user-credential` in a WAKEUP packet. It accepts a TOML integer, a decimal string, or a hexadecimal string prefixed with `0x`. Do not use the outer hex-encoded text from the RP-Registkey request header directly. The real configuration file is excluded by `.gitignore`; the local configuration file has permissions set to `600`.

Run on this Mac:

```fish
/opt/homebrew/bin/python3 /Users/chao/.local/share/repos/wake-ps5/wake-ps5.py
```

Validate the configuration without sending packets:

```fish
/opt/homebrew/bin/python3 /Users/chao/.local/share/repos/wake-ps5/wake-ps5.py --check
```

Use a specific configuration file:

```fish
/opt/homebrew/bin/python3 /Users/chao/.local/share/repos/wake-ps5/wake-ps5.py --config /path/to/config.toml
```

The script does not check whether the source IP belongs to Tailscale, require a standby response before sending the wake request, or read PCAP files. Packets follow the system routing table. IPv4 and IPv6 are supported.

After sending one wake request, the script sends SRCH queries without credentials to the same IP on UDP port 9302. A `200` response prints `PS5 is ready. You can connect with Remote Play.` and exits with code 0. A `620` response means the console is still in standby, so polling continues. The default timeout is 45 seconds; use `--timeout 60` to change it. On timeout, the script reports `PS5 readiness was not confirmed` and exits with code 1. It does not resend the wake request.

"Ready" means the console has returned a ready status; it does not mean a Remote Play stream has been established. Select PlayStation in Remote Play to connect.

Run tests (loopback only, using mock credentials; no contact with a real PS5):

```fish
cd /Users/chao/.local/share/repos/wake-ps5
/opt/homebrew/bin/python3 -m unittest -v
```

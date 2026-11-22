# wake-ps5

Wake a PS5 once, or run a local SOCKS5 UDP relay for PS Remote Play discovery.

Requires Python 3.11 or later. No third-party packages are needed. By default, the script reads `config.toml` next to the script. Use `--config` to select another file.

```toml
[ps5]
ip = "10.10.10.65"
credential = "123456789" # Example value; replace with your own credential.
```

`credential` is the integer value of `user-credential` in a WAKEUP packet. It accepts a TOML integer, a decimal string, or a hexadecimal string prefixed with `0x`. Do not use the outer hex-encoded text from the RP-Registkey request header directly. The real configuration file is excluded by `.gitignore`; the local configuration file has permissions set to `600`.

## Wake once

Run from the checkout:

```fish
./wake-ps5.py
```

The executable uses `python3` from `PATH`. You can also run `python3 wake-ps5.py`, or invoke a specific interpreter such as `/opt/homebrew/bin/python3 wake-ps5.py` on an Apple Silicon Mac.

Use `-h` for both modes, or validate the wake configuration without sending packets:

```fish
./wake-ps5.py -h
./wake-ps5.py --check
```

Select a configuration file or override the PS5 address for one run:

```fish
./wake-ps5.py --config /path/to/config.toml --ip 10.10.10.65
```

The script does not check whether the source IP belongs to Tailscale, require a standby response before sending the wake request, or read PCAP files. Packets follow the system routing table. IPv4 and IPv6 are supported.

After sending one wake request, the script sends SRCH queries without credentials to the same IP on UDP port 9302. A `200` response prints `PS5 is ready. You can connect with Remote Play.` and exits with code 0. A `620` response means the console is still in standby, so polling continues. The default timeout is 45 seconds; use `--timeout 60` to change it. On timeout, the script reports `PS5 readiness was not confirmed` and exits with code 1. It does not resend the wake request.

"Ready" means the console has returned a ready status; it does not mean a Remote Play stream has been established. Select PlayStation in Remote Play to connect.

## Run the discovery relay

```fish
./wake-ps5.py --relay --check
./wake-ps5.py --relay
```

The relay runs in the foreground until stopped. It listens on `127.0.0.1:19302` for SOCKS5 `UDP ASSOCIATE` requests and forwards PS Remote Play discovery requests to the configured PS5 over UDP port `9302`. Use `--port` to change the TCP control port. Each association uses a temporary local UDP port and expires after 30 seconds of socket inactivity.

The relay uses only `[ps5].ip`, which must be an IPv4 address. Pass `--ip` to override it. With `--ip` and no `--config`, the relay can run without a TOML file. `--check` validates these options without binding a port or sending any packets.

```fish
./wake-ps5.py --relay --ip 10.10.10.65 --port 19302
```

## Run as a user service

Both service definitions run `wake-ps5.py --relay`. Set `[ps5].ip` in `config.toml` before installing the service, and stop any foreground relay using the same port.

### macOS launchd

Run from the checkout. The commands install the scripts and `config.toml` under `~/Library/Application Support/wake-ps5`, then set the paths in the installed copy of [`release/launchd/local.wake-ps5.relay.plist`](release/launchd/local.wake-ps5.relay.plist). If Python 3.11 or later is installed elsewhere, change `python3_path` to its absolute path.

```fish
set python3_path /opt/homebrew/bin/python3
set app_dir "$HOME/Library/Application Support/wake-ps5"
set agent "$HOME/Library/LaunchAgents/local.wake-ps5.relay.plist"
set domain "gui/"(id -u)

mkdir -p "$app_dir" "$HOME/Library/LaunchAgents"
and install -m 644 wake-ps5.py ps5_discovery_relay.py "$app_dir/"
and install -m 600 config.toml "$app_dir/config.toml"
and $python3_path "$app_dir/wake-ps5.py" --relay --check
and install -m 644 release/launchd/local.wake-ps5.relay.plist "$agent"
and set program_args ($python3_path -c 'import json, sys; print(json.dumps(sys.argv[1:]))' \
    "$python3_path" "$app_dir/wake-ps5.py" --relay)
and plutil -replace ProgramArguments -json "$program_args" "$agent"
and plutil -replace StandardOutPath -string "$app_dir/relay.log" "$agent"
and plutil -replace StandardErrorPath -string "$app_dir/relay.err.log" "$agent"
and plutil -lint "$agent"
and launchctl bootstrap "$domain" "$agent"
```

The agent starts at login and writes `relay.log` and `relay.err.log` in `~/Library/Application Support/wake-ps5/`. Inspect it with `launchctl print "$domain/local.wake-ps5.relay"`, or unload it for the current session with `launchctl bootout "$domain" "$agent"`.

### Linux systemd

Run from the checkout. The unit expects the checkout and its `config.toml` at `$HOME/.local/share/repos/wake-ps5`.

```fish
set unit_dir "$HOME/.config/systemd/user"

python3 wake-ps5.py --relay --check
and install -Dm644 release/systemd/wake-ps5-relay.service "$unit_dir/wake-ps5-relay.service"
```

If the checkout lives elsewhere, change `ExecStart` in the installed unit before loading it. You can also set an absolute Python 3.11+ path in `ExecStart` if the user service manager's `PATH` differs from the shell's.

```fish
systemctl --user daemon-reload
and systemctl --user enable --now wake-ps5-relay.service
```

Check status with `systemctl --user status wake-ps5-relay.service` and logs with `journalctl --user -u wake-ps5-relay.service`. To stop the relay and disable automatic startup, run `systemctl --user disable --now wake-ps5-relay.service`. Running the user manager outside login sessions requires lingering to be enabled for your account.

## Tests

Run the loopback tests with mock credentials:

```fish
python3 -m unittest -v
```

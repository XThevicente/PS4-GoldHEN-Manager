#!/usr/bin/env python3
"""Mock PS4 client used to validate the LAN protocol on a PC."""
import json
import socket
import urllib.request
from urllib.parse import urlencode

DISCOVERY_PORT = 8786
OFFER_MAGIC = "PS4GH_OFFER_V1"


def get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, json.loads(r.read())


def discover(timeout=4):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", DISCOVERY_PORT))
    s.settimeout(timeout)
    data, addr = s.recvfrom(1024)
    msg = data.decode("ascii", "replace")
    parts = msg.split("|")
    if len(parts) < 4 or parts[0] != OFFER_MAGIC:
        raise RuntimeError(f"Bad discovery packet: {msg!r}")
    return parts[1], int(parts[2]), parts[3], addr


def main():
    ip, port, name, addr = discover()
    base = f"http://{ip}:{port}"
    print("DISCOVERED", name, base, "from", addr)
    print("PING", get(base + "/api/v1/ping"))
    code = input("Pair code shown by server: ").strip()
    _, pair = get(base + "/api/v1/pair?" + urlencode({"code": code, "device": "MockPS4"}))
    token = pair["token"]
    print("PAIRED token_prefix=", token[:8])
    print("STATUS", get(base + "/api/v1/status", token))


if __name__ == "__main__":
    main()

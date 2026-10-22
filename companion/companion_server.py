#!/usr/bin/env python3
"""PS4 GoldHEN Manager Companion Server v0.1

LAN-only companion endpoint for a PS4 homebrew client.
No external services are required.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_NAME = "PS4 GoldHEN Manager"
PROTOCOL = "ps4gh-companion/1"
HTTP_PORT = 8787
DISCOVERY_PORT = 8786
OFFER_MAGIC = "PS4GH_OFFER_V1"


def app_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    p = root / "PS4GoldHENManager"
    p.mkdir(parents=True, exist_ok=True)
    return p


def local_ipv4() -> str:
    for target in (("1.1.1.1", 80), ("8.8.8.8", 80)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(target)
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass
        finally:
            s.close()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


@dataclass
class CompanionState:
    pair_code: str
    token: str | None = None
    paired_device: str | None = None
    last_seen: float | None = None
    ps4_firmware: str | None = None
    app_version: str = "0.1.0"

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


class CompanionService:
    def __init__(self, host: str = "0.0.0.0", http_port: int = HTTP_PORT, discovery_port: int = DISCOVERY_PORT):
        self.host = host
        self.http_port = http_port
        self.discovery_port = discovery_port
        self.state_path = app_data_dir() / "companion.json"
        self.state = self._load_state()
        self.state.pair_code = f"{secrets.randbelow(1_000_000):06d}"
        self.state.save(self.state_path)
        self._stop = threading.Event()
        self._http: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []

    def _load_state(self) -> CompanionState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return CompanionState(
                pair_code=str(raw.get("pair_code") or "000000"),
                token=raw.get("token"),
                paired_device=raw.get("paired_device"),
                last_seen=raw.get("last_seen"),
                ps4_firmware=raw.get("ps4_firmware"),
                app_version=str(raw.get("app_version") or "0.1.0"),
            )
        except Exception:
            return CompanionState(pair_code="000000")

    def _handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PS4GHCompanion/0.1"

            def log_message(self, fmt, *args):
                print(f"[HTTP] {self.address_string()} - {fmt % args}")

            def _json(self, status: int, payload: dict):
                data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _authorized(self) -> bool:
                token = service.state.token
                if not token:
                    return False
                auth = self.headers.get("Authorization", "")
                return auth == f"Bearer {token}"

            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)

                if parsed.path == "/api/v1/ping":
                    return self._json(HTTPStatus.OK, {
                        "ok": True,
                        "protocol": PROTOCOL,
                        "server": APP_NAME,
                        "version": service.state.app_version,
                        "ip": local_ipv4(),
                        "port": service.http_port,
                        "paired": bool(service.state.token),
                    })

                if parsed.path == "/api/v1/pair":
                    code = (qs.get("code") or [""])[0]
                    device = (qs.get("device") or ["PS4"])[0][:64]
                    if not secrets.compare_digest(code, service.state.pair_code):
                        return self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "bad_pair_code"})
                    if not service.state.token:
                        service.state.token = secrets.token_urlsafe(24)
                    service.state.paired_device = device
                    service.state.last_seen = time.time()
                    service.state.save(service.state_path)
                    return self._json(HTTPStatus.OK, {
                        "ok": True,
                        "protocol": PROTOCOL,
                        "token": service.state.token,
                        "device": service.state.paired_device,
                    })

                if parsed.path == "/api/v1/status":
                    if not self._authorized():
                        return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    service.state.last_seen = time.time()
                    service.state.save(service.state_path)
                    return self._json(HTTPStatus.OK, {
                        "ok": True,
                        "protocol": PROTOCOL,
                        "server": APP_NAME,
                        "version": service.state.app_version,
                        "paired_device": service.state.paired_device,
                        "last_seen": service.state.last_seen,
                        "capabilities": ["ping", "pair", "status", "events"],
                    })

                return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path != "/api/v1/event":
                    return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 65536)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
                service.state.last_seen = time.time()
                if isinstance(payload, dict) and isinstance(payload.get("firmware"), str):
                    service.state.ps4_firmware = payload["firmware"][:32]
                service.state.save(service.state_path)
                print("[PS4 EVENT]", json.dumps(payload, ensure_ascii=False))
                return self._json(HTTPStatus.OK, {"ok": True})

        return Handler

    def _advertise_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        while not self._stop.is_set():
            payload = f"{OFFER_MAGIC}|{local_ipv4()}|{self.http_port}|{socket.gethostname()}".encode("ascii", "ignore")
            try:
                s.sendto(payload, ("255.255.255.255", self.discovery_port))
            except OSError:
                pass
            self._stop.wait(1.0)
        s.close()

    def start(self):
        self._http = ThreadingHTTPServer((self.host, self.http_port), self._handler())
        t_http = threading.Thread(target=self._http.serve_forever, name="PS4GH-HTTP", daemon=True)
        t_udp = threading.Thread(target=self._advertise_loop, name="PS4GH-Discovery", daemon=True)
        self._threads = [t_http, t_udp]
        for t in self._threads:
            t.start()
        print(f"{APP_NAME} Companion v{self.state.app_version}")
        print(f"HTTP:      http://{local_ipv4()}:{self.http_port}")
        print(f"Discovery: UDP broadcast :{self.discovery_port}")
        print(f"PAIR CODE: {self.state.pair_code}")
        print("Ctrl+C to stop.")

    def stop(self):
        self._stop.set()
        if self._http:
            self._http.shutdown()
            self._http.server_close()
        for t in self._threads:
            t.join(timeout=2)


def main():
    ap = argparse.ArgumentParser(description="PS4 GoldHEN Manager LAN companion server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=HTTP_PORT)
    ap.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT)
    args = ap.parse_args()
    service = CompanionService(args.host, args.port, args.discovery_port)
    service.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        service.stop()


if __name__ == "__main__":
    main()

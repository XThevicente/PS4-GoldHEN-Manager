#!/usr/bin/env python3
"""PS4 GoldHEN Companion Server v0.4.0.

LAN-only bridge for the PS4 GoldHEN Companion homebrew.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import threading
import time
import unicodedata
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from retro_manager import RetroManager

APP_NAME = "PS4 GoldHEN Manager"
PROTOCOL = "ps4gh-companion/1"
APP_VERSION = "0.5.1"
HTTP_PORT = 8787
DISCOVERY_PORT = 8786
OFFER_MAGIC = "PS4GH_OFFER_V1"
MAX_PUSH_FILE = 512 * 1024


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


def safe_text(text: str, limit: int = 80) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in str(text))
    return text.replace("\\", "/").replace('"', "'").strip()[:limit]


def safe_file_name(name: str) -> str:
    raw = Path(name).name
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "._-")
    return (cleaned or "ps4gh_received.bin")[:64]


@dataclass
class CompanionState:
    pair_code: str
    token: str | None = None
    paired_device: str | None = None
    last_seen: float | None = None
    ps4_firmware: str | None = None
    app_version: str = APP_VERSION
    command_seq: int = 0
    pending_command: dict | None = None
    last_ack: dict | None = None
    last_info: str | None = None
    last_file: str | None = None

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
        self.state.app_version = APP_VERSION
        self.state.pending_command = None
        self.state.last_ack = None
        self.state.save(self.state_path)
        self._stop = threading.Event()
        self._http: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._files: dict[int, tuple[str, bytes]] = {}
        self._file_seq = 0
        self.retro = RetroManager(app_data_dir())

    def _load_state(self) -> CompanionState:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return CompanionState(
                pair_code=str(raw.get("pair_code") or "000000"),
                token=raw.get("token"),
                paired_device=raw.get("paired_device"),
                last_seen=raw.get("last_seen"),
                ps4_firmware=raw.get("ps4_firmware"),
                app_version=APP_VERSION,
                command_seq=int(raw.get("command_seq") or 0),
                last_info=raw.get("last_info"),
                last_file=raw.get("last_file"),
            )
        except Exception:
            return CompanionState(pair_code="000000")

    def queue_command(self, name: str, **payload) -> dict:
        with self._lock:
            if self.state.pending_command:
                raise ValueError("Hay un comando pendiente")
            self.state.command_seq += 1
            cmd = {
                "id": self.state.command_seq,
                "name": safe_text(name, 32),
                "created_at": int(time.time()),
            }
            for key, value in payload.items():
                if isinstance(value, str):
                    cmd[key] = safe_text(value, 96)
                elif isinstance(value, (int, float, bool)) or value is None:
                    cmd[key] = value
            self.state.pending_command = cmd
            self.state.last_ack = None
            self.state.save(self.state_path)
            return dict(cmd)

    def register_file(self, name: str, data: bytes) -> tuple[int, str]:
        if len(data) > MAX_PUSH_FILE:
            raise ValueError(f"El archivo supera {MAX_PUSH_FILE // 1024} KiB")
        with self._lock:
            self._file_seq += 1
            fid = self._file_seq
            fname = safe_file_name(name)
            self._files[fid] = (fname, bytes(data))
            # Keep memory bounded.
            for old_id in sorted(self._files)[:-4]:
                self._files.pop(old_id, None)
            return fid, fname

    def is_online(self, max_age: float = 5.0) -> bool:
        with self._lock:
            return bool(self.state.last_seen and time.time() - self.state.last_seen <= max_age)

    def _touch(self) -> None:
        with self._lock:
            self.state.last_seen = time.time()
            self.state.save(self.state_path)

    def _handler(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PS4GHCompanion/0.5.1"

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
                with service._lock:
                    token = service.state.token
                if not token:
                    return False
                auth = self.headers.get("Authorization", "")
                return secrets.compare_digest(auth, f"Bearer {token}")

            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)

                if parsed.path.startswith("/api/v1/retro/"):
                    if not self._authorized():
                        return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    service._touch()
                    try:
                        if parsed.path == "/api/v1/retro/library":
                            offset = max(0, int((qs.get("offset") or ["0"])[0]))
                            system = (qs.get("system") or [""])[0]
                            page = service.retro.page(offset=offset, system=system)
                            for item in page["items"]:
                                item["title"] = safe_text(item["title"], 48).replace("{", "(").replace("}", ")")
                            return self._json(HTTPStatus.OK, page)
                        if parsed.path == "/api/v1/retro/status":
                            return self._json(HTTPStatus.OK, service.retro.status())
                        if parsed.path == "/api/v1/retro/launch":
                            return self._json(HTTPStatus.OK, service.retro.launch(
                                (qs.get("id") or [""])[0], (qs.get("request") or [""])[0]))
                        if parsed.path == "/api/v1/retro/stop":
                            return self._json(HTTPStatus.OK, service.retro.stop())
                        return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                    except (ValueError, OSError) as exc:
                        return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": safe_text(str(exc), 80)})

                if parsed.path == "/api/v1/ping":
                    with service._lock:
                        paired = bool(service.state.token)
                    return self._json(HTTPStatus.OK, {
                        "ok": True,
                        "protocol": PROTOCOL,
                        "server": APP_NAME,
                        "version": APP_VERSION,
                        "ip": local_ipv4(),
                        "port": service.http_port,
                        "paired": paired,
                    })

                if parsed.path == "/api/v1/pair":
                    code = (qs.get("code") or [""])[0]
                    device = safe_text((qs.get("device") or ["PS4"])[0], 64)
                    with service._lock:
                        if not secrets.compare_digest(code, service.state.pair_code):
                            return self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "bad_pair_code"})
                        if not service.state.token:
                            service.state.token = secrets.token_urlsafe(24)
                        service.state.paired_device = device
                        service.state.last_seen = time.time()
                        service.state.save(service.state_path)
                        token = service.state.token
                    return self._json(HTTPStatus.OK, {
                        "ok": True,
                        "protocol": PROTOCOL,
                        "token": token,
                        "device": device,
                    })

                if parsed.path in ("/api/v1/status", "/api/v1/poll"):
                    if not self._authorized():
                        return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    service._touch()
                    with service._lock:
                        payload = {
                            "ok": True,
                            "protocol": PROTOCOL,
                            "server": APP_NAME,
                            "version": APP_VERSION,
                            "paired_device": service.state.paired_device,
                            "last_seen": service.state.last_seen,
                            "online": True,
                            "capabilities": [
                                "ping", "pair", "status", "poll", "ack",
                                "message", "get_info", "reconnect", "fetch_file", "retro_library", "retro_launch_pc"
                            ],
                        }
                        if parsed.path == "/api/v1/poll":
                            payload["command"] = service.state.pending_command
                    return self._json(HTTPStatus.OK, payload)

                if parsed.path == "/api/v1/file":
                    if not self._authorized():
                        return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    try:
                        file_id = int((qs.get("id") or ["0"])[0])
                    except ValueError:
                        file_id = 0
                    with service._lock:
                        item = service._files.get(file_id)
                    if not item:
                        return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "file_not_found"})
                    name, data = item
                    service._touch()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-PS4GH-Name", name)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                    return

                if parsed.path == "/api/v1/ack":
                    if not self._authorized():
                        return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    try:
                        cmd_id = int((qs.get("id") or ["0"])[0])
                    except ValueError:
                        cmd_id = 0
                    result = safe_text((qs.get("result") or ["ok"])[0], 120)
                    service._touch()
                    with service._lock:
                        pending = service.state.pending_command
                        if pending and int(pending.get("id", 0)) == cmd_id:
                            name = pending.get("name")
                            service.state.last_ack = {
                                "id": cmd_id,
                                "name": name,
                                "result": result,
                                "time": time.time(),
                            }
                            if name == "get_info":
                                service.state.last_info = result
                            elif name == "fetch_file":
                                service.state.last_file = result
                            service.state.pending_command = None
                            service.state.save(service.state_path)
                            return self._json(HTTPStatus.OK, {"ok": True, "acked": cmd_id})
                    return self._json(HTTPStatus.CONFLICT, {"ok": False, "error": "command_mismatch"})

                return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path != "/api/v1/event":
                    return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
                if not self._authorized():
                    return self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 65536:
                        return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "body_too_large"})
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
                service._touch()
                with service._lock:
                    if isinstance(payload, dict) and isinstance(payload.get("firmware"), str):
                        service.state.ps4_firmware = safe_text(payload["firmware"], 32)
                    service.state.save(service.state_path)
                print("[PS4 EVENT]", json.dumps(payload, ensure_ascii=False))
                return self._json(HTTPStatus.OK, {"ok": True})

        return Handler

    def _advertise_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        while not self._stop.is_set():
            ip = local_ipv4()
            payload = f"{OFFER_MAGIC}|{ip}|{self.http_port}|{socket.gethostname()}".encode("ascii", "ignore")
            targets = [("255.255.255.255", self.discovery_port)]
            parts = ip.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                targets.append((".".join(parts[:3] + ["255"]), self.discovery_port))
            for target in dict.fromkeys(targets):
                try:
                    s.sendto(payload, target)
                except OSError:
                    pass
            self._stop.wait(0.75)
        s.close()

    def start(self):
        self._stop.clear()
        self._http = ThreadingHTTPServer((self.host, self.http_port), self._handler())
        self.http_port = self._http.server_address[1]
        t_http = threading.Thread(target=self._http.serve_forever, name="PS4GH-HTTP", daemon=True)
        t_udp = threading.Thread(target=self._advertise_loop, name="PS4GH-Discovery", daemon=True)
        self._threads = [t_http, t_udp]
        for t in self._threads:
            t.start()
        print(f"{APP_NAME} Companion v{APP_VERSION}")
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


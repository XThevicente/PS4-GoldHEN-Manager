from __future__ import annotations

import ftplib
import hashlib
import http.server
import json
import os
import posixpath
import queue
import re
import socket
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import tempfile
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import END, BOTH, LEFT, RIGHT, X, Y, filedialog, messagebox, simpledialog
from tkinter import ttk

APP_NAME = "PS4 GoldHEN Manager"
APP_VERSION = "1.0.0"

FTP_PORT = 2121
BINLOADER_PORT = 9090
KLOG_PORT = 3232
PS4DEBUG_PORT = 744
RPI_PORT = 12800
PKG_HTTP_PORT = 8000
CONNECT_TIMEOUT = 4
SCAN_TIMEOUT = 0.18

# ps4debug-NG v1.3.2: optional notification bridge. It is NOT bundled.
# The app only downloads it after an explicit user action, verifies the official
# release SHA-256, and then sends it to GoldHEN BinLoader.
PS4DEBUG_NG_VERSION = "1.3.2"
PS4DEBUG_NG_URL = (
    "https://github.com/Pharaoh2k/ps4debug-NG/releases/download/1.3.2/"
    "ps4debug-ng_v1.3.2_release_2026-09-20.bin"
)
PS4DEBUG_NG_SHA256 = "28829f48465d3dfdf97afc15f01edafd8802f8213c44635af6f21e0e8b48c8f6"

PACKET_MAGIC = 0xFFAABBCC
CMD_VERSION = 0xBD000001
CMD_FW_VERSION = 0xBD000500
CMD_BRANDING = 0xBD000501
CMD_PROTOCOL_ID = 0xBD000502
CMD_CONSOLE_NOTIFY = 0xBDDD0004
CMD_PROC_LIST = 0xBDAA0001
CMD_PROC_READ = 0xBDAA0002
CMD_PROC_WRITE = 0xBDAA0003
CMD_PROC_MAPS = 0xBDAA0004
CMD_PROC_INFO = 0xBDAA000A
CMD_CONSOLE_FOREGROUND_APP = 0xBDDD0006
CMD_SUCCESS = 0x80000000
NOTIFY_MESSAGE_TYPE = 222


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        p = Path(base) / "PS4GoldHENManager"
    else:
        p = Path.home() / ".ps4_goldhen_manager"
    p.mkdir(parents=True, exist_ok=True)
    return p


SETTINGS_FILE = app_data_dir() / "settings.json"
PAYLOAD_DIR = app_data_dir() / "payloads"
PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
PS4DEBUG_LOCAL = PAYLOAD_DIR / f"ps4debug-ng_v{PS4DEBUG_NG_VERSION}.bin"

GOLDHEN_CHEAT_REPO_ZIP = "https://github.com/GoldHEN/GoldHEN_Cheat_Repository/archive/refs/heads/main.zip"
CHEAT_CACHE_DIR = app_data_dir() / "cheat_cache"
CHEAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class RemoteItem:
    name: str
    path: str
    is_dir: bool
    size: int | None = None


@dataclass
class PkgInfo:
    path: Path
    name: str
    title: str = ""
    title_id: str = ""
    content_id: str = ""
    pkg_type: str = "Desconocido"
    version: str = ""
    size: int = 0
    valid: bool = False
    error: str = ""


class Settings:
    DEFAULTS = {
        "host": "",
        "ftp_port": FTP_PORT,
        "local_path": str(Path.home()),
        "notify_on_connect": True,
        "notify_on_transfer": True,
        "auto_load_notification_bridge": False,
        "pkg_http_port": PKG_HTTP_PORT,
        "auto_connect_startup": True,
    }

    def __init__(self) -> None:
        self.data = dict(self.DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except Exception:
            pass

    def save(self) -> None:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            self.save()
        except Exception:
            pass


def human_size(value: int | None) -> str:
    if value is None:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def get_local_ipv4() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def local_scan_prefixes(preferred_host: str = "") -> list[str]:
    """Return likely /24 LAN prefixes, preferring the PS4/manual host network.

    This avoids selecting VirtualBox/VPN adapters as the only scan network.
    """
    ips: list[str] = []
    if preferred_host:
        try:
            socket.inet_aton(preferred_host)
            ips.append(preferred_host)
        except OSError:
            pass
    try:
        ips.append(get_local_ipv4())
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    # Windows often has several adapters; ipconfig lets us see Wi-Fi/Ethernet too.
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.check_output(["ipconfig"], text=True, encoding="utf-8", errors="ignore", timeout=3, creationflags=(0x08000000 if os.name == "nt" else 0))
            ips.extend(re.findall(r"(?:IPv4[^:]*|IPv4 Address[^:]*):\s*([0-9]+(?:\.[0-9]+){3})", out, flags=re.I))
        except Exception:
            pass
    prefixes: list[str] = []
    for ip in ips:
        parts = ip.split(".")
        if len(parts) != 4 or ip.startswith("127.") or ip.startswith("169.254."):
            continue
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            continue
        if not all(0 <= x <= 255 for x in nums):
            continue
        # Prefer normal private LANs; ignore public/VPN addresses for discovery.
        private = nums[0] == 10 or (nums[0] == 172 and 16 <= nums[1] <= 31) or (nums[0] == 192 and nums[1] == 168)
        if not private:
            continue
        prefix = ".".join(parts[:3])
        if prefix not in prefixes:
            prefixes.append(prefix)
    return prefixes


def can_connect(ip: str, port: int, timeout: float = SCAN_TIMEOUT) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        block = sock.recv(remaining)
        if not block:
            raise ConnectionError("La PS4 cerró la conexión antes de completar la respuesta")
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


class PS4DebugClient:
    """Tiny client for only the benign ps4debug-NG commands we use."""

    @staticmethod
    def version(host: str, port: int = PS4DEBUG_PORT, timeout: float = 2.0) -> str:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(struct.pack("<III", PACKET_MAGIC, CMD_VERSION, 0))
            (length,) = struct.unpack("<I", recv_exact(s, 4))
            if length > 4096:
                raise ValueError("Respuesta de versión no válida")
            return recv_exact(s, length).rstrip(b"\0").decode("utf-8", errors="replace")

    @staticmethod
    def firmware(host: str, port: int = PS4DEBUG_PORT, timeout: float = 2.0) -> str:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III", PACKET_MAGIC, CMD_FW_VERSION, 0))
            raw = struct.unpack("<H", recv_exact(s, 2))[0]
            # ps4debug-NG returns firmware as packed BCD.
            # Examples: 0x505 -> 5.05, 0x1352 -> 13.52.
            major = raw >> 8
            minor_hi = (raw >> 4) & 0xF
            minor_lo = raw & 0xF
            if minor_hi > 9 or minor_lo > 9:
                raise ValueError(f"Firmware BCD no válido: 0x{raw:04X}")
            return f"{major:X}.{minor_hi}{minor_lo}"

    @staticmethod
    def branding(host: str, port: int = PS4DEBUG_PORT, timeout: float = 2.0) -> str:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III", PACKET_MAGIC, CMD_BRANDING, 0))
            (length,) = struct.unpack("<I", recv_exact(s,4))
            if length > 4096: raise ValueError("Branding no válido")
            return recv_exact(s,length).rstrip(b"\0").decode("utf-8",errors="replace")

    @staticmethod
    def protocol_id(host: str, port: int = PS4DEBUG_PORT, timeout: float = 2.0) -> int:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III", PACKET_MAGIC, CMD_PROTOCOL_ID, 0))
            return struct.unpack("<H", recv_exact(s,2))[0]

    @staticmethod
    def process_info(host: str, pid: int, port: int = PS4DEBUG_PORT, timeout: float = 3.0):
        body=struct.pack("<I",pid)
        with socket.create_connection((host,port),timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III",PACKET_MAGIC,CMD_PROC_INFO,len(body))+body)
            PS4DebugClient._status(s); raw=recv_exact(s,188)
            pid2=struct.unpack_from("<I",raw,0)[0]
            def z(a,b): return raw[a:b].split(b"\0",1)[0].decode("utf-8",errors="replace")
            return {"pid":pid2,"name":z(4,44),"path":z(44,108),"title_id":z(108,124),"content_id":z(124,188)}

    @staticmethod
    def foreground_app(host: str, port: int = PS4DEBUG_PORT, timeout: float = 3.0):
        with socket.create_connection((host,port),timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III",PACKET_MAGIC,CMD_CONSOLE_FOREGROUND_APP,0))
            # Protocol v1.3.2: CMD_SUCCESS followed by exactly 132 bytes:
            # u32 pid; char titleid[16], contentid[64], name[40], app_ver[8].
            PS4DebugClient._status(s); raw=recv_exact(s,132)
            pid=struct.unpack_from("<I",raw,0)[0]
            def z(a,b): return raw[a:b].split(b"\0",1)[0].decode("utf-8",errors="replace")
            return {"pid":pid,"title_id":z(4,20),"content_id":z(20,84),"name":z(84,124),"version":z(124,132)}

    @staticmethod
    def notify(host: str, text: str, port: int = PS4DEBUG_PORT, timeout: float = 3.0, message_type: int = NOTIFY_MESSAGE_TYPE) -> None:
        payload = text.encode("utf-8", errors="replace") + b"\0"
        request = struct.pack("<II", int(message_type), len(payload))
        header = struct.pack("<III", PACKET_MAGIC, CMD_CONSOLE_NOTIFY, len(request))
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(header)
            s.sendall(request)
            s.sendall(payload)
            (status,) = struct.unpack("<I", recv_exact(s, 4))
            if status != CMD_SUCCESS:
                raise RuntimeError(f"ps4debug-NG devolvió estado 0x{status:08X}")


    @staticmethod
    def _status(s):
        (status,) = struct.unpack("<I", recv_exact(s, 4))
        if status != CMD_SUCCESS:
            raise RuntimeError(f"ps4debug-NG devolvió estado 0x{status:08X}")

    @staticmethod
    def processes(host: str, port: int = PS4DEBUG_PORT, timeout: float = 3.0):
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(struct.pack("<III", PACKET_MAGIC, CMD_PROC_LIST, 0))
            PS4DebugClient._status(s)
            (num,) = struct.unpack("<I", recv_exact(s, 4))
            if num > 4096: raise ValueError("Lista de procesos no válida")
            raw = recv_exact(s, num * 36)
            out=[]
            for i in range(num):
                name,pid=struct.unpack_from("<32si", raw, i*36)
                out.append((pid, name.split(b"\\0",1)[0].decode("utf-8",errors="replace")))
            return out

    @staticmethod
    def maps(host: str, pid: int, port: int = PS4DEBUG_PORT, timeout: float = 4.0):
        body=struct.pack("<I", pid)
        with socket.create_connection((host,port),timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III",PACKET_MAGIC,CMD_PROC_MAPS,len(body))+body)
            PS4DebugClient._status(s); (num,)=struct.unpack("<I",recv_exact(s,4))
            if num>65536: raise ValueError("Mapa de memoria no válido")
            raw=recv_exact(s,num*64); out=[]
            for i in range(num):
                start,end,off,prot,name=struct.unpack_from("<QQQQ32s",raw,i*64)
                out.append((start,end,prot,name.split(b"\\0",1)[0].decode("utf-8",errors="replace")))
            return out

    @staticmethod
    def read_memory(host: str, pid: int, address: int, length: int, port: int = PS4DEBUG_PORT, timeout: float = 4.0) -> bytes:
        if not (1 <= length <= 1024*1024): raise ValueError("Longitud fuera de rango")
        body=struct.pack("<IQI",pid,address,length)
        with socket.create_connection((host,port),timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III",PACKET_MAGIC,CMD_PROC_READ,len(body))+body)
            PS4DebugClient._status(s); return recv_exact(s,length)

    @staticmethod
    def write_memory(host: str, pid: int, address: int, data: bytes, port: int = PS4DEBUG_PORT, timeout: float = 4.0):
        body=struct.pack("<IQI",pid,address,len(data))
        with socket.create_connection((host,port),timeout=timeout) as s:
            s.settimeout(timeout); s.sendall(struct.pack("<III",PACKET_MAGIC,CMD_PROC_WRITE,len(body))+body)
            PS4DebugClient._status(s); s.sendall(data); PS4DebugClient._status(s)


class GoldHENCheatParser:
    """Parser estricto para el subconjunto JSON de GoldHEN usado por el control remoto."""
    @staticmethod
    def load(path: Path) -> dict:
        data=json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data,dict): raise ValueError("JSON GoldHEN no válido")
        tid=str(data.get("id") or "").upper().strip()
        ver=str(data.get("version") or "").strip().lstrip("vV")
        proc=str(data.get("process") or "eboot.bin").strip()
        if not re.fullmatch(r"CUSA\d{5}",tid): raise ValueError("Title ID no válido")
        mods=[]
        for idx,m in enumerate(data.get("mods") or []):
            if not isinstance(m,dict) or str(m.get("type") or "").lower()!="checkbox": continue
            patches=[]
            for ent in m.get("memory") or []:
                if not isinstance(ent,dict): continue
                off_s=str(ent.get("offset") or "").strip()
                try: off=int(off_s,0)
                except ValueError:
                    try: off=int(off_s,16) if re.fullmatch(r"[0-9A-Fa-f]+",off_s) else int(off_s)
                    except Exception: raise ValueError(f"Offset no válido en {m.get('name','mod')}: {off_s}")
                on_s=re.sub(r"[^0-9A-Fa-f]","",str(ent.get("on") or "")); off_b_s=re.sub(r"[^0-9A-Fa-f]","",str(ent.get("off") or ""))
                if not on_s or len(on_s)%2 or not off_b_s or len(off_b_s)%2: continue
                on_b=bytes.fromhex(on_s); off_b=bytes.fromhex(off_b_s)
                if len(on_b)!=len(off_b): continue
                patches.append({"offset":off,"on":on_b,"off":off_b})
            if patches: mods.append({"index":idx,"name":str(m.get("name") or f"Mod {idx+1}"),"type":"checkbox","patches":patches})
        return {"name":str(data.get("name") or tid),"id":tid,"version":ver,"process":proc,"mods":mods,"source":str(path)}

    @staticmethod
    def compatible(cheat: dict, foreground: dict) -> bool:
        return (str(foreground.get("title_id") or "").upper()==cheat["id"] and
                PS4GoldHENManager._norm_game_version(str(foreground.get("version") or ""))==PS4GoldHENManager._norm_game_version(cheat["version"]))


class PayloadClient:
    @staticmethod
    def send(host: str, payload_path: Path, port: int = BINLOADER_PORT, progress=None) -> None:
        total = payload_path.stat().st_size
        sent = 0
        with socket.create_connection((host, port), timeout=5.0) as s, payload_path.open("rb") as f:
            s.settimeout(15.0)
            while True:
                block = f.read(256 * 1024)
                if not block:
                    break
                s.sendall(block)
                sent += len(block)
                if progress:
                    progress(sent, total)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass


def get_local_ipv4_for(remote_host: str) -> str:
    """Return the local IPv4 address that the OS would use to reach the PS4."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, 9))
        return s.getsockname()[0]
    except OSError:
        return get_local_ipv4()
    finally:
        s.close()


def parse_param_sfo(data: bytes) -> dict[str, object]:
    """Minimal PARAM.SFO parser; enough for title/version/category metadata."""
    out: dict[str, object] = {}
    if len(data) < 20 or data[:4] != b"\x00PSF":
        return out
    try:
        _magic, _version, key_off, data_off, count = struct.unpack_from("<4sIIII", data, 0)
        if count > 4096 or key_off >= len(data) or data_off >= len(data):
            return out
        for i in range(count):
            off = 20 + i * 16
            if off + 16 > len(data):
                break
            key_rel, fmt, length, _max_len, value_rel = struct.unpack_from("<HHIII", data, off)
            k0 = key_off + key_rel
            if k0 >= len(data):
                continue
            k1 = data.find(b"\0", k0)
            if k1 < 0:
                continue
            key = data[k0:k1].decode("utf-8", errors="replace")
            v0 = data_off + value_rel
            v1 = min(len(data), v0 + length)
            if v0 < 0 or v0 > len(data):
                continue
            raw = data[v0:v1]
            if fmt in (0x0204, 0x0004):
                out[key] = raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
            elif fmt == 0x0404 and len(raw) >= 4:
                out[key] = struct.unpack_from("<I", raw, 0)[0]
            else:
                out[key] = raw
    except (struct.error, ValueError):
        return {}
    return out


def inspect_pkg(path: Path) -> PkgInfo:
    info = PkgInfo(path=path, name=path.name, size=path.stat().st_size)
    try:
        with path.open("rb") as f:
            header = f.read(0x2000)
            if len(header) < 0x438 or header[:4] != b"\x7fCNT":
                raise ValueError("No parece un PKG de PS4 (cabecera \\x7fCNT ausente)")
            entry_count = struct.unpack_from(">I", header, 0x10)[0]
            entry_table_offset = struct.unpack_from(">I", header, 0x18)[0]
            content_id = header[0x40:0x40 + 0x24].split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
            content_type = struct.unpack_from(">I", header, 0x74)[0]
            content_flags = struct.unpack_from(">I", header, 0x78)[0]
            declared_size = struct.unpack_from(">Q", header, 0x430)[0]
            if declared_size and info.size != declared_size:
                info.error = f"Tamaño real {info.size} != declarado {declared_size}"

            info.content_id = content_id
            m = re.search(r"([A-Z]{4}\d{5})", content_id, re.I)
            if m:
                info.title_id = m.group(1).upper()

            patch_bits = 0x00100000 | 0x00200000 | 0x40000000 | 0x41000000 | 0x60000000
            if content_type == 0x1E or (content_flags & patch_bits):
                info.pkg_type = "PATCH"
            elif content_type == 0x1A:
                info.pkg_type = "BASE / APP"
            elif content_type == 0x1B:
                info.pkg_type = "DLC / THEME"
            elif content_type == 0x1C:
                info.pkg_type = "DLC (sin datos)"
            else:
                info.pkg_type = f"Tipo 0x{content_type:X}"

            # Locate PARAM.SFO from the PKG entry table, as documented by the RPI source.
            if 0 < entry_count <= 65535 and entry_table_offset > 0:
                f.seek(entry_table_offset)
                table = f.read(entry_count * 0x20)
                for i in range(entry_count):
                    off = i * 0x20
                    if off + 0x20 > len(table):
                        break
                    entry_id = struct.unpack_from(">I", table, off)[0]
                    if entry_id != 0x1000:
                        continue
                    sfo_off = struct.unpack_from(">I", table, off + 0x10)[0]
                    sfo_size = struct.unpack_from(">I", table, off + 0x14)[0]
                    if not (0 < sfo_size <= 16 * 1024 * 1024):
                        break
                    f.seek(sfo_off)
                    sfo = parse_param_sfo(f.read(sfo_size))
                    info.title = str(sfo.get("TITLE") or sfo.get("TITLE_01") or "")
                    info.title_id = str(sfo.get("TITLE_ID") or info.title_id or "")
                    info.content_id = str(sfo.get("CONTENT_ID") or info.content_id or "")
                    info.version = str(sfo.get("APP_VER") or sfo.get("VERSION") or "")
                    category = str(sfo.get("CATEGORY") or "")
                    if category and info.pkg_type == "Desconocido":
                        info.pkg_type = category
                    break
            info.valid = True
            return info
    except Exception as exc:
        info.error = str(exc)
        return info


class RangePackageServer:
    """Small threaded HTTP server that exposes only explicitly registered PKGs."""

    def __init__(self) -> None:
        self.httpd: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.bind_ip = ""
        self.port = 0
        self.files: dict[str, Path] = {}
        self.lock = threading.RLock()
        self.bytes_served = 0
        self.requests = 0

    @property
    def running(self) -> bool:
        return self.httpd is not None

    def start(self, bind_ip: str, port: int) -> int:
        if self.running and self.bind_ip == bind_ip and self.port == port:
            return self.port
        self.stop()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = "PS4GoldHENManager/0.3"
            protocol_version = "HTTP/1.1"

            def log_message(self, _fmt, *_args):
                return

            def do_HEAD(self):
                self._serve(False)

            def do_GET(self):
                self._serve(True)

            def _serve(self, send_body: bool):
                route = urllib.parse.urlsplit(self.path).path
                with outer.lock:
                    file_path = outer.files.get(route)
                if not file_path or not file_path.exists() or not file_path.is_file():
                    self.send_error(404, "PKG no registrado")
                    return
                size = file_path.stat().st_size
                start, end = 0, max(0, size - 1)
                status = 200
                range_header = self.headers.get("Range", "").strip()
                if range_header:
                    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                    if not m:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    a, b = m.groups()
                    if a == "" and b:
                        suffix = min(int(b), size)
                        start = size - suffix
                    elif a:
                        start = int(a)
                        if b:
                            end = min(int(b), size - 1)
                    if start >= size or start < 0 or end < start:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
                length = 0 if size == 0 else end - start + 1
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Connection", "close")
                self.end_headers()
                with outer.lock:
                    outer.requests += 1
                if not send_body or length <= 0:
                    return
                remaining = length
                try:
                    with file_path.open("rb") as f:
                        f.seek(start)
                        while remaining:
                            block = f.read(min(1024 * 1024, remaining))
                            if not block:
                                break
                            self.wfile.write(block)
                            remaining -= len(block)
                            with outer.lock:
                                outer.bytes_served += len(block)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        last_error = None
        candidates = [port] + [p for p in range(port + 1, port + 11)]
        for candidate in candidates:
            try:
                self.httpd = Server((bind_ip, candidate), Handler)
                self.port = int(self.httpd.server_address[1])
                self.bind_ip = bind_ip
                break
            except OSError as exc:
                last_error = exc
                self.httpd = None
        if self.httpd is None:
            raise OSError(f"No se pudo abrir el servidor HTTP en {bind_ip}:{port}-{port+10}: {last_error}")
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="pkg-http", daemon=True)
        self.thread.start()
        return self.port

    def register(self, path: Path) -> str:
        path = path.resolve()
        digest = hashlib.sha1((str(path) + str(path.stat().st_mtime_ns)).encode("utf-8")).hexdigest()[:16]
        route = f"/pkg/{digest}.pkg"
        with self.lock:
            self.files[route] = path
        return f"http://{self.bind_ip}:{self.port}{route}"

    def stop(self) -> None:
        httpd = self.httpd
        self.httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self.thread = None
        self.bind_ip = ""
        self.port = 0
        with self.lock:
            self.files.clear()


class RemotePackageInstallerClient:
    def __init__(self, host: str, port: int = RPI_PORT) -> None:
        self.host = host
        self.port = port

    @staticmethod
    def _decode_response(raw: bytes) -> dict:
        text = raw.decode("utf-8", errors="replace").strip()
        # flatz RPI prints a few numeric fields as 0xABCD, which is not strict JSON.
        normalized = re.sub(r"(:\s*)0x([0-9A-Fa-f]+)", lambda m: m.group(1) + str(int(m.group(2), 16)), text)
        try:
            data = json.loads(normalized)
            return data if isinstance(data, dict) else {"raw": text}
        except json.JSONDecodeError:
            return {"raw": text}

    def post(self, endpoint: str, payload: dict, timeout: float = 8.0) -> dict:
        url = f"http://{self.host}:{self.port}{endpoint}"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(1024 * 1024)
        except urllib.error.HTTPError as exc:
            raw = exc.read(1024 * 1024)
            data = self._decode_response(raw)
            raise RuntimeError(data.get("error") or data.get("raw") or f"HTTP {exc.code}") from exc
        data = self._decode_response(raw)
        if data.get("status") == "fail":
            raise RuntimeError(str(data.get("error") or data.get("error_code") or data))
        return data

    def ready(self) -> bool:
        try:
            data = self.post("/api/is_exists", {"title_id": "CUSA00000"}, timeout=2.5)
            return data.get("status") == "success"
        except Exception:
            return False

    def install(self, package_urls: list[str]) -> dict:
        return self.post("/api/install", {"type": "direct", "packages": package_urls}, timeout=45.0)

    def progress(self, task_id: int) -> dict:
        return self.post("/api/get_task_progress", {"task_id": int(task_id)}, timeout=4.0)

    def task_action(self, action: str, task_id: int) -> dict:
        if action not in {"start", "stop", "pause", "resume", "unregister"}:
            raise ValueError("Acción de tarea no válida")
        return self.post(f"/api/{action}_task", {"task_id": int(task_id)}, timeout=5.0)


class FTPClient:
    def __init__(self) -> None:
        self.ftp: ftplib.FTP | None = None
        self.lock = threading.RLock()
        self.host = ""
        self.port = FTP_PORT

    @property
    def connected(self) -> bool:
        return self.ftp is not None

    def connect(self, host: str, port: int) -> str:
        self.disconnect()
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=CONNECT_TIMEOUT)
        try:
            ftp.login("anonymous", "ps4-manager@local")
        except ftplib.error_perm:
            ftp.login()
        ftp.voidcmd("TYPE I")
        self.ftp = ftp
        self.host = host
        self.port = port
        try:
            return ftp.getwelcome() or "FTP conectado"
        except Exception:
            return "FTP conectado"

    def disconnect(self) -> None:
        with self.lock:
            if self.ftp is not None:
                try:
                    self.ftp.quit()
                except Exception:
                    try:
                        self.ftp.close()
                    except Exception:
                        pass
            self.ftp = None

    def listdir(self, path: str) -> list[RemoteItem]:
        if not self.ftp:
            raise RuntimeError("No conectado")
        ftp = self.ftp
        items: list[RemoteItem] = []
        with self.lock:
            original = ftp.pwd()
            try:
                ftp.cwd(path)
                current = ftp.pwd()
                try:
                    for name, facts in ftp.mlsd():
                        if name in (".", ".."):
                            continue
                        typ = (facts.get("type") or "").lower()
                        is_dir = typ == "dir"
                        size = None
                        if not is_dir:
                            try:
                                size = int(facts.get("size", ""))
                            except (ValueError, TypeError):
                                size = None
                        items.append(RemoteItem(name, posixpath.join(current, name), is_dir, size))
                except (ftplib.error_perm, AttributeError):
                    names = ftp.nlst()
                    for raw in names:
                        name = posixpath.basename(raw.rstrip("/"))
                        if not name or name in (".", ".."):
                            continue
                        full = posixpath.join(current, name)
                        is_dir = False
                        size = None
                        try:
                            ftp.cwd(full)
                            is_dir = True
                            ftp.cwd(current)
                        except Exception:
                            try:
                                ftp.cwd(current)
                            except Exception:
                                pass
                            try:
                                size = ftp.size(full)
                            except Exception:
                                size = None
                        items.append(RemoteItem(name, full, is_dir, size))
            finally:
                try:
                    ftp.cwd(original)
                except Exception:
                    pass
        items.sort(key=lambda i: (not i.is_dir, i.name.lower()))
        return items

    def mkdir(self, path: str) -> None:
        if not self.ftp:
            raise RuntimeError("No conectado")
        with self.lock:
            self.ftp.mkd(path)

    def ensure_dir(self, path: str) -> None:
        if path in ("", "/"):
            return
        parts = [p for p in path.split("/") if p]
        cur = ""
        for part in parts:
            cur += "/" + part
            try:
                self.mkdir(cur)
            except ftplib.error_perm:
                pass

    def rename(self, old: str, new: str) -> None:
        if not self.ftp:
            raise RuntimeError("No conectado")
        with self.lock:
            self.ftp.rename(old, new)

    def delete_file(self, path: str) -> None:
        if not self.ftp:
            raise RuntimeError("No conectado")
        with self.lock:
            self.ftp.delete(path)

    def rmdir(self, path: str) -> None:
        if not self.ftp:
            raise RuntimeError("No conectado")
        with self.lock:
            self.ftp.rmd(path)

    def upload_file(self, local_path: Path, remote_path: str, progress=None) -> None:
        if not self.ftp:
            raise RuntimeError("No conectado")
        total = local_path.stat().st_size
        sent = 0

        def callback(block: bytes):
            nonlocal sent
            sent += len(block)
            if progress:
                progress(sent, total)

        with self.lock, local_path.open("rb") as f:
            self.ftp.storbinary(f"STOR {remote_path}", f, blocksize=256 * 1024, callback=callback)

    def download_file(self, remote_path: str, local_path: Path, progress=None) -> None:
        if not self.ftp:
            raise RuntimeError("No conectado")
        total = None
        with self.lock:
            try:
                total = self.ftp.size(remote_path)
            except Exception:
                total = None
        received = 0
        local_path.parent.mkdir(parents=True, exist_ok=True)

        with local_path.open("wb") as f:
            def callback(block: bytes):
                nonlocal received
                f.write(block)
                received += len(block)
                if progress:
                    progress(received, total)

            with self.lock:
                self.ftp.retrbinary(f"RETR {remote_path}", callback, blocksize=256 * 1024)


class PS4ManagerApp(tk.Tk):
    @staticmethod
    def _resource_path(*parts: str) -> Path:
        # PyInstaller one-file extracts bundled resources below sys._MEIPASS.
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return root.joinpath(*parts)

    def _apply_app_icon(self) -> None:
        """Apply the branded icon to Tk and the native Windows window."""
        ico = self._resource_path("assets", "ps4_goldhen_manager.ico")
        png = self._resource_path("assets", "ps4_goldhen_manager.png")
        errors = []
        if os.name == "nt" and ico.exists():
            try:
                # Tk/Win32 title bar + taskbar icon.
                self.iconbitmap(str(ico))
            except Exception as exc:
                errors.append(f"ICO: {exc}")
        if png.exists():
            try:
                # Keep a strong reference: Tk otherwise may discard the image.
                self._app_icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._app_icon_photo)
            except Exception as exc:
                errors.append(f"PNG: {exc}")
        # Retry after the HWND has been realized; this fixes some Windows/Tk builds.
        if os.name == "nt" and ico.exists():
            def _retry_icon() -> None:
                try:
                    self.iconbitmap(str(ico))
                    if hasattr(self, "_app_icon_photo"):
                        self.iconphoto(True, self._app_icon_photo)
                except Exception:
                    pass
            self.after(250, _retry_icon)

    def __init__(self) -> None:
        # Give Windows a dedicated application identity BEFORE Tk creates the native window.
        # This prevents the taskbar from grouping the app under python.exe/pythonw.exe.
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "PS4GoldHEN.Manager.0.8"
                )
            except Exception:
                pass

        super().__init__()
        self.settings = Settings()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self._apply_app_icon()
        self.geometry("1536x1024")
        self.minsize(1280, 800)
        self.configure(bg="#101820")

        self.ftp = FTPClient()
        self.remote_items: dict[str, RemoteItem] = {}
        self.library_rows: list[dict[str, str]] = []
        p = Path(str(self.settings.get("local_path", Path.home()))).expanduser()
        self.local_path = p if p.exists() and p.is_dir() else Path.home()
        self.remote_path = "/"
        self.ui_queue: queue.Queue = queue.Queue()
        self.klog_stop = threading.Event()
        self.klog_thread: threading.Thread | None = None
        self.pkg_server = RangePackageServer()
        self.pkg_items: dict[str, PkgInfo] = {}
        self.rpi_task_id: int | None = None
        self.rpi_task_active = False
        self.rpi_monitor_stop = threading.Event()
        self.rpi_monitor_thread: threading.Thread | None = None
        self.service_state = {FTP_PORT: False, BINLOADER_PORT: False, KLOG_PORT: False, PS4DEBUG_PORT: False, RPI_PORT: False}

        self._setup_style()
        self._build_ui()
        self.refresh_local()
        self.after(100, self._process_ui_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if bool(self.settings.get("auto_connect_startup", True)):
            self.after(650, self._startup_auto_connect)

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try: style.theme_use("clam")
        except Exception: pass
        bg="#061525"; card="#0a2138"; card2="#0c2945"; line="#1b5685"; text="#f4f8fc"; muted="#a8c2d9"; blue="#087cff"
        self.configure(bg=bg)
        style.configure(".", background=bg, foreground=text, fieldbackground="#0b1d30", font=("Segoe UI",10))
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("Panel.TFrame", background=card2)
        style.configure("TLabel", background=bg, foreground=text)
        style.configure("Card.TLabel", background=card, foreground=text)
        style.configure("Panel.TLabel", background=card2, foreground=text)
        style.configure("Header.TLabel", font=("Segoe UI",22,"bold"), foreground="#ffffff")
        style.configure("Gold.TLabel", font=("Segoe UI",22,"bold"), foreground="#ffc928")
        style.configure("Sub.TLabel", foreground=muted)
        style.configure("TEntry", fieldbackground="#091b2d", foreground="#ffffff", insertcolor="#ffffff", padding=7)
        style.configure("TButton", padding=(14,9), background="#0c2945", foreground=text, bordercolor="#3478ae")
        style.map("TButton", background=[("active","#123c62")])
        style.configure("Accent.TButton", background=blue, foreground="white", bordercolor="#21a5ff")
        style.map("Accent.TButton", background=[("active","#1593ff")])
        style.configure("Danger.TButton", background="#b9133a", foreground="white")
        style.configure("Nav.TButton", padding=(14,13), anchor="w", background="#071a2d", foreground="#cde2f5", borderwidth=0)
        style.map("Nav.TButton", background=[("active","#0b4e89")])
        style.configure("Treeview", background="#071b2d", fieldbackground="#071b2d", foreground="#eef6ff", rowheight=34, bordercolor=line)
        style.configure("Treeview.Heading", background="#12385b", foreground="#ffffff", font=("Segoe UI",10,"bold"), padding=7)
        style.map("Treeview", background=[("selected","#086fc9")])
        style.configure("Horizontal.TProgressbar", troughcolor="#081a2b", background="#0788ff")
        style.configure("TNotebook", background=bg, borderwidth=0)
        # La navegación principal es el menú lateral; ocultamos las pestañas duplicadas.
        style.configure("Hidden.TNotebook", background=bg, borderwidth=0, tabmargins=0)
        style.layout("Hidden.TNotebook.Tab", [])
        style.configure("TCheckbutton", background=card, foreground=text)
        style.configure("TLabelframe", background=card, foreground=text, bordercolor=line)
        style.configure("TLabelframe.Label", background=card, foreground="#cfe5f7", font=("Segoe UI",10,"bold"))

    def _build_ui(self) -> None:
        # Header inspirado en la maqueta aprobada.
        header = ttk.Frame(self, style="Card.TFrame", padding=(20,14))
        header.pack(fill=X)
        ttk.Label(header,text="🎮",font=("Segoe UI Emoji",24),style="Card.TLabel").pack(side=LEFT,padx=(0,10))
        ttk.Label(header,text="PS4",font=("Segoe UI",22,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Label(header,text=" GoldHEN",style="Gold.TLabel").pack(side=LEFT)
        ttk.Label(header,text=" Manager",style="Header.TLabel").pack(side=LEFT)
        ttk.Label(header,text="FTP  •  PKG remoto  •  GoldHEN services  •  Payloads  •  Cheats & Mods",style="Card.TLabel").pack(side=LEFT,padx=(18,0),pady=(8,0))
        ttk.Label(header,text=f"v{APP_VERSION}",style="Card.TLabel").pack(side=RIGHT,padx=8)

        conn = ttk.Frame(self, style="Panel.TFrame", padding=(18,12)); conn.pack(fill=X,padx=14,pady=(8,4))
        ttk.Label(conn,text="IP PS4:",style="Panel.TLabel").pack(side=LEFT)
        self.host_var=tk.StringVar(value=str(self.settings.get("host",""))); ttk.Entry(conn,textvariable=self.host_var,width=20).pack(side=LEFT,padx=(7,18))
        ttk.Label(conn,text="FTP:",style="Panel.TLabel").pack(side=LEFT)
        self.port_var=tk.StringVar(value=str(self.settings.get("ftp_port",FTP_PORT))); ttk.Entry(conn,textvariable=self.port_var,width=8).pack(side=LEFT,padx=(7,18))
        ttk.Button(conn,text="Buscar PS4",command=self.scan_ps4).pack(side=LEFT,padx=5)
        ttk.Button(conn,text="Conectar",style="Accent.TButton",command=self.connect_ps4).pack(side=LEFT,padx=5)
        ttk.Button(conn,text="Desconectar",command=self.disconnect_ps4).pack(side=LEFT,padx=5)
        ttk.Button(conn,text="Revisar servicios",command=self.refresh_services).pack(side=LEFT,padx=5)
        self.connection_var=tk.StringVar(value="● PS4 desconectada"); ttk.Label(conn,textvariable=self.connection_var,style="Panel.TLabel").pack(side=RIGHT)

        services=ttk.Frame(self,style="Card.TFrame",padding=(18,9)); services.pack(fill=X,padx=14,pady=(0,7))
        row=ttk.Frame(services,style="Card.TFrame"); row.pack(fill=X)
        ttk.Label(row,text="Servicios:",style="Card.TLabel").pack(side=LEFT,padx=(0,10)); self.service_vars={}
        for label,port in (("FTP",FTP_PORT),("Payload",BINLOADER_PORT),("Klog",KLOG_PORT),("Debug",PS4DEBUG_PORT),("RPI",RPI_PORT)):
            v=tk.StringVar(value=f"● {label} :{port}"); self.service_vars[port]=v; ttk.Label(row,textvariable=v,style="Card.TLabel").pack(side=LEFT,padx=(0,18))
        self.notify_connect_var=tk.BooleanVar(value=bool(self.settings.get("notify_on_connect",True)))
        self.notify_transfer_var=tk.BooleanVar(value=bool(self.settings.get("notify_on_transfer",True)))
        self.auto_bridge_var=tk.BooleanVar(value=bool(self.settings.get("auto_load_notification_bridge",False)))
        opts=ttk.Frame(services,style="Card.TFrame"); opts.pack(fill=X,pady=(6,0))
        for txt,var in (("Aviso al conectar",self.notify_connect_var),("Avisos de transferencias",self.notify_transfer_var),("Auto-cargar puente",self.auto_bridge_var)):
            ttk.Checkbutton(opts,text=txt,variable=var,command=self.save_preferences).pack(side=LEFT,padx=(0,18))

        workspace=ttk.Frame(self); workspace.pack(fill=BOTH,expand=True,padx=14)
        nav=ttk.Frame(workspace,style="Card.TFrame",padding=(5,8)); nav.pack(side=LEFT,fill=Y,padx=(0,8))
        self.notebook=ttk.Notebook(workspace, style="Hidden.TNotebook"); self.notebook.pack(side=LEFT,fill=BOTH,expand=True)
        self.files_tab=ttk.Frame(self.notebook); self.pkg_tab=ttk.Frame(self.notebook); self.library_tab=ttk.Frame(self.notebook); self.firmware_tab=ttk.Frame(self.notebook); self.tools_tab=ttk.Frame(self.notebook); self.console_tab=ttk.Frame(self.notebook); self.cheats_tab=ttk.Frame(self.notebook)
        tabs=((self.files_tab,"Archivos","📁  Archivos"),(self.pkg_tab,"Instalador PKG","📦  Instalador PKG"),(self.library_tab,"Biblioteca","🎮  Biblioteca"),(self.firmware_tab,"Firmware","🛡️  Firmware"),(self.tools_tab,"Herramientas GoldHEN","🔧  Herramientas"),(self.console_tab,"Mi PS4","▣  Mi PS4"),(self.cheats_tab,"Cheats & Mods","🎮  Cheats & Mods"))
        for i,(frame,title,navtitle) in enumerate(tabs):
            self.notebook.add(frame,text=title); ttk.Button(nav,text=navtitle,style="Nav.TButton",width=20,command=lambda n=i:self.notebook.select(n)).pack(fill=X,pady=3)
        self._build_files_tab(); self._build_pkg_tab(); self._build_library_tab(); self._build_firmware_tab(); self._build_tools_tab(); self._build_console_tab(); self._build_cheats_tab()
        bottom=ttk.Frame(self,padding=(14,7)); bottom.pack(fill=X)
        self.progress=ttk.Progressbar(bottom,mode="determinate",maximum=100); self.progress.pack(fill=X)
        self.status_var=tk.StringVar(value="Listo"); ttk.Label(bottom,textvariable=self.status_var,style="Sub.TLabel").pack(side=LEFT,pady=(5,0))
        ttk.Label(bottom,text=f"PS4 GoldHEN Manager v{APP_VERSION}",style="Sub.TLabel").pack(side=RIGHT,pady=(5,0))

    def _build_files_tab(self) -> None:
        main = ttk.Frame(self.files_tab, padding=(0, 6))
        main.pack(fill=BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0)
        main.columnconfigure(2, weight=1)
        main.rowconfigure(0, weight=1)

        local_card = ttk.Frame(main, style="Card.TFrame", padding=10)
        local_card.grid(row=0, column=0, sticky="nsew")
        remote_card = ttk.Frame(main, style="Card.TFrame", padding=10)
        remote_card.grid(row=0, column=2, sticky="nsew")

        local_header = ttk.Frame(local_card, style="Card.TFrame")
        local_header.pack(fill=X, pady=(0, 8))
        ttk.Label(local_header, text="MI PC", font=("Segoe UI", 11, "bold"), style="Card.TLabel").pack(side=LEFT)
        ttk.Button(local_header, text="Elegir carpeta", command=self.choose_local_folder).pack(side=RIGHT)
        self.local_path_var = tk.StringVar(value=str(self.local_path))
        local_path_row = ttk.Frame(local_card, style="Card.TFrame")
        local_path_row.pack(fill=X, pady=(0, 8))
        ttk.Button(local_path_row, text="↑", width=3, command=self.local_up).pack(side=LEFT, padx=(0, 6))
        local_entry = ttk.Entry(local_path_row, textvariable=self.local_path_var)
        local_entry.pack(side=LEFT, fill=X, expand=True)
        local_entry.bind("<Return>", lambda _e: self.local_go())
        ttk.Button(local_path_row, text="Ir", command=self.local_go).pack(side=LEFT, padx=(6, 0))

        self.local_tree = ttk.Treeview(local_card, columns=("type", "size"), show="tree headings", selectmode="extended")
        self.local_tree.heading("#0", text="Nombre")
        self.local_tree.heading("type", text="Tipo")
        self.local_tree.heading("size", text="Tamaño")
        self.local_tree.column("#0", width=280)
        self.local_tree.column("type", width=80, anchor="center")
        self.local_tree.column("size", width=95, anchor="e")
        self.local_tree.pack(fill=BOTH, expand=True)
        self.local_tree.bind("<Double-1>", self.local_double_click)
        self.local_tree.bind("<Control-a>", lambda e: self._select_all_tree(self.local_tree))
        self._enable_mouse_multiselect(self.local_tree)

        mid = ttk.Frame(main, padding=8)
        mid.grid(row=0, column=1, sticky="ns")
        ttk.Label(mid, text="").pack(expand=True)
        ttk.Button(mid, text="Subir  →", style="Accent.TButton", command=self.upload_selected).pack(pady=6)
        ttk.Button(mid, text="←  Descargar", command=self.download_selected).pack(pady=6)
        ttk.Label(mid, text="").pack(expand=True)

        remote_header = ttk.Frame(remote_card, style="Card.TFrame")
        remote_header.pack(fill=X, pady=(0, 8))
        ttk.Label(remote_header, text="MI PS4", font=("Segoe UI", 11, "bold"), style="Card.TLabel").pack(side=LEFT)
        ttk.Button(remote_header, text="Nueva carpeta", command=self.remote_mkdir).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(remote_header, text="Renombrar", command=self.remote_rename).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(remote_header, text="Borrar", command=self.remote_delete).pack(side=RIGHT)
        ttk.Button(remote_header, text="Seleccionar todo", command=lambda: self._select_all_tree(self.remote_tree)).pack(side=RIGHT, padx=(5, 0))

        quick = ttk.Frame(remote_card, style="Card.TFrame")
        quick.pack(fill=X, pady=(0, 7))
        for path in ("/", "/data", "/data/pkg", "/user", "/mnt/usb0", "/mnt/usb1"):
            ttk.Button(quick, text=path, command=lambda p=path: self.remote_quick(p)).pack(side=LEFT, padx=(0, 4))

        self.remote_path_var = tk.StringVar(value=self.remote_path)
        remote_path_row = ttk.Frame(remote_card, style="Card.TFrame")
        remote_path_row.pack(fill=X, pady=(0, 8))
        ttk.Button(remote_path_row, text="↑", width=3, command=self.remote_up).pack(side=LEFT, padx=(0, 6))
        remote_entry = ttk.Entry(remote_path_row, textvariable=self.remote_path_var)
        remote_entry.pack(side=LEFT, fill=X, expand=True)
        remote_entry.bind("<Return>", lambda _e: self.remote_go())
        ttk.Button(remote_path_row, text="Ir", command=self.remote_go).pack(side=LEFT, padx=(6, 0))

        self.remote_tree = ttk.Treeview(remote_card, columns=("type", "size"), show="tree headings", selectmode="extended")
        self.remote_tree.heading("#0", text="Nombre")
        self.remote_tree.heading("type", text="Tipo")
        self.remote_tree.heading("size", text="Tamaño")
        self.remote_tree.column("#0", width=280)
        self.remote_tree.column("type", width=80, anchor="center")
        self.remote_tree.column("size", width=95, anchor="e")
        self.remote_tree.pack(fill=BOTH, expand=True)
        self.remote_tree.bind("<Double-1>", self.remote_double_click)
        self.remote_tree.bind("<Control-a>", lambda e: self._select_all_tree(self.remote_tree))
        self._enable_mouse_multiselect(self.remote_tree)
        self.remote_tree.bind("<Delete>", lambda e: self.remote_delete())

    def _build_pkg_tab(self) -> None:
        wrap = ttk.Frame(self.pkg_tab, padding=(0, 8))
        wrap.pack(fill=BOTH, expand=True)

        bar = ttk.Frame(wrap, style="Card.TFrame", padding=10)
        bar.pack(fill=X, pady=(0, 8))
        ttk.Button(bar, text="Añadir PKG", style="Accent.TButton", command=self.add_pkg_files).pack(side=LEFT)
        ttk.Button(bar, text="Quitar", command=self.remove_pkg_files).pack(side=LEFT, padx=(6, 0))
        ttk.Button(bar, text="Instalar seleccionado", command=self.install_selected_pkg).pack(side=LEFT, padx=(14, 0))
        ttk.Button(bar, text="Instalar como partes", command=self.install_selected_as_parts).pack(side=LEFT, padx=(6, 0))
        ttk.Button(bar, text="Subir a /data/pkg", command=self.upload_pkg_from_installer_tab).pack(side=LEFT, padx=(6, 0))
        ttk.Button(bar, text="Comprobar RPI", command=self.check_rpi).pack(side=RIGHT)

        server = ttk.Frame(wrap, style="Card.TFrame", padding=10)
        server.pack(fill=X, pady=(0, 8))
        ttk.Label(server, text="Servidor PC:", style="Card.TLabel").pack(side=LEFT)
        self.pkg_server_var = tk.StringVar(value="Detenido")
        ttk.Label(server, textvariable=self.pkg_server_var, style="Card.TLabel").pack(side=LEFT, padx=(6, 16))
        ttk.Label(server, text="Puerto:", style="Card.TLabel").pack(side=LEFT)
        self.pkg_http_port_var = tk.StringVar(value=str(self.settings.get("pkg_http_port", PKG_HTTP_PORT)))
        ttk.Entry(server, textvariable=self.pkg_http_port_var, width=7).pack(side=LEFT, padx=(5, 8))
        ttk.Button(server, text="Detener servidor", command=self.stop_pkg_server).pack(side=LEFT)
        self.rpi_status_var = tk.StringVar(value="RPI sin comprobar")
        ttk.Label(server, textvariable=self.rpi_status_var, style="Card.TLabel").pack(side=RIGHT)

        card = ttk.Frame(wrap, style="Card.TFrame", padding=10)
        card.pack(fill=BOTH, expand=True, pady=(0, 8))
        cols = ("title", "titleid", "kind", "version", "size", "content")
        self.pkg_tree = ttk.Treeview(card, columns=cols, show="tree headings", selectmode="extended")
        self.pkg_tree.heading("#0", text="Archivo")
        self.pkg_tree.heading("title", text="Título")
        self.pkg_tree.heading("titleid", text="Title ID")
        self.pkg_tree.heading("kind", text="Tipo")
        self.pkg_tree.heading("version", text="Versión")
        self.pkg_tree.heading("size", text="Tamaño")
        self.pkg_tree.heading("content", text="Content ID")
        self.pkg_tree.column("#0", width=250)
        self.pkg_tree.column("title", width=250)
        self.pkg_tree.column("titleid", width=95, anchor="center")
        self.pkg_tree.column("kind", width=105, anchor="center")
        self.pkg_tree.column("version", width=75, anchor="center")
        self.pkg_tree.column("size", width=90, anchor="e")
        self.pkg_tree.column("content", width=300)
        sx = ttk.Scrollbar(card, orient="horizontal", command=self.pkg_tree.xview)
        sy = ttk.Scrollbar(card, orient="vertical", command=self.pkg_tree.yview)
        self.pkg_tree.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.pkg_tree.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)

        task = ttk.Frame(wrap, style="Card.TFrame", padding=10)
        task.pack(fill=X)
        self.pkg_task_var = tk.StringVar(value="Sin tarea activa")
        ttk.Label(task, textvariable=self.pkg_task_var, style="Card.TLabel").pack(side=LEFT)
        ttk.Button(task, text="Pausar", command=lambda: self.pkg_task_action("pause")).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(task, text="Reanudar", command=lambda: self.pkg_task_action("resume")).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(task, text="Detener", command=lambda: self.pkg_task_action("stop")).pack(side=RIGHT, padx=(5, 0))
        ttk.Button(task, text="Eliminar tarea", command=lambda: self.pkg_task_action("unregister")).pack(side=RIGHT, padx=(5, 0))
        self.pkg_progress = ttk.Progressbar(wrap, mode="determinate", maximum=100)
        self.pkg_progress.pack(fill=X, pady=(6, 0))
        self.pkg_detail_var = tk.StringVar(value="Para instalación remota, abre Remote Package Installer en la PS4 y mantenlo en primer plano al iniciar la tarea.")
        ttk.Label(wrap, textvariable=self.pkg_detail_var, style="Sub.TLabel", wraplength=1180).pack(fill=X, pady=(5, 0))

    def _build_library_tab(self) -> None:
        wrap = ttk.Frame(self.library_tab, padding=(0, 8))
        wrap.pack(fill=BOTH, expand=True)

        bar = ttk.Frame(wrap, style="Card.TFrame", padding=10)
        bar.pack(fill=X, pady=(0, 8))
        ttk.Button(bar, text="Cargar biblioteca de la PS4", style="Accent.TButton", command=self.load_game_library).pack(side=LEFT)
        ttk.Button(bar, text="Backup app.db", command=self.backup_app_db).pack(side=LEFT, padx=(6, 0))
        ttk.Button(bar, text="Abrir carpeta del juego", command=self.library_open_selected).pack(side=LEFT, padx=(6, 0))
        ttk.Label(bar, text="Buscar:", style="Card.TLabel").pack(side=LEFT, padx=(18, 5))
        self.library_filter_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.library_filter_var, width=28)
        ent.pack(side=LEFT)
        ent.bind("<KeyRelease>", lambda _e: self.filter_library())
        self.library_count_var = tk.StringVar(value="Sin cargar")
        ttk.Label(bar, textvariable=self.library_count_var, style="Card.TLabel").pack(side=RIGHT)

        card = ttk.Frame(wrap, style="Card.TFrame", padding=10)
        card.pack(fill=BOTH, expand=True)
        self.library_tree = ttk.Treeview(card, columns=("name", "content", "visible"), show="headings", selectmode="browse")
        self.library_tree.heading("name", text="Nombre")
        self.library_tree.heading("content", text="Title ID / Content ID")
        self.library_tree.heading("visible", text="Visible")
        self.library_tree.column("name", width=430)
        self.library_tree.column("content", width=430)
        self.library_tree.column("visible", width=80, anchor="center")
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.library_tree.yview)
        self.library_tree.configure(yscrollcommand=scroll.set)
        self.library_tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        self.library_tree.bind("<Double-1>", lambda _e: self.library_open_selected())

    def _build_online_tab(self) -> None:
        wrap=ttk.Frame(self.online_tab,padding=(0,8)); wrap.pack(fill=BOTH,expand=True)
        top=ttk.Frame(wrap,style="Card.TFrame",padding=10); top.pack(fill=X,pady=(0,8))
        ttk.Label(top,text="Catálogo Online",font=("Segoe UI",14,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Label(top,text="Payloads y PKG homebrew / contenido autorizado",style="Card.TLabel").pack(side=LEFT,padx=(14,20))
        ttk.Button(top,text="Actualizar",style="Accent.TButton",command=self.online_refresh).pack(side=RIGHT)
        src=ttk.Frame(wrap,style="Card.TFrame",padding=10); src.pack(fill=X,pady=(0,8))
        ttk.Label(src,text="Catálogo JSON:",style="Card.TLabel").pack(side=LEFT)
        self.online_source_var=tk.StringVar(value=str(self.settings.get("online_catalog_url","")))
        ttk.Entry(src,textvariable=self.online_source_var).pack(side=LEFT,fill=X,expand=True,padx=8)
        ttk.Button(src,text="Guardar fuente",command=self.online_save_source).pack(side=LEFT)
        ttk.Button(src,text="Limpiar",command=lambda:self.online_source_var.set("")).pack(side=LEFT,padx=(6,0))
        filt=ttk.Frame(wrap,style="Card.TFrame",padding=10); filt.pack(fill=X,pady=(0,8))
        self.online_kind_var=tk.StringVar(value="Todos"); self.online_search_var=tk.StringVar()
        ttk.Label(filt,text="Tipo:",style="Card.TLabel").pack(side=LEFT)
        cb=ttk.Combobox(filt,textvariable=self.online_kind_var,values=("Todos","Payload","PKG"),state="readonly",width=12); cb.pack(side=LEFT,padx=(6,18)); cb.bind("<<ComboboxSelected>>",lambda _e:self.online_render())
        ttk.Label(filt,text="Buscar:",style="Card.TLabel").pack(side=LEFT)
        ent=ttk.Entry(filt,textvariable=self.online_search_var,width=35); ent.pack(side=LEFT,padx=6); ent.bind("<KeyRelease>",lambda _e:self.online_render())
        ttk.Label(filt,text="FW: 13.52",style="Card.TLabel").pack(side=RIGHT)
        body=ttk.Frame(wrap); body.pack(fill=BOTH,expand=True)
        card=ttk.Frame(body,style="Card.TFrame",padding=10); card.pack(side=LEFT,fill=BOTH,expand=True,padx=(0,8))
        self.online_tree=ttk.Treeview(card,columns=("kind","name","version","fw","size","source","state"),show="headings",selectmode="browse")
        for c,t,w in (("kind","Tipo",80),("name","Nombre",250),("version","Versión",80),("fw","Firmware",115),("size","Tamaño",80),("source","Fuente",150),("state","Estado",145)):
            self.online_tree.heading(c,text=t); self.online_tree.column(c,width=w,anchor="w")
        sy=ttk.Scrollbar(card,orient="vertical",command=self.online_tree.yview); self.online_tree.configure(yscrollcommand=sy.set); self.online_tree.pack(side=LEFT,fill=BOTH,expand=True); sy.pack(side=RIGHT,fill=Y)
        self.online_tree.bind("<<TreeviewSelect>>",lambda _e:self.online_show_details())
        side=ttk.Frame(body,style="Card.TFrame",padding=12); side.pack(side=RIGHT,fill=Y); side.configure(width=330); side.pack_propagate(False)
        ttk.Label(side,text="DETALLES",font=("Segoe UI",12,"bold"),style="Card.TLabel").pack(anchor="w")
        self.online_detail_var=tk.StringVar(value="Selecciona un elemento del catálogo.")
        ttk.Label(side,textvariable=self.online_detail_var,style="Card.TLabel",wraplength=300,justify="left").pack(fill=X,pady=(12,18))
        self.online_download_btn=ttk.Button(side,text="Descargar al PC",command=self.online_download_selected); self.online_download_btn.pack(fill=X,pady=4)
        self.online_send_btn=ttk.Button(side,text="Enviar payload a PS4",style="Accent.TButton",command=self.online_send_payload); self.online_send_btn.pack(fill=X,pady=4)
        self.online_pkg_btn=ttk.Button(side,text="Instalar PKG en PS4",style="Accent.TButton",command=self.online_install_pkg); self.online_pkg_btn.pack(fill=X,pady=4)
        ttk.Separator(side).pack(fill=X,pady=12)
        self.online_status_var=tk.StringVar(value="Catálogo integrado listo. Añade una fuente JSON para ampliar el catálogo.")
        ttk.Label(side,textvariable=self.online_status_var,style="Card.TLabel",wraplength=300,justify="left").pack(fill=X)
        self.online_items=[]; self.online_visible=[]
        self.online_refresh()
        self.after(500, self.online_update_states)

    def _online_builtin(self):
        return [{"type":"payload","name":"ps4debug-NG","version":PS4DEBUG_NG_VERSION,"firmware":"13.52 compatible","url":PS4DEBUG_NG_URL,"sha256":PS4DEBUG_NG_SHA256,"description":"Puente de depuración/notificaciones usado por el propio Manager.","source":"GitHub · Pharaoh2k"}]

    def _online_official_release_sources(self):
        # Repositorios oficiales que publican binarios homebrew legalmente distribuibles.
        return [
            {"repo":"bucanero/apollo-ps4", "name":"Apollo Save Tool", "type":"pkg", "exts":(".pkg",), "firmware":"Todos los FW (>= v2.0.0)", "description":"Gestor homebrew de partidas guardadas para PS4."},
            {"repo":"LightningMods/Itemzflow", "name":"Itemzflow Game Manager", "type":"pkg", "exts":(".pkg",), "firmware":"13.52 · sin verificar", "description":"Gestor/homebrew alternativo para PS4. GitHub publica la release, pero el PKG se distribuye desde PKG-Zone.", "direct_url":"https://pkg-zone.com/download/ps4/ITEM00001/latest", "filename":"Itemzflow-latest.pkg", "source_label":"GitHub · LightningMods + PKG-Zone"},
        ]

    def _online_fetch_github_latest(self, src):
        api=f"https://api.github.com/repos/{src['repo']}/releases/latest"
        req=urllib.request.Request(api,headers={"User-Agent":f"{APP_NAME}/{APP_VERSION}","Accept":"application/vnd.github+json"})
        with urllib.request.urlopen(req,timeout=12) as r:
            data=json.loads(r.read(2*1024*1024).decode("utf-8"))
        version=str(data.get("tag_name") or data.get("name") or "latest").lstrip("vV")
        rows=[]
        # Algunos proyectos publican las notas/versiones en GitHub pero distribuyen
        # el PKG desde su repositorio homebrew oficial. Itemzflow es uno de ellos.
        direct=str(src.get("direct_url") or "").strip()
        if direct.startswith("https://"):
            rows.append({"type":src["type"],"name":src["name"],"version":version,"firmware":src["firmware"],"url":direct,"filename":src.get("filename") or f"{src['name']}-{version}.pkg","size":0,"description":src["description"],"source":src.get("source_label") or f"GitHub · {src['repo']}"})
            return rows
        for a in data.get("assets") or []:
            name=str(a.get("name") or "")
            url=str(a.get("browser_download_url") or "")
            if not url.startswith("https://") or not name.lower().endswith(tuple(src["exts"])):
                continue
            rows.append({"type":src["type"],"name":src["name"],"version":version,"firmware":src["firmware"],"url":url,"filename":name,"size":int(a.get("size") or 0),"description":src["description"],"source":f"GitHub · {src['repo']}"})
        return rows

    def online_save_source(self):
        u=self.online_source_var.get().strip(); self.settings.set("online_catalog_url",u); self.online_status_var.set("Fuente guardada." if u else "Fuente externa eliminada; se mantienen las fuentes oficiales.")

    def online_refresh(self):
        base=self._online_builtin(); custom=getattr(self,"online_source_var",tk.StringVar(value="")).get().strip()
        if custom and not custom.lower().startswith(("https://","http://")):
            self.online_status_var.set("La fuente adicional debe ser una URL HTTP/HTTPS."); custom=""
        self.online_status_var.set("Actualizando fuentes oficiales…")
        def task():
            rows=[]; errors=[]
            for src in self._online_official_release_sources():
                try: rows.extend(self._online_fetch_github_latest(src))
                except Exception as e: errors.append(f"{src['name']}: {e}")
            if custom:
                try:
                    req=urllib.request.Request(custom,headers={"User-Agent":f"{APP_NAME}/{APP_VERSION}"})
                    with urllib.request.urlopen(req,timeout=12) as r: raw=r.read(2*1024*1024)
                    data=json.loads(raw.decode("utf-8-sig")); ext=data.get("items",data) if isinstance(data,dict) else data
                    if not isinstance(ext,list): raise ValueError("el JSON no contiene una lista items")
                    for x in ext:
                        if not isinstance(x,dict): continue
                        kind=str(x.get("type") or "").lower(); dl=str(x.get("url") or "").strip()
                        if kind in ("payload","pkg") and dl.startswith(("https://","http://")):
                            y=dict(x); y["type"]=kind; rows.append(y)
                except Exception as e: errors.append(f"Fuente personalizada: {e}")
            # Evita duplicados por URL.
            seen=set(); out=[]
            for x in rows:
                u=x.get("url","")
                if not u or u in seen: continue
                seen.add(u); out.append(x)
            return out,errors
        def done(result):
            rows,errors=result; self.online_items=base+rows; self.online_render()
            msg=f"Catálogo actualizado: {len(self.online_items)} elemento(s)."
            if errors: msg += "  Avisos: " + " | ".join(errors)
            self.online_status_var.set(msg)
        self.run_async(task,on_done=done,on_error=lambda e:(setattr(self,"online_items",base),self.online_render(),self.online_status_var.set(f"No se pudo actualizar: {e}")))

    def online_render(self):
        if not hasattr(self,"online_tree"): return
        self.online_tree.delete(*self.online_tree.get_children()); self.online_visible=[]
        kind=self.online_kind_var.get().lower(); q=self.online_search_var.get().strip().lower()
        for x in self.online_items:
            k=str(x.get("type","")); hay=" ".join(str(x.get(z,"")) for z in ("name","version","firmware","description","source")).lower()
            if kind!="todos" and k!=kind: continue
            if q and q not in hay: continue
            i=len(self.online_visible); self.online_visible.append(x)
            size=x.get("size",""); size=human_size(int(size)) if str(size).isdigit() else str(size or "—")
            state=self._online_item_state(x)
            self.online_tree.insert("",END,iid=str(i),values=(k.upper(),x.get("name","—"),x.get("version","—"),x.get("firmware","—"),size,x.get("source","—"),state))

    def _online_selected(self):
        sel=self.online_tree.selection() if hasattr(self,"online_tree") else ()
        if not sel: return None
        try: return self.online_visible[int(sel[0])]
        except Exception: return None

    def _online_local_path(self,x):
        url=str(x.get("url") or ""); name=str(x.get("filename") or Path(urllib.parse.urlparse(url).path).name or (x.get("name") or "download.bin"))
        dest=(PAYLOAD_DIR if x.get("type")=="payload" else app_data_dir()/"online_pkg")
        return dest/name

    def _online_is_debug(self,x):
        return x.get("type")=="payload" and "ps4debug" in str(x.get("name","")).lower()

    def _online_item_state(self,x):
        if self._online_is_debug(x) and bool(self.service_state.get(PS4DEBUG_PORT)):
            return "✓ Ya cargado"
        p=self._online_local_path(x)
        if p.exists() and p.stat().st_size>0:
            if x.get("type")=="payload":
                return "✓ Descargado" if self.service_state.get(BINLOADER_PORT) else "✓ Descargado · 9090 OFF"
            return "✓ Descargado"
        if x.get("type")=="payload" and not self.service_state.get(BINLOADER_PORT): return "9090 OFF"
        return "Disponible"

    def online_update_states(self):
        if hasattr(self,"online_tree"): self.online_render()
        self.online_show_details()

    def online_show_details(self):
        x=self._online_selected()
        if not x:
            self.online_detail_var.set("Selecciona un elemento del catálogo.")
            if hasattr(self,"online_send_btn"): self.online_send_btn.configure(state="disabled")
            if hasattr(self,"online_pkg_btn"): self.online_pkg_btn.configure(state="disabled")
            return
        sha=str(x.get("sha256") or ""); state=self._online_item_state(x)
        self.online_detail_var.set(f"{x.get('name','—')}\n\nEstado: {state}\nTipo: {str(x.get('type','')).upper()}\nVersión: {x.get('version','—')}\nFirmware: {x.get('firmware','—')}\nFuente: {x.get('source','—')}\nSHA-256: {sha[:16]+'…' if sha else 'no publicado'}\n\n{x.get('description','Sin descripción.')}")
        payload=x.get("type")=="payload"; pkg=x.get("type")=="pkg"
        already=self._online_is_debug(x) and bool(self.service_state.get(PS4DEBUG_PORT))
        self.online_send_btn.configure(state=("normal" if payload and not already else "disabled"), text=("ps4debug-NG ya está activo ✓" if already else "Enviar payload a PS4"))
        self.online_pkg_btn.configure(state=("normal" if pkg else "disabled"))

    def _online_download(self,x):
        url=str(x.get("url") or ""); name=str(x.get("filename") or Path(urllib.parse.urlparse(url).path).name or (x.get("name") or "download.bin"))
        dest=(PAYLOAD_DIR if x.get("type")=="payload" else app_data_dir()/"online_pkg"); dest.mkdir(parents=True,exist_ok=True); path=dest/name
        if path.exists() and path.stat().st_size>0:
            expected=str(x.get("sha256") or "").lower().strip()
            if not expected or hashlib.sha256(path.read_bytes()).hexdigest()==expected:
                return path
        req=urllib.request.Request(url,headers={"User-Agent":f"{APP_NAME}/{APP_VERSION}"})
        with urllib.request.urlopen(req,timeout=20) as r, path.open("wb") as f:
            total=int(r.headers.get("Content-Length") or 0); got=0
            while True:
                b=r.read(256*1024)
                if not b: break
                f.write(b); got+=len(b); self.post_progress(got,total or got,f"Descargando {name}")
        expected=str(x.get("sha256") or "").lower().strip()
        if expected:
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=expected: path.unlink(missing_ok=True); raise ValueError("SHA-256 incorrecto; descarga descartada.")
        return path

    def online_download_selected(self):
        x=self._online_selected()
        if not x: messagebox.showinfo(APP_NAME,"Selecciona un elemento."); return
        self.run_async(lambda:self._online_download(x),on_done=lambda p:(self.progress.configure(value=100),self.online_status_var.set(f"✓ Descargado y guardado: {p.name}"),self.online_update_states()),on_error=lambda e:messagebox.showerror(APP_NAME,f"Descarga fallida:\n{e}"))

    def online_send_payload(self):
        x=self._online_selected()
        if not x or x.get("type")!="payload": messagebox.showinfo(APP_NAME,"Selecciona un payload."); return
        host=self.host_var.get().strip()
        if not host: messagebox.showwarning(APP_NAME,"Detecta o introduce primero la IP de la PS4."); return
        if self._online_is_debug(x) and self.service_state.get(PS4DEBUG_PORT):
            self.online_status_var.set("✓ ps4debug-NG ya está activo en :744; no se volverá a cargar."); self.online_update_states(); return
        # Conserva la descarga aunque Payload Server esté apagado.
        p=self._online_local_path(x)
        if not p.exists():
            self.online_status_var.set("Descargando payload al PC…")
        def task():
            p=self._online_download(x)
            if not can_connect(host,BINLOADER_PORT,1.2): return (p,False)
            PayloadClient.send(host,p,BINLOADER_PORT,lambda c,t:self.post_progress(c,t,f"Enviando {p.name}")); return (p,True)
        def done(result):
            p,sent=result; self.progress.configure(value=100)
            if sent:
                self.online_status_var.set(f"✓ Payload enviado: {p.name}. Comprobando servicios…")
                self.after(500,self.refresh_services)
            else:
                self.online_status_var.set(f"✓ {p.name} descargado. Payload Server :9090 está apagado; actívalo en GoldHEN y pulsa Enviar de nuevo.")
                self._apply_service_state({**self.service_state,BINLOADER_PORT:False})
                self.online_update_states()
                messagebox.showinfo(APP_NAME,"El payload está descargado y guardado.\n\nActiva GoldHEN → Servers → Payload Server (:9090) y pulsa Enviar de nuevo. No necesitas volver a descargarlo.")
        self.run_async(task,on_done=done,on_error=lambda e:(self.online_status_var.set(f"Payload conservado si la descarga terminó. Error: {e}"),messagebox.showerror(APP_NAME,f"No se pudo completar el envío:\n{e}")))

    def online_install_pkg(self):
        x=self._online_selected()
        if not x or x.get("type")!="pkg": messagebox.showinfo(APP_NAME,"Selecciona un PKG."); return
        if not messagebox.askyesno(APP_NAME,f"¿Descargar '{x.get('name')}' al PC y prepararlo para Remote Package Installer?\n\nInstala únicamente homebrew o contenido que tengas derecho a usar."): return
        def task():
            p=self._online_download(x); info=inspect_pkg(p)
            if not info.valid: raise ValueError(f"El archivo descargado no supera la validación PKG: {info.error}")
            return info
        def done(info):
            key="pkg:"+hashlib.sha1(str(info.path.resolve()).encode()).hexdigest(); self.pkg_items[key]=info
            if self.pkg_tree.exists(key): self.pkg_tree.delete(key)
            self.pkg_tree.insert("",END,iid=key,text=info.name,values=(info.title or "PKG válido",info.title_id,info.pkg_type,info.version,human_size(info.size),info.content_id)); self.notebook.select(self.pkg_tab); self.pkg_tree.selection_set(key); self.pkg_tree.see(key); self._start_pkg_install([info],False)
        self.run_async(task,on_done=done,on_error=lambda e:messagebox.showerror(APP_NAME,f"No se pudo preparar el PKG:\n{e}"))

    def _build_trophies_tab(self) -> None:
        wrap=ttk.Frame(self.trophies_tab,padding=(0,8)); wrap.pack(fill=BOTH,expand=True)
        head=ttk.Frame(wrap,style="Card.TFrame",padding=14); head.pack(fill=X,pady=(0,8))
        ttk.Label(head,text="🏆 Trofeos",font=("Segoe UI",15,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Label(head,text="Gestión segura mediante Apollo Save Tool",style="Card.TLabel").pack(side=LEFT,padx=14)
        body=ttk.Frame(wrap,style="Card.TFrame",padding=18); body.pack(fill=BOTH,expand=True)
        ttk.Label(body,text="Apollo puede listar, montar, exportar y respaldar sets de trofeos en PS4.",style="Card.TLabel",font=("Segoe UI",11,"bold")).pack(anchor=tk.W)
        ttk.Label(body,text="El Manager no falsifica trofeos PSN. La función Fake XMB de Apollo solo modifica la base de datos local y puede revertirse al ejecutar el juego.",style="Card.TLabel",wraplength=900,justify=LEFT).pack(anchor=tk.W,pady=(8,18))
        row=ttk.Frame(body,style="Card.TFrame"); row.pack(anchor=tk.W)
        ttk.Button(row,text="Buscar Apollo en Online",style="Accent.TButton",command=self._open_apollo_online).pack(side=LEFT,padx=(0,8))
        ttk.Button(row,text="Actualizar estado PS4",command=self.refresh_services).pack(side=LEFT)
        self.trophy_status_var=tk.StringVar(value="Apollo 2.x es la vía recomendada para la gestión avanzada de trofeos.")
        ttk.Label(body,textvariable=self.trophy_status_var,style="Card.TLabel",wraplength=900).pack(anchor=tk.W,pady=(20,0))

    def _open_apollo_online(self):
        self.notebook.select(self.online_tab)
        if hasattr(self,"online_search_var"):
            self.online_search_var.set("Apollo"); self.online_kind_var.set("PKG"); self.online_render()

    def _build_themes_tab(self) -> None:
        wrap=ttk.Frame(self.themes_tab,padding=(0,8)); wrap.pack(fill=BOTH,expand=True)
        head=ttk.Frame(wrap,style="Card.TFrame",padding=14); head.pack(fill=X,pady=(0,8))
        ttk.Label(head,text="🎨 Temas Online",font=("Segoe UI",15,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Label(head,text="Temas homebrew/gratuitos y contenido autorizado",style="Card.TLabel").pack(side=LEFT,padx=14)
        body=ttk.Frame(wrap,style="Card.TFrame",padding=16); body.pack(fill=BOTH,expand=True)
        ttk.Label(body,text="Catálogo de temas",style="Card.TLabel",font=("Segoe UI",11,"bold")).pack(anchor=tk.W)
        ttk.Label(body,text="Puedes añadir un catálogo JSON HTTPS de temas distribuibles. Los PKG se descargan al PC y pasan por la validación del instalador antes de enviarlos a la PS4.",style="Card.TLabel",wraplength=900,justify=LEFT).pack(anchor=tk.W,pady=(6,12))
        r=ttk.Frame(body,style="Card.TFrame"); r.pack(fill=X)
        self.theme_catalog_var=tk.StringVar(value=str(self.settings.get("theme_catalog_url","")))
        ttk.Entry(r,textvariable=self.theme_catalog_var).pack(side=LEFT,fill=X,expand=True)
        ttk.Button(r,text="Guardar",command=self._save_theme_catalog).pack(side=LEFT,padx=6)
        ttk.Button(r,text="Abrir catálogo Online",style="Accent.TButton",command=lambda:self.notebook.select(self.online_tab)).pack(side=LEFT)
        self.theme_status_var=tk.StringVar(value="No se incluyen temas comerciales de PS Store ni fuentes no autorizadas.")
        ttk.Label(body,textvariable=self.theme_status_var,style="Card.TLabel",wraplength=900).pack(anchor=tk.W,pady=(14,0))

    def _save_theme_catalog(self):
        u=self.theme_catalog_var.get().strip()
        if u and not u.startswith("https://"):
            messagebox.showwarning(APP_NAME,"El catálogo de temas debe usar HTTPS."); return
        self.settings.set("theme_catalog_url",u); self.theme_status_var.set("Catálogo guardado." if u else "Catálogo eliminado.")

    def _build_firmware_tab(self) -> None:
        wrap=ttk.Frame(self.firmware_tab,padding=(0,8)); wrap.pack(fill=BOTH,expand=True)
        head=ttk.Frame(wrap,style="Card.TFrame",padding=14); head.pack(fill=X,pady=(0,8))
        ttk.Label(head,text="🛡️ Firmware / Spoof",font=("Segoe UI",15,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Button(head,text="Comprobar",style="Accent.TButton",command=self.refresh_firmware_panel).pack(side=RIGHT)
        body=ttk.Frame(wrap,style="Card.TFrame",padding=18); body.pack(fill=BOTH,expand=True)
        self.fw_real_var=tk.StringVar(value="—"); self.fw_spoof_var=tk.StringVar(value="No aplicado"); self.fw_block_var=tk.StringVar(value="GoldHEN ofrece FW Update Block; estado no verificable por red")
        for label,var in (("Firmware real",self.fw_real_var),("Firmware reportado / spoof",self.fw_spoof_var),("Bloqueo de actualizaciones",self.fw_block_var)):
            r=ttk.Frame(body,style="Card.TFrame"); r.pack(fill=X,pady=5); ttk.Label(r,text=label+":",width=28,style="Card.TLabel").pack(side=LEFT); ttk.Label(r,textvariable=var,style="Card.TLabel").pack(side=LEFT)
        ttk.Separator(body).pack(fill=X,pady=16)
        ttk.Label(body,text="Firmware Spoof",font=("Segoe UI",11,"bold"),style="Card.TLabel").pack(anchor=tk.W)
        ttk.Label(body,text="Preparado para mantener el firmware real y cambiar únicamente la versión reportada. Por seguridad, Aplicar permanece bloqueado hasta disponer de un método verificado específicamente para 13.52; no se reutilizan offsets de otros firmwares.",style="Card.TLabel",wraplength=900,justify=LEFT).pack(anchor=tk.W,pady=(6,12))
        self.spoof_target_var=tk.StringVar(value="Última versión (sin verificar)")
        r=ttk.Frame(body,style="Card.TFrame"); r.pack(anchor=tk.W)
        ttk.Entry(r,textvariable=self.spoof_target_var,width=30,state="readonly").pack(side=LEFT)
        b=ttk.Button(r,text="Aplicar spoof",state=tk.DISABLED); b.pack(side=LEFT,padx=8)
        ttk.Label(r,text="Bloqueado: no hay payload 13.52 verificado",style="Card.TLabel").pack(side=LEFT)

    def refresh_firmware_panel(self):
        host=self.host_var.get().strip()
        if not host: self.fw_real_var.set("PS4 no conectada"); return
        def task():
            if not can_connect(host,PS4DEBUG_PORT,0.6): return "Requiere Debug :744"
            return PS4DebugClient.firmware(host)
        def done(v):
            self.fw_real_var.set(str(v)); self.fw_spoof_var.set("No aplicado (se conserva el firmware real)")
        self.run_async(task,on_done=done,on_error=lambda e:self.fw_real_var.set(f"Error: {e}"))

    def _build_tools_tab(self) -> None:
        wrap = ttk.Frame(self.tools_tab, padding=(0, 8))
        wrap.pack(fill=BOTH, expand=True)

        left = ttk.Frame(wrap, style="Card.TFrame", padding=12)
        left.pack(side=LEFT, fill=Y, padx=(0, 8))
        ttk.Label(left, text="Herramientas", font=("Segoe UI", 12, "bold"), style="Card.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Button(left, text="Enviar payload .bin", command=self.send_custom_payload).pack(fill=X, pady=4)
        ttk.Button(left, text="Preparar notificaciones", command=self.prepare_notifications).pack(fill=X, pady=4)
        ttk.Button(left, text="Enviar notificación…", command=self.custom_notification).pack(fill=X, pady=4)
        ttk.Button(left, text="Subir PKG a /data/pkg", command=self.upload_pkg_to_data).pack(fill=X, pady=4)
        ttk.Separator(left).pack(fill=X, pady=10)
        ttk.Button(left, text="Iniciar Klog", command=self.start_klog).pack(fill=X, pady=4)
        ttk.Button(left, text="Detener Klog", command=self.stop_klog).pack(fill=X, pady=4)
        ttk.Button(left, text="Limpiar Klog", command=lambda: self.klog_text.delete("1.0", END)).pack(fill=X, pady=4)

        info = (
            "Los avisos usan ps4debug-NG únicamente como puente de notificaciones. "
            "La descarga es opcional, se verifica por SHA-256 y se envía a GoldHEN Payload Server (:9090).\n\n"
            "El cargador de payloads ejecuta código en la consola. Usa solamente payloads de fuentes que conozcas y compatibles con tu firmware."
        )
        ttk.Label(left, text=info, style="Card.TLabel", wraplength=285, justify="left").pack(anchor="w", pady=(14, 0))

        right = ttk.Frame(wrap, style="Card.TFrame", padding=12)
        right.pack(side=RIGHT, fill=BOTH, expand=True)
        ttk.Label(right, text="Klog de GoldHEN (:3232)", font=("Segoe UI", 12, "bold"), style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        text_frame = ttk.Frame(right, style="Card.TFrame")
        text_frame.pack(fill=BOTH, expand=True)
        self.klog_text = tk.Text(
            text_frame,
            bg="#0d151c",
            fg="#d9e5ef",
            insertbackground="#ffffff",
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
        )
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.klog_text.yview)
        self.klog_text.configure(yscrollcommand=scroll.set)
        self.klog_text.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)

    def _build_console_tab(self) -> None:
        wrap=ttk.Frame(self.console_tab,padding=(0,8)); wrap.pack(fill=BOTH,expand=True)
        head=ttk.Frame(wrap,style="Card.TFrame",padding=12); head.pack(fill=X,pady=(0,8))
        ttk.Label(head,text="Mi PS4",font=("Segoe UI",15,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Label(head,text="Información obtenida localmente desde tu consola; no se envía fuera del PC.",style="Card.TLabel").pack(side=LEFT,padx=14)
        ttk.Button(head,text="Actualizar datos",style="Accent.TButton",command=self.refresh_console_info).pack(side=RIGHT)
        body=ttk.Frame(wrap); body.pack(fill=BOTH,expand=True)
        left=ttk.Frame(body,style="Card.TFrame",padding=14); left.pack(side=LEFT,fill=BOTH,expand=True,padx=(0,4))
        right=ttk.Frame(body,style="Card.TFrame",padding=14); right.pack(side=LEFT,fill=BOTH,expand=True,padx=(4,0))
        self.console_info_vars={}
        fields=(("ip","IP de la PS4"),("mac","MAC (LAN)"),("firmware","Firmware"),("debug","ps4debug-NG"),("branding","Branding"),("protocol","Protocolo debug"),("ftp","FTP GoldHEN"),("payload","Payload Server"),("klog","Klog"),("rpi","RPI"))
        for key,label in fields:
            row=ttk.Frame(left,style="Card.TFrame"); row.pack(fill=X,pady=4)
            ttk.Label(row,text=label+":",width=20,style="Card.TLabel").pack(side=LEFT)
            v=tk.StringVar(value="—"); self.console_info_vars[key]=v; ttk.Label(row,textvariable=v,style="Card.TLabel").pack(side=LEFT)
        ttk.Label(right,text="Aplicación en primer plano",font=("Segoe UI",11,"bold"),style="Card.TLabel").pack(anchor="w",pady=(0,8))
        self.foreground_vars={}
        for key,label in (("name","Nombre"),("process","Proceso"),("pid","PID"),("title_id","Title ID"),("content_id","Content ID"),("version","Versión")):
            row=ttk.Frame(right,style="Card.TFrame"); row.pack(fill=X,pady=4)
            ttk.Label(row,text=label+":",width=14,style="Card.TLabel").pack(side=LEFT)
            v=tk.StringVar(value="—"); self.foreground_vars[key]=v; ttk.Label(row,textvariable=v,style="Card.TLabel").pack(side=LEFT)
        ttk.Separator(right).pack(fill=X,pady=12)
        ttk.Label(right,text="Identificadores de consola",font=("Segoe UI",11,"bold"),style="Card.TLabel").pack(anchor="w")
        ttk.Label(right,text="No se inventan ni extraen IDs secretos: solo se muestran identificadores que los servicios activos exponen de forma explícita.",style="Card.TLabel",wraplength=520).pack(anchor="w",pady=(6,0))
        self.console_status_var=tk.StringVar(value="Conecta la PS4 y activa Debug :744 para obtener todos los datos.")
        ttk.Label(right,textvariable=self.console_status_var,style="Card.TLabel",wraplength=520).pack(anchor="w",pady=(12,0))

    def _arp_mac(self, host: str) -> str:
        import subprocess
        try:
            out=subprocess.check_output(["arp","-a",host],text=True,errors="replace",timeout=2,creationflags=(0x08000000 if os.name == "nt" else 0))
            m=re.search(r"(?:[0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}",out)
            return m.group(0).upper() if m else "No disponible"
        except Exception: return "No disponible"

    def refresh_console_info(self):
        host=self.host_var.get().strip()
        if not host: messagebox.showwarning(APP_NAME,"Detecta o conecta primero la PS4."); return
        self.console_status_var.set("Leyendo información de la PS4…")
        def task():
            data={"ip":host,"mac":self._arp_mac(host)}
            for key,port in (("ftp",FTP_PORT),("payload",BINLOADER_PORT),("klog",KLOG_PORT),("rpi",RPI_PORT)):
                data[key]="Activo" if can_connect(host,port,0.35) else "No disponible"
            if can_connect(host,PS4DEBUG_PORT,0.5):
                data["debug"]=PS4DebugClient.version(host); data["firmware"]=PS4DebugClient.firmware(host)
                data["branding"]=PS4DebugClient.branding(host); data["protocol"]=str(PS4DebugClient.protocol_id(host))
                try: data["foreground"]=PS4DebugClient.foreground_app(host)
                except Exception: data["foreground"]={}
            else:
                data.update(debug="No disponible",firmware="Requiere Debug :744",branding="—",protocol="—",foreground={})
            return data
        def done(data):
            for k,v in self.console_info_vars.items(): v.set(str(data.get(k,"—")))
            fg=dict(data.get("foreground") or {})
            fg["process"]=fg.get("name") or "—"
            fg["name"]=self._resolve_game_title(fg.get("title_id",""),fg.get("name","")) if fg else "—"
            for k,v in self.foreground_vars.items(): v.set(str(fg.get(k) or "—"))
            self.console_status_var.set("Datos actualizados. Los identificadores mostrados proceden de tu PS4 en la red local.")
        self.run_async(task,on_done=done,on_error=lambda e:self.console_status_var.set(f"Error: {e}"))

    def _startup_auto_connect(self):
        if self.ftp.connected: return
        known=self.host_var.get().strip() or str(self.settings.get("host","")).strip()
        if known and can_connect(known,FTP_PORT,0.25):
            self.host_var.set(known); self.port_var.set(str(FTP_PORT)); self.connect_ps4(); return
        self.scan_ps4()

    def _resolve_game_title(self, title_id: str, fallback: str = "") -> str:
        tid=(title_id or "").strip().upper()
        # 1) biblioteca ya cargada
        for row in getattr(self,"library_rows",[]):
            if str(row.get("title_id","")).upper()==tid and row.get("name") and row.get("name")!=tid:
                return str(row["name"])
        # 2) app.db en caché: es la fuente más fiable porque procede de la propia PS4
        db=app_data_dir()/"app.db"
        if tid and db.exists():
            try:
                rows,_=self._parse_app_db(db)
                for row in rows:
                    if str(row.get("title_id","")).upper()==tid and row.get("name"):
                        return str(row["name"])
            except Exception:
                pass
        # 3) índice local de cheats descargados
        for row in getattr(self,"_cheat_matches",[]):
            if str(row.get("title_id","")).upper()==tid:
                n=str(row.get("name") or "").strip()
                if n: return n
        bad={"eboot.bin","eboot","—",""}
        return fallback if fallback.lower() not in bad else (tid or "Juego desconocido")

    def _build_cheats_tab(self) -> None:
        wrap=ttk.Frame(self.cheats_tab,padding=(0,8)); wrap.pack(fill=BOTH,expand=True)
        head=ttk.Frame(wrap,style="Card.TFrame",padding=12); head.pack(fill=X,pady=(0,8))
        ttk.Label(head,text="Cheats & Mods",font=("Segoe UI",15,"bold"),style="Card.TLabel").pack(side=LEFT)
        ttk.Label(head,text="Coincidencias exactas por Title ID + versión · control local/offline",style="Card.TLabel").pack(side=LEFT,padx=14)
        ttk.Button(head,text="Detectar juego",command=self.cheats_detect_game).pack(side=RIGHT,padx=4)
        ttk.Button(head,text="Buscar online",style="Accent.TButton",command=self.cheats_search_online).pack(side=RIGHT,padx=4)
        ttk.Button(head,text="Abrir JSON",command=self.cheats_load_local_json).pack(side=RIGHT,padx=4)

        body=ttk.Frame(wrap); body.pack(fill=BOTH,expand=True)
        left=ttk.Frame(body); left.pack(side=LEFT,fill=BOTH,expand=True,padx=(0,8))
        right=ttk.Frame(body,style="Card.TFrame",padding=12,width=285); right.pack(side=RIGHT,fill=Y); right.pack_propagate(False)

        game=ttk.Frame(left,style="Card.TFrame",padding=12); game.pack(fill=X,pady=(0,8))
        self.cheat_game_vars={}
        for key,label in (("name","Juego"),("title_id","Title ID"),("version","Versión"),("process","Proceso")):
            row=ttk.Frame(game,style="Card.TFrame"); row.pack(side=LEFT,padx=(0,22))
            ttk.Label(row,text=label+":",style="Card.TLabel").pack(side=LEFT)
            v=tk.StringVar(value="—"); self.cheat_game_vars[key]=v
            ttk.Label(row,textvariable=v,style="Card.TLabel",font=("Segoe UI",10,"bold")).pack(side=LEFT,padx=(5,0))

        card=ttk.Frame(left,style="Card.TFrame",padding=10); card.pack(fill=BOTH,expand=True)
        self.cheat_tree=ttk.Treeview(card,columns=("format","title","version","source","state"),show="headings",selectmode="extended")
        for c,t,w in (("format","Formato",90),("title","Title ID",130),("version","Versión",100),("source","Archivo",430),("state","Estado",150)):
            self.cheat_tree.heading(c,text=t); self.cheat_tree.column(c,width=w,anchor="w")
        self.cheat_tree.pack(fill=BOTH,expand=True)
        actions=ttk.Frame(left,style="Card.TFrame",padding=10); actions.pack(fill=X,pady=(8,0))
        ttk.Button(actions,text="Instalar seleccionados en GoldHEN",style="Accent.TButton",command=self.cheats_install_selected).pack(side=LEFT,padx=(0,6))
        ttk.Button(actions,text="Abrir caché",command=lambda: os.startfile(CHEAT_CACHE_DIR) if os.name=="nt" else None).pack(side=LEFT,padx=6)
        self.cheat_status_var=tk.StringVar(value="Abre un juego y pulsa Detectar juego. Solo se instalan coincidencias exactas.")
        ttk.Label(actions,textvariable=self.cheat_status_var,style="Card.TLabel").pack(side=RIGHT)
        self._cheat_matches=[]; self._active_cheat=None; self._active_fg=None; self._mod_vars=[]
        remote=ttk.LabelFrame(left,text="Control remoto del mod (local/offline)",padding=8); remote.pack(fill=X,pady=(8,0))
        self.remote_mods_frame=ttk.Frame(remote); self.remote_mods_frame.pack(fill=X)
        ttk.Label(self.remote_mods_frame,text="Abre un JSON compatible para generar sus controles.").pack(anchor="w",padx=8,pady=4)

        # Panel derecho del juego: carátula + datos. La carátula se obtiene automáticamente
        # del icon0.png que la propia PS4 guarda para ese CUSA y queda en caché local.
        ttk.Label(right,text="JUEGO DETECTADO",font=("Segoe UI",11,"bold"),style="Card.TLabel").pack(anchor="w",pady=(0,10))
        self.cover_label=ttk.Label(right,text="🎮\n\nCarátula",anchor="center",style="Panel.TLabel",font=("Segoe UI",15,"bold"))
        self.cover_label.pack(fill=X,ipady=72)
        self._cover_photo=None
        self.cover_status_var=tk.StringVar(value="Esperando juego…")
        ttk.Label(right,textvariable=self.cover_status_var,style="Card.TLabel").pack(anchor="w",pady=(8,12))
        self.cheat_side_vars={}
        for key,label in (("name","Juego"),("title_id","Title ID"),("version","Versión"),("process","Proceso")):
            ttk.Label(right,text=label+":",style="Card.TLabel",font=("Segoe UI",9,"bold")).pack(anchor="w",pady=(4,0))
            v=tk.StringVar(value="—"); self.cheat_side_vars[key]=v
            ttk.Label(right,textvariable=v,style="Card.TLabel",wraplength=250).pack(anchor="w")

    def _set_cover_image(self, path: Path, title_id: str, source: str = "caché local") -> None:
        try:
            # Pillow permite mostrar carátulas JPEG/WebP y conservar formato vertical.
            try:
                from PIL import Image, ImageTk
                im=Image.open(path).convert("RGB")
                box=(255,340)
                im.thumbnail(box, Image.Resampling.LANCZOS)
                canvas=Image.new("RGB",box,(9,27,48))
                x=(box[0]-im.width)//2; y=(box[1]-im.height)//2
                canvas.paste(im,(x,y))
                img=ImageTk.PhotoImage(canvas)
            except Exception:
                img=tk.PhotoImage(file=str(path))
                factor=max(1,max(img.width(),img.height())//260)
                if factor>1: img=img.subsample(factor,factor)
            self._cover_photo=img
            self.cover_label.configure(image=img,text="")
            self.cover_status_var.set(f"Carátula · {title_id} · {source}")
        except Exception as e:
            self._cover_photo=None; self.cover_label.configure(image="",text="🎮\n\nCarátula no disponible")
            self.cover_status_var.set(f"No se pudo mostrar la carátula: {e}")

    def _download_online_cover(self, title_id: str, game_name: str, dest: Path):
        """Best-effort: PlayStation Store search. Never required for operation."""
        # The Store changes markup periodically, so this is deliberately a soft provider.
        q=urllib.parse.quote((game_name or title_id).strip())
        url=f"https://store.playstation.com/es-es/search/{q}"
        req=urllib.request.Request(url,headers={"User-Agent":f"Mozilla/5.0 {APP_NAME}/{APP_VERSION}","Accept-Language":"es-ES,es;q=0.9,en;q=0.7"})
        with urllib.request.urlopen(req,timeout=12) as r:
            html=r.read(3_000_000).decode("utf-8","ignore")
        # Prefer Sony CDN artwork. JSON/HTML can escape slashes and unicode ampersands.
        html=html.replace("\\u0026","&").replace("\\/","/")
        urls=re.findall(r'https://image\.api\.playstation\.com[^"\\\s<>]+',html,re.I)
        if not urls: raise RuntimeError("PlayStation Store no devolvió una imagen utilizable")
        # Larger product art tends to include /vulcan/ and is preferable to icons/logos.
        urls=sorted(dict.fromkeys(urls),key=lambda u:("/vulcan/" not in u, "icon" in u.lower(), len(u)))
        last=None
        for image_url in urls[:12]:
            try:
                req=urllib.request.Request(image_url,headers={"User-Agent":"Mozilla/5.0","Referer":"https://store.playstation.com/"})
                with urllib.request.urlopen(req,timeout=12) as r:
                    data=r.read(8_000_000)
                    ctype=(r.headers.get("Content-Type") or "").lower()
                if len(data)<10_000 or "image" not in ctype: continue
                tmp=dest.with_suffix(".online.tmp")
                tmp.write_bytes(data); tmp.replace(dest)
                return dest
            except Exception as e: last=e
        raise RuntimeError(f"No se pudo descargar arte de PlayStation ({last})")

    def _load_game_cover(self, title_id: str) -> None:
        tid=(title_id or "").strip().upper()
        if not re.fullmatch(r"CUSA\d{5}",tid): return
        cover_dir=app_data_dir()/"covers"; cover_dir.mkdir(parents=True,exist_ok=True)
        online=cover_dir/f"{tid}_cover.jpg"
        icon=cover_dir/f"{tid}_icon.png"
        if online.exists() and online.stat().st_size>1000:
            self._set_cover_image(online,tid,"online · caché local"); return
        game_name=self.cheat_game_vars.get("name").get() if getattr(self,"cheat_game_vars",None) else tid
        self.cover_status_var.set(f"Buscando carátula de {tid}…")
        def task():
            # 1) portada online; 2) icon0.png de la consola como fallback.
            try:
                return (self._download_online_cover(tid,game_name,online),"online")
            except Exception as online_error:
                if not self.ftp.connected: raise online_error
                last=online_error
                for remote in (f"/user/appmeta/{tid}/icon0.png",f"/user/appmeta/external/{tid}/icon0.png"):
                    tmp=icon.with_suffix(".tmp.png")
                    try:
                        tmp.unlink(missing_ok=True)
                        self.ftp.download_file(remote,tmp)
                        if tmp.exists() and tmp.stat().st_size>100:
                            tmp.replace(icon); return (icon,"icono PS4 · fallback")
                    except Exception as e:
                        last=e; tmp.unlink(missing_ok=True)
                raise RuntimeError(str(last))
        def done(result):
            path,source=result; self._set_cover_image(Path(path),tid,source)
        def err(e):
            self.cover_label.configure(image="",text="🎮\n\nSin carátula")
            self.cover_status_var.set("Sin carátula online ni icono PS4")
        self.run_async(task,on_done=done,on_error=err)

    @staticmethod
    def _norm_game_version(v: str) -> str:
        v=(v or "").strip().lower().lstrip("v")
        m=re.search(r"(\d+)[._](\d+)",v)
        if not m: return v
        return f"{int(m.group(1)):02d}.{int(m.group(2)):02d}"

    def cheats_detect_game(self):
        host=self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME,"Detecta o conecta primero la PS4."); return
        def done(fg):
            fg=dict(fg or {})
            fg["process"]=fg.get("name") or "eboot.bin"
            fg["name"]=self._resolve_game_title(fg.get("title_id",""), fg.get("name",""))
            for k,v in self.cheat_game_vars.items(): v.set(str(fg.get(k) or "—"))
            for k,v in getattr(self,"cheat_side_vars",{}).items(): v.set(str(fg.get(k) or "—"))
            self._active_fg=fg
            self.cheat_status_var.set(f"● Juego detectado · {fg.get('name')} · {fg.get('title_id')} v{fg.get('version')}")
            self._load_game_cover(fg.get("title_id",""))
        self.run_async(lambda:PS4DebugClient.foreground_app(host),on_done=done,on_error=lambda e:messagebox.showerror(APP_NAME,f"No se pudo detectar el juego. Debug :744 debe estar activo.\n\n{e}"))

    def _download_cheat_repo(self) -> Path:
        zip_path=CHEAT_CACHE_DIR / "GoldHEN_Cheat_Repository-main.zip"
        req=urllib.request.Request(GOLDHEN_CHEAT_REPO_ZIP,headers={"User-Agent":f"{APP_NAME}/{APP_VERSION}"})
        with urllib.request.urlopen(req,timeout=30) as r, zip_path.open("wb") as f:
            total=int(r.headers.get("Content-Length") or 0); got=0
            while True:
                b=r.read(256*1024)
                if not b: break
                f.write(b); got+=len(b); self.post_progress(got,total,"Descargando cheats oficiales")
        return zip_path

    def cheats_search_online(self):
        title_id=self.cheat_game_vars["title_id"].get().strip().upper()
        version=self._norm_game_version(self.cheat_game_vars["version"].get())
        if not re.fullmatch(r"CUSA\d{5}",title_id) or not version:
            self.cheats_detect_game()
            messagebox.showinfo(APP_NAME,"Primero detecta el juego. Cuando aparezcan Title ID y versión, pulsa Buscar online de nuevo.")
            return
        self.cheat_status_var.set(f"Buscando {title_id} v{version}…")
        def task():
            zp=self._download_cheat_repo(); exact=[]; other=[]
            with zipfile.ZipFile(zp) as z:
                for info in z.infolist():
                    if info.is_dir(): continue
                    name=Path(info.filename).name
                    if not name.lower().endswith((".json",".shn",".mc4")): continue
                    m=re.match(r"^(CUSA\d{5})_(\d{2}\.\d{2})(?:_.*)?\.(json|shn|mc4)$",name,re.I)
                    if not m or m.group(1).upper()!=title_id: continue
                    file_ver=self._norm_game_version(m.group(2)); compatible=(file_ver==version)
                    dest=CHEAT_CACHE_DIR/name
                    if compatible:
                        with z.open(info) as src, dest.open("wb") as dst: shutil.copyfileobj(src,dst)
                    row={"path":dest,"name":name,"title_id":title_id,"version":file_ver,"format":m.group(3).lower(),"compatible":compatible}
                    (exact if compatible else other).append(row)
            other.sort(key=lambda r:r["version"])
            return exact+other
        def done(rows):
            self._cheat_matches=rows; self.cheat_tree.delete(*self.cheat_tree.get_children())
            exact_count=0; versions=[]
            for i,r in enumerate(rows):
                ok=bool(r.get("compatible")); exact_count+=int(ok)
                if not ok and r["version"] not in versions: versions.append(r["version"])
                state="Compatible · exacta" if ok else "Otra versión · NO aplicar"
                self.cheat_tree.insert("",END,iid=str(i),values=(r["format"].upper(),r["title_id"],r["version"],r["name"],state))
            if exact_count:
                self.cheat_status_var.set(f"{exact_count} cheat(s) compatible(s) para {title_id} v{version}." + (f" También hay otras versiones: {', '.join(versions)}." if versions else ""))
            elif versions:
                self.cheat_status_var.set(f"Sin cheats exactos para v{version}. Disponibles para {', '.join(versions)}; se muestran solo como referencia y no se pueden instalar.")
            else:
                self.cheat_status_var.set(f"No hay cheats para {title_id} en el repositorio oficial.")
        self.run_async(task,on_done=done,on_error=lambda e:messagebox.showerror(APP_NAME,f"No se pudo consultar el repositorio oficial de GoldHEN.\n\n{e}"))

    def cheats_install_selected(self):
        if not self.ftp.connected:
            messagebox.showwarning(APP_NAME,"Conecta primero por FTP."); return
        sel=self.cheat_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME,"Selecciona uno o más cheats compatibles."); return
        rows=[self._cheat_matches[int(i)] for i in sel]
        incompatible=[r for r in rows if not r.get("compatible",True)]
        if incompatible:
            messagebox.showwarning(APP_NAME,"Has seleccionado un cheat de otra versión. Por seguridad solo se pueden instalar coincidencias exactas de CUSA + versión.")
            return
        # GoldHEN currently supports one format per Title ID/version; avoid silently installing conflicts.
        formats={r["format"] for r in rows}
        if len(formats)>1:
            messagebox.showwarning(APP_NAME,"GoldHEN admite un solo formato por Title ID/versión. Selecciona únicamente JSON, SHN o MC4."); return
        if not messagebox.askyesno(APP_NAME,f"¿Instalar {len(rows)} archivo(s) para {rows[0]['title_id']} v{rows[0]['version']}?\n\nSolo se han aceptado coincidencias exactas de Title ID y versión."):
            return
        def task():
            for r in rows:
                remote_dir=f"/user/data/GoldHEN/cheats/{r['format']}"
                self.ftp.ensure_dir(remote_dir)
                self.ftp.upload_file(r["path"],remote_dir+"/"+r["name"],lambda c,t:self.post_progress(c,t,"Instalando cheat"))
            return len(rows)
        def done(n):
            self.cheat_status_var.set(f"{n} cheat(s) instalados. Abre ⭐ GoldHEN Cheat Menu dentro del juego.")
            self.send_ps4_notification(f"{APP_NAME} v{APP_VERSION}\n{n} cheat(s) instalados para {rows[0]['title_id']} v{rows[0]['version']}", silent=True)
            for iid in sel:
                vals=list(self.cheat_tree.item(iid,"values")); vals[-1]="Instalado en GoldHEN"; self.cheat_tree.item(iid,values=vals)
        self.run_async(task,on_done=done,on_error=lambda e:messagebox.showerror(APP_NAME,f"No se pudieron instalar los cheats.\n\n{e}"))


    def cheats_load_local_json(self):
        path=filedialog.askopenfilename(title="Abrir cheat JSON de GoldHEN",filetypes=[("GoldHEN JSON","*.json"),("Todos","*.*")])
        if not path: return
        try: cheat=GoldHENCheatParser.load(Path(path))
        except Exception as e:
            messagebox.showerror(APP_NAME,f"No se pudo interpretar el JSON de GoldHEN.\n\n{e}"); return
        host=self.host_var.get().strip()
        if not host: messagebox.showwarning(APP_NAME,"Conecta primero la PS4."); return
        def done(fg):
            if not GoldHENCheatParser.compatible(cheat,fg):
                messagebox.showerror(APP_NAME,f"El cheat no coincide con el juego abierto.\n\nCheat: {cheat['id']} v{cheat['version']}\nPS4: {fg.get('title_id') or '—'} v{fg.get('version') or '—'}"); return
            self._active_cheat=cheat; self._active_fg=fg
            for k,v in self.cheat_game_vars.items(): v.set(str(fg.get(k) or "—"))
            self._render_remote_mods()
            self.cheat_status_var.set(f"{len(cheat['mods'])} opción(es) cargadas · {cheat['id']} v{cheat['version']}")
        self.run_async(lambda:PS4DebugClient.foreground_app(host),on_done=done,on_error=lambda e:messagebox.showerror(APP_NAME,f"No se pudo validar el juego abierto.\n\n{e}"))

    def _render_remote_mods(self):
        for w in self.remote_mods_frame.winfo_children(): w.destroy()
        self._mod_vars=[]
        cheat=getattr(self,"_active_cheat",None)
        if not cheat: return
        for i,mod in enumerate(cheat["mods"]):
            var=tk.BooleanVar(value=False); self._mod_vars.append(var)
            cb=ttk.Checkbutton(self.remote_mods_frame,text=mod["name"],variable=var,command=lambda idx=i:self.cheats_toggle_mod(idx))
            cb.pack(anchor="w",padx=8,pady=4)

    def _resolve_process_base(self, host: str, pid: int, process_name: str) -> int:
        maps=PS4DebugClient.maps(host,pid)
        wanted=(process_name or "eboot.bin").lower()
        candidates=[m for m in maps if wanted in (m[3] or "").lower()]
        if not candidates:
            candidates=[m for m in maps if "eboot" in (m[3] or "").lower()]
        if not candidates: raise RuntimeError(f"No se encontró el mapa de memoria de {process_name}")
        return min(m[0] for m in candidates)

    def cheats_toggle_mod(self, idx: int):
        cheat=getattr(self,"_active_cheat",None); fg=getattr(self,"_active_fg",None)
        if not cheat or not fg or idx>=len(cheat["mods"]): return
        desired=bool(self._mod_vars[idx].get()); mod=cheat["mods"][idx]; host=self.host_var.get().strip(); pid=int(fg.get("pid") or 0)
        if not pid:
            self._mod_vars[idx].set(not desired); messagebox.showerror(APP_NAME,"PID del juego no disponible."); return
        def task():
            fresh=PS4DebugClient.foreground_app(host)
            if not GoldHENCheatParser.compatible(cheat,fresh) or int(fresh.get("pid") or 0)!=pid: raise RuntimeError("El juego abierto cambió. Se canceló el parche.")
            base=self._resolve_process_base(host,pid,cheat["process"])
            for patch in mod["patches"]:
                addr=base+patch["offset"]; target=patch["on"] if desired else patch["off"]; opposite=patch["off"] if desired else patch["on"]
                before=PS4DebugClient.read_memory(host,pid,addr,len(target))
                # Only touch memory when it is in one of the two declared states.
                if before not in (target,opposite): raise RuntimeError(f"Bytes inesperados en 0x{addr:X}: {before.hex().upper()} (esperado {opposite.hex().upper()} o {target.hex().upper()})")
                if before!=target:
                    PS4DebugClient.write_memory(host,pid,addr,target)
                    after=PS4DebugClient.read_memory(host,pid,addr,len(target))
                    if after!=target: raise RuntimeError(f"Verificación fallida en 0x{addr:X}")
            return True
        def done(_):
            self.cheat_status_var.set(f"{mod['name']}: {'ON' if desired else 'OFF'} · verificado en memoria")
        def err(e):
            self._mod_vars[idx].set(not desired); messagebox.showerror(APP_NAME,f"No se pudo {'activar' if desired else 'desactivar'} {mod['name']}.\n\n{e}")
        self.run_async(task,on_done=done,on_error=err)

    # ---------- async helpers ----------
    def run_async(self, func, *, on_done=None, on_error=None) -> None:
        def worker():
            try:
                result = func()
                self.ui_queue.put(("done", on_done, result))
            except Exception as exc:
                self.ui_queue.put(("error", on_error, exc))
        threading.Thread(target=worker, daemon=True).start()

    def _process_ui_queue(self) -> None:
        try:
            while True:
                kind, callback, payload = self.ui_queue.get_nowait()
                if kind == "progress":
                    current, total, label = payload
                    if total:
                        pct = max(0, min(100, current * 100 / total))
                        self.progress["value"] = pct
                        self.status_var.set(f"{label}  {pct:.1f}%  ({human_size(current)} / {human_size(total)})")
                    else:
                        self.progress["value"] = 0
                        self.status_var.set(f"{label}  {human_size(current)}")
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "klog":
                    self.klog_text.insert(END, str(payload))
                    self.klog_text.see(END)
                elif kind == "service":
                    self._apply_service_state(payload)
                elif kind == "pkg_task":
                    self._apply_pkg_task_progress(payload)
                elif kind == "done":
                    if callback:
                        callback(payload)
                elif kind == "error":
                    if callback:
                        callback(payload)
                    else:
                        messagebox.showerror(APP_NAME, str(payload))
        except queue.Empty:
            pass
        self.after(100, self._process_ui_queue)

    def post_progress(self, current: int, total: int | None, label: str) -> None:
        self.ui_queue.put(("progress", None, (current, total, label)))

    def post_status(self, text: str) -> None:
        self.ui_queue.put(("status", None, text))

    # ---------- preferences ----------
    def save_preferences(self) -> None:
        self.settings.set("notify_on_connect", bool(self.notify_connect_var.get()))
        self.settings.set("notify_on_transfer", bool(self.notify_transfer_var.get()))
        self.settings.set("auto_load_notification_bridge", bool(self.auto_bridge_var.get()))
        try:
            self.settings.set("pkg_http_port", int(self.pkg_http_port_var.get()))
        except Exception:
            pass

    def _enable_mouse_multiselect(self, tree: ttk.Treeview):
        state={"anchor":None}
        def press(e):
            row=tree.identify_row(e.y); state["anchor"]=row or None
        def drag(e):
            a=state.get("anchor"); b=tree.identify_row(e.y)
            if not a or not b: return
            rows=list(tree.get_children())
            try: i,j=rows.index(a),rows.index(b)
            except ValueError: return
            lo,hi=sorted((i,j)); tree.selection_set(rows[lo:hi+1])
        tree.bind("<ButtonPress-1>",press,add="+")
        tree.bind("<B1-Motion>",drag,add="+")

    def _select_all_tree(self, tree: ttk.Treeview):
        items = tree.get_children("")
        if items:
            tree.selection_set(items)
            tree.focus(items[0])
        return "break"

    # ---------- local ----------
    def refresh_local(self) -> None:
        self.local_path_var.set(str(self.local_path))
        self.local_tree.delete(*self.local_tree.get_children())
        try:
            entries = sorted(self.local_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        for p in entries:
            try:
                is_dir = p.is_dir()
                size = None if is_dir else p.stat().st_size
            except OSError:
                is_dir = False
                size = None
            self.local_tree.insert("", END, iid=str(p), text=p.name, values=("Carpeta" if is_dir else "Archivo", human_size(size)))

    def choose_local_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=str(self.local_path))
        if folder:
            self.local_path = Path(folder)
            self.settings.set("local_path", str(self.local_path))
            self.refresh_local()

    def local_up(self) -> None:
        parent = self.local_path.parent
        if parent != self.local_path:
            self.local_path = parent
            self.settings.set("local_path", str(self.local_path))
            self.refresh_local()

    def local_go(self) -> None:
        p = Path(self.local_path_var.get()).expanduser()
        if p.is_dir():
            self.local_path = p
            self.settings.set("local_path", str(self.local_path))
            self.refresh_local()
        else:
            messagebox.showwarning(APP_NAME, "La ruta local no es una carpeta válida.")

    def local_double_click(self, _event=None) -> None:
        sel = self.local_tree.selection()
        if len(sel) == 1:
            p = Path(sel[0])
            if p.is_dir():
                self.local_path = p
                self.settings.set("local_path", str(self.local_path))
                self.refresh_local()

    # ---------- discovery / connection ----------
    def scan_ps4(self) -> None:
        known=self.host_var.get().strip() or str(self.settings.get("host", "")).strip()
        self.status_var.set("Buscando PS4…")
        def task():
            # Fast path: the last working IP normally answers in a few ms.
            if known and can_connect(known, FTP_PORT, 0.22): return [(known, 100)]
            prefixes=local_scan_prefixes(known)
            candidates=[]
            for prefix in prefixes: candidates.extend(f"{prefix}.{i}" for i in range(1,255))
            # Fast parallel FTP pass. Stop ranking complexity: GoldHEN FTP :2121 is our primary signature.
            found=[]
            with ThreadPoolExecutor(max_workers=160) as ex:
                futs={ex.submit(can_connect,ip,FTP_PORT,0.18):ip for ip in dict.fromkeys(candidates)}
                for fut in as_completed(futs):
                    try:
                        if fut.result(): found.append((futs[fut],5))
                    except Exception: pass
            if found: return found
            # Fallback for FTP disabled: Klog/Payload signatures.
            with ThreadPoolExecutor(max_workers=160) as ex:
                futs={ex.submit(can_connect,ip,p,0.18):(ip,p) for ip in dict.fromkeys(candidates) for p in (KLOG_PORT,BINLOADER_PORT)}
                score={}
                for fut in as_completed(futs):
                    try:
                        if fut.result(): score[futs[fut][0]]=score.get(futs[fut][0],0)+1
                    except Exception: pass
            return [(ip,sc) for ip,sc in score.items() if sc]
        def done(found):
            if not found:
                self.status_var.set("No se encontró la PS4 automáticamente."); messagebox.showinfo(APP_NAME,"No se encontró la PS4. Comprueba que PC y PS4 estén en la misma red y que FTP o Payload Server estén activos."); return
            ip=found[0][0]; self.host_var.set(ip); self.port_var.set(str(FTP_PORT)); self.status_var.set(f"PS4 encontrada: {ip}. Conectando…"); self.connect_ps4()
        self.run_async(task,on_done=done)

    def connect_ps4(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Introduce la IP de la PS4 o pulsa Buscar PS4.")
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showwarning(APP_NAME, "Puerto FTP no válido.")
            return
        self.status_var.set(f"Conectando a {host}:{port}…")

        def task():
            return self.ftp.connect(host, port)

        def done(welcome: str):
            self.settings.set("host", host)
            self.settings.set("ftp_port", port)
            self.connection_var.set(f"● Conectada  {host}:{port}")
            self.status_var.set(welcome)
            self.remote_path = "/"
            self.refresh_remote()
            self.refresh_services(after_connect=True)

        def error(exc: Exception):
            self.connection_var.set("● Desconectada")
            self.status_var.set("Error de conexión")
            messagebox.showerror(APP_NAME, f"No se pudo conectar a {host}:{port}\n\n{exc}")

        self.run_async(task, on_done=done, on_error=error)

    def disconnect_ps4(self) -> None:
        self.stop_klog()
        self.ftp.disconnect()
        self.connection_var.set("● Desconectada")
        self.remote_tree.delete(*self.remote_tree.get_children())
        self.remote_items.clear()
        self.service_state = {p: False for p in self.service_state}
        self._apply_service_state(self.service_state)
        self.status_var.set("Desconectada")

    def refresh_services(self, after_connect: bool = False) -> None:
        host = self.host_var.get().strip()
        if not host:
            return
        self.post_status("Comprobando servicios GoldHEN…")

        def task():
            ports = (FTP_PORT, BINLOADER_PORT, KLOG_PORT, PS4DEBUG_PORT, RPI_PORT)
            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = {p: ex.submit(can_connect, host, p, 0.7) for p in ports}
                state = {p: bool(futs[p].result()) for p in ports}
            if state[PS4DEBUG_PORT]:
                try:
                    state["debug_version"] = PS4DebugClient.version(host)
                except Exception:
                    state[PS4DEBUG_PORT] = False
            if state[RPI_PORT]:
                state["rpi_tcp"] = True
                state[RPI_PORT] = RemotePackageInstallerClient(host).ready()
            return state

        def done(state):
            self._apply_service_state(state)
            active = sum(1 for p in (FTP_PORT, BINLOADER_PORT, KLOG_PORT, PS4DEBUG_PORT, RPI_PORT) if state.get(p))
            self.status_var.set(f"Servicios activos: {active}/5")
            if state.get(RPI_PORT):
                self.rpi_status_var.set("● RPI listo :12800")
            elif state.get("rpi_tcp"):
                self.rpi_status_var.set("◐ RPI abierto pero no responde")
            else:
                self.rpi_status_var.set("○ RPI no disponible")
            if hasattr(self, "console_info_vars"):
                self.refresh_console_info()
            if hasattr(self, "online_tree"):
                self.online_update_states()
            if after_connect and self.notify_connect_var.get():
                if state.get(PS4DEBUG_PORT):
                    self.send_ps4_notification(f"{APP_NAME}\nPC conectado correctamente ✓\nGoldHEN listo para usar 🎮", silent=True)
                elif self.auto_bridge_var.get() and state.get(BINLOADER_PORT) and PS4DEBUG_LOCAL.exists():
                    self._auto_load_bridge_then_notify()
                elif not state.get(BINLOADER_PORT):
                    self.status_var.set("FTP conectado ✓  |  Aviso automático pendiente: activa Payload Server :9090 en GoldHEN")

        self.run_async(task, on_done=done, on_error=lambda e: self.post_status(f"No se pudieron revisar servicios: {e}"))

    def _apply_service_state(self, state) -> None:
        for port in (FTP_PORT, BINLOADER_PORT, KLOG_PORT, PS4DEBUG_PORT, RPI_PORT):
            val = bool(state.get(port, False)) if isinstance(state, dict) else False
            self.service_state[port] = val
            label = {FTP_PORT: "FTP", BINLOADER_PORT: "Payload", KLOG_PORT: "Klog", PS4DEBUG_PORT: "Debug", RPI_PORT: "RPI"}[port]
            self.service_vars[port].set(f"{'●' if val else '○'} {label} :{port}")

    def require_connection(self) -> bool:
        if not self.ftp.connected:
            messagebox.showwarning(APP_NAME, "Primero conecta con la PS4.")
            return False
        return True

    # ---------- notifications / payloads ----------
    def _download_verified_ps4debug(self) -> Path:
        if PS4DEBUG_LOCAL.exists():
            digest = hashlib.sha256(PS4DEBUG_LOCAL.read_bytes()).hexdigest()
            if digest.lower() == PS4DEBUG_NG_SHA256:
                return PS4DEBUG_LOCAL
            try:
                PS4DEBUG_LOCAL.unlink()
            except OSError:
                pass

        tmp = PS4DEBUG_LOCAL.with_suffix(".download")
        req = urllib.request.Request(PS4DEBUG_NG_URL, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as response, tmp.open("wb") as f:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            received = 0
            while True:
                block = response.read(256 * 1024)
                if not block:
                    break
                f.write(block)
                received += len(block)
                self.post_progress(received, total, "Descargando ps4debug-NG")
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest.lower() != PS4DEBUG_NG_SHA256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("La firma SHA-256 del payload descargado no coincide con la publicación oficial.")
        tmp.replace(PS4DEBUG_LOCAL)
        return PS4DEBUG_LOCAL

    def prepare_notifications(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Introduce o detecta primero la IP de la PS4.")
            return
        text = (
            "Para mostrar avisos en la PS4, esta versión usa ps4debug-NG v1.3.2 como puente.\n\n"
            "Se descargará desde su release oficial, se verificará su SHA-256 y se enviará a GoldHEN Payload Server (puerto 9090). "
            "El payload también ofrece funciones de depuración avanzadas, por lo que solo debes usarlo en tu propia red y consola.\n\n"
            "¿Continuar?"
        )
        if not messagebox.askyesno(APP_NAME, text):
            return

        self.status_var.set("Preparando sistema de notificaciones…")
        self.progress["value"] = 0

        def task():
            if can_connect(host, PS4DEBUG_PORT, 0.8):
                version = PS4DebugClient.version(host)
                PS4DebugClient.notify(host, f"{APP_NAME} v{APP_VERSION}\nNotificaciones preparadas.")
                return version
            payload = self._download_verified_ps4debug()
            try:
                PayloadClient.send(host, payload, BINLOADER_PORT, lambda c, t: self.post_progress(c, t, "Cargando ps4debug-NG"))
            except OSError as exc:
                raise ConnectionError(f"No se pudo abrir una conexión nueva con Payload Server {host}:9090 ({exc}). En GoldHEN debe estar marcada 'Enable Payload Server'.") from exc
            deadline = time.time() + 12
            while time.time() < deadline:
                if can_connect(host, PS4DEBUG_PORT, 0.5):
                    version = PS4DebugClient.version(host)
                    PS4DebugClient.notify(host, f"{APP_NAME} v{APP_VERSION}\nNotificaciones preparadas.")
                    return version
                time.sleep(0.35)
            raise TimeoutError("El payload se envió, pero el servicio de avisos no apareció en el puerto 744.")

        def done(version: str):
            self.progress["value"] = 100
            self.auto_bridge_var.set(True)
            self.save_preferences()
            self.status_var.set(f"Notificaciones listas ({version})")
            self.refresh_services()
            messagebox.showinfo(APP_NAME, "Notificaciones preparadas.\n\nA partir de ahora el programa puede mostrar avisos en la pantalla de la PS4. También he activado la recarga automática del puente cuando sea necesario.")

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"No se pudieron preparar los avisos:\n\n{e}"))

    def _auto_load_bridge_then_notify(self) -> None:
        host = self.host_var.get().strip()
        if not host or not PS4DEBUG_LOCAL.exists():
            return

        def task():
            PayloadClient.send(host, PS4DEBUG_LOCAL, BINLOADER_PORT)
            deadline = time.time() + 10
            while time.time() < deadline:
                if can_connect(host, PS4DEBUG_PORT, 0.4):
                    PS4DebugClient.notify(host, f"{APP_NAME}\nPC conectado correctamente ✓\nGoldHEN listo para usar 🎮")
                    return True
                time.sleep(0.35)
            return False

        def done(ok: bool):
            if ok:
                self.service_state[PS4DEBUG_PORT] = True
                self._apply_service_state(self.service_state)
                self.status_var.set("Conectada y notificación enviada")
            else:
                self.status_var.set("FTP conectado; el puente de avisos no respondió")

        self.run_async(task, on_done=done, on_error=lambda e: self.post_status(f"FTP conectado; aviso no disponible: {e}"))

    def send_ps4_notification(self, text: str, silent: bool = False) -> None:
        host = self.host_var.get().strip()
        if not host:
            return

        def task():
            PS4DebugClient.notify(host, text, message_type=NOTIFY_MESSAGE_TYPE)
            return True

        def done(_):
            self.service_state[PS4DEBUG_PORT] = True
            self._apply_service_state(self.service_state)
            if not silent:
                self.status_var.set("Notificación enviada a la PS4")

        def error(exc: Exception):
            self.service_state[PS4DEBUG_PORT] = False
            self._apply_service_state(self.service_state)
            if not silent:
                messagebox.showerror(APP_NAME, f"No se pudo enviar el aviso.\n\nPulsa 'Preparar avisos' si todavía no has cargado el puente.\n\n{exc}")

        self.run_async(task, on_done=done, on_error=error)

    def test_notification(self) -> None:
        self.send_ps4_notification(f"{APP_NAME} v{APP_VERSION}\n¡Conexión con la PS4 correcta!")

    def custom_notification(self) -> None:
        text = simpledialog.askstring(APP_NAME, "Texto que aparecerá en la PS4:", initialvalue="Mensaje enviado desde PS4 GoldHEN Manager")
        if text:
            self.send_ps4_notification(text)

    def send_custom_payload(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Introduce primero la IP de la PS4.")
            return
        path = filedialog.askopenfilename(title="Seleccionar payload", filetypes=[("PS4 payload", "*.bin *.elf"), ("Todos", "*.*")])
        if not path:
            return
        payload = Path(path)
        if not messagebox.askyesno(APP_NAME, f"¿Enviar este payload a {host}:{BINLOADER_PORT}?\n\n{payload.name}\n\nSolo continúa si confías en el archivo y es compatible con tu firmware."):
            return

        def task():
            if not can_connect(host, BINLOADER_PORT, 1.0):
                raise ConnectionError("BinLoader :9090 no está disponible")
            PayloadClient.send(host, payload, BINLOADER_PORT, lambda c, t: self.post_progress(c, t, f"Enviando {payload.name}"))

        def done(_):
            self.progress["value"] = 100
            self.status_var.set(f"Payload enviado: {payload.name}")
            self.refresh_services()

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"Error enviando payload:\n{e}"))

    # ---------- remote ----------
    def refresh_remote(self) -> None:
        if not self.require_connection():
            return
        path = self.remote_path
        self.remote_path_var.set(path)
        self.status_var.set(f"Leyendo {path}…")

        def task():
            return self.ftp.listdir(path)

        def done(items: list[RemoteItem]):
            self.remote_tree.delete(*self.remote_tree.get_children())
            self.remote_items.clear()
            for idx, item in enumerate(items):
                iid = f"remote:{idx}"
                self.remote_items[iid] = item
                self.remote_tree.insert("", END, iid=iid, text=item.name, values=("Carpeta" if item.is_dir else "Archivo", human_size(item.size)))
            self.remote_path_var.set(self.remote_path)
            self.status_var.set(f"{len(items)} elementos en {self.remote_path}")

        def error(exc: Exception):
            messagebox.showerror(APP_NAME, f"No se pudo abrir {path}\n\n{exc}")

        self.run_async(task, on_done=done, on_error=error)

    def remote_quick(self, path: str) -> None:
        if not self.require_connection():
            return
        self.remote_path = path
        self.refresh_remote()

    def remote_double_click(self, _event=None) -> None:
        sel = self.remote_tree.selection()
        if len(sel) == 1:
            item = self.remote_items.get(sel[0])
            if item and item.is_dir:
                self.remote_path = item.path
                self.refresh_remote()

    def remote_up(self) -> None:
        if self.remote_path != "/":
            self.remote_path = posixpath.dirname(self.remote_path.rstrip("/")) or "/"
            self.refresh_remote()

    def remote_go(self) -> None:
        value = self.remote_path_var.get().strip() or "/"
        if not value.startswith("/"):
            value = "/" + value
        self.remote_path = posixpath.normpath(value)
        self.refresh_remote()

    def remote_mkdir(self) -> None:
        if not self.require_connection():
            return
        name = simpledialog.askstring(APP_NAME, "Nombre de la nueva carpeta:")
        if not name:
            return
        target = posixpath.join(self.remote_path, name)
        self.run_async(lambda: self.ftp.mkdir(target), on_done=lambda _r: self.refresh_remote(), on_error=lambda e: messagebox.showerror(APP_NAME, str(e)))

    def remote_rename(self) -> None:
        if not self.require_connection():
            return
        sel = self.remote_tree.selection()
        if len(sel) != 1:
            messagebox.showinfo(APP_NAME, "Selecciona un único archivo o carpeta.")
            return
        item = self.remote_items[sel[0]]
        new_name = simpledialog.askstring(APP_NAME, "Nuevo nombre:", initialvalue=item.name)
        if not new_name or new_name == item.name:
            return
        new_path = posixpath.join(posixpath.dirname(item.path), new_name)
        self.run_async(lambda: self.ftp.rename(item.path, new_path), on_done=lambda _r: self.refresh_remote(), on_error=lambda e: messagebox.showerror(APP_NAME, str(e)))

    def remote_delete(self) -> None:
        if not self.require_connection():
            return
        selected = [self.remote_items[i] for i in self.remote_tree.selection() if i in self.remote_items]
        if not selected:
            return
        names = "\n".join(i.name for i in selected[:12])
        extra = f"\n… y {len(selected)-12} más" if len(selected) > 12 else ""
        if not messagebox.askyesno(APP_NAME, f"¿Borrar {len(selected)} elemento(s) de la PS4?\n\n{names}{extra}\n\nLas carpetas se borrarán de forma recursiva."):
            return

        def delete_recursive(item: RemoteItem):
            if item.is_dir:
                for child in self.ftp.listdir(item.path):
                    delete_recursive(child)
                self.ftp.rmdir(item.path)
            else:
                self.ftp.delete_file(item.path)

        def task():
            for item in selected:
                self.post_status(f"Borrando {item.path}")
                delete_recursive(item)

        self.run_async(task, on_done=lambda _r: self.refresh_remote(), on_error=lambda e: messagebox.showerror(APP_NAME, f"Error al borrar:\n{e}"))

    # ---------- transfers ----------
    def _upload_paths(self, selected: list[Path], base_remote: str, completion_label: str = "Subida completada") -> None:
        self.progress["value"] = 0

        def upload_path(local: Path, remote: str):
            if local.is_dir():
                self.ftp.ensure_dir(remote)
                for child in sorted(local.iterdir(), key=lambda p: p.name.lower()):
                    upload_path(child, posixpath.join(remote, child.name))
            else:
                label = f"Subiendo {local.name}"
                self.ftp.upload_file(local, remote, lambda c, t: self.post_progress(c, t, label))

        def task():
            self.ftp.ensure_dir(base_remote)
            for p in selected:
                upload_path(p, posixpath.join(base_remote, p.name))

        def done(_r):
            self.progress["value"] = 100
            self.status_var.set(completion_label)
            self.refresh_remote()
            if self.notify_transfer_var.get():
                self.send_ps4_notification(f"{APP_NAME}\n{completion_label}", silent=True)

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"Error durante la subida:\n{e}"))

    def upload_selected(self) -> None:
        if not self.require_connection():
            return
        selected = [Path(i) for i in self.local_tree.selection()]
        if not selected:
            messagebox.showinfo(APP_NAME, "Selecciona archivos o carpetas en el panel del PC.")
            return
        self._upload_paths(selected, self.remote_path)

    def upload_pkg_to_data(self) -> None:
        if not self.require_connection():
            return
        paths = filedialog.askopenfilenames(title="Seleccionar PKG", filetypes=[("PS4 PKG", "*.pkg"), ("Todos", "*.*")])
        if not paths:
            return
        files = [Path(p) for p in paths]
        self.remote_path = "/data/pkg"
        self.remote_path_var.set(self.remote_path)
        self._upload_paths(files, "/data/pkg", completion_label=f"{len(files)} PKG subido(s) a /data/pkg")

    def download_selected(self) -> None:
        if not self.require_connection():
            return
        selected = [self.remote_items[i] for i in self.remote_tree.selection() if i in self.remote_items]
        if not selected:
            messagebox.showinfo(APP_NAME, "Selecciona archivos o carpetas en el panel de la PS4.")
            return
        base_local = self.local_path
        self.progress["value"] = 0

        def download_item(item: RemoteItem, local_target: Path):
            if item.is_dir:
                local_target.mkdir(parents=True, exist_ok=True)
                for child in self.ftp.listdir(item.path):
                    download_item(child, local_target / child.name)
            else:
                label = f"Descargando {item.name}"
                self.ftp.download_file(item.path, local_target, lambda c, t: self.post_progress(c, t, label))

        def task():
            for item in selected:
                download_item(item, base_local / item.name)

        def done(_r):
            self.progress["value"] = 100
            self.status_var.set("Descarga completada")
            self.refresh_local()
            if self.notify_transfer_var.get():
                self.send_ps4_notification(f"{APP_NAME}\nDescarga al PC completada.", silent=True)

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"Error durante la descarga:\n{e}"))

    # ---------- remote PKG installer ----------
    def add_pkg_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Añadir PKG", filetypes=[("PS4 PKG", "*.pkg"), ("Todos", "*.*")])
        if not paths:
            return
        self.status_var.set("Leyendo metadatos de PKG…")

        def task():
            return [inspect_pkg(Path(p)) for p in paths]

        def done(infos: list[PkgInfo]):
            for info in infos:
                key = "pkg:" + hashlib.sha1(str(info.path.resolve()).encode("utf-8")).hexdigest()
                self.pkg_items[key] = info
                if self.pkg_tree.exists(key):
                    self.pkg_tree.delete(key)
                title = info.title or ("PKG válido" if info.valid else f"ERROR: {info.error}")
                self.pkg_tree.insert(
                    "", END, iid=key, text=info.name,
                    values=(title, info.title_id, info.pkg_type, info.version, human_size(info.size), info.content_id),
                )
            valid = sum(1 for i in infos if i.valid)
            self.status_var.set(f"{valid}/{len(infos)} PKG válidos añadidos")

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"No se pudieron leer los PKG:\n{e}"))

    def remove_pkg_files(self) -> None:
        for iid in self.pkg_tree.selection():
            self.pkg_items.pop(iid, None)
            if self.pkg_tree.exists(iid):
                self.pkg_tree.delete(iid)

    def _selected_pkg_infos(self) -> list[PkgInfo]:
        return [self.pkg_items[i] for i in self.pkg_tree.selection() if i in self.pkg_items]

    def check_rpi(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Introduce o detecta primero la IP de la PS4.")
            return
        self.rpi_status_var.set("Comprobando RPI…")

        def task():
            tcp = can_connect(host, RPI_PORT, 1.0)
            ready = RemotePackageInstallerClient(host).ready() if tcp else False
            return tcp, ready

        def done(result):
            tcp, ready = result
            self.service_state[RPI_PORT] = ready
            self._apply_service_state(self.service_state)
            if ready:
                self.rpi_status_var.set("● RPI listo :12800")
                self.status_var.set("Remote Package Installer responde correctamente")
            elif tcp:
                self.rpi_status_var.set("◐ RPI abierto pero bloqueado")
                messagebox.showwarning(APP_NAME, "El puerto 12800 está abierto, pero la API no responde. Reinicia/abre Remote Package Installer en la PS4 y déjalo en primer plano.")
            else:
                self.rpi_status_var.set("○ RPI no disponible")
                messagebox.showinfo(APP_NAME, "Remote Package Installer no responde en el puerto 12800.\n\nÁbrelo en la PS4 y mantenlo en primer plano al iniciar la instalación.")

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, str(e)))

    def _pkg_server_parameters(self) -> tuple[str, int, str]:
        host = self.host_var.get().strip()
        if not host:
            raise ValueError("Introduce o detecta primero la IP de la PS4.")
        try:
            port = int(self.pkg_http_port_var.get())
        except ValueError as exc:
            raise ValueError("Puerto del servidor PKG no válido.") from exc
        if not (1 <= port <= 65535):
            raise ValueError("Puerto del servidor PKG fuera de rango.")
        bind_ip = get_local_ipv4_for(host)
        if bind_ip.startswith("127."):
            raise RuntimeError("No se pudo determinar una IP LAN del PC accesible desde la PS4.")
        return host, port, bind_ip

    def _start_pkg_install(self, infos: list[PkgInfo], as_parts: bool) -> None:
        if not infos:
            messagebox.showinfo(APP_NAME, "Selecciona al menos un PKG.")
            return
        invalid = [i for i in infos if not i.valid]
        if invalid:
            messagebox.showerror(APP_NAME, f"Hay PKG que no superaron la comprobación de cabecera:\n\n{invalid[0].name}: {invalid[0].error}")
            return
        if not as_parts and len(infos) != 1:
            messagebox.showinfo(APP_NAME, "Para 'Instalar seleccionado' elige un solo PKG. Si son piezas del mismo paquete, usa 'Instalar como partes'.")
            return
        if as_parts and len(infos) < 2:
            messagebox.showinfo(APP_NAME, "Selecciona dos o más piezas del mismo PKG.")
            return
        try:
            host, requested_port, bind_ip = self._pkg_server_parameters()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        if as_parts:
            names = "\n".join(i.name for i in infos[:8])
            if not messagebox.askyesno(APP_NAME, f"Se enviarán {len(infos)} archivos como PIEZAS consecutivas de un único paquete.\n\n{names}\n\nContinúa solo si realmente pertenecen al mismo PKG dividido."):
                return

        self.pkg_progress["value"] = 0
        self.pkg_detail_var.set("Preparando servidor HTTP con soporte Range…")
        self.status_var.set("Preparando instalación remota…")

        def task():
            client = RemotePackageInstallerClient(host)
            if not client.ready():
                raise ConnectionError("Remote Package Installer no responde correctamente en :12800. Ábrelo en la PS4 y mantenlo en primer plano.")
            actual_port = self.pkg_server.start(bind_ip, requested_port)
            urls = [self.pkg_server.register(i.path) for i in infos]
            result = client.install(urls)
            task_id = result.get("task_id")
            if task_id is None:
                raise RuntimeError(f"RPI aceptó la conexión pero no devolvió task_id: {result}")
            return int(task_id), str(result.get("title") or infos[0].title or infos[0].name), bind_ip, actual_port, urls

        def done(result):
            task_id, title, ip, port, urls = result
            self.settings.set("pkg_http_port", port)
            self.pkg_http_port_var.set(str(port))
            self.pkg_server_var.set(f"● http://{ip}:{port}  ({len(self.pkg_server.files)} PKG)")
            self.rpi_task_id = task_id
            self.rpi_task_active = True
            self.pkg_task_var.set(f"Tarea #{task_id} — {title}")
            self.pkg_detail_var.set("Tarea aceptada por la PS4. No cierres PS4 GoldHEN Manager mientras la consola siga descargando el PKG del PC.")
            self.service_state[RPI_PORT] = True
            self._apply_service_state(self.service_state)
            self.rpi_status_var.set("● RPI listo :12800")
            self._start_rpi_monitor(task_id, title)

        def error(exc: Exception):
            self.pkg_detail_var.set(str(exc))
            messagebox.showerror(APP_NAME, f"No se pudo iniciar la instalación remota:\n\n{exc}")

        self.run_async(task, on_done=done, on_error=error)

    def install_selected_pkg(self) -> None:
        self._start_pkg_install(self._selected_pkg_infos(), as_parts=False)

    def install_selected_as_parts(self) -> None:
        self._start_pkg_install(self._selected_pkg_infos(), as_parts=True)

    def upload_pkg_from_installer_tab(self) -> None:
        if not self.require_connection():
            return
        infos = self._selected_pkg_infos()
        if not infos:
            messagebox.showinfo(APP_NAME, "Selecciona uno o más PKG en la lista.")
            return
        self.remote_path = "/data/pkg"
        self.remote_path_var.set(self.remote_path)
        self._upload_paths([i.path for i in infos], "/data/pkg", completion_label=f"{len(infos)} PKG subido(s) a /data/pkg")

    def stop_pkg_server(self) -> None:
        if self.rpi_task_active:
            if not messagebox.askyesno(APP_NAME, "Hay una tarea PKG que puede seguir descargando desde este PC. Si detienes el servidor ahora, la instalación puede fallar.\n\n¿Detenerlo igualmente?"):
                return
        self.pkg_server.stop()
        self.pkg_server_var.set("Detenido")
        self.status_var.set("Servidor PKG detenido")

    def _start_rpi_monitor(self, task_id: int, title: str) -> None:
        self.rpi_monitor_stop.set()
        old = self.rpi_monitor_thread
        if old and old.is_alive() and old is not threading.current_thread():
            old.join(timeout=0.2)
        self.rpi_monitor_stop = threading.Event()
        stop_event = self.rpi_monitor_stop
        host = self.host_var.get().strip()

        def worker():
            client = RemotePackageInstallerClient(host)
            failures = 0
            while not stop_event.is_set():
                try:
                    data = client.progress(task_id)
                    failures = 0
                    data["_task_id"] = task_id
                    data["_title"] = title
                    self.ui_queue.put(("pkg_task", None, data))
                    length = int(data.get("length_total") or data.get("length") or 0)
                    transferred = int(data.get("transferred_total") or data.get("transferred") or 0)
                    local_copy = int(data.get("local_copy_percent") or 0)
                    error_code = int(data.get("error") or 0)
                    if error_code:
                        break
                    if length > 0 and transferred >= length and local_copy >= 100:
                        break
                except Exception as exc:
                    failures += 1
                    if failures >= 5:
                        self.ui_queue.put(("pkg_task", None, {"_task_id": task_id, "_title": title, "_monitor_error": str(exc)}))
                        break
                stop_event.wait(1.25)

        self.rpi_monitor_thread = threading.Thread(target=worker, name="rpi-monitor", daemon=True)
        self.rpi_monitor_thread.start()

    def _apply_pkg_task_progress(self, data: dict) -> None:
        if data.get("_monitor_error"):
            self.pkg_detail_var.set(f"No se pudo seguir consultando la tarea: {data['_monitor_error']}")
            return
        task_id = data.get("_task_id")
        title = str(data.get("_title") or "PKG")
        length = int(data.get("length_total") or data.get("length") or 0)
        transferred = int(data.get("transferred_total") or data.get("transferred") or 0)
        preparing = max(0, int(data.get("preparing_percent") or 0))
        local_copy = max(0, int(data.get("local_copy_percent") or 0))
        rest = max(0, int(data.get("rest_sec_total") or data.get("rest_sec") or 0))
        error_code = int(data.get("error") or 0)
        pct = (transferred * 100 / length) if length > 0 else float(preparing)
        if length > 0 and transferred >= length:
            pct = max(pct, float(local_copy))
        pct = max(0.0, min(100.0, pct))
        self.pkg_progress["value"] = pct
        self.progress["value"] = pct
        if error_code:
            self.rpi_task_active = False
            self.pkg_task_var.set(f"Tarea #{task_id} — ERROR 0x{error_code & 0xFFFFFFFF:08X}")
            self.pkg_detail_var.set(f"La PS4 devolvió un error en la tarea de {title}.")
            return
        if preparing and transferred == 0:
            phase = f"Preparando {preparing}%"
        elif length > 0 and transferred < length:
            phase = "Descargando desde el PC"
        elif local_copy < 100:
            phase = f"Instalando {local_copy}%"
        else:
            phase = "Completado"
        eta = f" · {rest}s restantes" if rest and phase != "Completado" else ""
        amount = f" · {human_size(transferred)} / {human_size(length)}" if length else ""
        self.pkg_task_var.set(f"Tarea #{task_id} — {title} — {phase}")
        self.pkg_detail_var.set(f"{pct:.1f}%{amount}{eta} · HTTP servido por el PC: {human_size(self.pkg_server.bytes_served)} en {self.pkg_server.requests} peticiones")
        self.status_var.set(f"PKG: {title} — {pct:.1f}%")
        if phase == "Completado":
            was_active = self.rpi_task_active
            self.rpi_task_active = False
            self.pkg_progress["value"] = 100
            self.progress["value"] = 100
            if was_active and self.notify_transfer_var.get():
                self.send_ps4_notification(f"{APP_NAME}\nPKG completado: {title}", silent=True)

    def pkg_task_action(self, action: str) -> None:
        if self.rpi_task_id is None:
            messagebox.showinfo(APP_NAME, "No hay una tarea PKG seleccionada/activa.")
            return
        host = self.host_var.get().strip()
        task_id = self.rpi_task_id

        def task():
            return RemotePackageInstallerClient(host).task_action(action, task_id)

        def done(_):
            labels = {"pause": "pausada", "resume": "reanudada", "stop": "detenida", "unregister": "eliminada", "start": "iniciada"}
            self.status_var.set(f"Tarea #{task_id} {labels.get(action, action)}")
            if action == "resume":
                self.rpi_task_active = True
                self._start_rpi_monitor(task_id, self.pkg_task_var.get().split(" — ")[1] if " — " in self.pkg_task_var.get() else "PKG")
            elif action == "unregister":
                self.rpi_monitor_stop.set()
                self.rpi_task_id = None
                self.rpi_task_active = False
                self.pkg_task_var.set("Sin tarea activa")
                self.pkg_progress["value"] = 0
            elif action == "stop":
                self.rpi_task_active = False

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"No se pudo {action} la tarea #{task_id}:\n{e}"))

    # ---------- game library / app.db ----------
    @staticmethod
    def _parse_app_db(path: Path) -> tuple[list[dict[str, str]], int]:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        rows: dict[str, dict[str, str]] = {}
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tbl_appbrowse_%'")]
            for table in tables:
                safe = table.replace('"', '""')
                info = list(conn.execute(f'PRAGMA table_info("{safe}")'))
                cols = [r[1] for r in info]
                by_lower = {c.lower(): c for c in cols}
                title_col = by_lower.get("titleid")
                if not title_col:
                    continue
                name_col = next((by_lower[k] for k in ("titlename", "name", "title") if k in by_lower), None)
                content_col = by_lower.get("contentid")
                visible_col = by_lower.get("visible")
                folder_col = by_lower.get("foldertype")
                query = f'SELECT * FROM "{safe}"'
                for row in conn.execute(query):
                    title_id = str(row[title_col] or "").strip()
                    if not re.match(r"^(CUSA|PCAS|PLAS|NPXS|NPXX)[A-Z0-9_-]*$", title_id, re.IGNORECASE):
                        continue
                    if folder_col is not None:
                        try:
                            if int(row[folder_col] or 0) != 0:
                                continue
                        except Exception:
                            pass
                    name = str(row[name_col] or "").strip() if name_col else ""
                    content = str(row[content_col] or "").strip() if content_col else ""
                    visible = ""
                    if visible_col is not None:
                        try:
                            visible = "Sí" if int(row[visible_col]) else "No"
                        except Exception:
                            visible = str(row[visible_col] or "")
                    current = rows.get(title_id)
                    candidate = {"title_id": title_id, "name": name or title_id, "content_id": content, "visible": visible}
                    if current is None or (candidate["name"] != title_id and current["name"] == title_id):
                        rows[title_id] = candidate

            # Fallback names from tbl_appinfo when appbrowse has only IDs.
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tbl_appinfo'").fetchone()
            if exists:
                try:
                    for r in conn.execute("SELECT titleid, key, val FROM tbl_appinfo"):
                        tid = str(r[0] or "").strip()
                        key = str(r[1] or "").upper()
                        val = str(r[2] or "").strip()
                        if tid in rows and rows[tid]["name"] == tid and key in ("TITLE", "TITLE_NAME") and val:
                            rows[tid]["name"] = val
                except Exception:
                    pass
        finally:
            conn.close()
        result = sorted(rows.values(), key=lambda x: (x["name"].lower(), x["title_id"]))
        return result, len(tables)

    def load_game_library(self) -> None:
        if not self.require_connection():
            return
        local_db = app_data_dir() / "app.db"
        remote_db = "/system_data/priv/mms/app.db"
        self.progress["value"] = 0
        self.status_var.set("Descargando app.db…")

        def task():
            self.ftp.download_file(remote_db, local_db, lambda c, t: self.post_progress(c, t, "Descargando app.db"))
            return self._parse_app_db(local_db)

        def done(result):
            rows, table_count = result
            self.library_rows = rows
            self.filter_library()
            self.progress["value"] = 100
            self.status_var.set(f"Biblioteca cargada: {len(rows)} títulos / {table_count} tabla(s) de usuario")

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"No se pudo cargar la biblioteca:\n\n{e}"))

    def filter_library(self) -> None:
        if not hasattr(self, "library_tree"):
            return
        needle = self.library_filter_var.get().strip().lower() if hasattr(self, "library_filter_var") else ""
        self.library_tree.delete(*self.library_tree.get_children())
        shown = 0
        for row in self.library_rows:
            hay = " ".join((row["title_id"], row["name"], row["content_id"])).lower()
            if needle and needle not in hay:
                continue
            iid = row["title_id"]
            content = row["title_id"] + (f"  •  {row['content_id']}" if row["content_id"] else "")
            self.library_tree.insert("", END, iid=iid, values=(row["name"], content, row["visible"]))
            shown += 1
        self.library_count_var.set(f"{shown}/{len(self.library_rows)} títulos")

    def library_open_selected(self) -> None:
        sel = self.library_tree.selection() if hasattr(self, "library_tree") else ()
        if not sel:
            messagebox.showinfo(APP_NAME, "Selecciona un juego de la biblioteca.")
            return
        title_id = sel[0]
        self.remote_path = f"/user/app/{title_id}"
        self.remote_path_var.set(self.remote_path)
        self.notebook.select(self.files_tab)
        self.refresh_remote()

    def backup_app_db(self) -> None:
        if not self.require_connection():
            return
        default = f"app_db_backup_{time.strftime('%Y%m%d_%H%M%S')}.db"
        out = filedialog.asksaveasfilename(title="Guardar copia de app.db", initialfile=default, defaultextension=".db", filetypes=[("SQLite DB", "*.db"), ("Todos", "*.*")])
        if not out:
            return
        target = Path(out)
        self.progress["value"] = 0

        def task():
            self.ftp.download_file("/system_data/priv/mms/app.db", target, lambda c, t: self.post_progress(c, t, "Backup app.db"))
            return target

        def done(path):
            self.progress["value"] = 100
            self.status_var.set(f"Backup guardado: {path}")

        self.run_async(task, on_done=done, on_error=lambda e: messagebox.showerror(APP_NAME, f"No se pudo guardar el backup:\n\n{e}"))

    # ---------- Klog ----------
    def start_klog(self) -> None:
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Introduce primero la IP de la PS4.")
            return
        if self.klog_thread and self.klog_thread.is_alive():
            self.status_var.set("Klog ya está activo")
            return
        self.klog_stop.clear()
        self.klog_text.insert(END, f"\n--- Conectando Klog {host}:{KLOG_PORT} ---\n")

        def worker():
            try:
                with socket.create_connection((host, KLOG_PORT), timeout=4.0) as s:
                    s.settimeout(1.0)
                    self.ui_queue.put(("status", None, "Klog conectado"))
                    while not self.klog_stop.is_set():
                        try:
                            data = s.recv(8192)
                            if not data:
                                break
                            self.ui_queue.put(("klog", None, data.decode("utf-8", errors="replace")))
                        except socket.timeout:
                            continue
                self.ui_queue.put(("klog", None, "\n--- Klog desconectado ---\n"))
            except Exception as exc:
                self.ui_queue.put(("klog", None, f"\n[Klog error] {exc}\n"))

        self.klog_thread = threading.Thread(target=worker, daemon=True)
        self.klog_thread.start()

    def stop_klog(self) -> None:
        self.klog_stop.set()
        if self.klog_thread and self.klog_thread.is_alive():
            self.status_var.set("Deteniendo Klog…")

    def on_close(self) -> None:
        self.stop_klog()
        self.rpi_monitor_stop.set()
        self.settings.set("host", self.host_var.get().strip())
        try:
            self.settings.set("ftp_port", int(self.port_var.get()))
        except Exception:
            pass
        try:
            self.settings.set("pkg_http_port", int(self.pkg_http_port_var.get()))
        except Exception:
            pass
        self.settings.set("local_path", str(self.local_path))
        try:
            self.pkg_server.stop()
            self.ftp.disconnect()
        finally:
            self.destroy()


if __name__ == "__main__":
    PS4ManagerApp().mainloop()

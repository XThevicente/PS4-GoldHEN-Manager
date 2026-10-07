"""Local ROM catalogue and explicitly configured PC emulator launcher."""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import struct
import threading
from pathlib import Path

SYSTEMS = {
    "nes": ("NES", {".nes"}), "snes": ("SNES", {".sfc", ".smc"}),
    "gb": ("GAME BOY", {".gb"}), "gbc": ("GAME BOY COLOR", {".gbc"}),
    "gba": ("GAME BOY ADVANCE", {".gba"}),
    "megadrive": ("MEGA DRIVE", {".md", ".gen", ".smd"}),
    "ps1": ("PLAYSTATION", {".cue", ".chd", ".pbp"}),
    "n64": ("NINTENDO 64", {".z64", ".n64", ".v64"}),
    "homebrew": ("HOMEBREW", {".rom"}),
}

def validate_emulator(executable, *, platform_name=None):
    """Validate an installed executable before saving or launching a profile."""
    if not str(executable).strip():
        raise ValueError("Selecciona el ejecutable del emulador instalado en el PC")
    try:
        exe = Path(executable).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("No se encuentra el emulador. Vuelve a seleccionarlo en Configurar emulador") from exc
    if not exe.is_file():
        raise ValueError("Selecciona el archivo ejecutable del emulador")
    if exe.suffix.lower() in {".nes", ".sfc", ".smc", ".gb", ".gbc", ".gba", ".md", ".gen", ".smd", ".cue", ".chd", ".pbp", ".n64", ".z64", ".v64", ".rom", ".bin"}:
        raise ValueError("Has seleccionado un juego. En Emulador elige Mesen.exe o el EXE de tu emulador; las ROMs van en Carpeta ROMs")
    if exe.suffix.lower() in {".dll", ".so", ".dylib"}:
        raise ValueError("Ese archivo es un core o biblioteca. Selecciona retroarch.exe como emulador y usa el core en los argumentos")
    if exe.suffix.lower() in {".zip", ".7z", ".rar"}:
        raise ValueError("Extrae la descarga del emulador y selecciona el EXE de dentro")
    if (platform_name or os.name) == "nt":
        if exe.suffix.lower() != ".exe":
            raise ValueError("En Windows selecciona el archivo .exe del emulador")
        with exe.open("rb") as file:
            header = file.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                raise ValueError("El archivo no tiene una cabecera EXE de Windows. Descarga y extrae la version Windows del emulador")
            offset = struct.unpack_from("<I", header, 60)[0]
            if offset < 64 or offset + 26 > exe.stat().st_size:
                raise ValueError("El EXE esta incompleto o danado; vuelve a extraerlo o descargarlo")
            file.seek(offset)
            pe = file.read(26)
            if pe[:4] != b"PE\x00\x00":
                raise ValueError("El archivo no es un ejecutable PE de Windows")
            characteristics = struct.unpack_from("<H", pe, 22)[0]
            if characteristics & 0x2000:
                raise ValueError("El archivo es una DLL aunque se llame .exe. Selecciona el EXE principal del emulador")
            optional_size = struct.unpack_from("<H", pe, 20)[0]
            magic = struct.unpack_from("<H", pe, 24)[0]
            if not characteristics & 0x0002 or optional_size < 2 or magic not in (0x10b, 0x20b) or offset + 24 + optional_size > exe.stat().st_size:
                raise ValueError("El EXE no tiene una cabecera ejecutable valida o esta incompleto")
    elif not os.access(exe, os.X_OK):
        raise ValueError("El archivo del emulador no tiene permiso de ejecucion")
    return exe

class RetroManager:
    def __init__(self, directory: Path):
        self.directory = directory
        self.config_path = directory / "retro_config.json"
        self.lock = threading.RLock()
        self.config = {"root": "", "emulators": {}}
        if self.config_path.exists():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw.get("root"), str) and isinstance(raw.get("emulators"), dict):
                    self.config = raw
            except (ValueError, OSError, AttributeError):
                pass
        self.entries = []
        self.process = None
        self.running_id = None
        self.requests = {}

    def _save(self):
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.config_path)

    def set_root(self, path):
        root = Path(path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Selecciona una carpeta de ROMs")
        with self.lock:
            self.config["root"] = str(root)
            self.entries = []
            self._save()

    def configure_emulator(self, system, executable, arguments):
        if system not in SYSTEMS:
            raise ValueError("Sistema desconocido")
        exe = validate_emulator(executable)
        if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
            raise ValueError('Argumentos: lista JSON, por ejemplo ["{rom}"]')
        if not any("{rom}" in a for a in arguments):
            raise ValueError("Los argumentos deben incluir {rom}")
        with self.lock:
            self.config["emulators"][system] = {"executable": str(exe), "arguments": arguments}
            self._save()

    def scan(self):
        with self.lock:
            root_text = self.config["root"]
        if not root_text:
            return 0
        root = Path(root_text).resolve(strict=True)
        result = []
        for system, (_, extensions) in SYSTEMS.items():
            folder = root / system
            if not folder.is_dir() or folder.is_symlink():
                continue
            for current, dirs, files in os.walk(folder, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
                for name in sorted(files):
                    path = Path(current) / name
                    if path.is_symlink() or path.suffix.lower() not in extensions:
                        continue
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        continue
                    relative = resolved.relative_to(root).as_posix()
                    cover = path.with_suffix(".png")
                    result.append({"id": hashlib.sha256(relative.encode()).hexdigest()[:16],
                                   "title": path.stem, "system": system, "path": str(resolved),
                                   "cover": str(cover) if cover.is_file() and not cover.is_symlink() else ""})
                    if len(result) >= 20000:
                        raise ValueError("Máximo 20000 ROMs; selecciona una carpeta más pequeña")
        with self.lock:
            if self.config["root"] != root_text:
                raise ValueError("La carpeta cambió durante el escaneo; vuelve a escanear")
            self.entries = sorted(result, key=lambda e: (e["system"], e["title"].casefold(), e["path"]))
        return len(result)

    def page(self, offset=0, limit=6, system=""):
        if system and system not in SYSTEMS:
            raise ValueError("Sistema desconocido")
        with self.lock:
            entries = [e for e in self.entries if not system or e["system"] == system]
            selected = entries[max(0, offset):max(0, offset) + min(6, max(1, limit))]
            items = [{"rom_id": e["id"], "title": e["title"], "system": e["system"],
                      "configured": e["system"] in self.config["emulators"]} for e in selected]
            return {"ok": True, "total": len(entries), "offset": max(0, offset), "items": items}

    def status(self):
        with self.lock:
            active = self.process is not None and self.process.poll() is None
            return {"ok": True, "running": active, "rom_id": self.running_id if active else None,
                    "display": "PC", "streaming": False}

    def launch(self, rom_id, request_id):
        with self.lock:
            if not request_id or len(request_id) > 64:
                raise ValueError("request_id requerido")
            if request_id in self.requests:
                previous_id, response = self.requests[request_id]
                if previous_id != rom_id:
                    raise ValueError("request_id reutilizado con otra ROM")
                return dict(response)
            if self.status()["running"]:
                raise ValueError("Ya hay un emulador abierto por Retro Manager")
            entry = next((e for e in self.entries if e["id"] == rom_id), None)
            if not entry:
                raise ValueError("ROM no encontrada; escanea la biblioteca")
            profile = self.config["emulators"].get(entry["system"])
            if not profile:
                raise ValueError("Configura el emulador de este sistema en el PC")
            root = Path(self.config["root"]).resolve(strict=True)
            path = Path(entry["path"]).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("La ROM ya no está dentro de la biblioteca")
            exe = validate_emulator(profile["executable"])
            argv = [str(exe)] + [a.replace("{rom}", str(path)) for a in profile["arguments"]]
            try:
                self.process = subprocess.Popen(argv, cwd=str(exe.parent),
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL, shell=False)
            except OSError as exc:
                if getattr(exc, "winerror", None) in (193, 216):
                    raise ValueError("Windows no puede abrir el emulador. Selecciona un EXE compatible con tu PC y comprueba que abre directamente") from exc
                raise
            self.running_id = rom_id
            response = {"ok": True, "result": "LAUNCHED_ON_PC", "rom_id": rom_id, "streaming": False}
            self.requests[request_id] = (rom_id, response)
            if len(self.requests) > 128:
                del self.requests[next(iter(self.requests))]
            return dict(response)

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None
            self.running_id = None
            return {"ok": True, "result": "STOPPED"}

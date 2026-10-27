"""Verified Xbox Controller Plugin v1.4.2 resources for PS4 GoldHEN.

All plugin file verification is local and strictly matches official release
checksums. The optional FTP action stages the approved PRX without overwriting.
An INI preparation tool backs up the remote config and generates a *local*
candidate; it NEVER overwrites the console's plugins.ini.
"""
from __future__ import annotations

import ftplib
import hashlib
import tempfile
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from console_profiles import PS4
from resumable_ftp import ResumableFTP

RELEASE_URL = "https://github.com/ChiricoG/xbox_controller_plugin/releases/tag/v1.4.2"
SOURCE_URL = "https://github.com/ChiricoG/xbox_controller_plugin"
PLUGIN_PATH = "/data/GoldHEN/plugins/xbox_controller.prx"
CONFIG_PATH = "/data/GoldHEN/plugins.ini"
FILES = {
    "xbox_controller.prx": (
        108752,
        "d9c104e824ff76f868dc60935b876866f5cb73259e7283f046e4f6d9f97b970b",
    ),
    "xbox_controller_configurator.pkg": (
        8192000,
        "53618788062f572d9a28862ded36894826f9a2ac024ead5668958170cc488bb4",
    ),
}
LINKS = {"release": RELEASE_URL, "source": SOURCE_URL}


@dataclass(frozen=True)
class VerifiedArtifact:
    path: Path
    size: int
    sha256: str


def official_link(which: str) -> str:
    if which not in LINKS:
        raise ValueError("Enlace no autorizado")
    return LINKS[which]


def verify_artifact(local: Path, expected_name: str) -> VerifiedArtifact:
    if expected_name not in FILES:
        raise ValueError("Archivo desconocido")
    if not local.is_file():
        raise FileNotFoundError(str(local))
    expected_size, expected_sha = FILES[expected_name]
    size = local.stat().st_size
    if size != expected_size:
        raise ValueError(
            f"Tamaño incorrecto para {expected_name}: {size}, se esperaban {expected_size} bytes."
        )
    digest = hashlib.sha256()
    with local.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_sha = digest.hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(
            f"SHA-256 distinto al original de {expected_name}: {actual_sha}"
        )
    return VerifiedArtifact(local, size, actual_sha)


def prepare_plugins_ini(original: bytes) -> tuple[bytes, bool]:
    """Add the plugin under [default], leaving unrelated lines unchanged.

    Reject oversized, malformed or ambiguous files instead of overwriting
    potentially important PS4 settings. Newline style and UTF-8 BOM preserved.
    """
    if len(original) > 128 * 1024 or b"\x00" in original:
        raise ValueError("plugins.ini demasiado grande o no válido")
    try:
        text = original.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("plugins.ini no es UTF-8; revisa el backup manualmente") from exc
    if "\r" in text.replace("\r\n", ""):
        raise ValueError("Saltos de línea no admitidos en plugins.ini")
    newline = "\r\n" if "\r\n" in text else "\n"
    had_bom = original.startswith(b"\xef\xbb\xbf")
    lines = text.splitlines(keepends=True)
    matched = [i for i, line in enumerate(lines)
               if line.strip().casefold() == PLUGIN_PATH.casefold()]
    if matched:
        return original, False
    headers = [i for i, line in enumerate(lines)
               if line.strip().casefold() == "[default]"]
    if len(headers) > 1:
        raise ValueError("Hay varias secciones [default]; revisa plugins.ini manualmente")
    if headers:
        start = headers[0] + 1
        end = next((i for i in range(start, len(lines))
                    if lines[i].lstrip().startswith("[")
                    and lines[i].strip().endswith("]")), len(lines))
        if end > 0 and lines[end - 1] and not lines[end - 1].endswith(("\n", "\r")):
            lines[end - 1] += newline
        lines.insert(end, PLUGIN_PATH + newline)
        result = "".join(lines)
    else:
        result = text
        if result and not result.endswith("\n"):
            result += newline
        if result:
            result += newline
        result += "[default]" + newline + PLUGIN_PATH + newline
    data = result.encode("utf-8")
    return (b"\xef\xbb\xbf" + data if had_bom else data), True


def local_ini_preview(ftp, out_dir: Path) -> tuple[Path | None, Path, bool]:
    """Download read-only config, then create a local candidate. No remote writes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise FileExistsError("El directorio de backup no está vacío")
    backup = out_dir / "plugins.ini.original"
    remote_missing = False
    try:
        ftp.download_file(CONFIG_PATH, backup)
    except ftplib.error_perm as exc:
        if not str(exc).startswith("550"):
            raise
        remote_missing = True
    if remote_missing:
        if backup.exists():
            backup.unlink()
        content = b""
    else:
        content = backup.read_bytes()
    candidate, changed = prepare_plugins_ini(content)
    proposed = out_dir / "plugins.ini.PREVIEW"
    with proposed.open("xb") as stream:
        stream.write(candidate)
    return (None if remote_missing else backup), proposed, changed


class XboxControllerMixin:
    def _build_xbox_controller_tab(self) -> None:
        page = ttk.Frame(self.xbox_controller_tab, padding=(14, 12))
        page.pack(fill="both", expand=True)
        intro = ttk.LabelFrame(page, text="Xbox Controller Plugin · v1.4.2", padding=14)
        intro.pack(fill="x", pady=(0, 9))
        ttk.Label(intro, text=(
            "Mandos Xbox 360, One, Series X|S, XInput y algunos PDP Switch por USB. "
            "Juegos y homebrew; NO controla el XMB nativo ni admite Bluetooth."),
            wraplength=950, justify="left").pack(anchor="w", pady=(2, 5))
        ttk.Label(intro, text=(
            "El configurador .pkg es opcional: incluye prueba de sticks y gatillos, "
            "calibración del drift, ajustes y pruebas de vibración. "
            "Mantén un DualShock 4 para el arranque y la navegación del sistema."),
            wraplength=950, justify="left").pack(anchor="w")
        links = ttk.Frame(intro)
        links.pack(fill="x", pady=(12, 0))
        ttk.Button(links, text="GitHub · release original",
                   command=lambda: self._xbox_open("release")).pack(side="left", padx=(0, 8))
        ttk.Button(links, text="Código y guía del autor",
                   command=lambda: self._xbox_open("source")).pack(side="left")

        files = ttk.LabelFrame(page, text="Comprobar archivos descargados", padding=14)
        files.pack(fill="x", pady=(0, 9))
        ttk.Button(files, text="Verificar xbox_controller.prx",
                   command=self.xbox_verify_prx).pack(side="left", padx=(0, 8))
        ttk.Button(files, text="Verificar configurador .pkg",
                   command=self.xbox_verify_pkg).pack(side="left")
        setup = ttk.LabelFrame(page, text="Preparación segura para GoldHEN", padding=14)
        setup.pack(fill="x", pady=(0, 9))
        ttk.Label(setup, text=(
            "1. Verifica el PRX original. 2. Puedes subirlo a /data/GoldHEN/plugins "
            "sin reemplazar archivos existentes. 3. Haz backup del plugins.ini y "
            "genera una propuesta local; nada se activa automáticamente."),
            wraplength=950, justify="left").pack(anchor="w", pady=(0, 9))
        ttk.Button(setup, text="Subir PRX verificado (sin sobrescribir)",
                   command=self.xbox_upload_prx).pack(side="left", padx=(0, 8))
        ttk.Button(setup, text="Backup + propuesta de plugins.ini",
                   command=self.xbox_prepare_ini).pack(side="left")
        ttk.Label(page, text=(
            "AVISO: después de revisar el archivo de propuesta, la activación es manual "
            "desde tu editor FTP. Antes de modificar el plugins.ini original, "
            "conserva el backup. No se ha validado este plugin en nuestra PS4."),
            wraplength=1050, justify="left").pack(anchor="w", pady=(10, 0))

    @staticmethod
    def _xbox_open(which: str) -> None:
        webbrowser.open_new_tab(official_link(which))

    def _xbox_choose(self, expected_name: str) -> Path | None:
        raw = filedialog.askopenfilename(
            title="Seleccionar " + expected_name,
            filetypes=[("Archivos GoldHEN", "*.prx *.pkg"), ("Todos los archivos", "*.*")])
        return Path(raw) if raw else None

    def _xbox_verify_ui(self, name: str) -> None:
        local = self._xbox_choose(name)
        if not local:
            return
        self.status_var.set("Calculando SHA-256 de " + name)
        self.run_async(
            lambda: verify_artifact(local, name),
            on_done=lambda verified: (
                self.status_var.set("SHA-256 verificado · " + name),
                messagebox.showinfo("Xbox Controller", (
                    "Archivo verificado: coincide en tamaño y SHA-256 con "
                    "la publicación de ChiricoG v1.4.2.\n\n"
                    "Esto no garantiza compatibilidad con tu PS4."
                ))
            ),
            on_error=lambda exc: (
                self.status_var.set("Verificación fallida · " + name),
                messagebox.showerror("Xbox Controller", str(exc))
            ))

    def xbox_verify_prx(self) -> None:
        self._xbox_verify_ui("xbox_controller.prx")

    def xbox_verify_pkg(self) -> None:
        self._xbox_verify_ui("xbox_controller_configurator.pkg")

    def xbox_upload_prx(self) -> None:
        if self.active_profile is not PS4 or not self.require_connection():
            return
        path = self._xbox_choose("xbox_controller.prx")
        if not path:
            return
        try:
            verified = verify_artifact(path, "xbox_controller.prx")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Xbox Controller", str(exc))
            return
        if not messagebox.askyesno("Xbox Controller v1.4.2", (
            "Subir el PRX verificado a:\n" + PLUGIN_PATH +
            "\n\nNO se reemplazará un PRX existente. "
            "NO se modificará plugins.ini ni se activará el plugin.\n"
            "¿Continuar?"
        )):
            return
        self.status_var.set("Subiendo plugin verificado, sin activar…")

        def task():
            # Do not trust SIZE=550 alone: it can indicate permission errors.
            # Demand an actual listing and reject any pre-existing final name.
            remote_files = self.ftp.listdir("/data/GoldHEN/plugins")
            if any(item.name.casefold() == "xbox_controller.prx" for item in remote_files):
                raise FileExistsError(
                    "Ya existe xbox_controller.prx. Haz copia y gestiona la "
                    "actualización manualmente; no se sobrescribe.")
            # Remote read-back validates the written bytes before final rename.
            ResumableFTP(self.ftp, verify_sha256=True).upload(
                verified.path, PLUGIN_PATH, lambda current, total, phase: self.post_progress(
                    current, total, "Xbox Controller · " + phase))
            return True

        self.run_async(
            task,
            on_done=lambda _result: messagebox.showinfo("Xbox Controller", (
                "PRX subido y verificado. No está activado. "
                "Prepara y revisa plugins.ini; guarda su copia de seguridad."
            )),
            on_error=lambda exc: messagebox.showerror("Xbox Controller", (
                "No se pudo subir el PRX: " + str(exc) +
                "\nLos archivos existentes no se sustituyen automáticamente."
            )))

    def xbox_prepare_ini(self) -> None:
        if self.active_profile is not PS4 or not self.require_connection():
            return
        if not messagebox.askyesno("Xbox Controller", (
            "Leer plugins.ini y guardar una copia de seguridad local junto con "
            "una PROPUESTA (sin enviar cambios a la PS4). ¿Continuar?"
        )):
            return
        backup_root = Path.home() / "Documents" / "PS4GoldHENManager" / "Backups"
        # mkdtemp provides a distinct directory and avoids overwriting old backups.
        def task():
            backup_root.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="xbox-controller-", dir=backup_root))
            return local_ini_preview(self.ftp, directory)
        def done(result):
            original, preview, changed = result
            messagebox.showinfo("Xbox Controller", (
                "Propuesta local creada (NO aplicada a la consola):\n" + str(preview)
                + "\n\nBackup remoto: " + (str(original) if original else "plugins.ini no existía")
                + "\n\n" + (
                    "Revisa el contenido antes de actualizar manualmente la PS4."
                    if changed else "La ruta del plugin ya figuraba en la configuración."
                )))
        self.run_async(task, on_done=done,
                       on_error=lambda exc: messagebox.showerror(
                           "Xbox Controller", "No se pudo preparar el backup: " + str(exc)))

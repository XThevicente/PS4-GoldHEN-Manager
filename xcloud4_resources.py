"""Curated XCloud4 v1.2.0-beta resources; never handles Xbox account credentials.

Only local file verification and fixed external HTTPS links are provided.
The manager does not bundle, download, execute, or automatically install the PKG.
"""
from __future__ import annotations

import hashlib
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

RELEASE_URL = "https://github.com/asp11edgr/XCloud4/releases/tag/v1.2.0-beta"
SOURCE_URL = "https://github.com/asp11edgr/XCloud4"
RELEASE_FILE = "XCloud4-1.2.0-beta-publico.pkg"
EXPECTED_SHA256 = "457473f98c9acc16c7f217781331271985f3a716172bdf2f439373222f7b4209"
EXPECTED_SIZE = 16646144
LINKS = {"release": RELEASE_URL, "source": SOURCE_URL}


def approved_xcloud_link(name: str) -> str:
    if name not in LINKS:
        raise ValueError("Enlace no aprobado")
    return LINKS[name]


def verify_pkg(path: Path) -> tuple[bool, str]:
    """Read local bytes without modifying or transferring them."""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    return path.stat().st_size == EXPECTED_SIZE and got == EXPECTED_SHA256, got


class XCloud4Mixin:
    def _build_xcloud4_resources(self, parent) -> None:
        card = ttk.LabelFrame(parent, text="XCloud4 · Xbox Cloud Gaming", padding=9)
        card.pack(fill="x", pady=(10, 0))
        ttk.Label(card, text=(
            "Beta 1.2.0 · PS4 GoldHEN · Fortnite sin Game Pass "
            "con cuenta Xbox gratuita, donde esté disponible."),
            wraplength=260, justify="left").pack(anchor="w")
        ttk.Label(card, text=(
            "1080p/60 FPS · servidores configurables. "
            "Pruebas del autor: PS4 Fat, FW 12.00 y GoldHEN v2.4b18.7. "
            "Otros firmwares sin verificar."),
            wraplength=260, justify="left").pack(anchor="w", pady=(7, 2))
        ttk.Button(card, text="Release oficial · GitHub",
                   command=lambda: self._xcloud_open("release")).pack(fill="x", pady=3)
        ttk.Button(card, text="Ver código fuente",
                   command=lambda: self._xcloud_open("source")).pack(fill="x", pady=3)
        ttk.Button(card, text="Verificar PKG descargado",
                   command=self.xcloud_verify_pkg).pack(fill="x", pady=3)
        ttk.Label(card, text=(
            "Descarga desde el autor, verifica el SHA-256 y usa "
            "tu instalador PKG habitual. No gestiona credenciales Xbox."),
            wraplength=260, justify="left").pack(anchor="w", pady=(7, 0))

    @staticmethod
    def _xcloud_open(name: str) -> None:
        webbrowser.open_new_tab(approved_xcloud_link(name))

    def xcloud_verify_pkg(self) -> None:
        selected = filedialog.askopenfilename(
            title="Seleccionar XCloud4-1.2.0-beta-publico.pkg para verificar",
            filetypes=[("Paquetes PS4", "*.pkg"), ("Todos", "*.*")])
        if not selected:
            return
        path = Path(selected)
        self.status_var.set(f"Verificando SHA-256: {path.name}…")

        def done(result):
            good, digest = result
            if good:
                messagebox.showinfo("XCloud4", (
                    "SHA-256 y tamaño coinciden con la release oficial v1.2.0-beta.\n"
                    "Esto verifica los bytes descargados, no la compatibilidad de tu consola."))
            else:
                messagebox.showerror("XCloud4", (
                    "Archivo distinto al publicado por el autor. No lo instales "
                    "desde este archivo.\n\nSHA-256 observado:\n" + digest))
            self.status_var.set(
                "XCloud4: archivo verificado" if good else "XCloud4: verificación incorrecta")

        self.run_async(lambda: verify_pkg(path), on_done=done,
                       on_error=lambda exc: messagebox.showerror(
                           "XCloud4", f"No se pudo verificar: {exc}"))

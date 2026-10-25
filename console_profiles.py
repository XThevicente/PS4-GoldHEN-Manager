"""Typed console profiles. PS4 operations must never silently target a PS5.

PS5 support is a disabled preview until a separate authenticated transport
is implemented and tested; a responsive TCP port alone does not identify a PS5.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsoleProfile:
    key: str
    label: str
    ready: bool
    transport: str | None
    description: str

    def require(self, operation: str) -> None:
        if not self.ready:
            raise RuntimeError(
                f"{self.label}: {operation} no está disponible. "
                "La integración PS5 es una vista previa; no uses los protocolos de PS4."
            )


PS4 = ConsoleProfile(
    "ps4_goldhen", "PS4 · GoldHEN", True, "goldhen_ftp",
    "FTP, Payload Server, Klog, ps4debug y Remote Package Installer."
)
PS5 = ConsoleProfile(
    "ps5_jailbreak", "PS5 · Jailbreak (vista previa)", False, None,
    "Necesita su propio cliente autenticado y pruebas con hardware real."
)
PROFILES = (PS4, PS5)


def get_profile(key: str | None) -> ConsoleProfile:
    """Unknown or corrupted preferences fall back to the existing PS4 flow."""
    return next((p for p in PROFILES if p.key == key), PS4)


def profile_from_label(label: str) -> ConsoleProfile:
    return next((p for p in PROFILES if p.label == label), PS4)

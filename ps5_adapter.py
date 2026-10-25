"""Independent PS5 transport boundary, intentionally not a working AVA1 client.

PS5Upload uses Noise_XX_25519_ChaChaPoly_BLAKE2b over AVA1 (TCP 9120).
An open port is not identity, and unpaired management requests are forbidden.
No PS4 FTP/BinLoader clients are reachable from this class.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PS5State(str, Enum):
    UNAVAILABLE = "unavailable"
    NEEDS_HELPER = "needs_helper"
    NEEDS_PAIRING = "needs_pairing"
    CONNECTED = "connected"


@dataclass(frozen=True)
class PS5Capabilities:
    authenticated: bool = False
    transfers: bool = False
    files: bool = False
    packages: bool = False


class PS5Transport:
    PROTOCOL = "AVA1"
    PORT = 9120

    def __init__(self) -> None:
        self.state = PS5State.UNAVAILABLE
        self.capabilities = PS5Capabilities()

    def connect(self, host: str) -> None:
        raise NotImplementedError(
            "PS5 aún no disponible. Requiere AVA1 completo, handshake Noise XX, "
            "confirmación de emparejamiento, identidad anclada y pruebas en hardware."
        )

    def upload(self, source: str, target: str) -> None:
        raise NotImplementedError("Las transferencias PS5 están desactivadas")

    def install_pkg(self, path: str) -> None:
        raise NotImplementedError("La instalación PS5 está desactivada")

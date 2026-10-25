"""Conservative, resumable PS4 FTP transfers with optional full SHA-256 readback.

Uses a private ".goldhen.part" file at each destination. Does NOT overwrite
an existing final file, delete a partial upload, or silently call a plain STOR
after a failed REST. All data verification uses bytes read back from the FTP
server: SIZE alone is not an integrity proof.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path
from typing import Callable


class TransferIntegrityError(RuntimeError):
    """Server data or the staging file failed an integrity check."""


Progress = Callable[[int, int, str], None]


def hash_local(path: Path, limit: int | None = None) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with path.open("rb") as stream:
        while True:
            n = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            if n <= 0:
                break
            chunk = stream.read(n)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    if remaining is not None and remaining != 0:
        raise TransferIntegrityError("El archivo local cambió durante la comprobación")
    return digest.hexdigest()


class ResumableFTP:
    """Requires a PS4 FTPClient with remote_size(), remote_sha256() and REST support."""

    def __init__(self, client, verify_sha256: bool = True) -> None:
        self.client = client
        self.verify_sha256 = verify_sha256

    @staticmethod
    def _assert_no_final(size: int | None, path: str) -> None:
        if size is not None:
            raise FileExistsError(
                f"El destino ya existe: {path}. Renómbralo o bórralo manualmente "
                "antes de iniciar esta transferencia; no se sobrescribirá."
            )

    def upload(self, local: Path, remote: str, progress: Progress) -> None:
        if not local.is_file():
            raise FileNotFoundError(str(local))
        total = local.stat().st_size
        if total == 0:
            # Creating an empty temporary file via STOR is still required.
            pass
        staging = remote + ".goldhen.part"
        self.client.ensure_dir(posixpath.dirname(remote))
        self._assert_no_final(self.client.remote_size(remote), remote)
        offset = self.client.remote_size(staging)
        offset = 0 if offset is None else offset
        if offset > total:
            raise TransferIntegrityError(
                "Archivo temporal remoto mayor que el local; revísalo antes de continuar."
            )
        if offset > 0:
            # A suffix is not safe unless the remote prefix is exactly the local prefix.
            if self.client.remote_sha256(staging) != hash_local(local, offset):
                raise TransferIntegrityError(
                    "El temporal remoto no coincide con el archivo local. "
                    "No se reanudará sobre datos diferentes."
                )
        if offset < total or (total == 0 and self.client.remote_size(staging) is None):
            self.client.upload_file(
                local, staging,
                lambda current, _total: progress(
                    offset + current, total, "Reanudando subida" if offset else "Subiendo"),
                rest=offset)
        staged_size = self.client.remote_size(staging)
        if staged_size != total:
            raise TransferIntegrityError(
                f"Subida incompleta: remoto {staged_size!r}, local {total}"
            )
        if self.verify_sha256:
            progress(total, total, "Verificando SHA-256 (lectura remota)")
            if self.client.remote_sha256(staging) != hash_local(local):
                raise TransferIntegrityError("SHA-256 de subida distinto: temporal conservado.")
        self._assert_no_final(self.client.remote_size(remote), remote)
        self.client.rename(staging, remote)
        progress(total, total, "Subida validada")

    def download(self, remote: str, local: Path, progress: Progress) -> None:
        total = self.client.remote_size(remote)
        if total is None:
            raise FileNotFoundError(f"No se puede consultar SIZE de {remote}")
        if local.exists():
            raise FileExistsError(
                f"El archivo local ya existe: {local}. "
                "No se sobrescribirá automáticamente."
            )
        local.parent.mkdir(parents=True, exist_ok=True)
        staging = local.with_name(local.name + ".goldhen.part")
        offset = staging.stat().st_size if staging.exists() else 0
        if offset > total:
            raise TransferIntegrityError("El temporal local excede el tamaño remoto")
        if offset < total or not staging.exists():
            self.client.download_file(
                remote, staging,
                lambda current, _total: progress(
                    current, total, "Reanudando descarga" if offset else "Descargando"),
                rest=offset)
        if staging.stat().st_size != total:
            raise TransferIntegrityError(
                f"Descarga incompleta: local {staging.stat().st_size}, remoto {total}"
            )
        if self.client.remote_size(remote) != total:
            raise TransferIntegrityError("El tamaño remoto cambió durante la descarga")
        if self.verify_sha256:
            progress(total, total, "Verificando SHA-256 (lectura remota)")
            if hash_local(staging) != self.client.remote_sha256(remote):
                raise TransferIntegrityError(
                    "SHA-256 de descarga distinto: temporal local conservado."
                )
        if local.exists():
            raise FileExistsError("El destino local apareció durante la transferencia")
        # On Windows, rename fails when destination exists, unlike os.replace.
        staging.rename(local)
        progress(total, total, "Descarga validada")

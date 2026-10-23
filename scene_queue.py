"""Cola de descargas del GoldHEN Scene Hub: lector seguro para Windows.

Nunca descarga, ejecuta ni instala paquetes. Solo valida y muestra enlaces
HTTPS guardados por el usuario. Admite importar un JSON local sin conectar cuenta.
"""
from __future__ import annotations

import ipaddress
import json
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HUB_QUEUE_API = "https://itrsr62yvx.zite.so/public/itrsr62yvx/api/getDesktopDeviceQueue"
PAIRING_PAGE = "https://itrsr62yvx.zite.so/?hub=tools&section=queue"
MAX_JSON_BYTES = 160_000
MAX_ITEMS = 30
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class QueueError(ValueError):
    """Error de red, autorización o contenido de la cola."""


def create_pairing_code() -> str:
    """Código aleatorio de 192 bits, nunca se genera en el servidor."""
    return secrets.token_urlsafe(24)


def valid_https_link(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 1500:
        return False
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            return False
        # No abrir puertos internos, localhost ni rangos IP privados al recibir datos.
        if url.hostname.lower() in ("localhost", "localhost.localdomain"):
            return False
        try:
            address = ipaddress.ip_address(url.hostname)
            if not address.is_global:
                return False
        except ValueError:
            pass
        return url.port in (None, 443)
    except ValueError:
        return False


def parse_queue(data: bytes) -> list[dict[str, str]]:
    if len(data) > MAX_JSON_BYTES:
        raise QueueError("La respuesta de la cola es demasiado grande")
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise QueueError("El archivo JSON no se puede interpretar") from exc
    if not isinstance(obj, dict):
        raise QueueError("Formato de la cola no válido")
    if obj.get("schema") == 1 and obj.get("ok") is True:
        rows = obj.get("items")
    elif obj.get("app") == "GoldHEN Scene Hub" and obj.get("version") == 1:
        rows = obj.get("items")
    else:
        # Algunos despliegues envuelven el resultado en una propiedad data.
        wrapped = obj.get("data")
        if isinstance(wrapped, dict) and wrapped.get("ok") is True:
            rows = wrapped.get("items")
        elif obj.get("ok") is True:
            rows = obj.get("items")
        else:
            raise QueueError("La cola no está autorizada o el formato es incorrecto")
    if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        raise QueueError("La cola supera el máximo de 30 elementos")
    result, seen = [], set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        name = item.get("name")
        url = item.get("url")
        platform = item.get("platform")
        if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
            continue
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            continue
        if not isinstance(platform, str) or len(platform) > 40:
            continue
        if not valid_https_link(url) or identifier in seen:
            continue
        seen.add(identifier)
        result.append({
            "id": identifier, "name": name.strip(),
            "url": url, "platform": platform
        })
    return result


def read_export_file(path: str | Path) -> list[dict[str, str]]:
    file = Path(path)
    if file.stat().st_size > MAX_JSON_BYTES:
        raise QueueError("Archivo de cola demasiado grande")
    return parse_queue(file.read_bytes())


def fetch_account_queue(token: str, timeout: float = 10.0) -> list[dict[str, str]]:
    """Consulta manual; código privado enviado solo mediante POST HTTPS."""
    if not TOKEN_RE.fullmatch(token):
        raise QueueError("El código del PC no tiene el formato correcto")
    payload = json.dumps({"token": token}).encode("utf-8")
    request = Request(HUB_QUEUE_API, data=payload, method="POST", headers={
        "User-Agent": "PS4GoldHENManager-DesktopQueue/1.1",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.geturl() != HUB_QUEUE_API:
                raise QueueError("Redirección inesperada del servidor")
            response_data = response.read(MAX_JSON_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise QueueError("No se pudo consultar la cola. Comprueba la vinculación y la conexión.") from exc
    return parse_queue(response_data)

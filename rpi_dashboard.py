"""Explicit browser shortcuts for Ari's Remote PKG Installer v3.57.

Dashboard port 12801 is distinct from legacy command API port 12800.
This module never probes or invokes installer endpoints and only accepts RFC1918
literal IPv4 addresses, preventing hostnames, IPv6 scopes, and URL injection.
"""
from __future__ import annotations

import ipaddress
import webbrowser
from tkinter import messagebox

RPI_DASHBOARD_PORT = 12801
RPI_RELEASES_URL = "https://codeberg.org/rawblue33/Remote-PKG-Installer-v3.53/releases"
_ALLOWED = tuple(ipaddress.ip_network(x) for x in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def private_ps4_address(raw: str) -> str:
    """Accept only canonical IPv4 hosts on the user's LAN."""
    value = raw.strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Introduce una dirección IPv4 privada de tu PS4.") from exc
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in _ALLOWED
    ):
        raise ValueError("El panel RPI sólo debe abrirse en una IP privada de tu red local.")
    return str(address)


def local_rpi_url(raw_host: str, *, log: bool = False) -> str:
    host = private_ps4_address(raw_host)
    return f"http://{host}:{RPI_DASHBOARD_PORT}" + ("/api/log" if log else "/")


class RPIDashboardMixin:
    """Optional read-only entrypoints; integration doesn't modify the RPI protocol."""

    def open_rpi_dashboard(self) -> None:
        self._open_rpi_site(log=False)

    def open_rpi_diagnostics(self) -> None:
        self._open_rpi_site(log=True)

    def _open_rpi_site(self, *, log: bool) -> None:
        try:
            url = local_rpi_url(self.host_var.get(), log=log)
        except ValueError as exc:
            messagebox.showwarning("PS4 GoldHEN Manager", str(exc))
            return
        operation = "registro de diagnóstico (puede contener datos locales)" if log else "panel de gestión RPI"
        if not messagebox.askyesno(
            "Remote PKG Installer v3.57",
            f"Abrir {operation} en el navegador?\n\n{url}\n\n"
            "Asegúrate de tener RPI v3.57 iniciado en tu PS4 y de usar una red de confianza."
        ):
            return
        webbrowser.open_new_tab(url)

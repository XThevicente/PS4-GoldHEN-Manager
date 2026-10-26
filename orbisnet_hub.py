"""OrbisNet discovery panel. Read-only, with no PSN credentials or installers.

Information was checked against the 25 October 2026 developer announcement.
The actual compatible-game catalogue, server status and installation flow
must always be checked on the project website.
"""
from __future__ import annotations

import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import ttk

ORIGINAL_POST = "https://www.reddit.com/r/ps4homebrew/comments/1xeory8/playstation_4_online_without_psn_on_jailbroken/"
ORIGINAL_SITE = "https://orbisnet.app/"
ORIGINAL_DISCORD = "https://discord.com/invite/y5gRdBP3bH"

GAMES = (
    "ARK: Survival Evolved",
    "Tekken 7",
    "Minecraft Dungeons",
    "Payday 2",
    "Helldivers",
    "Green Hell",
    "A Way Out",
)

APPROVED_LINKS = frozenset((ORIGINAL_POST, ORIGINAL_SITE, ORIGINAL_DISCORD))


def official_link(key: str) -> str:
    """Fixed approved destinations only; arbitrary remote links are never opened."""
    choices = {"site": ORIGINAL_SITE, "community": ORIGINAL_DISCORD, "announcement": ORIGINAL_POST}
    if key not in choices:
        raise ValueError("Enlace externo no autorizado")
    url = choices[key]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("URL no segura")
    return url


def find_games(term: str) -> tuple[str, ...]:
    search = term.casefold().strip()
    return tuple(game for game in GAMES if search in game.casefold())


class OrbisNetMixin:
    def _build_orbisnet_tab(self) -> None:
        page = ttk.Frame(self.orbisnet_tab, padding=(10, 8))
        page.pack(fill=tk.BOTH, expand=True)

        head = ttk.Frame(page, style="Card.TFrame", padding=(19, 17))
        head.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(head, text="ORBISNET · PS4 ONLINE",
                  font=("Segoe UI", 19, "bold"), style="Card.TLabel").pack(anchor="w")
        ttk.Label(head, text="Proyecto comunitario independiente · Beta temprana · Sin PSN oficial",
                  style="Card.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Label(head, text=(
            "OrbisNet emula funciones de red para juegos P2P en PS4 con jailbreak. "
            "No es el servicio oficial de Sony ni una herramienta LAN tradicional."),
            style="Card.TLabel", wraplength=850, justify="left").pack(anchor="w", pady=(11, 0))

        actions = ttk.Frame(head, style="Card.TFrame")
        actions.pack(fill=tk.X, pady=(15, 0))
        for label, link, accent in (
            ("Web y catálogo", "site", True),
            ("Discord · comunidad", "community", False),
            ("Anuncio del autor", "announcement", False),
        ):
            ttk.Button(actions, text=label,
                       style="Accent.TButton" if accent else "TButton",
                       command=lambda url_key=link: self._orbisnet_open(url_key)
                       ).pack(side=tk.LEFT, padx=(0, 9))

        body = ttk.Frame(page)
        body.pack(fill=tk.BOTH, expand=True)
        games_card = ttk.Frame(body, style="Card.TFrame", padding=(15, 12))
        games_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(games_card, text="JUEGOS ANUNCIADOS",
                  font=("Segoe UI", 12, "bold"), style="Card.TLabel").pack(anchor="w")
        ttk.Label(games_card, text=(
            "Ejemplos publicados el 25/10/2026; consulta la web para la lista actual "
            "y compatibilidad real por versión."),
            style="Card.TLabel", wraplength=610).pack(anchor="w", pady=(6, 8))
        self.orbisnet_search_var = tk.StringVar()
        search = ttk.Entry(games_card, textvariable=self.orbisnet_search_var)
        search.pack(fill=tk.X, pady=(0, 8))
        search.bind("<KeyRelease>", lambda _event: self._orbisnet_filter_games())
        self.orbisnet_game_tree = ttk.Treeview(
            games_card, columns=("game",), show="headings", height=8)
        self.orbisnet_game_tree.heading("game", text="Juego mencionado por el autor")
        self.orbisnet_game_tree.column("game", width=350)
        self.orbisnet_game_tree.pack(fill=tk.BOTH, expand=True)
        self._orbisnet_filter_games()

        note = ttk.Frame(body, style="Card.TFrame", padding=(16, 14))
        note.pack(side=tk.RIGHT, fill=tk.BOTH)
        ttk.Label(note, text="ANTES DE EMPEZAR",
                  font=("Segoe UI", 12, "bold"), style="Card.TLabel").pack(anchor="w")
        for line in (
            "1. Comprueba tu firmware y que tu PS4 ya tiene homebrew.",
            "2. Revisa los requisitos del autor en su web.",
            "3. Accede a su Discord para ayuda y registro.",
            "4. Confirma compatibilidad exacta del juego.",
            "5. No uses mirrors ni compartas claves de PSN."
        ):
            ttk.Label(note, text=line, style="Card.TLabel",
                      wraplength=330, justify="left").pack(anchor="w", pady=8)
        ttk.Separator(note).pack(fill=tk.X, pady=(13, 10))
        ttk.Label(note, text=(
            "Esta pestaña es informativa: no descarga, instala ni ejecuta "
            "software de OrbisNet; no registra cuentas ni controla sus servidores. "
            "La beta puede fallar y no garantiza privacidad de datos."),
            style="Card.TLabel", wraplength=330, justify="left").pack(anchor="w")

    def _orbisnet_filter_games(self) -> None:
        self.orbisnet_game_tree.delete(*self.orbisnet_game_tree.get_children())
        for name in find_games(self.orbisnet_search_var.get()):
            self.orbisnet_game_tree.insert("", "end", values=(name,))

    @staticmethod
    def _orbisnet_open(key: str) -> None:
        webbrowser.open_new_tab(official_link(key))

#!/usr/bin/env python3
"""GUI controller for PS4 GoldHEN Companion Server v0.4.0."""
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import json
import secrets
import os
from pathlib import Path
from retro_manager import SYSTEMS

from companion_server import (
    CompanionService, local_ipv4, HTTP_PORT, DISCOVERY_PORT,
    APP_VERSION, MAX_PUSH_FILE
)


class CompanionControl(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PS4 GoldHEN Companion v{APP_VERSION}")
        self.geometry("1060x780")
        self.minsize(700, 560)
        self.configure(bg="#071426")
        self.service = None
        self._retro_cached = None
        self._build()
        self.after(350, self._refresh)

    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background="#071426")
        style.configure("TLabel", background="#071426", foreground="#e9f5ff", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#071426", foreground="#ffffff", font=("Segoe UI", 18, "bold"))
        style.configure("Accent.TLabel", background="#071426", foreground="#49c8ff", font=("Consolas", 24, "bold"))
        style.configure("Online.TLabel", background="#071426", foreground="#6ee7a8", font=("Segoe UI", 11, "bold"))
        style.configure("Offline.TLabel", background="#071426", foreground="#ff8d8d", font=("Segoe UI", 11, "bold"))

        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True)
        connection = ttk.Frame(tabs, padding=12)
        retro = ttk.Frame(tabs, padding=12)
        tabs.add(connection, text="Conexión PS4")
        tabs.add(retro, text="Retro Manager")
        root = connection
        self._build_retro(retro)

        ttk.Label(root, text=f"PS4 GoldHEN Companion v{APP_VERSION}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="Conexión local PS4 ↔ PC · protocolo v1").pack(anchor="w", pady=(0, 14))

        self.status = tk.StringVar(value="Servidor detenido")
        self.addr = tk.StringVar(value=f"PC: {local_ipv4()}:{HTTP_PORT}   ·   Discovery UDP {DISCOVERY_PORT}")
        self.code = tk.StringVar(value="------")
        self.device = tk.StringVar(value="Dispositivo: —")
        self.command = tk.StringVar(value="Último comando: —")
        self.info = tk.StringVar(value="Info consola: —")
        self.file_status = tk.StringVar(value="Archivo: —")

        ttk.Label(root, textvariable=self.status).pack(anchor="w")
        ttk.Label(root, textvariable=self.addr).pack(anchor="w", pady=(4, 10))

        ttk.Label(root, text="Código de emparejamiento").pack(anchor="w")
        ttk.Label(root, textvariable=self.code, style="Accent.TLabel").pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(root)
        row.pack(fill="x", pady=(0, 10))
        ttk.Button(row, text="Iniciar servidor", command=self.start_server).pack(side="left")
        ttk.Button(row, text="Detener", command=self.stop_server).pack(side="left", padx=8)
        ttk.Button(row, text="Copiar código", command=self.copy_code).pack(side="left")

        ttk.Separator(root).pack(fill="x", pady=8)

        self.online_label = ttk.Label(root, text="PS4: OFFLINE", style="Offline.TLabel")
        self.online_label.pack(anchor="w", pady=(3, 2))
        ttk.Label(root, textvariable=self.device).pack(anchor="w")
        ttk.Label(root, textvariable=self.command).pack(anchor="w", pady=(2, 2))
        ttk.Label(root, textvariable=self.info).pack(anchor="w", pady=(2, 2))
        ttk.Label(root, textvariable=self.file_status).pack(anchor="w", pady=(2, 10))

        ttk.Label(root, text="Mensaje para la PS4").pack(anchor="w")
        msgrow = ttk.Frame(root)
        msgrow.pack(fill="x", pady=(4, 10))
        self.message_text = tk.StringVar(value="Hola desde el PC")
        self.message_entry = ttk.Entry(msgrow, textvariable=self.message_text, width=55)
        self.message_entry.pack(side="left", fill="x", expand=True)
        self.message_btn = ttk.Button(msgrow, text="Mostrar mensaje", command=self.send_message, state="disabled")
        self.message_btn.pack(side="left", padx=(8, 0))

        cmdrow = ttk.Frame(root)
        cmdrow.pack(fill="x", pady=(0, 10))
        self.ping_btn = ttk.Button(cmdrow, text="Probar PING", command=self.send_ping, state="disabled")
        self.ping_btn.pack(side="left")
        self.info_btn = ttk.Button(cmdrow, text="Pedir info consola", command=self.request_info, state="disabled")
        self.info_btn.pack(side="left", padx=8)
        self.reconnect_btn = ttk.Button(cmdrow, text="Reiniciar enlace HTTP", command=self.request_reconnect, state="disabled")
        self.reconnect_btn.pack(side="left")

        filerow = ttk.Frame(root)
        filerow.pack(fill="x", pady=(0, 12))
        self.file_btn = ttk.Button(filerow, text="Enviar archivo pequeño", command=self.send_small_file, state="disabled")
        self.file_btn.pack(side="left")
        ttk.Label(
            filerow,
            text=f"máx. {MAX_PUSH_FILE // 1024} KiB → /data/ps4gh_received.bin",
        ).pack(side="left", padx=10)

        ttk.Separator(root).pack(fill="x", pady=8)
        ttk.Label(
            root,
            text=(
                "v0.5: abre OPTIONS → RETRO MANAGER en PS4. Configura la carpeta y los emuladores en la pestaña Retro Manager. "
                "Los emuladores se ejecutan y muestran en el PC; esta versión no transmite vídeo a la PS4."
            ),
            wraplength=700,
        ).pack(anchor="w", pady=8)

    def _build_retro(self, root):
        ttk.Label(root, text="Retro Manager · biblioteca local", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="Inicia el servidor. Usa subcarpetas nes, snes, gb, gbc, gba, megadrive, ps1, n64, homebrew.").pack(anchor="w", pady=8)
        row = ttk.Frame(root); row.pack(fill="x")
        ttk.Button(row, text="Carpeta ROMs", command=self.retro_folder).pack(side="left")
        ttk.Button(row, text="Escanear", command=self.retro_scan).pack(side="left", padx=8)
        ttk.Button(row, text="Configurar emulador", command=self.retro_configure).pack(side="left")
        self.retro_status = tk.StringVar(value="Biblioteca sin cargar")
        ttk.Label(root, textvariable=self.retro_status, wraplength=980).pack(anchor="w", pady=10)
        self.emulator_status = tk.StringVar(value="Emulador PC: detenido")
        ttk.Label(root, textvariable=self.emulator_status).pack(anchor="w", pady=(0, 6))
        filters = ttk.Frame(root); filters.pack(fill="x")
        self.retro_filter = tk.StringVar(value="todos")
        combo = ttk.Combobox(filters, textvariable=self.retro_filter, values=["todos", *SYSTEMS], state="readonly", width=18)
        combo.pack(side="left"); combo.bind("<<ComboboxSelected>>", lambda e: self.retro_render())
        self.retro_search = tk.StringVar()
        ttk.Label(filters, text="Buscar:").pack(side="left", padx=8)
        ttk.Entry(filters, textvariable=self.retro_search).pack(side="left", fill="x", expand=True)
        self.retro_search.trace_add("write", lambda *a: self.retro_render())
        self.roms = ttk.Treeview(root, columns=("system", "title", "emulator"), show="headings", height=14)
        for key, title, width in [("system", "Sistema", 130), ("title", "Juego", 550), ("emulator", "Emulador", 140)]:
            self.roms.heading(key, text=title); self.roms.column(key, width=width)
        self.roms.pack(fill="both", expand=True, pady=10)
        self.roms.bind("<<TreeviewSelect>>", self.retro_cover)
        self.cover_label = ttk.Label(root, text="Carátula: PNG junto a la ROM, mismo nombre (hasta 512 × 512)")
        self.cover_label.pack(anchor="w")
        actions = ttk.Frame(root); actions.pack(fill="x", pady=10)
        ttk.Button(actions, text="Lanzar en PC", command=self.retro_launch).pack(side="left")
        ttk.Button(actions, text="Detener emulador", command=self.retro_stop).pack(side="left", padx=8)
        ttk.Label(root, text="X en PS4 solicita el lanzamiento en el PC. Sin streaming ni emuladores integrados. No se incluyen ROMs.").pack(anchor="w")

    def _retro(self):
        if not self.service:
            messagebox.showinfo("Retro Manager", "Inicia el servidor en Conexión PS4")
            return None
        return self.service.retro

    def retro_folder(self):
        retro = self._retro()
        if not retro: return
        path = filedialog.askdirectory(title="Carpeta principal de ROMs")
        if path:
            try:
                retro.set_root(path)
                self.retro_scan()
            except Exception as exc: messagebox.showerror("Retro Manager", str(exc))

    def retro_scan(self):
        retro = self._retro()
        if not retro: return
        if getattr(self, "_scan_running", False): return
        self._scan_running = True
        self.retro_status.set("Escaneando biblioteca…")
        def worker():
            try:
                count = retro.scan()
                self._scan_result = (count, None)
            except Exception as exc:
                self._scan_result = (0, str(exc))
        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._retro_scan_done)

    def _retro_scan_done(self):
        result = getattr(self, "_scan_result", None)
        if result is None:
            self.after(100, self._retro_scan_done); return
        self._scan_result = None; self._scan_running = False
        count, error = result
        self.retro_status.set(error or f"{count} ROMs · biblioteca disponible en el menú PS4")
        self.retro_render()

    def retro_render(self):
        if not hasattr(self, "roms"): return
        self.roms.delete(*self.roms.get_children())
        if not self.service: return
        retro = self.service.retro
        query = self.retro_search.get().casefold()
        system = self.retro_filter.get()
        with retro.lock:
            entries = list(retro.entries)
            configured = set(retro.config["emulators"])
        for entry in entries:
            if system != "todos" and system != entry["system"]: continue
            if query not in entry["title"].casefold(): continue
            self.roms.insert("", "end", iid=entry["id"], values=(SYSTEMS[entry["system"]][0], entry["title"], "Configurado" if entry["system"] in configured else "Sin configurar"))

    def retro_cover(self, event=None):
        self.cover_label.configure(image="", text="Sin carátula")
        self._cover_image = None
        if not self.service or not self.roms.selection(): return
        entry = next((e for e in self.service.retro.entries if e["id"] == self.roms.selection()[0]), None)
        if not entry or not entry["cover"]: return
        try:
            if Path(entry["cover"]).stat().st_size > 2 * 1024 * 1024: return
            with open(entry["cover"], "rb") as image_file:
                header = image_file.read(24)
            if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n": return
            if int.from_bytes(header[16:20], "big") > 512 or int.from_bytes(header[20:24], "big") > 512: return
            photo = tk.PhotoImage(file=entry["cover"])
            if photo.width() > 512 or photo.height() > 512: return
            self._cover_image = photo.subsample(max(1, (photo.width()+119)//120), max(1, (photo.height()+119)//120))
            self.cover_label.configure(image=self._cover_image, text="")
        except (OSError, tk.TclError): pass

    def retro_configure(self):
        retro = self._retro()
        if not retro: return
        win = tk.Toplevel(self); win.title("Configurar emulador PC"); win.geometry("700x350")
        system = tk.StringVar(value="nes"); exe = tk.StringVar(); args = tk.StringVar(value='["{rom}"]')
        ttk.Label(win, text="Sistema").pack(anchor="w", padx=16, pady=6)
        ttk.Combobox(win, textvariable=system, values=list(SYSTEMS), state="readonly").pack(fill="x", padx=16)
        ttk.Label(win, text="Ejecutable del emulador: por ejemplo Mesen.exe o retroarch.exe").pack(anchor="w", padx=16, pady=6)
        ttk.Entry(win, textvariable=exe).pack(fill="x", padx=16)
        ttk.Button(win, text="Seleccionar ejecutable", command=lambda: exe.set(filedialog.askopenfilename(
            parent=win, title="Selecciona el programa del emulador",
            filetypes=[("Emulador Windows (.exe)", "*.exe")] if os.name == "nt" else [("Ejecutables", "*")]
        ) or exe.get())).pack(anchor="w", padx=16)
        ttk.Label(win, text="El juego .nes se selecciona desde la biblioteca; aqui necesitas el programa que lo ejecuta.", wraplength=650).pack(anchor="w", padx=16, pady=6)
        ttk.Label(win, text='Argumentos JSON; RetroArch: ["-L", "ruta/al/core.dll", "{rom}"]').pack(anchor="w", padx=16, pady=6)
        ttk.Entry(win, textvariable=args).pack(fill="x", padx=16)
        def load_profile(event=None):
            with retro.lock:
                profile = retro.config["emulators"].get(system.get(), {})
            exe.set(profile.get("executable", ""))
            args.set(json.dumps(profile.get("arguments", ["{rom}"]), ensure_ascii=False))
        for child in win.winfo_children():
            if isinstance(child, ttk.Combobox): child.bind("<<ComboboxSelected>>", load_profile)
        load_profile()
        def save():
            try:
                retro.configure_emulator(system.get(), exe.get(), json.loads(args.get()))
                self.retro_render(); win.destroy()
            except Exception as exc: messagebox.showerror("Emulador", str(exc), parent=win)
        ttk.Button(win, text="Guardar y habilitar lanzamiento desde PS4", command=save).pack(pady=10)

    def retro_launch(self):
        retro = self._retro()
        if not retro or not self.roms.selection(): return
        try:
            retro.launch(self.roms.selection()[0], secrets.token_hex(12))
            self.retro_status.set("Emulador lanzado en el PC")
        except Exception as exc: messagebox.showerror("Lanzar", str(exc))

    def retro_stop(self):
        retro = self._retro()
        if not retro: return
        try:
            retro.stop(); self.retro_status.set("Emulador detenido")
        except Exception as exc: messagebox.showerror("Detener", str(exc))

    def start_server(self):
        if self.service:
            return
        try:
            self.service = CompanionService()
            if self._retro_cached is not None:
                self.service.retro = self._retro_cached
            self._retro_cached = self.service.retro
            self.service.start()
            self.retro_scan()
            self.code.set(self.service.state.pair_code)
            self.status.set("● Servidor activo")
            self.addr.set(f"PC: {local_ipv4()}:{self.service.http_port}   ·   Discovery UDP {self.service.discovery_port}")
        except Exception as e:
            self.service = None
            messagebox.showerror("Companion", str(e))

    def stop_server(self):
        if not self.service:
            return
        svc = self.service
        self.service = None
        threading.Thread(target=svc.stop, daemon=True).start()
        self.status.set("Servidor detenido")
        self.code.set("------")
        self.online_label.configure(text="PS4: OFFLINE", style="Offline.TLabel")
        self.device.set("Dispositivo: —")
        self.command.set("Último comando: —")
        self._set_command_buttons(False)

    def copy_code(self):
        code = self.code.get()
        if code != "------":
            self.clipboard_clear()
            self.clipboard_append(code)
            self.update()

    def _queue(self, name, **payload):
        if not self.service or not self.service.is_online():
            return
        if self.service.state.pending_command:
            messagebox.showinfo("Companion", "Hay un comando pendiente. Espera a que la PS4 lo confirme.")
            return
        try:
            cmd = self.service.queue_command(name, **payload)
        except ValueError as exc:
            messagebox.showinfo("Companion", str(exc)); return
        self.command.set(f"Último comando: {name.upper()} #{cmd['id']} · pendiente")

    def send_ping(self):
        self._queue("ping")

    def send_message(self):
        text = self.message_text.get().strip()
        if not text:
            return
        self._queue("message", text=text)

    def request_info(self):
        self._queue("get_info")

    def request_reconnect(self):
        self._queue("reconnect")

    def send_small_file(self):
        if not self.service or not self.service.is_online():
            return
        path = filedialog.askopenfilename(title=f"Archivo para PS4 (máx. {MAX_PUSH_FILE // 1024} KiB)")
        if not path:
            return
        p = Path(path)
        try:
            data = p.read_bytes()
            file_id, file_name = self.service.register_file(p.name, data)
        except Exception as e:
            messagebox.showerror("Enviar archivo", str(e))
            return
        self._queue("fetch_file", file_id=file_id, file_name=file_name, file_size=len(data))

    def _set_command_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in (self.ping_btn, self.info_btn, self.reconnect_btn, self.message_btn, self.file_btn):
            btn.configure(state=state)

    def _refresh(self):
        if self.service:
            st = self.service.state
            run_status = self.service.retro.status()
            self.emulator_status.set("Emulador PC: en ejecución" if run_status["running"] else "Emulador PC: detenido")
            online = self.service.is_online()
            pending = bool(st.pending_command)
            if online:
                age = max(0.0, time.time() - (st.last_seen or time.time()))
                self.online_label.configure(text=f"PS4: ONLINE · heartbeat {age:.1f}s", style="Online.TLabel")
            else:
                self.online_label.configure(text="PS4: OFFLINE", style="Offline.TLabel")

            self._set_command_buttons(online and not pending)
            self.device.set(f"Dispositivo: {st.paired_device or 'sin emparejar'}")
            self.info.set(f"Info consola: {st.last_info or '—'}")
            self.file_status.set(f"Archivo: {st.last_file or '—'}")

            if st.last_ack:
                self.command.set(
                    f"Último comando: {str(st.last_ack.get('name', '')).upper()} "
                    f"#{st.last_ack.get('id')} · {st.last_ack.get('result', 'ok').upper()} ✓"
                )
            elif st.pending_command:
                self.command.set(
                    f"Último comando: {str(st.pending_command.get('name', '')).upper()} "
                    f"#{st.pending_command.get('id')} · pendiente"
                )
        else:
            self.online_label.configure(text="PS4: OFFLINE", style="Offline.TLabel")
            self._set_command_buttons(False)
        self.after(350, self._refresh)

    def destroy(self):
        if self.service:
            try:
                self.service.stop()
            except Exception:
                pass
        super().destroy()


if __name__ == "__main__":
    CompanionControl().mainloop()


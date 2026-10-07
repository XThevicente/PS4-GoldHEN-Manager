#!/usr/bin/env python3
"""GUI controller for PS4 GoldHEN Companion Server v0.4.0."""
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
from pathlib import Path

from companion_server import (
    CompanionService, local_ipv4, HTTP_PORT, DISCOVERY_PORT,
    APP_VERSION, MAX_PUSH_FILE
)


class CompanionControl(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PS4 GoldHEN Companion v{APP_VERSION}")
        self.geometry("760x610")
        self.minsize(700, 560)
        self.configure(bg="#071426")
        self.service = None
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
                "v0.4: mensajes PC → PS4, lectura de versión del sistema, reinicio del enlace HTTP, "
                "transferencia de archivos pequeños y menú local con OPTIONS en el DualShock 4."
            ),
            wraplength=700,
        ).pack(anchor="w", pady=8)

    def start_server(self):
        if self.service:
            return
        try:
            self.service = CompanionService()
            self.service.start()
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
        cmd = self.service.queue_command(name, **payload)
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

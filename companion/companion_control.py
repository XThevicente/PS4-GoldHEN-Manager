#!/usr/bin/env python3
"""GUI controller for PS4 GoldHEN Companion Server v0.3.0."""
import time
import tkinter as tk
from tkinter import ttk, messagebox
import threading

from companion_server import CompanionService, local_ipv4, HTTP_PORT, DISCOVERY_PORT, APP_VERSION


class CompanionControl(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PS4 GoldHEN Companion v{APP_VERSION}")
        self.geometry("640x470")
        self.minsize(600, 430)
        self.configure(bg="#071426")
        self.service = None
        self._build()
        self.after(400, self._refresh)

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
        ttk.Label(root, text="Conexión local PS4 ↔ PC · protocolo v1").pack(anchor="w", pady=(0, 16))

        self.status = tk.StringVar(value="Servidor detenido")
        self.addr = tk.StringVar(value=f"PC: {local_ipv4()}:{HTTP_PORT}   ·   Discovery UDP {DISCOVERY_PORT}")
        self.code = tk.StringVar(value="------")
        self.device = tk.StringVar(value="Dispositivo: —")
        self.command = tk.StringVar(value="Último comando: —")

        ttk.Label(root, textvariable=self.status).pack(anchor="w")
        ttk.Label(root, textvariable=self.addr).pack(anchor="w", pady=(4, 12))

        ttk.Label(root, text="Código de emparejamiento").pack(anchor="w")
        ttk.Label(root, textvariable=self.code, style="Accent.TLabel").pack(anchor="w", pady=(0, 12))

        row = ttk.Frame(root)
        row.pack(fill="x", pady=(0, 12))
        ttk.Button(row, text="Iniciar servidor", command=self.start_server).pack(side="left")
        ttk.Button(row, text="Detener", command=self.stop_server).pack(side="left", padx=8)
        ttk.Button(row, text="Copiar código", command=self.copy_code).pack(side="left")

        ttk.Separator(root).pack(fill="x", pady=8)

        self.online_label = ttk.Label(root, text="PS4: OFFLINE", style="Offline.TLabel")
        self.online_label.pack(anchor="w", pady=(4, 2))
        ttk.Label(root, textvariable=self.device).pack(anchor="w")
        ttk.Label(root, textvariable=self.command).pack(anchor="w", pady=(2, 10))

        cmdrow = ttk.Frame(root)
        cmdrow.pack(fill="x", pady=(0, 12))
        self.test_btn = ttk.Button(cmdrow, text="Probar PC → PS4", command=self.send_test_command, state="disabled")
        self.test_btn.pack(side="left")

        ttk.Separator(root).pack(fill="x", pady=8)
        ttk.Label(
            root,
            text=(
                "v0.3: heartbeat continuo, estado online/offline y primer canal de órdenes PC → PS4.\n"
                "En una instalación nueva, el código se introduce directamente con el mando en la PS4."
            ),
            wraplength=580,
        ).pack(anchor="w", pady=8)

    def start_server(self):
        if self.service:
            return
        try:
            self.service = CompanionService()
            self.service.start()
            self.code.set(self.service.state.pair_code)
            self.status.set("● Servidor activo")
            self.addr.set(
                f"PC: {local_ipv4()}:{self.service.http_port}   ·   Discovery UDP {self.service.discovery_port}"
            )
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
        self.test_btn.configure(state="disabled")

    def copy_code(self):
        code = self.code.get()
        if code != "------":
            self.clipboard_clear()
            self.clipboard_append(code)
            self.update()

    def send_test_command(self):
        if not self.service or not self.service.is_online():
            return
        cmd = self.service.queue_command("ping")
        self.command.set(f"Último comando: PING #{cmd['id']} · pendiente")

    def _refresh(self):
        if self.service:
            st = self.service.state
            online = self.service.is_online()
            if online:
                age = max(0.0, time.time() - (st.last_seen or time.time()))
                self.online_label.configure(text=f"PS4: ONLINE · heartbeat {age:.1f}s", style="Online.TLabel")
                self.test_btn.configure(state="normal")
            else:
                self.online_label.configure(text="PS4: OFFLINE", style="Offline.TLabel")
                self.test_btn.configure(state="disabled")

            self.device.set(f"Dispositivo: {st.paired_device or 'sin emparejar'}")
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
            self.test_btn.configure(state="disabled")
        self.after(400, self._refresh)

    def destroy(self):
        if self.service:
            try:
                self.service.stop()
            except Exception:
                pass
        super().destroy()


if __name__ == "__main__":
    CompanionControl().mainloop()

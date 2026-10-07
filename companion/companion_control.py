#!/usr/bin/env python3
"""Small GUI controller for PS4 GoldHEN Companion Server v0.1."""
import tkinter as tk
from tkinter import ttk, messagebox
import threading
from companion_server import CompanionService, local_ipv4, HTTP_PORT, DISCOVERY_PORT

class CompanionControl(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PS4 GoldHEN Companion v0.1")
        self.geometry("560x360")
        self.minsize(520, 330)
        self.configure(bg="#071426")
        self.service = None
        self._build()
        self.after(500, self._refresh)

    def _build(self):
        style=ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background="#071426")
        style.configure("TLabel", background="#071426", foreground="#e9f5ff", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#071426", foreground="#ffffff", font=("Segoe UI", 18, "bold"))
        style.configure("Accent.TLabel", background="#071426", foreground="#49c8ff", font=("Consolas", 24, "bold"))
        root=ttk.Frame(self,padding=20)
        root.pack(fill="both",expand=True)
        ttk.Label(root,text="PS4 GoldHEN Companion",style="Title.TLabel").pack(anchor="w")
        ttk.Label(root,text="Conexión local PS4 ↔ PC · protocolo v1").pack(anchor="w",pady=(0,18))
        self.status=tk.StringVar(value="Servidor detenido")
        self.addr=tk.StringVar(value=f"PC: {local_ipv4()}:{HTTP_PORT}   ·   Discovery UDP {DISCOVERY_PORT}")
        self.code=tk.StringVar(value="------")
        ttk.Label(root,textvariable=self.status).pack(anchor="w")
        ttk.Label(root,textvariable=self.addr).pack(anchor="w",pady=(4,15))
        ttk.Label(root,text="Código de emparejamiento").pack(anchor="w")
        ttk.Label(root,textvariable=self.code,style="Accent.TLabel").pack(anchor="w",pady=(0,16))
        row=ttk.Frame(root)
        row.pack(fill="x",pady=(0,14))
        ttk.Button(row,text="Iniciar servidor",command=self.start_server).pack(side="left")
        ttk.Button(row,text="Detener",command=self.stop_server).pack(side="left",padx=8)
        ttk.Button(row,text="Copiar código",command=self.copy_code).pack(side="left")
        ttk.Separator(root).pack(fill="x",pady=8)
        ttk.Label(root,text="Primera conexión: crea /data/ps4gh_pair_code.txt en la PS4 con este código.\nDespués la PS4 guarda un token y ya no necesita el código.",wraplength=500).pack(anchor="w",pady=8)
        self.paired=tk.StringVar(value="Dispositivo: —")
        ttk.Label(root,textvariable=self.paired).pack(anchor="w",pady=(10,0))

    def start_server(self):
        if self.service:
            return
        try:
            self.service=CompanionService()
            self.service.start()
            self.code.set(self.service.state.pair_code)
            self.status.set("● Servidor activo")
            self.addr.set(f"PC: {local_ipv4()}:{self.service.http_port}   ·   Discovery UDP {self.service.discovery_port}")
        except Exception as e:
            self.service=None
            messagebox.showerror("Companion",str(e))

    def stop_server(self):
        if not self.service:
            return
        svc=self.service
        self.service=None
        threading.Thread(target=svc.stop,daemon=True).start()
        self.status.set("Servidor detenido")
        self.code.set("------")

    def copy_code(self):
        c=self.code.get()
        if c != "------":
            self.clipboard_clear()
            self.clipboard_append(c)
            self.update()

    def _refresh(self):
        if self.service:
            st=self.service.state
            self.paired.set(f"Dispositivo: {st.paired_device or 'sin emparejar'}" + (" · conectado recientemente" if st.last_seen else ""))
        else:
            self.paired.set("Dispositivo: —")
        self.after(500,self._refresh)

    def destroy(self):
        if self.service:
            try:
                self.service.stop()
            except Exception:
                pass
        super().destroy()

if __name__=="__main__":
    CompanionControl().mainloop()

"""Transfer UI and execution adapter for the legacy Tkinter PS4 manager.

The new queue changes only the FTP transfers initiated through the Files panel.
PKG Remote Installer and payload operations remain separate protocols.
"""
from __future__ import annotations

import posixpath
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from console_profiles import PS4
from resumable_ftp import ResumableFTP


class TransferMixin:
    def _build_transfers_tab(self) -> None:
        frame = ttk.Frame(self.transfers_tab, padding=(12, 12))
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Cola de transferencias FTP",
                  font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(0, 7))
        ttk.Label(frame, text=(
            "Una transferencia a la vez. Errores visibles; "
            "reanudación segura con temporales y SHA-256; sin instalación PKG implícita."),
            style="Sub.TLabel").pack(anchor="w", pady=(0, 10))
        self.transfer_tree = ttk.Treeview(
            frame, columns=("direction", "state", "source", "target", "progress", "detail"),
            show="headings", selectmode="browse")
        for key, label, width in (
            ("direction", "Operación", 92), ("state", "Estado", 100),
            ("source", "Origen", 290), ("target", "Destino", 290),
            ("progress", "Progreso", 105), ("detail", "Detalles / error", 230)
        ):
            self.transfer_tree.heading(key, text=label)
            self.transfer_tree.column(key, width=width, stretch=key in ("source", "target", "detail"))
        self.transfer_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Button(frame, text="Cancelar pendientes",
                   command=self._cancel_pending_transfers).pack(anchor="e", pady=(10, 2))

    def _refresh_transfer_view(self) -> None:
        if not hasattr(self, "transfer_tree"):
            return
        self.transfer_tree.delete(*self.transfer_tree.get_children())
        labels = {"pending": "Pendiente", "running": "En curso", "completed": "Completa",
                  "failed": "Error", "cancelled": "Cancelada"}
        for job in self.transfers.snapshot():
            percentage = f"{job.percent}%" if job.percent is not None else "—"
            self.transfer_tree.insert("", "end", iid=str(job.id), values=(
                "Subida" if job.direction == "upload" else "Descarga",
                labels[job.state], job.source, job.target, percentage,
                job.error or job.detail))

    def _cancel_pending_transfers(self) -> None:
        count = self.transfers.cancel_pending()
        for job in self.transfers.snapshot():
            if job.state == "cancelled":
                self._transfer_runners.pop(job.id, None)
        self._refresh_transfer_view()
        self.status_var.set(f"{count} transferencia(s) pendiente(s) cancelada(s)")

    def _transfer_progress(self, job_id: int, done: int, total: int | None,
                           label: str) -> None:
        self.transfers.progress(job_id, done, total, label)
        now = time.monotonic()
        if now - self._last_transfer_redraw >= 0.25:
            self._last_transfer_redraw = now
            self.post_progress(done, total, label)
            self.ui_queue.put(("transfer_refresh", None, None))

    def _enqueue_transfer(self, direction: str, source: str, target: str, runner) -> None:
        self.active_profile.require("transferencias FTP")
        job = self.transfers.add(direction, source, target)
        self._transfer_runners[job.id] = runner
        self._refresh_transfer_view()
        self._run_next_transfer()

    def _run_next_transfer(self) -> None:
        if self.active_profile is not PS4 or not self.ftp.connected:
            return
        job = self.transfers.start_next()
        if job is None:
            return
        runner = self._transfer_runners[job.id]
        self._refresh_transfer_view()

        def done(_result):
            self.transfers.finish(job.id)
            self._transfer_runners.pop(job.id, None)
            self._refresh_transfer_view()
            self.status_var.set(f"Transferencia completa: {job.source}")
            if job.direction == "upload":
                self.refresh_remote()
            else:
                self.refresh_local()
            self._run_next_transfer()

        def error(exc: Exception):
            self.transfers.finish(job.id, str(exc))
            self._transfer_runners.pop(job.id, None)
            self._refresh_transfer_view()
            self.status_var.set(f"Error de transferencia: {exc}")
            self._run_next_transfer()

        self.run_async(lambda: runner(job.id), on_done=done, on_error=error)

    def queue_upload_paths(self, selected: list[Path], base_remote: str,
                           completion_label: str = "Subida completada") -> None:
        """Queue selected paths without claiming transfer resume."""
        for source in selected:
            destination = posixpath.join(base_remote, source.name)

            def runner(job_id, local=source, remote=destination):
                if not local.exists():
                    raise FileNotFoundError(str(local))
                if local.is_file():
                    self.ftp.ensure_dir(posixpath.dirname(remote))
                    ResumableFTP(self.ftp).upload(
                        local, remote,
                        lambda done, total, phase: self._transfer_progress(
                            job_id, done, total, f"{phase}: {local.name}"))
                    return
                if not local.is_dir():
                    raise ValueError(f"Ruta no válida: {local}")
                self.ftp.ensure_dir(remote)
                directories = sorted(p for p in local.rglob("*") if p.is_dir())
                files = sorted(p for p in local.rglob("*") if p.is_file())
                for directory in directories:
                    self.ftp.ensure_dir(posixpath.join(
                        remote, directory.relative_to(local).as_posix()))
                total = sum(p.stat().st_size for p in files)
                completed = 0
                for file in files:
                    remote_file = posixpath.join(
                        remote, file.relative_to(local).as_posix())
                    size = file.stat().st_size
                    ResumableFTP(self.ftp).upload(
                        file, remote_file,
                        lambda done, _total, phase, base=completed, name=file.name:
                            self._transfer_progress(job_id, base + done, total,
                                                    f"{phase}: {name}"))
                    completed += size
                self._transfer_progress(job_id, total, total, f"Carpeta {local.name}")

            self._enqueue_transfer("upload", str(source), destination, runner)
        self.status_var.set(f"{len(selected)} elemento(s) añadido(s) a la cola")

    def queue_download_selected(self) -> None:
        if not self.require_connection():
            return
        selected = [self.remote_items[i] for i in self.remote_tree.selection()
                    if i in self.remote_items]
        if not selected:
            messagebox.showinfo("PS4 GoldHEN Manager",
                                "Selecciona archivos o carpetas en el panel de la PS4.")
            return
        base_local = self.local_path

        def safe_name(name: str) -> str:
            if not name or name in (".", "..") or "/" in name or chr(92) in name:
                raise ValueError(f"Nombre remoto no seguro: {name!r}")
            return name

        for item in selected:
            try:
                destination = base_local / safe_name(item.name)
            except ValueError as exc:
                messagebox.showerror("PS4 GoldHEN Manager", str(exc))
                return

            def runner(job_id, remote_item=item, local_target=destination):
                completed = 0

                def visit(entry, target: Path):
                    nonlocal completed
                    if entry.is_dir:
                        target.mkdir(parents=True, exist_ok=True)
                        for child in self.ftp.listdir(entry.path):
                            visit(child, target / safe_name(child.name))
                    else:
                        before = completed

                        def progress(received, _total):
                            self._transfer_progress(job_id, before + received,
                                                    None, f"Descargando {entry.name}")

                        ResumableFTP(self.ftp).download(
                            entry.path, target,
                            lambda done, total, phase: self._transfer_progress(
                                job_id, before + done, None, f"{phase}: {entry.name}"))
                        completed += target.stat().st_size

                visit(remote_item, local_target)

            self._enqueue_transfer("download", item.path, str(destination), runner)
        self.status_var.set(f"{len(selected)} elemento(s) añadido(s) a la cola")

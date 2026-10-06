# PS4 GoldHEN Manager

Free and open-source Windows companion app for PS4 consoles running GoldHEN.

## v1.0.0 public scope

This first public release focuses on the stable core:

- Automatic PS4 discovery on the local network.
- FTP file manager over GoldHEN FTP (`:2121`).
- Local/remote file transfers and `/data/pkg` workflow.
- Remote PKG Installer support (`:12800`) with integrated HTTP server.
- GoldHEN Payload Server support (`:9090`).
- Klog viewer (`:3232`).
- ps4debug-NG integration (`:744`) for notifications and supported debug operations.
- Installed-game library from `app.db` copies/backups.
- PS4 information and service status.
- Cheats & Mods support for compatible GoldHEN cheat definitions, intended for local/offline use.
- Windows branding/icon and PyInstaller build script.

Experimental Online catalog, Themes and Trophies UI are intentionally excluded from v1.0.0 while they are being redesigned and tested.

## Requirements

- Windows 10/11.
- PS4 and PC on the same local network.
- GoldHEN active on the PS4.
- Enable the specific GoldHEN service you want to use (FTP, Payload Server, Klog, etc.).
- Remote Package Installer must be running on the PS4 for RPI installs.

## Run from source

```bat
pip install -r requirements.txt
python ps4_manager.py
```

## Build the Windows EXE

Run:

```bat
build_windows.bat
```

The compiled executable is created under `dist/` and uses the bundled PS4 GoldHEN Manager icon.

## GitHub Actions

The workflow in `.github/workflows/build-windows.yml` builds the Windows executable on GitHub Actions, so contributors do not need to build locally just to validate packaging.

## Safety / legal

Use only software, homebrew, payloads and PKG content that you are authorized to use. The project does not include commercial games, license keys or DRM-bypass content.

Cheats & Mods are intended for local/offline use. Do not use them to interfere with competitive online play.

## Credits

PS4 GoldHEN Manager builds on public PS4 homebrew interfaces and projects including GoldHEN, ps4debug-NG and Remote Package Installer. Those projects remain the work of their respective authors.

## License

MIT License. See `LICENSE`.

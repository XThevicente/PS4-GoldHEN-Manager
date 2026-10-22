# Changelog

## 1.1.0-beta.2

- Keep PS4 discovery independent from the FTP session: Payload, Klog, Debug or RPI can identify a reachable console while FTP is unavailable.
- Show console detection and FTP status separately, with a dedicated FTP retry button and available service indicators.
- Preserve the configured FTP port during discovery, startup and service checks.
- Keep non-FTP services visible after an FTP failure; retain detailed login and unreachable-host errors.
- Ignore stale connection/service callbacks after disconnecting or changing the selected host.
- Add offline connection regression tests and a real-console acceptance guide.

## 1.0.0

First public GitHub release candidate.

- Stable-core public scope.
- PS4 discovery and GoldHEN FTP file management.
- PKG analysis, `/data/pkg` transfers and Remote Package Installer support.
- Payload Server, Klog and ps4debug-NG integration.
- PS4/game information and Cheats & Mods tools.
- Windows application icon and PyInstaller packaging.
- Removed experimental Online, Themes and Trophies navigation from the public UI for now.
- Added MIT license and cleaned repository files for public distribution.

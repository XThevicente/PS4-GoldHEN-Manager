# Contributing to PS4 GoldHEN Manager

Thanks for helping improve the project.

## Bug reports

Please use the GitHub bug-report template and include:

- PS4 firmware
- GoldHEN version
- Windows version
- App version
- Which service was enabled (FTP / Payload / Klog / ps4debug-NG / RPI)
- Exact steps to reproduce
- Screenshot or traceback when available

Avoid attaching commercial game content, license keys, copyrighted PKG files, or personal credentials.

## Feature requests

Open a feature request and explain:

- What problem the feature solves
- How you expect it to behave
- Whether it depends on a specific GoldHEN service or PS4 firmware

## Pull requests

1. Fork the repository.
2. Create a focused branch.
3. Keep changes small and testable.
4. Run the app from source and make sure it starts cleanly.
5. If packaging changed, run the Windows build or let GitHub Actions validate it.
6. Open a pull request with a short explanation and testing notes.

## Scope

The public v1.x branch focuses on the stable core: discovery, FTP, PKG workflows, GoldHEN services, ps4debug-NG, PS4 info/library and local/offline Cheats & Mods.

Experimental Online catalog, Themes and Trophies work is intentionally not part of the initial public scope.

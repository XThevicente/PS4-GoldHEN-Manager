# Eden4PS4 — experimental Switch emulator port for PS4

This folder tracks a **research/porting experiment**, not a finished emulator.

The starting point is the public `eden-emulator/mirror` branch `eden-orbis-ps4`, which already contains substantial OpenOrbis work (PS4 audio/input/platform code, Dynarmic adjustments and a Vulkan path). We pair it with the public `orbis-sdk-v1` bundle from `orbis-ports/orbis-porting-kit`, which contains OpenOrbis v0.5.4, orbis-compat and the PS4 RADV/Mesa build.

## First milestone

1. Configure Eden for `x86_64-pc-freebsd12-elf`.
2. Link the PS4 RADV static driver (`libvulkan_radeon.a`).
3. Build `eden-cli`.
4. Produce an OpenOrbis `eboot.bin`.
5. Only then move to real PS4 hardware tests with freely distributable Switch homebrew/test software.

## What this does NOT contain

No Nintendo firmware, production keys, commercial game images or other proprietary console files are included or downloaded by this project.

## Current upstream inputs

- Eden: `eden-emulator/mirror@eden-orbis-ps4`
- PS4 SDK bundle: `orbis-ports/orbis-porting-kit@orbis-sdk-v1`
- OpenOrbis inside bundle: v0.5.4
- Mesa/RADV inside bundle: `orbis-mesa-1a59cc147b54`

The CI workflow deliberately runs on an experimental branch and does not modify the stable PS4 GoldHEN Manager release.

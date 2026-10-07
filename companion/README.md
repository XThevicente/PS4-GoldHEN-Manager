# PS4 GoldHEN Companion v0.3

Experimental **PS4 app ↔ PS4 GoldHEN Manager on PC** bridge.

## Implemented

- PC HTTP companion service on TCP `8787`.
- Automatic PC discovery using LAN UDP broadcast on `8786`.
- Six-digit pairing code with persistent bearer token.
- **v0.3:** code entry directly on PS4 with the DualShock 4 (D-pad + X).
- Continuous authenticated heartbeat/poll.
- PC GUI online/offline state.
- First PC → PS4 test command with PS4 acknowledgement.
- OpenOrbis PS4 UI with visible connection/error states.
- Manual PC-IP fallback through `/data/ps4gh_pc_ip.txt`.
- Boot trace at `/data/ps4gh_companion_boot.log`.
- LAN-only; no cloud service.

## Hardware test

1. Run **PS4_GoldHEN_Companion_PC_v0.3.0.exe**.
2. Press **Iniciar servidor**.
3. Install/start the v0.3 PKG on PS4.
4. Existing v0.2.1 paired consoles should reuse their token automatically.
5. On a fresh pairing, enter the six digits shown on the PC using:
   - D-pad Left/Right: choose digit.
   - D-pad Up/Down: change digit.
   - X: accept.
6. When connected, the PC should show **PS4: ONLINE**.
7. Click **Probar PC → PS4**. The PS4 should show **COMANDO RECIBIDO / PING DESDE EL PC**, vibrate briefly, and the PC should show the command as acknowledged.

## Files

The PS4 stores:

- `/data/ps4gh_companion.token`
- `/data/ps4gh_companion_last.json`
- `/data/ps4gh_companion_boot.log`

Legacy/fallback:

- `/data/ps4gh_pair_code.txt` only if controller initialization is unavailable.
- `/data/ps4gh_pc_ip.txt` to bypass automatic discovery.

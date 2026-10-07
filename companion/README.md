# PS4 GoldHEN Companion v0.1

First working protocol prototype for **PS4 app ↔ PS4 GoldHEN Manager on PC**.

## Implemented

- PC HTTP companion service on TCP `8787`.
- Automatic PC discovery using a one-way LAN UDP broadcast on `8786`.
- Six-digit pairing code generated on every PC server start.
- Persistent random bearer token after pairing.
- OpenOrbis PS4 client source that discovers the PC, pings it, pairs and performs an authenticated status request.
- The PS4 stores its token in `/data/ps4gh_companion.token`.
- The last server response is written to `/data/ps4gh_companion_last.json`.
- No cloud service is used; this is LAN-only.

## PC test

```bat
cd companion
py -3 companion_server.py
```

The server prints its local URL and a six-digit pairing code.

## PS4 build

The PS4 app is based on the public OpenOrbis v0.5.4 toolchain and its network/HTTP APIs.

```bash
cd companion/ps4_app
./prepare_openorbis_assets.sh
make
```

Expected package:

`IV0000-BREW00152_00-GOLDHENCOMPANION.pkg`

## First pairing on PS4

1. Start `companion_server.py` on the PC.
2. Note the six-digit pair code.
3. Create `/data/ps4gh_pair_code.txt` on the PS4 containing only that code.
4. Start **PS4 GoldHEN Companion**.
5. The app listens for the PC advertisement on UDP 8786 and obtains the PC IP automatically.
6. It pairs over HTTP 8787 and stores a random token.
7. Later launches reuse the saved token.

This v0.1 is intentionally a transport prototype. The next phase is a proper PS4 UI/controller screen and integration of Start/Stop + pair code directly into the Windows Manager.

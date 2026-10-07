# PS4 GoldHEN Companion v0.4

Experimental PS4 ↔ PC bridge for PS4 GoldHEN Manager.

## Working foundation

Already validated on real PS4 hardware in v0.3:

- UDP discovery.
- HTTP 8787 authenticated link.
- Persistent token pairing.
- Continuous heartbeat.
- PC → PS4 command.
- PS4 → PC acknowledgement.

## New in v0.4

- **Mostrar mensaje**: type text on the PC and show it on the PS4.
- **Pedir info consola**: PS4 reads the system software version and reports it to Windows.
- **Reiniciar enlace HTTP**: recreates the PS4 HTTP/SSL connection without rebooting the PS4.
- **Enviar archivo pequeño**: up to 512 KiB; saved as `/data/ps4gh_received.bin`.
- **Local DS4 menu**: press OPTIONS while connected.
- Pairing with the controller remains available on fresh installations.

## v0.4 hardware test

1. Start `PS4_GoldHEN_Companion_PC_v0.4.0.exe`.
2. Start the v0.4 PS4 PKG.
3. Confirm Windows shows `PS4: ONLINE`.
4. Test **Mostrar mensaje**.
5. Test **Pedir info consola**.
6. Test **Reiniciar enlace HTTP** and verify the heartbeat returns.
7. Send a small disposable test file and confirm `/data/ps4gh_received.bin` exists.
8. Press **OPTIONS** on the DualShock 4 and navigate the local menu.

The v0.4 branch is still a hardware-test build and is not merged into the public stable Manager.

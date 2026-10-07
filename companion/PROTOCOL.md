# PS4GH Companion Protocol v1

## LAN ports

- UDP `8786`: PC advertisement
- TCP `8787`: HTTP control API

Advertisement payload:

```
PS4GH_OFFER_V1|<ipv4>|<http-port>|<hostname>
```

## Pairing

Unauthenticated:

- `GET /api/v1/ping`
- `GET /api/v1/pair?code=NNNNNN&device=PS4`

The six-digit code is shown by the Windows Companion. From v0.3 the PS4 app can enter it with the DualShock 4 D-pad and X, so no FTP pairing file is required on normal hardware.

## Authenticated requests

Send:

```
Authorization: Bearer <token>
```

Endpoints:

- `GET /api/v1/status` — authenticated status.
- `GET /api/v1/poll` — heartbeat + pending PC command.
- `GET /api/v1/ack?id=<command-id>&result=ok` — PS4 acknowledgement.
- `POST /api/v1/event` — PS4 event payload.

### Command object

```json
{"id":1,"name":"ping","created_at":0}
```

The server keeps a command pending until the PS4 acknowledges the same ID. The v0.3 hardware test implements `ping` as the first PC → PS4 command.

## Presence

The PS4 polls approximately every two seconds. The PC GUI considers it online while the last authenticated poll is recent.

The protocol is intended for the local network only and should not be exposed directly to the Internet.

# PS4GH Companion Protocol v1

## LAN ports

- UDP `8786`: PC advertisement.
- TCP `8787`: authenticated HTTP control/data API.

Advertisement:

```
PS4GH_OFFER_V1|<ipv4>|<http-port>|<hostname>
```

## Pairing

Unauthenticated:

- `GET /api/v1/ping`
- `GET /api/v1/pair?code=NNNNNN&device=PS4`

The PS4 stores the returned bearer token and reuses it on later launches.

## Authenticated requests

Header:

```
Authorization: Bearer <token>
```

Endpoints:

- `GET /api/v1/status`
- `GET /api/v1/poll`
- `GET /api/v1/ack?id=<command-id>&result=<short-result>`
- `GET /api/v1/file?id=<file-id>`
- `POST /api/v1/event`

## v0.4 commands

The PC places one command at a time in the poll response.

### Message

```json
{"id":10,"name":"message","text":"Hola desde el PC"}
```

The PS4 shows it on-screen and ACKs `shown`.

### Console information

```json
{"id":11,"name":"get_info"}
```

The PS4 reads the system software string using `sceKernelGetSystemSwVersion` and ACKs a short result such as:

```
fw_13.520.000_app_0.4.0
```

### HTTP reconnect

```json
{"id":12,"name":"reconnect"}
```

The PS4 ACKs, tears down its HTTP/SSL/pool context and creates a fresh local link without rebooting the console.

### Small file push

The PC keeps a selected file in memory (maximum 512 KiB), queues:

```json
{"id":13,"name":"fetch_file","file_id":1,"file_name":"test.bin","file_size":1234}
```

The PS4 downloads it from `/api/v1/file?id=1` and stores it at:

```
/data/ps4gh_received.bin
```

This is intentionally a first transport test, not yet a general file manager.

## Local PS4 menu

Press **OPTIONS** while connected:

- ESTADO
- INFO CONSOLA
- RECONECTAR HTTP
- VOLVER

Use D-pad Up/Down, X to accept, Circle to go back.

The protocol is designed for the local network only and must not be exposed directly to the Internet.

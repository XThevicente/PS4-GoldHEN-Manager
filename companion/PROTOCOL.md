# PS4GH Companion Protocol v1

## LAN ports

- UDP `8786`: PC advertisement
- TCP `8787`: HTTP control API

Advertisement payload:

```
PS4GH_OFFER_V1|<ipv4>|<http-port>|<hostname>
```

## Unauthenticated

- `GET /api/v1/ping`
- `GET /api/v1/pair?code=NNNNNN&device=PS4`

## Authenticated

Send:

```
Authorization: Bearer <token>
```

Endpoints:

- `GET /api/v1/status`
- `POST /api/v1/event`

The protocol is intended for the local network only and should not be exposed directly to the Internet.

# PC discovery protocol ("HTDQ/HTDR", version 1)

Shared by the Android app (`android-beam-eyes-tracker`) and the PC app (`pc-beam-eyes-tracker`).
Purpose: the phone finds the PC on the LAN without the user typing an IP address.

## Transport

- UDP. The PC listens on the **discovery port 4244** (configurable) bound to `0.0.0.0`, and
  also answers discovery requests that arrive on its **tracking port** (default 4242): the PC
  tells the two apart by size and magic, so a phone that already knows the tracking port can
  probe it directly.
- The phone sends one request to the IPv4 limited broadcast `255.255.255.255:4244` and to the
  directed broadcast of its Wi-Fi subnet (e.g. `192.168.1.255:4244`), from an ephemeral socket
  with `SO_BROADCAST`. It repeats the request every 500 ms for ~1.5 s and collects replies,
  de-duplicated by source address. Replies arrive on the same ephemeral socket.
- Everything is little-endian. Neither side authenticates: trusted LAN only, like the pose port.

## Request — 16 bytes, phone → PC

| Offset | Type | Field | Value |
| --- | --- | --- | --- |
| 0 | 4 × u8 | magic | ASCII `HTDQ` |
| 4 | u8 | version | 1 |
| 5 | u8 | kind | 0 = request |
| 6 | u16 | reserved | 0 |
| 8 | u32 | nonce | random; the reply echoes it |
| 12 | u32 | reserved | 0 |

A receiver rejects anything that is not exactly 16 bytes with this magic and version.

## Reply — 16 + n bytes (n ≤ 64), PC → phone, unicast to the request's source

| Offset | Type | Field | Notes |
| --- | --- | --- | --- |
| 0 | 4 × u8 | magic | ASCII `HTDR` |
| 4 | u8 | version | 1 |
| 5 | u8 | kind | 1 = reply |
| 6 | u16 | trackPort | UDP port the PC accepts pose packets on (default 4242) |
| 8 | u32 | nonce | copied from the request |
| 12 | u16 | capabilities | bit field, see below |
| 14 | u8 | nameLength | n, 0..64 |
| 15 | u8 | reserved | 0 |
| 16 | n × u8 | name | UTF-8 PC name (hostname or user-chosen), no terminator |

Capabilities:

| Bit | Name | Meaning for the phone |
| --- | --- | --- |
| 0 | ACCEPTS_POSE | the tracking port takes 48-byte opentrack packets |
| 1 | ACCEPTS_EXTENDED | the tracking port also takes 72-byte HeadTrack packets |
| 2 | ANSWERS_PINGS | 20-byte `HTPG` pings sent to the tracking port are answered → the phone may enable pings and show *acknowledged* / RTT |
| 3 | GAME_OUTPUT | the PC is currently feeding a game directly (freetrack / TrackIR emulation) |
| 4 | LOCAL_TRACKING | the PC is tracking with its own webcam right now (a phone connecting will override it only if the user allows; informational) |

Validation on the phone: exact magic/version/kind, `length == 16 + nameLength`, `nameLength ≤ 64`,
valid UTF-8 (invalid → name shown as the IP), `trackPort` in 1..65535, nonce matches one of the
requests sent in this scan (otherwise dropped). Unknown capability bits are ignored.

## Behaviour when a discovered PC is chosen

The phone fills host = reply source IP, port = `trackPort`. If `ACCEPTS_EXTENDED` and
`ANSWERS_PINGS` are both set it switches to the 72-byte format with pings on, so the link
ladder reaches *acknowledged* and shows the RTT; otherwise it keeps the 48-byte opentrack
format with pings off (never ping a plain opentrack).

## Why not mDNS

mDNS needs multicast membership and `NsdManager` on Android, which is flaky on many phones
and needs a lock on some vendors. A broadcast request/reply over plain UDP works on every
Wi-Fi network that lets the two devices talk at all, which is already required for tracking,
and reuses the same socket code as the pose stream.

# MeshCore on the Universe 2026 badge: feasibility

Date: 2026-10-28. Nothing here was run on a badge or a MeshCore node. Labels: **verified** = read in a cited source, **inferred** = my deduction from sources, **unknown** = needs a test.

## Bottom line

- Best path: badge as BLE central to your own BLE companion node. Protocol is documented. Odds it works today: roughly 25% before review, revised to about 3% (see "What changed after review"), because the node requires an encrypted PIN-paired link and I could not confirm the badge BLE stack can pair (see below).
- Wi-Fi TCP path is technically simpler but needs a Wi-Fi companion build you compile yourself. Likely not what your nodes run.
- Qw/ST serial board: plausible, but needs extra hardware, and nothing is built.
- Public data over Wi-Fi: no documented public API found.

## Badge side

| Claim | Label | Source |
|---|---|---|
| RM2 wireless module, use firmware networking APIs, do not drive WL_* pins | verified | repo `hardware/README.md` "Wireless module" |
| Wi-Fi and Bluetooth are listed as needing real-badge checks | verified | repo `.github/skills/hyb26-skill/references/capabilities.md` |
| Firmware exposes the standard MicroPython `bluetooth` module (`bluetooth.BLE()`, `gap_scan`, `gap_connect`, `gattc_*`) | verified | `badge/apps/camera/camera_ble.py` uses all of these as a central |
| Camera app avoids BLE SMP pairing and does its own application-level pairing over a custom GATT service | verified (code), reason inferred | `camera_ble.py`, `camera_pairing.py`. Possibly because SMP is not available, but no comment says so |
| Simulator Bluetooth is an inert stub, no raw sockets | verified | `references/simulator.md` |
| `gap_pair`, `_IRQ_ENCRYPTION_UPDATE`, `_IRQ_PASSKEY_ACTION` exist in upstream MicroPython, but "currently only supported when using the NimBLE stack on ESP32, STM32 and Unix" | verified | https://docs.micropython.org/en/latest/library/bluetooth.html (Pairing and bonding) |
| RP2350 with CYW43/RM2 uses the BTstack, not NimBLE | inferred. Upstream Pico 2 W board sets `MICROPY_BLUETOOTH_BTSTACK ON` ; Pimoroni's tufty2350 README does not say which stack its firmware uses | https://raw.githubusercontent.com/micropython/micropython/master/ports/rp2/boards/RPI_PICO2_W/mpconfigboard.cmake ; https://github.com/pimoroni/tufty2350 |
| Badge firmware (1.29 preview) can run `gap_pair` | unknown | test on badge |
| No repo app uses `gap_pair`, `gap_passkey` or `config(bond=...)` | verified | grep of `badge/` |

## MeshCore companion protocol

| Claim | Label | Source |
|---|---|---|
| BLE service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (Nordic UART). RX `...0002...` is app to node (write). TX `...0003...` is node to app (notify/read) | verified | https://docs.meshcore.io/companion_protocol/ and `#define SERVICE_UUID/CHARACTERISTIC_UUID_RX/TX` in https://github.com/meshcore-dev/MeshCore/blob/main/src/helpers/esp32/SerialBLEInterface.cpp |
| BLE frame = one characteristic write or notification, no length prefix | verified | https://github.com/meshcore-dev/MeshCore/wiki/Companion-Radio-Protocol ; docs.meshcore.io/companion_protocol |
| Default MTU 23 (20 byte payload), app should request larger (doc example 512); firmware calls `BLEDevice::setMTU(MAX_FRAME_SIZE)` | verified | docs.meshcore.io/companion_protocol ; SerialBLEInterface.cpp |
| Node requires encrypted, MITM-protected read/write on both characteristics, secure connections + bonding, static PIN | verified | SerialBLEInterface.cpp: `ESP_LE_AUTH_REQ_SC_MITM_BOND`, `ESP_GATT_PERM_READ_ENC_MITM`, `ESP_GATT_PERM_WRITE_ENC_MITM`, `setStaticPIN` |
| Default PIN is `123456`; PIN also reported in device info bytes 4-7 | verified | https://github.com/meshcore-dev/MeshCore/blob/main/docs/faq.md section 6.5 ; MyMesh.cpp DEVICE_INFO layout |
| So an unencrypted GATT connection cannot read or write anything useful | inferred | from the permissions above |
| Serial framing: app to radio `<` (0x3c) + u16 LE length + body; radio to app `>` (0x3e) + u16 LE length + body | verified | Companion-Radio-Protocol wiki; `SerialWifiInterface.cpp` header comment |
| Wi-Fi companion uses the same 3-byte header over TCP; port is a caller parameter; FAQ says Ethernet uses port 5000 "same as the WiFi companion radio" | verified (framing), inferred (port 5000 for Wi-Fi) | https://github.com/meshcore-dev/MeshCore/blob/main/src/helpers/wifi/SerialWifiInterface.cpp ; faq.md 7.8 |
| Wi-Fi companion firmware must be compiled by you with SSID and password | verified | faq.md 7.6 |
| Command codes: APP_START 1, SEND_TXT_MSG 2, GET_CONTACTS 4, GET_DEVICE_TIME 5, SYNC_NEXT_MESSAGE 10, DEVICE_QUERY 22 | verified | https://github.com/meshcore-dev/MeshCore/blob/main/examples/companion_radio/MyMesh.cpp |
| APP_START: code, app_ver, 6 reserved, app name (wiki). MyMesh.cpp reads app name at offset 8 | verified | wiki + MyMesh.cpp |
| DEVICE_QUERY: `[22, app_target_ver]`; reply code 13, 82 bytes | verified | wiki + MyMesh.cpp |
| SELF_INFO (5): node name starts at offset 58 | verified | MyMesh.cpp |
| GET_CONTACTS reply: CONTACTS_START 2, CONTACT 3 each, END_OF_CONTACTS 4 | verified | wiki |
| CONTACT frame: key 1-32, type, flags, out_path_len, out_path (MAX_PATH_SIZE), name 32, last_advert u32, lat, lon, lastmod | verified | MyMesh.cpp. MAX_PATH_SIZE value not read, so the prototype parses name and last_advert from the frame end (inferred offsets) |
| Messages: V3 only if app_target_ver >= 3. DM v3 (16): snr, 2 reserved, 6-byte pubkey prefix, path_len, txt_type, u32 timestamp, text. Channel v3 (17): snr, 2 reserved, channel_idx, path_len, txt_type, u32 timestamp, text. Non-V3 codes 7 and 8 drop the 3 snr/reserved bytes | verified | wiki + MyMesh.cpp |
| Push 0x83 MSG_WAITING means call SYNC_NEXT_MESSAGE; empty queue replies NO_MORE_MESSAGES (10) | verified | wiki + MyMesh.cpp constants |
| SEND_TXT_MSG: `[2, txt_type, attempt, u32 timestamp, 6-byte pubkey prefix, text<=160]`; reply SENT (6) or ERR (1) | verified | wiki + MyMesh.cpp |
| Channel text arrives as "sender: text" in the text field | unknown | not in MyMesh.cpp (frame carries channel_idx only, no channel name or sender) or meshcore_py reader.py. The app just shows the text |
| CURR_TIME (9) reply is `[9, u32 LE]` | verified | MyMesh.cpp, meshcore_py reader.py (example 78 56 34 12 = 0x12345678) |
| Send to a channel (CMD 3): `[3, txt_type 0, channel_idx, u32 LE ts, text]` | verified (not used by the app) | MyMesh.cpp, meshcore_py messaging.py |
| Pages say "still in development", may be inaccurate. Wiki says "may be outdated" | verified | both pages |
| CCCD descriptor sits at value handle + 1 on the node | unknown. No longer assumed, the app now discovers descriptors and looks for 0x2902 | see review section |

## Options ranked (effort, then chance of working today)

1. **BLE central to a BLE companion node.** Effort: prototype exists. Chance: ~25%. Fails if the badge BTstack cannot do SMP passkey pairing, or does not persist keys. Fallback idea: none on the badge side, since the node enforces encryption.
2. **Wi-Fi TCP client to a Wi-Fi companion node.** Effort: low to medium (reuse the same frame parser, add `socket` plus the 3-byte header). Chance: ~70% if you have such a node, ~0% if not, and building it needs a firmware compile. No encryption or PIN described in the files read (unknown whether there is auth). Venue Wi-Fi client isolation could block it (inferred).
3. **Qw/ST serial or LoRa board.** Effort: medium to high (buy or wire a board, write a driver, GPIO4/5 I2C on 3V3_SW per `badge/AGENTS.md`). MeshCore serial companion exists (faq.md 1.2.3) but needs a UART, and Qw/ST is I2C, so an I2C-UART bridge chip is needed (inferred). Chance: moderate, not today.
4. **MQTT or Wi-Fi bridge from a node.** Effort: medium to high. `src/helpers/bridges/` exists in the repo but I did not read what it does (unknown). Chance: unknown.
5. **Public map or stats data.** https://map.meshcore.io is listed in the FAQ (1.2) but I found no documented API on the map page or in the FAQ. Do not build on this without finding one. The map site's GitHub link was not followed.

## What the prototype does

`badge/apps/meshcore_feed/`. Demo mode has canned messages and contacts and is the only part that can run in the simulator. Live mode: scan for the Nordic UART UUID in advertisements, connect, `gap_pair` with a keyboard-only IO capability and PIN 123456, exchange MTU, discover, enable notifications, then DEVICE_QUERY, APP_START, GET_DEVICE_TIME, GET_CONTACTS, SYNC_NEXT_MESSAGE. SELECT on a DM sends a canned reply with SEND_TXT_MSG, which would put a real message on your mesh, so test it with a contact you know.

Validators passed (py_compile, validate_app, validate_app_readmes). That proves syntax and structure only.

## Test order on the badge

1. Run Demo. Confirm layout and scrolling in both orientations.
2. Live: does `bluetooth.BLE().config(bond=True, mitm=True, le_secure=True, io=3)` raise? If so, pairing config is not supported and the BLE path is dead.
3. Does the status reach "encrypted, discovering"? If it stalls at "pairing", `gap_pair` is a no-op or the passkey action never fires.
4. If paired: do contacts and messages appear? A failed subscribe points at the CCCD handle assumption.
5. Only then try a reply send.

## What changed after review (2026-10-28)

Nothing below ran on a badge or a node. Sources were read over HTTPS, some through a summarizing fetch tool, so re-check the quoted code lines before relying on them.

### Revised odds: about 3% that live mode works on this firmware

Why:

- Pairing is compiled only for NimBLE on rp2. In upstream `ports/rp2/CMakeLists.txt` the `MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING=1` define sits in the NimBLE branch only (verified in upstream master, https://raw.githubusercontent.com/micropython/micropython/master/ports/rp2/CMakeLists.txt). The default in `extmod/modbluetooth.h` is 0 (verified). The CYW43 boards use BTstack (verified for Pico 2 W; inferred for the badge firmware).
- Even where BTstack pairing is compiled, `mp_bluetooth_gap_passkey()` returns `MP_EOPNOTSUPP` and there are no `SM_EVENT_PASSKEY_*` handlers (verified, `extmod/btstack/modbluetooth_btstack.c` master). MeshCore needs MITM with a static PIN, so the badge would have to type the PIN. That path does not exist in that file.
- Docs say pairing is only supported on NimBLE (ESP32, STM32, Unix) (verified, docs.micropython.org/en/latest/library/bluetooth.html).
- The camera app does its own application-level pairing and the repo has no `gap_pair` use (verified by grep). That fits, but is not proof.
- The remaining 3% is for a Pimoroni or GitHub build that differs from upstream (unknown) or a node set to a no-PIN mode (not seen in the sources).

The protocol side is now mostly verified, so a firmware that could pair would probably get to messages. The blocker is the radio stack, not the protocol.

### Protocol checks against the app

- Contact frame is 148 bytes with `MAX_PATH_SIZE` 64: name at 100-131, last_advert u32 LE at 132. The app counts from the end (`f[-48:-16]`, `len-16`), which gives the same offsets and works for any path size. Verified: MyMesh.cpp, meshcore_py reader.py.
- DM v3 (16): prefix 4-9, txt_type 11, timestamp 12-15 LE, text 16. v2 (7): prefix 1-6, txt_type 8, timestamp 9-12, text 13. Channel v3 (17): channel 4, timestamp 7-10, text 11. v2 (8): timestamp 4-7, text 8. All matched the app.
- SEND_TXT_MSG `[2, 0, attempt, u32 LE ts, 6 byte prefix, text]`, APP_START name at offset 8, DEVICE_QUERY `[22, 3]` matched.
- Fixed: DMs with txt_type 2 (signed) carry 4 signature bytes before the text. The app showed them as text garbage. meshcore_py reader.py parses them the same way.
- Fixed: CCCD handle no longer assumed. The app discovers descriptors after the TX value handle and writes the 0x2902 one.

### Code fixes in the app

- Live stops before scanning with "this firmware cannot pair" if `gap_pair` or the pairing `config()` is missing. Passkey failure is also caught.
- Per-state deadlines for scan, connect, pairing, discovery and subscribe. Bad PIN, disconnect and write refusal show a specific message on the start screen. Re-pick Live to retry.
- A write-done status other than 0 (for example "insufficient authentication") is now an error instead of being ignored.
- Malformed frames are caught per frame. Text is cleaned to printable ASCII without `bytes.decode(errors=)`, which MicroPython may not honor. Long words wrap by character, names and titles truncate with "..".
- Contacts: parsed with a hard stop at 300 and 100 kept, newest first.
- The simulator showed that `bytes(bluetooth.UUID(...))` and `bytes[::-1]` fail in MicroPython. Both were removed. Scan match also accepts a name starting with "MeshCore", since the UUID may only be in the scan response.
- Reply safety: SELECT on a DM opens the canned list, SELECT picks a text, a confirm screen shows recipient and text, and a second SELECT (ignored for 700 ms) sends. The recipient is fixed when the reply starts, so a new message arriving cannot change it. Sending also needs a ready link and a node time. Canned texts are 4 short plain replies. Demo mode never sends.
- Start screen now says live is probably going to fail.

### Still only a real node and badge can show

- Whether the badge firmware has any pairing support at all.
- Whether `bytes(UUID)` order and the advertising match find the node.
- Whether the ESP32 node notifies frames up to 172 bytes after the badge MTU exchange (contacts, messages). With MTU 23 they would be cut off.
- Whether notifications are queued correctly under 80 events when 50+ contacts arrive.
- Channel text format and whether `sender: text` is in the text.

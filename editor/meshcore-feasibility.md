# MeshCore on the Universe 2026 badge: feasibility

Date: 2026-10-28. Nothing here was run on a badge or a MeshCore node. Labels: **verified** = read in a cited source, **inferred** = my deduction from sources, **unknown** = needs a test.

## Bottom line

- Best path: badge as BLE central to your own BLE companion node. Protocol is documented. Odds it works today: roughly 25%, because the node requires an encrypted PIN-paired link and I could not confirm the badge BLE stack can pair (see below).
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
| RP2350 with CYW43/RM2 uses the BTstack, not NimBLE | inferred (from general MicroPython rp2 port knowledge, not checked in this repo) | n/a |
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
| Channel text arrives as "sender: text" in the text field | unknown | not in sources read |
| CURR_TIME (9) reply is `[9, u32 LE]` | inferred | code 9 verified, layout not read |
| Send to a channel (CMD 3) layout | unknown | not read, prototype does not send to channels |
| Pages say "still in development", may be inaccurate. Wiki says "may be outdated" | verified | both pages |
| CCCD descriptor sits at value handle + 1 on the node | unknown | the prototype assumes this. Discover descriptors if the subscribe write fails |

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

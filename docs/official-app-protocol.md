# Official App Protocol Decode (LOY SPACE APK)

We found the companion Android app ("LOY SPACE" v1.2.19) that was almost
certainly used to reverse-engineer this project, and de-minified its JS. This
doc records everything learned from it. Companion docs: `README.md`,
`docs/protocol-and-research.md`, `docs/server-and-api.md`.

## How to un-minify (the tool)

The app JS is minified uni-app bundle code. We used **js-beautify** (npm):

```bash
cd APK/LOY-SPACE_1.2.19_apkcube/base/assets/apps/__UNI__F2139F6/www
mkdir -p www.beautified
for f in app-service.js app-view.js app-config-service.js; do
  npx --yes js-beautify "$f" -o "www.beautified/$f"
done
```

`app-service.js` contains all BLE/protocol code. Beautified copies are kept at
`www.beautified/`. (No other tool needed; js-beautify via npx requires no
global install.)

## Device discovered

Probe results (`python3 cli_tools/panel_probe.py`) against the panel:

```
dev_info:  id=525C110050  model=EXYC-A  FW=V2.8.8#V01
          dtype=server,tc  cid=1317053139463734
          features: pwd (password), remote, FONT:en_US04, HS (high-speed stream)
param_dev: 64x64
rotate:    0°
pgm_play:  model=1 index=0 ids=[1..60]  <- panel DEFAULT play queue (all 60 slots), NOT occupancy!
pgm_key:   pro 1 -> non-zero key (content stored); pro 2..60 -> all-zero keys (EMPTY)
```

**Reading it correctly (we got this wrong once):** `ids_pro` = the panel's
full 60-slot play queue/sequence, not a list of stored programs. Occupancy is
per-slot and lives in `pgm_key` (non-zero 20-byte key = content, all zeros =
empty). On this panel only **slot 1** has content (= the single GIF the app
shows). Empty slots fall back to attract/demo, which is why the panel seemed to
"revert" — it was cycling slots 2..60 in its default queue. Sending
`dispatch`/`pgm_play` for a specific slot pins the panel to that one program.

## Frame format (confirmed byte-for-byte identical to our Python)

```
AA 55 FF FF LEN(2) SNO(2) FLAGS(1) TYPE(1) PAYLOAD [CHK16 if FLAGS&0x80]
total_len = LEN + 6;  checksum = sum(all preceding bytes) & 0xFFFF
```

The app's `ve()` writes magic `21930`(=0x55AA) + `FFFF`, len, sno, flags, type,
payload, checksum — exactly what `led_matrix_api/protocol/frame.py` does.

## Frame types (msg_type)

| type | meaning |
|---|---|
| `0x02` | rt_show / program-stream frames (flags 0xC1) |
| `0x03` | **plain command frames** (get/set/delete/play control) — flags 0xC1 |
| `0x06` | unsolicited **upload** frames FROM panel (e.g. pgm_play changes) |
| `0x82/0x83/0x84` | acks FROM panel: `0x82`/`0x84` = ok/failed(errcode byte), `0x83` = tagged payload |
| `0x80` | sentry/other ack (`0x80` seen with payload 1) |
| `0x85` | err (payload 0xFF seen for unsupported type) |

## Command catalog (wire bytes)

All commands start with a TLV-ish `[TAG, LEN, ...]`. Command frames = type 0x03.

| cmd | bytes | notes |
|---|---|---|
| **get (query)** | `[tag, 0]` | tag = the ack tag you expect back. Ge list below. |
| delete all | `[8, 2, 0, 255]` | del_ids=[255] = everything |
| delete ids | `[8, 1+n, 0, (id-1)...]` | 1-based ids on wire minus 1 |
| delete rects | `[8, …, 1, (pro-1), (rect-1)...]` | subtype 1 |
| delete items | `[8, …, 2, (pro-1), (rect-1), (item-1)...]` | subtype 2 |
| format | `[8, 1, 3]` | full erase/reset |
| power on/off | `[4, 2, 0, type]` | type 0/1 (clamped to ≤1) |
| power schedule | `[4, 2+4n, count, 127, (HH:MM-HH:MM)*n]` | week bitmask + periods |
| brightness fixed | `[6, 2, type, value]` | value 0..15 (0→15) |
| brightness schedule | `[6, 1+3n, count, (value, HH, MM)*n]` | |
| param_dev set | `[27, 5, 1, w u16, h u16]` | set panel size |
| dispatch/play | `[24, 6, 2, (pro-1), ignore, mode, loop u16]` | play program `pro`, loop forever |
| dispatch next/prev | `[24, len, value(0|1)…]` | type 0, target 0 |
| dispatch type1 item | `[24, len, 5, (pro-1), (rect-1), (item-1), …]` | play single item |
| pgm_play set | `[54, 2+n, model, index, (id-1)...]` | model0 = single by index; model2 = ids list |
| pgm_play get | `[54, 0]` | → returns playing queue! |
| pgm_key get | `[53, n, (id-1)...]` | n=0 → ts_update; n>0 → keys for those programs |
| rotate set | `[47, 1, deg/90 %4]` | |
| timing (RTC) | `[5, 9, yy u16, mo, day, hh, mm, ss, ms u16]` | set device clock |
| show_dev | `[41, 1, e]` | show device-info screen on panel |
| pgm_flicker | `[45, 6, type, count_pgm, time u32]` | program-switching flash |
| rt_draw fill | `[50, 13, 1, color3, x0,y0,x1,y1 u16*4]` | draw rect |
| rt_draw px | type 16 + data `[[x,y]...]` | direct pixel write |
| bluetooth set | `[52, 2, type, val]` | |
| ani_num | `[51, …]` | clock/date widget text |
| pwd | `[55, …]` | bluetooth password |
| upgrade | case 14 | firmware flash via BLE (md5 + checksum) |

### "get" command tags (frame type 0x03, payload `[tag, 0]` unless noted)

`4` power · `6` light · `10` dev_info · `27` param_dev · `45` pgm_flicker ·
`47` rotate · `51` ani_num · `52` bluetooth · `53` pgm_key · `54` pgm_play ·
`55` pwd · `56` global_ani · `57` channel_color · `58` mirror · `62` sensor
· `dev_info_all` = `[10,0,27,0,4,0,6,0]` (dev_info+param_dev+power+light in one).

## ACK / upload decode (from panel, frame type 0x83 and 0x06)

Tag data formats (see app `l()` + `p()`):

- **54 pgm_play**: `model=e[0], index=e[1], ids_pro=[e[i]+1 …]` — the current
  play queue. `index` is which one is active.
- **53 pgm_key**: 4 bytes = `ts_update` (unix ts); otherwise rows of 21 bytes:
  `id_pro = e[i]+1, key = next 20 bytes (hex)`.
- **10 dev_info**: comma string `id_dev,ver1,ver2,model,expend[,cid,other…]`.
  `other` markers: `pwd`, `FONT:…`, `HS` (high-speed), `CLOCK:…`, `remote`.
- **27 param_dev**: `w u16, h u16` (defined-form only).
- **6 light / 4 power / 47 rotate / 45 pgm_flicker / 51 ani_num …** per above.
- `0x83` can pack **multi_ack**: `[tag][len][data]` repeated.

The panel **pushes uploads** (type `0x06`) on its own, e.g. `pgm_play` changes
(tag 54), `power`, `game_info`. `e.upload.pgm_play.ids_pro` = current ids.

## Keys to our earlier mysteries (answers!)

1. **"Can we ask the current running program?" → YES.** `get: pgm_play`
   (type 0x03, `[54,0]`) → `{model, index, ids_pro}`. Also passive uploads.
2. **60-slot play queue (not occupancy)** — `pgm_play` returns all 60 slots
   by default; real occupancy is per-slot via `pgm_key` (non-zero key =
   content). Our panel: only slot 1 has the GIF.
3. **Why content "reverted"/attract-mode came back:** the panel cycles its
   default 60-slot queue and empty slots show attract/demo. We only ever wrote
   slot 1; without `dispatch` pinning one program, the queue keeps cycling.
   Fix: either `delete` old slots (del_all) before uploading, or upload to a
   fresh slot and `pgm_play`/`dispatch` it.
4. **Our Python builders are byte-identical to the official app** — text
   segments (`[0,fg3,bg3][1,code,font,size]…`), region headers (tags 8/9/12/
   13/17/20/25/28/29/22), brightness `[6,2,…]`, dispatch `[24,6,2,…]`,
   delete `[8,…]`. The "server doesn't work" issue was delivery/ack pacing,
   not protocol.
5. **Write parameters**: `size_ble = 180` (our proven chunk!), `write_time`
   pacing, windowed send (`swnd_max` 5–10 with per-ack continuation),
   TLV packets of **960** (`GSize_max_pkt_tlv` 997, legacy 128 pre-V2.8.7),
   MTU-3 "high-speed" mode when the device advertises MTU. ACK-paced
   sequential sending (our approach) is a valid degenerate version.
6. **Continuous-scroll animation exists** (`type_ani=0` = continuous left
   scroll, 27 = right) — the firmware can ticker-scroll without GIF, but the
   text renderer still wraps at panel width; our GIF approach is still valid.

## Probe tool

`cli_tools/panel_probe.py` — read-only queries (sends only `get` commands):

```bash
python3 cli_tools/panel_probe.py                          # default gets
python3 cli_tools/panel_probe.py --gets pgm_key=all       # inventory keys
python3 cli_tools/panel_probe.py --gets dev_info,param_dev,pgm_play
```

## Meaning of dev_info feature markers (remote, HS, …)

`dev_info`'s comma-separated `other` list advertises optional features:

- **`remote`** → the panel supports the optional **RF remote-control accessory**
  (telecontrol). App shows remote-config/pairing UI (`startpairing`, key
  mapping, `unpair`); pairing/binding uses the `telecontrol` command (tag 52);
  i18n: *"requires the additional purchase of a remote-control accessory"*.
  Not a BLE thing — it's the IR/RF remote accessory.
- **`HS`** → **high-speed BLE streaming** enabled. The app then uses
  `type_streamsend = 2` (sliding-window sender, `swnd_max` = 10 in-flight
  packets, ack-driven window) and bigger GATT writes
  (`write_max_byte = high_speed_max_byte`, default 509, or `MTU:`-3 if the
  device advertises MTU; 248 for old FW < V2.8.8). Without HS it falls back to
  `write_max_byte = 180`, `swnd_max` 3–5, sequential sends — the lane our
  Python uses (works, just slower). HS only speeds up transfers; it is not
  required for correctness.
- `pwd` → BLE pairing password supported; `FONT:...` → font pack; `CLOCK:...`
  → clock widget; `car_linkage` → car-linkage accessory; `MTU:NNN` → negotiated
  ATT MTU (write size = NNN−3).

## Storage capacity — NOT exposed

There is **no protocol command or dev_info field that reports flash/storage
size** (we searched). Known constraints instead:
- **max 60 programs** (count limit; a 60-slot default play queue always
  exists, but most slots are empty on our panel — only slot 1 has content),
- up to **8 partitions** per program, 10 saved templates,
- the panel errors with **"storage space is full" (`data_error`)** if an upload
  exceeds free space.

To estimate capacity you'd have to either look up the EXYC-A controller
hardware sheet, or experimentally upload increasingly large synthetic programs
until the full-storage error bites (invasive — not recommended casually).

---

## Remaining unknowns / fun leads

- Exact meaning of `model` in pgm_play (0=sequence?, 1/2=…) and `index` vs
  ids semantics; test `pgm_play {model:2, ids:[…]}` to play a custom subset.
- `dev_key` (BLE password) handshake — panel has `pwd` feature; `get dev_info`
  with a key is `[10, len, key…]`.
- `rt_draw type 16` = raw pixel drawing — could render without GIF at all.
- `upgrade` — full OTA over BLE.
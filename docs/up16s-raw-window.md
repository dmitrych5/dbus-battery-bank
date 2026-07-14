# UP16S raw status window (0x78 at 0x3000+)

An undocumented read command: a raw window into the BMS's internal status array. Its value
over the JBD-documented commands is freshness — PackStatus (0x78 0x1000) answered by a chain
master for a slave comes from a cache refreshed only about every 50 s, while this window
reads the addressed pack's live state, including forwarded slaves.

**Status: UNTRUSTED.** Not JBD-documented, so nothing decoded from it may feed control until
validated (strategy below). The decode lives in `battery_bank/transport/up16s_raw_window.py`;
today it is only dumped to the log once per pack at discovery.

## Addressing

The window maps request start address → status-array register: `register = start_addr −
0x3000`. The scan is end-exclusive, so to read registers R0..R1 request start `0x3000+R0`,
end `0x3000+R1+1`. Range cap: `register < 0x200`; on the deployed firmware everything at and
after register 0x101 reads as zeros, so the driver requests only registers 0x000–0x100 (as
two fixed part commands, keeping each response near the proven PackStatus size).

Example — pack voltage through all cells and temps (regs 6…0x1F) from slave 2:
`02 78 30 06 30 20 00 00 <crc>`.

## Validation strategy

- **Master**: read 0x78 0x1000 (fully JBD-tested) and 0x78 0x3000 back-to-back; pack voltage,
  per-cell voltages, current, temps, SOC should match or be very close. This validates the
  entire offset map every cycle.
- **Slave**: cross-check the live 0x3000 decode against the master's cached view of that same
  slave (0x78 0x1000 addressed to the slave, or 0x44). The cache is built from the slave's
  0x45 response, i.e. a different serializer over the same registers. When the read is fresh,
  they agree; a structural mismatch (not just staleness) means one path moved — stop trusting
  0x3000.
- **Compare in physical units** (V, A, °C, %), not raw shorts: the commands use different
  encodings for the same quantity — e.g. current is `(val−300000)/100` in 0x78 0x1000 but
  `(val−30000)/100` in the 0x3000 reg-8 view, and temps are Celsius-offset in both of those
  but Kelvin in 0x45.
- **Layout-independent physical invariants** (catch a shift even if both paths moved
  together), to run on every sample; on violation discard, alarm, and fall back:
  - Σ(16 cell voltages) ≈ pack voltage (reg 6) — a wrong cell offset breaks this immediately.
  - The max/min-cell-voltage registers (0x25/0x27) equal the max/min of the decoded cell
    list, and the index registers (0x24/0x26) point at the matching cell.
  - Ranges: cells within ~2.0–3.8 V (LFP), temps within a sane band, SOC 0–100.
  - Current sign agrees with the operation-status register (0x35: 1 = charging,
    2 = discharging) and with the SOC trend over time.

## Caveats

1. **The protection and warning bitmasks are not in this window.** They live in separate
   firmware globals, not the status array, so 0x3000 cannot answer "is this pack in a
   fault." For alarm/protection state the serialized paths remain the only source of truth —
   0x78 0x1000 offsets 32/36, or 0x45.
2. The advertised CCL/DCL/CV/DV are here (0x3C–0x3F per pack, 0xD1–0xD4 aggregated) — the
   limits the BMS is actively computing and will enforce.
3. Scales often differ from the serialized 0x1000/0x45/0x50 commands (current especially);
   decode to engineering units before trusting or cross-checking.

## Register map

Indices/scales are what this firmware actually does. Cell/sensor indices are 1-based.

### Per-battery live measurements

Valid on whichever battery is addressed, including forwarded slaves.

| reg | field | decode |
|---|---|---|
| 0x06 | pack voltage | V = val/100 |
| 0x08 | pack current | A = (val − 30000)/100, signed offset (±~327 A range) |
| 0x09 | MOSFET temp | °C = (val − 500)/10 |
| 0x0A | ambient temp | °C = (val − 500)/10 |
| 0x0C–0x1B | cell voltages 1–16 | mV |
| 0x1C–0x1F | cell temp sensors 1–4 | °C = (val − 500)/10 |
| 0x24 / 0x25 | max-voltage cell # / value | index (1-based) / mV |
| 0x26 / 0x27 | min-voltage cell # / value | index / mV |
| 0x28 / 0x29 | max-temp sensor # / value | index / °C = (val − 500)/10 |
| 0x2A / 0x2B | min-temp sensor # / value | index / °C = (val − 500)/10 |
| 0x30 | MOS state | bit0 = charge MOS on, bit1 = discharge MOS on |
| 0x31 | SOC (fine) | 0.01 % (0–10000) |
| 0x32 | residual capacity | Ah = val/100 |
| 0x35 | operation status | 0 = idle, 1 = charging, 2 = discharging |
| 0x36 | avg cell temp | °C = (val − 500)/10 |
| 0x37 | avg cell voltage | mV |
| 0x38 | cell spread (max − min) | mV — medium confidence; great imbalance signal |
| 0x3A | cell fault/offline bitmask | bit = cell — medium confidence |
| 0x3B | temp-sensor fault bitmask | bit = sensor open/short — medium confidence |

### Per-battery dynamic limits

What the BMS itself is advertising for this pack, so worth respecting directly.

| reg | field | decode |
|---|---|---|
| 0x3C | charge current limit (CCL) | A = val/10 |
| 0x3D | discharge current limit (DCL) | A = val/10 |
| 0x3E | charge voltage limit | V = val/10 |
| 0x3F | discharge voltage limit | V = val/10 |

### Counters / health (this pack)

On the master the 0xC8+ mirror is bank-wide instead.

| reg | field | decode |
|---|---|---|
| 0x41:0x42 | total charged | Ah = val/10, 32-bit (hi:lo) — medium confidence |
| 0x43:0x44 | total discharged | Ah = val/10, 32-bit — medium confidence |
| 0xE1 | SOH | % |
| 0xF3 | cycle count | count |

### Aggregated view

On the master these are the whole-bank rollup; read from a forwarded slave they describe
that slave alone.

| reg | field | decode |
|---|---|---|
| 0xC8 | pack voltage (avg) | V = val/100 |
| 0xC9:0xCA | total current | 32-bit; ≈ A = val/100 signed — verify scale (the 0x50 field of the same quantity is val/10) |
| 0xD0 | SOC (coarse) | % (0–100) |
| 0xD1 / 0xD2 | CCL / DCL aggregated | A = val/10 |
| 0xD3 / 0xD4 | charge / discharge voltage limit aggregated | V = val/10 |
| 0xD5 | packs-available bitmask (low 16) | bit = pack |
| 0xDA / 0xDC | max / min cell voltage | mV |
| 0xDE / 0xE0 | max / min cell temp | °C = (val − 500)/10 |
| 0xF6 | pack temperature | deci-Kelvin: °C = (val − 0x0AAB)/10 — different encoding, easy trap |

### Tooling / state (situational, not for control)

0x46 debug-mode state, 0x4B/0x4C/0x4E tooling MOS/contact/balance overrides, 0x4D sleep
command, 0x51 balancing bitmask (cell 1 = LSB), 0x52 battery address, 0x55 MOS enable/disable
control, 0x58/0x59 comm watchdog, 0x5A shutdown request. 0x49 is an ADC-derived
hardware-ID-ish value.

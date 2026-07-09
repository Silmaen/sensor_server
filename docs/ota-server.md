# OTA firmware delivery — server contract

This document is the **server-side contract** for over-the-air (OTA) firmware
delivery. It is the counterpart of the firmware project's protocol spec
(`../sensor_iot/docs/ota-calibration-protocol.md`) and is authoritative for
everything that runs inside this stack: the hardware registry, the firmware
catalog, binary hosting, the publication API, the extended MQTT ingestion, and
the OTA push orchestration.

> **Status: CONTRACT FROZEN v1.0 — server & firmware converged.** All decisions
> D1–D14 are closed, including **D3** (the 8-char `HW_CODE` seed is frozen, see §9).
> Implementation may start (sequencing: firmware doc §10).

**Related docs:** [mqtt-protocol](mqtt-protocol.md) · [read-api](read-api.md) ·
firmware side: `../sensor_iot/docs/ota-calibration-protocol.md`

---

## 0. Scope & principles

The server plays three distinct roles, kept strictly separate:

1. **Hardware registry + firmware catalog** — the source of truth for which
   hardware types and revisions exist and which firmware images have been
   published for them.
2. **Binary hosting** — persistent storage and plain-HTTP serving of the `.bin`
   images to devices on the filtered LAN.
3. **Push orchestration** — deciding, per device, which compatible image to send
   and tracking the outcome.

Two invariants govern the whole design:

- **Publish ≠ deploy.** Publishing an image (registry + catalog + hosting) never
  pushes anything to a device. Deployment is a separate, explicit action.
- **The registry is strictly what the CI publishes.** The server never writes a
  `HardwareCode`/`HardwareRevision`/`Firmware` row on the strength of what a
  device reports over MQTT. A device that claims an unknown hardware code is
  treated exactly like a device that reports no code at all (see §4).

Threat model (from the firmware spec §0): filtered IoT LAN, no Internet, trusted
internal image server. Security v1 = **HTTP + MD5** (integrity only). No TLS, no
image signing in v1.

---

## 1. Data model

### 1.1 `HardwareCode` — hardware type registry

| Field | Type | Notes |
|-------|------|-------|
| `hw_code` | `CharField(max_length=8, primary_key=True)` | validated `^[A-Z0-9]{8}$` |
| `platform` | `CharField` | e.g. `ESP8266`, `ESP32-C3`, `SAMD21` |
| `description` | `CharField` | human label |
| `modules` | `JSONField` (list) | e.g. `["BME280", "BATTERY", "DEEP_SLEEP"]` |
| `created_at` / `updated_at` | `DateTimeField` | |

Populated **only** by the publication API (§3, endpoint A1). The 8-char code is
just a stable key; the meaning lives in this row.

### 1.2 `HardwareRevision` — physical/electrical revision

| Field | Type | Notes |
|-------|------|-------|
| `hardware_code` | `FK → HardwareCode` (`CASCADE`, `related_name="revisions"`) | |
| `hw_rev` | `PositiveSmallIntegerField` | |
| `description` | `CharField` | |
| `bat_divider_nominal` | `FloatField(null=True)` | nominal divider ratio for this rev |
| `notes` | `TextField(blank=True)` | |
| `created_at` / `updated_at` | `DateTimeField` | |

`unique_together = (hardware_code, hw_rev)`. Populated only by the API (A2).

### 1.3 `Firmware` — published image catalog

| Field | Type | Notes |
|-------|------|-------|
| `hardware_revision` | `FK → HardwareRevision` (`PROTECT`, `related_name="firmwares"`) | identity is `(hw_code, hw_rev)` |
| `version` | `CharField` | semver |
| `file` | `FileField(upload_to="fw/<hw_code>/<hw_rev>/")` | stored as `<version>.bin` |
| `md5` | `CharField(max_length=32)` | recomputed server-side on upload |
| `size` | `PositiveIntegerField` | |
| `uploaded_at` | `DateTimeField(auto_now_add=True)` | |
| `notes` | `TextField(blank=True)` | |

`unique_together = (hardware_revision, version)`. Populated only by the API (A3).

### 1.4 `Device` — new fields

The existing `fw_version`, `hardware_id`, `display_name` are kept. Changes:

| Field | Type | Notes |
|-------|------|-------|
| `hardware_code` | `FK → HardwareCode` (`null=True, blank=True, on_delete=SET_NULL`) | replaces the free-string `hw_code`; set only when the reported code resolves in the registry, else `NULL` |
| `hw_rev` | `PositiveSmallIntegerField(null=True, blank=True)` | reported revision |
| `ota_capable` | `BooleanField(default=False)` | from capabilities `ota` |
| `calibration` | `JSONField(default=dict, blank=True)` | server-side mirror (§5) |
| `commands` | `JSONField(default=list, blank=True)` | from the `commands` message |
| `command_params` | `JSONField(default=dict, blank=True)` | from the `commands` message |

**Single FK (decided).** The free-string `hw_code` is replaced by the FK alone.
When the reported code is absent from the registry the FK is `NULL` and the device
is flagged "needs update"; the raw claimed code is **not** retained. A device that
reports no code and one that reports an unpublished code are therefore
indistinguishable in the UI — both show the generic "needs update" state.

`hw_rev` is a plain int, **not** a composite FK to `HardwareRevision` (Django has
no native composite FK). Compatibility of `(hw_code, hw_rev)` is validated at push
time (§6), not by a DB constraint.

---

## 2. Firmware hosting

This stack serves the binaries itself; nothing is delegated to an external host.

- **Storage:** firmware images are Django **media**. `MEDIA_ROOT` lives under
  `${DATA_DIR}` via a bind mount (persistent across rebuilds); firmware is laid out
  `fw/<hw_code>/<hw_rev>/<version>.bin`. The publication API (A3) writes here.
- **Serving:** **nginx** serves Django static + media from disk (standard
  deployment); the firmware `.bin` is exposed to devices at **`location /fw/`**
  (matching the frozen `ota_update.value` URL), streamed read-only from the
  volume. nginx never does TLS (external). Daphne is untouched by the download.
- **Auth:** the download is **unauthenticated** — devices hold no credentials and
  the frozen protocol puts a plain, token-less URL in `ota_update.value`. This is
  a documented exception to "only `/healthz/` is unauthenticated" (security.md),
  justified by the threat model (trusted filtered LAN, HTTP + MD5).
- **Base URL:** an `OTA_BASE_URL` env var (e.g. `http://srv.interne`) prepended to
  build the `value` field of the `ota_update` command. Must be reachable by
  devices on the LAN.

Firmware upload handling must stream to disk (images ~400 KB–1 MB); verify
`DATA_UPLOAD_MAX_MEMORY_SIZE` / file upload handlers.

---

## 3. Publication API (CI → server)

Endpoints called by the firmware project's `publish_firmware.py` (§8/§8bis of the
firmware spec). Verbs/paths are indicative.

| # | Need | Request | Server effect |
|---|------|---------|---------------|
| A1 | Register/update a **hardware code** | `PUT /api/hw/codes/<hw_code>` `{platform, description, modules[]}` | upsert `HardwareCode`; validate `^[A-Z0-9]{8}$` |
| A2 | Register/update a **hardware revision** | `PUT /api/hw/codes/<hw_code>/revs/<hw_rev>` `{description, bat_divider_nominal?, notes?}` | upsert `HardwareRevision` under the code |
| A3 | **Publish a firmware image** | `POST /api/firmwares` multipart: `firmware.bin` + `{hw_code, hw_rev, version, md5, size, notes?}` | validate code+rev exist, recompute MD5 == provided, store `.bin`, create `Firmware` |
| A4 | **List** published firmwares | `GET /api/firmwares?hw_code=&hw_rev=` | avoid duplicates / see latest |
| A5 | Get the **latest version** of a type | `GET /api/firmwares/latest?hw_code=&hw_rev=` | push decision aid |

Rules:

- **A3 rejects** if `(hw_code, hw_rev)` does not already exist (A1/A2 must precede)
  — enforces registry consistency.
- The server **recomputes the MD5** of the received binary and rejects on
  mismatch (end-to-end integrity up to hosting).
- Re-publishing the **same** `(hw_code, hw_rev, version)` → **`409` by default**,
  explicit replacement via **`?overwrite=true`** (decided).
- The API **pushes nothing to devices**.

### 3.1 Authentication — separate from the read API

The publication API does **not** use the read-only `api.ApiKey` (which stays
read-only). It uses a **distinct credential** scoped to publication, designed for
a **non-interactive caller — ultimately CI-only (TeamCity)**.

**Decided:** a single **`OTA_PUBLISH_TOKEN`** env var (generated at container
startup like the other secrets), presented as `Authorization: Bearer <token>`,
rotatable via env, no admin UI. If more than one publisher ever exists, a small
`PublishToken` model can replace it later without changing the protocol.

---

## 4. Hardware-code resolution & "needs update"

On every capabilities message the server resolves the claimed code against the
registry:

```
code = capabilities["hw"]
device.hardware_code = HardwareCode.objects.filter(pk=code).first()   # None if unpublished
```

`needs_firmware_update` (a `Device` property, drives the "to update" UI badge) now
merges three cases, treated identically for the user:

1. the device reports **no** `hw_code` (firmware too old);
2. the device reports **no** `fw_version`;
3. the device reports a `hw_code` **absent from the registry** (firmware never
   published via CI).

New semantics: `capabilities reported AND (fw_version empty OR hardware_code is None)`.

The three cases are **not** distinguished in the UI: all surface the generic
"needs update" state. The raw claimed code is not retained (decided).

---

## 5. Extended MQTT ingestion

Changes to the worker (see firmware spec §5). MQTT conventions: all commands to
sleeping devices are **QoS 1**; `ota_update` is **retain=false** (§6).

| Topic | Direction | Server action |
|-------|-----------|---------------|
| `{type}/{id}/capabilities` | dev → srv | parse `id`, `hw`, `hwrev`, `fw`, `ota`, `intrvl`, `metrics`, **`cal`** (store-empty flag). **No longer** expects the command list here. |
| `{type}/{id}/commands` | dev → srv | **new.** Update `Device.commands` / `command_params`. Server sends `request_commands` (QoS 1) alongside `request_capabilities`. |
| `{type}/{id}/calibration` | dev → srv | **new.** Update `Device.calibration` (the mirror). |

### 5.1 Calibration mirror & re-push

The server keeps a per-device calibration mirror, **editable by an admin**. It
applies to **all platforms**, not only OTA-capable ones: SAMD/MKR migrates its
compiled offsets to the runtime store too (firmware D14), so it mirrors and
re-pushes like the ESP devices — even though OTA itself stays ESP-only.

The device advertises its store state via a **`cal` flag in capabilities**:
`cal: 1` = at least one calibration key is written, `cal: 0` = none (new board,
factory reset, chip swap). The flag is **independent of `device_id`** — a
provisioned-but-uncalibrated device reports `cal: 0`.

Re-push flow: when a device reports `cal: 0` **and** the server holds a mirror for
that `device_id`, the server re-pushes the stored values via `set_offset` /
`set_calibration` (QoS 1). No mirror → nothing to push. The server also captures
calibration into the mirror whenever a `calibration` report arrives (bootstrap for
already-tuned units).

**Manual seeding for deployed-unit migration (firmware D13).** Units already in
the field carry compiled calibration. Migration path: an admin enters their known
values into the mirror (per `device_id`) **before** the unit is reflashed with
erase; after provisioning the device boots with an empty store (`cal: 0`) and the
server re-pushes the seeded values. This is why the mirror must be admin-editable
(`Device.calibration` JSON), not only device-reported.

---

## 6. OTA push (server → device)

`send_ota_update(device, firmware)`:

1. **Guard:** refuse unless `device.ota_capable` and `firmware.hardware_revision`
   matches the device's `(hardware_code, hw_rev)`. The server never sends an image
   of another code/revision.
2. Publish the command **QoS 1, retain=false** (queued for a sleeping device via
   the persistent session; `retain=false` avoids redelivery after success — the
   firmware's `ver` guard is the backstop):

```json
{
  "action": "ota_update",
  "value":  "<OTA_BASE_URL>/fw/E8BMEBAT/1/1.1.0.bin",
  "md5":    "<hex 32>",
  "ver":    "1.1.0",
  "hw":     "E8BMEBAT",
  "hwrev":  1
}
```

3. Log a `CommandLog(action="ota_update", status=pending)`.

**Ack handling** (`.../ack`): `status:"start"` is informational; `status:"error"`
with `message ∈ {hw_mismatch, same_version, low_battery, download_failed,
md5_mismatch}` → `CommandLog.mark(FAILED, message)`. Success is **not** acked
(the device reboots).

**Success detection:** at the next `capabilities` carrying the pushed `version`,
`CommandLog.mark(SUCCESS)`; no such capabilities within timeout → `mark(TIMEOUT)`.
If that post-OTA capabilities also reports `cal: 0`, trigger the re-push (§5.1).

---

## 7. UI / admin

- **"Push firmware" action** on a device: enabled only for `ota_capable` devices,
  offering only `Firmware` rows compatible with the device's `(hw_code, hw_rev)`.
- Registry and catalog (`HardwareCode`, `HardwareRevision`, `Firmware`) are
  read-mostly in the Django admin — created by the API, browsable by admins.
- Device admin surfaces the OTA state: `ota_capable`, `hw_rev`, resolved vs
  unresolved hardware code, calibration mirror.

---

## 8. Migrations

- New models: `HardwareCode`, `HardwareRevision`, `Firmware`.
- `Device`: replace free-string `hw_code` with FK `hardware_code`; add `hw_rev`,
  `ota_capable`, `calibration`, `commands`, `command_params`.
- Data migration: resolve existing `hw_code` string values against the (CI-seeded)
  registry; matches set the FK, non-matches → `NULL`; drop the string column. No
  `device_id` remap (firmware D1-b keeps existing ids).

---

## 9. Decisions (all closed)

| Ref | Point | State |
|-----|-------|-------|
| D3 | Freeze the 8-char `HW_CODE` seed and seed the registry via A1 | ✅ frozen: `E8BMEBAT`, `E8SHTBAT`, `E8SHTDSP`, `C3BMELUX`, `MKENVBAT`. Seed the registry via A1 before any OTA push. |

Everything is decided on both sides (firmware doc §11, decisions D1–D14). The server
data layer (registry, `Firmware`, `Device` fields, extended ingestion) and the firmware
portable layer can start in parallel; the OTA push depends on the registry being seeded
with the codes above.

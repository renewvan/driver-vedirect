# driver-vedirect

Reads Victron VE.Direct text mode over USB serial (read-only) and publishes battery, solar and charger to the renewvan MQTT bus

Scaffolded from `renewvan/driver-tank`'s reference shape via `renewvan/skills`'
`renewvan-create-driver` skill (per the **Device** term in `hub/docs/architecture.md`'s
Terminology section: "anything that publishes one specific device or feed
onto the renewvan bus, normalized into the device model"). Layout: pure,
unit-testable conversion math with no I/O, a layered
`config.default.ini`/`config.ini` loader (`config.py`), a persistent MQTT
publisher with LWT (`publisher.py`), a thin publish loop (`driver.py`), a
systemd unit, and its own Dockerfile + CI publishing a versioned image.

**This repo is a scaffold, not a working device yet** — `driver.py`'s
`read_and_publish()` raises `NotImplementedError` until the design round
below is resolved and implemented.

## Design checklist (resolve before first real commit)

- [ ] **Sensing or remapping?** Does this device own the sensing (reads
      hardware directly, like driver-tank's ADS1115) or remap an existing feed
      (like driver-venus's Victron remap)?
- [ ] **Topics published** — replace the section below with the real
      `renewvan/vedirect/<id>/<property>` fields, split into identity
      (published once) vs live (republished every poll). See driver-tank's
      README "Topics published" section for the shape to match.
- [ ] **Hub schema** — does VE.Direct extend an existing
      `hub/schema/*.schema.json` entity, or need a new
      `hub/schema/vedirect.schema.json` + example fixtures
      (`hub/schema/validate-examples.sh` must pass)?
- [ ] **Wiring** — replace the section below with real hardware
      wiring/config, or delete it if this device remaps an existing feed
      instead of reading hardware directly.
- [ ] **`manifest.json`** — fill in `source` (the TODO) and check `publishes`
      lists every entity type this Driver publishes. It must validate against
      the hub's `driver-manifest.schema.json` (CI's Manifest job runs it);
      never add `version` or `board_serial`. See `hub/docs/authoring-a-driver.md`.
- [ ] **Demo fleet** — add this Driver to `hub/schema/demo/fleet.demo-fleet.json`
      if Demo mode should show it (`hub/docs/demo-mode.md`).
- [ ] **`DeviceConfig`** (`driver_vedirect/config.py`) — add the hardware/feed
      -specific fields this device needs, mirrored into `config.default.ini`.
- [ ] **Hub integration** — once a version is tagged, add a
      `driver-vedirect` service block to `hub/docker-compose.yml` (pattern:
      driver-tank's block) and any new keys to `hub/.env.example`.

## Topics published

TODO — see the design checklist above.

## Wiring

TODO — see the design checklist above.

## Development

```bash
git clone git@github.com:renewvan/driver-vedirect.git
cd driver-vedirect
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-test.txt
```

Keep conversion/decision logic in pure functions tested against fixture
values — no MQTT broker or real hardware required for any of it. Before
opening a PR: `pytest tests/ -v` must pass (same command CI runs), and any
change to a published field needs a matching
`hub/schema/vedirect.schema.json` update in a `hub` PR (per
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`,
the two repos are never coupled at build time, only at the schema-version
level).

## Configuration

`config.default.ini` ships every key with a default. Copy
`config.ini.example` to `config.ini` (gitignored) and override only what
differs.

## Running

```bash
pip install -r requirements.txt
python -m driver_vedirect.main --config config.ini
```

Install `driver_vedirect/systemd/driver-vedirect.service` to run at boot
(`Restart=on-failure`).

## Testing

```bash
pip install -r requirements.txt -r requirements-test.txt
pytest tests/ -v
```

## Releasing

Tagging a GitHub release builds and publishes
`ghcr.io/<owner>/driver-vedirect:<tag>`, which `hub`'s deployment compose file pins
by tag (never builds from source — see
`hub/docs/adr/0001-compose-services-via-pinned-images-not-git-submodules.md`).

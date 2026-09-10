# Halo-Asset-Tag-Printer

A small, self-contained Docker service that lets a HaloITSM agent request a
physical asset tag from *inside Halo*, on the asset record they're already
looking at, and have it print on a label printer on the LAN — no separate
app, no web page, no context switch.

## How it works

An agent types a number into an asset custom field (`CFAssetTagPrintQTY`)
and saves. That's the entire interaction. The service polls Halo every 15
seconds for assets with that field set positive, claims the request
(writes the field back to `0`), renders a label (logo, tag number, QR
code), and sends it to the printer over a raw TCP socket.

It polls rather than reacting to a webhook because of one hard
constraint: **the printer is never exposed to the internet.** HaloITSM is
cloud-hosted, so a webhook would mean Halo's cloud reaching *into* the
LAN — the one direction this design exists to avoid. Every connection the
service makes is outbound: HTTPS to Halo's API, and a LAN-local TCP
connection to the printer. Nothing listens for inbound traffic, and
nothing needs to be exposed for this to work.

## Compatibility

Built and verified against a **Brady i4311** in ZPL II (Zebra emulation)
mode, printing 0.5" × 2" polyester stock with resin ribbon. It should work
with any ZPL-compatible printer that accepts raw jobs on TCP port 9100
(Zebra printers included) — the layout math resolves the label's long and
short edge by magnitude rather than by config field name, and scales
every dimension from the configured DPI rather than assuming one. Other
media sizes are supported by that same layout math but are unverified in
the field. If you hit a rough edge on different hardware, please open an
issue.

## Prerequisites

- Docker and Docker Compose (or Python 3.11+ if running without Docker)
- A ZPL-compatible label printer reachable from the Docker host over TCP
  port 9100
- A HaloITSM tenant where you can create a custom field and an API
  application

## Setup

### 1. Clone and configure the printer

```bash
git clone <this repo>
cd halo-asset-tag-printer
cp config/printers.example.yaml config/printers.yaml
```

Edit `config/printers.yaml` with your printer's real IP, media
dimensions, and darkness/speed. **The service refuses to start if the IP
is still the example placeholder** (`192.0.2.10`) — that's deliberate, so
a forgotten edit fails loudly at startup instead of silently timing out.

Start conservative on darkness/speed for a resin ribbon (try a notch
below whatever your printer's documentation suggests) and confirm with a
test print (step 4) before turning it up.

### 2. Set up the Halo side

**Custom field** (Configuration > Custom Fields, or Asset Fields
depending on your Halo version — see below): create one Integer field,
default `0` or blank, added to each asset type that should get a physical
tag. **Verify the default lands on `0` or blank** — a positive default
would flag every newly created asset for printing.

> Halo's UI has moved custom-field management around across versions.
> "Configuration > Custom Fields" and "Configuration > Asset Management >
> Asset Fields" may or may not be the same underlying mechanism in your
> version. What actually matters: after creating the field, check a test
> asset through Halo's API browser/Swagger (`GET /api/Asset/{id}`) and
> confirm the field shows up in a `fields` array with a numeric `id` (not
> a `customfields` array — that's a different, separate mechanism Assets
> also have). You'll need that numeric id either way.

**API application** (Configuration > Integrations > HaloITSM API > View
Applications > New):
- Auth: Client ID and Secret (Services) — OAuth2 client credentials.
- **Log in as: a dedicated Agent account, not Application identity.**
  This matters more than it sounds like it should: in at least one Halo
  version, Application-identity login could not resolve Asset visibility
  at all, no matter what permissions were granted on the application
  itself — Asset visibility turned out to be gated by the underlying
  Agent's own role permissions. Create a dedicated, API-only agent (no
  license, can't log into the web/mobile apps) and make sure *that
  agent's role* has read/update access to Assets.
- Permissions: read assets (list and get), update the qty field. Nothing
  else — no asset create or delete.

**Saved list** (optional): an asset list filtered on the qty field
`> 0`. Not currently used by the service (see "Known limitations" below)
but useful for keeping an eye on what's pending.

### 3. Configure the service

```bash
cp .env.example .env
```

Fill in your Halo tenant's base URL, auth URL, client ID/secret, and the
qty field's name and numeric id (from step 2). Leave `SHADOW_MODE=1` for
your first run against the live tenant — it polls, renders, and logs
exactly what it would do, without claiming or printing anything.

Optionally:
```bash
cp config/fields.example.yaml config/fields.yaml
```
and set `asset_tag` to whichever field actually holds your tag number
(`inventory_number` by default — confirm this matches your convention and
its length against the naming constraints below).

### 4. Use your own logo

Drop any image (PNG, JPEG, GIF, BMP, WebP — anything Pillow can read) into
`assets/` and point `LOGO_ASSET` at it in `.env`. Before spending a
label, preview exactly what it will look like at print resolution:

```bash
python -m tools.preview_logo assets/yourlogo.png
```

This writes `docs/logo_preview_dither.png` and `docs/logo_preview_threshold.png`
at true print size and proportion (a wide or tall logo is fit and
letterboxed correctly, not squashed into a square) — pick whichever
method reproduces your logo's detail more legibly and set `LOGO_METHOD`
accordingly. An empty `LOGO_ASSET` means no logo at all; a missing or
unreadable file prints logo-less with a warning rather than failing the
tag. The running service re-checks the logo file for changes every
`LOGO_REFRESH_MINUTES` (default 5), so replacing the file doesn't require
a restart.

### 5. Bring-up and testing

Two standalone tools let you verify each half independently, with no
physical printing and no Halo writes:

```bash
# Print pipeline only, no Halo involved:
python -m tools.print_test_tag --dry-run          # writes ZPL to a file
python -m tools.print_test_tag                    # sends a real test label

# Halo connectivity only, no printing:
python -m tools.check_halo                        # auth + get_pending() round-trip
```

Once both work, run the real service with `SHADOW_MODE=1` still set and
confirm the logs show exactly the assets you expect before ever flipping
it to `0`.

### 6. Deploy

```bash
docker compose up -d --build
```

Runs as a non-root user, no published ports. The healthcheck watches a
heartbeat file the poll loop touches every cycle; if the loop dies (but
the container doesn't), it goes unhealthy.

## Asset tag naming constraints

The QR code and the printed text always encode the same value — the tag
number alone, no URL, no prefix (Halo's mobile app resolves an asset from
the tag number by itself; a URL only adds length and shrinks the QR).
That value has real constraints on this label size:

- Font size is **fixed**, sized to comfortably fit up to
  `text_target_char_count` characters (default **9** — see
  `config/layout.yaml`), not scaled to each tag's actual length. A tag
  longer than this raises a clear error rather than shrinking further or
  letting it silently truncate. Raise the setting if your convention runs
  longer, but every tag then renders at a smaller fixed size.
- The QR code targets the *minimum* legible module size, not the largest
  that fits — this keeps it visually consistent across tag lengths. At
  quartile error correction (the default — these tags live on handled
  hardware), a version-1 QR symbol holds up to 16 alphanumeric characters
  (uppercase, digits, `-`); longer values force a larger QR version, which
  still fits on this label up to roughly 25 characters before running out
  of room. Avoid lowercase letters in your tag convention — they force a
  much less space-efficient QR encoding mode.

If a tag value doesn't fit — too long, or below the legibility floor —
the service logs a clear error naming the value and its length, and
leaves the asset flagged rather than printing something illegible.

## Guardrails

| Setting | Default | What it does |
|---|---|---|
| `MAX_TAGS_PER_ASSET` | 5 | Caps copies per request; over the cap, clamps and logs it |
| `MAX_ASSETS_PER_POLL` | 25 | Caps how many assets one poll processes; the rest wait for the next poll |
| `SHADOW_MODE` | — | Polls and logs what it would do; claims and prints nothing |

There is deliberately no hourly safety valve. Every print traces back to
someone setting the field by hand, and at this project's scale a
rolling-window counter plus a "stay paused until restart" state (which a
container's own restart policy would silently undo on an unrelated crash)
wasn't worth the added complexity. If your usage pattern ever needs one,
it's a small addition, not a redesign.

## Known limitations

- **Claim before print, not after.** The service writes the qty field
  back to `0` *before* sending the print job, not after confirming it
  printed. If the service dies mid-print, the field is already cleared —
  the agent has to notice no tag came out and re-flag it. This is the
  deliberate tradeoff (biasing toward a visible, self-correcting *missing*
  tag over a silent *duplicate* one), not an oversight.
- **A successful send means the printer accepted the bytes, not that a
  label physically printed.** Out of media, ribbon out, head open, and
  paused all still accept a job over the raw socket.
- **Single instance only.** The claim step is only safe because there is
  exactly one writer. Don't run replicas without adding real distributed
  locking.
- **No server-side filter for pending assets.** Halo's Asset API has no
  equivalent of a saved-list `view_id` or a filter-by-custom-field-value
  parameter for this entity (confirmed against a live tenant's Swagger).
  The service fetches the full asset list every poll and filters
  client-side. Fine at hundreds of assets; worth revisiting if your
  tenant grows into the thousands and polling becomes a real cost rather
  than a hypothetical one.
- **No on-asset audit trail.** A successful print isn't recorded back
  onto the asset (no timestamp field) — the service's own logs are the
  record of what printed and when.

## Configuration reference

See `.env.example`, `config/printers.example.yaml`,
`config/fields.example.yaml`, and `config/layout.example.yaml` for every
setting and what it does — each is documented inline.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## License

MIT — see `LICENSE`.

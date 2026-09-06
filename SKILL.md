---
name: query-luma-events
description: Query Luma's public, read-only event discovery surface without an API key. Use when Codex needs to find upcoming Luma events by city, category, keyword, date, price, availability, or calendar; inspect a specific Luma event; resolve a luma.com/lu.ma slug or URL; or list Luma discovery places and categories.
license: MIT
---

# Query Luma Events

Use the bundled dependency-free CLI to query Luma's live public event data. Treat the underlying endpoints as undocumented and unstable: keep requests read-only, page conservatively, and report when Luma rejects or changes a route.

## Run the CLI

Set the skill directory explicitly, then run the script:

```bash
LUMA_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/query-luma-events"
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --help
```

The CLI writes JSON to stdout and errors to stderr. Add `--pretty` for indented JSON. It requires only Python 3.9+ and no Luma account or API key.

## Choose a command

- Find events: use `search`.
- Inspect one event from an `evt-...` ID, public slug, or Luma URL: use `event`.
- List events from a public Luma calendar: use `calendar`.
- Identify what a Luma slug or URL represents: use `resolve`.
- Look up valid city/place slugs: use `places`.
- Look up valid category names, slugs, and IDs: use `categories`.

Read [references/api.md](references/api.md) only when debugging a route, extending the CLI, or interpreting raw response fields.

## Query workflow

1. Translate the request into API-native selectors:
   - Location: `--city "New York"` or `--place-id discplace-...`
   - Topic: `--category AI` or `--category-id cat-ai`
   - Text: `--query "agent"`
   - Calendar: `--calendar claw` or a Luma calendar URL
2. Add local filters when requested:
   - Dates: `--from YYYY-MM-DD --to YYYY-MM-DD`
   - Modality: `--mode online|offline`
   - Price: `--price free|paid`
   - Availability: `--available`
   - Attendance: `--min-guests N`
   - Distance: `--radius-km N` with a city or coordinates
3. Keep the output small. Start with `--limit 10`; increase `--max-pages` only if filters require scanning further ahead.
4. Fetch event detail only for candidates that need description, hosts, ticket, or venue detail.
5. Present event times in the event's named timezone or the user's requested timezone. Preserve the canonical `https://luma.com/<slug>` link.

## Examples

Find AI events near New York:

```bash
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty search \
  --city "New York" --category AI --limit 10
```

Find free, available events matching a keyword during an inclusive date range:

```bash
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty search \
  --city nyc --query "founder" \
  --from 2026-08-01 --to 2026-08-07 \
  --price free --available --limit 20
```

Inspect an event:

```bash
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty event \
  "https://luma.com/example-slug"
```

Search a public calendar:

```bash
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty calendar \
  claw --query meetup --limit 10
```

Resolve ambiguous names before querying:

```bash
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty places --match "New York"
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty categories --match AI
python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty resolve "https://luma.com/claw"
```

## Guardrails and caveats

- Query only public data. Do not add cookies, credentials, private guest endpoints, or mutation routes.
- Do not confuse this discovery surface (`https://api.luma.com`) with Luma's documented paid management API (`https://public-api.luma.com`).
- Discovery searches return upcoming events. Past-event queries are supported only for calendars with `calendar --period past`.
- City plus category uses Luma's category-near-coordinate behavior. Add `--radius-km` for a strict local radius.
- Date-only bounds are inclusive and compare against each event's local calendar date. Timestamp bounds compare absolute instants.
- Stop or reduce paging on HTTP 429. The CLI retries conservatively and caps scans with `--max-pages`.
- Internal response fields and routes can change without notice. If parsing fails, rerun with `--raw`, inspect [references/api.md](references/api.md), and update the script.

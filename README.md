# Query Luma Events

A Codex skill and dependency-free Python CLI for finding and inspecting public
Luma events without an API key.

The skill can:

- search upcoming events by city, category, keyword, date, modality, price,
  availability, attendance, or distance;
- inspect a public event from its ID, slug, or URL;
- list future or past events from a public Luma calendar;
- resolve Luma URLs and slugs; and
- list Luma discovery places and categories.

It uses Luma's public, read-only discovery surface. That surface is undocumented
and can change without notice.

## Install

Clone the repository into your Codex skills directory:

```bash
git clone https://github.com/jlreyes/query-luma-events.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/query-luma-events"
```

Restart Codex if the skill does not appear immediately.

## Use with Codex

Ask Codex to use `$query-luma-events`, for example:

> Use $query-luma-events to find free AI events in New York next week.

The skill includes the query workflow, guardrails, and output guidance in
[`SKILL.md`](SKILL.md).

## Use the CLI directly

Python 3.9 or newer is the only requirement.

```bash
LUMA_SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/query-luma-events"

python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty search \
  --city "New York" --category AI --limit 10

python3 "$LUMA_SKILL_DIR/scripts/luma_events.py" --pretty event \
  "https://luma.com/example-slug"
```

Run `python3 scripts/luma_events.py --help` for every command and option. See
[`references/api.md`](references/api.md) for the observed API behavior.

## Data and safety boundary

The CLI sends conservative `GET` requests only. It does not use cookies,
credentials, private guest endpoints, or mutation routes. Raw output can still
contain any information Luma exposes on a public event page, so handle saved
results accordingly.

## License

[MIT](LICENSE)

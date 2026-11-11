---
name: lancedb-memory-system
description: "LanceDB memory architecture, deployment, and how to change it - including how to tailor it to its user by asking before changing behaviour."
version: 7.2.0
triggers:
  - "memory setup"
  - "tune memory"
  - "memory health check"
  - "lancedb memory"
  - "vector memory"
  - "lancedb rebuild"
  - "lance db"
  - "lancedb plugin wipe"
  - "graph shows nothing"
  - "memory tools missing"
---

# LanceDB Memory System

Architecture, deployment and modification guide for the local vector memory
store. Format rules for writing memories live in the `memory-writing` skill;
this skill covers how the system is built, how it is deployed, and how to change
or repair it when something is wrong.

Everything here is meant to be acted on: every symptom has a diagnosis step and
a fix. No step asks you to guess.

## Architecture

```
~/.hermes/plugins/lancedb-suite/                      <- Canonical user plugin (survives Hermes updates), with skills/
~/.hermes/hermes-agent/plugins/memory/lancedb-suite/  <- Runtime copy (bundled-first discovery order)
~/.hermes/lancedb/                              <- Database: 4 LanceDB tables
~/.hermes/lancedb-viz/                          <- Deployed visualizer (server.py, maintenance.py, static/, scripts/)
```

`deploy-local.sh` writes both plugin copies and the visualizer deployment from
this repository. The canonical user copy is what keeps the plugin alive across
Hermes source-tree updates; the runtime copy exists because bundled-first
discovery wins when both are present.

**The repository is the source of truth.** Never patch a deployed file
(`~/.hermes/lancedb-viz/`, `~/.hermes/plugins/lancedb-suite/`) directly: the next
deploy overwrites it and the fix disappears. Patch the repository, deploy, then
compare hashes.

## Deployment

The visualizer runs as the `lancedb-viz` Docker container on port 7777
(`lancedb-viz:local` image). With Hermes Hub it is started by the Hub's
`~/github/hermes-hub/services/lancedb-viz/docker-compose.yaml` (external
`hub-services` network, `restart: unless-stopped`); without the Hub, the
`docker-compose.yml` at this repository's root runs it standalone. A systemd
unit on port 7778 is a recovery fallback (`deploy-local.sh --systemd-fallback`),
not an equivalent path.

Chain: repository -> `deploy-local.sh` -> `~/.hermes/lancedb-viz/` -> Docker bind
mounts -> port 7777.

```bash
./scripts/deploy-local.sh --dry-run    # inspect every action first
./scripts/deploy-local.sh              # sync + restart the container
systemctl --user restart hermes-gateway   # REQUIRED when plugin/ changed
./scripts/verify-setup.sh
```

**Python does not hot-reload.** `deploy-local.sh` restarts the container, so
`server.py` changes take effect immediately, but the gateway keeps the plugin
module it already imported. Restart `hermes-gateway` after any `plugin/` change,
or the running agent keeps using the old store. Static assets (`static/`) are
re-read per request and need no restart.

## How the agent is told to use memory

- **System prompt block**: short rules, not a tool list - search before
  answering about a named project, the user's setup or a past decision; write
  without being asked after a correction, a decision, or a config or fix found
  with effort; load `lancedb-suite:memory-writing` before the first write.
- **Skills** ship in `plugin/skills/` and are registered as `lancedb-suite:<name>`
  by `register()`. They are not copied into `~/.hermes/skills/`. A personal skill
  with the same short name can sit beside them as a complement.
- **Write reminder**: every `write_nudge_every` user turns (default 8, `0` off),
  `prefetch()` returns one line asking to store what was just learned. No store
  read, no memory content, no recall indicator.
- **Daily review** (optional): an LLM cron job that reads the last 24 hours of
  conversations, stores what was learned, supersedes what turned out wrong, and
  answers `[SILENT]` otherwise. Created at setup (`daily_review: true`) or from
  the blueprint `lancedb-suite:daily-review`.
- **Recall stays a tool call**: `auto_prefetch` (push memories into every turn) is
  off by default; an off-topic match spliced into the user message misleads more
  than it helps.

Config keys live under `memory.lancedb-suite` in `config.yaml`: `db_path`,
`embed_model`, `daily_review`, `daily_review_time`, `write_nudge_every`,
`embed_keep_alive`, `auto_prefetch`.

## Tables and schema

Four tables in `~/.hermes/lancedb/`, all created and migrated by the store.

**`memories`** (one row per memory)

| Column | Type | Notes |
|---|---|---|
| `id` | string | UUID |
| `content` | string | Canonical rendered text |
| `category` | string | One of 10 categories |
| `entities` | string | JSON array, auto-extracted |
| `links` | string | JSON array, cosine-similar neighbours (untyped) |
| `relations` | string | JSON array of typed relations |
| `tags` | string | JSON array, auto-tagged |
| `quality` | double | Persisted utility score |
| `type` | string | Granular sub-type |
| `source`, `session_id`, `user_id` | string | Provenance |
| `created_at`, `updated_at`, `accessed_at` | double | Unix timestamps |
| `access_count` | int64 | Read counter |
| `vector` | fixed_size_list[768] | `nomic-embed-text` embedding |

**`memory_edges`** (typed relations) - `source_id`, `relation_type`,
`target_label`, `target_id`, `edge_key`, `created_at`.

**`memory_conflicts`** and **`memory_conflicts_archive`** - deterministic
same-subject contradictions: `memory_a_id`, `memory_b_id`, `subject`,
`claim_key`, `value_a`, `value_b`, `status`, `resolution_note`, `resolved_by`,
`conflict_key`, plus timestamps. Similarity never lands here; only an explicit
`key=value` disagreement does.

## Write contract

Structured only. Callers pass `domain`, `subject`, `facts`, `tier`, `category`
(plus optional `relations`); `memory_contract.py` normalizes, validates,
fingerprints and renders the stored string. Never build or parse
`Domain:Subject ... [Tier=N]` by hand. Full field grammar and error codes are in
`docs/memory-contract-v2.md`.

Measured limits (constants in `plugin/memory_contract.py`):

| Limit | Constant | Value |
|---|---|---|
| Domain length | `MAX_DOMAIN_CHARS` | 32 |
| Subject length | `MAX_SUBJECT_CHARS` | 80 |
| Facts per memory | `MAX_FACTS` | 12 |
| Characters per fact | `MAX_FACT_CHARS` | 1000 |
| Characters across all facts | `MAX_TOTAL_FACT_CHARS` | 2000 |
| Relations per memory | `MAX_RELATIONS` | 20 |

A 240-character *hard* cap was measured and rejected: it discarded 56.5% of
valid existing facts, while 1000 characters retained 95.3%. Do not lower
`MAX_FACT_CHARS` without re-running that measurement. Density is steered at
write time instead: `long_fact` (a fact over 240 characters) and `no_relations`
(3+ facts, no relation) come back as warnings from `density_warnings()`. They are
never rejections, and audits and the review inbox do not report them: content
parsed back from storage has lost its relations.

Categories: `project`, `tech`, `fact`, `correction`, `user_pref`, `decision`,
`insight`, `reference`, `pattern`, `question`. Unknown values are rejected, not
coerced.

Relation types: `part_of`, `depends`, `requires`, `runs_on`, `connects_to`,
`uses`, `extends`, `supersedes`, `invalidates`, `contradicts`. Each relation
takes exactly one of `target_id` or `target`; prefer an exact `target_id` from a
search.

`write_mode` defaults to `create` and is non-destructive: an exact retry returns
`status=idempotent`, a same-subject write returns `update_suggested`, and
conflicting claims are blocked. Use `lancedb_update` with a known ID to replace
a memory intentionally.

## Search

Hybrid retrieval fuses BM25 (Tantivy FTS) and vector cosine through Reciprocal
Rank Fusion, behind a deterministic local router: exact strings and identifiers
go lexical, relationship questions go hybrid plus one-hop edge traversal,
everything else goes hybrid. No model call, no extra service.

Admission thresholds (constants in `plugin/store.py`):

- `SEARCH_MIN_BM25_SCORE = 12.80`
- `SEARCH_MAX_COSINE_DISTANCE = 0.30`

A candidate is kept when it clears either. **The BM25 threshold is specific to
pinned `lancedb==0.34.0`**: identical rows and code scored differently under
0.34.0 and 0.38.0, so changing the engine invalidates the calibration even with
no code change. Every response reports `calibrated_engine` and `running_engine`
in its diagnostics; a `matches: false` there means the score is not comparable
and must not be used as evidence.

Single-token queries are a known sharp edge: a bare proper noun often abstains
while the same query in `mode='lexical'` returns the row. The BM25 score of one
short token does not clear 12.80 and the cosine distance stays above 0.30. Use
the full `Domain:Subject`, more tokens, or explicit lexical mode. An abstaining
compact search returns a `hint` saying so.

**What the agent receives.** `lancedb_search` and `lancedb_list` return compact
rows by default: `id`, `key`, `category`, `tier`, up to 4 facts of 220
characters, up to 6 relations as `type -> target (id)`, `match` or `via`, and
`age`; 6 results by default. `view: "full"` returns every stored field and the
scoring detail; `lancedb_get(id)` returns one full memory. The compact view is
rendered in `plugin/__init__.py` (`compact_memory`), so the store, the viz and the
benchmark see unchanged rows.

**Latency.** A warm hybrid search is about 50 ms; almost all of a slow search is
Ollama reloading the embedding model (0.8-2 s) after `embed_keep_alive` (default
30 s) of idle time. Raise it (`10m`, `-1`) if the model may stay resident.

## Tailoring the system to its user

The defaults are a reasonable start, not a fit. How much someone writes, what
their machine runs next to Ollama, and how much they want the agent to act on
its own decide the right settings, and those are the user's calls.

**Ask before changing behaviour.** Anything that changes what gets stored,
recalled, deleted or exposed - thresholds, limits, the daily review, deletion
policy, auto-prefetch, the viz binding - is proposed with its trade-off and
applied only after the user agrees. Read-only checks need no permission.

**Questions that orient the settings.** Ask the ones whose answer you do not
already know, a few at a time, in plain words; skip the rest.

| Ask | Answer -> setting |
|---|---|
| How often will you come back to past work, and on how many projects? | Many projects or long breaks -> keep the write reminder (`write_nudge_every` 8) and suggest the daily review. Occasional use -> `write_nudge_every: 0`. |
| Should I store things without asking, or check with you first? | "Without asking" -> current rules. "Ask first" -> say so in the user's own instructions (SOUL or equivalent); the plugin has no per-write confirmation switch. |
| Do you want a daily review of the last 24 hours? When, and may it delete memories? | Yes -> `daily_review: true`, `daily_review_time`, deletion policy `report` (default) or `allow`. It costs one agent run a day. |
| Does a local LLM share the GPU with the embedding model? How much VRAM/RAM is free? | Room to spare -> `embed_keep_alive: 10m` or `-1` (no 1-2 s reload per search). Tight -> keep `30s`. |
| Do searches miss things you know are stored? | Check fragmentation and FTS coverage first (below), then suggest fuller queries or `mode: lexical`; the abstention thresholds are calibrated and only change with a re-run of the benchmark. |
| Should the visualizer be reachable from your phone or other machines (LAN, Tailscale)? Edit from there, or only read? | `LANCEDB_VIZ_BIND` (`127.0.0.1`, a Tailscale IP, or `0.0.0.0`), plus `LANCEDB_VIZ_TRUSTED_HOSTS` with the tailnet name to allow edits. Recreate the container after a bind change. See the README, "Remote access". |
| Is the hourly maintenance timer installed? | No (catalog install without `deploy-local.sh`) -> offer to install it, or to run the plan periodically. |

**A health check to offer.** Measure, then recommend, then let the user pick:

1. `curl -fsS http://127.0.0.1:7777/api/stats` - size, categories, tiers, `ages`.
   Ignore `access`: reads stopped counting accesses on 2026-09-11 (reads never
   write), so it describes old usage only. Count memories without typed
   relations instead (`/api/typed-edges`): a large share means an unlinked graph.
2. `curl -fsS http://127.0.0.1:7777/api/maintenance/compact/plan` - fragments,
   FTS lag, whether maintenance is due or deferred.
3. `curl -fsS http://127.0.0.1:7777/api/review` - format drift, contradictions,
   broken references (read-only).
4. `lancedb_conflicts` - open conflicts waiting for a decision.
5. A few `lancedb_search` calls on topics the user cares about: do the right
   memories come back?

Report findings as a short list of "what I saw -> what I suggest -> what it
costs", one decision per line, and apply only what the user approves.

**Record the outcome in the user's companion skill `lancedb-memory-system`** (their
own skill of that name, read after this one): the settings chosen and why, what
they declined, their deployment specifics (paths, ports, how the viz is reached,
what restarts what) and pitfalls met on their machine, dated. Next session reads
it instead of asking again. Create it with `skill_manage` if it does not exist
(frontmatter `name: lancedb-memory-system`, a description saying it complements
`lancedb-suite:lancedb-memory-system`), patch it afterwards. Never store
credentials there.

## Changing the system

Where the knobs are:

| Want to change | Edit |
|---|---|
| Field limits, categories, relation types | `plugin/memory_contract.py` constants |
| Abstention thresholds, embedder, dimension | `plugin/store.py` constants |
| Maintenance triggers, retention, cooldown | `server/maintenance.py` constants or the `LANCEDB_*` env vars |
| HTTP surface, UI behaviour | `server/server.py`, `static/` |
| Visualizer look | `static/style.css`, `static/app.js`, `static/graph.js` |

Routine maintenance (hourly timer, through the viz API) and its env overrides:

| Trigger | Default | Env |
|---|---|---|
| Compact above N fragments | 16 | `LANCEDB_MAINTENANCE_MAX_FRAGMENTS` |
| Reindex when more than N% of rows are missing from content FTS | 5 | `LANCEDB_MAINTENANCE_MAX_FTS_UNINDEXED_PCT` |
| Cooldown between compactions (s) | 3600 | `LANCEDB_MAINTENANCE_COOLDOWN_SECONDS` |
| History kept by routine runs (s) | 86400 | `LANCEDB_ROUTINE_RETENTION_SECONDS` |
| Version cleanup at most every (s) | 86400 | `LANCEDB_MAINTENANCE_CLEANUP_INTERVAL_SECONDS` |

Fragmentation and FTS lag cost recall, not only speed. Rows written since the
last FTS build are still searched (LanceDB scans unindexed fragments) but slower
and scored apart from the indexed set; on a store with 51 fragments and 18%
unindexed rows, compaction plus reindex brought two benchmark answers back over
the abstention gate. The database path follows `HERMES_HOME`
(`$HERMES_HOME/lancedb`).

After any change: run the tests, deploy, and verify. A change to retrieval or
the write contract is a behavior change, not a cosmetic one.

## Troubleshooting

### `lancedb_*` tools are missing

The provider is registered but its tools are gated. `memory_provider_tools_enabled()`
(`agent/memory_manager.py`) exposes provider tools when `memory` is in
`enabled_toolsets`, OR when the built-in `memory` tool is present, OR when
`enabled_toolsets` is `None`. A restricted allowlist that omits `memory` loses
all eight tools while the provider stays active, silently.

Fix: add `memory` to the allowlist (for a cron job,
`cronjob(action='update', job_id=..., enabled_toolsets=[..., "memory"])`) and
re-verify. Cron also passes `skip_memory=False`, so memory work in cron is
supported and expected.

### All `lancedb_*` calls return `'NoneType' object has no attribute ...`

The provider initialized with `self._store = None` and did not raise. Two causes
seen in practice:

1. `lancedb` / `pyarrow` missing from the Hermes venv:
   `cd ~/.hermes/hermes-agent && venv/bin/python -c "import lancedb, pyarrow; print('OK')"`.
   Fix: `venv/bin/pip install -r <repo>/requirements.txt`, then restart the gateway.
2. A missing import in `plugin/__init__.py` (dropped by a rebase or cherry-pick).
   Check the gateway log for
   `provider 'lancedb-suite' initialize failed: name 'LanceDBStore' is not defined`.

Data is never lost in either case: the store reopens the existing tables.

### Tools return 0 results while the table clearly has rows

The plugin handle can be pinned to an old MVCC snapshot after an external write.
Verify the row count directly before concluding anything was lost:

```bash
~/.hermes/hermes-agent/venv/bin/python -c "
import lancedb
print(lancedb.connect('$HOME/.hermes/lancedb').open_table('memories').count_rows())"
```

Then restart the gateway or start a new session to reload the handle. The store
calls `checkout_latest()` before every operation (`_fresh()`), but a session
that started before a deploy keeps the old module in memory.

### The deployed plugin is empty after a Hermes update

Hermes updates can clean untracked files in `~/.hermes/hermes-agent/`. The
canonical copy at `~/.hermes/plugins/lancedb-suite/` survives, and its discovery is
the fallback. Restore the runtime copy and restart the gateway:

```bash
cp -r ~/.hermes/plugins/lancedb-suite/{store.py,memory_contract.py,__init__.py,plugin.yaml,skills} \
   ~/.hermes/hermes-agent/plugins/memory/lancedb-suite/
systemctl --user restart hermes-gateway
```

### The graph shows nothing or renders inconsistently

Check `curl -s http://127.0.0.1:7777/api/stats` first. If it returns HTML instead
of JSON, the container is serving a stale Python process: `docker restart lancedb-viz`.
Static-only changes need no restart; a hard browser refresh (Ctrl+F5) clears
cached assets, because the server sends `Cache-Control: no-cache` but browsers
can still render from memory.

### The database grows without bound

A writer batch that replaces the FTS index costs one index generation, one table
version and roughly 5.2 KB of index files. Deferred refresh removes that
rotation entirely, and incumbent BM25 scores provably do not move under deferred
coverage, so the writer reuses any compatible index and rebuilds partial
coverage only on an explicit `refresh_fts_index()` or through maintenance
`optimize()`.

Routine maintenance normally keeps this bounded (see the triggers above). If the
hourly timer is not installed (a catalog install without `deploy-local.sh`), the
database only grows; install it or run the plan below periodically.

Diagnose with the plan endpoint, which reports database bytes, managed backups,
total footprint and estimated reclaimable bytes separately:

```bash
curl -fsS 'http://127.0.0.1:7777/api/maintenance/compact/plan' | python3 -m json.tool
```

Apply with an explicit reclaim (requires `confirmed: true`):

```bash
curl -fsS -X POST -H 'Content-Type: application/json' \
  --data '{"confirmed":true,"mode":"reclaim"}' \
  http://127.0.0.1:7777/api/maintenance/compact | python3 -m json.tool
```

Apply budgets space for the full backup and compaction scratch, creates an
atomic `lancedb-pre-compact-*` copy, verifies its tables, row counts, IDs and
sample content, then rotates older managed backups (two newest verified copies
kept). Manual index-directory deletion stays disabled: a directory absent from
the current version can still be needed by a retained snapshot.

### Format drift, invalid rows, or un-updatable memories

`scripts/audit-memory-format.py` reports drift read-only and never repairs:

```bash
~/.hermes/hermes-agent/venv/bin/python scripts/audit-memory-format.py --db-path ~/.hermes/lancedb --pretty
```

`scripts/migrate-memory-format.py` classifies rows (`canonical`, `auto_fix`,
`quarantine`, `warning_only`) and is **always a dry-run**: there is no apply
mode. Review the report, then plan a separately approved migration on a verified
copy.

A memory whose stored content is not canonical cannot be patched by
`lancedb_update`, because the store re-parses the existing content first and
reports an error about the old content regardless of the patch. Fix those rows
by writing the canonical rendering directly, and never without backing up the
original first.

## API surface

The visualizer server exposes the read and write surface:

```
/api/dashboard /api/stats /api/memories /api/memory /api/search /api/timeline
/api/tags /api/tags/rename /api/tags/merge /api/tags/delete
/api/duplicates /api/conflicts /api/review /api/stale /api/clusters
/api/projection /api/graph /api/typed-edges
/api/update /api/delete /api/import /api/export /api/update_entities
/api/memories/bulk-delete /api/memories/bulk-tag /api/memories/bulk-type
/api/health /api/refresh
/api/maintenance/compact /api/maintenance/compact/plan
```

Add `diagnostics=1` to `/api/search` to see the selected route, abstention
reason, timings, and the calibrated-versus-running engine check.

## Scripts

| Script | Purpose |
|---|---|
| `deploy-local.sh` | Sync repository -> plugin copies + visualizer, restart and verify |
| `verify-setup.sh` | Post-deployment smoke test against the canonical endpoint |
| `docker-run.sh` | Standalone dev container (same container name; do not run beside the Hub service) |
| `audit-memory-format.py` | Read-only format drift report, `--fail-on-drift` for CI |
| `migrate-memory-format.py` | Dry-run-only row classification |
| `migrate_graph_retention.py` | Backfill concrete relation target IDs, build the conflict ledger |
| `reembed-entries.py` | Re-embed after batch content edits (Ollama must be running) |
| `auto-merge-duplicates.py` | Detect and merge near-duplicate memories (dry-run by default) |
| `compact-if-needed.py` | Trigger bounded maintenance through the API |

## Verification

Run the release gate before and after any change:

```bash
PYTHONPATH=$PWD:/path/to/hermes-agent /path/to/hermes-agent/venv/bin/python -m pytest -q tests
node --check static/app.js && node --check static/graph.js
```

The retrieval retention gate copies the live database read-only into a
disposable `/tmp` fixture and never benchmarks by writing the real database:

```bash
cd "$HOME/github/hermes-lancedb-memory-suite"
PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" OLLAMA_HOST=http://127.0.0.1:11434 \
  "$HOME/.hermes/hermes-agent/venv/bin/python" \
  audit/repro/benchmark-retrieval.py --prepare-fixture --replace-fixture \
  --output /tmp/bench-retrieval.json
```

The final split requires exact abstention and preservation of every frozen
critical correction. Recall and MRR are corpus-dependent, so they guard against
collapse toward the frozen lexical control rather than deciding promotion
themselves; frozen targets live in `audit/repro/retrieval-baseline-reference.json`.

---
name: lancedb-memory-system
description: "LanceDB memory architecture, deployment, and how to change it."
version: 6.0.0
triggers:
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
~/.hermes/plugins/lancedb-suite/                      <- Canonical user plugin (survives Hermes updates)
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

The visualizer runs as the `lancedb-viz` Docker container on `127.0.0.1:7777`,
started by the Hermes Hub Compose service at
`~/github/hermes-hub/services/lancedb-viz/docker-compose.yaml` (`lancedb-viz:local`
image, external `hub-services` network, `restart: unless-stopped`). A disabled
systemd unit on port 7778 is a recovery fallback, not an equivalent path.

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

A 240-character cap was measured and rejected: it discarded 56.5% of valid
existing facts, while 1000 characters retained 95.3%. Do not lower
`MAX_FACT_CHARS` without re-running that measurement.

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
the full `Domain:Subject`, more tokens, or explicit lexical mode.

## Changing the system

Where the knobs are:

| Want to change | Edit |
|---|---|
| Field limits, categories, relation types | `plugin/memory_contract.py` constants |
| Abstention thresholds, embedder, dimension | `plugin/store.py` constants |
| Maintenance triggers, retention, cooldown | `server/maintenance.py` constants or the `LANCEDB_*` env vars |
| HTTP surface, UI behaviour | `server/server.py`, `static/` |
| Visualizer look | `static/style.css`, `static/app.js`, `static/graph.js` |

Maintenance constants and their env overrides: `MAINTENANCE_MAX_VERSIONS` (64),
`MAINTENANCE_MAX_FRAGMENTS` (64), `MAINTENANCE_MAX_ORPHAN_INDEX_DIRECTORIES` (4),
plus `LANCEDB_MAINTENANCE_MIN_RECLAIMABLE_BYTES`,
`LANCEDB_MAINTENANCE_MIN_STORAGE_RATIO`,
`LANCEDB_MAINTENANCE_COOLDOWN_SECONDS`, `LANCEDB_ROUTINE_RETENTION_SECONDS`.
`LANCEDB_PATH` overrides the database path for the server.

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
   `provider 'lancedb' initialize failed: name 'LanceDBStore' is not defined`.

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
cp ~/.hermes/plugins/lancedb-suite/{store.py,memory_contract.py,__init__.py,plugin.yaml} \
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

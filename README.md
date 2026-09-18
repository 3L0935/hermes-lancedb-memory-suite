# Hermes LanceDB Memory Suite

Local-first vector memory for [Hermes Agent](https://github.com/nousresearch/hermes-agent).
LanceDB-only storage with Ollama embeddings, entity extraction, hybrid search
(BM25 + vector), and an interactive web dashboard. No server, no daemon, no API
key, and no data leaving your machine.

## What this is

A complete memory system for Hermes Agent that persists across sessions, not a
visualizer with a plugin attached:

- **Plugin** (`plugin/`) - LanceDB memory provider for Hermes. 8 MCP tools:
  search, add, update, delete, get, list, graph, conflicts. Auto entity extraction,
  auto-tagging, quality decay, target-ID relations, local query routing, one-hop
  graph recall, and deterministic contradiction detection.
- **Visualizer** (`server/` + `static/`) - lightweight web UI, normally run in
  Docker on port 7777, with an optional systemd fallback on port 7778. 11 pages:
  Dashboard, Memories, Timeline, Tags, Duplicates, Conflicts, Review, Embeddings,
  Clusters, Stale, and Graph. [See the screenshots](#visualizer).
- **Scripts** (`scripts/`) - relation migration, contradiction backfill,
  re-embedding, duplicate consolidation, deployment, and verification.
- **Audit harness** (`audit/repro/`) - the retrieval retention gate, write-cost
  measurement, and frozen calibration baselines behind the numbers below.
- **Docs** (`docs/`) - setup guide, the versioned write contract, measured
  investigations, and the skill references.

## Why LanceDB

- **Embedded, not served.** LanceDB is a Rust columnar format on disk. There is no
  server and no daemon to supervise, and the database is a directory you can copy.
- **Native hybrid search.** BM25 (Tantivy FTS) and vector cosine fused through
  Reciprocal Rank Fusion, in the engine. No separate search stack to run.
- **Fully local embeddings.** `nomic-embed-text` (768-dim) through Ollama. No API
  call, no key, no corpus leaving the machine.
- **Versioned writes.** Every write is an MVCC version, and superseded versions can
  be compacted on demand. One measured reclaim went from 157.8 MB to 2.9 MB.
- **Engine-specific calibration is treated as real.** BM25 scores change between
  LanceDB releases, so the abstention threshold is pinned to `lancedb==0.34.0` and
  every search response reports the running engine beside the calibrated one.

## Numbers on a real database

Measured on the author's live instance (not a fixture):

| Metric | Value |
|--------|-------|
| Memories | 543 |
| Entities | 1,708 |
| Typed edges | 234 |
| Semantic edges | 1,880 |
| Tables | 4 (`memories`, `memory_edges`, `memory_conflicts`, `memory_conflicts_archive`) |
| Footprint | 20 MB total, 13.2 MB of table data |

Retrieval gate, frozen final split: recall@5 0.76 (lexical control) to 0.85
(corrected hybrid), no-answer false results 1.0 to 0.0. Both are documented with
the exact command that produced them in `audit/repro/`.

## Architecture

```
~/.hermes/plugins/lancedb-suite/                       <- Canonical plugin copy
~/.hermes/hermes-agent/plugins/memory/lancedb-suite/   <- Runtime compatibility copy
~/.hermes/lancedb/                               <- Persistent LanceDB tables
~/.hermes/lancedb-viz/                           <- Deployed visualizer files
```

The canonical user plugin survives Hermes Agent source-tree updates. The runtime
copy is retained for bundled-first discovery compatibility. Both are synced from
this repository's `plugin/` directory.

**Data flow:**

```
Agent → MCP tools (lancedb_search, lancedb_add, lancedb_update, ...)
  → MemoryWrite / MemoryPatch
    → memory_contract.py (normalize, validate, fingerprint, render)
      → LanceDBStore preflight (idempotency, subject match, conflicts)
        → Ollama /api/embed (nomic-embed-text, 768-dim)
          → Entity extraction + auto-tagging
            → LanceDB table (memories + vectors)
            → BM25 index (Tantivy FTS)
            → memory_edges table (typed relations with concrete target IDs)
            → memory_conflicts table (non-destructive key=value contradictions)
```

Search defaults to a deterministic local router: exact strings and identifiers
use lexical retrieval, relationship questions use hybrid seeds plus one-hop edge
traversal, and all other queries use hybrid BM25/vector retrieval. It adds no
model call and no service.

## Requirements

- **Hermes Agent** installed and running
- **Ollama** running on localhost:11434
- **nomic-embed-text** model pulled: `ollama pull nomic-embed-text`
- **Python 3.11+** with `lancedb==0.34.0`, `pyarrow`, `httpx`, `numpy`
- **Docker** for the primary visualizer deployment
- **systemd user services** only for the optional fallback deployment

## Quick Start

### 1. Install Ollama + embedding model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull nomic-embed-text
```

### 2. Install the plugin

Two paths. Pick one, not both.

**Via Hermes (recommended).** The plugin lives in the `plugin/` subdirectory of this
repository, which the installer understands:

```bash
hermes plugins install 3L0935/hermes-lancedb-memory-suite/plugin
hermes plugins enable lancedb
```

This installs into `~/.hermes/plugins/lancedb-suite/` and pins the revision. Once the entry is
accepted into the Hermes plugin catalog you can use the bare name instead:

```bash
hermes plugins install lancedb-memory-suite   # after catalog admission
```

**From a checkout (development).** Bundled-first discovery means the gateway reads
`~/.hermes/hermes-agent/plugins/memory/lancedb-suite/`, not the user plugin directory, so a local
checkout has to be synced to both copies:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
for D in "$HERMES_HOME/plugins/lancedb-suite" \
         "$HERMES_HOME/hermes-agent/plugins/memory/lancedb-suite"; do
  mkdir -p "$D"
  cp plugin/{store.py,memory_contract.py,__init__.py,plugin.yaml} "$D/"
done
```

`scripts/deploy-local.sh` does exactly this, plus the visualizer files.

**Dependencies.** The manifest declares them (`python_dependencies` in `plugin.yaml`), so a
catalog install surfaces them. A checkout install needs them explicitly:

```bash
"$HOME/.hermes/hermes-agent/venv/bin/pip" install -r requirements.txt
```

Do not replace this with an unpinned `pip install lancedb`. The BM25 score is produced by
the retrieval engine, not by this repository alone: identical rows and code scored
differently under LanceDB 0.34.0 and 0.38.0. The abstention threshold is calibrated for the
pinned `lancedb==0.34.0` engine.

### 3. Configure Hermes

In `~/.hermes/config.yaml`:

```yaml
memory:
  provider: lancedb
  lancedb:
    db_path: ~/.hermes/lancedb
    embed_model: nomic-embed-text
```

`db_path` and `embed_model` are the only settings you normally need. One more exists:

| Key | Default | Meaning |
|-----|---------|---------|
| `auto_prefetch` | `false` | Push automatically recalled memories into every turn. See below. |

**Why `auto_prefetch` defaults to off.** Hermes calls `prefetch()` before every non-trivial
turn and splices the returned text onto the user message as relevant memory
(`agent/turn_context.py`, `compose_user_api_content`). Measured against a real database, that
push is not trustworthy: the query "and where is the backup" returned an unrelated
skills-backup entry and a Skate3 patch, the latter through the vector path at cosine 0.2957,
under the 0.3 floor. An explicit `lancedb_search` has no such failure mode — the query is
deliberate, the results are visible before you use them, and a bad hit is discarded instead
of injected. Set `auto_prefetch: true` to restore the push.

Turning it off also removes the embedding round-trip from the turn's critical path, so a
hung Ollama no longer costs the 30s HTTP timeout before the model sees your message.

Restart Hermes:

```bash
systemctl --user restart hermes-gateway
hermes tools | grep lancedb
```

### 4. Deploy the visualizer

The canonical UI is the `lancedb-viz` Docker container on `127.0.0.1:7777`,
managed by the Hermes Hub Compose service. Start or recreate it from the Hub,
then synchronize this repository. Because `deploy-local.sh` rewrites both
plugin copies but does not restart `hermes-gateway`, restart the gateway after
deploying plugin changes; otherwise its Python process keeps the previously
imported module while the container serves the new code.

```bash
cd "$HOME/github/hermes-hub/services/lancedb-viz"
docker compose up -d

cd "$HOME/github/hermes-lancedb-memory-suite"
./scripts/deploy-local.sh --dry-run
./scripts/deploy-local.sh
systemctl --user restart hermes-gateway
./scripts/verify-setup.sh
```

Dashboard: `http://localhost:7777`

The disabled systemd user unit on port 7778 is a recovery fallback, not an
equivalent deployment path. Use `./scripts/deploy-local.sh --systemd-fallback`
only when deliberately recovering without the canonical Docker service.

For the full setup guide, see [docs/setup.md](docs/setup.md).

## MCP Tools

The plugin exposes 8 tools to the Hermes agent:

| Tool | Purpose |
|------|---------|
| `lancedb_search` | Auto-routed lexical, hybrid, or one-hop graph recall. Supports explicit `mode` and `relation_depth`. |
| `lancedb_add` | Create a validated memory from structured fields. Exact retries are idempotent; same-subject writes are preflighted before embedding. |
| `lancedb_update` | Replace a memory by ID from its complete structured form, re-embed changed content, and echo the replaced content. |
| `lancedb_delete` | Delete a memory and clean incoming and outgoing typed edges. |
| `lancedb_get` | Get a single memory by ID with all fields. |
| `lancedb_list` | Exact listing with category, tier, and quality filters. |
| `lancedb_graph` | Export the memory graph. |
| `lancedb_conflicts` | List deterministic same-subject `key=value` contradictions. |

### Strict write contract

`lancedb_add` and `lancedb_update` accept structured data. Callers must not
construct or parse the persisted string themselves.

```json
{
  "domain": "Project",
  "subject": "ApiGateway",
  "facts": ["transport=SSE", "port=7777", "reconnect uses exponential backoff"],
  "tier": 2,
  "category": "tech",
  "relations": [{"type": "depends", "target_id": "memory-id"}]
}
```

The five fields `domain`, `subject`, `facts`, `tier`, and `category` are
required. Facts may be dense `key=value` claims or concise single-sentence
prose. Current limits are 12 facts, 1,000 characters per fact, and 2,000
characters across all facts. These limits were selected against existing data;
the measured distribution and rationale are documented in
`plugin/memory_contract.py`.

- **Tier 1**: correction, safety rule, critical command, or recurring failure.
- **Tier 2**: reusable configuration, architecture, endpoint, or workflow fact.
- **Tier 3**: durable background context.
- **Categories**: `project`, `tech`, `fact`, `correction`, `user_pref`,
  `decision`, `insight`, `reference`, `pattern`, and `question`.
- **Relations**: `part_of`, `depends`, `requires`, `runs_on`, `connects_to`,
  `uses`, `extends`, `supersedes`, `invalidates`, and `contradicts`.

The contract normalizes labels and whitespace, rejects embedded tier or
relation markers, validates categories and relations, and renders the internal
canonical representation:

```
Domain:Subject fact one. fact two. [Tier=N]
```

Typed relations are persisted separately. The legacy `::relations::` syntax is
accepted only by fenced import and migration adapters, not by agent tools.

#### Non-destructive add behavior

`write_mode` defaults to `create`:

| Preflight result | Tool result |
|------------------|-------------|
| Exact canonical fingerprint already exists | `success: true`, `status: idempotent`, existing `memory_id` |
| Same subject, no conflicting explicit claim | `success: false`, `status: update_suggested`, existing ID and content |
| Same subject, conflicting `key=value` claim | Blocked with `error.code: conflicting_claims` |
| New subject | `success: true`, `status: created` |

`write_mode="upsert_subject"` is explicit and only succeeds when exactly one
non-conflicting same-subject memory exists. Its response includes
`replaced_content`. For intentional claim corrections, use `lancedb_update`
with a known memory ID. No write path silently merges or overwrites content.

Generic subjects are accepted with an `overly_broad_subject` warning because
they increase false conflict matches. Embedding failures are retryable errors
and never create a zero-vector row.

Validation failures use a stable machine-readable shape:

```json
{
  "success": false,
  "error": {
    "code": "invalid_category",
    "field": "category",
    "message": "category is not supported",
    "received": "note",
    "expected": ["project", "tech", "fact"]
  },
  "retryable": false
}
```

The `expected` value may contain the full supported set; it is abbreviated in
this example. Successful and preflight responses echo `canonical_content`.

See [docs/skills/memory-writing.md](docs/skills/memory-writing.md) for the full writing guide.

### Quality score

Retrieval exposes separate read-only signals instead of rewriting quality when
a result or detail view is opened:

| Field | Meaning |
|-------|---------|
| `persisted_quality` | Durable utility value stored with the memory |
| `freshness` | Read-time `0.985^days since accessed_at`, never persisted by a read |
| `protected` | True for tier-1 critical rules and corrections |
| `quality` | Effective utility; tier-1 entries have a 0.5 floor |

Age alone does not make a memory false or low-quality. Detail, list, lexical,
and hybrid reads use the same decoration path and do not create MVCC versions.

### Recall is a tool call, not a push

The plugin does not inject memories into your turns by default. Recall happens when the agent
calls `lancedb_search`, with a deliberate query, and reads the results before using them. See
`auto_prefetch` under [Configure Hermes](#3-configure-hermes) for the measurement behind that
default and how to re-enable the push.

The built-in `MEMORY.md` / `USER.md` blocks are unaffected and stay injected on every session.
They are a separate path in the core (`agent/system_prompt.py`, `_memory_parts`) and are not
bounded by any provider setting. They are also far too small to hold a technical corpus of this
kind: 545 entries at ~371 characters average is ~202k characters, against a 2,500-character
budget. The two systems are additive: the built-in carries durable identity and conventions,
this plugin carries the technical record.

### Mutation concurrency

All public store mutations share a process-local re-entrant lock keyed by the
resolved database path. The lock covers structured preflight and commit, so two
store instances in one process cannot both create the same canonical memory.
`_fresh()` keeps MVCC handles current, but neither mechanism provides atomicity
between separate processes. Cross-process writers must be externally
serialized; this limitation is explicit rather than inferred from MVCC.

### Auto-tagging

Tags are auto-extracted from entities at write time:

| Score | Type | Examples |
|-------|------|----------|
| 3 | Known tech keywords | hermes, docker, python, godot, steam |
| 2 | CamelCase projects / ALL CAPS acronyms | BloodReaver, VIGIL |
| 1 | Other non-blocked words | kept if space available |

A noise filter (~140+ words) blocks generic FR/EN words.

### Hybrid search

LanceDB native hybrid retrieval combines BM25 (Tantivy FTS) and vector cosine
through Reciprocal Rank Fusion. It abstains with no result when every candidate
is below both calibrated evidence thresholds:

- `SEARCH_MIN_BM25_SCORE = 12.80`
- `SEARCH_MAX_COSINE_DISTANCE = 0.30`

A candidate is retained when its BM25 score is at least 12.80 or its cosine
distance is at most 0.30. The BM25 threshold is specific to
`lancedb==0.34.0`; changing the engine invalidates that calibration even when
the code and corpus are identical. Exact and relationship-shaped queries are
routed locally without an LLM call.

## Visualizer

A lightweight web UI with 11 pages, served on `127.0.0.1:7777`.

> The screenshots below come from `docs/screenshots/`, captured against a **synthetic
> fixture** of 18 invented memories. No screenshot contains data from a real database.

### Dashboard

Counters, category and tier breakdown, tags, most-accessed memories.

![Dashboard](docs/screenshots/01-dashboard.png)

### Graph

The graph opens on the **whole corpus** and draws embedding-similarity edges only.
Clicking a memory does not reload it: the sidebar opens over the graph and the node and
its neighbours glow. `Escape`, the close button, or a click on empty canvas clears it.
The API and slider both default to cosine similarity `0.8` (`th` is a supported alias
for `threshold`). Every memory remains visible, including rows without a usable vector.
Semantic edges use mutual top-8 neighbours and a fixed 1,200-edge cap; the UI reports
this policy. Declared relations and category/entity hub connectors are separate and
are never charged to the semantic cap.

| Whole corpus | Memory selected |
|---|---|
| ![Graph, whole corpus](docs/screenshots/11-graph-full.png) | ![Graph with a memory selected](docs/screenshots/12-graph-selected.png) |

Grouping is switchable at any time: category hubs, entity hubs, or the raw embedding view.

![Graph grouped by category hubs](docs/screenshots/13-graph-hubs.png)

### Embeddings

On-demand UMAP projection of at most the first 500 stored rows (invalid vectors are
omitted), next to a read-only local health inspection. The page reports whether that
selection was truncated; opening the root page does not precompute the projection.

![Embeddings and local health](docs/screenshots/08-embedding-health.png)

### Browse and review

| Memories | Timeline | Tags |
|---|---|---|
| ![Memories](docs/screenshots/02-memories.png) | ![Timeline](docs/screenshots/03-timeline.png) | ![Tags](docs/screenshots/04-tags.png) |

| Duplicates | Conflicts |
|---|---|
| ![Duplicates](docs/screenshots/05-duplicates.png) | ![Conflicts](docs/screenshots/06-conflicts.png) |

| Review | Clusters | Stale |
|---|---|---|
| ![Review](docs/screenshots/07-review.png) | ![Clusters](docs/screenshots/09-clusters.png) | ![Stale](docs/screenshots/10-stale.png) |

### Pages

| Page | What it shows |
|---|---|
| **Dashboard** | totals, category breakdown, tier distribution, most-accessed memories |
| **Memories** | paginated list (20/page), filters by category, type, tag, quality, date, and search |
| **Timeline** | memories grouped by day |
| **Tags** | every tag with its count, plus rename, merge and delete |
| **Duplicates** | near-duplicate groups by cosine similarity, threshold slider |
| **Conflicts** | same-subject claims that contradict each other, open and resolved |
| **Review** | bounded read-only consistency inbox: format drift, contradictions, broken references |
| **Embeddings** | UMAP 2D projection of all vectors + local health diagnostics |
| **Clusters** | deterministic semantic clusters plus largest-group concentration, coverage, isolated-memory count, and a low-discrimination warning |
| **Stale** | old and low-quality memories, the cleanup candidates |
| **Graph** | whole corpus by default, embedding links, optional typed-relation overlay, hub grouping, freshness halo, tier filter |

### API endpoints

The standard-library server handles requests concurrently. Each request owns its
LanceDB handle so table-version refreshes are never shared between threads. Graph
and projection calculations share two admission slots; a third request waits at
most 250 ms, then receives `503` with `Retry-After: 1`. This keeps small routes
responsive without claiming to reduce CPU-bound Python work.

JSON and compressible static responses negotiate gzip, including explicit
`gzip;q=0` refusal, and send `Vary: Accept-Encoding` plus the encoded body's real
length. Memory JSON remains `Cache-Control: no-store`. Mutable static URLs use a
representation-specific content `ETag` with `Cache-Control: private, no-cache`, so
unchanged revalidation returns 304; they are deliberately not marked `immutable`.

```
GET  /api/graph                         — whole corpus; `threshold`/`th` default 0.8, mutual top-8 semantic edges capped at 1,200
GET  /api/stats                         — raw statistics
GET  /api/typed-edges                   — all persisted typed edges
GET  /api/search                        — routed search (`q`, `top_k`, `diagnostics`)
GET  /api/export                        — download the memory export
GET  /api/memory?id=:id                 — legacy memory detail lookup
GET  /api/memories                      — paginated and filtered list
GET  /api/memories/:id                  — memory detail lookup
GET  /api/tags                          — all tags with counts
GET  /api/timeline                      — memories grouped by day
GET  /api/duplicates                    — duplicate groups (`threshold`)
GET  /api/projection                    — UMAP projection (`n_neighbors`, `min_dist`)
GET  /api/health                        — read-only storage and dependency diagnostics
GET  /api/maintenance/compact/plan      — read-only thresholds, compaction, and backup plan
GET  /api/clusters                      — stable semantic clusters + diagnostics (`threshold`, `min_size`)
GET  /api/stale                         — stale memories (`days`, `quality_max`)
GET  /api/conflicts                     — contradiction ledger (`status`, `memory_id`, `limit`)
GET  /api/review                        — bounded read-only review inbox
GET  /api/dashboard                     — enriched dashboard statistics
GET  /api/refresh                       — discard the request store and invalidate derived caches
POST /api/delete                        — legacy delete (`memory_id` in JSON body)
POST /api/update                        — legacy memory update
POST /api/update_entities               — legacy entity update
POST /api/import                        — import memories
POST /api/conflicts/:id/resolve          — resolve a conflict with an audit note
POST /api/memories/bulk-delete           — delete a bounded ID set
POST /api/memories/bulk-tag              — add or remove tags in bulk
POST /api/memories/bulk-type             — set memory type in bulk
POST /api/tags/rename                    — rename one tag
POST /api/tags/delete                    — remove one tag
POST /api/tags/merge                     — merge tags
POST /api/maintenance/compact            — confirmed backup, compaction, and verification
POST /api/memories/:id                   — contract-validated memory update
POST /api/memories/:id/access            — increment access count
POST /api/memories/:id/preview           — validate and render an update without writing
```

### Search diagnostics

Opt in with `diagnostics=1` when investigating routing or abstention:

```bash
curl -fsS --get \
  --data-urlencode 'q=memory retrieval policy' \
  --data 'top_k=5' \
  --data 'diagnostics=1' \
  http://127.0.0.1:7777/api/search | python3 -m json.tool
```

The response reports the selected route, abstention reason, timing, result
match type, cosine distance and BM25 score when available. Its `diagnostics`
object includes the active thresholds plus `calibrated_engine`,
`running_engine`, and `matches`. A false `matches` value means the BM25 score
is not comparable with the calibration and must be investigated before using
the result as evidence.

### Maintenance and compaction

Store construction and every read path leave the existing FTS index untouched.
The outer writer batch owns one FTS refresh when the `memories` version changes;
raw batch scripts must use `with store.write_batch():` so they share that
boundary and the inter-process writer lock. LanceDB 0.34.0 searches unindexed
fragments during an active batch, and the regression suite verifies recall both
before and after the refresh.

The read-only health response separates database bytes, managed backup bytes,
their total footprint, estimated active bytes, and estimated reclaimable bytes.
It also reports retained version count separately from the cumulative current
version. The plan recommends maintenance above 64 retained versions, 64
fragments, or 4 unreferenced current-version index directories. It also uses a
byte trigger when estimated reclaimable space reaches 16 MiB and the database is
at least four times the estimated active storage.

The starting values can be changed with
`LANCEDB_MAINTENANCE_MIN_RECLAIMABLE_BYTES`,
`LANCEDB_MAINTENANCE_MIN_STORAGE_RATIO`,
`LANCEDB_MAINTENANCE_COOLDOWN_SECONDS`, and
`LANCEDB_ROUTINE_RETENTION_SECONDS`.

`lancedb-viz-maintenance.timer` checks hourly. Routine maintenance keeps 24 hours
of history and observes a one-hour cooldown; an unchanged table-version snapshot
does not create another backup. Zero-age cleanup is reserved for an explicit
reclaim after old readers have drained:

```bash
curl -fsS 'http://127.0.0.1:7777/api/maintenance/compact/plan?mode=reclaim' \
  | python3 -m json.tool
curl -fsS -X POST \
  -H 'Content-Type: application/json' \
  --data '{"confirmed":true,"mode":"reclaim"}' \
  http://127.0.0.1:7777/api/maintenance/compact \
  | python3 -m json.tool
```

Apply budgets space for the complete backup and compaction scratch, creates an
atomic `lancedb-pre-compact-*` copy, verifies its tables, row counts, IDs and
sample content, then rotates older managed backups. It retains the two newest
verified recovery copies. Compaction verifies row counts, readable versions,
active index directories and the memories FTS index. Manual index-directory
deletion is skipped because a directory absent from the current version can be
needed by a retained or tagged snapshot. A failed response includes
`failed_step`; once backup creation succeeds it also includes `backup_created`,
which remains available for recovery. Cooperating store writers share an
advisory lock with maintenance; pause any raw writer that does not use
`store.write_batch()`.

## Scripts

### deploy-local.sh

Synchronizes the repository into the canonical user plugin, the runtime
compatibility copy, and the visualizer deployment. It does not restart the
Hermes gateway: Python does not reload the already imported plugin module, so
always restart `hermes-gateway` after plugin changes. By default the script
restarts and verifies the existing Docker container on port 7777. The
`--systemd-fallback` flag targets only the disabled recovery service on 7778.
Deployment also installs and enables the hourly maintenance timer; it targets
the primary Docker endpoint on port 7777.

```bash
./scripts/deploy-local.sh --dry-run
./scripts/deploy-local.sh
systemctl --user restart hermes-gateway
./scripts/deploy-local.sh --dry-run --systemd-fallback
```

### docker-run.sh

This is a standalone development helper, not the Hub deployment path. It owns
the same `lancedb-viz` container name and port, so do not run it while the
Hub-managed service is active. `--rebuild` rebuilds `lancedb-viz:local` before
starting the standalone container.

### verify-setup.sh

Run the post-deployment smoke checks against the canonical Docker service:

```bash
./scripts/verify-setup.sh
```

For deliberate fallback recovery on port 7778, set
`LANCEDB_VIZ_MODE=systemd`.

### Retrieval retention gate

The deterministic harness copies the live database read-only into a disposable
fixture below `/tmp`; it never benchmarks by writing the real database. Run it
with the pinned host environment and record the engine with every measurement:

```bash
cd "$HOME/github/hermes-lancedb-memory-suite"
PYTHONPATH="$PWD:$HOME/.hermes/hermes-agent" \
OLLAMA_HOST=http://127.0.0.1:11434 \
"$HOME/.hermes/hermes-agent/venv/bin/python" \
  audit/repro/benchmark-retrieval.py \
  --prepare-fixture \
  --replace-fixture \
  --output /tmp/bench-retrieval.json
```

The final-split gate requires exact abstention and preservation of every
`old_critical_correction`. MRR and recall are corpus-dependent, so they only
guard against collapse toward the frozen lexical control; the same-run hybrid
delta is reported but never decides promotion. Frozen targets, measured corpus
noise, cost, and calibrated engine live in
`audit/repro/retrieval-baseline-reference.json`; each result records engine,
table versions, and row counts under `corpus_versions`.

### audit-memory-format.py

Reads `id`, `content`, and `category` directly from an existing LanceDB table
and emits a JSON drift report. It never repairs rows. Use `--fail-on-drift` to
return exit code 1 when invalid or non-canonical content is found.

```bash
python3 scripts/audit-memory-format.py --db-path /path/to/lancedb --pretty
python3 scripts/audit-memory-format.py --db-path /path/to/lancedb --fail-on-drift
```

### migrate-memory-format.py

Reads projected fields and classifies rows as `canonical`, `auto_fix`,
`quarantine`, or `warning_only`. It is always a dry-run: `auto_fix` means a
change is mechanically unambiguous, not that the script applies it. There is
no write or apply mode.

```bash
python3 scripts/migrate-memory-format.py \
  --source-db /path/to/lancedb \
  --dry-run --pretty
```

Review the JSON report before planning a separately approved migration on a
verified copy. This hardening does not promote `domain`, `subject`, or `facts`
to LanceDB columns; the current table schema is unchanged. Any future schema
migration must use create, copy, verify, and drop—not `rename_table()`.

### migrate_graph_retention.py

Backfills concrete target IDs on legacy relations, removes source-orphaned edges on apply, and builds the deterministic conflict ledger. Dry-run is the default. Back up the database before `--apply`.

```bash
~/.hermes/hermes-agent/venv/bin/python scripts/migrate_graph_retention.py
~/.hermes/hermes-agent/venv/bin/python scripts/migrate_graph_retention.py --apply
```

### reembed-entries.py

Re-embed all entries after batch content modifications (>5 entries).
Ollama must be running.

```bash
~/.hermes/hermes-agent/venv/bin/python3 scripts/reembed-entries.py [--dry-run]
```

### auto-merge-duplicates.py

Detect and merge duplicate memories by vector similarity.

```bash
# Dry run (default)
python3 scripts/auto-merge-duplicates.py --threshold 0.92

# Actually merge
python3 scripts/auto-merge-duplicates.py --threshold 0.92 --apply
```

Behavior:
- Detects groups with cosine similarity >= threshold (default 0.92)
- Picks the best entry (quality > access_count > content length > oldest)
- Merges tags from all duplicates into the keeper
- Deletes duplicates with exact content match (safe auto-merge)
- Skips duplicates with different content (needs manual review)

## Testing

Hermes supplies the provider base classes, so tests run against a Hermes
checkout. Replace `/path/to/hermes-agent` with that checkout:

```bash
PYTHONPATH=$PWD:/path/to/hermes-agent \
  /path/to/hermes-agent/venv/bin/python -m pytest -q tests
node --check static/app.js
node --check static/graph.js
```

268 tests pass on a clean checkout. Ollama, the visualizer container, and a live
database are not required: tests build temporary synthetic data. The suite covers
the write contract, store links and retention, write budgets, auto-merge,
migration scripts, the FTS and disk-growth invariants, server security, and the
frontend modules through `node --test`.

Two invariants worth knowing before you change retrieval:

- Tests must not read source files as text to assert on their shape, and they
  must not freeze values that are expected to change.
- The retrieval gate judges frozen absolute targets, because a same-run baseline
  can be flipped by adding a single memory with no code change.

## Docker deployment

The production definition is
`~/github/hermes-hub/services/lancedb-viz/docker-compose.yaml`. It runs image
`lancedb-viz:local` as container `lancedb-viz`, publishes only
`127.0.0.1:7777`, and joins the existing external `hub-services` network. It
bind-mounts `server.py`, `maintenance.py`, and `static/` from
`~/.hermes/lancedb-viz/`, the Hermes Agent tree read-only, and the LanceDB data
plus `~/.hermes/backups/` read-write.

```bash
cd "$HOME/github/hermes-hub/services/lancedb-viz"
docker compose up -d
docker compose ps
```

The repository-level Compose file and `scripts/docker-run.sh` support local
development; they are not the production service definition. The systemd unit
on `127.0.0.1:7778` is disabled and reserved for recovery.

## License

MIT — see [LICENSE](LICENSE).

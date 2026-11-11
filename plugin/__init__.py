"""LanceDB memory plugin — MemoryProvider for local vector memory.

Provides semantic search, graph export, and explicit memory write
through the MemoryProvider interface.
Auto-sync is intentionally disabled — only explicit memory writes.
Auto-injection of memories (prefetch) is off by default: recall is a tool call,
not a push. What IS pushed by default is a one-line write reminder every few
turns (``write_nudge_every``), with no store read and no memory content.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider, RecallStatus
from tools.registry import tool_error

from .memory_contract import (
    MemoryContractError,
    MemoryPatch,
    MemoryWrite,
    parse_content_parts,
)
from .store import (  # noqa: F401 — used in initialize()
    LanceDBStore,
    MemoryEmbeddingError,
    STORE_IMPLEMENTATION_SHA256,
)

logger = logging.getLogger(__name__)


def _write_error(error: Exception) -> str:
    if isinstance(error, (MemoryContractError, MemoryEmbeddingError)):
        return json.dumps({
            "success": False,
            "error": error.to_dict(),
            "retryable": bool(getattr(error, "retryable", False)),
        }, ensure_ascii=False, default=str)
    return tool_error(str(error))


def _normalized_write_fields(
    memory: MemoryWrite,
    *,
    include_relations: bool = True,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "domain": memory.domain,
        "subject": memory.subject,
        "facts": list(memory.facts),
        "tier": memory.tier,
        "category": memory.category,
        "write_mode": memory.write_mode,
    }
    if include_relations:
        fields["relations"] = [relation.to_dict() for relation in memory.relations]
    return fields

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_SEARCH_SCHEMA = {
    "name": "lancedb_search",
    "description": (
        "Recall what was learned in earlier sessions. Call it BEFORE answering about a "
        "named project, the user's setup or preferences, a past decision or bug, and "
        "before writing a memory (to find the subject to update or link to). "
        "Returns compact results: key, the first facts, typed relations, memory id; "
        "use lancedb_get(id) for the full memory. An empty result means nothing "
        "passed the calibrated evidence gate, not an error."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query — what you want to recall.",
            },
            "top_k": {
                "type": "integer",
                "description": "Max results (default: 6, max: 50).",
            },
            "category": {
                "type": "string",
                "enum": ["", "project", "tech", "fact", "correction", "user_pref", "decision", "insight", "reference", "pattern", "question"],
                "description": "Optional category filter.",
            },
            "view": {
                "type": "string",
                "enum": ["compact", "full"],
                "description": (
                    "compact (default): key, first facts, relations, id. full: every stored "
                    "field and scoring detail, for debugging retrieval."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["auto", "hybrid", "lexical", "graph"],
                "description": "Local retrieval strategy. auto uses deterministic rules; graph adds bounded one-hop typed relations.",
            },
            "relation_depth": {
                "type": "integer",
                "enum": [0, 1],
                "description": "Typed-relation traversal depth. Only 0 or 1 is supported to keep recall bounded.",
            },
            "neighbor_budget": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5,
                "description": "Maximum declared one-hop neighbors (default and hard max: 5).",
            },
            "diagnostics": {
                "type": "boolean",
                "description": "Include query-free retrieval diagnostics and retain a bounded local history entry.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

_CATEGORY_VALUES = [
    "project", "tech", "fact", "correction", "user_pref", "decision",
    "insight", "reference", "pattern", "question",
]
_RELATION_VALUES = [
    "part_of", "depends", "requires", "runs_on", "connects_to", "uses",
    "extends", "supersedes", "invalidates", "contradicts",
]
_RELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": _RELATION_VALUES},
        "target_id": {"type": "string", "minLength": 1},
        "target": {"type": "string", "minLength": 1},
    },
    "required": ["type"],
    "oneOf": [
        {"required": ["target_id"], "not": {"required": ["target"]}},
        {"required": ["target"], "not": {"required": ["target_id"]}},
    ],
    "additionalProperties": False,
}
_STRUCTURED_WRITE_PROPERTIES = {
    "domain": {"type": "string", "minLength": 1, "maxLength": 32},
    "subject": {"type": "string", "minLength": 1, "maxLength": 80},
    "facts": {
        "type": "array",
        "minItems": 1,
        "maxItems": 12,
        "items": {"type": "string", "minLength": 1, "maxLength": 1000},
        "description": (
            "One claim per fact, about 200 chars max, key=value preferred "
            "(port=7777, cause=fd leak). 3-6 facts; more means the subject is too broad: "
            "split it into linked memories."
        ),
    },
    "tier": {
        "type": "integer",
        "enum": [1, 2, 3],
        "description": "1 = forgetting it breaks things, 2 = recurring project knowledge, 3 = background.",
    },
    "category": {"type": "string", "enum": _CATEGORY_VALUES},
    "relations": {
        "type": "array",
        "maxItems": 20,
        "items": _RELATION_SCHEMA,
        "description": (
            "Typed links from this memory to others (target_id from lancedb_search). "
            "part_of: piece of a bigger project/component. supersedes: replaces the target. "
            "invalidates: proves it wrong. contradicts: disagrees, unsettled. extends: adds "
            "detail. depends/requires: needs the target. runs_on: hosted by it. uses: calls it "
            "as a tool/library. connects_to: network/API link only, not 'related'."
        ),
    },
}
_STRUCTURED_REQUIRED = ["domain", "subject", "facts", "tier", "category"]

_ADD_SCHEMA = {
    "name": "lancedb_add",
    "description": (
        "Store one durable memory. Use it without being asked when the user corrects you, "
        "a decision is made, or a path/port/config/fix is found after effort. Load skill "
        "lancedb-suite:memory-writing before your first write of a session. Search first: "
        "update an existing subject instead of duplicating it, and link related memories "
        "with relations. create is non-destructive (update_suggested for an existing "
        "subject); upsert_subject replaces it. Read the returned warnings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            **_STRUCTURED_WRITE_PROPERTIES,
            "write_mode": {
                "type": "string",
                "enum": ["create", "upsert_subject"],
                "default": "create",
            },
        },
        "required": _STRUCTURED_REQUIRED,
        "additionalProperties": False,
    },
}

_GRAPH_SCHEMA = {
    "name": "lancedb_graph",
    "description": (
        "Export the full memory knowledge graph as nodes + edges. "
        "Nodes represent individual memories with content, category, quality (0-1), "
        "tier (1/2/3), tags, entities, and freshness (accessed_at). "
        "Edges represent entity-based links (2+ shared entities). "
        "Use this to understand how memories are connected."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

_UPDATE_SCHEMA = {
    "name": "lancedb_update",
    "description": (
        "Replace an existing memory by ID using a complete structured canonical form. "
        "The response echoes both canonical and replaced content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "minLength": 1,
                "description": "Existing memory ID from search or get.",
            },
            **_STRUCTURED_WRITE_PROPERTIES,
            "tags": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string"},
                "description": "New tags (replaces existing tags entirely).",
            },
            "quality": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "type": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
            },
        },
        "required": ["memory_id", *_STRUCTURED_REQUIRED],
        "additionalProperties": False,
    },
}

_DELETE_SCHEMA = {
    "name": "lancedb_delete",
    "description": "Delete a memory by its ID. Rebuilds remaining links after deletion.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "ID of the memory to delete. Get IDs from lancedb_search, lancedb_graph, or the web UI.",
            },
        },
        "required": ["memory_id"],
    },
}

_GET_SCHEMA = {
    "name": "lancedb_get",
    "description": (
        "Get the full memory behind a search or list result, by its ID, with all fields: "
        "content, category, quality, tier, tags, entities, "
        "relations (typed links to other memories), links (cosine-similar linked IDs), "
        "access_count, created_at, updated_at. "
        "Use after lancedb_search or lancedb_list to get full detail on a specific entry."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {
                "type": "string",
                "description": "ID of the memory to retrieve.",
            },
        },
        "required": ["memory_id"],
    },
}

_LIST_SCHEMA = {
    "name": "lancedb_list",
    "description": (
        "List all memories with optional filters. "
        "Exact listing, no approximate search — use when you need every entry "
        "of a category, or when search is too broad/narrow. "
        "Returns compact rows (key, first facts, relations, id) unless view=full."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["", "project", "tech", "fact", "correction", "user_pref", "decision", "insight", "reference", "pattern", "question"],
                "description": "Optional category filter (empty = all).",
            },
            "quality_min": {
                "type": "number",
                "description": "Minimum quality score (0-1). Default 0. Filters out noise.",
            },
            "tier": {
                "type": "string",
                "enum": ["", "1", "2", "3"],
                "description": "Optional tier filter (1=critical, 2=useful, 3=contextual).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 50, max: 200).",
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset (default: 0).",
            },
            "view": {
                "type": "string",
                "enum": ["compact", "full"],
                "description": "compact (default) or full stored rows.",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Compact rendering of memories returned to the agent
# ---------------------------------------------------------------------------

# A full row is ~1600 chars, more than half of it scoring and bookkeeping the
# model never uses. The compact view keeps what answers a question and what
# lets the agent dig further (the id, the relations).
COMPACT_MAX_FACTS = 4
COMPACT_MAX_FACT_CHARS = 220
COMPACT_MAX_RELATIONS = 6
DEFAULT_SEARCH_TOP_K = 6


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _age_label(memory: dict) -> str:
    stamp = memory.get("updated_at") or memory.get("created_at")
    try:
        days = (time.time() - float(stamp)) / 86400
    except (TypeError, ValueError):
        return ""
    if days < 1:
        return "today"
    if days < 60:
        return f"{int(days)}d"
    return f"{int(days // 30)}mo"


def compact_memory(memory: dict) -> dict:
    """Render one stored memory for the agent: answer first, bookkeeping out."""
    content = str(memory.get("content") or "")
    try:
        parts = parse_content_parts(content)
        key = f"{parts.domain}:{parts.subject}"
        facts = [fact.strip() for fact in parts.body.split("::fact::") if fact.strip()]
        tier = parts.tier
    except Exception:
        key, facts, tier = "", [content], memory.get("tier")
    out: dict[str, Any] = {"id": memory.get("id")}
    if key:
        out["key"] = key
    out["category"] = memory.get("category")
    if tier not in (None, "", "none"):
        out["tier"] = tier
    out["facts"] = [_clip(fact, COMPACT_MAX_FACT_CHARS) for fact in facts[:COMPACT_MAX_FACTS]]
    if len(facts) > COMPACT_MAX_FACTS:
        out["more_facts"] = len(facts) - COMPACT_MAX_FACTS
    relations = memory.get("relations") or []
    if isinstance(relations, str):
        try:
            relations = json.loads(relations)
        except ValueError:
            relations = []
    rendered = []
    for relation in relations if isinstance(relations, list) else []:
        if not isinstance(relation, dict):
            continue
        target = relation.get("target") or relation.get("target_id") or "?"
        label = f"{relation.get('type', '?')} -> {_clip(target, 80)}"
        if relation.get("target_id") and relation.get("target"):
            label += f" ({relation['target_id']})"
        rendered.append(label)
    if rendered:
        out["relations"] = rendered[:COMPACT_MAX_RELATIONS]
        if len(rendered) > COMPACT_MAX_RELATIONS:
            out["more_relations"] = len(rendered) - COMPACT_MAX_RELATIONS
    if memory.get("retrieval_source") == "relation":
        out["via"] = (
            f"{memory.get('relation_type')} ({memory.get('relation_direction')}) "
            f"of {memory.get('relation_seed_id')}"
        )
        if memory.get("relation_status") in {"replacement", "contradiction"}:
            out["status"] = memory["relation_status"]
    elif memory.get("match_type"):
        out["match"] = memory["match_type"]
    age = _age_label(memory)
    if age:
        out["age"] = age
    return out

_CONFLICTS_SCHEMA = {
    "name": "lancedb_conflicts",
    "description": (
        "List or resolve conservative local contradiction records. Conflicts are created only "
        "when memories with the same Domain:Subject key contain different explicit "
        "key=value claims. Detection is deterministic and never mutates either memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "resolve"],
                "default": "list",
                "description": "List conflicts or resolve one with an audit note.",
            },
            "status": {
                "type": "string",
                "enum": ["", "open", "resolved", "archived"],
                "description": "Filter by state/storage; empty returns the hot registry.",
            },
            "memory_id": {
                "type": "string",
                "description": "Optional memory ID to inspect.",
            },
            "limit": {
                "type": "integer",
                "default": 100,
                "description": "Maximum rows to return (1-500).",
            },
            "conflict_id": {
                "type": "string",
                "description": "Conflict ID required by the resolve action.",
            },
            "resolution_note": {
                "type": "string",
                "description": "Required audit note explaining a resolution.",
            },
            "resolved_by": {
                "type": "string",
                "description": "Actor recorded in the audit trail (default: user).",
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Daily memory review (optional LLM cron job)
# ---------------------------------------------------------------------------

# Writing during a conversation competes with the task at hand. A daily pass over
# the last 24 hours catches what was learned but never stored, and tidies what
# turned out wrong. Offered at setup (memory.lancedb-suite.daily_review) and as
# the automation blueprint lancedb-suite:daily-review.
DAILY_REVIEW_NAME = "LanceDB daily memory review"
DAILY_REVIEW_PROMPT = (
    "You are the daily memory reviewer. Curate the LanceDB memory from the user's "
    "conversations of the last 24 hours.\n"
    "1. Load skill lancedb-suite:memory-writing.\n"
    "2. Read the last 24 hours of conversations (session_search). For each durable fact "
    "that is not stored yet, ask: will this matter in two weeks? Durable means a "
    "correction, a decision and its reason, a path/port/command/config or fix found after "
    "effort, a lasting preference. Task progress, one-off edits and chatter are not.\n"
    "3. lancedb_search before writing. Update the existing memory (lancedb_update) rather "
    "than adding a near-duplicate; otherwise add a small memory linked with typed "
    "relations.\n"
    "4. Tidy what you met on the way: a memory proven wrong gets a newer memory that "
    "supersedes or invalidates it; open conflicts you can settle get lancedb_conflicts "
    "resolve with a note. Deleting memories: {deletes}.\n"
    "5. Never edit MEMORY.md or USER.md and never store secrets or credentials; mention "
    "suggestions for them in the report instead.\n"
    "Report in at most 5 lines (Added / Updated / Superseded / Deleted, one subject per "
    "line). If nothing needed doing, respond with exactly [SILENT]."
)
DAILY_REVIEW_DELETE_POLICIES = {
    "report": "never delete; list deletion candidates in the report",
    "allow": "allowed for exact duplicates and memories made obsolete by a newer one",
}


def _parse_hhmm(value: Any, default: str = "13:00") -> tuple[int, int]:
    try:
        hour, minute = str(value or default).strip().split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
        if 0 <= hour_i <= 23 and 0 <= minute_i <= 59:
            return hour_i, minute_i
    except ValueError:
        pass
    hour, minute = default.split(":")
    return int(hour), int(minute)


def ensure_daily_review_job(time_of_day: Any = "13:00", deletes: str = "report") -> dict:
    """Create the daily review cron job once; an existing job with the same name is kept."""
    from cron.jobs import create_job, list_jobs

    for job in list_jobs(include_disabled=True):
        if job.get("name") == DAILY_REVIEW_NAME:
            return {"created": False, "job_id": job.get("id"), "reason": "already exists"}
    hour, minute = _parse_hhmm(time_of_day)
    policy = DAILY_REVIEW_DELETE_POLICIES.get(deletes, DAILY_REVIEW_DELETE_POLICIES["report"])
    job = create_job(
        prompt=DAILY_REVIEW_PROMPT.format(deletes=policy),
        schedule=f"{minute} {hour} * * *",
        name=DAILY_REVIEW_NAME,
        deliver="local",
        skills=["lancedb-suite:memory-writing"],
    )
    return {"created": True, "job_id": job.get("id"), "schedule": f"{minute} {hour} * * *"}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------


class LanceDBMemoryProvider(MemoryProvider):
    """Vector memory provider using LanceDB + Ollama embeddings."""

    def __init__(self, config: dict | None = None):
        self._config = config or {}
        self._store = None
        self._db_path = None
        # Last turn number seen by on_turn_start (drives the write reminder).
        self._turn_number = 0
        # Count injected by the LAST prefetch, consumed by recall_status().
        self._last_recall_count = 0

    @property
    def name(self) -> str:
        return "lancedb-suite"

    def is_available(self) -> bool:
        try:
            import lancedb  # noqa: F401
            return True
        except ImportError:
            return False

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "db_path",
                "description": "Path to LanceDB database directory",
                "default": "~/.hermes/lancedb",
            },
            {
                "key": "embed_model",
                "description": "Ollama embedding model name",
                "default": "nomic-embed-text",
            },
            {
                "key": "write_nudge_every",
                "description": (
                    "Every N user turns, add one line reminding the agent to store durable "
                    "facts it just learned (no memory content, no store read). 0 disables it."
                ),
                "default": 8,
            },
            {
                "key": "daily_review",
                "description": (
                    "Create a daily LLM job that reviews the last 24 hours of conversations, "
                    "stores what was learned but not written, and tidies outdated memories. "
                    "Costs one agent run per day; silent when there is nothing to do. "
                    "Manage it later with /cron (or /blueprint lancedb-suite:daily-review)."
                ),
                "type": "boolean",
                "default": False,
            },
            {
                "key": "daily_review_time",
                "description": "Local time of the daily review (HH:MM), used when daily_review is on.",
                "default": "13:00",
            },
            {
                "key": "embed_keep_alive",
                "description": (
                    "How long Ollama keeps the embedding model loaded after a call (e.g. 30s, "
                    "10m, -1). Each search embeds the query: a longer value avoids reloading the "
                    "model (0.8-2 s) at the cost of ~350 MB of VRAM or RAM held while idle."
                ),
                "default": "30s",
            },
            {
                "key": "auto_prefetch",
                "description": (
                    "Push automatically recalled memories into every turn. Off by default: "
                    "an off-topic match gets spliced onto the user message as pertinent "
                    "context. Use lancedb_search instead."
                ),
                "default": False,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        from pathlib import Path
        import yaml
        config_path = Path(hermes_home) / "config.yaml"
        existing = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8-sig") as f:
                existing = yaml.safe_load(f) or {}
        existing.setdefault("memory", {}).setdefault("lancedb-suite", {})
        for k, v in values.items():
            existing["memory"]["lancedb-suite"][k] = v
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(existing, f, default_flow_style=False)
        if str(values.get("daily_review", "")).strip().lower() in {"1", "true", "yes", "on"}:
            try:
                result = ensure_daily_review_job(values.get("daily_review_time") or "13:00")
                if result.get("created"):
                    print(f"  Daily memory review scheduled ({result['schedule']}, job {result['job_id']}).")
                else:
                    print(f"  Daily memory review already scheduled (job {result.get('job_id')}).")
            except Exception as error:  # never fail the setup over the optional job
                logger.warning("Daily memory review not scheduled: %s", error)
                print("  Could not schedule the daily review automatically; add it with "
                      "/blueprint lancedb-suite:daily-review.")

    def initialize(self, session_id: str, **kwargs) -> None:
        from pathlib import Path
        from hermes_constants import get_hermes_home

        hermes_home = get_hermes_home()
        db_path = self._config.get("db_path", str(hermes_home / "lancedb"))
        # Resolve ~ and $HERMES_HOME
        db_path = db_path.replace("$HERMES_HOME", str(hermes_home))
        db_path = db_path.replace("${HERMES_HOME}", str(hermes_home))
        db_path = str(Path(db_path).expanduser())

        self._db_path = db_path

        # Set embed model env var if configured
        import os as _os
        if embed_model := self._config.get("embed_model"):
            _os.environ["LANCE_EMBED_MODEL"] = embed_model
        if keep_alive := self._config.get("embed_keep_alive"):
            _os.environ["LANCE_EMBED_KEEP_ALIVE"] = str(keep_alive)

        self._store = LanceDBStore(db_path)
        logger.info(
            "LanceDB store loaded sha256=%s module=%s",
            STORE_IMPLEMENTATION_SHA256,
            Path(__file__).resolve(),
        )

    def system_prompt_block(self) -> str:
        """Rules, not a tool list: the tool schemas already describe each tool.

        Static per session (cache-friendly). Says what belongs here, when to search,
        when to write without being asked, and which skill to load first.
        """
        if not self._store:
            return ""
        try:
            count = self._store.count()
        except Exception:
            count = 0
        stored = f"{count} memories stored." if count else "Empty store: start recording durable facts."
        return (
            "# LanceDB Memory\n"
            f"Active. {stored} LanceDB holds the technical record across sessions: "
            "projects, setup and config, decisions, corrections, preferences. "
            "MEMORY.md/USER.md keep identity only.\n"
            "- SEARCH (lancedb_search) before answering about a named project, the user's "
            "setup, a past decision or bug, and before asking the user something they may "
            "already have told you.\n"
            "- WRITE (lancedb_add / lancedb_update) without being asked when: the user corrects "
            "you; a decision is made; a path, port, command, config or fix is found after "
            "effort; the user states a lasting preference or says \"remember\". Write when it "
            "is established, not at the end of the session.\n"
            "- Before your first write of a session, load skill lancedb-suite:memory-writing. "
            "Small memories (one claim per fact, key=value), linked with typed relations; "
            "search first and update instead of duplicating.\n"
        )

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self._turn_number = int(turn_number or 0)

    def _write_nudge(self) -> str:
        """One line every N turns so writing does not depend on the model remembering to.

        No store read, no memory content, no recall indicator.
        """
        try:
            every = int(self._config.get("write_nudge_every", 8))
        except (TypeError, ValueError):
            every = 8
        if every <= 0 or self._turn_number <= 0 or self._turn_number % every:
            return ""
        return (
            "## LanceDB Memory\n"
            "Memory check: if the recent exchange settled a durable fact (a correction, a "
            "decision, a config or fix that took effort), store it now with lancedb_add or "
            "lancedb_update. Otherwise ignore this line."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Auto-injection is intentionally disabled — recall is a tool call, not a push.

        The core calls this before every non-trivial turn and splices the returned text
        onto the user message as "relevant memory". Measured on a real database, that push
        is not reliable: an ambiguous query ("and where is the backup") returned an
        unrelated skills-backup entry and a Skate3 patch through the vector path, below
        the lax cosine floor. Presented as pertinent context, that misleads more than it
        helps.

        Explicit lancedb_search has no such failure mode: the query is deliberate, the
        results are visible, and an off-topic hit is discarded instead of injected. Set
        memory.lancedb-suite.auto_prefetch: true in config.yaml to restore the push.
        """
        self._last_recall_count = 0
        if not self._config.get("auto_prefetch"):
            return self._write_nudge()
        if not self._store or not query:
            return ""
        try:
            results = self._store.search(query, top_k=5)
            if not results:
                return ""
            lines = []
            for r in results:
                cat = r.get("category", "?")
                score = r.get("score", 0)
                quality = r.get("quality", 0.5) or 0.5
                content = r.get("content", "")
                rels = r.get("relations", [])
                rel_str = ""
                if rels:
                    rel_str = " →rels: " + ", ".join(f"{rel.get('type','?')}={rel.get('target','?')}" for rel in rels)
                lines.append(f"[{cat}] ({score:.2f} q={quality:.2f}) {content}{rel_str}")
            self._last_recall_count = len(results)
            return "## LanceDB Memory\n" + "\n".join(lines)
        except Exception as e:
            logger.debug("LanceDB prefetch failed: %s", e)
            return ""

    def recall_status(self) -> RecallStatus | None:
        """Deterministic recall indicator. Reflects only the LAST prefetch: the count is
        reset at the top of prefetch(), so a turn that injected nothing stays silent."""
        if not self._last_recall_count:
            return None
        return RecallStatus(provider_label="LanceDB", count=self._last_recall_count)

    def identity_signature(self) -> Dict[str, Any]:
        """Busts a cached gateway agent when the writer identity changes.

        Called on an UNINITIALIZED instance on every inbound message, so this only
        reads the deployed manifest version from disk and never touches the store.
        """
        version = ""
        try:
            import yaml
            from pathlib import Path
            manifest = Path(__file__).resolve().parent / "plugin.yaml"
            if manifest.is_file():
                version = str((yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("version") or "")
        except Exception as e:
            logger.debug("identity_signature version read failed: %s", e)
        return {"lancedb_version": version}

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: list | None = None) -> None:
        """Auto-sync is intentionally disabled. Only explicit lancedb_add."""
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            _SEARCH_SCHEMA, _ADD_SCHEMA, _GRAPH_SCHEMA, _DELETE_SCHEMA,
            _UPDATE_SCHEMA, _GET_SCHEMA, _LIST_SCHEMA, _CONFLICTS_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "lancedb_search":
            return self._handle_search(args)
        elif tool_name == "lancedb_add":
            return self._handle_add(args)
        elif tool_name == "lancedb_graph":
            return self._handle_graph(args)
        elif tool_name == "lancedb_delete":
            return self._handle_delete(args)
        elif tool_name == "lancedb_update":
            return self._handle_update(args)
        elif tool_name == "lancedb_get":
            return self._handle_get(args)
        elif tool_name == "lancedb_list":
            return self._handle_list(args)
        elif tool_name == "lancedb_conflicts":
            return self._handle_conflicts(args)
        return tool_error(f"Unknown tool: {tool_name}")

    # -------------------------------------------------------------------
    # Tool handlers
    # -------------------------------------------------------------------

    def _handle_search(self, args: dict) -> str:
        query = args.get("query", "")
        top_k = min(max(int(args.get("top_k", DEFAULT_SEARCH_TOP_K)), 1), 50)
        category = args.get("category", "") or None
        mode = args.get("mode", "auto")
        relation_depth = 1 if int(args.get("relation_depth", 1)) > 0 else 0
        neighbor_budget = min(max(int(args.get("neighbor_budget", 5)), 0), 5)
        diagnostics = bool(args.get("diagnostics", False))
        try:
            outcome = self._store.search_with_diagnostics(
                query,
                top_k=top_k,
                category=category,
                mode=mode,
                relation_depth=relation_depth,
                neighbor_budget=neighbor_budget,
                diagnostics=diagnostics,
            )
            if args.get("view", "compact") != "full":
                outcome["results"] = [compact_memory(row) for row in outcome.get("results", [])]
                outcome.pop("timings", None)
                if not outcome["results"]:
                    outcome["hint"] = (
                        "Nothing passed the evidence gate. Try other words (names, paths, "
                        "error text), a category filter, or mode=lexical for exact text."
                    )
            return json.dumps(outcome, ensure_ascii=False, default=str)
        except Exception as e:
            return _write_error(e)

    def _handle_add(self, args: dict) -> str:
        try:
            memory = MemoryWrite.from_mapping(args)
            result = self._store.add_memory(memory)
            memory_id = result.get("memory_id", "")
            try:
                potential_conflicts = self._store.get_conflicts(
                    status="open", memory_id=memory_id, limit=20
                )
            except Exception as error:
                potential_conflicts = []
                warning = {
                    "code": "conflict_listing_failed",
                    "field": "conflicts",
                    "message": "memory result is valid but conflict listing failed",
                    "received": str(error),
                    "expected": "readable conflict registry",
                }
                logger.warning("Conflict listing failed after memory write: %s", error)
                result.setdefault("warnings", []).append(warning)
            result["potential_conflicts"] = potential_conflicts
            result["conflicts"] = potential_conflicts
            result["normalized_fields"] = _normalized_write_fields(memory)
            result["message"] = f"Memory {result['status']}: {result['canonical_content'][:80]}..."
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as error:
            return _write_error(error)

    def _handle_graph(self, args: dict) -> str:
        try:
            graph = self._store.graph()
            return json.dumps({
                "nodes": graph["nodes"],
                "edges": graph["edges"],
                "node_count": len(graph["nodes"]),
                "edge_count": len(graph["edges"]),
            }, ensure_ascii=False, default=str)
        except Exception as e:
            return tool_error(str(e))

    def _handle_delete(self, args: dict) -> str:
        memory_id = args.get("memory_id", "")
        if not memory_id:
            return tool_error("memory_id is required")
        try:
            ok = self._store.delete(memory_id)
            if ok:
                return json.dumps({
                    "success": True,
                    "message": f"Memory {memory_id} deleted.",
                })
            return json.dumps({
                "success": False,
                "message": f"Memory {memory_id} not found.",
            })
        except Exception as e:
            return tool_error(str(e))

    def _handle_update(self, args: dict) -> str:
        try:
            replacement = MemoryWrite.from_mapping({
                field: args[field]
                for field in (*_STRUCTURED_REQUIRED, "relations")
                if field in args
            })
            patch_data = {
                "memory_id": args.get("memory_id"),
                "domain": replacement.domain,
                "subject": replacement.subject,
                "facts": list(replacement.facts),
                "tier": replacement.tier,
                "category": replacement.category,
            }
            if "relations" in args:
                patch_data["relations"] = [
                    relation.to_dict() for relation in replacement.relations
                ]
            for field in ("tags", "quality", "type"):
                if field in args:
                    patch_data[field] = args[field]
            result = self._store.update_memory(MemoryPatch.from_mapping(patch_data))
            try:
                conflicts = self._store.get_conflicts(
                    status="open", memory_id=result.get("memory_id", ""), limit=20
                )
            except Exception as error:
                conflicts = []
                result.setdefault("warnings", []).append({
                    "code": "conflict_listing_failed",
                    "field": "conflicts",
                    "message": "memory result is valid but conflict listing failed",
                    "received": str(error),
                    "expected": "readable conflict registry",
                })
            result["conflicts"] = conflicts
            result["normalized_fields"] = _normalized_write_fields(
                replacement,
                include_relations="relations" in args,
            )
            result["updated_fields"] = [
                field for field in args if field != "memory_id"
            ]
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as error:
            return _write_error(error)

    def _handle_get(self, args: dict) -> str:
        memory_id = args.get("memory_id", "")
        if not memory_id:
            return tool_error("memory_id is required")
        try:
            mem = self._store.get_by_id(memory_id)
            if not mem:
                return json.dumps({"success": False, "message": f"Memory {memory_id} not found."})
            # Parse tier from content
            content = mem.get("content", "")
            tier = "none"
            for t in ["1", "2", "3"]:
                if f"[Tier={t}]" in content:
                    tier = t
                    break
            mem["tier"] = tier
            return json.dumps(mem, ensure_ascii=False, default=str)
        except Exception as e:
            return tool_error(str(e))

    def _handle_list(self, args: dict) -> str:
        try:
            category = args.get("category", "") or None
            quality_min = float(args.get("quality_min", 0))
            tier_filter = args.get("tier", "") or None
            limit = min(int(args.get("limit", 50)), 200)
            offset = int(args.get("offset", 0))

            all_mems = self._store.get_all()

            # Filter + enrich
            results = []
            for m in all_mems:
                # Category filter
                if category and m.get("category") != category:
                    continue
                # Quality filter
                if quality_min > 0 and (m.get("quality") or 0) < quality_min:
                    continue
                # Tier filter
                if tier_filter:
                    content = m.get("content", "")
                    if f"[Tier={tier_filter}]" not in content:
                        continue
                # Enrich with tier
                content = m.get("content", "")
                tier = "none"
                for t in ["1", "2", "3"]:
                    if f"[Tier={t}]" in content:
                        tier = t
                        break
                m["tier"] = tier
                results.append(m)

            total = len(results)
            page = results[offset:offset + limit]
            if args.get("view", "compact") != "full":
                page = [compact_memory(row) for row in page]

            return json.dumps({
                "total": total,
                "count": len(page),
                "offset": offset,
                "limit": limit,
                "results": page,
            }, ensure_ascii=False, default=str)
        except Exception as e:
            return tool_error(str(e))

    def _handle_conflicts(self, args: dict) -> str:
        try:
            action = args.get("action", "list")
            if action == "resolve":
                conflict_id = str(args.get("conflict_id") or "").strip()
                resolution_note = str(args.get("resolution_note") or "").strip()
                resolved_by = str(args.get("resolved_by") or "user").strip()
                if not conflict_id:
                    return tool_error("conflict_id is required for resolve")
                if not resolution_note:
                    return tool_error("resolution_note is required for resolve")
                if not resolved_by:
                    return tool_error("resolved_by is required for resolve")
                ok = self._store.resolve_conflict(
                    conflict_id,
                    resolution_note=resolution_note,
                    resolved_by=resolved_by,
                )
                if not ok:
                    return tool_error("Conflict not found or already resolved")
                return json.dumps({
                    "success": True,
                    "conflict_id": conflict_id,
                    "resolution_note": resolution_note,
                    "resolved_by": resolved_by,
                }, ensure_ascii=False)
            if action != "list":
                return tool_error("action must be list or resolve")
            status = args.get("status", "")
            if status not in {"", "open", "resolved", "archived"}:
                return tool_error("status must be empty, open, resolved, or archived")
            conflicts = self._store.get_conflicts(
                status=status,
                memory_id=args.get("memory_id", ""),
                limit=min(max(int(args.get("limit", 100)), 1), 500),
            )
            return json.dumps({
                "count": len(conflicts),
                "conflicts": conflicts,
            }, ensure_ascii=False, default=str)
        except Exception as e:
            return tool_error(str(e))

    def shutdown(self) -> None:
        self._store = None


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register the LanceDB memory provider with Hermes plugin system."""
    from hermes_cli.config import cfg_get
    import yaml

    config = {}
    try:
        from hermes_constants import get_hermes_home
        config_path = get_hermes_home() / "config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8-sig") as f:
                all_config = yaml.safe_load(f) or {}
            config = cfg_get(all_config, "memory", "lancedb-suite", default={}) or {}
    except Exception:
        pass

    provider = LanceDBMemoryProvider(config=config)
    ctx.register_memory_provider(provider)

    # Ship the skills with the plugin: a catalog install copies plugin/ only, so a
    # skill kept elsewhere in the repo never reached the agent. They resolve as
    # lancedb-suite:<name> and load on demand (one listing line each).
    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        skills_dir = Path(__file__).resolve().parent / "skills"
        for name, description in (
            ("memory-writing", "How to write LanceDB memories: when, shape, typed relations. Load before the first lancedb_add/lancedb_update of a session."),
            ("lancedb-memory-system", "How the LanceDB memory plugin works: retrieval, contract, maintenance, deployment."),
        ):
            skill_file = skills_dir / name / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                register_skill(name, skill_file, description)
            except Exception as error:  # older Hermes, name clash: never block the provider
                logger.debug("LanceDB skill %s not registered: %s", name, error)

    register_blueprint = getattr(ctx, "register_automation_blueprint", None)
    if callable(register_blueprint):
        try:
            register_blueprint(
                "daily-review",
                title=DAILY_REVIEW_NAME,
                description=(
                    "Once a day, store what the last 24 hours of conversations taught but "
                    "nobody wrote down, and supersede memories that turned out wrong. "
                    "Silent when there is nothing to do."
                ),
                schedule_template="{minute} {hour} * * *",
                prompt_template=DAILY_REVIEW_PROMPT,
                category="memory",
                slots=[
                    {"name": "time", "type": "time", "label": "What time?", "default": "13:00",
                     "help": "24h local time"},
                    {"name": "deletes", "type": "enum", "label": "May it delete memories?",
                     "default": DAILY_REVIEW_DELETE_POLICIES["report"],
                     "options": tuple(DAILY_REVIEW_DELETE_POLICIES.values()), "strict": True},
                    {"name": "deliver", "type": "enum", "label": "Where to send the report?",
                     "default": "local", "options": ("local", "origin", "telegram", "discord", "email"),
                     "strict": False},
                ],
                deliver_default="local",
                skills=["lancedb-suite:memory-writing"],
                tags=["memory", "lancedb", "daily"],
            )
        except Exception as error:
            logger.debug("LanceDB daily-review blueprint not registered: %s", error)

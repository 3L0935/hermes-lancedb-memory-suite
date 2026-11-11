"""Hub structure: group memories by topic and find the hub each one belongs to.

A hub is the memory a topic hangs on (a project, a component): other memories
point at it with ``part_of``, and a family of projects can point at a parent hub.
The topic of a memory is the first word of its subject (``Gateway`` in
``Hermes:Gateway-Restart``), compared across domains, because one project spans
several (``Projet:LanceDB_viz``, ``Correction:LanceDB_index``, ``Tech:LanceDB_FTS``).

Shared entities are deliberately NOT used: inferring links from them produced
mostly wrong edges on a real store (58 of 61 dead). Subjects are chosen by the
writer, so they are the signal the writer already gave.

Nothing here writes. ``suggest_hubs`` feeds ``suggested_relations`` in write
responses; ``propose_attachments`` builds a reviewable plan for
``scripts/propose-hubs.py``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable

_PREFIX_RE = re.compile(r"^([^\s:]+):(\S+)")

# Topic words too generic to group on: they would tie unrelated memories together.
GENERIC_TOPICS = frozenset({
    "", "a", "the", "le", "la", "les", "un", "une", "de", "du",
    "test", "tests", "config", "configuration", "setup", "misc", "notes", "note",
    "general", "global", "system", "service", "services", "plugin", "plugins",
    "tool", "tools", "skill", "skills", "bug", "bugs", "fix", "fixes", "erreurs",
    "errors", "error", "memory", "memoire", "memories", "project", "projet",
    "projects", "projets", "decision", "pattern", "reference", "question",
    "correction", "pref", "user", "data", "api", "doc", "docs", "todo", "hub",
    "overview", "new", "old", "v1", "v2",
})

# Domains that name a kind of memory rather than a project. For any other domain
# (BO3:..., Bodycam:..., FeatureUpvote:...) the domain itself is the topic.
GENERIC_DOMAINS = frozenset({
    "tech", "projet", "project", "projets", "projects", "correction", "corrections",
    "decision", "decisions", "pref", "prefs", "preference", "user", "system", "reference",
    "references", "insight", "insights", "pattern", "patterns", "question", "questions",
    "fact", "facts", "deadend", "config", "test", "tests", "security", "gaming", "llm",
    "hermes", "tool", "tools", "misc", "note", "notes", "elo",
})

# A memory needs at least this many incoming relations to count as an existing hub.
MIN_HUB_INDEGREE = 2


def key_of(content: str) -> tuple[str, str]:
    match = _PREFIX_RE.match(content or "")
    return (match.group(1), match.group(2)) if match else ("", "")


def topic_of(subject: str, domain: str = "") -> str:
    """The project a memory belongs to: its domain when the domain names one
    (``BO3:t7-source`` -> ``bo3``), else the first word of its subject."""
    domain_word = (domain or "").lower()
    if domain_word and domain_word not in GENERIC_DOMAINS and domain_word not in GENERIC_TOPICS:
        return re.split(r"[-_.]", domain_word, maxsplit=1)[0] if len(domain_word) >= 3 else domain_word
    word = re.split(r"[-_.]", subject or "", maxsplit=1)[0].lower()
    if word in GENERIC_TOPICS or len(word) < 3 or word.isdigit():
        return ""
    if word == (domain or "").lower():
        # Hermes:Hermes-Config says nothing more than its domain.
        rest = re.split(r"[-_.]", subject, maxsplit=1)
        return topic_of(rest[1], "") if len(rest) > 1 else ""
    return word


def _edges_by_memory(edges: Iterable[dict]) -> tuple[Counter, Counter, dict]:
    indegree, outdegree, part_of = Counter(), Counter(), Counter()
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        source = str(edge.get("from") or edge.get("source_id") or "")
        target = str(edge.get("to") or edge.get("target_id") or "")
        if not source or not target:
            continue
        outdegree[source] += 1
        indegree[target] += 1
        if str(edge.get("relation_type") or edge.get("type") or "") == "part_of":
            part_of[source] += 1
        outgoing[source].append(edge)
    return indegree, outdegree, part_of


def _describe(rows: Iterable[dict], edges: Iterable[dict]) -> list[dict]:
    indegree, outdegree, part_of = _edges_by_memory(edges)
    described = []
    for row in rows:
        domain, subject = key_of(str(row.get("content") or ""))
        memory_id = str(row.get("id") or "")
        described.append({
            "id": memory_id,
            "key": f"{domain}:{subject}" if domain else "",
            "domain": domain,
            "subject": subject,
            "topic": topic_of(subject, domain),
            "category": row.get("category"),
            "indegree": indegree[memory_id],
            "outdegree": outdegree[memory_id],
            "part_of": part_of[memory_id],
        })
    return described


_HUB_WORDS = ("hub", "overview", "index", "project", "projet")
_PROJECT_DOMAINS = {"projet", "project", "projets", "projects"}


def _is_project_hub(memory: dict, topic: str) -> bool:
    """Is this memory the hub OF the topic itself (not an operation inside it)?

    ``Projet:Quock``, ``Quock:Overview``, ``Projet:LanceDB_Hub``: yes.
    ``Tech:LanceDB_Nettoyage_14-09_Hub`` (an operation), ``Correction:LanceDB``
    (a correction about it): no.
    """
    domain, subject = memory["domain"].lower(), memory["subject"].lower()
    words = [w for w in re.split(r"[-_.]", subject) if w]
    if domain in _PROJECT_DOMAINS and words and words[0] == topic and all(w in _HUB_WORDS for w in words[1:]):
        return True
    if domain == topic and words and all(w in _HUB_WORDS for w in words):
        return True
    return False


def _hub_score(memory: dict) -> tuple:
    return (memory["indegree"], memory.get("category") == "project", -len(memory["subject"]))


def find_hub(members: list[dict], topic: str = "") -> dict | None:
    """The member that IS the topic's hub, if one exists."""
    candidates = [m for m in members if _is_project_hub(m, topic or m["topic"])]
    return max(candidates, key=_hub_score) if candidates else None


def _display_name(topic: str, members: list[dict]) -> str:
    """The spelling the writer used most for the topic word (Quock, LanceDB, bo3...)."""
    spellings = Counter()
    for m in members:
        for label in (m["domain"], m["subject"]):
            word = re.split(r"[-_.]", label, maxsplit=1)[0]
            if word.lower() == topic:
                spellings[word] += 1
    return spellings.most_common(1)[0][0] if spellings else topic.capitalize()


def new_hub_spec(topic: str, members: list[dict]) -> dict:
    """A short hub memory to create: what the topic is, and its parts."""
    name = _display_name(topic, members)
    parts = sorted({m["subject"] for m in members if m["subject"]})
    listing = ", ".join(parts)
    if len(listing) > 200:
        listing = listing[:199].rsplit(", ", 1)[0] + ", ..."
    return {
        "key": f"Projet:{name}",
        "category": "project",
        "tier": 2,
        "facts": [f"hub={name}, {len(members)} memories", f"parts={listing}"],
    }


def suggest_hubs(domain: str, subject: str, rows: Iterable[dict], edges: Iterable[dict],
                 *, exclude_id: str = "", limit: int = 2) -> list[dict]:
    """Existing hubs a new memory on this subject probably belongs to (never persisted)."""
    topic = topic_of(subject, domain)
    if not topic:
        return []
    described = [m for m in _describe(rows, edges) if m["id"] != exclude_id and m["topic"] == topic]
    hubs = sorted((m for m in described if _is_project_hub(m, topic)), key=_hub_score, reverse=True)
    if not hubs:
        hubs = sorted((m for m in described if m["indegree"] >= MIN_HUB_INDEGREE), key=_hub_score, reverse=True)
    return [
        {"type": "part_of", "target_id": hub["id"], "target": hub["key"],
         "reason": f"hub of topic '{topic}' ({hub['indegree']} memories point to it)"}
        for hub in hubs[:limit]
    ]


def propose_attachments(rows: Iterable[dict], edges: Iterable[dict], *, min_group: int = 3,
                        create_missing: bool = True) -> dict:
    """Plan: give every topic of ``min_group``+ memories its hub, and attach to it the
    members that declare no relation yet.

    The hub is the memory that IS the topic's hub (``Projet:Quock``), or a short new
    one to create (``create_hubs``). Members that already declare a relation are
    left alone when they already declare a ``part_of``: their writer chose a
    parent. Other relations are kept and the ``part_of`` is added. Sub-hubs without a parent (an operation hub
    many memories point at) are loose too, so they end up one level below: a
    two-level star. With ``create_missing=False`` hub-less topics go to
    ``needs_hub`` instead.
    """
    rows, edges = list(rows), list(edges)
    described = _describe(rows, edges)
    groups: dict[str, list[dict]] = defaultdict(list)
    for memory in described:
        if memory["topic"]:
            groups[memory["topic"]].append(memory)
    plan = {"attach": [], "create_hubs": [], "needs_hub": [], "summary": {}}
    attached = 0
    for topic, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) < min_group:
            continue
        # Not yet part of anything. Other relations (connects_to...) are kept as they are.
        loose = [m for m in members if m["part_of"] == 0]
        hub = find_hub(members, topic)
        if hub is None:
            if not loose:
                continue
            if create_missing:
                spec = new_hub_spec(topic, members)
                spec["topic"] = topic
                spec["children"] = [m["id"] for m in loose]
                spec["children_keys"] = [m["key"] for m in loose]
                plan["create_hubs"].append(spec)
                attached += len(loose)
            else:
                plan["needs_hub"].append({
                    "topic": topic,
                    "members": [{"id": m["id"], "key": m["key"]} for m in members],
                })
            continue
        targets = [m for m in loose if m["id"] != hub["id"]]
        if not targets:
            continue
        plan["attach"].append({
            "topic": topic,
            "hub": {"id": hub["id"], "key": hub["key"], "indegree": hub["indegree"]},
            "members": [{"id": m["id"], "key": m["key"]} for m in targets],
        })
        attached += len(targets)
    plan["summary"] = {
        "memories": len(described),
        "without_outgoing_relation": sum(1 for m in described if m["outdegree"] == 0),
        "without_part_of": sum(1 for m in described if m["part_of"] == 0),
        "isolated": sum(1 for m in described if m["outdegree"] == 0 and m["indegree"] == 0),
        "proposed_attachments": attached,
        "topics_with_hub": len(plan["attach"]),
        "hubs_to_create": len(plan["create_hubs"]),
        "topics_needing_a_hub": len(plan["needs_hub"]),
    }
    return plan

#!/usr/bin/env python3
"""Propose, then apply, a hub structure for a LanceDB memory store.

  propose-hubs.py --db-path ~/.hermes/lancedb --out plan.json        # read-only plan
  propose-hubs.py --db-path ~/.hermes/lancedb --apply plan.json      # after review

The plan groups memories by the first word of their subject, across domains, and
proposes ``part_of`` from each member that declares no relation to the topic's
existing hub. Topics without a clear hub are listed under ``needs_hub``.

Review the plan before applying it: remove wrong groups, change a hub, and add
hubs to create under ``create_hubs``:

  {"key": "Projet:Quock", "category": "project", "tier": 2,
   "facts": ["Quock = ...", "parts=repo, agent, android build"],
   "children": ["<id>", ...],          # get part_of -> this hub
   "parent": "<id of a parent hub>"}   # optional: this hub gets part_of -> parent
                                       # or "parent_key": a hub created by the same plan

An ``attach`` group's hub may name a created hub by key: {"hub": {"key": "Projet:Quock"}}.

Applying copies the database to --backup-dir first, then only ADDS relations:
existing relations are kept, content and vectors of attached memories are not
rewritten. Created hubs go through the write contract like any memory.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_plugin(plugin_dir: Path):
    package = types.ModuleType("lancedb_suite_hubs_plugin")
    package.__path__ = [str(plugin_dir)]
    sys.modules[package.__name__] = package
    modules = {}
    for name in ("memory_contract", "hubs", "store"):
        spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", plugin_dir / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def read_store(store):
    rows = store.get_all()
    edges = store.get_typed_edges(include_unresolved=False)
    return rows, edges


def propose(args, mods) -> int:
    store = mods["store"].LanceDBStore(str(Path(args.db_path).expanduser()))
    rows, edges = read_store(store)
    plan = mods["hubs"].propose_attachments(rows, edges, min_group=args.min_group)
    plan["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = plan["summary"]
    print(json.dumps(summary, indent=1))
    for group in plan["attach"]:
        print(f"\n[{group['topic']}] -> {group['hub']['key']} ({group['hub']['indegree']} incoming)")
        for member in group["members"]:
            print(f"    {member['key']}  {member['id']}")
    for hub in plan["create_hubs"]:
        print(f"\n[{hub['topic']}] NEW {hub['key']}  facts={hub['facts']}")
        for key in hub["children_keys"]:
            print(f"    {key}")
    if plan["needs_hub"]:
        print("\nTopics without a clear hub (add them under create_hubs if they deserve one):")
        for group in plan["needs_hub"]:
            print(f"  [{group['topic']}] " + ", ".join(m["key"] for m in group["members"]))
    print(f"\nPlan written to {args.out}. Nothing was changed.")
    return 0


def _merge_part_of(store, contract, memory_id: str, hub_id: str) -> str:
    current = store.get_by_id(memory_id)
    if not current:
        return "missing"
    relations = current.get("relations") or []
    if isinstance(relations, str):
        relations = json.loads(relations or "[]")
    kept = []
    for relation in relations:
        # The contract wants exactly one target form; keep the id when it resolves.
        target_id = relation.get("target_id") or ""
        if target_id and store.get_by_id(target_id):
            kept.append({"type": relation["type"], "target_id": target_id})
        elif relation.get("target"):
            kept.append({"type": relation["type"], "target": relation["target"]})
    if any(r.get("target_id") == hub_id and r["type"] == "part_of" for r in kept):
        return "already"
    kept.append({"type": "part_of", "target_id": hub_id})
    store.update_memory(contract.MemoryPatch.from_mapping({"memory_id": memory_id, "relations": kept}))
    return "linked"


def apply(args, mods) -> int:
    plan = json.loads(Path(args.apply).read_text(encoding="utf-8"))
    db_path = Path(args.db_path).expanduser()
    backup = Path(args.backup_dir).expanduser() / f"lancedb-pre-hubs-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(db_path, backup, symlinks=True)
    print(f"Backup: {backup}")

    contract = mods["memory_contract"]
    store = mods["store"].LanceDBStore(str(db_path))
    report = {"backup": str(backup), "created": [], "linked": 0, "already": 0, "missing": [], "errors": []}

    created: dict[str, str] = {}

    def resolve(ref: dict) -> str:
        """A hub reference: an existing id, or the key of a hub created by this plan."""
        return ref.get("id") or created.get(ref.get("key", ""), "")

    # Parents first: a hub whose parent_key is created by the same plan waits for it.
    pending = list(plan.get("create_hubs", []))
    while pending:
        ready = [h for h in pending if not h.get("parent_key") or h["parent_key"] in created]
        if not ready:
            report["errors"].append({"hubs": [h["key"] for h in pending], "error": "parent_key not found in plan"})
            break
        for hub in ready:
            pending.remove(hub)
            domain, subject = hub["key"].split(":", 1)
            parent = hub.get("parent") or created.get(hub.get("parent_key", ""), "")
            try:
                result = store.add_memory(contract.MemoryWrite.from_mapping({
                    "domain": domain, "subject": subject, "facts": hub["facts"],
                    "tier": hub.get("tier", 2), "category": hub.get("category", "project"),
                    "relations": ([{"type": "part_of", "target_id": parent}] if parent else []),
                }))
            except Exception as error:
                report["errors"].append({"hub": hub["key"], "error": str(error)})
                continue
            hub_id = result.get("memory_id")
            created[hub["key"]] = hub_id
            report["created"].append({"key": hub["key"], "id": hub_id, "status": result.get("status")})
            if hub.get("children"):
                plan.setdefault("attach", []).append({
                    "topic": hub["key"], "hub": {"id": hub_id, "key": hub["key"]},
                    "members": [{"id": child, "key": ""} for child in hub["children"]],
                })

    for group in plan.get("attach", []):
        hub_id = resolve(group["hub"])
        if not hub_id:
            report["errors"].append({"hub": group["hub"], "error": "hub not found"})
            continue
        for member in group["members"]:
            if member["id"] == hub_id:
                continue
            try:
                outcome = _merge_part_of(store, contract, member["id"], hub_id)
            except Exception as error:
                report["errors"].append({"id": member["id"], "hub": group["hub"].get("key"), "error": str(error)})
                continue
            if outcome == "missing":
                report["missing"].append(member["id"])
            else:
                report[outcome] += 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report["errors"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default="~/.hermes/lancedb")
    parser.add_argument("--plugin-dir", default=str(ROOT / "plugin"))
    parser.add_argument("--out", default="hub-plan.json", help="where the proposed plan is written")
    parser.add_argument("--min-group", type=int, default=3, help="smallest topic considered")
    parser.add_argument("--apply", metavar="PLAN", help="apply a reviewed plan (backs up first)")
    parser.add_argument("--backup-dir", default="~/.hermes/backups")
    args = parser.parse_args()
    mods = load_plugin(Path(args.plugin_dir))
    return apply(args, mods) if args.apply else propose(args, mods)


if __name__ == "__main__":
    raise SystemExit(main())

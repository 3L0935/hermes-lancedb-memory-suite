"""1.7: compact retrieval, density warnings, write reminder, bundled skills, maintenance triggers."""

import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin import LanceDBMemoryProvider, compact_memory, register
from plugin.memory_contract import MemoryWrite, contract_warnings
from server.maintenance import (
    MAINTENANCE_MAX_FRAGMENTS,
    MAINTENANCE_MAX_FTS_UNINDEXED_PCT,
    _routine_actions,
)

ROOT = Path(__file__).resolve().parents[1]


def stored(**extra):
    row = {
        "id": "abc-123",
        "content": "Hermes:Gateway_Restart symptom=hangs 90s ::fact:: cause=watcher fd ::fact:: "
                   "fix=close watcher on shutdown [Tier=2]",
        "category": "correction",
        "relations": [{"type": "part_of", "target_id": "hub-1", "target": "Hermes:Gateway"}],
        "updated_at": time.time() - 3 * 86400,
        "match_type": "hybrid",
        "vector": [0.0] * 768,
        "links": ["x", "y"], "entities": ["hermes"], "bm25_score": 13.2, "distance": 0.2,
    }
    row.update(extra)
    return row


class CompactViewTests(unittest.TestCase):
    def test_compact_keeps_the_answer_and_drops_bookkeeping(self):
        out = compact_memory(stored())
        self.assertEqual("Hermes:Gateway_Restart", out["key"])
        self.assertEqual(2, out["tier"])
        self.assertEqual(["symptom=hangs 90s", "cause=watcher fd", "fix=close watcher on shutdown"], out["facts"])
        self.assertEqual(["part_of -> Hermes:Gateway (hub-1)"], out["relations"])
        self.assertEqual("3d", out["age"])
        for noise in ("vector", "links", "entities", "bm25_score", "distance", "content"):
            self.assertNotIn(noise, out)

    def test_compact_caps_facts_and_length(self):
        facts = " ::fact:: ".join(f"k{i}=" + "v" * 400 for i in range(7))
        out = compact_memory(stored(content=f"Project:Alpha {facts} [Tier=3]"))
        self.assertEqual(4, len(out["facts"]))
        self.assertEqual(3, out["more_facts"])
        self.assertTrue(all(len(fact) <= 220 for fact in out["facts"]))

    def test_neighbor_says_how_it_was_reached(self):
        out = compact_memory(stored(retrieval_source="relation", relation_type="supersedes",
                                    relation_direction="incoming", relation_seed_id="seed-9",
                                    relation_status="replacement"))
        self.assertEqual("supersedes (incoming) of seed-9", out["via"])
        self.assertEqual("replacement", out["status"])

    def test_unparseable_content_is_returned_not_dropped(self):
        out = compact_memory(stored(content="legacy text without wrapper"))
        self.assertEqual(["legacy text without wrapper"], out["facts"])


class SearchStore:
    def __init__(self, rows):
        self.rows = rows

    def search_with_diagnostics(self, *_a, **_k):
        return {"success": True, "count": len(self.rows), "results": [dict(r) for r in self.rows],
                "abstained": not self.rows, "timings": {"embedding_ms": 1.0, "search_ms": 2.0}}

    def count(self):
        return len(self.rows)


class SearchToolTests(unittest.TestCase):
    def test_search_is_compact_by_default_and_full_on_request(self):
        provider = LanceDBMemoryProvider()
        provider._store = SearchStore([stored()])
        compact = json.loads(provider.handle_tool_call("lancedb_search", {"query": "restart"}))
        full = json.loads(provider.handle_tool_call("lancedb_search", {"query": "restart", "view": "full"}))
        self.assertIn("key", compact["results"][0])
        self.assertNotIn("timings", compact)
        self.assertIn("content", full["results"][0])

    def test_empty_compact_search_suggests_a_next_step(self):
        provider = LanceDBMemoryProvider()
        provider._store = SearchStore([])
        payload = json.loads(provider.handle_tool_call("lancedb_search", {"query": "nothing"}))
        self.assertIn("hint", payload)


class DensityWarningTests(unittest.TestCase):
    def write(self, **fields):
        base = {"domain": "Project", "subject": "Alpha_Port", "facts": ["port=7777"],
                "tier": 2, "category": "project"}
        base.update(fields)
        return MemoryWrite.from_mapping(base)

    def codes(self, memory):
        return [issue.code for issue in contract_warnings(memory)]

    def test_short_linked_memory_has_no_warning(self):
        self.assertEqual([], self.codes(self.write(
            facts=["a=1", "b=2", "c=3"], relations=[{"type": "part_of", "target": "Project:Alpha"}])))

    def test_long_fact_and_missing_relations_warn_without_rejecting(self):
        codes = self.codes(self.write(facts=["x" * 300, "b=2", "c=3"]))
        self.assertEqual(["long_fact", "no_relations"], codes)

    def test_broad_subject_stays_first(self):
        codes = self.codes(self.write(subject="Project", facts=["a=1", "b=2", "c=3"]))
        self.assertEqual("overly_broad_subject", codes[0])


class WriteReminderTests(unittest.TestCase):
    def test_reminder_every_n_turns_without_touching_the_store(self):
        provider = LanceDBMemoryProvider(config={"write_nudge_every": 3})

        class Exploding:
            def search(self, *a, **k):
                raise AssertionError("the reminder must not read the store")

        provider._store = Exploding()
        seen = []
        for turn in range(1, 7):
            provider.on_turn_start(turn, "msg")
            seen.append(bool(provider.prefetch("msg")))
        self.assertEqual([False, False, True, False, False, True], seen)
        self.assertIsNone(provider.recall_status())

    def test_zero_disables_the_reminder(self):
        provider = LanceDBMemoryProvider(config={"write_nudge_every": 0})
        provider.on_turn_start(8, "msg")
        self.assertEqual("", provider.prefetch("msg"))

    def test_prompt_block_names_the_skill_and_the_triggers(self):
        provider = LanceDBMemoryProvider()
        provider._store = SearchStore([stored()])
        block = provider.system_prompt_block()
        self.assertIn("lancedb-suite:memory-writing", block)
        self.assertIn("without being asked", block)


_FAKE_CONFIG = types.ModuleType("hermes_cli.config")
_FAKE_CONFIG.cfg_get = lambda *_a, default=None, **_k: default


@patch.dict(sys.modules, {"hermes_cli.config": _FAKE_CONFIG})
class RegistrationTests(unittest.TestCase):
    def test_register_ships_skills_and_the_daily_review_blueprint(self):
        calls = {"skills": [], "blueprints": []}

        class Ctx:
            def register_memory_provider(self, provider):
                calls["provider"] = provider

            def register_skill(self, name, path, description=""):
                self_path = Path(path)
                assert self_path.is_file(), self_path
                calls["skills"].append(name)

            def register_automation_blueprint(self, key, **kwargs):
                assert "[SILENT]" in kwargs["prompt_template"]
                calls["blueprints"].append(key)

        register(Ctx())
        self.assertEqual(["memory-writing", "lancedb-memory-system"], calls["skills"])
        self.assertEqual(["daily-review"], calls["blueprints"])

    def test_register_survives_a_host_without_skills_or_blueprints(self):
        class OldCtx:
            def register_memory_provider(self, provider):
                self.provider = provider

        ctx = OldCtx()
        register(ctx)
        self.assertIsInstance(ctx.provider, LanceDBMemoryProvider)

    def test_docs_skill_paths_still_resolve(self):
        for name in ("memory-writing", "lancedb-memory-system"):
            self.assertTrue((ROOT / "docs" / "skills" / f"{name}.md").resolve().is_file())


class MaintenanceTriggerTests(unittest.TestCase):
    def actions(self, *, fragments=2, unindexed=0, rows=1000):
        diagnostics = {
            "tables": {"memories": {"rows": rows, "fragments": fragments, "current_version": 5,
                                    "cleanup_eligible_versions": 0}},
            "fts": {"num_unindexed_rows": unindexed},
        }
        from datetime import datetime, timezone
        return _routine_actions(diagnostics, {}, datetime.now(timezone.utc))[0]

    def test_fragment_threshold_is_low_enough_to_act(self):
        self.assertLessEqual(MAINTENANCE_MAX_FRAGMENTS, 16)
        self.assertEqual({"memories": ["compact"]}, self.actions(fragments=MAINTENANCE_MAX_FRAGMENTS + 1))

    def test_lagging_fts_triggers_a_reindex(self):
        lagging = 1000 * MAINTENANCE_MAX_FTS_UNINDEXED_PCT // 100 + 1
        self.assertEqual({"memories": ["reindex"]}, self.actions(unindexed=lagging))
        self.assertEqual({}, self.actions(unindexed=1))


if __name__ == "__main__":
    unittest.main()

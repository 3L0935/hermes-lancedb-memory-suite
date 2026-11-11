"""Hub structure: topics, hub detection, plans, write-time suggestions, search expansion."""

import json
import unittest

from plugin import LanceDBMemoryProvider
from plugin.hubs import find_hub, propose_attachments, suggest_hubs, topic_of
from plugin.store import LanceDBStore


def row(memory_id, key, category="tech"):
    return {"id": memory_id, "content": f"{key} fact=1 [Tier=2]", "category": category}


def part_of(source, target):
    return {"from": source, "to": target, "relation_type": "part_of"}


class TopicTests(unittest.TestCase):
    def test_project_domains_are_topics_generic_domains_are_not(self):
        self.assertEqual("bo3", topic_of("t7-source", "BO3"))
        self.assertEqual("quock", topic_of("Quock_Agent_Plan", "Projet"))
        self.assertEqual("kanban", topic_of("Kanban-worker-toolsets", "Hermes"))
        self.assertEqual("", topic_of("Config", "Tech"))
        self.assertEqual("", topic_of("Test-Qwen", "Projet"))


class HubDetectionTests(unittest.TestCase):
    def test_project_hub_beats_an_operation_hub_with_more_links(self):
        rows = [row("p", "Projet:LanceDB", "project"), row("op", "Tech:LanceDB_Cleanup_14-09_Hub"),
                row("c", "Correction:LanceDB_index")]
        edges = [part_of(f"x{i}", "op") for i in range(30)] + [part_of("y", "p"), part_of("z", "p")]
        plan = propose_attachments(rows, edges)
        self.assertEqual("Projet:LanceDB", plan["attach"][0]["hub"]["key"])
        attached = {m["key"] for m in plan["attach"][0]["members"]}
        # The operation hub becomes a child: a two-level star.
        self.assertEqual({"Tech:LanceDB_Cleanup_14-09_Hub", "Correction:LanceDB_index"}, attached)

    def test_topic_without_hub_gets_one_to_create(self):
        rows = [row(f"q{i}", f"Projet:Quock_Part{i}", "project") for i in range(4)]
        plan = propose_attachments(rows, [])
        self.assertEqual([], plan["attach"])
        self.assertEqual("Projet:Quock", plan["create_hubs"][0]["key"])
        self.assertEqual(4, len(plan["create_hubs"][0]["children"]))

    def test_members_with_a_part_of_are_left_alone(self):
        rows = [row("h", "Projet:Godot", "project")] + [row(f"g{i}", f"Godot:Part{i}") for i in range(3)]
        edges = [part_of("g0", "elsewhere")]
        plan = propose_attachments(rows, edges)
        self.assertEqual({"g1", "g2"}, {m["id"] for m in plan["attach"][0]["members"]})

    def test_small_or_generic_groups_are_ignored(self):
        rows = [row("a", "Projet:Tiny_A"), row("b", "Projet:Tiny_B"), row("t", "Tech:Config")]
        plan = propose_attachments(rows, [])
        self.assertEqual(([], []), (plan["attach"], plan["create_hubs"]))

    def test_find_hub_needs_the_topic_itself(self):
        members = [{"id": "a", "domain": "Correction", "subject": "LanceDB", "indegree": 9, "category": "correction"}]
        self.assertIsNone(find_hub(members, "lancedb"))


class SuggestionTests(unittest.TestCase):
    def test_new_memory_is_pointed_at_its_project_hub(self):
        rows = [row("h", "Projet:Quock", "project"), row("m", "Projet:Quock_Repo")]
        suggestions = suggest_hubs("Projet", "Quock_Build", rows, [], exclude_id="new")
        self.assertEqual([("part_of", "h")], [(s["type"], s["target_id"]) for s in suggestions])

    def test_add_without_relations_returns_suggestions(self):
        class Store:
            def add_memory(self, memory):
                return {"success": True, "status": "created", "memory_id": "new",
                        "canonical_content": "Projet:Quock_Build x=1 [Tier=2]"}

            def get_conflicts(self, **_):
                return []

            def get_all(self):
                return [row("h", "Projet:Quock", "project")]

            def get_typed_edges(self):
                return []

        provider = LanceDBMemoryProvider()
        provider._store = Store()
        payload = json.loads(provider.handle_tool_call("lancedb_add", {
            "domain": "Projet", "subject": "Quock_Build", "facts": ["x=1"], "tier": 2, "category": "project"}))
        self.assertEqual("h", payload["suggested_relations"][0]["target_id"])


class HubExpansionTests(unittest.TestCase):
    def store(self, hubs, neighbors):
        store = LanceDBStore.__new__(LanceDBStore)
        store._hub_ids = lambda: set(hubs)
        store._expand_relation_context = lambda seeds, top_k, category, **_: list(seeds) + [dict(n) for n in neighbors]
        return store

    def test_children_of_a_found_hub_fill_free_slots(self):
        store = self.store({"hub"}, [{"id": "c1"}, {"id": "c2"}, {"id": "direct"}])
        results = store._expand_hub_children([{"id": "direct"}, {"id": "hub"}], top_k=4)
        self.assertEqual(["direct", "hub", "c1", "c2"], [r["id"] for r in results])
        self.assertEqual("hub", results[2]["retrieval_source"])

    def test_no_hub_no_change_and_full_results_stay_full(self):
        store = self.store(set(), [{"id": "c1"}])
        self.assertEqual([{"id": "a"}], store._expand_hub_children([{"id": "a"}], top_k=6))
        store = self.store({"a"}, [{"id": "c1"}])
        self.assertEqual(1, len(store._expand_hub_children([{"id": "a"}], top_k=1)))


if __name__ == "__main__":
    unittest.main()

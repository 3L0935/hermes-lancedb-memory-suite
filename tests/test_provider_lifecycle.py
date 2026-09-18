"""MemoryProvider lifecycle contract tests.

Covers the provider methods the core calls that were previously absent:
recall_status() (deterministic recall indicator) and identity_signature()
(gateway agent-cache busting), plus the prefetch()/recall_status() reset contract.
"""

import json
import unittest

from agent.memory_provider import MemoryProvider, RecallStatus

from plugin import LanceDBMemoryProvider


class SearchStore:
    """Store stub whose search() returns a fixed list of dicts."""

    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = 0

    def search(self, query, top_k=5):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.results)

    def count(self):
        return len(self.results)


def hits(n):
    return [
        {"category": "tech", "score": 0.5, "quality": 0.8, "content": f"m{i}", "relations": []}
        for i in range(n)
    ]


class MinimalProvider(MemoryProvider):
    """Minimal concrete subclass that inherits every default — the negative control."""

    @property
    def name(self) -> str:
        return "minimal"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        pass

    def get_tool_schemas(self):
        return []


class RecallStatusTests(unittest.TestCase):
    def setUp(self):
        self.provider = LanceDBMemoryProvider(config={"auto_prefetch": True})
        self.store = SearchStore(results=hits(3))
        self.provider._store = self.store

    def test_recall_status_is_none_before_any_prefetch(self):
        self.assertIsNone(LanceDBMemoryProvider().recall_status())

    def test_recall_status_reports_the_count_injected_by_the_last_prefetch(self):
        self.provider.prefetch("anything")
        status = self.provider.recall_status()
        self.assertIsInstance(status, RecallStatus)
        self.assertEqual("LanceDB", status.provider_label)
        self.assertEqual(3, status.count)

    def test_recall_status_reflects_only_the_last_prefetch(self):
        """A prefetch that injects nothing must clear the previous count."""
        self.provider.prefetch("anything")
        self.assertEqual(3, self.provider.recall_status().count)

        self.store.results = []
        self.provider.prefetch("anything")
        self.assertIsNone(self.provider.recall_status())

    def test_recall_status_is_none_when_no_store_or_empty_query(self):
        self.provider._store = None
        self.provider.prefetch("anything")
        self.assertIsNone(self.provider.recall_status())

        self.provider._store = self.store
        self.provider.prefetch("")
        self.assertIsNone(self.provider.recall_status())

    def test_recall_status_is_none_when_the_search_raises(self):
        self.provider._store = SearchStore(error=RuntimeError("boom"))
        self.provider.prefetch("anything")
        self.assertIsNone(self.provider.recall_status())

    def test_base_class_renders_nothing_by_default(self):
        """Negative control: the default is None, so our count is what makes it show."""
        self.assertIsNone(MinimalProvider().recall_status())

    def test_provider_overrides_the_lifecycle_methods(self):
        self.assertIsNot(LanceDBMemoryProvider.recall_status, MemoryProvider.recall_status)
        self.assertIsNot(LanceDBMemoryProvider.identity_signature, MemoryProvider.identity_signature)


class AutoPrefetchDisabledTests(unittest.TestCase):
    """The push path is off by default: an off-topic match must never reach the turn."""

    def test_default_provider_injects_nothing(self):
        provider = LanceDBMemoryProvider()  # no config -> default
        provider._store = SearchStore(results=hits(3))
        self.assertEqual("", provider.prefetch("anything"))
        self.assertEqual(0, provider._store.calls, "search must not even run")
        self.assertIsNone(provider.recall_status())

    def test_explicit_false_injects_nothing(self):
        provider = LanceDBMemoryProvider(config={"auto_prefetch": False})
        provider._store = SearchStore(results=hits(3))
        self.assertEqual("", provider.prefetch("anything"))
        self.assertEqual(0, provider._store.calls)

    def test_opt_in_restores_injection(self):
        provider = LanceDBMemoryProvider(config={"auto_prefetch": True})
        provider._store = SearchStore(results=hits(2))
        text = provider.prefetch("anything")
        self.assertTrue(text.startswith("## LanceDB Memory"))
        self.assertEqual(2, provider.recall_status().count)

    def test_disabled_path_never_calls_ollama(self):
        """The disabled path must not pay the embedding round-trip either."""

        class ExplodingStore:
            calls = 0

            def search(self, *a, **k):
                ExplodingStore.calls += 1
                raise AssertionError("the disabled prefetch must not touch the store")

        provider = LanceDBMemoryProvider()
        provider._store = ExplodingStore()
        self.assertEqual("", provider.prefetch("anything"))
        self.assertEqual(0, ExplodingStore.calls)


class IdentitySignatureTests(unittest.TestCase):
    @staticmethod
    def _manifest_version() -> str:
        import yaml
        from pathlib import Path
        manifest = Path(__file__).resolve().parent.parent / "plugin" / "plugin.yaml"
        return str(yaml.safe_load(manifest.read_text(encoding="utf-8"))["version"])

    def test_signature_is_a_json_serializable_mapping_with_the_manifest_version(self):
        signature = LanceDBMemoryProvider().identity_signature()
        self.assertIsInstance(signature, dict)
        self.assertEqual({"lancedb_version"}, set(signature))
        # Contract between two pieces of data, not a snapshot: the signature carries the
        # version the deployed manifest declares, whatever that version currently is.
        self.assertEqual(self._manifest_version(), signature["lancedb_version"])
        json.dumps(signature)  # the gateway folds this into its cache key

    def test_signature_works_without_initialize(self):
        """The gateway calls this on an UNINITIALIZED instance on every message."""
        provider = LanceDBMemoryProvider()
        self.assertIsNone(provider._store)
        self.assertEqual(self._manifest_version(), provider.identity_signature()["lancedb_version"])

    def test_signature_does_not_raise_when_the_manifest_is_unreadable(self):
        import plugin as plugin_module

        original = plugin_module.__file__
        try:
            plugin_module.__file__ = "/nonexistent/plugin/__init__.py"
            self.assertEqual(
                {"lancedb_version": ""}, LanceDBMemoryProvider().identity_signature()
            )
        finally:
            plugin_module.__file__ = original


if __name__ == "__main__":
    unittest.main()

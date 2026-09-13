"""Regression tests for bounded LanceDB metadata write amplification."""

import json
from pathlib import Path

import numpy as np
import pytest

from plugin.memory_contract import MemoryWrite
from plugin.store import LanceDBStore


def fake_embed(_self, _text):
    return np.eye(1, 768, dtype=np.float32)[0]


def physical_state(store):
    return {
        "version": int(store._table.version),
        "files": {
            str(path.relative_to(store._path)): path.stat().st_size
            for path in store._path.rglob("*")
            if path.is_file()
        },
    }


@pytest.fixture
def stored_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(LanceDBStore, "_embed", fake_embed)
    store = LanceDBStore(tmp_path / "db")
    result = store.add_memory(MemoryWrite.from_mapping({
        "domain": "Project",
        "subject": "WriteBudget",
        "facts": ["state=active"],
        "tier": 2,
        "category": "project",
    }))
    memory_id = result["memory_id"]
    assert store.update_tags(memory_id, ["alpha", "beta"])
    assert store.update_entities(memory_id, ["Aurora", "Beacon"])
    return store, memory_id


def test_identical_tag_and_entity_updates_are_physically_read_only(stored_memory):
    store, memory_id = stored_memory
    before = physical_state(store)

    assert store.update_tags(memory_id, ["beta", "alpha"])
    assert store.update_entities(memory_id, ["Beacon", "Aurora"])

    assert physical_state(store) == before


def test_bulk_tag_counts_only_changed_rows_and_repeat_is_read_only(stored_memory):
    store, memory_id = stored_memory
    before = physical_state(store)

    unchanged = store.bulk_tag([memory_id], add_tags=["alpha"], remove_tags=["missing"])

    assert unchanged == {"updated": 0, "errors": []}
    assert physical_state(store) == before

    changed = store.bulk_tag([memory_id], add_tags=["gamma"])
    assert changed == {"updated": 1, "errors": []}
    after_change = physical_state(store)
    repeated = store.bulk_tag([memory_id], add_tags=["gamma"])
    assert repeated == {"updated": 0, "errors": []}
    assert physical_state(store) == after_change


def test_rename_and_merge_equivalent_tag_sets_do_not_write(stored_memory):
    store, memory_id = stored_memory
    assert store.update_tags(memory_id, ["alpha", "beta", "target"])
    before = physical_state(store)

    assert store.rename_tag("alpha", "alpha") == 0
    assert store.merge_tags(["target"], "target") == 0

    assert physical_state(store) == before
    raw = store._get_by_id_raw(memory_id)
    assert set(raw["tags"]) == {"alpha", "beta", "target"}


def test_metadata_changes_preserve_timestamp_on_noop(stored_memory):
    store, memory_id = stored_memory
    timestamp = store._get_by_id_raw(memory_id)["updated_at"]

    assert store.update_tags(memory_id, ["alpha", "beta"])
    assert store.update_entities(memory_id, ["Aurora", "Beacon"])

    raw = store._get_by_id_raw(memory_id)
    assert raw["updated_at"] == timestamp
    assert json.dumps(raw["tags"], sort_keys=True)

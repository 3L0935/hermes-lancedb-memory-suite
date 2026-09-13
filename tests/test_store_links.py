"""Behavioral coverage of stable, differential entity-link selection."""
import json
from unittest.mock import patch

import numpy as np
import pytest

from plugin.memory_contract import MemoryPatch, MemoryWrite
from plugin.store import LanceDBStore, _STOP_ENTITIES


def memory_id(index):
    return f"{index:08x}-000"


def raw_rows(store):
    return {row["id"]: row for row in store._table.to_arrow().to_pylist()}


def link_sets(store):
    return {key: set(json.loads(row["links"])) for key, row in raw_rows(store).items()}


def expected_links(store):
    """Small independent all-pairs oracle; production uses an inverted index."""
    signatures = {
        key: set(json.loads(row["entities"])) - _STOP_ENTITIES
        for key, row in raw_rows(store).items()
    }
    return {
        key: set(sorted(other for other, sig in signatures.items()
                        if other != key and len(entities & sig) >= 2)[:8])
        for key, entities in signatures.items()
    }


def non_link_rows(store):
    return {key: {k: v for k, v in row.items() if k != "links"}
            for key, row in raw_rows(store).items()}


def storage_state(store):
    return (store._table.version, {
        str(p.relative_to(store._path)): p.stat().st_size
        for p in store._path.rglob("*") if p.is_file()
    })


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(LanceDBStore, "_embed", lambda *_: np.eye(1, 768, dtype=np.float32)[0])
    result = LanceDBStore(tmp_path / "db")
    rows = []
    for index in [*range(10, 22), 50, 51, 52, 70, 71]:
        entities = (["Aurora", "Beacon"] if index < 50 else
                    ["Cobalt", "Dahlia"] if index < 70 else ["Aurora", "git", "code"])
        rows.append({
            "id": memory_id(index),
            "content": f"Project:LinkFixture{index} marker=entry{index} [Tier=2]",
            "category": "project", "entities": json.dumps(entities), "links": "[]",
            "relations": "[]", "tags": "[]", "quality": 0.5, "type": "project",
            "source": "synthetic-link-test", "session_id": "", "user_id": "test",
            "created_at": 1.0, "updated_at": 2.0, "access_count": 0, "accessed_at": 0.0,
            "vector": [1.0] + [0.0] * 767,
        })
    result._table.add(list(reversed(rows)))
    return result


@pytest.mark.parametrize("touched", [None, 10, 11, 17, 21, 50, 70])
def test_rebuild_converges_without_updates_after_neutral_rewrites(store, touched):
    original_columns = non_link_rows(store)
    store._rebuild_all_links()
    if touched is not None:
        key = memory_id(touched)
        stored = raw_rows(store)[key]["links"]
        store._table.update(f"id = '{key}'", {"links": stored})
    before = storage_state(store)
    with patch.object(store._table, "update", wraps=store._table.update) as update:
        store._rebuild_all_links()
        store._rebuild_all_links()
    assert update.call_count == 0, "unchanged rebuilds must issue no updates after convergence"
    assert storage_state(store) == before
    assert link_sets(store) == expected_links(store)
    assert non_link_rows(store) == original_columns


@pytest.mark.parametrize("single", [False, True])
def test_selection_survives_scan_permutations_and_reopening(store, single):
    store._rebuild_all_links()
    snapshots = store.get_all()
    before = storage_state(store)
    for order in [list(reversed(snapshots)), snapshots[5:] + snapshots[:5]]:
        with patch.object(store, "get_all", return_value=order):
            with patch.object(store._table, "update", wraps=store._table.update) as update:
                if single:
                    for index in [10, 17, 21, 50, 70]:
                        store._rebuild_links_for(memory_id(index))
                else:
                    store._rebuild_all_links()
            assert update.call_count == 0
    reopened = LanceDBStore(store._path)
    reopened._rebuild_all_links()
    assert storage_state(reopened) == before
    assert link_sets(reopened) == expected_links(reopened)


def assert_global_is_noop(store):
    assert link_sets(store) == expected_links(store)
    before = storage_state(store)
    with patch.object(store._table, "update", wraps=store._table.update) as update:
        store._rebuild_all_links()
    assert update.call_count == 0
    assert storage_state(store) == before


def test_insertion_recalculates_all_candidate_neighbors_not_only_selected_eight(store):
    store._rebuild_all_links()
    before = non_link_rows(store)
    # A low ID must enter every old clique member's retained subset, including
    # those absent from the new memory's own eight outgoing links.
    with patch("uuid.uuid4", return_value="00000001-0000-0000-0000-000000000000"):
        with patch("plugin.store.extract_entities", return_value=["Aurora", "Beacon"]):
            outcome = store.add_memory(MemoryWrite.from_mapping({
                "domain": "Project", "subject": "NewLinkFixture", "facts": ["marker=fresh"],
                "tier": 2, "category": "project",
            }))
    new_id = outcome["memory_id"]
    assert new_id == memory_id(1)
    links = link_sets(store)
    assert len(links[new_id]) == 8
    assert all(new_id in links[memory_id(i)] for i in range(10, 22))
    after = non_link_rows(store)
    assert {key: after[key] for key in before} == before
    assert_global_is_noop(store)


def test_single_rebuild_repairs_old_neighbors_after_entity_change(store):
    store._rebuild_all_links()
    key = memory_id(10)
    store._table.update(f"id = '{key}'", {"entities": json.dumps(["Cobalt", "Dahlia"])})
    before = non_link_rows(store)
    store._rebuild_links_for(key)
    assert non_link_rows(store) == before
    assert_global_is_noop(store)


@pytest.mark.parametrize("operation", ["update_entities", "update_memory", "import", "delete", "bulk_delete"])
def test_public_mutations_leave_links_converged(store, operation):
    store._rebuild_all_links()
    if operation == "update_entities":
        assert store.update_entities(memory_id(10), ["Cobalt", "Dahlia"])
    elif operation == "update_memory":
        with patch("plugin.store.extract_entities", return_value=["Cobalt", "Dahlia"]):
            outcome = store.update_memory(MemoryPatch.from_mapping({
                "memory_id": memory_id(10), "facts": ["marker=changed"],
            }))
        assert outcome["success"]
    elif operation == "import":
        result = store.import_records([{
            "id": memory_id(1), "content": "Project:ImportedLinkFixture marker=fresh [Tier=2]",
            "category": "project", "entities": ["Aurora", "Beacon"],
        }])
        assert result["imported"] == 1
    elif operation == "delete":
        assert store.delete(memory_id(10))
    else:
        assert store.bulk_delete([memory_id(10), memory_id(17)])["deleted"] == 2
    assert_global_is_noop(store)


def test_missing_single_target_and_legacy_ids_do_not_trigger_other_writes(store):
    store._table.update(f"id = '{memory_id(21)}'", {"id": "legacy-link-id"})
    before = non_link_rows(store)
    store._rebuild_all_links()
    assert link_sets(store) == expected_links(store)
    assert non_link_rows(store) == before
    with patch.object(store._table, "update", wraps=store._table.update) as update:
        store._rebuild_links_for(memory_id(99))
        store._rebuild_links_for(memory_id(10))
    assert update.call_count == 0


def test_many_link_changes_use_one_merge_and_preserve_other_columns(store):
    store._rebuild_all_links()
    key = memory_id(10)
    store._table.update(
        f"id = '{key}'", {"entities": json.dumps(["Cobalt", "Dahlia"])}
    )
    before = non_link_rows(store)
    version_before = int(store._table.version)

    with patch.object(
        store, "_merge_memory_rows", wraps=store._merge_memory_rows
    ) as merge:
        store._rebuild_all_links()

    assert merge.call_count == 1
    assert len(merge.call_args.args[0]) > 1
    assert int(store._table.version) == version_before + 1
    assert non_link_rows(store) == before
    assert_global_is_noop(store)


def test_delete_has_bounded_memories_commits_independent_of_link_fanout(store):
    store._rebuild_all_links()
    store.refresh_fts_index()
    before = non_link_rows(store)
    version_before = int(store._table.version)

    assert store.delete(memory_id(10))

    # One delete, one complete-row link merge, one writer-batch FTS refresh.
    assert int(store._table.version) == version_before + 3
    after = non_link_rows(store)
    assert memory_id(10) not in after
    assert after == {key: value for key, value in before.items() if key != memory_id(10)}
    assert_global_is_noop(store)


@pytest.mark.parametrize("row_count", [20, 200])
def test_delete_commit_budget_does_not_scale_with_unrelated_rows(
    tmp_path, monkeypatch, row_count
):
    monkeypatch.setattr(
        LanceDBStore, "_embed", lambda *_: np.eye(1, 768, dtype=np.float32)[0]
    )
    candidate = LanceDBStore(tmp_path / f"db-{row_count}")
    rows = []
    for index in range(row_count):
        entities = (
            ["SharedCluster", "SharedSignal"]
            if index < 12
            else [f"Unique{index}", f"Marker{index}"]
        )
        rows.append({
            "id": memory_id(index),
            "content": f"Project:Scale{index} marker={index} [Tier=2]",
            "category": "project",
            "entities": json.dumps(entities),
            "links": "[]",
            "relations": "[]",
            "tags": "[]",
            "quality": 0.5,
            "type": "project",
            "source": "synthetic-scale-test",
            "session_id": "",
            "user_id": "test",
            "created_at": 1.0,
            "updated_at": 2.0,
            "access_count": 0,
            "accessed_at": 0.0,
            "vector": [1.0] + [0.0] * 767,
        })
    candidate._table.add(rows)
    candidate._rebuild_all_links()
    candidate.refresh_fts_index()
    version_before = int(candidate._table.version)

    assert candidate.delete(memory_id(0))

    assert int(candidate._table.version) == version_before + 3
    assert link_sets(candidate) == expected_links(candidate)

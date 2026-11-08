from pathlib import Path

import polars as pl
import pytest

from instagraph.errors import NoDataError
from instagraph.store.cache import make_key, response_cache
from instagraph.store.dataset import (
    MESSAGE_TYPES,
    MESSAGES_SCHEMA,
    TABLE_SCHEMAS,
    Dataset,
    DatasetStore,
    conform,
)
from instagraph.testing import build_dataset


def test_schemas_conform(small_dataset: Dataset) -> None:
    for name, schema in TABLE_SCHEMAS.items():
        assert small_dataset.table(name).collect_schema() == pl.Schema(schema), name


def test_conform_fills_defaults() -> None:
    df = conform(pl.DataFrame({"message_id": [1], "type": ["text"]}), MESSAGES_SCHEMA)
    row = df.row(0, named=True)
    assert row["n_photos"] == 0 and row["links"] == [] and row["call_missed"] is False
    assert conform(pl.DataFrame(), MESSAGES_SCHEMA).height == 0
    assert "text" in MESSAGE_TYPES


def test_save_load_roundtrip(tmp_path: Path, small_dataset: Dataset) -> None:
    small_dataset.import_report = {"files": 3}
    small_dataset.save(tmp_path / "ds")
    small_dataset.save(tmp_path / "ds")  # overwrite works
    loaded = Dataset.load(tmp_path / "ds")
    assert loaded.import_report == {"files": 3}
    for name in TABLE_SCHEMAS:
        assert loaded.table(name).collect().equals(small_dataset.table(name).collect()), name
    assert [p.name for p in tmp_path.iterdir()] == ["ds"]  # no temp dirs left


def test_store_lifecycle(tmp_path: Path, small_dataset: Dataset) -> None:
    store = DatasetStore(tmp_path)
    with pytest.raises(NoDataError):
        store.get()
    response_cache.set(make_key(0, "x"), 1)
    installed = store.replace(small_dataset)
    assert len(response_cache) == 0
    assert installed.version == 1 and store.get() is installed
    assert installed.counts()["messages"] == 20

    store2 = DatasetStore(tmp_path)  # "restart"
    assert store2.load_from_disk() is not None
    assert store2.get().version == 1
    assert store2.bump_version() == 2

    store2.clear()
    assert not store2.is_loaded() and not (tmp_path / "dataset").exists()
    assert store2.replace(small_dataset, persist=False).version == 4  # monotonic


def test_build_dataset_requires_chronological_ids() -> None:
    with pytest.raises(ValueError, match="chronological"):
        build_dataset(
            [{"display_name": "A"}],
            [{"participants": [0]}],
            [
                {"thread_id": 0, "sender_id": 0, "ts": "2024-01-02T00:00:00Z", "text": "b"},
                {"thread_id": 0, "sender_id": 0, "ts": "2024-01-01T00:00:00Z", "text": "a"},
            ],
        )


def test_color_indices_owner_first(small_dataset: Dataset) -> None:
    colors = small_dataset.color_indices()
    assert colors[0] == 0
    assert sorted(colors.values()) == list(range(5))

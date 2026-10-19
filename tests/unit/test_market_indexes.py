"""Unit tests for IndexStore persistence, honest placeholder seeds, and throttled cache flushing."""

import json
from unittest.mock import patch

from src.web_server.data_layer import DEFAULT_INDEXES, TRACKED_INDEXES, IndexStore


def test_default_indexes_are_honest_placeholders():
    """All 5 tracked indexes start as placeholders with null prices instead of fabricated numbers."""
    for key in TRACKED_INDEXES:
        entry = DEFAULT_INDEXES[key]
        assert entry["currentPrice"] is None
        assert entry["openPrice"] is None
        assert entry["change"] is None
        assert entry["changePercent"] is None
        assert entry["priceType"] == "placeholder"
        assert entry["lastUpdate"] is None


def test_index_store_does_not_persist_placeholder_entries(tmp_path):
    """_write_snapshot only writes real entries, omitting unseeded placeholders from index_cache.json."""
    cache_file = str(tmp_path / "index_cache_no_placeholders.json")
    store = IndexStore(cache_file=cache_file, flush_interval=60.0)

    # Flush on a completely unseeded store writes an empty object (no fabricated defaults)
    store.flush_cache(force=True)
    with open(cache_file, encoding="utf-8") as f:
        saved_empty = json.load(f)
    assert saved_empty == {}

    # Updating one index persists only that index, leaving the other 4 out of the file
    store.update_index("SP500", current_price=5820.0, change=15.0, change_percent=0.26)
    store.flush_cache(force=True)
    with open(cache_file, encoding="utf-8") as f:
        saved_one = json.load(f)
    assert list(saved_one.keys()) == ["SP500"]
    assert saved_one["SP500"]["currentPrice"] == 5820.0


def test_index_store_load_ignores_placeholder_and_legacy_fabricated_entries(tmp_path):
    """IndexStore.load() ignores placeholder entries and old fabricated 16:00:00 seed prices."""
    cache_file = str(tmp_path / "index_cache_legacy.json")
    raw_cache = {
        "VIX": {
            "index": "VIX",
            "displayName": "VIX",
            "ticker": "^VIX",
            "currentPrice": 15.25,
            "change": -0.45,
            "changePercent": -2.87,
            "priceType": "closing",
            "lastUpdate": "16:00:00",
        },
        "DJIA": {
            "index": "DJIA",
            "displayName": "DJIA",
            "ticker": "^DJI",
            "currentPrice": None,
            "priceType": "placeholder",
            "lastUpdate": None,
        },
        "SP500": {
            "index": "SP500",
            "displayName": "S&P 500",
            "ticker": "SPX",
            "currentPrice": 5900.25,
            "change": 12.5,
            "changePercent": 0.21,
            "priceType": "live",
            "lastUpdate": "14:45:10",
        },
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(raw_cache, f)

    store = IndexStore(cache_file=cache_file, flush_interval=60.0)
    assert store.get_index("VIX")["currentPrice"] is None
    assert store.get_index("VIX")["priceType"] == "placeholder"
    assert store.get_index("DJIA")["currentPrice"] is None
    assert store.get_index("DJIA")["priceType"] == "placeholder"
    assert store.get_index("SP500")["currentPrice"] == 5900.25
    assert store.get_index("SP500")["priceType"] == "live"
    assert store.get_index("UNKNOWN_IDX")["currentPrice"] is None
    assert store.get_index("UNKNOWN_IDX")["priceType"] == "placeholder"


def test_index_store_dirty_flag_and_throttled_flush(tmp_path):
    """Rapid update_index calls mark dirty and throttle disk writes until flush_interval or flush_cache()."""
    cache_file = str(tmp_path / "index_cache.json")
    store = IndexStore(cache_file=cache_file, flush_interval=60.0)

    with patch.object(store, "_write_snapshot", wraps=store._write_snapshot) as spy_save:
        # First update on a fresh store flushes immediately
        store.update_index("SP500", current_price=5800.0, change=10.0, change_percent=0.17)
        assert spy_save.call_count == 1
        assert store._dirty is False

        # Rapid subsequent updates within flush_interval update memory and set _dirty without disk I/O
        store.update_index("SP500", current_price=5810.0, change=20.0, change_percent=0.35)
        store.update_index("NASDAQ", current_price=18600.0, change=50.0, change_percent=0.27)
        assert spy_save.call_count == 1
        assert store._dirty is True
        assert store.get_index("SP500")["currentPrice"] == 5810.0
        assert store.get_index("NASDAQ")["currentPrice"] == 18600.0

        # Explicit flush_cache persists pending changes and clears _dirty
        store.flush_cache()
        assert spy_save.call_count == 2
        assert store._dirty is False

    reloaded = IndexStore(cache_file=cache_file)
    assert reloaded.get_index("SP500")["currentPrice"] == 5810.0
    assert reloaded.get_index("NASDAQ")["currentPrice"] == 18600.0


def test_index_store_flush_cache_noop_when_clean_and_not_forced(tmp_path):
    """flush_cache(force=False) skips disk write when _dirty is False."""
    cache_file = str(tmp_path / "index_cache_clean.json")
    store = IndexStore(cache_file=cache_file, flush_interval=60.0)

    with patch.object(store, "_write_snapshot") as mock_save:
        store.flush_cache(force=False)
        mock_save.assert_not_called()

        store.flush_cache(force=True)
        mock_save.assert_called_once()

"""Unit tests for IndexStore persistence and throttled cache flushing."""

from unittest.mock import patch

from src.web_server.data_layer import IndexStore


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

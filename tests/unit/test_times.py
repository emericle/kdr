"""Tests for the UTC time helpers in ``src/times.py``."""
from datetime import UTC, datetime, timedelta, timezone

from src.times import epoch_minute, epoch_seconds, hhmmss, minute_key, now_utc, parse_iso, to_utc

# 2025-11-11 07:30:00 UTC, the shape the market buffer stores.
EPOCH_SECONDS = 1762846200.0


def test_now_utc_is_aware():
    value = now_utc()
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0


def test_naive_datetime_is_read_as_utc():
    naive = datetime(2026, 10, 11, 15, 30)
    aware = to_utc(naive)
    assert aware.tzinfo is not None
    assert aware.strftime("%H:%M:%S") == "15:30:00"


def test_aware_datetime_is_converted_not_relabelled():
    new_york = datetime(2026, 10, 11, 11, 30, tzinfo=timezone(timedelta(hours=-4)))
    assert to_utc(new_york) == datetime(2026, 10, 11, 15, 30, tzinfo=UTC)


def test_epoch_seconds_round_trip():
    assert to_utc(EPOCH_SECONDS).strftime("%Y-%m-%d %H:%M:%S") == "2025-11-11 07:30:00"
    assert epoch_seconds(EPOCH_SECONDS) == EPOCH_SECONDS


def test_epoch_milliseconds_are_detected_by_magnitude():
    as_milliseconds = int(EPOCH_SECONDS * 1e3)
    assert to_utc(as_milliseconds) == to_utc(EPOCH_SECONDS)
    assert epoch_seconds(as_milliseconds) == EPOCH_SECONDS


def test_epoch_nanoseconds_are_detected_by_magnitude():
    as_nanoseconds = int(EPOCH_SECONDS * 1e9)
    assert to_utc(as_nanoseconds) == to_utc(EPOCH_SECONDS)
    assert epoch_seconds(as_nanoseconds) == EPOCH_SECONDS


def test_iso_text_is_parsed():
    assert to_utc("2026-10-11T15:30:00Z") == datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
    assert to_utc("2026-10-11 15:30:00") == datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
    assert parse_iso("2026-10-11T15:30:00+02:00") == datetime(2026, 10, 11, 13, 30, tzinfo=UTC)


def test_malformed_text_raises_value_error():
    """A bad timestamp is a bad request, so it must stay a ``ValueError``."""
    for bad in ("not-a-time", "2026-13-45"):
        try:
            parse_iso(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} parsed without error")


def test_rejects_values_that_are_not_timestamps():
    """``None`` and bools are not timestamps and must not become "now"."""
    for bad in (None, True, False, [1, 2], object()):
        try:
            to_utc(bad)
        except TypeError:
            continue
        raise AssertionError(f"{bad!r} accepted as a timestamp")


def test_epoch_minute_collapses_representations_of_one_minute():
    for shape in (
        EPOCH_SECONDS,
        EPOCH_SECONDS * 1_000,
        EPOCH_SECONDS * 1_000_000_000,
        datetime(2025, 11, 11, 7, 30, 30, tzinfo=UTC),
    ):
        assert epoch_minute(shape) == int(EPOCH_SECONDS) // 60


def test_minute_key_matches_the_utc_minute():
    assert minute_key("2026-10-11T15:30:42Z") == "2026-10-11 15:30"
    assert minute_key(datetime(2026, 10, 11, 15, 30, 42)) == "2026-10-11 15:30"


def test_hhmmss_accepts_every_stored_shape():
    """The market buffer stores epoch seconds, so ``hhmmss`` must take a float."""
    assert hhmmss(EPOCH_SECONDS) == "07:30:00"
    assert hhmmss(EPOCH_SECONDS * 1e9) == "07:30:00"
    assert hhmmss(datetime(2025, 11, 11, 7, 30, tzinfo=UTC)) == "07:30:00"


def test_helpers_agree_with_the_clock():
    current = now_utc()
    assert hhmmss(current) == current.strftime("%H:%M:%S")
    assert epoch_minute(current) == int(epoch_seconds(current)) // 60


def test_stored_timestamps_are_aware():
    """Anything the system stores through these helpers carries an offset."""
    for value in (now_utc(), to_utc(EPOCH_SECONDS), parse_iso("2026-10-11T15:30:00Z")):
        assert value.tzinfo is not None


def test_only_now_utc_reads_the_clock():
    """Every other helper is a pure conversion, so tests of them are stable."""
    import ast
    import inspect

    import src.times

    tree = ast.parse(inspect.getsource(src.times))
    readers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "now"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        )
    }
    assert readers == {"now_utc"}


def test_epoch_seconds_of_a_stored_datetime_is_stable():
    value = datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
    assert epoch_seconds(value) == 1791732600.0
    assert to_utc(epoch_seconds(value)) == value


class MsgpackTimestamp:
    """Stand-in for ``msgpack.Timestamp``, the object the Alpaca websocket decodes into."""

    def to_datetime(self) -> datetime:
        return datetime(2026, 10, 11, 15, 30, tzinfo=UTC)


def test_msgpack_timestamp_objects_are_converted_not_rejected():
    """Alpaca stream ticks carry a msgpack ``Timestamp``, which is a real timestamp."""
    assert to_utc(MsgpackTimestamp()) == datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
    assert epoch_seconds(MsgpackTimestamp()) == 1791732600.0
    assert hhmmss(MsgpackTimestamp()) == "15:30:00"
    assert minute_key(MsgpackTimestamp()) == "2026-10-11 15:30"
    assert epoch_minute(MsgpackTimestamp()) == 1791732600 // 60

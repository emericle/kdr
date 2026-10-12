"""Single source of truth for time handling.

Every timestamp the system stores or compares is UTC. This module is the only
place that reads the clock, so a naive ``datetime.now()`` cannot sneak into a
comparison that would otherwise raise a "can't compare offset-in-timezones"
error.

Timestamps arrive in four shapes, and every helper here accepts all four:

* ``datetime`` -- naive ones are read as UTC, aware ones are converted.
* epoch seconds (``int``/``float``) -- what the market buffer stores.
* epoch nanoseconds (``int``) -- what the Alpaca stream sends.
* ISO-8601 text -- what the query API accepts.

Magnitude decides between the numeric shapes. A value at or above
``NANOSECOND_THRESHOLD`` is nanoseconds, one at or above
``MILLISECOND_THRESHOLD`` is milliseconds, and anything smaller is seconds.
The thresholds are epoch seconds past the year 30000, so no real timestamp can
be mistaken for the wrong shape.
"""

from datetime import datetime, timezone

NANOSECOND_THRESHOLD = 1e15
MILLISECOND_THRESHOLD = 1e12


def now_utc() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_utc(value):
    """Normalize any timestamp shape to a timezone-aware UTC datetime.

    Accepts a ``datetime`` (naive means UTC), epoch seconds, epoch
    milliseconds, epoch nanoseconds, or ISO-8601 text. The result is always
    aware, so it can be compared against stored timestamps without a type
    error. Anything else -- ``None``, a bool, an arbitrary object -- raises
    ``TypeError`` rather than quietly becoming "now".
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        return to_utc(datetime.fromisoformat(value))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if abs(seconds) >= NANOSECOND_THRESHOLD:
            seconds /= 1e9
        elif abs(seconds) >= MILLISECOND_THRESHOLD:
            seconds /= 1e3
        return from_epoch(seconds)

    raise TypeError(f"not a timestamp: {value!r}")


def from_epoch(seconds: float) -> datetime:
    """Convert epoch seconds to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(float(seconds), tz=timezone.utc)


def epoch_seconds(value) -> float:
    """Convert any timestamp shape to epoch seconds."""
    return to_utc(value).timestamp()


def parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Text carrying an offset is converted; text without one is read as UTC, the
    same convention every other helper here uses. Malformed text raises
    ``ValueError``, which is what callers of the query API want: an
    unparseable ``start_time`` is a bad request, not an empty result.
    """
    return to_utc(datetime.fromisoformat(text))


def epoch_minute(value) -> int:
    """Minutes since the epoch for any timestamp shape.

    The scraper keys one-minute bars by this value, so two representations of
    the same minute -- a datetime, epoch seconds, or nanoseconds -- collapse to
    one key.
    """
    return int(epoch_seconds(value)) // 60


def minute_key(value) -> str:
    """Render any timestamp shape as its UTC minute key, ``YYYY-MM-DD HH:MM``."""
    return to_utc(value).strftime("%Y-%m-%d %H:%M")


def hhmmss(value) -> str:
    """Render any timestamp shape as ``HH:MM:SS`` in UTC."""
    return to_utc(value).strftime("%H:%M:%S")

"""Human text for durations and bands. Rounded on purpose: a band printed
to the second would read as more precise than it is."""

from __future__ import annotations


def duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        m, r = divmod(s, 60)
        # under 10 minutes keep half-minute detail, above that whole minutes
        if m < 10 and r >= 30:
            return "%dm30s" % m
        return "%dm" % (m + (1 if r >= 30 and m >= 10 else 0))
    h, r = divmod(s, 3600)
    m = int(round(r / 60.0))
    if m == 60:
        h, m = h + 1, 0
    return "%dh" % h if m == 0 else "%dh%02dm" % (h, m)


def span(low: float, high: float) -> str:
    lo, hi = duration(low), duration(high)
    return lo if lo == hi else "%s–%s" % (lo, hi)


def clock(seconds: float) -> str:
    """A stopwatch reading, 1:12 or 1:02:05, for a time that really passed."""
    s = max(0, int(seconds))
    h, r = divmod(s, 3600)
    m, ss = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, ss) if h else "%d:%02d" % (m, ss)


def band_line(low: float, high: float, n: int, coverage: str, learning: bool = False,
              group: str = "") -> str:
    """Forecast 40s–3m · half of your similar turns (57)
    Forecast 40s–3m · half of turns like this  (the global model)"""
    share = "half" if coverage == "50" else "8 in 10"
    if group == "global":
        return "Forecast %s · %s of turns like this" % (span(low, high), share)
    tail = "%d, still learning" % n if learning else "%d" % n
    return "Forecast %s · %s of your similar turns (%s)" % (span(low, high), share, tail)


def done_line(dur: float, low, high) -> str:
    """done in 1:12 · inside the forecast"""
    took = "done in " + clock(dur)
    if low is None or high is None:
        return took
    if dur > high:
        return took + " · longer than the forecast (%s)" % span(low, high)
    if dur < low:
        return took + " · quicker than the forecast (%s)" % span(low, high)
    return took + " · inside the forecast"

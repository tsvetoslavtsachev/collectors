"""Unit tests for fetch_prices._weekly — the session-aware completed-week guard.

Adversarial Gate-2 finding: the old `label > last_obs` guard kept a PARTIAL bar on
an intraday/after-close Friday run (label == last_obs == today). The fix gates on
`label < today`. `today` is injected so these are deterministic (no real clock).

Run: python -m collectors.vrm.tests.test_weekly   (or pytest)
"""
import datetime as dt
import pandas as pd

from collectors.vrm.fetch_prices import _weekly


def _daily(dates, vals):
    return pd.Series(vals, index=pd.to_datetime(dates))


def test_midweek_pull_drops_future_friday():
    # Tue 2026-06-23 pull: last completed Friday is 06-19; the 06-26 bin is future.
    d = _daily(["2026-06-18", "2026-06-19", "2026-06-22", "2026-06-23"],
               [100.0, 101.0, 102.0, 103.0])
    w = _weekly(d, today=dt.date(2026, 6, 23))
    assert w.index[-1].date() == dt.date(2026, 6, 19), w.index[-1]
    assert dt.date(2026, 6, 26) not in [x.date() for x in w.index]


def test_intraday_friday_drops_partial_bar():
    # THE look-ahead case: pull ON Friday 06-19 mid-session. label == today == last
    # obs -> the old `> last_obs` guard kept it; `< today` correctly drops it.
    d = _daily(["2026-06-11", "2026-06-12", "2026-06-17", "2026-06-18", "2026-06-19"],
               [99.0, 100.0, 100.5, 101.0, 105.5])
    w = _weekly(d, today=dt.date(2026, 6, 19))
    assert dt.date(2026, 6, 19) not in [x.date() for x in w.index], "partial Friday kept!"
    assert w.index[-1].date() == dt.date(2026, 6, 12)
    assert w.iloc[-1] == 100.0   # prior completed week, not the 105.5 intraday value


def test_after_close_friday_also_deferred_to_next_run():
    # Even an after-close Friday run defers that Friday (conservative, no clock-time
    # check) -> it lands on the Saturday/Monday run below.
    d = _daily(["2026-06-18", "2026-06-19"], [101.0, 102.0])
    w = _weekly(d, today=dt.date(2026, 6, 19))
    assert len(w) == 0 or w.index[-1].date() < dt.date(2026, 6, 19)


def test_saturday_pull_keeps_just_closed_friday():
    d = _daily(["2026-06-17", "2026-06-18", "2026-06-19"], [100.0, 101.0, 102.0])
    w = _weekly(d, today=dt.date(2026, 6, 20))   # Saturday
    assert w.index[-1].date() == dt.date(2026, 6, 19)
    assert w.iloc[-1] == 102.0


def test_holiday_friday_week_kept_once_past():
    # Juneteenth 2026-06-19 = market closed; the week's bar holds Thu 06-18's close,
    # labeled 06-19. A later (Monday) run keeps it as a real completed past bar.
    d = _daily(["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18"],
               [100.0, 100.5, 101.0, 101.5])
    w = _weekly(d, today=dt.date(2026, 6, 22))   # Monday
    assert w.index[-1].date() == dt.date(2026, 6, 19)
    assert w.iloc[-1] == 101.5   # Thursday's close under the Friday label


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")

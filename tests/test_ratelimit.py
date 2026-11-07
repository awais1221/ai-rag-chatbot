import pytest

from app.ratelimit import RateLimiter, RateLimitExceeded


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_per_minute_limit_and_window_slides():
    clock = Clock()
    rl = RateLimiter(per_minute=3, per_day=100, daily_cap=1000, clock=clock)
    for _ in range(3):
        rl.check("1.1.1.1")
    with pytest.raises(RateLimitExceeded) as err:
        rl.check("1.1.1.1")
    assert err.value.retry_after > 0
    rl.check("2.2.2.2")  # other visitors are unaffected
    clock.t += 61
    rl.check("1.1.1.1")  # window has moved on


def test_per_day_limit():
    clock = Clock()
    rl = RateLimiter(per_minute=100, per_day=2, daily_cap=1000, clock=clock)
    rl.check("ip")
    rl.check("ip")
    with pytest.raises(RateLimitExceeded, match="today"):
        rl.check("ip")


def test_global_daily_cap():
    rl = RateLimiter(per_minute=100, per_day=100, daily_cap=2, clock=Clock())
    rl.check("a")
    rl.check("b")
    with pytest.raises(RateLimitExceeded, match="daily"):
        rl.check("c")


def test_new_day_resets_counters():
    clock = Clock()
    rl = RateLimiter(per_minute=100, per_day=1, daily_cap=1, clock=clock)
    rl.check("ip")
    with pytest.raises(RateLimitExceeded):
        rl.check("ip")
    clock.t += 86400
    rl.check("ip")

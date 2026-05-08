from __future__ import annotations

from datetime import UTC, datetime, timedelta

from syncarr_server.pipeline import RateSample
from syncarr_server.services.rate_tracker import RateTracker, rate_tracker


def test_empty_returns_empty_sequence() -> None:
    tracker = RateTracker()

    assert tracker.samples_for(("client-1", 1)) == []


def test_record_and_retrieve() -> None:
    tracker = RateTracker()
    sample = RateSample(at=datetime(2026, 5, 8, tzinfo=UTC), bytes_downloaded=123)

    tracker.record(("client-1", 1), sample)

    assert tracker.samples_for(("client-1", 1)) == [sample]


def test_different_keys_are_isolated() -> None:
    tracker = RateTracker()
    sample_a = RateSample(at=datetime(2026, 5, 8, tzinfo=UTC), bytes_downloaded=100)
    sample_b = RateSample(at=datetime(2026, 5, 8, 0, 1, tzinfo=UTC), bytes_downloaded=200)

    tracker.record(("client-a", 1), sample_a)
    tracker.record(("client-b", 1), sample_b)

    assert tracker.samples_for(("client-a", 1)) == [sample_a]
    assert tracker.samples_for(("client-b", 1)) == [sample_b]


def test_max_samples_evicts_oldest() -> None:
    tracker = RateTracker(max_samples=3)
    start = datetime(2026, 5, 8, tzinfo=UTC)

    for index in range(5):
        tracker.record(
            ("client-1", 1),
            RateSample(
                at=start + timedelta(seconds=index),
                bytes_downloaded=index,
            ),
        )

    samples = tracker.samples_for(("client-1", 1))

    assert len(samples) == 3
    assert samples[0].bytes_downloaded == 2
    assert samples[1].bytes_downloaded == 3
    assert samples[2].bytes_downloaded == 4


def test_module_singleton_exists() -> None:
    assert isinstance(rate_tracker, RateTracker)

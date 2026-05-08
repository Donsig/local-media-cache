from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from syncarr_server.pipeline import RateSample

AssignmentKey = tuple[str, int]


class RateTracker:
    def __init__(self, max_samples: int = 8) -> None:
        self._samples: dict[AssignmentKey, deque[RateSample]] = {}
        self._max_samples = max_samples

    def record(self, key: AssignmentKey, sample: RateSample) -> None:
        if key not in self._samples:
            self._samples[key] = deque(maxlen=self._max_samples)
        self._samples[key].append(sample)

    def samples_for(self, key: AssignmentKey) -> Sequence[RateSample]:
        return list(self._samples.get(key, []))


rate_tracker = RateTracker()

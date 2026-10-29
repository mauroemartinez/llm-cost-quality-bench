"""Small statistics helpers: Wilson interval, percentiles, outliers."""

from __future__ import annotations

import math
from collections.abc import Sequence


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval for a proportion: (estimate, low, high).

    Preferred over the normal approximation for small samples and for
    proportions near 0 or 1, which is exactly where manual reviews live."""
    if n <= 0:
        return (0.0, 0.0, 0.0)
    p = successes / n
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denominator
    return (p, max(0.0, centre - margin), min(1.0, centre + margin))


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; pct in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return float(ordered[rank - 1])


def median(values: Sequence[float]) -> float:
    return percentile(values, 50)


def outliers(values: Sequence[float], factor: float = 2.0) -> list[int]:
    """Indexes of values above `factor` times the median. A few expensive
    calls hide behind a reasonable mean; the median exposes them."""
    if not values:
        return []
    threshold = factor * median(values)
    return [i for i, v in enumerate(values) if v > threshold and threshold > 0]

from math import ceil


def estimate_tokens(value: str) -> int:
    """Return a deterministic, conservative estimate for mixed prose and code."""
    if not value:
        return 0
    return ceil(len(value.encode("utf-8")) / 3)

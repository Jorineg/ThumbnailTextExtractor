"""MAX_TEXT_LENGTH: positive int = max chars; 0 or negative = no limit (default 0)."""
import os


def max_text_length_cap() -> int | None:
    try:
        v = int(os.getenv("MAX_TEXT_LENGTH", "0").strip() or "0")
    except ValueError:
        v = 0
    return None if v <= 0 else v


def truncate_text(s: str, cap: int | None) -> str:
    if cap is None or len(s) <= cap:
        return s
    return s[:cap]

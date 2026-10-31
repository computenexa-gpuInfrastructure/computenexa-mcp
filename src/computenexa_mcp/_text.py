"""Small helpers: dollars, durations, and making text people typed safe to show an assistant."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]")
_SPACES = re.compile(r"\s+")


def clean(value: Any, limit: int = 80) -> str | None:
    """Text a person typed (a machine label, a project or team name): one line, no control or invisible characters,
    at most `limit` characters. Tool results are read by the assistant, so these never carry line breaks or
    formatting that could pass for instructions."""
    if value is None:
        return None
    text = _SPACES.sub(" ", _CONTROL.sub(" ", str(value))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def quoted(value: Any, limit: int = 80) -> str:
    """`clean`, in quotation marks, for sentences: 'project “titanic”'."""
    text = clean(value, limit)
    return "“" + (text or "").replace("“", "'").replace("”", "'") + "”"


def usd(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def money(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.2f}"


def charge_cents(price_per_hour_cents: int, minutes: int) -> int:
    """What a block of `minutes` costs at this hourly price, rounded up to the cent (as the API charges)."""
    return -(-price_per_hour_cents * minutes // 60)


def whole_hours(minutes: int) -> int:
    """Hour-billed GPUs take durations in whole hours."""
    return max(1, math.ceil(minutes / 60)) * 60


def duration(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    minutes = round(minutes)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h" if rest == 0 else f"{hours} h {rest} min"


def when(value: datetime | None) -> str | None:
    """ISO 8601 in UTC, for structured results."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def dollars(obj: Any) -> Any:
    """A copy of an API value with every `*_cents` field turned into `*_usd` (dollars, 2 decimals)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.endswith("_cents") and (v is None or isinstance(v, (int, float))):
                out[k[: -len("_cents")] + "_usd"] = None if v is None else round(v / 100, 2)
            else:
                out[k] = dollars(v)
        return out
    if isinstance(obj, list):
        return [dollars(v) for v in obj]
    if isinstance(obj, str):
        return clean(obj, 200)
    return obj

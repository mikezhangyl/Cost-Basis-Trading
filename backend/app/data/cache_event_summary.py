import json
from pathlib import Path
from typing import Any


CACHE_EVENT_SUMMARY_KEYS = (
    "cache_event_count",
    "endpoint_count",
    "endpoints",
    "request_count",
    "hit_count",
    "miss_count",
    "hit_rate_percent",
    "miss_rate_percent",
    "stale_count",
    "stale_rate_percent",
    "fetched_date_count",
    "suppressed_no_data_count",
)


def summarize_cache_events_jsonl(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_cache_event_summary()
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return summarize_cache_event_rows(events)


def summarize_cache_event_rows(events: list[dict[str, Any]]) -> dict[str, Any]:
    endpoints = sorted({str(event.get("endpoint")) for event in events if event.get("endpoint")})
    hit_count = _sum_event_int(events, "hit_count")
    miss_count = _sum_event_int(events, "miss_count")
    stale_count = _sum_event_int(events, "stale_count")
    request_count = hit_count + miss_count + stale_count
    return {
        "cache_event_count": len(events),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
        "request_count": request_count,
        "hit_count": hit_count,
        "miss_count": miss_count,
        "hit_rate_percent": _percent(hit_count, request_count),
        "miss_rate_percent": _percent(miss_count, request_count),
        "stale_count": stale_count,
        "stale_rate_percent": _percent(stale_count, request_count),
        "fetched_date_count": _sum_event_int(events, "fetched_date_count"),
        "suppressed_no_data_count": _sum_event_int(events, "suppressed_no_data_count"),
    }


def empty_cache_event_summary() -> dict[str, Any]:
    return {
        "cache_event_count": 0,
        "endpoint_count": 0,
        "endpoints": [],
        "request_count": 0,
        "hit_count": 0,
        "miss_count": 0,
        "hit_rate_percent": 0.0,
        "miss_rate_percent": 0.0,
        "stale_count": 0,
        "stale_rate_percent": 0.0,
        "fetched_date_count": 0,
        "suppressed_no_data_count": 0,
    }


def _sum_event_int(events: list[dict[str, Any]], key: str) -> int:
    return sum(int(event.get(key) or 0) for event in events)


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)

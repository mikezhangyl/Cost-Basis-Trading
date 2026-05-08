import json
from pathlib import Path

from app.data.cache_event_summary import (
    CACHE_EVENT_SUMMARY_KEYS,
    empty_cache_event_summary,
    summarize_cache_event_rows,
    summarize_cache_events_jsonl,
)


def test_cache_event_summary_contract_includes_hit_miss_stale_rates() -> None:
    summary = summarize_cache_event_rows(
        [
            {
                "endpoint": "daily",
                "hit_count": 3,
                "miss_count": 1,
                "stale_count": 1,
                "fetched_date_count": 2,
                "suppressed_no_data_count": 1,
            },
            {
                "endpoint": "cyq_chips",
                "hit_count": 0,
                "miss_count": 4,
                "stale_count": 1,
                "fetched_date_count": 5,
                "suppressed_no_data_count": 0,
            },
        ]
    )

    assert list(summary) == list(CACHE_EVENT_SUMMARY_KEYS)
    assert summary == {
        "cache_event_count": 2,
        "endpoint_count": 2,
        "endpoints": ["cyq_chips", "daily"],
        "request_count": 10,
        "hit_count": 3,
        "miss_count": 5,
        "hit_rate_percent": 30.0,
        "miss_rate_percent": 50.0,
        "stale_count": 2,
        "stale_rate_percent": 20.0,
        "fetched_date_count": 7,
        "suppressed_no_data_count": 1,
    }


def test_cache_event_summary_empty_contract_is_stable() -> None:
    assert summarize_cache_event_rows([]) == empty_cache_event_summary()
    assert list(empty_cache_event_summary()) == list(CACHE_EVENT_SUMMARY_KEYS)


def test_cache_event_summary_reads_jsonl_and_missing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing-cache-events.jsonl"
    assert summarize_cache_events_jsonl(missing_path) == empty_cache_event_summary()

    path = tmp_path / "cache-events.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"endpoint": "daily", "hit_count": 1, "miss_count": 0, "stale_count": 0}),
                "",
                json.dumps({"endpoint": "daily", "hit_count": 0, "miss_count": 0, "stale_count": 1}),
            ]
        ),
        encoding="utf-8",
    )

    assert summarize_cache_events_jsonl(path)["request_count"] == 2
    assert summarize_cache_events_jsonl(path)["stale_rate_percent"] == 50.0

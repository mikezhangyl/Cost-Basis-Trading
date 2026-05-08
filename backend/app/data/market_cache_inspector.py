from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


TABLES = {
    "current_entries": "market_cache_current_entries",
    "entry_versions": "market_cache_entry_versions",
    "conflicts": "market_cache_conflicts",
    "write_jobs": "cache_write_jobs",
}


def inspect_summary(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {
            "cache_path": str(cache_path),
            "exists": False,
            "totals": {name: 0 for name in TABLES},
            "by_endpoint": [],
            "jobs": {},
            "conflicts": {},
        }
    with _connect(cache_path) as connection:
        return {
            "cache_path": str(cache_path),
            "exists": True,
            "totals": {name: _count(connection, table) for name, table in TABLES.items()},
            "by_endpoint": _endpoint_counts(connection),
            "jobs": _group_counts(connection, "cache_write_jobs", "status"),
            "conflicts": _group_counts(connection, "market_cache_conflicts", "resolution"),
        }


def inspect_entries(
    cache_path: Path,
    endpoint: str | None = None,
    instrument_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not cache_path.exists():
        return []
    where = []
    params: list[Any] = []
    if endpoint:
        where.append("current.endpoint = ?")
        params.append(endpoint)
    if instrument_id:
        where.append("current.instrument_id = ?")
        params.append(instrument_id)
    params.append(_safe_limit(limit))
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with _connect(cache_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
              current.provider,
              current.endpoint,
              current.instrument_id,
              current.date_key,
              current.date_key_role,
              current.semantic_params_hash,
              current.current_version_id,
              current.current_payload_checksum,
              current.current_fetched_at,
              current.updated_at,
              versions.payload_kind,
              versions.cache_schema_version,
              versions.provider_updated_at,
              versions.superseded_at
            FROM market_cache_current_entries AS current
            LEFT JOIN market_cache_entry_versions AS versions
              ON versions.version_id = current.current_version_id
            {where_sql}
            ORDER BY current.endpoint, current.instrument_id, current.date_key, current.semantic_params_hash
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def inspect_jobs(cache_path: Path, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if not cache_path.exists():
        return []
    params: list[Any] = []
    where_sql = ""
    if status:
        where_sql = "WHERE status = ?"
        params.append(status)
    params.append(_safe_limit(limit))
    with _connect(cache_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
              job_id,
              status,
              provider,
              endpoint,
              instrument_id,
              date_key,
              date_key_role,
              semantic_params_hash,
              payload_checksum,
              payload_kind,
              fetched_at,
              provider_updated_at,
              cache_schema_version,
              attempt_count,
              next_attempt_at,
              last_error,
              created_at,
              updated_at
            FROM cache_write_jobs
            {where_sql}
            ORDER BY updated_at DESC, created_at DESC, job_id
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _connect(cache_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _endpoint_counts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
          current.endpoint,
          COUNT(*) AS current_entries,
          COUNT(DISTINCT current.instrument_id) AS instruments,
          MIN(current.date_key) AS min_date_key,
          MAX(current.date_key) AS max_date_key,
          SUM(CASE WHEN versions.payload_kind = 'ROWS' THEN 1 ELSE 0 END) AS row_entries,
          SUM(CASE WHEN versions.payload_kind = 'PROVISIONAL_NO_DATA' THEN 1 ELSE 0 END) AS provisional_no_data_entries,
          SUM(CASE WHEN versions.payload_kind = 'PERMANENT_NO_DATA' THEN 1 ELSE 0 END) AS permanent_no_data_entries
        FROM market_cache_current_entries AS current
        LEFT JOIN market_cache_entry_versions AS versions
          ON versions.version_id = current.current_version_id
        GROUP BY current.endpoint
        ORDER BY current.endpoint
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _group_counts(connection: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = connection.execute(
        f"""
        SELECT {column} AS key, COUNT(*) AS count
        FROM {table}
        GROUP BY {column}
        ORDER BY {column}
        """
    ).fetchall()
    return {str(row["key"]): int(row["count"]) for row in rows}


def _safe_limit(limit: int) -> int:
    return max(1, min(int(limit), 1000))

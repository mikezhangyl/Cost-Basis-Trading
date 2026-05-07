from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

CACHE_SCHEMA_VERSION = 1


class CachePayloadKind(StrEnum):
    ROWS = "ROWS"
    PROVISIONAL_NO_DATA = "PROVISIONAL_NO_DATA"
    PERMANENT_NO_DATA = "PERMANENT_NO_DATA"


@dataclass(frozen=True)
class CacheEntryIdentity:
    provider: str
    endpoint: str
    instrument_id: str
    date_key: str
    date_key_role: str
    semantic_params_hash: str


@dataclass(frozen=True)
class CacheKey:
    provider: str
    endpoint: str
    instrument_id: str
    date_key: str
    date_key_role: str
    semantic_params: dict[str, object]

    @property
    def semantic_params_hash(self) -> str:
        return build_semantic_params_hash(self.semantic_params)

    @property
    def identity(self) -> CacheEntryIdentity:
        return CacheEntryIdentity(
            provider=self.provider,
            endpoint=self.endpoint,
            instrument_id=self.instrument_id,
            date_key=self.date_key,
            date_key_role=self.date_key_role,
            semantic_params_hash=self.semantic_params_hash,
        )


@dataclass(frozen=True)
class CacheWrite:
    key: CacheKey
    payload_kind: CachePayloadKind
    payload: object
    source_params: dict[str, object]
    fetched_at: str
    provider_updated_at: str | None
    cache_schema_version: int

    @property
    def payload_checksum(self) -> str:
        return _checksum(self.payload)


@dataclass(frozen=True)
class CacheEntry:
    identity: CacheEntryIdentity
    version_id: str
    payload_kind: CachePayloadKind
    payload: object
    payload_checksum: str
    fetched_at: str
    provider_updated_at: str | None
    cache_schema_version: int


@dataclass(frozen=True)
class CacheLookupResult:
    hits: dict[CacheEntryIdentity, CacheEntry]
    misses: list[CacheEntryIdentity]
    stale: list[CacheEntryIdentity]


@dataclass(frozen=True)
class CacheUpsertResult:
    status: str
    version_id: str


@dataclass(frozen=True)
class EndpointContract:
    endpoint: str
    required_semantic_params: set[str]
    allowed_date_key_roles: set[str]
    allows_no_data: bool


class MarketCacheStore:
    def __init__(
        self,
        path: Path,
        recent_refresh_days: int = 10,
        provisional_no_data_ttl_seconds: int = 24 * 60 * 60,
    ) -> None:
        self.path = path
        self.recent_refresh_days = max(0, recent_refresh_days)
        self.provisional_no_data_ttl_seconds = max(0, provisional_no_data_ttl_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def upsert(self, write: CacheWrite, allow_provider_correction: bool = False) -> CacheUpsertResult:
        validate_cache_write(write)
        identity = write.key.identity
        now = _now_iso()
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            current = self._current_row(connection, identity)
            if current is None:
                version_id = self._insert_version(connection, write, supersedes_version_id=None, created_at=now)
                self._upsert_current(connection, identity, version_id, write.payload_checksum, write.fetched_at, now)
                return CacheUpsertResult(status="inserted", version_id=version_id)

            current_version_id = str(current["current_version_id"])
            current_checksum = str(current["current_payload_checksum"])
            current_version_valid = self._current_version_is_valid(
                connection,
                identity,
                current_version_id,
                current_checksum,
            )
            if not current_version_valid:
                version_id = self._insert_version(
                    connection,
                    write,
                    supersedes_version_id=current_version_id,
                    created_at=now,
                )
                self._upsert_current(connection, identity, version_id, write.payload_checksum, write.fetched_at, now)
                return CacheUpsertResult(status="repaired", version_id=version_id)

            if current_checksum == write.payload_checksum:
                version_id = current_version_id
                if write.payload_kind == CachePayloadKind.PROVISIONAL_NO_DATA:
                    self._refresh_current_version(connection, version_id, write.fetched_at, write.provider_updated_at)
                    self._upsert_current(connection, identity, version_id, write.payload_checksum, write.fetched_at, now)
                    return CacheUpsertResult(status="refreshed", version_id=version_id)
                return CacheUpsertResult(status="idempotent", version_id=version_id)

            if not allow_provider_correction:
                self._record_conflict(
                    connection=connection,
                    identity=identity,
                    previous_checksum=current_checksum,
                    incoming_checksum=write.payload_checksum,
                    previous_version_id=current_version_id,
                    incoming_version_id=None,
                    resolution="REJECTED",
                    created_at=now,
                )
                return CacheUpsertResult(status="conflict_rejected", version_id=current_version_id)

            previous_version_id = current_version_id
            version_id = self._insert_version(
                connection,
                write,
                supersedes_version_id=previous_version_id,
                created_at=now,
            )
            connection.execute(
                "UPDATE market_cache_entry_versions SET superseded_at = ? WHERE version_id = ?",
                (now, previous_version_id),
            )
            self._record_conflict(
                connection=connection,
                identity=identity,
                previous_checksum=current_checksum,
                incoming_checksum=write.payload_checksum,
                previous_version_id=previous_version_id,
                incoming_version_id=version_id,
                resolution="SUPERSEDED",
                created_at=now,
            )
            self._upsert_current(connection, identity, version_id, write.payload_checksum, write.fetched_at, now)
            return CacheUpsertResult(status="superseded", version_id=version_id)

    def read_many(
        self,
        keys: list[CacheKey],
        current_date: str | None = None,
        current_time: str | None = None,
    ) -> CacheLookupResult:
        hits: dict[CacheEntryIdentity, CacheEntry] = {}
        misses: list[CacheEntryIdentity] = []
        stale: list[CacheEntryIdentity] = []
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            for key in keys:
                identity = key.identity
                row = self._entry_row(connection, identity)
                if row is None:
                    misses.append(identity)
                    continue
                try:
                    payload = json.loads(str(row["payload_json"]))
                    payload_kind = CachePayloadKind(str(row["payload_kind"]))
                    cache_schema_version = int(row["cache_schema_version"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    misses.append(identity)
                    continue
                if cache_schema_version != CACHE_SCHEMA_VERSION or str(row["payload_checksum"]) != _checksum(payload):
                    misses.append(identity)
                    continue
                entry = CacheEntry(
                    identity=identity,
                    version_id=str(row["version_id"]),
                    payload_kind=payload_kind,
                    payload=payload,
                    payload_checksum=str(row["payload_checksum"]),
                    fetched_at=str(row["fetched_at"]),
                    provider_updated_at=row["provider_updated_at"],
                    cache_schema_version=cache_schema_version,
                )
                if self._is_stale(entry, current_date=current_date, current_time=current_time):
                    stale.append(identity)
                    continue
                hits[identity] = entry
        return CacheLookupResult(hits=hits, misses=misses, stale=stale)

    def enqueue_write_job(self, write: CacheWrite) -> str:
        validate_cache_write(write)
        job_id = uuid4().hex
        now = _now_iso()
        key = write.key
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO cache_write_jobs (
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
                  payload_json,
                  semantic_params_json,
                  source_params_json,
                  fetched_at,
                  provider_updated_at,
                  cache_schema_version,
                  attempt_count,
                  next_attempt_at,
                  last_error,
                  created_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "PENDING",
                    key.provider,
                    key.endpoint,
                    key.instrument_id,
                    key.date_key,
                    key.date_key_role,
                    key.semantic_params_hash,
                    write.payload_checksum,
                    write.payload_kind.value,
                    _canonical_json(write.payload),
                    _canonical_json(key.semantic_params),
                    _canonical_json(write.source_params),
                    write.fetched_at,
                    write.provider_updated_at,
                    write.cache_schema_version,
                    0,
                    None,
                    None,
                    now,
                    now,
                ),
            )
        return job_id

    def pending_jobs(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM cache_write_jobs WHERE status = ? ORDER BY created_at, job_id",
                ("PENDING",),
            ).fetchall()
        return [dict(row) for row in rows]

    def cache_write_from_job(self, job: dict[str, object]) -> CacheWrite:
        semantic_params = json.loads(str(job["semantic_params_json"]))
        source_params = json.loads(str(job["source_params_json"]))
        payload = json.loads(str(job["payload_json"]))
        key = CacheKey(
            provider=str(job["provider"]),
            endpoint=str(job["endpoint"]),
            instrument_id=str(job["instrument_id"]),
            date_key=str(job["date_key"]),
            date_key_role=str(job["date_key_role"]),
            semantic_params=semantic_params,
        )
        return CacheWrite(
            key=key,
            payload_kind=CachePayloadKind(str(job["payload_kind"])),
            payload=payload,
            source_params=source_params,
            fetched_at=str(job["fetched_at"]),
            provider_updated_at=job["provider_updated_at"],
            cache_schema_version=int(job["cache_schema_version"]),
        )

    def mark_job_succeeded(self, job_id: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                UPDATE cache_write_jobs
                SET status = ?, updated_at = ?
                WHERE job_id = ?
                """,
                ("SUCCEEDED", _now_iso(), job_id),
            )

    def mark_job_failed(self, job_id: str, error_message: str) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                UPDATE cache_write_jobs
                SET status = ?,
                    attempt_count = attempt_count + 1,
                    last_error = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                ("FAILED_PERMANENT", error_message, _now_iso(), job_id),
            )

    def count_pending_jobs(self) -> int:
        with sqlite3.connect(self.path) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM cache_write_jobs WHERE status = ?",
                    ("PENDING",),
                ).fetchone()[0]
            )

    def failed_jobs(self) -> list[dict[str, object]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM cache_write_jobs WHERE status = ? ORDER BY updated_at, job_id",
                ("FAILED_PERMANENT",),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_versions(self) -> int:
        with sqlite3.connect(self.path) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM market_cache_entry_versions").fetchone()[0])

    def count_conflicts(self) -> int:
        with sqlite3.connect(self.path) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM market_cache_conflicts").fetchone()[0])

    def version(self, version_id: str) -> dict[str, object]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM market_cache_entry_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return dict(row)

    def _initialize(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_cache_current_entries (
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  date_key_role TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  current_version_id TEXT NOT NULL,
                  current_payload_checksum TEXT NOT NULL,
                  current_fetched_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (
                    provider,
                    endpoint,
                    instrument_id,
                    date_key,
                    date_key_role,
                    semantic_params_hash
                  )
                );

                CREATE TABLE IF NOT EXISTS market_cache_entry_versions (
                  version_id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  date_key_role TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  semantic_params_json TEXT NOT NULL,
                  payload_kind TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  payload_checksum TEXT NOT NULL,
                  source_params_json TEXT NOT NULL,
                  fetched_at TEXT NOT NULL,
                  provider_updated_at TEXT,
                  cache_schema_version INTEGER NOT NULL,
                  supersedes_version_id TEXT,
                  superseded_at TEXT,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_cache_conflicts (
                  conflict_id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  date_key_role TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  previous_payload_checksum TEXT NOT NULL,
                  incoming_payload_checksum TEXT NOT NULL,
                  previous_version_id TEXT NOT NULL,
                  incoming_version_id TEXT,
                  resolution TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cache_write_jobs (
                  job_id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  instrument_id TEXT NOT NULL,
                  date_key TEXT NOT NULL,
                  date_key_role TEXT NOT NULL,
                  semantic_params_hash TEXT NOT NULL,
                  payload_checksum TEXT NOT NULL,
                  payload_kind TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  semantic_params_json TEXT NOT NULL,
                  source_params_json TEXT NOT NULL,
                  fetched_at TEXT NOT NULL,
                  provider_updated_at TEXT,
                  cache_schema_version INTEGER NOT NULL,
                  attempt_count INTEGER NOT NULL,
                  next_attempt_at TEXT,
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )

    def _current_row(self, connection: sqlite3.Connection, identity: CacheEntryIdentity) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM market_cache_current_entries
            WHERE provider = ?
              AND endpoint = ?
              AND instrument_id = ?
              AND date_key = ?
              AND date_key_role = ?
              AND semantic_params_hash = ?
            """,
            (
                identity.provider,
                identity.endpoint,
                identity.instrument_id,
                identity.date_key,
                identity.date_key_role,
                identity.semantic_params_hash,
            ),
        ).fetchone()

    def _entry_row(self, connection: sqlite3.Connection, identity: CacheEntryIdentity) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT versions.*
            FROM market_cache_current_entries AS current
            JOIN market_cache_entry_versions AS versions
              ON versions.version_id = current.current_version_id
             AND versions.provider = current.provider
             AND versions.endpoint = current.endpoint
             AND versions.instrument_id = current.instrument_id
             AND versions.date_key = current.date_key
             AND versions.date_key_role = current.date_key_role
             AND versions.semantic_params_hash = current.semantic_params_hash
            WHERE current.provider = ?
              AND current.endpoint = ?
              AND current.instrument_id = ?
              AND current.date_key = ?
              AND current.date_key_role = ?
              AND current.semantic_params_hash = ?
            """,
            (
                identity.provider,
                identity.endpoint,
                identity.instrument_id,
                identity.date_key,
                identity.date_key_role,
                identity.semantic_params_hash,
            ),
        ).fetchone()

    def _current_version_is_valid(
        self,
        connection: sqlite3.Connection,
        identity: CacheEntryIdentity,
        version_id: str,
        expected_checksum: str,
    ) -> bool:
        row = connection.execute(
            "SELECT * FROM market_cache_entry_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return False
        if (
            str(row["provider"]) != identity.provider
            or str(row["endpoint"]) != identity.endpoint
            or str(row["instrument_id"]) != identity.instrument_id
            or str(row["date_key"]) != identity.date_key
            or str(row["date_key_role"]) != identity.date_key_role
            or str(row["semantic_params_hash"]) != identity.semantic_params_hash
            or str(row["payload_checksum"]) != expected_checksum
        ):
            return False
        try:
            payload = json.loads(str(row["payload_json"]))
            cache_schema_version = int(row["cache_schema_version"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return cache_schema_version == CACHE_SCHEMA_VERSION and _checksum(payload) == expected_checksum

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        write: CacheWrite,
        supersedes_version_id: str | None,
        created_at: str,
    ) -> str:
        key = write.key
        version_id = uuid4().hex
        connection.execute(
            """
            INSERT INTO market_cache_entry_versions (
              version_id,
              provider,
              endpoint,
              instrument_id,
              date_key,
              date_key_role,
              semantic_params_hash,
              semantic_params_json,
              payload_kind,
              payload_json,
              payload_checksum,
              source_params_json,
              fetched_at,
              provider_updated_at,
              cache_schema_version,
              supersedes_version_id,
              superseded_at,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                key.provider,
                key.endpoint,
                key.instrument_id,
                key.date_key,
                key.date_key_role,
                key.semantic_params_hash,
                _canonical_json(key.semantic_params),
                write.payload_kind.value,
                _canonical_json(write.payload),
                write.payload_checksum,
                _canonical_json(write.source_params),
                write.fetched_at,
                write.provider_updated_at,
                write.cache_schema_version,
                supersedes_version_id,
                None,
                created_at,
            ),
        )
        return version_id

    def _refresh_current_version(
        self,
        connection: sqlite3.Connection,
        version_id: str,
        fetched_at: str,
        provider_updated_at: str | None,
    ) -> None:
        connection.execute(
            """
            UPDATE market_cache_entry_versions
            SET fetched_at = ?,
                provider_updated_at = ?
            WHERE version_id = ?
            """,
            (fetched_at, provider_updated_at, version_id),
        )

    def _upsert_current(
        self,
        connection: sqlite3.Connection,
        identity: CacheEntryIdentity,
        version_id: str,
        payload_checksum: str,
        fetched_at: str,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO market_cache_current_entries (
              provider,
              endpoint,
              instrument_id,
              date_key,
              date_key_role,
              semantic_params_hash,
              current_version_id,
              current_payload_checksum,
              current_fetched_at,
              updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, endpoint, instrument_id, date_key, date_key_role, semantic_params_hash)
            DO UPDATE SET
              current_version_id = excluded.current_version_id,
              current_payload_checksum = excluded.current_payload_checksum,
              current_fetched_at = excluded.current_fetched_at,
              updated_at = excluded.updated_at
            """,
            (
                identity.provider,
                identity.endpoint,
                identity.instrument_id,
                identity.date_key,
                identity.date_key_role,
                identity.semantic_params_hash,
                version_id,
                payload_checksum,
                fetched_at,
                updated_at,
            ),
        )

    def _record_conflict(
        self,
        connection: sqlite3.Connection,
        identity: CacheEntryIdentity,
        previous_checksum: str,
        incoming_checksum: str,
        previous_version_id: str,
        incoming_version_id: str | None,
        resolution: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO market_cache_conflicts (
              conflict_id,
              provider,
              endpoint,
              instrument_id,
              date_key,
              date_key_role,
              semantic_params_hash,
              previous_payload_checksum,
              incoming_payload_checksum,
              previous_version_id,
              incoming_version_id,
              resolution,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                identity.provider,
                identity.endpoint,
                identity.instrument_id,
                identity.date_key,
                identity.date_key_role,
                identity.semantic_params_hash,
                previous_checksum,
                incoming_checksum,
                previous_version_id,
                incoming_version_id,
                resolution,
                created_at,
            ),
        )

    def _is_stale(self, entry: CacheEntry, current_date: str | None, current_time: str | None) -> bool:
        if entry.payload_kind == CachePayloadKind.PERMANENT_NO_DATA:
            return False
        fetched_at = _parse_datetime(entry.fetched_at)
        if fetched_at is None:
            return True
        now = _parse_datetime(current_time) if current_time is not None else datetime.now(UTC)
        if now is None:
            now = datetime.now(UTC)
        if entry.payload_kind == CachePayloadKind.ROWS:
            return self._is_recent_row_stale(entry, fetched_at, current_date)
        age_seconds = (now - fetched_at).total_seconds()
        return age_seconds > self.provisional_no_data_ttl_seconds

    def _is_recent_row_stale(
        self,
        entry: CacheEntry,
        fetched_at: datetime,
        current_date: str | None,
    ) -> bool:
        if self.recent_refresh_days <= 0:
            return False
        current = _parse_date_key(current_date) if current_date is not None else datetime.now(UTC).date()
        entry_date = _parse_date_key(entry.identity.date_key)
        if current is None or entry_date is None:
            return False
        if entry_date > current:
            return True
        if (current - entry_date).days > self.recent_refresh_days:
            return False
        return fetched_at.date() < current


def endpoint_contract_for(endpoint: str) -> EndpointContract:
    contracts = {
        "cyq_chips": EndpointContract(
            endpoint="cyq_chips",
            required_semantic_params={"schema_version", "fields"},
            allowed_date_key_roles={"trade_date"},
            allows_no_data=True,
        ),
        "daily": EndpointContract(
            endpoint="daily",
            required_semantic_params={"schema_version", "fields", "price_adjustment", "asset", "freq"},
            allowed_date_key_roles={"trade_date"},
            allows_no_data=True,
        ),
        "adj_factor": EndpointContract(
            endpoint="adj_factor",
            required_semantic_params={"schema_version", "fields", "asset"},
            allowed_date_key_roles={"trade_date"},
            allows_no_data=True,
        ),
        "trade_cal": EndpointContract(
            endpoint="trade_cal",
            required_semantic_params={"schema_version", "exchange", "fields"},
            allowed_date_key_roles={"calendar_date"},
            allows_no_data=False,
        ),
        "stock_basic": EndpointContract(
            endpoint="stock_basic",
            required_semantic_params={"schema_version", "fields", "query_scope"},
            allowed_date_key_roles={"latest_snapshot", "snapshot_date"},
            allows_no_data=True,
        ),
        "derived_adjusted_bar": EndpointContract(
            endpoint="derived_adjusted_bar",
            required_semantic_params={
                "schema_version",
                "price_adjustment",
                "adjustment_anchor_date",
                "asset",
                "freq",
                "raw_price_checksum",
                "adj_factor_checksum",
            },
            allowed_date_key_roles={"trade_date"},
            allows_no_data=False,
        ),
    }
    if endpoint not in contracts:
        raise KeyError(f"Unknown market cache endpoint contract: {endpoint}")
    return contracts[endpoint]


def validate_cache_write(write: CacheWrite) -> None:
    key = write.key
    contract = endpoint_contract_for(key.endpoint)
    missing = contract.required_semantic_params - set(key.semantic_params)
    if missing:
        raise ValueError(f"Missing semantic params for {key.endpoint}: {sorted(missing)}")
    if key.date_key_role not in contract.allowed_date_key_roles:
        raise ValueError(
            f"Invalid date_key_role for {key.endpoint}: {key.date_key_role}. "
            f"Allowed roles: {sorted(contract.allowed_date_key_roles)}"
        )
    if write.payload_kind != CachePayloadKind.ROWS and not contract.allows_no_data:
        raise ValueError(f"Endpoint {key.endpoint} does not allow no-data cache payloads.")


def build_semantic_params_hash(semantic_params: dict[str, object]) -> str:
    return _checksum(semantic_params)


def _checksum(payload: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_current_date(value: str | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    return datetime.strptime(value, "%Y%m%d").date()


def _parse_date_key(value: str) -> date | None:
    if not re.fullmatch(r"\d{8}", value):
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)

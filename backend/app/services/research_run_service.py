import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.domain.models import (
    BacktestRequest,
    BacktestResponse,
    ResearchAggregateScore,
    ResearchObservationScore,
    ResearchRunRequest,
    ResearchRunResponse,
    ResearchSampleResult,
    ResearchStrategyScore,
    StrategySignal,
)
from app.services.backtest_service import OBSERVATION_OFFSETS, BacktestMarketDataClient, BacktestService
from app.services.code_normalizer import normalize_ts_code
from app.services.research_agent_client import ResearchAgentClient
from app.strategies.candidates import build_research_strategy_signals


class ResearchRunService:
    def __init__(
        self,
        market_data_client: BacktestMarketDataClient,
        artifact_root: Path | None = None,
        research_agent_client: ResearchAgentClient | None = None,
    ) -> None:
        self.market_data_client = market_data_client
        self.artifact_root = artifact_root or _default_artifact_root()
        self.research_agent_client = research_agent_client

    def run(self, request: ResearchRunRequest) -> ResearchRunResponse:
        run_id = _build_run_id()
        ts_code = normalize_ts_code(request.stock_code)
        run_dir = self.artifact_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        api_call_log = run_dir / "api-calls.jsonl"

        _write_json(
            run_dir / "run-config.json",
            {
                "run_id": run_id,
                "created_at": _now_iso(),
                "stock_code": request.stock_code,
                "ts_code": ts_code,
                "start_dates": request.start_dates,
                "window_days": request.window_days,
                "observation_offsets": OBSERVATION_OFFSETS,
                "candidate_strategy_ids": request.candidate_strategy_ids,
            },
        )

        samples = [
            self._run_sample(run_id, run_dir, api_call_log, ts_code, start_date, request)
            for start_date in request.start_dates
        ]
        aggregate_scores = _aggregate_scores(samples)
        stock_name = _stock_name_from_artifacts(run_dir, samples)
        ai_review = self._run_ai_research_agents(run_dir, run_id, ts_code, request, samples, aggregate_scores)

        response = ResearchRunResponse.create(
            run_id=run_id,
            ts_code=ts_code,
            stock_name=stock_name,
            window_days=request.window_days,
            observation_offsets=OBSERVATION_OFFSETS,
            artifact_dir=str(run_dir),
            aggregate_scores=aggregate_scores,
            samples=samples,
        )
        _write_json(
            run_dir / "run-manifest.json",
            {
                "run_id": run_id,
                "status": "completed",
                "created_at": _now_iso(),
                "sample_count": len(samples),
                "output_refs": ["run-config.json", "api-calls.jsonl", "run-manifest.json"],
                "aggregate_scores": [score.model_dump(mode="json") for score in aggregate_scores],
                "ai_review_status": ai_review["status"],
            },
        )
        return response

    def _run_ai_research_agents(
        self,
        run_dir: Path,
        run_id: str,
        ts_code: str,
        request: ResearchRunRequest,
        samples: list[ResearchSampleResult],
        aggregate_scores: list[ResearchAggregateScore],
    ) -> dict[str, Any]:
        payload = _build_ai_payload(run_id, ts_code, request, samples, aggregate_scores)
        aggregate_dir = run_dir / "aggregate"
        if self.research_agent_client is None:
            ai_review = {
                "status": "skipped",
                "reason": "research_agent_client_not_configured",
                "review_summary": "AI research agent was skipped because no agent client was configured.",
                "final_report": "AI research agent skipped. Deterministic scores and artifacts were still generated.",
                "agent_decisions": [],
            }
        else:
            try:
                ai_review = self.research_agent_client.analyze_research_run(payload)
            except Exception as error:
                ai_review = {
                    "status": "failed",
                    "reason": "research_agent_client_error",
                    "review_summary": "AI research agent failed; deterministic scores remain available.",
                    "final_report": "AI research agent failed. See ai_review.json for error metadata.",
                    "agent_decisions": [],
                    "error": str(error),
                }

        _write_json(aggregate_dir / "ai_review.json", _redact_sensitive_review(ai_review))
        _write_jsonl(
            aggregate_dir / "agent-decisions.jsonl",
            [
                {
                    "timestamp": _now_iso(),
                    "run_id": run_id,
                    "agent": decision.get("agent", "research-agent"),
                    "decision_type": decision.get("decision_type", "run_review"),
                    "reasoning_summary": decision.get("reasoning_summary", ""),
                    "status": ai_review.get("status", "unknown"),
                }
                for decision in ai_review.get("agent_decisions", [])
            ],
        )
        _write_text(aggregate_dir / "final_report.md", str(ai_review.get("final_report", "")))
        return ai_review

    def _run_sample(
        self,
        run_id: str,
        run_dir: Path,
        api_call_log: Path,
        ts_code: str,
        start_date: str,
        request: ResearchRunRequest,
    ) -> ResearchSampleResult:
        sample_id = f"{ts_code}-{start_date}-N{request.window_days}"
        sample_dir = run_dir / "samples" / sample_id
        logging_client = LoggingMarketDataClient(self.market_data_client, api_call_log, run_id, sample_id)
        backtest = BacktestService(logging_client).run(
            BacktestRequest(stock_code=ts_code, start_date=start_date, window_days=request.window_days)
        )

        feature_set = _build_feature_set(backtest)
        feature_dir = sample_dir / "features"
        signal_dir = sample_dir / "signals"
        backtest_dir = sample_dir / "backtest"
        _write_json(feature_dir / "feature_set.json", feature_set)
        _write_manifest(
            feature_dir / "manifest.json",
            run_dir=run_dir,
            run_id=run_id,
            sample_id=sample_id,
            stage="features",
            input_refs=["api-calls.jsonl"],
            output_refs=[f"samples/{sample_id}/features/feature_set.json"],
            row_counts=backtest.row_counts,
            date_coverage={
                "analysis_start": backtest.analysis_range["start_date"],
                "signal_date": backtest.signal_date,
                "future_offsets": OBSERVATION_OFFSETS,
            },
        )

        strategy_scores = [
            _score_strategy(strategy_id, signal, backtest)
            for strategy_id, signal in build_research_strategy_signals(
                backtest.signal,
                backtest.market_context,
                request.candidate_strategy_ids,
            )
        ]
        for strategy_score in strategy_scores:
            _write_json(
                signal_dir / f"signal_{strategy_score.strategy_id}.json",
                {
                    "run_id": run_id,
                    "sample_id": sample_id,
                    "strategy_id": strategy_score.strategy_id,
                    "status": "pending_backtest",
                    "signal": strategy_score.signal.model_dump(mode="json"),
                },
            )
        _write_jsonl(
            signal_dir / "agent_decision_log.jsonl",
            [
                {
                    "timestamp": _now_iso(),
                    "run_id": run_id,
                    "sample_id": sample_id,
                    "agent": "strategy-agent",
                    "decision_type": "rule_signal",
                    "strategy_id": strategy_score.strategy_id,
                    "action": strategy_score.signal.action,
                    "confidence": strategy_score.signal.confidence,
                    "reasoning_summary": strategy_score.signal.reasons[0] if strategy_score.signal.reasons else "",
                    "status": "pending_backtest",
                }
                for strategy_score in strategy_scores
            ],
        )
        _write_manifest(
            signal_dir / "manifest.json",
            run_dir=run_dir,
            run_id=run_id,
            sample_id=sample_id,
            stage="signals",
            input_refs=[f"samples/{sample_id}/features/feature_set.json"],
            output_refs=[f"samples/{sample_id}/signals/signal_{score.strategy_id}.json" for score in strategy_scores],
            row_counts={},
            date_coverage={"signal_date": backtest.signal_date},
        )

        _write_json(
            backtest_dir / "backtest_score.json",
            {
                "run_id": run_id,
                "sample_id": sample_id,
                "strategy_scores": [score.model_dump(mode="json") for score in strategy_scores],
            },
        )
        _write_manifest(
            backtest_dir / "manifest.json",
            run_dir=run_dir,
            run_id=run_id,
            sample_id=sample_id,
            stage="backtest",
            input_refs=[f"samples/{sample_id}/signals/manifest.json"],
            output_refs=[f"samples/{sample_id}/backtest/backtest_score.json"],
            row_counts={},
            date_coverage={"future_offsets": OBSERVATION_OFFSETS},
        )

        return ResearchSampleResult(
            sample_id=sample_id,
            start_date=start_date,
            signal_date=backtest.signal_date,
            status="completed",
            artifact_dir=str(sample_dir),
            strategies=strategy_scores,
        )


class LoggingMarketDataClient:
    def __init__(self, wrapped: BacktestMarketDataClient, api_call_log: Path, run_id: str, sample_id: str) -> None:
        self.wrapped = wrapped
        self.api_call_log = api_call_log
        self.run_id = run_id
        self.sample_id = sample_id

    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        return self._record(
            "trade_cal",
            {"start_date": start_date, "n_days": n_days},
            lambda: self.wrapped.resolve_trading_days_from(start_date, n_days),
        )

    def get_stock_name(self, ts_code: str) -> str | None:
        return self._record("stock_basic", {"ts_code": ts_code}, lambda: self.wrapped.get_stock_name(ts_code))

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[Any]:
        return self._record(
            "cyq_chips",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            lambda: self.wrapped.get_chip_distribution(ts_code, start_date, end_date),
        )

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[Any]:
        return self._record(
            "daily",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            lambda: self.wrapped.get_daily_prices(ts_code, start_date, end_date),
        )

    def _record(self, endpoint: str, params: dict[str, Any], call: Any) -> Any:
        started_at = perf_counter()
        try:
            result = call()
        except Exception as error:
            _append_jsonl(
                self.api_call_log,
                {
                    "timestamp": _now_iso(),
                    "run_id": self.run_id,
                    "sample_id": self.sample_id,
                    "source": "market_data_client",
                    "endpoint": endpoint,
                    "params": params,
                    "status": "ERROR",
                    "row_count": 0,
                    "duration_ms": int((perf_counter() - started_at) * 1000),
                    "error_message": str(error),
                },
            )
            raise
        _append_jsonl(
            self.api_call_log,
            {
                "timestamp": _now_iso(),
                "run_id": self.run_id,
                "sample_id": self.sample_id,
                "source": "market_data_client",
                "endpoint": endpoint,
                "params": params,
                "status": "OK",
                "row_count": len(result) if isinstance(result, list) else (1 if result else 0),
                "duration_ms": int((perf_counter() - started_at) * 1000),
                "error_message": None,
            },
        )
        return result


def _build_feature_set(backtest: BacktestResponse) -> dict[str, Any]:
    return {
        "ts_code": backtest.ts_code,
        "stock_name": backtest.stock_name,
        "analysis_range": backtest.analysis_range,
        "signal_date": backtest.signal_date,
        "baseline_signal_features": backtest.signal.features,
        "market_context": backtest.market_context.model_dump(mode="json"),
        "row_counts": backtest.row_counts,
    }


def _stock_name_from_artifacts(run_dir: Path, samples: list[ResearchSampleResult]) -> str | None:
    for sample in samples:
        feature_path = run_dir / "samples" / sample.sample_id / "features" / "feature_set.json"
        if feature_path.exists():
            stock_name = json.loads(feature_path.read_text(encoding="utf-8")).get("stock_name")
            if isinstance(stock_name, str):
                return stock_name
    return None


def _score_strategy(strategy_id: str, signal: StrategySignal, backtest: BacktestResponse) -> ResearchStrategyScore:
    observation_scores = [
        _score_observation(signal.action, observation.offset_days, observation.period_return)
        for observation in backtest.observations
    ]
    match_count = sum(1 for score in observation_scores if score.match_label == "MATCH")
    mismatch_count = sum(1 for score in observation_scores if score.match_label == "MISMATCH")
    neutral_count = sum(1 for score in observation_scores if score.match_label == "NEUTRAL")
    average_directional_score = sum(score.directional_score for score in observation_scores) / len(observation_scores)
    return ResearchStrategyScore(
        strategy_id=strategy_id,
        signal=signal,
        observation_scores=observation_scores,
        average_directional_score=average_directional_score,
        match_count=match_count,
        mismatch_count=mismatch_count,
        neutral_count=neutral_count,
    )


def _score_observation(action: str, offset_days: int, period_return: float) -> ResearchObservationScore:
    if action == "BUY":
        match_label = "MATCH" if period_return > 0 else "MISMATCH"
        directional_score = period_return
    elif action == "SELL":
        match_label = "MATCH" if period_return < 0 else "MISMATCH"
        directional_score = -period_return
    else:
        match_label = "NEUTRAL"
        directional_score = -abs(period_return)
    return ResearchObservationScore(
        offset_days=offset_days,
        period_return=period_return,
        match_label=match_label,
        directional_score=directional_score,
    )


def _aggregate_scores(samples: list[ResearchSampleResult]) -> list[ResearchAggregateScore]:
    grouped: dict[str, list[ResearchStrategyScore]] = defaultdict(list)
    for sample in samples:
        for score in sample.strategies:
            grouped[score.strategy_id].append(score)
    return [
        ResearchAggregateScore(
            strategy_id=strategy_id,
            sample_count=len(scores),
            average_directional_score=sum(score.average_directional_score for score in scores) / len(scores),
            match_count=sum(score.match_count for score in scores),
            mismatch_count=sum(score.mismatch_count for score in scores),
            neutral_count=sum(score.neutral_count for score in scores),
        )
        for strategy_id, scores in grouped.items()
    ]


def _build_ai_payload(
    run_id: str,
    ts_code: str,
    request: ResearchRunRequest,
    samples: list[ResearchSampleResult],
    aggregate_scores: list[ResearchAggregateScore],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "ts_code": ts_code,
        "window_days": request.window_days,
        "observation_offsets": OBSERVATION_OFFSETS,
        "sample_count": len(samples),
        "candidate_strategy_ids": request.candidate_strategy_ids,
        "aggregate_scores": [score.model_dump(mode="json") for score in aggregate_scores],
        "samples": [
            {
                "sample_id": sample.sample_id,
                "start_date": sample.start_date,
                "signal_date": sample.signal_date,
                "status": sample.status,
                "strategies": [
                    {
                        "strategy_id": strategy.strategy_id,
                        "action": strategy.signal.action,
                        "confidence": strategy.signal.confidence,
                        "reason": strategy.signal.reasons[0] if strategy.signal.reasons else "",
                        "average_directional_score": strategy.average_directional_score,
                        "match_count": strategy.match_count,
                        "mismatch_count": strategy.mismatch_count,
                        "neutral_count": strategy.neutral_count,
                        "observation_scores": [
                            observation.model_dump(mode="json")
                            for observation in strategy.observation_scores
                        ],
                    }
                    for strategy in sample.strategies
                ],
            }
            for sample in samples
        ],
    }


def _redact_sensitive_review(review: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in review.items() if key.lower() not in {"api_key", "token", "secret"}}


def _write_manifest(
    path: Path,
    *,
    run_dir: Path,
    run_id: str,
    sample_id: str,
    stage: str,
    input_refs: list[str],
    output_refs: list[str],
    row_counts: dict[str, int],
    date_coverage: dict[str, Any],
) -> None:
    _write_json(
        path,
        {
            "run_id": run_id,
            "sample_id": sample_id,
            "stage": stage,
            "status": "completed",
            "created_at": _now_iso(),
            "input_refs": input_refs,
            "output_refs": output_refs,
            "row_counts": row_counts,
            "date_coverage": date_coverage,
            "checksum": {ref: _sha256_for_path(run_dir / ref) for ref in output_refs},
            "error": None,
        },
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(row, ensure_ascii=False)}\n" for row in rows), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{json.dumps(row, ensure_ascii=False)}\n")


def _sha256_for_path(path: Path) -> str | None:
    if not path.exists():
        return None
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _default_artifact_root() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "research-runs"


def _build_run_id() -> str:
    return f"run-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()

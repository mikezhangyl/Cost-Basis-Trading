import hashlib
import json
import re
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
    ResearchAiReviewSummary,
    ResearchObservationScore,
    ResearchReportValidation,
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


SCORING_POLICY = {
    "match_label": {
        "BUY": "MATCH when forward return is greater than 0, otherwise MISMATCH.",
        "SELL": "MATCH when forward return is less than 0, otherwise MISMATCH.",
        "HOLD": "Always labeled NEUTRAL because HOLD makes no directional long or short claim.",
        "N/A": "Observation unavailable because there are not enough future trading days from the sample start.",
    },
    "directional_score": {
        "BUY": "forward return",
        "SELL": "negative forward return",
        "HOLD": "negative absolute forward return, used as opportunity/movement penalty only",
        "N/A": "excluded from average directional score",
    },
    "source": "Project scoring convention derived from the feature-set design note; not a paper threshold.",
}


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
        api_retry_log = run_dir / "api-retry-events.jsonl"
        cache_event_log = run_dir / "cache-events.jsonl"
        api_retry_log.touch()
        cache_event_log.touch()

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
            self._run_sample(
                run_id,
                run_dir,
                api_call_log,
                api_retry_log,
                cache_event_log,
                ts_code,
                start_date,
                request,
            )
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
            ai_review=_build_ai_review_summary(run_dir, ai_review),
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
                "output_refs": [
                    "run-config.json",
                    "api-calls.jsonl",
                    "api-retry-events.jsonl",
                    "cache-events.jsonl",
                    "run-manifest.json",
                ],
                "logged_market_data_call_count": _count_jsonl_rows(api_call_log),
                "api_retry_summary": _summarize_api_retries(api_retry_log),
                "cache_event_summary": _summarize_cache_events(cache_event_log),
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

        ai_review = _ensure_report_observation_coverage(ai_review, samples)
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
        api_retry_log: Path,
        cache_event_log: Path,
        ts_code: str,
        start_date: str,
        request: ResearchRunRequest,
    ) -> ResearchSampleResult:
        sample_id = f"{ts_code}-{start_date}-N{request.window_days}"
        sample_dir = run_dir / "samples" / sample_id
        logging_client = LoggingMarketDataClient(
            self.market_data_client,
            api_call_log,
            api_retry_log,
            cache_event_log,
            run_id,
            sample_id,
        )
        try:
            backtest = BacktestService(logging_client).run(
                BacktestRequest(stock_code=ts_code, start_date=start_date, window_days=request.window_days)
            )
        finally:
            logging_client.close()

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
    def __init__(
        self,
        wrapped: BacktestMarketDataClient,
        api_call_log: Path,
        api_retry_log: Path,
        cache_event_log: Path,
        run_id: str,
        sample_id: str,
    ) -> None:
        self.wrapped = wrapped
        self.api_call_log = api_call_log
        self.api_retry_log = api_retry_log
        self.cache_event_log = cache_event_log
        self.run_id = run_id
        self.sample_id = sample_id
        self.supports_adjustment_factors = callable(getattr(wrapped, "get_adjustment_factors", None))
        set_retry_event_handler = getattr(wrapped, "set_retry_event_handler", None)
        self.previous_retry_event_handler = getattr(wrapped, "retry_event_handler", None)
        if callable(set_retry_event_handler):
            set_retry_event_handler(self._record_retry_event)
        set_cache_event_handler = getattr(wrapped, "set_cache_event_handler", None)
        self.previous_cache_event_handler = getattr(wrapped, "cache_event_handler", None)
        if callable(set_cache_event_handler):
            set_cache_event_handler(self._record_cache_event)

    def close(self) -> None:
        set_retry_event_handler = getattr(self.wrapped, "set_retry_event_handler", None)
        if callable(set_retry_event_handler):
            set_retry_event_handler(self.previous_retry_event_handler)
        set_cache_event_handler = getattr(self.wrapped, "set_cache_event_handler", None)
        if callable(set_cache_event_handler):
            set_cache_event_handler(self.previous_cache_event_handler)

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

    def get_adjustment_factors(self, ts_code: str, start_date: str, end_date: str) -> list[Any]:
        get_adjustment_factors = getattr(self.wrapped, "get_adjustment_factors", None)
        if not callable(get_adjustment_factors):
            return []
        return self._record(
            "adj_factor",
            {"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
            lambda: get_adjustment_factors(ts_code, start_date, end_date),
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

    def _record_retry_event(self, event: dict[str, Any]) -> None:
        _append_jsonl(
            self.api_retry_log,
            {
                "timestamp": _now_iso(),
                "run_id": self.run_id,
                "sample_id": self.sample_id,
                "source": "market_data_client",
                **event,
            },
        )

    def _record_cache_event(self, event: dict[str, Any]) -> None:
        _append_jsonl(
            self.cache_event_log,
            {
                "timestamp": _now_iso(),
                "run_id": self.run_id,
                "sample_id": self.sample_id,
                "source": "market_data_cache",
                **event,
            },
        )


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
    unavailable_count = sum(1 for score in observation_scores if score.match_label == "N/A")
    directional_scores = [score.directional_score for score in observation_scores if score.directional_score is not None]
    average_directional_score = sum(directional_scores) / len(directional_scores) if directional_scores else 0
    return ResearchStrategyScore(
        strategy_id=strategy_id,
        signal=signal,
        observation_scores=observation_scores,
        average_directional_score=average_directional_score,
        match_count=match_count,
        mismatch_count=mismatch_count,
        neutral_count=neutral_count,
        unavailable_count=unavailable_count,
    )


def _score_observation(action: str, offset_days: int, period_return: float | None) -> ResearchObservationScore:
    if period_return is None:
        return ResearchObservationScore(
            offset_days=offset_days,
            period_return=None,
            match_label="N/A",
            directional_score=None,
        )
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
            unavailable_count=sum(score.unavailable_count for score in scores),
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
    observation_labels = _observation_labels()
    return {
        "run_id": run_id,
        "ts_code": ts_code,
        "window_days": request.window_days,
        "observation_offsets": OBSERVATION_OFFSETS,
        "observation_labels": observation_labels,
        "scoring_policy": SCORING_POLICY,
        "report_requirements": {
            "canonical_observation_labels": observation_labels,
            "observation_label_contract": (
                "最终报告必须逐项覆盖 canonical_observation_labels 中的每个观察点；"
                "即使某个观察点因为未来交易日不足为 N/A，也必须明确写出该标签和原因。"
            ),
            "traceability_contract": (
                "最终报告需要引用 artifact_refs 或每个 sample 的 artifact_refs，说明结论来自哪些"
                " feature/backtest/signal/API 调用产物；不能只给概括性判断。"
            ),
            "future_leak_contract": (
                "只能基于 signal_date 当日及之前窗口生成策略信号；未来观察点仅用于评分。"
                "若证据不足，必须说明 residual future-leak risk。"
            ),
            "investment_advice_contract": "只能做研究复核和实验建议，不提供真实个股投资建议。",
        },
        "artifact_refs": {
            "run_config": "run-config.json",
            "run_manifest": "run-manifest.json",
            "api_calls": "api-calls.jsonl",
            "cache_events": "cache-events.jsonl",
            "aggregate_ai_review": "aggregate/ai_review.json",
            "aggregate_agent_decisions": "aggregate/agent-decisions.jsonl",
            "aggregate_report": "aggregate/final_report.md",
        },
        "sample_count": len(samples),
        "candidate_strategy_ids": request.candidate_strategy_ids,
        "aggregate_scores": [score.model_dump(mode="json") for score in aggregate_scores],
        "samples": [
            {
                "sample_id": sample.sample_id,
                "start_date": sample.start_date,
                "signal_date": sample.signal_date,
                "status": sample.status,
                "artifact_refs": _sample_artifact_refs(sample),
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


def _observation_labels() -> list[str]:
    return [f"N+{offset}" for offset in OBSERVATION_OFFSETS]


def _sample_artifact_refs(sample: ResearchSampleResult) -> dict[str, Any]:
    sample_prefix = f"samples/{sample.sample_id}"
    return {
        "feature_set": f"{sample_prefix}/features/feature_set.json",
        "feature_manifest": f"{sample_prefix}/features/manifest.json",
        "signal_manifest": f"{sample_prefix}/signals/manifest.json",
        "strategy_decision_log": f"{sample_prefix}/signals/agent_decision_log.jsonl",
        "signal_files": [
            f"{sample_prefix}/signals/signal_{strategy.strategy_id}.json"
            for strategy in sample.strategies
        ],
        "backtest_score": f"{sample_prefix}/backtest/backtest_score.json",
        "backtest_manifest": f"{sample_prefix}/backtest/manifest.json",
    }


def _ensure_report_observation_coverage(
    ai_review: dict[str, Any],
    samples: list[ResearchSampleResult],
) -> dict[str, Any]:
    report = str(ai_review.get("final_report", ""))
    observation_labels = _observation_labels()
    missing_labels = [label for label in observation_labels if not _contains_observation_label(report, label)]
    validation = {
        "canonical_observation_labels": observation_labels,
        "missing_observation_labels": missing_labels,
        "status": "passed" if not missing_labels else "corrected",
    }
    if not missing_labels:
        return {**ai_review, "report_validation": validation}
    corrected_report = "\n\n".join(
        [
            report.strip(),
            _build_deterministic_observation_section(samples, observation_labels, missing_labels),
        ]
    ).strip()
    return {**ai_review, "final_report": corrected_report, "report_validation": validation}


def _build_deterministic_observation_section(
    samples: list[ResearchSampleResult],
    observation_labels: list[str],
    missing_labels: list[str],
) -> str:
    lines = [
        "## 确定性观察点覆盖校验",
        "",
        "AI 原始报告未逐项覆盖所有配置观察点，系统已追加本确定性覆盖摘要。",
        f"配置观察点：{' / '.join(observation_labels)}",
        f"原始报告缺失标签：{' / '.join(missing_labels)}",
        "",
        "样本观察点摘要：",
    ]
    for sample in samples:
        lines.append(f"- {sample.sample_id} signal_date={sample.signal_date}")
        for strategy in sample.strategies:
            observation_summary = ", ".join(
                f"N+{score.offset_days}={_format_observation_score(score)}"
                for score in strategy.observation_scores
            )
            lines.append(
                f"  - {strategy.strategy_id}: action={strategy.signal.action}, "
                f"confidence={strategy.signal.confidence:.2f}, {observation_summary}"
            )
    return "\n".join(lines)


def _format_observation_score(score: ResearchObservationScore) -> str:
    if score.period_return is None:
        return "N/A"
    return f"{score.match_label}({score.period_return:.2%})"


def _contains_observation_label(text: str, label: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(label)}(?!\d)", text) is not None


def _redact_sensitive_review(review: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in review.items() if key.lower() not in {"api_key", "token", "secret"}}


def _build_ai_review_summary(run_dir: Path, ai_review: dict[str, Any]) -> ResearchAiReviewSummary:
    aggregate_dir = run_dir / "aggregate"
    status = str(ai_review.get("status", "failed"))
    if status not in {"completed", "skipped", "failed"}:
        status = "failed"
    model = ai_review.get("model")
    return ResearchAiReviewSummary(
        status=status,
        model=model if isinstance(model, str) else None,
        summary=str(ai_review.get("review_summary") or "AI research review did not provide a summary."),
        report_validation=_build_report_validation(ai_review.get("report_validation")),
        artifact_refs={
            "review": str(aggregate_dir / "ai_review.json"),
            "decisions": str(aggregate_dir / "agent-decisions.jsonl"),
            "report": str(aggregate_dir / "final_report.md"),
        },
    )


def _build_report_validation(payload: Any) -> ResearchReportValidation | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if status not in {"passed", "corrected"}:
        return None
    return ResearchReportValidation(
        status=status,
        canonical_observation_labels=[
            str(label)
            for label in payload.get("canonical_observation_labels", [])
        ],
        missing_observation_labels=[
            str(label)
            for label in payload.get("missing_observation_labels", [])
        ],
    )


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


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _summarize_api_retries(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "retry_event_count": 0,
            "total_retry_event_count": 0,
            "retried_endpoint_count": 0,
            "retried_endpoints": [],
            "succeeded_after_retry_count": 0,
            "final_failure_count": 0,
            "had_retry": False,
        }
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    retry_events = [event for event in events if event.get("status") == "retrying"]
    final_failure_events = [event for event in events if event.get("status") == "failed"]
    succeeded_after_retry_events = [event for event in events if event.get("status") == "succeeded_after_retry"]
    retried_endpoints = sorted({str(event.get("endpoint")) for event in retry_events if event.get("endpoint")})
    return {
        "retry_event_count": len(retry_events),
        "total_retry_event_count": len(events),
        "retried_endpoint_count": len(retried_endpoints),
        "retried_endpoints": retried_endpoints,
        "succeeded_after_retry_count": len(succeeded_after_retry_events),
        "final_failure_count": len(final_failure_events),
        "had_retry": bool(retry_events),
    }


def _summarize_cache_events(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_cache_event_summary()
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    endpoints = sorted({str(event.get("endpoint")) for event in events if event.get("endpoint")})
    return {
        "cache_event_count": len(events),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
        "hit_count": _sum_event_int(events, "hit_count"),
        "miss_count": _sum_event_int(events, "miss_count"),
        "stale_count": _sum_event_int(events, "stale_count"),
        "fetched_date_count": _sum_event_int(events, "fetched_date_count"),
        "suppressed_no_data_count": _sum_event_int(events, "suppressed_no_data_count"),
    }


def _empty_cache_event_summary() -> dict[str, Any]:
    return {
        "cache_event_count": 0,
        "endpoint_count": 0,
        "endpoints": [],
        "hit_count": 0,
        "miss_count": 0,
        "stale_count": 0,
        "fetched_date_count": 0,
        "suppressed_no_data_count": 0,
    }


def _sum_event_int(events: list[dict[str, Any]], key: str) -> int:
    return sum(int(event.get(key) or 0) for event in events)


def _default_artifact_root() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "research-runs"


def _build_run_id() -> str:
    return f"run-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()

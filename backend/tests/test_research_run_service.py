import json
from pathlib import Path

from app.domain.models import ChipDistributionPoint, DailyPriceBar, ResearchRunRequest
from app.services.research_run_service import ResearchRunService


class FakeResearchRunClient:
    def resolve_trading_days_from(self, start_date: str, n_days: int) -> list[str]:
        dates = self._dates()
        start_index = dates.index(start_date)
        return dates[start_index : start_index + n_days]

    def get_stock_name(self, ts_code: str) -> str:
        return "平安银行"

    def get_chip_distribution(self, ts_code: str, start_date: str, end_date: str) -> list[ChipDistributionPoint]:
        dates = [date for date in self._dates() if start_date <= date <= end_date]
        rows: list[ChipDistributionPoint] = []
        for index, trade_date in enumerate(dates):
            rows.extend(
                [
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=10 + index * 0.1, percent=30),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=10.4 + index * 0.1, percent=45),
                    ChipDistributionPoint(ts_code=ts_code, trade_date=trade_date, price=10.8 + index * 0.1, percent=25),
                ]
            )
        return rows

    def get_daily_prices(self, ts_code: str, start_date: str, end_date: str) -> list[DailyPriceBar]:
        bars = []
        for index, trade_date in enumerate(self._dates()):
            close = 10 + index * 0.08
            bars.append(
                DailyPriceBar(
                    ts_code=ts_code,
                    trade_date=trade_date,
                    open=close - 0.03,
                    high=close + 0.08,
                    low=close - 0.06,
                    close=close,
                    vol=1000 + index * 30,
                    amount=(1000 + index * 30) * close,
                )
            )
        return [bar for bar in bars if start_date <= bar.trade_date <= end_date]

    def _dates(self) -> list[str]:
        return [
            "20260401",
            "20260402",
            "20260403",
            "20260407",
            "20260408",
            "20260409",
            "20260410",
            "20260413",
            "20260414",
            "20260415",
            "20260416",
            "20260417",
            "20260420",
            "20260421",
            "20260422",
        ]


class FakeResearchAgentClient:
    def analyze_research_run(self, payload: dict) -> dict:
        assert payload["ts_code"] == "000001.SZ"
        assert payload["sample_count"] == 2
        assert payload["observation_offsets"] == [1, 3, 5, 15, 30, 60, 90, 180]
        assert payload["scoring_policy"]["match_label"]["HOLD"].startswith("Always labeled NEUTRAL")
        assert payload["scoring_policy"]["match_label"]["N/A"].startswith("Observation unavailable")
        return {
            "status": "completed",
            "model": "fake-agent",
            "review_summary": "样本数量较少，当前只能作为流程验证。",
            "final_report": "本次研究流程完成，两个候选策略均已评分。",
            "agent_decisions": [
                {
                    "agent": "critic-agent",
                    "decision_type": "run_review",
                    "reasoning_summary": "未发现未来函数；样本覆盖仍不足。",
                },
                {
                    "agent": "report-agent",
                    "decision_type": "run_report",
                    "reasoning_summary": "生成最终研究摘要。",
                },
            ],
        }


def test_research_run_service_scores_strategies_and_writes_artifacts(tmp_path: Path) -> None:
    service = ResearchRunService(
        FakeResearchRunClient(),
        artifact_root=tmp_path,
        research_agent_client=FakeResearchAgentClient(),
    )

    result = service.run(
        ResearchRunRequest(
            stock_code="000001",
            start_dates=["20260401", "20260408"],
            window_days=5,
        )
    )

    assert result.ts_code == "000001.SZ"
    assert result.stock_name == "平安银行"
    assert result.sample_count == 2
    assert result.observation_offsets == [1, 3, 5, 15, 30, 60, 90, 180]
    assert {score.strategy_id for score in result.aggregate_scores} == {
        "composite_baseline",
        "market_context_followthrough",
    }
    assert all(sample.status == "completed" for sample in result.samples)
    assert all(len(sample.strategies) == 2 for sample in result.samples)
    assert result.ai_review.status == "completed"
    assert result.ai_review.model == "fake-agent"
    assert result.ai_review.summary == "样本数量较少，当前只能作为流程验证。"
    assert result.ai_review.artifact_refs == {
        "review": str(tmp_path / result.run_id / "aggregate" / "ai_review.json"),
        "decisions": str(tmp_path / result.run_id / "aggregate" / "agent-decisions.jsonl"),
        "report": str(tmp_path / result.run_id / "aggregate" / "final_report.md"),
    }

    run_dir = tmp_path / result.run_id
    assert run_dir.exists()
    assert (run_dir / "run-config.json").exists()
    assert (run_dir / "run-manifest.json").exists()
    assert (run_dir / "api-calls.jsonl").exists()
    assert (run_dir / "aggregate" / "agent-decisions.jsonl").exists()
    assert (run_dir / "aggregate" / "final_report.md").exists()

    first_sample_dir = run_dir / "samples" / result.samples[0].sample_id
    assert (first_sample_dir / "features" / "feature_set.json").exists()
    assert (first_sample_dir / "signals" / "signal_composite_baseline.json").exists()
    assert (first_sample_dir / "backtest" / "backtest_score.json").exists()

    feature_manifest = json.loads((first_sample_dir / "features" / "manifest.json").read_text())
    assert feature_manifest["status"] == "completed"
    assert feature_manifest["row_counts"]["price_bars"] == 5
    assert feature_manifest["row_counts"]["chip_points"] == 15

    score_payload = json.loads((first_sample_dir / "backtest" / "backtest_score.json").read_text())
    assert len(score_payload["strategy_scores"]) == 2
    assert score_payload["strategy_scores"][0]["observation_scores"][0]["offset_days"] == 1
    assert score_payload["strategy_scores"][0]["observation_scores"][-1]["match_label"] == "N/A"
    assert score_payload["strategy_scores"][0]["observation_scores"][-1]["period_return"] is None
    assert score_payload["strategy_scores"][0]["observation_scores"][-1]["directional_score"] is None
    assert score_payload["strategy_scores"][0]["unavailable_count"] == 5

    report_text = (run_dir / "aggregate" / "final_report.md").read_text()
    assert "本次研究流程完成" in report_text

    review_payload = json.loads((run_dir / "aggregate" / "ai_review.json").read_text())
    assert review_payload["status"] == "completed"
    assert review_payload["model"] == "fake-agent"


def test_research_run_service_records_skipped_ai_agent_when_unconfigured(tmp_path: Path) -> None:
    service = ResearchRunService(FakeResearchRunClient(), artifact_root=tmp_path)

    result = service.run(
        ResearchRunRequest(
            stock_code="000001",
            start_dates=["20260401"],
            window_days=5,
        )
    )

    review_payload = json.loads((tmp_path / result.run_id / "aggregate" / "ai_review.json").read_text())
    assert review_payload["status"] == "skipped"
    assert review_payload["reason"] == "research_agent_client_not_configured"
    assert result.ai_review.status == "skipped"
    assert result.ai_review.summary == "AI research agent was skipped because no agent client was configured."

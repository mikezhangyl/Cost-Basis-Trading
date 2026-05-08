import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { App } from "../src/App"

const scanResponse = {
  success: true,
  data: {
    scan_id: "scan-1",
    requested_at: "2026-04-28T15:00:00Z",
    n_days: 10,
    results: [
      {
        ts_code: "600519.SH",
        stock_name: "贵州茅台",
        date_range: { start_date: "20260413", end_date: "20260424" },
        signal: {
          strategy_name: "trend_confirmed_chip_signal",
          action: "BUY",
          confidence: 0.78,
          reasons: ["最新价向上突破主要筹码峰，且近 10 日涨幅为正。"],
          features: {
            latest_close: 112,
            n_day_return: 0.1089,
            dominant_peak_price: 100,
            weighted_chip_cost: 102.3,
            percent_below_close: 80
          }
        },
        strategy_signals: [],
        data_quality: { status: "OK", message: null, error_code: null },
        row_counts: { chip_points: 42, price_bars: 10 }
      }
    ]
  },
  error: null
}

const backtestResponse = {
  success: true,
  data: {
    backtest_id: "backtest-1",
    requested_at: "2026-04-28T15:00:00Z",
    ts_code: "600519.SH",
    stock_name: "贵州茅台",
    analysis_range: { start_date: "20260101", end_date: "20260114" },
    window_days: 10,
    signal_date: "20260114",
    signal: {
      strategy_name: "trend_confirmed_chip_signal",
      action: "BUY",
      confidence: 0.78,
      reasons: ["最新价向上突破主要筹码峰，且近 10 日涨幅为正。"],
      features: {
        latest_close: 1400,
        n_day_return: 0.04
      }
    },
    market_context: {
      price_return: 0.04,
      volume_ratio_5: 1.36,
      amount_ratio_5: 1.36,
      volume_trend: 0.5,
      close_vs_ma5: 0.0196,
      close_vs_ma10: 0.025,
      doji_count: 1,
      bullish_candle_count: 7,
      bearish_candle_count: 2,
      long_upper_shadow_count: 1,
      long_lower_shadow_count: 2,
      context_summary: "量能放大，价格站上 5 日均线，K 线结构偏多。"
    },
    observations: [
      {
        offset_days: 1,
        observation_date: "20260115",
        signal_close: 1400,
        observation_close: 1414,
        period_return: 0.01,
        match_label: "MATCH",
        interpretation: "N+1 上涨，买入建议得到阶段验证。"
      },
      {
        offset_days: 3,
        observation_date: "20260119",
        signal_close: 1400,
        observation_close: 1428,
        period_return: 0.02,
        match_label: "MATCH",
        interpretation: "N+3 上涨，买入建议得到阶段验证。"
      },
      {
        offset_days: 5,
        observation_date: "20260121",
        signal_close: 1400,
        observation_close: 1372,
        period_return: -0.02,
        match_label: "MISMATCH",
        interpretation: "N+5 下跌，买入建议阶段未得到验证。"
      },
      {
        offset_days: 15,
        observation_date: null,
        signal_close: 1400,
        observation_close: null,
        period_return: null,
        match_label: "N/A",
        interpretation: "N+15 未来交易日不足，暂无法观察。"
      }
    ],
    row_counts: { chip_points: 900, price_bars: 10 }
  },
  error: null
}

const marketCacheSummaryResponse = {
  success: true,
  data: {
    cache_path: "/tmp/market_data.sqlite3",
    exists: true,
    totals: {
      current_entries: 109,
      entry_versions: 109,
      conflicts: 0,
      write_jobs: 109
    },
    by_endpoint: [
      {
        endpoint: "cyq_chips",
        current_entries: 23,
        instruments: 1,
        min_date_key: "20260304",
        max_date_key: "20260403",
        row_entries: 23,
        provisional_no_data_entries: 0,
        permanent_no_data_entries: 0
      },
      {
        endpoint: "daily",
        current_entries: 23,
        instruments: 1,
        min_date_key: "20260304",
        max_date_key: "20260403",
        row_entries: 23,
        provisional_no_data_entries: 0,
        permanent_no_data_entries: 0
      }
    ],
    jobs: { SUCCEEDED: 109 },
    conflicts: {}
  },
  error: null
}

const researchRunResponse = {
  success: true,
  data: {
    run_id: "run-test-1",
    requested_at: "2026-04-29T15:00:00Z",
    ts_code: "000001.SZ",
    stock_name: "平安银行",
    window_days: 10,
    observation_offsets: [1, 3, 5, 15, 30, 60, 90, 180],
    sample_count: 2,
    artifact_dir: "docs/research-runs/run-test-1",
    cache_event_summary: {
      cache_event_count: 5,
      endpoint_count: 3,
      endpoints: ["cyq_chips", "daily", "trade_cal"],
      request_count: 171,
      hit_count: 120,
      miss_count: 46,
      hit_rate_percent: 70.18,
      miss_rate_percent: 26.9,
      stale_count: 5,
      stale_rate_percent: 2.92,
      fetched_date_count: 46,
      suppressed_no_data_count: 0
    },
    ai_review: {
      status: "completed",
      model: "deepseek-v4-pro",
      summary: "未发现未来函数风险，但样本数量较少。",
      report_validation: {
        status: "corrected",
        canonical_observation_labels: ["N+1", "N+3", "N+5", "N+15", "N+30", "N+60", "N+90", "N+180"],
        missing_observation_labels: ["N+15", "N+30"]
      },
      artifact_refs: {
        review: "docs/research-runs/run-test-1/aggregate/ai_review.json",
        decisions: "docs/research-runs/run-test-1/aggregate/agent-decisions.jsonl",
        report: "docs/research-runs/run-test-1/aggregate/final_report.md"
      }
    },
    aggregate_scores: [
      {
        strategy_id: "composite_baseline",
        sample_count: 2,
        average_directional_score: 0.012,
        match_count: 3,
        mismatch_count: 1,
        neutral_count: 2,
        unavailable_count: 10,
      },
      {
        strategy_id: "market_context_followthrough",
        sample_count: 2,
        average_directional_score: 0.018,
        match_count: 4,
        mismatch_count: 1,
        neutral_count: 1,
        unavailable_count: 10,
      }
    ],
    samples: [
      {
        sample_id: "000001.SZ-20260301-N10",
        start_date: "20260301",
        signal_date: "20260313",
        status: "completed",
        artifact_dir: "docs/research-runs/run-test-1/samples/000001.SZ-20260301-N10",
        strategies: [
          {
            strategy_id: "composite_baseline",
            signal: {
              strategy_name: "trend_confirmed_chip_signal",
              action: "HOLD",
              confidence: 0.5,
              reasons: ["筹码与价格趋势混合，暂不确认方向优势。"],
              features: { latest_close: 10.9 }
            },
            observation_scores: [
              { offset_days: 1, period_return: -0.001, match_label: "NEUTRAL", directional_score: -0.001 },
              { offset_days: 3, period_return: 0.003, match_label: "NEUTRAL", directional_score: -0.003 },
              { offset_days: 5, period_return: -0.014, match_label: "NEUTRAL", directional_score: -0.014 },
              { offset_days: 15, period_return: null, match_label: "N/A", directional_score: null },
              { offset_days: 30, period_return: 0.0527, match_label: "NEUTRAL", directional_score: -0.0527 },
              { offset_days: 60, period_return: 0.1227, match_label: "NEUTRAL", directional_score: -0.1227 },
              { offset_days: 90, period_return: 0.0655, match_label: "NEUTRAL", directional_score: -0.0655 },
              { offset_days: 180, period_return: 0.0064, match_label: "NEUTRAL", directional_score: -0.0064 }
            ],
            average_directional_score: -0.006,
            match_count: 0,
            mismatch_count: 0,
            neutral_count: 3,
            unavailable_count: 1,
          }
        ]
      }
    ]
  },
  error: null
}

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => scanResponse
      })
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("submits manual stock codes and renders scan signals", async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.clear(screen.getByLabelText("Stock codes"))
    await user.type(screen.getByLabelText("Stock codes"), "600519")
    await user.click(screen.getByRole("button", { name: /run scan/i }))

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/scans",
      expect.objectContaining({
        method: "POST"
      })
    ))
    expect(screen.getByLabelText("Scan log")).toBeInTheDocument()
    expect(screen.getByText("请求 Tushare 交易日历、筹码明细和日线行情。")).toBeInTheDocument()
    expect(await screen.findByText("600519.SH")).toBeInTheDocument()
    expect(screen.getByText("BUY")).toBeInTheDocument()
    expect(screen.getByText("78%")).toBeInTheDocument()
    expect(screen.getByText(/主要筹码峰/)).toBeInTheDocument()
    expect(screen.getByText("600519.SH：已获取 42 条筹码明细，信号 BUY。")).toBeInTheDocument()
  })

  it("renders per-stock data quality errors", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        success: true,
        data: {
          ...scanResponse.data,
          results: [
            {
              ...scanResponse.data.results[0],
              signal: { ...scanResponse.data.results[0].signal, action: "HOLD", confidence: 0, reasons: ["No cyq_chips rows returned."] },
              data_quality: { status: "ERROR", message: "No cyq_chips rows returned.", error_code: "EMPTY_DATA" }
            }
          ]
        },
        error: null
      })
    } as Response)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: /run scan/i }))

    expect(await screen.findByText("EMPTY_DATA")).toBeInTheDocument()
    expect(screen.getByText("No cyq_chips rows returned.")).toBeInTheDocument()
  })

  it("runs a backtest and renders multi-horizon validation", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => backtestResponse
    } as Response)
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByLabelText("Backtest stock code")).toHaveValue("000001")
    await user.click(screen.getByRole("button", { name: /run backtest/i }))

    expect(await screen.findByText("BUY")).toBeInTheDocument()
    expect(screen.getByText("78%")).toBeInTheDocument()
    expect(screen.getAllByText("+1.00%").length).toBeGreaterThan(0)
    expect(screen.getAllByText("+2.00%").length).toBeGreaterThan(0)
    expect(screen.getAllByText("-2.00%").length).toBeGreaterThan(0)
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0)
    expect(screen.getByText("600519.SH 贵州茅台")).toBeInTheDocument()
    expect(screen.getByText(/分析区间：20260101 至 20260114/)).toBeInTheDocument()
    expect(screen.getAllByText("匹配")).toHaveLength(2)
    expect(screen.getByText("不匹配")).toBeInTheDocument()
    expect(screen.getByText("量价与K线确认")).toBeInTheDocument()
    expect(screen.getByText("量能放大，价格站上 5 日均线，K 线结构偏多。")).toBeInTheDocument()
    expect(screen.getByText("量比5日")).toBeInTheDocument()
    expect(screen.getAllByText("1.36x").length).toBeGreaterThan(0)
    expect(screen.getByText("阳线/阴线")).toBeInTheDocument()
    expect(screen.getByText("7 / 2")).toBeInTheDocument()
    expect(screen.getByText("N+5 下跌，买入建议阶段未得到验证。")).toBeInTheDocument()
    expect(screen.getByText("N+15 未来交易日不足，暂无法观察。")).toBeInTheDocument()
  })

  it("loads market cache health without exposing cached payloads", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => marketCacheSummaryResponse
    } as Response)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: /refresh cache/i }))

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/market-cache/summary"))
    const cacheHealth = await screen.findByLabelText("Market cache health")
    expect(within(cacheHealth).getByText("109")).toBeInTheDocument()
    expect(within(cacheHealth).getByText("cyq_chips")).toBeInTheDocument()
    expect(within(cacheHealth).getByText("daily")).toBeInTheDocument()
    expect(within(cacheHealth).getByText("SUCCEEDED: 109")).toBeInTheDocument()
    expect(screen.queryByText("payload_json")).not.toBeInTheDocument()
  })

  it("runs a research workflow and renders strategy scores with artifact path", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => researchRunResponse
    } as Response)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: /run research/i }))

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/research-runs",
      expect.objectContaining({
        method: "POST"
      })
    ))
    expect(await screen.findByText("run-test-1")).toBeInTheDocument()
    expect(screen.getByText("000001.SZ 平安银行")).toBeInTheDocument()
    expect(screen.getByText("2 samples / 10 days")).toBeInTheDocument()
    expect(screen.getAllByText("N+1 / N+3 / N+5 / N+15 / N+30 / N+60 / N+90 / N+180").length).toBeGreaterThan(0)
    expect(screen.getByText("market_context_followthrough")).toBeInTheDocument()
    expect(screen.getByText("+1.80%")).toBeInTheDocument()
    expect(screen.getByText("docs/research-runs/run-test-1")).toBeInTheDocument()
    expect(screen.getByText("AI agent review")).toBeInTheDocument()
    const cacheUsage = screen.getByLabelText("Cache usage")
    expect(within(cacheUsage).getByText("Cache usage")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("70.18%")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("120 / 171")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("Miss")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("46 / 26.9%")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("Stale")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("5 / 2.92%")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("cyq_chips / daily / trade_cal")).toBeInTheDocument()
    expect(screen.getByText("completed / deepseek-v4-pro")).toBeInTheDocument()
    expect(screen.getByText("Report validation")).toBeInTheDocument()
    expect(screen.getByText("corrected")).toBeInTheDocument()
    expect(screen.getByText("N+15 / N+30")).toBeInTheDocument()
    expect(screen.getByText("未发现未来函数风险，但样本数量较少。")).toBeInTheDocument()
    expect(screen.getByText("docs/research-runs/run-test-1/aggregate/final_report.md")).toBeInTheDocument()
    const coverage = screen.getByLabelText("Observation coverage")
    expect(within(coverage).getByText("Observation coverage")).toBeInTheDocument()
    for (const offset of ["N+1", "N+3", "N+5", "N+15", "N+30", "N+60", "N+90", "N+180"]) {
      expect(within(coverage).getByRole("columnheader", { name: offset })).toBeInTheDocument()
    }
    expect(screen.getByText("000001.SZ-20260301-N10 / composite_baseline")).toBeInTheDocument()
    expect(screen.getByText("-0.10%")).toBeInTheDocument()
    expect(screen.getByText("+12.27%")).toBeInTheDocument()
    expect(screen.getAllByText("NEUTRAL").length).toBeGreaterThan(0)
  })

  it("shows a readable error when research workflow returns an empty response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 502,
      text: async () => ""
    } as Response)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: /run research/i }))

    expect(await screen.findByText("Research run request failed: empty response from server.")).toBeInTheDocument()
  })

  it("renders a safe zero cache panel for older research responses without cache summary", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ...researchRunResponse,
        data: {
          ...researchRunResponse.data,
          cache_event_summary: undefined
        }
      })
    } as Response)
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole("button", { name: /run research/i }))

    const cacheUsage = await screen.findByLabelText("Cache usage")
    expect(within(cacheUsage).getByText("0%")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("0 / 0")).toBeInTheDocument()
    expect(within(cacheUsage).getByText("No cache endpoints recorded")).toBeInTheDocument()
  })
})

import { render, screen, waitFor } from "@testing-library/react"
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
      }
    ],
    row_counts: { chip_points: 900, price_bars: 10 }
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
  })
})

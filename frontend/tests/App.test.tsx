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
})

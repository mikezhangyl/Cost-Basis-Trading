export type SignalAction = "BUY" | "HOLD" | "SELL"

export type StrategySignal = {
  strategy_name: string
  action: SignalAction
  confidence: number
  reasons: string[]
  features: Record<string, number | string | null>
}

export type StockScanResult = {
  ts_code: string
  stock_name: string | null
  date_range: {
    start_date: string | null
    end_date: string | null
  }
  signal: StrategySignal
  data_quality: {
    status: "OK" | "WARNING" | "ERROR"
    message: string | null
    error_code: string | null
  }
  row_counts: Record<string, number>
}

export type ScanResponse = {
  scan_id: string
  requested_at: string
  n_days: number
  results: StockScanResult[]
}

export type BacktestResponse = {
  backtest_id: string
  requested_at: string
  ts_code: string
  stock_name: string | null
  analysis_range: {
    start_date: string
    end_date: string
  }
  window_days: number
  signal_date: string
  signal: StrategySignal
  market_context: {
    price_return: number | null
    volume_ratio_5: number | null
    amount_ratio_5: number | null
    volume_trend: number | null
    close_vs_ma5: number | null
    close_vs_ma10: number | null
    doji_count: number
    bullish_candle_count: number
    bearish_candle_count: number
    long_upper_shadow_count: number
    long_lower_shadow_count: number
    context_summary: string
  }
  observations: Array<{
    offset_days: number
    observation_date: string | null
    signal_close: number
    observation_close: number | null
    period_return: number | null
    match_label: "MATCH" | "MISMATCH" | "NEUTRAL" | "N/A"
    interpretation: string
  }>
  row_counts: Record<string, number>
}

export type CacheEventSummary = {
  cache_event_count: number
  endpoint_count: number
  endpoints: string[]
  request_count: number
  hit_count: number
  miss_count: number
  hit_rate_percent: number
  miss_rate_percent: number
  stale_count: number
  stale_rate_percent: number
  fetched_date_count: number
  suppressed_no_data_count: number
}

export type ResearchRunResponse = {
  run_id: string
  requested_at: string
  ts_code: string
  stock_name: string | null
  window_days: number
  observation_offsets: number[]
  sample_count: number
  artifact_dir: string
  cache_event_summary?: Partial<CacheEventSummary> | null
  ai_review: {
    status: "completed" | "skipped" | "failed"
    model: string | null
    summary: string
    report_validation: {
      status: "passed" | "corrected"
      canonical_observation_labels: string[]
      missing_observation_labels: string[]
    } | null
    artifact_refs: {
      review: string
      decisions: string
      report: string
    }
  }
  aggregate_scores: Array<{
    strategy_id: string
    sample_count: number
    average_directional_score: number
    match_count: number
    mismatch_count: number
    neutral_count: number
    unavailable_count: number
  }>
  samples: Array<{
    sample_id: string
    start_date: string
    signal_date: string
    status: "completed" | "invalid" | "failed"
    artifact_dir: string
    strategies: Array<{
      strategy_id: string
      signal: StrategySignal
      observation_scores: Array<{
        offset_days: number
        period_return: number | null
        match_label: "MATCH" | "MISMATCH" | "NEUTRAL" | "N/A"
        directional_score: number | null
      }>
      average_directional_score: number
      match_count: number
      mismatch_count: number
      neutral_count: number
      unavailable_count: number
    }>
  }>
}

type ApiEnvelope<T> = {
  success: boolean
  data: T | null
  error: string | null
}

async function parseEnvelope<T>(response: Response, fallbackMessage: string): Promise<T> {
  const envelope = await readEnvelope<T>(response, fallbackMessage)
  if (!response.ok || !envelope.success || !envelope.data) {
    throw new Error(envelope.error ?? fallbackMessage)
  }
  return envelope.data
}

async function readEnvelope<T>(response: Response, fallbackMessage: string): Promise<ApiEnvelope<T>> {
  const messagePrefix = fallbackMessage.replace(/[.。]+$/, "")
  if (typeof response.text === "function") {
    const payload = await response.text()
    if (!payload.trim()) {
      throw new Error(`${messagePrefix}: empty response from server.`)
    }
    try {
      return JSON.parse(payload) as ApiEnvelope<T>
    } catch {
      throw new Error(`${messagePrefix}: server returned non-JSON response.`)
    }
  }
  return (await response.json()) as ApiEnvelope<T>
}

export async function runScan(stockCodes: string[], nDays: number): Promise<ScanResponse> {
  const response = await fetch("/api/scans", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      stock_codes: stockCodes,
      n_days: nDays
    })
  })
  return parseEnvelope<ScanResponse>(response, "Scan request failed.")
}

export async function runBacktest(params: {
  stockCode: string
  startDate: string
  windowDays: number
}): Promise<BacktestResponse> {
  const response = await fetch("/api/backtests", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      stock_code: params.stockCode,
      start_date: params.startDate,
      window_days: params.windowDays
    })
  })
  return parseEnvelope<BacktestResponse>(response, "Backtest request failed.")
}

export async function runResearchRun(params: {
  stockCode: string
  startDates: string[]
  windowDays: number
}): Promise<ResearchRunResponse> {
  const response = await fetch("/api/research-runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      stock_code: params.stockCode,
      start_dates: params.startDates,
      window_days: params.windowDays
    })
  })
  return parseEnvelope<ResearchRunResponse>(response, "Research run request failed.")
}

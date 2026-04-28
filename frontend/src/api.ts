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
  observations: Array<{
    offset_days: number
    observation_date: string
    signal_close: number
    observation_close: number
    period_return: number
    match_label: "MATCH" | "MISMATCH" | "NEUTRAL"
    interpretation: string
  }>
  row_counts: Record<string, number>
}

type ApiEnvelope<T> = {
  success: boolean
  data: T | null
  error: string | null
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
  const envelope = (await response.json()) as ApiEnvelope<ScanResponse>
  if (!response.ok || !envelope.success || !envelope.data) {
    throw new Error(envelope.error ?? "Scan request failed.")
  }
  return envelope.data
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
  const envelope = (await response.json()) as ApiEnvelope<BacktestResponse>
  if (!response.ok || !envelope.success || !envelope.data) {
    throw new Error(envelope.error ?? "Backtest request failed.")
  }
  return envelope.data
}

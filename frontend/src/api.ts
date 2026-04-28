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

export type BacktestTrade = {
  trade_date: string
  action: "BUY" | "SELL"
  price: number
  shares: number
  cash_after: number
  reason: string
}

export type BacktestResponse = {
  backtest_id: string
  requested_at: string
  ts_code: string
  stock_name: string | null
  date_range: {
    start_date: string
    end_date: string
  }
  n_days: number
  summary: {
    initial_cash: number
    final_value: number
    total_return: number
    benchmark_return: number
    max_drawdown: number
    trade_count: number
    signal_count: number
  }
  trades: BacktestTrade[]
  equity_curve: Array<{
    trade_date: string
    close: number
    cash: number
    shares: number
    portfolio_value: number
    signal_action: SignalAction
  }>
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
  endDate: string
  nDays: number
  initialCash: number
}): Promise<BacktestResponse> {
  const response = await fetch("/api/backtests", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      stock_code: params.stockCode,
      start_date: params.startDate,
      end_date: params.endDate,
      n_days: params.nDays,
      initial_cash: params.initialCash
    })
  })
  const envelope = (await response.json()) as ApiEnvelope<BacktestResponse>
  if (!response.ok || !envelope.success || !envelope.data) {
    throw new Error(envelope.error ?? "Backtest request failed.")
  }
  return envelope.data
}

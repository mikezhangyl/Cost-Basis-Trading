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

import { Activity, AlertTriangle, BarChart3, CheckCircle2, History, Loader2, Play, ShieldCheck } from "lucide-react"
import { FormEvent, useMemo, useState } from "react"

import { BacktestResponse, runBacktest, runScan, ScanResponse, StockScanResult } from "./api"

const defaultCodes = "600519\n000001"

export function App() {
  const [stockInput, setStockInput] = useState(defaultCodes)
  const [nDays, setNDays] = useState(10)
  const [scan, setScan] = useState<ScanResponse | null>(null)
  const [scanLogs, setScanLogs] = useState<string[]>([])
  const [backtestCode, setBacktestCode] = useState("600519")
  const [backtestStart, setBacktestStart] = useState("20260101")
  const [backtestEnd, setBacktestEnd] = useState("20260428")
  const [initialCash, setInitialCash] = useState(100000)
  const [backtest, setBacktest] = useState<BacktestResponse | null>(null)
  const [backtestError, setBacktestError] = useState<string | null>(null)
  const [isBacktesting, setIsBacktesting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  const stockCodes = useMemo(
    () =>
      stockInput
        .split(/[\n,，\s]+/)
        .map((code) => code.trim())
        .filter(Boolean),
    [stockInput]
  )

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsLoading(true)
    setError(null)
    setScanLogs([
      `解析输入：${stockCodes.length} 只股票，窗口 ${nDays} 个交易日。`,
      "连接本地扫描服务。",
      "请求 Tushare 交易日历、筹码明细和日线行情。"
    ])
    try {
      const nextScan = await runScan(stockCodes, nDays)
      setScan(nextScan)
      setScanLogs([
        `解析输入：${stockCodes.length} 只股票，窗口 ${nDays} 个交易日。`,
        "连接本地扫描服务。",
        "请求 Tushare 交易日历、筹码明细和日线行情。",
        ...nextScan.results.map((result) =>
          `${result.ts_code}：${result.data_quality.status === "OK" ? "已获取" : "获取异常"} ${result.row_counts.chip_points ?? 0} 条筹码明细，信号 ${result.signal.action}。`
        ),
        `扫描完成：${nextScan.results.length} 只股票。`
      ])
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Scan request failed.")
      setScanLogs((currentLogs) => [...currentLogs, "扫描失败，请检查后端服务、Tushare token 或接口权限。"])
    } finally {
      setIsLoading(false)
    }
  }

  async function handleBacktest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsBacktesting(true)
    setBacktestError(null)
    try {
      const nextBacktest = await runBacktest({
        stockCode: backtestCode,
        startDate: backtestStart,
        endDate: backtestEnd,
        nDays,
        initialCash
      })
      setBacktest(nextBacktest)
    } catch (caught) {
      setBacktestError(caught instanceof Error ? caught.message : "Backtest request failed.")
    } finally {
      setIsBacktesting(false)
    }
  }

  return (
    <main className="app-shell">
      <section className="scanner-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Local Tushare workstation</p>
            <h1>Cost Basis Trading</h1>
          </div>
          <div className="status-pill">
            <ShieldCheck size={16} />
            Research signals
          </div>
        </div>

        <form className="scan-form" onSubmit={handleSubmit}>
          <label className="field">
            <span>Stock codes</span>
            <textarea
              aria-label="Stock codes"
              value={stockInput}
              rows={5}
              spellCheck={false}
              onChange={(event) => setStockInput(event.target.value)}
            />
          </label>
          <label className="field compact-field">
            <span>Trading days</span>
            <input
              aria-label="Trading days"
              type="number"
              min={1}
              max={120}
              value={nDays}
              onChange={(event) => setNDays(Number(event.target.value))}
            />
          </label>
          <button className="primary-action" disabled={isLoading || stockCodes.length === 0} type="submit">
            <Play size={16} />
            {isLoading ? "Scanning" : "Run scan"}
          </button>
        </form>

        {error ? (
          <div className="error-banner" role="alert">
            <AlertTriangle size={18} />
            {error}
          </div>
        ) : null}

        <ScanLogPanel logs={scanLogs} isLoading={isLoading} />
      </section>

      <section className="results-panel">
        <div className="results-heading">
          <div>
            <p className="eyebrow">Phase 1 signal list</p>
            <h2>Signals</h2>
          </div>
          <div className="scan-meta">
            <Activity size={16} />
            {scan ? `${scan.results.length} stocks / ${scan.n_days} days` : `${stockCodes.length} queued / ${nDays} days`}
          </div>
        </div>

        {scan ? <SignalTable results={scan.results} /> : <EmptyState />}
      </section>

      <section className="backtest-panel">
        <div className="results-heading">
          <div>
            <p className="eyebrow">Historical simulation</p>
            <h2>Backtest</h2>
          </div>
          <div className="scan-meta">
            <History size={16} />
            Long only
          </div>
        </div>

        <form className="backtest-form" onSubmit={handleBacktest}>
          <label className="field">
            <span>Stock code</span>
            <input aria-label="Backtest stock code" value={backtestCode} onChange={(event) => setBacktestCode(event.target.value)} />
          </label>
          <label className="field">
            <span>Start date</span>
            <input aria-label="Backtest start date" value={backtestStart} onChange={(event) => setBacktestStart(event.target.value)} />
          </label>
          <label className="field">
            <span>End date</span>
            <input aria-label="Backtest end date" value={backtestEnd} onChange={(event) => setBacktestEnd(event.target.value)} />
          </label>
          <label className="field">
            <span>Initial cash</span>
            <input
              aria-label="Initial cash"
              min={1}
              type="number"
              value={initialCash}
              onChange={(event) => setInitialCash(Number(event.target.value))}
            />
          </label>
          <button className="secondary-action" disabled={isBacktesting} type="submit">
            <Play size={16} />
            {isBacktesting ? "Backtesting" : "Run backtest"}
          </button>
        </form>

        {backtestError ? (
          <div className="error-banner" role="alert">
            <AlertTriangle size={18} />
            {backtestError}
          </div>
        ) : null}

        {backtest ? <BacktestSummaryView backtest={backtest} /> : null}
      </section>
    </main>
  )
}

function ScanLogPanel({ logs, isLoading }: { logs: string[]; isLoading: boolean }) {
  if (logs.length === 0) {
    return null
  }

  return (
    <section className="scan-log-panel" aria-label="Scan log" aria-live="polite">
      <div className="scan-log-heading">
        {isLoading ? <Loader2 className="spin-icon" size={16} /> : <CheckCircle2 size={16} />}
        <span>{isLoading ? "正在拉取数据" : "扫描日志"}</span>
      </div>
      <ol className="scan-log-list">
        {logs.map((log, index) => (
          <li key={`${index}-${log}`}>{log}</li>
        ))}
      </ol>
    </section>
  )
}

function EmptyState() {
  return (
    <div className="empty-state">
      <BarChart3 size={36} />
      <p>Run a scan to compare chip distribution detail with recent price movement.</p>
    </div>
  )
}

function SignalTable({ results }: { results: StockScanResult[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Signal</th>
            <th>Confidence</th>
            <th>Latest close</th>
            <th>10D return</th>
            <th>Chip rows</th>
            <th>Quality</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {results.map((result) => (
            <tr key={result.ts_code}>
              <td className="code-cell">{result.ts_code}</td>
              <td>{result.stock_name ?? "-"}</td>
              <td>
                <span className={`signal signal-${result.signal.action.toLowerCase()}`}>{result.signal.action}</span>
              </td>
              <td>{formatPercent(result.signal.confidence)}</td>
              <td>{formatNumber(result.signal.features.latest_close)}</td>
              <td>{formatSignedPercent(result.signal.features.n_day_return)}</td>
              <td>{result.row_counts.chip_points ?? 0}</td>
              <td>
                {result.data_quality.error_code ? (
                  <span className="quality-error">{result.data_quality.error_code}</span>
                ) : (
                  <span className="quality-ok">{result.data_quality.status}</span>
                )}
              </td>
              <td className="reason-cell">{result.data_quality.message ?? result.signal.reasons[0]}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BacktestSummaryView({ backtest }: { backtest: BacktestResponse }) {
  return (
    <div className="backtest-output">
      <div className="metric-grid">
        <Metric label="Total return" value={formatSignedPercent(backtest.summary.total_return)} tone={backtest.summary.total_return >= 0 ? "good" : "bad"} />
        <Metric label="Benchmark" value={formatSignedPercent(backtest.summary.benchmark_return)} />
        <Metric label="Max drawdown" value={formatSignedPercent(backtest.summary.max_drawdown)} tone="bad" />
        <Metric label="Trades" value={String(backtest.summary.trade_count)} />
        <Metric label="Signals" value={String(backtest.summary.signal_count)} />
        <Metric label="Final value" value={formatCurrency(backtest.summary.final_value)} />
      </div>

      <div className="trade-list">
        <h3>{backtest.ts_code} {backtest.stock_name ?? ""}</h3>
        {backtest.trades.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Action</th>
                <th>Price</th>
                <th>Shares</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {backtest.trades.map((trade) => (
                <tr key={`${trade.trade_date}-${trade.action}-${trade.price}`}>
                  <td>{trade.trade_date}</td>
                  <td>
                    <span className={`signal signal-${trade.action === "BUY" ? "buy" : "sell"}`}>{trade.action}</span>
                  </td>
                  <td>{formatNumber(trade.price)}</td>
                  <td>{trade.shares}</td>
                  <td className="reason-cell">{trade.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted-text">No trades were triggered in this date range.</p>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "good" | "bad" }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={tone ? `metric-${tone}` : undefined}>{value}</strong>
    </div>
  )
}

function formatPercent(value: number) {
  return `${Math.round(value * 100)}%`
}

function formatSignedPercent(value: number | string | null | undefined) {
  if (typeof value !== "number") {
    return "-"
  }
  const prefix = value > 0 ? "+" : ""
  return `${prefix}${(value * 100).toFixed(2)}%`
}

function formatNumber(value: number | string | null | undefined) {
  return typeof value === "number" ? value.toFixed(2) : "-"
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 0
  }).format(value)
}

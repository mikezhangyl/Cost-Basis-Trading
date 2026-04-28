import { Activity, AlertTriangle, BarChart3, CheckCircle2, Loader2, Play, ShieldCheck } from "lucide-react"
import { FormEvent, useMemo, useState } from "react"

import { runScan, ScanResponse, StockScanResult } from "./api"

const defaultCodes = "600519\n000001"

export function App() {
  const [stockInput, setStockInput] = useState(defaultCodes)
  const [nDays, setNDays] = useState(10)
  const [scan, setScan] = useState<ScanResponse | null>(null)
  const [scanLogs, setScanLogs] = useState<string[]>([])
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

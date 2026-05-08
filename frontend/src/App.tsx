import { Activity, AlertTriangle, BarChart3, CheckCircle2, Database, History, Loader2, Play, RefreshCw, ShieldCheck } from "lucide-react"
import { FormEvent, useMemo, useState } from "react"

import {
  BacktestResponse,
  CacheEventSummary,
  getMarketCacheSummary,
  MarketCacheSummary,
  ResearchRunResponse,
  runBacktest,
  runResearchRun,
  runScan,
  ScanResponse,
  StockScanResult
} from "./api"

const defaultCodes = "000001"

export function App() {
  const [stockInput, setStockInput] = useState(defaultCodes)
  const [nDays, setNDays] = useState(10)
  const [scan, setScan] = useState<ScanResponse | null>(null)
  const [scanLogs, setScanLogs] = useState<string[]>([])
  const [backtestCode, setBacktestCode] = useState("000001")
  const [backtestStart, setBacktestStart] = useState("20260301")
  const [backtestWindowDays, setBacktestWindowDays] = useState(10)
  const [backtest, setBacktest] = useState<BacktestResponse | null>(null)
  const [backtestError, setBacktestError] = useState<string | null>(null)
  const [isBacktesting, setIsBacktesting] = useState(false)
  const [researchCode, setResearchCode] = useState("000001")
  const [researchStartDates, setResearchStartDates] = useState("20260301\n20260306")
  const [researchWindowDays, setResearchWindowDays] = useState(10)
  const [researchRun, setResearchRun] = useState<ResearchRunResponse | null>(null)
  const [researchError, setResearchError] = useState<string | null>(null)
  const [isResearching, setIsResearching] = useState(false)
  const [cacheSummary, setCacheSummary] = useState<MarketCacheSummary | null>(null)
  const [cacheSummaryError, setCacheSummaryError] = useState<string | null>(null)
  const [isLoadingCacheSummary, setIsLoadingCacheSummary] = useState(false)
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
        windowDays: backtestWindowDays
      })
      setBacktest(nextBacktest)
    } catch (caught) {
      setBacktestError(caught instanceof Error ? caught.message : "Backtest request failed.")
    } finally {
      setIsBacktesting(false)
    }
  }

  async function handleResearchRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const startDates = researchStartDates
      .split(/[\n,，\s]+/)
      .map((date) => date.trim())
      .filter(Boolean)
    setIsResearching(true)
    setResearchError(null)
    try {
      const nextRun = await runResearchRun({
        stockCode: researchCode,
        startDates,
        windowDays: researchWindowDays
      })
      setResearchRun(nextRun)
    } catch (caught) {
      setResearchError(caught instanceof Error ? caught.message : "Research run request failed.")
    } finally {
      setIsResearching(false)
    }
  }

  async function handleCacheSummaryRefresh() {
    setIsLoadingCacheSummary(true)
    setCacheSummaryError(null)
    try {
      setCacheSummary(await getMarketCacheSummary())
    } catch (caught) {
      setCacheSummaryError(caught instanceof Error ? caught.message : "Market cache summary request failed.")
    } finally {
      setIsLoadingCacheSummary(false)
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

      <section className="cache-health-panel" aria-label="Market cache health">
        <div className="results-heading">
          <div>
            <p className="eyebrow">Local data layer</p>
            <h2>Cache health</h2>
          </div>
          <button className="secondary-action" disabled={isLoadingCacheSummary} type="button" onClick={handleCacheSummaryRefresh}>
            {isLoadingCacheSummary ? <Loader2 className="spin-icon" size={16} /> : <RefreshCw size={16} />}
            {isLoadingCacheSummary ? "Refreshing" : "Refresh cache"}
          </button>
        </div>

        {cacheSummaryError ? (
          <div className="error-banner" role="alert">
            <AlertTriangle size={18} />
            {cacheSummaryError}
          </div>
        ) : null}

        {cacheSummary ? <MarketCacheHealthView summary={cacheSummary} /> : <CacheHealthEmptyState />}
      </section>

      <section className="backtest-panel">
        <div className="results-heading">
          <div>
            <p className="eyebrow">Historical simulation</p>
            <h2>Window check</h2>
          </div>
          <div className="scan-meta">
            <History size={16} />
            N + 1 / 3 / 5 validation
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
            <span>Window days</span>
            <input
              aria-label="Backtest window days"
              min={2}
              max={120}
              type="number"
              value={backtestWindowDays}
              onChange={(event) => setBacktestWindowDays(Number(event.target.value))}
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

      <section className="research-panel">
        <div className="results-heading">
          <div>
            <p className="eyebrow">Agent workflow prototype</p>
            <h2>Research run</h2>
          </div>
          <div className="scan-meta">
            <History size={16} />
            Artifact trace
          </div>
        </div>

        <form className="research-form" onSubmit={handleResearchRun}>
          <label className="field">
            <span>Stock code</span>
            <input aria-label="Research stock code" value={researchCode} onChange={(event) => setResearchCode(event.target.value)} />
          </label>
          <label className="field">
            <span>Start dates</span>
            <textarea
              aria-label="Research start dates"
              rows={4}
              value={researchStartDates}
              onChange={(event) => setResearchStartDates(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Window days</span>
            <input
              aria-label="Research window days"
              min={2}
              max={120}
              type="number"
              value={researchWindowDays}
              onChange={(event) => setResearchWindowDays(Number(event.target.value))}
            />
          </label>
          <button className="secondary-action" disabled={isResearching} type="submit">
            <Play size={16} />
            {isResearching ? "Researching" : "Run research"}
          </button>
        </form>

        {researchError ? (
          <div className="error-banner" role="alert">
            <AlertTriangle size={18} />
            {researchError}
          </div>
        ) : null}

        {researchRun ? <ResearchRunView researchRun={researchRun} /> : null}
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

function CacheHealthEmptyState() {
  return (
    <div className="cache-health-empty">
      <Database size={28} />
      <p>Refresh to inspect the local SQLite cache without reading cached payloads.</p>
    </div>
  )
}

function MarketCacheHealthView({ summary }: { summary: MarketCacheSummary }) {
  const jobSummary = Object.entries(summary.jobs)
    .map(([status, count]) => `${status}: ${count}`)
    .join(" / ") || "No jobs"
  return (
    <div className="cache-health-output">
      <div className="metric-grid cache-health-metrics">
        <Metric label="Current entries" value={String(summary.totals.current_entries)} />
        <Metric label="Versions" value={`${summary.totals.entry_versions} versions`} />
        <Metric label="Write jobs" value={`${summary.totals.write_jobs} jobs`} />
        <Metric label="Conflicts" value={`${summary.totals.conflicts} conflicts`} tone={summary.totals.conflicts > 0 ? "bad" : "good"} />
      </div>
      <div className="context-panel">
        <div>
          <h4>{summary.exists ? "Cache file found" : "Cache file missing"}</h4>
          <p className="muted-text">{summary.cache_path}</p>
        </div>
        <div className="context-grid cache-status-grid">
          <ContextItem label="Jobs" value={jobSummary} />
          <ContextItem label="Conflict resolution" value={formatGroupCounts(summary.conflicts)} />
        </div>
      </div>
      {summary.by_endpoint.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Endpoint</th>
                <th>Entries</th>
                <th>Instruments</th>
                <th>Date range</th>
                <th>Rows</th>
                <th>No data</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_endpoint.map((row) => (
                <tr key={row.endpoint}>
                  <td className="code-cell">{row.endpoint}</td>
                  <td>{row.current_entries}</td>
                  <td>{row.instruments}</td>
                  <td>{formatDateRange(row.min_date_key, row.max_date_key)}</td>
                  <td>{row.row_entries}</td>
                  <td>{row.provisional_no_data_entries + row.permanent_no_data_entries}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted-text">No endpoint entries recorded.</p>
      )}
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
        <Metric label="Suggestion" value={backtest.signal.action} tone={signalTone(backtest.signal.action)} />
        <Metric label="Confidence" value={formatPercent(backtest.signal.confidence)} />
        {backtest.observations.map((observation) => (
          <Metric
            key={observation.offset_days}
            label={`N+${observation.offset_days}`}
            value={formatObservationReturn(observation.period_return)}
            tone={observationTone(observation.period_return)}
          />
        ))}
        <Metric label="Chip rows" value={String(backtest.row_counts.chip_points ?? 0)} />
      </div>

      <div className="backtest-detail">
        <h3>{backtest.ts_code} {backtest.stock_name ?? ""}</h3>
        <p className="muted-text">
          分析区间：{backtest.analysis_range.start_date} 至 {backtest.analysis_range.end_date}，
          第 {backtest.window_days} 个交易日生成建议。
        </p>
        <p className="reason-callout">{backtest.signal.reasons[0]}</p>
        <MarketContextPanel backtest={backtest} />
        <table>
          <thead>
            <tr>
              <th>Window</th>
              <th>Observe Date</th>
              <th>Close</th>
              <th>Return</th>
              <th>Match</th>
              <th>Interpretation</th>
            </tr>
          </thead>
          <tbody>
            {backtest.observations.map((observation) => (
              <tr key={observation.offset_days}>
                <td>N+{observation.offset_days}</td>
                <td>{observation.observation_date ?? "N/A"}</td>
                <td>{formatNumber(observation.observation_close)}</td>
                <td>{formatSignedPercent(observation.period_return)}</td>
                <td>
                  <span className={`match-label ${matchLabelClass(observation.match_label)}`}>
                    {matchLabelText(observation.match_label)}
                  </span>
                </td>
                <td className="reason-cell">{observation.interpretation}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ResearchRunView({ researchRun }: { researchRun: ResearchRunResponse }) {
  return (
    <div className="research-output">
      <div className="metric-grid">
        <Metric label="Run ID" value={researchRun.run_id} />
        <Metric label="Samples" value={`${researchRun.sample_count} samples / ${researchRun.window_days} days`} />
        <Metric label="Strategies" value={String(researchRun.aggregate_scores.length)} />
        <Metric label="Offsets" value={researchRun.observation_offsets.map((offset) => `N+${offset}`).join(" / ")} />
      </div>

      <div className="backtest-detail">
        <h3>{researchRun.ts_code} {researchRun.stock_name ?? ""}</h3>
        <p className="muted-text">{researchRun.artifact_dir}</p>
        <CacheUsagePanel summary={researchRun.cache_event_summary} />
        <section className="ai-review-panel" aria-label="AI agent review">
          <div>
            <h4>AI agent review</h4>
            <p className="muted-text">{aiReviewStatusText(researchRun)}</p>
          </div>
          <p className="reason-callout">{researchRun.ai_review.summary}</p>
          {researchRun.ai_review.report_validation ? (
            <div className="validation-grid">
              <ContextItem label="Report validation" value={researchRun.ai_review.report_validation.status} />
              <ContextItem
                label="Canonical offsets"
                value={researchRun.ai_review.report_validation.canonical_observation_labels.join(" / ")}
              />
              <ContextItem
                label="Missing labels"
                value={
                  researchRun.ai_review.report_validation.missing_observation_labels.length > 0
                    ? researchRun.ai_review.report_validation.missing_observation_labels.join(" / ")
                    : "None"
                }
              />
            </div>
          ) : null}
          <div className="artifact-grid">
            <ContextItem label="Review" value={researchRun.ai_review.artifact_refs.review} />
            <ContextItem label="Decisions" value={researchRun.ai_review.artifact_refs.decisions} />
            <ContextItem label="Report" value={researchRun.ai_review.artifact_refs.report} />
          </div>
        </section>
        <p className="muted-text">
          回测口径：BUY 后续收益为正记为匹配，SELL 后续收益为负记为匹配；HOLD 不表达方向性，因此观察结果记为中性，
          方向分使用后续涨跌幅绝对值的负数作为机会成本/波动惩罚。
        </p>
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Samples</th>
              <th>Score</th>
              <th>Match</th>
              <th>Mismatch</th>
              <th>Neutral</th>
              <th>N/A</th>
            </tr>
          </thead>
          <tbody>
            {researchRun.aggregate_scores.map((score) => (
              <tr key={score.strategy_id}>
                <td className="code-cell">{score.strategy_id}</td>
                <td>{score.sample_count}</td>
                <td>{formatSignedPercent(score.average_directional_score)}</td>
                <td>{score.match_count}</td>
                <td>{score.mismatch_count}</td>
                <td>{score.neutral_count}</td>
                <td>{score.unavailable_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <ObservationCoverageTable researchRun={researchRun} />
        <table>
          <thead>
            <tr>
              <th>Sample</th>
              <th>Start</th>
              <th>Signal date</th>
              <th>Status</th>
              <th>Artifacts</th>
            </tr>
          </thead>
          <tbody>
            {researchRun.samples.map((sample) => (
              <tr key={sample.sample_id}>
                <td className="code-cell">{sample.sample_id}</td>
                <td>{sample.start_date}</td>
                <td>{sample.signal_date}</td>
                <td>{sample.status}</td>
                <td className="reason-cell">{sample.artifact_dir}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function CacheUsagePanel({ summary }: { summary?: Partial<CacheEventSummary> | null }) {
  const normalized = normalizeCacheSummary(summary)
  return (
    <section className="cache-usage-panel" aria-label="Cache usage">
      <div>
        <h4>Cache usage</h4>
        <p className="muted-text">{normalized.endpoints.join(" / ") || "No cache endpoints recorded"}</p>
      </div>
      <div className="cache-usage-grid">
        <Metric label="Hit rate" value={`${formatSummaryPercent(normalized.hit_rate_percent)}%`} tone="good" />
        <ContextItem label="Hits" value={`${normalized.hit_count} / ${normalized.request_count}`} />
        <ContextItem label="Miss" value={`${normalized.miss_count} / ${formatSummaryPercent(normalized.miss_rate_percent)}%`} />
        <ContextItem label="Stale" value={`${normalized.stale_count} / ${formatSummaryPercent(normalized.stale_rate_percent)}%`} />
        <ContextItem label="Fetched dates" value={String(normalized.fetched_date_count)} />
        <ContextItem label="No data" value={String(normalized.suppressed_no_data_count)} />
      </div>
    </section>
  )
}

function normalizeCacheSummary(summary?: Partial<CacheEventSummary> | null): CacheEventSummary {
  return {
    cache_event_count: numberOrZero(summary?.cache_event_count),
    endpoint_count: numberOrZero(summary?.endpoint_count),
    endpoints: Array.isArray(summary?.endpoints) ? summary.endpoints.map(String) : [],
    request_count: numberOrZero(summary?.request_count),
    hit_count: numberOrZero(summary?.hit_count),
    miss_count: numberOrZero(summary?.miss_count),
    hit_rate_percent: numberOrZero(summary?.hit_rate_percent),
    miss_rate_percent: numberOrZero(summary?.miss_rate_percent),
    stale_count: numberOrZero(summary?.stale_count),
    stale_rate_percent: numberOrZero(summary?.stale_rate_percent),
    fetched_date_count: numberOrZero(summary?.fetched_date_count),
    suppressed_no_data_count: numberOrZero(summary?.suppressed_no_data_count)
  }
}

function ObservationCoverageTable({ researchRun }: { researchRun: ResearchRunResponse }) {
  return (
    <section className="observation-coverage" aria-label="Observation coverage">
      <div>
        <h4>Observation coverage</h4>
        <p className="muted-text">每个样本在信号冻结后，对所有配置观察点的收益与评分标签。</p>
      </div>
      <div className="table-wrap">
        <table className="coverage-table">
          <thead>
            <tr>
              <th>Sample / Strategy</th>
              <th>Action</th>
              <th>Confidence</th>
              {researchRun.observation_offsets.map((offset) => (
                <th key={offset}>N+{offset}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {researchRun.samples.flatMap((sample) =>
              sample.strategies.map((strategy) => (
                <tr key={`${sample.sample_id}-${strategy.strategy_id}`}>
                  <td className="code-cell">{sample.sample_id} / {strategy.strategy_id}</td>
                  <td>
                    <span className={`signal signal-${strategy.signal.action.toLowerCase()}`}>{strategy.signal.action}</span>
                  </td>
                  <td>{formatPercent(strategy.signal.confidence)}</td>
                  {researchRun.observation_offsets.map((offset) => {
                    const score = strategy.observation_scores.find((observation) => observation.offset_days === offset)
                    return (
                      <td key={offset}>
                        {score ? (
                          <div className="coverage-cell">
                            <strong>{formatObservationReturn(score.period_return)}</strong>
                            <span className={`match-label ${matchLabelClass(score.match_label)}`}>
                              {score.match_label}
                            </span>
                          </div>
                        ) : (
                          <span className="muted-text">-</span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function MarketContextPanel({ backtest }: { backtest: BacktestResponse }) {
  const context = backtest.market_context
  return (
    <section className="context-panel" aria-label="Market context">
      <div>
        <h4>量价与K线确认</h4>
        <p className="muted-text">{context.context_summary}</p>
      </div>
      <div className="context-grid">
        <ContextItem label="区间涨跌" value={formatSignedPercent(context.price_return)} />
        <ContextItem label="量比5日" value={formatRatio(context.volume_ratio_5)} />
        <ContextItem label="额比5日" value={formatRatio(context.amount_ratio_5)} />
        <ContextItem label="量能趋势" value={formatSignedPercent(context.volume_trend)} />
        <ContextItem label="收盘/MA5" value={formatSignedPercent(context.close_vs_ma5)} />
        <ContextItem label="阳线/阴线" value={`${context.bullish_candle_count} / ${context.bearish_candle_count}`} />
        <ContextItem label="十字星" value={String(context.doji_count)} />
        <ContextItem label="长上/长下影" value={`${context.long_upper_shadow_count} / ${context.long_lower_shadow_count}`} />
      </div>
    </section>
  )
}

function ContextItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="context-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function aiReviewStatusText(researchRun: ResearchRunResponse) {
  return researchRun.ai_review.model
    ? `${researchRun.ai_review.status} / ${researchRun.ai_review.model}`
    : researchRun.ai_review.status
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

function formatSummaryPercent(value: number) {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

function numberOrZero(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0
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

function formatRatio(value: number | null | undefined) {
  return typeof value === "number" ? `${value.toFixed(2)}x` : "-"
}

function formatGroupCounts(counts: Record<string, number>) {
  const entries = Object.entries(counts)
  return entries.length > 0 ? entries.map(([key, count]) => `${key}: ${count}`).join(" / ") : "None"
}

function formatDateRange(start: string | null, end: string | null) {
  return start && end ? `${start} - ${end}` : "-"
}

function signalTone(action: string) {
  if (action === "BUY") {
    return "good"
  }
  if (action === "SELL") {
    return "bad"
  }
  return undefined
}

function formatObservationReturn(periodReturn: number | null) {
  if (periodReturn === null) {
    return "N/A"
  }
  return formatSignedPercent(periodReturn)
}

function observationTone(periodReturn: number | null) {
  if (periodReturn === null) {
    return undefined
  }
  return periodReturn >= 0 ? "good" : "bad"
}

function matchLabelClass(label: string) {
  if (label === "N/A") {
    return "match-na"
  }
  return `match-${label.toLowerCase()}`
}

function matchLabelText(label: string) {
  if (label === "N/A") {
    return "N/A"
  }
  if (label === "MATCH") {
    return "匹配"
  }
  if (label === "MISMATCH") {
    return "不匹配"
  }
  return "记录"
}

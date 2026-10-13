import { useCallback, useEffect, useState } from 'react'
import type { Session } from '../App'
import { api, timeAgo } from '../api'
import { Spinner } from '../components/bits'

interface Usage {
  model: string; prices_per_million: Record<string, number>
  totals: { calls: number; inp: number; cached: number; cw: number; out: number; cost: number; cache_savings_usd: number }
  today: { calls: number; tokens: number; cost: number; cap_tokens: number; user_cap_tokens: number }
  by_agent: { agent: string; calls: number; avg_requests: number; avg_input: number; avg_cached: number; avg_output: number; avg_cost: number; total_cost: number; avg_latency: number }[]
  by_run: { run_id: string; kind: string; status: string; started: string; calls: number; inp: number; cached: number; out: number; cost: number }[]
  model_costs: { fixed_per_trading_day_usd: number; fixed_tokens_per_trading_day: number; per_user_day_usd: number; per_user_month_usd: number; trading_days_per_month: number; scale: { users: number; monthly_usd: number; per_user_month_usd: number }[] }
}
interface RunRow { id: string; kind: string; started_at: string; finished_at: string | null; status: string; round_id: number | null }
interface TraceStep { step: string; at: string; detail: Record<string, unknown>; usage: { input_tokens: number; cache_read_tokens: number; output_tokens: number; cost_usd: number; latency_s: number; requests: number } | null }
interface SafetyEvent { id: number; ts: string; kind: string; detail: string; user_id: number | null }

const usd = (n: number) => n < 0.01 ? `$${n.toFixed(5)}` : `$${n.toFixed(4)}`
const AGENT_STEPS = new Set(['scout', 'editor', 'editor_retry', 'factcheck', 'factcheck_retry', 'analyst', 'coach'])

const AGENTS = [
  { name: 'News Scout', tools: 'search_news, get_price_history, get_market_snapshot', job: 'Groups ~40 pre-filtered headlines into up to 8 market-moving events.' },
  { name: 'Editor', tools: 'none', job: 'Writes 3-5 neutral cards with a target ticker and cited sources.' },
  { name: 'Fact-checker', tools: 'none', job: 'Checks every claim against its sources and blocks direction hints.' },
  { name: 'Inflection Analyst', tools: 'get_minute_bars, search_news', job: 'Links each sharp index move to the news that most likely caused it, or says "no clear cause".' },
  { name: 'Coach', tools: 'get_price_history, get_inflections', job: 'Explains each settled card and writes one testable rule; personal feedback on missed calls.' },
  { name: 'Moderator', tools: 'none', job: 'Approves, flags or rejects community posts after the code safety filter.' },
]

export default function Engine({ s }: { s: Session }) {
  const [tab, setTab] = useState<'agents' | 'runs' | 'usage' | 'safety'>('agents')
  const [usage, setUsage] = useState<Usage | null>(null)
  const [runs, setRuns] = useState<RunRow[]>([])
  const [trace, setTrace] = useState<(RunRow & { trace: TraceStep[] }) | null>(null)
  const [events, setEvents] = useState<SafetyEvent[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    const [u, r, e] = await Promise.all([api<Usage>('/api/usage'), api<RunRow[]>('/api/pipeline/runs'), api<SafetyEvent[]>('/api/safety/events')])
    setUsage(u); setRuns(r); setEvents(e)
  }, [])
  useEffect(() => { void load() }, [load])

  async function openRun(id: string) { setTrace(await api(`/api/pipeline/runs/${id}`)); setTab('runs') }
  async function run(kind: 'morning' | 'evening') {
    if (!s.profile) return s.needLogin()
    setBusy(kind); setMsg(null)
    try {
      const r = await api<{ status: string; run_id?: string; trade_date: string }>(`/api/pipeline/${kind}`, { method: 'POST' })
      setMsg(`${kind} pipeline: ${r.status} (${r.trade_date})`)
      await load(); if (r.run_id) await openRun(r.run_id)
    } catch (e) { setMsg((e as Error).message) } finally { setBusy(null) }
  }

  return (
    <>
      <div className="page-head">
        <div><h1>Under the Hood</h1><div className="muted">How the agents work, what every run costs, and what the safety layer caught. All numbers are measured from real runs.</div></div>
        <div className="tabs">
          {(['agents', 'runs', 'usage', 'safety'] as const).map(t => (
            <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
              {t === 'agents' ? 'Agents' : t === 'runs' ? 'Pipeline runs' : t === 'usage' ? 'Token usage' : 'Safety log'}</button>
          ))}
        </div>
      </div>

      {tab === 'agents' && (
        <div className="stack">
          <div className="panel">
            <div className="panel-title">Daily flow - code runs the schedule, agents only read, write and judge text</div>
            <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
              {[['06:00', 'News in + pre-filter', 'code'], ['', 'Scout', 'ai'], ['', 'Editor', 'ai'], ['', 'Fact-checker', 'ai'], ['09:25', 'Lock bets', 'code'],
                ['16:10', 'Settle + LP', 'code'], ['', 'Inflection scan', 'code'], ['', 'Inflection Analyst', 'ai'], ['', 'Coach', 'ai']].map(([t, n, k], i) => (
                <div key={i} className="panel flat" style={{ padding: 10, borderColor: k === 'ai' ? 'var(--accent)' : undefined, borderWidth: k === 'ai' ? 2 : 1 }}>
                  <div className="faint">{t || ' '}</div><b>{n}</b><div className="faint">{k === 'ai' ? 'AI agent' : 'plain code, 0 tokens'}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="panel table-wrap">
            <table>
              <thead><tr><th>Agent</th><th>Job</th><th>MCP tools</th></tr></thead>
              <tbody>{AGENTS.map(a => <tr key={a.name}><td><b>{a.name}</b></td><td>{a.job}</td><td className="faint">{a.tools}</td></tr>)}</tbody>
            </table>
            <div className="faint" style={{ marginTop: 8 }}>Model: {usage?.model ?? 'gpt-6-luna'} via Portkey · structured outputs (Pydantic) · static prompts cached · max 8 model calls per agent run.</div>
          </div>
          <div className="panel spread wrap">
            <div><b>Run a pipeline now</b><div className="faint">Rate-limited and capped by the daily token budget.</div></div>
            <div className="row">
              <button className="btn" onClick={() => run('morning')} disabled={!!busy}>{busy === 'morning' ? <Spinner /> : 'Build next round'}</button>
              <button className="btn" onClick={() => run('evening')} disabled={!!busy}>{busy === 'evening' ? <Spinner /> : 'Run evening (settle + lessons)'}</button>
            </div>
          </div>
          {msg && <div className="notice">{msg}</div>}
        </div>
      )}

      {tab === 'runs' && (
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', alignItems: 'start' }}>
          <div className="panel stack" style={{ gap: 6 }}>
            <div className="panel-title">Recent runs</div>
            {runs.map(r => (
              <button key={r.id} className="btn ghost" style={{ justifyContent: 'space-between', background: trace?.id === r.id ? 'var(--panel-2)' : undefined }} onClick={() => openRun(r.id)}>
                <span><b>{r.kind}</b> <span className="faint">{timeAgo(r.started_at)}</span></span>
                <span className={`pill ${r.status === 'ok' ? 'up' : r.status === 'error' ? 'down' : 'locked'}`}>{r.status}</span>
              </button>
            ))}
            {runs.length === 0 && <div className="faint">No runs yet.</div>}
          </div>
          <div className="panel stack" style={{ gridColumn: 'span 2' }}>
            {!trace && <div className="faint">Pick a run to see every step, its output and its token cost.</div>}
            {trace && <>
              <div className="spread"><h2>{trace.kind} run</h2><span className="faint">{trace.id}</span></div>
              {trace.trace.map((st, i) => (
                <div key={i} className={`trace-step${AGENT_STEPS.has(st.step) ? ' agent' : ''}`}>
                  <div className="spread wrap">
                    <b>{i + 1}. {st.step}{AGENT_STEPS.has(st.step) ? ' (AI agent)' : ' (code)'}</b>
                    {st.usage && <span className="faint mono">{st.usage.input_tokens.toLocaleString()} in ({st.usage.cache_read_tokens.toLocaleString()} cached) · {st.usage.output_tokens.toLocaleString()} out · {usd(st.usage.cost_usd)} · {st.usage.latency_s}s</span>}
                  </div>
                  <pre className="json">{JSON.stringify(st.detail, null, 2)}</pre>
                </div>
              ))}
            </>}
          </div>
        </div>
      )}

      {tab === 'usage' && usage && (
        <div className="stack">
          <div className="panel">
            <div className="stats">
              <div className="stat"><span className="k">Model calls</span><span className="v">{usage.totals.calls}</span></div>
              <div className="stat"><span className="k">Input tokens</span><span className="v">{usage.totals.inp.toLocaleString()}</span></div>
              <div className="stat"><span className="k">Cached input</span><span className="v">{usage.totals.cached.toLocaleString()}</span></div>
              <div className="stat"><span className="k">Output tokens</span><span className="v">{usage.totals.out.toLocaleString()}</span></div>
              <div className="stat"><span className="k">Total cost</span><span className="v">{usd(usage.totals.cost)}</span></div>
              <div className="stat"><span className="k">Saved by caching</span><span className="v">{usd(usage.totals.cache_savings_usd)}</span></div>
            </div>
            <div style={{ marginTop: 14 }}>
              <div className="spread faint"><span>Today's budget: {usage.today.tokens.toLocaleString()} of {usage.today.cap_tokens.toLocaleString()} tokens</span><span>per-player cap {usage.today.user_cap_tokens.toLocaleString()}</span></div>
              <div className="bar"><div style={{ width: `${Math.min(100, 100 * usage.today.tokens / usage.today.cap_tokens)}%` }} /></div>
            </div>
          </div>
          <div className="grid two">
            <div className="panel">
              <div className="panel-title">Cost model (measured)</div>
              <div className="stack" style={{ gap: 4, fontSize: 14 }}>
                <div className="spread"><span>Shared pipeline per trading day</span><b className="mono">{usd(usage.model_costs.fixed_per_trading_day_usd)}</b></div>
                <div className="spread"><span>Tokens per trading day</span><b className="mono">{usage.model_costs.fixed_tokens_per_trading_day.toLocaleString()}</b></div>
                <div className="spread"><span>Per active player per day (coach + moderation)</span><b className="mono">{usd(usage.model_costs.per_user_day_usd)}</b></div>
              </div>
              <table style={{ marginTop: 12 }}>
                <thead><tr><th>Players</th><th className="num">Per month</th><th className="num">Per player / month</th></tr></thead>
                <tbody>{usage.model_costs.scale.map(r => <tr key={r.users}><td className="mono">{r.users.toLocaleString()}</td><td className="num">{usd(r.monthly_usd)}</td><td className="num">{usd(r.per_user_month_usd)}</td></tr>)}</tbody>
              </table>
              <div className="faint" style={{ marginTop: 8 }}>{usage.model_costs.trading_days_per_month} trading days/month. Prices per 1M tokens: input ${usage.prices_per_million.input}, cached ${usage.prices_per_million.cached}, output ${usage.prices_per_million.output}.</div>
            </div>
            <div className="panel table-wrap">
              <div className="panel-title">Per agent (average per call)</div>
              <table>
                <thead><tr><th>Agent</th><th className="num">Calls</th><th className="num">In</th><th className="num">Cached</th><th className="num">Out</th><th className="num">Cost</th></tr></thead>
                <tbody>{usage.by_agent.map(a => <tr key={a.agent}><td>{a.agent}</td><td className="num">{a.calls}</td><td className="num">{a.avg_input?.toLocaleString()}</td><td className="num">{a.avg_cached?.toLocaleString()}</td><td className="num">{a.avg_output?.toLocaleString()}</td><td className="num">{usd(a.avg_cost)}</td></tr>)}</tbody>
              </table>
            </div>
          </div>
          <div className="panel table-wrap">
            <div className="panel-title">Per pipeline run</div>
            <table>
              <thead><tr><th>Run</th><th>Kind</th><th className="num">Calls</th><th className="num">Input</th><th className="num">Cached</th><th className="num">Output</th><th className="num">Cost</th></tr></thead>
              <tbody>{usage.by_run.map(r => <tr key={r.run_id} style={{ cursor: 'pointer' }} onClick={() => openRun(r.run_id)}><td className="faint">{r.run_id}</td><td>{r.kind}</td><td className="num">{r.calls}</td><td className="num">{r.inp.toLocaleString()}</td><td className="num">{r.cached.toLocaleString()}</td><td className="num">{r.out.toLocaleString()}</td><td className="num">{usd(r.cost)}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'safety' && (
        <div className="panel table-wrap">
          <div className="panel-title">What the safety layer caught</div>
          <table>
            <thead><tr><th>When</th><th>Defense</th><th>Detail</th></tr></thead>
            <tbody>
              {events.map(e => <tr key={e.id}><td className="faint" style={{ whiteSpace: 'nowrap' }}>{timeAgo(e.ts)}</td><td><span className="pill">{e.kind}</span></td><td style={{ fontSize: 13 }}>{e.detail}</td></tr>)}
              {events.length === 0 && <tr><td colSpan={3} className="faint">Nothing caught yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

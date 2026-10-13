import { useEffect, useState } from 'react'
import type { Session } from '../App'
import type { Direction } from '../api'
import { api, CATEGORIES, CATEGORY_LABEL, fmtPct, setToken } from '../api'
import { CategoryPill, DirPill } from '../components/bits'

interface JournalRow {
  id: number; card_id: number; direction: Direction; stake: number; reason: string; practice: number
  payout: number | null; lp_change: number | null; created_at: string; settled_at: string | null
  title: string; category: string; ticker: string; kind: string; outcome: string | null; move_pct: number | null
  trade_date: string | null; rule_text: string | null; coach_note: { feedback: string; suggested_rule: string } | null
}

export default function MyEdge({ s }: { s: Session }) {
  const [journal, setJournal] = useState<JournalRow[]>([])
  const [text, setText] = useState('')
  const [cat, setCat] = useState('macro')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => { if (s.profile) api<JournalRow[]>('/api/bets/mine').then(setJournal).catch(() => {}) }, [s.profile])

  if (!s.profile) return (
    <div className="panel empty"><h2>Your edge lives here</h2>
      <p>Your rulebook and betting journal: every call, your reason, and what the market did.</p>
      <button className="btn primary" onClick={s.needLogin}>Log in</button></div>
  )
  const p = s.profile
  const acc = p.settled_bets ? Math.round(100 * p.correct_bets / p.settled_bets) : null

  async function addRule(e: React.FormEvent) {
    e.preventDefault(); setErr(null)
    try { await api('/api/rules', { body: { text, category: cat } }); setText(''); await s.refresh() }
    catch (e) { setErr((e as Error).message) }
  }
  async function del(id: number) { await api(`/api/rules/${id}`, { method: 'DELETE' }); await s.refresh() }

  return (
    <>
      <div className="page-head">
        <div><h1>My Edge</h1><div className="muted">Your rules and your record.</div>
          <button className="link show-sm" style={{ marginTop: 6 }} onClick={async () => { await api('/api/logout', { method: 'POST' }).catch(() => {}); setToken(null); await s.refresh() }}>Log out</button></div>
      </div>

      <div className="panel" style={{ marginBottom: 18 }}>
        <div className="stats">
          <div className="stat"><span className="k">Settled calls</span><span className="v">{p.settled_bets}</span></div>
          <div className="stat"><span className="k">Accuracy</span><span className="v">{acc === null ? '-' : `${acc}%`}</span></div>
          <div className="stat"><span className="k">Live calls</span><span className="v">{p.live_bets}</span></div>
          <div className="stat"><span className="k">Rules</span><span className="v">{s.rules.length}</span></div>
          <div className="stat"><span className="k">Streak</span><span className="v">{p.streak} day{p.streak === 1 ? '' : 's'}</span></div>
          <div className="stat"><span className="k">Points</span><span className="v">{p.points.toLocaleString()}</span></div>
        </div>
      </div>

      <div className="grid two">
        <section className="panel stack">
          <h2>Rulebook</h2>
          <form className="stack" onSubmit={addRule}>
            <input placeholder='New rule, e.g. "Hot CPI → Nasdaq lags"' value={text} onChange={e => setText(e.target.value)} maxLength={200} aria-label="New rule" />
            <div className="row">
              <select value={cat} onChange={e => setCat(e.target.value)} aria-label="Rule category" style={{ maxWidth: 180 }}>
                {CATEGORIES.map(c => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
              </select>
              <button className="btn primary" disabled={text.trim().length < 5}>Add rule</button>
            </div>
          </form>
          {err && <div className="notice error">{err}</div>}
          {s.rules.length === 0 && <div className="faint">No rules yet. Save the Coach's lesson after a settled card, or write your own.</div>}
          {s.rules.map(r => (
            <div key={r.id} className="panel flat" style={{ padding: 12 }}>
              <div className="spread" style={{ alignItems: 'flex-start' }}>
                <div className="stack" style={{ gap: 4 }}>
                  <div style={{ fontWeight: 600 }}>{r.text}</div>
                  <div className="row wrap"><CategoryPill c={r.category} />{r.source === 'coach' && <span className="pill">from Coach</span>}
                    <span className="faint">{r.uses ? `${r.wins} of ${r.uses} correct` : 'not used yet'}</span></div>
                </div>
                <button className="btn ghost small" onClick={() => del(r.id)} aria-label={`Delete rule ${r.text}`}>✕</button>
              </div>
              {r.uses > 0 && <div className={`bar ${r.win_rate! >= 0.5 ? 'good' : 'bad'}`} style={{ marginTop: 8 }}><div style={{ width: `${Math.round(r.win_rate! * 100)}%` }} /></div>}
            </div>
          ))}
        </section>

        <section className="panel stack">
          <h2>Journal</h2>
          {journal.length === 0 && <div className="faint">Your calls will appear here with your reasons and results.</div>}
          <div className="stack">
            {journal.slice(0, 40).map(b => {
              const won = b.outcome && b.outcome !== 'void' ? b.outcome === b.direction : null
              return (
                <div key={b.id} className="panel flat" style={{ padding: 12 }}>
                  <div className="spread" style={{ alignItems: 'flex-start' }}>
                    <div style={{ fontWeight: 600, fontSize: 14 }}>{b.title}</div>
                    {b.lp_change != null && <span className="pill mono" style={{ color: b.lp_change >= 0 ? 'var(--up)' : 'var(--down)' }}>{b.lp_change >= 0 ? '+' : ''}{b.lp_change.toFixed(1)} LP</span>}
                  </div>
                  <div className="row wrap" style={{ marginTop: 6 }}>
                    <CategoryPill c={b.category} />
                    <span className="faint">{b.practice ? 'Replay' : b.trade_date}</span>
                    <span>You: <DirPill d={b.direction} /></span>
                    {b.outcome ? <span>Market: <DirPill d={b.outcome} /> <span className="mono faint">{fmtPct(b.move_pct)}</span></span> : <span className="pill locked">pending</span>}
                    {won !== null && <b style={{ color: won ? 'var(--up)' : 'var(--down)' }}>{won ? '✓' : '✗'}</b>}
                  </div>
                  {b.reason && <div className="faint" style={{ marginTop: 6 }}>"{b.reason}"{b.rule_text ? ' · rule used' : ''}</div>}
                  {b.coach_note && <div className="notice" style={{ marginTop: 8, fontSize: 13 }}>{b.coach_note.feedback}</div>}
                </div>
              )
            })}
          </div>
        </section>
      </div>
    </>
  )
}

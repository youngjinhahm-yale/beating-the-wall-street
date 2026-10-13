import { useEffect, useState } from 'react'
import type { Session } from '../App'
import { api, CATEGORIES, CATEGORY_LABEL } from '../api'
import { TierBadge } from '../components/bits'

interface Row { user_id: number; username: string; lp: number | null; tier: string; wins: number; losses: number; points?: number; streak?: number }

const LADDER: [string, number | null][] = [['Iron', 0], ['Bronze', 100], ['Silver', 200], ['Gold', 300], ['Platinum', 400],
  ['Emerald', 500], ['Diamond', 600], ['Master', 700], ['Grandmaster', null], ['Challenger', null]]

export default function Ranks({ s }: { s: Session }) {
  const [cat, setCat] = useState<string>('')
  const [rows, setRows] = useState<Row[]>([])

  useEffect(() => { api<Row[]>(`/api/leaderboard${cat ? `?category=${cat}` : ''}`).then(setRows).catch(() => setRows([])) }, [cat])

  return (
    <>
      <div className="page-head">
        <div><h1>Ranks</h1><div className="muted">Your tier in each kind of news.</div></div>
      </div>

      {s.profile && (
        <div className="grid tiers" style={{ marginBottom: 18 }}>
          {s.profile.categories.map(c => {
            const next = LADDER.find(([, need]) => need !== null && need > c.lp)
            const prevNeed = [...LADDER].reverse().find(([, need]) => need !== null && need <= c.lp)?.[1] ?? 0
            const pct = next ? Math.round(100 * (c.lp - prevNeed) / ((next[1] as number) - prevNeed)) : 100
            return (
              <div key={c.category} className="panel stack" style={{ gap: 8 }}>
                <div className="spread"><span className="panel-title" style={{ margin: 0 }}>{CATEGORY_LABEL[c.category]}</span>
                  <span className="faint mono">{c.wins}-{c.losses}</span></div>
                <TierBadge tier={c.tier} size={30} big />
                {c.placement_left > 0
                  ? <div className="faint">{c.placement_left} call{c.placement_left === 1 ? '' : 's'} to rank</div>
                  : <>
                      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
                      <div className="faint mono">{c.lp.toFixed(0)} LP{next ? ` · ${(next[1] as number) - Math.floor(c.lp)} to ${next[0]}` : ''}</div>
                    </>}
              </div>
            )
          })}
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', alignItems: 'start' }}>
        <section className="panel">
          <div className="stack" style={{ marginBottom: 12, gap: 8 }}>
            <h2>Leaderboard</h2>
            <div className="tabs">
              <button className={cat === '' ? 'active' : ''} onClick={() => setCat('')}>Overall</button>
              {CATEGORIES.map(c => <button key={c} className={cat === c ? 'active' : ''} onClick={() => setCat(c)}>{CATEGORY_LABEL[c]}</button>)}
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Player</th><th>Tier</th><th className="num">LP</th><th className="num">W–L</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.user_id} style={r.user_id === s.profile?.id ? { background: 'var(--panel-2)' } : undefined}>
                    <td className="mono">{i + 1}</td><td><b>{r.username}</b></td><td><TierBadge tier={r.tier} /></td>
                    <td className="num">{r.lp == null ? '-' : r.lp.toFixed(0)}</td><td className="num">{r.wins}-{r.losses}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={5} className="faint">No settled calls yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
        <section className="panel stack">
          <h2>How LP works</h2>
          <div className="muted" style={{ fontSize: 14 }}>
            Right: <b>+30 × (1 − p)</b>. Wrong: <b>−30 × p</b>. Here p is the crowd's share on your pick at lock.
            You only climb on average if you judge better than the crowd. Following the herd earns little; a correct contrarian call earns a lot.
          </div>
          <div className="stack" style={{ gap: 6 }}>
            {LADDER.map(([t, need]) => (
              <div key={t} className="spread"><TierBadge tier={t} /><span className="faint mono">{need === null ? (t === 'Challenger' ? '#1 Master' : 'Top 3 Masters') : `${need} LP`}</span></div>
            ))}
          </div>
        </section>
      </div>
    </>
  )
}

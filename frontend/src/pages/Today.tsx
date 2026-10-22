import { useCallback, useEffect, useState } from 'react'
import type { Session } from '../App'
import type { LiveCard, Mission, Results, Today } from '../api'
import { api, etTime, fmtPct } from '../api'
import CardView from '../components/CardView'
import { Spinner, useCountdown } from '../components/bits'

const SNAP = ['^GSPC', '^IXIC', '^DJI', '^VIX', '^TNX', 'CL=F']

export default function TodayPage({ s }: { s: Session }) {
  const [data, setData] = useState<Today | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  const [building, setBuilding] = useState(false)
  const [buildMsg, setBuildMsg] = useState<string | null>(null)
  const [showHow, setShowHow] = useState(false)
  const [live, setLive] = useState<Record<number, LiveCard>>({})
  const [mission, setMission] = useState<Mission | null>(null)
  const [results, setResults] = useState<Results | null>(null)

  const load = useCallback(async () => {
    try { setData(await api<Today>('/api/today')); setErr(null) } catch (e) { setErr((e as Error).message) }
  }, [])

  useEffect(() => { void load() }, [load, s.profile?.id])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  useEffect(() => { const t = setInterval(() => void load(), 30000); return () => clearInterval(t) }, [load])
  useEffect(() => { if (s.profile && s.profile.settled_bets === 0 && s.profile.live_bets === 0) setShowHow(true) }, [s.profile])

  const rnd = data?.round

  // Mission progress for the open round, and the scoreboard of the last settled round.
  useEffect(() => {
    if (!s.profile || !rnd) { setMission(null); setResults(null); return }
    if (rnd.status === 'open') api<{ mission: Mission }>(`/api/me/round/${rnd.id}`).then(r => setMission(r.mission)).catch(() => {})
    else setMission(null)
    api<Results | null>('/api/me/last-results').then(setResults).catch(() => {})
  }, [s.profile, rnd?.id, rnd?.status, data])

  // Live tracker: while the round is locked (market hours), refresh each card's move every minute.
  useEffect(() => {
    if (!rnd || rnd.status !== 'locked') { setLive({}); return }
    const pull = () => api<{ cards: LiveCard[] }>(`/api/rounds/${rnd.id}/live`)
      .then(r => setLive(Object.fromEntries(r.cards.map(c => [c.card_id, c])))).catch(() => {})
    void pull()
    const t = setInterval(pull, 60000)
    return () => clearInterval(t)
  }, [rnd?.id, rnd?.status])
  const countdown = useCountdown(rnd?.status === 'open' ? rnd.lock_at : data?.next_lock_at, now)

  async function build() {
    if (!s.profile) return s.needLogin()
    setBuilding(true); setBuildMsg('Agents are reading the news: Scout → Editor → Fact-checker. This takes about a minute...')
    try {
      const r = await api<{ status: string; trade_date: string }>('/api/pipeline/morning', { method: 'POST' })
      setBuildMsg(r.status === 'exists' ? `A round for ${r.trade_date} already exists.` : `Published the round for ${r.trade_date}.`)
      await load()
    } catch (e) { setBuildMsg((e as Error).message) } finally { setBuilding(false) }
  }

  const played = rnd ? rnd.cards.filter(c => c.my_bet).length : 0

  return (
    <>
      <section className="hero">
        <div className="stack" style={{ gap: 6 }}>
          <div className="row wrap" style={{ gap: 8 }}>
            {rnd?.status === 'open' && <span className="pill live"><span className="dot pulse" /> Betting open</span>}
            {rnd?.status === 'locked' && <span className="pill locked">Locked · settles 4 PM ET</span>}
            {rnd?.status === 'settled' && <span className="pill">Settled</span>}
            {rnd && s.profile && rnd.status === 'open' && mission && (
              <span className="pill mission">{mission.done ? `✓ Mission +${mission.bonus}` : `🎯 Call all ${mission.total} · ${mission.called}/${mission.total} · +${mission.bonus}`}</span>
            )}
            {rnd && s.profile && rnd.status !== 'open' && <span className="pill">{played}/{rnd.cards.length} called</span>}
          </div>
          <h1>{rnd ? new Date(rnd.trade_date + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }) : 'No round yet'}</h1>
          <div className="muted">{rnd ? 'Will each market close above, near, or below its open?' : 'Agents build a round from the news every trading morning.'}</div>
        </div>
        <div className="countdown">
          <div className="label">{rnd?.status === 'open' ? 'Closes in' : 'Next round'}</div>
          <div className="big">{countdown}</div>
          <div className="muted" style={{ fontSize: 13 }}>{rnd?.status === 'open' ? etTime(rnd.lock_at) : data ? etTime(data.next_lock_at) : ''}</div>
          <button className="btn ghost small" style={{ color: 'rgba(255,255,255,.9)', marginTop: 6 }} onClick={() => setShowHow(h => !h)}>{showHow ? 'Hide help' : 'How to play'}</button>
        </div>
      </section>

      {showHow && (
        <div className="howto">
          <span><b>1</b> Read the card</span>
          <span><b>2</b> Pick Up · Flat · Down before 9:25 ET</span>
          <span><b>3</b> Beat the crowd, earn more</span>
          <span><b>4</b> After 4 PM, save the lesson as a rule</span>
        </div>
      )}

      {results && (
        <section className="results-card" aria-label="Your last results">
          <div className="spread">
            <span className="r-label">Results · {results.trade_date ? new Date(results.trade_date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}</span>
            {results.streak > 0 && <span className="r-streak">🔥 {results.streak}-day streak</span>}
          </div>
          <div className="r-main">
            <div><b>{results.correct}/{results.called}</b><span>correct</span></div>
            <div className={results.net_points >= 0 ? 'pos' : 'neg'}><b>{results.net_points >= 0 ? '+' : ''}{results.net_points}</b><span>points</span></div>
            <div className={results.lp >= 0 ? 'pos' : 'neg'}><b>{results.lp >= 0 ? '+' : ''}{results.lp}</b><span>LP</span></div>
          </div>
          <div className="r-cards">
            {results.cards.map((c, i) => (
              <span key={i} className={c.outcome === 'void' ? 'void' : c.pick === c.outcome ? 'win' : 'loss'}>
                {c.outcome === 'void' ? '•' : c.pick === c.outcome ? '✓' : '✗'} {c.ticker}
              </span>
            ))}
          </div>
        </section>
      )}

      {rnd && rnd.snapshot.length > 0 && (
        <div className="strip" style={{ marginBottom: 18 }} aria-label="Market snapshot">
          {rnd.snapshot.filter(t => SNAP.includes(t.ticker)).map(t => (
            <div className="tick" key={t.ticker}>
              <div className="name">{t.name}</div>
              <div className="spread"><span className="val">{t.close.toLocaleString()}</span>
                <span className="mono" style={{ color: t.change_pct >= 0 ? 'var(--up)' : 'var(--down)', fontSize: 13, fontWeight: 700 }}>{fmtPct(t.change_pct)}</span></div>
            </div>
          ))}
        </div>
      )}

      {err && <div className="notice error">{err}</div>}
      {!data && !err && <div className="empty"><Spinner /></div>}

      {rnd ? (
        <div className="grid cards">
          {rnd.cards.map(c => (
            <CardView key={c.id} card={c} rules={s.rules} loggedIn={!!s.profile} onNeedLogin={s.needLogin} live={live[c.id]}
              onChanged={async () => { await load(); await s.refresh() }} onRuleSaved={s.refresh} />
          ))}
        </div>
      ) : data && (
        <div className="panel empty">
          <h2>The agents haven't built a round yet</h2>
          <p>Rounds are built automatically at 6:00 AM ET on trading days. You can also build the next one now from the latest news.</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            <button className="btn primary" onClick={build} disabled={building}>{building ? <Spinner /> : "Build the next round now"}</button>
            <a className="btn" href="#practice">Play Replay instead</a>
          </div>
        </div>
      )}

      {rnd && rnd.status !== 'open' && (
        <div className="panel flat" style={{ marginTop: 18 }}>
          <div className="spread wrap">
            <div><b>Next round:</b> <span className="muted">session of {data?.next_trade_date}, betting closes {data ? etTime(data.next_lock_at) : ''}.</span></div>
            <button className="btn small" onClick={build} disabled={building}>{building ? <Spinner /> : 'Build it now'}</button>
          </div>
        </div>
      )}
      {buildMsg && <div className="notice" style={{ marginTop: 12 }}>{buildMsg}</div>}
    </>
  )
}

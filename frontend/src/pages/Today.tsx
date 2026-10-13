import { useCallback, useEffect, useState } from 'react'
import type { Session } from '../App'
import type { Today } from '../api'
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

  const load = useCallback(async () => {
    try { setData(await api<Today>('/api/today')); setErr(null) } catch (e) { setErr((e as Error).message) }
  }, [])

  useEffect(() => { void load() }, [load, s.profile?.id])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  useEffect(() => { const t = setInterval(() => void load(), 30000); return () => clearInterval(t) }, [load])
  useEffect(() => { if (s.profile && s.profile.settled_bets === 0 && s.profile.live_bets === 0) setShowHow(true) }, [s.profile])

  const rnd = data?.round
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
            {rnd && s.profile && <span className="pill">{played}/{rnd.cards.length} called</span>}
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
            <CardView key={c.id} card={c} rules={s.rules} loggedIn={!!s.profile} onNeedLogin={s.needLogin}
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

import { useEffect, useState } from 'react'
import type { Today } from '../api'
import { api } from '../api'
import { CategoryPill, TierBadge, useCountdown } from '../components/bits'

const LADDER = ['Iron', 'Bronze', 'Silver', 'Gold', 'Platinum', 'Emerald', 'Diamond', 'Master', 'Grandmaster', 'Challenger']

export default function Welcome({ onStart, onLogin, onPeek }: { onStart: () => void; onLogin: () => void; onPeek: () => void }) {
  const [today, setToday] = useState<Today | null>(null)
  const [now, setNow] = useState(Date.now())
  useEffect(() => { api<Today>('/api/today').then(setToday).catch(() => {}) }, [])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])

  const rnd = today?.round
  const open = rnd?.status === 'open'
  const countdown = useCountdown(open ? rnd!.lock_at : today?.next_lock_at, now)
  const sample = rnd?.cards[0]

  return (
    <div className="welcome">
      <section className="w-hero">
        <div className="w-badge">{open ? <><span className="dot pulse" /> Today's round is open · {countdown}</> : <>Next round in {countdown}</>}</div>
        <h1>Beat Wall Street,<br />one call a day.</h1>
        <p>Read the news. Call <b>Up</b>, <b>Flat</b> or <b>Down</b>. Learn why it moved.</p>
        <button className="btn w-cta" onClick={onStart}>Start playing — it's free</button>
        <button className="w-link" onClick={onLogin}>I already have an account</button>
      </section>

      <section className="w-steps">
        <div className="w-step"><span className="w-num">1</span><div><b>Read</b><span>AI picks 3–5 market-moving stories each morning.</span></div></div>
        <div className="w-step"><span className="w-num">2</span><div><b>Call it</b><span>Up, Flat or Down before 9:25 AM ET.</span></div></div>
        <div className="w-step"><span className="w-num">3</span><div><b>Learn</b><span>After the close, save the lesson as your rule.</span></div></div>
      </section>

      {sample && (
        <section className="w-sample" onClick={onPeek} role="button" tabIndex={0} onKeyDown={e => { if (e.key === 'Enter') onPeek() }}>
          <div className="w-label">Today's first card</div>
          <div className="panel gcard">
            <div className="row"><CategoryPill c={sample.category} /><span className="ticker">{sample.ticker}</span></div>
            <div className="title">{sample.title}</div>
            <div className="summary">{sample.summary}</div>
            <div className="picks">
              {(['up', 'flat', 'down'] as const).map(d => (
                <div key={d} className={`pick ${d}`}>
                  <span className="pick-name">{d === 'up' ? '▲ Up' : d === 'down' ? '▼ Down' : '■ Flat'}</span>
                  <span className="pick-odds">{Math.round(sample.pools[d].share * 100)}%</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      <section className="w-ladder">
        <div className="w-label">Climb from Iron to Challenger</div>
        <div className="w-shields">{LADDER.map(t => <TierBadge key={t} tier={t} size={26} label={false} />)}</div>
      </section>

      <section className="w-why">
        <div><b>Rules, not hunches.</b><span>Every miss becomes a rule you can test.</span></div>
        <div><b>Beat the crowd.</b><span>Correct calls against the crowd pay more.</span></div>
        <div><b>No real money.</b><span>Points only. Pure practice.</span></div>
      </section>

      <button className="btn w-cta dark" onClick={onStart}>Make my first call</button>
    </div>
  )
}

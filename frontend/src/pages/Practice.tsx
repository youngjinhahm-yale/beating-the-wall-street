import { useCallback, useEffect, useState } from 'react'
import type { Session } from '../App'
import type { Card } from '../api'
import { api } from '../api'
import CardView from '../components/CardView'
import { Spinner } from '../components/bits'

export default function Practice({ s }: { s: Session }) {
  const [cards, setCards] = useState<Card[] | null>(null)
  const [done, setDone] = useState(0)
  const [answered, setAnswered] = useState<Card[]>([])
  const [filter, setFilter] = useState<'all' | 'inflection' | 'daily'>('all')
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!s.profile) return
    try {
      const r = await api<{ done: number; cards: Card[] }>('/api/practice')
      setCards(r.cards); setDone(r.done)
    } catch (e) { setErr((e as Error).message) }
  }, [s.profile])
  useEffect(() => { void load() }, [load])

  if (!s.profile) return (
    <div className="panel empty"><h2>Play past rounds</h2>
      <p>Real cards. Instant results.</p>
      <button className="btn primary" onClick={s.needLogin}>Log in to play</button></div>
  )

  async function onAnswered(id: number) {
    const c = await api<Card>(`/api/cards/${id}`)
    setAnswered(a => [c, ...a])
    setCards(cs => (cs ?? []).filter(x => x.id !== id))
    setDone(d => d + 1)
    await s.refresh()
  }

  const shown = (cards ?? []).filter(c => filter === 'all' || c.kind === filter)

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Replay</h1>
          <div className="muted">Play past rounds. Instant results.</div>
        </div>
        <div className="row">
          <span className="pill">{done} replayed</span>
          <div className="tabs">
            {(['all', 'inflection', 'daily'] as const).map(f => (
              <button key={f} className={filter === f ? 'active' : ''} onClick={() => setFilter(f)}>
                {f === 'all' ? 'All' : f === 'inflection' ? 'Big moves' : 'Daily'}</button>
            ))}
          </div>
        </div>
      </div>
      {err && <div className="notice error">{err}</div>}

      {answered.length > 0 && (
        <>
          <h2 style={{ margin: '4px 0 12px' }}>Just played</h2>
          <div className="grid cards" style={{ marginBottom: 24 }}>
            {answered.map(c => <CardView key={c.id} card={c} rules={s.rules} loggedIn onNeedLogin={s.needLogin} onChanged={() => {}} onRuleSaved={s.refresh} />)}
          </div>
        </>
      )}

      {!cards && <div className="empty"><Spinner /></div>}
      {cards && shown.length === 0 && <div className="panel empty"><h2>All caught up</h2><p>New cards after every trading day.</p></div>}
      <div className="grid cards">
        {shown.slice(0, 12).map(c => (
          <CardView key={c.id} card={c} rules={s.rules} loggedIn onNeedLogin={s.needLogin} onChanged={() => onAnswered(c.id)} onRuleSaved={s.refresh} />
        ))}
      </div>
    </>
  )
}

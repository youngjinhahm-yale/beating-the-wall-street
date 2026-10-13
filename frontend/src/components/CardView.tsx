import { useMemo, useState } from 'react'
import type { Card, Direction, Rule } from '../api'
import { api, fmtPct, parsePM } from '../api'
import { CategoryPill, Sparkline, Spinner } from './bits'

interface Props {
  card: Card
  rules: Rule[]
  loggedIn: boolean
  onNeedLogin: () => void
  onChanged: () => void
  onRuleSaved?: () => void
}

const DIRS: Direction[] = ['up', 'flat', 'down']
const ICON: Record<string, string> = { up: '▲', flat: '■', down: '▼', void: '•' }
const NAME: Record<string, string> = { up: 'Up', flat: 'Flat', down: 'Down', void: 'Void' }

export default function CardView({ card, rules, loggedIn, onNeedLogin, onChanged, onRuleSaved }: Props) {
  const [pick, setPick] = useState<Direction | null>(null)
  const [stake, setStake] = useState(30)
  const [reason, setReason] = useState('')
  const [ruleId, setRuleId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [coach, setCoach] = useState<{ feedback: string; suggested_rule: string } | null>(null)
  const [saved, setSaved] = useState<string[]>([])
  const [open, setOpen] = useState<'why' | 'sources' | null>(null)

  const myBet = card.my_bet
  const practice = card.mode === 'practice'
  const canBet = !myBet && (card.mode === 'live' || practice)
  const settled = !!card.outcome
  const pm = parsePM(card.post_mortem)
  const myRules = useMemo(() => [...rules].sort((a, b) => Number(b.category === card.category) - Number(a.category === card.category)), [rules, card.category])

  // What each button shows: live = crowd share + payout; replay = historical frequency.
  const share = (d: Direction) => practice ? card.base_rates[d] : card.pools[d].share
  const mult = (d: Direction) => practice ? null : card.pools[d].multiplier

  async function submit() {
    if (!loggedIn) return onNeedLogin()
    if (!pick) return setErr('Pick Up, Flat or Down.')
    setBusy(true); setErr(null)
    try {
      await api('/api/bets', { body: { card_id: card.id, direction: pick, stake: practice ? 0 : stake, reason, rule_id: ruleId } })
      onChanged()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function askCoach() {
    if (!myBet) return
    setBusy(true); setErr(null)
    try { setCoach(await api(`/api/bets/${myBet.id}/coach`, { method: 'POST' })) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  async function saveRule(text: string) {
    try { await api('/api/rules', { body: { text, category: card.category, source: 'coach' } }); setSaved(s => [...s, text]); onRuleSaved?.() }
    catch (e) { setErr((e as Error).message) }
  }

  const won = myBet && settled && card.outcome !== 'void' ? myBet.direction === card.outcome : null
  const chosen = myBet?.direction ?? pick

  return (
    <article className="panel gcard" aria-labelledby={`card-${card.id}`}>
      <div className="spread">
        <div className="row"><CategoryPill c={card.category} /><span className="ticker">{card.ticker}</span></div>
        {card.mode === 'live' && !myBet && <span className="status live"><span className="dot pulse" />Open</span>}
        {card.mode === 'locked' && <span className="status locked">Locked</span>}
        {practice && !myBet && <span className="status practice">{card.kind === 'inflection' ? 'Inflection replay' : 'Replay'}</span>}
      </div>

      <div className="title" id={`card-${card.id}`}>{card.title}</div>
      <div className="summary">{card.summary}</div>

      {card.chart.length > 1 && (
        <div className="chartline">
          <Sparkline data={card.chart} height={38} />
          {card.context?.last_close != null && <span className="mono faint">{card.context.last_close}</span>}
        </div>
      )}

      {/* The three picks double as the odds display. */}
      <div className="picks" role="radiogroup" aria-label="Your call">
        {DIRS.map(d => {
          const isOutcome = settled && card.outcome === d
          const cls = ['pick', d, chosen === d ? 'sel' : '', isOutcome ? 'outcome' : ''].join(' ')
          return (
            <button key={d} className={cls} disabled={!canBet} onClick={() => setPick(d)} role="radio" aria-checked={chosen === d}>
              <span className="pick-name">{ICON[d]} {NAME[d]}{d === 'flat' && <span className="pick-band">±{card.flat_band}%</span>}</span>
              <span className="pick-odds">{Math.round(share(d) * 100)}%{mult(d) ? ` · ${mult(d)}x` : ''}</span>
            </button>
          )
        })}
      </div>

      {canBet && (
        <div className="betbox">
          {!practice && (
            <div className="row" style={{ gap: 12 }}>
              <input type="range" min={10} max={100} step={5} value={stake} onChange={e => setStake(+e.target.value)} aria-label="Stake" />
              <span className="mono stake">{stake} pts</span>
            </div>
          )}
          {myRules.length > 0 && (
            <select value={ruleId ?? ''} aria-label="Use a rule from your rulebook" onChange={e => {
              const id = e.target.value ? +e.target.value : null
              setRuleId(id); const r = rules.find(x => x.id === id); if (r) setReason(r.text)
            }}>
              <option value="">Use one of my rules…</option>
              {myRules.map(r => <option key={r.id} value={r.id}>{r.text}{r.uses ? ` (${r.wins}/${r.uses})` : ''}</option>)}
            </select>
          )}
          <input placeholder="Why? (one line)" value={reason} maxLength={280} aria-label="Reason"
            onChange={e => { setReason(e.target.value); if (ruleId) setRuleId(null) }} />
          <button className="btn primary block" onClick={submit} disabled={busy}>
            {busy ? <Spinner /> : practice ? 'Reveal' : pick ? `Bet ${stake} on ${NAME[pick]}` : 'Pick a side'}
          </button>
        </div>
      )}

      {myBet && !settled && (
        <div className="mybet">
          <span>You: <b className={`c-${myBet.direction}`}>{ICON[myBet.direction]} {NAME[myBet.direction]}</b> · {myBet.stake} pts</span>
          <span className="faint">{card.mode === 'live' ? 'locks 9:25 ET' : 'settles 4 PM ET'}</span>
        </div>
      )}

      {myBet && settled && (
        <div className={`result ${won === null ? 'neutral' : won ? 'win' : 'loss'}`}>
          <div className="spread">
            <b>{won === null ? 'Void · refunded' : won ? '✓ Correct' : '✗ Missed'}</b>
            <span className="mono">{fmtPct(card.move_pct)}
              {myBet.lp_change != null && <> · {myBet.lp_change >= 0 ? '+' : ''}{myBet.lp_change.toFixed(1)} LP</>}
              {!myBet.practice && myBet.payout ? <> · +{myBet.payout} pts</> : null}</span>
          </div>
        </div>
      )}

      {myBet && settled && card.lesson_rule && (
        <div className="lesson">
          <span>{card.lesson_rule}</span>
          <button className="btn small" disabled={saved.includes(card.lesson_rule)} onClick={() => saveRule(card.lesson_rule!)}>
            {saved.includes(card.lesson_rule) ? 'Saved' : '+ Rule'}</button>
        </div>
      )}

      {coach && (
        <div className="notice">
          <div>{coach.feedback}</div>
          <div className="lesson" style={{ marginTop: 8 }}>
            <span>{coach.suggested_rule}</span>
            <button className="btn small" disabled={saved.includes(coach.suggested_rule)} onClick={() => saveRule(coach.suggested_rule)}>
              {saved.includes(coach.suggested_rule) ? 'Saved' : '+ Rule'}</button>
          </div>
        </div>
      )}

      {open === 'why' && pm && (
        <div className="why">
          <div>{pm.what_happened}</div>
          {pm.why && <div className="muted">{pm.why}</div>}
        </div>
      )}
      {open === 'sources' && (
        <div className="sources">
          {card.sources.map((s, i) => <a key={i} href={s.url} target="_blank" rel="noreferrer noopener">{s.source}: {s.title}</a>)}
        </div>
      )}

      {err && <div className="notice error" role="alert">{err}</div>}

      <div className="cardfoot">
        {myBet && settled && pm && <button className="link" onClick={() => setOpen(o => o === 'why' ? null : 'why')}>{open === 'why' ? 'Hide why' : 'Why it moved'}</button>}
        {won === false && !coach && <button className="link" onClick={askCoach} disabled={busy}>Ask Coach</button>}
        {card.sources.length > 0 && <button className="link" onClick={() => setOpen(o => o === 'sources' ? null : 'sources')}>{open === 'sources' ? 'Hide sources' : `Sources (${card.sources.length})`}</button>}
      </div>
    </article>
  )
}

import type { Direction, Pool } from '../api'
import { CATEGORY_LABEL } from '../api'

export const TIER_COLORS: Record<string, [string, string]> = {
  Unranked: ['#94a3b8', '#cbd5e1'], Iron: ['#5b5f66', '#9aa0a8'], Bronze: ['#8c5a35', '#d39a6a'],
  Silver: ['#7d8a96', '#d5dde5'], Gold: ['#b8860b', '#f5d36b'], Platinum: ['#1f8f84', '#7fe0d2'],
  Emerald: ['#137a4a', '#5fd99a'], Diamond: ['#3d5bd9', '#9fb4ff'], Master: ['#7b3fb5', '#d0a6ff'],
  Grandmaster: ['#b3261e', '#ff8f87'], Challenger: ['#c58a00', '#fff1a8'],
}

export function TierBadge({ tier, size = 18, big = false, label = true }: { tier: string; size?: number; big?: boolean; label?: boolean }) {
  const [dark, light] = TIER_COLORS[tier] ?? TIER_COLORS.Unranked
  const id = `tg-${tier}-${size}`
  return (
    <span className={`tier${big ? ' big' : ''}`} title={`${tier} tier`}>
      <svg width={size} height={size * 1.15} viewBox="0 0 20 23" aria-hidden="true">
        <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={light} /><stop offset="1" stopColor={dark} /></linearGradient></defs>
        <path d="M10 1 L18.5 4.5 V11.5 C18.5 16.5 14.8 20 10 22 C5.2 20 1.5 16.5 1.5 11.5 V4.5 Z" fill={`url(#${id})`} stroke={dark} strokeWidth="1.2" />
        {tier === 'Master' || tier === 'Grandmaster' || tier === 'Challenger'
          ? <path d="M5.5 9 L8 12 L10 7.5 L12 12 L14.5 9 L13.5 15 H6.5 Z" fill="#fff" opacity=".9" />
          : <path d="M10 6 L12.2 10.5 L10 15.5 L7.8 10.5 Z" fill="#fff" opacity={tier === 'Unranked' ? 0.5 : 0.85} />}
      </svg>
      {label && <span style={{ color: dark }}>{tier}</span>}
    </span>
  )
}

export function CategoryPill({ c }: { c: string }) {
  return <span className={`pill ${c}`}>{CATEGORY_LABEL[c] ?? c}</span>
}

export function DirPill({ d }: { d: string }) {
  const arrow = d === 'up' ? '▲' : d === 'down' ? '▼' : d === 'flat' ? '■' : '•'
  return <span className={`pill ${d}`}>{arrow} {d === 'void' ? 'Void' : d[0].toUpperCase() + d.slice(1)}</span>
}

export function Sparkline({ data, height = 56, color }: { data: { d: string; c: number }[]; height?: number; color?: string }) {
  if (!data || data.length < 2) return null
  const w = 300
  const vals = data.map(p => p.c)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const span = hi - lo || 1
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, height - 4 - ((v - lo) / span) * (height - 8)])
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')
  const rising = vals[vals.length - 1] >= vals[0]
  const stroke = color ?? (rising ? 'var(--up)' : 'var(--down)')
  const last = pts[pts.length - 1]
  return (
    <svg viewBox={`0 0 ${w} ${height}`} width="100%" height={height} preserveAspectRatio="none" role="img"
      aria-label={`Price trend: ${vals[0]} to ${vals[vals.length - 1]}`}>
      <path d={`${d} L${w} ${height} L0 ${height} Z`} fill={stroke} opacity=".08" />
      <path d={d} fill="none" stroke={stroke} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="3" fill={stroke} />
    </svg>
  )
}

export function OddsBar({ pools, highlight }: { pools: Record<Direction, Pool>; highlight?: string }) {
  const dirs: Direction[] = ['up', 'flat', 'down']
  return (
    <div>
      <div className="odds" role="img" aria-label={dirs.map(d => `${d} ${Math.round(pools[d].share * 100)}%`).join(', ')}>
        {dirs.map(d => (
          <div key={d} className={d} style={{ flex: Math.max(pools[d].share, 0.08), outline: highlight === d ? '2px solid currentColor' : undefined, outlineOffset: -2 }}>
            {Math.round(pools[d].share * 100)}%
          </div>
        ))}
      </div>
      <div className="odds-legend">
        {dirs.map(d => (
          <span key={d} style={{ textAlign: d === 'up' ? 'left' : d === 'flat' ? 'center' : 'right' }}>
            pays <b className="mono" style={{ color: 'var(--ink)' }}>{pools[d].multiplier ? `${pools[d].multiplier}x` : '-'}</b>
            <span className="hide-sm"> · {pools[d].bettors} bet{pools[d].bettors === 1 ? '' : 's'}</span></span>
        ))}
      </div>
    </div>
  )
}

export function Spinner() { return <span className="spinner" aria-label="Loading" /> }

export function useCountdown(target: string | null | undefined, now: number): string {
  if (!target) return '--:--:--'
  const s = Math.max(0, Math.floor((Date.parse(target) - now) / 1000))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  if (h >= 48) return `${Math.floor(h / 24)}d ${h % 24}h`
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

// Thin fetch wrapper. The session token lives in localStorage (wrapped: storage can be blocked).

export type Direction = 'up' | 'flat' | 'down'

export interface Pool { players: number; bettors: number; pool: number; share: number; multiplier: number | null }
export interface Bet {
  id: number; card_id: number; direction: Direction; stake: number; reason: string; rule_id: number | null
  practice: number; crowd_share: number | null; payout: number | null; lp_change: number | null
  created_at: string; settled_at: string | null
}
export interface PostMortem { what_happened: string; why: string; driver: string }
export interface Card {
  id: number; round_id: number | null; kind: 'daily' | 'inflection'; category: string; title: string
  summary: string; ticker: string; ticker_name: string; question: string; event_time: string | null
  flat_band: number; created_at: string; context: { background?: string; last_close?: number; last_close_date?: string }
  sources: { title: string; url: string; source: string; published_at: string }[]
  chart: { d: string; c: number }[]; base_rates: Record<Direction, number>
  mode: 'live' | 'locked' | 'practice'; pools: Record<Direction, Pool>
  trade_date: string | null; lock_at: string | null; my_bet: Bet | null
  outcome?: Direction | 'void'; move_pct?: number; ref_price?: number; close_price?: number
  post_mortem?: string | null; lesson_rule?: string | null
}
export interface Snapshot { name: string; ticker: string; as_of: string; close: number; change_pct: number }
export interface Round { id: number; trade_date: string; status: string; lock_at: string; brief: string | null; snapshot: Snapshot[]; cards: Card[] }
export interface Today { server_time: string; next_trade_date: string; next_lock_at: string; round: Round | null }
export interface CategoryRating { category: string; lp: number; wins: number; losses: number; bets: number; tier: string; placement_left: number }
export interface Profile {
  id: number; username: string; points: number; streak: number; is_admin: boolean
  categories: CategoryRating[]; overall: { lp: number | null; tier: string }
  settled_bets: number; correct_bets: number; live_bets: number
}
export interface Rule { id: number; text: string; category: string; source: string; uses: number; wins: number; win_rate: number | null; created_at: string }
export interface Post {
  id: number; user_id: number; username: string; card_id: number | null; category: string; parent_id: number | null
  title: string | null; body: string; status: string; mod_reason: string | null; mod_by: string | null; created_at: string
  author_tier: string; author_record: string; score: number; my_vote: number; is_mine: boolean
  author_bet: { direction: Direction; stake: number } | null; card_title?: string | null; card_outcome?: string | null
  replies?: number; children?: Post[]
}

const TOKEN_KEY = 'btws_token'

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t: string | null) {
  try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY) } catch { /* storage blocked */ }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const tok = getToken()
  if (tok) headers.Authorization = `Bearer ${tok}`
  const res = await fetch(path, { method: opts.method ?? (opts.body ? 'POST' : 'GET'), headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined })
  const text = await res.text()
  let data: unknown = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!res.ok) {
    const d = (data as { detail?: unknown })?.detail
    const msg = typeof d === 'string' ? d : Array.isArray(d) ? 'Please check the form fields.' : `Request failed (${res.status})`
    throw new ApiError(res.status, msg)
  }
  return data as T
}

export const CATEGORIES = ['macro', 'company', 'geopolitics', 'sector'] as const
export const CATEGORY_LABEL: Record<string, string> = {
  macro: 'Macro', company: 'Company', geopolitics: 'Geopolitics', sector: 'Sector',
}
export const DIR_LABEL: Record<string, string> = { up: 'Up', flat: 'Flat', down: 'Down', void: 'Void' }

export function parsePM(s?: string | null): PostMortem | null {
  if (!s) return null
  try { return JSON.parse(s) as PostMortem } catch { return { what_happened: s, why: '', driver: '' } }
}

export function fmtPct(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined) return '-'
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

export function timeAgo(iso: string): string {
  const s = (Date.now() - Date.parse(iso)) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function etTime(iso: string): string {
  return new Date(iso).toLocaleString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit',
    month: 'short', day: 'numeric' }) + ' ET'
}

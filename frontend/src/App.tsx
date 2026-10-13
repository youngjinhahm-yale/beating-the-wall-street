import { useCallback, useEffect, useState } from 'react'
import type { Profile, Rule } from './api'
import { api, getToken, setToken } from './api'
import { TierBadge } from './components/bits'
import AuthModal from './pages/AuthModal'
import Community from './pages/Community'
import Engine from './pages/Engine'
import MyEdge from './pages/MyEdge'
import Practice from './pages/Practice'
import Ranks from './pages/Ranks'
import TodayPage from './pages/Today'

const PAGES = [
  { id: 'today', label: 'Today' },
  { id: 'practice', label: 'Replay' },
  { id: 'edge', label: 'My Edge' },
  { id: 'ranks', label: 'Ranks' },
  { id: 'community', label: 'Community' },
  { id: 'engine', label: 'Under the Hood' },
] as const
type PageId = typeof PAGES[number]['id']

function pageFromHash(): PageId {
  const h = window.location.hash.replace('#', '')
  return (PAGES.find(p => p.id === h)?.id ?? 'today') as PageId
}

export interface Session {
  profile: Profile | null
  rules: Rule[]
  refresh: () => Promise<void>
  needLogin: () => void
}

export default function App() {
  const [page, setPage] = useState<PageId>(pageFromHash)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [rules, setRules] = useState<Rule[]>([])
  const [authOpen, setAuthOpen] = useState(false)

  const refresh = useCallback(async () => {
    if (!getToken()) { setProfile(null); setRules([]); return }
    try {
      const [p, r] = await Promise.all([api<Profile>('/api/me'), api<Rule[]>('/api/rules')])
      setProfile(p); setRules(r)
    } catch { setToken(null); setProfile(null); setRules([]) }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const on = () => setPage(pageFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const go = (id: PageId) => { window.location.hash = id; setPage(id); window.scrollTo(0, 0) }
  const session: Session = { profile, rules, refresh, needLogin: () => setAuthOpen(true) }

  async function logout() {
    try { await api('/api/logout', { method: 'POST' }) } catch { /* ignore */ }
    setToken(null); await refresh()
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="logo" href="#today" onClick={e => { e.preventDefault(); go('today') }}>
            <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#0f1b2d" /><path d="M6 22l6-7 5 4 9-10" fill="none" stroke="#22c55e" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /><circle cx="26" cy="9" r="2.5" fill="#f5b83d" /></svg>
            <span className="hide-md">Beating the Wall Street</span>
          </a>
          <nav className="nav" aria-label="Main">
            {PAGES.map(p => (
              <button key={p.id} className={page === p.id ? 'active' : ''} onClick={() => go(p.id)} aria-current={page === p.id ? 'page' : undefined}>{p.label}</button>
            ))}
          </nav>
          {profile ? (
            <div className="userchip">
              <TierBadge tier={profile.overall.tier} label={false} />
              <span className="hide-md"><b>{profile.username}</b></span>
              <span className="points" title="Points">◆ {profile.points.toLocaleString()}</span>
              {profile.streak > 0 && <span className="pill hide-sm" title="Daily streak">🔥 {profile.streak}</span>}
              <button className="btn ghost small" onClick={logout}>Log out</button>
            </div>
          ) : (
            <button className="btn primary small" onClick={() => setAuthOpen(true)}>Log in / Join</button>
          )}
        </div>
      </header>

      <main className="page">
        {page === 'today' && <TodayPage s={session} />}
        {page === 'practice' && <Practice s={session} />}
        {page === 'edge' && <MyEdge s={session} />}
        {page === 'ranks' && <Ranks s={session} />}
        {page === 'community' && <Community s={session} />}
        {page === 'engine' && <Engine s={session} />}
      </main>

      <footer className="footer">
        Education only - not investment advice. Points have no cash value and can't be bought, sold or withdrawn.
        Market data: Yahoo Finance via yfinance. News: publisher RSS feeds and Google News. AI: gpt-6-luna.
      </footer>

      {authOpen && <AuthModal onClose={() => setAuthOpen(false)} onDone={async () => { setAuthOpen(false); await refresh() }} />}
    </>
  )
}

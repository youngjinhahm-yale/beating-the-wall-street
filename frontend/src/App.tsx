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
import Welcome from './pages/Welcome'

const PAGES = [
  { id: 'today', label: 'Today', icon: 'M4 13h4v7H4zM10 8h4v12h-4zM16 4h4v16h-4z' },
  { id: 'practice', label: 'Replay', icon: 'M12 5V2L7 6l5 4V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z' },
  { id: 'edge', label: 'My Edge', icon: 'M6 3h9l4 4v14H6zM9 10h7M9 14h7M9 18h4' },
  { id: 'ranks', label: 'Ranks', icon: 'M12 2l8 3v6c0 5-3.5 9-8 11-4.5-2-8-6-8-11V5z' },
  { id: 'community', label: 'Community', icon: 'M4 5h16v11H9l-5 4z' },
  { id: 'engine', label: 'Under the Hood', icon: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3' },
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
  const [authOpen, setAuthOpen] = useState<false | 'register' | 'login'>(false)
  const [checked, setChecked] = useState(false)
  const [hasHash, setHasHash] = useState(() => window.location.hash.length > 1)

  const refresh = useCallback(async () => {
    if (!getToken()) { setProfile(null); setRules([]); setChecked(true); return }
    try {
      const [p, r] = await Promise.all([api<Profile>('/api/me'), api<Rule[]>('/api/rules')])
      setProfile(p); setRules(r)
    } catch { setToken(null); setProfile(null); setRules([]) }
    setChecked(true)
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const on = () => { setPage(pageFromHash()); setHasHash(window.location.hash.length > 1) }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const go = (id: PageId) => { window.location.hash = id; setPage(id); setHasHash(true); window.scrollTo(0, 0) }
  const session: Session = { profile, rules, refresh, needLogin: () => setAuthOpen('register') }
  // First visit, logged out, no deep link: show the welcome screen instead of the game.
  const welcome = checked && !profile && !hasHash

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
          <button className={`btn ghost small gear${page === 'engine' ? ' on' : ''}`} onClick={() => go('engine')} aria-label="Under the Hood">
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
          {profile ? (
            <div className="userchip">
              <TierBadge tier={profile.overall.tier} label={false} />
              <span className="hide-md"><b>{profile.username}</b></span>
              <span className="points" title="Points">◆ {profile.points.toLocaleString()}</span>
              {profile.streak > 0 && <span className="pill hide-sm" title="Daily streak">🔥 {profile.streak}</span>}
              <button className="btn ghost small hide-sm" onClick={logout}>Log out</button>
            </div>
          ) : (
            <button className="btn primary small" onClick={() => setAuthOpen('login')}>Log in</button>
          )}
        </div>
      </header>

      <main className={welcome ? 'page wpage' : 'page'}>
        {welcome && <Welcome onStart={() => setAuthOpen('register')} onLogin={() => setAuthOpen('login')} onPeek={() => go('today')} />}
        {!welcome && page === 'today' && <TodayPage s={session} />}
        {!welcome && page === 'practice' && <Practice s={session} />}
        {!welcome && page === 'edge' && <MyEdge s={session} />}
        {!welcome && page === 'ranks' && <Ranks s={session} />}
        {!welcome && page === 'community' && <Community s={session} />}
        {!welcome && page === 'engine' && <Engine s={session} />}
      </main>

      <footer className="footer">
        Education only - not investment advice. Points have no cash value and can't be bought, sold or withdrawn.
        Market data: Yahoo Finance. News: publisher RSS feeds and Google News. AI: gpt-6-luna.
      </footer>

      {!welcome && <nav className="tabbar" aria-label="Main (mobile)">
        {PAGES.slice(0, 5).map(p => (
          <button key={p.id} className={page === p.id ? 'active' : ''} onClick={() => go(p.id)} aria-current={page === p.id ? 'page' : undefined}>
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d={p.icon} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" /></svg>
            <span>{p.label}</span>
          </button>
        ))}
      </nav>}

      {authOpen && <AuthModal initialMode={authOpen} onClose={() => setAuthOpen(false)}
        onDone={async () => { setAuthOpen(false); await refresh(); go(page) }} />}
    </>
  )
}

import { useState } from 'react'
import { api, setToken } from '../api'
import { Spinner } from '../components/bits'

export default function AuthModal({ onClose, onDone, initialMode = 'register' }: { onClose: () => void; onDone: () => void; initialMode?: 'login' | 'register' }) {
  const [mode, setMode] = useState<'login' | 'register'>(initialMode)
  const [username, setUsername] = useState('')
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      const r = await api<{ token: string }>(`/api/${mode}`, { body: { username, pin } })
      setToken(r.token); onDone()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="modal-back" onClick={onClose} role="presentation">
      <form className="modal stack" onClick={e => e.stopPropagation()} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <h2 id="auth-title">{mode === 'register' ? 'Join the game' : 'Welcome back'}</h2>
        <p className="muted" style={{ margin: 0 }}>
          {mode === 'register' ? 'Pick a username and a 4-8 digit PIN. No email needed. You start with 1,000 points.' : 'Log in with your username and PIN.'}
        </p>
        <div className="tabs" role="tablist">
          <button type="button" className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>New player</button>
          <button type="button" className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>Log in</button>
        </div>
        <label className="stack" style={{ gap: 4 }}><span className="faint">Username</span>
          <input autoFocus value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" placeholder="e.g. value_hunter" /></label>
        <label className="stack" style={{ gap: 4 }}><span className="faint">PIN</span>
          <input type="password" inputMode="numeric" value={pin} onChange={e => setPin(e.target.value.replace(/\D/g, '').slice(0, 8))}
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'} placeholder="4-8 digits" /></label>
        {err && <div className="notice error" role="alert">{err}</div>}
        <button className="btn primary block" disabled={busy || username.length < 3 || pin.length < 4}>
          {busy ? <Spinner /> : mode === 'register' ? 'Create account' : 'Log in'}</button>
        <button type="button" className="btn ghost block" onClick={onClose}>Cancel</button>
      </form>
    </div>
  )
}

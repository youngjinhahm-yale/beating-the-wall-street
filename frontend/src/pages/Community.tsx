import { useCallback, useEffect, useState } from 'react'
import type { Session } from '../App'
import type { Post } from '../api'
import { api, CATEGORIES, CATEGORY_LABEL, timeAgo } from '../api'
import { CategoryPill, DirPill, Spinner, TierBadge } from '../components/bits'

type ModResult = { id: number; status: string; reason: string; by: string }

function ModNote({ p }: { p: Post }) {
  if (p.status === 'flagged') return <div className="modnote">⚠ Moderator note: {p.mod_reason}</div>
  if (p.status === 'rejected' && p.is_mine) return <div className="modnote bad">Only you can see this - removed by {p.mod_by === 'rule_filter' ? 'the safety filter' : 'the AI moderator'}: {p.mod_reason}</div>
  if (p.status === 'hidden' && p.is_mine) return <div className="modnote">Only you can see this - {p.mod_reason}</div>
  return null
}

function Composer({ s, parent, category, cardId, onDone, insight }: {
  s: Session; parent?: number; category?: string; cardId?: number | null; onDone: () => void; insight?: boolean
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [cat, setCat] = useState(category ?? 'macro')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: string; text: string } | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!s.profile) return s.needLogin()
    setBusy(true); setMsg(null)
    try {
      const r = await api<ModResult>('/api/posts', { body: { body, title: insight ? title : null, parent_id: parent ?? null, category: cat, card_id: cardId ?? null } })
      if (r.status === 'visible') { setMsg({ kind: 'ok', text: 'Posted. The AI moderator approved it.' }); setBody(''); setTitle('') }
      else if (r.status === 'flagged') { setMsg({ kind: 'warn', text: `Posted with a warning label: ${r.reason}` }); setBody(''); setTitle('') }
      else setMsg({ kind: 'error', text: `Not published (${r.by === 'rule_filter' ? 'safety filter' : r.by === 'moderator' ? 'AI moderator' : 'held'}): ${r.reason}` })
      onDone()
    } catch (e) { setMsg({ kind: 'error', text: (e as Error).message }) } finally { setBusy(false) }
  }

  return (
    <form className="stack" onSubmit={submit}>
      {insight && <input placeholder="Insight title" value={title} onChange={e => setTitle(e.target.value)} maxLength={200} aria-label="Title" />}
      <textarea rows={insight ? 4 : 2} placeholder={insight ? 'Share your reasoning: what you watch, the rule you use, and your evidence.' : 'Add a reply...'}
        value={body} onChange={e => setBody(e.target.value)} maxLength={4000} aria-label="Post text" />
      <div className="row">
        {!parent && !category && (
          <select value={cat} onChange={e => setCat(e.target.value)} style={{ maxWidth: 170 }} aria-label="Category">
            {CATEGORIES.map(c => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
          </select>
        )}
        <button className="btn primary small" disabled={busy || body.trim().length < 3 || (insight && title.trim().length < 3)}>
          {busy ? <Spinner /> : insight ? 'Post insight' : parent ? 'Reply' : 'Comment'}</button>
      </div>
      {msg && <div className={`notice ${msg.kind}`} role="status">{msg.text}</div>}
    </form>
  )
}

function PostView({ p, s, onChanged, depth = 0 }: { p: Post; s: Session; onChanged: () => void; depth?: number }) {
  const [replying, setReplying] = useState(false)
  const [score, setScore] = useState(p.score)
  const [mine, setMine] = useState(p.my_vote)
  const [note, setNote] = useState<string | null>(null)

  async function vote(v: number) {
    if (!s.profile) return s.needLogin()
    try { const r = await api<{ score: number; my_vote: number }>(`/api/posts/${p.id}/vote`, { body: { value: mine === v ? 0 : v } }); setScore(r.score); setMine(r.my_vote) }
    catch (e) { setNote((e as Error).message) }
  }
  async function report() {
    if (!s.profile) return s.needLogin()
    const r = await api<{ hidden: boolean }>(`/api/posts/${p.id}/report`, { method: 'POST' })
    setNote(r.hidden ? 'Reported. The post is now hidden for review.' : 'Reported. Thanks.')
  }

  return (
    <div className="post">
      <div className="vote" aria-label="Votes">
        <button className={mine === 1 ? 'on' : ''} onClick={() => vote(1)} aria-label="Upvote">▲</button>
        <span className="mono">{score % 1 ? score.toFixed(1) : score}</span>
        <button className={mine === -1 ? 'on' : ''} onClick={() => vote(-1)} aria-label="Downvote">▼</button>
      </div>
      <div>
        <div className="post-meta">
          <b style={{ color: 'var(--ink)' }}>{p.username}</b>
          <TierBadge tier={p.author_tier} />
          <span className="mono">{CATEGORY_LABEL[p.category]} {p.author_record}</span>
          {p.author_bet && <span>bet <DirPill d={p.author_bet.direction} /></span>}
          <span>· {timeAgo(p.created_at)}</span>
        </div>
        {p.title && depth === 0 && <h3 style={{ marginTop: 4 }}>{p.title}</h3>}
        <div className="post-body">{p.body}</div>
        <ModNote p={p} />
        <div className="row" style={{ gap: 4 }}>
          {depth < 2 && <button className="btn ghost small" onClick={() => s.profile ? setReplying(r => !r) : s.needLogin()}>Reply</button>}
          {!p.is_mine && <button className="btn ghost small" onClick={report}>Report</button>}
        </div>
        {note && <div className="faint">{note}</div>}
        {replying && <div style={{ marginTop: 8 }}><Composer s={s} parent={p.id} category={p.category} onDone={() => { setReplying(false); onChanged() }} /></div>}
        {p.children && p.children.length > 0 && (
          <div className="children">{p.children.map(c => <PostView key={c.id} p={c} s={s} onChanged={onChanged} depth={depth + 1} />)}</div>
        )}
      </div>
    </div>
  )
}

export default function Community({ s }: { s: Session }) {
  const [cat, setCat] = useState('')
  const [list, setList] = useState<Post[] | null>(null)
  const [open, setOpen] = useState<Post | null>(null)
  const [compose, setCompose] = useState<'insight' | 'comment' | null>(null)

  const load = useCallback(async () => {
    setList(await api<Post[]>(`/api/posts${cat ? `?category=${cat}` : ''}`))
  }, [cat])
  useEffect(() => { void load() }, [load, s.profile?.id])

  async function openThread(id: number) { setOpen(await api<Post>(`/api/posts/${id}`)); window.scrollTo(0, 0) }

  const myTier = (c: string) => s.profile?.categories.find(x => x.category === c)?.tier ?? 'Unranked'

  if (open) return (
    <>
      <button className="btn ghost small" onClick={() => { setOpen(null); void load() }} style={{ marginBottom: 12 }}>← All threads</button>
      <div className="panel">
        {open.card_title && <div className="faint" style={{ marginBottom: 8 }}>On card: <b>{open.card_title}</b>{open.card_outcome && <> · result <DirPill d={open.card_outcome} /></>}</div>}
        <PostView p={open} s={s} onChanged={() => openThread(open.id)} />
      </div>
    </>
  )

  return (
    <>
      <div className="page-head">
        <div><h1>Community</h1><div className="muted">Talk about today's calls.</div></div>
        <div className="row">
          <button className="btn" onClick={() => setCompose(c => c === 'comment' ? null : 'comment')}>Comment</button>
          <button className="btn primary" onClick={() => setCompose(c => c === 'insight' ? null : 'insight')}>New insight</button>
        </div>
      </div>

      {compose && (
        <div className="panel" style={{ marginBottom: 16 }}>
          {compose === 'insight' && s.profile && (
            <div className="faint" style={{ marginBottom: 8 }}>Your tiers: {CATEGORIES.map(c => `${CATEGORY_LABEL[c]} ${myTier(c)}`).join(' · ')}</div>
          )}
          <Composer s={s} insight={compose === 'insight'} onDone={load} />
        </div>
      )}

      <div className="tabs" style={{ marginBottom: 14 }}>
        <button className={cat === '' ? 'active' : ''} onClick={() => setCat('')}>All</button>
        {CATEGORIES.map(c => <button key={c} className={cat === c ? 'active' : ''} onClick={() => setCat(c)}>{CATEGORY_LABEL[c]}</button>)}
      </div>

      {!list && <div className="empty"><Spinner /></div>}
      {list && list.length === 0 && <div className="panel empty"><h2>No posts yet</h2><p>Be the first.</p></div>}
      <div className="stack">
        {list?.map(p => (
          <div key={p.id} className="panel" style={{ cursor: 'pointer' }} onClick={() => openThread(p.id)} role="button" tabIndex={0}
            onKeyDown={e => { if (e.key === 'Enter') void openThread(p.id) }}>
            <div className="spread" style={{ alignItems: 'flex-start' }}>
              <div className="stack" style={{ gap: 4 }}>
                <div className="row wrap"><CategoryPill c={p.category} />{p.title ? <span className="pill">Insight</span> : <span className="pill">Comment</span>}
                  {p.status === 'flagged' && <span className="pill locked">⚠ flagged</span>}
                  {p.status !== 'visible' && p.status !== 'flagged' && p.is_mine && <span className="pill down">only you see this</span>}</div>
                <h3>{p.title ?? (p.body.length > 120 ? p.body.slice(0, 120) + '...' : p.body)}</h3>
                <div className="post-meta"><b style={{ color: 'var(--ink)' }}>{p.username}</b><TierBadge tier={p.author_tier} />
                  <span>· {timeAgo(p.created_at)}</span>{p.card_title && <span>· on "{p.card_title}"</span>}</div>
              </div>
              <div className="stack" style={{ alignItems: 'flex-end', gap: 2 }}>
                <span className="mono" style={{ fontWeight: 700 }}>▲ {p.score % 1 ? p.score.toFixed(1) : p.score}</span>
                <span className="faint">{p.replies} repl{p.replies === 1 ? 'y' : 'ies'}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

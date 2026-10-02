import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../style.css';

const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
const navigation = [['scan-section', 'Scan'], ['digest-section', 'Digest'], ['excluded-section', 'Excluded']];
const filters = [['All', 'All notable'], ['Playtest', 'Playtests'], ['Application update', 'Applications'], ['Mentor', 'Mentor'], ['Credit card', 'Credit cards']];
const metrics = [['scanned', 'Threads scanned'], ['notable', 'Notable threads'], ['excluded', 'Excluded threads'], ['failed', 'Retrieval failures']];
const date = value => new Date(value).toLocaleString();

async function request(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'X-Scanner-Token': token, ...options.headers } });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not connect to the dashboard.');
  return result;
}

function Header({ excludedRef }) {
  const [scrolled, setScrolled] = useState(false);
  const [active, setActive] = useState('scan-section');
  useEffect(() => {
    function update() {
      setScrolled(window.scrollY > 24);
      let current = 'scan-section';
      for (const [id] of navigation) {
        if (document.getElementById(id)?.getBoundingClientRect().top <= window.innerHeight * .35) current = id;
      }
      setActive(current);
    }
    update();
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    return () => { window.removeEventListener('scroll', update); window.removeEventListener('resize', update); };
  }, []);
  function navigate(id) {
    if (id === 'excluded-section') excludedRef.current.open = true;
    document.getElementById(id).scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
  return <header className={`site-header${scrolled ? ' scrolled' : ''}`}>
    <nav className="navigation" aria-label="Main navigation">
      <button className="wordmark" onClick={() => navigate('scan-section')} aria-label="Personal Scanner home">PS<span>.</span></button>
      <div className="nav-links">{navigation.map(([id, label]) => <button key={id} className={active === id ? 'active' : ''} aria-current={active === id ? 'location' : undefined} onClick={() => navigate(id)}>{label}</button>)}</div>
    </nav>
  </header>;
}

function ThreadCard({ item }) {
  // React escapes mailbox text. Never render email HTML or load external images.
  return <article className="card">
    <div className="card-top"><span className="category">{item.category}</span><span className="unread-dot">{item.unread ? 'Unread' : 'Read'}</span></div>
    <h2>{item.subject}</h2><div className="meta">{item.sender} · {date(item.received)}</div>
    {item.review && <p className="warning">Needs review: possible application correspondence.</p>}
    <p className="type">{item.summary_type}</p><p className="summary">{item.summary}</p><p className="reason">Why included: {item.reason}</p>
    {item.action_quote && <p>Requested action (source quote): {item.action_quote}</p>}
    {item.evidence?.map((quote, i) => <blockquote key={i}>{quote}</blockquote>)}
    {item.warnings.map((warning, i) => <p className="warning" key={i}>{warning}</p>)}
    {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer">Open original in Gmail ↗</a>}
    <details><summary>Read thread source</summary>{item.messages.map((mail, i) => <div className="message" key={mail.id || i}>
      <strong>{mail.sender} · {date(mail.received)}</strong><p>{mail.body}</p>
      {!!mail.attachments.length && <p>Attachments not read: {mail.attachments.join(', ')}</p>}
    </div>)}</details>
  </article>;
}

function App() {
  const [state, setState] = useState(null);
  const [limit, setLimit] = useState('40');
  const [category, setCategory] = useState('All');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const excludedRef = useRef(null);
  const initialized = useRef(false);
  const inputRef = useRef(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    let cancelled = false, timer;
    async function poll() {
      const version = requestVersion.current;
      try {
        const next = await request('/api/state');
        if (cancelled || version !== requestVersion.current) return;
        if (!initialized.current) { setLimit(String(next.max_threads)); initialized.current = true; }
        setState(next); setConnected(true); setError('');
      } catch (failure) {
        if (!cancelled && version === requestVersion.current) { setConnected(false); setError(failure.message); }
      } finally {
        if (!cancelled) timer = setTimeout(poll, 1500);
      }
    }
    poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  async function scan(event) {
    event.preventDefault();
    const maxThreads = Number(limit);
    if (!Number.isInteger(maxThreads) || maxThreads < 1 || maxThreads > 150) {
      inputRef.current.setCustomValidity('Thread limit must be a whole number from 1 to 150.');
      inputRef.current.reportValidity(); return;
    }
    requestVersion.current += 1;
    setSubmitting(true); setError('');
    try {
      await request('/api/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ max_threads: maxThreads, account_id: state.selected_account }) });
      const next = await request('/api/state');
      setState(next); setConnected(true);
    } catch (failure) { setError(failure.message); }
    finally { setSubmitting(false); }
  }

  async function selectAccount(accountId) {
    requestVersion.current += 1;
    setSubmitting(true); setError('');
    try {
      await request('/api/account', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account_id: accountId }) });
      const next = await request('/api/state');
      setState(next); setConnected(true); setCategory('All'); setUnreadOnly(false);
    } catch (failure) { setError(failure.message); }
    finally { setSubmitting(false); }
  }

  const busy = submitting || !!state?.busy;
  const items = (state?.items || []).filter(item => (category === 'All' || item.category === category) && (!unreadOnly || item.unread));
  return <>
    <Header excludedRef={excludedRef} />
    <main id="main">
      <section id="scan-section" className="scan-section">
        <div className="hero-heading fade-section"><div><p className="eyebrow">YOUR INBOX, WITH CONTEXT</p><h1>Less noise.<br /><span>More clarity.</span></h1><p className="subtitle">Personal Scanner brings the messages worth your attention into focus.</p></div><div className="local">Processing on this computer</div></div>
        <section className="toolbar fade-section">
          <div><span id="mode" className="badge">{state ? (state.demo ? 'FICTIONAL DEMO' : 'LIVE GMAIL') : 'Loading'}</span><span id="engine" className="badge">{state?.engine}</span>
            <p id="scope">{state && (state.demo ? 'Sample messages only · The contact’s demo address is fictional' : `${state.account_email ? state.account_email + ' · ' : ''}Last ${state.lookback_days} days · Up to ${state.max_threads} threads · ${state.mentor_configured ? 'Mentor address configured' : 'Add the contact’s address in config.json'}`)}</p>
          </div>
          <form className="scan-options" onSubmit={scan}>
            <div className="account-field"><label htmlFor="gmail-account">Gmail account</label>
              <select id="gmail-account" value={state?.selected_account || ''} disabled={busy || !connected} onChange={event => selectAccount(event.target.value)}>
                {!state && <option value="">Loading accounts…</option>}
                {state?.accounts.map(account => <option key={account.id} value={account.id}>{account.label}{account.email ? ` · ${account.email}` : ''}</option>)}
              </select>
            </div>
            <label htmlFor="max-threads">Thread limit (1–150)</label><input ref={inputRef} id="max-threads" type="number" min="1" max="150" step="1" required value={limit} disabled={busy} onChange={event => { event.target.setCustomValidity(''); setLimit(event.target.value); }} />
            <button id="scan" type="submit" disabled={busy || !connected}>{state?.busy ? 'Scanning…' : submitting ? 'Loading…' : state?.demo ? 'Scan demo messages' : 'Scan Gmail'}</button>
          </form>
        </section>
        <p id="status" role="status">{state ? (state.incomplete ? 'INCOMPLETE · ' : '') + state.status : 'Opening dashboard…'}</p><p id="error" role="alert">{error || state?.error || ''}</p><p id="last">{state?.last_scan ? 'Last finished scan: ' + date(state.last_scan) : 'No completed scan yet.'}</p>
        <section id="stats" className="stats">{metrics.map(([key, label]) => <div className="stat" key={key}><strong>{state?.counts[key] || 0}</strong><span>{label}</span></div>)}</section>
      </section>
      <section id="digest-section" className="digest-section">
        <div className="section-heading fade-section"><div><p className="eyebrow">THE SIGNAL</p><h2>Your digest</h2></div><p className="section-note">The important threads, in one place.</p></div>
        <section className="controls"><div id="filters">{filters.map(([value, label]) => <button key={value} className={`filter${category === value ? ' active' : ''}`} aria-pressed={category === value} onClick={() => setCategory(value)}>{label}</button>)}</div><label><input id="unread" type="checkbox" checked={unreadOnly} onChange={event => setUnreadOnly(event.target.checked)} /> Unread only</label></section>
        <section id="items" className="items">{items.length ? items.map(item => <ThreadCard key={item.thread_id} item={item} />) : <div className="empty">{state?.last_scan ? 'No notable messages in this view. Check the excluded list and scan coverage.' : 'Run a scan to see your digest.'}</div>}</section>
      </section>
      <details ref={excludedRef} id="excluded-section" className="audit fade-section"><summary id="audit-title">Excluded messages ({state?.excluded.length || 0})</summary><p>Review these to spot messages the prototype may have missed.</p><div id="excluded">{state?.excluded.map((item, i) => <div className="excluded-row" key={i}><strong>{item.subject}</strong><p>{item.sender}</p><p>{item.reason}</p></div>)}</div></details>
      <footer>Gmail read-only · No tracking pixels loaded · No cloud AI<br />Sender matching helps relevance; it does not verify sender identity. Check payment requests in your bank account.</footer>
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);

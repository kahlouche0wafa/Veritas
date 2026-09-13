import React, { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';

// ---------- verdict + signal helpers ------------------------------------

const VERDICT_KEY = {
  CONSISTENT:            { klass: 'consistent',   mark: '✓', label: 'CONSISTENT' },
  REASSIGNMENT:          { klass: 'reassignment', mark: '↷', label: 'REASSIGNMENT' },
  CONTRADICTED:          { klass: 'contradicted', mark: '✗', label: 'CONTRADICTED' },
  INSUFFICIENT:          { klass: 'insufficient', mark: '?', label: 'INSUFFICIENT' },
  NO_VERDICT_NO_CONSENT: { klass: 'no_verdict_no_consent', mark: '⊘', label: 'NO CONSENT' },
};
const CLASS_LABEL = {
  current:          'live signal',
  accumulated:      'historical',
  carrier_computed: 'carrier',
};

function summarizeSignal(item) {
  const v = item.value;
  switch (item.signal_name) {
    case 'connectivity':
      if (v == null) return 'no value';
      return `status: ${v.status || 'n/a'}`;
    case 'geofence_vs_claimed_site':
    case 'geofence_vs_historical_site': {
      if (!v || v.ok === false) return `unavailable — ${v?.error || 'no result'}`;
      const inside = v.inside ? 'INSIDE' : 'OUTSIDE';
      const site = v.claimed_site || v.historical_site || {};
      const label = site.label || '(unnamed)';
      const dist = v.distance_m == null ? '?' : `${Number(v.distance_m).toLocaleString()} m`;
      const radius = v.effective_radius_m == null ? '?' : `${Number(v.effective_radius_m).toLocaleString()} m`;
      return `${inside}  ${label} · ${dist} vs ${radius} radius`;
    }
    case 'device_swap':
      if (!v || v.ok === false) return `unavailable — ${v?.error || 'no result'}`;
      return v.swapped ? 'device recently swapped' : 'no recent device swap';
    case 'tenure':
      if (!v || v.ok === false) return `unavailable — ${v?.error || 'no result'}`;
      return `tenureDateCheck=${v.tenure_date_check} · contract=${v.contract_type || '?'}`;
    case 'number_verification':
      return item.note || 'not queried';
    case 'verdict_rule_matched':
      return `rule: ${v}`;
    case 'friction_action':
      if (v == null) return 'none (passive check)';
      return `${v.challenge_type || 'challenge'} → ${v.outcome || 'queued'}`;
    case 'consent_check':
      return `consent: ${v}`;
    default:
      return typeof v === 'object' ? JSON.stringify(v).slice(0, 120) : String(v);
  }
}

// Map a full evidence record → four partial-strip signals for the table.
function partialEvidence(record) {
  const by = Object.fromEntries((record?.evidence || []).map(e => [e.signal_name, e.value]));
  const state = (ok, pass) => (ok == null ? 'na' : (ok && pass ? 'pass' : 'fail'));
  const loc = by.geofence_vs_claimed_site;
  const conn = by.connectivity;
  const tenure = by.tenure;
  return [
    { label: 'Location retrieval', state: state(loc?.ok, loc?.ok) },
    { label: 'Device status',      state: state(conn?.ok, conn && conn.status && conn.status !== 'NOT_CONNECTED') },
    { label: 'KYC match',          state: state(tenure?.ok, tenure?.tenure_date_check) },
    { label: 'Geofencing',         state: state(loc?.ok, loc?.inside) },
  ];
}

// ---------- small UI atoms ----------------------------------------------

function TriangleLogo() {
  return (
    <svg className="brand-logo" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M16 3 L29 27 L3 27 Z" stroke="currentColor" strokeWidth="1.5" fill="none" />
      <path d="M16 3 L16 27" stroke="currentColor" strokeWidth="1" opacity="0.4" />
      <circle cx="16" cy="18" r="2" fill="currentColor" />
    </svg>
  );
}

function VerdictPill({ verdict }) {
  const v = VERDICT_KEY[verdict];
  if (!v) return <span className="pill pill--insufficient">—</span>;
  return (
    <span className={`pill pill--${v.klass}`}>
      <span className="pill-mark">{v.mark}</span>
      {v.label}
    </span>
  );
}

function StatusStrip({ health }) {
  if (!health) return <div className="status-strip"><span className="status-dot status-dot--down" />CONNECTING…</div>;
  const b = health.backends;
  const live = b.camara && b.camara.toLowerCase().includes('live');
  return (
    <div className="status-strip" title={`camara=${b.camara} storage=${b.storage} llm=${b.llm_model || 'n/a'}`}>
      <span className={`status-dot${live ? '' : ' status-dot--down'}`} />
      <span>CAMARA</span>
      <span className="status-sep">/</span>
      <span>Nokia Network APIs</span>
      <span className="status-sep">/</span>
      <span>LLM</span>
    </div>
  );
}

// ---------- agent trace panel (unchanged behaviour, restyled) -----------

const TRACE_STYLES = {
  normalise:      { color: '#1e40af', bg: '#dbeafe', icon: '⌘', label: 'normalise' },
  select_profile: { color: '#1e40af', bg: '#dbeafe', icon: '≡', label: 'select profile' },
  plan_evidence:  { color: '#1e40af', bg: '#dbeafe', icon: '☰', label: 'plan evidence' },
  gather:         { color: '#9a3412', bg: '#ffedd5', icon: '→', label: 'gather' },
  result:         { color: '#166534', bg: '#dcfce7', icon: '←', label: 'result' },
  skip:           { color: '#6b7280', bg: '#f3f4f6', icon: '⊘', label: 'skip' },
  history:        { color: '#5b21b6', bg: '#ede9fe', icon: '↺', label: 'history' },
  geofence_check: { color: '#0e7490', bg: '#cffafe', icon: '⊙', label: 'geofence' },
  verdict:        { color: '#111827', bg: '#fef9c3', icon: '★', label: 'verdict' },
  explain:        { color: '#1e40af', bg: '#dbeafe', icon: '✎', label: 'explain' },
  complete:       { color: '#374151', bg: '#f9fafb', icon: '✓', label: 'complete' },
};

function TraceStep({ step, index }) {
  const s = TRACE_STYLES[step.action] || { color: '#374151', bg: '#f3f4f6', icon: '•', label: step.action };
  const isSkip = step.action === 'skip';
  return (
    <li className="trace-step" style={{ animationDelay: `${Math.min(index, 30) * 90}ms` }}>
      <div className="trace-index">{step.step}</div>
      <div className={`trace-badge${isSkip ? ' trace-badge--skip' : ''}`} style={{ color: s.color, background: s.bg }}>
        <span className="trace-icon">{s.icon}</span>{s.label}
      </div>
      <div className={`trace-detail${isSkip ? ' trace-detail--skip' : ''}`}>{step.detail}</div>
      <div className="trace-time">{new Date(step.timestamp).toLocaleTimeString([], { hour12: false })}</div>
    </li>
  );
}

function AgentTracePanel({ trace, defaultOpen = true }) {
  const [expanded, setExpanded] = useState(defaultOpen);
  if (!trace || trace.length === 0) return null;
  return (
    <div className="trace-panel">
      <button className="trace-toggle-btn" onClick={() => setExpanded(v => !v)} aria-expanded={expanded}>
        <h3 style={{ margin: 0 }}>Agent trace <small style={{ color: 'var(--text-quiet)', fontWeight: 400 }}>({trace.length} steps)</small></h3>
        <span className="trace-arrow">{expanded ? 'collapse ▴' : 'expand ▾'}</span>
      </button>
      {expanded && (
        <ol className="trace-list">
          {trace.map((step, i) => <TraceStep key={i} step={step} index={i} />)}
        </ol>
      )}
    </div>
  );
}

function EvidenceItem({ item }) {
  const cls = CLASS_LABEL[item.evidence_class] || item.evidence_class;
  return (
    <li className="evi-item">
      <div className="evi-head">
        <span className="evi-name">{item.signal_name.replaceAll('_', ' ')}</span>
        <span className={`evi-class evi-class--${item.evidence_class}`}>{cls}</span>
      </div>
      <div className="evi-body">{summarizeSignal(item)}</div>
      {item.why && <div className="evi-why">why — {item.why}</div>}
      {item.note && item.signal_name !== 'number_verification' && (
        <div className="evi-note">note — {item.note}</div>
      )}
    </li>
  );
}

function LivenessCard({ liveness }) {
  if (!liveness) return null;
  return (
    <div className="liveness">
      <div className="liveness-head">↷ LIVENESS ESCALATION (PROTOTYPE STUB)</div>
      <div className="liveness-body">
        <div><b>Challenge:</b> {liveness.challenge_type} — {liveness.outcome}</div>
        <div>
          <b>Sent:</b> {new Date(liveness.sent_at).toLocaleTimeString()}
          &nbsp;·&nbsp;<b>Replied:</b> {liveness.replied_at ? new Date(liveness.replied_at).toLocaleTimeString() : '—'}
          &nbsp;·&nbsp;<b>Latency:</b> {liveness.response_latency_seconds ?? '—'}s
        </div>
        <div><b>Zone:</b> {liveness.responder_zone}</div>
        <div className="liveness-note">{liveness.note}</div>
      </div>
    </div>
  );
}

function ClaimDetailCard({ result }) {
  if (!result) return null;
  const { claim, evidence_record: record, agent_trace: trace } = result;
  const vk = VERDICT_KEY[record.verdict] || VERDICT_KEY.INSUFFICIENT;
  return (
    <div className="detail-card">
      <div className="detail-head">
        <div className={`verdict-badge verdict-badge--${vk.klass}`}>
          <span className="verdict-tag">{vk.mark} {vk.label}</span>
          {record.confidence && <span className="verdict-conf">confidence {record.confidence}</span>}
        </div>
        <div className="detail-meta">
          <span className="chip">profile: {record.profile}</span>
          <span className="chip">subject: {claim.subject_id}</span>
          <span className="chip">phone: {claim.phone}</span>
        </div>
      </div>

      <p className="claim-quote">"{claim.assertion_text}"</p>

      <AgentTracePanel trace={trace} />

      <div className="rationale">
        <h3>Rationale</h3>
        <p>{record.rationale}</p>
      </div>

      <LivenessCard liveness={record.liveness} />

      <div className="evi-list-wrap">
        <h3>Evidence chain <small style={{ color: 'var(--text-quiet)', fontWeight: 400 }}>(final state)</small></h3>
        <ul className="evi-list">
          {record.evidence.map((item, i) => <EvidenceItem key={i} item={item} />)}
        </ul>
      </div>

      <div className="ids-row">
        <span>claim <code>{claim.id.slice(0, 8)}…</code></span>
        <span>record <code>{record.id.slice(0, 8)}…</code></span>
        <span>consent: <code>{record.consent_status}</code></span>
        <span>{new Date(record.created_at).toLocaleString()}</span>
      </div>
    </div>
  );
}

// ---------- claims table ------------------------------------------------

function workerNum(subjectId) {
  const m = /(\d+)/.exec(subjectId || '');
  return m ? `#${m[1].padStart(4, '0')}` : (subjectId || '—');
}
function locLabel(claim) {
  const raw = claim.claimed_site?.label || '—';
  return raw.replace(' HQ', ' — HQ').replace(' Site ', ' — Site ');
}
function fmtTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleString([], { month: '2-digit', day: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

function ClaimsTable({ items, activeId, onToggle, onViewDetails }) {
  if (!items || items.length === 0) {
    return (
      <div className="claims-table">
        <div className="empty">No claims yet — submit one above.</div>
      </div>
    );
  }
  return (
    <div className="claims-table">
      <div className="claims-header">
        <div>VERDICT</div>
        <div>CLAIM</div>
        <div>WORKER ID</div>
        <div>LOCATION</div>
        <div>TIMESTAMP</div>
        <div></div>
      </div>
      {items.map(({ claim, evidence_record: record }) => {
        const isActive = activeId === claim.id;
        return (
          <React.Fragment key={claim.id}>
            <div className={`claims-row${isActive ? ' is-active' : ''}`} onClick={() => onToggle(claim.id)}>
              <div><VerdictPill verdict={record?.verdict} /></div>
              <div className="col-claim">{claim.assertion_text}</div>
              <div className="col-worker">{workerNum(claim.subject_id)}</div>
              <div className="col-location">{locLabel(claim)}</div>
              <div className="col-time">{fmtTime(claim.created_at)}</div>
              <div className="col-arrow">→</div>
            </div>
            {isActive && record && (
              <div className="evidence-strip">
                <div className="strip-label">EVIDENCE (partial)</div>
                <div className="strip-signals">
                  {partialEvidence(record).map((s) => (
                    <span className="strip-sig" key={s.label}>
                      <span className={`sig-mark sig-mark--${s.state}`}>{s.state === 'pass' ? '✓' : s.state === 'fail' ? '✗' : '·'}</span>
                      {s.label}
                    </span>
                  ))}
                </div>
                <a className="view-details" onClick={(e) => { e.stopPropagation(); onViewDetails(claim.id); }}>View details ↓</a>
              </div>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ---------- main app ----------------------------------------------------

export default function App() {
  const [health, setHealth] = useState(null);
  const [scenarios, setScenarios] = useState([]);
  const [freeText, setFreeText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [error, setError] = useState(null);
  const [activeId, setActiveId] = useState(null);

  const refresh = async () => {
    try {
      const [h, s, list] = await Promise.all([api.health(), api.scenarios(), api.listClaims(20)]);
      setHealth(h); setScenarios(s.scenarios); setHistory(list.items);
    } catch (e) { setError(e.message); }
  };
  useEffect(() => { refresh(); }, []);

  async function submit(text) {
    setBusy(true); setError(null); setResult(null);
    try {
      const body = await api.submitFree(text);
      setResult(body);
      setActiveId(body.claim.id);
      const list = await api.listClaims(20);
      setHistory(list.items);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function loadClaimFull(id) {
    setBusy(true); setError(null);
    try {
      const body = await api.getClaim(id);
      setResult(body);
      setActiveId(id);
      // scroll detail into view for the user
      setTimeout(() => {
        document.querySelector('.detail-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 60);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function reseed() {
    setBusy(true); setError(null); setResult(null); setActiveId(null);
    try {
      await api.reseed();
      const list = await api.listClaims(20);
      setHistory(list.items);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="app">
      {/* ---------- topbar ---------- */}
      <header className="topbar">
        <div className="brand">
          <TriangleLogo />
          <div className="brand-text">
            <div className="brand-name">VERITAS</div>
            <div className="brand-sub">Binding Integrity Engine</div>
          </div>
        </div>
        <StatusStrip health={health} />
      </header>

      {/* ---------- hero ---------- */}
      <div className="hero">
        <h1>Claim Verification</h1>
        <p>VERITAS checks an asserted claim against available network evidence.</p>
      </div>

      {/* ---------- scenarios ---------- */}
      <section>
        <div className="section-label">Try a demo scenario</div>
        <div className="scenario-grid">
          {scenarios.map((s) => (
            <button
              key={s.id}
              className="scenario"
              onClick={() => { setFreeText(s.text); submit(s.text); }}
              disabled={busy}
              title={s.text}
            >
              <div className="scenario-head">
                <span className={`dot dot--${s.id}`} />
                <span className="scenario-verdict">{s.label}</span>
              </div>
              <div className="scenario-desc">{s.description}</div>
            </button>
          ))}
        </div>
      </section>

      {/* ---------- input ---------- */}
      <section>
        <div className="section-label">Or write your own claim</div>
        <div className="input-card">
          <textarea
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            placeholder='e.g. "Omar clocked in at Riyadh Site A this morning at 08:00."'
            rows={2}
            disabled={busy}
          />
          <div className="input-actions">
            <button className="btn-ghost" onClick={reseed} disabled={busy} title="Reset + reseed the demo history">Reset demo</button>
            <button className="btn-primary" onClick={() => freeText.trim() && submit(freeText.trim())} disabled={busy || !freeText.trim()}>
              {busy ? 'Verifying…' : 'Verify claim →'}
            </button>
          </div>
          {error && <div className="error">Error: {error}</div>}
        </div>
      </section>

      {/* ---------- detail (verdict + trace + rationale + evidence) ---------- */}
      {busy && !result && <div className="busy">Querying network signals…</div>}
      {result && <ClaimDetailCard result={result} />}

      {/* ---------- recent claims table ---------- */}
      <section>
        <div className="section-head">
          <h2>Recent claims</h2>
          <a className="view-all" onClick={refresh}>Refresh ↻</a>
        </div>
        <ClaimsTable
          items={history}
          activeId={activeId}
          onToggle={(id) => setActiveId(activeId === id ? null : id)}
          onViewDetails={loadClaimFull}
        />
      </section>

      {/* ---------- footer ---------- */}
      <footer className="footer">
        <div className="footer-right">VERITAS // v0.1</div>
      </footer>
    </div>
  );
}

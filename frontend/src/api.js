// Small fetch wrapper. Uses the Vite dev-server proxy at /api → 127.0.0.1:8765.

const BASE = '/svc/api';

async function jsonReq(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const detail = (body && body.detail) || text || `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body;
}

export const api = {
  health:        ()      => jsonReq('/health'),
  scenarios:     ()      => jsonReq('/demo/scenarios'),
  catalog:       ()      => jsonReq('/catalog'),
  listClaims:    (limit=20) => jsonReq(`/claims?limit=${limit}`),
  getClaim:      (id)    => jsonReq(`/claims/${id}`),
  submitFree:    (text)  => jsonReq('/claims', { method: 'POST', body: JSON.stringify({ text }) }),
  submitStruct:  (payload) => jsonReq('/claims', { method: 'POST', body: JSON.stringify(payload) }),
  reseed:        ()      => jsonReq('/demo/seed', { method: 'POST' }),
};

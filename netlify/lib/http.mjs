const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const cache = new Map();

export const nowSec = () => Math.floor(Date.now() / 1000);

export const cacheGet = (key) => {
  const hit = cache.get(key);
  if (!hit) return null;
  if (hit.expire < nowSec()) {
    cache.delete(key);
    return null;
  }
  return hit.value;
};

export const cacheSet = (key, value, ttlSec) => {
  cache.set(key, { expire: nowSec() + Math.max(1, ttlSec || 300), value });
  return value;
};

export const cacheDel = (key) => cache.delete(key);

export async function fetchText(url, { timeout = 25000, headers = {}, method = 'GET', body = undefined } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url, {
      method,
      body,
      signal: ctrl.signal,
      redirect: 'follow',
      headers: {
        'User-Agent': UA,
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
        ...headers,
      },
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
    return text;
  } finally {
    clearTimeout(t);
  }
}

export async function fetchJson(url, opts = {}) {
  const text = await fetchText(url, {
    ...opts,
    headers: { Accept: 'application/json, text/plain, */*', ...(opts.headers || {}) },
  });
  return JSON.parse(text);
}

export async function fetchBytes(url, { timeout = 20000, headers = {}, maxBytes = 15 * 1024 * 1024 } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url, {
      redirect: 'follow',
      signal: ctrl.signal,
      headers: { 'User-Agent': UA, ...headers },
    });
    if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
    const len = Number(r.headers.get('content-length') || 0);
    if (len && len > maxBytes) throw new Error(`response too large (${len} bytes)`);
    const buf = Buffer.from(await r.arrayBuffer());
    if (buf.length > maxBytes) throw new Error(`response too large (${buf.length} bytes)`);
    return { buffer: buf, contentType: r.headers.get('content-type') || 'application/octet-stream' };
  } finally {
    clearTimeout(t);
  }
}

export const clean = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
export const absUrl = (base, href) => new URL(href, base).toString();
export const deSub = (t) => clean(t).replace(/\s*(Subtitle Indonesia|Sub Indo)\s*$/i, '').trim();

export const json = (obj, status = 200, headers = {}) => new Response(JSON.stringify(obj), {
  status,
  headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers },
});

export const b64urlEncode = (s) => Buffer.from(String(s)).toString('base64url');
export const b64urlDecode = (s) => Buffer.from(String(s), 'base64url').toString('utf8');

import * as O from '../lib/otakudesu.mjs';
import * as N from '../lib/nhentai.mjs';
import * as S from '../lib/mangadex.mjs';
import { json, fetchBytes } from '../lib/http.mjs';

const text = (body, status = 200, headers = {}) => new Response(body, {
  status,
  headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store', ...headers },
});
const xml = (body, status = 200) => new Response(body, {
  status,
  headers: { 'content-type': 'image/svg+xml; charset=utf-8', 'cache-control': 'no-store' },
});
const placeholderSvg = (label = 'NO IMAGE') => `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600" viewBox="0 0 400 600"><rect width="400" height="600" fill="#16161c"/><rect x="128" y="252" width="144" height="13" rx="6.5" fill="#26262e"/><rect x="148" y="284" width="104" height="10" rx="5" fill="#26262e"/><text x="200" y="336" fill="#3a3a45" font-family="sans-serif" font-size="15" letter-spacing="3" text-anchor="middle">${label}</text></svg>`;

const intParam = (url, name, def = 1) => {
  const n = Number(url.searchParams.get(name) || def);
  return Number.isFinite(n) ? Math.max(1, Math.trunc(n)) : def;
};

const route = async (req) => {
  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/api\/?/, '').replace(/^\/+|\/+$/g, '');
  const method = req.method || 'GET';

  if (path === 'health') return json({ ok: true, ts: Math.floor(Date.now() / 1000) });

  if (path === 'ongoing') return json({ items: await O.ongoing() });
  if (path === 'list') {
    const page = intParam(url, 'page', 1);
    const per = 60;
    const q = (url.searchParams.get('q') || '').trim().toLowerCase();
    let items = await O.listAnime();
    if (q) items = items.filter(i => i.title.toLowerCase().includes(q));
    const start = (page - 1) * per;
    return json({ total: items.length, page, per_page: per, items: items.slice(start, start + per), max_page: Math.max(1, Math.ceil(items.length / per)) });
  }
  if (path === 'search') {
    const q = (url.searchParams.get('q') || '').trim();
    if (!q) return json({ results: [] });
    const local = (await O.listAnime()).filter(i => i.title.toLowerCase().includes(q.toLowerCase())).slice(0, 12);
    const remote = local.length ? [] : await O.search(q);
    return json({ results: local.length ? local : remote, source: local.length ? 'local' : 'remote', total: local.length || remote.length });
  }
  if (path === 'detail') {
    const target = url.searchParams.get('url') || '';
    if (!target.startsWith(O.BASE)) return json({ error: 'invalid url' }, 400);
    const d = await O.detail(target);
    if (!d.anime) return json({ error: 'not found', anime: null, episodes: [] }, 404);
    return json({ anime: d.anime, episodes: d.episodes, batch: d.batch || [], is_fav: false, progress: null });
  }
  if (path === 'thumb') {
    const target = url.searchParams.get('url') || '';
    if (!target.startsWith(O.BASE)) return json({ thumb: null, srcset: null }, 400);
    return json(await O.thumbBundle(target));
  }
  if (path === 'genres') return json({ genres: await O.genres() });
  if (path === 'genre') {
    const slug = url.searchParams.get('slug') || '';
    const page = intParam(url, 'page', 1);
    return json({ slug, items: await O.byGenre(slug, page) });
  }
  if (path === 'schedule') return json({ schedule: await O.schedule() });
  if (path === 'top') return json({ items: await O.topAnime(12, 12) });
  if (path === 'stats') return json(await O.stats());
  if (path === 'stream') {
    const ep = url.searchParams.get('ep') || '';
    const q = url.searchParams.get('q') || '360p';
    const i = url.searchParams.has('i') ? Number(url.searchParams.get('i')) : null;
      if (!ep.startsWith(O.BASE)) return json({ error: 'invalid episode url' }, 400);
    return json(await O.resolveStream(ep, q, Number.isFinite(i) ? i : null));
  }

  // SHIRO / MangaDex
  if (path === 'shiro/list') return json(await S.listItems({ page: intParam(url, 'page', 1), genre: url.searchParams.get('genre') || '', q: url.searchParams.get('q') || '', sort: url.searchParams.get('sort') || 'date' }));
  if (path === 'shiro/gallery') {
    const g = await S.gallery(url.searchParams.get('url') || '');
    return g ? json({ configured: true, manga: g }) : json({ error: 'invalid or not found' }, 404);
  }
  if (path === 'shiro/pages') {
    const d = await S.pages(url.searchParams.get('url') || '', url.searchParams.get('ch') || '1');
    return d ? json({ configured: true, ...d }) : json({ error: 'invalid url' }, 400);
  }
  if (path === 'shiro/genres') return json({ configured: true, genres: S.shiroGenres() });
  if (path.startsWith('shiro/cover/')) {
    const parts = path.split('/');
    const got = await S.fetchCover(parts[2], parts[3]);
    if (!got) return text('Not Found', 404);
    return new Response(got.buffer, { status: 200, headers: { 'content-type': got.contentType, 'cache-control': 'public, max-age=604800' } });
  }

  // YOMU / nhentai (18+)
  if (path === 'yomu/list') return json(await N.listGalleries({ page: intParam(url, 'page', 1), genre: url.searchParams.get('genre') || '', q: url.searchParams.get('q') || '', sort: url.searchParams.get('sort') || 'date' }));
  if (path === 'yomu/gallery') {
    const g = await N.gallery(url.searchParams.get('url') || '');
    return g ? json({ configured: true, gallery: g }) : json({ error: 'invalid or not found' }, 404);
  }
  if (path === 'yomu/pages') {
    const fresh = ['1', 'true', 'yes', 'on'].includes((url.searchParams.get('fresh') || '').toLowerCase());
      const target = url.searchParams.get('url') || '';
    if (!N.validGalleryUrl(target)) return json({ error: 'invalid url' }, 400);
    return json({ configured: true, ...(await N.pages(target, { fresh })) });
  }
  if (path === 'yomu/genres') return json({ configured: true, genres: await N.nhentaiGenres() });

  // Legacy manga API (MIRU tab): same engine as YOMU
  if (path === 'manga/list') {
    const d = await N.listGalleries({ page: intParam(url, 'page', 1), genre: url.searchParams.get('genre') || '', q: url.searchParams.get('q') || '', sort: url.searchParams.get('sort') || 'date' });
    return json({ configured: true, items: d.items, page: d.page, total: d.total, max_page: d.max_page });
  }
  if (path === 'manga/genres') return json({ configured: true, genres: await N.nhentaiGenres() });
  if (path === 'manga/detail') {
    const target = url.searchParams.get('url') || '';
    const g = await N.gallery(target);
    if (!g) return json({ error: 'not found' }, 404);
    return json({ configured: true, manga: { ...g, chapters: [{ label: 'Gallery', url: g.url, date: g.date }], latest: 1 }, chapters: [{ label: 'Gallery', url: g.url, date: g.date }], is_fav: false });
  }
  if (path === 'manga/chapter') {
    const target = url.searchParams.get('url') || '';
    if (!N.validGalleryUrl(target)) return json({ error: 'invalid url' }, 400);
    const fresh = ['1', 'true', 'yes', 'on'].includes((url.searchParams.get('fresh') || '').toLowerCase());
    const d = await N.pages(target, { fresh });
    return json({ configured: true, ...d, prev: null, next: null });
  }
  if (path === 'manga/img') {
    const src = N.unrelay(url.searchParams.get('u') || '');
    if (!src || !N.validImgUrl(src)) return xml(placeholderSvg('NO IMAGE'));
    try {
      const r = await fetchBytes(src, { headers: { Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8' }, maxBytes: 14 * 1024 * 1024 });
      return new Response(r.buffer, { status: 200, headers: { 'content-type': (r.contentType || 'image/webp').split(';')[0], 'cache-control': 'public, max-age=86400' } });
    } catch {
      return xml(placeholderSvg('NO IMAGE'));
    }
  }

  if (path === 'favorites' || path === 'progress' || path === 'continue') {
    // Frontend memakai localStorage; endpoint ini hanya fallback kosong.
    if (method === 'POST') return json({ ok: true, is_fav: false });
    return json({ items: [] });
  }
  if (path === 'links' || path === 'mirrors') return json({ links: {}, mirrors: [], items: [] });
  if (path === 'warm') return json({ ok: true });
  return json({ error: 'not found', path }, 404);
};

const run = async (req) => {
  try {
    return await route(req);
  } catch (e) {
    return json({ error: 'internal error', message: String(e?.message || e).slice(0, 200) }, 500);
  }
};

export default run;

const lambdaToRequest = (event) => {
  const rawPath = event?.rawPath || event?.path || '/';
  const rawQuery = event?.rawQueryString ?? (event?.queryStringParameters ? new URLSearchParams(event.queryStringParameters).toString() : '');
  const method = event?.httpMethod || event?.requestContext?.http?.method || 'GET';
  const url = 'https://elegant-sable-dd27bc.netlify.app' + rawPath + (rawQuery ? '?' + rawQuery : '');
  const headers = new Headers();
  for (const [k, v] of Object.entries(event?.headers || {})) headers.set(k, String(v));
  let body = event?.body;
  if (body && event?.isBase64Encoded) body = Buffer.from(body, 'base64');
  return new Request(url, { method, headers, body: method === 'GET' || method === 'HEAD' ? undefined : body });
};

export const handler = async (event) => {
  const res = await run(lambdaToRequest(event));
  const headers = Object.fromEntries(res.headers.entries());
  return {
    statusCode: res.status,
    headers,
    body: await res.text(),
    isBase64Encoded: false,
  };
};

export const config = { path: '/api/*' };

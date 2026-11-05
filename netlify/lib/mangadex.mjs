import { fetchJson, fetchBytes, cacheGet, cacheSet, clean, nowSec, b64urlEncode } from './http.mjs';

export const BASE = 'https://api.mangadex.org';
export const UPLOADS = 'https://uploads.mangadex.org';
export const UA = 'AnimeHub-SHIRO/1.1 (+https://github.com/Ikyletwar/animehub)';
const TIMEOUT = 25000;

export const validId = (s) => /^[0-9a-fA-F-]{36}$/.test(String(s || '').trim());
export const validCoverName = (n) => /^[A-Za-z0-9._-]+\.(jpg|jpeg|png|webp|gif)$/i.test(String(n || ''));

export const GENRES = [
  ['Action','action'],['Adventure','adventure'],['Comedy','comedy'],['Drama','drama'],['Fantasy','fantasy'],
  ['Horror','horror'],['Mystery','mystery'],['Romance','romance'],['Sci-Fi','sci-fi'],['Slice of Life','slice-of-life'],
  ['Sports','sports'],['Supernatural','supernatural'],['Thriller','thriller'],['Historical','historical'],['Psychological','psychological'],
  ['Mecha','mecha'],['Music','music'],['School Life','school-life'],['Seinen','seinen'],['Shounen','shounen'],
  ['Josei','josei'],['Shoujo','shoujo'],
];

const TAG_IDS = new Map([
  ['action','391b0423-d847-456f-aff0-8b0cfc03066b'], ['adventure','87cc87cd-a395-47af-b27a-93258283bbc6'],
  ['comedy','4d32cc48-9f00-4cca-9b5a-a839f0764984'], ['drama','b9af3a63-f058-46de-a9a0-e0c13906197a'],
  ['fantasy','cdc58593-87dd-415e-bbc0-2ec27bf404cc'], ['horror','cdad7e68-1419-41dd-bdce-27753074a640'],
  ['mystery','ee968100-4191-4968-93d3-f82d72be7e46'], ['romance','423e2eae-a7a2-4a8b-ac03-a8351462d71d'],
  ['sci-fi','256c8bd9-4904-4360-bf4f-508a76d67183'], ['slice-of-life','e5301a23-ebd9-49dd-a0cb-2add944c7fe9'],
  ['sports','69964a64-2f90-4d33-beeb-f3ed2875eb4c'], ['supernatural','eabc5b4c-6aff-42f3-b657-3e90cbd00b75'],
  ['thriller','07251805-a27e-4d59-b488-f0bfbec15168'], ['historical','33771934-028e-4cb3-8744-691e866a923e'],
  ['psychological','3b60b75c-a2d7-4860-ab56-05f391bb889c'], ['mecha','50880a9d-5440-4732-9afb-8f457127e836'],
  ['music','f42fbf9e-188a-447b-9fdc-f19dc1e4d685'], ['school-life','caaa44eb-cd40-4177-b930-79d3ef2afe87'],
]);
const DEMOGRAPHIC = new Set(['seinen','shounen','josei','shoujo']);
const SORTS = { date: 'latestUploadedChapter', popular: 'followedCount', rating: 'rating', title: 'title' };

const params = (items) => {
  const out = [];
  for (const [k, v] of items) {
    if (v === undefined || v === null) continue;
    for (const x of Array.isArray(v) ? v : [v]) out.push([k, String(x)]);
  }
  return out;
};
const queryString = (items) => params(items).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
const get = async (path, items, ttl = 300) => {
  const url = `${BASE}${path}?${queryString(items || [])}`;
  const key = `md:${url}`;
  const cached = cacheGet(key);
  if (cached) return cached;
  const d = await fetchJson(url, { timeout: TIMEOUT, headers: { 'User-Agent': UA } });
  return cacheSet(key, d, ttl);
};
const isoToTs = (s) => {
  if (!s) return null;
  const t = Date.parse(String(s));
  return Number.isNaN(t) ? null : Math.floor(t / 1000);
};
const titleOf = (a) => {
  const t = a?.title || {};
  for (const k of ['id', 'en', 'ja-ro', 'ko-ro', 'zh-ro']) if (t[k]) return String(t[k]);
  return clean(Object.values(t)[0] || 'Untitled');
};
const altOf = (a) => {
  for (const x of a?.altTitles || []) { if (x.en) return String(x.en); const v = Object.values(x)[0]; if (v) return String(v); }
  return '';
};
const descOf = (a) => {
  const d = a?.description || {};
  let v = d.id || d.en || Object.values(d)[0] || '';
  v = String(v)
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return v.slice(0, 700);
};
const rel = (it, type) => (it.relationships || []).filter(r => r.type === type);
const coverUrl = (it) => {
  const mid = it.id;
  for (const r of rel(it, 'cover_art')) {
    const fn = r?.attributes?.fileName;
    if (fn && validCoverName(fn)) return `/api/shiro/cover/${mid}/${fn}.512.jpg`;
  }
  return null;
};
const authorOf = (it) => {
  const names = [];
  for (const r of [...rel(it, 'author'), ...rel(it, 'artist')]) {
    const n = r?.attributes?.name;
    if (n && !names.includes(n)) names.push(String(n));
  }
  return names.slice(0, 2).join(' · ');
};
const tagsOf = (a) => (a?.tags || []).map(t => t?.attributes?.name?.en).filter(Boolean);

const statistics = async (ids) => {
  if (!ids.length) return {};
  try {
    const d = await get('/statistics/manga', params([['manga[]', ids]]), 600);
    return d.statistics || {};
  } catch { return {}; }
};

const itemJson = (it, stats = {}) => {
  const a = it.attributes || {};
  const st = stats[it.id] || {};
  const rating = Number(st?.rating?.bayesian || st?.rating?.average || 0);
  let last = 0;
  try { last = Number(a.lastChapter || a.latestUploadedChapter || 0) || 0; } catch {}
  return {
    id: it.id,
    url: it.id,
    title: titleOf(a),
    alt: altOf(a),
    author: authorOf(it),
    status: (a.status || '').replace(/^./, c => c.toUpperCase()),
    year: a.year || '',
    rating: Math.round(rating * 100) / 100,
    follows: Number(st.follows || 0),
    favorites: Number(st.follows || 0),
    chapters: Number.isFinite(last) ? Math.trunc(last) : 0,
    latest: Number.isFinite(last) && last ? last : null,
    pages: 0,
    genres: tagsOf(a),
    desc: descOf(a),
    date: isoToTs(a.updatedAt || a.createdAt),
    thumb: coverUrl(it),
  };
};

export const shiroGenres = () => GENRES.map(([name, slug]) => ({ slug, name }));

const feedParams = (offset, limit = 500) => params([
  ['limit', limit], ['offset', offset], ['translatedLanguage[]', ['id', 'en']], ['order[chapter]', 'asc'],
  ['includeEmptyPages', 0], ['includeFutureUpdates', 0],
]);

export const feed = async (mangaId) => {
  const out = [];
  let offset = 0;
  while (true) {
    const d = await get(`/manga/${mangaId}/feed`, feedParams(offset), 300);
    const rows = d.data || [];
    out.push(...rows);
    offset += rows.length;
    const total = Number(d.total || 0);
    if (!rows.length || offset >= total || offset >= 5000) break;
  }
  const byKey = new Map();
  for (const r of out) {
    const a = r.attributes || {};
    const ch = a.chapter;
    if (!ch) continue;
    const no = Number(ch);
    if (!Number.isFinite(no)) continue;
    const lang = a.translatedLanguage || '';
    const key = String(no);
    const cur = byKey.get(key);
    const rank = lang === 'id' ? 0 : 1;
    const curRank = cur ? ((cur.attributes?.translatedLanguage || '') === 'id' ? 0 : 1) : 9;
    if (!cur || rank < curRank) byKey.set(key, r);
  }
  return [...byKey.values()].sort((x, y) => Number(x.attributes?.chapter || 0) - Number(y.attributes?.chapter || 0));
};

export const hasHostedChapter = async (id) => {
  try {
    const d = await get(`/manga/${id}/feed`, params([
      ['limit', 1], ['offset', 0], ['translatedLanguage[]', ['id', 'en']], ['includeEmptyPages', 0], ['includeFutureUpdates', 0],
    ]), 900);
    return Number(d.total || 0) > 0;
  } catch { return true; }
};

export const listItems = async ({ page = 1, genre = '', q = '', sort = 'date' } = {}) => {
  page = Math.max(1, Number(page) || 1);
  genre = clean(genre).toLowerCase();
  q = clean(q);
  sort = clean(sort).toLowerCase() || 'date';
  const perPage = 24, fetchLimit = 30, offset = (page - 1) * perPage;
  const p = [
    ['limit', fetchLimit], ['offset', offset],
    ['includes[]', ['cover_art', 'author', 'artist']],
    ['contentRating[]', ['safe']],
    ['availableTranslatedLanguage[]', ['id', 'en']],
    [`order[${SORTS[sort] || 'latestUploadedChapter'}]`, 'desc'],
  ];
  if (q) p.push(['title', q]);
  if (genre) {
    if (DEMOGRAPHIC.has(genre)) p.push(['publicationDemographic[]', genre]);
    else if (TAG_IDS.has(genre)) p.push(['includedTags[]', TAG_IDS.get(genre)]);
  }
  const d = await get('/manga', p, 180);
  const rows = d.data || [];
  const ids = rows.map(r => r.id).filter(Boolean);
  const stats = await statistics(ids);
  const flags = await Promise.all(ids.map(hasHostedChapter));
  const filtered = rows.filter((r, i) => flags[i]).slice(0, perPage);
  return { configured: true, items: filtered.map(r => itemJson(r, stats)), page, max_page: Math.max(1, Math.ceil(Number(d.total || 0) / perPage)), total: Number(d.total || 0), genre, sort };
};

const chapterJson = (r) => {
  const a = r.attributes || {};
  const no = Number(a.chapter || 0);
  const ts = isoToTs(a.publishAt || a.readableAt || a.createdAt);
  return { no: Number.isFinite(no) ? no : 0, title: a.title || `Chapter ${a.chapter}`, pages: Number(a.pages || 0), date: ts, id: r.id, lang: a.translatedLanguage || '' };
};

export const gallery = async (url) => {
  const id = String(url || '').trim();
  if (!validId(id)) return null;
  const d = await get(`/manga/${id}`, params([['includes[]', ['cover_art', 'author', 'artist', 'tag']]]), 300);
  if (!d.data) return null;
  const stats = await statistics([id]);
  const g = itemJson(d.data, stats);
  try {
    const chs = (await feed(id)).map(chapterJson);
    g.chapters = chs;
    g.latest = chs.length ? chs[chs.length - 1].no : g.latest;
    g.pages = chs.length ? chs[chs.length - 1].pages : 0;
    if (chs.length) g.date = chs[chs.length - 1].date;
  } catch {
    g.chapters = [];
  }
  return g;
};

export const pages = async (url, ch) => {
  const id = String(url || '').trim();
  if (!validId(id)) return null;
  const want = Number(ch || 1);
  const rows = await feed(id);
  const chosen = rows.find(r => Math.abs(Number(r.attributes?.chapter || -1) - want) < 1e-9);
  if (!chosen) return { images: [], count: 0, label: `Chapter ${want}`, src_ok: false, reason: 'Tidak ada chapter ID/EN yang dihost di MangaDex' };
  const cid = chosen.id;
  const a = chosen.attributes || {};
  const d = await get(`/at-home/server/${cid}`, [], 600);
  const base = d.baseUrl, chap = d.chapter || {}, h = chap.hash, data = chap.data || [];
  if (!base || !h || !data.length) return { images: [], count: 0, label: a.title || `Chapter ${a.chapter}`, src_ok: false, reason: 'Server gambar MangaDex tidak mengembalikan data chapter' };
  const urls = data.map(name => `${base}/data/${h}/${name}`);
  let src_ok = true;
  try {
    await fetchBytes(urls[0], { headers: { Range: 'bytes=0-0' }, maxBytes: 1024 * 1024 });
  } catch { src_ok = false; }
  return { images: urls, count: urls.length, label: a.title || `Chapter ${a.chapter}`, src_ok, chapter_id: cid, lang: a.translatedLanguage || '' };
};

export const fetchCover = async (mangaId, filename) => {
  if (!validId(mangaId) || !validCoverName(filename)) return null;
  const url = `${UPLOADS}/covers/${mangaId}/${filename}`;
  try {
    const r = await fetchBytes(url, { headers: { 'User-Agent': UA, Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8' }, maxBytes: 14 * 1024 * 1024 });
    const type = (r.contentType || 'image/jpeg').split(';')[0].toLowerCase();
    if (!type.startsWith('image/')) return null;
    return { ...r, contentType: type };
  } catch {
    return null;
  }
};

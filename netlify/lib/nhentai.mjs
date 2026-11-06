import { fetchJson, fetchBytes, cacheGet, cacheSet, clean, nowSec, b64urlEncode, b64urlDecode } from './http.mjs';

export const BASE = 'https://nhentai.net';
export const API = `${BASE}/api/v2`;
export const IMG_HOSTS = ['nhentai.net'];

const TTL_LIST = 6 * 3600;
const TTL_DETAIL = 900;
const TTL_TAG = 24 * 3600;

export const validGalleryUrl = (url) => /^https:\/\/nhentai\.net\/g\/\d+\/?$/.test(String(url || '').trim());
export const galleryId = (url) => Number((String(url || '').match(/\/g\/(\d+)\/?/) || [])[1] || 0);
export const validImgUrl = (url) => {
  try {
    const u = new URL(url);
    return IMG_HOSTS.some(h => u.hostname === h || u.hostname.endsWith('.' + h));
  } catch {
    return false;
  }
};

const api = (path, params) => {
  const u = new URL(API + path);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return;
    u.searchParams.set(k, String(v));
  });
  return fetchJson(u.toString(), { headers: { Origin: BASE, Referer: BASE + '/' } });
};

const cdnKey = 'nhentai:cdn';
export const getCdn = async () => {
  const cached = cacheGet(cdnKey);
  if (cached) return cached;
  const d = await api('/cdn');
  const cdn = {
    images: d.image_servers || ['https://i1.nhentai.net'],
    thumbs: d.thumb_servers || ['https://t1.nhentai.net'],
  };
  return cacheSet(cdnKey, cdn, 3600);
};

export const serversFor = async (mediaId) => {
  const cdn = await getCdn();
  const n = Math.abs(Number(mediaId) || 0);
  return {
    image: cdn.images[n % cdn.images.length],
    thumb: cdn.thumbs[n % cdn.thumbs.length],
  };
};

export const relay = (url) => `/api/manga/img?u=${b64urlEncode(url)}`;
export const unrelay = (param) => {
  try { return b64urlDecode(param); } catch { return ''; }
};

const tagCacheKey = (ids) => `tags:${ids.join(',')}`;
const chunk = (arr, size) => Array.from({ length: Math.ceil(arr.length / size) }, (_, i) => arr.slice(i * size, i * size + size));

export const resolveTags = async (ids) => {
  const unique = [...new Set((ids || []).map(Number).filter(Boolean))];
  const out = new Map();
  const missing = [];
  for (const id of unique) {
    const hit = cacheGet(`tag:${id}`);
    if (hit && hit.name) out.set(id, hit);
    else missing.push(id);
  }
  for (const part of chunk(missing, 80)) {
    try {
      const list = await api('/tags/ids', { ids: part.join(',') });
      for (const t of list || []) {
        const obj = { id: Number(t.id), type: t.type || '', name: t.name || '' };
        cacheSet(`tag:${obj.id}`, obj, TTL_TAG);
        out.set(obj.id, obj);
      }
    } catch {}
  }
  return out;
};

const splitMeta = (tagIds, tags) => {
  let artist = '', category = '', lang = '';
  const genres = [];
  for (const tid of tagIds || []) {
    const t = tags.get(Number(tid));
    if (!t || !t.name) continue;
    if (t.type === 'artist') artist ||= t.name;
    else if (t.type === 'category') category ||= t.name;
    else if (t.type === 'language') lang ||= t.name;
    else if (t.type === 'tag' || t.type === 'parody') {
      if (genres.length < 12) genres.push(t.name);
    }
  }
  return { artist, category, lang, genres };
};

export const itemFromList = async (it) => {
  const id = Number(it.id || 0);
  const mediaId = String(it.media_id || '');
  const tags = await resolveTags(it.tag_ids || []);
  const { artist, category, lang, genres } = splitMeta(it.tag_ids || [], tags);
  const { thumb } = await serversFor(mediaId);
  const thumbUrl = it.thumbnail ? `${thumb}/${it.thumbnail}` : null;
  return {
    id,
    url: `${BASE}/g/${id}/`,
    title: clean(it.english_title || it.japanese_title || `#${id}`),
    alt: clean(it.japanese_title || ''),
    author: artist,
    category,
    lang,
    pages: Number(it.num_pages || 0),
    favorites: Number(it.num_favorites || 0),
    genres,
    desc: '',
    date: null,
    thumb: thumbUrl,
  };
};

export const itemFromGallery = async (j) => {
  const id = Number(j.id || 0);
  const mediaId = String(j.media_id || '');
  const titleObj = j.title || {};
  const title = clean(titleObj.pretty || titleObj.english || titleObj.japanese || `#${id}`);
  const alt = clean(titleObj.japanese || '');
  const tagObjects = Array.isArray(j.tags) ? j.tags : [];
  const tags = new Map(tagObjects.map(t => [Number(t.id), { id: Number(t.id), type: t.type || '', name: t.name || '' }]));
  const tagIds = tagObjects.map(t => Number(t.id));
  const { artist, category, lang, genres } = splitMeta(tagIds, tags);
  const pages = Number(j.num_pages || 0);
  const fav = Number(j.num_favorites || 0);
  const { thumb } = await serversFor(mediaId);
  const coverPath = (j.thumbnail && j.thumbnail.path) ? j.thumbnail.path : (j.thumbnail || '');
  const cover = coverPath ? `${thumb}/${coverPath}` : null;
  const langs = tagObjects.filter(t => t.type === 'language').map(t => t.name).filter(Boolean).slice(0, 3);
  const descParts = [`${pages} halaman`, `${fav.toLocaleString('id-ID')} favorit`];
  if (langs.length) descParts.push('Bahasa ' + langs.join('/'));
  if (j.scanlator) descParts.push('Scanlator ' + j.scanlator);
  return {
    id,
    url: `${BASE}/g/${id}/`,
    title,
    alt,
    author: artist,
    category,
    lang,
    pages,
    favorites: fav,
    genres,
    desc: descParts.join(' · '),
    date: Number(j.upload_date || 0) || null,
    thumb: cover,
    scanlator: j.scanlator || '',
  };
};

const tagQuery = (genre) => {
  const g = clean(genre).toLowerCase();
  if (!g) return '';
  if (g.startsWith('type:')) return `category:${g.slice(5)}`;
  if (g.startsWith('genre:')) return `tag:${g.slice(6)}`;
  return g;
};

export const listGalleries = async ({ page = 1, genre = '', q = '', sort = 'date' } = {}) => {
  page = Math.max(1, Number(page) || 1);
  const query = q ? clean(q) : tagQuery(genre);
  const path = query ? '/search' : '/galleries';
  const params = query ? { query, sort, page } : { page, sort };
  const d = await api(path, params);
  const rows = d.result || [];
  const items = [];
  for (const it of rows) items.push(await itemFromList(it));
  return {
    configured: true,
    items,
    page,
    max_page: Number(d.num_pages || 1),
    total: Number(d.total || items.length),
    genre: clean(genre).toLowerCase(),
    sort: clean(sort).toLowerCase() || 'date',
  };
};

export const gallery = async (url) => {
  const id = galleryId(url);
  if (!id) return null;
  const key = `gallery:${id}`;
  const cached = cacheGet(key);
  if (cached) return cached;
  const j = await api(`/galleries/${id}`);
  const item = await itemFromGallery(j);
  return cacheSet(key, item, TTL_DETAIL);
};

export const pages = async (url, { fresh = false } = {}) => {
  const id = galleryId(url);
  if (!id) return { images: [], count: 0, label: '', src_ok: false };
  const key = `pages:${id}`;
  if (!fresh) {
    const cached = cacheGet(key);
    if (cached) return cached;
  }
  const j = await api(`/galleries/${id}`);
  const mediaId = String(j.media_id || '');
  const pageFiles = j.pages || [];
  const { image } = await serversFor(mediaId);
  const images = [];
  for (const p of pageFiles) {
    const num = p.number || (images.length + 1);
    const imagePath = (p.path && String(p.path).startsWith('galleries/')) ? String(p.path) : null;
    if (imagePath) {
      images.push(`${image}/${imagePath}`);
      continue;
    }
    const ext = p.extension || 'webp';
    images.push(`${image}/galleries/${mediaId}/${num}.${ext}`);
  }
  // src_ok probe: cek gambar pertama dari sisi server
  let src_ok = true;
  if (images.length) {
    try {
      const r = await fetchBytes(images[0], { headers: { Range: 'bytes=0-0' }, maxBytes: 1024 * 1024 });
      src_ok = !!r.buffer.length;
    } catch {
      src_ok = false;
    }
  }
  const title = clean((j.title && (j.title.pretty || j.title.english || j.title.japanese)) || `#${id}`);
  const value = { images, count: images.length, label: title, src_ok, gallery_id: id };
  return cacheSet(key, value, TTL_DETAIL);
};

export const nhentaiGenres = async () => [
  { name: 'Doujinshi', slug: 'type:Doujinshi' },
  { name: 'Manga', slug: 'type:Manga' },
  { name: 'Manhwa', slug: 'type:Manhwa' },
  { name: 'Western', slug: 'type:Western' },
  { name: 'Inggris', slug: 'genre:english' },
  { name: 'Indonesia', slug: 'genre:indonesian' },
  { name: 'Original', slug: 'genre:original' },
  { name: 'Berwarna', slug: 'genre:colorful' },
  { name: 'Milf', slug: 'genre:milf' },
  { name: 'Netorare', slug: 'genre:netorare' },
  { name: 'Seragam Sekolah', slug: 'genre:schoolgirl' },
];

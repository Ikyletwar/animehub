import * as cheerio from 'cheerio';
import { absUrl, cacheGet, cacheSet, clean, deSub, fetchText, nowSec } from './http.mjs';
import seed from './seed.mjs';

export const BASE = 'https://otakudesu.blog';
const TTL_LIST = 6 * 3600;
const TTL_ONGOING = 180;
const TTL_DETAIL = 180;
const TTL_GENRE = 6 * 3600;
const TTL_SCHED = 900;

const get = async (url) => {
  try { return await fetchText(url, { headers: { Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', Referer: BASE } }); }
  catch { return null; }
};

const anime = (x) => ({
  title: deSub(x.title || ''),
  url: x.url || '',
  thumb: x.thumb || null,
  srcset: x.srcset || null,
  aspect: '225/319',
  status: x.status || null,
  score: x.score || null,
  genres: x.genres || [],
  typ: x.typ || null,
  total_ep: x.total_ep || null,
  synopsis: x.synopsis || null,
  sched: x.sched || null,
});

export async function listAnime() {
  const cached = cacheGet('list');
  if (cached) return cached;
  const html = await get(`${BASE}/anime-list/`);
  if (!html) return cacheGet('list') || seed.list;
  const $ = cheerio.load(html);
  const out = [], seen = new Set();
  $('.abjtext li a.hodebgst, #abtext li a.hodebgst, .daftarkartun li a').each((_, el) => {
    const href = $(el).attr('href');
    if (!href) return;
    const url = absUrl(BASE, href);
    if (seen.has(url)) return;
    seen.add(url);
    const title = clean($(el).text()) || clean($(el).attr('title') || '');
    if (title) out.push(anime({ title, url }));
  });
  return cacheSet('list', out.length ? out : seed.list, TTL_LIST);
}

export async function ongoing() {
  const cached = cacheGet('ongoing');
  if (cached) return cached;
  const out = [], seen = new Set();
  for (let page = 1; page <= 6; page++) {
    const url = page === 1 ? `${BASE}/ongoing-anime/` : `${BASE}/ongoing-anime/page/${page}/`;
    const html = await get(url);
    if (!html) break;
    const $ = cheerio.load(html);
    const block = $('.rseries .rapi .venz').first().length ? $('.rseries .rapi .venz').first() : $('.venz').first();
    const dets = block.find('li .detpost');
    if (!dets.length) break;
    dets.each((_, det) => {
      const d = $(det);
      const a = d.find('.thumb a[href]').first();
      const href = a.attr('href');
      if (!href) return;
      const url2 = absUrl(BASE, href);
      if (seen.has(url2)) return;
      seen.add(url2);
      const title = clean(d.find('.jdlflm').first().text()) || clean(d.find('img').first().attr('alt') || '');
      const img = d.find('.thumb img').first();
      const sched = [
        clean(d.find('.epz').first().text()).replace(/dashicons dashicons-desktop/g, ''),
        clean(d.find('.epztipe').first().text()),
        clean(d.find('.newnime').first().text()),
      ].filter(Boolean).join(' · ');
      if (title) out.push(anime({ title, url: url2, thumb: img.attr('src') || null, srcset: img.attr('srcset') || null, sched }));
    });
  }
  return cacheSet('ongoing', out.length ? out : seed.ongoing, TTL_ONGOING);
}

export async function search(q) {
  q = clean(q);
  if (!q) return [];
  const key = `search:${q.toLowerCase()}`;
  const cached = cacheGet(key);
  if (cached) return cached;
  const html = await get(`${BASE}/?s=${encodeURIComponent(q)}&post_type=anime`);
  if (!html) return [];
  const $ = cheerio.load(html);
  const out = [];
  $('ul.chivsrc li').each((_, el) => {
    const li = $(el);
    const a = li.find('h2 a[href]').first();
    const href = a.attr('href');
    if (!href) return;
    const img = li.find('img').first();
    let genres = [], status = null, score = null;
    li.find('div.set').each((_, s) => {
      const t = clean($(s).text());
      const low = t.toLowerCase();
      if (low.startsWith('genres') && t.includes(':')) genres = t.split(':')[1].split(',').map(s2 => clean(s2)).filter(Boolean);
      else if (low.startsWith('status') && t.includes(':')) status = clean(t.split(':')[1]);
      else if (low.startsWith('rating') && t.includes(':')) score = clean(t.split(':')[1]);
    });
    out.push(anime({ title: a.text(), url: absUrl(BASE, href), thumb: img.attr('src') || null, srcset: img.attr('srcset') || null, status, score, genres }));
  });
  return cacheSet(key, out, 300);
}

export async function detail(url) {
  if (!url || !url.startsWith(BASE)) return { anime: null, episodes: [], batch: [] };
  const key = `detail:${url}`;
  const cached = cacheGet(key);
  if (cached) return cached;
  const html = await get(url);
  if (!html) return { anime: null, episodes: [], batch: [] };
  const $ = cheerio.load(html);
  const title = deSub(clean($('.jdlrx h1').first().text()));
  const img = $('.fotoanime img').first();
  const genres = [];
  $(".infozingle a[href*='/genres/']").each((_, a) => genres.push(clean($(a).text())));
  const info = {};
  $('.infozingle p').each((_, p) => {
    const txt = clean($(p).text());
    const i = txt.indexOf(':');
    if (i > 0) info[txt.slice(0, i).trim().toLowerCase()] = txt.slice(i + 1).trim();
  });
  const synopsis = clean($('.sinopc').first().text()) || null;
  const a = anime({
    title, url,
    thumb: img.attr('src') || null,
    genres,
    status: info.status || null,
    typ: info.tipe || null,
    total_ep: info['total episode'] || null,
    score: info.skor || null,
    synopsis,
  });
  const eps = [], seen = new Set(), batch = [];
  $('.episodelist ul').each((_, ul) => {
    $(ul).find('li').each((_, li) => {
      const lo = $(li);
      const link = lo.find('a[href]').first();
      const href = link.attr('href');
      if (!href) return;
      const url2 = absUrl(BASE, href);
      if (seen.has(url2)) return;
      seen.add(url2);
      const label = deSub(clean(link.text()));
      const item = { label, url: url2, date: clean(lo.find('span.zeebr').first().text()) || null };
      if (url2.includes('/batch/') || label.toLowerCase().includes('batch')) batch.push(item);
      else eps.push(item);
    });
  });
  if (!a.total_ep || !String(a.total_ep).trim().match(/^\d+$/)) a.total_ep = String(eps.length);
  const value = { anime: a, episodes: eps, batch };
  return cacheSet(key, value, TTL_DETAIL);
}

export async function thumbBundle(animeUrl) {
  if (!animeUrl || !animeUrl.startsWith(BASE)) return { thumb: null, srcset: null };
  const key = `thumb:${animeUrl}`;
  const cached = cacheGet(key);
  if (cached) return cached;
  const html = await get(animeUrl);
  let thumb = null, srcset = null;
  if (html) {
    const $ = cheerio.load(html);
    const im = $('.fotoanime img').first().length ? $('.fotoanime img').first() : $('.thumb img').first();
    thumb = (im.attr('data-src') || im.attr('src') || '').trim() || null;
    srcset = (im.attr('srcset') || '').trim() || null;
    if (!thumb) thumb = $('meta[property="og:image"]').attr('content') || null;
  }
  return cacheSet(key, { thumb, srcset }, 7 * 24 * 3600);
}

export async function genres() {
  const cached = cacheGet('genres');
  if (cached) return cached;
  const html = await get(`${BASE}/genre-list/`);
  if (!html) return seed.genres;
  const $ = cheerio.load(html);
  const seen = new Set(), out = [];
  $('a[href*="/genres/"]').each((_, a) => {
    const href = $(a).attr('href') || '';
    const slug = href.replace(/\/+$/, '').split('/').pop();
    const name = clean($(a).text());
    if (slug && name && !seen.has(slug)) {
      seen.add(slug);
      out.push({ slug, name });
    }
  });
  out.sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  return cacheSet('genres', out.length ? out : seed.genres, TTL_GENRE);
}

export async function byGenre(slug, page = 1) {
  if (!/^[a-z0-9-]+$/.test(slug || '')) return [];
  const url = page <= 1 ? `${BASE}/genres/${slug}/` : `${BASE}/genres/${slug}/page/${page}/`;
  const html = await get(url);
  if (!html) return [];
  const $ = cheerio.load(html);
  const root = $('.page').first().length ? $('.page').first() : $.root();
  const out = [], seen = new Set();
  root.find('a[href*="/anime/"]').each((_, a) => {
    const href = $(a).attr('href');
    if (!href) return;
    const url2 = absUrl(BASE, href);
    if (seen.has(url2) || url2.includes('/batch/')) return;
    seen.add(url2);
    const img = $(a).find('img').first();
    const title = clean($(a).text()) || img.attr('alt') || '';
    if (title) out.push(anime({ title, url: url2, thumb: img.attr('src') || null }));
  });
  return out;
}

export async function schedule() {
  const cached = cacheGet('schedule');
  if (cached) return cached;
  const html = await get(`${BASE}/jadwal-rilis/`);
  if (!html) return seed.schedule;
  const $ = cheerio.load(html);
  const days = ['senin', 'selasa', 'rabu', 'kamis', 'jumat', 'sabtu', 'minggu'];
  const res = [];
  $('.kglist321').each((_, box) => {
    const day = clean($(box).find('h2').first().text());
    if (!days.some(d => day.toLowerCase().includes(d))) return;
    const items = [];
    $(box).find('li').each((_, li) => {
      const a = $(li).find('a[href]').first();
      const href = a.attr('href');
      if (!href) return;
      const url2 = absUrl(BASE, href);
      const im = $(li).find('img').first();
      items.push({ title: deSub(clean(a.text())), thumb: im.attr('src') || '', url: url2, is_episode: url2.includes('/episode/') });
    });
    if (items.length) res.push({ day, items });
  });
  return cacheSet('schedule', res.length ? res : seed.schedule, TTL_SCHED);
}

export async function topAnime(limit = 12, sample = 12) {
  // Top nyata membutuhkan fetch banyak halaman detail. Untuk Netlify, pakai urutan sumber
  // sebagai fallback kosong agar frontend memakai ongoing sebagai populer.
  return [];
}

export async function stats() {
  const [list, ong, gs] = await Promise.all([listAnime(), ongoing(), genres()]);
  return { total_anime: list.length, total_ongoing: ong.length, total_genres: gs.length, genres_top: gs.slice(0, 14).map(g => g.name), est_episodes_sampled: 0 };
}

const postForm = async (url, data) => {
  const body = new URLSearchParams(data);
  return fetchText(url, {
    method: 'POST',
    body,
    timeout: 25000,
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
  });
};

let nonceCache = { value: null, at: 0 };
const ajaxNonce = async () => {
  if (nonceCache.value && Date.now() - nonceCache.at < 600_000) return nonceCache.value;
  try {
    const raw = await postForm(`${BASE}/wp-admin/admin-ajax.php`, { action: 'aa1208d27f29ca340c92c66d1926f13f' });
    const j = JSON.parse(raw);
    const nonce = (j && j.data) ? String(j.data) : raw.trim().replace(/^"|"$/g, '');
    if (nonce) {
      nonceCache = { value: nonce, at: Date.now() };
      return nonce;
    }
  } catch {}
  return null;
};

const extractIframe = (html) => {
  const $ = cheerio.load(html || '');
  const src = $('iframe[src]').first().attr('src') || html.match(/<iframe[^>]+src=["']([^"']+)["']/i)?.[1];
  return src || null;
};

export async function resolveStream(episodeUrl, quality = '360p', server = null) {
  const result = { url: null, kind: null, server: null, embed: null, iframe_only: true, quality, available: [] };
  if (!episodeUrl || !episodeUrl.startsWith(BASE)) return result;
  const opts = await streamOptions(episodeUrl);
  result.available = opts.map(o => ({ quality: o.quality, server: o.server, label: o.label }));
  const q = quality || '360p';
  const cand = opts.filter(o => o.quality === q && (server == null || Number(o.server) === Number(server)));
  const withIframe = cand.find(o => o.iframe) || opts.find(o => o.quality === q && o.iframe) || opts.find(o => o.iframe);
  if (withIframe && withIframe.quality === q) {
    result.embed = withIframe.iframe;
    result.server = withIframe.label;
    result.quality = withIframe.quality;
    return result;
  }
  const nonce = await ajaxNonce();
  for (const opt of cand.length ? cand : opts.filter(o => o.quality === q)) {
    if (!nonce || opt.id == null) continue;
    try {
      const raw = await postForm(`${BASE}/wp-admin/admin-ajax.php`, {
        id: opt.id,
        i: opt.server,
        q: opt.quality,
        nonce,
        action: '2a3505c93b0035d3f455df82bf976b84',
      });
      const j = JSON.parse(raw);
      const html = Buffer.from((j && j.data) ? j.data : raw.trim().replace(/^"|"$/g, ''), 'base64').toString('utf8');
      const iframe = extractIframe(html);
      if (iframe) {
        result.embed = iframe;
        result.server = opt.label;
        result.quality = opt.quality;
        return result;
      }
    } catch {}
  }
  if (withIframe) {
    result.embed = withIframe.iframe;
    result.server = withIframe.label;
    result.quality = withIframe.quality;
    return result;
  }
  result.error = 'Halaman ini tidak punya pemutar video. Kemungkinan ini link download batch, bukan episode.';
  return result;
}

export async function streamOptions(episodeUrl) {
  if (!episodeUrl || !episodeUrl.startsWith(BASE)) return [];
  const key = `opts:${episodeUrl}`;
  const cached = cacheGet(key);
  if (cached) return cached;
  const html = await get(episodeUrl);
  if (!html) return [];
  const $ = cheerio.load(html);
  const out = [];
  const pembed = $('#pembed iframe[src], iframe[src*="desustream"]').first();
  if (pembed.length) {
    out.push({ quality: '360p', server: 0, label: 'desustream', iframe: pembed.attr('src'), kind: 'iframe' });
  }
  $('.mirrorstream a[data-content]').each((i, a) => {
    try {
      const decoded = JSON.parse(Buffer.from($(a).attr('data-content'), 'base64').toString('utf8'));
      out.push({ quality: decoded.q || '360p', server: decoded.i || 0, label: clean($(a).text()) || 'mirror', iframe: null, kind: 'mirror', id: decoded.id });
    } catch {}
  });
  return cacheSet(key, out, 300);
}

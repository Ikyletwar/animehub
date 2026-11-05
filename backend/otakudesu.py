"""
otakudesu.py — inti scraper otakudesu.blog (minimal & efisien).
Dipakai oleh server.py. Semua fungsi adalah generator/sinkron ringan via httpx.Client
dengan cache TTL in-memory untuk Newfoundland & ongoing.
"""
from __future__ import annotations

import re
import time
import html as ihtml
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx
from bs4 import BeautifulSoup

BASE = "https://otakudesu.blog"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

_client: Optional[httpx.Client] = None
_cache: dict[str, tuple[float, object]] = {}   # key -> (expire_ts, value)
_TTL_LIST = 6 * 3600       # list anime: 6 jam
_TTL_ONGOING = 180         # ongoing: 3 menit (butuh "realtime")
_TTL_DETAIL = 180          # detail: 3 menit
_TTL_STREAM = 0            # stream: selalu fresh (URL punya expire ~8jam, tapi
                            # harus fresh supaya tidak basi)


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(headers=HEADERS, timeout=25.0,
                               follow_redirects=True)
    return _client


def _cached(key: str, ttl: int):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _store(key: str, val):
    _cache[key] = (time.time() + (_TTL_STREAM or 1), val)
    return val


def _set(key: str, val, ttl: int):
    _cache[key] = (time.time() + ttl, val)
    return val


def _get(url: str, params: Optional[dict] = None, referer: Optional[str] = None) -> Optional[str]:
    try:
        hdr = {"Referer": referer} if referer else {}
        r = client().get(url, params=params, headers=hdr)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


def _abs(href: str) -> str:
    return urllib.parse.urljoin(BASE, href)


def _cln(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _de_sub(t: str) -> str:
    """Buang sufiks 'Subtitle Indonesia'/'Sub Indo'."""
    return re.sub(r"\s*(Subtitle Indonesia|Sub Indo)\s*$", "", t, flags=re.I).strip()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass
class Anime:
    title: str
    url: str
    thumb: Optional[str] = None
    srcset: Optional[str] = None      # varian 212x300 untuk kartu kecil
    aspect: str = "225/319"           # rasio asli gambar (hindari distorsi)
    status: Optional[str] = None
    score: Optional[str] = None
    genres: list = field(default_factory=list)
    typ: Optional[str] = None
    total_ep: Optional[str] = None
    synopsis: Optional[str] = None
    sched: Optional[str] = None   # info jadwal (episode/day) untuk ongoing

    def json(self):
        return asdict(self)


@dataclass
class Episode:
    label: str
    url: str
    date: Optional[str] = None

    def json(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# 1. LIST ANIME  (semua, ~1897)
# ---------------------------------------------------------------------------
def list_anime(force: bool = False) -> list[Anime]:
    """
    Daftar semua anime (~1897) dari /anime-list/.

    Catatan: halaman ini tidak menyediakan metadata episode/genre, jadi
    total_ep hanya terisi jika sudah ada di cache SQLite (diisi dari
    halaman detail). Kolom itu dipakai fitur "Paling Banyak Episode".
    """
    if not force:
        c = _cached("list", 1)
        if c:
            return c
    html = _get(f"{BASE}/anime-list/")
    if not html:
        return _cached("list", 1) or []
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.select(".abjtext li a.hodebgst, #abtext li a.hodebgst, .daftarkartun li a"):
        href = a.get("href")
        if not href:
            continue
        u = _abs(href)
        if u in seen:
            continue
        seen.add(u)
        t = _cln(a.get_text(" ", strip=True)) or (a.get("title") or "")
        if not t:
            continue
        out.append(Anime(title=_de_sub(t), url=u))
    return _set("list", out, _TTL_LIST)


# ---------------------------------------------------------------------------
# 2. ONGOING
# ---------------------------------------------------------------------------
def ongoing(force: bool = False) -> list[Anime]:
    if not force:
        c = _cached("ongoing", 1)
        if c:
            return c
    out, seen = [], set()
    for page in range(1, 7):
        url = f"{BASE}/ongoing-anime/" if page == 1 else f"{BASE}/ongoing-anime/page/{page}/"
        h = _get(url)
        if not h:
            break
        soup = BeautifulSoup(h, "lxml")
        block = soup.select_one(".rseries .rapi .venz") or soup.select_one(".venz")
        if not block:
            break
        dets = block.select("li .detpost")
        for det in dets:
            a = det.select_one(".thumb a[href]")
            if not a:
                continue
            au = _abs(a["href"])
            if au in seen:
                continue
            seen.add(au)
            te = det.select_one(".jdlflm")
            title = _cln(te.get_text()) if te else ""
            if not title:
                im = det.select_one("img")
                title = (im.get("alt") if im else "") or ""
            epz = det.select_one(".epz")
            tipe = det.select_one(".epztipe")
            baru = det.select_one(".newnime")
            img = det.select_one(".thumb img")
            sched_parts = []
            if epz:
                sched_parts.append(_cln(epz.get_text()).replace("dashicons dashicons-desktop", "").strip())
            if tipe:
                sched_parts.append(_cln(tipe.get_text()))
            if baru:
                sched_parts.append(_cln(baru.get_text()))
            out.append(Anime(
                title=_de_sub(title), url=au,
                thumb=(img.get("src") if img else None),
                srcset=(img.get("srcset") if img else None),
                sched=" · ".join(p for p in sched_parts if p),
            ))
        if not dets:
            break
    return _set("ongoing", out, _TTL_ONGOING)


# ---------------------------------------------------------------------------
# 3. SEARCH
# ---------------------------------------------------------------------------
def search(q: str) -> list[Anime]:
    if not q:
        return []
    key = f"search:{q.strip().lower()}"
    c = _cached(key, 1)
    if c:
        return c
    h = _get(f"{BASE}/", params={"s": q, "post_type": "anime"})
    if not h:
        return []
    soup = BeautifulSoup(h, "lxml")
    out = []
    for li in soup.select("ul.chivsrc li"):
        a = li.select_one("h2 a[href]")
        img = li.select_one("img")
        genres, status, score = [], None, None
        for s in li.select("div.set"):
            t = _cln(s.get_text(" ", strip=True))
            low = t.lower()
            if low.startswith("genres") and ":" in t:
                genres = [g.strip() for g in t.split(":", 1)[1].split(",") if g.strip()]
            elif low.startswith("status") and ":" in t:
                status = t.split(":", 1)[1].strip()
            elif low.startswith("rating") and ":" in t:
                score = t.split(":", 1)[1].strip()
        out.append(Anime(
            title=_de_sub(_cln(a.get_text()) if a else ((img.get("alt") if img else "") or "")),
            url=_abs(a["href"]) if a else "",
            thumb=(img.get("src") if img else None),
            srcset=(img.get("srcset") if img else None),
            status=status, score=score, genres=genres,
        ))
    return _set(key, out, 300)


# ---------------------------------------------------------------------------
# 4. DETAIL + EPISODE
# ---------------------------------------------------------------------------
def detail(url: str):
    key = f"detail:{url}"
    c = _cached(key, 1)
    if c:
        return c
    h = _get(url)
    if not h:
        return None, []
    soup = BeautifulSoup(h, "lxml")
    t = soup.select_one(".jdlrx h1")
    title = _de_sub(_cln(t.get_text())) if t else ""
    img = soup.select_one(".fotoanime img")
    genres = [a.get_text(strip=True) for a in soup.select(".infozingle a[href*='/genres/']")]
    info = {}
    for p in soup.select(".infozingle p"):
        txt = _cln(p.get_text(" ", strip=True))
        if ":" in txt:
            k, v = txt.split(":", 1)
            info[k.strip().lower()] = v.strip()
    sin = soup.select_one(".sinopc")
    anime = Anime(
        title=title, url=url,
        thumb=(img.get("src") if img else None),
        genres=genres,
        status=info.get("status"), typ=info.get("tipe"),
        total_ep=info.get("total episode"), score=info.get("skor"),
        synopsis=_cln(sin.get_text(" ", strip=True)) if sin else None,
    )
    eps, seen, batch = [], set(), []
    for ul in soup.select(".episodelist ul"):
        for li in ul.select("li"):
            a = li.select_one("a[href]")
            if not a:
                continue
            eu = _abs(a["href"])
            if eu in seen:
                continue
            seen.add(eu)
            zb = li.select_one("span.zeebr")
            item = Episode(label=_de_sub(_cln(a.get_text())),
                           url=eu,
                           date=_cln(zb.get_text()) if zb else None)
            # link batch download BUKAN episode playable -> pisahkan
            if "/batch/" in eu or "batch" in item.label.lower():
                batch.append(item)
            else:
                eps.append(item)
    # total_ep bisa kosong/tanda tanya di sumber -> isi dari jumlah episode
    if not anime.total_ep or not str(anime.total_ep).strip().isdigit():
        anime.total_ep = str(len(eps))
    res = _set(key, (anime, eps), _TTL_DETAIL)
    if batch:
        _set(key + ":batch", batch, _TTL_DETAIL)
    return res


def detail_batch(url: str) -> list[Episode]:
    """Link batch download (bukan episode playable)."""
    r = _cached(f"detail:{url}:batch", 1)
    return r or []


# ---------------------------------------------------------------------------
# 5. STREAM RESOLVER  -> URL siap pakai (hls/mp4) atau iframe fallback
# ---------------------------------------------------------------------------
_M3U8 = re.compile(r"https?://[^\s\"'<>\\]+?\.m3u8[^\s\"'<>\\]*", re.I)
_GOOG = re.compile(r"https?://[^\s\"'<>\\]+?videoplayback[^\s\"'<>\\]*", re.I)
_MP4 = re.compile(r"https?://[^\s\"'<>\\]+?\.mp4[^\s\"'<>\\]*", re.I)


def resolve_stream(episode_url: str, quality: str = "360p",
                   server: Optional[int] = None) -> dict:
    """
    Resolusi URL video siap pakai untuk satu episode pada quality tertentu.

    quality: "360p" | "480p" | "720p"   (None/"" = pakai default 360p)
    server : indeks mirror; None = mirror pertama yang tersedia

    Return:
      {url, kind, server, embed, iframe_only, quality, available:[...]}

    Tidak di-cache: URL punya tanda tangan + masa berlaku (±8 jam).
    """
    result = {"url": None, "kind": None, "server": None, "embed": None,
              "iframe_only": True, "quality": quality}
    if not episode_url.startswith(BASE):
        return result

    # ambil opsi (iframe per quality)
    try:
        opts = stream_options(episode_url)
    except Exception:
        opts = []
    if not opts:
        return result

    result["available"] = [
        {"quality": o["quality"], "server": o["server"], "label": o["label"]}
        for o in opts
    ]

    # pilih opsi sesuai quality + server
    if quality:
        cand = [o for o in opts if o["quality"] == quality]
        if server is not None:
            cand = [o for o in cand if o["server"] == server]
    else:
        cand = opts

    # coba tiap kandidat sampai ada yang memberi URL video langsung yang valid
    for chosen in cand:
        embed = chosen["iframe"]
        emb = _get(embed, referer=episode_url)
        if not emb:
            continue
        for pat, kind in ((_M3U8, "hls"), (_GOOG, "mp4"), (_MP4, "mp4")):
            m = pat.search(emb)
            if not m:
                continue
            vurl = ihtml.unescape(m.group(0)).strip()
            # pastikan benar-benar video yang bisa diputar
            if not validate_stream(vurl):
                continue
            result.update(
                url=vurl, kind=kind, iframe_only=False,
                embed=embed, server=chosen["label"],
                quality=chosen["quality"],
                res=probe_resolution(vurl, f"{episode_url}|{chosen['quality']}"))
            return result

    # tidak ada mirror yang bisa di-proxy -> pakai iframe (server punya
    # player sendiri, kualitas elegida di sana)
    if cand:
        result.update(embed=cand[0]["iframe"], server=cand[0]["label"],
                      quality=cand[0]["quality"])
    else:
        # Tidak ada embed sama sekali (mis. halaman batch, atau embed mati).
        # Jangan return iframe kosong — beri tahu frontend agar tampil pesan.
        result["error"] = ("Halaman ini tidak punya pemutar video. Kemungkinan "
                           "ini link download batch, bukan episode.")
    return result


def episode_meta(episode_url: str) -> dict:
    """Judul episode untuk judul halaman player."""
    h = _get(episode_url, referer=BASE)
    if not h:
        return {}
    soup = BeautifulSoup(h, "lxml")
    t = soup.select_one("h1") or soup.select_one("title")
    return {"title": _cln(t.get_text()) if t else ""}


# ---------------------------------------------------------------------------
# 6. THUMBNAIL ON-DEMAND (untuk halaman List yang tidak punya <img>)
# ---------------------------------------------------------------------------
_TTL_THUMB = 7 * 24 * 3600   # 7 hari: thumbnail anime relatif stabil


def thumb_bundle(anime_url: str) -> tuple[str, str]:
    """
    Ambil (url, srcset) thumbnail untuk satu anime, dalam SATU request.
    Dipakai List Anime — sumbernya tidak menyediakan <img> di halaman daftar.
    Cache 7 hari supaya tiap anime hanya di-fetch sekali.
    """
    if not anime_url.startswith(BASE):
        return ("", "")
    key = f"thumb:{anime_url}"
    c = _cached(key, 1)
    if c is not None:
        return c
    h = _get(anime_url)
    url, ss = "", ""
    if h:
        soup = BeautifulSoup(h, "lxml")
        im = soup.select_one(".fotoanime img") or soup.select_one(".thumb img")
        if im:
            url = (im.get("data-src") or im.get("src") or "").strip()
            ss = (im.get("srcset") or "").strip()
        if not url:
            og = soup.select_one('meta[property="og:image"]')
            if og and og.get("content"):
                url = og["content"].strip()
    return _set(key, (url, ss), _TTL_THUMB)


def thumb_cached(anime_url: str) -> tuple[str, str]:
    """
    Baca thumbnail HANYA dari cache, tanpa request jaringan.

    Dipakai endpoint list/genre: mengisi cover dari anime yang sudah pernah
    dikunjungi. Mengembalikan (url, srcset).

    PENTING: nilai cache adalah TUPLE (url, srcset). Versi lama menyimpan
    string; keduanya ditangani agar data lama di memori tidak melempar
    TypeError saat di-escape oleh UI.
    """
    c = _cached(f"thumb:{anime_url}", 1)
    if isinstance(c, tuple):
        url = c[0] if len(c) > 0 else ""
        ss = c[1] if len(c) > 1 else ""
        return (url or "", ss or "")
    if isinstance(c, str):
        return (c, "")
    return ("", "")


def thumb_of(anime_url: str) -> Optional[str]:
    return thumb_bundle(anime_url)[0] or None


def thumb_srcset_of(anime_url: str) -> Optional[str]:
    return thumb_bundle(anime_url)[1] or None


# ---------------------------------------------------------------------------
# 7. MULTI-QUALITY STREAMING  (360p / 480p / 720p + beberapa mirror)
# ---------------------------------------------------------------------------
import base64 as _b64
import json as _json

_AJAX = f"{BASE}/wp-admin/admin-ajax.php"
_ACT_NONCE = "aa1208d27f29ca340c92c66d1926f13f"
_ACT_PLAYER = "2a3505c93b0035d3f455df82bf976b84"

_nonce_cache = {"v": None, "t": 0.0}


def _ajax_nonce(c: "httpx.Client") -> Optional[str]:
    """Nonce untuk endpoint pemilih server (valid singkat)."""
    if _nonce_cache["v"] and (time.time() - _nonce_cache["t"]) < 600:
        return _nonce_cache["v"]
    try:
        r = c.post(_AJAX, data={"action": _ACT_NONCE}, timeout=20)
        data = _json.loads(r.text)
        n = data.get("data") if isinstance(data, dict) else r.text.strip().strip('"')
    except Exception:
        return None
    if n:
        _nonce_cache.update(v=n, t=time.time())
    return n


def stream_options(episode_url: str) -> list[dict]:
    """
    Daftar opsi streaming untuk satu episode.

    Sumber halaman episode memuat JSON base64 di atribut data-content:
        {"id":150463,"i":0,"q":"720p"}   -> id=post, i=indeks server, q=quality
    Iframe default di <div id="pembed"> dianggap kualitas 360p.

    Setiap opsi: {quality, server, label, iframe, index}

    Cache 5 menit: resolusi opsimahالا involves 9 request HTTP (1 GET +
    1 nonce + 7 POST AJAX), terlalu berat untuk dilakukan tiap klik.
    """
    if not episode_url.startswith(BASE):
        return []
    key = f"opts:{episode_url}"
    cached = _cached(key, 1)
    if cached is not None:
        return cached
    h = _get(episode_url, referer=BASE)
    if not h:
        return []
    out: list[dict] = []
    seen: set[tuple] = set()

    # iframe default = 360p (server pertama)
    m = re.search(r'<div[^>]*id="pembed"[^>]*>\s*<iframe src="([^"]+)"', h)
    if not m:
        m = re.search(r'<iframe src="([^"]*desustream[^"]+)"', h)
    if m:
        out.append({"quality": "360p", "server": 0, "label": "desustream",
                    "iframe": ihtml.unescape(m.group(1)), "kind": "iframe"})

    # opsi dari data-content
    combos = []
    for mm in re.finditer(r'data-content="([^"]+)"', h):
        try:
            j = _json.loads(_b64.b64decode(mm.group(1)))
        except Exception:
            continue
        if "q" in j and "i" in j:
            combos.append(j)
    if not combos:
        return _set(key, out, 300)

    nonce = _ajax_nonce(client())
    if not nonce:
        return _set(key, out, 300)
    for j in combos:
        k = (j.get("q"), j.get("i"))
        if k in seen:
            continue
        seen.add(k)
        try:
            r = client().post(_AJAX, headers={"Referer": episode_url},
                              data={**j, "nonce": nonce, "action": _ACT_PLAYER},
                              timeout=25)
            html = _b64.b64decode(r.text).decode(errors="replace")
        except Exception:
            continue
        mi = re.search(r'<iframe src="([^"]+)"', html)
        if not mi:
            continue
        src = ihtml.unescape(mi.group(1))
        out.append({"quality": j["q"], "server": j["i"],
                    "label": _server_label(src),
                    "iframe": src, "kind": "iframe"})
    return _set(key, out, 300)


def _server_label(src: str) -> str:
    for key, name in (("odvidhide", "odvidhide"), ("mega.nz", "mega"),
                      ("desustream.net/dstream/arcg", "arcg"),
                      ("desustream", "desustream"), ("odstream", "odstream")):
        if key in src:
            return name
    return "server"


# ---------------------------------------------------------------------------
# Validasi & probing resolusi stream
# ---------------------------------------------------------------------------
import shutil as _shutil
import threading
import tempfile as _tempfile

_TTL_RES = 3600


_val_cache: dict[str, tuple[float, bool]] = {}


def validate_stream(url: str) -> bool:
    """
    Cek apakah URL beneran video yang bisa diputar.
    Menolak: halaman error (mis. archive.org 'Item not available'),
    file kecil, atau content-type non-video.
    Cache 5 menit (kunci = host+path, tanpa query).
    """
    k = "v:" + url.split("?")[0][:150]
    hit = _val_cache.get(k)
    if hit and hit[0] > time.time():
        return hit[1]
    ok = False
    try:
        r = client().get(url, headers={"Range": "bytes=0-2047",
                                       "Accept": "*/*"}, timeout=20)
        ctype = r.headers.get("content-type", "").lower()
        clen = int(r.headers.get("content-length") or 0)
        total = 0
        cr = r.headers.get("content-range", "")
        if "/" in cr:
            try:
                total = int(cr.split("/")[-1])
            except ValueError:
                total = 0
        size = total or clen
        if (ctype.startswith("video/") or "mpegurl" in ctype) and \
           (not size or size >= 200_000):
            ok = True
    except Exception:
        ok = False
    _val_cache[k] = (time.time() + 300, ok)
    return ok


_probe_scheduled: set = set()


def probe_resolution(url: str, ckey: str = "") -> Optional[str]:
    """
    Ambil resolusi asli (WxH) dari sebuah URL video dengan ffprobe.

    TIDAK memblokir. Kalau belum ada di cache, probe dijalankan di thread
    latar; hasilnya dipakai pada request berikutnya. Unduh 3 MB + ffprobe
    bisa memakan 5-8 detik — terlalu lambat untuk memblokir jalur playback.
    Resolusi akan muncul di UI saat episode dibuka untuk kedua kalinya.

    ckey = cache key stabil (episode+quality), bukan URL mentah yang berubah
           tiap request (googlevideo terus regenerate signature).
    """
    key = f"res:{ckey}" if ckey else "res:" + url.split("?")[0][:150]
    c = _cached(key, 1)
    if c is not None:
        return c or None
    if key in _probe_scheduled or not _shutil.which("ffprobe"):
        return None
    _probe_scheduled.add(key)

    def _bg():
        try:
            _probe_do(url, key)
        except Exception:
            pass
        finally:
            _probe_scheduled.discard(key)

    threading.Thread(target=_bg, daemon=True).start()
    return None


def _probe_do(url: str, key: str) -> None:
    """Kerjaan berat probe resolusi (dijalankan di thread latar)."""
    import subprocess as _sp
    import os as _os
    res = ""
    tmp = None
    try:
        with _tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            tmp = f.name
        total = 0
        with client().stream("GET", url,
                             headers={"Range": "bytes=0-3145727"}) as r:
            with open(tmp, "wb") as out:
                for chunk in r.iter_bytes(65536):
                    out.write(chunk)
                    total += len(chunk)
                    if total > 3 * 1024 * 1024:
                        break
        if total > 100_000:
            o = _sp.run([_shutil.which("ffprobe"), "-v", "error",
                         "-select_streams", "v:0", "-show_entries",
                         "stream=width,height", "-of", "csv=p=0", tmp],
                        capture_output=True, text=True, timeout=30).stdout.strip()
            if o and "," in o:
                res = f"{o.split(',')[0]}x{o.split(',')[1]}"
    except Exception:
        res = ""
    finally:
        if tmp:
            try:
                _os.unlink(tmp)
            except Exception:
                pass
    _set(key, res, _TTL_RES)


def stream_links(episode_url: str) -> dict:
    """Link download/mirror per quality (link.desustream.com) dari halaman episode."""
    if not episode_url.startswith(BASE):
        return {}
    h = _get(episode_url, referer=BASE)
    if not h:
        return {}
    res: dict = {}
    for mm in re.finditer(r'<strong>\s*(\d+p)\s*</strong>(.*?)(?=<strong>|$)', h, re.S):
        q = mm.group(1)
        links = {}
        for u, t in re.findall(r'<a href="(https://link\.desustream\.com/[^"]+)"[^>]*>([^<]+)</a>',
                               mm.group(2)):
            links[t.strip()] = u
        if links:
            res[q] = links
    return res


# ---------------------------------------------------------------------------
# 8. GENRE
# ---------------------------------------------------------------------------
_TTL_GENRE = 6 * 3600


def genres() -> list[dict]:
    """Daftar genre + jumlah anime."""
    c = _cached("genres", 1)
    if c is not None:
        return c
    h = _get(f"{BASE}/genre-list/")
    if not h:
        return _set("genres", [], _TTL_GENRE)
    soup = BeautifulSoup(h, "lxml")
    out = []
    for a in soup.select('a[href*="/genres/"]'):
        slug = a["href"].rstrip("/").split("/")[-1]
        name = _cln(a.get_text())
        if slug and name:
            out.append({"slug": slug, "name": name})
    # urutkan & unik
    seen, uniq = set(), []
    for g in out:
        if g["slug"] in seen:
            continue
        seen.add(g["slug"]); uniq.append(g)
    uniq.sort(key=lambda x: x["name"].lower())
    return _set("genres", uniq, _TTL_GENRE)


def by_genre(slug: str, page: int = 1) -> list[Anime]:
    """Anime dalam satu genre."""
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        return []
    url = f"{BASE}/genres/{slug}/" if page <= 1 else f"{BASE}/genres/{slug}/page/{page}/"
    h = _get(url)
    if not h:
        return []
    soup = BeautifulSoup(h, "lxml")
    root = soup.select_one(".page") or soup
    out, seen = [], set()
    for a in root.select('a[href*="/anime/"]'):
        au = _abs(a["href"])
        if au in seen or "/batch/" in au:
            continue
        seen.add(au)
        title = _cln(a.get_text(" ", strip=True))
        img = a.find("img")
        if not title:
            title = (img.get("alt") if img else "") or ""
        if not title:
            continue
        out.append(Anime(title=_de_sub(title), url=au,
                         thumb=(img.get("src") if img else None)))
    return out


# ---------------------------------------------------------------------------
# 9. JADWAL RILIS
# ---------------------------------------------------------------------------
_TTL_SCHED = 900


def schedule() -> list[dict]:
    """
    Jadwal rilis per hari.
    Struktur sumber: .kgjdwl321 > .kglist321 > (h2 = nama hari, ul>li>a = anime)
    """
    c = _cached("sched", 1)
    if c is not None:
        return c
    h = _get(f"{BASE}/jadwal-rilis/")
    if not h:
        return _set("sched", [], _TTL_SCHED)
    soup = BeautifulSoup(h, "lxml")
    days = ["senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu"]
    res = []
    for box in soup.select(".kglist321"):
        h2 = box.select_one("h2")
        if not h2:
            continue
        day = _cln(h2.get_text())
        if not any(d in day.lower() for d in days):
            continue
        items = []
        for li in box.select("li"):
            a = li.select_one("a[href]")
            if not a:
                continue
            au = _abs(a["href"])
            im = li.find("img")
            items.append({
                "title": _de_sub(_cln(a.get_text(" ", strip=True))),
                "thumb": (im.get("src") if im else "") or "",
                "url": au,
                "is_episode": "/episode/" in au,
            })
        if items:
            res.append({"day": day, "items": items})
    return _set("sched", res, _TTL_SCHED)


# ---------------------------------------------------------------------------
# 10. STATISTIK
# ---------------------------------------------------------------------------
def stats() -> dict:
    """Statistik ringan dari data yang sudah ter-cache."""
    lst = list_anime()
    ong = ongoing()
    gs = genres()
    total_ep = 0
    for a in lst[:400]:            # sampel 400 untuk hemat waktu
        if a.total_ep and a.total_ep.isdigit():
            total_ep += int(a.total_ep)
    return {
        "total_anime": len(lst),
        "total_ongoing": len(ong),
        "total_genres": len(gs),
        "genres_top": [g["name"] for g in gs[:14]],
        "est_episodes_sampled": total_ep,
    }


def top_anime(limit: int = 12, sample: int = 24) -> list[Anime]:
    """
    Anime dengan episode terbanyak (proxy 'terpopuler').
    Jumlah sampel dibatasi karena tiap sampel = 1 fetch halaman detail.
    """
    key = f"top:{limit}"
    c = _cached(key, 1)
    if c is not None:
        return c
    import random
    lst = list_anime()
    if not lst:
        return _set(key, [], 3600)
    random.seed(42)
    picks = random.sample(lst, min(sample, len(lst)))
    out: list[Anime] = []
    for a in picks:
        try:
            an, _eps = detail(a.url)
        except Exception:
            continue
        if an and an.total_ep and an.total_ep.isdigit():
            out.append(an)
    out.sort(key=lambda x: int(x.total_ep or 0), reverse=True)
    res = out[:limit]
    return _set(key, res, 3600)


if __name__ == "__main__":
    print("list    :", len(list_anime()), "anime")
    print("ongoing :", len(ongoing()), "item")
    s = search("one piece")
    print("search  :", [x.title for x in s])
    if s:
        a, e = detail(s[0].url)
        print("detail  :", a.title, "| total_ep", a.total_ep, "|", len(e), "episode")

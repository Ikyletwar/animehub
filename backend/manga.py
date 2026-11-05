"""
manga.py — scraper manga untuk sumber nhentai.net (API v2 resmi).

Sumber: https://nhentai.net/api/v2/docs (FastAPI/OpenAPI).
Endpoint yang dipakai (tanpa auth):
    GET /api/v2/cdn                         -> daftar server gambar
    GET /api/v2/galleries?page=N            -> semua gallery (terbaru)
    GET /api/v2/search?query=&sort=&page=   -> pencarian / filter
    GET /api/v2/galleries/{id}              -> detail penuh (tags + pages)
    GET /api/v2/tags/ids?ids=a,b,c          -> resolver id tag -> nama

Fitur:
    - Resolver DoH in-process: jaringan lokal menghijack DNS nhentai.net
      (Internet Positif), jadi host nhentai.net & i1..i4/t1..t4 diselesaikan
      lewat Cloudflare DoH (verifikasi TLS tetap normal via SNI).
    - API lama (/api/galleries/...) resmi dipensiunkan (403: "Use new API"),
      scraper ini memakai v2.
    - Relay gambar: semua gambar (thumbnail & isi halaman) dikembalikan sebagai
      URL relay /api/manga/img?... supaya browser cukup bicara ke server lokal.
    - Placeholder SVG otomatis bila gambar gagal diambil dari sumber.

Kontrak data (dipakai server.py + UI):
    Manga   : {title,url,thumb,status,rating,latest,genres[],desc}
    Chapter : {label,url,date}
    detail  : (Manga|None, [Chapter])            -- satu "chapter" per gallery
    chapter : {images:[url], prev:url|None, next:url|None, label:str, src_ok}
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import os
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx

BASE = "https://nhentai.net"         # origin sumber manga
API = f"{BASE}/api/v2"               # root API v2
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": BASE,
    "Referer": BASE + "/",
}

IMG_ENDPOINT = "/api/manga/img"       # relay gambar (ditangani server.py)
IMG_PROXY = ""                        # opsional: prefix proxy eksternal
_IMG_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "cache", "manga_img")
# i1..i4 / t1..t4 / i. / t. / nhentai.net semuanya tertutup oleh akhiran ini
_IMG_HOSTS = ("nhentai.net",)
_MAX_IMG = 14 * 1024 * 1024
_CT = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
       ".png": "image/png", ".gif": "image/gif", ".avif": "image/avif"}

_client: Optional[httpx.Client] = None
_cache: dict[str, tuple[float, object]] = {}
_TTL_LIST = 6 * 3600
_TTL_DETAIL = 900
_TTL_CHAPTER = 900
_TTL_TAG = 24 * 3600


# ---------------------------------------------------------------------------
# Resolver DoH (bypass DNS hijack jaringan untuk host nhentai.net)
# ---------------------------------------------------------------------------
_DOH_URL = "https://cloudflare-dns.com/dns-query"
_DOH_HOSTS = ("nhentai.net",)
_doh_cache: dict[str, tuple[float, list]] = {}
_doh_lock = threading.Lock()


def _doh_resolve(host: str) -> list:
    now = time.time()
    with _doh_lock:
        hit = _doh_cache.get(host)
        if hit and hit[0] > now:
            return hit[1]
    ips: list = []
    try:
        r = httpx.get(_DOH_URL, params={"name": host, "type": "A"},
                      headers={"accept": "application/dns-json"}, timeout=8.0)
        if r.status_code == 200:
            ips = [a["data"] for a in r.json().get("Answer", [])
                   if a.get("type") == 1]
    except Exception:
        ips = []
    with _doh_lock:
        _doh_cache[host] = (now + 3600, ips)
    return ips


def _install_doh() -> None:
    if getattr(socket.getaddrinfo, "_animehub_doh", False):
        return
    orig = socket.getaddrinfo

    def gai(host, port, family=0, type=0, proto=0, flags=0):
        try:
            h = host.decode() if isinstance(host, bytes) else host
        except Exception:
            h = host
        if (isinstance(h, str)
                and any(h == d or h.endswith("." + d) for d in _DOH_HOSTS)):
            ips = _doh_resolve(h)
            if ips:
                return [(socket.AF_INET, socket.SOCK_STREAM,
                         socket.IPPROTO_TCP, "", (ip, port)) for ip in ips]
        return orig(host, port, family, type, proto, flags)

    gai._animehub_doh = True
    socket.getaddrinfo = gai


_install_doh()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass
class Manga:
    title: str
    url: str
    thumb: Optional[str] = None
    status: Optional[str] = None
    rating: Optional[str] = None
    latest: Optional[str] = None
    genres: list = field(default_factory=list)
    desc: Optional[str] = None

    def json(self):
        return asdict(self)


@dataclass
class Chapter:
    label: str
    url: str
    date: Optional[str] = None

    def json(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Infrastruktur bersama
# ---------------------------------------------------------------------------
def configured() -> bool:
    return bool(BASE)


def gallery_id(url: str) -> Optional[int]:
    if not isinstance(url, str):
        return None
    m = re.search(r"/g/(\d+)", url)
    return int(m.group(1)) if m else None


def valid_url(url: str) -> bool:
    if not BASE or not isinstance(url, str) or not url.startswith(BASE + "/g/"):
        return False
    return gallery_id(url) is not None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(headers=HEADERS, timeout=30.0,
                               follow_redirects=True)
    return _client


def _cached(key: str, ttl: int):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _set(key: str, val, ttl: int):
    _cache[key] = (time.time() + ttl, val)
    return val


# ---------------------------------------------------------------------------
# Server gambar (konfigurasi CDN dari API v2)
# ---------------------------------------------------------------------------
_CDN_DEFAULT_IMG = ["https://i1.nhentai.net", "https://i2.nhentai.net",
                    "https://i3.nhentai.net", "https://i4.nhentai.net"]
_CDN_DEFAULT_THUMB = ["https://t1.nhentai.net", "https://t2.nhentai.net",
                      "https://t3.nhentai.net", "https://t4.nhentai.net"]
_cdn: tuple = (list(_CDN_DEFAULT_IMG), list(_CDN_DEFAULT_THUMB), 0.0)


def _cdn_hosts() -> tuple[list, list]:
    global _cdn
    now = time.time()
    img, thumb, ts = _cdn
    if now < ts:
        return img, thumb
    try:
        r = client().get(f"{API}/cdn")
        if r.status_code == 200:
            j = r.json()
            img = j.get("image_servers") or img
            thumb = j.get("thumb_servers") or thumb
    except Exception:
        pass
    _cdn = (img, thumb, now + 6 * 3600)
    return img, thumb


def _servers_for(media_id) -> tuple[str, str]:
    """Pilih host i{n} dan t{n} untuk satu gallery (distribusi merata)."""
    img, thumb = _cdn_hosts()
    try:
        idx = int(media_id) % len(img)
    except (TypeError, ValueError):
        idx = 0
    return img[idx % len(img)], thumb[idx % len(thumb)]


# ---------------------------------------------------------------------------
# Relay gambar
# ---------------------------------------------------------------------------
def relay(u: str, n: Optional[int] = None) -> str:
    """Ubah URL gambar sumber menjadi URL relay lokal."""
    if not u or u.startswith("data:") or u.startswith(IMG_ENDPOINT):
        return u
    b = base64.urlsafe_b64encode(u.encode("utf-8")).decode("ascii")
    out = f"{IMG_ENDPOINT}?u={b}"
    if n is not None:
        out += f"&n={n}"
    return out


def decode_img(s: str) -> str:
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
    except Exception:
        return ""


def valid_img_url(u: str) -> bool:
    try:
        h = urllib.parse.urlparse(u).hostname or ""
    except Exception:
        return False
    return any(h == d or h.endswith("." + d) for d in _IMG_HOSTS)


def _ctype_for(path: str) -> str:
    return _CT.get(os.path.splitext(path)[1].lower(), "image/webp")


def fetch_image(src: str) -> tuple[Optional[bytes], Optional[str]]:
    """Ambil gambar dari sumber (dengan cache disk). -> (bytes, ctype).

    Bila `IMG_PROXY` diisi (mis. proxy/VPS), URL dijemput lewat proxy itu.
    """
    if not valid_img_url(src):
        return None, None
    key = hashlib.sha1(src.encode("utf-8")).hexdigest()
    ext = os.path.splitext(urllib.parse.urlparse(src).path)[1].lower()
    if ext not in _CT:
        ext = ".webp"
    path = os.path.join(_IMG_CACHE_DIR, key + ext)
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read(), _ctype_for(path)
    except OSError:
        pass
    target = IMG_PROXY + urllib.parse.quote(src, safe="") if IMG_PROXY else src
    try:
        hdr = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": BASE + "/",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }
        r = client().get(target, headers=hdr)
        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
        if r.status_code == 200 and r.content and (
                ct.startswith("image/") or len(r.content) > 2048):
            data = r.content
            if len(data) <= _MAX_IMG:
                try:
                    os.makedirs(_IMG_CACHE_DIR, exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
                except OSError:
                    pass
            return data, (ct or _ctype_for(path))
    except Exception:
        pass
    return None, None


def placeholder_svg(n: Optional[int] = None) -> bytes:
    label = f"HALAMAN {n}" if n else "GAMBAR MANGA"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1200">'
        '<rect width="800" height="1200" fill="#0f0f13"/>'
        '<rect x="40" y="40" width="720" height="1120" fill="none" '
        'stroke="#26262e" stroke-width="2" stroke-dasharray="10 10"/>'
        '<text x="400" y="560" fill="#3a3a45" font-family="sans-serif" '
        'font-size="26" letter-spacing="6" text-anchor="middle">' + label + '</text>'
        '<text x="400" y="620" fill="#2f2f38" font-family="sans-serif" '
        'font-size="20" text-anchor="middle">Gambar sumber sedang tidak tersedia</text>'
        '<text x="400" y="652" fill="#2f2f38" font-family="sans-serif" '
        'font-size="20" text-anchor="middle">akan muncul otomatis saat sumber pulih</text>'
        '</svg>'
    )
    return svg.encode("utf-8")


# ---------------------------------------------------------------------------
# Resolver tag (id -> nama)
# ---------------------------------------------------------------------------
_tag_cache: dict[int, tuple[str, str]] = {}   # id -> (type, name)
_tag_fail: dict[int, float] = {}              # id -> coba lagi setelah ts
_TAG_RETRY = 120.0


def _tag_load(ids: list) -> None:
    now = time.time()
    need: list[int] = []
    seen: set[int] = set()
    for raw in ids:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if i in _tag_cache or i in seen:
            continue
        if _tag_fail.get(i, 0.0) > now:      # baru saja gagal -> jangan spam
            continue
        seen.add(i)
        need.append(i)
    if not need:
        return
    # endpoint menolak batch besar (400) -> potong 80 id per permintaan
    for k in range(0, len(need), 80):
        chunk = need[k:k + 80]
        got: set[int] = set()
        try:
            csv = ",".join(str(i) for i in chunk)
            r = client().get(f"{API}/tags/ids", params={"ids": csv})
            if r.status_code == 200:
                for t in r.json():
                    tid = int(t.get("id"))
                    _tag_cache[tid] = (t.get("type", ""), t.get("name", ""))
                    got.add(tid)
        except Exception:
            pass
        for i in chunk:
            if i not in got:                  # coba lagi nanti, jangan racuni
                _tag_fail[i] = now + _TAG_RETRY


def _tag(name_or_id) -> tuple[str, str]:
    return _tag_cache.get(name_or_id, ("", ""))


# ---------------------------------------------------------------------------
# Konversi JSON gallery -> model UI
# ---------------------------------------------------------------------------
def _thumb_url(it: dict) -> Optional[str]:
    t = it.get("thumbnail")
    if isinstance(t, dict):
        t = t.get("path")
    if not t or not isinstance(t, str):
        return None
    _, th = _servers_for(it.get("media_id") or 0)
    return th + "/" + t


def gallery_to_manga(it: dict, tags: Optional[dict] = None) -> Manga:
    gid = int(it.get("id") or 0)
    title = (it.get("english_title") or it.get("japanese_title")
             or f"Gallery #{gid}").strip()
    thumb = _thumb_url(it)
    status = None
    genres: list[str] = []
    if tags is None:
        tags = {}
    for tid in (it.get("tag_ids") or []):
        typ, name = tags.get(tid) or ("", "")
        if typ == "category" and not status:
            status = name or None
        elif typ in ("tag", "parody", "artist") and name and len(genres) < 5:
            genres.append(name)
    return Manga(title=title, url=f"{BASE}/g/{gid}/", thumb=relay(thumb)
                 if thumb else None, status=status, rating=None, latest=None,
                 genres=genres, desc=None)


def _gallery(gid: int, fresh: bool = False) -> Optional[dict]:
    if not gid:
        return None
    key = f"gal:{gid}"
    if not fresh:
        hit = _cached(key, _TTL_DETAIL)
        if hit is not None:
            return hit
    try:
        r = client().get(f"{API}/galleries/{gid}")
        if r.status_code != 200:
            return None
        return _set(key, r.json(), _TTL_DETAIL)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API scraper (v2)
# ---------------------------------------------------------------------------
GENRE_PRESETS = [
    {"slug": "type:Doujinshi", "name": "Doujinshi", "q": "category:doujinshi"},
    {"slug": "type:Manga", "name": "Manga", "q": "category:manga"},
    {"slug": "type:Manhwa", "name": "Manhwa", "q": "category:manhwa"},
    {"slug": "type:Western", "name": "Western", "q": "category:western"},
    {"slug": "genre:english", "name": "Inggris", "q": "language:english"},
    {"slug": "genre:indonesian", "name": "Indonesia", "q": "language:indonesian"},
    {"slug": "genre:original", "name": "Original", "q": "parody:original"},
    {"slug": "genre:colorful", "name": "Berwarna", "q": "tag:colored"},
    {"slug": "genre:milf", "name": "Milf", "q": "tag:milf"},
    {"slug": "genre:netorare", "name": "Netorare", "q": "tag:netorare"},
    {"slug": "genre:schoolgirl", "name": "Seragam Sekolah",
     "q": "tag:schoolgirl uniform"},
    {"slug": "genre:fantasy", "name": "Fantasi", "q": "tag:fantasy"},
]


def genre_to_query(genre: str) -> str:
    genre = (genre or "").strip()
    if not genre:
        return ""
    for p in GENRE_PRESETS:
        if genre == p["slug"]:
            return p["q"]
    if genre.startswith("type:"):
        return "category:" + genre[5:].strip().lower()
    if genre.startswith("genre:"):
        return "tag:" + genre[6:].strip()
    return "tag:" + genre


def list_manga(page: int = 1, genre: str = "") -> tuple[list[Manga], int]:
    """Daftar gallery untuk satu halaman. -> (items, max_page)."""
    if not configured():
        return [], 1
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    q = genre_to_query((genre or "").strip())
    key = f"list:{page}|{q}"
    hit = _cached(key, _TTL_LIST)
    if hit is not None:
        return hit
    try:
        params: dict = {"page": page}
        if q:
            params.update(query=q, sort="date")
            url = f"{API}/search"
        else:
            url = f"{API}/galleries"
        r = client().get(url, params=params)
        if r.status_code != 200:
            return [], 1
        j = r.json()
    except Exception:
        return [], 1
    rows = j.get("result") or []
    try:
        _tag_load([i for it in rows for i in (it.get("tag_ids") or [])])
    except Exception:
        pass
    tags = _tag_cache
    items = [gallery_to_manga(it, tags) for it in rows]
    max_page = int(j.get("num_pages") or 1)
    return _set(key, (items, max_page), _TTL_LIST)


def search(q: str) -> list[Manga]:
    """Cari gallery berdasarkan kata kunci (JSON tag/teks). -> [Manga]"""
    q = (q or "").strip()
    if not configured() or not q:
        return []
    try:
        r = client().get(f"{API}/search",
                         params={"query": q, "sort": "date", "page": 1})
        if r.status_code != 200:
            return []
        rows = r.json().get("result") or []
    except Exception:
        return []
    try:
        _tag_load([i for it in rows for i in (it.get("tag_ids") or [])])
    except Exception:
        pass
    return [gallery_to_manga(it, _tag_cache) for it in rows]


def detail(url: str) -> tuple[Optional[Manga], list[Chapter]]:
    """Detail gallery + daftar "chapter" (satu entri per gallery)."""
    gid = gallery_id(url)
    if not gid:
        return None, []
    key = f"det:{url}"
    hit = _cached(key, _TTL_DETAIL)
    if hit is not None:
        return hit
    j = _gallery(gid)
    if not j:
        return None, []
    img, th = _servers_for(j.get("media_id") or 0)
    t = (j.get("thumbnail") or {}).get("path") or ""
    title = ((j.get("title") or {}).get("pretty")
             or (j.get("title") or {}).get("english")
             or (j.get("title") or {}).get("japanese") or f"#{gid}").strip()
    tags = j.get("tags") or []
    status = next((tg["name"] for tg in tags if tg.get("type") == "category"),
                  None)
    genres: list[str] = []
    for tg in tags:
        if tg.get("type") in ("tag", "parody", "artist") and len(genres) < 10:
            genres.append(tg.get("name") or "")
    genres = [g for g in genres if g]
    langs = [tg["name"] for tg in tags
             if tg.get("type") == "language" and tg.get("name")]
    pages = int(j.get("num_pages") or 0)
    fav = int(j.get("num_favorites") or 0)
    parts = [f"{pages} halaman", f"{fav:,}".replace(",", ".") + " favorit"]
    if langs:
        parts.append("Bahasa " + "/".join(langs[:3]))
    if j.get("scanlator"):
        parts.append("Scanlator " + j["scanlator"])
    date = None
    ud = j.get("upload_date")
    if isinstance(ud, (int, float)):
        try:
            date = datetime.datetime.fromtimestamp(ud).strftime("%Y-%m-%d")
        except Exception:
            date = None
    m = Manga(title=title, url=f"{BASE}/g/{gid}/",
              thumb=relay(th + "/" + t) if t else None,
              status=status, rating=f"{pages} hal", latest=None,
              genres=genres, desc=" · ".join(parts))
    chs = [Chapter(label=f"{pages} halaman" if pages else "Baca",
                   url=f"{BASE}/g/{gid}/", date=date)]
    return _set(key, (m, chs), _TTL_DETAIL)


_probe_cache: dict[str, tuple[float, bool]] = {}
_PROBE_TTL = 90


def probe_src(src: str, force: bool = False) -> bool:
    """
    Cek cepat apakah gambar sumber dapat diambil (cache 90 dtk per host).

    Dipakai supaya UI tidak me-render puluhan halaman yang pasti gagal saat
    host gambar sumber sedang menolak koneksi.
    """
    try:
        host = urllib.parse.urlparse(src).hostname or ""
    except Exception:
        host = ""
    now = time.time()
    if not force:
        hit = _probe_cache.get(host)
        if hit and hit[0] > now:
            return hit[1]
    ok = False
    try:
        hdr = {
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": BASE + "/",
            "Range": "bytes=0-2047",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }
        r = client().get(src, headers=hdr)
        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
        ok = r.status_code in (200, 206) and (ct.startswith("image/")
                                              or len(r.content) > 1024)
    except Exception:
        ok = False
    _probe_cache[host] = (now + _PROBE_TTL, ok)
    return ok


def chapter(url: str, fresh: bool = False) -> dict:
    """Halaman baca gallery (semua halaman sekali muat). -> dict images dst."""
    empty = {"images": [], "prev": None, "next": None, "label": "", "src_ok": None}
    gid = gallery_id(url)
    if not gid:
        return empty
    key = f"chap:{url}"
    if not fresh:
        hit = _cached(key, _TTL_CHAPTER)
        if hit is not None:
            return hit
    j = _gallery(gid, fresh=fresh)
    if not j:
        return empty
    img, _ = _servers_for(j.get("media_id") or 0)
    raw = [img + "/" + p.get("path", "") for p in (j.get("pages") or [])
           if p.get("path")]
    raw = [u for u in raw if valid_img_url(u)]
    images = [relay(u, i + 1) for i, u in enumerate(raw)]
    label = ((j.get("title") or {}).get("pretty")
             or (j.get("title") or {}).get("english")
             or (j.get("title") or {}).get("japanese") or f"Gallery #{gid}").strip()
    src_ok = probe_src(raw[0], force=fresh) if raw else None
    res = {"images": images, "prev": None, "next": None, "label": label,
           "src_ok": src_ok}
    return _set(key, res, _TTL_CHAPTER)


def genres() -> list[dict]:
    """Daftar filter (tipe + genre populer). -> [{slug,name}]"""
    if not configured():
        return []
    return [{"slug": p["slug"], "name": p["name"]} for p in GENRE_PRESETS]
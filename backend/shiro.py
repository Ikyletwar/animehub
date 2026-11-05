"""
shiro.py — SHIRO: manga biasa (bukan 18+) via MangaDex API publik.

Kontrak dipakai oleh /api/shiro/*:
  list_items(page, genre, q, sort) -> {items, page, max_page, genre, sort}
  gallery(url)                    -> dict manga + chapters[]
  pages(url, ch)                  -> {images, count, label, src_ok}
  genres()                        -> [{slug, name}]

Catatan sumber:
  - MangaDex API (https://api.mangadex.org), tanpa auth.
  - Hanya contentRating safe; konten suggestive/erotica/pornographic tidak diminta.
  - Katalog diprioritaskan yang punya chapter Bahasa Indonesia, fallback EN.
"""
from __future__ import annotations

import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

BASE = "https://api.mangadex.org"
UPLOADS = "https://uploads.mangadex.org"
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.(jpg|jpeg|png|webp|gif)$", re.I)
UA = "AnimeHub-SHIRO/1.0 (+https://github.com/)"
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}

GENRES = [
    ("Action", "action"), ("Adventure", "adventure"), ("Comedy", "comedy"),
    ("Drama", "drama"), ("Fantasy", "fantasy"), ("Horror", "horror"),
    ("Mystery", "mystery"), ("Romance", "romance"), ("Sci-Fi", "sci-fi"),
    ("Slice of Life", "slice-of-life"), ("Sports", "sports"),
    ("Supernatural", "supernatural"), ("Thriller", "thriller"),
    ("Historical", "historical"), ("Psychological", "psychological"),
    ("Mecha", "mecha"), ("Music", "music"), ("School Life", "school-life"),
    ("Seinen", "seinen"), ("Shounen", "shounen"), ("Josei", "josei"), ("Shoujo", "shoujo"),
]

# Tag MangaDex (genre/theme) yang cocok dengan filter frontend.
_TAG_IDS = {
    "action": "391b0423-d847-456f-aff0-8b0cfc03066b",
    "adventure": "87cc87cd-a395-47af-b27a-93258283bbc6",
    "comedy": "4d32cc48-9f00-4cca-9b5a-a839f0764984",
    "drama": "b9af3a63-f058-46de-a9a0-e0c13906197a",
    "fantasy": "cdc58593-87dd-415e-bbc0-2ec27bf404cc",
    "horror": "cdad7e68-1419-41dd-bdce-27753074a640",
    "mystery": "ee968100-4191-4968-93d3-f82d72be7e46",
    "romance": "423e2eae-a7a2-4a8b-ac03-a8351462d71d",
    "sci-fi": "256c8bd9-4904-4360-bf4f-508a76d67183",
    "slice-of-life": "e5301a23-ebd9-49dd-a0cb-2add944c7fe9",
    "sports": "69964a64-2f90-4d33-beeb-f3ed2875eb4c",
    "supernatural": "eabc5b4c-6aff-42f3-b657-3e90cbd00b75",
    "thriller": "07251805-a27e-4d59-b488-f0bfbec15168",
    "historical": "33771934-028e-4cb3-8744-691e866a923e",
    "psychological": "3b60b75c-a2d7-4860-ab56-05f391bb889c",
    "mecha": "50880a9d-5440-4732-9afb-8f457127e836",
    "music": "f42fbf9e-188a-447b-9fdc-f19dc1e4d685",
    "school-life": "caaa44eb-cd40-4177-b930-79d3ef2afe87",
}
_DEMOGRAPHIC = {"seinen", "shounen", "josei", "shoujo"}
_SORTS = {
    "date": "latestUploadedChapter",
    "popular": "followedCount",
    "rating": "rating",
    "title": "title",
}


def valid_id(s: str) -> bool:
    return bool(s and UUID_RE.match(s.strip()))


def valid_cover_name(name: str) -> bool:
    return bool(name and FILENAME_RE.match(name))


def fetch_cover(manga_id: str, filename: str) -> tuple[bytes, str] | None:
    if not valid_id(manga_id) or not valid_cover_name(filename):
        return None
    url = f"{UPLOADS}/covers/{manga_id}/{filename}"
    try:
        r = httpx.get(url, headers={"User-Agent": UA, "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
                      timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            return None
        return r.content, ctype
    except Exception:
        return None


def _get(url: str, params: Any = None, ttl: int = 300) -> dict:
    key = url + "?" + repr(params or [])
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    r = httpx.get(url, params=params, headers={"User-Agent": UA}, timeout=_TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    d = r.json()
    with _lock:
        _cache[key] = (now + ttl, d)
    return d


def _has_hosted_chapter(manga_id: str) -> bool:
    try:
        d = _get(f"{BASE}/manga/{manga_id}/feed",
                 _params([("limit", 1), ("offset", 0),
                          ("translatedLanguage[]", ["id", "en"]),
                          ("includeEmptyPages", 0),
                          ("includeFutureUpdates", 0)]),
                 ttl=900)
        return int(d.get("total") or 0) > 0
    except Exception:
        # Saat sumber error, tampilkan apa adanya daripada menyembunyikan judul.
        return True


def _params(items: list[tuple[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in items:
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            for x in v:
                out.append((k, str(x)))
        else:
            out.append((k, str(v)))
    return out


def _title(attrs: dict) -> str:
    t = attrs.get("title") or {}
    for k in ("id", "en", "ja-ro", "ko-ro", "zh-ro"):
        if t.get(k):
            return str(t[k])
    return next(iter(t.values()), "Untitled") if t else "Untitled"


def _alt(attrs: dict) -> str:
    for a in attrs.get("altTitles") or []:
        if a.get("en"):
            return str(a["en"])
    for a in attrs.get("altTitles") or []:
        v = next(iter(a.values()), "")
        if v:
            return str(v)
    return ""


def _desc(attrs: dict) -> str:
    d = attrs.get("description") or {}
    for k in ("id", "en"):
        if d.get(k):
            v = str(d[k])
            break
    else:
        v = next(iter(d.values()), "") if d else ""
        v = str(v)
    # rapikan markdown/link yang sering muncul di deskripsi MangaDex
    v = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", v)
    v = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", v)
    v = re.sub(r"https?://\S+", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v[:700]


def _rel(item: dict, rtype: str) -> list[dict]:
    return [r for r in item.get("relationships") or [] if r.get("type") == rtype]


def _cover_url(item: dict) -> str | None:
    mid = item.get("id")
    for r in _rel(item, "cover_art"):
        fn = (r.get("attributes") or {}).get("fileName")
        if fn and FILENAME_RE.match(fn):
            # Lewat relay lokal: browser mendapat challenge dari uploads.mangadex.org.
            return f"/api/shiro/cover/{mid}/{fn}.512.jpg"
    return None


def _author(item: dict) -> str:
    names = []
    for r in _rel(item, "author") + _rel(item, "artist"):
        n = (r.get("attributes") or {}).get("name")
        if n and n not in names:
            names.append(str(n))
    return " · ".join(names[:2])


def _tags(attrs: dict) -> list[str]:
    out = []
    for t in attrs.get("tags") or []:
        n = (t.get("attributes") or {}).get("name", {}).get("en")
        if n and n not in out:
            out.append(str(n))
    return out


def _iso_to_ts(s: str | None) -> int | None:
    if not s:
        return None
    try:
        from datetime import datetime
        s2 = s.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s2).timestamp())
    except Exception:
        return None


def _statistics(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    try:
        d = _get(f"{BASE}/statistics/manga", _params([("manga[]", ids)]), ttl=600)
        return d.get("statistics") or {}
    except Exception:
        return {}


def _item_json(item: dict, stats: dict[str, dict] | None = None) -> dict:
    attrs = item.get("attributes") or {}
    st = (stats or {}).get(item.get("id") or "", {})
    rating = ((st.get("rating") or {}).get("bayesian")
              or (st.get("rating") or {}).get("average") or 0)
    last = attrs.get("lastChapter") or attrs.get("latestUploadedChapter") or 0
    try:
        last_f = float(last)
    except Exception:
        last_f = 0.0
    updated = _iso_to_ts(attrs.get("updatedAt") or attrs.get("createdAt"))
    return {
        "id": item.get("id"),
        "url": item.get("id"),
        "title": _title(attrs),
        "alt": _alt(attrs),
        "author": _author(item),
        "status": (attrs.get("status") or "").capitalize(),
        "year": attrs.get("year") or "",
        "rating": round(float(rating or 0), 2),
        "follows": int(st.get("follows") or 0),
        "favorites": int(st.get("follows") or 0),
        "chapters": int(last_f) if last_f else 0,
        "latest": last_f if last_f else None,
        "pages": 0,
        "genres": _tags(attrs),
        "desc": _desc(attrs),
        "date": updated,
        "thumb": _cover_url(item),
    }


def list_items(page: int = 1, genre: str = "", q: str = "", sort: str = "date") -> dict:
    page = max(1, int(page or 1))
    genre = (genre or "").strip().lower()
    q = (q or "").strip()
    sort = (sort or "date").strip().lower()
    limit = 24
    fetch_limit = 30
    offset = (page - 1) * limit
    params = [
        ("limit", fetch_limit), ("offset", offset),
        ("includes[]", ["cover_art", "author", "artist"]),
        ("contentRating[]", ["safe"]),
        ("availableTranslatedLanguage[]", ["id", "en"]),
        ("order[%s]" % _SORTS.get(sort, "latestUploadedChapter"), "desc"),
    ]
    if q:
        params.append(("title", q))
    if genre:
        if genre in _DEMOGRAPHIC:
            params.append(("publicationDemographic[]", genre))
        elif genre in _TAG_IDS:
            params.append(("includedTags[]", _TAG_IDS[genre]))
    d = _get(f"{BASE}/manga", _params(params), ttl=180)
    rows = d.get("data") or []
    ids = [r.get("id") for r in rows if r.get("id")]
    stats = _statistics(ids)
    # Sembunyikan judul yang tidak punya chapter ID/EN yang dihost (hanya tautan eksternal/resmi).
    with ThreadPoolExecutor(max_workers=12) as ex:
        flags = list(ex.map(_has_hosted_chapter, ids))
    rows = [r for r, ok in zip(rows, flags) if ok][:limit]
    total = int(d.get("total") or 0)
    max_page = max(1, (total + limit - 1) // limit)
    return {"configured": True,
            "items": [_item_json(r, stats) for r in rows],
            "page": page, "max_page": max_page, "total": total,
            "genre": genre, "sort": sort}


def _feed(manga_id: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    limit = 500
    while True:
        d = _get(f"{BASE}/manga/{manga_id}/feed",
                 _params([("limit", limit), ("offset", offset),
                          ("translatedLanguage[]", ["id", "en"]),
                          ("order[chapter]", "asc"),
                          ("includeEmptyPages", 0),
                          ("includeFutureUpdates", 0)]),
                 ttl=300)
        rows = d.get("data") or []
        out.extend(rows)
        offset += len(rows)
        total = int(d.get("total") or 0)
        if not rows or offset >= total or offset >= 5000:
            break
    # Pilih chapter id, kalau tidak ada baru en. Buang duplikat nomor chapter.
    by_no: dict[tuple[int, str], dict] = {}
    for r in out:
        a = r.get("attributes") or {}
        ch = a.get("chapter")
        if not ch:
            continue
        try:
            no = float(ch)
        except Exception:
            continue
        lang = a.get("translatedLanguage") or ""
        key = (no, ch)
        cur = by_no.get(key)
        rank = 0 if lang == "id" else 1
        if cur is None:
            by_no[key] = r
            continue
        cur_lang = (cur.get("attributes") or {}).get("translatedLanguage") or ""
        cur_rank = 0 if cur_lang == "id" else 1
        if rank < cur_rank:
            by_no[key] = r
    items = list(by_no.values())
    def cno(r: dict) -> float:
        try:
            return float((r.get("attributes") or {}).get("chapter") or 0)
        except Exception:
            return 0.0
    items.sort(key=cno)
    return items


def _chapter_json(r: dict) -> dict:
    a = r.get("attributes") or {}
    try:
        no = float(a.get("chapter") or 0)
    except Exception:
        no = 0.0
    ts = _iso_to_ts(a.get("publishAt") or a.get("readableAt") or a.get("createdAt"))
    title = a.get("title") or f"Chapter {a.get('chapter')}"
    return {"no": no, "title": title, "pages": int(a.get("pages") or 0), "date": ts,
            "id": r.get("id"), "lang": a.get("translatedLanguage")}


def gallery(url: str) -> dict | None:
    mid = (url or "").strip()
    if not valid_id(mid):
        return None
    d = _get(f"{BASE}/manga/{mid}",
             _params([("includes[]", ["cover_art", "author", "artist", "tag"])]),
             ttl=300)
    item = d.get("data")
    if not item:
        return None
    stats = _statistics([mid])
    g = _item_json(item, stats)
    try:
        chs = [_chapter_json(r) for r in _feed(mid)]
    except Exception:
        chs = []
    g["chapters"] = chs
    g["latest"] = chs[-1]["no"] if chs else g["latest"]
    g["pages"] = chs[-1]["pages"] if chs else 0
    if chs:
        g["date"] = chs[-1]["date"]
    return g


def pages(url: str, ch: int | float | str) -> dict | None:
    mid = (url or "").strip()
    if not valid_id(mid):
        return None
    try:
        want = float(ch)
    except Exception:
        want = 1.0
    feed = _feed(mid)
    chosen = None
    for r in feed:
        try:
            no = float((r.get("attributes") or {}).get("chapter") or -1)
        except Exception:
            continue
        if abs(no - want) < 1e-9:
            chosen = r
            break
    if not chosen:
        return {"images": [], "count": 0, "label": f"Chapter {want:g}", "src_ok": False,
                "reason": "Tidak ada chapter ID/EN yang dihost di MangaDex"}
    cid = chosen.get("id")
    a = chosen.get("attributes") or {}
    d = _get(f"{BASE}/at-home/server/{cid}", ttl=600)
    base = d.get("baseUrl")
    chap = d.get("chapter") or {}
    h = chap.get("hash")
    data = chap.get("data") or []
    if not base or not h or not data:
        return {"images": [], "count": 0, "label": a.get("title") or f"Chapter {want:g}", "src_ok": False,
                "reason": "Server gambar MangaDex tidak mengembalikan data chapter"}
    urls = [f"{base}/data/{h}/{name}" for name in data]
    src_ok = True
    try:
        r = httpx.get(urls[0], headers={"User-Agent": UA, "Range": "bytes=0-0"},
                      timeout=httpx.Timeout(8.0, connect=5.0), follow_redirects=True)
        src_ok = r.status_code in (200, 206)
    except Exception:
        src_ok = False
    label = a.get("title") or f"Chapter {a.get('chapter')}"
    return {"images": urls, "count": len(urls), "label": label,
            "src_ok": src_ok, "chapter_id": cid,
            "lang": a.get("translatedLanguage")}


def genres() -> list[dict]:
    return [{"slug": slug, "name": name} for name, slug in GENRES]

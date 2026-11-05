"""
yomu.py — adaptor data untuk UI YOMU (mangabaru.html) dari sumber nhentai.

YOMU punya model "manga berchapter", sedangkan nhentai = satu gallery berisi
banyak halaman. Adaptor ini menghasilkan bentuk data yang dibutuhkan template
YOMU (title/alt/author/category/lang/pages/favorites/genres/desc/date/thumb)
dan menyerahkan pembentukan "1 unit baca" ke frontend.

Semua mesin pengambilan (httpx + resolver DoH + relay gambar + cache) dipakai
ulang dari manga.py supaya hanya ada satu sumber kebenaran.
"""
from __future__ import annotations

from typing import Optional

import manga as M

TTL_LIST = 6 * 3600
TTL_DETAIL = 900


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _s(v) -> str:
    return v if isinstance(v, str) else ""


def _meta_from_tag_ids(tag_ids, tags):
    artist = category = lang = ""
    genres: list[str] = []
    for tid in (tag_ids or []):
        try:
            typ, name = tags.get(int(tid)) or ("", "")
        except (TypeError, ValueError):
            continue
        if not name:
            continue
        if typ == "artist":
            artist = artist or name
        elif typ == "category":
            category = category or name
        elif typ == "language":
            lang = lang or name
        elif typ in ("tag", "parody"):
            if len(genres) < 8:
                genres.append(name)
    return artist, category, lang, genres


def _thumb(it) -> Optional[str]:
    t = M._thumb_url(it)
    return M.relay(t) if t else None


def item_from_list(it: dict, tags: dict) -> dict:
    gid = int(it.get("id") or 0)
    title = (_s(it.get("english_title")) or _s(it.get("japanese_title"))
             or f"#{gid}")
    artist, category, lang, genres = _meta_from_tag_ids(it.get("tag_ids"), tags)
    return {
        "id": gid,
        "url": f"{M.BASE}/g/{gid}/",
        "title": title.strip(),
        "alt": _s(it.get("japanese_title")).strip(),
        "author": artist,
        "category": category,
        "lang": lang,
        "pages": int(it.get("num_pages") or 0),
        "favorites": int(it.get("num_favorites") or 0),
        "genres": genres,
        "desc": "",
        "date": None,
        "thumb": _thumb(it),
    }


def item_from_gallery(j: dict) -> dict:
    gid = int(j.get("id") or 0)
    tags = j.get("tags") or []
    t = j.get("title") or {}
    title = (_s(t.get("pretty")) or _s(t.get("english"))
             or _s(t.get("japanese")) or f"#{gid}").strip()
    alt = _s(t.get("japanese")).strip()

    artist = category = lang = ""
    genres: list[str] = []
    langs: list[str] = []
    for tg in tags:
        typ = tg.get("type")
        name = tg.get("name") or ""
        if not name:
            continue
        if typ == "artist":
            artist = artist or name
        elif typ == "category":
            category = category or name
        elif typ == "language":
            lang = lang or name
            langs.append(name)
        elif typ in ("tag", "parody"):
            if len(genres) < 12:
                genres.append(name)

    pages = int(j.get("num_pages") or 0)
    fav = int(j.get("num_favorites") or 0)
    cover = (j.get("thumbnail") or {}).get("path") or ""
    _, th = M._servers_for(j.get("media_id") or 0)

    desc_parts = [f"{pages} halaman", f"{fav:,}".replace(",", ".") + " favorit"]
    if langs:
        desc_parts.append("Bahasa " + "/".join(langs[:3]))
    if j.get("scanlator"):
        desc_parts.append("Scanlator " + j["scanlator"])

    date = j.get("upload_date")
    if not isinstance(date, (int, float)):
        date = None

    return {
        "id": gid,
        "url": f"{M.BASE}/g/{gid}/",
        "title": title,
        "alt": alt,
        "author": artist,
        "category": category,
        "lang": lang,
        "pages": pages,
        "favorites": fav,
        "genres": genres,
        "desc": " · ".join(desc_parts),
        "date": int(date) if date else None,
        "thumb": M.relay(th + "/" + cover) if cover else None,
        "scanlator": j.get("scanlator") or "",
    }


# ---------------------------------------------------------------------------
# Daftar katalog
# ---------------------------------------------------------------------------
def list_items(page: int = 1, genre: str = "", q: str = "",
               sort: str = "date", per_page: int = 36) -> dict:
    if not M.configured():
        return {"configured": False, "items": [], "page": 1, "max_page": 1}
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    q = (q or "").strip()
    sort = sort if sort in ("date", "popular") else "date"
    query = q or M.genre_to_query((genre or "").strip())

    key = f"y:{page}|{query}|{sort}|{per_page}"
    hit = M._cached(key, TTL_LIST)
    if hit is not None:
        return hit

    rows: list = []
    max_page = 1
    try:
        c = M.client()
        if query:
            r = c.get(f"{M.API}/search",
                      params={"query": query, "sort": sort, "page": page})
            if r.status_code == 200:
                j = r.json()
                rows = j.get("result") or []
                max_page = int(j.get("num_pages") or 1)
        elif sort == "popular":
            r = c.get(f"{M.API}/galleries/popular")
            if r.status_code == 200:
                j = r.json()
                rows = j if isinstance(j, list) else (j.get("result") or [])
                max_page = 1
        else:
            r = c.get(f"{M.API}/galleries",
                      params={"page": page, "per_page": per_page})
            if r.status_code == 200:
                j = r.json()
                rows = j.get("result") or []
                max_page = int(j.get("num_pages") or 1)
    except Exception:
        return {"configured": True, "items": [], "page": page, "max_page": 1}

    try:
        M._tag_load([i for it in rows for i in (it.get("tag_ids") or [])])
    except Exception:
        pass

    items = [item_from_list(it, M._tag_cache) for it in rows]
    res = {"configured": True, "items": items, "page": page,
           "max_page": max_page, "sort": sort, "genre": genre}
    return M._set(key, res, TTL_LIST)


def gallery(url: str) -> Optional[dict]:
    gid = M.gallery_id(url)
    if not gid:
        return None
    j = M._gallery(gid)
    if not j:
        return None
    return item_from_gallery(j)


# ---------------------------------------------------------------------------
# Halaman baca (semua halaman gallery)
# ---------------------------------------------------------------------------
def pages(url: str, fresh: bool = False) -> dict:
    d = M.chapter(url, fresh=fresh)
    imgs = d.get("images") or []
    return {"images": imgs, "count": len(imgs), "label": d.get("label") or "",
            "src_ok": d.get("src_ok")}


def genres() -> list:
    return M.genres()

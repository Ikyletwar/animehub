"""
server.py — AnimeHub: web nonton anime dengan scraping real-time.
10 fitur: genre, lanjut nonton, favorit, auto-next, shortcut, download
manager, PWA, full-text search, failover mirror, stats + jadwal rilis.
"""
from __future__ import annotations

import base64
import os
import re
import threading
import time
import urllib.parse

import httpx
from flask import Flask, jsonify, request, Response, send_from_directory

import otakudesu as O
import store as S
import manga as M
import yomu as Y
import shiro as H

app = Flask(__name__, static_folder="web", static_url_path="")
RELAY_TOKEN = "otakudesu"


# ---------------------------------------------------------------------------
# Background: refresh ongoing (realtime) + populate cache anime ke SQLite
# ---------------------------------------------------------------------------
def _refresher():
    while True:
        try:
            O.ongoing(force=True)
        except Exception:
            pass
        time.sleep(180)


def _warmer():
    """
    Isi SQLite bertahap:
      1. daftar 1897 anime (dengan cover, tanpa total_ep)
      2. ambil detail sebagian anime secara bertahap untuk mengisi total_ep,
         genre, sinopsis -> powering fitur search lokal & "Paling Banyak Episode".
   Dijalankan bertahap agar tidak membanjiri sumber.
    """
    time.sleep(15)
    while True:
        try:
            # (1) daftar anime
            if not S.stats().get("anime_cached"):
                S.upsert_anime([a.json() for a in O.list_anime()])
            # (2) lengkapi detail bertahap (rotating cursor via updated_at)
            import store as _S
            with _S._lock:
                c = _S.conn()
                rows = c.execute(
                    "SELECT url FROM anime WHERE total_ep IS NULL OR total_ep='' "
                    "ORDER BY seen_at LIMIT 25").fetchall()
            for r in rows:
                try:
                    an, _ = O.detail(r["url"])
                    if an:
                        S.upsert_anime([an.json()])
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(20)


threading.Thread(target=_refresher, daemon=True).start()
threading.Thread(target=_warmer, daemon=True).start()


# ---------------------------------------------------------------------------
# Halaman + PWA
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return Response(status=302, headers={"Location": "/baru"})


@app.get("/legacy")
@app.get("/legacy/")
def legacy():
    r = send_from_directory(app.static_folder, "index.html")
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r


@app.get("/manifest.webmanifest")
def manifest():
    r = send_from_directory(app.static_folder, "manifest.webmanifest")
    r.headers["Content-Type"] = "application/manifest+json"
    return r


@app.get("/sw.js")
def sw():
    r = send_from_directory(app.static_folder, "sw.js")
    r.headers["Content-Type"] = "application/javascript"
    r.headers["Cache-Control"] = "no-cache"
    return r


# ---------------------------------------------------------------------------
# API — data anime
# ---------------------------------------------------------------------------
@app.get("/api/ongoing")
def api_ongoing():
    return jsonify({"items": [a.json() for a in O.ongoing()]})


@app.get("/api/list")
def api_list():
    items = [a.json() for a in O.list_anime()]
    q = (request.args.get("q") or "").strip().lower()
    if q:
        items = [i for i in items if q in i["title"].lower()]
    for i in items:
        if not i.get("thumb"):
            t, ss = O.thumb_cached(i["url"])
            if t:
                i["thumb"] = t
                i["srcset"] = ss or None
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per = 60
    total = len(items)
    start = (page - 1) * per
    return jsonify({"total": total, "page": page, "per_page": per,
                    "items": items[start:start + per],
                    "max_page": max(1, (total + per - 1) // per)})


@app.get("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    # 1) coba pencarian lokal (SQLite, instan, mencakup 1897 anime)
    try:
        res, total = S.search_anime(q, limit=12)
        if res:
            return jsonify({"results": res, "source": "local", "total": total})
    except Exception:
        pass
    # 2) fallback ke pencarian server sumber (maks 12 hasil)
    return jsonify({"results": [a.json() for a in O.search(q)],
                    "source": "remote"})


@app.get("/api/detail")
def api_detail():
    url = request.args.get("url") or ""
    if not url.startswith(O.BASE):
        return jsonify({"error": "invalid url"}), 400
    anime, eps = O.detail(url)
    if not anime:
        return jsonify({"error": "not found"}), 404
    try:
        S.upsert_anime([anime.json()])
    except Exception:
        pass
    prog = S.get_progress(url)
    return jsonify({"anime": anime.json(),
                    "episodes": [e.json() for e in eps],
                    "batch": [b.json() for b in O.detail_batch(url)],
                    "is_fav": S.is_fav(url),
                    "progress": prog})


@app.get("/api/thumb")
def api_thumb():
    """
    Thumbnail on-demand + srcset untuk List Anime.
    srcset wajib: sumber hanya menyediakan 225px & 212px; tanpa srcset,
    browser meregang 225px ke kartu lebar -> gambar terlihat blur.
    """
    url = request.args.get("url") or ""
    t, ss = O.thumb_bundle(url)
    return jsonify({"thumb": t or None, "srcset": ss or None})


# ---------------------------------------------------------------------------
# API — genre (fitur 1)
# ---------------------------------------------------------------------------
@app.get("/api/genres")
def api_genres():
    return jsonify({"genres": O.genres()})


@app.get("/api/genre")
def api_genre():
    slug = request.args.get("slug") or ""
    page = request.args.get("page", 1)
    try:
        page = max(1, int(page))
    except ValueError:
        page = 1
    items = [a.json() for a in O.by_genre(slug, page)]
    for i in items:
        if not i.get("thumb"):
            t, ss = O.thumb_cached(i["url"])
            if t:
                i["thumb"] = t
                i["srcset"] = ss or None
    return jsonify({"slug": slug, "items": items})


# ---------------------------------------------------------------------------
# API — jadwal rilis & stats (fitur 10)
# ---------------------------------------------------------------------------
@app.get("/api/schedule")
def api_schedule():
    return jsonify({"schedule": O.schedule()})


@app.get("/api/stats")
def api_stats():
    try:
        src = O.stats()
    except Exception:
        src = {}
    try:
        loc = S.stats()
    except Exception:
        loc = {}
    return jsonify({"source": src, "local": loc})


@app.get("/api/top")
def api_top():
    # Diambil dari SQLite (instan). Kalau cache anime belum terisi,
    # jalankan pengisian top-anime di background.
    try:
        items = S.top_anime(12)
    except Exception:
        items = []
    if not items:
        def work():
            try:
                S.upsert_anime([a.json() for a in O.top_anime(12, 24)])
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()
        return jsonify({"items": [], "pending": True})
    return jsonify({"items": items, "pending": False})


# ---------------------------------------------------------------------------
# API — favorit & progres (fitur 2 & 3)
# ---------------------------------------------------------------------------
@app.get("/api/favorites")
def api_fav_list():
    return jsonify({"items": S.list_favs()})


@app.post("/api/favorites")
def api_fav_toggle():
    d = request.get_json(silent=True) or {}
    url = d.get("url") or ""
    if not url.startswith(O.BASE):
        return jsonify({"error": "invalid url"}), 400
    added = S.toggle_fav(url, d.get("title", ""), d.get("thumb", ""))
    return jsonify({"is_fav": added})


@app.post("/api/progress")
def api_progress():
    d = request.get_json(silent=True) or {}
    if not d.get("anime_url"):
        return jsonify({"error": "missing anime_url"}), 400
    S.set_progress(
        d["anime_url"], d.get("episode_url", ""),
        float(d.get("position") or 0), float(d.get("duration") or 0),
        d.get("anime_title", ""), d.get("ep_title", ""),
        d.get("thumb", ""), d.get("quality", ""))
    return jsonify({"ok": True})


@app.get("/api/continue")
def api_continue():
    return jsonify({"items": S.continue_watching(20)})


@app.delete("/api/progress")
def api_progress_clear():
    url = request.args.get("anime_url") or ""
    S.clear_progress(url)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API — streaming (fitur 6, 9)
# ---------------------------------------------------------------------------
@app.get("/api/stream")
def api_stream():
    ep = request.args.get("ep") or ""
    if not ep.startswith(O.BASE):
        return jsonify({"error": "invalid url"}), 400
    q = (request.args.get("q") or "360p").strip()
    srv = request.args.get("i")
    try:
        srv = int(srv) if srv not in (None, "") else None
    except ValueError:
        srv = None
    st = O.resolve_stream(ep, quality=q, server=srv)
    st["meta"] = O.episode_meta(ep)
    if st.get("url"):
        tok = base64.urlsafe_b64encode(st["url"].encode()).decode().rstrip("=")
        st["relay"] = f"/relay?t={RELAY_TOKEN}&u={tok}"
    return jsonify(st)


@app.get("/api/links")
def api_links():
    ep = request.args.get("ep") or ""
    if not ep.startswith(O.BASE):
        return jsonify({"error": "invalid url"}), 400
    return jsonify({"links": O.stream_links(ep)})


@app.get("/api/mirrors")
def api_mirrors():
    """Health check tiap mirror untuk satu episode (fitur 9)."""
    ep = request.args.get("ep") or ""
    if not ep.startswith(O.BASE):
        return jsonify({"error": "invalid url"}), 400
    out = []

    def check(opt):
        import time as _t
        t0 = _t.time()
        emb = O._get(opt["iframe"], referer=ep)
        if not emb:
            out.append({**opt, "ok": False, "ms": 0, "note": "embed gagal dimuat"})
            return
        m = re.search(r"https?://[^\s\"'<>]+?(?:m3u8|videoplayback|\.mp4)[^\s\"'<>]*",
                      emb, re.I)
        if not m:
            out.append({**opt, "ok": False, "ms": int((_t.time()-t0)*1000),
                        "note": "JS/obfuscation (pakai iframe)"})
            return
        url = O.ihtml.unescape(m.group(0))
        valid = O.validate_stream(url)
        out.append({**opt, "ok": valid, "ms": int((_t.time()-t0)*1000),
                    "note": "OK" if valid else "link mati"})

    for opt in O.stream_options(ep):
        check(opt)
    return jsonify({"mirrors": out})


# ---------------------------------------------------------------------------
# API — scheduler untuk stats DB (dipakai frontend)
# ---------------------------------------------------------------------------
@app.post("/api/warm")
def api_warm():
    threading.Thread(target=lambda: S.upsert_anime([a.json() for a in O.list_anime()]),
                     daemon=True).start()
    return jsonify({"ok": True, "note": "warm started"})


# ---------------------------------------------------------------------------
# Relay proxy
# ---------------------------------------------------------------------------
_pool = httpx.Client(
    timeout=httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=30.0),
    limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
    follow_redirects=True,
    headers={"User-Agent": O.UA, "Referer": O.BASE,
             "Accept": "*/*", "Accept-Encoding": "identity"},
)


@app.get("/relay")
def relay():
    if request.args.get("t") != RELAY_TOKEN:
        return Response("forbidden", status=403)
    tok = request.args.get("u") or ""
    pad = "=" * (-len(tok) % 4)
    try:
        target = base64.urlsafe_b64decode(tok + pad).decode()
    except Exception:
        return Response("bad token", status=400)
    if not target.startswith("http"):
        return Response("bad target", status=400)

    headers = {}
    rng = request.headers.get("Range")
    if rng:
        headers["Range"] = rng
    try:
        upstream = _pool.send(_pool.build_request("GET", target, headers=headers),
                              stream=True)
    except Exception:
        return Response("upstream error", status=502)

    out_headers = {
        "Content-Type": upstream.headers.get("content-type", "video/mp4"),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for hk in ("content-length", "content-range"):
        if hk in upstream.headers:
            out_headers[hk.title()] = upstream.headers[hk]

    def gen():
        try:
            for chunk in upstream.iter_bytes(64 * 1024):
                yield chunk
        except Exception:
            pass
        finally:
            upstream.close()

    return Response(gen(), status=upstream.status_code, headers=out_headers)


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})


# ---------------------------------------------------------------------------
# UI alternatif "MIRU" (webbaru.html) — disajikan dari direktori kerjanya
# ---------------------------------------------------------------------------
MIRU_DIR = "/home/nihongo/animehub"


@app.get("/baru")
@app.get("/baru/")
def miru_page():
    try:
        with open(os.path.join(MIRU_DIR, "webbaru.html"), encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return Response("webbaru.html tidak ditemukan", status=404)
    return Response(body, mimetype="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/baru/<path:fname>")
def miru_asset(fname):
    return send_from_directory(MIRU_DIR, fname)


# ---------------------------------------------------------------------------
# API — MANGA  (kerangka; aktif otomatis setelah manga.py dikonfigurasi)
# ---------------------------------------------------------------------------
@app.get("/api/manga/list")
def api_manga_list():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    q = (request.args.get("q") or "").strip()
    genre = (request.args.get("genre") or "").strip()
    cfg = M.configured()
    items, max_page = [], 1
    if cfg:
        try:
            if q:
                items = [m.json() for m in M.search(q)]
            else:
                rows, max_page = M.list_manga(page, genre)
                items = [m.json() for m in rows]
        except Exception:
            items, max_page = [], 1
    return jsonify({"configured": cfg, "items": items, "page": page,
                    "total": len(items), "max_page": max_page})


@app.get("/api/manga/genres")
def api_manga_genres():
    cfg = M.configured()
    gs = []
    if cfg:
        try:
            gs = M.genres()
        except Exception:
            gs = []
    return jsonify({"configured": cfg, "genres": gs})


@app.get("/api/manga/detail")
def api_manga_detail():
    url = request.args.get("url") or ""
    if not M.configured():
        return jsonify({"configured": False, "manga": None, "chapters": []})
    if not M.valid_url(url):
        return jsonify({"error": "invalid url"}), 400
    try:
        manga, chs = M.detail(url)
    except Exception:
        return jsonify({"error": "gagal memuat detail"}), 502
    if not manga:
        return jsonify({"error": "not found"}), 404
    return jsonify({"configured": True, "manga": manga.json(),
                    "chapters": [c.json() for c in chs]})


@app.get("/api/manga/chapter")
def api_manga_chapter():
    url = request.args.get("url") or ""
    fresh = request.args.get("fresh") in ("1", "true", "yes", "on")
    if not M.configured():
        return jsonify({"configured": False, "images": [], "prev": None,
                        "next": None, "label": ""})
    if not M.valid_url(url):
        return jsonify({"error": "invalid url"}), 400
    try:
        d = M.chapter(url, fresh=fresh)
    except Exception:
        return jsonify({"error": "gagal memuat chapter"}), 502
    return jsonify({"configured": True, **d})


@app.get("/api/manga/img")
def api_manga_img():
    """
    Relay gambar manga (thumbnail + isi chapter).

    Browser cukup bicara ke server lokal; server yang mengambil dari sumber
    (dengan resolver DoH untuk host doujindesu.*). Bila sumber gagal diambil,
    dikirim placeholder SVG supaya tiap halaman tetap tampil.
    """
    u = request.args.get("u") or ""
    n = request.args.get("n")
    try:
        n = int(n) if n else None
    except (TypeError, ValueError):
        n = None
    src = M.decode_img(u)
    if not src or not M.valid_img_url(src):
        return Response(M.placeholder_svg(n), mimetype="image/svg+xml",
                        headers={"Cache-Control": "no-store"})
    data, ctype = M.fetch_image(src)
    if not data:
        return Response(M.placeholder_svg(n), mimetype="image/svg+xml",
                        headers={"Cache-Control": "no-store"})
    return Response(data, mimetype=ctype or "image/webp",
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------------------
# UI MANGA "YOMU" (mangabaru.html) — halaman baca mandiri (dari /manga)
# ---------------------------------------------------------------------------
@app.get("/manga")
@app.get("/manga/")
def yomu_page():
    try:
        with open(os.path.join(MIRU_DIR, "mangabaru.html"), encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return Response("mangabaru.html tidak ditemukan", status=404)
    return Response(body, mimetype="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/manga/<path:fname>")
def yomu_asset(fname):
    return send_from_directory(MIRU_DIR, fname)


@app.get("/api/yomu/list")
def api_yomu_list():
    """Katalog YOMU. -> {configured, items, page, max_page, genre, sort}."""
    if not Y.M.configured():
        return jsonify({"configured": False, "items": [], "page": 1,
                        "max_page": 1})
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        d = Y.list_items(page=page,
                         genre=(request.args.get("genre") or "").strip(),
                         q=(request.args.get("q") or "").strip(),
                         sort=(request.args.get("sort") or "date").strip())
    except Exception:
        return jsonify({"configured": True, "items": [], "page": page,
                        "max_page": 1})
    return jsonify(d)


@app.get("/api/yomu/gallery")
def api_yomu_gallery():
    """Detail satu gallery YOMU. -> {configured, gallery}."""
    url = request.args.get("url") or ""
    if not Y.M.configured():
        return jsonify({"configured": False, "gallery": None})
    if not Y.M.valid_url(url):
        return jsonify({"error": "invalid url"}), 400
    try:
        g = Y.gallery(url)
    except Exception:
        return jsonify({"error": "gagal memuat gallery"}), 502
    if not g:
        return jsonify({"error": "not found"}), 404
    return jsonify({"configured": True, "gallery": g})


@app.get("/api/yomu/pages")
def api_yomu_pages():
    """Semua halaman gambar satu gallery. -> {images, count, label, src_ok}."""
    url = request.args.get("url") or ""
    fresh = request.args.get("fresh") in ("1", "true", "yes", "on")
    if not Y.M.configured():
        return jsonify({"configured": False, "images": [], "count": 0})
    if not Y.M.valid_url(url):
        return jsonify({"error": "invalid url"}), 400
    try:
        d = Y.pages(url, fresh=fresh)
    except Exception:
        return jsonify({"error": "gagal memuat halaman"}), 502
    return jsonify({"configured": True, **d})


@app.get("/api/yomu/genres")
def api_yomu_genres():
    cfg = Y.M.configured()
    gs = []
    if cfg:
        try:
            gs = Y.genres()
        except Exception:
            gs = []
    return jsonify({"configured": cfg, "genres": gs})


# ---------------------------------------------------------------------------
# API — SHIRO (manga biasa via MangaDex)
# ---------------------------------------------------------------------------
@app.get("/api/shiro/list")
def api_shiro_list():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    genre = (request.args.get("genre") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    sort = (request.args.get("sort") or "date").strip().lower()
    try:
        return jsonify(H.list_items(page=page, genre=genre, q=q, sort=sort))
    except Exception:
        return jsonify({"configured": True, "items": [], "page": page,
                        "max_page": 1, "genre": genre, "sort": sort})


@app.get("/api/shiro/gallery")
def api_shiro_gallery():
    url = (request.args.get("url") or "").strip()
    if not H.valid_id(url):
        return jsonify({"error": "invalid url"}), 400
    try:
        g = H.gallery(url)
    except Exception:
        return jsonify({"error": "gagal memuat detail"}), 502
    if not g:
        return jsonify({"error": "not found"}), 404
    return jsonify({"configured": True, "manga": g})


@app.get("/api/shiro/pages")
def api_shiro_pages():
    url = (request.args.get("url") or "").strip()
    if not H.valid_id(url):
        return jsonify({"error": "invalid url"}), 400
    try:
        ch = float(request.args.get("ch", "1") or 1)
    except ValueError:
        ch = 1.0
    try:
        d = H.pages(url, ch)
    except Exception:
        return jsonify({"error": "gagal memuat halaman"}), 502
    if not d:
        return jsonify({"error": "not found"}), 404
    return jsonify({"configured": True, **d})


@app.get("/api/shiro/genres")
def api_shiro_genres():
    return jsonify({"configured": True, "genres": H.genres()})


@app.get("/api/shiro/cover/<manga_id>/<filename>")
def api_shiro_cover(manga_id, filename):
    got = H.fetch_cover(manga_id, filename)
    if not got:
        return Response("Not Found", status=404, mimetype="text/plain")
    data, ctype = got
    return Response(data, mimetype=ctype,
                    headers={"Cache-Control": "public, max-age=604800"})


# ---------------------------------------------------------------------------
# UI MANGA "SHIRO" (shirobaru.html) — manga biasa (bukan 18+)
# ---------------------------------------------------------------------------
@app.get("/shiro")
@app.get("/shiro/")
def shiro_page():
    try:
        with open(os.path.join(MIRU_DIR, "shirobaru.html"), encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return Response("shirobaru.html tidak ditemukan", status=404)
    return Response(body, mimetype="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/shiro/<path:fname>")
def shiro_asset(fname):
    return send_from_directory(MIRU_DIR, fname)


if __name__ == "__main__":
    from waitress import serve
    print("AnimeHub  ->  http://127.0.0.1:8000")
    serve(app, host="0.0.0.0", port=8000, threads=24,
          channel_timeout=300, outbuf_overflow=4 * 1024 * 1024,
          max_request_body_size=4 * 1024 * 1024)

"""
store.py — persistensi lokal (SQLite): watch progress, favorit, cache anime.
Dipakai untuk fitur: Lanjut Nonton, Favorites, Full-Text Search.
"""
from __future__ import annotations

import os
import json
import sqlite3
import threading
import time
from typing import Any, Optional

DB_PATH = os.environ.get("ANIMEHUB_DB",
                         os.path.join(os.path.dirname(__file__), "animehub.db"))

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _init(_conn)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript("""
    -- cache anime (agar tidak scrape ulang setelah restart)
    CREATE TABLE IF NOT EXISTS anime (
        url   TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        thumb TEXT,
        genres TEXT,          -- JSON array
        status TEXT,
        scored TEXT,
        total_ep TEXT,
        synopsis TEXT,
        seen_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_anime_title ON anime(title);

    -- progres tonton per anime
    CREATE TABLE IF NOT EXISTS progress (
        anime_url   TEXT NOT NULL,
        episode_url TEXT NOT NULL,
        anime_title TEXT,
        ep_title    TEXT,
        thumb       TEXT,
        position    REAL DEFAULT 0,
        duration    REAL DEFAULT 0,
        quality     TEXT,
        updated_at  REAL,
        PRIMARY KEY (anime_url)
    );
    CREATE INDEX IF NOT EXISTS idx_progress_upd ON progress(updated_at DESC);

    -- favorit
    CREATE TABLE IF NOT EXISTS favorites (
        url       TEXT PRIMARY KEY,
        title     TEXT,
        thumb     TEXT,
        added_at  REAL
    );

    -- riwayat episode ditonton (untuk statistik)
    CREATE TABLE IF NOT EXISTS history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        anime_url  TEXT,
        ep_url     TEXT,
        watched_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_hist_at ON history(watched_at DESC);
    """)
    try:
        # FTS5 untuk pencarian cepat (opsional; skip bila tidak tersedia)
        c.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS anime_fts
          USING fts5(title, content='anime', content_rowid='rowid');
        """)
    except sqlite3.OperationalError:
        pass
    c.commit()


# ---------------------------------------------------------------------------
# Anime cache
# ---------------------------------------------------------------------------
def upsert_anime(items: list[dict]) -> int:
    if not items:
        return 0
    now = time.time()
    rows = [(i.get("url"), i.get("title", ""), i.get("thumb"),
             json.dumps(i.get("genres") or []),
             i.get("status"), i.get("score"), i.get("total_ep"),
             i.get("synopsis"), now)
            for i in items if i.get("url")]
    with _lock:
        c = conn()
        c.executemany("""
            INSERT INTO anime(url,title,thumb,genres,status,scored,total_ep,
                              synopsis,seen_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
              title=excluded.title, thumb=excluded.thumb,
              genres=excluded.genres, status=excluded.status,
              scored=excluded.scored, total_ep=excluded.total_ep,
              synopsis=excluded.synopsis, seen_at=excluded.seen_at
        """, rows)
        c.commit()
    return len(rows)


def search_anime(q: str, limit: int = 60, offset: int = 0) -> tuple[list[dict], int]:
    """Pencarian anime. Coba FTS5 dulu, fallback ke LIKE."""
    q = (q or "").strip()
    if not q:
        return [], 0
    like = f"%{q}%"
    with _lock:
        c = conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM anime WHERE anime_fts MATCH ?",
                          (q.replace('"', ' '),)).fetchone()[0]
            if n:
                rs = c.execute("""
                    SELECT a.url,a.title,a.thumb,a.genres,a.status,a.scored
                    FROM anime_fts f JOIN anime a ON a.rowid=f.rowid
                    WHERE anime_fts MATCH ? ORDER BY rank LIMIT ? OFFSET ?
                """, (q.replace('"', ' '), limit, offset)).fetchall()
                return [_row_anime(r) for r in rs], n
        except sqlite3.OperationalError:
            pass
        n = c.execute("SELECT COUNT(*) FROM anime WHERE title LIKE ?",
                      (like,)).fetchone()[0]
        rs = c.execute("""
            SELECT url,title,thumb,genres,status,scored FROM anime
            WHERE title LIKE ? ORDER BY title LIMIT ? OFFSET ?
        """, (like, limit, offset)).fetchall()
        return [_row_anime(r) for r in rs], n


def _row_anime(r: sqlite3.Row) -> dict:
    try:
        g = json.loads(r["genres"]) if r["genres"] else []
    except Exception:
        g = []
    return {"url": r["url"], "title": r["title"], "thumb": r["thumb"],
            "genres": g, "status": r["status"], "score": r["scored"]}


def top_anime(limit: int = 12) -> list[dict]:
    """Anime dengan total episode terbanyak, diambil dari SQLite (instan)."""
    with _lock:
        c = conn()
        rs = c.execute("""
            SELECT url,title,thumb,genres,status,scored,total_ep
            FROM anime
            WHERE total_ep GLOB '[0-9]*' AND CAST(total_ep AS INTEGER) > 0
            ORDER BY CAST(total_ep AS INTEGER) DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [_row_anime(r) | {"total_ep": r["total_ep"]} for r in rs]


def stats() -> dict:
    with _lock:
        c = conn()
        q = lambda s: c.execute(s).fetchone()[0]
        return {
            "anime_cached": q("SELECT COUNT(*) FROM anime"),
            "with_thumb": q("SELECT COUNT(*) FROM anime WHERE thumb<>'' AND thumb IS NOT NULL"),
            "favorites": q("SELECT COUNT(*) FROM favorites"),
            "in_progress": q("SELECT COUNT(*) FROM progress"),
            "episodes_watched": q("SELECT COUNT(DISTINCT ep_url) FROM history"),
            "db_size_kb": round(os.path.getsize(DB_PATH) / 1024, 1)
            if os.path.exists(DB_PATH) else 0,
        }


# ---------------------------------------------------------------------------
# Watch progress  -> fitur "Lanjut Nonton"
# ---------------------------------------------------------------------------
def set_progress(anime_url: str, episode_url: str, position: float,
                 duration: float, anime_title: str = "", ep_title: str = "",
                 thumb: str = "", quality: str = "") -> None:
    with _lock:
        c = conn()
        c.execute("""
            INSERT INTO progress(anime_url,episode_url,anime_title,ep_title,
                                 thumb,position,duration,quality,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(anime_url) DO UPDATE SET
              episode_url=excluded.episode_url, position=excluded.position,
              duration=excluded.duration, quality=excluded.quality,
              anime_title=excluded.anime_title, ep_title=excluded.ep_title,
              thumb=excluded.thumb, updated_at=excluded.updated_at
        """, (anime_url, episode_url, anime_title, ep_title, thumb,
              position, duration, quality, time.time()))
        c.execute("INSERT INTO history(anime_url,ep_url,watched_at) VALUES(?,?,?)",
                  (anime_url, episode_url, time.time()))
        c.commit()


def continue_watching(limit: int = 20) -> list[dict]:
    with _lock:
        c = conn()
        rs = c.execute("""
            SELECT anime_url,anime_title,ep_title,episode_url,thumb,position,
                   duration,quality,updated_at
            FROM progress ORDER BY updated_at DESC LIMIT ?
        """, (limit,)).fetchall()
    out = []
    for r in rs:
        pct = round(r["position"] / r["duration"] * 100) if r["duration"] else 0
        out.append(dict(r))
        out[-1]["percent"] = min(100, pct)
    return out


def get_progress(anime_url: str) -> Optional[dict]:
    with _lock:
        c = conn()
        r = c.execute("SELECT * FROM progress WHERE anime_url=?",
                      (anime_url,)).fetchone()
    return dict(r) if r else None


def clear_progress(anime_url: str) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM progress WHERE anime_url=?", (anime_url,))
        c.commit()


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------
def add_fav(url: str, title: str = "", thumb: str = "") -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO favorites(url,title,thumb,added_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(url) DO UPDATE SET title=excluded.title, "
                  "thumb=excluded.thumb", (url, title, thumb, time.time()))
        c.commit()


def remove_fav(url: str) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM favorites WHERE url=?", (url,))
        c.commit()


def toggle_fav(url: str, title: str = "", thumb: str = "") -> bool:
    if is_fav(url):
        remove_fav(url)
        return False
    add_fav(url, title, thumb)
    return True


def is_fav(url: str) -> bool:
    with _lock:
        c = conn()
        return c.execute("SELECT 1 FROM favorites WHERE url=?",
                         (url,)).fetchone() is not None


def list_favs() -> list[dict]:
    with _lock:
        c = conn()
        rs = c.execute("SELECT * FROM favorites ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rs]

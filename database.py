"""SQLite persistence e configurazione palette di PixelAnim Broadcast."""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from fastapi import HTTPException

DEVICES_DB = os.getenv("DEVICES_DB", "/data/devices.db")
PALETTE_CACHE_TTL = float(os.getenv("PALETTE_CACHE_TTL", "30"))
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_PALETTE = ["#000000", "#ff6b2b", "#ff2d2d", "#ffd21f", "#39ff14", "#00e5c8",
                   "#2f8bff", "#3a3aff", "#a855f7", "#ff3aa8", "#ffffff", "#ff9d2e",
                   "#9dff2e", "#8a5a2b", "#7a7a7a", "#1d4d2b"]

# ─── DB ──────────────────────────────────────────────────────────────────────

def init_devices_db():
    Path(DEVICES_DB).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DEVICES_DB) as db:
        # WAL: permette letture e scritture concorrenti tra processo principale
        # e processi worker che leggono sottografi dal DB.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("""CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 6053, api_key TEXT NOT NULL,
            width INTEGER NOT NULL DEFAULT 8, height INTEGER NOT NULL DEFAULT 8,
            color INTEGER NOT NULL DEFAULT 0, rotate INTEGER NOT NULL DEFAULT 0,
            autostart INTEGER NOT NULL DEFAULT 0, shuffle INTEGER NOT NULL DEFAULT 0,
            text_fps INTEGER NOT NULL DEFAULT 10)""")
        cols = {r[1] for r in db.execute("PRAGMA table_info(devices)")}
        if "ip" in cols and "host" not in cols:
            db.execute("ALTER TABLE devices RENAME COLUMN ip TO host")
        for name, ddl in (("width", "INTEGER NOT NULL DEFAULT 8"),
                          ("height", "INTEGER NOT NULL DEFAULT 8"),
                          ("color", "INTEGER NOT NULL DEFAULT 0"),
                          ("rotate", "INTEGER NOT NULL DEFAULT 0"),
                          ("autostart", "INTEGER NOT NULL DEFAULT 0"),
                          ("shuffle", "INTEGER NOT NULL DEFAULT 0"),
                          ("text_fps", "INTEGER NOT NULL DEFAULT 10")):
            if name not in cols:
                db.execute(f"ALTER TABLE devices ADD COLUMN {name} {ddl}")

        db.execute("""CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL,
            position INTEGER NOT NULL, animation_id INTEGER NOT NULL,
            duration INTEGER NOT NULL DEFAULT 10, fps INTEGER NOT NULL DEFAULT 8,
            color TEXT NOT NULL DEFAULT '#00ff66', brightness INTEGER NOT NULL DEFAULT 100,
            rotate INTEGER NOT NULL DEFAULT 0, use_palette INTEGER NOT NULL DEFAULT 1,
            type TEXT NOT NULL DEFAULT 'animation', text TEXT NOT NULL DEFAULT '',
            pause_mode TEXT NOT NULL DEFAULT 'hold', graph_id INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1, auto_duration INTEGER NOT NULL DEFAULT 0,
            repeat_x INTEGER NOT NULL DEFAULT 0)""")
        pcols = {r[1] for r in db.execute("PRAGMA table_info(playlist_items)")}
        for name, ddl in (("use_palette", "INTEGER NOT NULL DEFAULT 1"),
                          ("type", "TEXT NOT NULL DEFAULT 'animation'"),
                          ("text", "TEXT NOT NULL DEFAULT ''"),
                          ("pause_mode", "TEXT NOT NULL DEFAULT 'hold'"),
                          ("graph_id", "INTEGER NOT NULL DEFAULT 0"),
                          ("enabled", "INTEGER NOT NULL DEFAULT 1"),
                          ("auto_duration", "INTEGER NOT NULL DEFAULT 0"),
                          ("repeat_x", "INTEGER NOT NULL DEFAULT 0")):
            if name not in pcols:
                db.execute(f"ALTER TABLE playlist_items ADD COLUMN {name} {ddl}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_playlist_device "
                   "ON playlist_items(device_id, position)")

        db.execute("""CREATE TABLE IF NOT EXISTS graphs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            data TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.commit()


def device_db():
    db = sqlite3.connect(DEVICES_DB)
    db.row_factory = sqlite3.Row
    return db


def public_device(row):
    return {"id": row["id"], "name": row["name"], "host": row["host"],
            "port": row["port"], "has_key": bool(row["api_key"]),
            "width": row["width"], "height": row["height"],
            "color": bool(row["color"]), "rotate": bool(row["rotate"]),
            "autostart": bool(row["autostart"]),
            "shuffle": bool(row["shuffle"]) if "shuffle" in row.keys() else False,
            "text_fps": row["text_fps"]}


def autostart_devices():
    with device_db() as db:
        return db.execute("SELECT id, name FROM devices WHERE autostart=1 AND api_key != '' "
                          "AND EXISTS (SELECT 1 FROM playlist_items WHERE device_id=devices.id)").fetchall()


def get_device(device_id):
    with device_db() as db:
        return db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()


def item_dict(row):
    return {"animation_id": row["animation_id"], "duration": row["duration"],
            "fps": row["fps"], "color": row["color"], "brightness": row["brightness"],
            "rotate": bool(row["rotate"]), "use_palette": bool(row["use_palette"]),
            "type": row["type"], "text": row["text"], "pause_mode": row["pause_mode"],
            "graph_id": row["graph_id"], "enabled": bool(row["enabled"]),
            "auto_duration": bool(row["auto_duration"]), "repeat_x": row["repeat_x"]}


def playlist_items(device_id):
    with device_db() as db:
        rows = db.execute("SELECT * FROM playlist_items WHERE device_id=? ORDER BY position",
                          (device_id,)).fetchall()
    return [item_dict(r) for r in rows]


def save_playlist(device_id, items):
    with device_db() as db:
        db.execute("DELETE FROM playlist_items WHERE device_id=?", (device_id,))
        db.executemany(
            "INSERT INTO playlist_items(device_id,position,animation_id,duration,fps,color,"
            "brightness,rotate,use_palette,type,text,pause_mode,graph_id,enabled,"
            "auto_duration,repeat_x) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(device_id, pos, it["animation_id"], it["duration"], it["fps"], it["color"],
              it["brightness"], int(it["rotate"]), int(it["use_palette"]), it["type"],
              it["text"], it["pause_mode"], it["graph_id"], int(it["enabled"]),
              int(it["auto_duration"]), int(it.get("repeat_x", 0)))
             for pos, it in enumerate(items)])
        db.commit()


def normalize_palette(palette):
    if len(palette) != 16 or not all(COLOR_RE.match(c) for c in palette):
        raise HTTPException(400, "La palette deve avere 16 colori nel formato #rrggbb")
    return ["#000000"] + [c.lower() for c in palette[1:]]


# ─── Cache palette ───────────────────────────────────────────────────────────
_palette_cache = {"value": None, "ts": 0.0}
_palette_cache_stats = {
    "hits": 0, "misses": 0, "expired": 0,
    "loads": 0, "invalidations": 0,
}


def get_palette():
    now = time.time()
    if _palette_cache["value"] is not None:
        if now - _palette_cache["ts"] < PALETTE_CACHE_TTL:
            _palette_cache_stats["hits"] += 1
            return _palette_cache["value"]
        _palette_cache_stats["expired"] += 1
    _palette_cache_stats["misses"] += 1
    with device_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key='palette'").fetchone()
    if row:
        try:
            val = normalize_palette(json.loads(row["value"]))
        except (ValueError, HTTPException):
            val = DEFAULT_PALETTE[:]
    else:
        val = DEFAULT_PALETTE[:]
    _palette_cache["value"] = val
    _palette_cache["ts"] = now
    _palette_cache_stats["loads"] += 1
    return val


def _invalidate_palette_cache():
    _palette_cache_stats["invalidations"] += 1
    _palette_cache["value"] = None
    _palette_cache["ts"] = 0.0



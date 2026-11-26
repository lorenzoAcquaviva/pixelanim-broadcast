import logging
from collections import deque
from logging.handlers import RotatingFileHandler
import ast, asyncio, hashlib, ipaddress, itertools, json, math, os, random, re, signal, socket, sqlite3, threading, time, unicodedata
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx, websockets
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from database import (
    DEVICES_DB, PALETTE_CACHE_TTL, init_devices_db, device_db, public_device,
    autostart_devices, get_device, item_dict, playlist_items, save_playlist,
    normalize_palette, get_palette, _invalidate_palette_cache,
    _palette_cache, _palette_cache_stats,
)

BASE_DIR = Path(__file__).resolve().parent
MAX_PLAYLIST_ITEMS = 100

PIXELANIM_URL = os.getenv("PIXELANIM_URL", "http://pixelanim:9900").rstrip("/")
PIXELANIM_WS = os.getenv("PIXELANIM_WS") or (
    PIXELANIM_URL.replace("https://", "wss://").replace("http://", "ws://")
    + "/api/esp/stream"
)


FIRMWARE_DIMS = {(8, 8), (32, 8), (16, 16)}

# ─── Opzioni cache (via env) ─────────────────────────────────────────────────
# RENDER_CACHE_DISABLED=1  → disattiva completamente la cache dei render
# RENDER_RESULT_MAX=N      → max voci in cache (default 8)
# RENDER_RESULT_MAX_FRAMES → cap complessivo frame in cache (default 24000)
RENDER_CACHE_DISABLED     = os.getenv("RENDER_CACHE_DISABLED", "0").lower() in ("1", "true", "yes", "on")
RENDER_RESULT_MAX         = max(1, int(os.getenv("RENDER_RESULT_MAX", "8")))
RENDER_RESULT_MAX_FRAMES  = max(1, int(os.getenv("RENDER_RESULT_MAX_FRAMES", "24000")))

_rev_counter = itertools.count(1)
active_frames: dict = {}

# Input esterni: registro globale volatile, mai persistito nel database.
_EXTERNAL_INPUTS: dict[str, dict] = {}
_EXTERNAL_INPUT_LOCK = threading.RLock()
_EXTERNAL_INPUT_REVISION = 0
MAX_EXTERNAL_INPUTS = 256

def set_external_inputs(values: dict) -> int:
    """Aggiorna in modo atomico il registro globale degli input numerici."""
    global _EXTERNAL_INPUT_REVISION
    now = time.time()
    with _EXTERNAL_INPUT_LOCK:
        if len(_EXTERNAL_INPUTS) + sum(1 for k in values if k not in _EXTERNAL_INPUTS) > MAX_EXTERNAL_INPUTS:
            raise ValueError(f"Massimo {MAX_EXTERNAL_INPUTS} chiavi esterne")
        for key, value in values.items():
            _EXTERNAL_INPUTS[key] = {"value": float(value), "updated_at": now}
        _EXTERNAL_INPUT_REVISION += 1
        return _EXTERNAL_INPUT_REVISION

def get_external_inputs() -> tuple[dict, int]:
    with _EXTERNAL_INPUT_LOCK:
        return ({k: dict(v) for k, v in _EXTERNAL_INPUTS.items()}, _EXTERNAL_INPUT_REVISION)

def external_input_revision() -> int:
    with _EXTERNAL_INPUT_LOCK:
        return _EXTERNAL_INPUT_REVISION


def _set_active_frame(device_id, frames, fps, width, height, color, palette, rgb, running=True):
    active_frames[device_id] = {
        "rev": next(_rev_counter), "frames": frames, "fps": fps,
        "width": width, "height": height, "color": color,
        "palette": palette, "rgb": rgb,
        "running": running, "updated_at": time.time()}


def _mark_running(device_id, running):
    if device_id in active_frames:
        active_frames[device_id]["running"] = running
        active_frames[device_id]["updated_at"] = time.time()


# ─── Logging su file (per analisi storica) ───────────────────────────────────
LOG_FILE       = os.getenv("LOG_FILE", "/data/pixelanim.log")
LOG_MAX_BYTES  = int(os.getenv("LOG_MAX_BYTES", "5000000"))
LOG_BACKUPS    = int(os.getenv("LOG_BACKUPS", "3"))

log = logging.getLogger("pixelanim")
log.setLevel(logging.INFO)


def _setup_logging():
    """stdout: messaggio nudo (docker logs invariato). File: con timestamp."""
    if log.handlers:
        return
    log.propagate = False
    try:
        Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES,
                                  backupCount=LOG_BACKUPS, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(fh)
    except OSError as exc:
        print(f"[log] impossibile aprire {LOG_FILE}: {exc}", flush=True)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)


# ─── Statistiche runtime (in memoria, sempre accurate) ──────────────────────
_STATS_MAX_DURATIONS = int(os.getenv("STATS_MAX_DURATIONS", "5000"))
_STATS_MAX_ERRORS    = 100

_stats = {
    "started_at": time.time(),
    "render": {
        "total": 0, "hit": 0, "miss": 0, "bypass": 0,
        "durations": deque(maxlen=_STATS_MAX_DURATIONS),
        "by_scope": {},       # static/hour/minute/second/other -> {hit,miss,bypass}
        "by_resolution": {},  # "WxH" -> {hit,miss,bypass}
        "by_fps": {},         # fps (str) -> {hit,miss,bypass}
    },
    "play_item": {
        "total": 0,
        "by_kind": {},        # kind -> count
        "by_device": {},      # device_id (str) -> count
    },
    "errors": deque(maxlen=_STATS_MAX_ERRORS),
}


def _scope_type(scope: str) -> str:
    if scope == "static":
        return "static"
    if len(scope) == 7:      # tHHMMSS
        return "second"
    if len(scope) == 5:      # tHHMM
        return "minute"
    if len(scope) == 3:      # tHH
        return "hour"
    return "other"


def _bump(bucket: dict, key, field: str):
    slot = bucket.setdefault(str(key), {"hit": 0, "miss": 0, "bypass": 0})
    slot[field] = slot.get(field, 0) + 1


def _stats_record_render(status: str, scope: str, width: int, height: int,
                          fps: int, duration: float):
    r = _stats["render"]
    r["total"] += 1
    r[status] += 1
    r["durations"].append(float(duration))
    stype = _scope_type(scope)
    _bump(r["by_scope"], stype, status)
    _bump(r["by_resolution"], f"{width}x{height}", status)
    _bump(r["by_fps"], fps, status)


def _stats_record_play(device_id, kind: str):
    p = _stats["play_item"]
    p["total"] += 1
    p["by_kind"][kind] = p["by_kind"].get(kind, 0) + 1
    p["by_device"][str(device_id)] = p["by_device"].get(str(device_id), 0) + 1


def _stats_record_error(msg: str):
    _stats["errors"].append({
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "msg": msg,
    })


def _percentile(sorted_values, p: float):
    if not sorted_values:
        return None
    k = int(round((len(sorted_values) - 1) * p))
    return sorted_values[k]


def _pct(part, total):
    return f"{(part / total * 100):5.1f}%" if total else "  n/a "


def _build_stats() -> dict:
    r, p = _stats["render"], _stats["play_item"]
    durations = sorted(r["durations"])
    return {
        "uptime_sec": round(time.time() - _stats["started_at"], 1),
        "render": {
            "total": r["total"],
            "hit": r["hit"], "miss": r["miss"], "bypass": r["bypass"],
            "hit_ratio": round(r["hit"] / r["total"], 3) if r["total"] else None,
            "duration_sec": {
                "count": len(durations),
                "min": round(durations[0], 3) if durations else None,
                "max": round(durations[-1], 3) if durations else None,
                "avg": round(sum(durations) / len(durations), 3) if durations else None,
                "p50": round(_percentile(durations, 0.50), 3) if durations else None,
                "p95": round(_percentile(durations, 0.95), 3) if durations else None,
            },
            "by_scope": r["by_scope"],
            "by_resolution": r["by_resolution"],
            "by_fps": r["by_fps"],
        },
        "play_item": {
            "total": p["total"],
            "by_kind": p["by_kind"],
            "by_device": p["by_device"],
        },
        "cache": {
            "disabled": RENDER_CACHE_DISABLED,
            "entries": len(_render_result_cache),
            "frames": sum(len(e["result"]["frames"]) for e in _render_result_cache.values()),
            "max_entries": RENDER_RESULT_MAX,
            "max_frames": RENDER_RESULT_MAX_FRAMES,
            "stats": dict(_render_cache_stats),
            "hit_rate": (
                round(_render_cache_stats["hits"] / (
                    _render_cache_stats["hits"] + _render_cache_stats["misses"]), 4)
                if (_render_cache_stats["hits"] + _render_cache_stats["misses"]) else None
            ),
        },
        "palette_cache": {
            "ttl_sec": PALETTE_CACHE_TTL,
            "loaded": _palette_cache["value"] is not None,
            "age_sec": round(time.time() - _palette_cache["ts"], 2)
                if _palette_cache["value"] is not None else None,
            "stats": dict(_palette_cache_stats),
            "hit_rate": (
                round(_palette_cache_stats["hits"] / (
                    _palette_cache_stats["hits"] + _palette_cache_stats["misses"]), 4)
                if (_palette_cache_stats["hits"] + _palette_cache_stats["misses"]) else None
            ),
        },
        "anim_cache": {
            "entries": len(ANIM_CACHE),
            "ids": sorted(ANIM_CACHE.keys()),
        },
        "recent_errors": list(_stats["errors"])[-20:],
    }


def _format_stats_text(s: dict) -> str:
    lines = ["=" * 62, " PixelAnim Broadcast — report", "=" * 62]
    if "uptime_sec" in s:
        lines.append(f" uptime: {s['uptime_sec']:.0f}s")
    if s.get("log_file"):
        lines.append(f" log:    {s['log_file']}")
        lines.append(f" righe:  {s.get('lines_analyzed', '?')} analizzate")

    r = s.get("render") or {}
    if r:
        lines += ["", " RENDER",
                  f"  totali: {r.get('total', 0)}",
                  f"  hit:    {r.get('hit', 0)}  ({_pct(r.get('hit', 0), r.get('total', 0))})",
                  f"  miss:   {r.get('miss', 0)} ({_pct(r.get('miss', 0), r.get('total', 0))})",
                  f"  bypass: {r.get('bypass', 0)}"]
        d = r.get("duration_sec") or {}
        if d.get("count"):
            lines.append(f"  durata: min={d['min']}s  p50={d['p50']}s  "
                         f"p95={d['p95']}s  avg={d['avg']}s  max={d['max']}s  "
                         f"(n={d['count']})")
        lines.append("")
        lines.append("  Per scope:")
        for k in ("static", "hour", "minute", "second", "other"):
            v = (r.get("by_scope") or {}).get(k)
            if v:
                t = v["hit"] + v["miss"] + v["bypass"]
                lines.append(f"   {k:8s}: {t:5d}  hit={v['hit']:5d} "
                             f"miss={v['miss']:5d} bypass={v['bypass']:5d} "
                             f"({_pct(v['hit'], t)})")
        lines.append("")
        lines.append("  Per risoluzione:")
        for k, v in sorted((r.get("by_resolution") or {}).items()):
            t = v["hit"] + v["miss"] + v["bypass"]
            lines.append(f"   {k:8s}: {t:5d} (hit {_pct(v['hit'], t)})")
        lines.append("")
        lines.append("  Per fps:")
        for k, v in sorted((r.get("by_fps") or {}).items(),
                            key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
            t = v["hit"] + v["miss"] + v["bypass"]
            lines.append(f"   {str(k):8s}: {t:5d} (hit {_pct(v['hit'], t)})")

    p = s.get("play_item") or {}
    if p:
        lines += ["", " PLAY ITEM", f"  totali: {p.get('total', 0)}"]
        for k, n in sorted((p.get("by_kind") or {}).items()):
            lines.append(f"   kind={k:10s} {n:5d}")
        if p.get("by_device"):
            lines.append("  Per device:")
            for k, n in sorted((p.get("by_device") or {}).items()):
                lines.append(f"   device={k:10s} {n:5d}")

    c = s.get("cache")
    if c:
        lines += ["", " CACHE",
                  f"  render: {'DISATTIVATA' if c['disabled'] else 'attiva'}",
                  f"   voci: {c['entries']}/{c['max_entries']}  "
                  f"frame: {c['frames']}/{c['max_frames']}"]
        ac = s.get("anim_cache") or {}
        lines.append(f"  animazioni: {ac.get('entries', 0)} "
                     f"({', '.join(str(i) for i in ac.get('ids', [])) or '—'})")

    errs = s.get("recent_errors")
    if errs:
        lines += ["", " ERRORI RECENTI"]
        for e in errs[-10:]:
            lines.append(f"  [{e['ts']}] {e['msg']}")

    return "\n".join(lines) + "\n"


def _tail_lines(path: str, max_lines: int):
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8", errors="replace") as f:
        return list(deque(f, maxlen=max_lines))

# ─── Host / PlayError ────────────────────────────────────────────────────────

HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                     r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


def checked_host(value):
    value = value.strip().rstrip(".")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if HOST_RE.match(value):
        return value.lower()
    raise HTTPException(400, "Indirizzo IP o nome host non valido")


class PlayError(Exception):
    def __init__(self, msg, status=502):
        super().__init__(msg)
        self.msg, self.status = msg, status


async def resolve_host(host):
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise PlayError(f"Impossibile risolvere il nome host: {host}", 400)
    return infos[0][4][0]


# ─── Encoding ────────────────────────────────────────────────────────────────

def check_dims(device):
    w, h = int(device["width"]), int(device["height"])
    if (w, h) not in FIRMWARE_DIMS:
        raise PlayError(
            f"Board {w}×{h} non supportata dal firmware (FIRMWARE_DIMS={sorted(FIRMWARE_DIMS)}). "
            f"Flasha un firmware aggiornato o cambia le dimensioni della board.", 400)


def tile_frames(frames, src_w, src_h, dst_w, dst_h, repeat_x=1):
    if src_w <= 0 or src_h <= 0:
        return frames
    if repeat_x <= 0:
        repeat_x = 1
    if repeat_x * src_w > dst_w:
        repeat_x = max(1, dst_w // src_w)
    y_off = max(0, (dst_h - src_h) // 2)
    out = []
    for f in frames:
        pixels = f.get("pixels", [])
        new_pix = [[0] * dst_w for _ in range(dst_h)]
        for r in range(min(src_h, dst_h)):
            row = pixels[r] if r < len(pixels) else []
            for copy in range(repeat_x):
                x0 = copy * src_w
                if x0 >= dst_w:
                    break
                for c in range(min(src_w, dst_w - x0)):
                    new_pix[y_off + r][x0 + c] = row[c] if c < len(row) else 0
        out.append({"pixels": new_pix})
    return out


def encode_animation(frames, width, height, rotate=False):
    bpr = max(1, width // 8)
    out = []
    for frame in frames:
        pixels = frame.get("pixels", [])
        rows = []
        for r in range(height):
            row = []
            for block in range(bpr):
                byte = 0
                for bit in range(8):
                    col = block * 8 + bit
                    if col < width and r < len(pixels) and col < len(pixels[r]) and pixels[r][col]:
                        byte |= 1 << (7 - bit)
                row.append(byte)
            rows.append(row)
        if rotate:
            rows.reverse()
            rows = [list(reversed(row)) for row in rows]
            rows = [[int(f"{v:08b}"[::-1], 2) for v in row] for row in rows]
        out.append("".join(f"{v:02x}" for row in rows for v in row))
    return out


def encode_color(frames, width, height, rotate=False, binary=False):
    out = []
    for frame in frames:
        pixels = frame.get("pixels", [])
        cells = []
        for r in range(height):
            for c in range(width):
                try:
                    v = int(pixels[r][c]) if r < len(pixels) and c < len(pixels[r]) else 0
                except (TypeError, ValueError):
                    v = 0
                v = max(0, min(15, v))
                cells.append((1 if v else 0) if binary else v)
        if rotate:
            cells.reverse()
        out.append("".join(f"{v:x}" for v in cells))
    return out


def build_palette(use_palette, color, brightness, palette):
    entries = ["#000000"] * 16
    if use_palette:
        entries = ["#000000"] + palette[1:]
    else:
        entries[1] = color
    out = []
    for c in entries:
        out += [f"{round(int(c[i:i+2], 16) * brightness / 100):02x}" for i in (1, 3, 5)]
    return "".join(out)


# ─── Font 5×7 ────────────────────────────────────────────────────────────────

FONT_5X7 = {
    ' ': (0, 0), '!': (95,), '"': (3, 0, 3), '#': (20, 127, 20, 127, 20),
    '$': (36, 42, 127, 42, 18), '%': (35, 19, 8, 100, 98), '&': (54, 73, 85, 34, 80), "'": (5, 3),
    '(': (28, 34, 65), ')': (65, 34, 28), '*': (20, 8, 62, 8, 20), '+': (8, 8, 62, 8, 8),
    ',': (80, 48), '-': (8, 8, 8, 8, 8), '.': (96, 96), '/': (32, 16, 8, 4, 2),
    '0': (62, 81, 73, 69, 62), '1': (66, 127, 64), '2': (66, 97, 81, 73, 70),
    '3': (33, 65, 69, 75, 49), '4': (24, 20, 18, 127, 16), '5': (39, 69, 69, 69, 57),
    '6': (60, 74, 73, 73, 48), '7': (1, 113, 9, 5, 3), '8': (54, 73, 73, 73, 54),
    '9': (6, 73, 73, 41, 30), ':': (54, 54), ';': (86, 54), '<': (8, 20, 34, 65),
    '=': (20, 20, 20, 20, 20), '>': (65, 34, 20, 8), '?': (2, 1, 81, 9, 6),
    '@': (50, 73, 121, 65, 62), 'A': (126, 17, 17, 17, 126), 'B': (127, 73, 73, 73, 54),
    'C': (62, 65, 65, 65, 34), 'D': (127, 65, 65, 34, 28), 'E': (127, 73, 73, 73, 65),
    'F': (127, 9, 9, 9, 1), 'G': (62, 65, 73, 73, 122), 'H': (127, 8, 8, 8, 127),
    'I': (65, 127, 65), 'J': (32, 64, 65, 63, 1), 'K': (127, 8, 20, 34, 65),
    'L': (127, 64, 64, 64, 64), 'M': (127, 2, 12, 2, 127), 'N': (127, 4, 8, 16, 127),
    'O': (62, 65, 65, 65, 62), 'P': (127, 9, 9, 9, 6), 'Q': (62, 65, 81, 33, 94),
    'R': (127, 9, 25, 41, 70), 'S': (70, 73, 73, 73, 49), 'T': (1, 1, 127, 1, 1),
    'U': (63, 64, 64, 64, 63), 'V': (31, 32, 64, 32, 31), 'W': (63, 64, 56, 64, 63),
    'X': (99, 20, 8, 20, 99), 'Y': (7, 8, 112, 8, 7), 'Z': (97, 81, 73, 69, 67), '[': (127, 65, 65),
    '\\': (2, 4, 8, 16, 32), ']': (65, 65, 127), '^': (4, 2, 1, 2, 4), '_': (64, 64, 64, 64, 64),
    '`': (1, 2, 4), 'a': (32, 84, 84, 84, 120), 'b': (127, 72, 68, 68, 56),
    'c': (56, 68, 68, 68, 32), 'd': (56, 68, 68, 72, 127), 'e': (56, 84, 84, 84, 24),
    'f': (8, 126, 9, 1, 2), 'g': (12, 82, 82, 82, 62), 'h': (127, 8, 4, 4, 120), 'i': (68, 125, 64),
    'j': (32, 64, 68, 61), 'k': (127, 16, 40, 68), 'l': (65, 127, 64), 'm': (124, 4, 24, 4, 120),
    'n': (124, 8, 4, 4, 120), 'o': (56, 68, 68, 68, 56), 'p': (124, 20, 20, 20, 8),
    'q': (8, 20, 20, 24, 124), 'r': (124, 8, 4, 4, 8), 's': (72, 84, 84, 84, 32),
    't': (4, 63, 68, 64, 32), 'u': (60, 64, 64, 32, 124), 'v': (28, 32, 64, 32, 28),
    'w': (60, 64, 48, 64, 60), 'x': (68, 40, 16, 40, 68), 'y': (12, 80, 80, 80, 60),
    'z': (68, 100, 84, 76, 68), '{': (8, 54, 65), '|': (127,), '}': (65, 54, 8),
    '~': (16, 8, 8, 16, 8)}

DIGITS_3X5 = {
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111")}


def _build_accented():
    for ch, (base, kind) in {"à": ("a", "g"), "è": ("e", "g"), "é": ("e", "a"),
                             "ì": ("i", "g"), "ò": ("o", "g"), "ù": ("u", "g")}.items():
        cols = [c & ~0b11 for c in FONT_5X7[base]]
        mid = len(cols) // 2
        if kind == "g":
            cols[max(mid - 1, 0)] |= 1
        else:
            cols[min(mid + 1, len(cols) - 1)] |= 1
        cols[mid] |= 2
        FONT_5X7[ch] = tuple(cols)
    FONT_5X7["°"] = (2, 5, 2)


_build_accented()
_PUNCT = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...",
          "\u00a0": " ", "\n": " ", "\t": " ", "\r": " "}


def normalize_text(text):
    out = []
    for ch in unicodedata.normalize("NFC", text):
        if ch in FONT_5X7:
            out.append(ch)
        elif ch in _PUNCT:
            out.append(_PUNCT[ch])
        else:
            base = unicodedata.normalize("NFD", ch)[0]
            if ch in "ÀÈÉÌÒÙ" and base in FONT_5X7:
                out.append(base + "'")
            else:
                out.append(base if base in FONT_5X7 else "?")
    return "".join(out)


def text_columns(text):
    cols = []
    for ch in normalize_text(text):
        if cols:
            cols.append(0)
        cols.extend(FONT_5X7[ch])
    return cols


def _default_y0(height):
    return 1 if height <= 8 else max(1, (height - 8) // 2 + 1)


def cols_to_frame(cols, width, height, x0, y0=None):
    if y0 is None:
        y0 = _default_y0(height)
    frame = [[0] * width for _ in range(height)]
    for i, bits in enumerate(cols):
        x = x0 + i
        if 0 <= x < width:
            for r in range(7):
                if (bits >> r) & 1 and 0 <= y0 + r < height:
                    frame[y0 + r][x] = 1
    return frame


def render_text(text, width, height):
    cols = text_columns(text)
    if not any(cols):
        raise PlayError("Testo vuoto", 400)
    if len(cols) <= width:
        return [cols_to_frame(cols, width, height, (width - len(cols)) // 2)]
    return [cols_to_frame(cols, width, height, width - k) for k in range(1, len(cols) + width)]


def _digit_cols(d):
    g = list(FONT_5X7[d])
    left = (5 - len(g)) // 2
    return [0] * left + g + [0] * (5 - len(g) - left)


def _pair_3x5(s, height):
    y0 = max(0, (height - 5) // 2)
    frame = [[0] * 8 for _ in range(height)]
    for n, d in enumerate(s):
        for r, line in enumerate(DIGITS_3X5[d]):
            for c, v in enumerate(line):
                if v == "1" and 0 <= y0 + r < height:
                    frame[y0 + r][n * 4 + c] = 1
    return frame


def render_clock(width, height, now):
    hh, mm = f"{now.hour:02d}", f"{now.minute:02d}"
    if width >= 32:
        left = _digit_cols(hh[0]) + [0] + _digit_cols(hh[1])
        right = _digit_cols(mm[0]) + [0] + _digit_cols(mm[1])
        colon = list(FONT_5X7[":"])
        x0 = (width - (len(left) + len(right) + len(colon) + 2)) // 2
        on = left + [0] + colon + [0] + right
        off = left + [0] + [0] * len(colon) + [0] + right
        return [cols_to_frame(on, width, height, x0), cols_to_frame(off, width, height, x0)]
    h, m = _pair_3x5(hh, height), _pair_3x5(mm, height)
    return [h, h, m, m]


def local_now():
    try:
        return datetime.now(ZoneInfo(os.getenv("TZ") or "Europe/Rome"))
    except Exception:
        return datetime.now()


def clock_key():
    n = local_now()
    return (n.hour, n.minute)


def clock_key_seconds():
    n = local_now()
    return (n.hour, n.minute, n.second)


# ─── Grafi a nodi ────────────────────────────────────────────────────────────

GRAPH_SOURCE_TYPES = {"time", "random"}
GRAPH_EXTERNAL_TYPES = {"external"}
GRAPH_MEASURE_TYPES = {"width"}
GRAPH_DERIVED_TYPES = {"math"}
# "algo" è insieme un nodo di disegno e una sorgente di valori (uscite 1 e 2: link con from_port "1"/"2")
GRAPH_VALUE_TYPES = GRAPH_SOURCE_TYPES | GRAPH_EXTERNAL_TYPES | GRAPH_MEASURE_TYPES | GRAPH_DERIVED_TYPES | {"algo"}
GRAPH_CALC_TYPES = GRAPH_MEASURE_TYPES | GRAPH_DERIVED_TYPES | {"algo"}
GRAPH_ALGO_BIND_PORTS = {"in1", "in2"}
GRAPH_MATH_OPS = {"add", "sub", "mul", "div"}
GRAPH_DRAW_TYPES = {"pixel", "rect", "text", "clock", "gradient", "bitmap", "circle", "subgraph", "animation", "algo", "viewport"}
GRAPH_MOD_TYPES = {"repeat", "combine", "color", "move", "rotate", "scale", "mirror", "blink", "trail", "viewport"}
GRAPH_OUT_TYPES = GRAPH_DRAW_TYPES.union(GRAPH_MOD_TYPES)
GRAPH_NODE_TYPES = GRAPH_OUT_TYPES.union(GRAPH_VALUE_TYPES)
GRAPH_TIME_MODES = {"linear", "pingpong", "sine"}
GRAPH_BIND_PORTS = {"x", "y", "w", "h", "scale", "r"}
GRAPH_REPEAT_BIND_PORTS = {"x", "y", "dx", "dy", "nx", "ny"}
GRAPH_MATH_BIND_PORTS = {"va", "vb"}
GRAPH_TEXT_BIND_PORTS = {"text"}
MAX_GRAPH_NODES = 100
GRAPH_CANVAS_W, GRAPH_CANVAS_H = 6000, 4000
GRAPH_FILE_FORMAT, GRAPH_BUNDLE_FORMAT, GRAPH_FILE_VERSION = "pixelanim-graph", "pixelanim-graphs", 1
MAX_GRAPH_IMPORT = 200
MAX_SUBGRAPH_DEPTH = 3
GRAPH_SUB_SIZES = {"8x8", "16x8", "32x8", "8x16", "16x16", "32x16"}
ANIM_TTL = 60
MAX_ANIM_FRAMES, MAX_ANIM_DIM = 1000, 64
# Nodo algoritmico (script Python)
ALGO_MAX_SCRIPT = 20000                                   # caratteri
ALGO_MAX_FRAMES = 600                                     # frame per ciclo
ALGO_TIME_BUDGET = float(os.getenv("ALGO_TIME_BUDGET", "4"))   # secondi di CPU per nodo
ALGO_MEM_MB = int(os.getenv("ALGO_MEM_MB", "256"))             # memoria extra consentita allo script


def _pi(p, key, lo, hi, default):
    try:
        v = int(p.get(key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _pf(p, key, default):
    try:
        return float(p.get(key, default))
    except (TypeError, ValueError):
        return default


def _eval_time(p, t_sec):
    period = max(1, _pi(p, "period", 1, 3600, 4))
    phase = float(p.get("phase", 0) or 0) % 1.0
    v01 = ((t_sec / period) + phase) % 1.0
    mode = p.get("mode", "linear")
    if mode == "pingpong":
        v01 = v01 * 2 if v01 < 0.5 else 2 - v01 * 2
    elif mode == "sine":
        v01 = 0.5 - 0.5 * math.cos(2 * math.pi * v01)
    lo, hi = _pi(p, "min", -256, 256, 0), _pi(p, "max", -256, 256, 31)
    q = _pi(p, "quantize", 0, 256, 1)
    v = lo + (hi - lo) * v01
    if q > 0:
        v = round(v / q) * q
    return int(round(v))


def _eval_random(p, k):
    seed, step = _pi(p, "seed", 0, 99999, 1), max(1, _pi(p, "step", 1, 256, 1))
    step_idx = k // step
    lo, hi = _pi(p, "min", -256, 256, 0), _pi(p, "max", -256, 256, 31)
    q = _pi(p, "quantize", 0, 256, 1)
    if hi < lo:
        lo, hi = hi, lo
    h = 2166136261
    for b in f"{int(seed)}:{int(step_idx)}".encode("utf-8"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    v = lo + (h % (hi - lo + 1))
    if q > 0:
        v = round(v / q) * q
    return max(lo, min(hi, v))


def _eval_value(node, k, fps):
    p = node.get("params") or {}
    return _eval_random(p, k) if node.get("type") == "random" else _eval_time(p, k / max(1, fps))


def _node_active_at(node, t_sec):
    p = node.get("params") or {}
    rf, rt = _pf(p, "range_from", 0), _pf(p, "range_to", 0)
    if rf > 0 and t_sec < rf:
        return False
    if rt > 0 and t_sec > rt:
        return False
    return True


def _resolve(p, key, time_values, binds_for_node, lo, hi, default):
    src = binds_for_node.get(key)
    if src:
        v = time_values.get(src, default)
        return max(lo, min(hi, int(math.floor(v + 0.5))))
    return _pi(p, key, lo, hi, default)


def graph_layers(graph):
    nodes = {n["id"]: n for n in graph.get("nodes", [])
             if isinstance(n, dict) and isinstance(n.get("id"), str)}
    order, seen = [], set()
    for link in graph.get("links", []):
        if not isinstance(link, dict) or link.get("to") != "out":
            continue
        frm = link.get("from")
        if frm in seen:
            continue
        n = nodes.get(frm)
        if not n or n.get("type") not in GRAPH_OUT_TYPES:
            continue
        order.append({"kind": "node", "node": n})
        seen.add(frm)
    return order


def graph_time_binds(graph):
    types = {n["id"]: n.get("type") for n in graph.get("nodes", [])
             if isinstance(n, dict) and isinstance(n.get("id"), str)}
    value_ids = {i for i, t in types.items() if t in GRAPH_VALUE_TYPES}
    binds = {}
    for link in graph.get("links", []):
        if not isinstance(link, dict):
            continue
        to, frm = link.get("to"), link.get("from")
        if not to or to == "out" or frm not in value_ids:
            continue
        port = link.get("to_port", "x")
        ok = (port in GRAPH_BIND_PORTS or port in GRAPH_REPEAT_BIND_PORTS or port in GRAPH_MATH_BIND_PORTS
              or (port in GRAPH_ALGO_BIND_PORTS and types.get(to) == "algo")
              or (port in GRAPH_TEXT_BIND_PORTS and types.get(to) == "text"))
        if not ok:
            continue
        if types[frm] == "algo":            # sorgente a due uscite: la chiave è "<id>.<1|2>"
            fp = str(link.get("from_port", ""))
            if fp not in ("1", "2"):
                continue
            frm = f"{frm}.{fp}"
        binds.setdefault(to, {})[port] = frm
    return binds


def _src_node(src):
    """Id del nodo da cui arriva un valore ("n5.2" -> "n5")."""
    return str(src).split(".", 1)[0]


def _sub_size(p):
    w, h = str(p.get("size", "8x8")).split("x")
    return int(w), int(h)


def _load_subgraph(p, stack=()):
    gid = _pi(p, "graph_id", 0, 10**9, 0)
    with device_db() as db:
        row = db.execute("SELECT id,data FROM graphs WHERE id=?", (gid,)).fetchone() if gid else None
        if row is None and p.get("graph_name"):
            row = db.execute("SELECT id,data FROM graphs WHERE name=? COLLATE NOCASE",
                             (str(p["graph_name"]),)).fetchone()
    if row is None or row["id"] in stack:
        return None
    try:
        g = json.loads(row["data"])
    except ValueError:
        return None
    return (row["id"], g) if isinstance(g, dict) else None


def graph_time_nodes(graph):
    return {n["id"]: n for n in graph.get("nodes", [])
            if isinstance(n, dict) and n.get("type") in GRAPH_SOURCE_TYPES
            and isinstance(n.get("id"), str)}


def graph_calc_nodes(graph):
    return {n["id"]: n for n in graph.get("nodes", [])
            if isinstance(n, dict) and n.get("type") in GRAPH_CALC_TYPES
            and isinstance(n.get("id"), str)}


GRAPH_INPUT_KEYS = ("repeat", "combine_a", "combine_b", "color", "move", "rotate",
                    "scale", "mirror", "blink", "trail", "width", "viewport")


def graph_inputs(nodes, links):
    inputs = {k: {} for k in GRAPH_INPUT_KEYS}
    for link in links:
        if not isinstance(link, dict):
            continue
        to, frm, port = link.get("to"), link.get("from"), link.get("to_port")
        if not to or to == "out":
            continue
        dst = nodes.get(to)
        if not dst:
            continue
        t = dst.get("type")
        if t == "combine":
            if port == "a":
                inputs["combine_a"][to] = frm
            elif port == "b":
                inputs["combine_b"][to] = frm
        elif port == "in" and t in inputs:
            inputs[t][to] = frm
    return inputs


def _children(node, inputs):
    t, nid = node.get("type"), node.get("id")
    if t == "combine":
        return [inputs["combine_a"].get(nid), inputs["combine_b"].get(nid)]
    if t in inputs:
        return [inputs[t].get(nid)]
    return []


def _value_order(nodes, inputs, binds, calc):
    def deps(cid):
        out, seen = {_src_node(s) for s in binds.get(cid, {}).values() if _src_node(s) in calc}, set()
        stack = [inputs["width"].get(cid)] if nodes[cid].get("type") == "width" else []
        while stack:
            nid = stack.pop()
            if not nid or nid in seen or nid not in nodes:
                continue
            seen.add(nid)
            if nid in calc and nid != cid and nodes[nid].get("type") == "algo":
                out.add(nid)                 # un algo misurato da un nodo Larghezza va eseguito prima
            out.update(_src_node(s) for s in binds.get(nid, {}).values() if _src_node(s) in calc)
            stack.extend(_children(nodes[nid], inputs))
        return out

    order, state = [], {}

    def visit(cid):
        if state.get(cid):
            return
        state[cid] = 1
        for d in deps(cid):
            visit(d)
        state[cid] = 2
        order.append(cid)

    for cid in calc:
        visit(cid)
    return order


def _clamp_pos(d, key, hi, default):
    try:
        v = int(round(float(d.get(key, default))))
    except (TypeError, ValueError, OverflowError):
        v = default
    d[key] = max(0, min(hi, v))


def _check_acyclic(links):
    edges = {}
    for l in links:
        frm, to = l.get("from"), l.get("to")
        if to and to != "out":
            edges.setdefault(frm, []).append(to)
    state = {}

    def cyclic(n):
        s = state.get(n)
        if s is not None:
            return s == 1
        state[n] = 1
        if any(cyclic(m) for m in edges.get(n, ())):
            return True
        state[n] = 2
        return False

    if any(cyclic(n) for n in list(edges)):
        raise HTTPException(400, "Il grafo contiene un anello: un nodo non può dipendere da se stesso")


def check_graph(graph):
    nodes, links = graph.get("nodes"), graph.get("links", [])
    if not isinstance(nodes, list) or not isinstance(links, list) \
            or len(nodes) > MAX_GRAPH_NODES or len(links) > 10 * MAX_GRAPH_NODES:
        raise HTTPException(400, "Grafo non valido o troppo grande")
    ids = {}
    for n in nodes:
        if not isinstance(n, dict) or n.get("type") not in GRAPH_NODE_TYPES \
                or not isinstance(n.get("id"), str) or not isinstance(n.get("params", {}), dict):
            raise HTTPException(400, "Nodo non valido")
        if n["id"] in ids:
            raise HTTPException(400, f"Id nodo duplicato: {n['id'][:40]}")
        ids[n["id"]] = n
        _clamp_pos(n, "x", GRAPH_CANVAS_W - 230, 30)
        _clamp_pos(n, "y", GRAPH_CANVAS_H - 200, 30)
        name = n.get("name")
        if isinstance(name, str) and name.strip():
            n["name"] = name.strip()[:40]
        else:
            n.pop("name", None)
        if n.get("collapsed") is not True:
            n.pop("collapsed", None)
        if n["type"] == "time":
            n.setdefault("params", {})["mode"] = (n["params"].get("mode", "linear")
                                                  if n["params"].get("mode") in GRAPH_TIME_MODES else "linear")
        if n["type"] == "width":
            n.setdefault("params", {})["misura"] = "box" if n["params"].get("misura") == "box" else "ink"
        if n["type"] == "subgraph":
            sp = n.setdefault("params", {})
            sp["graph_id"] = max(0, _pi(sp, "graph_id", 0, 10**9, 0))
            sp["graph_name"] = str(sp.get("graph_name") or "")[:60]
            sp["size"] = sp.get("size") if sp.get("size") in GRAPH_SUB_SIZES else "8x8"
            sp["modo"] = "mono" if sp.get("modo") == "mono" else "color"
        if n["type"] == "animation":
            ap = n.setdefault("params", {})
            ap["anim_id"] = max(0, _pi(ap, "anim_id", 0, 10**9, 0))
            ap["anim_name"] = str(ap.get("anim_name") or "")[:60]
            ap["modo"] = "mono" if ap.get("modo") == "mono" else "color"
            ap["hold"] = _pi(ap, "hold", 1, 60, 1)
        if n["type"] == "algo":
            gp = n.setdefault("params", {})
            gp["w"] = _pi(gp, "w", 1, MAX_ANIM_DIM, 8)
            gp["h"] = _pi(gp, "h", 1, MAX_ANIM_DIM, 8)
            gp["frames"] = _pi(gp, "frames", 1, ALGO_MAX_FRAMES, 32)
            gp["seed"] = _pi(gp, "seed", 0, 99999, 1)
            gp["modo"] = "mono" if gp.get("modo") == "mono" else "color"
            gp["keep"] = bool(gp.get("keep"))
            for kk in ("in1", "in2"):
                gp[kk] = _algo_num(_pf(gp, kk, 0.0))
            if not isinstance(gp.get("script", ""), str):
                gp["script"] = ""
            if len(gp.get("script", "")) > ALGO_MAX_SCRIPT:
                raise HTTPException(400, f"Script del nodo troppo lungo (max {ALGO_MAX_SCRIPT} caratteri)")
        if n["type"] == "bitmap":
            bp = n.setdefault("params", {})
            bw = _pi(bp, "w", 1, 128, 8)
            bh = _pi(bp, "h", 1, 128, 8)
            raw_frames = bp.get("frames")
            if not isinstance(raw_frames, list) or not raw_frames:
                # Migrazione delle vecchie bitmap 8x8 (un bit per pixel).
                old = str(bp.get("bits", ""))
                if len(old) >= 16:
                    old = (old + "0" * 16)[:16]
                    f = []
                    for yy in range(8):
                        try:
                            byte = int(old[yy * 2:yy * 2 + 2], 16)
                        except ValueError:
                            byte = 0
                        f.extend(format(_pi(bp, "color", 0, 15, 1), "X") if byte & (1 << (7 - xx)) else "0" for xx in range(8))
                    raw_frames = ["".join(f)]
                    bw, bh = 8, 8
                else:
                    raw_frames = ["0" * (bw * bh)]
            clean = []
            for frame in raw_frames[:1000]:
                src = str(frame or "").upper()
                src = "".join(ch for ch in src if ch in "0123456789ABCDEF")
                clean.append((src + "0" * (bw * bh))[:bw * bh])
            bp["w"], bp["h"], bp["frames"] = bw, bh, clean or ["0" * (bw * bh)]
            raw_durations = bp.get("frame_durations") if isinstance(bp.get("frame_durations"), list) else []
            bp["frame_durations"] = [max(20, min(10000, _pi({"v": raw_durations[i] if i < len(raw_durations) else 100}, "v", 20, 10000, 100))) for i in range(len(bp["frames"]))]
            bp.pop("bits", None)
            bp["color"] = _pi(bp, "color", 0, 15, 1)
        if n["type"] == "external":
            ep = n.setdefault("params", {})
            key = str(ep.get("key") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", key):
                raise HTTPException(400, "Chiave Input esterno non valida (usa lettere, numeri, _, ., : o -)")
            ep["key"] = key
            ep["default"] = _algo_num(_pf(ep, "default", 0.0))
            ep["timeout"] = _pi(ep, "timeout", 0, 86400, 30)
        if n["type"] == "math":
            n.setdefault("params", {})["op"] = n["params"].get("op") if n["params"].get("op") in GRAPH_MATH_OPS else "add"
    for l in links:
        if not isinstance(l, dict):
            raise HTTPException(400, "Link non valido")
        frm, to = l.get("from"), l.get("to")
        if frm not in ids:
            raise HTTPException(400, "Link da un nodo inesistente")
        if to is None or to == "out":
            if ids[frm]["type"] not in GRAPH_OUT_TYPES:
                raise HTTPException(400, f"Il nodo {ids[frm]['type']} non si può collegare all'uscita")
            continue
        if to not in ids:
            raise HTTPException(400, "Link verso un nodo inesistente")
        frm_node, to_node = ids[frm], ids[to]
        port = l.get("to_port", "x")
        if port in ("in", "a", "b"):
            if to_node["type"] not in GRAPH_MOD_TYPES | GRAPH_MEASURE_TYPES:
                raise HTTPException(400, f"Il nodo {to_node['type']} non accetta input da disegno")
            if frm_node["type"] not in GRAPH_OUT_TYPES:
                raise HTTPException(400, "L'ingresso accetta solo nodi di disegno o modificatori")
            if to_node["type"] == "combine" and port not in ("a", "b"):
                raise HTTPException(400, "Il nodo Combina accetta solo le porte 'a' e 'b'")
            if to_node["type"] == "viewport" and frm_node["type"] not in ("subgraph", "animation", "bitmap"):
                raise HTTPException(400, "Il Viewport accetta come sorgente un nodo Grafo o Animazione")
            if to_node["type"] != "combine" and port != "in":
                raise HTTPException(400, f"Il nodo {to_node['type']} accetta solo la porta 'in'")
            continue
        if frm_node["type"] not in GRAPH_VALUE_TYPES:
            raise HTTPException(400, "Solo un nodo Tempo, Casuale, Input esterno, Larghezza, Matematica o Algoritmico può essere sorgente di un binding")
        if frm_node["type"] == "algo" and str(l.get("from_port")) not in ("1", "2"):
            raise HTTPException(400, "Un nodo Algoritmico si collega a una porta blu solo tramite le sue uscite 1 e 2")
        if to_node["type"] == "repeat":
            if port not in GRAPH_REPEAT_BIND_PORTS:
                raise HTTPException(400, f"Porta '{port}' non valida per Ripetizioni")
        elif to_node["type"] in GRAPH_DRAW_TYPES:
            if port not in GRAPH_BIND_PORTS and not (to_node["type"] == "algo" and port in GRAPH_ALGO_BIND_PORTS) and not (to_node["type"] == "text" and port in GRAPH_TEXT_BIND_PORTS):
                raise HTTPException(400, f"Porta '{port}' non valida")
        elif to_node["type"] in GRAPH_DERIVED_TYPES:
            if port not in GRAPH_MATH_BIND_PORTS:
                raise HTTPException(400, f"Porta '{port}' non valida per {to_node['type']}")
        else:
            raise HTTPException(400, f"Il nodo {to_node['type']} non accetta binding")
    out = graph.get("out")
    if isinstance(out, dict):
        _clamp_pos(out, "x", GRAPH_CANVAS_W - 200, 620)
        _clamp_pos(out, "y", GRAPH_CANVAS_H - 200, 30)
    elif out is not None:
        graph["out"] = {"x": 620, "y": 30}
    _check_acyclic(links)
    if len(json.dumps(graph)) > 200_000:
        raise HTTPException(413, "Grafo troppo grande")


def _digit3_cols(d):
    return [sum(1 << r for r in range(5) if DIGITS_3X5[d][r][c] == "1") for c in range(3)]


def _clock_cols(p, k, fps, now, width=32):
    part = p.get("part", "HH:MM")
    hh, mm, ss = f"{now.hour:02d}", f"{now.minute:02d}", f"{now.second:02d}"
    small = p.get("font") == "3x5" or part == "alterna" or part == "HH:MM:SS"
    dig = _digit3_cols if small else _digit_cols
    colon = [10] if small else list(FONT_5X7[":"])
    blink = p.get("blink", True)

    def pair(s):
        return dig(s[0]) + [0] + dig(s[1])

    def colon_k():
        if not blink:
            return colon
        return colon if (k % fps) * 2 < fps else [0] * len(colon)

    def colon_sec():
        if not blink:
            return colon
        return colon if now.second % 2 == 0 else [0] * len(colon)

    if part == "HH":
        return pair(hh)
    if part == "MM":
        return pair(mm)
    if part == "SS":
        return pair(ss)
    if part == "alterna":
        return pair(hh if (k // (2 * fps)) % 2 == 0 else mm)
    if part == "HH:MM:SS":
        if width >= 32:
            c = colon_sec()
            return pair(hh) + [0] + c + [0] + pair(mm) + [0] + c + [0] + pair(ss)
        phase = (now.second // 2) % 3
        if phase == 0:
            return pair(hh)
        if phase == 1:
            return pair(mm)
        return pair(ss)
    return pair(hh) + [0] + colon_k() + [0] + pair(mm)


def _anchor_x(ncols, width, x, align):
    if align == "center":
        return (width - ncols) // 2 + x
    if align == "right":
        return width - ncols - x
    return x


def _draw_cols(canvas, cols, x0, y0, color, width, height):
    for i in range(max(0, -x0), min(len(cols), width - x0)):
        bits = cols[i]
        if not bits:
            continue
        for r in range(7):
            if (bits >> r) & 1 and 0 <= y0 + r < height:
                canvas[y0 + r][x0 + i] = color


def _draw_gradient(canvas, x, y, w, h, direction, ca, cb, width, height):
    if w <= 0 or h <= 0:
        return
    ca = max(0, min(15, ca))
    cb = max(0, min(15, cb))
    for yy in range(max(0, y), min(height, y + h)):
        for xx in range(max(0, x), min(width, x + w)):
            t = (yy - y) / max(1, h - 1) if direction == "v" else (xx - x) / max(1, w - 1)
            canvas[yy][xx] = int(round(ca + (cb - ca) * t))


def node_period(node, width, fps, time_nodes, binds_for_node, loops=None):
    p = node.get("params") or {}
    t = node["type"]
    periods = [1]
    if loops and node.get("id") in loops:
        periods.append(max(1, loops[node["id"]]))
    if t == "text" and p.get("mode") == "scroll":
        text_len = 120 if binds_for_node.get("text") else len(text_columns(str(p.get("text", ""))))
        periods.append(text_len + width)
    elif t == "clock":
        part, blink = p.get("part", "HH:MM"), p.get("blink", True)
        if part == "alterna":
            periods.append(4 * fps)
        elif part == "HH:MM:SS":
            periods.append(1)
        elif blink and part == "HH:MM":
            periods.append(fps)
    elif t == "random":
        step = max(1, _pi(p, "step", 1, 256, 1))
        periods.append(max(16, step * fps * 4))
    elif t == "blink":
        on, off = _pi(p, "on", 0, 99, 1), _pi(p, "off", 0, 99, 1)
        if on + off > 0:
            periods.append(on + off)
    elif t == "move":
        modo = p.get("modo", "ciclo")
        px = _pi(p, "px", 0, 256, 0) or max(1, width - 1)
        py = _pi(p, "py", 0, 256, 0) or max(1, 8 - 1)
        if modo == "rimbalzo":
            periods.append(max(2, 2 * px, 2 * py))
        else:
            periods.append(max(1, px, py))
    elif t == "rotate":
        if p.get("modo", "continuo") == "pendolo":
            vel = abs(_pf(p, "vel", 15))
            if vel > 0:
                periods.append(max(2, round(360 / vel)))
    elif t == "scale":
        if p.get("modo", "rimbalzo") == "rimbalzo":
            vel, rng = abs(_pf(p, "vel", 0.1)), _pf(p, "range", 1)
            if vel > 0:
                periods.append(max(2, round(2 * rng / vel)))
    elif t == "trail":
        n_count, step = _pi(p, "n", 0, 30, 3), _pi(p, "passo", 1, 30, 1)
        periods.append(max(2, n_count * step + 1))
    for src in binds_for_node.values():
        tnode = time_nodes.get(src)
        if tnode:
            tn_p = tnode.get("params") or {}
            if tnode.get("type") == "random":
                step = max(1, _pi(tn_p, "step", 1, 256, 1))
                periods.append(max(16, step * fps * 4))
            else:
                periods.append(max(1, _pi(tn_p, "period", 1, 3600, 4) * fps))
    cycle = 1
    for per in periods:
        cycle = math.lcm(cycle, per)
    return cycle


def _draw_node(canvas, node, k, width, height, fps, now, time_values, binds_for_node):
    p = node.get("params") or {}
    t = node["type"]
    color = _pi(p, "color", 0, 15, 1)
    x = _resolve(p, "x", time_values, binds_for_node, -256, 256, 0)
    y = _resolve(p, "y", time_values, binds_for_node, -256, 256, _default_y0(height))

    if t == "pixel":
        nx, ny = _pi(p, "nx", 1, 32, 1), _pi(p, "ny", 1, 32, 1)
        dx, dy = _pi(p, "dx", -256, 256, 1), _pi(p, "dy", -256, 256, 1)
        for j in range(ny):
            for i in range(nx):
                px, py = x + i * dx, y + j * dy
                if 0 <= px < width and 0 <= py < height:
                    canvas[py][px] = color
    elif t == "rect":
        w = _resolve(p, "w", time_values, binds_for_node, 0, 256, 1)
        h = _resolve(p, "h", time_values, binds_for_node, 0, 256, 1)
        nx, ny = _pi(p, "nx", 1, 32, 1), _pi(p, "ny", 1, 32, 1)
        ddx, ddy = _pi(p, "dx", -256, 256, 4), _pi(p, "dy", -256, 256, 4)
        for j in range(ny):
            for i in range(nx):
                x0, y0 = x + i * ddx, y + j * ddy
                for yy in range(max(0, y0), min(height, y0 + h)):
                    for xx in range(max(0, x0), min(width, x0 + w)):
                        if p.get("fill") or yy in (y0, y0 + h - 1) or xx in (x0, x0 + w - 1):
                            canvas[yy][xx] = color
    elif t == "gradient":
        w = _resolve(p, "w", time_values, binds_for_node, 1, 256, 8)
        h = _resolve(p, "h", time_values, binds_for_node, 1, 256, 8)
        ca, cb = _pi(p, "color_a", 0, 15, 1), _pi(p, "color_b", 0, 15, 7)
        direction = p.get("direction", "h")
        nx, ny = _pi(p, "nx", 1, 32, 1), _pi(p, "ny", 1, 32, 1)
        ddx, ddy = _pi(p, "dx", -256, 256, 8), _pi(p, "dy", -256, 256, 8)
        for j in range(ny):
            for i in range(nx):
                _draw_gradient(canvas, x + i * ddx, y + j * ddy, w, h, direction, ca, cb, width, height)
    elif t == "bitmap":
        bw = _pi(p, "w", 1, 128, 8)
        bh = _pi(p, "h", 1, 128, 8)
        frames = p.get("frames") if isinstance(p.get("frames"), list) else []
        if not frames:
            # Compatibilità in lettura con grafi non ancora normalizzati.
            old = (str(p.get("bits", "")) + "0" * 16)[:16]
            frame = []
            for yy in range(8):
                try: byte = int(old[yy * 2:yy * 2 + 2], 16)
                except ValueError: byte = 0
                frame.extend(format(color, "X") if byte & (1 << (7 - xx)) else "0" for xx in range(8))
            frames = ["".join(frame)]
            bw = bh = 8
        frame = str(frames[_bitmap_frame_index(p, k, fps)] or "")
        sc = _resolve(p, "scale", time_values, binds_for_node, 1, 16, 1)
        for yy in range(bh):
            for xx in range(bw):
                pos = yy * bw + xx
                try: pixel_color = int(frame[pos], 16)
                except (ValueError, IndexError): pixel_color = 0
                if not pixel_color:
                    continue
                for dy in range(sc):
                    for dx in range(sc):
                        px, py = x + xx * sc + dx, y + yy * sc + dy
                        if 0 <= px < width and 0 <= py < height:
                            canvas[py][px] = pixel_color
    elif t == "circle":
        r = _resolve(p, "r", time_values, binds_for_node, 0, 256, 4)
        fill = p.get("fill", True)
        if r <= 0:
            if 0 <= x < width and 0 <= y < height:
                canvas[y][x] = color
        else:
            inner2, outer2 = (r - 0.5) ** 2, (r + 0.5) ** 2
            y0, y1 = max(0, int(y - r) - 1), min(height, int(y + r) + 2)
            x0, x1 = max(0, int(x - r) - 1), min(width, int(x + r) + 2)
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    dx, dy = xx - x, yy - y
                    d2 = dx * dx + dy * dy
                    if fill:
                        if d2 <= outer2:
                            canvas[yy][xx] = color
                    else:
                        if inner2 <= d2 <= outer2:
                            canvas[yy][xx] = color
    elif t == "text":
        text_src = binds_for_node.get("text")
        text_value = time_values.get(text_src, p.get("text", "")) if text_src else p.get("text", "")
        if isinstance(text_value, float):
            text_value = str(int(text_value)) if text_value.is_integer() else str(round(text_value, 2))
        cols = text_columns(str(text_value)[:120])
        if p.get("mode") == "scroll":
            x0 = width - (k % (len(cols) + width))
        else:
            x0 = _anchor_x(len(cols), width, x, p.get("align", "left"))
        _draw_cols(canvas, cols, x0, y, color, width, height)
    elif t == "clock":
        cols = _clock_cols(p, k, fps, now, width)
        _draw_cols(canvas, cols, _anchor_x(len(cols), width, x, p.get("align", "center")), y, color, width, height)


def _tri(x, period):
    if period <= 0:
        return 0
    x = x % (2 * period)
    return x if x <= period else 2 * period - x


def _drive(v0, vel, t, modo, rng):
    if modo == "continuo":
        return v0 + vel * t
    if modo == "rimbalzo":
        return v0 + _tri(vel * t, rng)
    if modo == "pendolo":
        return v0 + rng * math.sin(vel * t * math.pi / 180)
    return v0 + vel * t


def _cols_width(cols, mode):
    if mode == "box":
        return len(cols)
    lit = [i for i, c in enumerate(cols) if c]
    return lit[-1] - lit[0] + 1 if lit else 0


def _ink_width(matrix):
    xs = [x for row in matrix for x, v in enumerate(row) if v]
    return max(xs) - min(xs) + 1 if xs else 0


def measure_width(node_id, k, ctx, mode="ink"):
    n = ctx["nodes"].get(node_id) if node_id else None
    if n is None or node_id in ctx["mbusy"]:
        return 0
    t, p = n.get("type"), n.get("params") or {}
    if t in GRAPH_OUT_TYPES and not _node_active_at(n, k / max(1, ctx["fps"])):
        return 0
    if t in ("color", "blink"):
        ctx["mbusy"].add(node_id)
        try:
            return measure_width(ctx["inputs"][t].get(node_id), k, ctx, mode)
        finally:
            ctx["mbusy"].discard(node_id)
    if t == "text":
        return _cols_width(text_columns(str(p.get("text", ""))[:120]), mode)
    if t == "clock":
        return _cols_width(_clock_cols(p, k, ctx["fps"], ctx["now"], ctx["width"]), mode)
    if t == "animation":
        entry, frame = ctx["anims"].get(node_id), _anim_frame(n, k, ctx)
        return 0 if frame is None else (entry["w"] if mode == "box" else _ink_width(frame))
    if t == "algo":
        frame = ctx["algo_mat"].get(node_id)
        if not frame:
            return 0
        return _pi(p, "w", 1, MAX_ANIM_DIM, 8) if mode == "box" else _ink_width(frame)
    if t == "subgraph":
        frames = ctx["sub_frames"].get(node_id)
        if not frames:
            return 0
        return _sub_size(p)[0] if mode == "box" else _ink_width(frames[k % len(frames)])
    if t == "bitmap":
        bw = _pi(p, "w", 1, 128, 8)
        bh = _pi(p, "h", 1, 128, 8)
        frames = p.get("frames") if isinstance(p.get("frames"), list) else []
        frame = str(frames[_bitmap_frame_index(p, k, ctx["fps"])] ) if frames else ""
        sc = _resolve(p, "scale", ctx["time_values"], ctx["binds"].get(node_id, {}), 1, 16, 1)
        if mode == "box":
            return bw * sc
        lit = [x for y in range(bh) for x in range(bw)
               if y * bw + x < len(frame) and frame[y * bw + x] != "0"]
        return (max(lit) - min(lit) + 1) * sc if lit else 0
    return _ink_width(eval_node_matrix(node_id, k, ctx))


def _eval_width(node, k, ctx):
    p = node.get("params") or {}
    src = ctx["inputs"]["width"].get(node["id"])
    if not src:
        return 0
    mode = "box" if p.get("misura") == "box" else "ink"
    return max(-4096, min(4096, measure_width(src, k, ctx, mode) + _pi(p, "offset", -256, 256, 0)))


def _math_arg(p, key, ctx, node_id):
    src = ctx["binds"].get(node_id, {}).get(key)
    v = float(ctx["time_values"].get(src, 0)) if src else _pf(p, key, 0.0)
    return max(-1e6, min(1e6, v)) if math.isfinite(v) else 0.0


def _eval_math(node, ctx):
    p, nid = node.get("params") or {}, node["id"]
    a, b = _math_arg(p, "va", ctx, nid), _math_arg(p, "vb", ctx, nid)
    op = p.get("op")
    if op == "sub":
        r = a - b
    elif op == "mul":
        r = a * b
    elif op == "div":
        r = a / b if b else 0.0
    else:
        r = a + b
    r = round(max(-1e6, min(1e6, r)), 4)
    return int(r) if r == int(r) else r


def _bitmap_frame_index(params, k, fps):
    frames = params.get("frames") if isinstance(params.get("frames"), list) else []
    if not frames: return 0
    durations = params.get("frame_durations") if isinstance(params.get("frame_durations"), list) else []
    ds = [max(20, min(10000, int(durations[i]))) if i < len(durations) else 100 for i in range(len(frames))]
    total = sum(ds)
    elapsed = (max(0, int(k)) * 1000 / max(1, int(fps))) % total
    acc = 0
    for i, duration in enumerate(ds):
        acc += duration
        if elapsed < acc: return i
    return len(frames) - 1


def eval_node_matrix(node_id, k, ctx):
    if not node_id or node_id not in ctx["nodes"]:
        return [[0] * ctx["width"] for _ in range(ctx["height"])]
    cache_key = (node_id, k)
    if cache_key in ctx["cache"]:
        return ctx["cache"][cache_key]
    if node_id in ctx["busy"]:
        return [[0] * ctx["width"] for _ in range(ctx["height"])]
    ctx["busy"].add(node_id)
    try:
        return _eval_node_matrix(node_id, k, ctx)
    finally:
        ctx["busy"].discard(node_id)


def _blit(matrix, frame, n, ctx):
    p, rb = n.get("params") or {}, ctx["binds"].get(n["id"], {})
    x = _resolve(p, "x", ctx["time_values"], rb, -256, 256, 0)
    y = _resolve(p, "y", ctx["time_values"], rb, -256, 256, 0)
    mono, col = p.get("modo") == "mono", _pi(p, "color", 0, 15, 1)
    width, height = ctx["width"], ctx["height"]
    for sy, row in enumerate(frame):
        py = y + sy
        if not 0 <= py < height:
            continue
        for sx, v in enumerate(row):
            px = x + sx
            if v and 0 <= px < width:
                matrix[py][px] = col if mono else v


def _draw_subgraph(matrix, n, k, ctx):
    frames = ctx["sub_frames"].get(n["id"])
    if frames:
        _blit(matrix, frames[k % len(frames)], n, ctx)


def _draw_algo(matrix, n, k, ctx):
    frame = ctx["algo_mat"].get(n["id"])
    if frame:
        _blit(matrix, frame, n, ctx)


def _eval_algo_frame(cn, k, t_sec, ctx):
    """Esegue (o legge dal precalcolo) il frame k del nodo algo e pubblica le uscite 1 e 2."""
    nid, tv = cn["id"], ctx["time_values"]
    st = ctx["algo"].get(nid)
    tv[f"{nid}.1"] = tv[f"{nid}.2"] = 0
    ctx["algo_mat"][nid] = None
    if not st or st.get("dead") or not _node_active_at(cn, t_sec):
        return
    j = k % st["n"]
    if st.get("pre") is not None:
        frames, outs = st["pre"]
        ctx["algo_mat"][nid] = frames[j]
        tv[f"{nid}.1"], tv[f"{nid}.2"] = outs[j]
        return
    p, b = cn.get("params") or {}, ctx["binds"].get(nid, {})
    ins = [float(tv.get(b[key], 0)) if key in b else _pf(p, key, 0.0) for key in ("in1", "in2")]
    try:
        mat, o1, o2 = st["runner"].step(j, *ins)
    except AlgoError as e:
        st["dead"] = True
        label = cn.get("name") or f"Algoritmico {nid}"
        ctx["warn"].append({"node": nid, "err": str(e), "msg": f"Nodo «{label}»: {e}"})
        return
    ctx["algo_mat"][nid] = mat
    tv[f"{nid}.1"], tv[f"{nid}.2"] = o1, o2


def _anim_frame(n, k, ctx):
    entry = ctx["anims"].get(n["id"])
    if not entry:
        return None
    hold = _pi(n.get("params") or {}, "hold", 1, 60, 1)
    return entry["frames"][(k // hold) % len(entry["frames"])]


def _draw_animation(matrix, n, k, ctx):
    frame = _anim_frame(n, k, ctx)
    if frame:
        _blit(matrix, frame, n, ctx)


# ─── Nodo algoritmico: script Python eseguito in sandbox ─────────────────────
# Lo script gira una volta per frame e disegna con pixel(), rect(), line()…
# NON è un confine di sicurezza a prova di attaccante (pensato per uso personale/LAN).
# Difese: AST filtrato (niente import, try, class, attributi "_x"), builtins ridotti,
# timeout di CPU (SIGALRM) e tetto di memoria (RLIMIT_AS) per ogni nodo.

class AlgoError(Exception):
    pass


class _AlgoTimeout(BaseException):
    pass


_ALGO_BAD_NODES = {
    ast.Import: "import non consentito (math e le funzioni di disegno sono già disponibili)",
    ast.ImportFrom: "import non consentito (math e le funzioni di disegno sono già disponibili)",
    ast.ClassDef: "class non consentito",
    ast.AsyncFunctionDef: "async non consentito", ast.AsyncFor: "async non consentito",
    ast.AsyncWith: "async non consentito", ast.Await: "await non consentito",
    ast.Try: "try/except non consentito", ast.With: "with non consentito",
    ast.Yield: "yield non consentito", ast.YieldFrom: "yield non consentito",
}
if hasattr(ast, "TryStar"):
    _ALGO_BAD_NODES[ast.TryStar] = "try/except non consentito"
_ALGO_BAD_NAMES = {"eval", "exec", "compile", "open", "input", "getattr", "setattr", "delattr",
                   "hasattr", "globals", "locals", "vars", "dir", "type", "object", "super",
                   "breakpoint", "help", "memoryview", "exit", "quit"}
_ALGO_BAD_ATTRS = {"format", "format_map", "mro"}
_ALGO_BAD_ATTR_PREFIX = ("_", "gi_", "cr_", "ag_", "f_", "tb_", "co_")

import builtins as _builtins
_ALGO_BUILTINS = {n: getattr(_builtins, n) for n in (
    "abs", "all", "any", "bool", "chr", "divmod", "enumerate", "filter", "float", "int",
    "isinstance", "len", "list", "dict", "map", "max", "min", "ord", "pow", "range",
    "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip")}


def _algo_compile(script):
    try:
        tree = ast.parse(script, "<algo>", "exec")
    except SyntaxError as e:
        raise AlgoError(f"riga {e.lineno}: errore di sintassi ({e.msg})")
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", "?")
        why = _ALGO_BAD_NODES.get(type(node))
        if why:
            raise AlgoError(f"riga {ln}: {why}")
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in _ALGO_BAD_NAMES):
            raise AlgoError(f"riga {ln}: il nome «{node.id}» non è consentito")
        if isinstance(node, ast.Attribute) and (node.attr in _ALGO_BAD_ATTRS
                                                or node.attr.startswith(_ALGO_BAD_ATTR_PREFIX)):
            raise AlgoError(f"riga {ln}: l'attributo «.{node.attr}» non è consentito")
    return compile(tree, "<algo>", "exec")


def _algo_alarm(signum, frame):
    raise _AlgoTimeout()


def _vm_bytes():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return None


def _algo_num(v):
    """Numero finito e limitato (ingressi/uscite dello script); intero se possibile."""
    try:
        v = float(v)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(v):
        return 0
    v = max(-1e6, min(1e6, v))
    return int(v) if v == int(v) else v


class _AlgoLimits:
    """Context manager: timeout di CPU + tetto di memoria mentre gira lo script."""

    def __init__(self, budget):
        self.budget = budget

    def __enter__(self):
        self._mem = None
        self._alarm = False
        try:
            import resource
            vm = _vm_bytes()
            if vm and ALGO_MEM_MB > 0:
                soft, hard = resource.getrlimit(resource.RLIMIT_AS)
                limit = vm + ALGO_MEM_MB * 1024 * 1024
                if hard == resource.RLIM_INFINITY or limit <= hard:
                    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
                    self._mem = (resource, soft, hard)
        except Exception:
            self._mem = None
        if (self.budget > 0 and hasattr(signal, "setitimer")
                and threading.current_thread() is threading.main_thread()):
            self._old = signal.signal(signal.SIGALRM, _algo_alarm)
            signal.setitimer(signal.ITIMER_REAL, self.budget)
            self._alarm = True
        return self

    def __exit__(self, *exc):
        if self._alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._old)
        if self._mem:
            resource, soft, hard = self._mem
            try:
                resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
            except Exception:
                pass
        return False


def _algo_line(exc):
    ln, tb = None, exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<algo>":
            ln = tb.tb_lineno
        tb = tb.tb_next
    return ln


def _algo_exc_text(exc):
    ln = _algo_line(exc)
    if isinstance(exc, _AlgoTimeout):
        msg = f"tempo massimo superato ({ALGO_TIME_BUDGET:g} s): script troppo lento o ciclo infinito"
    elif isinstance(exc, MemoryError):
        msg = "memoria insufficiente"
    elif isinstance(exc, RecursionError):
        msg = "ricorsione troppo profonda"
    elif isinstance(exc, NameError):
        msg = f"nome non definito: {getattr(exc, 'name', None) or exc}"
    elif isinstance(exc, ZeroDivisionError):
        msg = "divisione per zero"
    else:
        msg = f"{type(exc).__name__}: {exc}"
    return f"riga {ln}: {msg}" if ln else msg


class AlgoRunner:
    """Esegue lo script di un nodo Algoritmico un frame alla volta.
    Ingressi dello script: in1, in2. Uscite: out1, out2 (variabili lette dopo ogni frame).
    Il tempo di CPU è un budget cumulativo per nodo (ALGO_TIME_BUDGET)."""

    def __init__(self, params, fps):
        script = str(params.get("script") or "")
        self.W = W = _pi(params, "w", 1, MAX_ANIM_DIM, 8)
        self.H = H = _pi(params, "h", 1, MAX_ANIM_DIM, 8)
        self.N = N = _pi(params, "frames", 1, ALGO_MAX_FRAMES, 32)
        self.seed = seed = _pi(params, "seed", 0, 99999, 1)
        self.keep = bool(params.get("keep"))
        default_c = _pi(params, "color", 0, 15, 1)
        self.empty = not script.strip()
        self.code = None if self.empty else _algo_compile(script)   # AlgoError se non valido
        self.spent = 0.0
        self.cv = cv = [[0] * W for _ in range(H)]
        self.rng = rng = random.Random(seed)

        def _col(c):
            if c is None:
                return default_c
            try:
                return max(0, min(15, int(c)))
            except (TypeError, ValueError, OverflowError):
                return default_c

        def _ri(v):
            return int(math.floor(v + 0.5))

        def clear():
            for row in cv:
                row[:] = [0] * W
        self._clear = clear

        def pixel(x, y, c=None):
            try:
                xi, yi = _ri(x), _ri(y)
            except (TypeError, ValueError, OverflowError):
                return
            if 0 <= xi < W and 0 <= yi < H:
                cv[yi][xi] = _col(c)

        def get(x, y):
            xi, yi = _ri(x), _ri(y)
            return cv[yi][xi] if 0 <= xi < W and 0 <= yi < H else 0

        def rect(x, y, w, h, c=None, fill=True):
            x, y, w, h = _ri(x), _ri(y), _ri(w), _ri(h)
            if w <= 0 or h <= 0:
                return
            col = _col(c)
            for yy in range(max(0, y), min(H, y + h)):
                for xx in range(max(0, x), min(W, x + w)):
                    if fill or yy in (y, y + h - 1) or xx in (x, x + w - 1):
                        cv[yy][xx] = col

        def line(x0, y0, x1, y1, c=None):
            x0, y0, x1, y1 = _ri(x0), _ri(y0), _ri(x1), _ri(y1)
            col = _col(c)
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
            err = dx - dy
            for _ in range(min(dx + dy + 1, 20000)):
                if 0 <= x0 < W and 0 <= y0 < H:
                    cv[y0][x0] = col
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x0 += sx
                if e2 < dx:
                    err += dx
                    y0 += sy

        def circle(cx, cy, r, c=None, fill=True):
            r = float(r)
            if r < 0:
                return
            col = _col(c)
            inner2, outer2 = (r - 0.5) ** 2, (r + 0.5) ** 2
            for yy in range(max(0, int(cy - r) - 1), min(H, int(cy + r) + 2)):
                for xx in range(max(0, int(cx - r) - 1), min(W, int(cx + r) + 2)):
                    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
                    if d2 <= outer2 and (fill or d2 >= inner2):
                        cv[yy][xx] = col

        self.ns = {"__builtins__": _ALGO_BUILTINS,
                   "W": W, "H": H, "N": N, "fps": fps, "seed": seed,
                   "in1": 0, "in2": 0, "out1": 0, "out2": 0,
                   "pixel": pixel, "clear": clear, "get": get, "rect": rect, "line": line, "circle": circle,
                   "rand": rng.random, "randint": rng.randint, "choice": rng.choice,
                   "math": math, "pi": math.pi, "sin": math.sin, "cos": math.cos, "tan": math.tan,
                   "atan2": math.atan2, "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
                   "hypot": math.hypot, "radians": math.radians, "degrees": math.degrees,
                   "exp": math.exp, "log": math.log}
        self.fps = fps

    def step(self, k, in1=0, in2=0):
        """Un frame. k = indice nel ciclo (0..N-1). Ritorna (matrice, out1, out2); solleva AlgoError."""
        ns = self.ns
        if self.empty:
            return [row[:] for row in self.cv], 0, 0
        if k == 0:                       # inizio ciclo: stessi casuali e tela pulita, come al primo giro
            self.rng.seed(self.seed)
            if self.keep:
                self._clear()
        ns["k"], ns["t"] = k, k / max(1, self.fps)
        ns["in1"], ns["in2"] = _algo_num(in1), _algo_num(in2)
        if not self.keep:
            self._clear()
        left = ALGO_TIME_BUDGET - self.spent
        t0 = time.perf_counter()
        try:
            if left <= 0:
                raise _AlgoTimeout()
            with _AlgoLimits(left):
                exec(self.code, ns)
        except (_AlgoTimeout, Exception) as exc:
            raise AlgoError(_algo_exc_text(exc))
        finally:
            self.spent += time.perf_counter() - t0
        return [row[:] for row in self.cv], _algo_num(ns.get("out1", 0)), _algo_num(ns.get("out2", 0))


def run_algo(params, fps, in1=0, in2=0):
    """Esegue tutto il ciclo (N frame) con ingressi costanti.
    Ritorna (frames, outs, errore); outs = [(out1, out2), ...]. In caso di errore o script vuoto: ([], [], err)."""
    try:
        runner = AlgoRunner(params, fps)
    except AlgoError as e:
        return [], [], str(e)
    if runner.empty:
        return [], [], None
    frames, outs = [], []
    try:
        for k in range(runner.N):
            m, o1, o2 = runner.step(k, in1, in2)
            frames.append(m)
            outs.append((o1, o2))
    except AlgoError as e:
        return [], [], str(e)
    return frames, outs, None


def _eval_node_matrix(node_id, k, ctx):
    if not node_id or node_id not in ctx["nodes"]:
        return [[0] * ctx["width"] for _ in range(ctx["height"])]
    cache_key = (node_id, k)
    if cache_key in ctx["cache"]:
        return ctx["cache"][cache_key]

    n = ctx["nodes"][node_id]
    t = n.get("type")
    p = n.get("params") or {}
    width, height = ctx["width"], ctx["height"]
    matrix = [[0] * width for _ in range(height)]

    if t in GRAPH_OUT_TYPES:
        if not _node_active_at(n, k / max(1, ctx["fps"])):
            ctx["cache"][cache_key] = matrix
            return matrix

    if t == "viewport":
        source_id = ctx["inputs"].get("viewport", {}).get(node_id)
        source_node = ctx["nodes"].get(source_id) if source_id else None
        source_frame = None
        if source_node and _node_active_at(source_node, k / max(1, ctx["fps"])):
            if source_node.get("type") == "subgraph":
                source_frames = ctx["sub_frames"].get(source_id)
                if source_frames:
                    source_frame = source_frames[k % len(source_frames)]
            elif source_node.get("type") == "animation":
                source_frame = _anim_frame(source_node, k, ctx)
            elif source_node.get("type") == "bitmap":
                bp = source_node.get("params") or {}
                bw, bh = _pi(bp, "w", 1, 128, 8), _pi(bp, "h", 1, 128, 8)
                bframes = bp.get("frames") if isinstance(bp.get("frames"), list) else []
                if bframes:
                    raw = str(bframes[_bitmap_frame_index(bp, k, ctx["fps"])] or "")
                    source_frame = []
                    for by in range(bh):
                        row = []
                        for bx in range(bw):
                            try: row.append(int(raw[by * bw + bx], 16))
                            except (ValueError, IndexError): row.append(0)
                        source_frame.append(row)
        if source_frame:
            source_h = len(source_frame)
            source_w = len(source_frame[0]) if source_h else 0
            rb = ctx["binds"].get(node_id, {})
            x0 = _resolve(p, "x", ctx["time_values"], rb, 0, max(0, source_w - width), 0)
            y0 = _resolve(p, "y", ctx["time_values"], rb, 0, max(0, source_h - height), 0)
            source_params = source_node.get("params") or {}
            mono = source_params.get("modo") == "mono"
            mono_color = _pi(source_params, "color", 0, 15, 1)
            for yy in range(height):
                sy = y0 + yy
                if sy >= source_h:
                    break
                row = source_frame[sy]
                for xx in range(width):
                    sx = x0 + xx
                    if sx >= source_w:
                        break
                    value = row[sx]
                    if value:
                        matrix[yy][xx] = mono_color if mono else value
        ctx["cache"][cache_key] = matrix
        return matrix

    if t in ("subgraph", "animation", "algo"):
        {"subgraph": _draw_subgraph, "animation": _draw_animation, "algo": _draw_algo}[t](matrix, n, k, ctx)
        ctx["cache"][cache_key] = matrix
        return matrix

    if t in GRAPH_DRAW_TYPES:
        _draw_node(matrix, n, k, width, height, ctx["fps"], ctx["now"],
                   ctx["time_values"], ctx["binds"].get(node_id, {}))
        ctx["cache"][cache_key] = matrix
        return matrix

    def get_in(key):
        return ctx["inputs"].get(key, {}).get(node_id)

    if t == "repeat":
        inner_mat = eval_node_matrix(get_in("repeat"), k, ctx)
        rb = ctx["binds"].get(n["id"], {})
        x = _resolve(p, "x", ctx["time_values"], rb, -256, 256, 0)
        y = _resolve(p, "y", ctx["time_values"], rb, -256, 256, 0)
        nx = _resolve(p, "nx", ctx["time_values"], rb, 1, 32, 3)
        ny = _resolve(p, "ny", ctx["time_values"], rb, 1, 32, 3)
        dx = _resolve(p, "dx", ctx["time_values"], rb, -256, 256, 8)
        dy = _resolve(p, "dy", ctx["time_values"], rb, -256, 256, 8)
        mode = p.get("color_mode", "override")
        cstart, cstep = _pi(p, "color_start", 0, 15, 1), _pi(p, "color_step", -15, 15, 1)
        for j in range(ny):
            for i in range(nx):
                idx = j * nx + i
                target = (cstart + cstep * idx) % 16 if mode == "override" else None
                tx, ty = x + i * dx, y + j * dy
                for yy in range(height):
                    for xx in range(width):
                        v = inner_mat[yy][xx]
                        if not v:
                            continue
                        px, py = xx + tx, yy + ty
                        if 0 <= px < width and 0 <= py < height:
                            matrix[py][px] = target if target is not None else v
    elif t == "combine":
        mat_a = eval_node_matrix(get_in("combine_a"), k, ctx)
        mat_b = eval_node_matrix(get_in("combine_b"), k, ctx)
        modo = p.get("modo", "sottrai")
        for y in range(height):
            for x in range(width):
                va, vb = mat_a[y][x], mat_b[y][x]
                if modo == "unione":
                    matrix[y][x] = vb or va
                elif modo == "intersezione":
                    matrix[y][x] = va if vb else 0
                elif modo == "xor":
                    matrix[y][x] = 0 if (va and vb) else (va or vb)
                else:
                    matrix[y][x] = 0 if vb else va
    elif t == "color":
        inner_mat = eval_node_matrix(get_in("color"), k, ctx)
        col = _pi(p, "col", 0, 15, 2)
        for y in range(height):
            for x in range(width):
                if inner_mat[y][x]:
                    matrix[y][x] = col
    elif t == "move":
        inner_mat = eval_node_matrix(get_in(t), k, ctx)
        vx, vy = _pf(p, "vx", 1), _pf(p, "vy", 0)
        x0, y0 = _pf(p, "x0", 0), _pf(p, "y0", 0)
        modo = p.get("modo", "ciclo")
        px, py = _pi(p, "px", 0, 256, 0), _pi(p, "py", 0, 256, 0)
        if modo == "rimbalzo":
            dx = x0 + _tri(vx * k, px if px > 0 else width - 1)
            dy = y0 + _tri(vy * k, py if py > 0 else height - 1)
        else:
            dx = x0 + vx * k
            dy = y0 + vy * k
        wx = px if px > 0 else width if modo == "ciclo" else 9999
        wy = py if py > 0 else height if modo == "ciclo" else 9999
        for y in range(height):
            for x in range(width):
                u, v = x - dx, y - dy
                if modo == "ciclo":
                    u = u % wx if wx > 0 else u
                    v = v % wy if wy > 0 else v
                u, v = int(round(u)), int(round(v))
                if 0 <= u < width and 0 <= v < height:
                    matrix[y][x] = inner_mat[v][u]
    elif t == "rotate":
        inner_mat = eval_node_matrix(get_in(t), k, ctx)
        a0, vel = _pf(p, "a0", 0), _pf(p, "vel", 15)
        modo, rng = p.get("modo", "continuo"), _pf(p, "range", 360)
        cx, cy = _pf(p, "cx", (width - 1) / 2), _pf(p, "cy", (height - 1) / 2)
        a = math.radians(_drive(a0, vel, k, modo, rng))
        co, si = math.cos(a), math.sin(a)
        for y in range(height):
            for x in range(width):
                rx, ry = x - cx, y - cy
                u = int(round(cx + rx * co + ry * si))
                v = int(round(cy - rx * si + ry * co))
                if 0 <= u < width and 0 <= v < height:
                    matrix[y][x] = inner_mat[v][u]
    elif t == "scale":
        inner_mat = eval_node_matrix(get_in(t), k, ctx)
        s0, vel = _pf(p, "s0", 0.5), _pf(p, "vel", 0.1)
        modo, rng = p.get("modo", "rimbalzo"), _pf(p, "range", 1)
        cx, cy = _pf(p, "cx", (width - 1) / 2), _pf(p, "cy", (height - 1) / 2)
        s = max(0.05, _drive(s0, vel, k, modo, rng))
        for y in range(height):
            for x in range(width):
                u = int(round(cx + (x - cx) / s))
                v = int(round(cy + (y - cy) / s))
                if 0 <= u < width and 0 <= v < height:
                    matrix[y][x] = inner_mat[v][u]
    elif t == "mirror":
        inner_mat = eval_node_matrix(get_in(t), k, ctx)
        h, v = p.get("h", True), p.get("v", False)
        cx, cy = _pf(p, "cx", (width - 1) / 2), _pf(p, "cy", (height - 1) / 2)
        for y in range(height):
            for x in range(width):
                u = int(round(2 * cx - x)) if h else x
                v_val = int(round(2 * cy - y)) if v else y
                if 0 <= u < width and 0 <= v_val < height:
                    matrix[y][x] = inner_mat[v_val][u]
    elif t == "blink":
        on, off = _pi(p, "on", 0, 99, 1), _pi(p, "off", 0, 99, 1)
        fase = _pi(p, "fase", 0, 99, 0)
        inner_id = get_in(t)
        if on + off > 0:
            if (k + fase) % (on + off) < on:
                matrix = eval_node_matrix(inner_id, k, ctx)
        else:
            matrix = eval_node_matrix(inner_id, k, ctx)
    elif t == "trail":
        inner_id = get_in(t)
        n_count, step, eco = _pi(p, "n", 0, 30, 3), _pi(p, "passo", 1, 30, 1), _pi(p, "eco", 0, 15, 0)
        for i in range(n_count, -1, -1):
            past_k = (k - i * step) % ctx["cycle"]
            past_mat = eval_node_matrix(inner_id, past_k, ctx)
            for yy in range(height):
                for xx in range(width):
                    val = past_mat[yy][xx]
                    if val:
                        matrix[yy][xx] = eco if (i > 0 and eco > 0) else val

    ctx["cache"][cache_key] = matrix
    return matrix


def render_graph(graph, width, height, fps, now, max_frames=4000, values_out=None, _depth=0, _stack=(),
                 warnings_out=None, external_values=None):
    layers = graph_layers(graph)
    time_nodes = graph_time_nodes(graph)
    calc_nodes = graph_calc_nodes(graph)
    binds = graph_time_binds(graph)
    nodes = {n["id"]: n for n in graph.get("nodes", [])
             if isinstance(n, dict) and isinstance(n.get("id"), str)}
    inputs = graph_inputs(nodes, graph.get("links", []))
    calc_order = _value_order(nodes, inputs, binds, calc_nodes)

    def reach(roots):
        out, stack = [], list(roots)
        seen = set()
        while stack:
            nid = stack.pop()
            if not nid or nid not in nodes or nid in seen:
                continue
            seen.add(nid)
            out.append(nid)
            nn = nodes[nid]
            stack.extend(_children(nn, inputs))
            stack.extend(_src_node(src_id) for src_id in binds.get(nid, {}).values()
                         if (_src_node(src_id) in calc_nodes
                             or nodes.get(_src_node(src_id), {}).get("type") in GRAPH_EXTERNAL_TYPES))
        return out

    reached = reach(item["node"]["id"] for item in layers)

    sub_frames = {}
    used = list(dict.fromkeys(reached + reach(calc_nodes)))
    if _depth < MAX_SUBGRAPH_DEPTH:
        wanted = [nid for nid in used if nodes[nid].get("type") == "subgraph"]
        done = {}
        for nid in wanted:
            sp = nodes[nid].get("params") or {}
            loaded = _load_subgraph(sp, _stack)
            if loaded is None:
                continue
            gid, sg = loaded
            sw, sh = _sub_size(sp)
            if (gid, sw, sh) not in done:
                sub_w = []
                done[(gid, sw, sh)] = render_graph(sg, sw, sh, fps, now, max_frames,
                                                   _depth=_depth + 1, _stack=_stack + (gid,),
                                                   warnings_out=sub_w, external_values=external_values)
                if warnings_out is not None:
                    warnings_out.extend(
                        {"node": None, "msg": f"Grafo incorporato «{sp.get('graph_name') or gid}»: {w['msg']}"}
                        for w in sub_w)
            sub_frames[nid] = done[(gid, sw, sh)]

    anims = {}
    for nid in used:
        if nodes[nid].get("type") == "animation":
            entry = ANIM_CACHE.get(_pi(nodes[nid].get("params") or {}, "anim_id", 0, 10**9, 0))
            if entry:
                anims[nid] = entry
    loops = {nid: len(fr) for nid, fr in sub_frames.items()}
    for nid in used:
        if nodes[nid].get("type") == "bitmap":
            bp = nodes[nid].get("params") or {}
            frames = bp.get("frames")
            if isinstance(frames, list) and frames:
                durations = bp.get("frame_durations") if isinstance(bp.get("frame_durations"), list) else []
                total_ms = sum(max(20, min(10000, int(durations[i]))) if i < len(durations) else 100 for i in range(len(frames)))
                loops[nid] = max(1, math.ceil(total_ms * fps / 1000))
    for nid, entry in anims.items():
        loops[nid] = len(entry["frames"]) * _pi(nodes[nid].get("params") or {}, "hold", 1, 60, 1)

    algo_state, algo_done = {}, {}
    local_warn = warnings_out if warnings_out is not None else []

    def _algo_warn(nid, err):
        label = nodes[nid].get("name") or f"Algoritmico {nid}"
        local_warn.append({"node": nid, "err": err, "msg": f"Nodo «{label}»: {err}"})

    for nid in used:
        if nodes[nid].get("type") != "algo":
            continue
        ap = nodes[nid].get("params") or {}
        n_loop = _pi(ap, "frames", 1, ALGO_MAX_FRAMES, 32)
        loops[nid] = n_loop
        if binds.get(nid, {}).keys() & GRAPH_ALGO_BIND_PORTS:
            # ingressi collegati: lo script gira un frame alla volta, durante il rendering del grafo
            try:
                algo_state[nid] = {"n": n_loop, "runner": AlgoRunner(ap, fps), "pre": None}
            except AlgoError as e:
                algo_state[nid] = {"n": n_loop, "dead": True}
                _algo_warn(nid, str(e))
            continue
        # nessun ingresso collegato: il ciclo è fisso, lo si calcola una volta sola
        keys = ("script", "w", "h", "frames", "seed", "keep", "color", "in1", "in2")
        sig = json.dumps([ap.get(k) for k in keys] + [fps], sort_keys=True)
        if sig not in algo_done:
            algo_done[sig] = run_algo(ap, fps, _pf(ap, "in1", 0.0), _pf(ap, "in2", 0.0))
        fr, outs, err = algo_done[sig]
        if err:
            _algo_warn(nid, err)
        algo_state[nid] = {"n": len(fr) or n_loop, "pre": (fr, outs)} if fr else {"n": n_loop, "dead": True}
        if not fr:
            algo_state[nid]["dead"] = True

    periods = [max(1, node_period(nodes[nid], width, fps, time_nodes, binds.get(nid, {}), loops))
               for nid in reached]

    if not periods:
        periods = [1]
    cycle = 1
    for per in periods:
        cycle = math.lcm(cycle, per)
    min_cycle = min(max_frames, max(16, fps * 2))
    if cycle < min_cycle:
        cycle = min_cycle
    if cycle > max_frames:
        cycle = max_frames

    if values_out is not None:
        for cid in calc_order:
            if calc_nodes[cid]["type"] == "algo":
                values_out[f"{cid}.1"], values_out[f"{cid}.2"] = [], []
            else:
                values_out[cid] = []

    external_values = external_values or {}
    external_nodes = {nid: nodes[nid] for nid in used if nodes[nid].get("type") == "external"}
    if values_out is not None:
        for nid in external_nodes:
            values_out[nid] = []

    frames = []
    external_now_ts = time.time()
    for k in range(cycle):
        t_sec = k / max(1, fps)
        time_values = {}
        for tid, tn in time_nodes.items():
            time_values[tid] = _eval_value(tn, k, fps) if _node_active_at(tn, t_sec) else 0
        for eid, en in external_nodes.items():
            ep = en.get("params") or {}
            key = ep.get("key", "")
            record = external_values.get(key) or {}
            timeout = _pi(ep, "timeout", 0, 86400, 30)
            fresh = bool(record) and (timeout == 0 or external_now_ts - float(record.get("updated_at", 0)) <= timeout)
            value = record.get("value", ep.get("default", 0)) if fresh else ep.get("default", 0)
            time_values[eid] = _algo_num(value) if _node_active_at(en, t_sec) else 0
            if values_out is not None:
                values_out[eid].append(time_values[eid])
        ctx = {"nodes": nodes, "inputs": inputs, "binds": binds, "time_values": time_values,
               "width": width, "height": height, "fps": fps, "now": now, "cycle": cycle,
               "cache": {}, "busy": set(), "mbusy": set(), "sub_frames": sub_frames, "anims": anims,
               "algo": algo_state, "algo_mat": {}, "warn": local_warn}
        for cid in calc_order:
            cn = calc_nodes[cid]
            if cn["type"] == "algo":
                _eval_algo_frame(cn, k, t_sec, ctx)
                if values_out is not None:
                    values_out[f"{cid}.1"].append(time_values[f"{cid}.1"])
                    values_out[f"{cid}.2"].append(time_values[f"{cid}.2"])
                continue
            if not _node_active_at(cn, t_sec):
                time_values[cid] = 0
            elif cn["type"] == "math":
                time_values[cid] = _eval_math(cn, ctx)
            else:
                time_values[cid] = _eval_width(cn, k, ctx)
            if values_out is not None:
                values_out[cid].append(time_values[cid])
        canvas = [[0] * width for _ in range(height)]
        for item in layers:
            mat = eval_node_matrix(item["node"]["id"], k, ctx)
            for y in range(height):
                for x in range(width):
                    if mat[y][x] != 0:
                        canvas[y][x] = mat[y][x]
        frames.append(canvas)
    return frames


def graph_data(graph_id):
    with device_db() as db:
        row = db.execute("SELECT data FROM graphs WHERE id=?", (graph_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def _graph_inputs(g):
    nodes = {n["id"]: n for n in g.get("nodes", [])
             if isinstance(n, dict) and isinstance(n.get("id"), str)}
    return nodes, graph_inputs(nodes, g.get("links", []))


def find_nodes_in_tree(root_id, nodes, inputs, binds=None, seen=None):
    seen = set() if seen is None else seen
    if not root_id or root_id not in nodes or root_id in seen:
        return []
    seen.add(root_id)
    n = nodes[root_id]
    res = [n]
    for child in _children(n, inputs):
        res += find_nodes_in_tree(child, nodes, inputs, binds, seen)
    for src in (binds or {}).get(root_id, {}).values():
        src = _src_node(src)
        if nodes.get(src, {}).get("type") in GRAPH_CALC_TYPES:
            res += find_nodes_in_tree(src, nodes, inputs, binds, seen)
    return res


def _graph_tree_nodes(g, stack=(), depth=0):
    nodes, inputs = _graph_inputs(g)
    binds = graph_time_binds(g)
    res = []
    for layer in graph_layers(g):
        res += find_nodes_in_tree(layer["node"]["id"], nodes, inputs, binds)
    if depth < MAX_SUBGRAPH_DEPTH:
        for n in [n for n in res if n["type"] == "subgraph"]:
            loaded = _load_subgraph(n.get("params") or {}, stack)
            if loaded:
                res += _graph_tree_nodes(loaded[1], stack + (loaded[0],), depth + 1)
    return res


def _external_nodes_in_graph(graph, _stack=(), _depth=0):
    if not isinstance(graph, dict) or _depth > MAX_SUBGRAPH_DEPTH:
        return []
    found = [n for n in graph.get("nodes", [])
             if isinstance(n, dict) and n.get("type") == "external"]
    if _depth < MAX_SUBGRAPH_DEPTH:
        for n in _subgraph_nodes(graph):
            loaded = _load_subgraph(n.get("params") or {}, _stack)
            if loaded:
                gid, sub = loaded
                if gid not in _stack:
                    found.extend(_external_nodes_in_graph(sub, _stack + (gid,), _depth + 1))
    return found


def item_has_external_input(item):
    if item.get("type") != "graph":
        return False
    graph = item.get("graph") or graph_data(item.get("graph_id", 0))
    return graph_has_external_inputs(graph or {})


def item_external_next_expiry(item):
    if item.get("type") != "graph":
        return None
    graph = item.get("graph") or graph_data(item.get("graph_id", 0))
    nodes = _external_nodes_in_graph(graph or {})
    values, _ = get_external_inputs()
    now = time.time()
    expiries = []
    for node in nodes:
        params = node.get("params") or {}
        timeout = _pi(params, "timeout", 0, 86400, 30)
        record = values.get(params.get("key", "")) or {}
        if timeout > 0 and record:
            expiry = float(record.get("updated_at", 0)) + timeout
            if expiry > now:
                expiries.append(expiry)
    return min(expiries) if expiries else None


def item_has_clock(item):
    t = item.get("type")
    if t == "clock":
        return True
    if t == "graph":
        g = item.get("graph") or graph_data(item.get("graph_id", 0)) or {}
        return any(n["type"] == "clock" for n in _graph_tree_nodes(g))
    return False


def item_has_seconds_clock(item):
    if item.get("type") != "graph":
        return False
    g = item.get("graph") or graph_data(item.get("graph_id", 0)) or {}
    return any(n["type"] == "clock" and (n.get("params") or {}).get("part", "HH:MM") in ("SS", "HH:MM:SS")
               for n in _graph_tree_nodes(g))


def rgb_from(color, brightness):
    c = color.lstrip("#")
    if len(c) != 6:
        raise PlayError("Colore non valido", 400)
    try:
        return [round(int(c[i:i+2], 16) * brightness / 100) for i in (0, 2, 4)]
    except ValueError:
        raise PlayError("Colore esadecimale non valido", 400)


async def fetch_animation(animation_id):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{PIXELANIM_URL}/api/animations")
            r.raise_for_status()
            animation = next((a for a in r.json() if int(a["id"]) == animation_id), None)
            if animation is None:
                raise PlayError("Animazione non trovata", 404)
            r = await client.get(f"{PIXELANIM_URL}/api/animations/{animation_id}/frames")
            r.raise_for_status()
            frames = r.json()
    except PlayError:
        raise
    except Exception as exc:
        raise PlayError(f"Errore PixelAnim: {exc}")
    if not frames:
        raise PlayError("L'animazione non contiene frame", 400)
    return animation, frames


ANIM_CACHE = {}


def _clean_anim_frames(raw):
    mats = []
    for f in (raw if isinstance(raw, list) else [])[:MAX_ANIM_FRAMES]:
        pix = f.get("pixels") if isinstance(f, dict) else None
        if not isinstance(pix, list):
            continue
        rows = []
        for row in pix[:MAX_ANIM_DIM]:
            cells = []
            for v in (row if isinstance(row, list) else [])[:MAX_ANIM_DIM]:
                try:
                    cells.append(max(0, min(15, int(v))))
                except (TypeError, ValueError):
                    cells.append(0)
            rows.append(cells)
        mats.append(rows)
    if not mats:
        return [], 0, 0
    h = max(len(m) for m in mats)
    w = max((len(r) for m in mats for r in m), default=0)
    mats = [[(r + [0] * (w - len(r))) for r in m] + [[0] * w for _ in range(h - len(m))] for m in mats]
    return mats, w, h


def _subgraph_nodes(graph):
    """Restituisce i nodi che incorporano un altro grafo."""
    return [
        n for n in graph.get("nodes", [])
        if isinstance(n, dict) and n.get("type") == "subgraph"
    ]


def _graph_animation_ids(graph, depth=0, stack=()):
    ids = set()
    for n in graph.get("nodes", []):
        if isinstance(n, dict) and n.get("type") == "animation":
            aid = _pi(n.get("params") or {}, "anim_id", 0, 10**9, 0)
            if aid:
                ids.add(aid)
    if depth < MAX_SUBGRAPH_DEPTH:
        for n in _subgraph_nodes(graph):
            loaded = _load_subgraph(n.get("params") or {}, stack)
            if loaded:
                ids |= _graph_animation_ids(loaded[1], depth + 1, stack + (loaded[0],))
    return ids


async def prefetch_animations(graph):
    ids = _graph_animation_ids(graph)
    t0 = time.monotonic()
    todo = sorted(i for i in ids if i not in ANIM_CACHE or t0 - ANIM_CACHE[i]["t"] > ANIM_TTL)
    warnings = []
    if todo:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(f"{PIXELANIM_URL}/api/animations")
                r.raise_for_status()
                metas = {int(a["id"]): a for a in r.json()}
                for aid in todo:
                    meta = metas.get(aid)
                    if meta is None:
                        ANIM_CACHE.pop(aid, None)
                        warnings.append(f"Animazione #{aid} non trovata in PixelAnim")
                        continue
                    r = await client.get(f"{PIXELANIM_URL}/api/animations/{aid}/frames")
                    r.raise_for_status()
                    frames, w, h = _clean_anim_frames(r.json())
                    if not frames:
                        ANIM_CACHE.pop(aid, None)
                        warnings.append(f"L'animazione «{meta.get('name', aid)}» non contiene frame")
                        continue
                    ANIM_CACHE[aid] = {"t": t0, "w": w, "h": h, "name": meta.get("name"), "frames": frames}
        except Exception as exc:
            warnings.append(f"PixelAnim non raggiungibile: {exc}")
    return warnings


# ─── Render pool (processi separati per i grafi) ─────────────────────────────
# render_graph è CPU-bound e condivide il GIL col processo principale:
# eseguirlo sull'event loop blocca playlist, monitor, messaggi e stream verso
# le board. Lo spostiamo in un pool di processi, ognuno con il proprio GIL.

_render_pool: ProcessPoolExecutor | None = None


def _make_render_pool() -> ProcessPoolExecutor:
    method = os.getenv("RENDER_MP_METHOD", "fork")
    try:
        ctx = mp.get_context(method)
    except ValueError:
        ctx = mp.get_context("fork")
    n = max(1, int(os.getenv("RENDER_WORKERS", "2")))
    return ProcessPoolExecutor(max_workers=n, mp_context=ctx)


def _render_worker(payload: dict) -> dict:
    """Gira in un processo worker. Riceve solo dati picklabili: grafo, dimensioni,
    fps, timestamp ISO e uno snapshot delle animazioni già scaricate dal padre."""
    global ANIM_CACHE
    ANIM_CACHE.clear()
    ANIM_CACHE.update(payload.get("anims") or {})

    try:
        now = datetime.fromisoformat(payload["now"]) if payload.get("now") else local_now()
    except (TypeError, ValueError):
        now = local_now()

    values: dict = {}
    warnings: list = []
    frames = render_graph(
        payload["graph"], payload["width"], payload["height"], payload["fps"],
        now,
        max_frames=payload.get("max_frames", 4000),
        values_out=values,
        warnings_out=warnings,
        external_values=payload.get("external_values") or {},
    )
    total = len(frames)
    max_out = payload.get("max_out")
    if max_out:
        frames = frames[:max_out]
        values = {nid: v[:max_out] for nid, v in values.items()}
    return {"total": total, "frames": frames, "values": values, "warnings": warnings}


# ─── Cache render risultato completo ─────────────────────────────────────────
# render_graph è deterministico dato (graph, width, height, fps): se il grafo
# non cambia, il risultato è identico. Cachiamo l'intero output.
# La chiave include uno `scope` legato all'orologio, così un grafo con orologio
# fa cache-miss automatico quando cambia il minuto/seconda/ora.
# RENDER_CACHE_DISABLED=1 disattiva tutto (bypass, sempre render completo).

_render_result_cache: dict = {}      # key -> {"ts", "ttl", "result"}
_render_result_order: list = []      # LRU (oldest in testa)
_render_cache_stats = {
    "hits": 0, "misses": 0, "bypass": 0,
    "expired": 0, "evictions": 0, "puts": 0, "invalidations": 0,
}


def _cache_scope(graph, now):
    """Granularità minima dell'orologio. Ritorna (scope, ttl_secondi)."""
    has_sec = has_min = has_hour = False
    for n in _graph_tree_nodes(graph):
        if n["type"] != "clock":
            continue
        part = (n.get("params") or {}).get("part", "HH:MM")
        if part in ("SS", "HH:MM:SS"):
            has_sec = True
        elif part in ("MM", "HH:MM", "alterna"):
            has_min = True
        elif part == "HH":
            has_hour = True
    if has_sec:
        return f"t{now.hour:02d}{now.minute:02d}{now.second:02d}", 10
    if has_min:
        return f"t{now.hour:02d}{now.minute:02d}", 60
    if has_hour:
        return f"t{now.hour:02d}", 3600
    return "static", 3600


def _render_cache_key(graph, width, height, fps, max_frames, max_out, scope):
    blob = json.dumps([graph, width, height, fps, max_frames, max_out],
                      sort_keys=True, separators=(",", ":"))
    h = hashlib.blake2b(blob.encode(), digest_size=16).hexdigest()
    return f"{h}:{scope}"


def _render_cache_get(key):
    ent = _render_result_cache.get(key)
    if ent is None:
        _render_cache_stats["misses"] += 1
        return None
    if time.time() - ent["ts"] > ent["ttl"]:
        _render_cache_stats["expired"] += 1
        _render_cache_stats["misses"] += 1
        _render_result_cache.pop(key, None)
        try:
            _render_result_order.remove(key)
        except ValueError:
            pass
        return None
    _render_cache_stats["hits"] += 1
    try:
        _render_result_order.remove(key)
        _render_result_order.append(key)
    except ValueError:
        pass
    return ent["result"]


def _render_cache_put(key, result, ttl):
    _render_cache_stats["puts"] += 1
    _render_result_cache[key] = {"ts": time.time(), "ttl": ttl, "result": result}
    try:
        _render_result_order.remove(key)
    except ValueError:
        pass
    _render_result_order.append(key)
    total = sum(len(_render_result_cache[k]["result"]["frames"]) for k in _render_result_order)
    while _render_result_order and (
            len(_render_result_order) > RENDER_RESULT_MAX
            or total > RENDER_RESULT_MAX_FRAMES):
        old = _render_result_order.pop(0)
        ent = _render_result_cache.pop(old, None)
        if ent:
            _render_cache_stats["evictions"] += 1
            total -= len(ent["result"]["frames"])


def _invalidate_render_cache():
    """Da chiamare quando un grafo salvato cambia (le cache di grafi che lo
    incorporano come sottografo sarebbero altrimenti stantie)."""
    if _render_result_cache:
        _render_cache_stats["invalidations"] += 1
    _render_result_cache.clear()
    _render_result_order.clear()


def graph_has_external_inputs(graph, _stack=(), _depth=0):
    if not isinstance(graph, dict) or _depth > MAX_SUBGRAPH_DEPTH:
        return False
    nodes = graph.get("nodes", [])
    if any(isinstance(n, dict) and n.get("type") == "external" for n in nodes):
        return True
    if _depth >= MAX_SUBGRAPH_DEPTH:
        return False
    for n in nodes:
        if not isinstance(n, dict) or n.get("type") != "subgraph":
            continue
        loaded = _load_subgraph(n.get("params") or {}, _stack)
        if loaded:
            gid, sub = loaded
            if gid not in _stack and graph_has_external_inputs(sub, _stack + (gid,), _depth + 1):
                return True
    return False


async def _render_in_pool(graph, width, height, fps, *, max_frames, max_out, timeout):
    if _render_pool is None:
        raise PlayError("Render pool non disponibile", 503)

    now = local_now()
    dynamic_inputs = graph_has_external_inputs(graph)
    external_values, external_revision = get_external_inputs()
    scope, ttl = _cache_scope(graph, now)
    cache_key = _render_cache_key(graph, width, height, fps, max_frames, max_out, scope)
    t_start = time.monotonic()

    if not RENDER_CACHE_DISABLED and not dynamic_inputs:
        cached = _render_cache_get(cache_key)
        if cached is not None:
            dt = time.monotonic() - t_start
            log.info(f"[render] cache HIT  ({dt:.4f}s) scope={scope} "
                     f"{width}x{height}@{fps}fps frames={len(cached['frames'])}")
            _stats_record_render("hit", scope, width, height, fps, dt)
            return cached

    await prefetch_animations(graph)
    needed = _graph_animation_ids(graph)
    anims_snapshot = {aid: ANIM_CACHE[aid] for aid in needed if aid in ANIM_CACHE}
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_render_pool, _render_worker, {
                "graph": graph,
                "width": width, "height": height, "fps": fps,
                "now": now.isoformat(),
                "anims": anims_snapshot,
                "external_values": external_values,
                "max_frames": max_frames,
                "max_out": max_out,
            }),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _stats_record_error(
            f"render timeout {width}x{height}@{fps}fps scope={scope}")
        raise PlayError("Render troppo lento: riduci la complessità del grafo o gli FPS", 504)

    if dynamic_inputs:
        result["_external_revision"] = external_revision
        result["_external_state"] = {key: {"updated_at": rec.get("updated_at", 0)}
                                     for key, rec in external_values.items()}
    dt = time.monotonic() - t_start
    if RENDER_CACHE_DISABLED or dynamic_inputs:
        _render_cache_stats["bypass"] += 1
        reason = "external-input" if dynamic_inputs else "disabled"
        log.info(f"[render] BYPASS    ({dt:.2f}s) reason={reason} scope={scope} "
                 f"{width}x{height}@{fps}fps frames={len(result['frames'])}")
        _stats_record_render("bypass", scope, width, height, fps, dt)
    else:
        _render_cache_put(cache_key, result, ttl)
        log.info(f"[render] cache MISS ({dt:.2f}s) scope={scope} "
                 f"{width}x{height}@{fps}fps frames={len(result['frames'])}")
        _stats_record_render("miss", scope, width, height, fps, dt)
    return result

# ─── Streaming verso ESPHome ─────────────────────────────────────────────────

class BoardLink:
    def __init__(self, host, port, key, color=False, width=8, height=8):
        self.host, self.port, self.key, self.color = host, port, key, color
        self.width, self.height = int(width), int(height)
        self.ws = None
        self.reader = None
        self.closed = True
        self.last_error = None

    async def open(self):
        ip = await resolve_host(self.host)
        try:
            self.ws = await websockets.connect(PIXELANIM_WS, ping_interval=20, max_size=2**22)
            await self.ws.send(json.dumps({
                "type": "open", "ip": ip, "port": self.port,
                "key": self.key, "color": self.color,
                "width": self.width, "height": self.height}))
            resp = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=15))
        except Exception as exc:
            await self.close()
            raise PlayError(f"Connessione a PixelAnim fallita: {exc}")
        if resp.get("type") != "ready":
            await self.close()
            raise PlayError(resp.get("detail", "Connessione ESPHome fallita"))
        self.closed = False
        self.last_error = None
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "error":
                    self.last_error = msg.get("detail", "Errore dalla board")
                elif msg.get("type") == "sent":
                    self.last_error = None
        except Exception:
            pass
        finally:
            self.closed = True

    async def _send(self, payload):
        if self.closed or self.ws is None:
            raise PlayError("Connessione con PixelAnim chiusa")
        try:
            await self.ws.send(json.dumps(payload))
        except Exception as exc:
            raise PlayError(f"Errore streaming: {exc}")

    async def stop(self):
        await self._send({"type": "stop"})

    async def play(self, frames, fps, rgb, loop=True):
        await self._send({"type": "play", "frames": frames, "fps": fps,
                          "r": rgb[0], "g": rgb[1], "b": rgb[2], "loop": loop,
                          "width": self.width, "height": self.height})

    async def cplay(self, frames, palette96, fps, loop=True):
        await self._send({"type": "cplay", "frames": frames, "pal": palette96,
                          "fps": fps, "loop": loop,
                          "width": self.width, "height": self.height})

    async def close(self):
        self.closed = True
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.send(json.dumps({"type": "stop"}))
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
        reader, self.reader = self.reader, None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def ensure_link(device, link, color):
    w, h = int(device["width"]), int(device["height"])
    if (link is not None and not link.closed
            and link.color == color and link.width == w and link.height == h):
        return link
    if link is not None:
        await link.close()
    new = BoardLink(device["host"], device["port"], device["api_key"], color, w, h)
    await new.open()
    return new


# ─── Play di un item ─────────────────────────────────────────────────────────

async def load_content(device, item):
    kind = item.get("type", "animation")
    width, height = int(device["width"]), int(device["height"])

    if kind == "animation":
        return await fetch_animation(item["animation_id"])

    if kind == "graph":
        graph = item.get("graph") or graph_data(item.get("graph_id", 0))
        if graph is None:
            raise PlayError("Grafo non trovato", 404)
        # Render nel pool: la riproduzione non blocca l'event loop.
        result = await _render_in_pool(
            graph, width, height, item["fps"],
            max_frames=4000, max_out=None, timeout=120.0,
        )
        frames = result["frames"]
        for w in result.get("warnings") or []:
            log.info(f"[graph] {w['msg']}")
            _stats_record_error(f"graph: {w['msg']}")
    elif kind == "text":
        frames = render_text(item["text"], width, height)
    else:
        frames = render_clock(width, height, local_now())
    return {"width": width, "height": height,
            "_external_revision": result.get("_external_revision") if kind == "graph" else None,
            "_external_state": result.get("_external_state", {}) if kind == "graph" else {}}, \
           [{"pixels": f} for f in frames]


async def play_pause(device, item, link):
    mode = item.get("pause_mode", "hold")
    w, h = int(device["width"]), int(device["height"])
    info = {"frames": 0, "width": w, "height": h, "rgb": [0, 0, 0]}
    did = device["id"]
    if mode == "release":
        if link is not None:
            await link.close()
        active_frames.pop(did, None)
        return None, info
    if mode == "hold":
        if link is not None and not link.closed:
            await link.stop()
        _mark_running(did, False)
        return link, info
    link = await ensure_link(device, link, bool(device["color"]))
    if device["color"]:
        empty = "0" * (w * h)
        await link.cplay([empty], "0" * 96, 1, True)
        _set_active_frame(did, [empty], 1, w, h, True, get_palette(), [0, 0, 0], running=True)
    else:
        empty = "0" * (w * h // 4)
        await link.play([empty], 1, [0, 0, 0], True)
        _set_active_frame(did, [empty], 1, w, h, False, get_palette(), [0, 0, 0], running=True)
    return link, info


async def play_item(device, item, link, loop=True, frame_offset=0, phase_started=None):
    kind = item.get("type", "animation")
    if kind == "pause":
        return await play_pause(device, item, link)

    check_dims(device)
    rgb = rgb_from(item["color"], item["brightness"])
    animation, frames = await load_content(device, item)
    fps = 1 if kind == "clock" else item["fps"]
    if phase_started is not None:
        frame_offset = int((time.monotonic() - phase_started) * max(1, fps))
    use_pal = item["use_palette"] and kind in ("animation", "graph")
    did = device["id"]

    dev_w, dev_h = int(device["width"]), int(device["height"])
    src_w = int(animation.get("width", dev_w))
    src_h = int(animation.get("height", dev_h))

    log.info(f"[play_item] device={did} kind={kind} fps={fps} frames={len(frames)} "
             f"src={src_w}x{src_h} dst={dev_w}x{dev_h}")
    _stats_record_play(did, kind)

    if src_w > dev_w:
        raise PlayError(
            f"Animazione troppo larga per la board: {src_w} > {dev_w}", 400)
    if src_h > dev_h:
        raise PlayError(
            f"Animazione troppo alta per la board: {src_h} > {dev_h}", 400)

    if src_w < dev_w or src_h < dev_h:
        repeat_x = int(item.get("repeat_x", 0) or 0)
        if repeat_x <= 0:
            repeat_x = max(1, dev_w // max(1, src_w))
        frames = tile_frames(frames, src_w, src_h, dev_w, dev_h, repeat_x)

    rotate = bool(device.get("rotate", False)) or bool(item.get("rotate", False))
    if frames and frame_offset:
        offset = int(frame_offset) % len(frames)
        if offset:
            frames = frames[offset:] + frames[:offset]

    if device["color"]:
        pix = encode_color(frames, dev_w, dev_h, rotate, binary=not use_pal)
        if all(set(p) <= {"0"} for p in pix):
            raise PlayError("Animazione vuota", 400)
        palette96 = build_palette(use_pal, item["color"], item["brightness"], get_palette())
        link = await ensure_link(device, link, True)
        await link.cplay(pix, palette96, fps, loop)
        _set_active_frame(did, pix, fps, dev_w, dev_h, True, get_palette(), rgb, running=True)
        return link, {"frames": len(pix), "width": dev_w, "height": dev_h, "rgb": rgb,
                      "external_revision": animation.get("_external_revision"),
                      "external_state": animation.get("_external_state", {})}

    hex_frames = encode_animation(frames, dev_w, dev_h, rotate)
    if all(not any(int(f[i:i+2], 16) for i in range(0, len(f), 2)) for f in hex_frames):
        raise PlayError("Animazione vuota", 400)
    link = await ensure_link(device, link, False)
    await link.play(hex_frames, fps, rgb, loop)
    _set_active_frame(did, hex_frames, fps, dev_w, dev_h, False, get_palette(), rgb, running=True)
    return link, {"frames": len(hex_frames), "width": dev_w, "height": dev_h, "rgb": rgb,
                  "external_revision": animation.get("_external_revision"),
                  "external_state": animation.get("_external_state", {})}


# ─── Runner / messaggi ──────────────────────────────────────────────────────

links = {}
runners = {}
runner_status = {}


async def stop_device(key):
    task = runners.pop(key, None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    link = links.pop(key, None)
    if link is not None:
        await link.close()
    runner_status.pop(key, None)
    _mark_running(key, False)


async def stop_all():
    for task in list(message_tasks.values()):
        task.cancel()
    await asyncio.gather(*message_tasks.values(), return_exceptions=True)
    for key in list(runners) + list(links):
        await stop_device(key)


message_queues = {}
message_events = {}
message_tasks = {}
message_status = {}
MAX_QUEUED_MESSAGES = 20


def runner_alive(device_id):
    t = runners.get(device_id)
    return t is not None and not t.done()


def message_item(device, msg):
    cols = text_columns(msg["text"])
    width = int(device["width"])
    if msg.get("duration"):
        duration = msg["duration"]
    elif len(cols) <= width:
        duration = 10
    else:
        duration = math.ceil((len(cols) + width - 1) * msg["repeat"] / msg["fps"]) + 1
    rotate = msg["rotate"] if msg["rotate"] is not None else bool(device.get("rotate"))
    return {"type": "text", "text": msg["text"], "animation_id": 0, "duration": duration,
            "fps": msg["fps"], "color": msg["color"], "brightness": msg["brightness"],
            "rotate": rotate, "use_palette": False, "pause_mode": "hold",
            "repeat_x": 0, "enabled": True, "graph_id": 0, "auto_duration": False}


async def wait_tick(device_id, timeout):
    ev = message_events.get(device_id)
    if ev is None:
        await asyncio.sleep(timeout)
        return
    try:
        await asyncio.wait_for(ev.wait(), timeout)
    except asyncio.TimeoutError:
        pass


async def deliver_messages(device_id, device, link):
    q = message_queues.get(device_id)
    ms = message_status.setdefault(device_id, {"showing": None, "error": None})
    while q:
        msg = q.pop(0)
        if not q:
            message_events[device_id].clear()
        item = message_item(device, msg)
        ms["showing"] = msg["text"]
        try:
            link, _ = await play_item(device, item, link)
            links[device_id] = link
            end = time.monotonic() + item["duration"]
            while time.monotonic() < end:
                await asyncio.sleep(min(1.0, max(0.0, end - time.monotonic())))
                if link.closed:
                    raise PlayError(link.last_error or "Connessione persa")
            ms["error"] = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ms["error"] = exc.msg if isinstance(exc, PlayError) else str(exc)
            raise
        finally:
            ms["showing"] = None
    return link


async def message_worker(device_id):
    try:
        while message_queues.get(device_id):
            row = get_device(device_id)
            if row is None:
                message_queues.pop(device_id, None)
                break
            old = links.pop(device_id, None)
            if old is not None:
                await old.close()
            try:
                await deliver_messages(device_id, dict(row), None)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                lk = links.pop(device_id, None)
                if lk is not None:
                    await lk.close()
    finally:
        message_tasks.pop(device_id, None)


async def playlist_runner(device_id, override=None):
    st = runner_status[device_id] = {"running": True, "index": None, "total": 0, "error": None}
    link = None
    last_idx = None
    try:
        while True:
            items = override if override is not None else playlist_items(device_id)
            st["total"] = len(items)
            if not items:
                st["error"] = "Palinsesto vuoto"
                return
            active = [(i, it) for i, it in enumerate(items) if it.get("enabled", True)]
            if not active:
                st["index"], st["error"] = None, "Nessun elemento attivo"
                try:
                    if message_queues.get(device_id):
                        row = get_device(device_id)
                        if row is not None:
                            link = await deliver_messages(device_id, dict(row), link)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                if link is not None:
                    await link.close()
                    link = None
                    links.pop(device_id, None)
                await wait_tick(device_id, 2.0)
                continue

            # Costruisce la coda del giro corrente (sequenziale o casuale)
            dev_check = get_device(device_id)
            if dev_check is None:
                st["error"] = "Board eliminata"
                return

            if dict(dev_check).get("shuffle") and len(active) > 1:
                pool = [pair for pair in active if pair[0] != last_idx] or active
                step_list = [random.choice(pool)]
            else:
                step_list = active

            for idx, item in step_list:
                if not item.get("enabled", True):
                    continue
                try:
                    device = get_device(device_id)
                    if device is None:
                        st["error"] = "Board eliminata"
                        return
                    # Se nel frattempo lo shuffle è stato attivato durante un giro sequenziale, interrompe il giro per ricalcolare
                    if len(step_list) > 1 and dict(device).get("shuffle"):
                        break
                    if message_queues.get(device_id):
                        link = await deliver_messages(device_id, dict(device), link)
                    log.info(f"[playlist] device={device_id} idx={idx + 1}/{len(items)} shuffle={bool(dict(device).get('shuffle'))}")
                    live_external = item_has_external_input(item)
                    link, play_info = await play_item(dict(device), item, link)
                    playback_started = time.monotonic()
                    external_rev = (play_info.get("external_revision")
                                    if live_external and play_info.get("external_revision") is not None
                                    else (external_input_revision() if live_external else None))
                    external_expiry = item_external_next_expiry(item) if live_external else None
                    last_idx = idx
                    if link is not None:
                        links[device_id] = link
                    else:
                        links.pop(device_id, None)
                    st["index"], st["error"] = idx, None
                    end = time.monotonic() + item["duration"]
                    refresh_seconds = item_has_seconds_clock(item)
                    refresh_clock = item_has_clock(item) and not refresh_seconds
                    shown, shown_sec = clock_key(), clock_key_seconds()
                    tick = 0.25 if (refresh_seconds or live_external) else 1.0
                    while time.monotonic() < end:
                        await wait_tick(device_id, min(tick, max(0.0, end - time.monotonic())))
                        input_changed = live_external and external_input_revision() != external_rev
                        input_expired = live_external and external_expiry is not None and time.time() >= external_expiry
                        if input_changed or input_expired:
                            t0 = time.monotonic()
                            external_rev = external_input_revision()
                            link, play_info = await play_item(dict(device), item, link, phase_started=playback_started)
                            external_rev = (play_info.get("external_revision")
                                            if play_info.get("external_revision") is not None
                                            else external_input_revision())
                            external_expiry = item_external_next_expiry(item)
                            if link is not None:
                                links[device_id] = link
                            end += time.monotonic() - t0
                            shown, shown_sec = clock_key(), clock_key_seconds()
                        if link is not None and link.closed:
                            raise PlayError(link.last_error or "Connessione persa")
                        if message_queues.get(device_id):
                            t0 = time.monotonic()
                            link = await deliver_messages(device_id, dict(device), link)
                            resume = item
                            if item.get("type") == "pause" and item.get("pause_mode") == "hold":
                                resume = {**item, "pause_mode": "blank"}
                            link, _ = await play_item(dict(device), resume, link)
                            if link is not None:
                                links[device_id] = link
                            else:
                                links.pop(device_id, None)
                            end += time.monotonic() - t0
                            shown, shown_sec = clock_key(), clock_key_seconds()
                        if refresh_seconds:
                            if clock_key_seconds() != shown_sec:
                                shown_sec = clock_key_seconds()
                                shown = clock_key()
                                link, _ = await play_item(dict(device), item, link)
                                links[device_id] = link
                        elif refresh_clock and clock_key() != shown:
                            shown = clock_key()
                            link, _ = await play_item(dict(device), item, link)
                            links[device_id] = link
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    st["error"] = exc.msg if isinstance(exc, PlayError) else str(exc)
                    _stats_record_error(f"device={device_id} {st['error']}")
                    if link is not None:
                        await link.close()
                        link = None
                    links.pop(device_id, None)
                    await asyncio.sleep(5)
    finally:
        st["running"] = False
        _mark_running(device_id, False)
        if link is not None:
            await link.close()
        if links.get(device_id) is link:
            links.pop(device_id, None)

@asynccontextmanager
async def lifespan(app):
    global _render_pool
    _setup_logging()                          # ← nuova riga
    init_devices_db()
    _render_pool = _make_render_pool()
    log.info(f"render pool: {_render_pool._max_workers} worker "
             f"(method={os.getenv('RENDER_MP_METHOD', 'fork')})")
    if RENDER_CACHE_DISABLED:
        log.info("render cache: DISABILITATA (RENDER_CACHE_DISABLED=1)")
    else:
        log.info(f"render cache: attiva (max={RENDER_RESULT_MAX} voci, "
                 f"{RENDER_RESULT_MAX_FRAMES} frame)")
    log.info(f"palette cache: TTL {PALETTE_CACHE_TTL:.1f}s")
    log.info(f"log file: {LOG_FILE}")
    for row in autostart_devices():
        runners[row["id"]] = asyncio.create_task(playlist_runner(row["id"]))
        log.info(f"autostart: '{row['name']}' (id {row['id']})")
    try:
        yield
    finally:
        if _render_pool is not None:
            _render_pool.shutdown(wait=False, cancel_futures=True)
            _render_pool = None
        await stop_all()





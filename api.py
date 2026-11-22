"""Endpoint HTTP di PixelAnim Broadcast. La logica applicativa risiede in core.py."""
from fastapi import APIRouter
import core as core_runtime
from core import *  # condivide funzioni, configurazione e stato runtime
from core import _build_stats, _bump, _format_stats_text, _invalidate_palette_cache, _invalidate_render_cache, _percentile, _pi, _render_in_pool, _scope_type, _stats, _tail_lines

router = APIRouter()

@router.get("/", response_class=HTMLResponse)
async def home():
    with open(BASE_DIR / "static" / "index.html", encoding="utf-8") as f:
        return f.read()


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    try:
        return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")
    except Exception:
        raise HTTPException(404)


@router.get("/api/animations")
async def get_animations():
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{PIXELANIM_URL}/api/animations")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(502, f"PixelAnim non raggiungibile: {exc}")


@router.get("/api/animations/{anim_id}/frames")
async def get_frames(anim_id: int):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{PIXELANIM_URL}/api/animations/{anim_id}/frames")
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(502, f"Errore frame: {exc}")


@router.get("/api/devices/{device_id}/monitor")
async def get_monitor_frame(device_id: int):
    if device_id not in active_frames:
        return {"ok": False}
    return {"ok": True, **active_frames[device_id]}


class SettingsRequest(BaseModel):
    palette: list[str] = Field(min_length=16, max_length=16)


@router.get("/api/settings")
async def get_settings():
    return {"palette": get_palette()}


@router.put("/api/settings")
async def put_settings(req: SettingsRequest):
    palette = normalize_palette(req.palette)
    with device_db() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('palette',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(palette),))
        db.commit()
    _invalidate_palette_cache()
    return {"palette": palette}


class DeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    host: str
    port: int = Field(default=6053, ge=1, le=65535)
    api_key: str = ""
    width: Literal[8, 16, 32] = 8
    height: Literal[8, 16] = 8
    color: bool = False
    rotate: bool = False
    text_fps: int = Field(default=10, ge=1, le=30)


@router.get("/api/devices")
async def list_devices():
    with device_db() as db:
        rows = db.execute("SELECT * FROM devices ORDER BY name COLLATE NOCASE").fetchall()
    return [public_device(row) for row in rows]


@router.post("/api/devices")
async def create_device(req: DeviceRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Inserisci un nome")
    host = checked_host(req.host)
    if not req.api_key.strip():
        raise HTTPException(400, "Inserisci la chiave noise_psk")
    with device_db() as db:
        cur = db.execute("INSERT INTO devices(name,host,port,api_key,width,height,color,rotate,text_fps) "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         (name, host, req.port, req.api_key.strip(), req.width, req.height,
                          int(req.color), int(req.rotate), req.text_fps))
        db.commit()
        row = db.execute("SELECT * FROM devices WHERE id=?", (cur.lastrowid,)).fetchone()
    return public_device(row)


@router.put("/api/devices/{device_id}")
async def update_device(device_id: int, req: DeviceRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Inserisci un nome")
    host = checked_host(req.host)
    with device_db() as db:
        old = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Dispositivo non trovato")
        key = req.api_key.strip() or old["api_key"]
        if not key:
            raise HTTPException(400, "Inserisci la chiave noise_psk")
        db.execute("UPDATE devices SET name=?,host=?,port=?,api_key=?,width=?,height=?,color=?,rotate=?,text_fps=? "
                   "WHERE id=?",
                   (name, host, req.port, key, req.width, req.height,
                    int(req.color), int(req.rotate), req.text_fps, device_id))
        db.commit()
        row = db.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    await stop_device(device_id)
    return public_device(row)


@router.delete("/api/devices/{device_id}")
async def delete_device(device_id: int):
    message_queues.pop(device_id, None)
    await stop_device(device_id)
    with device_db() as db:
        cur = db.execute("DELETE FROM devices WHERE id=?", (device_id,))
        db.execute("DELETE FROM playlist_items WHERE device_id=?", (device_id,))
        db.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Dispositivo non trovato")
    return {"ok": True}


class PlaylistItem(BaseModel):
    type: Literal["animation", "text", "clock", "pause", "graph"] = "animation"
    graph_id: int = 0
    enabled: bool = True
    pause_mode: Literal["hold", "blank", "release"] = "hold"
    animation_id: int = 0
    text: str = Field(default="", max_length=120)
    duration: int = Field(default=10, ge=1, le=86400)
    fps: int = Field(default=8, ge=1, le=30)
    color: str = Field(default="#00ff66", pattern=r"^#[0-9a-fA-F]{6}$")
    brightness: int = Field(default=100, ge=1, le=100)
    rotate: bool = False
    use_palette: bool = True
    auto_duration: bool = False
    repeat_x: int = Field(default=0, ge=0, le=64)

    @model_validator(mode="after")
    def check_type(self):
        if self.type == "animation" and self.animation_id <= 0:
            raise ValueError("Scegli un'animazione")
        if self.type == "graph" and self.graph_id <= 0:
            raise ValueError("Scegli un grafo")
        if self.type == "text" and not self.text.strip():
            raise ValueError("Il testo non può essere vuoto")
        return self


class PlaylistRequest(BaseModel):
    items: list[PlaylistItem] = Field(max_length=MAX_PLAYLIST_ITEMS)


class ImportRequest(BaseModel):
    source_id: int
    mode: Literal["replace", "append"] = "replace"


def require_device(device_id):
    device = get_device(device_id)
    if device is None:
        raise HTTPException(404, "Dispositivo non trovato")
    return device


class AutostartRequest(BaseModel):
    enabled: bool


@router.put("/api/devices/{device_id}/autostart")
async def set_autostart(device_id: int, req: AutostartRequest):
    require_device(device_id)
    with device_db() as db:
        db.execute("UPDATE devices SET autostart=? WHERE id=?", (int(req.enabled), device_id))
        db.commit()
    return {"autostart": req.enabled}


@router.get("/api/devices/{device_id}/playlist")
async def get_playlist(device_id: int):
    require_device(device_id)
    return playlist_items(device_id)


@router.put("/api/devices/{device_id}/playlist")
async def put_playlist(device_id: int, req: PlaylistRequest):
    require_device(device_id)
    save_playlist(device_id, [i.model_dump() for i in req.items])
    return playlist_items(device_id)


@router.post("/api/devices/{device_id}/playlist/import")
async def import_playlist(device_id: int, req: ImportRequest):
    require_device(device_id)
    require_device(req.source_id)
    if req.source_id == device_id:
        raise HTTPException(400, "Origine e destinazione coincidono")
    items = playlist_items(req.source_id)
    if req.mode == "append":
        items = playlist_items(device_id) + items
    if len(items) > MAX_PLAYLIST_ITEMS:
        raise HTTPException(400, "Troppi elementi")
    save_playlist(device_id, items)
    return playlist_items(device_id)


@router.post("/api/devices/{device_id}/playlist/start")
async def start_playlist(device_id: int):
    device = require_device(device_id)
    if not device["api_key"]:
        raise HTTPException(400, "Manca la chiave noise_psk")
    if not playlist_items(device_id):
        raise HTTPException(400, "Il palinsesto è vuoto")
    await stop_device(device_id)
    runners[device_id] = asyncio.create_task(playlist_runner(device_id))
    return {"ok": True}


@router.post("/api/devices/{device_id}/playlist/stop")
async def stop_playlist(device_id: int):
    await stop_device(device_id)
    return {"ok": True}


@router.get("/api/playlists/status")
async def playlists_status():
    out = {}
    for did, st in runner_status.items():
        link = links.get(did)
        out[str(did)] = {**st, "error": st["error"] or (link.last_error if link else None)}
    for did, ms in message_status.items():
        entry = out.setdefault(str(did), {"running": False, "index": None, "total": 0, "error": None})
        entry["message"] = {**ms, "pending": len(message_queues.get(did, []))}
    return out


class GraphIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    graph: dict


class GraphRenderRequest(BaseModel):
    graph: dict
    width: Literal[8, 16, 32] = 8
    height: Literal[8, 16] = 8
    fps: int = Field(default=8, ge=1, le=30)


class GraphPlayRequest(BaseModel):
    device_id: int
    graph: dict
    fps: int = Field(default=8, ge=1, le=30)
    color: str = Field(default="#00ff66", pattern=r"^#[0-9a-fA-F]{6}$")
    brightness: int = Field(default=100, ge=1, le=100)
    rotate: bool | None = None
    use_palette: bool = True


def graph_row_out(row):
    return {"id": row["id"], "name": row["name"], "updated_at": row["updated_at"]}


@router.get("/api/graphs")
async def list_graphs():
    with device_db() as db:
        rows = db.execute("SELECT id,name,updated_at FROM graphs ORDER BY name COLLATE NOCASE").fetchall()
    return [graph_row_out(r) for r in rows]


class GraphCheckRequest(BaseModel):
    graph: dict


class GraphImportItem(BaseModel):
    id: int | None = None
    name: str = "Senza nome"
    graph: dict


class GraphImportRequest(BaseModel):
    graphs: list[GraphImportItem]


def _unique_graph_name(db, name):
    base = (name or "").strip()[:60] or "Senza nome"
    taken = {r["name"].lower() for r in db.execute("SELECT name FROM graphs").fetchall()}
    if base.lower() not in taken:
        return base
    n = 2
    while True:
        suffix = f" ({n})"
        cand = base[:60 - len(suffix)] + suffix
        if cand.lower() not in taken:
            return cand
        n += 1


def _subgraph_nodes(graph):
    return [n for n in graph.get("nodes", []) if isinstance(n, dict) and n.get("type") == "subgraph"]


def _referenced_ids(db, graph):
    ids = set()
    for n in _subgraph_nodes(graph):
        p = n.get("params") or {}
        gid = _pi(p, "graph_id", 0, 10**9, 0)
        row = db.execute("SELECT id FROM graphs WHERE id=?", (gid,)).fetchone() if gid else None
        if row is None and p.get("graph_name"):
            row = db.execute("SELECT id FROM graphs WHERE name=? COLLATE NOCASE", (str(p["graph_name"]),)).fetchone()
        if row:
            ids.add(row["id"])
    return ids


def _check_no_self_inclusion(db, graph_id, graph):
    seen, stack = set(), list(_referenced_ids(db, graph))
    while stack:
        gid = stack.pop()
        if gid == graph_id:
            raise HTTPException(400, "Il grafo incorpora se stesso (direttamente o tramite altri grafi)")
        if gid in seen:
            continue
        seen.add(gid)
        row = db.execute("SELECT data FROM graphs WHERE id=?", (gid,)).fetchone()
        try:
            sub = json.loads(row["data"]) if row else {}
        except ValueError:
            sub = {}
        stack.extend(_referenced_ids(db, sub))


def _remap_subgraph_refs(db, graph, id_map=None):
    warnings = []
    for n in _subgraph_nodes(graph):
        p = n.get("params") or {}
        old, name = p.get("graph_id", 0), p.get("graph_name") or ""
        found = (id_map or {}).get(old) if old else None
        if found is None and name:
            row = db.execute("SELECT id,name FROM graphs WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if row:
                found = (row["id"], row["name"])
        if found is None:
            if old or name:
                warnings.append(f"Il grafo incorporato «{name or '#' + str(old)}» non esiste: scegline un altro nel nodo Grafo")
            p["graph_id"], p["graph_name"] = 0, ""
        else:
            p["graph_id"], p["graph_name"] = found[0], found[1][:60]
    return warnings


@router.post("/api/graphs/validate")
async def validate_graph(req: GraphCheckRequest):
    check_graph(req.graph)
    with device_db() as db:
        warnings = _remap_subgraph_refs(db, req.graph)
    return {"graph": req.graph, "warnings": warnings}


@router.get("/api/graphs/export")
async def export_graphs():
    with device_db() as db:
        rows = db.execute("SELECT id,name,data FROM graphs ORDER BY name COLLATE NOCASE").fetchall()
    return {"format": GRAPH_BUNDLE_FORMAT, "version": GRAPH_FILE_VERSION,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "graphs": [{"id": r["id"], "name": r["name"], "graph": json.loads(r["data"])} for r in rows]}


@router.post("/api/graphs/import")
async def import_graphs(req: GraphImportRequest):
    if not req.graphs or len(req.graphs) > MAX_GRAPH_IMPORT:
        raise HTTPException(400, f"Servono da 1 a {MAX_GRAPH_IMPORT} grafi")
    results, saved, id_map = [], [], {}
    with device_db() as db:
        for item in req.graphs:
            try:
                check_graph(item.graph)
            except HTTPException as e:
                results.append({"name": item.name[:60], "ok": False, "error": e.detail})
                continue
            name = _unique_graph_name(db, item.name)
            cur = db.execute("INSERT INTO graphs(name,data) VALUES(?,?)", (name, json.dumps(item.graph)))
            results.append({"name": name, "ok": True, "id": cur.lastrowid})
            if item.id is not None:
                id_map[item.id] = (cur.lastrowid, name)
            saved.append((cur.lastrowid, item.graph, results[-1]))
        for gid, graph, res in saved:
            if _subgraph_nodes(graph):
                warnings = _remap_subgraph_refs(db, graph, id_map)
                db.execute("UPDATE graphs SET data=? WHERE id=?", (json.dumps(graph), gid))
                if warnings:
                    res["warnings"] = warnings
        db.commit()
    _invalidate_render_cache()
    return {"imported": sum(1 for r in results if r["ok"]), "results": results}


def _squash(values):
    return values[:1] if values and all(v == values[0] for v in values) else values


# ─── Anteprima grafo: render in processo separato + cache ─────────────────────

@router.post("/api/graphs/render")
async def render_graph_preview(req: GraphRenderRequest):
    check_graph(req.graph)
    if core_runtime._render_pool is None:
        raise HTTPException(503, "Render pool non disponibile")
    try:
        result = await _render_in_pool(
            req.graph, req.width, req.height, req.fps,
            max_frames=2000,    # ciclo massimo dimezzato rispetto a play
            max_out=400,        # il client mostra solo i primi 400 frame
            timeout=30.0,
        )
    except PlayError as e:
        raise HTTPException(e.status, e.msg)
    except Exception as exc:
        raise HTTPException(500, f"Errore render: {exc}")

    warnings = await prefetch_animations(req.graph)
    algo_warn = result.get("warnings") or []
    warnings = [w["msg"] for w in algo_warn] + warnings

    return {"total": result["total"], "width": req.width, "height": req.height,
            "frames": ["".join(f"{v:x}" for row in fr for v in row) for fr in result["frames"]],
            "values": {nid: _squash(v) for nid, v in result["values"].items()},
            "warnings": warnings,
            "algo_errors": {w["node"]: w["err"] for w in algo_warn if w.get("node")},
            "palette": get_palette(),
            "cycle_sec": (result["total"] / req.fps) if result["total"] else 0}


@router.post("/api/graph/play")
async def play_graph(req: GraphPlayRequest):
    row = get_device(req.device_id)
    if row is None:
        raise HTTPException(404, "Dispositivo non trovato")
    if not row["api_key"]:
        raise HTTPException(400, "Manca la chiave noise_psk")
    check_graph(req.graph)
    rotate = bool(row["rotate"]) if req.rotate is None else req.rotate
    item = {"type": "graph", "graph": req.graph, "graph_id": 0, "animation_id": 0, "text": "",
            "duration": 86400, "fps": req.fps, "color": req.color, "brightness": req.brightness,
            "rotate": rotate, "use_palette": req.use_palette, "pause_mode": "hold",
            "repeat_x": 0, "enabled": True, "auto_duration": False}
    await stop_device(req.device_id)
    runners[req.device_id] = asyncio.create_task(playlist_runner(req.device_id, [item]))
    return {"ok": True}


@router.get("/api/graphs/{graph_id}")
async def get_graph(graph_id: int):
    with device_db() as db:
        row = db.execute("SELECT * FROM graphs WHERE id=?", (graph_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Grafo non trovato")
    return {**graph_row_out(row), "graph": json.loads(row["data"])}


@router.post("/api/graphs")
async def create_graph(req: GraphIn):
    check_graph(req.graph)
    with device_db() as db:
        cur = db.execute("INSERT INTO graphs(name,data) VALUES(?,?)",
                         (req.name.strip(), json.dumps(req.graph)))
        db.commit()
        row = db.execute("SELECT * FROM graphs WHERE id=?", (cur.lastrowid,)).fetchone()
    _invalidate_render_cache()
    return {**graph_row_out(row), "graph": req.graph}


@router.put("/api/graphs/{graph_id}")
async def update_graph(graph_id: int, req: GraphIn):
    check_graph(req.graph)
    with device_db() as db:
        _check_no_self_inclusion(db, graph_id, req.graph)
        cur = db.execute("UPDATE graphs SET name=?, data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (req.name.strip(), json.dumps(req.graph), graph_id))
        db.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Grafo non trovato")
        row = db.execute("SELECT * FROM graphs WHERE id=?", (graph_id,)).fetchone()
    _invalidate_render_cache()
    return {**graph_row_out(row), "graph": req.graph}


@router.delete("/api/graphs/{graph_id}")
async def delete_graph(graph_id: int):
    with device_db() as db:
        users = []
        for r in db.execute("SELECT id,name,data FROM graphs WHERE id<>?", (graph_id,)).fetchall():
            try:
                g = json.loads(r["data"])
            except ValueError:
                continue
            if graph_id in _referenced_ids(db, g):
                users.append(r["name"])
        if users:
            raise HTTPException(409, "Il grafo è incorporato in: " + ", ".join(users[:5]) + ("…" if len(users) > 5 else ""))
        cur = db.execute("DELETE FROM graphs WHERE id=?", (graph_id,))
        db.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Grafo non trovato")
    _invalidate_render_cache()
    return {"ok": True}


class MessageRequest(BaseModel):
    device: int | str
    text: str = Field(min_length=1, max_length=200)
    duration: int | None = Field(default=None, ge=1, le=3600)
    repeat: int = Field(default=1, ge=1, le=20)
    fps: int | None = Field(default=None, ge=1, le=30)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    brightness: int = Field(default=100, ge=1, le=100)
    rotate: bool | None = None


def find_device(ref):
    with device_db() as db:
        if isinstance(ref, int) or str(ref).strip().isdigit():
            row = db.execute("SELECT * FROM devices WHERE id=?", (int(ref),)).fetchone()
            if row:
                return row
        return db.execute("SELECT * FROM devices WHERE name=? COLLATE NOCASE",
                          (str(ref).strip(),)).fetchone()


@router.post("/api/message")
async def post_message(req: MessageRequest):
    row = find_device(req.device)
    if row is None:
        raise HTTPException(404, f"Board non trovata: {req.device}")
    device = dict(row)
    if not device["api_key"]:
        raise HTTPException(400, "Manca la chiave noise_psk")
    if (int(device["width"]), int(device["height"])) not in FIRMWARE_DIMS:
        raise HTTPException(400, f"Board {device['width']}×{device['height']} non supportata dal firmware")
    text = req.text.strip()
    if not any(text_columns(text)):
        raise HTTPException(400, "Il testo non contiene caratteri visibili")
    did = device["id"]
    queue = message_queues.setdefault(did, [])
    if len(queue) >= MAX_QUEUED_MESSAGES:
        raise HTTPException(429, "Coda piena")
    msg = {"text": text, "duration": req.duration, "repeat": req.repeat,
           "fps": req.fps or int(device.get("text_fps", 10) or 10),
           "color": req.color, "brightness": req.brightness, "rotate": req.rotate}
    queue.append(msg)
    message_events.setdefault(did, asyncio.Event()).set()
    if not runner_alive(did):
        task = message_tasks.get(did)
        if task is None or task.done():
            message_tasks[did] = asyncio.create_task(message_worker(did))
    return {"ok": True, "device": device["name"], "queued": len(queue),
            "duration": message_item(device, msg)["duration"], "fps": msg["fps"]}


class MessageQueueItemRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    duration: int | None = Field(default=None, ge=1, le=3600)
    repeat: int = Field(default=1, ge=1, le=20)
    fps: int = Field(default=10, ge=1, le=30)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    brightness: int = Field(default=100, ge=1, le=100)
    rotate: bool | None = None


@router.get("/api/devices/{device_id}/messages")
async def get_message_queue(device_id: int):
    device = require_device(device_id)
    queue = message_queues.get(device_id, [])
    return {"device": device["name"],
            "showing": message_status.get(device_id, {}).get("showing"),
            "items": [{**msg, "index": i,
                       "duration_resolved": message_item(dict(device), msg)["duration"]}
                      for i, msg in enumerate(queue)],
            "max": MAX_QUEUED_MESSAGES}


@router.put("/api/devices/{device_id}/messages/{index}")
async def update_queued_message(device_id: int, index: int, req: MessageQueueItemRequest):
    device = require_device(device_id)
    queue = message_queues.get(device_id, [])
    if index < 0 or index >= len(queue):
        raise HTTPException(404, "Messaggio in coda non trovato")
    text = req.text.strip()
    if not any(text_columns(text)):
        raise HTTPException(400, "Il testo non contiene caratteri visibili")
    queue[index] = {**req.model_dump(), "text": text}
    return {"ok": True, "item": {**queue[index], "index": index,
             "duration_resolved": message_item(dict(device), queue[index])["duration"]}}


@router.delete("/api/devices/{device_id}/messages/{index}")
async def delete_queued_message(device_id: int, index: int):
    require_device(device_id)
    queue = message_queues.get(device_id, [])
    if index < 0 or index >= len(queue):
        raise HTTPException(404, "Messaggio in coda non trovato")
    removed = queue.pop(index)
    if not queue:
        message_events.setdefault(device_id, asyncio.Event()).clear()
    return {"ok": True, "removed": removed, "remaining": len(queue)}


@router.delete("/api/devices/{device_id}/messages")
async def clear_message_queue(device_id: int):
    require_device(device_id)
    queue = message_queues.get(device_id, [])
    count = len(queue)
    queue.clear()
    message_events.setdefault(device_id, asyncio.Event()).clear()
    return {"ok": True, "cleared": count}


class PlayRequest(BaseModel):
    animation_id: int
    device_id: int | None = None
    host: str = ""
    port: int = Field(default=6053, ge=1, le=65535)
    api_key: str = ""
    fps: int = Field(default=8, ge=1, le=30)
    color: str = "#00ff66"
    brightness: int = Field(default=100, ge=1, le=100)
    rotate: bool = False
    loop: bool = True
    use_palette: bool = True
    repeat_x: int = 0


class StopRequest(BaseModel):
    device_id: int | None = None


@router.post("/api/stop")
async def stop(req: StopRequest | None = None):
    if req is not None and req.device_id is not None:
        await stop_device(req.device_id)
    else:
        await stop_all()
    return {"ok": True}

@router.get("/api/stats")
async def get_stats(format: Literal["json", "text"] = "json"):
    s = _build_stats()
    if format == "text":
        return PlainTextResponse(_format_stats_text(s))
    return s

@router.post("/api/stats/errors/clear")
async def clear_stats_errors():
    _stats["errors"].clear()
    return {"ok": True}


_LOG_RE_RENDER = re.compile(
    r"\[render\]\s+(cache HIT|cache MISS|BYPASS)\s+\(\s*(?P<sec>\d+\.\d+)s\)\s+"
    r"scope=(?P<scope>\S+)\s+(?P<w>\d+)x(?P<h>\d+)@(?P<fps>\d+)fps\s+frames=(?P<frames>\d+)")
_LOG_RE_PLAY = re.compile(
    r"\[play_item\]\s+device=(?P<dev>\S+)\s+kind=(?P<kind>\w+)\s+fps=(?P<fps>\d+)\s+"
    r"frames=(?P<frames>\d+)\s+src=(?P<sw>\d+)x(?P<sh>\d+)\s+dst=(?P<dw>\d+)x(?P<dh>\d+)")


@router.get("/api/logs/stats")
async def get_logs_stats(lines: int = 5000, format: Literal["json", "text"] = "json"):
    p = Path(LOG_FILE)
    if not p.exists():
        raise HTTPException(404, f"File di log non trovato: {LOG_FILE}")
    raw = _tail_lines(LOG_FILE, max(1, min(lines, 100_000)))

    render = {"total": 0, "hit": 0, "miss": 0, "bypass": 0,
              "durations": [], "by_scope": {}, "by_resolution": {}, "by_fps": {}}
    play = {"total": 0, "by_kind": {}, "by_device": {}}

    for line in raw:
        m = _LOG_RE_RENDER.search(line)
        if m:
            tag = m.group(1)
            field = {"cache HIT": "hit", "cache MISS": "miss", "BYPASS": "bypass"}[tag]
            scope = m.group("scope")
            w, h, fps = int(m.group("w")), int(m.group("h")), int(m.group("fps"))
            render["total"] += 1
            render[field] += 1
            render["durations"].append(float(m.group("sec")))
            _bump(render["by_scope"], _scope_type(scope), field)
            _bump(render["by_resolution"], f"{w}x{h}", field)
            _bump(render["by_fps"], fps, field)
            continue
        m = _LOG_RE_PLAY.search(line)
        if m:
            play["total"] += 1
            play["by_kind"][m.group("kind")] = play["by_kind"].get(m.group("kind"), 0) + 1
            play["by_device"][m.group("dev")] = play["by_device"].get(m.group("dev"), 0) + 1

    durations = sorted(render["durations"])
    report = {
        "log_file": LOG_FILE,
        "lines_analyzed": len(raw),
        "render": {
            "total": render["total"],
            "hit": render["hit"], "miss": render["miss"], "bypass": render["bypass"],
            "hit_ratio": round(render["hit"] / render["total"], 3) if render["total"] else None,
            "duration_sec": {
                "count": len(durations),
                "min": round(durations[0], 3) if durations else None,
                "max": round(durations[-1], 3) if durations else None,
                "avg": round(sum(durations) / len(durations), 3) if durations else None,
                "p50": round(_percentile(durations, 0.50), 3) if durations else None,
                "p95": round(_percentile(durations, 0.95), 3) if durations else None,
            },
            "by_scope": render["by_scope"],
            "by_resolution": render["by_resolution"],
            "by_fps": render["by_fps"],
        },
        "play_item": {
            "total": play["total"],
            "by_kind": play["by_kind"],
            "by_device": play["by_device"],
        },
    }
    if format == "text":
        return PlainTextResponse(_format_stats_text(report))
    return report

@router.post("/api/play")
async def play(req: PlayRequest):
    key = req.device_id if req.device_id is not None else "adhoc"
    if req.device_id is not None:
        row = get_device(req.device_id)
        if row is None:
            raise HTTPException(404, "Dispositivo salvato non trovato")
        device = dict(row)
    else:
        device = {"id": "adhoc", "host": req.host, "port": req.port,
                  "api_key": req.api_key.strip(), "color": 0, "width": 8, "height": 8}
    device["host"] = checked_host(device["host"])
    if not device["api_key"]:
        raise HTTPException(400, "Manca la chiave noise_psk")
    item = {"type": "animation", "animation_id": req.animation_id, "fps": req.fps,
            "color": req.color, "brightness": req.brightness, "rotate": req.rotate,
            "use_palette": req.use_palette, "repeat_x": req.repeat_x, "enabled": True,
            "graph_id": 0, "text": "", "pause_mode": "hold", "auto_duration": False,
            "duration": 0}
    try:
        rgb_from(req.color, req.brightness)
        await stop_device(key)
        link, info = await play_item(device, item, None, req.loop)
    except PlayError as e:
        raise HTTPException(e.status, e.msg)
    if link is not None:
        links[key] = link
    return {"ok": True, "frames": info["frames"], "width": info["width"],
            "height": info["height"], "fps": req.fps, "rgb": info["rgb"],
            "rotated": bool(req.rotate or device.get("rotate", False))}
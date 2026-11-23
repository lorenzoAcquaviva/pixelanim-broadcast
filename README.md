# PixelAnim Broadcast

Scheduling and real-time broadcasting for **ESPHome LED boards** (8×8, 16×16 and 32×8 matrices, monochrome or full-colour).

Broadcast does not create animations itself or store them in the boards' RAM: it **reads them from PixelAnim**, arranges them into a *playlist* (a loop of scheduled items), and **streams them** to the boards, together with text, a clock, pauses and animations generated using **node graphs**.

- Per-board playlists: animations, text, clock, pauses and graphs
- Urgent messages from other systems (n8n, Home Assistant, scripts), with priority over the playlist
- Node editor for algorithmic animations (time, randomness, text width, mathematics, etc.)
- Monitor showing what each board is displaying
- Automatic playlist start when the application starts
- JSON import/export for graphs

[🇮🇹 Leggi in italiano](README.it.md)

---

## How it works

```
┌────────────┐  HTTP: animation and frame list   ┌────────────────────┐
│  PixelAnim │◄───────────────────────────────────│                    │
│  (:9900)   │                                    │  PixelAnim         │
│            │◄──── WebSocket /api/esp/stream ────│  Broadcast (:9910) │
└─────┬──────┘                                    │                    │
      │ Native ESPHome API (noise, :6053)         │  SQLite (/data)    │
      ▼                                           └─────────▲──────────┘
┌────────────┐                                              │  browser / n8n / curl
│ LED board  │                                              │
└────────────┘                                          UI + REST API
```

Broadcast asks PixelAnim to send frames to the boards through its `/api/esp/stream` WebSocket. Boards are configured in Broadcast (IP address or hostname, port, `noise_psk` key, dimensions, colour and rotation).

---

## Requirements

- Python 3.12+ (uses `zoneinfo`, Pydantic v2 and `math.lcm`)
- A **PixelAnim** instance reachable over the network
- One or more ESPHome boards running compatible firmware (streaming services) and configured with the `noise_psk` key
- Firmware-supported dimensions: **8×8, 32×8, 16×16** (see `FIRMWARE_DIMS` in `core.py`)

Python dependencies: `fastapi`, `uvicorn`, `httpx`, `websockets`, `pydantic`, `tzdata`.

---

## Project structure

```
.
├── main.py              # FastAPI application entry point
├── api.py               # REST endpoints
├── core.py              # renderer, graphs and playlist management
├── database.py          # SQLite database access
├── static/
│   ├── index.html       # web interface
│   ├── app.js           # frontend logic
│   ├── i18n.js          # language management
│   ├── i18n/
│   │   ├── it.json      # Italian
│   │   └── en.json      # English
│   ├── style.css
│   └── favicon.svg
├── data/                # persistent data, excluded from Git
├── Dockerfile
├── compose.yml
└── requirements.txt
```

`main.py` starts FastAPI and serves the `static/` directory. No frontend build step is required.

---

## Installation

### Docker (recommended)

Clone the repository, create the local configuration file and start the service:

```bash
git clone https://github.com/OWNER/pixelanim-broadcast.git
cd pixelanim-broadcast
cp .env.example .env
docker compose up -d --build
```

The service exposes port `9910`. In `.env`, set `PIXELANIM_URL` to the HTTP address of the PixelAnim backend. If `PIXELANIM_WS` is left empty (the default), the WebSocket address is derived automatically: `http://` becomes `ws://` and `https://` becomes `wss://`, while keeping the `/api/esp/stream` path. Set `PIXELANIM_WS` only if the WebSocket uses a different host or path.

Example with the backend running on another LAN server:

```dotenv
PIXELANIM_URL=http://192.168.1.50:9900
PIXELANIM_WS=
```

The backend must be reachable and resolvable **from inside the Broadcast container**, not just from the browser or the computer used to open the page.

Persistent data (database and logs) is stored in `./data`, which must not be published in the repository.

#### Running behind Traefik or another reverse proxy

Do not edit the main Compose file to add personal domains or middleware. Create a local override, excluded from Git:

```bash
cp compose.local.example.yml compose.local.yml
```

Customize `compose.local.yml`, then start the service using both files:

```bash
docker compose -f compose.yml -f compose.local.yml up -d --build
```

`compose.local.yml` is excluded by `.gitignore`: it should contain only settings specific to your installation.

### Without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" httpx websockets pydantic tzdata
export DEVICES_DB=./data/devices.db
export PIXELANIM_URL=http://localhost:9900
uvicorn main:app --host 0.0.0.0 --port 9910
```

Open `http://localhost:9910`.

---

## Configuration

All settings are controlled through environment variables:

| Variable | Default | Description |
|---|---|---|
| `PIXELANIM_URL` | Required | HTTP address of the PixelAnim backend, reachable from the Broadcast container |
| `PIXELANIM_WS` | derived from `PIXELANIM_URL` + `/api/esp/stream` | Optional WebSocket address override |
| `DEVICES_DB` | `/data/devices.db` | SQLite database path |
| `TZ` | `Europe/Rome` | Time zone used by the clock |
| `BROADCAST_PORT` | `9910` | Port published on the Docker host |
| `RENDER_WORKERS` | `2` | Number of rendering worker processes |
| `RENDER_MP_METHOD` | `fork` | Multiprocessing method |
| `RENDER_CACHE_DISABLED` | `0` | Set to `1` to disable the render cache |
| `RENDER_RESULT_MAX` | `8` | Maximum number of results in the cache |
| `RENDER_RESULT_MAX_FRAMES` | `24000` | Total frame limit in the cache |
| `PALETTE_CACHE_TTL` | `30` | Palette cache lifetime, in seconds |
| `LOG_FILE` | `/data/pixelanim.log` | Log file path |

The database stores boards, playlists, graphs and settings (palette). Mount `/data` as a volume to preserve them.

> **Security.** The application **does not provide authentication** and stores the boards' `noise_psk` keys in the database. Keep it on a trusted network (LAN/VPN) or behind a proxy with an allowlist, and protect the `/data` directory.

---

## Usage

The page has four sections in the top navigation:

| Section | Purpose |
|---|---|
| **⚙ Settings** | Configure boards (name, IP address or hostname, port, `noise_psk`, dimensions, colour, 180° rotation) and the 16-colour palette |
| **☰ Playlist** | Build the item loop for each board, with start/stop and automatic start |
| **◈ Nodes** | Graph editor for algorithmic animations |
| **📺 Monitor** | Preview what each board is displaying |

From the main page you can also play a PixelAnim animation directly on a board (colour, brightness, FPS, loop and horizontal repeat).

### Boards

- Address: an IP address **or** hostname (e.g. `blu.fritz.box`).
- Colour: colour boards use the **16-colour palette** (editable in Settings); monochrome boards use a single colour plus brightness.
- 180° rotation: for boards mounted upside down.

Default palette: `#000000 #ff6b2b #ff2d2d #ffd21f #39ff14 #00e5c8 #2f8bff #3a3aff #a855f7 #ff3aa8 #ffffff #ff9d2e #9dff2e #8a5a2b #7a7a7a #1d4d2b`.

### Playlist

Each board has its own playlist (up to **100 items**), played in a loop. Available item types:

| Type | Content |
|---|---|
| **Animation** | A PixelAnim animation (including horizontal repeat) |
| **Text** | Static or scrolling text (max. 120 characters) |
| **Clock** | Current time |
| **Pause** | Stops the animation for the configured duration (see below) |
| **Graph** | A saved node graph |

Common parameters: duration (1 s – 24 h), FPS (1–30), colour, brightness (1–100), rotation, palette usage, automatic duration based on content, and the **Enabled** flag to temporarily skip an item without deleting it.

**Pause modes:**

- `hold` – hold the last frame
- `blank` – turn the display off
- `release` – release the board (close the connection)

Other features:

- **Automatic start**: configure each board to start its playlist when Broadcast starts.
- **Import from another board**: copy a board's playlist to another board (replace or append).

### Urgent messages (n8n, Home Assistant, scripts)

`POST /api/message` queues scrolling text on a board with **priority over the playlist**: the message is played as soon as possible, then the playlist resumes. The queue holds up to 20 messages per board (otherwise HTTP 429 is returned).

```bash
curl -X POST http://localhost:9910/api/message \
  -H 'Content-Type: application/json' \
  -d '{"device": "Living room", "text": "Doorbell!", "repeat": 2, "color": "#ff2d2d"}'
```

| Field | Default | Notes |
|---|---|---|
| `device` | – | Board **id** or **name** (case-insensitive) |
| `text` | – | 1–200 characters |
| `duration` | calculated | Seconds (1–3600); if omitted, depends on text length, `repeat` and `fps` |
| `repeat` | `1` | Number of scroll repetitions (1–20) |
| `fps` | `10` | 1–30 |
| `color` | `#ffffff` | Hexadecimal `#rrggbb` |
| `brightness` | `100` | 1–100 |
| `rotate` | board setting | `true`/`false` |

In n8n, use an **HTTP Request** node (POST, JSON body) targeting this endpoint.

---

## Node editor

With **◈ Nodes**, you can create animations generated by a graph. Each graph is saved under its own name and can then be used as a **Graph** item in a playlist. A fixed preview is shown on the right, with board size and FPS selection.

The **Viewport** node takes a saved Graph as its source and crops a window matching the current output size. Its **X** and **Y** coordinates can be bound to value nodes (Time, Random, Math or Algorithmic outputs), so a larger graph (for example 16×16 or 32×8) can scroll dynamically across a smaller board.

### Navigation and layout

- Drag the background to pan; use the **mouse wheel** to zoom (the dotted background helps indicate scale). Canvas size: 6000×4000.
- **⌖ Frame all** displays all nodes.
- Each node can be **collapsed** (▾/▸), **renamed** (double-click its header or use ✎), **duplicated** (⧉) and **deleted** (✕). **Collapse/Expand** applies to all nodes.

### Connections

| Port | Colour | What it does |
|---|---|---|
| Node output → graph **Output** | green | The node is drawn. Layer order (bottom to top) is changed in the list inside the Output node |
| Output → modifier input | orange | The modifier operates on that node |
| Value node → a parameter's **blue port** | dashed blue | The parameter follows the value (read-only field showing the live value) |

Double-click a port to disconnect it. Connections that create **loops** are rejected.

### Drawing nodes

| Node | Main parameters |
|---|---|
| **Pixel** | X, Y, repetitions (N.x/N.y, Δx/Δy), colour |
| **Rectangle** | X, Y, width, height, filled, repetitions, colour |
| **Gradient** | X, Y, width, height, direction, two colours, repetitions |
| **8×8 Bitmap** | X, Y, scale (1–16), 8×8 drawing, colour |
| **Circle** | X, Y, radius, filled, colour |
| **Text** | text, static/scrolling, alignment, X, Y, colour |
| **Time** | HH:MM, hours, minutes, seconds, HH:MM:SS or alternating hours/minutes; 5×7 or 3×5 font; blinking colon; X, Y, colour |
| **Graph** | Embeds **another saved graph** (see below): graph, size, X, Y, colours or monochrome |
| **Animation** | Inserts a **PixelAnim animation** (see below): animation, X, Y, colours or monochrome, slowdown |

### Embedding a graph inside another graph

The **Graph** node inserts an already-saved graph as if it were an animated image, which can be positioned wherever you like. For example, on a 32×8 board you can place an 8×8 graph on the left and scrolling text beside it.

- **Saved graph**: selects the graph to embed (the graph currently being edited is not listed).
- **Graph size**: the size at which it is drawn (8×8, 16×8, 32×8, 8×16, 16×16, 32×16). Choose the size it was designed for.
- **X / Y**: top-left corner; these have **blue ports**, so they can be controlled by Time, Random, Width or Math nodes. Anything outside the board is clipped.
- **Colours**: *Graph colours* preserves the embedded graph's palette; *Monochrome* colours every lit pixel with the **colour selected** in the node.
- Runs at the same FPS as the graph containing it and **loops**: the containing graph's cycle duration accounts for the embedded graph's duration. Like other drawing nodes, it can be combined with modifiers (Move, Rotate, Blink, etc.), respects the FROM/TO interval and can be measured by the **Width** node (*Lit pixels* = lit pixels in the frame, *Bounding box* = width of the selected size).
- Uses the **saved version** of the embedded graph: save it after editing to see the changes. If the embedded graph contains a clock, it continues to update in the playlist.
- Up to **3 nesting levels** are supported. A graph cannot embed itself, even indirectly through other graphs; saving is rejected. A graph embedded in other graphs cannot be deleted while in use (the error lists which graphs reference it).

### PixelAnim animation inside a graph

The **Animation** node inserts a PixelAnim animation into the graph, for example to place it on the left of a 32×8 board and scroll text on the right.

- **PixelAnim animation**: the list comes from PixelAnim. The animation's dimensions and frames are used.
- **X / Y**: top-left corner, with **blue ports** (can be bound). Anything outside the board is clipped.
- **Colours**: *Palette colours* preserves the animation's colour indexes (monochrome animations use palette colour 1); *Monochrome* colours every lit pixel with the **colour selected** in the node.
- **Slow down ×**: each animation frame lasts N graph frames (1–60). By default, one animation frame advances for every graph frame, in a loop; the graph's cycle duration accounts for the animation's duration.
- Like other drawing nodes, it can be combined with modifiers, respects the FROM/TO interval and can be measured by the **Width** node. It also works inside a graph embedded through the **Graph** node.
- Frames are downloaded from PixelAnim and kept in memory for **60 seconds**: a change made in PixelAnim appears within one minute. If PixelAnim does not respond, the last copy is used; if no copy is available, the node draws nothing and the preview shows a warning (⚠), for example "Animation #99 not found in PixelAnim".
- Limits: at most 1000 frames and 64×64 pixels per animation.
- In import/export files, the node stores the animation ID and name: files must be imported into an installation connected to the **same PixelAnim instance**.

### Modifiers

| Node | Effect |
|---|---|
| **Repeats** | Replicates the input on a grid, with starting colour and colour step |
| **Combine** | Union, subtraction, intersection or XOR of two inputs (A and B) |
| **Colourize** | Changes colour |
| **Move** | Continuous or bouncing movement, with speed and period per axis |
| **Rotate** | Continuous or pendulum rotation around a centre |
| **Scale** | Scales up/down around a centre |
| **Mirror** | Horizontal and/or vertical mirroring |
| **Blink** | On/off frames and phase |
| **Trail** | Echoes the image in previous frames |

### Value nodes (blue ports)

| Node | Colour | Output |
|---|---|---|
| **Time** | purple | Value that changes over time: increasing, forward/backward or sine wave; period, min, max, step |
| **Random** | yellow | Random value between min and max, changes every N frames; with seed and step |
| **Width** | light blue | Measures the pixel width of its input (text, bitmap or any node) |
| **Math** | green | A + B, A − B, A × B, A ÷ B; A and B can be numbers or connected to other value nodes |

**Width.** Text, Time and Bitmap are measured from their content (even when wider than the board, including scrolling mode). *Colourize* and *Blink* are transparent. For other nodes, the bounding box of lit pixels on the board is used. With **Measure**, choose *Lit pixels* (from the first to the last lit pixel) or *Bounding box* (including empty columns; bitmap = 8 × scale). The **Offset** is added to the result. Outside the measured node's FROM/TO interval, width is 0.

**Math.** Division by zero returns 0. The result can be decimal; when driving a pixel parameter it is rounded (0.5 rounds up).

Each node also has a **FROM/TO (seconds)** interval to limit its activity over time (outside the interval it does not draw, or returns 0 for a value node). A graph can contain up to **100 nodes**.

### Examples

**Two text elements in sequence.** Text A → **Width** (Offset = 2) → text B's blue **X** port (both left-aligned): B always starts immediately after A, regardless of A's length.

**Underline.** Text → **Width** → a **Rectangle** node's blue **Width** port, positioned one pixel below the text.

**Oscillating movement.** **Time** (forward/backward) → a node's blue **X** port: it moves from one side to the other.

**Half the width.** Width → **Math** (A ÷ B, B = 2) → blue **X** port: centres an element relative to the measured text.

### Importing and exporting graphs

In the toolbar of the Nodes view:

- **⬇ Export**: downloads the open graph (even if unsaved) as `name.pixelanim-graph.json`.
- **⬇ All**: downloads all saved graphs in a single backup file.
- **⬆ Import**: uploads a file.
  - *Single file*: the graph opens in the editor as new and **unsaved**; review it and click **Save**. Also accepts JSON containing only `nodes` and `links`.
  - *Backup*: asks for confirmation and saves all graphs; existing names become "Name (2)", "Name (3)", etc. Invalid graphs are discarded and reported. **Graph** nodes are relinked to imported graphs (by file ID, otherwise by name); if an embedded graph is missing, the import reports it and the node remains without a graph.

File formats:

```jsonc
// single graph
{ "format": "pixelanim-graph", "version": 1, "name": "My graph",
  "graph": { "nodes": [ … ], "links": [ … ], "out": { "x": 620, "y": 30 } } }

// backup
{ "format": "pixelanim-graphs", "version": 1,
  "graphs": [ { "id": 1, "name": "…", "graph": { … } } ] }
```

Each node is `{ "id", "type", "x", "y", "params": { … } }` (with optional `name` and `collapsed`); each connection is `{ "from", "to", "to_port" }`, with `to: "out"` for the output. Files are validated by the server: node types, duplicate IDs, loops and invalid positions are rejected or corrected.

---

## Languages

The interface supports Italian and English. The language is initially detected from the browser preferences; users can change it under **Settings → Language**. The selection is stored in the browser.

Translations are located in `static/i18n/it.json` and `static/i18n/en.json`. Italian is the source language: whenever new interface text is added, the corresponding entry must be added to both files. For dynamic messages, use `t('Original Italian text')` so that text generated by JavaScript is translated as well.

## Development and local configuration

Application code must remain the same in public and private installations. Environment-specific differences belong in `.env` and `compose.local.yml`, both excluded from Git. To update the local installation after publishing changes: `git pull`, then `docker compose -f compose.yml -f compose.local.yml up -d --build`. Backing up the `data` directory before updating is recommended.

---

## REST API

FastAPI's interactive documentation is available at `/docs`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/animations` | Animation list (from PixelAnim) |
| GET | `/api/animations/{id}/frames` | Frames for an animation |
| GET / PUT | `/api/settings` | 16-colour palette |
| GET / POST | `/api/devices` | Board list / creation |
| PUT / DELETE | `/api/devices/{id}` | Edit / delete a board |
| PUT | `/api/devices/{id}/autostart` | Automatic playlist start |
| GET / PUT | `/api/devices/{id}/playlist` | Read / save a playlist |
| POST | `/api/devices/{id}/playlist/import` | Copy a playlist from another board (`replace` or `append`) |
| POST | `/api/devices/{id}/playlist/start` · `/stop` | Start / stop a playlist |
| GET | `/api/playlists/status` | Status of running playlists |
| GET | `/api/devices/{id}/monitor` | Last frame displayed by a board |
| POST | `/api/play` · `/api/stop` | Play an animation / stop one or all boards |
| POST | `/api/message` | Urgent message (see above) |
| GET / POST | `/api/graphs` | Graph list / creation |
| GET / PUT / DELETE | `/api/graphs/{id}` | Read / edit / delete a graph |
| POST | `/api/graphs/render` | Preview: returns a graph's frames |
| POST | `/api/graphs/validate` | Validate and normalize a graph without saving it |
| GET | `/api/graphs/export` | Backup of all graphs |
| POST | `/api/graphs/import` | Import multiple graphs (max. 200 per request) |
| POST | `/api/graph/play` | Play a graph on a board |

---

## Limits

- Board dimensions accepted in Settings: width 8/16/32, height 8/16. The firmware correctly draws only 8×8, 32×8 and 16×16.
- Playlist: max. 100 items. Graph: max. 100 nodes, 200 KB and up to 3 levels of embedded graphs. Message queue: 20 per board.
- Graph preview displays at most the first 400 frames; the rendering cycle is limited to 4000 frames.
- PixelAnim animations inside graphs: max. 1000 frames and 64×64 pixels each.
- The intended use case is one user at a time: there are no accounts or permissions.

---

## Development

- The backend is split into `main.py`, `api.py`, `core.py` and `database.py`; the frontend is a build-free page (`static/index.html`) with logic in `static/app.js`.
- To add a board dimension supported by the firmware, update `FIRMWARE_DIMS` in `core.py`.
- Adding a node type requires two changes: the server-side definition (`GRAPH_*_TYPES`, validation in `check_graph` and rendering) and the entry in the frontend's `NG_TYPES`, plus the button in the **Add** menu.
- The node canvas has the same dimensions in the frontend (`NG_CW`, `NG_CH`) and backend (`GRAPH_CANVAS_W`, `GRAPH_CANVAS_H`): if you change them, update both.
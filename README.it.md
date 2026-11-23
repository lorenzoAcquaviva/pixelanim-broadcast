# PixelAnim Broadcast

Palinsesto e trasmissione in tempo reale per le **board LED ESPHome** (matrici 8×8, 16×16, 32×8, monocromatiche o a colori).

Broadcast non disegna le animazioni da solo e non le salva nella RAM delle board: le **legge da PixelAnim**, le monta in un *palinsesto* (loop di elementi programmati) e le **trasmette in streaming** alle board, insieme a testo, orologio, pause e animazioni generate da **grafi a nodi**.

- Palinsesto per board: animazioni, testo, orologio, pause, grafi
- Messaggi urgenti da altri sistemi (n8n, Home Assistant, script) con priorità sul palinsesto
- Editor a nodi per animazioni algoritmiche (tempo, casualità, larghezza del testo, matematica…)
- Monitor per vedere cosa sta mostrando ogni board
- Avvio automatico del palinsesto all'accensione
- Importazione/esportazione dei grafi in JSON

---

## Come funziona

```
┌────────────┐  HTTP: elenco animazioni e frame   ┌────────────────────┐
│  PixelAnim │◄───────────────────────────────────│                    │
│  (:9900)   │                                    │  PixelAnim         │
│            │◄──── WebSocket /api/esp/stream ────│  Broadcast (:9910) │
└─────┬──────┘                                    │                    │
      │ API nativa ESPHome (noise, :6053)         │  SQLite (/data)    │
      ▼                                           └─────────▲──────────┘
┌────────────┐                                              │  browser / n8n / curl
│ Board LED  │                                              │
└────────────┘                                          UI + API REST
```

Broadcast chiede a PixelAnim di inviare i frame alle board attraverso il suo WebSocket `/api/esp/stream`; le board sono definite in Broadcast (IP o nome host, porta, chiave `noise_psk`, dimensioni, colore, rotazione).

---

## Requisiti

- Python 3.12+ (usa `zoneinfo`, `pydantic` v2 e `math.lcm`)
- Un'istanza di **PixelAnim** raggiungibile via rete
- Una o più board ESPHome con il firmware compatibile (servizi di streaming) e la chiave `noise_psk`
- Dimensioni supportate dal firmware: **8×8, 32×8, 16×16** (vedi `FIRMWARE_DIMS` in `core.py`)

Dipendenze Python: `fastapi`, `uvicorn`, `httpx`, `websockets`, `pydantic`, `tzdata`.

---

## Struttura del progetto

```
.
├── main.py              # avvio dell'applicazione FastAPI
├── api.py               # endpoint REST
├── core.py              # renderer, grafi e gestione palinsesti
├── database.py          # accesso al database SQLite
├── static/
│   ├── index.html       # interfaccia web
│   ├── app.js           # logica frontend
│   ├── i18n.js          # gestione lingue
│   ├── i18n/
│   │   ├── it.json      # italiano
│   │   └── en.json      # inglese
│   ├── style.css
│   └── favicon.svg
├── data/                # dati persistenti, esclusi da Git
├── Dockerfile
├── compose.yml
└── requirements.txt
```

`main.py` avvia FastAPI e monta la cartella `static/`. Non è necessaria una fase di build del frontend.

---

## Installazione

### Docker (consigliato)

Clona il repository, crea il file di configurazione locale e avvia il servizio:

```bash
git clone https://github.com/OWNER/pixelanim-broadcast.git
cd pixelanim-broadcast
cp .env.example .env
docker compose up -d --build
```

Il servizio espone la porta `9910`. In `.env` imposta `PIXELANIM_URL` con l'indirizzo HTTP del backend PixelAnim. Se lasci `PIXELANIM_WS` vuota (impostazione predefinita), l'indirizzo WebSocket viene ricavato automaticamente: `http://` diventa `ws://` e `https://` diventa `wss://`, mantenendo il percorso `/api/esp/stream`. Imposta `PIXELANIM_WS` solo se il WebSocket usa un host o un percorso diverso.

Esempio di backend su un altro server della LAN:

```dotenv
PIXELANIM_URL=http://192.168.1.50:9900
PIXELANIM_WS=
```

Il backend deve essere raggiungibile e risolvibile **dal container Broadcast**, non soltanto dal browser o dal computer da cui apri la pagina.

I dati persistenti (database e log) sono salvati in `./data`, che non deve essere pubblicata nel repository.

#### Installazione dietro Traefik o altro reverse proxy

Non modificare il Compose principale per inserire domini e middleware personali. Crea un override locale, escluso da Git:

```bash
cp compose.local.example.yml compose.local.yml
```

Personalizza `compose.local.yml`, poi avvia con entrambi i file:

```bash
docker compose -f compose.yml -f compose.local.yml up -d --build
```

`compose.local.yml` è escluso da `.gitignore`: contiene esclusivamente le personalizzazioni dell'installazione.

### Senza Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" httpx websockets pydantic tzdata
export DEVICES_DB=./data/devices.db
export PIXELANIM_URL=http://localhost:9900
uvicorn main:app --host 0.0.0.0 --port 9910
```

Apri `http://localhost:9910`.

---

## Configurazione

Tutto si regola con variabili d'ambiente:

| Variabile | Default | Descrizione |
|---|---|---|
| `PIXELANIM_URL` | Obbligatoria | Indirizzo HTTP del backend PixelAnim, raggiungibile dal container Broadcast |
| `PIXELANIM_WS` | derivata da `PIXELANIM_URL` + `/api/esp/stream` | Override facoltativo dell'indirizzo WebSocket |
| `DEVICES_DB` | `/data/devices.db` | Percorso del database SQLite |
| `TZ` | `Europe/Rome` | Fuso orario usato dall'orologio |
| `BROADCAST_PORT` | `9910` | Porta pubblicata sull'host Docker |
| `RENDER_WORKERS` | `2` | Numero di processi worker per il rendering |
| `RENDER_MP_METHOD` | `fork` | Metodo multiprocessing |
| `RENDER_CACHE_DISABLED` | `0` | Imposta `1` per disattivare la cache dei render |
| `RENDER_RESULT_MAX` | `8` | Numero massimo di risultati nella cache |
| `RENDER_RESULT_MAX_FRAMES` | `24000` | Limite complessivo dei frame in cache |
| `PALETTE_CACHE_TTL` | `30` | Durata in secondi della cache delle palette |
| `LOG_FILE` | `/data/pixelanim.log` | Percorso del file di log |

Il database contiene board, palinsesti, grafi e impostazioni (palette). Montare `/data` come volume per non perderli.

> **Sicurezza.** L'app **non ha autenticazione** e salva nel database le chiavi `noise_psk` delle board. Tienila su una rete fidata (LAN/VPN) o dietro un proxy con whitelist, e proteggi la cartella `/data`.

---

## Uso

La pagina ha quattro sezioni, in alto:

| Sezione | A cosa serve |
|---|---|
| **⚙ Impostazioni** | Definisci le board (nome, IP o nome host, porta, `noise_psk`, dimensioni, colore, rotazione 180°) e la palette a 16 colori |
| **☰ Palinsesto** | Costruisci il loop di elementi per ogni board, con avvio/arresto e avvio automatico |
| **◈ Nodi** | Editor dei grafi per le animazioni algoritmiche |
| **📺 Monitor** | Anteprima di ciò che sta mostrando ogni board |

Dalla pagina principale puoi anche riprodurre direttamente un'animazione di PixelAnim su una board (colore, luminosità, FPS, loop, ripetizione orizzontale).

### Board

- Indirizzo: IP **oppure** nome host (es. `blu.fritz.box`).
- Colore: le board a colori usano la **palette a 16 colori** (modificabile in Impostazioni); quelle monocromatiche un solo colore + luminosità.
- Rotazione 180°: per board montate al contrario.

Palette predefinita: `#000000 #ff6b2b #ff2d2d #ffd21f #39ff14 #00e5c8 #2f8bff #3a3aff #a855f7 #ff3aa8 #ffffff #ff9d2e #9dff2e #8a5a2b #7a7a7a #1d4d2b`.

### Palinsesto

Ogni board ha il suo palinsesto (fino a **100 elementi**), eseguito in loop. Tipi di elemento:

| Tipo | Contenuto |
|---|---|
| **Animazione** | Un'animazione di PixelAnim (anche con ripetizione orizzontale) |
| **Testo** | Testo fisso o scorrevole (max 120 caratteri) |
| **Orologio** | Ora corrente |
| **Pausa** | Ferma l'animazione per il tempo impostato (vedi sotto) |
| **Grafo** | Un grafo a nodi salvato |

Parametri comuni: durata (1 s – 24 h), FPS (1–30), colore, luminosità (1–100), rotazione, uso della palette, durata automatica in base al contenuto e il flag **Attivo** per saltare temporaneamente un elemento senza cancellarlo.

**Modalità di pausa:**

- `hold` – blocca l'ultimo frame
- `blank` – schermo spento
- `release` – rilascia la board (chiude il collegamento)

Altre funzioni:

- **Avvio automatico**: per ogni board puoi far partire il palinsesto quando Broadcast si avvia.
- **Importa da un'altra board**: copia il palinsesto di una board su un'altra (sostituendo o aggiungendo).

### Messaggi urgenti (n8n, Home Assistant, script)

`POST /api/message` accoda un testo scorrevole su una board con **priorità sul palinsesto**: appena possibile il messaggio passa davanti, poi il palinsesto riprende. La coda tiene al massimo 20 messaggi per board (oltre: HTTP 429).

```bash
curl -X POST http://localhost:9910/api/message \
  -H 'Content-Type: application/json' \
  -d '{"device": "Salotto", "text": "Citofono!", "repeat": 2, "color": "#ff2d2d"}'
```

| Campo | Default | Note |
|---|---|---|
| `device` | – | **id** oppure **nome** della board (senza distinzione di maiuscole) |
| `text` | – | 1–200 caratteri |
| `duration` | calcolata | secondi (1–3600); se omessa dipende da lunghezza del testo, `repeat` e `fps` |
| `repeat` | `1` | quante volte scorre (1–20) |
| `fps` | `10` | 1–30 |
| `color` | `#ffffff` | esadecimale `#rrggbb` |
| `brightness` | `100` | 1–100 |
| `rotate` | impostazione della board | `true`/`false` |

In n8n basta un nodo **HTTP Request** (POST, body JSON) verso questo indirizzo.

---

## Editor a nodi

Con **◈ Nodi** crei animazioni generate da un grafo. Ogni grafo si salva col suo nome e poi si usa come elemento di tipo **Grafo** nel palinsesto. A destra c'è l'anteprima fissa, con scelta di dimensione della board e FPS.

Il nodo **Viewport** riceve un Grafo salvato e ne ritaglia una finestra delle dimensioni dell'uscita corrente. Le coordinate **X** e **Y** possono essere collegate ai nodi valore (Tempo, Casuale, Matematica o uscite Algoritmico), così un grafo più grande (per esempio 16×16 o 32×8) può scorrere dinamicamente su una board più piccola.

### Muoversi e organizzare

- Trascina lo sfondo per spostarti, **rotella** per lo zoom (sfondo a punti per capire la scala). Canvas 6000×4000.
- **⌖ Inquadra** mostra tutti i nodi.
- Ogni nodo si può **comprimere** (▾/▸), **rinominare** (doppio clic sull'intestazione o ✎), **duplicare** (⧉) ed **eliminare** (✕). **Comprimi/Espandi** agisce su tutti i nodi.

### Collegamenti

| Porta | Colore | Cosa fa |
|---|---|---|
| Uscita del nodo → **Uscita** del grafo | verde | Il nodo viene disegnato. L'ordine degli strati (dal fondo alla cima) si cambia dalla lista nel nodo Uscita |
| Uscita → ingresso di un modificatore | arancione | Il modificatore lavora su quel nodo |
| Nodo valore → **porta blu** di un parametro | blu tratteggiato | Il parametro segue il valore (campo in sola lettura, mostra il valore live) |

Doppio clic su una porta per scollegarla. I collegamenti che creano **anelli** sono rifiutati.

### Nodi di disegno

| Nodo | Parametri principali |
|---|---|
| **Pixel** | X, Y, ripetizioni (N.x/N.y, Δx/Δy), colore |
| **Rettangolo** | X, Y, Largh., Alt., pieno, ripetizioni, colore |
| **Gradiente** | X, Y, Largh., Alt., direzione, due colori, ripetizioni |
| **Bitmap 8×8** | X, Y, scala (1–16), disegno 8×8, colore |
| **Cerchio** | X, Y, raggio, pieno, colore |
| **Testo** | testo, fisso/scorrevole, allineamento, X, Y, colore |
| **Ora** | HH:MM, ore, minuti, secondi, HH:MM:SS o ore/minuti alternati; font 5×7 o 3×5; due punti lampeggianti; X, Y, colore |
| **Grafo** | Incorpora un **altro grafo salvato** (vedi sotto): grafo, dimensione, X, Y, colori o monocromatico |
| **Animazione** | Inserisce un'**animazione di PixelAnim** (vedi sotto): animazione, X, Y, colori o monocromatico, rallentamento |

### Grafo dentro un grafo

Il nodo **Grafo** inserisce un grafo già salvato come se fosse un'immagine animata, e lo posiziona dove vuoi. Esempio: su una board 32×8 un grafo 8×8 a sinistra e, accanto, un testo scorrevole.

- **Grafo salvato**: sceglie il grafo da incorporare (il grafo che stai modificando non compare nell'elenco).
- **Dimensione del grafo**: la grandezza con cui viene disegnato (8×8, 16×8, 32×8, 8×16, 16×16, 32×16). Scegli quella per cui l'avevi progettato.
- **X / Y**: angolo in alto a sinistra; hanno le **porte blu**, quindi si possono muovere con Tempo, Casuale, Larghezza o Matematica. Ciò che esce dalla board viene tagliato.
- **Colori**: *Colori del grafo* mantiene i colori della palette del grafo incorporato; *Monocromatico* colora ogni pixel acceso con il **colore scelto** nel nodo.
- Gira agli stessi FPS del grafo che lo contiene e **in loop**: la durata del ciclo tiene conto di quella del grafo incorporato. Come gli altri nodi di disegno si può combinare con modificatori (Muovi, Ruota, Lampeggia…), rispetta l'intervallo DA/A e può essere misurato dal nodo **Larghezza** (*Pixel accesi* = pixel accesi nel frame, *Ingombro* = larghezza della dimensione scelta).
- Usa la **versione salvata** del grafo incorporato: se lo modifichi, salvalo per vedere il cambiamento. Se un orologio è dentro il grafo incorporato, continua ad aggiornarsi nel palinsesto.
- Si possono annidare fino a **3 livelli**. Un grafo non può incorporare se stesso (nemmeno tramite altri): il salvataggio viene rifiutato. Un grafo incorporato in altri non si può eliminare finché è in uso (l'errore elenca quali).

### Animazione di PixelAnim dentro un grafo

Il nodo **Animazione** inserisce un'animazione di PixelAnim nel grafo, per esempio per metterla a sinistra su una 32×8 e far scorrere un testo a destra.

- **Animazione PixelAnim**: l'elenco viene da PixelAnim. Dimensioni e frame sono quelli dell'animazione.
- **X / Y**: angolo in alto a sinistra, con le **porte blu** (bindabili). Ciò che esce dalla board viene tagliato.
- **Colori**: *Colori della palette* mantiene gli indici di colore dell'animazione (le animazioni monocromatiche usano il colore 1 della palette); *Monocromatico* colora ogni pixel acceso con il **colore scelto** nel nodo.
- **Rallenta ×**: ogni frame dell'animazione dura N frame del grafo (1–60). Di base si avanza di un frame dell'animazione per ogni frame del grafo, in loop; la durata del ciclo del grafo tiene conto di quella dell'animazione.
- Come gli altri nodi di disegno si combina con i modificatori, rispetta l'intervallo DA/A e può essere misurato dal nodo **Larghezza**. Funziona anche dentro un grafo incorporato con il nodo **Grafo**.
- I frame vengono scaricati da PixelAnim e tenuti in memoria per **60 secondi**: una modifica fatta in PixelAnim compare entro un minuto. Se PixelAnim non risponde si usa l'ultima copia; se non ce n'è, il nodo non disegna nulla e l'anteprima mostra un avviso (⚠), per esempio "Animazione #99 non trovata in PixelAnim".
- Limiti: al massimo 1000 frame e 64×64 pixel per animazione.
- Nei file di importazione/esportazione il nodo conserva l'id e il nome dell'animazione: i file vanno importati su un'installazione collegata alla **stessa istanza di PixelAnim**.

### Modificatori

| Nodo | Effetto |
|---|---|
| **Ripetizioni** | Replica l'ingresso su una griglia, con colore iniziale e passo colore |
| **Combina** | Unione, sottrazione, intersezione o XOR di due ingressi (A e B) |
| **Colora** | Cambia colore |
| **Muovi** | Scorrimento continuo o a rimbalzo, con velocità e periodo per asse |
| **Ruota** | Rotazione continua o a pendolo attorno a un centro |
| **Scala** | Ingrandisce/rimpicciolisce attorno a un centro |
| **Specchia** | Orizzontale e/o verticale |
| **Lampeggia** | Frame accesi/spenti e fase |
| **Scia** | Echi dell'immagine nei frame precedenti |

### Nodi valore (porte blu)

| Nodo | Colore | Cosa produce |
|---|---|---|
| **Tempo** | viola | Valore che cambia nel tempo: cresce, avanti/indietro o sinusoide; periodo, min, max, passo |
| **Casuale** | giallo | Valore casuale tra min e max, cambia ogni N frame; con seed e passo |
| **Larghezza** | azzurro | Misura in pixel ciò che gli arriva (testo, bitmap o qualsiasi nodo) |
| **Matematica** | verde | A + B, A − B, A × B, A ÷ B; A e B sono numeri o si legano ad altri nodi valore |

**Larghezza.** Testo, Ora e Bitmap sono misurati sul contenuto (anche se più larghi della board, anche in modalità scorrevole). *Colora* e *Lampeggia* sono trasparenti. Per gli altri nodi conta il riquadro dei pixel accesi sulla board. Con **Misura** scegli *Pixel accesi* (dal primo all'ultimo pixel) oppure *Ingombro* (colonne vuote comprese; bitmap = 8 × scala). L'**Offset** viene sommato al risultato. Fuori dall'intervallo DA/A del nodo misurato la larghezza è 0.

**Matematica.** La divisione per zero dà 0. Il risultato può essere decimale; quando pilota un parametro in pixel viene arrotondato (.5 verso l'alto).

Ogni nodo ha anche un intervallo **DA/A (secondi)** per limitarne l'attività nel tempo (fuori intervallo non disegna, o vale 0 se è un nodo valore). Un grafo ha al massimo **100 nodi**.

### Esempi

**Due testi in fila.** Testo A → **Larghezza** (Offset = 2) → porta blu **X** del testo B (entrambi allineati a sinistra): B parte sempre subito dopo A, qualunque sia la lunghezza di A.

**Sottolineatura.** Testo → **Larghezza** → porta blu **Largh.** di un **Rettangolo** alto 1 pixel, posizionato sotto al testo.

**Movimento oscillante.** **Tempo** (avanti/indietro) → porta blu **X** di un nodo: si muove da un lato all'altro.

**Metà della larghezza.** Larghezza → **Matematica** (A ÷ B, B = 2) → porta blu **X**: centra un elemento rispetto al testo misurato.

### Importare ed esportare grafi

Nella barra della vista Nodi:

- **⬇ Esporta**: scarica il grafo aperto (anche non salvato) come `nome.pixelanim-graph.json`.
- **⬇ Tutti**: scarica tutti i grafi salvati in un unico file di backup.
- **⬆ Importa**: carica un file.
  - *File singolo*: il grafo si apre nell'editor come nuovo e **non salvato**; controllalo e premi **Salva**. Accetta anche un JSON con solo `nodes` e `links`.
  - *Backup*: chiede conferma e salva tutti i grafi; i nomi già esistenti diventano "Nome (2)", "Nome (3)"…; i grafi non validi vengono scartati e segnalati. I nodi **Grafo** vengono ricollegati ai grafi importati (per id del file, altrimenti per nome); se il grafo incorporato non esiste l'importazione lo segnala e il nodo resta senza grafo.

Formato dei file:

```jsonc
// singolo grafo
{ "format": "pixelanim-graph", "version": 1, "name": "Mio grafo",
  "graph": { "nodes": [ … ], "links": [ … ], "out": { "x": 620, "y": 30 } } }

// backup
{ "format": "pixelanim-graphs", "version": 1,
  "graphs": [ { "id": 1, "name": "…", "graph": { … } } ] }
```

Ogni nodo è `{ "id", "type", "x", "y", "params": { … } }` (più `name` e `collapsed` opzionali); ogni collegamento è `{ "from", "to", "to_port" }`, con `to: "out"` per l'uscita. I file vengono controllati dal server: tipi di nodo, id duplicati, anelli e posizioni non valide vengono respinti o corretti.

---

## Lingue

L'interfaccia supporta italiano e inglese. La lingua viene inizialmente rilevata dalle preferenze del browser; l'utente può cambiarla da **Impostazioni → Lingua**. La scelta viene memorizzata nel browser.

Le traduzioni si trovano in `static/i18n/it.json` e `static/i18n/en.json`. L'italiano è la lingua sorgente: quando si aggiunge un nuovo testo all'interfaccia, va aggiunta la relativa voce in entrambi i file. Le chiavi dei dizionari sono i testi sorgente italiani. Per i messaggi dinamici usa `t('Testo originale in italiano')`, così il testo viene tradotto anche quando è creato dal codice JavaScript.

## Sviluppo e configurazione locale

Il codice applicativo deve rimanere uguale tra installazione pubblica e privata. Le differenze di ambiente vanno mantenute in `.env` e `compose.local.yml`, entrambi esclusi da Git. Per aggiornare l'installazione locale dopo aver pubblicato le modifiche: `git pull` e poi `docker compose -f compose.yml -f compose.local.yml up -d --build`. Prima dell'aggiornamento è consigliato fare un backup della cartella `data`.


## API REST

La documentazione interattiva generata da FastAPI è su `/docs`.

| Metodo | Percorso | Descrizione |
|---|---|---|
| GET | `/api/animations` | Elenco animazioni (da PixelAnim) |
| GET | `/api/animations/{id}/frames` | Frame di un'animazione |
| GET / PUT | `/api/settings` | Palette a 16 colori |
| GET / POST | `/api/devices` | Elenco / creazione board |
| PUT / DELETE | `/api/devices/{id}` | Modifica / eliminazione board |
| PUT | `/api/devices/{id}/autostart` | Avvio automatico del palinsesto |
| GET / PUT | `/api/devices/{id}/playlist` | Lettura / salvataggio del palinsesto |
| POST | `/api/devices/{id}/playlist/import` | Copia il palinsesto da un'altra board (`replace` o `append`) |
| POST | `/api/devices/{id}/playlist/start` · `/stop` | Avvia / ferma il palinsesto |
| GET | `/api/playlists/status` | Stato dei palinsesti in esecuzione |
| GET | `/api/devices/{id}/monitor` | Ultimo frame mostrato dalla board |
| POST | `/api/play` · `/api/stop` | Riproduce un'animazione / ferma una o tutte le board |
| POST | `/api/message` | Messaggio urgente (vedi sopra) |
| GET / POST | `/api/graphs` | Elenco / creazione grafi |
| GET / PUT / DELETE | `/api/graphs/{id}` | Lettura / modifica / eliminazione di un grafo |
| POST | `/api/graphs/render` | Anteprima: restituisce i frame di un grafo |
| POST | `/api/graphs/validate` | Controlla e normalizza un grafo senza salvarlo |
| GET | `/api/graphs/export` | Backup di tutti i grafi |
| POST | `/api/graphs/import` | Importa più grafi (max 200 per richiesta) |
| POST | `/api/graph/play` | Riproduce un grafo su una board |

---

## Limiti

- Dimensioni board accettate nelle Impostazioni: larghezza 8/16/32, altezza 8/16. Il firmware disegna correttamente solo 8×8, 32×8 e 16×16.
- Palinsesto: max 100 elementi. Grafo: max 100 nodi, 200 KB, al massimo 3 livelli di grafi incorporati. Coda messaggi: 20 per board.
- L'anteprima dei grafi mostra al massimo i primi 400 frame; il ciclo di rendering è limitato a 4000 frame.
- Animazioni PixelAnim nei grafi: max 1000 frame e 64×64 pixel ciascuna.
- Un solo utente alla volta è il caso d'uso previsto: non ci sono account né permessi.

---

## Sviluppo

- Il backend è suddiviso in `main.py`, `api.py`, `core.py` e `database.py`; il frontend è una pagina senza build (`static/index.html`) con logica in `static/app.js`.
- Per aggiungere una dimensione di board supportata dal firmware, aggiorna `FIRMWARE_DIMS` in `core.py`.
- Per aggiungere un tipo di nodo servono due modifiche: la definizione lato server (`GRAPH_*_TYPES`, validazione in `check_graph` e rendering) e la voce in `NG_TYPES` del frontend, più il pulsante nel menu **Aggiungi**.
- Il canvas dei nodi ha le stesse dimensioni nel frontend (`NG_CW`, `NG_CH`) e nel backend (`GRAPH_CANVAS_W`, `GRAPH_CANVAS_H`): se le cambi, cambiale in entrambi.
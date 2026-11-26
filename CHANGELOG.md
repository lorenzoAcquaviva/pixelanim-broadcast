# Changelog


## [Unreleased]

- Bitmap: zoom (rotella sul punto, pulsanti −/+, tasti +/−/0, fino a 32×) e spostamento della vista (strumento ✋, tasto centrale/destro o Spazio+trascina); l'area di disegno a schermo intero ora occupa tutto lo spazio disponibile e viene ridisegnata nitida alla risoluzione dello schermo.
- Bitmap: esportazione GIF animata (pulsante «⇩ GIF») con tutti i frame, durate per frame, loop infinito e ingrandimento a scelta; i punti spenti sono neri.
- Bitmap: con la gomma attiva, cliccando su un pixel spento si torna automaticamente al pennello (simmetrico al passaggio alla gomma cliccando su un pixel acceso).
- Bitmap, importazione GIF: il popup ora sta sopra l'editor a schermo intero, è scorrevole (altezza limitata allo schermo) e i pulsanti Annulla/Importa restano sempre visibili in fondo.
- Bitmap, importazione GIF: logica di ritaglio resa esplicita: sull'originale compare il riquadro verde della parte che resta (trascinabile), i cursori sono attivi solo con «Riempi» e spiegano cosa fanno; l'anteprima si aggiorna da sola (elabora solo il primo frame).
- Bitmap: frecce ← → della tastiera per passare da un frame all'altro (anche a schermo intero); le frecce ‹ › non vengono più tagliate quando i frame sono molti.
- Bitmap: cliccando su un pixel acceso lo si cancella e lo strumento passa automaticamente alla gomma.
- Bitmap: aggiunto annullamento Ctrl+Z con cronologia fino a 20 operazioni, incluse pennellate, forme, ridimensionamento e gestione dei frame; disponibile anche il pulsante Annulla.


- Expanded the Bitmap graph node to editable 1×1–128×128 full-colour pixel art, with blank frames by default.
- Added continuous pointer drawing/erasing, line/circle/rectangle/triangle tools, up to five animated frames, and adjacent-frame onion skin.
- Preserved compatibility with legacy 8×8 bitmap data.

### Added
- Text node can bind its `text` input to a numeric value source.
- Node editor palette redesigned in a Node-RED style, with a search field, collapsible categories, colored node blocks, icons and visible connection ports; nodes can be clicked or dragged onto the canvas.
- Global numeric External Input nodes, fed through authenticated REST endpoints and kept in RAM only.
- Live editor preview and active-board re-rendering when external values change or expire; dynamic graphs bypass render cache.

### Changed
- External input updates re-render from the current animation frame phase instead of jumping back to frame zero.

### Bug Fixes

- Palinsesto (7f486ca)
- 1° play palinsesto (8c8047d)
- Test (bf71721)
- Update container path and file modification time in sync script (1f3531f)
- Collegamento nodo (2cedf21)
- Nodo random ok (bc02c17)
- Modificata la logica di valutazione per il nodo casuale (f5d14e6)
- Ultimo push collegato a pixanim per invio (87572ae)
- Colore32 (32ae16e)
- Clear Stats (b8fec52)

### Features

- La Viewport può ritagliare anche le animazioni PixelAnim, oltre ai grafi incorporati.

- Aggiunto il nodo Viewport con coordinate X/Y bindabili per ritagliare dinamicamente grafi di dimensioni maggiori.

- Enhance device management with color settings and palette support (217f3f3)
- Aggiunto effetto pausa nel palinsesto (bfe3579)
- Aggiunta opzione per partenza automatica (627a6de)
- Gestione avvio automatico (390999f)
- Add nodes view for algorithmic animations and graph management (46a02f1)
- Aggiunti nodi palinsesto (6563616)
- Possibilità di disattivare una riga nel palinsesto (8a3cff2)
- Implement zoom and pan functionality for the canvas (ab4b66e)
- Add auto_duration field to playlist items and enhance UI (76ca53c)
- Aggiunto scroll (34b2a02)
- Nodo orologio OK (b7c2145)
- Nodi gradiente, tempo (dbd4337)
- Enhance node interaction and time evaluation functionality (887eb9b)
- Aggiunta supporto per nodi di ripetizione e miglioramenti all'interfaccia utente (ac7fd70)
- Aggiunta supporto per nodi combinatori e coloratori, miglioramenti all'interfaccia utente (c5478e0)
- Primi palinsesti creati (abf8ce5)
- Aggiunta funzionalità di duplicazione nodo e miglioramenti all'interfaccia utente (eac80b7)
- Aggiunta nuovi nodi di trasformazione e miglioramenti all'interfaccia utente (4eb4ba1)
- Monitor animazioni (bef5787)
- Aggiunti monitor (574146b)
- Migliorata la grafica delle schede monitor e aggiunta funzione per disegnare LED (09062b4)
- Cerchi (9a96dae)
- Aggiunta nuove funzionalità per la gestione dei nodi, inclusi blink, move, rotate, scale e trail (b78998b)
- Matrice su 2432, ma si riavvia (d47fab6)
- Nodo larghezza (0e65813)
- Aggiunta funzionalità di importazione/esportazione per i grafi in formato JSON (eca7473)
- Aggiornamento del README.md con nuove funzionalità e miglioramenti, inclusa la descrizione dettagliata del palinsesto, dei requisiti e delle modalità di utilizzo. Aggiunta la sezione per l'importazione/esportazione dei grafi in JSON e chiarimenti sul funzionamento del sistema. (33d8f95)
- Nodi di nodi, ma molto CPU intenso... (b193d6b)
- Gestione cache animazioni (46c5ec2)
- Statistiche (63cbee9)
- Statistiche dashboard, ma ko (4fe1a8b)
- Aggiunta endpoint per svuotare errori nelle statistiche e aggiornamento della vista delle statistiche (b1b3c9a)
- Nodi algoritmici (3c43e02)
- Nodi algoritmici hanno le variabili in e out (07431bb)



### Added
- Bitmap GIF import with all animation frames (up to 1000), transparency mapped to empty pixels, and colors matched to the active 16-color palette.
- GIF frames are proportionally reduced only when needed to fit the Bitmap 128×128 limit; original frame delays are retained.
- Bitmap timeline supports IN/OUT temporal trimming and per-frame duration editing; animation playback honors individual frame durations.

### Bitmap animation editor improvements
- Added a focused full-screen Bitmap animation workspace.
- Replaced the long frame-button list with a compact neighboring-frame strip centered on the active frame.
- Added redo alongside undo, with a shared 50-step snapshot history; frame deletion is undoable and redoable.
- Added Ctrl+Y and Ctrl+Shift+Z shortcuts for redo (Ctrl/Cmd+Z for undo).

const $=id=>document.getElementById(id);
let animations=[],devices=[],editingId=null;
const JSONH={'Content-Type':'application/json'};

async function api(url,options={}){
  const r=await fetch(url,options);
  let d={};
  try{d=await r.json()}catch{}
  if(!r.ok){
    const msg=typeof d.detail==='string'?d.detail:
      Array.isArray(d.detail)?d.detail.map(x=>(x.msg||'').replace(/^Value error, /,'')).join('; '):
      'Errore richiesta';
    throw Error(window.t ? window.t(msg) : msg);
  }
  return d;
}

async function navigateTo(name){
  const onPlaylist = !$('playlistView').classList.contains('hidden');
  const onNodes    = !$('nodesView').classList.contains('hidden');
  const onSettings = !$('settingsView').classList.contains('hidden');
  if(onPlaylist && name!=='playlist'){
    if(plDirty&&!confirm('Hai modifiche non salvate al palinsesto: verranno perse. Continuare?'))return false;
    plDirty=false;
    if(plPollTimer){clearInterval(plPollTimer);plPollTimer=null}
  }
  if(onNodes && name!=='nodes'){
    if(ng.dirty&&!confirm('Hai modifiche non salvate ai nodi: verranno perse. Continuare?'))return false;
    ng.dirty=false;
    clearInterval(ngLoop);clearInterval(ngMinute);clearInterval(ngSecLoop);clearTimeout(ngPrevT);
    stopTick();
  }
  if(onSettings && name!=='settings'){ await loadDevices($('device').value); }
  showView(name);
  return true;
}

/* ANIMAZIONI */
async function loadAnimations(){
  try{
    animations=await api('/api/animations');
    $('anim').replaceChildren();
    animations.forEach(a=>{const o=document.createElement('option');o.value=a.id;o.textContent=a.name;o.dataset.noTranslate='1';$('anim').appendChild(o)});
    $('anim').onchange=updateInfo;
    updateInfo();
    $('status').textContent=animations.length?'Animazioni caricate.':'Non ci sono animazioni salvate.';
  }catch(e){$('status').textContent='Errore PixelAnim: '+e.message}
}
function updateInfo(){
  const a=animations.find(x=>String(x.id)===$('anim').value);
  $('info').textContent=a?`${a.width}×8 · ${a.frame_count??'?'} frame`:'Nessuna animazione selezionata';
  if($('showPreview').checked)loadPreview();
}

/* BOARD */
async function loadDevices(keep=null){
  try{
    devices=await api('/api/devices');
    const s=$('device');s.replaceChildren();
    const o=document.createElement('option');o.value='';o.textContent='Seleziona una board…';s.appendChild(o);
    devices.forEach(d=>{const x=document.createElement('option');x.value=d.id;x.textContent=`${d.name} · ${d.host}:${d.port}`;x.dataset.noTranslate='1';s.appendChild(x)});
    if(keep&&devices.some(d=>String(d.id)===String(keep)))s.value=keep;
    syncMainBoardRotation();
    if(!devices.length)$('status').textContent='Nessuna board: aggiungila da ⚙ Impostazioni.';
    if(!$('monitorView').classList.contains('hidden')) renderMonitorGrid();
  }catch(e){$('status').textContent='Errore board: '+e.message}
}
function syncMainBoardRotation(){
  const selected=devices.find(d=>String(d.id)===String($('device').value));
  const boardRotate=!!selected?.rotate;
  $('rotate').checked=boardRotate;
  $('rotate').disabled=boardRotate;
  $('rotate').title=boardRotate
    ? 'Rotazione 180° già attiva nelle impostazioni della board.'
    : 'Ruota questa riproduzione di 180°.';
}
function renderBoards(){
  const box=$('boardList');box.replaceChildren();
  if(!devices.length){
    const p=document.createElement('p');p.className='muted';p.textContent='Nessuna board definita.';box.appendChild(p);return;
  }
  devices.forEach(d=>{
    const row=document.createElement('div');row.className='board';
    const info=document.createElement('div');
    const n=document.createElement('b');n.textContent=d.name;n.dataset.noTranslate='1';
    const h=document.createElement('span');h.className='muted';h.textContent=`${d.host}:${d.port} · ${d.width}×${d.height} · ${d.color?'colore':'mono'}${d.rotate?' · ruotata 180°':''} · testo ${d.text_fps||10} fps${d.autostart?' · autostart':''}`+(d.has_key?'':' · chiave mancante');
    info.append(n,h);
    const act=document.createElement('div');act.className='board-actions';
    const ed=document.createElement('button');ed.type='button';ed.className='small';ed.textContent='Modifica';ed.onclick=()=>openForm(d);
    const del=document.createElement('button');del.type='button';del.className='small danger';del.textContent='Elimina';del.onclick=()=>deleteDevice(d);
    act.append(ed,del);row.append(info,act);box.appendChild(row);
  });
}
function openForm(d=null){
  editingId=d?d.id:null;
  $('formTitle').textContent=d?'MODIFICA BOARD':'NUOVA BOARD';
  $('deviceName').value=d?.name||'';
  $('deviceHost').value=d?.host||'';
  $('devicePort').value=d?.port||6053;
  $('deviceWidth').value=String(d?.width||8);
  $('deviceHeight').value=String(d?.height||8);
  $('deviceColor').value=d?.color?'1':'0';
  $('deviceRotate').value=d?.rotate?'1':'0';
  $('deviceTextFps').value=d?.text_fps||10;
  $('deviceKey').value='';
  $('deviceKey').placeholder=d?'Vuoto = mantieni':'Inserisci noise_psk';
  $('deviceForm').classList.remove('hidden');
  $('deviceName').focus();
}
function closeForm(){$('deviceForm').classList.add('hidden');editingId=null;$('deviceKey').value='';}
async function saveDevice(){
  const payload={
    name:$('deviceName').value.trim(), host:$('deviceHost').value.trim(),
    port:Number($('devicePort').value)||6053, api_key:$('deviceKey').value,
    width:Number($('deviceWidth').value), height:Number($('deviceHeight').value),
    color:$('deviceColor').value==='1', rotate:$('deviceRotate').value==='1',
    text_fps:Math.min(30,Math.max(1,Number($('deviceTextFps').value)||10))
  };
  try{
    const id=editingId;
    await api(id?`/api/devices/${id}`:'/api/devices',{method:id?'PUT':'POST',headers:JSONH,body:JSON.stringify(payload)});
    closeForm();
    await loadDevices($('device').value);
    renderBoards(); renderMsgDoc();
    $('settingsStatus').textContent='Board salvata.';
  }catch(e){$('settingsStatus').textContent='Errore salvataggio: '+e.message}
}
async function deleteDevice(d){
  if(!confirm(`Eliminare "${d.name}"?`))return;
  try{
    await api(`/api/devices/${d.id}`,{method:'DELETE'});
    await loadDevices($('device').value);
    renderBoards(); renderMsgDoc();
    $('settingsStatus').textContent='Board eliminata.';
  }catch(e){$('settingsStatus').textContent='Errore eliminazione: '+e.message}
}

/* MONITOR */


function renderMonitorGrid(){
  const box=$('monitorGrid');if(!box)return;
  box.replaceChildren();
  if(!devices.length){
    const p=document.createElement('p');p.className='muted';p.textContent='Nessuna board configurata.';box.appendChild(p);return;
  }
  devices.forEach(d=>{
    const st = monitorStates[d.id] || {};
    const isActive = !!st.active;
    const isRotate = !!st.rotate;
    const card=document.createElement('div');card.className='monitor-card';
    const title=document.createElement('h3');
    const nameSpan=document.createElement('span'); nameSpan.textContent=d.name; nameSpan.dataset.noTranslate='1';
    const ctrls=document.createElement('div'); ctrls.className='mon-controls';

    const labelActive=document.createElement('label');labelActive.className='mon-chk';
    const chkActive=document.createElement('input');chkActive.type='checkbox';chkActive.id=`mon_chk_${d.id}`;chkActive.checked=isActive;
    labelActive.append(chkActive, document.createTextNode(' Attivo'));

    const labelRotate=document.createElement('label');labelRotate.className='mon-chk';
    const chkRotate=document.createElement('input');chkRotate.type='checkbox';chkRotate.id=`mon_rot_${d.id}`;chkRotate.checked=isRotate;
    labelRotate.append(chkRotate, document.createTextNode(' 180°'));

    const btnPS=document.createElement('button');btnPS.type='button';btnPS.id=`mon_ps_${d.id}`;btnPS.className='mon-ps';
    btnPS.onclick = () => toggleBoardPlayback(d.id);

    ctrls.append(labelActive, labelRotate, btnPS);
    title.append(nameSpan, ctrls);

    const canvas=document.createElement('canvas');
    canvas.id=`mon_cv_${d.id}`;
    const basePx = Math.min(300, Math.max(80, d.width * 20));
    canvas.width = d.width * 20; canvas.height = d.height * 20;
    canvas.style.width = `${basePx}px`;
    canvas.style.height = `${Math.round(basePx * d.height / d.width)}px`;

    const info=document.createElement('div');info.className='info';info.id=`mon_info_${d.id}`;
    info.textContent = isActive ? 'In attesa…' : 'Spento';

    const bar=document.createElement('div');bar.className='timer-bar';
    const barInner=document.createElement('div');barInner.id=`mon_bar_${d.id}`;
    barInner.style.width = '0%'; bar.appendChild(barInner);

    card.append(title, canvas, info, bar); box.appendChild(card);
    updatePlayStopButton(d.id, st);

    chkActive.onchange = () => { if(chkActive.checked) activateMonitor(d.id, d); else deactivateMonitor(d.id); };
    chkRotate.onchange = () => {
      if(!monitorStates[d.id]) monitorStates[d.id] = {active: false};
      monitorStates[d.id].rotate = chkRotate.checked;
      if(monitorStates[d.id].active && monitorStates[d.id].frames) drawMonitorFrame(d.id, monitorStates[d.id]);
    };

    if (isActive && st.frames) {
      drawMonitorFrame(d.id, st);
      info.textContent = st.running ? `${st.frames.length} frame · ${st.fps} fps` : `In pausa`;
      updateBar(d.id, st);
    }
  });
}


function stopMonitorPolling(){ if (monitorPollTimer) { clearInterval(monitorPollTimer); monitorPollTimer = null; } }
function updateBar(did, st){
  const bar = $(`mon_bar_${did}`); if(!bar) return;
  if (!st || !st.active) { bar.style.width='0%'; return; }
  if (st.running) { bar.style.width='100%'; return; }
  const elapsed = Date.now() - (st.lastSeen || Date.now());
  const pct = Math.max(0, 100 - (elapsed / MONITOR_INACTIVITY_MS) * 100);
  bar.style.width = pct.toFixed(1) + '%';
}
function updatePlayStopButton(did, st){
  const btn = $(`mon_ps_${did}`); if (!btn) return;
  if (st && st.running) { btn.textContent='■ STOP'; btn.classList.add('stop'); btn.classList.remove('play'); }
  else { btn.textContent='▶ PLAY'; btn.classList.add('play'); btn.classList.remove('stop'); }
}
async function toggleBoardPlayback(did){
  const st = monitorStates[did] || {};
  if (st.running) {
    try {
      await api('/api/stop', { method:'POST', headers:JSONH, body:JSON.stringify({device_id:Number(did)}) });
      st.running = false; updatePlayStopButton(did, st);
      const info = $(`mon_info_${did}`); if (info) info.textContent='Fermato';
      updateBar(did, st);
    } catch (e) { alert('Errore fermando la board: ' + e.message); }
  } else {
    try {
      await api(`/api/devices/${did}/playlist/start`, { method:'POST' });
      st.running = true; updatePlayStopButton(did, st); pollMonitors();
    } catch (e) { alert('Impossibile avviare il palinsesto: ' + e.message); }
  }
}
/* MONITOR */
let monitorPollTimer=null;
let monitorStates={};
const MONITOR_INACTIVITY_MS = 120000;

function renderMonitorGrid(){
  const box=$('monitorGrid');if(!box)return;
  box.replaceChildren();
  if(!devices.length){
    const p=document.createElement('p');p.className='muted';p.textContent='Nessuna board configurata.';box.appendChild(p);return;
  }
  devices.forEach(d=>{
    const st = monitorStates[d.id] || {};
    const isActive = !!st.active;
    const isRotate = !!st.rotate;
    const card=document.createElement('div');card.className='monitor-card';
    const title=document.createElement('h3');
    const nameSpan=document.createElement('span'); nameSpan.textContent=d.name; nameSpan.dataset.noTranslate='1';
    const ctrls=document.createElement('div'); ctrls.className='mon-controls';

    const labelActive=document.createElement('label');labelActive.className='mon-chk';
    const chkActive=document.createElement('input');chkActive.type='checkbox';chkActive.id=`mon_chk_${d.id}`;chkActive.checked=isActive;
    labelActive.append(chkActive, document.createTextNode(' Attivo'));

    const labelRotate=document.createElement('label');labelRotate.className='mon-chk';
    const chkRotate=document.createElement('input');chkRotate.type='checkbox';chkRotate.id=`mon_rot_${d.id}`;chkRotate.checked=isRotate;
    labelRotate.append(chkRotate, document.createTextNode(' 180°'));

    const btnPS=document.createElement('button');btnPS.type='button';btnPS.id=`mon_ps_${d.id}`;btnPS.className='mon-ps';
    btnPS.onclick = () => toggleBoardPlayback(d.id);

    ctrls.append(labelActive, labelRotate, btnPS);
    title.append(nameSpan, ctrls);

    const canvas=document.createElement('canvas');
    canvas.id=`mon_cv_${d.id}`;
    const basePx = Math.min(300, Math.max(80, d.width * 20));
    canvas.width = d.width * 20; canvas.height = d.height * 20;
    canvas.style.width = `${basePx}px`;
    canvas.style.height = `${Math.round(basePx * d.height / d.width)}px`;

    const info=document.createElement('div');info.className='info';info.id=`mon_info_${d.id}`;
    info.textContent = isActive ? (st.noSignal ? 'Nessun segnale' : 'In attesa…') : 'Spento';

    const bar=document.createElement('div');bar.className='timer-bar';
    const barInner=document.createElement('div');barInner.id=`mon_bar_${d.id}`;
    barInner.style.width = '0%'; bar.appendChild(barInner);

    card.append(title, canvas, info, bar); box.appendChild(card);
    updatePlayStopButton(d.id, st);

    chkActive.onchange = () => { if(chkActive.checked) activateMonitor(d.id, d); else deactivateMonitor(d.id); };
    chkRotate.onchange = () => {
      if(!monitorStates[d.id]) monitorStates[d.id] = {active: false};
      monitorStates[d.id].rotate = chkRotate.checked;
      if(monitorStates[d.id].active && !monitorStates[d.id].noSignal && monitorStates[d.id].frames) {
        drawMonitorFrame(d.id, monitorStates[d.id]);
      }
    };

    if (isActive) {
      if (st.noSignal) {
        drawMonitorNoise(d.id, st);
      } else if (st.frames) {
        drawMonitorFrame(d.id, st);
        info.textContent = st.running ? `${st.frames.length} frame · ${st.fps} fps` : `In pausa`;
      }
      updateBar(d.id, st);
    }
  });
}

function startNoSignal(did, st, msg = 'Nessun segnale') {
  if (!st || !st.active) return;
  const info = $(`mon_info_${did}`);
  if (info) info.textContent = msg;
  if (st.noSignal && st.timer) return; // già in esecuzione

  if (st.timer) { clearInterval(st.timer); st.timer = null; }
  st.noSignal = true;
  st.rev = -1; // forza il ridisegno al ritorno del segnale

  drawMonitorNoise(did, st);
  st.timer = setInterval(() => {
    if (!st.active || !st.noSignal) return;
    drawMonitorNoise(did, st);
  }, 65); // ~15 FPS effetto neve
}

function activateMonitor(did, d){
  const prevRotate = (monitorStates[did] && monitorStates[did].rotate) || false;
  monitorStates[did] = {
    active: true, rotate: prevRotate, rev: -1, frames: null, fps: 8, k: 0,
    timer: null, lastSeen: Date.now(), running: false, noSignal: false,
    width: d.width, height: d.height
  };
  startNoSignal(did, monitorStates[did], 'Ricerca segnale…');
  pollMonitors();
  if(!monitorPollTimer) monitorPollTimer = setInterval(pollMonitors, 2000);
  updateBar(did, monitorStates[did]);
}

function deactivateMonitor(did){
  const st = monitorStates[did];
  if (st && st.timer) clearInterval(st.timer);
  monitorStates[did] = { active: false, rotate: (st && st.rotate) || false };
  const chkA = $(`mon_chk_${did}`); if (chkA) chkA.checked = false;
  const cvs = $(`mon_cv_${did}`);
  if (cvs) { const ctx = cvs.getContext('2d'); ctx.fillStyle='#000'; ctx.fillRect(0,0,cvs.width,cvs.height); }
  const info = $(`mon_info_${did}`); if (info) info.textContent = 'Spento';
  const bar = $(`mon_bar_${did}`); if (bar) bar.style.width = '0%';
  updatePlayStopButton(did, monitorStates[did]);
  if (!Object.values(monitorStates).some(s => s.active)) stopMonitorPolling();
}

function stopMonitorPolling(){ if (monitorPollTimer) { clearInterval(monitorPollTimer); monitorPollTimer = null; } }

function updateBar(did, st){
  const bar = $(`mon_bar_${did}`); if(!bar) return;
  if (!st || !st.active) { bar.style.width='0%'; return; }
  if (st.running) { bar.style.width='100%'; return; }
  const elapsed = Date.now() - (st.lastSeen || Date.now());
  const pct = Math.max(0, 100 - (elapsed / MONITOR_INACTIVITY_MS) * 100);
  bar.style.width = pct.toFixed(1) + '%';
}

function updatePlayStopButton(did, st){
  const btn = $(`mon_ps_${did}`); if (!btn) return;
  if (st && st.running) { btn.textContent='■ STOP'; btn.classList.add('stop'); btn.classList.remove('play'); }
  else { btn.textContent='▶ PLAY'; btn.classList.add('play'); btn.classList.remove('stop'); }
}

async function toggleBoardPlayback(did){
  const st = monitorStates[did] || {};
  if (st.running) {
    try {
      await api('/api/stop', { method:'POST', headers:JSONH, body:JSON.stringify({device_id:Number(did)}) });
      st.running = false; updatePlayStopButton(did, st);
      const info = $(`mon_info_${did}`); if (info) info.textContent='Fermato';
      updateBar(did, st);
    } catch (e) { alert('Errore fermando la board: ' + e.message); }
  } else {
    try {
      await api(`/api/devices/${did}/playlist/start`, { method:'POST' });
      st.running = true; updatePlayStopButton(did, st); pollMonitors();
    } catch (e) { alert('Impossibile avviare il palinsesto: ' + e.message); }
  }
}

async function pollMonitors(){
  const ids = Object.entries(monitorStates).filter(([_,s]) => s.active).map(([id]) => id);
  let stillActive = 0;
  for (const did of ids) {
    const st = monitorStates[did]; if (!st || !st.active) continue;
    try {
      const res = await api(`/api/devices/${did}/monitor`);
      if (!res.ok) {
        if (Date.now() - st.lastSeen > MONITOR_INACTIVITY_MS) { deactivateMonitor(did); continue; }
        st.running = false;
        updatePlayStopButton(did, st);
        startNoSignal(did, st, 'Nessun segnale');
        updateBar(did, st);
        stillActive++;
        continue;
      }

      st.lastSeen = Date.now();
      st.running = !!res.running;
      st.noSignal = false;
      updatePlayStopButton(did, st);

      if (st.rev !== res.rev) {
        st.rev = res.rev; st.frames = res.frames; st.fps = res.fps || 8;
        st.width = res.width; st.height = res.height; st.color = res.color;
        st.palette = res.palette; st.rgb = res.rgb; st.k = 0;
        if (st.timer) { clearInterval(st.timer); st.timer = null; }
        const draw = () => {
          if (!st.frames || !st.frames.length || st.noSignal) return;
          drawMonitorFrame(did, st);
          st.k = (st.k + 1) % st.frames.length;
        };
        draw();
        if (st.frames.length > 1) st.timer = setInterval(draw, 1000 / st.fps);
      }

      const info = $(`mon_info_${did}`);
      if (info && st.frames) info.textContent = st.running ? `${st.frames.length} frame · ${st.fps} fps` : `In pausa`;
      updateBar(did, st);
      stillActive++;
    } catch (e) {
      if (Date.now() - st.lastSeen > MONITOR_INACTIVITY_MS) { deactivateMonitor(did); continue; }
      st.running = false;
      updatePlayStopButton(did, st);
      startNoSignal(did, st, 'Errore connessione');
      updateBar(did, st);
      stillActive++;
    }
  }
  if (stillActive === 0) stopMonitorPolling();
}

function drawMonitorNoise(did, st) {
  const cvs = $(`mon_cv_${did}`); if (!cvs) return;
  const ctx = cvs.getContext('2d');
  const W = st.width || 8, H = st.height || 8;
  const sc = cvs.width / W;

  ctx.fillStyle = '#0a0d12';
  ctx.fillRect(0, 0, cvs.width, cvs.height);

  const cx_off = sc / 2;
  const radius = sc * 0.38;

  for (let r = 0; r < H; r++) {
    for (let c = 0; c < W; c++) {
      // ~42% di probabilità che il singolo LED si accenda con intensità casuale
      const on = Math.random() > 0.58;
      let color = null;
      if (on) {
        const lum = Math.floor(70 + Math.random() * 185);
        color = `rgb(${lum},${lum},${Math.min(255, lum + 12)})`;
      }
      drawLED(ctx, c * sc + cx_off, r * sc + cx_off, radius, color, on);
    }
  }
}
function drawLED(ctx, cx, cy, radius, color, on) {
  // Glow esterno (solo se acceso): due aloni concentrici semitrasparenti
  if (on) {
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.15;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.25, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.fill();

    ctx.globalAlpha = 1;
  }

  // Corpo del LED
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fillStyle = on ? color : '#0d1218';
  ctx.fill();

  // Bordo scuro (cupola)
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.strokeStyle = on ? 'rgba(0,0,0,0.45)' : 'rgba(0,0,0,0.55)';
  ctx.lineWidth = 1;
  ctx.stroke();

  // Highlight speculare (solo acceso)
  if (on) {
    ctx.beginPath();
    ctx.arc(cx - radius * 0.28, cy - radius * 0.28, radius * 0.32, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.65)';
    ctx.fill();
  }
}

function drawMonitorFrame(did, st) {
  const cvs = $(`mon_cv_${did}`); if (!cvs) return;
  const ctx = cvs.getContext('2d');
  const W = st.width, H = st.height;
  if (!W || !H || !st.frames) return;
  const sc = cvs.width / W;
  const rot = !!st.rotate;

  // Substrato scuro della matrice (non nero puro, così i LED spenti si vedono)
  ctx.fillStyle = '#0a0d12';
  ctx.fillRect(0, 0, cvs.width, cvs.height);

  const hex = st.frames[st.k % st.frames.length];
  if (!hex) return;

  const bpr = W / 8;
  const rgb = st.rgb || [0, 255, 102];
  const monoColor = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
  const cx_off = sc / 2;
  const radius = sc * 0.38;      // ~76% del passo → gap visibile tra LED

  for (let r = 0; r < H; r++) {
    for (let c = 0; c < W; c++) {
      const x = rot ? (W - 1 - c) : c;
      const y = rot ? (H - 1 - r) : r;
      let color = null;

      if (st.color) {
        const idx = r * W + c;
        const v = parseInt(hex[idx] || '0', 16);
        if (v > 0) color = (st.palette && st.palette[v]) || '#39c98a';
      } else {
        const block = Math.floor(c / 8);
        const bit = c % 8;
        const byteVal = parseInt(hex.substr(r * bpr * 2 + block * 2, 2), 16) || 0;
        if ((byteVal >> (7 - bit)) & 1) color = monoColor;
      }

      drawLED(ctx, x * sc + cx_off, y * sc + cx_off, radius, color, color !== null);
    }
  }
}
async function openMonitorView(){ renderMonitorGrid(); $('monitorStatus').textContent=''; }

/* RIPRODUZIONE */
async function play(){
  const a=animations.find(x=>String(x.id)===$('anim').value);
  if(!a)return;
  if(!$('device').value){$('status').textContent='Seleziona una board.';return}
  const payload={animation_id:Number(a.id),device_id:Number($('device').value),
    fps:Number($('fps').value)||8,color:$('color').value,
    brightness:Number($('brightness').value)||100,
    rotate:$('rotate').checked,loop:$('loop').checked,use_palette:$('usePalette').checked};
  $('status').textContent='Connessione e avvio…'; $('play').disabled=true;
  try{
    const d=await api('/api/play',{method:'POST',headers:JSONH,body:JSON.stringify(payload)});
    $('status').textContent=`In riproduzione: ${d.frames} frame · ${d.width}×8 · ${d.fps} FPS · RGB ${d.rgb.join(', ')}`;
  }catch(e){$('status').textContent='Errore: '+e.message}
  finally{$('play').disabled=false}
}
async function stop(){
  try{await api('/api/stop',{method:'POST',headers:JSONH,body:JSON.stringify({device_id:Number($('device').value)||null})});$('status').textContent='Riproduzione arrestata.'}
  catch(e){$('status').textContent='Errore STOP: '+e.message}
}

/* ANTEPRIMA COMPATTA */
async function loadPreview(){
  const a=animations.find(x=>String(x.id)===$('anim').value); if(!a)return;
  try{
    const f=await api(`/api/animations/${a.id}/frames`);
    const pixels=f?.[0]?.pixels||[], w=Number(a.width)===32?32:8, box=$('preview');
    box.dataset.width=String(w); box.replaceChildren();
    for(let y=0;y<8;y++)for(let x=0;x<w;x++){
      const p=document.createElement('span');p.className='pixel';
      if(pixels[y]&&pixels[y][x])p.style.background=$('color').value;
      box.appendChild(p);
    }
    $('previewInfo').textContent=`Primo frame · ${w}×8`;
  }catch{$('previewInfo').textContent='Anteprima non disponibile'}
}

/* PALETTE */
let palette=[];
async function loadPalette(){
  try{palette=(await api('/api/settings')).palette;renderPalette()}
  catch(e){$('paletteStatus').textContent='Errore palette: '+e.message}
}
function renderPalette(){
  const g=$('paletteGrid');g.replaceChildren();
  palette.forEach((c,i)=>{
    const w=document.createElement('div');
    const l=document.createElement('span');l.className='muted';l.textContent=i===0?'0 (spento)':String(i);
    const inp=document.createElement('input');inp.type='color';inp.value=c;
    if(i===0)inp.disabled=true;else inp.oninput=()=>{palette[i]=inp.value};
    w.append(l,inp);g.appendChild(w);
  });
}
async function savePalette(){
  try{
    palette=(await api('/api/settings',{method:'PUT',headers:JSONH,body:JSON.stringify({palette})})).palette;
    renderPalette();$('paletteStatus').textContent='Palette salvata.';
  }catch(e){$('paletteStatus').textContent='Errore salvataggio: '+e.message}
}

/* MESSAGGI */
function renderMsgDoc(){
  fillSelect($('msgDevice'),devices,$('msgDevice').value);
  const name=devices.find(d=>String(d.id)===$('msgDevice').value)?.name||devices[0]?.name||'nome-board';
  $('msgDoc').textContent=`POST ${location.origin}/api/message
Content-Type: application/json

`+
    JSON.stringify({device:name,text:'Citofono!',duration:10,repeat:1,color:'#ff0000',brightness:100},null,2);
}
async function sendTestMessage(){
  const dev=$('msgDevice').value,text=$('msgText').value.trim();
  if(!dev){$('msgStatus').textContent='Scegli una board.';return}
  if(!text){$('msgStatus').textContent='Scrivi il testo.';return}
  try{
    const d=await api('/api/message',{method:'POST',headers:JSONH,body:JSON.stringify({device:Number(dev),text})});
    $('msgStatus').textContent=`Messaggio in coda per "${d.device}" (${d.duration} s, ${d.fps} fps; ${d.queued} in attesa).`;
    await loadMessageQueue();
  }catch(e){$('msgStatus').textContent='Errore: '+e.message}
}
async function loadMessageQueue(){
  const dev=$('msgDevice').value,box=$('msgQueueList');
  if(!dev){box.textContent='Seleziona una board.';return}
  try{
    const data=await api(`/api/devices/${dev}/messages`);box.replaceChildren();
    if(data.showing){const p=document.createElement('p');p.className='muted';p.textContent='In riproduzione: '+data.showing;box.appendChild(p)}
    if(!data.items.length){const p=document.createElement('p');p.className='muted';p.textContent='Nessun messaggio in attesa.';box.appendChild(p);$('msgQueueStatus').textContent=`Coda vuota · limite ${data.max}`;return}
    data.items.forEach(item=>{
      const row=document.createElement('div');row.className='plrow';
      const field=(label,el,cls)=>{const f=document.createElement('div');f.className='f '+cls;const l=document.createElement('label');l.textContent=label;f.append(l,el);row.appendChild(f)};
      const text=document.createElement('input');text.type='text';text.maxLength=200;text.value=item.text;field('TESTO',text,'anim');
      const fps=document.createElement('input');fps.type='number';fps.min=1;fps.max=30;fps.value=item.fps;field('FPS',fps,'num');
      const dur=document.createElement('input');dur.type='number';dur.min=1;dur.max=3600;dur.value=item.duration||'';dur.placeholder=String(item.duration_resolved);field('DURATA s',dur,'num');
      const rep=document.createElement('input');rep.type='number';rep.min=1;rep.max=20;rep.value=item.repeat;field('RIPETI',rep,'num');
      const color=document.createElement('input');color.type='color';color.value=item.color;field('COLORE',color,'pos');
      const save=document.createElement('button');save.type='button';save.className='small';save.textContent='Salva';save.onclick=async()=>{
        try{await api(`/api/devices/${dev}/messages/${item.index}`,{method:'PUT',headers:JSONH,body:JSON.stringify({text:text.value,duration:dur.value?Number(dur.value):null,repeat:Number(rep.value)||1,fps:Number(fps.value)||10,color:color.value,brightness:item.brightness||100,rotate:item.rotate})});$('msgQueueStatus').textContent='Messaggio aggiornato.';await loadMessageQueue()}
        catch(e){$('msgQueueStatus').textContent='Errore: '+e.message}
      };
      const del=document.createElement('button');del.type='button';del.className='small danger';del.textContent='✕';del.title='Elimina dalla coda';del.onclick=async()=>{try{await api(`/api/devices/${dev}/messages/${item.index}`,{method:'DELETE'});await loadMessageQueue()}catch(e){$('msgQueueStatus').textContent='Errore: '+e.message}};
      row.append(save,del);box.appendChild(row);
    });
    $('msgQueueStatus').textContent=`${data.items.length}/${data.max} messaggi in attesa`;
  }catch(e){$('msgQueueStatus').textContent='Errore caricamento coda: '+e.message}
}
async function clearMessageQueue(){
  const dev=$('msgDevice').value;if(!dev)return;
  if(!confirm('Svuotare tutti i messaggi ancora in attesa per questa board?'))return;
  try{const d=await api(`/api/devices/${dev}/messages`,{method:'DELETE'});$('msgQueueStatus').textContent=`Rimossi ${d.cleared} messaggi.`;await loadMessageQueue()}
  catch(e){$('msgQueueStatus').textContent='Errore: '+e.message}
}

/* NODI */
const SVGNS='http://www.w3.org/2000/svg';
const NG_ALIGN=[['left','Sinistra'],['center','Centro'],['right','Destra']];
const NG_DRAW_TYPES=['pixel','rect','gradient','bitmap','circle','text','clock','subgraph','animation','algo'];
const NG_MOD_TYPES=['repeat','combine','color','move','rotate','scale','mirror','blink','trail'];
const NG_VALUE_TYPES=['time','random','width','math','algo'];
const NG_CALC_TYPES=['width','math','algo'];   // valori calcolati dal backend, non in JS
const NG_TYPES={
  pixel:{title:'Pixel',num:[['x','X',-256,256],['y','Y',-256,256]],bind:['x','y'],rep:true,color:true,def:{x:0,y:0,color:1,nx:1,ny:1,dx:1,dy:1}},
  rect:{title:'Rettangolo',num:[['x','X',-256,256],['y','Y',-256,256],['w','Largh.',0,256],['h','Alt.',0,256]],bind:['x','y','w','h'],rep:true,chk:[['fill','Pieno']],color:true,def:{x:0,y:0,w:4,h:4,fill:false,color:1,nx:1,ny:1,dx:4,dy:4}},
  gradient:{title:'Gradiente',num:[['x','X',-256,256],['y','Y',-256,256],['w','Largh.',1,256],['h','Alt.',1,256]],bind:['x','y','w','h'],sel:[['direction','Direzione',[['h','Orizzontale'],['v','Verticale']]]],rep:true,colors:true,def:{x:0,y:0,w:8,h:8,direction:'h',color_a:1,color_b:7,nx:1,ny:1,dx:8,dy:8}},
  bitmap:{title:'Bitmap 8x8',num:[['x','X',-256,256],['y','Y',-256,256],['scale','Scala',1,16]],bind:['x','y','scale'],color:true,def:{x:0,y:0,scale:1,bits:'FF8181FF818181FF',color:1}},
  circle:{title:'Cerchio',num:[['x','X',-256,256],['y','Y',-256,256],['r','Raggio',0,256]],bind:['x','y','r'],chk:[['fill','Pieno']],color:true,def:{x:4,y:4,r:3,fill:true,color:1}},
  text:{title:'Testo',txt:[['text','Testo',120]],sel:[['mode','Modo',[['static','Fisso'],['scroll','Scorre']]],['align','Allinea',NG_ALIGN]],num:[['x','X',-256,256],['y','Y',-256,256]],bind:['x','y'],color:true,def:{text:'CIAO',mode:'static',align:'center',x:0,y:1,color:1}},
  clock:{title:'Ora',sel:[['part','Parte',[['HH:MM','HH:MM'],['HH','Ore'],['MM','Minuti'],['SS','Secondi'],['HH:MM:SS','HH:MM:SS'],['alterna','Ore/minuti alternati']]],['font','Font',[['5x7','5×7'],['3x5','3×5']]],['align','Allinea',NG_ALIGN]],num:[['x','X',-256,256],['y','Y',-256,256]],bind:['x','y'],chk:[['blink','Due punti lampeggianti']],color:true,def:{part:'HH:MM',font:'5x7',align:'center',x:0,y:1,blink:true,color:1}},
  time:{title:'Tempo',sel:[['mode','Modo',[['linear','Cresce'],['pingpong','Avanti/indietro'],['sine','Sinusoide']]]],num:[['period','Periodo (s)',1,3600],['min','Min',-256,256],['max','Max',-256,256],['quantize','Passo',0,256]],def:{mode:'linear',period:4,min:0,max:31,quantize:1}},
  random:{title:'Casuale',num:[['min','Min',-256,256],['max','Max',-256,256],['step','Cambia ogni N frame',1,256],['quantize','Passo',0,256],['seed','Seed',0,99999]],def:{min:0,max:7,step:1,quantize:1,seed:1}},  
  animation:{title:'Animazione',sel:[['anim_id','Animazione PixelAnim',animationOptions],['modo','Colori',[['color','Colori della palette'],['mono','Monocromatico']]]],num:[['x','X',-256,256],['y','Y',-256,256],['hold','Rallenta ×',1,60]],bind:['x','y'],color:true,colorLabel:'COLORE (MONOCROMATICO)',def:{anim_id:0,anim_name:'',modo:'color',hold:1,x:0,y:0,color:1}},
  subgraph:{title:'Grafo',sel:[['graph_id','Grafo salvato',subgraphOptions],['size','Dimensione del grafo',[['8x8','8×8'],['16x8','16×8'],['32x8','32×8'],['8x16','8×16'],['16x16','16×16'],['32x16','32×16']]],['modo','Colori',[['color','Colori del grafo'],['mono','Monocromatico']]]],num:[['x','X',-256,256],['y','Y',-256,256]],bind:['x','y'],color:true,colorLabel:'COLORE (MONOCROMATICO)',def:{graph_id:0,graph_name:'',size:'8x8',modo:'color',x:0,y:0,color:1}},
  width:{title:'Larghezza',sel:[['misura','Misura',[['ink','Pixel accesi'],['box','Ingombro']]]],num:[['offset','Offset (px)',-256,256]],ins:['in'],readout:true,unit:'px',def:{misura:'ink',offset:0}},
  math:{title:'Matematica',sel:[['op','Operazione',[['add','A + B'],['sub','A − B'],['mul','A × B'],['div','A ÷ B']]]],num:[['va','A',-1000000,1000000],['vb','B',-1000000,1000000]],bind:['va','vb'],float:true,readout:true,unit:'',def:{op:'add',va:0,vb:1}},
  repeat:{title:'Ripetizioni',sel:[['color_mode','Colore',[['override','Cambia colore'],['original','Mantieni colore']]]],num:[['x','X',-256,256],['y','Y',-256,256],['nx','N.x',1,32],['ny','N.y',1,32],['dx','Δx',-256,256],['dy','Δy',-256,256],['color_start','Colore iniz.',0,15],['color_step','Passo colore',-15,15]],bind:['x','y','dx','dy','nx','ny'],ins:['in'],def:{x:0,y:0,nx:3,ny:3,dx:8,dy:8,color_mode:'override',color_start:1,color_step:1}},
  combine:{title:'Combina',sel:[['modo','Operazione',[['unione','Unione'],['sottrai','Sottrai'],['intersezione','Intersezione'],['xor','Xor']]]],ins:['a','b'],def:{modo:'sottrai'}},
  color:{title:'Colora',num:[['col','Colore',0,15]],ins:['in'],def:{col:2}},
  move:{title:'Muovi',num:[['vx','Velocità X',-10,10],['vy','Velocità Y',-10,10],['x0','Offset X',-256,256],['y0','Offset Y',-256,256],['px','Periodo X',0,256],['py','Periodo Y',0,256]],sel:[['modo','Modo',[['ciclo','Ciclo'],['rimbalzo','Rimbalzo']]]],ins:['in'],def:{vx:1,vy:0,x0:0,y0:0,modo:'ciclo',px:0,py:0}},
  rotate:{title:'Ruota',num:[['a0','Angolo iniz.',0,360],['vel','Velocità',-30,30],['range','Ampiezza',0,360],['cx','Centro X',-256,256],['cy','Centro Y',-256,256]],sel:[['modo','Modo',[['continuo','Continuo'],['pendolo','Pendolo']]]],ins:['in'],def:{a0:0,vel:15,modo:'continuo',range:360,cx:4,cy:4}},
  scale:{title:'Scala',num:[['s0','Scala iniz.',0.1,5],['vel','Velocità',-1,1],['range','Ampiezza',0,5],['cx','Centro X',-256,256],['cy','Centro Y',-256,256]],sel:[['modo','Modo',[['rimbalzo','Rimbalzo'],['continuo','Continuo']]]],ins:['in'],def:{s0:0.5,vel:0.1,modo:'rimbalzo',range:1,cx:4,cy:4}},
  mirror:{title:'Specchia',num:[['cx','Centro X',-256,256],['cy','Centro Y',-256,256]],chk:[['h','Orizzontale'],['v','Verticale']],ins:['in'],def:{h:true,v:false,cx:4,cy:4}},
  blink:{title:'Lampeggia',num:[['on','Frame accesi',0,30],['off','Frame spenti',0,30],['fase','Fase',0,30]],ins:['in'],def:{on:1,off:1,fase:0}},
  trail:{title:'Scia',num:[['n','Ripetizioni',0,20],['passo','Distanza (frame)',1,20],['eco','Colore echi',0,15]],ins:['in'],def:{n:3,passo:1,eco:0}}
};
const ALGO_EXAMPLES={
 "onda": {
  "name": "Onda sinusoidale",
  "code": "# Onda sinusoidale: un pixel per colonna\nfor x in range(W):\n    y = (H - 1) / 2 + (H / 2 - 0.5) * sin(2 * pi * (x / W - k / N))\n    pixel(x, round(y))\n"
 },
 "pioggia": {
  "name": "Pioggia",
  "code": "# Gocce che cadono (posizioni casuali ma ripetibili grazie al seed)\nif k == 0:\n    gocce = [[randint(0, W - 1), randint(0, H - 1), randint(1, 3) / 2] for _ in range(max(2, W // 2))]\nfor g in gocce:\n    pixel(g[0], g[1], 5)\n    pixel(g[0], g[1] - 1, 6)\n    g[1] += g[2]\n    if g[1] >= H + 1:\n        g[0] = randint(0, W - 1)\n        g[1] = 0\n"
 },
 "scanner": {
  "name": "Scanner",
  "code": "# Barra che va avanti e indietro (un ciclo completo in N frame)\ntri = 1 - abs(2 * k / N - 1)\nx = tri * (W - 1)\nrect(x - 1.5, 0, 4, H, 3)\nrect(x - 0.5, 0, 2, H, 2)\n"
 },
 "plasma": {
  "name": "Plasma a colori",
  "code": "# Plasma: ogni pixel prende un colore della palette (usa «Colori dello script»)\na = 2 * pi * k / N\nfor y in range(H):\n    for x in range(W):\n        v = sin(x / 2 + a) + sin(y / 2 + a) + sin((x + y) / 3 - a)\n        pixel(x, y, 1 + floor((v + 3) / 6 * 14.99))\n"
 },
 "life": {
  "name": "Game of Life",
  "code": "# Game of Life su griglia chiusa ad anello, con seme casuale\nif k == 0:\n    cell = [[1 if rand() < 0.35 else 0 for x in range(W)] for y in range(H)]\nelse:\n    new = [[0] * W for y in range(H)]\n    for y in range(H):\n        for x in range(W):\n            n = 0\n            for dy in (-1, 0, 1):\n                for dx in (-1, 0, 1):\n                    if dx or dy:\n                        n += cell[(y + dy) % H][(x + dx) % W]\n            new[y][x] = 1 if n == 3 or (n == 2 and cell[y][x]) else 0\n    cell = new\nfor y in range(H):\n    for x in range(W):\n        if cell[y][x]:\n            pixel(x, y)\n"
 }
};
NG_TYPES.algo={title:'Algoritmico',
  num:[['w','Largh.',1,64],['h','Alt.',1,64],['frames','Frame del ciclo',1,600],['in1','Ingresso in1',-1000000,1000000],['in2','Ingresso in2',-1000000,1000000],['x','X',-256,256],['y','Y',-256,256],['seed','Seed (casuali)',0,99999]],
  sel:[['modo','Colori',[['color','Colori dello script'],['mono','Monocromatico']]]],
  chk:[['keep','Non cancellare tra un frame e l\'altro']],
  bind:['x','y','in1','in2'],color:true,colorLabel:'COLORE (PREDEFINITO DI pixel())',script:true,
  def:{w:8,h:8,frames:32,in1:0,in2:0,x:0,y:0,seed:1,modo:'color',keep:false,color:1,script:ALGO_EXAMPLES.onda.code}};

let graphs=[], ng={id:null,name:'Nuovo grafo',graph:null,dirty:false};
let ngAlgoErr={}, algoEditId=null;
let ngFrames=[],ngW=8,ngH=8,ngPal=[],ngK=0,ngLoop=null,ngPrevT=null,ngMinute=null,ngSecLoop=null;
let ngTickerHandle=null, ngTimeOrigin=0, ngCycleSec=0;
let ngVals={};
function valueAt(id){const a=ngVals[id];return a&&a.length?a[ngK%a.length]:undefined}
function fmtVal(v){return Number.isInteger(v)?String(v):String(Math.round(v*100)/100)}
function readoutText(id,unit){const v=valueAt(id);return (v===undefined?'—':fmtVal(v))+(unit?' '+unit:'')}
function bindShown(v,isFloat){return isFloat?v:Math.floor(v+0.5)}
let ngZTop=10;

const NG_CW=6000, NG_CH=4000, NG_ZOOM_MIN=0.1, NG_ZOOM_MAX=3, NG_GRID=40;
$('ngInner').style.width=NG_CW+'px'; $('ngInner').style.height=NG_CH+'px';
let ngTx=0, ngTy=0, ngScale=1;

function applyNgTransform(){
  $('ngInner').style.transform=`translate(${ngTx}px, ${ngTy}px) scale(${ngScale})`;
  // sfondo a punti che segue zoom e spostamento: un punto ogni NG_GRID px del canvas,
  // più marcato ogni 5; quelli piccoli spariscono quando lo zoom li renderebbe troppo fitti
  const minor=NG_GRID*ngScale, major=minor*5, img=[], size=[], pos=[];
  img.push('radial-gradient(circle, #5d7390 1.6px, transparent 2.2px)'); size.push(`${major}px ${major}px`); pos.push(`${ngTx-major/2}px ${ngTy-major/2}px`);
  if(minor>=12){ img.push('radial-gradient(circle, #34465c 1px, transparent 1.6px)'); size.push(`${minor}px ${minor}px`); pos.push(`${ngTx-minor/2}px ${ngTy-minor/2}px`); }
  const w=$('ngWrap'); w.style.backgroundImage=img.join(','); w.style.backgroundSize=size.join(','); w.style.backgroundPosition=pos.join(',');
}
function clampPan(){
  const wrap=$('ngWrap'), W=wrap.clientWidth, H=wrap.clientHeight;
  const CW=NG_CW*ngScale, CH=NG_CH*ngScale, M=100;
  ngTx=Math.min(M, Math.max(W-CW-M, ngTx));
  ngTy=Math.min(M, Math.max(H-CH-M, ngTy));
}
function zoomAt(clientX, clientY, factor){
  const wrap=$('ngWrap'), rect=wrap.getBoundingClientRect();
  const px=clientX-rect.left, py=clientY-rect.top;
  const wx=(px-ngTx)/ngScale, wy=(py-ngTy)/ngScale;
  ngScale=Math.max(NG_ZOOM_MIN, Math.min(NG_ZOOM_MAX, ngScale*factor));
  ngTx=px-wx*ngScale; ngTy=py-wy*ngScale;
  applyNgTransform();
}
function fitView(){
  const wrap=$('ngWrap'), W=wrap.clientWidth, H=wrap.clientHeight;
  if(!ng.graph||!ng.graph.nodes){ngTx=0;ngTy=0;ngScale=1;applyNgTransform();return}
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  for(const n of ng.graph.nodes){
    minX=Math.min(minX,n.x); minY=Math.min(minY,n.y);
    maxX=Math.max(maxX,n.x+260); maxY=Math.max(maxY,n.y+260);
  }
  if(ng.graph.out){
    minX=Math.min(minX,ng.graph.out.x); minY=Math.min(minY,ng.graph.out.y);
    maxX=Math.max(maxX,ng.graph.out.x+260); maxY=Math.max(maxY,ng.graph.out.y+260);
  }
  if(!isFinite(minX)){ngTx=0;ngTy=0;ngScale=1;applyNgTransform();return}
  const pad=30;
  const contentW=(maxX-minX)+pad*2, contentH=(maxY-minY)+pad*2;
  ngScale=Math.max(0.3, Math.min(1, Math.min(W/contentW, H/contentH)));
  ngTx=(W-contentW*ngScale)/2-(minX-pad)*ngScale;
  ngTy=(H-contentH*ngScale)/2-(minY-pad)*ngScale;
  applyNgTransform();
}

$('ngWrap').addEventListener('wheel',e=>{
  if($('nodesView').classList.contains('hidden'))return;
  const t=e.target;
  if(t && (t.tagName==='INPUT'||t.tagName==='SELECT'||t.tagName==='TEXTAREA'||t.tagName==='BUTTON'||t.isContentEditable))return;
  e.preventDefault();
  zoomAt(e.clientX, e.clientY, e.deltaY<0?1.1:1/1.1);
},{passive:false});

$('ngWrap').addEventListener('pointerdown',e=>{
  if($('nodesView').classList.contains('hidden'))return;
  if(e.pointerType==='mouse' && e.button!==0)return;
  if(e.target.closest && e.target.closest('.node'))return;
  e.preventDefault();
  const sx=e.clientX, sy=e.clientY, ox=ngTx, oy=ngTy;
  $('ngWrap').classList.add('panning');
  const move=ev=>{ ngTx=ox+(ev.clientX-sx); ngTy=oy+(ev.clientY-sy); clampPan(); applyNgTransform(); };
  const up=()=>{
    document.removeEventListener('pointermove',move);
    document.removeEventListener('pointerup',up);
    document.removeEventListener('pointercancel',up);
    $('ngWrap').classList.remove('panning');
  };
  document.addEventListener('pointermove',move);
  document.addEventListener('pointerup',up);
  document.addEventListener('pointercancel',up);
});

document.addEventListener('wheel', e=>{
  const t=e.target;
  if(!t || t.tagName!=='INPUT' || t.type!=='number')return;
  e.preventDefault(); e.stopPropagation();
  if(t.readOnly)return;
  const step=parseFloat(t.step)||1;
  const min=t.min!==''?parseFloat(t.min):-Infinity;
  const max=t.max!==''?parseFloat(t.max):Infinity;
  let v=parseFloat(t.value); if(isNaN(v))v=0;
  v+=e.deltaY<0?step:-step;
  v=Math.max(min,Math.min(max,v));
  if(t.value!==String(v)){ t.value=v; if(t.onchange)t.onchange(); }
},{passive:false,capture:true});

/* Eval JS per binding live.
   - time: t_sec su period.
   - random: usa il frame k = floor(t_sec * fps). Il ciclo visivo è step*4 secondi
     (coerente con il backend: cycle = max(16, step*fps*4) frame). */
function evalValueJS(n, tSec, fps, width=8, height=8){
  const p = n.params || {};
  const _pi = (k,lo,hi,def) => {
    let v = parseInt(p[k],10);
    if(isNaN(v)) v = def;
    return Math.max(lo, Math.min(hi, v));
  };
  if(n.type === 'random'){
    const f = Math.max(1, fps || 8);
    const step = Math.max(1, _pi('step', 1, 256, 1));
    const cycle_sec = step * 4;
    const t_mod = tSec % cycle_sec;
    const k = Math.floor(t_mod * f);
    const step_idx = Math.floor(k / step);
    const seed = _pi('seed',0,99999,1);
    const lo = _pi('min',-256,256,0), hi = _pi('max',-256,256,31);
    const minV = Math.min(lo,hi), maxV = Math.max(lo,hi);
    // FNV-1a 32-bit identico al backend
    const s = `${seed}:${step_idx}`;
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h = Math.imul((h ^ s.charCodeAt(i)) >>> 0, 16777619) >>> 0;
    }
    let v = minV + (h % (maxV - minV + 1));
    const q = _pi('quantize',0,256,1);
    if (q > 0) v = Math.round(v/q)*q;
    return v;
  }
  const period = Math.max(1, _pi('period',1,3600,4));
  const phase = ((parseFloat(p.phase)||0)%1+1)%1;
  let v01 = ((tSec/period)+phase)%1;
  const mode = p.mode || 'linear';
  if(mode==='pingpong') v01 = v01<0.5 ? v01*2 : 2-v01*2;
  else if(mode==='sine') v01 = 0.5 - 0.5*Math.cos(2*Math.PI*v01);
  const lo=_pi('min',-256,256,0), hi=_pi('max',-256,256,31);
  const q=_pi('quantize',0,256,1);
  let v = lo + (hi-lo)*v01;
  if(q>0) v = Math.round(v/q)*q;
  return Math.round(v);
}

function updateBindInputs(t){
  if(!ng.graph) return;
  const fps = Math.min(30, Math.max(1, Number($('ngFps').value) || 8));
  const [w,h] = $('ngSize').value.split('x').map(Number);
  const valueNodes={};
  for(const n of ng.graph.nodes) if(NG_VALUE_TYPES.includes(n.type)) valueNodes[n.id]=n;
  const values={};
  for(const id in valueNodes){
    if(valueNodes[id].type==='algo'){values[id+'.1']=valueAt(id+'.1');values[id+'.2']=valueAt(id+'.2')}
    else values[id]=NG_CALC_TYPES.includes(valueNodes[id].type)?valueAt(id):evalValueJS(valueNodes[id], t, fps, w, h);
  }
  document.querySelectorAll('#ngInner input[data-bind]').forEach(inp=>{
    const vid=inp.dataset.bind;
    if(values[vid]!==undefined){
      const shown=bindShown(values[vid], !!inp.dataset.float);
      if(inp.value!==String(shown)) inp.value=shown;
    }
  });
  document.querySelectorAll('#ngInner .wval[data-wid], #ngInner .hdval[data-wid]').forEach(el=>{
    const txt=readoutText(el.dataset.wid, el.dataset.unit);
    if(el.textContent!==txt) el.textContent=txt;
  });
}

function updateRangeIndicators(t_real){
  if(!ng.graph) return;
  const cycle = ngCycleSec > 0 ? ngCycleSec : 0;
  const t_sec = cycle > 0 ? (t_real % cycle) : t_real;
  document.querySelectorAll('#ngInner .node[data-id]').forEach(el=>{
    const id = el.dataset.id;
    const n = ng.graph.nodes.find(x => x.id === id);
    if(!n) return;
    const p = n.params || {};
    const rf = Number(p.range_from) || 0;
    const rt = Number(p.range_to) || 0;
    if(rf===0 && rt===0){ el.classList.remove('range-off'); return; }
    const active = (rf===0 || t_sec >= rf) && (rt===0 || t_sec <= rt);
    el.classList.toggle('range-off', !active);
  });
}

function startTick(){
  stopTick();
  if(!ngTimeOrigin || ngTimeOrigin<=0) ngTimeOrigin=performance.now();
  const step=()=>{
    if($('nodesView').classList.contains('hidden')){ngTickerHandle=null;return}
    const t=(performance.now()-ngTimeOrigin)/1000;
    updateBindInputs(t);
    updateRangeIndicators(t);
    ngTickerHandle=requestAnimationFrame(step);
  };
  ngTickerHandle=requestAnimationFrame(step);
}
function stopTick(){ if(ngTickerHandle){cancelAnimationFrame(ngTickerHandle);ngTickerHandle=null} }

async function loadGraphs(){
  try{graphs=await api('/api/graphs')}catch{graphs=[]}
  fillGraphSelect();
}
function fillGraphSelect(){
  const s=$('ngSelect');s.replaceChildren();
  const o=document.createElement('option');o.value='';o.textContent='(nuovo grafo)';s.appendChild(o);
  graphs.forEach(g=>{const x=document.createElement('option');x.value=g.id;x.textContent=g.name;x.dataset.noTranslate='1';s.appendChild(x)});
  s.value=ng.id?String(ng.id):'';
}
function defaultGraph(){
  return {nodes:[{id:'n1',type:'text',x:30,y:30,params:{...NG_TYPES.text.def}}],links:[{from:'n1',to:'out'}],out:{x:620,y:30}};
}
function setGraph(id,name,graph){
  graph.out=graph.out||{x:620,y:30};graph.links=graph.links||[];
  ng={id,name,graph,dirty:false};ngVals={};ngAlgoErr={};
  ngTimeOrigin=performance.now(); ngCycleSec=0;
  $('ngName').value=name;$('ngDirty').textContent='';fillGraphSelect();renderNodes();schedulePreview();
}
function ngStatus(t){$('ngStatus').textContent=t}
function ngChanged(){ng.dirty=true;$('ngDirty').textContent='● modifiche non salvate';schedulePreview()}
function ngDirtyOnly(){ng.dirty=true;$('ngDirty').textContent='● modifiche non salvate'}   // solo aspetto: l'anteprima non cambia
function subgraphOptions(n){
  const cur=Number(n.params.graph_id)||0;
  const list=graphs.filter(g=>g.id!==ng.id).map(g=>[g.id,g.name]);
  const opts=[[0,'— scegli un grafo —'],...list];
  if(cur>0&&!list.some(([id])=>id===cur)) opts.push([cur,(n.params.graph_name||('#'+cur))+' (non trovato)']);
  return opts;
}
function animationOptions(n){
  const cur=Number(n.params.anim_id)||0;
  const opts=[[0,'— scegli un\'animazione —'],...animations.map(a=>[Number(a.id),`${a.name} (${a.width}×8)`])];
  if(cur>0&&!opts.some(([id])=>id===cur)) opts.push([cur,(n.params.anim_name||('#'+cur))+' (non trovata)']);
  return opts;
}
function nodeLabel(n){const t=NG_TYPES[n.type];return n.name||(t?t.title:n.type)}
function refreshNodeLabels(){
  document.querySelectorAll('#ngInner .layer span[data-nid]').forEach(s=>{
    const n=ng.graph.nodes.find(x=>x.id===s.dataset.nid); if(n) s.textContent=`${s.dataset.idx}. ${nodeSummary(n)}`;
  });
  document.querySelectorAll('#ngInner input[data-bind]').forEach(i=>{
    const n=ng.graph.nodes.find(x=>x.id===i.dataset.bind); if(n) i.title='Controllato dal nodo '+nodeLabel(n);
  });
}
function startRename(n,ttl,t){
  if(ttl.querySelector('input')) return;
  const inp=document.createElement('input'); inp.type='text'; inp.maxLength=40; inp.className='rename-input';
  inp.value=n.name||''; inp.placeholder=t.title;
  ttl.textContent=''; ttl.appendChild(inp); inp.focus(); inp.select();
  let done=false;
  const finish=save=>{
    if(done) return; done=true;
    if(save){
      const v=inp.value.trim().slice(0,40);
      const old=n.name||'';
      if(v && v!==t.title) n.name=v; else delete n.name;
      if((n.name||'')!==old){ ngDirtyOnly(); }
    }
    ttl.textContent=n.name||t.title.toUpperCase();
    if(n.name) ttl.dataset.noTranslate='1'; else delete ttl.dataset.noTranslate;
    ttl.title=n.name?`${t.title} · doppio clic per rinominare`:'Doppio clic per rinominare';
    refreshNodeLabels();
  };
  inp.onkeydown=e=>{ e.stopPropagation(); if(e.key==='Enter'){e.preventDefault();finish(true)} else if(e.key==='Escape'){e.preventDefault();finish(false)} };
  inp.onblur=()=>finish(true);
  inp.onpointerdown=e=>e.stopPropagation();
}
function setAllCollapsed(v){
  if(!ng.graph) return;
  ng.graph.nodes.forEach(n=>{ if(v) n.collapsed=true; else delete n.collapsed; });
  renderNodes(); ngDirtyOnly();
}
function nextNodeId(){return 'n'+(1+Math.max(0,...ng.graph.nodes.map(n=>parseInt(String(n.id).slice(1),10)||0)))}

function nodeSummary(n){ const s=nodeSummaryBase(n); return n.name?`${n.name} — ${s}`:s; }
function nodeSummaryBase(n){
  const p = n.params || {};
  const rf = Number(p.range_from) || 0;
  const rt = Number(p.range_to) || 0;
  const rng = (rf || rt) ? ` [${rf}-${rt || '∞'}s]` : '';
  if (n.type === 'text')      return `Testo: ${(p.text || '').slice(0,14)}` + rng;
  if (n.type === 'pixel')     return `Pixel (${p.x ?? 0},${p.y ?? 0})` + rng;
  if (n.type === 'rect')      return `Rettangolo ${p.w ?? 0}×${p.h ?? 0}` + rng;
  if (n.type === 'circle')    return `Cerchio r=${p.r ?? 4}` + rng;
  if (n.type === 'gradient')  return `Gradiente ${p.w ?? 0}×${p.h ?? 0}` + rng;
  if (n.type === 'bitmap')    return `Bitmap 8x8` + rng;
  if (n.type === 'time')      return `Tempo ${p.mode || 'linear'} ${p.period || 4}s [${p.min ?? 0}..${p.max ?? 31}]` + rng;
  if (n.type === 'random')    return `Casuale [${p.min ?? 0}..${p.max ?? 31}] ogni ${p.step ?? 1} fr` + rng;
  if (n.type === 'animation') return `Animazione: ${p.anim_name || '—'} (${p.modo === 'mono' ? 'monocromatico' : 'palette'}${Number(p.hold) > 1 ? ', ×' + p.hold : ''})` + rng;
  if (n.type === 'subgraph')  return `Grafo: ${p.graph_name || '—'} (${p.size || '8x8'}, ${p.modo === 'mono' ? 'monocromatico' : 'colori'})` + rng;
  if (n.type === 'algo')      return `Algoritmico ${p.w ?? 8}×${p.h ?? 8}, ${p.frames ?? 32} fr` + rng;
  if (n.type === 'math')      return `Matematica (A ${({add:'+',sub:'−',mul:'×',div:'÷'})[p.op] || '+'} B)` + rng;
  if (n.type === 'width')     return `Larghezza (${p.misura === 'box' ? 'ingombro' : 'pixel accesi'})` + rng;
  if (n.type === 'repeat')    return `Ripetizioni ${p.nx || 3}×${p.ny || 3}` + rng;
  if (n.type === 'combine')   return `Combina (${p.modo || 'sottrai'})` + rng;
  if (n.type === 'color')     return `Colora (${p.col ?? 2})` + rng;
  if (n.type === 'move')      return `Muovi (vx:${p.vx || 0}, vy:${p.vy || 0})` + rng;
  if (n.type === 'rotate')    return `Ruota (${p.a0 || 0}° + ${p.vel || 15}°)` + rng;
  if (n.type === 'scale')     return `Scala (${p.s0 || 0.5})` + rng;
  if (n.type === 'mirror')    return `Specchia (${p.h ? 'H' : ' '}${p.v ? 'V' : ' '})` + rng;
  if (n.type === 'blink')     return `Lampeggia (${p.on ?? 1}/${p.off ?? 1})` + rng;
  if (n.type === 'trail')     return `Scia (n=${p.n ?? 3}, passo=${p.passo ?? 1})` + rng;
  return `Ora ${p.part || 'HH:MM'}` + rng;
}

function bringToFront(el){ if(!el)return; el.style.zIndex = ++ngZTop; }
function layoutPorts(){
  document.querySelectorAll('#ngInner .node[data-id]').forEach(nodeEl=>{
    const collapsed=nodeEl.classList.contains('collapsed');
    const ins=[...nodeEl.querySelectorAll('.port.i.dyn.inport')];
    ins.forEach((p,idx)=>{ p.style.top=(collapsed ? (ins.length===1 ? 8 : 2+idx*14) : 16+idx*22)+'px'; });
    nodeEl.querySelectorAll('.port.i.dyn:not(.inport)').forEach(portEl=>{
      if(collapsed){      // nodo chiuso: restano visibili solo le porte già collegate, tutte nell'intestazione
        portEl.style.display=portEl.classList.contains('bound')?'':'none';
        portEl.style.top='8px'; portEl.style.pointerEvents='none';
        return;
      }
      portEl.style.display=''; portEl.style.pointerEvents='';
      const port=portEl.dataset.port;
      const inp=nodeEl.querySelector(`input[data-port="${port}"]`);
      if(inp && inp.offsetParent){ portEl.style.top = (inp.offsetTop + inp.offsetHeight/2 - 7) + 'px'; }
    });
  });
}

function renderNodes(){
  const inner=$('ngInner');
  [...inner.querySelectorAll('.node')].forEach(e=>e.remove());
  ng.graph.nodes.forEach(n=>inner.appendChild(buildNode(n)));
  inner.appendChild(buildOut());
  layoutPorts(); drawWires();
  if(!$('nodesView').classList.contains('hidden')) startTick();
}
function mkField(label,el,wide){
  const d=document.createElement('div');if(wide)d.className='wide';
  const l=document.createElement('label');l.textContent=label;d.append(l,el);return d;
}
function colorPicker(n,field){
  const key=field||'color';
  const box=document.createElement('div');box.className='sw';
  for(let i=0;i<16;i++){
    const b=document.createElement('button');b.type='button';b.title=i===0?'0 · spento':String(i);
    b.style.background=palette[i]||'#000';if(i===0)b.textContent='✕';
    if((n.params[key]??1)===i)b.classList.add('sel');
    b.onclick=()=>{
      n.params[key]=i;
      box.querySelectorAll('button').forEach((x,j)=>x.classList.toggle('sel',j===i));
      ngChanged();
      if(n.type==='bitmap' && key==='color'){
        const editor=document.querySelector(`#ngInner .node[data-id="${n.id}"] .bitmap-editor`);
        if(editor){ editor.querySelectorAll('.bm-cell').forEach(cell=>cell.style.setProperty('--c',palette[i]||'#39c98a')); }
      }
    };
    box.appendChild(b);
  }
  return box;
}
function createBitmapEditor(n){
  const container=document.createElement('div');
  container.className='bitmap-editor';
  container.style.display='grid';
  container.style.gridTemplateColumns='repeat(8, 1fr)';
  container.style.gap='2px';
  container.style.margin='4px auto';
  const renderGrid=()=>{
    container.innerHTML='';
    const hex=(n.params.bits||'0000000000000000').padEnd(16,'0').slice(0,16);
    for(let r=0;r<8;r++){
      const byteVal=parseInt(hex.substr(r*2,2),16)||0;
      for(let c=0;c<8;c++){
        const cell=document.createElement('div');
        const isOn=(byteVal&(1<<(7-c)))!==0;
        cell.className='bm-cell'+(isOn?' on':'');
        cell.style.setProperty('--c', palette[n.params.color]||'#39c98a');
        cell.onclick=()=>{
          const newByte=byteVal^(1<<(7-c));
          const newHex=hex.substring(0,r*2)+newByte.toString(16).padStart(2,'0')+hex.substring(r*2+2);
          n.params.bits=newHex; ngChanged(); renderGrid();
        };
        container.appendChild(cell);
      }
    }
  };
  renderGrid(); return container;
}

function buildNode(n){
  const t=NG_TYPES[n.type]; n.params=n.params||{};
  const el=document.createElement('div');
  let cls = 'node';
  if(n.type==='time') cls += ' time';
  else if(n.type==='random') cls += ' random';
  else if(n.type==='width') cls += ' width';
  else if(n.type==='math') cls += ' math';
  else if(n.type==='algo') cls += ' algo';
  else if(NG_MOD_TYPES.includes(n.type)) cls += ' ' + n.type;
  el.className=cls;
  if(n.collapsed) el.classList.add('collapsed');
  el.dataset.id=n.id;
  el.style.left=n.x+'px';el.style.top=n.y+'px';
  el.style.zIndex = ++ngZTop;
  el.addEventListener('pointerdown', ()=>bringToFront(el), true);

  const hd=document.createElement('div');hd.className='hd';
  const ttl=document.createElement('span');
  ttl.className='ttl';
  ttl.textContent=n.name||t.title.toUpperCase();
  if(n.name) ttl.dataset.noTranslate='1'; else delete ttl.dataset.noTranslate;
  ttl.title=n.name?`${t.title} · doppio clic per rinominare`:'Doppio clic per rinominare';
  const actions = document.createElement('div');
  actions.style.display='flex'; actions.style.gap='4px'; actions.style.marginLeft='8px';
  const dup=document.createElement('button');dup.type='button';dup.textContent='⧉';dup.title='Duplica nodo';dup.onclick=()=>duplicateNode(n.id);
  const del=document.createElement('button');del.type='button';del.textContent='✕';del.title='Elimina';del.onclick=()=>removeNode(n.id);
  const ren=document.createElement('button');ren.type='button';ren.textContent='✎';ren.title='Rinomina';ren.onclick=()=>startRename(n,ttl,t);
  const tg=document.createElement('button');tg.type='button';tg.className='tg';
  tg.textContent=n.collapsed?'▸':'▾';tg.title=n.collapsed?'Espandi':'Comprimi';
  tg.onclick=()=>{
    n.collapsed=!n.collapsed; if(!n.collapsed) delete n.collapsed;
    el.classList.toggle('collapsed',!!n.collapsed);
    tg.textContent=n.collapsed?'▸':'▾'; tg.title=n.collapsed?'Espandi':'Comprimi';
    layoutPorts(); drawWires(); ngDirtyOnly();
  };
  hd.addEventListener('dblclick',e=>{ if(e.target.closest&&e.target.closest('button,input')) return; startRename(n,ttl,t); });
  actions.append(ren, dup, del); hd.append(tg, ttl);
  if(t.readout){
    const hv=document.createElement('span');hv.className='hdval';hv.dataset.wid=n.id;hv.dataset.unit=t.unit||'';
    hv.textContent=readoutText(n.id,t.unit);hd.appendChild(hv);
  }
  hd.appendChild(actions);

  const port=document.createElement('div');
  if(n.type==='time'){ port.className='port o time-out'; port.title='Trascina sulle porte blu'; }
  else if(n.type==='random'){ port.className='port o random-out'; port.title='Trascina sulle porte blu'; }
  else if(n.type==='width'){ port.className='port o width-out'; port.title='Trascina sulle porte blu'; }
  else if(n.type==='math'){ port.className='port o math-out'; port.title='Trascina sulle porte blu'; }
  else{ port.className='port o'; port.title='Trascina sull\'Uscita o su una porta'; port.ondblclick=()=>toggleLink(n.id); }
  port.onpointerdown=e=>startLink(e,n.id);
  el.appendChild(port);
  if(n.type==='algo'){
    ['1','2'].forEach((outPort,idx)=>{
      const op=document.createElement('div');op.className='port o algo-value-out';op.dataset.port=outPort;
      op.title=`Uscita numerica out${outPort}`;op.style.top=(34+idx*22)+'px';op.style.background='#e879f9';
      op.onpointerdown=e=>startLink(e,n.id,outPort);
      const tag=document.createElement('span');tag.className='algo-port-label';tag.textContent=outPort;op.appendChild(tag);el.appendChild(op);
    });
  }

  const bd=document.createElement('div');bd.className='bd';
  if(t.readout){
    const r=document.createElement('div');r.className='wval';r.dataset.wid=n.id;r.dataset.unit=t.unit||'';
    r.textContent=readoutText(n.id,t.unit);bd.appendChild(r);
  }
  if(t.script){
    const w=document.createElement('div');w.className='wide';
    const b=document.createElement('button');b.type='button';b.className='small algo-edit';b.textContent='✎ Modifica script (Python)';
    b.onclick=()=>openAlgoEditor(n.id);
    const info=document.createElement('div');info.className='algo-info';info.dataset.algoInfo=n.id;info.textContent=algoInfoText(n);
    const outs=document.createElement('div');outs.style.cssText='display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:5px';
    ['1','2'].forEach(k=>{const v=document.createElement('div');v.className='wval';v.dataset.wid=n.id+'.'+k;v.textContent='out'+k+': '+readoutText(n.id+'.'+k,'');outs.appendChild(v)});
    const st=document.createElement('div');st.className='algo-status';st.dataset.algoStatus=n.id;
    st.textContent=ngAlgoErr[n.id]?'⚠ '+ngAlgoErr[n.id]:'';
    w.append(b,info,outs,st);bd.appendChild(w);
  }
  (t.txt||[]).forEach(([k,lb,max])=>{
    const i=document.createElement('input');i.type='text';i.maxLength=max;i.value=n.params[k]??'';
    i.oninput=()=>{n.params[k]=i.value;ngChanged()};
    bd.appendChild(mkField(lb,i,true));
  });
  (t.sel||[]).forEach(([k,lb,optsDef])=>{
    const opts=typeof optsDef==='function'?optsDef(n):optsDef;
    const s=document.createElement('select');
    opts.forEach(([v,x])=>{const o=document.createElement('option');o.value=v;o.textContent=x;s.appendChild(o)});
    s.value=n.params[k]??opts[0][0];
    s.onchange=()=>{
      if(k==='anim_id'&&n.type==='animation'){
        const id=Number(s.value)||0; n.params.anim_id=id;
        n.params.anim_name=(animations.find(a=>Number(a.id)===id)||{}).name||'';
      } else if(k==='graph_id'&&n.type==='subgraph'){
        const id=Number(s.value)||0; n.params.graph_id=id;
        n.params.graph_name=(graphs.find(g=>g.id===id)||{}).name||'';
      } else n.params[k]=s.value;
      ngChanged();
      if(k==='modo'&&(n.type==='subgraph'||n.type==='animation')){
        const nodeEl=document.querySelector(`#ngInner .node[data-id="${n.id}"]`);
        if(nodeEl) nodeEl.querySelectorAll('.color-param').forEach(el2=>el2.classList.toggle('hidden-param', s.value!=='mono'));
      }
      if(k==='color_mode' && n.type==='repeat'){
        const nodeEl=document.querySelector(`#ngInner .node[data-id="${n.id}"]`);
        if(nodeEl){ nodeEl.querySelectorAll('.color-param').forEach(el2=>{
          el2.classList.toggle('hidden-param', s.value==='original');
        });}
      }
    };
    bd.appendChild(mkField(lb,s,opts.length>2));
  });
  (t.num||[]).forEach(([k,lb,min,max])=>{
    const i=document.createElement('input');i.type='number';i.min=min;i.max=max;i.value=n.params[k]??0;
    i.dataset.port=k;
    if(t.float){ i.step='any'; i.dataset.float='1'; }
    let boundLink=null;
    if(t.bind && t.bind.includes(k)){
      boundLink=ng.graph.links.find(l=>l.to===n.id && l.to_port===k) || null;
      if(boundLink){
        i.dataset.bind=boundLink.from_port?`${boundLink.from}.${boundLink.from_port}`:boundLink.from;
        i.readOnly=true; i.classList.add('bound-input');
        i.title='Controllato dal nodo '+boundLink.from;
        const vn=ng.graph.nodes.find(x=>x.id===boundLink.from);
        if(vn){
          i.title='Controllato dal nodo '+nodeLabel(vn);
          const _fps = Math.min(30, Math.max(1, Number($('ngFps').value) || 8));
          const [_w,_h] = $('ngSize').value.split('x').map(Number);
          const _v = NG_CALC_TYPES.includes(vn.type) ? valueAt(boundLink.from_port?`${vn.id}.${boundLink.from_port}`:vn.id) : evalValueJS(vn, (performance.now()-ngTimeOrigin)/1000, _fps, _w, _h);
          if(_v!==undefined) i.value = bindShown(_v, !!t.float);
        }
      }
    }
    i.onchange=()=>{ const raw=t.float?parseFloat(i.value):parseInt(i.value,10); const v=Math.min(max,Math.max(min,raw||0)); i.value=v;n.params[k]=v;ngChanged(); if(n.type==='algo') refreshAlgoInfo(n.id); };
    const f=mkField(lb,i,true);
    if(k==='color_start' || k==='color_step'){
      f.classList.add('color-param');
      if(n.type==='repeat' && n.params.color_mode==='original') f.classList.add('hidden-param');
    }
    if(boundLink){
      const lbl=f.querySelector('label');
      lbl.style.display='flex';lbl.style.alignItems='center';lbl.style.justifyContent='space-between';
      const btn=document.createElement('button');
      btn.type='button';btn.className='unbind';btn.textContent='✕';btn.title='Scollega';
      btn.onclick=(ev)=>{ev.preventDefault();ev.stopPropagation();removeBind(n.id,k)};
      lbl.appendChild(btn);
    }
    bd.appendChild(f);
  });
  if(t.rep){
    const rep=[['nx','N.x',1,32],['ny','N.y',1,32],['dx','Δx',-256,256],['dy','Δy',-256,256]];
    rep.forEach(([k,lb,min,max])=>{
      const i=document.createElement('input');i.type='number';i.min=min;i.max=max;
      i.value=n.params[k]??(k==='nx'||k==='ny'?1:1);
      i.onchange=()=>{ const v=Math.min(max,Math.max(min,parseInt(i.value,10)||0)); i.value=v;n.params[k]=v;ngChanged(); };
      bd.appendChild(mkField(lb,i,true));
    });
  }
  (t.chk||[]).forEach(([k,lb])=>{
    const w=document.createElement('label');w.className='check wide';
    const c=document.createElement('input');c.type='checkbox';c.checked=!!n.params[k];
    c.onchange=()=>{n.params[k]=c.checked;ngChanged()};
    w.append(c,' '+lb);bd.appendChild(w);
  });
  if(n.type==='bitmap') bd.appendChild(mkField('DISEGNO (8×8)', createBitmapEditor(n), true));
  if(t.color){
    const cf=mkField(t.colorLabel||'COLORE (PALETTE)',colorPicker(n,'color'),true);
    if(n.type==='subgraph'||n.type==='animation'){ cf.classList.add('color-param'); if((n.params.modo||'color')!=='mono') cf.classList.add('hidden-param'); }
    bd.appendChild(cf);
  }
  if(t.colors){
    const row=document.createElement('div');row.className='wide';
    row.style.display='grid';row.style.gridTemplateColumns='1fr 1fr';row.style.gap='6px';
    row.append(mkField('COLORE A',colorPicker(n,'color_a')),mkField('COLORE B',colorPicker(n,'color_b')));
    bd.appendChild(row);
  }

  const rngRow = document.createElement('div');
  rngRow.className='wide';
  rngRow.style.cssText='margin-top:6px;padding-top:6px;border-top:1px solid #2d3a4b;display:grid;grid-template-columns:1fr 1fr;gap:6px;';
  const rfWrap = document.createElement('div');
  const rfLbl = document.createElement('label'); rfLbl.textContent='DA (s)';
  const rfInp = document.createElement('input'); rfInp.type='number'; rfInp.min=0; rfInp.max=3600;
  rfInp.value = n.params.range_from ?? 0;
  rfInp.onchange = () => { const v=Math.max(0,parseInt(rfInp.value,10)||0); rfInp.value=v; n.params.range_from=v; ngChanged(); };
  rfWrap.append(rfLbl, rfInp);
  const rtWrap = document.createElement('div');
  const rtLbl = document.createElement('label'); rtLbl.textContent='A (s)';
  const rtInp = document.createElement('input'); rtInp.type='number'; rtInp.min=0; rtInp.max=3600;
  rtInp.value = n.params.range_to ?? 0;
  rtInp.onchange = () => { const v=Math.max(0,parseInt(rtInp.value,10)||0); rtInp.value=v; n.params.range_to=v; ngChanged(); };
  rtWrap.append(rtLbl, rtInp);
  rngRow.append(rfWrap, rtWrap);
  bd.appendChild(rngRow);

  el.appendChild(hd);el.appendChild(bd);

  let inportIdx = 0;
  (t.ins||[]).forEach((pname)=>{
    const pp=document.createElement('div');
    pp.className='port i dyn inport';
    pp.style.top=(16 + inportIdx*22) + 'px';
    pp.dataset.for=n.id;pp.dataset.port=pname;
    pp.title=`Ingresso ${pname.toUpperCase()}`;
    if(ng.graph.links.some(l=>l.to===n.id && l.to_port===pname)) pp.classList.add('bound');
    pp.addEventListener('pointerdown',ev=>{ev.stopPropagation();bringToFront(el)});
    pp.ondblclick=()=>{
      const before=ng.graph.links.length;
      ng.graph.links=ng.graph.links.filter(l=>!(l.to===n.id && l.to_port===pname));
      if(ng.graph.links.length!==before) relinked();
    };
    el.appendChild(pp); inportIdx++;
  });

  (t.bind||[]).forEach((pname)=>{
    const pp=document.createElement('div');
    pp.className='port i dyn'; pp.style.top='0px';
    pp.dataset.for=n.id;pp.dataset.port=pname;
    pp.title=`Ingresso ${pname}`;
    if(ng.graph.links.some(l=>l.to===n.id && l.to_port===pname)) pp.classList.add('bound');
    pp.addEventListener('pointerdown', ev=>{ev.stopPropagation();bringToFront(el)});
    pp.ondblclick=()=>removeBind(n.id,pname);
    el.appendChild(pp);
  });

  dragNode(hd,n,el);
  return el;
}

function buildOut(){
  const o=ng.graph.out,el=document.createElement('div');el.className='node out';el.dataset.id='out';
  el.style.left=o.x+'px';el.style.top=o.y+'px';
  el.style.zIndex = ++ngZTop;
  el.addEventListener('pointerdown', ()=>bringToFront(el), true);
  const hd=document.createElement('div');hd.className='hd';hd.innerHTML='<span>USCITA</span>';
  const port=document.createElement('div');port.className='port i';
  const bd=document.createElement('div');bd.className='bd';
  const lbl=document.createElement('label');lbl.className='wide';lbl.textContent='STRATI (DAL FONDO ALLA CIMA)';bd.appendChild(lbl);
  const layers=ng.graph.links.filter(l=>l.to==='out');
  if(!layers.length){const p=document.createElement('div');p.className='muted wide';p.textContent='Nessun nodo collegato.';bd.appendChild(p)}
  layers.forEach((l,i)=>{
    const n=ng.graph.nodes.find(x=>x.id===l.from);if(!n)return;
    const r=document.createElement('div');r.className='layer wide';
    const s=document.createElement('span');s.textContent=`${i+1}. ${nodeSummary(n)}`;s.dataset.nid=n.id;s.dataset.idx=i+1;
    const up=document.createElement('button');up.type='button';up.textContent='↑';up.onclick=()=>moveLayer(l,-1);
    const dn=document.createElement('button');dn.type='button';dn.textContent='↓';dn.onclick=()=>moveLayer(l,1);
    const x=document.createElement('button');x.type='button';x.textContent='✕';x.title='Scollega';x.onclick=()=>toggleLink(l.from);
    r.append(s,up,dn,x);bd.appendChild(r);
  });
  el.append(hd,port,bd);dragNode(hd,o,el);
  return el;
}

function dragNode(hd,pos,el){
  hd.onpointerdown=e=>{
    if(e.target.tagName==='BUTTON'||e.target.tagName==='INPUT')return;
    e.stopPropagation(); bringToFront(el);
    const sx=e.clientX,sy=e.clientY,ox=pos.x,oy=pos.y;
    hd.setPointerCapture&&hd.setPointerCapture(e.pointerId);
    hd.onpointermove=ev=>{
      pos.x=Math.max(0,Math.min(NG_CW-230,Math.round(ox+(ev.clientX-sx)/ngScale)));
      pos.y=Math.max(0,Math.min(NG_CH-200,Math.round(oy+(ev.clientY-sy)/ngScale)));
      el.style.left=pos.x+'px';el.style.top=pos.y+'px';drawWires();
    };
    hd.onpointerup=hd.onpointercancel=()=>{hd.onpointermove=null;ng.dirty=true;$('ngDirty').textContent='● modifiche non salvate'};
  };
}
function drawPath(p,a,b){const dx=Math.max(40,Math.abs(b.x-a.x)/2);p.setAttribute('d',`M${a.x},${a.y} C${a.x+dx},${a.y} ${b.x-dx},${b.y} ${b.x},${b.y}`)}
function drawWires(){
  const svg=$('ngWires');svg.replaceChildren();
  ng.graph.links.forEach(l=>{
    const fromEl=document.querySelector(`#ngInner .node[data-id="${l.from}"]`);
    if(!fromEl)return;
    const sourcePort=l.from_port?fromEl.querySelector(`.algo-value-out[data-port="${l.from_port}"]`):null;
    const from={x:fromEl.offsetLeft+fromEl.offsetWidth-2,y:fromEl.offsetTop+(sourcePort?sourcePort.offsetTop+sourcePort.offsetHeight/2:16)};
    if(l.to==='out'){
      const out=document.querySelector('#ngInner .node.out');
      if(!out)return;
      const to={x:out.offsetLeft+2,y:out.offsetTop+16};
      const p=document.createElementNS(SVGNS,'path');
      drawPath(p,from,to);svg.appendChild(p);
      return;
    }
    const toNode=document.querySelector(`#ngInner .node[data-id="${l.to}"]`);
    if(!toNode)return;
    const portEl=toNode.querySelector(`.port.i.dyn[data-port="${l.to_port}"]`);
    if(!portEl)return;
    const to={
      x:toNode.offsetLeft + portEl.offsetLeft + portEl.offsetWidth/2,
      y:toNode.offsetTop  + portEl.offsetTop  + portEl.offsetHeight/2,
    };
    const p=document.createElementNS(SVGNS,'path');
    if(['in','a','b'].includes(l.to_port)){
      p.setAttribute('stroke','#ffa500'); p.setAttribute('stroke-width','2');
    }else{
      p.setAttribute('stroke','#2f8bff'); p.setAttribute('stroke-dasharray','4 3');
    }
    drawPath(p,from,to);svg.appendChild(p);
  });
}
function linkMakesCycle(fromId,toId){
  if(fromId===toId) return true;
  const seen=new Set(), st=[toId];
  while(st.length){
    const c=st.pop(); if(c===fromId) return true;
    if(seen.has(c)) continue; seen.add(c);
    ng.graph.links.forEach(l=>{ if(l.from===c && l.to!=='out') st.push(l.to); });
  }
  return false;
}
function startLink(e,fromId,fromPort=null){
  e.preventDefault();e.stopPropagation();
  const fromEl=document.querySelector(`#ngInner .node[data-id="${fromId}"]`);
  bringToFront(fromEl);
  const inner=$('ngInner');
  const sourcePort=fromPort?fromEl.querySelector(`.algo-value-out[data-port="${fromPort}"]`):null;
  const a={x:fromEl.offsetLeft+fromEl.offsetWidth-2,y:fromEl.offsetTop+(sourcePort?sourcePort.offsetTop+sourcePort.offsetHeight/2:16)};
  const tmp=document.createElementNS(SVGNS,'path');tmp.setAttribute('class','tmp');
  $('ngWires').appendChild(tmp);
  const move=ev=>{
    const r=inner.getBoundingClientRect();
    drawPath(tmp,a,{x:(ev.clientX-r.left)/ngScale,y:(ev.clientY-r.top)/ngScale});
  };
  const up=ev=>{
    document.removeEventListener('pointermove',move);
    document.removeEventListener('pointerup',up);
    tmp.remove();
    const hit=document.elementFromPoint(ev.clientX,ev.clientY);
    if(!hit||!hit.closest)return;
    const src=ng.graph.nodes.find(x=>x.id===fromId);
    if(!src)return;
    const portEl=hit.closest('.port.i.dyn');
    if(portEl){
      const toId=portEl.dataset.for,port=portEl.dataset.port;
      if(linkMakesCycle(fromId,toId)){ ngStatus('Collegamento non valido: creerebbe un anello.'); return; }
      if(['in','a','b'].includes(port)){
        if(!NG_DRAW_TYPES.includes(src.type) && !NG_MOD_TYPES.includes(src.type)){
          ngStatus('Questa porta accetta solo nodi di disegno o modificatori.'); return;
        }
        ng.graph.links=ng.graph.links.filter(l=>!(l.to===toId&&l.to_port===port));
        ng.graph.links.push({from:fromId,to:toId,to_port:port,...(fromPort?{from_port:fromPort}:{})}); relinked();
      }else{
        if(!NG_VALUE_TYPES.includes(src.type)){
          ngStatus('Solo un nodo numerico può essere collegato a una porta blu.'); return;
        }
        if(src.type==='algo'&&!fromPort){ngStatus('Per collegare un valore usa la porta viola numerata 1 o 2.');return}
        ng.graph.links=ng.graph.links.filter(l=>!(l.to===toId&&l.to_port===port));
        ng.graph.links.push({from:fromId,to:toId,to_port:port,...(fromPort?{from_port:fromPort}:{})}); relinked();
      }
      return;
    }
    const outEl=hit.closest('.node.out');
    if(outEl){
      if(NG_VALUE_TYPES.includes(src.type) && !NG_DRAW_TYPES.includes(src.type)){
        ngStatus('Il nodo '+src.type+' non si collega all\'Uscita.'); return;
      }
      if(!ng.graph.links.some(l=>l.from===fromId&&l.to==='out')){
        ng.graph.links.push({from:fromId,to:'out'}); relinked();
      }
    }
  };
  document.addEventListener('pointermove',move);
  document.addEventListener('pointerup',up);
}
function removeBind(toId,port){
  const before=ng.graph.links.length;
  ng.graph.links=ng.graph.links.filter(l=>!(l.to===toId&&l.to_port===port));
  if(ng.graph.links.length!==before) relinked();
}
function relinked(){renderNodes();ngChanged()}
function toggleLink(id){
  const i=ng.graph.links.findIndex(l=>l.from===id&&l.to==='out');
  if(i>=0)ng.graph.links.splice(i,1);else ng.graph.links.push({from:id,to:'out'});
  relinked();
}
function moveLayer(link,d){
  const L=ng.graph.links,i=L.indexOf(link),outs=L.map((l,k)=>l.to==='out'?k:-1).filter(k=>k>=0);
  const pos=outs.indexOf(i),j=pos+d;if(j<0||j>=outs.length)return;
  [L[outs[pos]],L[outs[j]]]=[L[outs[j]],L[outs[pos]]];relinked();
}
function removeNode(id){
  ng.graph.nodes=ng.graph.nodes.filter(n=>n.id!==id);
  ng.graph.links=ng.graph.links.filter(l=>l.from!==id&&l.to!==id);
  relinked();
}
function duplicateNode(id){
  const n=ng.graph.nodes.find(x=>x.id===id); if(!n)return;
  const newId=nextNodeId();
  const clone={id:newId,type:n.type,x:n.x+20,y:n.y+20,params:JSON.parse(JSON.stringify(n.params))};
  if(n.name) clone.name=n.name;
  if(n.collapsed) clone.collapsed=true;
  ng.graph.nodes.push(clone);
  ng.graph.links.filter(l=>l.to===id).forEach(l=>{
    ng.graph.links.push({from:l.from,to:newId,to_port:l.to_port});
  });
  relinked();
  const el=document.querySelector(`#ngInner .node[data-id="${newId}"]`);
  if(el) bringToFront(el);
}
function addNode(type){
  const i=ng.graph.nodes.length;
  const id=nextNodeId();
  const n={id,type,x:30+(i%3)*240,y:30+Math.floor(i/3)*250,params:{...NG_TYPES[type].def}};
  ng.graph.nodes.push(n); relinked();
  const el=document.querySelector(`#ngInner .node[data-id="${id}"]`);
  if(el) bringToFront(el);
}
async function selectGraph(){
  const v=$('ngSelect').value;
  if(ng.dirty&&!confirm('Hai modifiche non salvate: verranno perse. Continuare?')){fillGraphSelect();return}
  if(!v){setGraph(null,'Nuovo grafo',defaultGraph());fitView();return}
  try{const g=await api('/api/graphs/'+v);setGraph(g.id,g.name,g.graph);fitView()}
  catch(e){ngStatus('Errore: '+e.message)}
}
async function saveGraph(asNew=false){
  const name=$('ngName').value.trim()||'Senza nome';
  try{
    const id=asNew?null:ng.id;
    const g=await api(id?'/api/graphs/'+id:'/api/graphs',{method:id?'PUT':'POST',headers:JSONH,body:JSON.stringify({name,graph:ng.graph})});
    ng.id=g.id;ng.name=g.name;ng.dirty=false;$('ngDirty').textContent='';
    await loadGraphs();ngStatus('Grafo salvato.');
  }catch(e){ngStatus('Errore salvataggio: '+e.message)}
}
/* ── Import / esportazione JSON ─────────────────────────────────────────────
   File singolo: {format:'pixelanim-graph', version, name, graph}  (accettato anche il solo grafo {nodes, links, out})
   Backup:       {format:'pixelanim-graphs', version, graphs:[{name, graph}, …]} */
const GRAPH_FILE_VERSION=1;
function downloadJSON(filename,obj){
  const blob=new Blob([JSON.stringify(obj,null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename;
  document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
function safeFileName(s){
  return String(s||'').normalize('NFKD').replace(/[^\w\- ]+/g,'').trim().replace(/\s+/g,'-').slice(0,60)||'grafo';
}
function exportGraph(){
  if(!ng.graph) return;
  const name=$('ngName').value.trim()||'Senza nome';
  downloadJSON(`${safeFileName(name)}.pixelanim-graph.json`,
    {format:'pixelanim-graph',version:GRAPH_FILE_VERSION,name,exported_at:new Date().toISOString(),graph:ng.graph});
  ngStatus('Grafo esportato.');
}
async function exportAllGraphs(){
  try{
    const d=await api('/api/graphs/export');
    if(!d.graphs.length){ngStatus('Nessun grafo salvato da esportare.');return}
    downloadJSON(`pixelanim-grafi-${new Date().toISOString().slice(0,10)}.json`,d);
    ngStatus(`Esportati ${d.graphs.length} grafi.`);
  }catch(e){ngStatus('Errore esportazione: '+e.message)}
}
function readFileText(file){
  return new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(String(r.result));r.onerror=()=>rej(r.error);r.readAsText(file)});
}
async function importBundle(data){
  const items=data.graphs.filter(g=>g&&typeof g==='object'&&g.graph&&typeof g.graph==='object')
    .map(g=>({name:String(g.name||'Senza nome').slice(0,200),graph:g.graph}));
  if(!items.length){ngStatus('Il file non contiene grafi.');return}
  if(!confirm(`Importare ${items.length} grafi nel database? Quelli con un nome già esistente verranno rinominati.`)) return;
  try{
    const r=await api('/api/graphs/import',{method:'POST',headers:JSONH,body:JSON.stringify({graphs:items})});
    await loadGraphs();
    const bad=r.results.filter(x=>!x.ok);
    const warn=r.results.filter(x=>x.ok&&x.warnings&&x.warnings.length);
    ngStatus(`Importati ${r.imported} grafi`+(bad.length?`; scartati ${bad.length}: `+bad.map(b=>`${b.name} (${b.error})`).join('; '):'.')
      +(warn.length?' Attenzione: '+warn.map(x=>`${x.name}: ${x.warnings.join(', ')}`).join('; ')+'.':''));
  }catch(e){ngStatus('Importazione non riuscita: '+e.message)}
}
async function importFile(file){
  if(!file) return;
  if(file.size>5_000_000){ngStatus('File troppo grande.');return}
  let data;
  try{data=JSON.parse(await readFileText(file))}catch{ngStatus('File non valido: non è un JSON.');return}
  if(!data||typeof data!=='object'||Array.isArray(data)){ngStatus('File non riconosciuto: non contiene un grafo.');return}
  if(Number(data.version)>GRAPH_FILE_VERSION){ngStatus('File creato con una versione più recente: aggiorna Broadcast per importarlo.');return}
  if(Array.isArray(data.graphs)){
    if(data.format&&data.format!=='pixelanim-graphs'){ngStatus('Formato file non riconosciuto.');return}
    await importBundle(data);return;
  }
  let graph,name;
  const base=file.name.replace(/\.pixelanim-graph\.json$|\.json$/i,'');
  if(data.graph&&typeof data.graph==='object'){
    if(data.format&&data.format!=='pixelanim-graph'){ngStatus('Formato file non riconosciuto.');return}
    graph=data.graph;name=data.name;
  }else if(Array.isArray(data.nodes)){graph=data;name=base}
  else{ngStatus('File non riconosciuto: non contiene un grafo.');return}
  if(ng.dirty&&!confirm('Hai modifiche non salvate: verranno perse. Continuare?')) return;
  try{
    const r=await api('/api/graphs/validate',{method:'POST',headers:JSONH,body:JSON.stringify({graph})});
    setGraph(null,String(name||base||'Grafo importato').trim().slice(0,60)||'Grafo importato',r.graph);
    fitView();ngDirtyOnly();
    ngStatus('Grafo importato: controllalo e premi Salva per tenerlo.'+(r.warnings&&r.warnings.length?' Attenzione: '+r.warnings.join('; ')+'.':''));
  }catch(e){ngStatus('Importazione non riuscita: '+e.message)}
}
async function deleteGraph(){
  if(!ng.id){setGraph(null,'Nuovo grafo',defaultGraph());fitView();return}
  if(!confirm(`Eliminare il grafo "${ng.name}"?`))return;
  try{
    await api('/api/graphs/'+ng.id,{method:'DELETE'});
    await loadGraphs();
    if(graphs.length){const g=await api('/api/graphs/'+graphs[0].id);setGraph(g.id,g.name,g.graph)}
    else setGraph(null,'Nuovo grafo',defaultGraph());
    fitView();ngStatus('Grafo eliminato.');
  }catch(e){ngStatus('Errore: '+e.message)}
}
function graphHasSeconds(){
  if(!ng.graph||!ng.graph.nodes)return false;
  const findNodes=(nid)=>{
    const n=ng.graph.nodes.find(x=>x.id===nid); if(!n)return [];
    let res=[n];
    const ins=ng.graph.links.filter(l=>l.to===nid && ['in','a','b'].includes(l.to_port));
    for(const i of ins) res=res.concat(findNodes(i.from));
    return res;
  };
  const outs=(ng.graph.links||[]).filter(l=>l.to==='out').map(l=>l.from);
  for(const nid of outs){
    const tree=findNodes(nid);
    for(const n of tree){
      if(n.type==='clock'&&['SS','HH:MM:SS'].includes((n.params||{}).part))return true;
    }
  }
  return false;
}
function schedulePreview(){clearTimeout(ngPrevT);ngPrevT=setTimeout(loadGraphPreview,250)}
async function loadGraphPreview(){
  if(!ng.graph||$('nodesView').classList.contains('hidden'))return;
  const [w,h]=$('ngSize').value.split('x').map(Number);
  const fps=Math.min(30,Math.max(1,Number($('ngFps').value)||8));
  try{
    const d=await api('/api/graphs/render',{method:'POST',headers:JSONH,body:JSON.stringify({graph:ng.graph,width:w,height:h,fps})});
    ngFrames=d.frames;ngW=d.width;ngH=d.height;ngPal=d.palette;ngK=0;ngVals=d.values||{};
    ngAlgoErr=d.algo_errors||{};refreshAlgoStatus();
    ngCycleSec=d.cycle_sec||0;
    $('ngInfo').textContent=`${d.total} frame per ciclo`+(d.total>400?' (anteprima dei primi 400)':'')+' · anteprima a colori'
      +(d.warnings&&d.warnings.length?' · ⚠ '+d.warnings.join('; '):'');
    clearInterval(ngLoop); drawFrame();
    if(ngFrames.length>1)ngLoop=setInterval(()=>{ngK=(ngK+1)%ngFrames.length;drawFrame()},1000/fps);
    if(graphHasSeconds()){
      if(!ngSecLoop)ngSecLoop=setInterval(loadGraphPreview,1000);
    }else if(ngSecLoop){ clearInterval(ngSecLoop);ngSecLoop=null; }
  }catch(e){$('ngInfo').textContent='Anteprima non disponibile: '+e.message;if(algoEditId)setAlgoMsg('Anteprima non disponibile: '+e.message,true)}
}
function drawFrame(){ paintFrame($('ngPreview'),240); if(algoEditId) paintFrame($('algoPrev'),270); }
function paintFrame(cv,maxPx){
  if(!ngFrames.length||!cv||!cv.getContext)return;
  const ctx=cv.getContext('2d');if(!ctx)return;
  const sc=Math.max(8,Math.min(24,Math.floor(maxPx/ngW)));
  if(cv.width!==ngW*sc||cv.height!==ngH*sc){cv.width=ngW*sc;cv.height=ngH*sc}
  ctx.fillStyle='#000';ctx.fillRect(0,0,cv.width,cv.height);
  const f=ngFrames[ngK%ngFrames.length]; if(!f)return;
  for(let i=0;i<ngW*ngH;i++){
    const k=parseInt(f[i],16);
    if(k>0){ctx.fillStyle=ngPal[k]||'#fff';ctx.fillRect((i%ngW)*sc+1,Math.floor(i/ngW)*sc+1,sc-2,sc-2)}
  }
}
/* ── Editor dello script del nodo Algoritmico ───────────────────────────── */
let algoT=null;
function algoLines(n){return String((n.params&&n.params.script)||'').split('\n').length}
function algoInfoText(n){const p=n.params||{};return `${algoLines(n)} righe · ${p.w??8}×${p.h??8} · ${p.frames??32} frame`}
function refreshAlgoInfo(id){
  const n=ng.graph.nodes.find(x=>x.id===id);if(!n)return;
  document.querySelectorAll('#ngInner [data-algo-info]').forEach(e=>{if(e.dataset.algoInfo===id)e.textContent=algoInfoText(n)});
}
function refreshAlgoStatus(){
  document.querySelectorAll('#ngInner [data-algo-status]').forEach(e=>{
    const m=ngAlgoErr[e.dataset.algoStatus];e.textContent=m?'⚠ '+m:'';
  });
  if(algoEditId){
    const m=ngAlgoErr[algoEditId];
    setAlgoMsg(m?m:'✓ Nessun errore',!!m);
    updateGutter();
  }
}
function setAlgoMsg(text,isErr){const e=$('algoErr');e.textContent=text;e.classList.toggle('ok',!isErr)}
function algoErrLine(){
  const m=algoEditId&&ngAlgoErr[algoEditId];const r=m&&/riga (\d+)/.exec(m);return r?Number(r[1]):0;
}
function updateGutter(){
  const ta=$('algoText'),g=$('algoGutter');
  const n=ta.value.split('\n').length,bad=algoErrLine();
  let h='';for(let i=1;i<=n;i++)h+=i===bad?`<div class="err">${i}</div>`:`<div>${i}</div>`;
  g.innerHTML=h;g.style.paddingBottom=ta.clientHeight+'px';g.scrollTop=ta.scrollTop;
}
function algoInsert(ta,text){
  // execCommand mantiene la cronologia di annulla/ripeti; se non c'è si ripiega su setRangeText
  let done=false;
  try{done=typeof document.execCommand==='function'&&document.execCommand('insertText',false,text)}catch{}
  if(!done){
    ta.setRangeText(text,ta.selectionStart,ta.selectionEnd,'end');
    ta.dispatchEvent(new Event('input'));
  }
}
function algoIndent(ta,back){
  const v=ta.value,s=ta.selectionStart,e=ta.selectionEnd;
  if(s===e&&!back){algoInsert(ta,'    ');return}
  const ls=v.lastIndexOf('\n',s-1)+1;
  const end0=(e>s&&v[e-1]==='\n')?e-1:e;
  let le=v.indexOf('\n',end0);if(le<0)le=v.length;
  let dStart=0,dTotal=0;
  const out=v.slice(ls,le).split('\n').map((ln,i)=>{
    if(!back){if(i===0)dStart=4;dTotal+=4;return '    '+ln}
    const m=/^( {1,4}|\t)/.exec(ln),r=m?m[0].length:0;
    if(i===0)dStart=-r;dTotal-=r;return ln.slice(r);
  }).join('\n');
  ta.setSelectionRange(ls,le);algoInsert(ta,out);
  ta.setSelectionRange(Math.max(ls,s+dStart),e+dTotal);
}
function openAlgoEditor(id){
  const n=ng.graph.nodes.find(x=>x.id===id);if(!n)return;
  algoEditId=id;
  $('algoTitle').textContent='SCRIPT · '+nodeLabel(n).toUpperCase();
  const ta=$('algoText');ta.value=n.params.script||'';
  const as=$('algoSize');as.replaceChildren(...[...$('ngSize').options].map(o=>new Option(o.text,o.value)));as.value=$('ngSize').value;
  $('algoModal').classList.remove('hidden');document.body.style.overflow='hidden';
  const m=ngAlgoErr[id];setAlgoMsg(m?m:'✓ Nessun errore',!!m);
  updateGutter();drawFrame();ta.focus();ta.setSelectionRange(0,0);ta.scrollTop=0;
}
function closeAlgoEditor(){
  if(!algoEditId)return;
  const id=algoEditId;algoEditId=null;
  $('algoModal').classList.add('hidden');document.body.style.overflow='';
  refreshAlgoInfo(id);refreshAlgoStatus();
}
(function initAlgoEditor(){
  const ta=$('algoText'),sel=$('algoExamples');
  Object.entries(ALGO_EXAMPLES).forEach(([k,v])=>{const o=document.createElement('option');o.value=k;o.textContent=v.name;sel.appendChild(o)});
  ta.addEventListener('input',()=>{
    const n=ng.graph.nodes.find(x=>x.id===algoEditId);if(!n)return;
    n.params.script=ta.value;ngChanged();updateGutter();refreshAlgoInfo(n.id);
    setAlgoMsg('…',false);
  });
  ta.addEventListener('scroll',()=>{$('algoGutter').scrollTop=ta.scrollTop});
  ta.addEventListener('keydown',e=>{
    if(e.key==='Tab'){e.preventDefault();algoIndent(ta,e.shiftKey);return}
    if(e.key==='Enter'&&!e.shiftKey&&!e.ctrlKey&&!e.metaKey&&!e.altKey){
      const v=ta.value,s=ta.selectionStart,ls=v.lastIndexOf('\n',s-1)+1,line=v.slice(ls,s);
      const ind=/^[ \t]*/.exec(line)[0],extra=/:\s*$/.test(line.replace(/#.*$/,''))?'    ':'';
      e.preventDefault();algoInsert(ta,'\n'+ind+extra);
    }
  });
  sel.onchange=()=>{
    const ex=ALGO_EXAMPLES[sel.value];sel.value='';
    if(!ex)return;
    if(ta.value.trim()&&!confirm(`Sostituire lo script attuale con l'esempio «${ex.name}»?`))return;
    ta.focus();ta.select();algoInsert(ta,ex.code);ta.setSelectionRange(0,0);ta.scrollTop=0;
  };
  $('algoSize').onchange=()=>{$('ngSize').value=$('algoSize').value;schedulePreview()};
  $('algoClose').onclick=closeAlgoEditor;
  $('algoModal').addEventListener('mousedown',e=>{if(e.target===$('algoModal'))closeAlgoEditor()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&algoEditId){e.preventDefault();closeAlgoEditor()}});
  window.addEventListener('resize',()=>{if(algoEditId)updateGutter()});
})();

async function playOnBoard(){
  const dev=$('ngBoard').value;if(!dev){ngStatus('Scegli una board.');return}
  try{
    await api('/api/graph/play',{method:'POST',headers:JSONH,body:JSON.stringify({
      device_id:Number(dev),graph:ng.graph,
      fps:Math.min(30,Math.max(1,Number($('ngFps').value)||8)),
      color:$('ngColor').value,use_palette:true})});
    ngStatus('In onda sulla board (prova).');
    setTimeout(async()=>{try{const st=(await api('/api/playlists/status'))[dev];if(st&&st.error)ngStatus('⚠ '+st.error)}catch{}},1800);
  }catch(e){ngStatus('Errore: '+e.message)}
}
async function stopOnBoard(){
  const dev=$('ngBoard').value;if(!dev)return;
  try{await api('/api/stop',{method:'POST',headers:JSONH,body:JSON.stringify({device_id:Number(dev)})});ngStatus('Prova fermata.')}
  catch(e){ngStatus('Errore: '+e.message)}
}
async function openNodesView(){
  await loadPalette();await loadGraphs();
  fillSelect($('ngBoard'),devices,$('ngBoard').value,'Scegli una board…');
  if(!ng.graph){
    if(graphs.length){try{const g=await api('/api/graphs/'+graphs[0].id);setGraph(g.id,g.name,g.graph)}catch{setGraph(null,'Nuovo grafo',defaultGraph())}}
    else setGraph(null,'Nuovo grafo',defaultGraph());
  }else{renderNodes();schedulePreview()}
  requestAnimationFrame(()=>{fitView();layoutPorts();drawWires();});
  clearInterval(ngMinute);ngMinute=setInterval(schedulePreview,30000);
  startTick();
}

/* PALINSESTO */
let plItems=[],plDirty=false,plPollTimer=null;
const plDid=()=>$('plDevice').value;
function fillSelect(sel,list,keep,empty){
  sel.replaceChildren();
  if(empty){const o=document.createElement('option');o.value='';o.textContent=empty;sel.appendChild(o)}
  list.forEach(d=>{const o=document.createElement('option');o.value=d.id;o.textContent=d.name;o.dataset.noTranslate='1';sel.appendChild(o)});
  if(keep&&list.some(d=>String(d.id)===String(keep)))sel.value=keep;
}
function fillSources(){ fillSelect($('plSource'),devices.filter(d=>String(d.id)!==plDid()),$('plSource').value,'Scegli una board…'); }
async function openPlaylistView(){
  $('plStatus').textContent='';
  const keep=plDid()||$('device').value;
  fillSelect($('plDevice'),devices,keep);
  if(!devices.length){plItems=[];renderPlaylist();$('plRun').textContent='';$('plStatus').textContent='Nessuna board: aggiungila da ⚙ Impostazioni.';return}
  fillSources();
  await loadPlaylist();
  pollStatus();
  if(!plPollTimer)plPollTimer=setInterval(pollStatus,3000);
}
async function loadPlaylist(){
  if(!plDid())return;
  try{plItems=await api(`/api/devices/${plDid()}/playlist`);plDirty=false;renderPlaylist()}
  catch(e){$('plStatus').textContent='Errore caricamento: '+e.message}
}
function field(cls,label,el){
  const w=document.createElement('div');w.className='f '+cls;
  const l=document.createElement('label');l.textContent=label;w.append(l,el);return w;
}
function numInput(it,key,min,max){
  const i=document.createElement('input');i.type='number';i.min=min;i.max=max;i.value=it[key];
  i.onchange=()=>{const v=Math.min(max,Math.max(min,Number(i.value)||min));i.value=v;it[key]=v;plDirty=true;if(key==='duration')it.auto_duration=false};
  return i;
}
function iconBtn(text,fn,title){ const b=document.createElement('button');b.type='button';b.textContent=text;b.title=title;b.onclick=fn;return b; }
const plIsColor=()=>!!devices.find(d=>String(d.id)===plDid())?.color;
const plWidth=()=>devices.find(d=>String(d.id)===plDid())?.width||8;
const itemType=it=>it.type||'animation';
function autoDuration(it){ const px=(it.text||'').length*6,W=plWidth(); return px<=W?5:Math.min(86400,Math.ceil((px+W)/Math.max(1,it.fps||8))+1); }
let plFlash=-1;
function renderPlaylist(){
  const box=$('plItems');box.replaceChildren();
  const bd=devices.find(d=>String(d.id)===plDid());
  $('plBoardInfo').textContent=bd?`Board: ${bd.width}×${bd.height} · ${bd.color?'a colori':'monocromatica'}${bd.rotate?' · orientamento ruotato 180°':''}`:'';
  $('plNote').classList.toggle('hidden',!plIsColor());
  $('plAutostart').checked=!!bd?.autostart;$('plAutostart').disabled=!bd;
  if(!plItems.length){const p=document.createElement('p');p.className='muted';p.textContent='Palinsesto vuoto.';box.appendChild(p);return}
  plItems.forEach((it,i)=>{
    const t=itemType(it);
    const row=document.createElement('div');row.className='plrow'+(i===plFlash?' moved':'')+(it.enabled===false?' off':'');
    const pos=document.createElement('input');pos.type='number';pos.min=1;pos.max=plItems.length;pos.value=i+1;pos.title='Posizione';
    pos.onchange=()=>{const n=parseInt(pos.value,10);if(isNaN(n)){pos.value=i+1;return}moveTo(i,n-1)};
    pos.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();pos.blur()}};
    const dur=numInput(it,'duration',1,86400);
    const fps=numInput(it,'fps',1,30);
    fps.addEventListener('change',()=>{if(t==='text'&&it.auto_duration){it.duration=autoDuration(it);dur.value=it.duration}});
    const bri=numInput(it,'brightness',1,100);
    const col=document.createElement('input');col.type='color';col.value=it.color;
    col.oninput=()=>{it.color=col.value;plDirty=true};
    const rot=document.createElement('input');rot.type='checkbox';rot.checked=!!bd?.rotate||!!it.rotate;
    rot.disabled=!!bd?.rotate;
    rot.title=bd?.rotate?'Rotazione già attiva nelle impostazioni della board.':'Ruota questo elemento di 180°.';
    rot.onchange=()=>{it.rotate=rot.checked;plDirty=true};
    let first;
    if(t==='animation'){
      const sel=document.createElement('select');
      animations.forEach(a=>{const o=document.createElement('option');o.value=a.id;o.textContent=a.name;sel.appendChild(o)});
      if(!animations.some(a=>Number(a.id)===it.animation_id)){const o=document.createElement('option');o.value=it.animation_id;o.textContent=`#${it.animation_id} (non trovata)`;sel.appendChild(o)}
      sel.value=it.animation_id; sel.onchange=()=>{it.animation_id=Number(sel.value);plDirty=true;renderPlaylist()};
      first=field('anim','ANIMAZIONE',sel);
    }else if(t==='text'){
      const tx=document.createElement('input');tx.type='text';tx.maxLength=120;tx.value=it.text||'';tx.placeholder='Scrivi il testo…';
      tx.oninput=()=>{it.text=tx.value;plDirty=true;if(it.auto_duration){it.duration=autoDuration(it);dur.value=it.duration}};
      first=field('anim','TESTO',tx);
    }else if(t==='graph'){
      const gs=document.createElement('select');
      graphs.forEach(g=>{const o=document.createElement('option');o.value=g.id;o.textContent=g.name;gs.appendChild(o)});
      if(!graphs.some(g=>g.id===it.graph_id)){const o=document.createElement('option');o.value=it.graph_id;o.textContent=`#${it.graph_id} (non trovato)`;gs.appendChild(o)}
      gs.value=it.graph_id;gs.onchange=()=>{it.graph_id=Number(gs.value);plDirty=true};
      first=field('anim','GRAFO (NODI)',gs);
    }else if(t==='pause'){
      const pm=document.createElement('select');
      [['hold','Ultimo fotogramma fermo'],['blank','Schermo spento'],['release','Libera la board']].forEach(([v,x])=>{const o=document.createElement('option');o.value=v;o.textContent=x;pm.appendChild(o)});
      pm.value=it.pause_mode||'hold';pm.onchange=()=>{it.pause_mode=pm.value;plDirty=true};
      first=field('anim','PAUSA',pm);
    }else{
      const c=document.createElement('div');c.className='muted';c.style.padding='10px 0';
      c.textContent=plWidth()>=32?'🕒 Ora attuale HH:MM':'🕒 Ora attuale: ore e minuti si alternano';
      first=field('anim','OROLOGIO',c);
    }
    col.disabled=(t==='animation'||t==='graph')&&plIsColor()&&it.use_palette!==false;
    const en=document.createElement('input');en.type='checkbox';en.checked=it.enabled!==false;
    en.onchange=()=>{it.enabled=en.checked;plDirty=true;row.classList.toggle('off',!en.checked)};
    const parts=[field('pos','N°',pos),field('chk en','ATTIVO',en),first,field('num','DURATA (s)',dur)];
    if(t==='animation'){
      const a=animations.find(x=>Number(x.id)===it.animation_id);
      const aw=a?Number(a.width):8, bw=plWidth();
      if(aw>0 && aw<bw){
        const maxR=Math.max(1,Math.floor(bw/aw));
        const rsel=document.createElement('select');
        for(let k=1;k<=maxR;k++){
          const o=document.createElement('option');o.value=k;o.textContent=k+'× ('+(k*aw)+'px)';rsel.appendChild(o);
        }
        const cur=Math.max(1,Math.min(maxR,Number(it.repeat_x)||maxR));
        it.repeat_x=cur;rsel.value=String(cur);
        rsel.onchange=()=>{it.repeat_x=Number(rsel.value);plDirty=true};
        parts.push(field('num','DUPLICA',rsel));
      }
    }
      if(t==='animation'||t==='text'||t==='graph'){
      const ff = field('num', t==='text'?'VELOCITÀ':'FPS', fps);
      if(t==='graph' && Number(it.fps) < 2){
        const warn = document.createElement('div');
        warn.className = 'muted';
        warn.style.cssText = 'color:#ffd21f;font-size:11px;margin-top:2px;';
        warn.textContent = '⚠ FPS basso: il grafo si muoverà a ' + it.fps + ' fps';
        ff.appendChild(warn);
      }
      parts.push(ff);
    }
    if(t!=='pause')parts.push(field('','COLORE',col),field('num','LUMIN. %',bri),field('chk','180°',rot));
    if((t==='animation'||t==='graph')&&plIsColor()){
      const up=document.createElement('input');up.type='checkbox';up.checked=it.use_palette!==false;
      up.onchange=()=>{it.use_palette=up.checked;plDirty=true;renderPlaylist()};
      parts.push(field('chk','PALETTE',up));
    }
    const acts=document.createElement('div');acts.className='f';
    const bx=document.createElement('div');bx.className='board-actions';
    bx.append(iconBtn('↑',()=>moveItem(i,-1),'Su'),iconBtn('↓',()=>moveItem(i,1),'Giù'),iconBtn('✕',()=>{plItems.splice(i,1);plDirty=true;renderPlaylist()},'Rimuovi'));
    acts.appendChild(bx); parts.push(acts);
    row.append(...parts); box.appendChild(row);
  });
}
function moveTo(i,j){
  j=Math.max(0,Math.min(plItems.length-1,j));
  if(j===i){renderPlaylist();return}
  const [x]=plItems.splice(i,1);plItems.splice(j,0,x);
  plDirty=true;plFlash=j;renderPlaylist();
  setTimeout(()=>{plFlash=-1;document.querySelectorAll('#plItems .plrow.moved').forEach(r=>r.classList.remove('moved'))},1200);
}
function moveItem(i,d){const j=i+d;if(j<0||j>=plItems.length)return;moveTo(i,j)}
function addItem(type='animation'){
  if(type==='animation'&&!animations.length){$('plStatus').textContent='Non ci sono animazioni disponibili.';return}
  if(type==='graph'&&!graphs.length){$('plStatus').textContent='Non ci sono grafi: creane uno in ◈ Nodi.';return}
  const last=plItems[plItems.length-1];
  const it={type,enabled:true,animation_id:type==='animation'?Number(animations[0].id):0,graph_id:type==='graph'?graphs[0].id:0,text:'',duration:10,fps: (type === 'graph')
  ? Math.min(30, Math.max(1, Number($('ngFps').value) || 8))
  : (last?.type === 'clock' ? 8 : (last?.fps ?? 8)),color:last?.color??'#00ff66',brightness:last?.brightness??100,rotate:last?.rotate??(devices.find(d=>String(d.id)===plDid())?.rotate??false),use_palette:last?.use_palette??true,auto_duration:false,repeat_x:0};
  if(type==='animation'){
    const a=animations.find(x=>Number(x.id)===it.animation_id);
    const aw=a?Number(a.width):8, bw=plWidth();
    if(aw>0 && aw<bw) it.repeat_x=Math.max(1,Math.floor(bw/aw));
  }
  if(type==='text'){it.text='';it.auto_duration=true;it.duration=5}
  if(type==='clock'){it.fps=1;it.duration=15}
  it.pause_mode='hold';
  plItems.push(it);plDirty=true;renderPlaylist();
}
async function savePlaylist(){
  plItems.forEach(it=>{if(!it.pause_mode)it.pause_mode='hold'});
  if(plItems.some(it=>itemType(it)==='text'&&!(it.text||'').trim())){$('plStatus').textContent='Un elemento di testo è vuoto.';return false}
  try{plItems=await api(`/api/devices/${plDid()}/playlist`,{method:'PUT',headers:JSONH,body:JSON.stringify({items:plItems})});plDirty=false;renderPlaylist();$('plStatus').textContent='Palinsesto salvato.';return true}
  catch(e){$('plStatus').textContent='Errore salvataggio: '+e.message;return false}
}
async function startPlaylist(){
  if(!plDid())return;
  if(plDirty&&!await savePlaylist())return;
  try{await api(`/api/devices/${plDid()}/playlist/start`,{method:'POST'});$('plStatus').textContent='Palinsesto avviato.';pollStatus()}
  catch(e){$('plStatus').textContent='Errore avvio: '+e.message}
}
async function stopPlaylist(){
  if(!plDid())return;
  try{await api(`/api/devices/${plDid()}/playlist/stop`,{method:'POST'});$('plStatus').textContent='Palinsesto fermato.';pollStatus()}
  catch(e){$('plStatus').textContent='Errore stop: '+e.message}
}
async function importPlaylist(mode){
  const src=$('plSource').value;
  if(!src){$('plStatus').textContent='Scegli la board di origine.';return}
  const name=devices.find(d=>String(d.id)===src)?.name||'';
  if(plDirty&&!confirm('Hai modifiche non salvate: verranno perse. Continuare?'))return;
  if(mode==='replace'&&plItems.length&&!confirm(`Sostituire il palinsesto con quello di "${name}"?`))return;
  try{plItems=await api(`/api/devices/${plDid()}/playlist/import`,{method:'POST',headers:JSONH,body:JSON.stringify({source_id:Number(src),mode})});plDirty=false;renderPlaylist();$('plStatus').textContent=mode==='replace'?`Copiato da "${name}".`:`Elementi aggiunti in coda.`}
  catch(e){$('plStatus').textContent='Errore: '+e.message}
}
async function pollStatus(){
  if($('playlistView').classList.contains('hidden')||!plDid())return;
  try{
    const st=(await api('/api/playlists/status'))[plDid()],el=$('plRun');
    const m=st&&st.message,showing=m&&m.showing,queued=m&&m.pending?` (+${m.pending} in coda)`:'';
    const msg=showing?` · ✉ messaggio in onda: "${showing}"${queued}`:'';
    if(st&&st.running){el.className='muted on';el.textContent=`● In onda: elemento ${(st.index??-1)+1}/${st.total}`+msg+(st.error?` · ⚠ ${st.error}`:'')}
    else if(showing){el.className='muted on';el.textContent=`● Messaggio in onda: "${showing}"${queued}`}
    else{el.className='muted';el.textContent='Fermo'+(st&&st.error?` · ⚠ ${st.error}`:'')}
  }catch{}
}

let statsPollTimer = null;

function updateNav(name){
  $('nav').classList.remove('hidden');
  $('openPlaylist').disabled = name==='playlist';
  $('openNodes').disabled    = name==='nodes';
  $('openSettings').disabled = name==='settings';
  $('openMonitor').disabled  = name==='monitor';
  $('openStats').disabled    = name==='stats';
  $('openPlaylist').classList.toggle('active', name==='playlist');
  $('openNodes').classList.toggle('active', name==='nodes');
  $('openSettings').classList.toggle('active', name==='settings');
  $('openMonitor').classList.toggle('active', name==='monitor');
  $('openStats').classList.toggle('active', name==='stats');
}

function showView(name){
  $('mainView').classList.toggle('hidden',name!=='main');
  $('settingsView').classList.toggle('hidden',name!=='settings');
  $('playlistView').classList.toggle('hidden',name!=='playlist');
  $('nodesView').classList.toggle('hidden',name!=='nodes');
  $('monitorView').classList.toggle('hidden',name!=='monitor');
  $('statsView').classList.toggle('hidden',name!=='stats');
  document.body.classList.toggle('nodes-active', name==='nodes');
  $('ngPreviewPanel').classList.toggle('hidden', name!=='nodes');
  updateNav(name);
  if(name==='settings'){closeForm();$('settingsStatus').textContent='';$('paletteStatus').textContent='';renderBoards();loadPalette();renderMsgDoc();loadMessageQueue()}
  if(name==='playlist')openPlaylistView();
  if(name==='nodes')openNodesView();
  if(name==='monitor')openMonitorView();
  if(name!=='monitor') stopMonitorPolling();
  if(name==='stats'){
    loadDashboardStats();
    if(!statsPollTimer) statsPollTimer = setInterval(loadDashboardStats, 5000);
  } else if(statsPollTimer){
    clearInterval(statsPollTimer);
    statsPollTimer = null;
  }
}
function switchTab(tabId) {
  // Nascondi tutte le tab (adatta in base a come gestisci i tab nel tuo index.html)
  document.querySelectorAll('.tab-pane').forEach(el => el.style.display = 'none');
  
  const target = $(`section-${tabId}`);
  if (target) target.style.display = 'block';

  if (tabId === 'stats') {
    loadDashboardStats();
    if (!statsPollTimer) statsPollTimer = setInterval(loadDashboardStats, 5000); // Aggiorna ogni 5s
  } else {
    if (statsPollTimer) { clearInterval(statsPollTimer); statsPollTimer = null; }
  }
}

$('clearStatsErrors').onclick = async () => {
  try {
    await api('/api/stats/errors/clear', { method: 'POST' });
    await loadDashboardStats();
  } catch (e) {
    $('st-errors').textContent = 'Errore svuotando gli errori: ' + e.message;
  }
};

$('openStats').onclick = () => navigateTo('stats');
$('backFromStats').onclick = () => navigateTo('main');
$('refreshStats').onclick = () => loadDashboardStats();

async function loadDashboardStats() {
  const errBox = document.getElementById('st-errors');
  try {
    const data = (typeof api === 'function')
      ? await api('/api/stats')
      : await (await fetch('/api/stats')).json();

    if (!data || !data.render) {
      throw new Error('Risposta /api/stats non valida o vuota');
    }

    const setText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    const uptimeSec = Number(data.uptime_sec || 0);
    setText('st-uptime', `${Math.floor(uptimeSec / 60)} min (${uptimeSec}s)`);

    const cacheHitRate = data.cache?.hit_rate;
    const hitRatio = cacheHitRate !== null && cacheHitRate !== undefined
      ? `${(cacheHitRate * 100).toFixed(1)}%`
      : 'N/A';
    setText('st-hitrate', hitRatio);
    setText('st-rendertotal', String(data.render.total ?? 0));

    const avg = data.render.duration_sec && data.render.duration_sec.avg !== null
      ? `${data.render.duration_sec.avg}s`
      : 'N/A';
    setText('st-avgdur', avg);

    const cacheUl = document.getElementById('st-cache-list');
    if (cacheUl && data.cache) {
      cacheUl.replaceChildren();
      const cs = data.cache.stats || {};
      const cacheRate = data.cache.hit_rate == null ? 'N/D' : `${(data.cache.hit_rate * 100).toFixed(1)}%`;
      [
        `Stato: ${data.cache.disabled ? 'Disattivata' : 'Attiva'}`,
        `Hit rate: ${cacheRate}`,
        `HIT: ${cs.hits ?? data.render.hit ?? 0} · MISS: ${cs.misses ?? data.render.miss ?? 0} · BYPASS: ${cs.bypass ?? data.render.bypass ?? 0}`,
        `Voci in RAM: ${data.cache.entries} / ${data.cache.max_entries}`,
        `Frame in RAM: ${data.cache.frames} / ${data.cache.max_frames}`,
        `Scadute: ${cs.expired ?? 0} · Espulse per limite: ${cs.evictions ?? 0}`,
        `Inserimenti: ${cs.puts ?? 0} · Invalidazioni: ${cs.invalidations ?? 0}`
      ].forEach(text => {
        const li = document.createElement('li');
        li.textContent = text;
        cacheUl.appendChild(li);
      });
    }

    const paletteUl = document.getElementById('st-palette-cache-list');
    if (paletteUl && data.palette_cache) {
      paletteUl.replaceChildren();
      const pc = data.palette_cache;
      const ps = pc.stats || {};
      const paletteRate = pc.hit_rate == null ? 'N/D' : `${(pc.hit_rate * 100).toFixed(1)}%`;
      [
        `Stato: ${pc.loaded ? 'Caricata in RAM' : 'Non caricata'}`,
        `Hit rate: ${paletteRate} · TTL: ${pc.ttl_sec}s`,
        `HIT: ${ps.hits ?? 0} · MISS: ${ps.misses ?? 0}`,
        `Scadute: ${ps.expired ?? 0} · Letture da DB: ${ps.loads ?? 0}`,
        `Invalidazioni: ${ps.invalidations ?? 0}`,
        `Età voce corrente: ${pc.age_sec == null ? 'N/D' : `${pc.age_sec}s`}`
      ].forEach(text => {
        const li = document.createElement('li');
        li.textContent = text;
        paletteUl.appendChild(li);
      });
    }

    const animCacheUl = document.getElementById('st-anim-cache-list');
    if (animCacheUl) {
      animCacheUl.replaceChildren();
      const ac = data.anim_cache || { entries: 0, ids: [] };
      const li = document.createElement('li');
      li.textContent = `Animazioni in cache RAM: ${ac.entries} (ID: ${(ac.ids || []).join(', ') || 'Nessuna'})`;
      animCacheUl.appendChild(li);
    }

    const playUl = document.getElementById('st-play-list');
    if (playUl && data.play_item) {
      playUl.replaceChildren();
      const kinds = Object.entries(data.play_item.by_kind || {})
        .map(([k, v]) => `${k}: ${v}`).join(', ') || 'Nessuno';
      [
        `Riproduzioni Totali (Play): ${data.play_item.total ?? 0}`,
        `Tipi di Contenuto: ${kinds}`
      ].forEach(text => {
        const li = document.createElement('li');
        li.textContent = text;
        playUl.appendChild(li);
      });
    }

    if (errBox) {
      errBox.replaceChildren();
      const errs = data.recent_errors || [];
      if (errs.length > 0) {
        errs.forEach(err => {
          const div = document.createElement('div');
          div.textContent = `[${err.ts}] ${err.msg}`;
          errBox.appendChild(div);
        });
      } else {
        errBox.textContent = 'Nessun errore recente registrato.';
      }
    }
  } catch (e) {
    console.error('Errore nel recupero delle statistiche:', e);
    if (errBox) {
      errBox.textContent = `Errore caricamento statistiche: ${e.message || e}`;
    }
  }
}

let plPrev='';

/* WIRING */
$('device').addEventListener('change',syncMainBoardRotation);
$('openPlaylist').onclick=()=>navigateTo('playlist');
$('backFromPlaylist').onclick=()=>navigateTo('main');
$('openNodes').onclick=()=>navigateTo('nodes');
$('backFromNodes').onclick=()=>navigateTo('main');
$('openMonitor').onclick=()=>navigateTo('monitor');
$('backFromMonitor').onclick=()=>navigateTo('main');
$('openSettings').onclick=()=>navigateTo('settings');
$('backMain').onclick=()=>navigateTo('main');

$('ngSelect').onchange=selectGraph;
$('ngName').oninput=()=>{ng.dirty=true;$('ngDirty').textContent='● modifiche non salvate'};
$('ngSave').onclick=()=>saveGraph(false);
$('ngDup').onclick=()=>{$('ngName').value=($('ngName').value.trim()||'Grafo')+' (copia)';saveGraph(true)};
$('ngDel').onclick=deleteGraph;
$('ngResetView').onclick=fitView;
$('ngExport').onclick=exportGraph;
$('ngExportAll').onclick=exportAllGraphs;
$('ngImport').onclick=()=>$('ngImportFile').click();
$('ngImportFile').onchange=async e=>{const f=e.target.files[0];e.target.value='';await importFile(f)};
$('ngCollapseAll').onclick=()=>setAllCollapsed(true);
$('ngExpandAll').onclick=()=>setAllCollapsed(false);
document.querySelectorAll('.ngadd').forEach(b=>{
  b.onclick=()=>{ addNode(b.dataset.type); const det=b.closest('details'); if(det) det.open=false; };
});
document.querySelectorAll('.ngitem').forEach(b=>b.addEventListener('click',()=>{const d=b.closest('details');if(d)d.open=false}));
document.addEventListener('click',e=>{document.querySelectorAll('.ngadd-menu[open]').forEach(d=>{if(!d.contains(e.target))d.open=false})});
$('ngSize').onchange=schedulePreview;
$('ngFps').onchange=schedulePreview;
$('ngBoard').onchange=()=>{
  const d=devices.find(x=>String(x.id)===$('ngBoard').value);
  if(d&&[...$('ngSize').options].some(o=>o.value===`${d.width}x${d.height}`)){ $('ngSize').value=`${d.width}x${d.height}`;schedulePreview(); }
};
$('ngPlay').onclick=playOnBoard;
$('ngStop').onclick=stopOnBoard;

$('plDevice').onfocus=()=>{plPrev=plDid()};
$('plDevice').onchange=async()=>{
  if(plDirty&&!confirm('Hai modifiche non salvate: verranno perse. Continuare?')){$('plDevice').value=plPrev;return}
  plPrev=plDid();fillSources();await loadPlaylist();$('plStatus').textContent='';pollStatus();
};
$('plAutostart').onchange=async()=>{
  const d=devices.find(x=>String(x.id)===plDid());if(!d)return;
  const on=$('plAutostart').checked;
  try{await api(`/api/devices/${d.id}/autostart`,{method:'PUT',headers:JSONH,body:JSON.stringify({enabled:on})});d.autostart=on;$('plStatus').textContent=on?'Autostart attivato.':'Autostart disattivato.'}
  catch(e){$('plAutostart').checked=!on;$('plStatus').textContent='Errore: '+e.message}
};
$('plAdd').onclick=()=>addItem('animation');
$('plAddText').onclick=()=>addItem('text');
$('plAddClock').onclick=()=>addItem('clock');
$('plAddPause').onclick=()=>addItem('pause');
$('plAddGraph').onclick=()=>addItem('graph');
$('plSave').onclick=savePlaylist;
$('plStart').onclick=startPlaylist;
$('plStop').onclick=stopPlaylist;
$('plReplace').onclick=()=>importPlaylist('replace');
$('plAppend').onclick=()=>importPlaylist('append');

$('newDevice').onclick=()=>openForm();
$('saveDevice').onclick=saveDevice;
$('cancelDevice').onclick=closeForm;
$('savePalette').onclick=savePalette;
$('msgSend').onclick=sendTestMessage;
$('msgQueueRefresh').onclick=loadMessageQueue;
$('msgQueueClear').onclick=clearMessageQueue;
$('msgDevice').addEventListener('change',()=>{renderMsgDoc();loadMessageQueue()});
$('play').onclick=play;
$('stop').onclick=stop;
$('showPreview').onchange=()=>{$('previewArea').classList.toggle('hidden',!$('showPreview').checked);if($('showPreview').checked)loadPreview()};
$('color').oninput=()=>{if($('showPreview').checked)loadPreview()};

loadGraphs();
loadAnimations();
loadDevices();

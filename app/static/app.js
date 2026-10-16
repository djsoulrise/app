'use strict';
const $ = (s, e = document) => e.querySelector(s);
const $$ = (s, e = document) => [...e.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const state = {page: 'sada', curId: null, timer: null, lib: {q: '', view: '', tag: '', sort: 'artist', dir: 'asc', page: 1, size: 100}, sel: new Set(), mode: 'ravno', strict: 'normalno', styleLabels: {},
  view: /[?&]tiker=1/.test(location.search) ? 'tiker' : /[?&]mini=1/.test(location.search) ? 'mini'
    : (localStorage.getItem('view') || (localStorage.getItem('mini') === '1' ? 'mini' : 'full')),
  fs: +localStorage.getItem('fs') || 22, tikIdx: 0, tikItems: [], tikCur: null, rot: null};
state.mini = state.view !== 'full'; state.tiker = state.view === 'tiker';
const applyFs = () => document.documentElement.style.setProperty('--mfs', state.fs + 'px');
applyFs();

async function api(url, body, method) {
  const o = {};
  if (body !== undefined) { o.method = method || 'POST'; o.headers = {'Content-Type': 'application/json'}; o.body = JSON.stringify(body); }
  else if (method) o.method = method;
  const r = await fetch(url, o);
  let d = null; try { d = await r.json(); } catch {}
  if (!r.ok) throw new Error((d && (d.detail || d.error)) || ('Greska ' + r.status));
  return d;
}
function toast(m, err) {
  const t = $('#toast'); t.textContent = m; t.style.display = 'block'; t.style.borderColor = err ? 'var(--bad)' : 'var(--acc)';
  clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', 3500);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };
const name = t => (t.artist ? esc(t.artist) + ' - ' : '') + esc(t.title);
const mins = s => s ? Math.floor(s / 60) + ':' + String(Math.round(s % 60)).padStart(2, '0') : '';
function badges(t) {
  let sty = '';
  try { const v = JSON.parse(t.style || '{}'); const top = Object.entries(v).sort((a, b) => b[1] - a[1])[0]; if (top) sty = `<span class="badge" style="background:#3a3350">${esc(state.styleLabels[top[0]] || top[0])}</span>`; } catch {}
  return [sty, t.bpm ? `<span class="badge">${t.bpm.toFixed(1)} BPM</span>` : '<span class="badge bad">bez BPM</span>',
    t.camelot ? `<span class="badge blue">${t.camelot}</span>` : '<span class="badge bad">bez keya</span>',
    t.energy ? `<span class="badge acc" title="${esc(t.energy_src)}">E${t.energy}${t.energy_src === 'procena' ? '~' : ''}</span>` : '',
    t.fmt ? `<span class="badge">${esc(t.fmt)}</span>` : '',
    t.flex_grid ? '<span class="badge bad" title="vise beat markera">flex grid</span>' : '',
    (t.user_cues || t.loops) ? `<span class="badge ok" title="cue/loop iz Traktora">cue ${t.user_cues}/loop ${t.loops}</span>` : '',
    t.unsorted ? '<span class="badge">nesortirano</span>' : '',
    ...(t.tags || []).filter(x => !x.junk).slice(0, 6).map(x => `<span class="badge">${esc(x.t)}</span>`)].join('');
}

// ---------- navigation ----------
const PAGES = [['sada', 'Sada'], ['lib', 'Biblioteka'], ['tags', 'Tagovi'], ['dupes', 'Duplikati'], ['audio', 'Audio ritam'], ['grids', 'Fleksibilni gridovi'], ['pl', 'Plejliste'],
  ['eve', 'Vece'], ['req', 'Zahtevi'], ['ai', 'AI'], ['trend', 'Trendovi'], ['set', 'Podesavanja']];
function go(p) {
  if (state.mini) p = 'sada';
  document.body.classList.toggle('mini', state.mini);
  document.body.classList.toggle('tiker', state.tiker);
  $('#minibtn').textContent = {full: 'Mini', mini: 'Tiker', tiker: 'Pun prikaz'}[state.view];
  clearInterval(state.rot);
  state.page = p; clearInterval(state.timer); state.timer = null;
  $('#nav').innerHTML = PAGES.map(([k, v]) => `<button data-act="go" data-p="${k}" class="${k === p ? 'on' : ''}">${v}</button>`).join('');
  $('#main').innerHTML = '<p class="mut">Ucitavam...</p>';
  guard(pages[p])();
}

async function refreshStatus() {
  try {
    const s = await api('/api/status');
    const j = s.job, b = s.broadcast;
    $('#statusbar').innerHTML = `Pesama: ${s.counts.n || 0} (Traktor ${s.counts.trk || 0}, flex ${s.counts.flex || 0}) | ` +
      `Broadcast: ${b.running ? (b.last_connect ? 'povezan' : 'ceka') : 'ugasen'} | ` +
      (j.running ? `<b>${esc(j.phase)} ${j.done}/${j.total}</b>` : j.error ? `<span style="color:var(--bad)">${esc(j.error)}</span>` : '');
    state.status = s;
    updateScanControls(j);
    if (state.jobWasRunning && !j.running && !j.error) { toast(j.cancelled ? 'Zaustavljeno — rezultati sacuvani' : 'Zavrseno'); if (state.page !== 'sada') go(state.page); }
    state.jobWasRunning = j.running;
  } catch {}
}
setInterval(refreshStatus, 2500);

// ---------- pages ----------
const pages = {};

// --- Sada ---
pages.sada = async () => {
  const modes = state.mini ? [['ravno', 'Ravno'], ['podizanje', 'Gore'], ['spustanje', 'Dole']]
    : [['ravno', 'Ravno'], ['podizanje', 'Podizanje energije'], ['spustanje', 'Spustanje']];
  const drawModes = () => $('#modes').innerHTML = modes.map(([k, v]) => `<button class="tab ${state.mode === k ? 'on' : ''}" data-act="mode" data-m="${k}">${v}</button>`).join('') +
    (state.mini ? ' ' : ' <span class="mut small">Stil:</span> ') + ['strogo', 'normalno', 'slobodno'].map(k => `<button class="tab ${state.strict === k ? 'on' : ''}" data-act="strict" data-m="${k}">${k}</button>`).join('');
  state.curId = null; state.playedSig = '';
  if (state.tiker) {
    $('#main').innerHTML = '<div id="cur"></div><div id="sug"></div>';
    await tickNow(true);
    state.timer = setInterval(() => tickNow(false), 2000);
    state.rot = setInterval(() => { if (state.tikItems.length > 1) { state.tikIdx = (state.tikIdx + 1) % state.tikItems.length; renderTik(true); } }, 4000);
    return;
  }
  if (state.mini) {
    $('#main').innerHTML = '<div class="mbar"><span id="modes"></span></div><div id="cur"></div><div id="sug"></div>';
    drawModes(); state._drawModes = drawModes;
    await tickNow(true);
    state.timer = setInterval(() => tickNow(false), 2000);
    return;
  }
  $('#main').innerHTML = `
    <div id="cur"></div>
    <div class="row"><input id="q" placeholder="Rucni izbor trenutne pesme (naziv ili izvodjac)..." style="flex:1;min-width:240px">
      <span id="modes"></span></div>
    <div id="res"></div><div id="sug"></div>
    <div class="evening-layout"><section><h3>Veceras pusteno</h3><div id="played"></div></section>
    <section><h3>LIVE · 10 audio najslicnijih</h3><div id="liveaudio" class="card mut">Cekam trenutnu pesmu...</div></section></div>`;
  drawModes(); state._drawModes = drawModes;
  $('#q').addEventListener('input', guard(async e => {
    const q = e.target.value.trim();
    if (q.length < 2) { $('#res').innerHTML = ''; return; }
    const items = await api('/api/search?q=' + encodeURIComponent(q));
    $('#res').innerHTML = '<div class="card">' + items.map(t => `<div class="row"><button class="sm pri" data-act="use" data-id="${t.id}">Trenutna</button> ${name(t)} ${badges(t)}</div>`).join('') + '</div>';
  }));
  state.playedSig = '';
  await tickNow(true);
  state.timer = setInterval(() => tickNow(false), 2000);
};

async function tickNow(first) {
  if (state.tickBusy) return;
  state.tickBusy = true;
  try { await tickNowInner(first); }
  finally { state.tickBusy = false; }
}

async function tickNowInner(first) {
  const n = await api('/api/now');
  const audioChanged = state.status && (state.lastAudioRevision !== state.status.audio_revision || state.lastAudioThreshold !== state.status.audio_threshold);
  if (n.track_id && (first || n.track_id !== state.curId || audioChanged)) {
    state.curId = n.track_id; $('#res') && ($('#res').innerHTML = '');
    await loadSuggest(n);
  } else if (!n.track_id) {
    if ($('#liveaudio')) $('#liveaudio').textContent = 'Cekam trenutnu pesmu...';
    const b = state.status && state.status.broadcast;
    let diag = 'Jos nema trenutne pesme. Cekam Traktor (Broadcast / History) ili izaberi rucno ispod.';
    if (b && b.last_song) diag = `Broadcast je primio: <b>${esc(b.last_song)}</b> &mdash; ${b.last_match ? 'pesma je pronadjena' : '<b>nije pronadjena u biblioteci</b> (ucitaj biblioteku u Podesavanjima ili izaberi rucno)'}.`;
    else if (b && b.last_connect) diag = `Traktor je povezan (${b.requests} zahteva, ${b.bytes} bajtova toka), ali jos nije poslao naziv pesme. Pusti pesmu na deck-u; ako se ne pojavi, vidi Podesavanja.`;
    $('#cur').innerHTML = state.tiker ? '<div class="t-cur">cekam pesmu...</div>' : `<div class="card mut">${diag}</div>`;
  }
  const pe = $('#played');
  if (!pe) return;
  const pl = await api('/api/plays');
  const sig = JSON.stringify(pl.map(p => [p.id, p.rating]));
  if (sig !== state.playedSig) {
    state.playedSig = sig;
    pe.innerHTML = '<div class="card">' + (pl.length ? pl.slice().reverse().map(p => `<div class="row">
      <span class="mut small">${esc(p.ts.slice(11, 16))} ${esc(p.source)}</span> ${esc(p.artist)} - ${esc(p.title)}
      <button class="sm ${p.rating > 0 ? 'ok' : ''}" data-act="rate" data-id="${p.id}" data-r="1">palo</button>
      <button class="sm ${p.rating < 0 ? 'bad' : ''}" data-act="rate" data-id="${p.id}" data-r="-1">nije palo</button></div>`).join('') : '<span class="mut">Nista jos.</span>') + '</div>';
  }
}

function renderTik(animate) {
  const c = state.tikCur, items = state.tikItems;
  if (!c) return;
  const t = items[state.tikIdx];
  const meta = x => [x.bpm ? Math.round(x.bpm) + ' BPM' : '', x.camelot, x.energy ? 'E' + x.energy : ''].filter(Boolean).join(' \u00b7 ');
  $('#cur').innerHTML = `<div class="t-cur"><b>\u25b6</b> ${name(c)} <span>${meta(c)}</span></div>`;
  $('#sug').innerHTML = t ? `<div class="t-box" data-act="tiknext"><div class="t-n">${state.tikIdx + 1}/${items.length} \u00b7 ${t.audio_match ? 'AUDIO RITAM' : t.label === 'siguran izbor' ? 'sigurno' : 'iznenadjenje'} ${items.map((_, i) => i === state.tikIdx ? '\u25cf' : '\u25cb').join('')}</div>
    <div class="t-item ${animate ? 't-anim' : ''}"><div class="t-name">${name(t)}</div><div class="t-meta">${meta(t)}</div></div></div>` : '<div class="t-cur">nema predloga</div>';
}

async function loadSuggest(n) {
  const requestedId = state.curId;
  const revision = state.status?.audio_revision, threshold = state.status?.audio_threshold;
  const d = await api(`/api/suggest?track_id=${requestedId}&mode=${state.mode}&strict=${state.strict}`);
  if (state.page !== 'sada' || state.curId !== requestedId) return;
  state.lastAudioRevision = revision; state.lastAudioThreshold = threshold;
  renderLiveAudio(d);
  const c = d.current, src = n ? n.source : '';
  state.audioDiagnostics = d.audio_diagnostics;
  if (state.tiker) {
    state.tikCur = c; state.tikItems = d.items; state.tikIdx = 0;
    renderTik(false);
    return;
  }
  if (state.mini) {
    const meta = t => [t.bpm ? Math.round(t.bpm) + ' BPM' : '', t.camelot, t.energy ? 'E' + t.energy : ''].filter(Boolean).join(' · ');
    $('#cur').innerHTML = `<div class="m-cur">▶ ${name(c)} <span class="mut">${meta(c)}</span></div>`;
    $('#sug').innerHTML = d.items.map((t, i) => `<div class="m-row" data-act="use" data-id="${t.id}"><b>${i + 1}.</b> ${name(t)}
      <span class="mut">${meta(t)} · ${t.audio_match ? 'AUDIO RITAM' : t.label === 'siguran izbor' ? 'sigurno' : 'iznenadjenje'}</span>
      <div class="m-why">${esc(t.why[0] || '')}</div><div class="row"><button class="sm" data-act="copysong" data-id="${t.id}">Copy Song</button><button class="sm" data-act="dragsong" data-id="${t.id}">Prevuci</button><button class="sm" data-act="reveal" data-id="${t.id}">Explorer</button></div></div>`).join('') || '<div class="mut">Nema predloga.</div>';
    return;
  }
  const notes = [];
  if (d.audio_status) notes.push(esc(d.audio_status) + ' Zadrzan je izvorni predlog.');
  if (d.blocked_history && d.blocked_history.length) notes.push('Iz tvojih lista preskoceno zbog stila: ' + d.blocked_history.map(esc).join('; '));
  if (d.relaxed && d.relaxed.length) notes.push('Premalo pesama u istom stilu, opusten filter: ' + d.relaxed.join(', '));
  $('#cur').innerHTML = `<div class="card cur"><div class="mut small">TRENUTNA (${esc(src || '')}) &middot; stil: ${esc(c.style_label)}</div>
    <div class="big">${name(c)}</div>${badges(c)}</div>`;
  $('#sug').innerHTML = audioDiagnosticHtml(d.audio_diagnostics) + (notes.length ? `<div class="small mut">${notes.join('<br>')}</div>` : '') + '<h3>Sledecih 5</h3><div class="grid">' + d.items.map(t => `
    <div class="card">
      <div><span class="badge ${t.label === 'siguran izbor' ? 'ok' : 'acc'}">${t.label}</span><b style="float:right">${t.score}</b></div>
      <div class="big" style="font-size:20px">${name(t)}</div>${badges(t)}
      <div class="bar"><i style="width:${Math.min(100, t.score)}%"></i></div>
      <ul class="why">${t.why.map(w => `<li>${esc(w)}</li>`).join('')}</ul>
      ${t.note ? `<div class="small" style="color:var(--acc)">Beleska: ${esc(t.note)}</div>` : ''}
      <div class="row"><button class="sm pri" data-act="use" data-id="${t.id}">Pusteno / trenutna</button>
      <button class="sm" data-act="copysong" data-id="${t.id}">Copy Song</button>
      <button class="sm" data-act="dragsong" data-id="${t.id}">Prevuci u Traktor</button>
      <button class="sm" data-act="reveal" data-id="${t.id}">Explorer</button></div></div>`).join('') + '</div>' +
    (d.items.length ? '' : '<p class="mut">Nema predloga. Ucitaj biblioteku u Podesavanjima.</p>');
}

// --- Biblioteka ---
pages.lib = async () => {
  const L = state.lib;
  const views = [['', 'Sve'], ['flex_grid', 'Flex beatgrid'], ['bez_bpm', 'Bez BPM'], ['bez_key', 'Bez keya'], ['bez_taga', 'Bez taga'],
    ['nesortirano', 'Novo/nesortirano'], ['bez_izvodjaca', 'Bez izvodjaca'], ['zasticene', 'Sa cue/loop/ocenom'], ['nedostaje', 'Nedostaje na disku']];
  $('#main').innerHTML = `<h2>Biblioteka</h2>
    <div class="row"><input id="lq" placeholder="Pretraga" value="${esc(L.q)}" style="flex:1;min-width:200px">
      <select id="lview">${views.map(([k, v]) => `<option value="${k}" ${L.view === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
      <input id="ltag" placeholder="tag (zarez = svi)" value="${esc(L.tag)}" list="taglist" style="width:170px">
      <input id="lbmin" placeholder="BPM od" value="${L.bpm_min || ''}" style="width:90px"><input id="lbmax" placeholder="do" value="${L.bpm_max || ''}" style="width:70px">
      <input id="lkey" placeholder="Camelot 8A" value="${L.key || ''}" style="width:110px">
      <input id="lemin" placeholder="E od" value="${L.energy_min || ''}" style="width:70px"><input id="lemax" placeholder="do" value="${L.energy_max || ''}" style="width:60px">
      <select id="lfmt"><option value="">format</option>${['mp3', 'flac', 'wav'].map(f => `<option ${L.fmt === f ? 'selected' : ''}>${f}</option>`).join('')}</select>
      <button class="pri" data-act="libgo">Trazi</button></div>
    <datalist id="taglist"></datalist>
    <div class="row card"><span>Izabrano: <b id="selcount">${state.sel.size}</b></span>
      <input id="bulkadd" placeholder="dodaj tag(ove)" list="taglist"><button data-act="bulkadd">Dodaj</button>
      <input id="bulkrem" placeholder="ukloni tag" list="taglist"><button data-act="bulkrem">Ukloni</button>
      <button data-act="selnone">Ponisti izbor</button></div>
    <div id="libtable"></div>`;
  api('/api/tags').then(t => $('#taglist').innerHTML = t.filter(x => !x.junk).map(x => `<option value="${esc(x.display)}">`).join(''));
  await loadLib();
};

async function loadLib() {
  const L = state.lib, p = new URLSearchParams();
  Object.entries(L).forEach(([k, v]) => { if (v !== '' && v != null) p.set(k, v); });
  const d = await api('/api/tracks?' + p);
  const th = (k, t) => `<th data-act="sort" data-k="${k}">${t}${L.sort === k ? (L.dir === 'asc' ? ' ▲' : ' ▼') : ''}</th>`;
  const pages_ = Math.max(1, Math.ceil(d.total / d.size));
  $('#libtable').innerHTML = `<div class="row"><span class="mut">${d.total} pesama</span>
    <button class="sm" data-act="pg" data-d="-1" ${d.page <= 1 ? 'disabled' : ''}>◀</button> ${d.page}/${pages_}
    <button class="sm" data-act="pg" data-d="1" ${d.page >= pages_ ? 'disabled' : ''}>▶</button></div>
    <table><tr><th><input type="checkbox" data-act="selall"></th>${th('artist', 'Izvodjac')}${th('title', 'Naslov')}${th('bpm', 'BPM')}${th('key', 'Key')}${th('energy', 'E')}${th('year', 'God.')}${th('import', 'Uvoz')}<th>Tagovi / info</th></tr>` +
    d.items.map(t => `<tr><td><input type="checkbox" data-act="sel" data-id="${t.id}" ${state.sel.has(t.id) ? 'checked' : ''}></td>
      <td>${esc(t.artist)}</td><td><a href="#" data-act="edit" data-id="${t.id}">${esc(t.title)}</a></td><td>${t.bpm ? t.bpm.toFixed(1) : ''}</td>
      <td>${t.camelot || ''}</td><td>${t.energy || ''}</td><td>${t.year || ''}</td><td class="small">${t.import_date || ''}</td><td>${badges(t)}</td></tr>`).join('') + '</table>';
}

function readLibFilters() {
  const L = state.lib;
  Object.assign(L, {q: $('#lq').value, view: $('#lview').value, tag: $('#ltag').value, bpm_min: $('#lbmin').value, bpm_max: $('#lbmax').value,
    key: $('#lkey').value, energy_min: $('#lemin').value, energy_max: $('#lemax').value, fmt: $('#lfmt').value, page: 1});
}

async function openTrack(id) {
  const t = await api('/api/track/' + id);
  $('#modalbox').innerHTML = `<h2>${name(t)}</h2><div>${badges(t)}</div>
    <p class="mut small">${esc(t.path)}<br>Komentar iz Traktora: ${esc(t.comment)}</p>
    <label>Tagovi (odvojeni zarezom)</label><textarea id="etags" style="min-height:60px">${esc((t.tags || []).map(x => x.t).join(', '))}</textarea>
    <div class="row"><label>Energija (1-10, prazno = automatski)</label><input id="eenergy" type="number" min="1" max="10" value="${t.energy_src === 'ručno' ? t.energy : ''}" style="width:90px"></div>
    <div class="row"><label>Stil (rucno, prazno = automatski)</label><select id="estyle"><option value="">automatski</option>${Object.entries(state.styleLabels).map(([k, v]) => `<option value="${k}" ${t.style_override === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
    <div class="row"><button class="pri" data-act="savetrack" data-id="${id}">Sacuvaj</button><button data-act="use" data-id="${id}">Postavi kao trenutnu</button><button data-act="closemodal">Zatvori</button></div>`;
  $('#modal').classList.remove('hidden');
}

// --- Tagovi ---
pages.tags = async () => {
  const [tags, sug] = await Promise.all([api('/api/tags'), api('/api/tag-suggestions')]);
  $('#main').innerHTML = `<h2>Tagovi</h2>
    <p class="mut">Tagovi se cuvaju samo u bazi aplikacije (ne diraju Traktor ni fajlove). "Otpad" su tagovi iz preuzetih fajlova; mozes ih grupno ignorisati.</p>
    <div class="row"><button data-act="aitags" class="pri">AI predlog tagova za netagovane</button>
      <button data-act="junkall">Oznaci oznacene kao otpad</button></div>
    ${sug.length ? `<h3>Predlozi (potvrdi)</h3><div class="card">${sug.map(s => `<div class="row"><input type="checkbox" class="sg" data-tid="${s.track_id}" data-tag="${esc(s.tag)}" checked>
      ${esc(s.artist)} - ${esc(s.title)} <span class="badge acc">${esc(s.display)}</span></div>`).join('')}
      <button class="ok" data-act="sgyes">Prihvati oznacene</button> <button data-act="sgno">Odbaci oznacene</button></div>` : ''}
    <table><tr><th></th><th>Tag</th><th>Pesama</th><th>Status</th><th></th></tr>${tags.map(t => `<tr><td><input type="checkbox" class="tg" data-n="${esc(t.name)}"></td>
      <td>${esc(t.display)}</td><td>${t.n}</td><td>${t.junk ? '<span class="badge bad">otpad</span>' : ''}${t.ignored ? '<span class="badge">ignorisan</span>' : ''}</td>
      <td><button class="sm" data-act="tagren" data-n="${esc(t.name)}">Preimenuj/spoji</button>
      <button class="sm" data-act="tagign" data-n="${esc(t.name)}" data-v="${t.ignored ? 0 : 1}">${t.ignored ? 'Vrati' : 'Ignorisi'}</button>
      <button class="sm bad" data-act="tagdel" data-n="${esc(t.name)}">Obrisi</button></td></tr>`).join('')}</table>`;
};

// --- Duplikati ---
pages.dupes = async () => {
  $('#main').innerHTML = `<h2>Duplikati</h2><p class="mut">Poredjenje po trajanju i audio otisku (ako je instaliran fpcalc), inace po trajanju i metapodacima. Mashup/edit/remix verzije nisu duplikati originala. Nista se ne brise: fajlovi se samo premestaju u folder za duplikate posle potvrde.</p>
    <button class="pri" data-act="finddupes">Trazi duplikate</button><div id="dupres"></div>`;
};

// --- Plejliste ---
pages.pl = async () => {
  const [pls, src] = await Promise.all([api('/api/playlists'), api('/api/src-playlists')]);
  state.pls = pls;
  $('#main').innerHTML = `<h2>Plejliste</h2><p class="mut">Plejliste zive u aplikaciji. Izvoz je M3U8 fajl koji mozes da uvezes/prevucices u Traktor; kolekcija se ne menja.</p>
    <div class="row">${pls.map(p => `<button class="tab" data-act="plopen" data-id="${p.id}">${esc(p.name)}</button>`).join('')}<button class="pri" data-act="plnew">+ Nova pravilna</button></div>
    <div id="plbody"></div>
    <h3>Moje liste iz kojih aplikacija uci (${src.filter(s => s.use_learn).length}/${src.length})</h3>
    <div class="card" style="max-height:320px;overflow:auto">${src.map(s => `<div class="row"><input type="checkbox" data-act="learn" data-id="${s.id}" ${s.use_learn ? 'checked' : ''}> ${esc(s.name)} <span class="mut small">(${s.n}, ${esc(s.source)})</span></div>`).join('')}</div>`;
};

function plForm(p) {
  const r = p ? (typeof p.rules === 'string' ? JSON.parse(p.rules || '{}') : p.rules) : {};
  const f = (id, label, v = '', w = 90) => `<label class="small mut">${label}</label><input id="${id}" value="${esc(v ?? '')}" style="width:${w}px">`;
  return `<div class="card"><div class="row">${f('pn', 'Naziv', p ? p.name : '', 200)}
    ${f('pt1', 'Tagovi (svi)', (r.tags_all || []).join(','), 160)}${f('pt2', 'Tagovi (bilo koji)', (r.tags_any || []).join(','), 160)}${f('pt3', 'Bez tagova', (r.tags_none || []).join(','), 140)}</div>
    <div class="row">${f('pb1', 'BPM od', r.bpm_min)}${f('pb2', 'BPM do', r.bpm_max)}${f('pe1', 'E od', r.energy_min, 60)}${f('pe2', 'E do', r.energy_max, 60)}
    ${f('py1', 'God. od', r.year_min)}${f('py2', 'God. do', r.year_max)}${f('pd', 'Uvezeno u poslednjih N dana', r.import_days, 70)}${f('pg', 'Zanr', r.genre, 120)}${f('pl', 'Limit', r.limit || 200, 70)}</div>
    <button class="pri" data-act="plprev">Pregled</button> <button class="ok" data-act="plsave" data-id="${p ? p.id : ''}">Sacuvaj</button></div><div id="plprev"></div>`;
}
function readRules() {
  const sp = v => v.split(',').map(x => x.trim()).filter(Boolean), v = id => $('#' + id).value;
  return {tags_all: sp(v('pt1')), tags_any: sp(v('pt2')), tags_none: sp(v('pt3')), bpm_min: v('pb1'), bpm_max: v('pb2'), energy_min: v('pe1'),
    energy_max: v('pe2'), year_min: v('py1'), year_max: v('py2'), import_days: v('pd'), genre: v('pg'), limit: v('pl'), sort: 'import', dir: 'desc'};
}
function trackList(items, extra) {
  return `<table>${items.map((t, i) => `<tr><td>${i + 1}</td><td>${name(t)}</td><td>${badges(t)}</td>${extra ? `<td>${extra(t)}</td>` : ''}</tr>`).join('')}</table>`;
}

// --- Vece ---
const DEFAULT_PARTS = 'Docek; 3; 30\nVecera; 4; 60\nPrvi ples; 5; 15\nTorta; 6; 15\nVrhunac; 9; 60\nKraj; 8; 30';
pages.eve = async () => {
  const evs = await api('/api/events');
  const ev = evs.find(e => e.active) || evs[0];
  const parts = ev ? ev.parts : [];
  const plays = ev ? await api('/api/plays?event_id=' + ev.id) : [];
  $('#main').innerHTML = `<h2>Vece i istorija</h2>
    <div class="row"><input id="evn" placeholder="Naziv novog dogadjaja"><button data-act="evnew" class="pri">Novi dogadjaj</button>
      ${evs.map(e => `<button class="tab ${e.active ? 'on' : ''}" data-act="evact" data-id="${e.id}">${esc(e.name)} (${e.plays})</button>`).join('')}</div>
    ${ev ? `<h3>Plan: ${esc(ev.name)}</h3>
    <div class="curve">${parts.map(p => `<div style="height:${p.energy * 10}%" data-act="partsug" data-id="${ev.id}" data-pos="${p.pos}"><span>${esc(p.name)}<br>E${p.energy}</span></div>`).join('')}</div>
    <textarea id="parts" placeholder="naziv; energija; minuta">${esc(parts.length ? parts.map(p => `${p.name}; ${p.energy}; ${p.minutes}`).join('\n') : DEFAULT_PARTS)}</textarea>
    <button data-act="saveparts" data-id="${ev.id}">Sacuvaj plan</button> <span class="mut small">Klik na stub u krivi = predlozi za taj deo.</span>
    <div id="partres"></div>
    <h3>Odsvirano (${plays.length})</h3><div class="card">${plays.map((p, i) => `<div class="row"><span class="mut small">${esc(p.ts.slice(11, 16))}</span> ${esc(p.artist)} - ${esc(p.title)}
      <button class="sm ${p.rating > 0 ? 'ok' : ''}" data-act="rate" data-id="${p.id}" data-r="1" data-reload="1">palo</button>
      <button class="sm ${p.rating < 0 ? 'bad' : ''}" data-act="rate" data-id="${p.id}" data-r="-1" data-reload="1">nije palo</button>
      ${i > 0 ? `<button class="sm" data-act="note" data-a="${plays[i - 1].track_id}" data-b="${p.track_id}">beleska o prelazu</button>` : ''}
      <button class="sm bad" data-act="delplay" data-id="${p.id}">x</button></div>`).join('')}</div>` : ''}`;
};

// --- Zahtevi ---
pages.req = async () => {
  const [s, st, reqs, bans] = await Promise.all([api('/api/settings'), api('/api/status'), api('/api/requests'), api('/api/bans')]);
  const ev = st.event;
  $('#main').innerHTML = `<h2>Zahtevi gostiju</h2>
    <div class="card row"><img src="/api/qr?kind=guest" width="170" height="170" alt="QR"><div><div class="big">${esc(st.guest_url)}</div>
      <div class="mut small">Telefoni moraju biti na istoj Wi-Fi mrezi kao ovaj racunar (Windows firewall mora da dozvoli port ${s.port}).</div>
      <div class="row"><button class="${ev && ev.requests_open ? 'ok' : 'bad'}" data-act="reqopen" data-id="${ev ? ev.id : 0}" data-v="${ev && ev.requests_open ? 0 : 1}">Zahtevi: ${ev && ev.requests_open ? 'OTVORENI' : 'ZATVORENI'}</button></div></div></div>
    <h3>Red zahteva</h3>
    <table><tr><th>Glasova</th><th>Pesma</th><th>Gost</th><th>Status</th><th>Uklapanje</th><th></th></tr>${reqs.map(r => `<tr>
      <td>${r.votes}</td><td>${r.track ? name(r.track) + '<br>' + badges(r.track) : esc(r.free_text)}
        <div>${r.in_library ? '<span class="badge ok">imam</span>' : '<span class="badge bad">nemam</span>'}${r.played ? '<span class="badge acc">vec puštena</span>' : ''}${r.banned ? '<span class="badge bad">zabranjena</span>' : ''}</div></td>
      <td>${esc(r.guest)}<br><span class="mut small">${esc(r.message)}</span>${r.tip ? '<br><span class="badge ok">baksis: ' + esc(r.tip) + '</span>' : ''}</td>
      <td>${esc(r.status)}</td><td class="small">${r.fit ? 'BPM ' + r.fit.bpm + ' / key ' + r.fit.key : ''}</td>
      <td><button class="sm ok" data-act="reqst" data-id="${r.id}" data-s="prihvaceno">prihvati</button>
      <button class="sm" data-act="reqst" data-id="${r.id}" data-s="odlozeno">odlozi</button>
      <button class="sm bad" data-act="reqst" data-id="${r.id}" data-s="odbijeno">odbij</button>
      <button class="sm" data-act="reqtip" data-id="${r.id}">baksis</button>
      ${r.track ? `<button class="sm" data-act="use" data-id="${r.track.id}">trenutna</button>` : ''}</td></tr>`).join('')}</table>
    <h3>Zabranjeno za ovaj dogadjaj</h3><div class="card">${bans.map(b => `<span class="badge bad">${esc(b.kind)}: ${esc(b.kind === 'track' ? b.value : b.value)} <a href="#" data-act="delban" data-id="${b.id}">x</a></span>`).join('') || '<span class="mut">Nista.</span>'}
      <div class="row"><select id="bankind"><option value="genre">zanr</option><option value="text">izvodjac/naslov sadrzi</option><option value="track">ID pesme</option></select>
      <input id="banval" placeholder="vrednost"><button data-act="addban">Dodaj</button></div></div>
    <h3>Baksis</h3><div class="card"><label class="small mut">Tekst za goste</label><input id="tiptext" value="${esc(s.tip_text)}" style="width:100%">
      <label class="small mut">IPS QR sadrzaj (tekst iz IPS QR koda tvoje banke; QR se generise na stranici za goste)</label><input id="tipips" value="${esc(s.tip_ips_payload)}" style="width:100%">
      <div class="row"><button data-act="savetip" class="pri">Sacuvaj</button><label class="btn">Slika za gotovinu <input type="file" id="tipimg" accept="image/png,image/jpeg,image/webp" class="hidden"></label></div>
      <p class="mut small">Baksis ne obavezuje na pesmu. Prihode vodi u skladu sa svojim statusom (pitaj knjigovodju).</p></div>`;
  $('#tipimg').addEventListener('change', guard(async e => {
    const f = e.target.files[0]; if (!f) return;
    const r = await fetch('/api/tip-image', {method: 'POST', headers: {'Content-Type': f.type}, body: f});
    toast(r.ok ? 'Slika sacuvana' : 'Slika nije prihvacena', !r.ok);
  }));
};

// --- AI ---
pages.ai = async () => {
  const st = await api('/api/status');
  $('#main').innerHTML = `<h2>AI predlog po situaciji</h2>${st.ai ? '' : '<p class="mut">AI nije podesen: unesi Anthropic API kljuc u Podesavanjima. Ostalo radi i bez AI-ja.</p>'}
    <textarea id="aitext" placeholder="npr. kraj vecere, publika vec pije, hocu pesme za pevanje u glas, mesovita publika 30-50 god."></textarea>
    <div class="row"><input id="aicount" type="number" value="15" style="width:80px"><button class="pri" data-act="aisit">Predlozi iz moje biblioteke</button></div><div id="aires"></div>`;
};

// --- Trendovi ---
pages.trend = async () => {
  const [src, items] = [await api('/api/trends/sources'), await api('/api/trends/items')];
  const yt = l => { const m = /(?:youtu\.be\/|v=)([\w-]{11})/.exec(l || ''); return m ? m[1] : null; };
  $('#main').innerHTML = `<h2>Trendovi sa zabava i festivala</h2>
    <p class="mut">Samo kad ima interneta. Izvori su RSS/Atom feedovi koje sam dodas (proveri uslove koriscenja svakog izvora) i linkovi koje dodas rucno. Sajtovi se ne "grebu". Prikazuje se samo ono sto je stvarno pronadjeno.</p>
    <div class="card"><div class="row"><input id="tsn" placeholder="Naziv izvora"><input id="tsu" placeholder="URL RSS/Atom feeda" style="flex:1"><button data-act="tsadd">Dodaj izvor</button></div>
    ${src.map(s => `<div class="row"><b>${esc(s.name)}</b> <span class="mut small">${esc(s.url)}</span><button class="sm" data-act="tsfetch" data-id="${s.id}">Preuzmi</button><button class="sm bad" data-act="tsdel" data-id="${s.id}">x</button></div>`).join('')}</div>
    <div class="card"><div class="row"><input id="tmsong" placeholder="Izvodjac - Naslov"><input id="tmlink" placeholder="YouTube/Spotify link" style="flex:1"><button data-act="tmadd">Dodaj rucno</button></div></div>
    <table><tr><th>Pesma</th><th>Uklapanje</th><th>Biblioteka</th><th>Preslusaj</th><th></th></tr>${items.map(i => `<tr><td>${esc(i.song)}</td>
      <td>${i.fit != null ? i.fit : ''}<div class="small mut">${esc(i.why)}</div></td>
      <td>${i.track ? '<span class="badge ok">imam</span> ' + name(i.track) : '<span class="badge bad">nemam</span>' + (i.similar ? '<div class="small mut">slicno: ' + i.similar.map(x => name(x)).join('; ') + '</div>' : '')}</td>
      <td>${i.link ? (yt(i.link) ? `<button class="sm" data-act="play" data-v="${yt(i.link)}">▶ plejer</button> ` : '') + `<a href="${esc(i.link)}" target="_blank" rel="noopener noreferrer">link</a>` : ''}</td>
      <td><button class="sm bad" data-act="tidel" data-id="${i.id}">x</button></td></tr>`).join('')}</table>`;
};

// --- Podesavanja ---
pages.set = async () => {
  const s = await api('/api/settings'), st = await api('/api/status');
  const w = s.weights, W = {hist: 'Moje liste (history)', key: 'Key harmonija', bpm: 'BPM', vibe: 'Vajb (tagovi/pozicija)', energy: 'Energija', year: 'Godina', genre: 'Zanr', feedback: 'Ocene (palo/nije)'};
  $('#main').innerHTML = `<h2>Podesavanja</h2>
    <div class="card"><h3>Biblioteka</h3>
      <label class="small mut">Muzicki folderi (jedan po redu)</label><textarea id="sfold">${esc(s.music_folders.join('\n'))}</textarea>
      <label class="small mut">Traktor collection.nml (samo se cita, radi se na kopiji)</label><input id="snml" value="${esc(s.nml_path)}" style="width:100%">
      <label class="small mut">Traktor History folder (za pracenje trenutne pesme)</label><input id="shist" value="${esc(s.history_dir)}" style="width:100%">
      <label class="small mut">Folder za duplikate</label><input id="sdup" value="${esc(s.dupes_dir)}" style="width:100%">
      <label class="small mut">Folder za izvoz plejlista</label><input id="sexp" value="${esc(s.export_dir)}" style="width:100%">
      <div class="row"><button class="pri" data-act="saveset">Sacuvaj</button><button data-act="scan">Skeniraj foldere</button><button data-act="impnml">Ucitaj Traktor NML</button><button data-act="refresh">Sve</button></div></div>
    <div class="card"><h3>Pracenje iz Traktora</h3>
      <p>Broadcast prijemnik: ${st.broadcast.running ? 'radi na portu ' + st.broadcast.port : '<b>ne radi</b> ' + esc(st.broadcast.error || '')}${st.broadcast.last_song ? '<br>Poslednje: ' + esc(st.broadcast.last_song) + (st.broadcast.last_match ? ' (pronadjeno)' : ' (NIJE pronadjeno u biblioteci)') : ''}
      <br><span class="mut small">Zahteva: ${st.broadcast.requests}, bajtova toka: ${st.broadcast.bytes}</span>${(st.broadcast.log || []).length ? '<div class="small mut">Poslednji zahtevi iz Traktora:</div>' + st.broadcast.log.map(l => '<pre class="small mut" style="white-space:pre-wrap;margin:4px 0">' + esc(l) + '</pre>').join('') : ''}</p>
      <p class="mut small">U Traktoru: Preferences → Broadcasting → Server: 127.0.0.1, Port: ${s.broadcast_port}, Format Icecast 2, Mount: /live, lozinka bilo koja, pa ukljuci Broadcast (crvena ikonica). History fajl: ${esc(st.history.file || 'nije nadjen')}.</p>
      <div class="row"><label>Broadcast port</label><input id="sbp" value="${s.broadcast_port}" style="width:90px"><label><input type="checkbox" id="sbe" ${s.broadcast_enabled ? 'checked' : ''}> ukljucen (restart)</label></div></div>
    <div class="card"><h3>Tezine predloga</h3>${Object.keys(W).map(k => `<div class="row"><span style="width:240px">${W[k]}</span><input type="range" min="0" max="5" step="0.1" id="w_${k}" value="${w[k]}"><span class="small" id="wv_${k}">${w[k]}</span></div>`).join('')}
      <button data-act="saveset">Sacuvaj</button> <button data-act="rebuild">Ponovo nauci iz mojih lista</button></div>
    <div class="card"><h3>Profil publike (za AI i trendove)</h3>${Object.entries(s.audience).filter(([k]) => k !== 'cista_verzija').map(([k, v]) => `<div class="row"><span style="width:160px">${k}</span><input id="a_${k}" value="${esc(v)}" style="flex:1"></div>`).join('')}
      <label><input type="checkbox" id="a_cista" ${s.audience.cista_verzija ? 'checked' : ''}> samo cista verzija</label></div>
    <div class="card"><h3>AI (opciono)</h3><div class="row"><input id="skey" type="password" placeholder="${s.anthropic_key_set ? 'kljuc je sacuvan (upisi novi da zamenis)' : 'Anthropic API kljuc'}" style="flex:1"><input id="smodel" value="${esc(s.model)}" style="width:220px"></div>
      <p class="mut small">Kljuc se cuva lokalno u data/settings.json. Za AI se salje samo kratak spisak pesama iz biblioteke.</p></div>
    <div class="card"><h3>Gosti</h3><div class="row"><label>Max zahteva</label><input id="glc" value="${s.guest_limit.count}" style="width:70px"><label>na minuta</label><input id="glm" value="${s.guest_limit.minutes}" style="width:70px"></div>
      <p class="mut small">PIN za tablet: <b>${esc(s.dj_pin)}</b> — otvori na tabletu http://IP:${s.port}/login?pin=PIN</p></div>
    <button class="pri" data-act="saveset">Sacuvaj sve</button>`;
  $$('input[type=range]').forEach(r => r.addEventListener('input', () => $('#wv_' + r.id.slice(2)).textContent = r.value));
};

async function saveSettings() {
  const s = {
    music_folders: $('#sfold').value.split('\n').map(x => x.trim()).filter(Boolean), nml_path: $('#snml').value.trim(), history_dir: $('#shist').value.trim(),
    dupes_dir: $('#sdup').value.trim(), export_dir: $('#sexp').value.trim(), broadcast_port: +$('#sbp').value, broadcast_enabled: $('#sbe').checked, model: $('#smodel').value.trim(),
    weights: Object.fromEntries($$('input[type=range]').map(r => [r.id.slice(2), +r.value])),
    guest_limit: {count: +$('#glc').value, minutes: +$('#glm').value}, anthropic_key: $('#skey').value,
    audience: {...Object.fromEntries($$('[id^=a_]').filter(x => x.id !== 'a_cista').map(x => [x.id.slice(2), x.value])), cista_verzija: $('#a_cista').checked}};
  await api('/api/settings', s); toast('Sacuvano');
}

// ---------- actions ----------
const sendRating = async (id, r) => { await api(`/api/plays/${id}/rate`, {rating: r}); };
const actions = {
  go: el => go(el.dataset.p),
  mini: () => {
    state.view = {full: 'mini', mini: 'tiker', tiker: 'full'}[state.view];
    state.mini = state.view !== 'full'; state.tiker = state.view === 'tiker';
    localStorage.setItem('view', state.view); go('sada');
  },
  fs: el => { state.fs = Math.max(10, Math.min(90, state.fs + +el.dataset.d)); localStorage.setItem('fs', state.fs); applyFs(); },
  tiknext: () => { if (state.tikItems.length) { state.tikIdx = (state.tikIdx + 1) % state.tikItems.length; renderTik(true); } },
  mode: async el => { state.mode = el.dataset.m; state._drawModes(); if (state.curId) await loadSuggest(await api('/api/now')); },
  strict: async el => { state.strict = el.dataset.m; state._drawModes(); await api('/api/settings', {style_strictness: state.strict}); if (state.curId) await loadSuggest(await api('/api/now')); },
  use: async el => { await api('/api/now', {track_id: +el.dataset.id}); state.curId = null; closeModal(); if (state.page === 'sada') await tickNow(false); else toast('Postavljeno kao trenutna'); },
  copysong: async el => { await api('/api/copy-song/' + el.dataset.id, {}); toast('Audio fajl kopiran — ne samo putanja'); },
  dragsong: async el => { await api('/api/drag-song/' + el.dataset.id, {}); toast('Drzi naziv u otvorenom prozoru i prevuci ga u zaustavljeni Traktor deck'); },
  reveal: async el => { await api('/api/reveal/' + el.dataset.id, {}); },
  rate: async el => { await sendRating(el.dataset.id, +el.dataset.r); state.playedSig = ''; if (el.dataset.reload) go('eve'); else await tickNow(false); },
  closemodal: () => closeModal(),
  // library
  libgo: async () => { readLibFilters(); await loadLib(); },
  sort: async el => { const L = state.lib; L.dir = L.sort === el.dataset.k && L.dir === 'asc' ? 'desc' : 'asc'; L.sort = el.dataset.k; await loadLib(); },
  pg: async el => { state.lib.page += +el.dataset.d; await loadLib(); },
  sel: el => { el.checked ? state.sel.add(+el.dataset.id) : state.sel.delete(+el.dataset.id); $('#selcount').textContent = state.sel.size; },
  selall: el => { $$('[data-act=sel]').forEach(c => { c.checked = el.checked; actions.sel(c); }); },
  selnone: () => { state.sel.clear(); $$('[data-act=sel]').forEach(c => c.checked = false); $('#selcount').textContent = 0; },
  bulkadd: async () => { await api('/api/tracks/tag', {ids: [...state.sel], add: $('#bulkadd').value.split(',').map(x => x.trim()).filter(Boolean)}); toast('Dodato'); await loadLib(); },
  bulkrem: async () => { await api('/api/tracks/tag', {ids: [...state.sel], remove: $('#bulkrem').value.split(',').map(x => x.trim()).filter(Boolean)}); toast('Uklonjeno'); await loadLib(); },
  edit: el => openTrack(+el.dataset.id),
  savetrack: async el => {
    await api('/api/track/' + el.dataset.id, {tags: $('#etags').value.split(',').map(x => x.trim()).filter(Boolean), energy: $('#eenergy').value, style: $('#estyle').value});
    closeModal(); toast('Sacuvano'); if (state.page === 'lib') await loadLib();
  },
  // tags
  aitags: async () => { const r = await api('/api/ai/tag-suggest', {}); toast('Novih predloga: ' + r.added); go('tags'); },
  sgyes: async () => sgApply(true), sgno: async () => sgApply(false),
  tagren: async el => { const n = prompt('Novi naziv (ako vec postoji, tagovi se spajaju):', el.dataset.n); if (n) { await api('/api/tags/rename', {name: el.dataset.n, new: n}); go('tags'); } },
  tagign: async el => { await api('/api/tags/flag', {names: [el.dataset.n], ignored: +el.dataset.v}); go('tags'); },
  tagdel: async el => { if (confirm('Obrisati tag sa svih pesama?')) { await api('/api/tags/delete', {name: el.dataset.n}); go('tags'); } },
  junkall: async () => { await api('/api/tags/flag', {names: $$('.tg:checked').map(c => c.dataset.n), junk: 1, ignored: 1}); go('tags'); },
  audiosearch: async () => audioLoadSources(),
  audiomatches: audioLoadMatches,
  audiocurrent: async () => { const n = await api('/api/now'); if (!n.track_id) throw new Error('Nema trenutne pesme.'); state.audioSource = n.track_id; await audioLoadSources(n.track_id); await audioLoadMatches(); },
  audiothreshold: async () => { const value = +$('#audioThresholdNumber').value / 100; await api('/api/audio/threshold', {threshold: value}); state.audioSavedThreshold = value; toast('Prag sacuvan; koristi ga peti audio predlog'); await audioLoadMatches(); },
  audiocopyreport: async () => { if (!state.audioReport) throw new Error('Prvo prikazi slicne pesme.'); await navigator.clipboard.writeText(JSON.stringify(state.audioReport, null, 2)); toast('Audio izvestaj kopiran'); },
  scanstart: async el => { const endpoint = el.dataset.kind === 'grid_audio' ? '/api/grids/audio-search' : '/api/audio/scan'; await api(endpoint, {}); toast('Sken pokrenut / nastavljen'); await refreshStatus(); },
  scanstop: async el => { await api('/api/job/stop', {kind: el.dataset.kind}); toast('Zaustavljam sken; zavrseni rezultati ostaju'); await refreshStatus(); },
  gridscan: async () => { await api('/api/grids/scan', {}); toast('Provera gridova pokrenuta'); await refreshStatus(); },
  // dupes
  finddupes: async () => {
    $('#dupres').innerHTML = '<p class="mut">Trazim...</p>';
    const g = await api('/api/dupes');
    $('#dupres').innerHTML = `<p>${g.length} grupa</p>` + g.map((x, gi) => `<div class="card"><b>${esc(x.title)}</b> <span class="mut small">${esc(x.method)}</span>
      <div class="${x.conflict ? 'badge bad' : 'badge ok'}" style="display:block;margin:6px 0">${esc(x.message)}</div>
      ${x.files.map(f => `<div class="row"><input type="checkbox" class="dp" data-id="${f.id}" ${f.id === x.keep_id || f.protected ? '' : 'checked'}> <span class="badge">${esc(f.fmt)}</span>${f.id === x.keep_id ? '<span class="badge ok">ostaje</span>' : ''}${f.protected ? '<span class="badge acc">zasticeno (cue/loop/ocena)</span>' : ''}
        <span class="small">${esc(f.path)} ${mins(f.duration)} ${f.bitrate ? Math.round(f.bitrate / 1000) + 'k' : ''}</span></div>`).join('')}</div>`).join('') +
      (g.length ? '<button class="bad" data-act="movedupes">Premesti oznacene u folder za duplikate</button>' : '');
  },
  movedupes: async () => {
    const ids = $$('.dp:checked').map(c => +c.dataset.id);
    if (!ids.length || !confirm(`Premestiti ${ids.length} fajl(ova) u folder za duplikate?`)) return;
    const r = await api('/api/dupes/move', {ids, confirm: true}); toast(`Premesteno: ${r.filter(x => x.ok).length}`);
  },
  // playlists
  plopen: async el => {
    const id = +el.dataset.id, p = state.pls.find(x => x.id === id);
    const items = await api(`/api/playlists/${id}/tracks`);
    $('#plbody').innerHTML = `<h3>${esc(p.name)} (${items.length})</h3><div class="row"><button class="pri" data-act="plexp" data-id="${id}">Izvezi M3U8</button>${id ? `<button class="bad" data-act="pldel" data-id="${id}">Obrisi</button>` : ''}</div>` +
      (p.kind === 'rule' && id ? plForm(p) : '') + trackList(items.slice(0, 300));
  },
  plnew: () => { $('#plbody').innerHTML = plForm(null); },
  plprev: async () => { const items = await api('/api/playlists/preview', readRules()); $('#plprev').innerHTML = `<p>${items.length}+ pesama</p>` + trackList(items.slice(0, 100)); },
  plsave: async el => { await api('/api/playlists', {id: el.dataset.id ? +el.dataset.id : null, name: $('#pn').value || 'Plejlista', kind: 'rule', rules: readRules()}); go('pl'); },
  plexp: async el => { const r = await api(`/api/playlists/${el.dataset.id}/export`, {}); toast('Izvezeno: ' + r.path); },
  pldel: async el => { if (confirm('Obrisati plejlistu?')) { await api('/api/playlists/' + el.dataset.id, undefined, 'DELETE'); go('pl'); } },
  learn: async el => { await api('/api/src-playlists/' + el.dataset.id, {use: el.checked ? 1 : 0}); toast('Preracunato'); },
  // events
  evnew: async () => { const n = $('#evn').value.trim(); if (n) { const r = await api('/api/events', {name: n}); await api(`/api/events/${r.id}/activate`, {}); go('eve'); } },
  evact: async el => { await api(`/api/events/${el.dataset.id}/activate`, {}); go('eve'); },
  saveparts: async el => {
    const parts = $('#parts').value.split('\n').map(l => l.split(';').map(x => x.trim())).filter(a => a[0]).map(a => ({name: a[0], energy: +a[1] || 5, minutes: +a[2] || 30}));
    await api(`/api/events/${el.dataset.id}/parts`, {parts}); go('eve');
  },
  partsug: async el => {
    const d = await api(`/api/events/${el.dataset.id}/part-suggest/${el.dataset.pos}`);
    $('#partres').innerHTML = `<h3>Predlozi za: ${esc(d.part.name)} (E${d.part.energy})</h3>` + trackList(d.items, t => `<button class="sm" data-act="use" data-id="${t.id}">trenutna</button>`);
  },
  note: async el => { const n = prompt('Beleska o prelazu (npr. ubaci na refren):'); if (n !== null) { await api('/api/notes', {a: +el.dataset.a, b: +el.dataset.b, note: n}); toast('Sacuvano'); } },
  delplay: async el => { await api('/api/plays/' + el.dataset.id, undefined, 'DELETE'); go('eve'); },
  // requests
  reqopen: async el => { await api(`/api/events/${el.dataset.id}/requests-open`, {open: +el.dataset.v}); go('req'); },
  reqst: async el => { await api('/api/requests/' + el.dataset.id, {status: el.dataset.s}); go('req'); },
  reqtip: async el => { const t = prompt('Baksis (npr. gotovina / 500 din), prazno = ukloni:'); if (t !== null) { await api(`/api/requests/${el.dataset.id}/tip`, {tip: t || null}); go('req'); } },
  addban: async () => { if ($('#banval').value.trim()) { await api('/api/bans', {kind: $('#bankind').value, value: $('#banval').value.trim()}); go('req'); } },
  delban: async el => { await api('/api/bans/' + el.dataset.id, undefined, 'DELETE'); go('req'); },
  savetip: async () => { await api('/api/settings', {tip_text: $('#tiptext').value, tip_ips_payload: $('#tipips').value}); toast('Sacuvano'); },
  // ai
  aisit: async () => {
    $('#aires').innerHTML = '<p class="mut">AI razmislja...</p>';
    const items = await api('/api/ai/situation', {text: $('#aitext').value, count: +$('#aicount').value});
    state.aiItems = items;
    $('#aires').innerHTML = `<div class="row"><button class="ok" data-act="aisave">Snimi kao plejlistu</button></div>` + trackList(items, t => esc(t.why));
  },
  aisave: async () => { const n = prompt('Naziv plejliste:', $('#aitext').value.slice(0, 40)); if (n) { await api('/api/playlists', {name: n, kind: 'static', track_ids: state.aiItems.map(t => t.id)}); toast('Sacuvano u Plejliste'); } },
  // trends
  tsadd: async () => { await api('/api/trends/sources', {name: $('#tsn').value, url: $('#tsu').value, kind: 'rss'}); go('trend'); },
  tsdel: async el => { await api('/api/trends/sources/' + el.dataset.id, undefined, 'DELETE'); go('trend'); },
  tsfetch: async el => { const r = await api('/api/trends/fetch/' + el.dataset.id, {}); toast('Novih: ' + r.added); go('trend'); },
  tmadd: async () => { await api('/api/trends/items', {song: $('#tmsong').value, link: $('#tmlink').value}); go('trend'); },
  tidel: async el => { await api('/api/trends/items/' + el.dataset.id, undefined, 'DELETE'); go('trend'); },
  play: el => { $('#modalbox').innerHTML = `<iframe width="100%" height="360" src="https://www.youtube.com/embed/${encodeURIComponent(el.dataset.v)}" allow="autoplay; encrypted-media" allowfullscreen></iframe><div class="row"><button data-act="closemodal">Zatvori</button></div>`; $('#modal').classList.remove('hidden'); },
  // settings
  saveset: saveSettings,
  scan: async () => { await saveSettings(); await api('/api/scan', {}); toast('Skeniranje pokrenuto'); },
  impnml: async () => { await saveSettings(); await api('/api/import-nml', {}); toast('Citanje NML pokrenuto'); },
  refresh: async () => { await saveSettings(); await api('/api/refresh', {}); toast('Osvezavanje pokrenuto'); },
  rebuild: async () => { await api('/api/learning/rebuild', {}); toast('Preracunato'); },
};
async function sgApply(accept) {
  const items = $$('.sg:checked').map(c => ({track_id: +c.dataset.tid, tag: c.dataset.tag}));
  await api('/api/tag-suggestions/apply', {items, accept}); go('tags');
}
function closeModal() { $('#modal').classList.add('hidden'); $('#modalbox').innerHTML = ''; }

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el || el.tagName === 'INPUT') return;
  if (el.tagName === 'A') e.preventDefault();
  const fn = actions[el.dataset.act]; if (fn) guard(fn)(el, e);
});
document.addEventListener('change', e => {
  const el = e.target;
  if (el.tagName === 'INPUT' && el.type === 'checkbox' && el.dataset.act) { const fn = actions[el.dataset.act]; if (fn) guard(fn)(el, e); }
});
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Enter' && state.page === 'lib' && e.target.id && e.target.id.startsWith('l')) actions.libgo(); });

refreshStatus();
api('/api/styles').then(s => state.styleLabels = s).catch(() => {});
api('/api/settings').then(s => { state.strict = s.style_strictness || 'normalno'; }).catch(() => {}).finally(() => go('sada'));


pages.audio = async () => {
  const s = await api('/api/audio/status');
  state.audioSavedThreshold = s.threshold;
  $('#main').innerHTML = `<h2>Audio ritam — deep scan / slicne pesme</h2>
  <p>Skenirano: <b>${s.counts.ok || 0}</b>; greske: ${s.counts.error || 0}. Postojeci sken se koristi odmah; poredjenje i promena praga ne zahtevaju novi sken.</p>
  ${scanButtons('audio')}
  <div class="card"><h3>Prag audio slicnosti</h3>
  <div class="row"><input type="range" id="audioThresholdRange" min="50" max="100" step="1" value="${Math.round(s.threshold*100)}" style="width:260px">
  <input type="number" id="audioThresholdNumber" min="50" max="100" step="1" value="${Math.round(s.threshold*100)}" style="width:80px"> %
  <button class="pri" data-act="audiothreshold">Sacuvaj prag</button></div>
  <p>Nizi prag prihvata slabiju slicnost. Promena klizaca sluzi za pregled liste; tek 'Sacuvaj prag' menja peti predlog. Uslovi stabilnosti ritma i audio tempa i dalje vaze. Skor nije verovatnoca niti dokaz identicnog ritma.</p></div>
  <div class="card"><h3>Sta je sken pronasao slicno?</h3>
  <div class="row"><input id="audioQuery" placeholder="Pretrazi skenirane pesme po izvodjacu / naslovu" style="min-width:300px"><button data-act="audiosearch">Pretrazi</button><button data-act="audiocurrent">Trenutna pesma iz Traktora</button></div>
  <div class="row"><select id="audioSource" style="max-width:75%"></select><button class="pri" data-act="audiomatches">Prikazi slicne</button></div>
  <p>Ova lista poredi celu skeniranu biblioteku. Automatski peti predlog dodatno postuje izvorne filtere i ne ponavlja prve cetiri pesme.</p>
  <div id="audioResults"></div></div>
  ${s.errors.length ? '<details><summary>Greske skeniranja</summary>' + s.errors.map(t => `<p>${name(t)}: ${esc(t.error)}</p>`).join('') + '</details>' : ''}`;
  updateScanControls(state.status?.job || {});
  await audioLoadSources(state.audioSource || state.curId);
  if ($('#audioSource').value) await audioLoadMatches();
};

function audioDiagnosticHtml(d) {
  if (!d || !d.current_id) return '';
  return `<details class="card"><summary>Zasto jeste / nije izabran AUDIO RITAM?</summary>
  <p>Prag: ${(d.threshold*100).toFixed(0)}%; kandidata posle izvornih filtera: ${d.pool}; audio uporedjeno: ${d.compared}.</p>
  ${d.current_error ? '<p>' + esc(d.current_error) + '</p>' : `<p>Trenutna pesma, audio: ${d.current_audio.bpm} BPM; periodicnost ${d.current_audio.periodicity}; stabilnost ${d.current_audio.consistency}; promenljiv tempo: ${d.current_audio.variable_tempo_suspected ? 'sumnja' : 'ne'}.</p>`}
  ${Object.entries(d.counts || {}).map(([k,v]) => `<div>${esc(k)}: ${v}</div>`).join('')}
  ${d.top.length ? '<p>Najblizi u ovom skupu:</p>' + d.top.map(t => `<div>${name(t)} — ${t.score.toFixed(2)}%: ${t.accepted ? 'prolazi audio uslove' : esc(t.reasons.join('; '))}</div>`).join('') : ''}</details>`;
}

async function audioLoadSources(preferred) {
  const q = $('#audioQuery')?.value || '';
  const rows = await api('/api/audio/tracks?q=' + encodeURIComponent(q));
  const id = Number(preferred || $('#audioSource')?.value || state.audioSource || 0);
  if (id && !rows.some(t => t.id === id)) {
    const track = await api('/api/track/' + id);
    if (track) rows.unshift(track);
  }
  $('#audioSource').innerHTML = rows.map(t => `<option value="${t.id}" ${t.id === id ? 'selected' : ''}>${name(t)}</option>`).join('');
  if (!rows.length) $('#audioResults').textContent = 'Nema skeniranih pesama za tu pretragu.';
}

async function audioLoadMatches() {
  const id = +$('#audioSource').value;
  if (!id) return;
  const cutoff = +$('#audioThresholdNumber').value / 100;
  $('#audioResults').textContent = 'Poredim sacuvane audio obrasce...';
  state.audioSource = id;
  const d = await api(`/api/audio/matches?track_id=${id}&threshold=${cutoff}`);
  if (state.page !== 'audio' || +$('#audioSource').value !== id) return;
  state.audioReport = d;
  $('#audioResults').innerHTML = `<h3>${name(d.current)}</h3><p>${esc(d.scope)}</p>
  <p>Prag pregleda: ${(d.threshold*100).toFixed(0)}%; sacuvani prag preporuka: ${(d.saved_threshold*100).toFixed(0)}%. Uporedjeno: ${d.compared}/${d.pool}; audio uslove prolazi: <b>${d.accepted_total}</b>.</p>
  ${d.current_error ? '<p>' + esc(d.current_error) + '</p>' : ''}
  <button data-act="audiocopyreport">Kopiraj audio izvestaj</button>
  ${audioDiagnosticHtml(d.recommendation)}
  <h3>Pronadjena audio poklapanja pri ovom pragu</h3>
  ${d.accepted_items.length ? d.accepted_items.map(t => `<div>${name(t)} — <b>${t.score.toFixed(2)}%</b></div>`).join('') : '<p>Nema rezultata koji prolaze sve audio uslove.</p>'}
  <h3>Najblizi rezultati i razlozi odbacivanja</h3><p>Do 50 najblizih audio rezultata. Odbaceni su vidljivi radi provere, ali nisu automatski predlozi.</p>
  <div style="overflow-x:auto"><table><thead><tr><th>Pesma</th><th>Audio slicnost</th><th>Obrazac udaraca</th><th>Audio BPM</th><th>Razlika tempa</th><th>Nalaz</th></tr></thead><tbody>
  ${d.items.map(t => `<tr><td>${name(t)}<div class="row"><button class="sm" data-act="copysong" data-id="${t.id}">Copy Song</button><button class="sm" data-act="dragsong" data-id="${t.id}">Prevuci</button><button class="sm" data-act="reveal" data-id="${t.id}">Explorer</button></div></td><td><b>${t.score.toFixed(2)}%</b></td><td>${t.groove.toFixed(2)}%</td><td>${t.audio.bpm}</td><td>${t.tempo_delta.toFixed(2)}%</td><td>${t.accepted ? '<span class="badge ok">PROLAZI AUDIO USLOVE</span>' : esc(t.reasons.join('; '))}</td></tr>`).join('')}</tbody></table></div>`;
}

document.addEventListener('input', e => {
  if (e.target.id === 'audioThresholdRange') $('#audioThresholdNumber').value = e.target.value;
  if (e.target.id === 'audioThresholdNumber') $('#audioThresholdRange').value = e.target.value;
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.id === 'audioQuery') guard(audioLoadSources)();
});

pages.grids = async () => {
  const d = await api('/api/grids');
  state.gridItems = d.items;
  $('#main').innerHTML = `<h2>Fleksibilni beatgridovi</h2>
  <button class="pri" data-act="gridscan" data-scan-start="grid_meta">Proveri gridove iz collection.nml</button>
  <h3>Audio pretraga promenljivog tempa</h3>${scanButtons('grid_audio')}
  <p>Provera NML cita Traktor markere. Audio pretraga koristi postojeci deep scan i obradjuje samo nove ili izmenjene fajlove. Audio nalaz je sumnja na promenljiv tempo, ne automatska popravka gridova.</p>
  <p>${d.items.length} pesama; ${d.nml_checked ? 'NML provera postoji' : 'pokreni proveru za detalje BPM markera'}.</p>
  <input id="gridQuery" placeholder="Pretrazi pronadjene fleksibilne gridove po nazivu / izvodjacu" style="width:100%;margin-bottom:12px">
  <div id="gridResults"></div>`;
  renderGridResults('');
  updateScanControls(state.status?.job || {});
};

function renderGridResults(query) {
  const q = query.toLocaleLowerCase();
  const items = (state.gridItems || []).filter(t => `${t.artist} ${t.title}`.toLocaleLowerCase().includes(q));
  $('#gridResults').innerHTML = `<p>Prikazano: ${items.length}</p><table><thead><tr><th>Pesma</th><th>Markeri / BPM vrednosti</th><th>Nalaz</th><th>Audio</th><th></th></tr></thead><tbody>
  ${items.map(t => `<tr><td>${name(t)}</td><td>${t.grid_markers_checked || 0} / ${t.grid_bpm_values ?? '?'}</td><td>${esc(t.grid_reason)}</td><td>${t.audio_variable ? 'sumnja na promenu tempa' : 'nema audio potvrde promene'}</td><td><button data-act="reveal" data-id="${t.id}">Explorer</button></td></tr>`).join('')}</tbody></table>`;
}

function scanButtons(kind) {
  return `<div class="row"><button class="pri" data-act="scanstart" data-kind="${kind}" data-scan-start="${kind}">Start / nastavi</button>
  <button class="bad" data-act="scanstop" data-kind="${kind}" data-scan-stop="${kind}" disabled>Stop</button>
  <span class="small mut" data-scan-status="${kind}">Sken miruje</span></div>`;
}

function updateScanControls(job) {
  $$('[data-scan-start]').forEach(el => { el.disabled = !!job.running; });
  $$('[data-scan-stop]').forEach(el => { el.disabled = !job.running || job.kind !== el.dataset.scanStop || !!job.stop_requested; });
  $$('[data-scan-status]').forEach(el => {
    if (job.kind !== el.dataset.scanStatus) { el.textContent = job.running ? 'Drugi sken je u toku' : 'Sken miruje'; return; }
    const phase = job.stop_requested && job.running ? 'Zaustavljanje...' : job.cancelled ? 'Zaustavljeno — sacuvano' : job.running ? job.phase : job.error ? job.error : 'Zavrseno';
    el.textContent = `${phase} · ${job.done || 0}/${job.total || 0}`;
  });
}

function renderLiveAudio(data) {
  const element = $('#liveaudio');
  if (!element) return;
  const report = data.audio_diagnostics || {}, items = report.accepted_top || [];
  element.className = 'card';
  element.innerHTML = `<p class="small mut">${data.current ? name(data.current) : ''}<br>Prag: ${report.threshold ? (report.threshold*100).toFixed(0)+'%' : '?'} · isti uslovi kao peti AUDIO RITAM predlog</p>` +
    (items.length ? items.map((t,i) => `<div class="live-audio-item"><div><b>${i+1}. ${name(t)}</b> <span class="badge acc">${t.score.toFixed(1)}%</span></div>
      <div class="small mut">Audio BPM ${t.audio.bpm} · razlika tempa ${t.tempo_delta.toFixed(1)}%</div>
      <div class="row"><button class="sm" data-act="copysong" data-id="${t.id}">Copy Song</button><button class="sm" data-act="dragsong" data-id="${t.id}">Prevuci</button><button class="sm" data-act="reveal" data-id="${t.id}">Explorer</button></div></div>`).join('') : `<p class="mut">${esc(data.audio_status || 'Nema rezultata koji prolaze audio uslove.')}</p>`) +
    (items.length && items.length<10 ? `<p class="small mut">Prolazi ${items.length} pesama; lista se ne dopunjava nasumicno.</p>` : '');
}

document.addEventListener('input', e => {
  if (e.target.id === 'gridQuery') renderGridResults(e.target.value);
});

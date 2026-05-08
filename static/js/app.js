'use strict';

const BASE_CATALOG = {
  "BAQ 💳":  ["Fea","Crypto","Option Credit","E-Carte Bleue","Option Internationale","Option Voyageur","Sobrio","Carte Visa classique","Carte Premier","Carte Infinite","Forfait retrait DAB illimites","Mon compte en bref"],
  "PAQ ⚖️":  ["Mon assurance Mobile","Certi Compte","Certi Epargne","Protection Juridique"],
  "PREV 🌳": ["Complementaire sante","Genea","AAV","GOB","Garantie de Salaire"],
  "ERS 💰":  ["ERS"],
  "DECLIC ⚡":     ["DECLIC"],
  "CCO 🤑":  ["CCO"],
  "IARD 🚗": ["MRA","MRH"],
  "PRO 💼":  ["PJ PRO"],
  "CREDIT 💲":["Alterna"]
};

const NOTE_CONTENT = `🎯 Objectif du logiciel

Ce logiciel a été conçu avec une ambition claire :
optimiser l'efficacité opérationnelle au CRC et améliorer la performance individuelle.

Après plusieurs analyses terrain et une recherche approfondie des meilleures méthodes d'organisation, cet outil a été développé pour répondre à trois enjeux majeurs :

  • Structurer le suivi d'activité
  • Gagner en productivité au quotidien
  • Améliorer la performance commerciale de manière mesurable

Il s'agit d'un outil pratique, pensé par un Conseiller CRC, pour des Conseillers CRC.

🚀 Philosophie

La performance n'est pas le fruit du hasard.
Elle repose sur :

  • Une organisation rigoureuse
  • Une vision claire des priorités
  • Un suivi précis des actions

Ce logiciel est un levier pour transformer ces principes en résultats concrets.

💡 Amélioration continue

Votre retour est essentiel.
Si vous avez des suggestions, des idées ou des retours d'expérience, n'hésitez pas à les partager.

🏆 Esprit d'équipe

Travaillez avec exigence. Suivez vos indicateurs. Faites la différence.

Bon courage à toi, je sais que tu gères ;-).

Luka Augustin`;

// ── State ─────────────────────────────────────────────────
let state = { version:2, sales:[], rebonds:[], currentClient:null, catalogCustom:{}, catalogOverrides:{}, catalogDeleted:{}, sgAgenda:0 };
let undoStack = [];  // [{type:'sale'|'rebond', item, idx?}]
let sessionGoal = parseInt(localStorage.getItem('t850_goal') || '0') || 0;
const sessionStart = Date.now();

// ── DOM helpers ───────────────────────────────────────────
function $(id) { return document.getElementById(id); }
function escHtml(s) { return (s||'').toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function pad(n) { return (n<10?'0':'')+n; }
function dtNow() {
  const d = new Date();
  return { iso: d.toISOString(), display: `${pad(d.getDate())}/${pad(d.getMonth()+1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}` };
}
function deepCopy(x) { return JSON.parse(JSON.stringify(x)); }

// ── Catalog ───────────────────────────────────────────────
function getCatalog() {
  const base = deepCopy(BASE_CATALOG);
  const { catalogCustom:cust={}, catalogDeleted:del={}, catalogOverrides:ov={} } = state;
  Object.keys(base).forEach(cat => {
    const delList = (del[cat]||[]).map(x => x.toLowerCase());
    base[cat] = base[cat].filter(p => !delList.includes(p.toLowerCase()));
    const o = ov[cat]||{};
    base[cat] = base[cat].map(p => o[p]||p);
  });
  Object.keys(cust).forEach(cat => {
    if (!base[cat]) base[cat] = [];
    (cust[cat]||[]).forEach(p => { if (!base[cat].includes(p)) base[cat].push(p); });
  });
  return base;
}

// ── Save status indicator ─────────────────────────────────
function setSaveStatus(status) {
  const dot = $('saveDot'), lbl = $('saveLabel');
  if (!dot) return;
  dot.className = 'save-dot ' + status;
  lbl.textContent = status === 'pending' ? 'Sauvegarde…' : status === 'error' ? 'Erreur' : 'Sauvegardé';
}

let saveTimer;
function scheduleSave() {
  setSaveStatus('pending');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try {
      const res = await fetch('/api/save', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(state) });
      if (!res.ok) throw new Error('HTTP '+res.status);
      setSaveStatus('saved');
    } catch(e) {
      setSaveStatus('error');
      console.warn('[T850] Save error:', e);
    }
  }, 400);
}

// ── Toast ─────────────────────────────────────────────────
let toastTimer;
function showToast(m) {
  const t = $('toast');
  t.textContent = m;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
}

// ── Modal helpers ─────────────────────────────────────────
function openModal(id)  { $(id).classList.add('show'); }
function closeModal(id) { $(id).classList.remove('show'); }
document.querySelectorAll('.modal-overlay').forEach(m => {
  m.addEventListener('click', e => { if (e.target === m) m.classList.remove('show'); });
});

// ── Theme ─────────────────────────────────────────────────
function applyTheme(dark) {
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  $('iconSun').style.display  = dark ? 'none' : '';
  $('iconMoon').style.display = dark ? '' : 'none';
  localStorage.setItem('t850_theme', dark ? 'dark' : 'light');
}
(function initTheme() {
  const saved = localStorage.getItem('t850_theme');
  applyTheme(saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches));
})();
$('themeToggle').addEventListener('click', () => {
  applyTheme(document.documentElement.getAttribute('data-theme') !== 'dark');
});

// ── Splash ────────────────────────────────────────────────
setTimeout(() => {
  const s = $('splash');
  if (s) { s.classList.add('hide'); setTimeout(() => s.remove(), 800); }
}, 2200);

// ── Undo ──────────────────────────────────────────────────
function pushUndo(type, item) {
  undoStack.push({ type, item: deepCopy(item) });
  if (undoStack.length > 20) undoStack.shift();
  $('undoBtn').disabled = false;
}
function doUndo() {
  if (!undoStack.length) return;
  const { type, item } = undoStack.pop();
  if (type === 'sale')   state.sales.splice(state.sales.indexOf(item) >= 0 ? state.sales.lastIndexOf(item) : state.sales.length, 0, item);
  if (type === 'sale_delete') state.sales.push(item);
  if (type === 'rebond') state.rebonds.splice(state.rebonds.length, 0, item);
  if (!undoStack.length) $('undoBtn').disabled = true;
  scheduleSave(); render();
  showToast('Action annulée ↩');
}
$('undoBtn').addEventListener('click', doUndo);

// ── Goal ──────────────────────────────────────────────────
$('goalSetBtn').addEventListener('click', () => { $('goalInput').value = sessionGoal || ''; openModal('goalModal'); });
$('cancelGoal').addEventListener('click', () => closeModal('goalModal'));
$('clearGoal').addEventListener('click', () => { sessionGoal = 0; localStorage.removeItem('t850_goal'); render(); closeModal('goalModal'); });
$('saveGoal').addEventListener('click', () => {
  const v = parseInt($('goalInput').value);
  if (!isNaN(v) && v > 0) { sessionGoal = v; localStorage.setItem('t850_goal', v); render(); closeModal('goalModal'); showToast('Objectif : ' + v + ' ventes'); }
});

// ── Selects helper ────────────────────────────────────────
function initSelects(catSel, prodSel, curCat='', curProd='') {
  const CAT = getCatalog();
  catSel.innerHTML = '<option value="">— Catégorie —</option>';
  Object.keys(CAT).forEach(c => {
    const o = document.createElement('option');
    o.value = c; o.textContent = c;
    if (c === curCat) o.selected = true;
    catSel.appendChild(o);
  });
  prodSel.innerHTML = '<option value="">— Produit —</option>';
  prodSel.disabled = !curCat;
  if (curCat) (CAT[curCat]||[]).forEach(p => {
    const o = document.createElement('option'); o.value = p; o.textContent = p;
    if (p === curProd) o.selected = true;
    prodSel.appendChild(o);
  });
  catSel.onchange = () => {
    const c = catSel.value;
    prodSel.innerHTML = '<option value="">— Produit —</option>';
    if (!c) { prodSel.disabled = true; return; }
    prodSel.disabled = false;
    (CAT[c]||[]).forEach(p => { const o = document.createElement('option'); o.value = p; o.textContent = p; prodSel.appendChild(o); });
  };
}

// ── Week bounds ───────────────────────────────────────────
function weekBounds() {
  const n = new Date(), day = (n.getDay()+6)%7, m = new Date(n);
  m.setDate(n.getDate()-day); m.setHours(0,0,0,0);
  const s = new Date(m); s.setDate(m.getDate()+6); s.setHours(23,59,59,999);
  return { monday:m, sunday:s };
}
function weekSales()   { const {monday,sunday} = weekBounds(); return state.sales.filter(s => { const d=new Date(s.dt); return d>=monday&&d<=sunday; }); }
function weekRebonds() { const {monday,sunday} = weekBounds(); return state.rebonds.filter(r => { const d=new Date(r.dt); return d>=monday&&d<=sunday; }); }

// ── Session timer ─────────────────────────────────────────
setInterval(() => {
  const elapsed = Math.floor((Date.now() - sessionStart) / 1000);
  const h = Math.floor(elapsed/3600), m = Math.floor((elapsed%3600)/60), s = elapsed%60;
  const timerEl = $('sessionTimer'); if (timerEl) timerEl.textContent = `${pad(h)}:${pad(m)}:${pad(s)}`;
  const hours = elapsed / 3600;
  const rateEl = $('sessionRate'); if (rateEl) rateEl.textContent = hours > 0.01 ? (state.sales.length / hours).toFixed(1) : '—';
}, 1000);

// ── Milestones & confetti ─────────────────────────────────
const MILESTONES = [5,10,15,20,25,30,40,50];
let lastMilestone = 0;
function checkMilestone(count) {
  const m = MILESTONES.filter(x => x <= count && x > lastMilestone).pop();
  if (m) { lastMilestone = m; launchConfetti(); showToast(`🎉 ${m} ventes !`); }
}
function launchConfetti() {
  const canvas = $('confettiCanvas');
  if (!canvas) return;
  canvas.width = window.innerWidth; canvas.height = window.innerHeight;
  const ctx = canvas.getContext('2d');
  const pieces = Array.from({length:80}, () => ({
    x: Math.random()*canvas.width, y: -10,
    vx: (Math.random()-0.5)*6, vy: Math.random()*4+2,
    r: Math.random()*6+3,
    color: ['#0052FF','#00D4FF','#00875A','#FFB800','#E11D48'][Math.floor(Math.random()*5)],
    rot: Math.random()*360, rotV: (Math.random()-0.5)*8,
  }));
  let frame = 0;
  function draw() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    pieces.forEach(p => {
      p.x += p.vx; p.y += p.vy; p.vy += 0.1; p.rot += p.rotV;
      ctx.save(); ctx.translate(p.x,p.y); ctx.rotate(p.rot*Math.PI/180);
      ctx.fillStyle = p.color; ctx.fillRect(-p.r/2,-p.r/2,p.r,p.r);
      ctx.restore();
    });
    frame++;
    if (frame < 120) requestAnimationFrame(draw);
    else ctx.clearRect(0,0,canvas.width,canvas.height);
  }
  draw();
}

// ── Render ────────────────────────────────────────────────
let searchQuery = '';
function render() {
  const CAT = getCatalog();
  const totalSales = state.sales.length, totalRebonds = state.rebonds.length;

  $('kpiTotal').textContent   = totalSales;
  $('kpiRebonds').textContent = totalRebonds;
  const convRate = totalRebonds ? Math.round(totalSales/(totalSales+totalRebonds)*100) : (totalSales ? 100 : 0);
  $('kpiConv').textContent = (totalSales+totalRebonds) ? `${convRate}%` : '—';

  // Multi VAD
  const byClient = {};
  state.sales.forEach(s => {
    const k = s.client&&(s.client.nom||s.client.prenom) ? `${s.client.nom}|${s.client.prenom}|${s.client.naissance||''}`.toLowerCase() : null;
    if (k) byClient[k] = (byClient[k]||0)+1;
  });
  const multi = Object.values(byClient).filter(c=>c>=2).length;
  $('kpiMulti').textContent = multi || '—';
  $('kpiAgenda').textContent = state.sgAgenda||0;

  // Goal
  if (sessionGoal > 0) {
    $('goalBarWrap').style.display = '';
    const pct = Math.min(100, Math.round(totalSales/sessionGoal*100));
    $('goalBarFill').style.width = pct+'%';
  } else {
    $('goalBarWrap').style.display = 'none';
  }

  // Client
  if (state.currentClient) {
    const c = state.currentClient;
    const ini = ((c.prenom||' ')[0]+(c.nom||' ')[0]).toUpperCase().trim();
    $('clientAvatar').textContent = ini||'C';
    $('clientName').textContent   = `${c.prenom||''} ${c.nom||''}`.trim()||'—';
    $('clientDob').textContent    = c.naissance||'—';
  } else {
    $('clientAvatar').textContent = '?';
    $('clientName').textContent   = 'Aucun client';
    $('clientDob').textContent    = '—';
  }

  // Cat stats sidebar
  const catSales = {};
  state.sales.forEach(s => { catSales[s.category] = (catSales[s.category]||0)+1; });
  const maxCat = Math.max(1, ...Object.values(catSales), 1);
  $('catStatList').innerHTML = Object.keys(CAT).map(c => {
    const v = catSales[c]||0, pct = Math.round(v/maxCat*100);
    return `<div class="cat-stat-row"><span class="cat-stat-name">${escHtml(c)}</span><div class="cat-stat-bar-wrap"><div class="cat-stat-bar" style="width:${pct}%"></div></div><span class="cat-stat-count">${v}</span></div>`;
  }).join('');

  // Sales list with search
  const q = searchQuery.trim().toLowerCase();
  const filtered = q ? state.sales.filter(s =>
    (s.product||'').toLowerCase().includes(q) ||
    (s.category||'').toLowerCase().includes(q) ||
    ((s.client&&s.client.nom)||'').toLowerCase().includes(q) ||
    ((s.client&&s.client.prenom)||'').toLowerCase().includes(q)
  ) : state.sales;

  $('historyBadge').textContent = totalSales;
  if (q) {
    $('filteredBadge').style.display = '';
    $('filteredBadge').textContent = filtered.length + ' filtré'+(filtered.length>1?'s':'');
  } else {
    $('filteredBadge').style.display = 'none';
  }

  if (!filtered.length) {
    $('salesList').innerHTML = `<div class="sale-empty">${q ? 'Aucun résultat.' : 'Aucune vente pour l\'instant.'}</div>`;
  } else {
    $('salesList').innerHTML = filtered.slice().reverse().map((s, i) => {
      const realIdx = state.sales.lastIndexOf(s);
      const hl = q ? ' sale-highlight' : '';
      return `<div class="sale-item${hl}" data-edit-sale="${realIdx}" style="cursor:pointer"><div class="sale-dot"></div><div class="sale-info"><div class="sale-product">${escHtml(s.product)}</div><div class="sale-meta">${escHtml(s.category)} • ${escHtml(s.dt_display||'')}</div></div></div>`;
    }).join('');
  }

  // Product table
  const prodV={}, prodR={};
  state.sales.forEach(s => { prodV[s.product]=(prodV[s.product]||0)+1; });
  state.rebonds.forEach(r => { prodR[r.product]=(prodR[r.product]||0)+1; });
  const allProds = new Set([...Object.keys(prodV),...Object.keys(prodR)]);
  const rows = [...allProds].map(p => {
    const v=prodV[p]||0, r=prodR[p]||0, t=v+r;
    const conv = t ? Math.round(v/t*100) : 0;
    let cls='conv-none', lbl='—';
    if (t) { lbl=`${conv}%`; if(conv>=70)cls='conv-high'; else if(conv>=40)cls='conv-mid'; else cls='conv-low'; }
    return {p,v,r,conv,cls,lbl};
  }).sort((a,b) => b.v-a.v||b.r-a.r);
  $('prodTableBody').innerHTML = rows.length
    ? rows.map(x => `<tr><td>${escHtml(x.p)}</td><td>${x.v}</td><td>${x.r}</td><td><span class="conv-badge ${x.cls}">${x.lbl}</span></td></tr>`).join('')
    : `<tr><td colspan="4" style="text-align:center;padding:20px 0;font-style:italic;color:var(--ink-muted)">Aucune donnée.</td></tr>`;

  // Time stats
  const BINS = [['8h-10h',8,10],['10h-12h',10,12],['12h-14h',12,14],['14h-16h',14,16],['16h-18h',16,18],['18h-20h',18,20]];
  const binCounts = {}; BINS.forEach(([l]) => binCounts[l]=0); let other=0;
  state.sales.forEach(s => {
    const h = new Date(s.dt).getHours(); let placed=false;
    for (const [l,start,end] of BINS) { if(h>=start&&h<end){binCounts[l]++;placed=true;break;} }
    if (!placed) other++;
  });
  const maxBin = Math.max(1, ...Object.values(binCounts), other);
  $('timeStatsList').innerHTML = [...BINS.map(([l])=>({l,v:binCounts[l]})),{l:'Autres',v:other}].map(({l,v}) => {
    const pct = Math.round(v/maxBin*100);
    return `<div class="bar-row"><span class="bar-label">${l}</span><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="bar-num">${v}</span></div>`;
  }).join('');

  // Cat detail
  $('catDetailList').innerHTML = Object.keys(CAT).map(cat => {
    const v = catSales[cat]||0, pct = totalSales ? Math.round(v/totalSales*100) : 0;
    return `<div class="bar-row"><span class="bar-label">${escHtml(cat)}</span><div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="bar-num">${v}</span></div>`;
  }).join('');
}

// ── Search ────────────────────────────────────────────────
$('searchInput').addEventListener('input', e => { searchQuery = e.target.value; render(); });

// ── Sale add/edit ─────────────────────────────────────────
$('salesList').addEventListener('click', e => {
  const btn = e.target.closest('[data-edit-sale]'); if (!btn) return;
  openSaleEditor(parseInt(btn.dataset.editSale, 10));
});
let editIdx = -1;
function openSaleEditor(idx) {
  const s = state.sales[idx]; if (!s) return; editIdx = idx;
  $('saleEditInfo').textContent = `Vente du ${s.dt_display||s.dt||''}`;
  initSelects($('editCategorySelect'),$('editProductSelect'),s.category,s.product);
  openModal('saleEditModal');
}
$('cancelSaleEdit').addEventListener('click', () => { editIdx=-1; closeModal('saleEditModal'); });
$('saveSaleEdit').addEventListener('click', () => {
  if (editIdx<0) return;
  const cat=$('editCategorySelect').value, prod=$('editProductSelect').value;
  if (!cat||!prod) { showToast('Choisis catégorie + produit ⚠️'); return; }
  state.sales[editIdx].category=cat; state.sales[editIdx].product=prod;
  scheduleSave(); render(); closeModal('saleEditModal'); editIdx=-1; showToast('Vente modifiée ✅');
});
$('deleteSaleBtn').addEventListener('click', () => {
  if (editIdx<0) return;
  const removed = state.sales.splice(editIdx,1)[0];
  pushUndo('sale_delete', removed);
  editIdx=-1; scheduleSave(); render(); closeModal('saleEditModal'); showToast('Vente supprimée ✅');
});

$('addBtn').addEventListener('click', () => { initSelects($('categorySelect'),$('productSelect')); openModal('saleModal'); });
$('cancelSale').addEventListener('click', () => closeModal('saleModal'));
$('confirmSale').addEventListener('click', () => {
  const cat=$('categorySelect').value, prod=$('productSelect').value; if(!cat||!prod) return;
  const {iso,display} = dtNow();
  const sale = {dt:iso,dt_display:display,category:cat,product:prod,client:state.currentClient?{...state.currentClient}:{nom:'',prenom:'',naissance:''}};
  state.sales.push(sale);
  if (state.sales.length > 3000) state.sales.shift();
  pushUndo('sale', sale);
  scheduleSave(); render(); closeModal('saleModal'); showToast('Vente ajoutée ✅');
  checkMilestone(state.sales.length);
});

$('rebondBtn').addEventListener('click', () => { initSelects($('rebondCategorySelect'),$('rebondProductSelect')); openModal('rebondModal'); });
$('cancelRebond').addEventListener('click', () => closeModal('rebondModal'));
$('confirmRebond').addEventListener('click', () => {
  const cat=$('rebondCategorySelect').value, prod=$('rebondProductSelect').value; if(!cat||!prod) return;
  const {iso,display} = dtNow();
  const rebond = {dt:iso,dt_display:display,category:cat,product:prod};
  state.rebonds.push(rebond);
  if (state.rebonds.length > 5000) state.rebonds.shift();
  pushUndo('rebond', rebond);
  scheduleSave(); render(); closeModal('rebondModal'); showToast('Rebond ajouté ✅');
});

// ── Client ────────────────────────────────────────────────
$('clientBtn').addEventListener('click', () => {
  $('nomInput').value      = state.currentClient?.nom||'';
  $('prenomInput').value   = state.currentClient?.prenom||'';
  $('naissanceInput').value= state.currentClient?.naissance||'';
  openModal('clientModal');
});
$('cancelClient').addEventListener('click', () => closeModal('clientModal'));
$('saveClient').addEventListener('click', () => {
  const nom=($('nomInput').value||'').trim(), prenom=($('prenomInput').value||'').trim(), naissance=($('naissanceInput').value||'').trim();
  state.currentClient = (!nom&&!prenom&&!naissance) ? null : {nom,prenom,naissance};
  scheduleSave(); render(); closeModal('clientModal'); showToast('Client enregistré ✅');
});

// ── Catalogue ─────────────────────────────────────────────
$('catalogBtn').addEventListener('click', () => { initCatalogModal(); openModal('catalogModal'); });
$('closeCatalog').addEventListener('click', () => closeModal('catalogModal'));
function initCatalogModal() {
  const CAT = getCatalog(), sel = $('catalogCategorySelect');
  sel.innerHTML = '';
  Object.keys(CAT).forEach(cat => { const o=document.createElement('option'); o.value=cat; o.textContent=cat; sel.appendChild(o); });
  sel.value = Object.keys(CAT)[0]||''; renderCatalogProducts();
}
$('catalogCategorySelect').addEventListener('change', renderCatalogProducts);
function isDeleted(cat,prod) { return (state.catalogDeleted?.[cat]||[]).map(x=>x.toLowerCase()).includes(prod.toLowerCase()); }
function renderCatalogProducts() {
  const CAT=getCatalog(), cat=$('catalogCategorySelect').value;
  const prods=(CAT[cat]||[]).slice().sort((a,b)=>a.localeCompare(b,'fr'));
  const baseAll=(BASE_CATALOG[cat]||[]).slice().sort((a,b)=>a.localeCompare(b,'fr'));
  const deletedBase=baseAll.filter(p=>isDeleted(cat,p)), el=$('catalogProductsList');
  if (!prods.length&&!deletedBase.length) { el.innerHTML=`<div style="font-size:13px;color:var(--ink-muted);padding:12px 0">Aucun produit.</div>`; return; }
  el.innerHTML = [
    ...prods.map(p => `<div class="sale-item"><div class="sale-dot"></div><div class="sale-info"><div class="sale-product">${escHtml(p)}</div></div><div style="display:flex;gap:6px"><button class="btn btn-secondary btn-sm" data-edit="1" data-cat="${escHtml(cat)}" data-prod="${escHtml(p)}">Modifier</button><button class="btn btn-danger-ghost btn-sm" data-del="1" data-cat="${escHtml(cat)}" data-prod="${escHtml(p)}">Suppr.</button></div></div>`),
    deletedBase.length ? `<div style="margin:10px 0 4px;font-size:10px;font-weight:600;color:var(--ink-muted);letter-spacing:1px;text-transform:uppercase">Supprimés</div>` : '',
    ...deletedBase.map(p => `<div class="sale-item" style="opacity:.5"><div class="sale-dot" style="background:var(--ink-muted)"></div><div class="sale-info"><div class="sale-product" style="font-style:italic">${escHtml(p)}</div></div><button class="btn btn-secondary btn-sm" data-restore="1" data-cat="${escHtml(cat)}" data-prod="${escHtml(p)}">Restaurer</button></div>`)
  ].join('');
}
$('catalogProductsList').addEventListener('click', e => {
  const btn = e.target.closest('[data-cat]'); if (!btn) return;
  const cat=btn.dataset.cat, prod=btn.dataset.prod;
  if (btn.dataset.edit) {
    const v=(prompt('Modifier le produit :',prod)||'').trim(); if(!v) return;
    const exists=(getCatalog()[cat]||[]).some(x=>x.toLowerCase()===v.toLowerCase());
    if (exists&&v.toLowerCase()!==prod.toLowerCase()) { showToast('Ce nom existe déjà ⚠️'); return; }
    state.catalogOverrides??={}; state.catalogOverrides[cat]??={}; state.catalogOverrides[cat][prod]=v;
    scheduleSave(); renderCatalogProducts(); render(); showToast('Produit modifié ✅');
  } else if (btn.dataset.del) {
    if (!confirm(`Supprimer "${prod}" ?`)) return;
    state.catalogCustom[cat]=(state.catalogCustom[cat]||[]).filter(x=>x!==prod);
    state.catalogDeleted??={}; state.catalogDeleted[cat]??=[];
    if (!state.catalogDeleted[cat].includes(prod)) state.catalogDeleted[cat].push(prod);
    scheduleSave(); renderCatalogProducts(); render(); showToast('Supprimé ✅');
  } else if (btn.dataset.restore) {
    state.catalogDeleted??={}; state.catalogDeleted[cat]=(state.catalogDeleted[cat]||[]).filter(x=>x!==prod);
    scheduleSave(); renderCatalogProducts(); render(); showToast('Restauré ✅');
  }
});
$('addProductBtn').addEventListener('click', () => {
  const cat=$('catalogCategorySelect').value, prod=($('newProductInput').value||'').trim();
  if (!cat||!prod) return;
  if ((getCatalog()[cat]||[]).some(x=>x.toLowerCase()===prod.toLowerCase())) { showToast('Ce produit existe déjà ⚠️'); return; }
  state.catalogCustom??={}; state.catalogCustom[cat]??=[]; state.catalogCustom[cat].push(prod);
  $('newProductInput').value='';
  scheduleSave(); renderCatalogProducts(); render(); showToast('Produit ajouté ✅');
});

// ── SG Agenda ─────────────────────────────────────────────
function getSGA() { return parseInt(state.sgAgenda)||0; }
function setSGA(n) { state.sgAgenda=Math.max(0,parseInt(n)||0); scheduleSave(); render(); }
$('agendaPlus').addEventListener('click',  e => { e.stopPropagation(); setSGA(getSGA()+1); showToast('SG Agenda : '+getSGA()); });
$('agendaMinus').addEventListener('click', e => { e.stopPropagation(); if(getSGA()>0)setSGA(getSGA()-1); });
$('kpiAgenda').addEventListener('dblclick', () => {
  const v=prompt('SG Agenda :',getSGA()); if(v===null)return;
  const n=parseInt(v); if(!isNaN(n)&&n>=0){setSGA(n);showToast('SG Agenda ✅');}
});

// ── Reset ─────────────────────────────────────────────────
$('resetBtn').addEventListener('click', () => openModal('resetModal'));
$('cancelReset').addEventListener('click', () => closeModal('resetModal'));
$('confirmReset').addEventListener('click', async () => {
  closeModal('resetModal');
  try {
    const res = await fetch('/api/reset_sales',{method:'POST'});
    const data = await res.json();
    if (data.ok&&data.state) { Object.assign(state,data.state); if(!Array.isArray(state.rebonds))state.rebonds=[]; undoStack=[]; $('undoBtn').disabled=true; render(); showToast('Réinitialisé ✅'); }
  } catch(e) { showToast('Erreur reset ❌'); }
});

// ── PDF export ────────────────────────────────────────────
async function exportPdf(payload, btn) {
  if (btn.disabled) return;
  const orig=btn.innerHTML; btn.disabled=true; btn.textContent='Génération…';
  try {
    const res = await fetch('/export_pdf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data = await res.json();
    if (data.ok) showToast('PDF exporté ✅');
    else { showToast('Erreur PDF ❌'); console.error('[T850] PDF error:',data.error); }
  } catch(e) { showToast('Erreur PDF ❌'); }
  finally { btn.disabled=false; btn.innerHTML=orig; }
}
$('pdfBtn').addEventListener('click', function() {
  exportPdf({title:'RECAP VENTES',general:state.sales.length,sales:state.sales,rebonds:state.rebonds,sgAgenda:getSGA()}, this);
});
$('pdfWeekBtn').addEventListener('click', function() {
  const ws=weekSales(), wr=weekRebonds(), {monday,sunday}=weekBounds();
  const lbl=`${pad(monday.getDate())}/${pad(monday.getMonth()+1)}/${monday.getFullYear()} → ${pad(sunday.getDate())}/${pad(sunday.getMonth()+1)}/${sunday.getFullYear()}`;
  exportPdf({title:`RECAP SEMAINE (${lbl})`,general:ws.length,sales:ws,rebonds:wr,sgAgenda:getSGA()}, this);
});

// ── CSV export ────────────────────────────────────────────
$('csvBtn').addEventListener('click', async function() {
  const orig = this.innerHTML; this.disabled=true; this.textContent='Export…';
  try {
    const res = await fetch('/export_csv',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sales:state.sales,rebonds:state.rebonds})});
    if (!res.ok) throw new Error();
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download=`t850_${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
    showToast('CSV téléchargé ✅');
  } catch(e) { showToast('Erreur CSV ❌'); }
  finally { this.disabled=false; this.innerHTML=orig; }
});

// ── Keyboard shortcuts ────────────────────────────────────
document.addEventListener('keydown', e => {
  if (document.querySelector('.modal-overlay.show')) {
    if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show'));
    return;
  }
  const tag = document.activeElement?.tagName?.toLowerCase();
  if (tag === 'input' || tag === 'select' || tag === 'textarea') return;

  if (e.key === 'v' || e.key === 'V') { e.preventDefault(); initSelects($('categorySelect'),$('productSelect')); openModal('saleModal'); }
  if (e.key === 'r' || e.key === 'R') { e.preventDefault(); initSelects($('rebondCategorySelect'),$('rebondProductSelect')); openModal('rebondModal'); }
  if (e.key === 'c' || e.key === 'C') { e.preventDefault(); $('nomInput').value=state.currentClient?.nom||''; $('prenomInput').value=state.currentClient?.prenom||''; $('naissanceInput').value=state.currentClient?.naissance||''; openModal('clientModal'); }
  if ((e.key === 'z' || e.key === 'Z') && (e.ctrlKey||e.metaKey)) { e.preventDefault(); doUndo(); }
  if ((e.key === 'p' || e.key === 'P') && (e.ctrlKey||e.metaKey)) { e.preventDefault(); $('pdfBtn').click(); }
  if (e.key === '?') { openModal('shortcutsModal'); }
});

$('helpBtn').addEventListener('click', () => openModal('shortcutsModal'));
$('closeShortcuts').addEventListener('click', () => closeModal('shortcutsModal'));
$('helpBtnSidebar').addEventListener('click', () => { $('noteText').textContent = NOTE_CONTENT; openModal('noteModal'); });
$('closeNote').addEventListener('click', () => closeModal('noteModal'));

// ── Secret admin ──────────────────────────────────────────
let secretClicks=0, secretTimer;
$('logoClickZone').addEventListener('click', async () => {
  secretClicks++; clearTimeout(secretTimer);
  secretTimer = setTimeout(() => { secretClicks=0; }, 1200);
  if (secretClicks >= 7) {
    secretClicks=0;
    const code = prompt('Admin — code ? (Reset total)');
    if (!code||code.trim().toUpperCase()!=='LUKA') { showToast('Code incorrect ❌'); return; }
    if (!confirm('RESET TOTAL : ventes + rebonds + client + catalogue. Continuer ?')) return;
    try {
      const res=await fetch('/api/reset_all',{method:'POST'});
      const data=await res.json();
      if (data.ok&&data.state) { Object.assign(state,data.state); undoStack=[]; $('undoBtn').disabled=true; render(); showToast('Reset total effectué ✅'); }
    } catch(e) { showToast('Erreur ❌'); }
  }
});

// ── Boot ──────────────────────────────────────────────────
(async function boot() {
  try {
    const res = await fetch('/api/load');
    const data = await res.json();
    if (data.ok && data.state) {
      Object.assign(state, data.state);
      if (!Array.isArray(state.rebonds)) state.rebonds = [];
      lastMilestone = MILESTONES.filter(m => m <= state.sales.length).pop() || 0;
    }
  } catch(e) {}
  render();
})();

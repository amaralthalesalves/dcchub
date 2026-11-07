/* DCCHub — front-end sem dependências (SPA com rotas por hash). */
'use strict';

// ---------------------------------------------------------------- utilidades
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = $('#main');
const DAYS = ['', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
const STATUS_LABEL = { ativo: 'Ativo', voluntario: 'Voluntário', emerito: 'Emérito', aposentado: 'Aposentado', externo: 'Externo', substituto: 'Substituto' };

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// links vindos de dados raspados: só http(s)/mailto viram href (javascript:, data: etc. viram '#'); a CSP já bloqueia, isto é a 2ª barreira
const safeHref = (u) => (/^(https?:\/\/|mailto:)/i.test(String(u ?? '').trim()) ? esc(String(u).trim()) : '#');
const fold = (v) => String(v ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const fmt = (n, d = 1) => (n == null ? '—' : Number(n).toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d }));
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const store = {
  get(key, fallback) { try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch (_) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* sem armazenamento */ } },
  del(key) { try { localStorage.removeItem(key); } catch (_) { /* idem */ } },
};

// avaliações guardam só o dia (meia-noite de Brasília): "hoje", "ontem", "há 3 dias"…
function dayAgo(ts) {
  const BRT = 3 * 3600, today = Math.floor((Date.now() / 1000 - BRT) / 86400);
  const days = today - Math.floor((ts - BRT) / 86400);
  if (days <= 0) return 'hoje';
  if (days === 1) return 'ontem';
  return timeAgo(ts);
}

function timeAgo(ts) {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  const steps = [[60, 'segundo'], [3600, 'minuto', 60], [86400, 'hora', 3600], [2592000, 'dia', 86400], [31536000, 'mês', 2592000], [Infinity, 'ano', 31536000]];
  for (const [limit, unit, div = 1] of steps) {
    if (s < limit) {
      const n = Math.floor(s / div);
      const units = unit === 'mês' ? (n === 1 ? 'mês' : 'meses') : n === 1 ? unit : `${unit}s`;
      return `há ${n} ${units}`;
    }
  }
  return '';
}

async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const opts = { method, headers: { Accept: 'application/json', ...headers }, credentials: 'same-origin' };
  if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch(path, opts); } catch (_) { throw Object.assign(new Error('Sem conexão com o servidor.'), { status: 0 }); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || 'Algo deu errado.'), { status: res.status, data });
  return data;
}

let toastTimer;
function toast(message, kind = 'ok') {
  const el = $('#toast');
  el.textContent = message;
  el.dataset.kind = kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

function scoreClass(v, neutral = false) {
  if (v == null) return 'none';
  if (neutral) return v >= 4 ? 'n-high' : v >= 2.6 ? 'n-mid' : 'n-low';
  return v >= 4 ? 'good' : v >= 3 ? 'ok' : 'bad';
}
// escalas neutras (dificuldade, carga): nome do ponto mais próximo da média — "3,8" sozinho parece nota boa
const level = (c, v) => (v == null || !c?.levels ? '' : c.levels[Math.min(4, Math.max(0, Math.round(v) - 1))]);
// com `crit`, a pílula é neutra quando o critério é e ganha o nome do nível no tooltip (tabelas não têm espaço para o texto)
const pill = (v, { neutral = false, title = '', crit = null } = {}) => {
  if (crit) { neutral = !!crit.neutral; title = title || (level(crit, v) ? `${crit.label}: ${level(crit, v).toLowerCase()}` : ''); }
  return `<span class="pill ${scoreClass(v, neutral)}" ${title ? `title="${esc(title)}"` : ''}>${v == null ? '—' : fmt(v)}</span>`;
};
const critByKey = (k) => state.boot.criteria.find((c) => c.key === k);

function stars(v) {
  const pct = v == null ? 0 : (v / 5) * 100;
  return `<span class="stars" role="img" aria-label="${v == null ? 'sem nota' : `${fmt(v)} de 5`}"><span class="stars-fill" style="width:${pct}%"></span></span>`;
}

function initials(name) {
  const parts = String(name).split(/\s+/).filter((w) => w.length > 2 || /^[A-ZÁÉÍÓÚ]/.test(w));
  return ((parts[0] || '')[0] || '') + ((parts[parts.length - 1] || '')[0] || '');
}
function avatar(p, size = 'md') {
  if (p && p.photo) return `<img class="avatar avatar-${size}" src="${esc(p.photo)}" alt="" loading="lazy" decoding="async">`;
  const hue = [...String(p?.name || '')].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return `<span class="avatar avatar-${size} avatar-initials" style="--h:${hue}" aria-hidden="true">${esc(initials(p?.name || '?'))}</span>`;
}

const fmtSlot = (s) => `${DAYS[s.day]} ${s.start}–${s.end}`;
function fmtSchedule(schedule) {
  if (!schedule || !schedule.length) return '<span class="muted">horário não informado</span>';
  // agrupa dias com o mesmo horário: "Ter/Qui 14:55–16:35"
  const groups = new Map();
  schedule.forEach((s) => { const k = `${s.start}–${s.end}`; groups.set(k, [...(groups.get(k) || []), DAYS[s.day]]); });
  return [...groups].map(([time, days]) => `<span class="slot">${days.join('/')} ${time}</span>`).join(' ');
}
const courseCode = (c) => (c.kind === 'topico' ? `Tópicos · ${c.codes.join('/')}` : c.codes.join(' / '));
const firstName = (name) => String(name).split(' ')[0];

// ---------------------------------------------------------------- estado
const state = {
  boot: null,
  prof: new Map(),
  course: new Map(),
  me: null,
};

async function loadBoot(force = false) {
  if (state.boot && !force) return state.boot;
  const boot = await api('/api/bootstrap');
  state.boot = boot;
  state.prof = new Map(boot.professors.map((p) => [p.slug, p]));
  state.course = new Map(boot.courses.map((c) => [c.id, c]));
  boot.courses.forEach((c) => (c.aliases || []).forEach((a) => state.course.set(a, c))); // códigos antigos -> versão atual
  boot.searchIndex = [
    ...boot.professors.map((p) => ({ type: 'prof', key: fold(p.name), label: p.name, sub: p.areas.slice(0, 2).join(' · ') || STATUS_LABEL[p.status], href: `#/professor/${p.slug}`, item: p, weight: p.status === 'ativo' || p.status === 'voluntario' ? 2 : 1 })),
    ...boot.courses.filter((c) => c.active).map((c) => ({ type: 'course', key: fold(`${c.name} ${c.codes.join(' ')}`), label: c.name, sub: courseCode(c), href: `#/disciplina/${encodeURIComponent(c.id)}`, item: c, weight: c.offeredNow ? 2 : 1 })),
  ];
  const card = $('#sidebar-card');
  if (card && boot.latestTerm) {
    const live = boot.courses.filter((c) => c.offeredNow).length;
    card.innerHTML = boot.offerPublished
      ? `<span class="small"><span class="live-dot" aria-hidden="true"></span>Semestre atual</span><b>Oferta ${esc(boot.latestTerm)}</b><span>${plural(live, 'disciplina ofertada', 'disciplinas ofertadas')}${boot.termSources?.[boot.latestTerm] === 'SIGAA' ? ' · fonte: SIGAA' : ''}</span><a href="#/oferta">Montar minha grade →</a>`
      : `<span class="small"><span class="live-dot pending" aria-hidden="true"></span>Semestre ${esc(boot.currentTerm)}</span><b>Oferta ainda não publicada</b><span>O DCC não publicou a oferta de ${esc(boot.currentTerm)}. Mostramos a última oficial (${esc(boot.latestTerm)}).</span><a href="#/sobre#atual">Como ajudar →</a>`;
    card.hidden = false;
  }
  const note = $('#footer-note');
  if (note) note.innerHTML = `Dados públicos do <a href="${safeHref(boot.sources.professors || 'https://dcc.ufmg.br/professores/')}" rel="noreferrer" target="_blank">site do DCC</a>, ofertas de ${esc(boot.terms[0] || '')} a ${esc(boot.latestTerm || '')}${boot.generatedAt ? ` · sincronizado em ${new Date(boot.generatedAt).toLocaleDateString('pt-BR')}` : ''}.`;
  return boot;
}

async function loadMe(force = false) {
  if (state.me && !force) return state.me;
  state.me = await api('/api/me').catch(() => ({ reviews: [] }));
  return state.me;
}

function search(query, limit = 8) {
  const q = fold(query).trim();
  if (!q) return [];
  const words = q.split(/\s+/);
  return state.boot.searchIndex
    .map((e) => {
      if (!words.every((w) => e.key.includes(w))) return null;
      let score = e.weight;
      if (e.key.startsWith(q)) score += 4;
      if (e.key.split(/\s+/).some((w) => w.startsWith(words[0]))) score += 2;
      if (e.type === 'course' && e.item.codes.some((c) => fold(c) === q)) score += 10;
      return { ...e, score };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score || a.label.localeCompare(b.label))
    .slice(0, limit);
}

// ---------------------------------------------------------------- busca global
function setupGlobalSearch() {
  const input = $('#global-search');
  const box = $('#global-results');
  let active = -1;
  let results = [];
  const layer = $('#search-layer');
  const close = () => { box.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
  // a busca abre por cima da página (botão da pílula ou "/") e fecha com Esc, clique fora ou ao escolher um resultado
  const openLayer = () => { layer.hidden = false; requestAnimationFrame(() => input.focus()); };
  const closeLayer = () => { input.value = ''; close(); layer.hidden = true; };
  const render = () => {
    if (!results.length) {
      box.innerHTML = input.value.trim() ? '<p class="search-empty">Nada encontrado. Tente o código (ex.: DCC206) ou parte do nome.</p>' : '';
      box.hidden = !input.value.trim();
      return;
    }
    box.innerHTML = results.map((r, i) => `
      <a role="option" id="sr-${i}" class="search-item ${i === active ? 'active' : ''}" href="${r.href}" aria-selected="${i === active}">
        ${r.type === 'prof' ? avatar(r.item, 'sm') : '<span class="search-kind" aria-hidden="true"><svg class="ico"><use href="#i-book"/></svg></span>'}
        <span><b>${esc(r.label)}</b><small>${esc(r.sub)}</small></span>
        ${r.item.stats ? pill(r.item.stats.overall) : ''}
      </a>`).join('');
    box.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    if (active >= 0) input.setAttribute('aria-activedescendant', `sr-${active}`); else input.removeAttribute('aria-activedescendant');
  };
  input.addEventListener('input', () => { if (!state.boot) return; results = search(input.value); active = results.length ? 0 : -1; render(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(results.length - 1, active + 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(0, active - 1); render(); }
    else if (e.key === 'Enter') {
      const pick = results[active] || results[0];
      if (pick) { location.hash = pick.href; closeLayer(); }
      else if (input.value.trim()) { location.hash = `#/professores?q=${encodeURIComponent(input.value.trim())}`; closeLayer(); }
    } else if (e.key === 'Escape') { closeLayer(); $('#search-open')?.focus(); }
  });
  input.addEventListener('focus', () => { if (input.value.trim()) render(); });
  box.addEventListener('click', () => closeLayer());
  $('#search-open').addEventListener('click', openLayer);
  layer.addEventListener('click', (e) => { if (e.target === layer) closeLayer(); });
  window.addEventListener('hashchange', () => { if (!layer.hidden) closeLayer(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.body.classList.contains('gated')) { e.preventDefault(); openLayer(); }
  });
}

// ---------------------------------------------------------------- roteador
const routes = [
  [/^$/, viewHome],
  [/^professores$/, viewProfessors],
  [/^professor\/([^/]+)$/, viewProfessor],
  [/^disciplinas$/, viewCourses],
  [/^disciplina\/([^/]+)$/, viewCourse],
  [/^oferta$/, viewOffer],
  [/^avaliar$/, viewReviewForm],
  [/^minhas$/, viewMine],
  [/^sobre$/, viewAbout],
  [/^materiais$/, viewProvas],
  [/^oportunidades$/, viewOportunidades],
  [/^provas$/, () => location.replace('#/materiais')], // endereço antigo
  [/^moderacao$/, viewModeration],
  [/^feedback$/, viewFeedback],
];

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, qs = ''] = raw.split('?');
  const [cleanPath, anchor] = path.split('#');
  return { path: decodeURIComponent(cleanPath.replace(/\/$/, '')), params: new URLSearchParams(qs), anchor };
}

function setQuery(params, { replace = true } = {}) {
  const { path } = parseHash();
  const qs = params.toString();
  const url = `#/${path}${qs ? `?${qs}` : ''}`;
  if (replace) history.replaceState(null, '', url); else location.hash = url;
}

// ---- transições entre abas: o destaque da pílula desliza até a aba nova e o conteúdo novo entra com um esmaecimento
// curto. Só quando muda a página (não a cada filtro); "reduzir movimento" do sistema desliga tudo pelo CSS.
function moveNavIndicator() {
  const nav = $('#nav'), mark = $('.pill-indicator', nav), active = $('a[aria-current]', nav);
  if (!mark) return;
  if (!active || !active.offsetWidth) { mark.style.opacity = '0'; return; }
  // posição com subpixel (offsetLeft arredonda e deixaria o destaque 1 px fora do lugar)
  const r = active.getBoundingClientRect(), n = nav.getBoundingClientRect();
  Object.assign(mark.style, { opacity: '1', width: `${r.width}px`, height: `${r.height}px`,
    transform: `translate(${r.left - n.left - nav.clientLeft}px, ${r.top - n.top - nav.clientTop}px)` });
  requestAnimationFrame(() => mark.classList.add('ready')); // a primeira posição não desliza a partir do canto
}
window.addEventListener('resize', moveNavIndicator);
let lastPath = null;
function enterRoute(path) {
  if (path === lastPath) return;
  lastPath = path;
  main.classList.remove('route-in');
  void main.offsetWidth; // reinicia a animação
  main.classList.add('route-in');
}

let renderToken = 0;
async function router() {
  const token = ++renderToken;
  const { path, params, anchor } = parseHash();
  $$('#nav a[data-nav]').forEach((a) => a.toggleAttribute('aria-current', a.dataset.nav ? path.startsWith(a.dataset.nav) : path === ''));
  moveNavIndicator();
  if (state.boot?.docente) { viewDocente().catch((err) => { main.innerHTML = errorBlock(err); }); return; }
  for (const [re, view] of routes) {
    const m = path.match(re);
    if (!m) continue;
    try {
      await loadBoot();
      if (token !== renderToken) return;
      await view({ params, args: m.slice(1).map(decodeURIComponent), anchor, isCurrent: () => token === renderToken });
      if (token !== renderToken) return;
      enterRoute(path);
      if (anchor) document.getElementById(anchor)?.scrollIntoView();
      else if (!history.state?.keepScroll) window.scrollTo(0, 0);
    } catch (err) {
      if (token !== renderToken) return;
      if (err.data?.gate) { viewGate(err.data.methods); return; }
      if (err.data?.docente) { viewDocente().catch((e) => { main.innerHTML = errorBlock(e); }); return; }
      main.innerHTML = errorBlock(err);
    }
    return;
  }
  main.innerHTML = `<section class="page narrow empty-state"><h1>Página não encontrada</h1><p>O endereço não existe ou mudou.</p><a class="btn" href="#/">Voltar ao início</a></section>`;
}

// Tela de acesso: o servidor só entrega dados a quem entrou com o e-mail @ufmg.br (código de 6 dígitos, sem conta) ou com
// o código da comunidade. methods vem do servidor: ['email'], ['code'] ou os dois.
function viewGate(methods = ['code']) {
  setTitle('Acesso da comunidade');
  document.body.classList.add('gated');
  const email = methods.includes('email'), code = methods.includes('code');
  const codeForm = `<form id="gate-code" class="gate-form">
        <label class="field">Código de acesso<input name="code" autocomplete="off" autocapitalize="off" spellcheck="false" required placeholder="Digite o código"></label>
        <button class="btn ${email ? 'btn-ghost' : 'btn-lg'}" type="submit">Entrar com código</button>
        <p class="err" role="alert"></p>
      </form>`;
  main.innerHTML = `<section class="page narrow gate">
    <div class="gate-card">
      <span class="brand-logo" aria-hidden="true">D</span>
      <h1>DCC<span>Hub</span></h1>
      <p>Espaço da comunidade estudantil do DCC/UFMG: avaliações anônimas de turmas, oferta do semestre, materiais e oportunidades.</p>
      ${email ? `<form id="gate-email" class="gate-form" novalidate>
        <label class="field">E-mail da UFMG<input name="email" type="email" autocomplete="email" autocapitalize="off" spellcheck="false" required placeholder="voce@ufmg.br"></label>
        <button class="btn btn-lg" type="submit">Receber código de acesso</button>
        <p class="err" role="alert"></p>
      </form>
      <div class="gate-sent" id="gate-sent" hidden>
        <span class="gate-sent-icon" aria-hidden="true"><svg class="ico"><use href="#i-mail"/></svg></span>
        <p class="gate-sent-msg" role="status"></p>
        <form id="gate-otp" class="gate-form" novalidate>
          <label class="field">Código de 6 dígitos<input name="code" class="otp-input" inputmode="numeric" autocomplete="one-time-code" maxlength="7" pattern="[0-9 ]*" required placeholder="000000"></label>
          <button class="btn btn-lg" type="submit">Entrar</button>
          <p class="err" role="alert"></p>
        </form>
        <p class="small muted">O e-mail da UFMG pode levar <b>alguns minutos</b> para chegar (mais ainda se ele é redirecionado para outra caixa). Pode abrir no celular e digitar o código aqui.</p>
        <p class="small muted">Não chegou? Confira o spam ou <button class="link-btn" type="button" id="gate-retry">peça outro código</button><span id="gate-wait"></span>. Os códigos anteriores continuam valendo.</p>
      </div>
      <p class="small muted">Sem conta e sem senha: o e-mail só é usado para enviar o código e <b>não fica guardado</b>. Seu acesso não tem ligação com as avaliações que você escreve, que continuam anônimas.</p>
      ${code ? `<details class="gate-alt"><summary>Tenho um código de acesso</summary>${codeForm}</details>` : ''}`
    : `${codeForm}
      <p class="small muted">O código é divulgado nos canais dos estudantes (centro acadêmico, grupos de turma). Ele não identifica você: o acesso continua anônimo.</p>`}
      <p class="small muted">Projeto independente de estudantes, sem vínculo com o DCC ou com a UFMG. Pedidos de remoção de conteúdo: <a href="mailto:dcchubufmg@gmail.com">dcchubufmg@gmail.com</a>.</p>
    </div>
  </section>`;
  const enter = () => { document.body.classList.remove('gated'); state.boot = null; router(); };
  $('#gate-code')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button', e.target);
    btn.disabled = true;
    try {
      await api('/api/access', { method: 'POST', body: { code: new FormData(e.target).get('code').trim() } });
      enter();
    } catch (err) {
      $('.err', e.target).textContent = err.message;
      btn.disabled = false;
    }
  });
  // os desafios (tokens assinados, sem o código e sem o e-mail) ficam na aba: recarregar a página não perde o código
  // pedido. Guarda os últimos 3: com e-mail lento, a pessoa pede outro e o primeiro chega depois — qualquer um serve.
  const KEY = 'dcchub-otp', PENDING = 3, RESEND_WAIT = 60_000;
  const load = () => {
    try {
      const v = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      const items = (v?.items || []).filter((i) => i.exp > Date.now());
      return items.length ? { ...v, items } : null;
    } catch (_) { return null; }
  };
  const save = (v) => { try { v ? sessionStorage.setItem(KEY, JSON.stringify(v)) : sessionStorage.removeItem(KEY); } catch (_) { /* sem armazenamento */ } };
  let pending = load();
  const form = $('#gate-email'), otp = $('#gate-otp'), retry = $('#gate-retry');
  // espera antes de pedir outro código: quase sempre o primeiro só está atrasado
  let waitTimer = null;
  const tickWait = () => {
    clearTimeout(waitTimer);
    const left = pending ? Math.ceil((pending.sentAt + RESEND_WAIT - Date.now()) / 1000) : 0;
    if (!retry?.isConnected) return; // saiu da tela de acesso
    retry.disabled = left > 0;
    $('#gate-wait').textContent = left > 0 ? ` (em ${left} s)` : '';
    if (left > 0) waitTimer = setTimeout(tickWait, 1000);
  };
  const showOtp = (msg) => {
    $('.gate-sent-msg').textContent = msg;
    form.hidden = true;
    $('#gate-sent').hidden = false;
    $('.err', otp).textContent = '';
    $('input', otp).value = '';
    $('input', otp).focus();
    tickWait();
  };
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button', form), err = $('.err', form);
    const value = new FormData(form).get('email').trim();
    err.textContent = '';
    if (!/^[^@\s]+@[^@\s]+$/.test(value)) { err.textContent = 'Digite um e-mail válido.'; return; }
    // com o modo docente, o e-mail publicado de um docente também vale: quem decide é o servidor
    if (!methods.includes('docente') && !value.toLowerCase().endsWith('@ufmg.br')) { err.textContent = 'Use seu e-mail @ufmg.br.'; return; }
    btn.disabled = true;
    try {
      const res = await api('/api/access/email', { method: 'POST', body: { email: value } });
      const item = { challenge: res.challenge, exp: Date.now() + res.expiresIn * 1000 };
      pending = { items: [item, ...(load()?.items || [])].slice(0, PENDING), msg: res.message, sentAt: Date.now() };
      save(pending);
      showOtp(res.message);
    } catch (e2) {
      err.textContent = e2.message;
    }
    btn.disabled = false;
  });
  otp?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('button', otp), err = $('.err', otp);
    const code = $('input', otp).value.replace(/\D/g, '');
    err.textContent = '';
    if (code.length !== 6) { err.textContent = 'Digite os 6 dígitos do código.'; return; }
    pending = load();
    if (!pending) { err.textContent = 'O código expirou. Peça outro.'; return; }
    btn.disabled = true;
    try {
      const res = await api('/api/access/email/verify', { method: 'POST', body: { challenges: pending.items.map((i) => i.challenge), code } });
      save(null);
      toast(res.docente ? 'Pronto! Este navegador tem acesso à sua página por 30 dias.' : 'Pronto! Este navegador tem acesso por 180 dias.');
      enter();
      return;
    } catch (e2) {
      err.textContent = e2.message;
      $('input', otp).select();
    }
    btn.disabled = false;
  });
  // colou ou digitou os 6 dígitos: envia sozinho
  $('input', otp)?.addEventListener('input', (e) => { if (e.target.value.replace(/\D/g, '').length === 6) otp.requestSubmit(); });
  retry?.addEventListener('click', () => { $('#gate-sent').hidden = true; form.hidden = false; $('input', form).focus(); });
  if (email && pending) { showOtp(pending.msg); return; }
  $(email ? 'input[name=email]' : 'input[name=code]').focus();
}

function errorBlock(err) {
  const notFound = err.status === 404;
  return `<section class="page narrow empty-state"><h1>${notFound ? 'Não encontrado' : 'Não deu para carregar'}</h1>
    <p>${esc(err.message)}</p><a class="btn" href="#/">Voltar ao início</a> ${notFound ? '' : '<button class="btn btn-ghost" data-action="reload">Tentar de novo</button>'}</section>`;
}

function setTitle(title) { document.title = title ? `${title} · DCCHub` : 'DCCHub — avaliações, oferta, materiais e oportunidades do DCC/UFMG'; }

// ---------------------------------------------------------------- componentes
// cartão alto do carrossel de destaques: foto grande, nome, área e nota
function rankCard(p) {
  const s = p.stats;
  return `<li class="rank-card"><a href="#/professor/${p.slug}">
    <span class="rank-photo">${p.photo ? `<img src="${esc(p.photo)}" alt="" loading="lazy" decoding="async">` : `<span class="rank-initials">${esc(initials(p.name))}</span>`}</span>
    <span class="rank-body"><b>${esc(p.name)}</b><small>${esc(p.areas[0] || STATUS_LABEL[p.status])}</small>
      <span class="rank-score">${s ? `${pill(s.overall)} ${plural(s.count, 'avaliação', 'avaliações')}` : '<span class="muted">Sem avaliações ainda</span>'}</span></span>
  </a></li>`;
}

function profCard(p) {
  const s = p.stats;
  return `<a class="card prof-card" href="#/professor/${p.slug}">
    ${avatar(p)}
    <div class="prof-card-body">
      <h3>${esc(p.name)}</h3>
      <p class="muted small">${esc(p.areas.slice(0, 2).join(' · ') || STATUS_LABEL[p.status])}</p>
      <div class="prof-card-meta">
        ${s ? `${pill(s.overall)} <span class="small">${plural(s.count, 'avaliação', 'avaliações')}</span>${s.retake != null ? `<span class="small muted retake-note">${s.retake}% faria de novo</span>` : ''}`
            : '<span class="small muted">Sem avaliações ainda</span>'}
      </div>
      <div class="badges">${p.teachingNow ? `<span class="badge badge-live">Em ${esc(state.boot.latestTerm)}</span>` : ''}${p.status !== 'ativo' ? `<span class="badge">${STATUS_LABEL[p.status]}</span>` : ''}</div>
    </div>
  </a>`;
}

function courseRow(c) {
  const s = c.stats;
  const profs = c.professors.slice(0, 3).map((slug) => state.prof.get(slug)?.name).filter(Boolean);
  return `<a class="card course-card" href="#/disciplina/${encodeURIComponent(c.id)}">
    <div class="course-card-main">
      <span class="code">${esc(courseCode(c))}</span>
      <h3>${esc(c.name)}</h3>
      <p class="muted small">${profs.length ? esc(profs.map(firstNameLast).join(', ')) + (c.professors.length > 3 ? ` +${c.professors.length - 3}` : '') : 'Docentes não informados'}</p>
    </div>
    <div class="course-card-side">
      ${s ? `${pill(s.overall)}<span class="small">${plural(s.count, 'avaliação', 'avaliações')}</span>` : '<span class="small muted">sem avaliações</span>'}
      <span class="small muted">${plural(c.terms.length, 'semestre', 'semestres')}</span>
      ${c.offeredNow ? `<span class="badge badge-live">Em ${esc(state.boot.latestTerm)}</span>` : c.active ? '' : `<span class="badge">até ${esc(c.terms[c.terms.length - 1])}</span>`}
    </div>
  </a>`;
}
function firstNameLast(name) { const p = name.split(' '); return p.length > 1 ? `${p[0]} ${p[p.length - 1]}` : name; }

function bars(avg, criteria, { answered = {}, count = 0 } = {}) {
  const hasLevels = criteria.some((c) => c.levels); // coluna do nível em todas as linhas, para as barras ficarem alinhadas
  return `<dl class="bars">${criteria.map((c) => {
    const v = avg?.[c.key];
    const partial = answered[c.key] != null && answered[c.key] < count; // ex.: "passar" não existia nas avaliações antigas
    const note = partial ? (answered[c.key] ? ` <small class="muted">(${answered[c.key]} de ${count})</small>` : ' <small class="muted">(ainda sem respostas)</small>') : '';
    return `<div class="bar-row" title="${esc(c.hint)}${partial ? ' — pergunta nova: as avaliações anteriores não a respondiam' : ''}">
      <dt>${esc(c.label)}${note}</dt>
      <dd><span class="bar"><span class="bar-fill ${scoreClass(v, c.neutral)}" style="width:${v ? (v / 5) * 100 : 0}%"></span></span><b>${fmt(v)}</b>${hasLevels ? `<small class="bar-level">${esc(level(c, v))}</small>` : ''}</dd>
    </div>`;
  }).join('')}</dl>`;
}

function summaryBlock(summary, { subject = 'docente' } = {}) {
  const crit = state.boot.criteria;
  if (!summary || !summary.count) {
    return `<div class="summary summary-empty"><p class="big-muted">Ainda sem avaliações</p><p class="muted">Cursou com ${subject === 'docente' ? 'este docente' : 'esta disciplina'}? Sua experiência ajuda muita gente a decidir.</p></div>`;
  }
  const dist = summary.distribution;
  const max = Math.max(...Object.values(dist), 1);
  const grades = Object.entries(summary.grades || {});
  const gradeTotal = grades.reduce((a, [, n]) => a + n, 0);
  return `<div class="summary">
    <div class="summary-score">
      <div class="ring ${scoreClass(summary.avg.overall)}" style="--p:${(summary.avg.overall / 5) * 100}"><span class="score-big">${fmt(summary.avg.overall)}</span><small>de 5</small></div>
      ${stars(summary.avg.overall)}
      <span class="muted small">${plural(summary.count, 'avaliação', 'avaliações')}</span>
      ${summary.retake != null ? `<span class="retake"><b>${summary.retake}%</b> faria de novo</span>` : ''}
    </div>
    <div class="summary-bars">${bars(summary.avg, crit.filter((c) => c.key !== 'overall'), { answered: summary.answered, count: summary.count })}</div>
    <div class="summary-dist" aria-label="Distribuição das notas gerais">
      ${[5, 4, 3, 2, 1].map((n) => `<div class="dist-row"><span>${n}★</span><span class="bar"><span class="bar-fill ${scoreClass(n)}" style="width:${(dist[n] / max) * 100}%"></span></span><span class="muted">${dist[n]}</span></div>`).join('')}
    </div>
    ${summary.tags.length ? `<div class="summary-tags"><h4>O que mais dizem</h4><div class="chips">${summary.tags.map((t) => `<span class="chip ${tagTone(t.tag)}">${esc(t.tag)} <b>${t.count}</b></span>`).join('')}</div></div>` : ''}
    ${gradeTotal ? `<div class="summary-grades"><h4>Conceitos informados</h4><div class="grade-bars">${grades.map(([g, n]) => `<div class="grade-col" title="${n} × ${g}"><span class="grade-bar" style="height:${(n / gradeTotal) * 100}%"></span><span>${g}</span></div>`).join('')}</div></div>` : ''}
  </div>`;
}

// ---- evolução por semestre cursado: linha da nota geral (1–5), um ponto por semestre com avaliações.
// Ponto vazado = menos de EVOLUTION_MIN avaliações naquele semestre (pouco para chamar de tendência).
const EVOLUTION_MIN = 3;
function evolutionBlock(summary) {
  const terms = summary?.byTerm || [];
  if (terms.length < 2) return '';
  const crit = state.boot.criteria;
  return `<section class="block evolution" aria-labelledby="evo-title">
    <div class="section-head"><h2 id="evo-title">Evolução por semestre</h2><span class="muted small">nota geral, pelo semestre em que a turma foi cursada</span></div>
    <div class="evo-chart" id="evo-chart"></div>
    <p class="small muted evo-note"><span class="evo-key evo-key-few" aria-hidden="true"></span> menos de ${EVOLUTION_MIN} avaliações no semestre · clique num semestre para ver as avaliações dele</p>
    <details class="evo-table"><summary class="small">Ver em tabela</summary>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Semestre</th><th class="num">Aval.</th>${crit.map((c) => `<th class="num" title="${esc(c.label)}">${esc(c.short || c.label)}</th>`).join('')}</tr></thead>
        <tbody>${terms.slice().reverse().map((t) => `<tr><td>${esc(t.term)}</td><td class="num">${t.count}</td>${crit.map((c) => `<td class="num">${pill(t[c.key], { crit: c })}</td>`).join('')}</tr>`).join('')}</tbody>
      </table></div>
    </details>
  </section>`;
}

function wireEvolution(summary, { onPick } = {}) {
  const box = $('#evo-chart');
  if (!box) return;
  const terms = summary.byTerm;
  const svgNS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}, parent) => { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.append(n); return n; };
  const tip = document.createElement('div');
  tip.className = 'evo-tip';
  tip.hidden = true;
  const draw = () => {
    const W = Math.max(280, box.clientWidth);
    const H = 190;
    const m = { top: 14, right: 48, bottom: 28, left: 42 };
    const x = (i) => m.left + (terms.length === 1 ? (W - m.left - m.right) / 2 : (i * (W - m.left - m.right)) / (terms.length - 1));
    const y = (v) => m.top + ((5 - v) / 4) * (H - m.top - m.bottom);
    box.replaceChildren();
    const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'group', 'aria-label': 'Nota geral por semestre' }, box);
    const deco = el('g', { 'aria-hidden': 'true' }, svg); // grade, eixos, linha e pontos: o leitor de tela usa os alvos e a tabela
    for (let v = 1; v <= 5; v += 1) { // grade recessiva + rótulos do eixo
      el('line', { x1: m.left, x2: W - m.right + 8, y1: y(v), y2: y(v), class: 'evo-grid' }, deco);
      el('text', { x: m.left - 10, y: y(v) + 4, class: 'evo-axis', 'text-anchor': 'end' }, deco).textContent = v;
    }
    const every = Math.ceil(terms.length / Math.max(1, Math.floor((W - m.left - m.right) / 56))); // rótulos do X sem colidir
    const labeled = new Set(terms.map((_, i) => i).filter((i) => i % every === 0));
    const lastI = terms.length - 1;
    if (!labeled.has(lastI)) { labeled.delete(lastI - (lastI % every)); labeled.add(lastI); } // o mais recente sempre aparece
    terms.forEach((t, i) => {
      if (labeled.has(i)) el('text', { x: x(i), y: H - 8, class: 'evo-axis', 'text-anchor': 'middle' }, deco).textContent = t.term;
    });
    const cross = el('line', { y1: m.top - 4, y2: H - m.bottom, class: 'evo-cross', visibility: 'hidden' }, deco);
    el('polyline', { points: terms.map((t, i) => `${x(i)},${y(t.overall)}`).join(' '), class: 'evo-line' }, deco);
    terms.forEach((t, i) => el('circle', { cx: x(i), cy: y(t.overall), r: 4.5, class: t.count < EVOLUTION_MIN ? 'evo-dot few' : 'evo-dot' }, deco));
    const last = terms[terms.length - 1]; // valor só na ponta (o resto fica no tooltip e na tabela)
    el('text', { x: x(terms.length - 1) + 10, y: y(last.overall) + 4, class: 'evo-end' }, deco).textContent = fmt(last.overall);
    // alvos de hover/foco: a faixa inteira de cada semestre, bem maior que o ponto
    const half = terms.length > 1 ? (x(1) - x(0)) / 2 : 40;
    terms.forEach((t, i) => {
      const hit = el('rect', { x: x(i) - half, y: 0, width: half * 2, height: H, class: 'evo-hit', tabindex: 0, role: 'button',
        'aria-label': `${t.term}: nota geral ${fmt(t.overall)}, ${t.count} ${t.count === 1 ? 'avaliação' : 'avaliações'}. Ver avaliações deste semestre.` }, svg);
      const show = () => {
        cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
        tip.replaceChildren();
        const head = document.createElement('div'); head.className = 'evo-tip-head';
        const val = document.createElement('b'); val.textContent = fmt(t.overall);
        const lbl = document.createElement('span'); lbl.textContent = ` geral · ${t.term}`;
        head.append(val, lbl);
        const rows = document.createElement('dl');
        state.boot.criteria.filter((c) => c.key !== 'overall' && t[c.key] != null).forEach((c) => {
          const dt = document.createElement('dt'); dt.textContent = c.short || c.label;
          const dd = document.createElement('dd'); dd.textContent = fmt(t[c.key]);
          rows.append(dt, dd);
        });
        const foot = document.createElement('div'); foot.className = 'evo-tip-foot';
        foot.textContent = `${t.count} ${t.count === 1 ? 'avaliação' : 'avaliações'}${t.count < EVOLUTION_MIN ? ' · poucas para indicar tendência' : ''}`;
        tip.append(head, rows, foot);
        tip.hidden = false;
        // ao lado da linha vertical (direita, ou esquerda se não couber): nunca cobre o ponto nem a linha
        const right = x(i) + 14;
        tip.style.left = `${right + tip.offsetWidth <= W ? right : Math.max(0, x(i) - 14 - tip.offsetWidth)}px`;
        tip.style.top = '0px';
      };
      const hide = () => { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; };
      hit.addEventListener('pointerenter', show);
      hit.addEventListener('focus', show);
      hit.addEventListener('pointerleave', hide);
      hit.addEventListener('blur', hide);
      const pick = () => onPick?.(t.term);
      hit.addEventListener('click', pick);
      hit.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    });
    box.append(tip);
  };
  draw();
  if (window.ResizeObserver) {
    let lastW = box.clientWidth;
    new ResizeObserver(() => { if (box.isConnected && box.clientWidth !== lastW) { lastW = box.clientWidth; draw(); } }).observe(box);
  }
}

function tagTone(tag) {
  const t = state.boot.tags;
  return t.positivo.includes(tag) ? 'pos' : t.negativo.includes(tag) ? 'neg' : 'neu';
}

function reviewCard(r, { show = 'course' } = {}) {
  const course = state.course.get(r.course);
  const prof = state.prof.get(r.professor);
  const crit = state.boot.criteria.filter((c) => c.key !== 'overall');
  const heading = show === 'course'
    ? `<a href="#/disciplina/${encodeURIComponent(r.course)}">${esc(course?.name || r.course)}</a>`
    : show === 'both'
      ? `<a href="#/professor/${r.professor}">${esc(prof?.name || r.professor)}</a> · <a href="#/disciplina/${encodeURIComponent(r.course)}">${esc(course?.name || r.course)}</a>`
      : `<a href="#/professor/${r.professor}">${esc(prof?.name || r.professor)}</a>`;
  const paragraphs = esc(r.comment).split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
  return `<article class="review" id="review-${r.id}">
    <header class="review-head">
      <span class="score-tile ${scoreClass(r.ratings.overall)}" aria-label="Nota geral ${r.ratings.overall} de 5">${r.ratings.overall}</span>
      <div>
        <h3>${heading}</h3>
        <p class="muted small">${esc(r.term)}${r.verified ? ' · <span class="verified" title="Esta turma consta na oferta oficial publicada pelo DCC">✓ turma confirmada</span>' : ''}
          · ${dayAgo(r.createdAt)}${r.updatedAt ? ' · editada' : ''}${r.status === 'held' ? ' · <span class="badge badge-warn">em revisão</span>' : ''}</p>
      </div>
    </header>
    <ul class="review-metrics">
      ${crit.filter((c) => r.ratings[c.key] != null).map((c) => `<li><span>${esc(c.label)}</span>${pill(r.ratings[c.key], { crit: c })}${c.levels ? `<small>${esc(level(c, r.ratings[c.key]).toLowerCase())}</small>` : ''}</li>`).join('')}
      ${r.wouldRetake != null ? `<li><span>Faria de novo</span><b class="${r.wouldRetake ? 'yes' : 'no'}">${r.wouldRetake ? 'Sim' : 'Não'}</b></li>` : ''}
      ${r.grade ? `<li><span>Conceito</span><b>${esc(r.grade)}</b></li>` : ''}
    </ul>
    ${r.tags.length ? `<div class="chips">${r.tags.map((t) => `<span class="chip ${tagTone(t)}">${esc(t)}</span>`).join('')}</div>` : ''}
    <div class="review-text">${paragraphs}</div>
    <footer class="review-foot">
      ${r.mine
        ? `<span class="muted small">Esta é a sua avaliação</span>
           <a class="link-btn" href="#/avaliar?edit=${r.id}">Editar</a>
           <button class="link-btn danger" data-action="delete-review" data-id="${r.id}">Apagar</button>`
        : `<button class="helpful ${r.voted ? 'on' : ''}" data-action="helpful" data-id="${r.id}" aria-pressed="${!!r.voted}">▲ Útil <b>${r.helpful || ''}</b></button>
           <button class="link-btn muted" data-action="report" data-id="${r.id}">Denunciar</button>`}
    </footer>
  </article>`;
}

function reviewsSection({ reviews, show, filterOptions, filterLabel, ctaHref, subjectName }) {
  const opts = filterOptions?.length > 1
    ? `<label class="select-inline">${esc(filterLabel)} <select data-filter="reviews"><option value="">Todas</option>${filterOptions.map((o) => `<option value="${esc(o.value)}">${esc(o.label)} (${o.count})</option>`).join('')}</select></label>` : '';
  const termCounts = reviews.reduce((acc, r) => ({ ...acc, [r.term]: (acc[r.term] || 0) + 1 }), {});
  const termKeys = Object.keys(termCounts).sort().reverse();
  const termOpts = termKeys.length > 1
    ? `<label class="select-inline">Semestre <select data-filter-term="reviews"><option value="">Todos</option>${termKeys.map((t) => `<option value="${esc(t)}">${esc(t)} (${termCounts[t]})</option>`).join('')}</select></label>` : '';
  return `<section class="reviews-section" id="avaliacoes">
    <div class="section-head">
      <h2>Avaliações <span class="count">${reviews.length}</span></h2>
      <div class="row-gap">${opts}${termOpts}
        <label class="select-inline">Ordenar <select data-sort="reviews">
          <option value="helpful">Mais úteis</option><option value="recent">Mais recentes</option><option value="high">Melhores notas</option><option value="low">Piores notas</option>
        </select></label>
      </div>
    </div>
    <div class="review-list" data-show="${show}">
      ${reviews.length ? reviews.map((r) => reviewCard(r, { show })).join('') : `<div class="empty-state small-empty"><p>Ninguém avaliou ${esc(subjectName)} ainda.</p>${ctaHref ? `<a class="btn" href="${ctaHref}">Escrever a primeira avaliação</a>` : ''}</div>`}
    </div>
  </section>`;
}

function wireReviewList(reviews, show) {
  const list = $('.review-list');
  if (!list) return;
  let filter = '';
  let term = '';
  let sort = 'helpful';
  const redraw = () => {
    const sorted = reviews.filter((r) => (!filter || r.course === filter || r.professor === filter) && (!term || r.term === term));
    const by = {
      helpful: (a, b) => b.helpful - a.helpful || b.createdAt - a.createdAt || b.id - a.id,
      recent: (a, b) => b.createdAt - a.createdAt || b.id - a.id,
      high: (a, b) => b.ratings.overall - a.ratings.overall || b.createdAt - a.createdAt || b.id - a.id,
      low: (a, b) => a.ratings.overall - b.ratings.overall || b.createdAt - a.createdAt || b.id - a.id,
    }[sort];
    list.innerHTML = sorted.sort(by).map((r) => reviewCard(r, { show })).join('') || '<p class="muted">Nenhuma avaliação com esse filtro.</p>';
  };
  $('[data-filter="reviews"]')?.addEventListener('change', (e) => { filter = e.target.value; redraw(); });
  $('[data-sort="reviews"]')?.addEventListener('change', (e) => { sort = e.target.value; redraw(); });
  const termSelect = $('[data-filter-term="reviews"]');
  termSelect?.addEventListener('change', (e) => { term = e.target.value; redraw(); });
  list._reviews = reviews;
  // usado pelo gráfico de evolução: clicar num semestre filtra a lista
  return {
    showTerm(t) {
      if (!termSelect || ![...termSelect.options].some((o) => o.value === t)) return;
      termSelect.value = t;
      term = t;
      redraw();
      $('#avaliacoes')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
  };
}

// ---------------------------------------------------------------- ações globais (delegação)
document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const { action, id } = el.dataset;
  if (action === 'reload') { router(); return; }
  if (action === 'helpful') {
    e.preventDefault();
    el.disabled = true;
    try {
      const res = await api(`/api/reviews/${id}/helpful`, { method: 'POST', body: {} });
      el.classList.toggle('on', res.voted);
      el.setAttribute('aria-pressed', String(res.voted));
      el.querySelector('b').textContent = res.helpful || '';
      const r = $('.review-list')?._reviews?.find((x) => String(x.id) === id);
      if (r) { r.voted = res.voted; r.helpful = res.helpful; }
    } catch (err) { toast(err.message, 'error'); }
    el.disabled = false;
  }
  if (action === 'report') openReportDialog(id);
  if (action === 'delete-review') {
    if (!confirm('Apagar esta avaliação? Isso não pode ser desfeito.')) return;
    try {
      await api(`/api/reviews/${id}`, { method: 'DELETE' });
      toast('Avaliação apagada.');
      state.me = null;
      await loadBoot(true);
      router();
    } catch (err) { toast(err.message, 'error'); }
  }
});

// Aviso quando o semestre corrente ainda não tem oferta oficial: o que aparece como "atual" é a última publicada.
// Nome que a oferta oficial traz mas não corresponde a ninguém da lista do DCC (substituto, só o primeiro nome…)
function unidentified(o, fallback) {
  if (!o.unresolved?.length) return fallback;
  return `<span class="muted" title="Nome como aparece na oferta do DCC. Não foi possível identificar com segurança na lista de docentes (substituto, docente de outro departamento ou só o primeiro nome).">${esc(o.unresolved.join(', '))} <small>(não identificado)</small></span>`;
}

// Semestre importado do mapa de oferta do SIGAA (o DCC ainda não publicou a página oficial)
const viaSigaa = (term) => state.boot.termSources?.[term] === 'SIGAA';

function staleNotice(extra = '') {
  const b = state.boot;
  if (b.offerPublished || !b.communityTerm) return '';
  return `<div class="notice" role="note"><svg class="ico" aria-hidden="true"><use href="#i-info"/></svg><div>
    <b>A oferta de ${esc(b.currentTerm)} ainda não foi publicada pelo DCC.</b>
    Turmas e horários marcados como “${esc(b.latestTerm)}” são da última oferta oficial. ${extra}</div></div>`;
}

// Turmas do semestre corrente informadas por estudantes (só existem enquanto a oferta oficial não sai).
function communityBlock(community, { mode, subject }) {
  if (!community) return '';
  const t = esc(community.term);
  const rows = community.items.map((it) => {
    const p = state.prof.get(it.professor);
    const c = state.course.get(it.course);
    const label = mode === 'prof'
      ? `<a href="#/disciplina/${encodeURIComponent(it.course)}"><b>${esc(c?.name || it.course)}</b></a> <span class="code">${esc(c ? courseCode(c) : '')}</span>`
      : `<a class="prof-inline" href="#/professor/${it.professor}">${p ? avatar(p, 'sm') : ''} <span>${esc(p?.name || it.professor)}</span></a>`;
    return `<li>${label}
      <span class="small muted">${plural(it.count, 'estudante confirmou', 'estudantes confirmaram')}</span>
      ${it.mine ? `<button class="link-btn small" data-creport="del" data-prof="${esc(it.professor)}" data-course="${esc(it.course)}">desfazer meu relato</button>`
                : `<button class="btn btn-sm btn-ghost" data-creport="add" data-prof="${esc(it.professor)}" data-course="${esc(it.course)}">Confirmar</button>`}</li>`;
  }).join('');
  return `<section class="block community" id="comunidade">
    <div class="section-head"><div><h2>Em ${t}, segundo estudantes</h2><p class="small muted">Não é dado oficial: some quando o DCC publicar a oferta de ${t}.</p></div>
      <button class="btn btn-sm" data-creport="new">${mode === 'prof' ? `Informar disciplina de ${esc(subject)}` : 'Informar docente'}</button></div>
    ${rows ? `<ul class="community-list">${rows}</ul>` : `<p class="muted small">Ninguém informou ainda. Sabe ${mode === 'prof' ? `o que ${esc(subject)} está dando` : 'quem está dando esta disciplina'} em ${t}?</p>`}
  </section>`;
}

function wireCommunity({ professor, course, mode, reload }) {
  const box = $('#comunidade');
  if (!box) return;
  const send = async (method, prof, crs) => {
    try {
      const res = await api('/api/class-reports', { method, body: { professor: prof, course: crs } });
      toast(res.message || 'Feito.');
      reload();
    } catch (err) { toast(err.message, 'error'); }
  };
  box.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-creport]');
    if (!btn) return;
    const kind = btn.dataset.creport;
    if (kind === 'add') return send('POST', btn.dataset.prof, btn.dataset.course);
    if (kind === 'del') return send('DELETE', btn.dataset.prof, btn.dataset.course);
    // novo relato: escolhe a disciplina (página do docente) ou o docente (página da disciplina)
    const b = state.boot;
    const dlg = $('#dialog');
    let options;
    if (mode === 'prof') {
      const prof = state.prof.get(professor);
      const mine = new Set(prof?.courses || []);
      const list = b.courses.filter((c) => c.active).sort((x, y) => (mine.has(y.id) - mine.has(x.id)) || (x.kind === y.kind ? x.name.localeCompare(y.name) : x.kind === 'regular' ? -1 : 1));
      options = list.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} — ${esc(c.codes.join('/'))}${mine.has(c.id) ? ' ★' : ''}</option>`).join('');
    } else {
      const crs = state.course.get(course);
      const before = new Set(crs?.professors || []);
      const list = b.professors.filter((p) => ['ativo', 'voluntario', 'externo', 'substituto'].includes(p.status)).sort((x, y) => (before.has(y.slug) - before.has(x.slug)) || x.name.localeCompare(y.name));
      options = list.map((p) => `<option value="${esc(p.slug)}">${esc(p.name)}${before.has(p.slug) ? ' ★' : ''}</option>`).join('');
    }
    dlg.innerHTML = `<form method="dialog" class="dialog-body" id="creport-form">
      <h2>${mode === 'prof' ? 'Qual disciplina?' : 'Qual docente?'}</h2>
      <p class="muted">Informe só o que você sabe de fonte direta (está matriculado, viu no SIGA). ★ = já lecionou ${mode === 'prof' ? 'antes' : 'esta disciplina'}.</p>
      <label class="field">${mode === 'prof' ? 'Disciplina' : 'Docente'}<select name="pick" required><option value="">Selecione…</option>${options}</select></label>
      <div class="row-gap end"><button class="btn btn-ghost" value="cancel" formnovalidate>Cancelar</button><button class="btn" value="send">Informar turma de ${esc(b.communityTerm)}</button></div>
    </form>`;
    dlg.showModal();
    $('#creport-form').addEventListener('submit', (ev) => {
      if (ev.submitter?.value !== 'send') return;
      ev.preventDefault();
      const pick = new FormData(ev.target).get('pick');
      if (!pick) return;
      dlg.close();
      send('POST', mode === 'prof' ? professor : pick, mode === 'prof' ? pick : course);
    });
  });
}

function openReportDialog(id) {
  const dlg = $('#dialog');
  dlg.innerHTML = `<form method="dialog" class="dialog-body" id="report-form">
    <h2>Denunciar avaliação</h2>
    <p class="muted">Denúncias são anônimas e vão para a moderação. Denunciada por várias pessoas, de redes diferentes, a avaliação sai do ar até ser revisada.</p>
    <fieldset class="radio-list">${state.boot.reportReasons.map((r, i) => `<label><input type="radio" name="reason" value="${esc(r)}" ${i === 0 ? 'checked' : ''}> ${esc(r)}</label>`).join('')}</fieldset>
    <label class="field">Detalhes (opcional)<textarea name="detail" maxlength="300" rows="3"></textarea></label>
    <div class="row-gap end"><button class="btn btn-ghost" value="cancel" formnovalidate>Cancelar</button><button class="btn" value="send">Enviar denúncia</button></div>
  </form>`;
  dlg.showModal();
  $('#report-form').addEventListener('submit', async (e) => {
    if (e.submitter?.value !== 'send') return;
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api(`/api/reviews/${id}/report`, { method: 'POST', body: { reason: fd.get('reason'), detail: fd.get('detail') } });
      toast(res.message);
      dlg.close();
    } catch (err) { toast(err.message, 'error'); }
  });
}

// ---------------------------------------------------------------- páginas
async function viewHome() {
  setTitle('');
  const b = state.boot;
  const recent = await api('/api/reviews?sort=recent&limit=6').then((d) => d.reviews).catch(() => []);
  // carrossel com 10 docentes: os mais bem avaliados (2+ avaliações); se faltar, quem tem 1; se ainda faltar, quem está
  // lecionando agora, mesmo sem avaliações (aí o título deixa de dizer "mais bem avaliados")
  const CAROUSEL_SIZE = 10;
  const inDept = (p) => p.status === 'ativo' || p.status === 'voluntario';
  const byScore = (x, y) => y.stats.score - x.stats.score;
  const rated = [
    ...b.professors.filter((p) => inDept(p) && p.stats?.count >= 2).sort(byScore),
    ...b.professors.filter((p) => inDept(p) && p.stats?.count === 1).sort(byScore),
  ].slice(0, CAROUSEL_SIZE);
  const allRated = rated.length >= CAROUSEL_SIZE;
  if (!allRated) {
    const taken = new Set(rated.map((p) => p.slug));
    rated.push(...b.professors.filter((p) => p.teachingNow && inDept(p) && !taken.has(p.slug))
      .sort((x, y) => Number(!!y.photo) - Number(!!x.photo) || x.name.localeCompare(y.name)).slice(0, CAROUSEL_SIZE - rated.length));
  }
  const live = b.courses.filter((c) => c.offeredNow && c.kind === 'regular');
  const mostReviewed = b.courses.filter((c) => c.stats).sort((x, y) => y.stats.count - x.stats.count).slice(0, 10);
  const featuredCourses = mostReviewed.length >= 3 ? mostReviewed : [...live].sort((x, y) => y.terms.length - x.terms.length || x.name.localeCompare(y.name)).slice(0, 10);
  const activeCount = b.professors.filter((p) => p.status === 'ativo' || p.status === 'voluntario').length;
  const teachingNow = b.professors.filter((p) => p.teachingNow).length;
  // disciplinas da oferta atual com mais avaliações (o que os colegas mais comentam entre o que dá para cursar agora)
  const spotCourses = live.filter((c) => c.stats).sort((x, y) => y.stats.count - x.stats.count).slice(0, 4);
  // a seção de baixo não repete o que já está no painel
  const spotIds = new Set(spotCourses.map((c) => c.id));
  const moreCourses = featuredCourses.filter((c) => !spotIds.has(c.id)).slice(0, 6);
  main.innerHTML = `
  <section class="page">
    <div class="hero">
      <div class="hero-inner">
        <div class="hero-copy">
          <h1>Docentes, disciplinas e oportunidades do DCC em um só lugar</h1>
          <ul class="meta-row" aria-label="Semestre">
            <li><svg class="ico"><use href="#i-calendar"/></svg>Semestre ${esc(b.currentTerm || '')}</li>
            ${b.offerPublished
              ? `<li><span class="live-dot" aria-hidden="true"></span>Oferta ${esc(b.latestTerm)} publicada${b.termSources?.[b.latestTerm] === 'SIGAA' ? ' (SIGAA)' : ''}</li>`
              : `<li><span class="live-dot pending" aria-hidden="true"></span><a href="#/sobre#atual">Oferta ${esc(b.currentTerm)} ainda não publicada</a></li>`}
            <li><svg class="ico ico-fill"><use href="#i-star"/></svg>${plural(b.totals.reviews, 'avaliação', 'avaliações')}</li>
          </ul>
          <p class="lead">Avaliações anônimas de estudantes, a oferta do semestre com montador de grade, provas antigas e chamadas de pesquisa do Departamento de Ciência da Computação da UFMG.</p>
          <form class="hero-search" id="hero-search" role="search">
            <input type="search" id="hero-q" placeholder="Ex.: Algoritmos I, DCC206, Eduardo…" aria-label="Buscar docente ou disciplina" autocomplete="off">
            <button class="btn" type="submit">Buscar</button>
            <div class="search-results hero-results" id="hero-results" hidden></div>
          </form>
          <div class="hero-actions"><a href="#/professores?sort=score">Docentes por avaliação</a><a href="#/disciplinas?now=1">Disciplinas de ${esc(b.latestTerm || '')}</a><a href="#/sobre">Como funciona</a></div>
        </div>
        <aside class="spotlight" aria-labelledby="spot-term">
          <p class="spot-label">${b.offerPublished ? 'Oferta do semestre' : 'Última oferta oficial'}</p>
          <p class="spot-term" id="spot-term">${esc(b.latestTerm || '')}</p>
          <ul class="spot-facts">
            <li><b>${live.length}</b> disciplinas</li>
            <li><b>${teachingNow}</b> docentes lecionando</li>
            <li><b>${activeCount}</b> em atividade no DCC</li>
          </ul>
          ${spotCourses.length ? `<p class="spot-sub">Mais avaliadas nesta oferta</p>
          <ol class="spot-list">${spotCourses.map((c) => `<li><a href="#/disciplina/${encodeURIComponent(c.id)}"><span><b>${esc(c.name)}</b><small>${esc(courseCode(c))} · ${plural(c.stats.count, 'avaliação', 'avaliações')}</small></span>${pill(c.stats.overall)}</a></li>`).join('')}</ol>` : ''}
          <a class="btn btn-lg spot-cta" href="#/oferta"><svg class="ico"><use href="#i-calendar"/></svg>Montar minha grade</a>
        </aside>
      </div>
    </div>
    <nav class="quick" aria-label="Atalhos">
      <a href="#/avaliar"><span class="q-ico"><svg class="ico"><use href="#i-pen"/></svg></span><span><b>Avaliar uma turma</b><small>Anônimo, sem cadastro</small></span></a>
      <a href="#/disciplinas"><span class="q-ico"><svg class="ico"><use href="#i-book"/></svg></span><span><b>Comparar docentes</b><small>Quem já deu cada disciplina</small></span></a>
      <a href="#/materiais"><span class="q-ico"><svg class="ico"><use href="#i-archive"/></svg></span><span><b>Materiais</b><small>Provas e listas de semestres anteriores</small></span></a>
      <a href="#/oportunidades"><span class="q-ico"><svg class="ico"><use href="#i-briefcase"/></svg></span><span><b>Oportunidades</b><small>Bolsas de IC, pesquisa e estágios</small></span></a>
    </nav>
    ${rated.length ? `<section class="carousel" id="carousel-top" aria-roledescription="carrossel" aria-labelledby="carousel-top-title">
      <div class="section-head"><h2 id="carousel-top-title">${allRated ? 'Docentes mais bem avaliados' : 'Docentes em destaque'}</h2>
        <div class="carousel-nav"><a href="#/professores?sort=score">Ver todos</a>
          <button class="pill-icon" type="button" data-step="-1" aria-label="Página anterior">‹</button><button class="pill-icon" type="button" data-step="1" aria-label="Próxima página">›</button></div></div>
      <ol class="carousel-track">${rated.map((p) => rankCard(p)).join('')}</ol>
      <div class="carousel-dots" role="group" aria-label="Páginas"></div>
    </section>` : ''}

    <div class="section-head"><div><p class="eyebrow">${mostReviewed.length >= 3 ? 'Mais comentadas' : `Oferta ${esc(b.latestTerm || '')}`}</p><h2>${mostReviewed.length >= 3 ? 'Disciplinas mais avaliadas' : b.offerPublished ? 'Disciplinas deste semestre' : `Disciplinas da oferta ${esc(b.latestTerm || '')}`}</h2></div><a href="#/disciplinas">Todas as disciplinas →</a></div>
    <div class="grid grid-2">${moreCourses.map(courseRow).join('')}</div>

    <div class="section-head"><div><p class="eyebrow">Comunidade</p><h2>Avaliações recentes</h2></div>${recent.length ? '' : ''}</div>
    ${recent.length ? `<div class="review-grid">${recent.map((r) => reviewCard(r, { show: 'both' })).join('')}</div>`
      : `<div class="empty-state callout"><h3>Ainda não há avaliações publicadas</h3><p>As avaliações aparecem aqui assim que forem publicadas. São anônimas e não exigem cadastro.</p><a class="btn" href="#/avaliar">Avaliar uma turma</a></div>`}

    <section class="band" aria-labelledby="band-title">
      <h2 id="band-title">Ninguém sabe quem escreveu. Nem a gente.</h2>
      <div class="band-points">
        <p><b>Sem conta.</b> Nada de nome, e-mail ou matrícula. Seu navegador guarda um identificador aleatório, e o servidor só conhece um hash dele.</p>
        <p><b>Ligado à oferta oficial.</b> Docentes, disciplinas e horários vêm do site do DCC. Turmas que constam na oferta ganham o selo “turma confirmada”.</p>
        <p><b>Moderado.</b> Críticas à disciplina são bem-vindas; ataques pessoais não são publicados.</p>
      </div>
      <a class="btn btn-lg" href="#/avaliar"><svg class="ico"><use href="#i-pen"/></svg>Avaliar uma turma</a>
    </section>
  </section>`;
  setupInlineSearch($('#hero-q'), $('#hero-results'), $('#hero-search'));
  setupCarousel($('#carousel-top'));
}

// Carrossel: a trilha rola por páginas (setas, pontos ou arrastando no celular). Sem rolagem automática: quem lê
// decide quando passar. As setas dão a volta (da última página para a primeira e vice-versa).
function setupCarousel(root) {
  if (!root) return;
  const track = $('.carousel-track', root), dots = $('.carousel-dots', root);
  const smooth = () => (matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth');
  // páginas contadas em cartões: com 6 cartões e 5 por página são 2 páginas (a última, parcial, mostra os 5 finais)
  const step = () => { const a = track.children[0], b = track.children[1]; return a && b ? b.offsetLeft - a.offsetLeft : track.clientWidth; };
  const perPage = () => Math.max(1, Math.round((track.clientWidth + 1) / step()));
  const pages = () => Math.max(1, Math.ceil(track.children.length / perPage()));
  const current = () => {
    const max = track.scrollWidth - track.clientWidth;
    if (max > 0 && track.scrollLeft >= max - 2) return pages() - 1; // chegou ao fim: é a última página, mesmo parcial
    return Math.min(pages() - 1, Math.round(track.scrollLeft / (perPage() * step())));
  };
  const go = (i) => track.scrollTo({ left: ((i + pages()) % pages()) * perPage() * step(), behavior: smooth() });
  const render = () => {
    const n = pages(), at = current();
    dots.hidden = n < 2;
    $$('[data-step]', root).forEach((b) => { b.hidden = n < 2; });
    if (dots.children.length !== n) {
      dots.innerHTML = Array.from({ length: n }, (_, i) => `<button type="button" data-page="${i}" aria-label="Página ${i + 1} de ${n}"></button>`).join('');
    }
    [...dots.children].forEach((d, i) => d.toggleAttribute('aria-current', i === at));
  };
  $$('[data-step]', root).forEach((b) => b.addEventListener('click', () => go(current() + Number(b.dataset.step))));
  dots.addEventListener('click', (e) => { const d = e.target.closest('[data-page]'); if (d) go(Number(d.dataset.page)); });
  let t;
  track.addEventListener('scroll', () => { clearTimeout(t); t = setTimeout(render, 60); }, { passive: true });
  // teclado: setas esquerda/direita com o foco dentro do carrossel
  root.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); go(current() + (e.key === 'ArrowRight' ? 1 : -1)); }
  });
  new ResizeObserver(render).observe(track);
  render();
}

function setupInlineSearch(input, box, form) {
  let results = [];
  const render = () => {
    results = search(input.value, 6);
    box.innerHTML = results.map((r) => `<a class="search-item" href="${r.href}">${r.type === 'prof' ? avatar(r.item, 'sm') : '<span class="search-kind" aria-hidden="true"><svg class="ico"><use href="#i-book"/></svg></span>'}<span><b>${esc(r.label)}</b><small>${esc(r.sub)}</small></span>${r.item.stats ? pill(r.item.stats.overall) : ''}</a>`).join('');
    box.hidden = !results.length;
  };
  input.addEventListener('input', render);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (results[0]) location.hash = results[0].href;
    else if (input.value.trim()) location.hash = `#/professores?q=${encodeURIComponent(input.value.trim())}`;
  });
}

// ---- lista de docentes
async function viewProfessors({ params }) {
  setTitle('Docentes');
  const b = state.boot;
  const areas = [...new Set(b.professors.flatMap((p) => p.areas))].sort((x, y) => x.localeCompare(y));
  main.innerHTML = `<section class="page">
    <div class="page-head"><p class="eyebrow">Diretório</p><h1>Docentes</h1><p class="lead-sm">Corpo docente do DCC/UFMG e quem mais lecionou disciplinas do departamento desde ${esc(b.terms[0])}.</p></div>
    <form class="filters" id="filters" role="search">
      <label class="field grow"><span class="sr-only">Buscar</span><input type="search" name="q" placeholder="Nome ou área de pesquisa…" value="${esc(params.get('q') || '')}"></label>
      <label class="field"><span class="sr-only">Área</span><select name="area"><option value="">Todas as áreas</option>${areas.map((a) => `<option ${params.get('area') === a ? 'selected' : ''}>${esc(a)}</option>`).join('')}</select></label>
      <label class="field"><span class="sr-only">Situação</span><select name="status">
        <option value="atuais" ${!params.get('status') || params.get('status') === 'atuais' ? 'selected' : ''}>Em atividade</option>
        <option value="agora" ${params.get('status') === 'agora' ? 'selected' : ''}>Lecionando em ${esc(b.latestTerm)}</option>
        <option value="todos" ${params.get('status') === 'todos' ? 'selected' : ''}>Todos (inclui aposentados e externos)</option>
      </select></label>
      <label class="field"><span class="sr-only">Ordenar</span><select name="sort">
        <option value="name">Nome (A–Z)</option>
        <option value="score" ${params.get('sort') === 'score' ? 'selected' : ''}>Melhor avaliados</option>
        <option value="count" ${params.get('sort') === 'count' ? 'selected' : ''}>Mais avaliados</option>
        <option value="easy" ${params.get('sort') === 'easy' ? 'selected' : ''}>Mais fácil de aprender</option>
        <option value="pass" ${params.get('sort') === 'pass' ? 'selected' : ''}>Mais fácil de passar</option>
      </select></label>
    </form>
    <p class="result-count muted" id="result-count" aria-live="polite"></p>
    <div class="grid grid-3" id="prof-grid"></div>
  </section>`;
  const form = $('#filters');
  const draw = () => {
    const fd = new FormData(form);
    const q = fold(fd.get('q'));
    const area = fd.get('area');
    const status = fd.get('status');
    const sort = fd.get('sort');
    let list = b.professors.filter((p) => {
      if (status === 'atuais' && !['ativo', 'voluntario', 'substituto'].includes(p.status)) return false;
      if (status === 'agora' && !p.teachingNow) return false;
      if (area && !p.areas.includes(area)) return false;
      if (q && !q.split(/\s+/).every((w) => fold(`${p.name} ${p.areas.join(' ')}`).includes(w))) return false;
      return true;
    });
    const by = {
      name: (x, y) => x.name.localeCompare(y.name),
      score: (x, y) => (y.stats?.score ?? -1) - (x.stats?.score ?? -1) || x.name.localeCompare(y.name),
      count: (x, y) => (y.stats?.count ?? 0) - (x.stats?.count ?? 0) || x.name.localeCompare(y.name),
      easy: (x, y) => (x.stats?.difficulty ?? 9) - (y.stats?.difficulty ?? 9) || x.name.localeCompare(y.name),
      pass: (x, y) => (x.stats?.passing ?? 9) - (y.stats?.passing ?? 9) || x.name.localeCompare(y.name),
    }[sort];
    list = list.sort(by);
    $('#result-count').textContent = plural(list.length, 'docente', 'docentes');
    $('#prof-grid').innerHTML = list.map(profCard).join('') || '<div class="empty-state"><p>Ninguém encontrado com esses filtros.</p></div>';
    const qs = new URLSearchParams();
    for (const [k, v] of fd) if (v && !(k === 'status' && v === 'atuais') && !(k === 'sort' && v === 'name')) qs.set(k, v);
    setQuery(qs);
  };
  form.addEventListener('input', draw);
  form.addEventListener('submit', (e) => e.preventDefault());
  draw();
}

// ---- página do docente
async function viewProfessor({ args, isCurrent }) {
  const [slug] = args;
  main.innerHTML = '<div class="loading-screen"><span class="spinner"></span></div>';
  const data = await api(`/api/professors/${encodeURIComponent(slug)}`);
  if (!isCurrent()) return;
  const p = data.professor;
  const boot = state.prof.get(slug) || {};
  setTitle(p.name);
  // contatos e links: cartão de informações ao lado do nome (linhas rótulo → valor)
  const ext = (href, label) => `<a href="${safeHref(href)}" target="_blank" rel="noreferrer">${label} ↗</a>`;
  const facts = [
    p.room && ['Sala', esc(p.room)],
    p.phone && ['Ramal', esc(p.phone)],
    p.email && ['E-mail', `<a href="${safeHref(`mailto:${p.email}`)}">${esc(p.email)}</a>`],
    (p.homepage || p.lattes || p.scholar || p.dccUrl) && ['Links', [p.homepage && ext(p.homepage, 'Página pessoal'), p.lattes && ext(p.lattes, 'Lattes'),
      p.scholar && ext(p.scholar, 'Scholar'), p.dccUrl && ext(p.dccUrl, 'DCC')].filter(Boolean).join(' ')],
  ].filter(Boolean);
  const terms = [...new Set(data.history.map((h) => h.term))].sort();
  const allTerms = data.termsTaught || terms; // contagem inclui semestres só com disciplinas hoje extintas
  const current = data.history.filter((h) => h.term === state.boot.latestTerm);
  const reviewCounts = {};
  data.reviews.forEach((r) => { reviewCounts[r.course] = (reviewCounts[r.course] || 0) + 1; });
  const activeCourses = data.courses.filter((c) => c.active || c.stats);
  const oldCourses = data.courses.filter((c) => !c.active && !c.stats);
  const published = state.boot.offerPublished;
  main.innerHTML = `<section class="page">
    <nav class="crumbs" aria-label="Trilha"><a href="#/professores">Docentes</a> <span>›</span> ${esc(p.name)}</nav>
    ${staleNotice(data.community ? `Sabe o que ${esc(firstName(p.name))} está dando agora? <a href="#comunidade">Informe abaixo</a>.` : '')}
    <header class="profile">
      ${avatar(p, 'xl')}
      <div class="profile-main">
        <h1>${esc(p.name)}</h1>
        <ul class="meta-row">
          ${data.summary.count ? `<li><svg class="ico ico-fill"><use href="#i-star"/></svg><b>${fmt(data.summary.avg.overall)}</b> de 5 · ${plural(data.summary.count, 'avaliação', 'avaliações')}</li>` : '<li>Ainda sem avaliações</li>'}
          ${boot.teachingNow ? `<li><span class="live-dot" aria-hidden="true"></span>${published ? 'Lecionando em' : 'Na oferta de'} ${esc(state.boot.latestTerm)}</li>` : ''}
          ${allTerms.length ? `<li><svg class="ico"><use href="#i-calendar"/></svg>${plural(allTerms.length, 'semestre', 'semestres')} na oferta (${esc(allTerms[0])}–${esc(allTerms[allTerms.length - 1])})</li>` : ''}
          <li>${STATUS_LABEL[p.status]}</li>
        </ul>
        ${p.areas?.length ? `<div class="chips">${p.areas.map((a) => `<a class="chip" href="#/professores?area=${encodeURIComponent(a)}&status=todos">${esc(a)}</a>`).join('')}</div>` : ''}
        <div class="profile-cta"><a class="btn btn-lg" href="#/avaliar?professor=${slug}"><svg class="ico"><use href="#i-pen"/></svg>Avaliar ${esc(firstName(p.name))}</a></div>
      </div>
      ${facts.length ? `<dl class="facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>` : ''}
    </header>

    ${summaryBlock(data.summary)}
    ${evolutionBlock(data.summary)}

    ${communityBlock(data.community, { mode: 'prof', subject: firstName(p.name) })}

    ${profOpportunities(data.opportunities, firstName(p.name))}

    ${current.length ? `<section class="block"><h2>Turmas em ${esc(state.boot.latestTerm)}${published ? '' : ' <small class="muted">· última oferta oficial</small>'}</h2>
      <ul class="turmas">${current.map((h) => {
        const c = state.course.get(h.course);
        return `<li><a href="#/disciplina/${encodeURIComponent(h.course)}"><b>${esc(c?.name || h.course)}</b></a> <span class="code">${esc(c?.kind === 'topico' ? courseCode(c) : (h.codes || []).join(' / '))}</span> ${fmtSchedule(h.schedule)}${h.plan ? ` <a class="small" href="${safeHref(h.plan)}" target="_blank" rel="noreferrer">plano de ensino ↗</a>` : ''}</li>`;
      }).join('')}</ul></section>` : ''}

    <section class="block">
      <div class="section-head"><h2>Disciplinas</h2><span class="muted small">segundo a oferta oficial do DCC</span></div>
      ${activeCourses.length ? `<div class="table-wrap"><table class="table">
        <thead><tr><th>Disciplina</th><th>Semestres</th><th>Geral</th><th>Didática</th><th title="Dificuldade para aprender">Aprender</th><th title="Dificuldade para passar">Passar</th><th>Carga</th><th></th></tr></thead>
        <tbody>${activeCourses.map((c) => `<tr>
          <td><a href="#/disciplina/${encodeURIComponent(c.id)}"><b>${esc(c.name)}</b></a><br><span class="code">${esc(courseCode(c))}</span></td>
          <td>${c.terms.length ? `${c.terms.length}× <span class="muted small">(último ${esc(c.terms[c.terms.length - 1])})</span>` : '<span class="muted small">—</span>'}</td>
          <td>${pill(c.stats?.overall)} <span class="muted small">${c.stats ? c.stats.count : ''}</span></td>
          <td>${pill(c.stats?.didactics)}</td>${['difficulty', 'passing', 'workload'].map((k) => `<td>${pill(c.stats?.[k], { crit: critByKey(k) })}</td>`).join('')}
          <td>${c.active ? `<a class="link-btn" href="#/avaliar?professor=${slug}&course=${encodeURIComponent(c.id)}">Avaliar</a>` : '<span class="small muted">fora de oferta</span>'}</td>
        </tr>`).join('')}</tbody></table></div>` : '<p class="muted">Nenhuma disciplina em oferta nos últimos semestres.</p>'}
      ${oldCourses.length ? `<p class="small muted old-courses">Também lecionou, em currículos ou temas que não são mais ofertados: ${oldCourses.map((c) => `<a href="#/disciplina/${encodeURIComponent(c.id)}">${esc(c.name)}</a> (${esc(c.terms[c.terms.length - 1])})`).join(', ')}.</p>` : ''}
    </section>

    ${terms.length ? `<details class="block history"><summary><h2>Histórico de turmas</h2><span class="muted small">${plural(data.history.length, 'turma', 'turmas')}</span></summary>
      <div class="timeline">${terms.slice().reverse().map((t) => `<div class="timeline-term"><b>${esc(t)}</b><ul>${data.history.filter((h) => h.term === t).map((h) => {
        const c = state.course.get(h.course);
        const co = h.with.map((s) => state.prof.get(s)).filter(Boolean);
        return `<li><a href="#/disciplina/${encodeURIComponent(h.course)}">${esc(c?.name || h.course)}</a> ${fmtSchedule(h.schedule)}${co.length ? ` <span class="muted small">com ${co.map((x) => `<a href="#/professor/${x.slug}">${esc(firstNameLast(x.name))}</a>`).join(', ')}</span>` : ''}${h.plan ? ` <a class="small" href="${safeHref(h.plan)}" target="_blank" rel="noreferrer">plano ↗</a>` : ''}</li>`;
      }).join('')}</ul></div>`).join('')}</div></details>` : ''}

    ${reviewsSection({
      reviews: data.reviews, show: 'course', subjectName: firstName(p.name), ctaHref: `#/avaliar?professor=${slug}`, filterLabel: 'Disciplina',
      filterOptions: Object.entries(reviewCounts).map(([id, count]) => ({ value: id, label: state.course.get(id)?.name || id, count })),
    })}
  </section>`;
  const profList = wireReviewList(data.reviews, 'course');
  wireEvolution(data.summary, { onPick: (t) => profList?.showTerm(t) });
  wireCommunity({ professor: slug, mode: 'prof', reload: () => router() });
}

// ---- lista de disciplinas
async function viewCourses({ params }) {
  setTitle('Disciplinas');
  const b = state.boot;
  main.innerHTML = `<section class="page">
    <div class="page-head"><p class="eyebrow">Catálogo</p><h1>Disciplinas</h1><p class="lead-sm">Disciplinas ofertadas pelo DCC de ${esc(b.activeTerms?.[0] || b.terms[0])} a ${esc(b.latestTerm)}. Versões de currículo (ex.: Estruturas de Dados DCC205 → DCC221) aparecem como uma só; tópicos especiais aparecem separados por tema.</p></div>
    <form class="filters" id="filters" role="search">
      <label class="field grow"><span class="sr-only">Buscar</span><input type="search" name="q" placeholder="Nome ou código (DCC206)…" value="${esc(params.get('q') || '')}"></label>
      <label class="field"><span class="sr-only">Tipo</span><select name="kind">
        <option value="">Regulares e tópicos</option>
        <option value="regular" ${params.get('kind') === 'regular' ? 'selected' : ''}>Só disciplinas regulares</option>
        <option value="topico" ${params.get('kind') === 'topico' ? 'selected' : ''}>Só tópicos especiais</option>
      </select></label>
      <label class="check"><input type="checkbox" name="now" value="1" ${params.get('now') ? 'checked' : ''}> Ofertadas em ${esc(b.latestTerm)}</label>
      <label class="field"><span class="sr-only">Ordenar</span><select name="sort">
        <option value="code">Código</option>
        <option value="name" ${params.get('sort') === 'name' ? 'selected' : ''}>Nome (A–Z)</option>
        <option value="score" ${params.get('sort') === 'score' ? 'selected' : ''}>Melhor avaliadas</option>
        <option value="count" ${params.get('sort') === 'count' ? 'selected' : ''}>Mais avaliadas</option>
        <option value="frequent" ${params.get('sort') === 'frequent' ? 'selected' : ''}>Mais ofertadas</option>
      </select></label>
    </form>
    <p class="result-count muted" id="result-count" aria-live="polite"></p>
    <div class="grid grid-2" id="course-grid"></div>
  </section>`;
  const form = $('#filters');
  const draw = () => {
    const fd = new FormData(form);
    const q = fold(fd.get('q'));
    const kind = fd.get('kind');
    const now = fd.get('now');
    const sort = fd.get('sort');
    let list = b.courses.filter((c) => (!kind || c.kind === kind) && (!now || c.offeredNow) && c.active
      && (!q || q.split(/\s+/).every((w) => fold(`${c.name} ${c.codes.join(' ')}`).includes(w))));
    const by = {
      code: (x, y) => (x.kind === y.kind ? (x.kind === 'regular' ? x.id.localeCompare(y.id) : x.name.localeCompare(y.name)) : x.kind === 'regular' ? -1 : 1),
      name: (x, y) => x.name.localeCompare(y.name),
      score: (x, y) => (y.stats?.score ?? -1) - (x.stats?.score ?? -1) || x.name.localeCompare(y.name),
      count: (x, y) => (y.stats?.count ?? 0) - (x.stats?.count ?? 0) || x.name.localeCompare(y.name),
      frequent: (x, y) => y.terms.length - x.terms.length || x.name.localeCompare(y.name),
    }[sort];
    list = list.sort(by);
    $('#result-count').textContent = plural(list.length, 'disciplina', 'disciplinas');
    $('#course-grid').innerHTML = list.map(courseRow).join('') || '<div class="empty-state"><p>Nenhuma disciplina com esses filtros.</p></div>';
    const qs = new URLSearchParams();
    for (const [k, v] of fd) if (v && !(k === 'sort' && v === 'code')) qs.set(k, v);
    setQuery(qs);
  };
  form.addEventListener('input', draw);
  form.addEventListener('submit', (e) => e.preventDefault());
  draw();
}

// ---- página da disciplina
async function viewCourse({ args, isCurrent }) {
  const [id] = args;
  main.innerHTML = '<div class="loading-screen"><span class="spinner"></span></div>';
  let data;
  try {
    data = await api(`/api/courses/${encodeURIComponent(id)}`);
  } catch (err) {
    // disciplina extinta: leva para a que a substituiu no currículo atual
    if (err.data?.successor && isCurrent()) { toast(err.message); location.replace(`#/disciplina/${encodeURIComponent(err.data.successor)}`); return; }
    throw err;
  }
  if (!isCurrent()) return;
  const c = data.course;
  if (c.id !== id) history.replaceState(null, '', `#/disciplina/${encodeURIComponent(c.id)}`); // código antigo -> versão atual
  setTitle(c.name);
  const published = state.boot.offerPublished;
  const otherCodes = c.kind === 'regular' ? c.codes.slice(1) : [];
  const latest = state.boot.latestTerm;
  const current = data.history.filter((h) => h.term === latest);
  const reviewCounts = {};
  data.reviews.forEach((r) => { reviewCounts[r.professor] = (reviewCounts[r.professor] || 0) + 1; });
  const crit = state.boot.criteria;
  main.innerHTML = `<section class="page">
    <nav class="crumbs" aria-label="Trilha"><a href="#/disciplinas">Disciplinas</a> <span>›</span> ${esc(c.name)}</nav>
    ${c.active ? staleNotice(data.community ? `Sabe quem está dando esta disciplina agora? <a href="#comunidade">Informe abaixo</a>.` : '') : `<div class="notice notice-old" role="note"><svg class="ico" aria-hidden="true"><use href="#i-info"/></svg><div>
      <b>Fora de oferta desde ${esc(c.terms[c.terms.length - 1])}.</b> Esta disciplina (ou este tema) não aparece nas ofertas do DCC dos últimos dois anos — provavelmente é de um currículo antigo. O histórico e as avaliações antigas continuam aqui, mas ela não recebe novas avaliações.</div></div>`}
    <header class="course-head">
      <div>
        <span class="code code-lg">${esc(c.kind === 'regular' ? c.codes[0] : courseCode(c))}</span>
        <h1>${esc(c.name)}</h1>
        ${otherCodes.length ? `<p class="small muted versions">Inclui ${otherCodes.length === 1 ? 'a versão' : 'as versões'} ${otherCodes.map((x) => `<span class="code">${esc(x)}</span>`).join(' ')} — mesmo conteúdo com código de outro currículo ou curso.</p>` : ''}
        <div class="badges">
          ${c.kind === 'topico' ? '<span class="badge">Tópico especial</span>' : ''}
          <span class="badge">${plural(c.terms.length, 'semestre', 'semestres')} ofertada (${esc(c.terms[0])}–${esc(c.terms[c.terms.length - 1])})</span>
          ${current.length ? `<span class="badge badge-live">${plural(current.length, 'turma', 'turmas')} em ${esc(latest)}</span>` : ''}
        </div>
      </div>
      ${c.active ? `<div class="profile-cta"><a class="btn" href="#/avaliar?course=${encodeURIComponent(c.id)}">Avaliar esta disciplina</a></div>` : ''}
    </header>

    ${c.active || data.summary.count ? summaryBlock(data.summary, { subject: 'disciplina' }) : ''}
    ${evolutionBlock(data.summary)}

    ${c.active ? communityBlock(data.community, { mode: 'course' }) : ''}

    ${examsBlock(data.exams)}

    ${current.length ? `<section class="block"><h2>Turmas em ${esc(latest)}${published ? '' : ' <small class="muted">· última oferta oficial</small>'}</h2>
      <div class="turma-cards">${current.map((h) => `<div class="turma-card">
        <div class="turma-profs">${h.professors.length ? h.professors.map((s) => { const p = state.prof.get(s); const st = data.professors.find((x) => x.slug === s)?.stats; return p ? `<div><a href="#/professor/${s}">${avatar(p, 'sm')} ${esc(p.name)}</a> ${pill(st?.overall, { title: st ? `${st.count} avaliação(ões) nesta disciplina` : 'sem avaliações nesta disciplina' })}</div>` : ''; }).join('') : unidentified(h, '<span class="muted">Docente a definir</span>')}</div>
        <div>${fmtSchedule(h.schedule)}${h.class ? ` <span class="muted small">turma ${esc(h.class)}</span>` : ''}${otherCodes.length ? ` <span class="code">${esc(h.codes.join(' / '))}</span>` : ''}</div>
        ${h.plan ? `<a class="small" href="${safeHref(h.plan)}" target="_blank" rel="noreferrer">Plano de ensino ↗</a>` : ''}
      </div>`).join('')}</div>
      <p class="small muted">Confirme sala e vagas no <a href="https://sistemas.ufmg.br/siga" target="_blank" rel="noreferrer">SIGA ↗</a>. <a href="#/oferta?q=${encodeURIComponent(c.codes[0])}">Adicionar à minha grade →</a></p>
    </section>` : ''}

    <section class="block">
      <div class="section-head"><h2>Comparar docentes</h2><span class="muted small">notas apenas desta disciplina</span></div>
      ${data.professors.length ? `<div class="table-wrap"><table class="table compare" id="compare">
        <thead><tr><th data-k="name">Docente</th><th data-k="sections" class="num">Turmas</th><th data-k="last">Último</th>
          ${crit.map((k) => `<th data-k="${k.key}" class="num" title="${esc(k.hint)}">${esc(k.short || k.label)}</th>`).join('')}
          <th data-k="retake" class="num">Faria de novo</th><th data-k="count" class="num">Aval.</th></tr></thead>
        <tbody></tbody></table></div>
        <p class="small muted">Clique no cabeçalho para ordenar. Dificuldade e carga: valores maiores = mais pesada.</p>`
        : '<p class="muted">Nenhum docente identificado na oferta para esta disciplina.</p>'}
    </section>

    <details class="block history"><summary><h2>Histórico de ofertas</h2><span class="muted small">${plural(data.history.length, 'turma', 'turmas')}</span></summary>
      <div class="timeline">${[...new Set(data.history.map((h) => h.term))].sort().reverse().map((t) => `<div class="timeline-term"><b>${esc(t)}</b><ul>${data.history.filter((h) => h.term === t).map((h) => `<li>${h.professors.map((s) => { const p = state.prof.get(s); return p ? `<a href="#/professor/${s}">${esc(firstNameLast(p.name))}</a>` : ''; }).join(', ') || unidentified(h, '<span class="muted" title="A página oficial de oferta não traz o docente desta turma">docente não informado pelo DCC</span>')} ${fmtSchedule(h.schedule)}${otherCodes.length ? ` <span class="code">${esc(h.codes.join(' / '))}</span>` : ''}${h.plan ? ` <a class="small" href="${safeHref(h.plan)}" target="_blank" rel="noreferrer">plano ↗</a>` : ''}</li>`).join('')}</ul></div>`).join('')}</div>
    </details>

    ${reviewsSection({
      reviews: data.reviews, show: 'prof', subjectName: 'esta disciplina', ctaHref: c.active ? `#/avaliar?course=${encodeURIComponent(c.id)}` : '', filterLabel: 'Docente',
      filterOptions: Object.entries(reviewCounts).map(([slug, count]) => ({ value: slug, label: state.prof.get(slug)?.name || slug, count })),
    })}
  </section>`;
  const courseList = wireReviewList(data.reviews, 'prof');
  wireEvolution(data.summary, { onPick: (t) => courseList?.showTerm(t) });
  wireCommunity({ course: c.id, mode: 'course', reload: () => router() });
  const table = $('#compare');
  if (table) {
    let key = 'last';
    let dir = -1;
    const val = (p, k) => (k === 'name' ? p.name : k === 'sections' ? p.sections : k === 'last' ? (p.terms[p.terms.length - 1] || '') : p.stats?.[k] ?? null);
    const draw = () => {
      const rows = data.professors.slice().sort((a, b) => {
        const x = val(a, key); const y = val(b, key);
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (typeof x === 'string' ? x.localeCompare(y) : x - y) * dir;
      });
      $('tbody', table).innerHTML = rows.map((p) => `<tr class="${p.terms.includes(latest) ? 'row-live' : ''}">
        <td><a class="prof-inline" href="#/professor/${p.slug}">${avatar(p, 'sm')} <span>${esc(p.name)}</span></a>${p.terms.includes(latest) ? ` <span class="badge badge-live">${esc(latest)}</span>` : ''}</td>
        <td class="num">${p.sections || '—'}</td><td>${esc(p.terms[p.terms.length - 1] || '—')}</td>
        ${crit.map((k) => `<td class="num">${pill(p.stats?.[k.key], { crit: k })}</td>`).join('')}
        <td class="num">${p.stats?.retake != null ? `${p.stats.retake}%` : '—'}</td><td class="num">${p.stats?.count || 0}</td></tr>`).join('');
      $$('th', table).forEach((th) => th.setAttribute('aria-sort', th.dataset.k === key ? (dir > 0 ? 'ascending' : 'descending') : 'none'));
    };
    $('thead', table).addEventListener('click', (e) => {
      const th = e.target.closest('th');
      if (!th) return;
      if (key === th.dataset.k) dir = -dir; else { key = th.dataset.k; dir = key === 'name' || key === 'difficulty' || key === 'passing' || key === 'workload' ? 1 : -1; }
      draw();
    });
    draw();
  }
}

// ---- oferta e montador de grade
let offerClick = null;
const SLOTS = ['07:30', '09:25', '11:10', '13:00', '14:55', '17:00', '19:00', '20:55'];
const shiftOf = (start) => (start < '12:00' ? 'manha' : start < '18:50' ? 'tarde' : 'noite');
const toMin = (t) => { const [h, m] = t.split(':').map(Number); return h * 60 + m; };

// currículos dos cursos (data/curriculos.json): carregados uma vez; sem o arquivo, a grade funciona como antes
async function loadCurricula() {
  if (!state.curricula) state.curricula = await api('/api/curriculos').then((d) => d.curriculos || []).catch(() => []);
  return state.curricula;
}
const ordinal = (n) => `${n}º`;

async function viewOffer({ params, isCurrent }) {
  const b = state.boot;
  const term = b.terms.includes(params.get('term')) ? params.get('term') : b.latestTerm;
  setTitle(`Oferta ${term}`);
  main.innerHTML = '<div class="loading-screen"><span class="spinner"></span></div>';
  const [data, curricula] = await Promise.all([api(`/api/offerings?term=${encodeURIComponent(term)}`), loadCurricula()]);
  if (!isCurrent()) return;
  // curso e período da pessoa: só neste navegador, nunca enviados ao servidor nem ligados às avaliações
  const profileKey = 'dcchub-curso';
  let profile = store.get(profileKey, null);
  if (profile && !curricula.some((c) => c.id === profile.id)) profile = null;
  // horários adicionados à mão (disciplinas sem turma do DCC, como as de outros departamentos)
  const extraKey = `avalie-grade-extra-${term}`;
  let extras = store.get(extraKey, []);
  const key = `avalie-grade-${term}`;
  let picked = new Set(store.get(key, []));
  const icsKey = `avalie-aulas-${term}`; // datas de início/fim das aulas, só neste navegador
  let icsDates = store.get(icsKey, {});
  let icsOpen = false;
  const offerings = data.offerings.map((o, i) => ({ ...o, uid: `${o.course}|${o.class || ''}|${o.professors.join(',')}|${o.schedule.map(fmtSlot).join(',')}|${i}` }));
  // ids estáveis entre visitas: sem o índice quando possível
  offerings.forEach((o) => { o.key = o.uid.replace(/\|\d+$/, ''); });
  main.innerHTML = `<section class="page wide">
    <div class="page-head"><p class="eyebrow">Planejamento</p><h1>Oferta ${esc(term)}</h1>
      <p class="lead-sm">Turmas publicadas pelo DCC com a nota de cada docente <em>naquela disciplina</em>. Marque turmas para montar sua grade e ver conflitos de horário.</p></div>
    ${term === b.latestTerm ? staleNotice('Use esta grade como referência e confira no SIGA.') : ''}
    ${viaSigaa(term) ? `<div class="notice notice-old" role="note"><svg class="ico" aria-hidden="true"><use href="#i-info"/></svg><div><b>Oferta de ${esc(term)} obtida do mapa de oferta do SIGAA.</b> O DCC ainda não publicou a página deste semestre; quando publicar, ela passa a ser a fonte. Confirme sala e vagas no SIGA.</div></div>` : ''}
    ${data.offerings.length && data.offerings.every((o) => !o.professors.length && !o.unresolved?.length) ? `<div class="notice notice-old" role="note"><svg class="ico" aria-hidden="true"><use href="#i-info"/></svg><div><b>A oferta oficial de ${esc(term)} não lista docentes.</b> A página publicada pelo DCC para este semestre traz só disciplina, turma e horário.</div></div>` : ''}
    ${curricula.length ? '<section class="my-course" id="my-course" aria-label="Meu curso"></section>' : ''}
    <form class="filters" id="filters" role="search">
      <label class="field"><span class="sr-only">Semestre</span><select name="term">${b.terms.slice().reverse().map((t) => `<option ${t === term ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></label>
      <label class="field grow"><span class="sr-only">Buscar</span><input type="search" name="q" placeholder="Disciplina, código ou docente…" value="${esc(params.get('q') || '')}"></label>
      <label class="field"><span class="sr-only">Turno</span><select name="shift"><option value="">Qualquer turno</option><option value="manha">Manhã</option><option value="tarde">Tarde</option><option value="noite">Noite</option></select></label>
      <label class="field"><span class="sr-only">Dia</span><select name="day"><option value="">Qualquer dia</option>${[1, 2, 3, 4, 5, 6].map((d) => `<option value="${d}">${DAYS[d]}</option>`).join('')}</select></label>
      <label class="check"><input type="checkbox" name="fits" value="1"> Só o que cabe na grade</label>
    </form>
    <div class="offer-layout">
      <div>
        <p class="result-count muted" id="result-count" aria-live="polite"></p>
        <div id="offer-list" class="offer-list"></div>
      </div>
      <aside class="planner" aria-label="Minha grade">
        <div class="planner-head"><h2>Minha grade</h2><button class="link-btn" id="clear-grade" type="button">Limpar</button></div>
        <div id="planner"></div>
      </aside>
    </div>
  </section>`;
  const form = $('#filters');
  const pickedOfferings = () => offerings.filter((o) => picked.has(o.key));
  const extraItems = () => extras.map((e) => ({ key: `x:${e.id}`, course: `x:${e.id}`, extra: e, schedule: e.schedule, professors: [] }));
  const allChosen = () => [...pickedOfferings(), ...extraItems()];
  const conflicts = (o, list) => list.filter((p) => p.key !== o.key && p.schedule.some((a) => o.schedule.some((x) => x.day === a.day && toMin(x.start) < toMin(a.end) && toMin(a.start) < toMin(x.end))));

  const drawPlanner = () => {
    const list = allChosen();
    const box = $('#planner');
    if (!list.length) { box.innerHTML = '<p class="muted small">Clique em “+ grade” nas turmas para montar seu horário. A grade fica salva só neste navegador.</p>'; return; }
    const days = [1, 2, 3, 4, 5, ...(list.some((o) => o.schedule.some((s) => s.day === 6)) ? [6] : [])];
    const starts = [...new Set([...SLOTS, ...list.flatMap((o) => o.schedule.map((s) => s.start))])].sort();
    const used = starts.filter((t) => list.some((o) => o.schedule.some((s) => s.start <= t && t < s.end)));
    const rows = used.length ? used : SLOTS;
    const hasConflict = list.some((o) => conflicts(o, list).length);
    const hue = new Map([...new Set(list.map((o) => o.course))].map((c, i) => [c, Math.round((i * 137.5 + 95) % 360)]));
    box.innerHTML = `<div class="week" style="--cols:${days.length}">
      <div></div>${days.map((d) => `<div class="week-day">${DAYS[d]}</div>`).join('')}
      ${rows.map((t) => `<div class="week-time">${t}</div>${days.map((d) => {
        const here = list.filter((o) => o.schedule.some((s) => s.day === d && s.start <= t && t < s.end));
        return `<div class="week-cell ${here.length > 1 ? 'clash' : ''}">${here.map((o) => `<span class="week-item" style="--h:${hue.get(o.course)}" title="${esc(o.extra ? o.extra.title : state.course.get(o.course)?.name || '')}">${esc(shortCourse(o))}</span>`).join('')}</div>`;
      }).join('')}`).join('')}
    </div>
    ${hasConflict ? '<p class="warn small">⚠ Há turmas com horários sobrepostos (em vermelho).</p>' : ''}
    <ul class="planner-list">${list.map((o) => { if (o.extra) return `<li><span class="dot" style="--h:${hue.get(o.course)}"></span><span class="planner-extra">${esc(o.extra.title)}</span> <span class="muted small">horário manual</span><button class="link-btn unpick" data-unextra="${esc(o.extra.id)}" aria-label="Remover">✕</button></li>`;
      const c = state.course.get(o.course); return `<li><span class="dot" style="--h:${hue.get(o.course)}"></span><a href="#/disciplina/${encodeURIComponent(o.course)}">${esc(c?.name || o.course)}</a> <span class="muted small">${esc(o.professors.map((s) => firstNameLast(state.prof.get(s)?.name || s)).join(', '))}</span><button class="link-btn unpick" data-unpick="${esc(o.key)}" aria-label="Remover">✕</button></li>`; }).join('')}</ul>
    <p class="small muted">${plural(list.length, 'disciplina', 'disciplinas')} · ${list.reduce((a, o) => a + o.schedule.reduce((x, s) => x + (toMin(s.end) - toMin(s.start)), 0), 0) / 60 | 0}h/semana em sala</p>
    <details class="ics-export" id="ics-export" ${icsOpen ? 'open' : ''}><summary>Exportar para o calendário (.ics)</summary>
      <form id="ics-form" class="ics-form">
        <div class="ics-dates">
          <label class="field"><span>Início das aulas</span><input type="date" name="start" required value="${esc(icsDates.start || '')}"></label>
          <label class="field"><span>Fim das aulas</span><input type="date" name="end" required value="${esc(icsDates.end || '')}"></label>
        </div>
        <button class="btn btn-sm" type="submit">Baixar ${esc(term)}.ics</button>
        <p class="small muted">Use as datas do calendário acadêmico da UFMG. Cada horário vira um evento semanal; feriados e recessos não são removidos. O arquivo é gerado aqui, no seu navegador.</p>
      </form>
    </details>`;
    $('#ics-export').addEventListener('toggle', (e) => { icsOpen = e.target.open; });
    $('#ics-form').addEventListener('input', (e) => { icsDates = { ...icsDates, [e.target.name]: e.target.value }; store.set(icsKey, icsDates); });
    $('#ics-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const fd = new FormData(e.target); // lê do formulário (preenchimento automático não dispara 'input')
      icsDates = { start: fd.get('start'), end: fd.get('end') };
      store.set(icsKey, icsDates);
      exportIcs(list);
    });
  };

  // grade -> .ics, gerado no navegador (static/ics.js): a grade continua sem passar pelo servidor
  const exportIcs = (list) => {
    try {
      const items = list.map((o) => {
        const c = state.course.get(o.course);
        const profs = o.professors.map((s) => state.prof.get(s)?.name || s).join(', ');
        return {
          uid: `${term}-${o.key}`,
          title: o.extra ? (o.extra.code ? `${o.extra.code} — ${o.extra.title}` : o.extra.title) : c ? `${c.codes[0]} — ${c.name}` : o.course,
          description: [o.class && `Turma ${o.class}`, profs && `Docentes: ${profs}`, `Oferta ${term} (DCCHub). Confira a sala no SIGA.`].filter(Boolean).join('\n'),
          schedule: o.schedule,
        };
      });
      const { ics, events, skipped } = window.DCCHubICS.build(items, { ...icsDates, term });
      if (!events) { toast('Nenhuma turma da grade tem horário dentro dessas datas.', 'warn'); return; }
      const url = URL.createObjectURL(new Blob([ics], { type: 'text/calendar;charset=utf-8' }));
      const a = Object.assign(document.createElement('a'), { href: url, download: `grade-${term}.ics` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      toast(`${plural(events, 'horário semanal exportado', 'horários semanais exportados')}${skipped ? ` · ${plural(skipped, 'turma sem horário ficou', 'turmas sem horário ficaram')} de fora` : ''}.`);
    } catch (err) { toast(err.message, 'error'); }
  };

  const draw = () => {
    const fd = new FormData(form);
    if (fd.get('term') !== term) { const qs = new URLSearchParams({ term: fd.get('term') }); setQuery(qs, { replace: false }); return; }
    const q = fold(fd.get('q'));
    const shift = fd.get('shift');
    const day = Number(fd.get('day'));
    const fits = fd.get('fits');
    const chosen = allChosen();
    const list = offerings.filter((o) => {
      const c = state.course.get(o.course);
      const hay = fold(`${c?.name} ${o.codes.join(' ')} ${o.professors.map((s) => state.prof.get(s)?.name).join(' ')}`);
      if (q && !q.split(/\s+/).every((w) => hay.includes(w))) return false;
      if (shift && !o.schedule.some((s) => shiftOf(s.start) === shift)) return false;
      if (day && !o.schedule.some((s) => s.day === day)) return false;
      if (fits && !picked.has(o.key) && conflicts(o, chosen).length) return false;
      return true;
    });
    const groups = new Map();
    list.forEach((o) => groups.set(o.course, [...(groups.get(o.course) || []), o]));
    const sorted = [...groups].sort(([a], [b2]) => {
      const ca = state.course.get(a); const cb = state.course.get(b2);
      return (ca.kind === cb.kind ? 0 : ca.kind === 'regular' ? -1 : 1) || (ca.kind === 'regular' ? a.localeCompare(b2) : ca.name.localeCompare(cb.name));
    });
    const courseCard = ([cid, items], tag = '') => {
      const c = state.course.get(cid);
      return `<article class="offer-course">
        <header><a href="#/disciplina/${encodeURIComponent(cid)}"><span class="code">${esc(courseCode(c))}${tag ? ` <span class="offer-tag">${esc(tag)}</span>` : ''}</span><h3>${esc(c.name)}</h3></a>${c.stats ? `<span class="small muted">disciplina: ${pill(c.stats.overall)} (${c.stats.count})</span>` : ''}</header>
        <ul>${items.map((o) => {
          const clash = conflicts(o, chosen);
          const on = picked.has(o.key);
          return `<li class="offer-row ${on ? 'on' : ''} ${clash.length && !on ? 'has-clash' : ''}">
            <div class="offer-profs">${o.professors.length ? o.professors.map((s) => { const p = state.prof.get(s); const st = o.stats[s]; return p ? `<a href="#/professor/${s}" class="prof-inline">${avatar(p, 'sm')}<span>${esc(firstNameLast(p.name))}</span></a>${pill(st?.overall, { title: st ? `${st.count} avaliação(ões) de ${p.name} nesta disciplina` : 'Ainda sem avaliações nesta disciplina' })}` : ''; }).join(' ') : unidentified(o, '<span class="muted small" title="A página oficial de oferta não traz o docente desta turma">docente não informado pelo DCC</span>')}</div>
            <div class="offer-time">${fmtSchedule(o.schedule)}${o.class ? ` <span class="muted small">${esc(o.class)}</span>` : ''}</div>
            <div class="offer-actions">${o.plan ? `<a class="small" href="${safeHref(o.plan)}" target="_blank" rel="noreferrer" title="Plano de ensino">plano ↗</a>` : ''}
              <button type="button" class="btn btn-sm ${on ? '' : 'btn-ghost'}" data-pick="${esc(o.key)}" aria-pressed="${on}" ${!o.schedule.length && !on ? 'title="Sem horário informado"' : ''}>${on ? '✓ na grade' : '+ grade'}</button></div>
            ${clash.length && !on ? `<p class="clash-note small">Conflita com ${esc(clash.map((x) => shortCourse(x)).join(', '))}</p>` : ''}
          </li>`;
        }).join('')}</ul></article>`;
    };
    const cur = profile && curricula.find((c) => c.id === profile.id);
    let html;
    let shown = sorted;
    if (!cur) {
      html = sorted.map((g) => courseCard(g)).join('');
    } else {
      // papel de cada disciplina no currículo escolhido: do período da pessoa, obrigatória de outro período ou optativa
      const role = new Map();
      cur.periodos.forEach((per) => per.disciplinas.forEach((d) => {
        if (!d.disciplina) return;
        const mine = per.n === profile.periodo;
        if (!role.has(d.disciplina) || mine) role.set(d.disciplina, { kind: d.natureza === 'OP' ? 'op' : mine ? 'now' : 'ob', periodo: per.n });
      }));
      cur.optativas.forEach((d) => { if (d.disciplina && !role.has(d.disciplina)) role.set(d.disciplina, { kind: 'op' }); });
      const part = (kind) => sorted.filter(([cid]) => role.get(cid)?.kind === kind);
      const now = part('now');
      const ob = part('ob').sort(([a], [b2]) => role.get(a).periodo - role.get(b2).periodo);
      const op = part('op');
      const rest = sorted.filter(([cid]) => !role.has(cid));
      // disciplinas do período sem turma do DCC neste semestre (outros departamentos, ou não ofertadas): só sem filtros
      const filtering = q || shift || day || fits;
      const period = cur.periodos.find((per) => per.n === profile.periodo);
      const missing = filtering || !period ? [] : period.disciplinas.filter((d) => !d.disciplina || !groups.has(d.disciplina));
      const section = (title, body, count, note = '') => (body ? `<section class="offer-section"><h2>${title} <span class="count">${count}</span></h2>${note}${body}</section>` : '');
      const missingCard = (d, i) => {
        const outside = d.depto && d.depto !== 'DCC';
        const has = extras.some((e) => e.ref === `${cur.id}:${d.codigo || d.nome}`);
        return `<article class="offer-course offer-missing">
          <header><div><span class="code">${esc(d.codigo || 'código a definir')}</span><h3>${esc(d.nome)}</h3></div>
            <span class="small muted">${outside ? `fora do DCC (${esc(d.depto)}) · horário no SIGA` : `sem turma do DCC em ${esc(term)}`}</span></header>
          <div class="offer-missing-actions">${has ? '<span class="small muted">✓ na grade (horário manual)</span>'
            : `<button type="button" class="btn btn-sm btn-ghost" data-extra-add="${i}">+ horário</button><span class="small muted">Adicione o horário da sua turma para ver conflitos.</span>`}</div>
        </article>`;
      };
      html = section(`Do seu ${ordinal(profile.periodo)} período`, now.map((g) => courseCard(g)).join('') + missing.map(missingCard).join(''), now.length + missing.length,
        missing.length ? '' : '')
        + section('Obrigatórias de outros períodos', ob.map((g) => courseCard(g, `${ordinal(role.get(g[0]).periodo)} período`)).join(''), ob.length)
        + section('Optativas do seu curso', op.map((g) => courseCard(g, 'optativa')).join(''), op.length)
        + (profile.only ? '' : section('Outras disciplinas', rest.map((g) => courseCard(g)).join(''), rest.length));
      shown = profile.only ? [...now, ...ob, ...op] : sorted;
      view.missing = missing;
    }
    const turmas = shown.reduce((a, [, items]) => a + items.length, 0);
    $('#result-count').textContent = `${plural(turmas, 'turma', 'turmas')} em ${plural(shown.length, 'disciplina', 'disciplinas')}`;
    $('#offer-list').innerHTML = html || '<div class="empty-state"><p>Nenhuma turma com esses filtros.</p></div>';
    drawPlanner();
  };
  const view = { missing: [] };

  // ---- "Meu curso": curso/currículo, período e "só do meu curso" (salvo só neste navegador)
  const drawProfile = () => {
    const box = $('#my-course');
    if (!box) return;
    const cur = profile && curricula.find((c) => c.id === profile.id);
    box.innerHTML = `<div class="my-course-row">
      <label class="field grow"><span>Meu curso</span><select id="mc-curso">
        <option value="">Escolha seu curso…</option>
        ${curricula.map((c) => `<option value="${esc(c.id)}" ${cur?.id === c.id ? 'selected' : ''}>${esc(c.rotulo)}</option>`).join('')}
      </select></label>
      ${cur ? `<label class="field"><span>Período</span><select id="mc-periodo">${cur.periodos.map((per) => `<option value="${per.n}" ${per.n === profile.periodo ? 'selected' : ''}>${ordinal(per.n)} período</option>`).join('')}</select></label>
      <label class="check"><input type="checkbox" id="mc-only" ${profile.only ? 'checked' : ''}> Só do meu curso</label>` : ''}
    </div>`;
    $('#mc-curso').addEventListener('change', (e) => {
      const c = curricula.find((x) => x.id === e.target.value);
      profile = c ? { id: c.id, periodo: Math.min(profile?.periodo || 1, c.periodos.length), only: !!profile?.only } : null;
      store.set(profileKey, profile); drawProfile(); draw();
    });
    $('#mc-periodo')?.addEventListener('change', (e) => { profile = { ...profile, periodo: Number(e.target.value) }; store.set(profileKey, profile); draw(); });
    $('#mc-only')?.addEventListener('change', (e) => { profile = { ...profile, only: e.target.checked }; store.set(profileKey, profile); draw(); });
  };

  // ---- horário manual para disciplina sem turma do DCC
  const saveExtras = () => store.set(extraKey, extras);
  const openExtraDialog = (d) => {
    const cur = curricula.find((c) => c.id === profile.id);
    const dlg = $('#dialog');
    const row = (i) => `<div class="extra-slot"><label class="field"><span>Dia</span><select name="day${i}"><option value="">—</option>${[1, 2, 3, 4, 5, 6].map((n) => `<option value="${n}">${DAYS[n]}</option>`).join('')}</select></label>
      <label class="field"><span>Início</span><input type="time" name="start${i}" step="300"></label><label class="field"><span>Fim</span><input type="time" name="end${i}" step="300"></label></div>`;
    dlg.innerHTML = `<form method="dialog" class="dialog-body" id="extra-form">
      <h2>Horário de ${esc(d.nome)}</h2>
      <p class="muted">Copie do SIGA o horário da sua turma. Ele entra na sua grade e na checagem de conflitos, e fica salvo só neste navegador.</p>
      ${row(0)}${row(1)}${row(2)}
      <p class="warn small" id="extra-err" role="alert"></p>
      <div class="row-gap end"><button class="btn btn-ghost" value="cancel" formnovalidate>Cancelar</button><button class="btn" value="save">Pôr na grade</button></div>
    </form>`;
    dlg.showModal();
    $('#extra-form').addEventListener('submit', (ev) => {
      if (ev.submitter?.value !== 'save') return;
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const schedule = [0, 1, 2].map((i) => ({ day: Number(fd.get(`day${i}`)), start: fd.get(`start${i}`), end: fd.get(`end${i}`) }))
        .filter((x) => x.day || x.start || x.end);
      const bad = schedule.find((x) => !x.day || !x.start || !x.end || x.start >= x.end);
      if (!schedule.length || bad) { $('#extra-err').textContent = 'Preencha dia, início e fim (o fim depois do início) de cada horário.'; return; }
      extras = [...extras, { id: Math.random().toString(36).slice(2, 10), ref: `${cur.id}:${d.codigo || d.nome}`, code: d.codigo || '', title: d.nome, schedule }];
      saveExtras(); dlg.close(); draw();
    });
  };
  const save = () => store.set(key, [...picked]);
  if (offerClick) main.removeEventListener('click', offerClick);
  offerClick = (e) => {
    if (!$('#offer-list')) return;
    const pick = e.target.closest('[data-pick]');
    const unpick = e.target.closest('[data-unpick]');
    if (pick) { const k = pick.dataset.pick; if (picked.has(k)) picked.delete(k); else picked.add(k); save(); draw(); }
    if (unpick) { picked.delete(unpick.dataset.unpick); save(); draw(); }
    const addExtra = e.target.closest('[data-extra-add]');
    if (addExtra) openExtraDialog(view.missing[Number(addExtra.dataset.extraAdd)]);
    const unextra = e.target.closest('[data-unextra]');
    if (unextra) { extras = extras.filter((x) => x.id !== unextra.dataset.unextra); saveExtras(); draw(); }
    if (e.target.id === 'clear-grade' && (picked.size || extras.length) && confirm('Limpar a grade deste semestre?')) { picked = new Set(); extras = []; save(); saveExtras(); draw(); }
  };
  main.addEventListener('click', offerClick);
  form.addEventListener('input', draw);
  form.addEventListener('submit', (e) => e.preventDefault());
  drawProfile();
  draw();
}
function shortCourse(o) {
  if (o.extra) return o.extra.code || (o.extra.title.length > 14 ? `${o.extra.title.slice(0, 12)}…` : o.extra.title);
  const c = state.course.get(o.course);
  if (!c) return o.course;
  if (c.kind === 'topico') return c.name.length > 18 ? `${c.name.slice(0, 16)}…` : c.name;
  return c.codes[0];
}

// ---- formulário de avaliação
async function viewReviewForm({ params, isCurrent }) {
  const b = state.boot;
  const editId = params.get('edit');
  let existing = null;
  if (editId) {
    const me = await loadMe(true);
    existing = me.reviews.find((r) => String(r.id) === editId);
    if (!existing) throw Object.assign(new Error('Essa avaliação não pertence a este navegador. Se você a escreveu em outro lugar, use o código de recuperação em “Minhas avaliações”.'), { status: 404 });
  }
  if (!isCurrent()) return;
  setTitle(existing ? 'Editar avaliação' : 'Avaliar');
  const draftKey = 'avalie-draft';
  const draft = existing ? null : store.get(draftKey, null);
  const init = existing || {
    professor: params.get('professor') || draft?.professor || '',
    course: params.get('course') || draft?.course || '',
    term: draft?.term || '', ratings: draft?.ratings || {}, wouldRetake: draft?.wouldRetake ?? null,
    grade: draft?.grade || '', tags: draft?.tags || [], comment: draft?.comment || '',
  };
  if ((params.get('professor') && draft?.professor && params.get('professor') !== draft.professor) || (params.get('course') && draft?.course && params.get('course') !== draft.course)) {
    Object.assign(init, { term: '', ratings: {}, wouldRetake: null, grade: '', tags: [], comment: '' });
  }
  const profs = b.professors.slice().sort((x, y) => (['ativo', 'voluntario'].includes(y.status) - ['ativo', 'voluntario'].includes(x.status)) || x.name.localeCompare(y.name));
  const termsAll = (() => {
    const out = [];
    let [y, s] = b.currentTerm.split('.').map(Number);
    while (y > 2014) { out.push(`${y}.${s}`); if (s === 2) s = 1; else { s = 2; y -= 1; } }
    return out;
  })();
  main.innerHTML = `<section class="page narrow">
    <div class="page-head"><p class="eyebrow">${existing ? 'Editar' : 'Sua vez'}</p><h1>${existing ? 'Editar avaliação' : 'Como foi a turma?'}</h1>
      <p class="lead-sm">Anônimo e sem dados pessoais. Fale sobre a disciplina e as práticas de ensino — não sobre a vida pessoal de ninguém.</p></div>
    <form id="review-form" class="review-form" novalidate>
      <fieldset class="form-step"><legend><span>1</span> Turma</legend>
        <label class="field">Docente
          <input list="prof-list" id="f-prof" autocomplete="off" placeholder="Digite para buscar…" required value="${esc(state.prof.get(init.professor)?.name || '')}">
          <datalist id="prof-list">${profs.map((p) => `<option value="${esc(p.name)}">${p.status !== 'ativo' ? STATUS_LABEL[p.status] : ''}</option>`).join('')}</datalist>
          <small class="err" data-err="professor"></small>
        </label>
        <div class="two">
          <label class="field">Disciplina<select id="f-course" required></select><small class="hint" id="course-hint"></small><small class="err" data-err="course"></small></label>
          <label class="field">Semestre cursado<select id="f-term" required></select><small class="err" data-err="term"></small></label>
        </div>
      </fieldset>

      <fieldset class="form-step"><legend><span>2</span> Notas</legend>
        ${b.criteria.map((c) => `<div class="rating-field" role="radiogroup" aria-labelledby="lbl-${c.key}">
          <div class="rating-label"><b id="lbl-${c.key}">${esc(c.label)}</b><small>${esc(c.hint)}</small></div>
          <div class="rating-scale ${c.neutral ? 'neutral' : ''}">
            ${[1, 2, 3, 4, 5].map((n) => `<label><input type="radio" name="${c.key}" value="${n}" ${init.ratings?.[c.key] === n ? 'checked' : ''}><span>${n}</span></label>`).join('')}
          </div>
          <div class="rating-ends"><small>${esc(c.low)}</small><small>${esc(c.high)}</small></div>
          <small class="err" data-err="${c.key}"></small>
        </div>`).join('')}
      </fieldset>

      <fieldset class="form-step"><legend><span>3</span> Detalhes <small>(opcionais, mas ajudam muito)</small></legend>
        <div class="two">
          <div class="field"><span>Faria de novo com este docente?</span>
            <div class="segmented" role="radiogroup">
              <label><input type="radio" name="retake" value="true" ${init.wouldRetake === true ? 'checked' : ''}><span>Sim</span></label>
              <label><input type="radio" name="retake" value="false" ${init.wouldRetake === false ? 'checked' : ''}><span>Não</span></label>
              <label><input type="radio" name="retake" value="" ${init.wouldRetake == null ? 'checked' : ''}><span>Prefiro não dizer</span></label>
            </div></div>
          <label class="field">Conceito obtido<select id="f-grade"><option value="">Prefiro não dizer</option>${b.grades.filter((g) => g !== 'Prefiro não dizer').map((g) => `<option ${init.grade === g ? 'selected' : ''}>${esc(g)}</option>`).join('')}</select></label>
        </div>
        <div class="field"><span>Marcadores <small class="muted">(até ${b.maxTags})</small></span>
          ${Object.entries(b.tags).map(([tone, list]) => `<div class="chips tag-picker">${list.map((t) => `<label class="chip ${tone === 'positivo' ? 'pos' : tone === 'negativo' ? 'neg' : 'neu'}"><input type="checkbox" name="tags" value="${esc(t)}" ${init.tags?.includes(t) ? 'checked' : ''}><span>${esc(t)}</span></label>`).join('')}</div>`).join('')}
          <small class="err" data-err="tags"></small>
        </div>
      </fieldset>

      <fieldset class="form-step"><legend><span>4</span> Comentário</legend>
        <label class="field"><span class="sr-only">Comentário</span>
          <textarea id="f-comment" rows="7" maxlength="${b.comment.max}" placeholder="Como eram as aulas? E as provas e trabalhos? O que você gostaria de ter sabido antes de se matricular? Alguma dica para quem vai cursar?">${esc(init.comment)}</textarea>
          <div class="mod-hints" id="mod-hints" aria-live="polite" hidden></div>
          <div class="field-foot"><small class="err" data-err="comment"></small><small class="muted"><span id="count">0</span>/${b.comment.max} · mínimo ${b.comment.min}</small></div>
        </label>
        <ul class="tips small muted"><li>Seja específico: exemplos concretos ajudam mais que adjetivos.</li><li>Critique práticas, não pessoas. Ofensas, comentários sobre aparência ou vida pessoal e dados pessoais impedem a publicação — o formulário avisa enquanto você escreve.</li><li>Você poderá editar ou apagar depois, neste navegador.</li></ul>
      </fieldset>
      <input id="f-website" name="website" tabindex="-1" autocomplete="off" aria-hidden="true" class="hp">
      <div class="form-actions">
        <button class="btn btn-lg" type="submit">${existing ? 'Salvar alterações' : 'Publicar avaliação'} <span aria-hidden="true">→</span></button>
        ${existing ? `<a class="btn btn-ghost" href="#/minhas">Cancelar</a>` : '<button type="button" class="link-btn muted" id="clear-draft">Limpar formulário</button>'}
      </div>
      <p id="form-message" class="form-message" role="status"></p>
    </form>
  </section>`;

  const profInput = $('#f-prof');
  const courseSel = $('#f-course');
  const termSel = $('#f-term');
  const byName = new Map(b.professors.map((p) => [fold(p.name), p.slug]));
  const currentProf = () => byName.get(fold(profInput.value.trim())) || '';
  const fillCourses = (keep) => {
    const slug = currentProf();
    const prof = state.prof.get(slug);
    const usable = (c) => c && (c.active || c.id === keep || c.id === init.course); // fora de oferta: só ao editar uma avaliação antiga
    const taught = prof ? [...new Set(prof.courses.map((id) => state.course.get(id)))].filter(usable) : [];
    const taughtIds = new Set(taught.map((c) => c.id));
    const others = b.courses.filter((c) => !taughtIds.has(c.id) && usable(c)).sort((x, y) => (x.kind === y.kind ? x.name.localeCompare(y.name) : x.kind === 'regular' ? -1 : 1));
    const opt = (c) => `<option value="${esc(c.id)}" ${keep === c.id ? 'selected' : ''}>${esc(c.name)} — ${esc(c.codes.join('/'))}</option>`;
    courseSel.innerHTML = `<option value="">Selecione…</option>
      ${taught.length ? `<optgroup label="Lecionadas por ${esc(firstName(prof.name))} (oferta oficial)">${taught.map(opt).join('')}</optgroup>` : ''}
      <optgroup label="${taught.length ? 'Outras disciplinas' : 'Todas as disciplinas'}">${others.map(opt).join('')}</optgroup>`;
    $('#course-hint').textContent = prof && !taught.length ? 'Não encontramos turmas deste docente na oferta publicada — escolha na lista completa.' : '';
    fillTerms(termSel.value || init.term);
  };
  const fillTerms = (keep) => {
    const slug = currentProf();
    const cid = courseSel.value;
    const pairTerms = new Set(slug && cid ? [] : []);
    if (slug && cid) {
      // termos em que esse docente deu essa disciplina (busca leve no catálogo de oferta via perfil)
      (state.pairCache?.[`${slug}|${cid}`] || []).forEach((t) => pairTerms.add(t));
    }
    const confirmed = termsAll.filter((t) => pairTerms.has(t));
    const rest = termsAll.filter((t) => !pairTerms.has(t));
    termSel.innerHTML = `<option value="">Selecione…</option>${confirmed.length ? `<optgroup label="Turmas confirmadas na oferta">${confirmed.map((t) => `<option ${keep === t ? 'selected' : ''}>${t}</option>`).join('')}</optgroup>` : ''}<optgroup label="${confirmed.length ? 'Outros semestres' : 'Semestre'}">${rest.map((t) => `<option ${keep === t ? 'selected' : ''}>${t}</option>`).join('')}</optgroup>`;
  };
  // carrega termos confirmados (pares docente×disciplina) sob demanda
  state.pairCache = state.pairCache || {};
  const loadPairs = async () => {
    const slug = currentProf();
    if (!slug || state.pairCache[`${slug}|*`]) return;
    try {
      const d = await api(`/api/professors/${slug}`);
      d.courses.forEach((c) => { state.pairCache[`${slug}|${c.id}`] = c.terms; });
      state.pairCache[`${slug}|*`] = true;
      fillTerms(termSel.value);
    } catch (_) { /* segue sem sugestões */ }
  };
  init.course = state.course.get(init.course)?.id || init.course; // rascunho/link com código antigo
  fillCourses(init.course);
  loadPairs();
  profInput.addEventListener('change', () => { fillCourses(courseSel.value); loadPairs(); });
  profInput.addEventListener('input', () => { if (currentProf()) { fillCourses(courseSel.value); loadPairs(); } });
  // Sem docente escolhido: quem lecionou a disciplina selecionada aparece primeiro nas sugestões.
  const fillProfs = () => {
    const course = state.course.get(courseSel.value);
    const first = new Set(course ? course.professors : []);
    const ordered = [...profs.filter((p) => first.has(p.slug)), ...profs.filter((p) => !first.has(p.slug))];
    $('#prof-list').innerHTML = ordered.map((p) => `<option value="${esc(p.name)}">${first.has(p.slug) ? `Lecionou ${esc(course.name)}` : p.status !== 'ativo' ? STATUS_LABEL[p.status] : ''}</option>`).join('');
  };
  fillProfs();
  courseSel.addEventListener('change', () => { fillTerms(termSel.value); if (!currentProf()) fillProfs(); });

  const form = $('#review-form');
  const comment = $('#f-comment');
  const count = () => { $('#count').textContent = comment.value.trim().length; };
  comment.addEventListener('input', count);
  // Checagem enquanto digita (moderation.py no servidor): mostra o que impediria a publicação antes de enviar
  let modTimer;
  let modSeq = 0;
  const showHints = (issues) => {
    const box = $('#mod-hints');
    if (!issues.length) { box.hidden = true; box.innerHTML = ''; return; }
    const reject = issues.filter((i) => i.action === 'reject');
    const hold = issues.filter((i) => i.action === 'hold');
    box.hidden = false;
    box.className = `mod-hints ${reject.length ? 'is-reject' : 'is-hold'}`;
    box.innerHTML = `<b>${reject.length ? 'Ajuste antes de enviar:' : 'Vai passar por uma pessoa antes de publicar:'}</b><ul>${[...reject, ...hold]
      .map((i) => `<li>${esc(i.message)}${i.evidence ? ` <span class="mod-evidence">trecho: “${esc(i.evidence)}”</span>` : ''}</li>`).join('')}</ul>`;
  };
  const checkComment = async () => {
    const text = comment.value.trim();
    const seq = ++modSeq;
    if (text.length < 15) { showHints([]); return; }
    try {
      const res = await api('/api/moderation/check', { method: 'POST', body: { comment: text, professor: currentProf() } });
      if (seq === modSeq) showHints(res.issues);
    } catch (_) { /* a validação do envio continua valendo */ }
  };
  comment.addEventListener('input', () => { clearTimeout(modTimer); modTimer = setTimeout(checkComment, 600); });
  if (comment.value.trim()) checkComment();
  count();
  form.addEventListener('change', (e) => {
    if (e.target.name === 'tags') {
      const checked = $$('input[name="tags"]:checked', form);
      if (checked.length > b.maxTags) { e.target.checked = false; toast(`Escolha no máximo ${b.maxTags} marcadores.`, 'error'); }
    }
  });
  const collect = () => {
    const fd = new FormData(form);
    const ratings = {};
    b.criteria.forEach((c) => { if (fd.get(c.key)) ratings[c.key] = Number(fd.get(c.key)); });
    const retake = fd.get('retake');
    return {
      professor: currentProf(), course: courseSel.value, term: termSel.value, ratings,
      wouldRetake: retake === 'true' ? true : retake === 'false' ? false : null,
      grade: $('#f-grade').value || null, tags: fd.getAll('tags'), comment: comment.value, website: $('#f-website').value,
    };
  };
  if (!existing) {
    form.addEventListener('input', () => store.set(draftKey, collect()));
    $('#clear-draft')?.addEventListener('click', () => { store.del(draftKey); location.hash = '#/avaliar'; router(); });
  }
  const showErrors = (fields = {}) => {
    $$('[data-err]', form).forEach((el) => { el.textContent = fields[el.dataset.err] || ''; });
    const first = Object.keys(fields)[0];
    if (first) $(`[data-err="${first}"]`, form)?.closest('.field, .rating-field')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = collect();
    const errors = {};
    if (!payload.professor) errors.professor = 'Escolha um docente da lista.';
    if (!payload.course) errors.course = 'Escolha a disciplina.';
    if (!payload.term) errors.term = 'Escolha o semestre.';
    b.criteria.forEach((c) => { if (!payload.ratings[c.key]) errors[c.key] = 'Escolha uma nota.'; });
    if (payload.comment.trim().length < b.comment.min) errors.comment = `Escreva pelo menos ${b.comment.min} caracteres.`;
    showErrors(errors);
    if (Object.keys(errors).length) { $('#form-message').textContent = 'Revise os campos destacados.'; return; }
    const btn = $('button[type="submit"]', form);
    btn.disabled = true;
    $('#form-message').textContent = 'Enviando…';
    try {
      const res = await api(existing ? `/api/reviews/${existing.id}` : '/api/reviews', { method: existing ? 'PUT' : 'POST', body: payload });
      store.del(draftKey);
      state.me = null;
      await loadBoot(true);
      toast(res.message, res.held ? 'warn' : 'ok');
      location.hash = res.held ? '#/minhas' : `#/professor/${payload.professor}`;
    } catch (err) {
      btn.disabled = false;
      $('#form-message').textContent = err.message;
      if (err.data?.fields) showErrors(err.data.fields);
      if (err.status === 409 && err.data?.reviewId) {
        $('#form-message').innerHTML = `${esc(err.message)} <a href="#/avaliar?edit=${err.data.reviewId}">Editar a avaliação existente →</a>`;
      }
    }
  });
}

// ---- minhas avaliações
async function viewMine() {
  setTitle('Minhas avaliações');
  const me = await loadMe(true);
  main.innerHTML = `<section class="page narrow">
    <div class="page-head"><p class="eyebrow">Neste navegador</p><h1>Minhas avaliações</h1>
      <p class="lead-sm">Suas avaliações ficam ligadas a um identificador anônimo guardado neste navegador. Ninguém — nem nós — consegue saber quem as escreveu.</p></div>
    ${me.reviews.length ? `<div class="review-list">${me.reviews.map((r) => reviewCard(r, { show: 'both' })).join('')}</div>`
      : `<div class="empty-state callout"><h3>Nenhuma avaliação por aqui</h3><p>Se você avaliou em outro navegador ou celular, use o código de recuperação abaixo.</p><a class="btn" href="#/avaliar">Escrever uma avaliação</a></div>`}
    <section class="block recovery" id="recuperacao">
      <h2>Código de recuperação</h2>
      <p class="muted">Use para continuar editando suas avaliações em outro navegador. <b>Guarde em segredo</b>: quem tiver o código pode editar e apagar suas avaliações.</p>
      <div class="row-gap">
        ${me.hasIdentity ? '<button class="btn btn-ghost" id="show-code" type="button">Mostrar meu código</button>' : '<span class="muted small">Este navegador ainda não tem um código (ele é criado na primeira avaliação).</span>'}
        <code id="code-box" class="code-box" hidden></code>
        <button class="btn btn-ghost btn-sm" id="copy-code" type="button" hidden>Copiar</button>
      </div>
      <form class="row-gap" id="restore-form">
        <label class="field grow"><span>Tenho um código</span><input name="code" autocomplete="off" spellcheck="false" placeholder="Cole aqui o código de 43 caracteres"></label>
        <button class="btn" type="submit">Recuperar</button>
      </form>
    </section>
  </section>`;
  $('#show-code')?.addEventListener('click', async () => {
    try {
      const { code } = await api('/api/me/code', { method: 'POST', body: {} });
      $('#code-box').textContent = code;
      $('#code-box').hidden = false;
      $('#copy-code').hidden = false;
    } catch (err) { toast(err.message, 'error'); }
  });
  $('#copy-code')?.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#code-box').textContent); toast('Código copiado.'); } catch (_) { toast('Selecione e copie manualmente.', 'warn'); }
  });
  $('#restore-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const code = new FormData(e.target).get('code').trim();
    if (me.hasIdentity && me.reviews.length && !confirm('Este navegador já tem avaliações. Ao recuperar outro código, elas deixam de aparecer aqui (a não ser que você guarde o código atual). Continuar?')) return;
    try {
      const res = await api('/api/me/restore', { method: 'POST', body: { code } });
      toast(res.message);
      state.me = null;
      router();
    } catch (err) { toast(err.message, 'error'); }
  });
}

// ---- sobre
async function viewAbout() {
  setTitle('Como funciona');
  const b = state.boot;
  main.innerHTML = `<section class="page narrow prose">
    <div class="page-head"><p class="eyebrow">Sobre</p><h1>Como funciona</h1>
      <p class="lead-sm">O DCCHub reúne o que estudantes do DCC precisam para escolher e cursar disciplinas: avaliações anônimas, a oferta do semestre, provas antigas e oportunidades de pesquisa — com transparência sobre os dados e respeito a quem ensina.</p></div>

    <p class="notice notice-old" role="note"><span><b>Projeto independente de estudantes.</b> O DCCHub não tem vínculo com o DCC nem com a UFMG e não fala em nome deles. É feito pela e para a comunidade estudantil; os dados de oferta, docentes, materiais e oportunidades vêm de fontes públicas ou da própria comunidade. Criado e mantido por Thales Amaral, estudante da UFMG — que, como qualquer pessoa, não tem como saber quem escreveu cada avaliação.</span></p>

    <h2 id="avaliar">O que você avalia</h2>
    <ul>${b.criteria.map((c) => `<li><b>${esc(c.label)}</b> — ${esc(c.hint)} (1 = ${esc(c.low.toLowerCase())}, 5 = ${esc(c.high.toLowerCase())}).</li>`).join('')}</ul>
    <p>Também dá para dizer se faria a disciplina de novo com o mesmo docente, o conceito obtido e marcadores como “Listas de exercícios” ou “Provas difíceis”. Cada pessoa avalia uma vez cada combinação de docente e disciplina, e pode editar quando quiser.</p>

    <h2 id="notas">Como as notas são calculadas</h2>
    <p>As notas exibidas são médias simples. Para <b>ordenar rankings</b> usamos uma média bayesiana, que puxa para a média geral quem tem poucas avaliações — assim uma única nota 5 não coloca ninguém no topo.</p>
    <p>A nota mostrada ao lado de cada docente em uma disciplina considera apenas avaliações <em>daquela disciplina</em>: o mesmo docente pode ser ótimo em uma optativa e exigente em uma obrigatória.</p>

    <h2 id="privacidade">Privacidade</h2>
    <ul>
      ${b.emailAccess
        ? `<li>Sem cadastro e sem conta. O e-mail @ufmg.br serve só para enviar o código de acesso: <b>não é guardado</b> (nem no banco, nem em logs), e o acesso que ele libera não tem nenhuma ligação com as avaliações que você escreve. Não pedimos nome nem matrícula.</li>`
        : '<li>Sem cadastro: não pedimos nome, e-mail ou matrícula.</li>'}
      <li>Seu navegador recebe um identificador aleatório (cookie <code>HttpOnly</code>). O banco guarda apenas um <abbr title="HMAC-SHA256 com chave secreta do servidor">hash</abbr> dele — usado só para impedir avaliações repetidas e permitir edição.</li>
      <li>O servidor não registra endereços IP em logs. O IP é usado apenas em memória, por alguns minutos, para limitar abusos.</li>
      <li>Sem Google Fonts, analytics ou qualquer recurso de terceiros: fotos e dados são servidos pelo próprio site.</li>
    </ul>

    <h2 id="regras">Regras da comunidade</h2>
    <ul>
      <li>Fale da disciplina e das práticas de ensino. Nada de aparência, vida pessoal ou boatos.</li>
      <li>Críticas são bem-vindas; ofensas não. Um filtro automático confere cada comentário enquanto você escreve e não deixa publicar palavrões, xingamentos, comentários sobre aparência, idade ou vida pessoal, ameaças, boatos, discriminação e dados pessoais — ele mostra o trecho e você reescreve na hora.</li>
      <li>Denúncias graves (assédio, abuso, crimes) não são publicadas aqui: o lugar certo é a Ouvidoria da UFMG ou o DCE, que têm canais sigilosos.</li>
      <li>Acusações de discriminação (machismo, racismo…) são lidas por uma pessoa da moderação antes de publicar.</li>
      <li>Qualquer pessoa pode denunciar uma avaliação; toda denúncia chega à moderação. Denunciada por várias pessoas, de redes diferentes, ela sai do ar até ser revisada — várias denúncias do mesmo computador ou da mesma rede não bastam.</li>
      <li>Docentes que se sentirem expostos de forma injusta podem usar o botão “Denunciar”.</li>
    </ul>
    <p>Sugestões, erros ou ideias? Mande um <a href="#/feedback">feedback anônimo</a>.</p>

    <h2 id="termos">Termos de uso</h2>
    <ul>
      <li><b>Quem escreve responde pelo que escreve.</b> Cada avaliação é a opinião de quem cursou a disciplina, publicada de forma anônima. Ao publicar, você declara que relata uma experiência própria e que segue as regras acima.</li>
      <li><b>O DCCHub não endossa as avaliações</b> nem garante que estejam corretas. As notas são médias de opiniões, não uma medida oficial de nada.</li>
      <li><b>Uso pessoal e acadêmico.</b> Não copie avaliações em massa para outros sites nem use o conteúdo para expor, perseguir ou retaliar quem quer que seja.</li>
      <li><b>Moderação.</b> Podemos segurar para revisão ou remover conteúdo que viole as regras, a qualquer momento.</li>
      <li><b>Anonimato também diante de pedidos.</b> Não sabemos quem escreveu cada avaliação: não há conta, e o site não guarda e-mail nem IP. Por isso, não temos como informar a autoria a ninguém.</li>
    </ul>
    <h3 id="remocao">Pedidos de remoção</h3>
    <p>Qualquer pessoa — inclusive docentes — pode pedir a remoção de uma avaliação que viole as regras (ofensa, dado pessoal, informação falsa, exposição indevida):</p>
    <ul>
      <li>pelo botão <b>“Denunciar”</b> na própria avaliação, que leva o pedido direto à moderação; ou</li>
      <li>por e-mail para <a href="mailto:dcchubufmg@gmail.com">dcchubufmg@gmail.com</a>, com o link da página, um trecho da avaliação e o motivo — o caminho para pedidos formais ou de quem não tem acesso ao site.</li>
    </ul>
    <p>Cada pedido é lido por uma pessoa. Enquanto isso, a avaliação pode ficar fora do ar. Avaliações que seguem as regras continuam publicadas, mesmo que sejam críticas.</p>

    <h2 id="dados">De onde vêm os dados</h2>
    <p>A lista de docentes (com áreas de pesquisa e contatos) vem de <a href="${safeHref(b.sources.professors || 'https://dcc.ufmg.br/professores/')}" target="_blank" rel="noreferrer">dcc.ufmg.br/professores</a>. Disciplinas, turmas, horários e planos de ensino vêm das páginas públicas de <b>oferta de disciplinas</b> do DCC (${esc(b.terms[0])} a ${esc(b.latestTerm)}).</p>
    <p id="versoes">Quando o currículo muda, a disciplina ganha um código novo e o antigo continua sendo ofertado por um tempo para quem está na versão anterior (ex.: <b>Estruturas de Dados</b> DCC205 → DCC221, <b>PDS II</b> DCC204 → DCC219, <b>Redes</b> DCC023 → DCC095). Aqui as versões aparecem como <b>uma só disciplina</b>, com todas as turmas e avaliações juntas. Disciplinas <b>extintas</b> — de currículos antigos, como Algoritmos e Estruturas de Dados I e II, ou que não aparecem em nenhuma oferta dos últimos dois anos (${esc(b.activeTerms?.[0] || '')}–${esc(b.latestTerm)}) — não aparecem no site. Quando há uma sucessora (ex.: Introdução à Ciência da Computação DCC050 e Fundamentos de SI DCC044 → Introdução à Computação DCC096, do tronco comum), links antigos levam a ela.</p>
    <h3 id="atual">E o semestre atual?</h3>
    <p>O site só mostra como oferta oficial o que o DCC publica. ${b.offerPublished ? `A oferta de ${esc(b.latestTerm)} já está publicada.` : `<b>A oferta de ${esc(b.currentTerm)} ainda não foi publicada</b>, então as turmas “atuais” exibidas são as de ${esc(b.latestTerm)}.`} Enquanto a oferta não sai, estudantes podem <b>informar quem está dando cada disciplina</b> na página do docente ou da disciplina. Esses relatos aparecem separados, com o número de pessoas que confirmaram, e somem quando a oferta oficial é publicada.</p>
    <p id="curriculos">Na <a href="#/oferta">grade</a>, ao escolher seu curso, as disciplinas do seu período aparecem primeiro. Os currículos vêm dos relatórios oficiais da Pró-reitoria de Graduação e, quando só existem como imagem no site do colegiado (Ciência da Computação a partir de 2025, Sistemas de Informação e Engenharia de Computação), foram transcritos à mão. Confira sempre no SIGA. O curso e o período escolhidos ficam só no seu navegador.</p>
    <p id="materiais">A seção <a href="#/materiais">Materiais</a> aponta para o <b>newCompleaks</b>, acervo público de provas e materiais mantido pelos estudantes no Google Drive. O site só guarda os links e a contagem de arquivos de cada pasta (<code>scripts/sync_provas.py</code>); o conteúdo continua no Drive, e nada é carregado de lá ao navegar aqui.</p>
    <p>Disciplinas de <b>tópicos especiais</b> (DCC030, DCC049, DCC831…) mudam de tema a cada semestre, então cada tema aparece como uma disciplina própria. Algumas ofertas antigas listam só o primeiro nome do docente; quando não foi possível identificar com segurança, a turma aparece como “docente não informado”.</p>
    <p class="muted small">Última sincronização: ${b.generatedAt ? new Date(b.generatedAt).toLocaleString('pt-BR') : '—'}. ${b.sources.offerings ? `${b.sources.offerings.length} páginas de oferta lidas.` : ''}</p>

    <h2 id="faq">Perguntas frequentes</h2>
    <details><summary>Perdi minhas avaliações ao trocar de navegador.</summary><p>Elas continuam publicadas. Para voltar a editá-las, use o código de recuperação em <a href="#/minhas">Minhas avaliações</a> no navegador antigo e cole no novo.</p></details>
    <details><summary>Não encontro um docente ou disciplina.</summary><p>Mostramos quem está no site do DCC e quem aparece na oferta desde 2020. Substitutos são identificados pela página oficial de professores substitutos do DCC; docentes de outros departamentos aparecem como “externo”. Quando a oferta traz só um apelido que não dá para identificar com segurança, a turma mostra esse nome com “(não identificado)”. Se faltar alguém, avise o centro acadêmico.</p></details>
    <details><summary>O que significa “turma confirmada”?</summary><p>Que a oferta oficial do DCC lista aquele docente naquela disciplina no semestre informado. Não confirma que quem avaliou cursou a turma — isso é impossível sem identificar a pessoa.</p></details>
    <details><summary>Posso avaliar uma disciplina de outro departamento?</summary><p>Por enquanto só disciplinas ofertadas pelo DCC.</p></details>
  </section>`;
}

// ---- modo docente: o docente que entra com o e-mail publicado vê só a própria página, reduzida. Tudo é decidido no
// servidor (o resto da API responde 403 para ele); aqui só se monta a tela, sem navegação para o resto do site.
async function viewDocente() {
  document.body.classList.add('gated');
  setTitle('Sua página');
  let d;
  try { d = await api('/api/docente'); } catch (err) {
    if (err.data?.gate) { state.boot = null; viewGate(err.data.methods); return; }
    throw err;
  }
  // estado mínimo para os componentes de resumo (critérios e marcadores); o resto do site não carrega
  state.boot = { docente: true, criteria: d.criteria, tags: d.tags, reportReasons: d.reportReasons };
  const crit = d.criteria.filter((c) => c.key !== 'overall');
  const card = (r) => `<article class="review" id="review-${r.id}">
    <header class="review-head"><span class="score-tile ${scoreClass(r.ratings.overall)}" aria-label="Nota geral ${r.ratings.overall} de 5">${r.ratings.overall}</span><div><p class="muted small">Nota geral</p></div></header>
    <ul class="review-metrics">
      ${crit.filter((c) => r.ratings[c.key] != null).map((c) => `<li><span>${esc(c.label)}</span>${pill(r.ratings[c.key], { crit: c })}${c.levels ? `<small>${esc(level(c, r.ratings[c.key]).toLowerCase())}</small>` : ''}</li>`).join('')}
      ${r.wouldRetake != null ? `<li><span>Faria de novo</span><b class="${r.wouldRetake ? 'yes' : 'no'}">${r.wouldRetake ? 'Sim' : 'Não'}</b></li>` : ''}
    </ul>
    ${r.tags.length ? `<div class="chips">${r.tags.map((t) => `<span class="chip ${tagTone(t)}">${esc(t)}</span>`).join('')}</div>` : ''}
    <div class="review-text">${esc(r.comment).split(/\n{2,}/).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('')}</div>
    <footer class="review-foot"><button class="link-btn muted" data-docente-report="${r.id}">Denunciar à moderação</button></footer>
  </article>`;
  const few = d.courses.filter((c) => !c.reviews);
  main.innerHTML = `<section class="page">
    <div class="page-head docente-head">${avatar(d.professor, 'xl')}<div><p class="eyebrow">Sua página no DCCHub</p><h1>${esc(d.professor.name)}</h1>
      <p class="lead-sm">${plural(d.total, 'avaliação publicada', 'avaliações publicadas')} por estudantes.</p></div>
      <button class="btn btn-ghost btn-sm" id="docente-exit" type="button">Sair</button></div>
    <p class="notice notice-old" role="note"><svg class="ico" aria-hidden="true"><use href="#i-shield"/></svg><span><b>Versão para docentes.</b> Para proteger quem escreveu, as avaliações aparecem sem semestre, conceito e data, e disciplinas com menos de ${d.minPerCourse} avaliações mostram só quantas há. Se algo for falso, ofensivo ou expuser alguém, use “Denunciar à moderação”: a avaliação é revisada por uma pessoa.</span></p>
    ${d.summary ? summaryBlock(d.summary) : '<p class="muted">Ainda não há disciplina com avaliações suficientes para mostrar.</p>'}
    ${d.courses.filter((c) => c.reviews).map((c) => `<section class="block">
      <div class="section-head"><h2>${esc(c.name)}</h2><span class="muted small">${esc(c.codes.join(' / '))} · ${plural(c.count, 'avaliação', 'avaliações')}${c.summary.avg.overall != null ? ` · média geral ${fmt(c.summary.avg.overall)}` : ''}</span></div>
      <div class="review-list">${c.reviews.map(card).join('')}</div></section>`).join('')}
    ${few.length ? `<section class="block"><h2>Com poucas avaliações</h2><ul class="community-list">${few.map((c) => `<li><span>${esc(c.name)}</span><span class="small muted">${plural(c.count, 'avaliação', 'avaliações')} — aparece a partir de ${d.minPerCourse}</span></li>`).join('')}</ul></section>` : ''}
  </section>`;
  $('#docente-exit').addEventListener('click', async () => {
    await api('/api/access/logout', { method: 'POST', body: {} }).catch(() => {});
    state.boot = null;
    router();
  });
  $('.page', main).addEventListener('click', (e) => {
    const btn = e.target.closest('[data-docente-report]');
    if (!btn) return;
    const dlg = $('#dialog');
    dlg.innerHTML = `<form method="dialog" class="dialog-body" id="report-form">
      <h2>Denunciar à moderação</h2>
      <p class="muted">A avaliação continua no ar até uma pessoa da moderação revisá-la.</p>
      <fieldset class="radio-list">${d.reportReasons.map((r, i) => `<label><input type="radio" name="reason" value="${esc(r)}" ${i === 0 ? 'checked' : ''}> ${esc(r)}</label>`).join('')}</fieldset>
      <label class="field">Detalhes (opcional)<textarea name="detail" maxlength="300" rows="3"></textarea></label>
      <div class="row-gap end"><button class="btn btn-ghost" value="cancel" formnovalidate>Cancelar</button><button class="btn" value="send">Enviar</button></div>
    </form>`;
    dlg.showModal();
    $('#report-form').addEventListener('submit', async (ev) => {
      if (ev.submitter?.value !== 'send') return;
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try {
        const res = await api(`/api/docente/reviews/${btn.dataset.docenteReport}/report`, { method: 'POST', body: { reason: fd.get('reason'), detail: fd.get('detail') } });
        toast(res.message);
        dlg.close();
      } catch (err) { toast(err.message, 'error'); }
    });
  });
}

// ---- feedback anônimo sobre o site
const FEEDBACK_KINDS = [['sugestao', 'Sugestão'], ['problema', 'Algo não funciona'], ['dados', 'Dado errado ou faltando'], ['outro', 'Outro']];
const FEEDBACK_MAX = 2000;

async function viewFeedback() {
  setTitle('Feedback');
  const draftKey = 'dcchub-feedback';
  const draft = store.get(draftKey) || {};
  main.innerHTML = `<section class="page narrow">
    <div class="page-head"><p class="eyebrow">Fale com a equipe</p><h1>Feedback anônimo</h1>
      <p class="lead-sm">Sugestões, problemas, dados errados ou qualquer ideia para melhorar o DCCHub. Chega direto para quem mantém o site.</p></div>
    <p class="notice" role="note"><svg class="ico" aria-hidden="true"><use href="#i-shield"/></svg><span><b>Anônimo de verdade.</b> O feedback não fica ligado ao seu navegador nem às suas avaliações, e não guardamos IP nem horário (só o dia). Por isso não temos como responder: se quiser retorno, deixe um contato no texto.</span></p>
    <form class="review-form" id="feedback-form" novalidate>
      <div class="field" role="radiogroup" aria-labelledby="fb-kind"><span id="fb-kind">Sobre o quê?</span>
        <div class="chips tag-picker">${FEEDBACK_KINDS.map(([v, l]) => `<label class="chip neu"><input type="radio" name="kind" value="${v}" ${(draft.kind || 'sugestao') === v ? 'checked' : ''}><span>${l}</span></label>`).join('')}</div>
      </div>
      <label class="field"><span>Mensagem</span>
        <textarea id="fb-message" name="message" rows="7" maxlength="${FEEDBACK_MAX}" placeholder="O que poderia ser melhor? Encontrou algum erro? Falta alguma coisa?">${esc(draft.message || '')}</textarea>
        <div class="field-foot"><small class="err" data-err="message"></small><small class="muted"><span id="fb-count">0</span>/${FEEDBACK_MAX}</small></div>
      </label>
      <p class="small muted">Denúncia sobre uma avaliação? Use o botão “Denunciar” nela. Assédio, abuso ou crimes: Ouvidoria da UFMG ou DCE, que têm canais sigilosos.</p>
      <input name="website" tabindex="-1" autocomplete="off" aria-hidden="true" class="hp">
      <div class="form-actions"><button class="btn btn-lg" type="submit">Enviar feedback <span aria-hidden="true">→</span></button></div>
      <p id="form-message" class="form-message" role="status"></p>
    </form>
  </section>`;
  const form = $('#feedback-form');
  const text = $('#fb-message');
  const sync = () => {
    $('#fb-count').textContent = text.value.length;
    store.set(draftKey, { kind: new FormData(form).get('kind'), message: text.value });
  };
  sync();
  form.addEventListener('input', sync);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const btn = form.querySelector('[type=submit]');
    $('[data-err=message]').textContent = '';
    btn.disabled = true;
    try {
      const res = await api('/api/feedback', { method: 'POST', body: { kind: fd.get('kind'), message: fd.get('message'), website: fd.get('website') } });
      store.del(draftKey);
      main.querySelector('.page').innerHTML = `<div class="empty-state callout"><h3>Obrigado!</h3><p>${esc(res.message)}</p><div class="row-gap"><a class="btn" href="#/">Voltar ao início</a><button class="btn btn-ghost" type="button" id="fb-again">Mandar outro</button></div></div>`;
      $('#fb-again').addEventListener('click', () => router());
    } catch (err) {
      if (err.data?.fields?.message) $('[data-err=message]').textContent = err.data.fields.message;
      $('#form-message').textContent = err.message;
      btn.disabled = false;
    }
  });
}

// ---- materiais (acervo newCompleaks no Google Drive: só links, nada é carregado do Drive)
const filesLabel = (n) => (n ? plural(n, 'arquivo', 'arquivos') : 'pasta vazia');

async function viewProvas({ params, isCurrent }) {
  setTitle('Materiais');
  main.innerHTML = '<div class="loading-screen"><span class="spinner"></span></div>';
  const data = await api('/api/provas');
  if (!isCurrent()) return;
  const total = data.sections.reduce((n, s) => n + s.courses.reduce((m, c) => m + c.files, 0), 0);
  const guide = data.extras.find((e) => /bem vindo/i.test(e.title));
  const extras = data.extras.filter((e) => e !== guide);
  main.innerHTML = `<section class="page">
    <div class="page-head"><p class="eyebrow">Acervo dos estudantes</p><h1>Materiais</h1>
      <p class="lead-sm">Provas, listas e materiais de semestres anteriores reunidos pelos estudantes no <b>${esc(data.name)}</b>, uma pasta pública no Google Drive organizada por período. São ${total.toLocaleString('pt-BR')} arquivos; os links abrem o Drive em outra aba.</p></div>
    <div class="exam-intro">
      <a class="btn" href="${safeHref(data.url)}" target="_blank" rel="noreferrer">Abrir o acervo completo ↗</a>
      ${guide ? `<a class="btn btn-ghost" href="${safeHref(guide.url)}" target="_blank" rel="noreferrer">Como contribuir ↗</a>` : ''}
      ${extras.length ? `<span class="small muted">Também no acervo: ${extras.map((e) => `<a href="${safeHref(e.url)}" target="_blank" rel="noreferrer">${esc(e.title)}</a>`).join(' · ')}</span>` : ''}
    </div>
    <form class="filters" id="filters" role="search">
      <label class="field grow"><span class="sr-only">Buscar</span><input type="search" name="q" placeholder="Disciplina ou código (DCC205)…" value="${esc(params.get('q') || '')}"></label>
      <label class="check"><input type="checkbox" name="all" value="1" ${params.get('all') ? 'checked' : ''}> Mostrar pastas vazias</label>
    </form>
    <div id="exam-list"></div>
  </section>`;
  const form = $('#filters');
  const draw = () => {
    const fd = new FormData(form);
    const q = fold(fd.get('q') || '');
    const all = fd.get('all');
    const match = (c) => (all || c.files) && (!q || q.split(/\s+/).every((w) => fold(`${c.code} ${c.title} ${state.course.get(c.course)?.name || ''} ${(state.course.get(c.course)?.codes || []).join(' ')}`).includes(w)));
    const html = data.sections.map((sec) => {
      const rows = sec.courses.filter(match);
      const folders = (q ? [] : sec.folders).filter((f) => all || f.files);
      if (!rows.length && !folders.length) return '';
      return `<section class="exam-section"><div class="section-head"><h2>${esc(sec.title)}</h2><a class="small" href="${safeHref(sec.url)}" target="_blank" rel="noreferrer">Pasta no Drive ↗</a></div>
        <ul class="exam-list">${rows.map((c) => {
          const course = state.course.get(c.course);
          return `<li class="${c.files ? '' : 'empty'}">
            <div class="exam-main"><span class="code">${esc(c.code)}</span>
              ${course ? `<a href="#/disciplina/${encodeURIComponent(course.id)}"><b>${esc(course.name)}</b></a>${fold(course.name) !== fold(c.title) ? ` <span class="small muted">(no acervo: ${esc(c.title)})</span>` : ''}` : `<b>${esc(c.title)}</b>`}</div>
            <span class="small muted exam-count">${filesLabel(c.files)}${c.note ? ` · ${esc(c.note)}` : ''}</span>
            <a class="btn btn-sm btn-ghost" href="${safeHref(c.url)}" target="_blank" rel="noreferrer">Abrir ↗</a></li>`;
        }).join('')}${folders.map((f) => `<li><div class="exam-main"><b>${esc(f.title)}</b></div><span class="small muted exam-count">${filesLabel(f.files)}</span><a class="btn btn-sm btn-ghost" href="${safeHref(f.url)}" target="_blank" rel="noreferrer">Abrir ↗</a></li>`).join('')}</ul></section>`;
    }).join('');
    $('#exam-list').innerHTML = html || '<div class="empty-state"><p>Nenhuma pasta encontrada. O acervo pode ter a disciplina com outro nome: <a href="' + esc(data.url) + '" target="_blank" rel="noreferrer">abra o acervo completo ↗</a>.</p></div>';
    const qs = new URLSearchParams();
    for (const [k, v] of fd) if (v) qs.set(k, v);
    setQuery(qs);
  };
  form.addEventListener('input', draw);
  form.addEventListener('submit', (e) => e.preventDefault());
  draw();
}

// Bloco na página da disciplina: pastas do acervo para ela (inclui versões antigas, ex.: DCC205 em Estruturas de Dados)
function examsBlock(exams) {
  if (!exams?.items?.length) return '';
  const withFiles = exams.items.filter((e) => e.files);
  return `<section class="block exams" id="materiais">
    <div class="section-head"><h2>Materiais</h2><a class="small" href="#/materiais">Todo o acervo →</a></div>
    <ul class="exam-list">${exams.items.map((e) => `<li class="${e.files ? '' : 'empty'}">
      <div class="exam-main"><span class="code">${esc(e.code)}</span> <b>${esc(e.title)}</b> <span class="small muted">· ${esc(e.section)}</span></div>
      <span class="small muted exam-count">${filesLabel(e.files)}</span>
      <a class="btn btn-sm ${e.files ? '' : 'btn-ghost'}" href="${safeHref(e.url)}" target="_blank" rel="noreferrer">${e.files ? 'Abrir pasta ↗' : 'Contribuir ↗'}</a></li>`).join('')}</ul>
    <p class="small muted">Material reunido por estudantes no ${esc(exams.name)} (Google Drive)${withFiles.length ? '' : '. A pasta desta disciplina ainda está vazia: se você tem provas ou listas antigas, considere contribuir'}.</p>
  </section>`;
}

// ---- oportunidades (bolsas de IC/pesquisa, estágios, pós-doc… publicadas em dcc.ufmg.br/oportunidades)
const fmtDate = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' }) : '');
const isHttp = (u) => /^(https?:|mailto:)/.test(u || '');

function stipendText(list) {
  if (!list?.length) return '';
  return list.map((x) => (x.who ? `${esc(x.who)}: <b>${esc(x.value)}</b>` : `<b>${esc(x.value)}</b>`)).join(' · ');
}

function oppCard(o) {
  const profs = (o.professors || []).map((s) => state.prof.get(s)).filter(Boolean);
  const facts = [
    o.stipends?.length && `<div><dt>Bolsa</dt><dd>${stipendText(o.stipends)}</dd></div>`,
    o.hours?.length && `<div><dt>Dedicação</dt><dd>${o.hours.map((h) => `${h}h`).join(' ou ')} semanais</dd></div>`,
    o.deadline && `<div><dt>Inscrições</dt><dd>${esc(o.deadline)}</dd></div>`,
  ].filter(Boolean);
  return `<article class="opp-card${o.open ? '' : ' closed'}">
    <div class="opp-tags">${o.types.map((t) => `<span class="badge badge-type">${esc(t)}</span>`).join('')}${o.audiences.filter((a) => !o.types.includes(a)).map((a) => `<span class="badge">${esc(a)}</span>`).join('')}${o.open ? '' : '<span class="badge">Encerrada</span>'}</div>
    <h3><a href="${safeHref(o.url)}" target="_blank" rel="noreferrer">${esc(o.title)}</a></h3>
    ${o.summary ? `<p class="opp-summary">${esc(o.summary)}</p>` : ''}
    ${facts.length ? `<dl class="opp-facts">${facts.join('')}</dl>` : ''}
    ${profs.length ? `<div class="opp-profs"><span class="small muted">Docentes:</span> ${profs.map((p) => `<a class="prof-inline" href="#/professor/${p.slug}">${avatar(p, 'sm')} <span>${esc(firstNameLast(p.name))}</span></a>`).join('')}</div>` : ''}
    <div class="opp-foot">
      <span class="small muted">${[...o.areas, ...o.groups].map(esc).join(' · ')}${o.published ? `${o.areas.length || o.groups.length ? ' · ' : ''}publicada em ${fmtDate(o.published)}` : ''}</span>
      <span class="row-gap">${o.open && isHttp(o.apply) ? `<a class="btn btn-sm" href="${safeHref(o.apply)}" target="_blank" rel="noreferrer">Candidatar-se ↗</a>` : ''}<a class="btn btn-sm btn-ghost" href="${safeHref(o.url)}" target="_blank" rel="noreferrer">Ver chamada ↗</a></span>
    </div>
  </article>`;
}

async function viewOportunidades({ params, isCurrent }) {
  setTitle('Oportunidades');
  main.innerHTML = '<div class="loading-screen"><span class="spinner"></span></div>';
  const data = await api('/api/oportunidades');
  if (!isCurrent()) return;
  const items = data.items;
  const types = [...new Set(items.flatMap((o) => o.types))].sort((a, b) => a.localeCompare(b));
  const auds = ['Graduação', 'Mestrado', 'Doutorado', 'Pós-doutorado', 'Profissionais'].filter((a) => items.some((o) => o.audiences.includes(a)));
  const nOpen = items.filter((o) => o.open).length;
  const sel = (name, v) => (params.get(name) === v ? 'selected' : '');
  main.innerHTML = `<section class="page">
    <div class="page-head"><p class="eyebrow">Pesquisa e projetos</p><h1>Oportunidades</h1>
      <p class="lead-sm">Bolsas de iniciação científica e de pesquisa, estágios e vagas em laboratórios e projetos do DCC, reunidas da página <a href="${safeHref(data.source)}" target="_blank" rel="noreferrer">Oportunidades do DCC ↗</a>. ${plural(nOpen, 'chamada aberta', 'chamadas abertas')}. Confira sempre os detalhes e o prazo na chamada original.</p></div>
    <form class="filters" id="filters" role="search">
      <label class="field grow"><span class="sr-only">Buscar</span><input type="search" name="q" placeholder="Tema, laboratório, docente…" value="${esc(params.get('q') || '')}"></label>
      <label class="field"><span class="sr-only">Situação</span><select name="status">
        <option value="">Abertas</option><option value="fechadas" ${sel('status', 'fechadas')}>Encerradas</option><option value="todas" ${sel('status', 'todas')}>Todas</option></select></label>
      <label class="field"><span class="sr-only">Tipo</span><select name="type"><option value="">Qualquer tipo</option>${types.map((t) => `<option ${sel('type', t)}>${esc(t)}</option>`).join('')}</select></label>
      <label class="field"><span class="sr-only">Para quem</span><select name="aud"><option value="">Qualquer nível</option>${auds.map((a) => `<option ${sel('aud', a)}>${esc(a)}</option>`).join('')}</select></label>
    </form>
    <p class="result-count muted" id="result-count" aria-live="polite"></p>
    <div class="opp-grid" id="opp-list"></div>
    <p class="small muted">Atualizado em ${data.generatedAt ? new Date(data.generatedAt).toLocaleDateString('pt-BR') : '—'} a partir do site do DCC. Resumos e valores são extraídos automaticamente; a chamada original é a referência.</p>
  </section>`;
  const form = $('#filters');
  const draw = () => {
    const fd = new FormData(form);
    const q = fold(fd.get('q') || '');
    const status = fd.get('status');
    const type = fd.get('type');
    const aud = fd.get('aud');
    const list = items.filter((o) => (status === 'todas' || (status === 'fechadas' ? !o.open : o.open))
      && (!type || o.types.includes(type)) && (!aud || o.audiences.includes(aud))
      && (!q || q.split(/\s+/).every((w) => fold(`${o.title} ${o.summary} ${o.areas.join(' ')} ${o.groups.join(' ')} ${(o.professors || []).map((s) => state.prof.get(s)?.name || '').join(' ')}`).includes(w))));
    $('#result-count').textContent = plural(list.length, 'oportunidade', 'oportunidades');
    $('#opp-list').innerHTML = list.map(oppCard).join('') || '<div class="empty-state"><p>Nenhuma oportunidade com esses filtros.</p></div>';
    const qs = new URLSearchParams();
    for (const [k, v] of fd) if (v) qs.set(k, v);
    setQuery(qs);
  };
  form.addEventListener('input', draw);
  form.addEventListener('submit', (e) => e.preventDefault());
  draw();
}

function profOpportunities(list, name) {
  if (!list?.length) return '';
  return `<section class="block" id="oportunidades">
    <div class="section-head"><h2>Oportunidades abertas</h2><a class="small" href="#/oportunidades">Todas as oportunidades →</a></div>
    <ul class="exam-list">${list.map((o) => `<li><div class="exam-main"><a href="${safeHref(o.url)}" target="_blank" rel="noreferrer"><b>${esc(o.title)}</b></a></div>
      <span class="small muted">${[o.types.join(', '), o.audiences.join(', ')].filter(Boolean).map(esc).join(' · ')}</span>
      <a class="btn btn-sm btn-ghost" href="${safeHref(o.url)}" target="_blank" rel="noreferrer">Ver chamada ↗</a></li>`).join('')}</ul>
    <p class="small muted">Chamadas publicadas pelo DCC que citam ${esc(name)}.</p>
  </section>`;
}

// ---- moderação
async function viewModeration() {
  setTitle('Moderação');
  let token = '';
  try { token = sessionStorage.getItem('avalie-admin') || ''; } catch (_) { /* sem armazenamento */ }
  main.innerHTML = `<section class="page narrow">
    <div class="page-head"><p class="eyebrow">Equipe</p><h1>Moderação</h1></div>
    ${state.boot.moderation ? '' : '<p class="warn">A moderação está desativada neste servidor (defina DCCHUB_ADMIN_TOKEN).</p>'}
    <form class="row-gap" id="admin-form"><label class="field grow"><span>Token</span><input type="password" name="token" value="${esc(token)}" autocomplete="off"></label><button class="btn">Entrar</button></form>
    <div id="queue"></div>
  </section>`;
  const load = async () => {
    try {
      const { queue, bursts = [] } = await api('/api/admin/queue', { headers: { Authorization: `Bearer ${token}` } });
      try { sessionStorage.setItem('avalie-admin', token); } catch (_) { /* idem */ }
      const burstBlock = bursts.length ? `<div class="notice notice-old" role="note"><svg class="ico" aria-hidden="true"><use href="#i-info"/></svg><div>
        <b>Picos de avaliações (últimos 30 dias)</b> — muitas avaliações do mesmo docente num só dia podem ser campanha, a favor ou contra.
        <ul>${bursts.map((b) => `<li>${new Date(b.day * 1000).toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' })}: <a href="#/professor/${encodeURIComponent(b.professor)}">${esc(state.prof.get(b.professor)?.name || b.professor)}</a> — ${plural(b.count, 'avaliação', 'avaliações')}, média geral ${fmt(b.avg)}</li>`).join('')}</ul></div></div>` : '';
      $('#queue').innerHTML = `${burstBlock}<h2>${plural(queue.length, 'item', 'itens')} na fila</h2>${queue.map((r) => `<div class="mod-item">
        ${reviewCard(r, { show: 'both' })}
        <p class="small"><b>Status:</b> ${esc(r.status)} ${r.heldReason ? `· <b>Motivo:</b> ${esc(r.heldReason)}` : ''} ${r.reports.length ? `· <b>Denúncias:</b> ${r.reports.map((x) => esc(x.reason + (x.detail ? ` (“${x.detail}”)` : ''))).join('; ')}` : ''}</p>
        <div class="row-gap"><button class="btn btn-sm" data-mod="publish" data-id="${r.id}">Publicar</button><button class="btn btn-sm btn-ghost" data-mod="hold" data-id="${r.id}">Manter retida</button><button class="btn btn-sm btn-danger" data-mod="remove" data-id="${r.id}">Remover</button></div>
      </div>`).join('') || '<p class="muted">Fila vazia.</p>'}`;
      const { items } = await api('/api/admin/class-reports', { headers: { Authorization: `Bearer ${token}` } });
      $('#queue').insertAdjacentHTML('beforeend', `<h2>Turmas informadas por estudantes</h2>${items.length ? `<ul class="community-list">${items.map((it) => `<li><b>${esc(it.term)}</b> ${esc(state.prof.get(it.professor)?.name || it.professor)} — ${esc(state.course.get(it.course)?.name || it.course)} <span class="small muted">${plural(it.count, 'relato', 'relatos')}</span>
        <button class="btn btn-sm btn-danger" data-cdel data-term="${esc(it.term)}" data-prof="${esc(it.professor)}" data-course="${esc(it.course)}">Remover</button></li>`).join('')}</ul>` : '<p class="muted">Nenhum relato.</p>'}`);
      const fb = await api('/api/admin/feedback', { headers: { Authorization: `Bearer ${token}` } });
      const kindLabel = Object.fromEntries(FEEDBACK_KINDS);
      $('#queue').insertAdjacentHTML('beforeend', `<h2 id="feedback">Feedback anônimo</h2>${fb.items.length ? fb.items.map((it) => `<div class="mod-item">
        <p class="small muted"><b>${esc(kindLabel[it.kind] || it.kind)}</b> · ${new Date(it.day * 1000).toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' })}</p>
        <p class="feedback-text">${esc(it.message)}</p>
        <div class="row-gap"><button class="btn btn-sm btn-ghost" data-fdel="${it.id}">Lido — apagar</button></div></div>`).join('') : '<p class="muted">Nenhum feedback.</p>'}`);
    } catch (err) { $('#queue').innerHTML = `<p class="warn">${esc(err.message)}</p>`; }
  };
  $('#admin-form').addEventListener('submit', (e) => { e.preventDefault(); token = new FormData(e.target).get('token'); load(); });
  $('#queue').addEventListener('click', async (e) => {
    const fdel = e.target.closest('[data-fdel]');
    if (fdel) {
      try {
        await api(`/api/admin/feedback/${fdel.dataset.fdel}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
        toast('Apagado.');
        load();
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    const del = e.target.closest('[data-cdel]');
    if (del) {
      try {
        const res = await api('/api/admin/class-reports/delete', { method: 'POST', body: { term: del.dataset.term, professor: del.dataset.prof, course: del.dataset.course }, headers: { Authorization: `Bearer ${token}` } });
        toast(res.message);
        load();
      } catch (err) { toast(err.message, 'error'); }
      return;
    }
    const btn = e.target.closest('[data-mod]');
    if (!btn) return;
    try {
      await api(`/api/admin/reviews/${btn.dataset.id}`, { method: 'POST', body: { action: btn.dataset.mod }, headers: { Authorization: `Bearer ${token}` } });
      toast('Feito.');
      load();
    } catch (err) { toast(err.message, 'error'); }
  });
  if (token) load();
}

// ---------------------------------------------------------------- inicialização
function setupChrome() {
  $('#theme-toggle').addEventListener('click', () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('avalie-theme', root.dataset.theme); } catch (_) { /* idem */ }
  });
  setupGlobalSearch();
  document.addEventListener('click', (e) => { if (!e.target.closest('.hero-search')) { const box = $('#hero-results'); if (box) box.hidden = true; } });
}

setupChrome();
window.addEventListener('hashchange', router);
router();

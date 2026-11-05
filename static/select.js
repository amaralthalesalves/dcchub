/* Seletor estilizado — substitui visualmente cada <select> da página por um botão + lista no estilo do site.
   O <select> nativo continua no DOM (escondido) e é a fonte da verdade: FormData, .value, eventos 'input'/'change',
   'required' e o código existente seguem funcionando. Opções reconstruídas via innerHTML ou valores definidos por
   código (select.value = …) são refletidos automaticamente. Teclado: setas, Home/End, Enter, Esc, digitação. */
(() => {
  'use strict';
  const SEARCH_MIN = 12; // listas com mais opções ganham campo de busca
  const fold = (s) => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
  const indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
  let uid = 0;
  let openSel = null;
  let openPlace = null;

  function labelText(select) {
    const label = select.closest('label');
    if (!label) return select.getAttribute('aria-label') || '';
    const clone = label.cloneNode(true);
    clone.querySelectorAll('select, .sel, small, .err, .hint').forEach((n) => n.remove());
    return clone.textContent.replace(/\s+/g, ' ').trim();
  }

  function enhance(select) {
    if (select.dataset.sel || select.multiple || select.size > 1) return;
    select.dataset.sel = '1';
    const id = `sel-${++uid}`;
    const wrap = document.createElement('div');
    wrap.className = 'sel';
    wrap.innerHTML = `<button type="button" class="sel-btn" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-controls="${id}-list">
        <span class="sel-label"><span class="sel-value"></span><span class="sel-sizer" aria-hidden="true"></span></span><svg class="sel-chev" viewBox="0 0 10 6" aria-hidden="true"><path d="M1 1l4 4 4-4"/></svg></button>
      <div class="sel-pop" hidden>
        <div class="sel-search" hidden><input type="search" autocomplete="off" spellcheck="false" placeholder="Buscar…" aria-label="Filtrar opções"></div>
        <ul class="sel-list" role="listbox" id="${id}-list" tabindex="-1"></ul>
        <p class="sel-empty" hidden>Nada encontrado.</p>
      </div>`;
    select.after(wrap);
    wrap.prepend(select);
    select.classList.add('sel-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    const btn = wrap.querySelector('.sel-btn');
    const valueEl = wrap.querySelector('.sel-value');
    const pop = wrap.querySelector('.sel-pop');
    const list = wrap.querySelector('.sel-list');
    const searchBox = wrap.querySelector('.sel-search');
    const search = searchBox.querySelector('input');
    const empty = wrap.querySelector('.sel-empty');
    const name = labelText(select);
    if (name) btn.setAttribute('aria-label', name);
    let active = -1;

    const items = () => [...list.querySelectorAll('.sel-opt:not([hidden]):not([aria-disabled="true"])')];

    function sync() {
      const opt = select.options[select.selectedIndex];
      valueEl.textContent = opt ? opt.textContent.trim() : '';
      wrap.classList.toggle('placeholder', !opt || opt.value === '');
      btn.disabled = select.disabled;
      list.querySelectorAll('.sel-opt').forEach((li) => li.setAttribute('aria-selected', String(li.dataset.index === String(select.selectedIndex))));
    }

    function build() {
      const html = [];
      let i = 0;
      const optHtml = (o) => {
        const idx = [...select.options].indexOf(o);
        // campo obrigatório: o "Selecione…" é só placeholder, não vira opção na lista
        if (select.required && o.value === '') return '';
        const text = o.textContent.trim();
        // "Algoritmos II — DCC207": o código vai à direita, discreto
        const cut = text.lastIndexOf(' — ');
        const [main, meta] = cut > 0 ? [text.slice(0, cut), text.slice(cut + 3)] : [text, ''];
        return `<li class="sel-opt" role="option" id="${id}-o${i++}" data-index="${idx}" aria-selected="false"${o.disabled ? ' aria-disabled="true"' : ''}>
          <svg class="sel-check" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg><span class="sel-text">${escapeHtml(main)}</span>${meta ? `<span class="sel-meta">${escapeHtml(meta)}</span>` : ''}</li>`;
      };
      for (const child of select.children) {
        if (child.tagName === 'OPTGROUP') {
          html.push(`<li class="sel-group" role="presentation">${escapeHtml(child.label)}</li>`);
          for (const o of child.children) html.push(optHtml(o));
        } else if (child.tagName === 'OPTION') html.push(optHtml(child));
      }
      list.innerHTML = html.join('');
      searchBox.hidden = select.options.length <= SEARCH_MIN;
      // listas curtas: o botão reserva a largura da opção mais longa (não "pula" ao trocar de valor)
      wrap.querySelector('.sel-sizer').innerHTML = searchBox.hidden ? [...select.options].map((o) => `<span>${escapeHtml(o.textContent.trim())}</span>`).join('') : '';
      sync();
    }

    function filter(q) {
      const f = fold(q.trim());
      let shown = 0;
      let group = null;
      let groupHas = false;
      list.querySelectorAll('li').forEach((li) => {
        if (li.classList.contains('sel-group')) {
          if (group) group.hidden = !groupHas;
          group = li; groupHas = false;
          return;
        }
        const hit = !f || fold(li.textContent).includes(f);
        li.hidden = !hit;
        if (hit) { shown++; groupHas = true; }
      });
      if (group) group.hidden = !groupHas;
      empty.hidden = shown > 0;
      setActive(f ? 0 : items().findIndex((li) => li.getAttribute('aria-selected') === 'true'));
    }

    function setActive(n) {
      const all = items();
      list.querySelectorAll('.sel-opt.active').forEach((li) => li.classList.remove('active'));
      active = all.length ? Math.max(0, Math.min(n, all.length - 1)) : -1;
      const li = all[active];
      if (li) {
        li.classList.add('active');
        btn.setAttribute('aria-activedescendant', li.id);
        const top = li.offsetTop, bottom = top + li.offsetHeight;
        if (top < list.scrollTop) list.scrollTop = top - 4;
        else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight + 4;
      } else btn.removeAttribute('aria-activedescendant');
    }

    // A lista fica em position: fixed, calculada a partir do botão: assim não é cortada por containers com
    // rolagem/overflow (diálogos, tabelas, cartões). Abre para cima se não couber embaixo.
    function place() {
      const r = btn.getBoundingClientRect();
      const gap = 6, margin = 12;
      const searchH = searchBox.hidden ? 0 : searchBox.offsetHeight + 4;
      // no celular a barra de abas fica fixa no rodapé: a lista não pode ficar atrás dela
      const nav = document.getElementById('nav');
      const navH = nav && getComputedStyle(nav).position === 'fixed' && !btn.closest('dialog') ? nav.offsetHeight : 0;
      const below = window.innerHeight - navH - r.bottom - gap - margin;
      const above = r.top - gap - margin;
      const want = Math.min(list.scrollHeight, 300) + searchH + 14;
      const up = below < Math.min(want, 220) && above > below;
      const room = up ? above : below;
      list.style.maxHeight = `${Math.max(120, Math.min(300, room - searchH - 14))}px`;
      pop.style.minWidth = `${r.width}px`;
      const width = Math.min(pop.offsetWidth, window.innerWidth - 2 * margin);
      pop.style.left = `${Math.max(margin, Math.min(r.left, window.innerWidth - margin - width))}px`;
      if (up) { pop.style.top = 'auto'; pop.style.bottom = `${window.innerHeight - r.top + gap}px`; }
      else { pop.style.bottom = 'auto'; pop.style.top = `${r.bottom + gap}px`; }
      wrap.classList.toggle('up', up);
      // botão saiu da tela (rolagem): fecha
      if (r.bottom < 0 || r.top > window.innerHeight) close();
    }

    function open() {
      if (btn.disabled || !pop.hidden) return;
      if (openSel && openSel !== close) openSel();
      openSel = close;
      search.value = '';
      filter('');
      pop.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      wrap.classList.add('open');
      place();
      openPlace = place;
      if (!searchBox.hidden) search.focus({ preventScroll: true });
      setActive(items().findIndex((li) => li.getAttribute('aria-selected') === 'true'));
    }

    function close(focus = false) {
      if (pop.hidden) return;
      pop.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      wrap.classList.remove('open', 'up');
      if (openSel === close) { openSel = null; openPlace = null; }
      if (focus) btn.focus();
    }

    function choose(li) {
      if (!li || li.getAttribute('aria-disabled') === 'true') return;
      const idx = Number(li.dataset.index);
      const changed = select.selectedIndex !== idx;
      indexDesc.set.call(select, idx);
      sync();
      close(true);
      if (changed) {
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }

    function key(e) {
      const all = items();
      switch (e.key) {
        case 'ArrowDown': e.preventDefault(); if (pop.hidden) open(); else setActive(active + 1); break;
        case 'ArrowUp': e.preventDefault(); if (pop.hidden) open(); else setActive(active - 1); break;
        case 'Home': if (!pop.hidden && e.target !== search) { e.preventDefault(); setActive(0); } break;
        case 'End': if (!pop.hidden && e.target !== search) { e.preventDefault(); setActive(all.length - 1); } break;
        case 'Enter':
          if (!pop.hidden) { e.preventDefault(); choose(all[active]); } else if (e.target === btn) { e.preventDefault(); open(); }
          break;
        case ' ':
          if (e.target === btn) { e.preventDefault(); if (pop.hidden) open(); else choose(all[active]); }
          break;
        case 'Escape': if (!pop.hidden) { e.preventDefault(); e.stopPropagation(); close(true); } break;
        case 'Tab': close(); break;
        default:
          // digitação no botão: vai para a busca (lista longa) ou pula para a opção que começa com a letra
          if (e.target === btn && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
            if (pop.hidden) open();
            if (!searchBox.hidden) { search.focus(); return; }
            const k = fold(e.key);
            const start = active + 1;
            const order = [...all.slice(start), ...all.slice(0, start)];
            const hit = order.find((li) => fold(li.textContent.trim()).startsWith(k));
            if (hit) setActive(all.indexOf(hit));
          }
      }
    }

    btn.addEventListener('click', () => (pop.hidden ? open() : close()));
    btn.addEventListener('keydown', key);
    search.addEventListener('keydown', key);
    search.addEventListener('input', () => filter(search.value));
    list.addEventListener('mousedown', (e) => e.preventDefault()); // mantém o foco no botão/busca
    list.addEventListener('click', (e) => choose(e.target.closest('.sel-opt')));
    list.addEventListener('mousemove', (e) => {
      const li = e.target.closest('.sel-opt');
      if (li && !li.classList.contains('active')) setActive(items().indexOf(li));
    });
    // clicar no <label> foca o select escondido: repassa para o botão
    select.addEventListener('focus', () => btn.focus());
    select.addEventListener('change', sync);
    new MutationObserver(build).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'label'] });
    // select.value = … / selectedIndex = … por código também atualizam o texto do botão
    Object.defineProperty(select, 'value', { configurable: true, get() { return valueDesc.get.call(this); }, set(v) { valueDesc.set.call(this, v); sync(); } });
    Object.defineProperty(select, 'selectedIndex', { configurable: true, get() { return indexDesc.get.call(this); }, set(v) { indexDesc.set.call(this, v); sync(); } });
    build();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function scan(root) {
    if (root.tagName === 'SELECT') enhance(root);
    else root.querySelectorAll?.('select').forEach(enhance);
  }

  document.addEventListener('mousedown', (e) => { if (openSel && !e.target.closest('.sel.open')) openSel(); });
  // rolagem (de qualquer container) ou redimensionamento: a lista acompanha o botão
  window.addEventListener('resize', () => openPlace && openPlace());
  document.addEventListener('scroll', (e) => { if (openPlace && !e.target.closest?.('.sel-pop')) openPlace(); }, true);
  // formulário resetado: reflete os valores restaurados
  document.addEventListener('reset', (e) => setTimeout(() => e.target.querySelectorAll('select[data-sel]').forEach((s) => s.dispatchEvent(new Event('change'))), 0));

  const start = () => {
    scan(document.body);
    new MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && scan(n))))
      .observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();

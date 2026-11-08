// Teste de ponta a ponta no navegador (Chrome headless via DevTools Protocol, sem dependências).
// Requer Node 22+, google-chrome e um servidor rodando com o banco de demonstração:
//   python3 tests/seed_demo.py /tmp/demo.db && DCCHUB_DB=/tmp/demo.db python3 app.py
//   node tests/e2e_browser.mjs http://localhost:8000
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const port = 9333, base = process.argv[2] || 'http://localhost:8000';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// O Chrome do runner do GitHub às vezes demora ou não sobe na primeira vez: espera até 30 s e tenta abrir de novo
// uma vez. Se não der, falha dizendo por quê (a saída de erro do Chrome), não com um TypeError obscuro.
let chrome, target, chromeLog = '';
for (let attempt = 1; attempt <= 2 && !target; attempt++) {
  chrome = spawn('google-chrome', ['--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'avalie-e2e-'))}`, '--window-size=1300,1000', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', (d) => { chromeLog = (chromeLog + d).slice(-4000); });
  chrome.on('error', (e) => { chromeLog += `\n${e.message}`; });
  for (let i = 0; i < 150 && !target; i++) {
    await sleep(200); try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === 'page'); } catch {}
  }
  if (!target) { console.log(`Chrome não respondeu (tentativa ${attempt})`); chrome.kill('SIGKILL'); await sleep(1000); }
}
if (!target) { console.error(`Chrome não abriu a depuração em 2 tentativas. Saída de erro:\n${chromeLog}`); process.exit(1); }
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r, reject) => { ws.addEventListener('open', r); ws.addEventListener('error', () => reject(new Error('WebSocket do Chrome não conectou'))); });
let id = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map((a) => a.value || a.description).join(' ')); });
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: `(async()=>{${expr}})()`, awaitPromise: true, returnByValue: true }); if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description); return r.result.result.value; };
await send('Runtime.enable');
const go = async (hash) => { await ev(`location.href='${base}/${hash}'`); await sleep(1200); };
const ok = (cond, msg) => { console.log(`${cond ? '✓' : '✗'} ${msg}`); if (!cond) process.exitCode = 1; };
await send('Page.enable');
await send('Page.navigate', { url: `${base}/#/` }); await sleep(1500);

// 0) regras da comunidade: aparecem na primeira visita, Esc não fecha, só sai aceitando
ok(await ev(`return !!document.querySelector('#rules-dialog[open]')`), 'regras da comunidade aparecem na primeira visita');
ok(await ev(`return document.querySelector('#rules-dialog button[value=accept]').disabled`), 'botão Participar começa desligado');
await ev(`document.querySelector('#rules-dialog').dispatchEvent(new Event('cancel',{cancelable:true}))`);
ok(await ev(`return !!document.querySelector('#rules-dialog[open]')`), 'Esc não fecha as regras');
await ev(`const b=document.querySelector('#rules-dialog input[name=ok]'); b.click(); document.querySelector('#rules-dialog button[value=accept]').click()`); await sleep(200);
ok(await ev(`return !document.querySelector('#rules-dialog') && localStorage.getItem('dcchub-regras') === '1'`), 'aceitar fecha as regras e lembra no navegador');
await go('#/'); ok(await ev(`return !document.querySelector('#rules-dialog')`), 'regras não voltam depois de aceitas');

// 1) busca global
ok(await ev(`const i=document.querySelector('#global-search'); i.value='dcc206'; i.dispatchEvent(new Event('input')); return document.querySelector('#global-results .search-item b')?.textContent`) === 'Algoritmos I', 'busca global por código acha Algoritmos I');
await ev(`document.querySelector('#global-search').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter'}))`); await sleep(1200);
ok((await ev(`return location.hash`)).startsWith('#/disciplina/DCC206'), 'Enter abre a disciplina');

// 2) formulário de avaliação completo
await go('#/avaliar?professor=renato-vimieiro'); await sleep(1500);
const courseOpts = await ev(`return [...document.querySelectorAll('#f-course optgroup')].map(g=>g.label)`);
ok(courseOpts[0]?.startsWith('Lecionadas por Renato'), 'disciplinas lecionadas aparecem primeiro: ' + courseOpts[0]);
await ev(`const s=document.querySelector('#f-course'); s.value='DCC207'; s.dispatchEvent(new Event('change'))`); await sleep(300);
const termGroups = await ev(`return [...document.querySelectorAll('#f-term optgroup')].map(g=>g.label+':'+g.children.length)`);
ok(termGroups[0]?.startsWith('Turmas confirmadas'), 'semestres confirmados sugeridos: ' + termGroups.join(' | '));
// envio vazio mostra erros
await ev(`document.querySelector('#review-form').requestSubmit()`); await sleep(200);
await sleep(300);
ok((await ev(`return document.querySelector('[data-err="overall"]')?.textContent || ''`)).length > 0, 'validação no cliente aponta notas faltando');
ok((await ev(`return document.querySelector('[data-err="passing"]')?.textContent || ''`)).length > 0, 'dificuldade para passar é pergunta separada e obrigatória');
await ev(`const t=document.querySelector('#f-term'); t.value=t.querySelector('optgroup option').value;
  for (const [k,v] of Object.entries({overall:5,didactics:4,fairness:5,difficulty:3,passing:2,workload:4})) document.querySelector('input[name='+k+'][value="'+v+'"]').click();
  document.querySelector('input[name=retake][value=true]').click();
  [...document.querySelectorAll('input[name=tags]')].slice(0,2).forEach(x=>x.click());
  const c=document.querySelector('#f-comment'); c.value='Ótima disciplina, provas coerentes com as listas e professor muito disponível.'; c.dispatchEvent(new Event('input',{bubbles:true}));
  document.querySelector('#review-form').requestSubmit();`);
await sleep(2000);
ok((await ev(`return location.hash`)) === '#/professor/renato-vimieiro', 'após publicar vai para a página do docente');
ok(await ev(`return !!document.querySelector('.review .review-foot a[href^="#/avaliar?edit="]')`), 'a própria avaliação aparece com Editar');
// 3) repetir dá 409 com link de edição
await go('#/avaliar?professor=renato-vimieiro&course=DCC207'); await sleep(800);
await ev(`const t=document.querySelector('#f-term'); t.value='2024.1';
  for (const k of ['overall','didactics','fairness','difficulty','passing','workload']) document.querySelector('input[name='+k+'][value="3"]').click();
  const c=document.querySelector('#f-comment'); c.value='Segunda tentativa de avaliação da mesma turma pelo mesmo navegador.'; document.querySelector('#review-form').requestSubmit();`);
await sleep(1200);
ok((await ev(`return document.querySelector('#form-message').innerHTML`)).includes('edit='), 'duplicada oferece editar a existente');
// 4) editar
const editHref = await ev(`return document.querySelector('#form-message a').getAttribute('href')`);
await go(editHref); await sleep(800);
ok((await ev(`return document.querySelector('#f-comment').value`)).startsWith('Ótima'), 'edição carrega o texto atual');
// 5) voto útil em avaliação de outra pessoa
await go('#/disciplina/DCC034'); await sleep(800);
const before = await ev(`return document.querySelector('.helpful b').textContent`);
await ev(`document.querySelector('.helpful').click()`); await sleep(800);
ok(await ev(`return document.querySelector('.helpful').classList.contains('on')`), `voto útil marcado (antes "${before}")`);
// 5b) evolução por semestre: gráfico, tooltip no foco, clique filtra as avaliações pelo semestre
await go('#/professor/adriano-cesar-machado-pereira');
const evo = await ev(`
  const hits = document.querySelectorAll('.evo-hit');
  if (hits.length < 2) return { hits: hits.length };
  hits[1].dispatchEvent(new FocusEvent('focus'));
  const tip = document.querySelector('.evo-tip');
  const tipOk = !tip.hidden && /geral/.test(tip.textContent);
  const term = hits[1].getAttribute('aria-label').split(':')[0];
  hits[1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 300));
  const sel = document.querySelector('[data-filter-term="reviews"]').value;
  const cards = [...document.querySelectorAll('.review-list .review')];
  return { hits: hits.length, tipOk, term, sel, cards: cards.length, allTerm: cards.every((c) => c.querySelector('header p').textContent.trim().startsWith(term)), table: document.querySelectorAll('.evo-table tbody tr').length };`);
ok(evo.hits >= 2 && evo.table === evo.hits, `gráfico de evolução com um ponto por semestre (${evo.hits}) e tabela equivalente`);
ok(evo.tipOk, 'tooltip do semestre aparece no foco do teclado');
ok(evo.sel === evo.term && evo.cards > 0 && evo.allTerm, `clicar em ${evo.term} filtra as avaliações daquele semestre (${evo.cards})`);
// 6) grade
await go('#/oferta'); await sleep(1000);
await ev(`document.querySelectorAll('[data-pick]')[0].click()`); await sleep(200);
await ev(`document.querySelectorAll('[data-pick]')[0].click()`); await sleep(100);
ok((await ev(`return document.querySelectorAll('#planner .week-item').length`)) === 0, 'clicar duas vezes tira da grade (sem listeners duplicados)');
await ev(`document.querySelectorAll('[data-pick]')[0].click()`); await sleep(200);
await go('#/'); await go('#/oferta'); await sleep(800);
ok((await ev(`return document.querySelectorAll('#planner .week-item').length`)) > 0, 'grade persiste ao voltar à página');
await ev(`document.querySelectorAll('.offer-row:not(.on) [data-pick]')[0].click()`); await sleep(200);
ok((await ev(`return document.querySelectorAll('.offer-row.on').length`)) === 2, 'segunda turma adicionada (um clique = uma ação)');
const clash = await ev(`return document.querySelectorAll('.week-cell.clash').length`);
console.log('  células em conflito:', clash);
// 6b) exportar a grade para .ics (gerado no navegador; captura o Blob em vez de baixar)
const ics = await ev(`
  let blob = null; const orig = URL.createObjectURL; URL.createObjectURL = (b) => { blob = b; return orig.call(URL, b); };
  document.querySelector('#ics-export').open = true;
  const f = document.querySelector('#ics-form');
  f.start.value = '2026-08-10'; f.end.value = '2026-12-12';
  f.requestSubmit();
  await new Promise((r) => setTimeout(r, 300));
  URL.createObjectURL = orig;
  return blob ? { type: blob.type, text: await blob.text() } : null;`);
ok(ics && ics.type.startsWith('text/calendar') && (ics.text.match(/BEGIN:VEVENT/g) || []).length >= 2 && /RRULE:FREQ=WEEKLY;UNTIL=20261213T025959Z/.test(ics.text), 'grade exportada para .ics com eventos semanais');
await go('#/oferta'); await sleep(800);
ok((await ev(`document.querySelector('#ics-export').open = true; return document.querySelector('#ics-form').start.value`)) === '2026-08-10', 'datas das aulas lembradas neste navegador');
// 7) minhas avaliações + código
await go('#/minhas'); await sleep(800);
ok((await ev(`return document.querySelectorAll('.review').length`)) === 1, 'minhas avaliações lista 1');
await ev(`document.querySelector('#show-code').click()`); await sleep(600);
ok((await ev(`return document.querySelector('#code-box').textContent.length`)) === 43, 'código de recuperação exibido');
// 8) apagar
await ev(`window.confirm=()=>true; document.querySelector('[data-action="delete-review"]').click()`); await sleep(1500);
ok((await ev(`return document.querySelectorAll('.review').length`)) === 0, 'avaliação apagada');
// 9) feedback anônimo: rascunho guardado, erro de validação, envio
await go('#/feedback');
await ev(`document.querySelector('input[name=kind][value=problema]').click(); const t=document.querySelector('#fb-message'); t.value='curto'; t.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#feedback-form').requestSubmit();`);
await sleep(600);
ok((await ev(`return document.querySelector('[data-err=message]').textContent`)).length > 0, 'feedback curto demais mostra erro no campo');
await go('#/'); await go('#/feedback');
ok(await ev(`return document.querySelector('#fb-message').value === 'curto' && document.querySelector('input[name=kind][value=problema]').checked`), 'rascunho do feedback lembrado neste navegador');
await ev(`const t=document.querySelector('#fb-message'); t.value='A grade poderia avisar quando duas turmas têm prova no mesmo dia.'; t.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('#feedback-form').requestSubmit();`);
await sleep(1000);
ok(/Obrigado/.test(await ev(`return document.querySelector('.callout')?.textContent || ''`)), 'feedback enviado com agradecimento');
ok(await ev(`return localStorage.getItem('dcchub-feedback') === null`), 'rascunho apagado após o envio');
ok(errors.length === 0, 'nenhum erro de JS no console' + (errors.length ? ': ' + errors.join(' | ') : ''));
ws.close(); chrome.kill('SIGKILL');

// Testes do gerador de .ics da grade (static/ics.js), sem dependências.
//   node tests/test_ics.mjs
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import test from 'node:test';

const { build } = createRequire(import.meta.url)('../static/ics.js');
const now = new Date('2026-08-01T15:00:00Z');
// 2026-08-10 é uma segunda-feira
const period = { start: '2026-08-10', end: '2026-12-12', term: '2026.2', now };
const unfold = (ics) => ics.replace(/\r\n /g, '');
const events = (ics) => unfold(ics).split('BEGIN:VEVENT').slice(1).map((e) => e.split('END:VEVENT')[0]);

test('cada horário vira um evento semanal a partir do primeiro dia certo', () => {
  const { ics, events: n, skipped } = build([{ uid: 'DCC206|TM1', title: 'DCC206 — Algoritmos I',
    schedule: [{ day: 2, start: '14:55', end: '16:35' }, { day: 4, start: '14:55', end: '16:35' }] }], period);
  assert.equal(n, 2);
  assert.equal(skipped, 0);
  const [tue, thu] = events(ics);
  assert.match(tue, /DTSTART;TZID=America\/Sao_Paulo:20260811T145500/);
  assert.match(tue, /DTEND;TZID=America\/Sao_Paulo:20260811T163500/);
  assert.match(thu, /DTSTART;TZID=America\/Sao_Paulo:20260813T145500/);
  // último dia inteiro em Brasília: 23:59:59 de 12/12 = 02:59:59Z de 13/12
  assert.match(tue, /RRULE:FREQ=WEEKLY;UNTIL=20261213T025959Z/);
  assert.match(tue, /DTSTAMP:20260801T150000Z/);
  assert.notEqual(tue.match(/UID:(.*)/)[1], thu.match(/UID:(.*)/)[1], 'UID único por evento');
});

test('formato iCalendar: CRLF, fuso, cabeçalho e rodapé', () => {
  const { ics } = build([{ uid: 'x', title: 'X', schedule: [{ day: 1, start: '07:30', end: '09:10' }] }], period);
  assert.ok(ics.startsWith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'));
  assert.ok(ics.endsWith('END:VCALENDAR\r\n'));
  assert.ok(!/[^\r]\n/.test(ics), 'só CRLF');
  assert.match(ics, /BEGIN:VTIMEZONE\r\nTZID:America\/Sao_Paulo/);
  assert.match(ics, /TZOFFSETTO:-0300/);
  assert.match(events(ics)[0], /DTSTART;TZID=America\/Sao_Paulo:20260810T073000/, 'início numa segunda já vale');
});

test('texto escapado e linhas longas dobradas sem partir acentos', () => {
  const title = 'Tópicos em Computação; Visão, Gráficos e Interação \\ ' + 'ção'.repeat(30);
  const { ics } = build([{ uid: 'TOP|1', title, description: 'Turma A\nDocentes: Ana, Bruno',
    schedule: [{ day: 3, start: '19:00', end: '20:40' }] }], period);
  for (const line of ics.split('\r\n')) assert.ok(new TextEncoder().encode(line).length <= 75, `linha longa: ${line}`);
  const ev = events(ics)[0];
  assert.ok(ev.includes('SUMMARY:Tópicos em Computação\\; Visão\\, Gráficos e Interação \\\\ ção'));
  assert.ok(ev.includes('DESCRIPTION:Turma A\\nDocentes: Ana\\, Bruno'));
  assert.ok(!ics.includes('�'));
});

test('UID só com caracteres seguros', () => {
  const { ics } = build([{ uid: 'DCC206|TM1|ana silva,bruno|Ter 14:55–16:35', title: 'X', schedule: [{ day: 2, start: '14:55', end: '16:35' }] }], period);
  assert.match(events(ics)[0], /UID:[A-Za-z0-9._-]+@dcchub/);
});

test('turmas sem horário ficam de fora, horários inválidos são ignorados', () => {
  const { events: n, skipped } = build([
    { uid: 'a', title: 'Sem horário', schedule: [] },
    { uid: 'b', title: 'Inválido', schedule: [{ day: 9, start: '10:00', end: '11:00' }, { day: 2, start: '25:00', end: '26:00' }, { day: 2, start: '11:00', end: '10:00' }] },
    { uid: 'c', title: 'Ok', schedule: [{ day: 5, start: '13:00', end: '14:40' }] },
  ], period);
  assert.equal(n, 1);
  assert.equal(skipped, 2);
});

test('dia da semana que não ocorre no período não gera evento', () => {
  const { events: n } = build([{ uid: 'a', title: 'Terça', schedule: [{ day: 2, start: '10:00', end: '11:00' }] }],
    { start: '2026-08-10', end: '2026-08-10', now });
  assert.equal(n, 0);
});

test('datas obrigatórias e em ordem', () => {
  const items = [{ uid: 'a', title: 'A', schedule: [{ day: 1, start: '10:00', end: '11:00' }] }];
  assert.throws(() => build(items, { start: '', end: '2026-12-12' }), /datas/);
  assert.throws(() => build(items, { start: '2026-13-40', end: '2026-12-12' }), /datas/);
  assert.throws(() => build(items, { start: '2026-12-12', end: '2026-08-10' }), /depois do início/);
});

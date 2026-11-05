// Exporta a grade para iCalendar (.ics) — gerado no próprio navegador: a grade nunca vai para o servidor.
// Cada horário de cada turma vira um evento semanal (RRULE) entre o início e o fim das aulas informados pela pessoa.
// Feriados e recessos não são removidos: o calendário acadêmico muda a cada semestre e não temos uma fonte confiável.
(function (root) {
  'use strict';

  const TZ = 'America/Sao_Paulo';
  const BYDAY = ['', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'];
  // Brasília sem horário de verão desde 2019: UTC-3 o ano todo
  const VTIMEZONE = ['BEGIN:VTIMEZONE', `TZID:${TZ}`, 'BEGIN:STANDARD', 'DTSTART:19700101T000000',
    'TZOFFSETFROM:-0300', 'TZOFFSETTO:-0300', 'TZNAME:-03', 'END:STANDARD', 'END:VTIMEZONE'];

  const pad = (n) => String(n).padStart(2, '0');
  const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(String(s || '')) && !Number.isNaN(Date.parse(`${s}T00:00:00Z`));
  const isTime = (s) => /^([01]\d|2[0-3]):[0-5]\d$/.test(String(s || ''));

  // texto de propriedade: \ ; , e quebras de linha escapados (RFC 5545 §3.3.11)
  const text = (v) => String(v ?? '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');

  // linhas com mais de 75 octetos são dobradas (RFC 5545 §3.1), sem partir um caractere UTF-8 no meio
  function fold(line) {
    const enc = new TextEncoder();
    if (enc.encode(line).length <= 75) return line;
    const out = [];
    let cur = '';
    let size = 0;
    for (const ch of line) {
      const n = enc.encode(ch).length;
      if (size + n > (out.length ? 74 : 75)) { out.push(cur); cur = ''; size = 0; }
      cur += ch;
      size += n;
    }
    out.push(cur);
    return out.join('\r\n ');
  }

  // primeira data >= start (AAAA-MM-DD) que cai no dia da semana pedido (1 = segunda … 6 = sábado)
  function firstOn(start, day) {
    const d = new Date(`${start}T12:00:00Z`);
    const want = day % 7; // getUTCDay: 0 = domingo, 1 = segunda…
    while (d.getUTCDay() !== want) d.setUTCDate(d.getUTCDate() + 1);
    return d;
  }
  const ymd = (d) => `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`;
  const hm = (t) => t.replace(':', '') + '00';
  const stamp = (d) => d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');

  // UNTIL em UTC: 23:59:59 do último dia em Brasília = 02:59:59Z do dia seguinte
  function untilUtc(end) {
    const d = new Date(`${end}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + 1);
    return `${ymd(d)}T025959Z`;
  }

  /**
   * items: [{ uid, title, description?, schedule: [{ day: 1..6, start: 'HH:MM', end: 'HH:MM' }] }]
   * opts:  { start: 'AAAA-MM-DD', end: 'AAAA-MM-DD', term?: '2026.2', now?: Date }
   * Devolve { ics, events, skipped }: turmas sem horário são puladas (e contadas).
   */
  function build(items, opts) {
    if (!isDate(opts?.start) || !isDate(opts?.end)) throw new Error('Informe as datas de início e fim das aulas.');
    if (opts.end < opts.start) throw new Error('O fim das aulas precisa ser depois do início.');
    const now = stamp(opts.now || new Date());
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//DCCHub//Grade//PT-BR', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
      `X-WR-CALNAME:${text(`Grade ${opts.term || ''}`.trim())}`, `X-WR-TIMEZONE:${TZ}`, ...VTIMEZONE];
    let events = 0;
    let skipped = 0;
    for (const item of items) {
      const slots = (item.schedule || []).filter((s) => BYDAY[s.day] && isTime(s.start) && isTime(s.end) && s.start < s.end);
      if (!slots.length) { skipped += 1; continue; }
      slots.forEach((s, i) => {
        const first = firstOn(opts.start, s.day);
        if (ymd(first) > opts.end.replace(/-/g, '')) return; // dia da semana que não ocorre no período
        lines.push('BEGIN:VEVENT',
          `UID:${String(item.uid).replace(/[^A-Za-z0-9._-]/g, '-')}-${s.day}-${hm(s.start)}-${i}@dcchub`,
          `DTSTAMP:${now}`,
          `DTSTART;TZID=${TZ}:${ymd(first)}T${hm(s.start)}`,
          `DTEND;TZID=${TZ}:${ymd(first)}T${hm(s.end)}`,
          `RRULE:FREQ=WEEKLY;UNTIL=${untilUtc(opts.end)}`,
          `SUMMARY:${text(item.title)}`,
          ...(item.description ? [`DESCRIPTION:${text(item.description)}`] : []),
          'TRANSP:OPAQUE',
          'END:VEVENT');
        events += 1;
      });
    }
    lines.push('END:VCALENDAR');
    return { ics: lines.map(fold).join('\r\n') + '\r\n', events, skipped };
  }

  const api = { build, isDate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.DCCHubICS = api;
})(typeof window !== 'undefined' ? window : globalThis);

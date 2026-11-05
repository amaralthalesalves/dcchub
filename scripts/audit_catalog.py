#!/usr/bin/env python3
"""Auditoria: toda linha de todas as páginas oficiais de oferta tem de bater com o catálogo.

Uso: python3 scripts/audit_catalog.py   (lê o site do DCC; sai com código 1 se houver divergência)
"""
import json, sys, collections, difflib
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent)); import sync_dcc as s  # noqa: E402
c = json.loads(s.OUT.read_text()); a = json.loads(s.archive_path(s.OUT).read_text())
courses = c['courses'] + a['courses']; offs = c['offerings'] + a['offerings']
alias = {x2: x['id'] for x in courses for x2 in [x['id'], *x.get('aliases', [])]}
P = {p['slug']: p for p in c['professors']}
def tok_eq(x, y): return x == y or s.tok_match(x, y) or (len(x) > 5 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.85)
FIRST = collections.Counter(s.sig(p['name'])[0] for p in P.values() if s.sig(p['name']))
def fits(raw, full):
    r, f = s.sig(raw), s.sig(full)
    if r and f and r[0] == f[0] and FIRST[r[0]] == 1:
        return True  # único docente com esse prenome ("Jeroen Antonius" = "Jeroen van de Graaf")
    if len(r) >= 2 and len(f) >= 2 and tok_eq(r[0], f[0]) and s.surname_compatible(r, f) and s.surname_compatible(f, r):
        return True  # mesmo prenome e sobrenome compatível ("Clodoveu Augusto Davis Junior" = "Clodoveu Davis")
    long_ = [t for t in r if len(t) > 3]
    if not (r and f and long_) or not tok_eq(r[0], f[0]) and len(r) > 1:
        return bool(long_) and len(r) == 1 and any(tok_eq(r[0], g) for g in f)  # só o sobrenome ("Loureiro")
    short, longer = (r, f) if len(r) <= len(f) else (f, r)
    return all(any(tok_eq(t, g) for g in longer) for t in short if len(t) > 3 or t == short[0])
stats = collections.Counter(); bad = []
for url in c['sources']['offerings']:
    for r in s.parse_offerings(s.fetch(url.replace(s.BASE, ''))):
        cid, _, _ = s.course_identity(r['codes'], r['name'])
        target = alias.get(cid) or next((x['id'] for x in courses if s.name_key(x['name']) == s.name_key(r['name'])), None)
        cand = [o for o in offs if o['course'] == target and o['schedule'] == r['schedule'] and set(r['codes']) & set(o['codes'])]
        if not target or not cand:
            stats['linha sem turma correspondente'] += 1; bad.append((url, r)); continue
        stats['linhas'] += 1
        for t in r['teachers']:
            names = [P[p] for o in cand for p in o['professors'] if p in P]
            if any(fits(t, p['name']) for p in names) or any(p['status'] == 'substituto' for p in names) and ('subst' in t.lower() or len(s.sig(t)) == 1):
                stats['docente identificado e compatível'] += 1
            elif any(t in o.get('unresolved', []) or s.SUBST_LABEL in o.get('unresolved', []) and (s.SUBST_ONLY.match(t) or t == s.SUBST_LABEL) for o in cand):
                stats['mostrado como não identificado'] += 1
            else:
                stats['DIVERGENTE'] += 1; bad.append((r['codes'], r['name'], t, [p['name'] for p in names]))
print(dict(stats)); [print(' ', b) for b in bad]
unres = collections.Counter(n for o in offs for n in o.get('unresolved', []))
print('nomes não identificáveis (mostrados como "(não identificado)"):', dict(unres))
sys.exit(1 if stats['DIVERGENTE'] or stats['linha sem turma correspondente'] else 0)

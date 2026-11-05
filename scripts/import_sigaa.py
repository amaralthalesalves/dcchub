#!/usr/bin/env python3
"""Importa o "Mapa de oferta por departamento" do SIGAA (PDF) para data/ofertas-sigaa/<semestre>.json.

Usado quando o DCC ainda não publicou a página "Oferta de disciplinas" do semestre. Só guarda os campos que o
DCC já publica nas próprias páginas de oferta: código, título, turma, horário e NOME dos docentes. O número de
inscrição dos docentes, as vagas e os cursos demandantes são descartados na leitura e nunca são gravados.

O relatório do SIGAA é marcado como de uso restrito; publicar estes dados é decisão de quem mantém o site.

Uso:
  python3 scripts/import_sigaa.py caminho/para/mapa.pdf
  python3 scripts/sync_dcc.py            # incorpora o semestre ao catálogo (a página oficial do DCC, se existir, prevalece)

Requer `pdftotext` (poppler-utils). O PDF tem as células em várias linhas e centralizadas na vertical; por isso o
parser usa as coordenadas de cada palavra (pdftotext -bbox) e atribui cada uma à coluna pelo cabeçalho da página.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "ofertas-sigaa"
CODE = re.compile(r"^[A-Z]{3}\d{3}$")
TIME = re.compile(r"^(\d{2}:\d{2}|\((Seg|Ter|Qua|Qui|Sex|S[áa]b|Dom)\)|-)$")
# "NOME COMPLETO, 123456, 60" (nº de inscrição pode terminar em X); vários docentes em sequência
TEACHER = re.compile(r"([A-ZÀ-ÖØ-Ý][A-ZÀ-ÖØ-Ý' .-]+?)\s*,\s*[\dX]{5,7}\s*,\s*\d+")


def words_by_page(pdf: Path) -> list[list[dict]]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bbox.html"
        subprocess.run(["pdftotext", "-bbox", str(pdf), str(out)], check=True)
        raw = out.read_text(encoding="utf-8")
    pages = []
    for page in re.findall(r"<page [^>]*>(.*?)</page>", raw, re.S):
        ws = [{"x": float(a), "y": float(b), "x2": float(c), "t": html.unescape(t)}
              for a, b, c, _, t in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', page)]
        pages.append(ws)
    return pages


def semester(pages: list[list[dict]]) -> str | None:
    text = " ".join(w["t"] for w in pages[0][:80]) if pages else ""
    m = re.search(r"SEMESTRE:\s*(\d{4})\s*/\s*([12])", text)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def parse_page(ws: list[dict]) -> list[dict]:
    head = {}
    for w in ws:
        if w["t"] in ("Código", "Título", "CH", "Oft.", "Id.", "Horário", "Sit.") and w["t"] not in head:
            head[w["t"]] = w
    if not {"Código", "CH", "Id.", "Sit."} <= head.keys():
        return []
    header_bottom = max(head[k]["y"] for k in head) + 6
    body = [w for w in ws if w["y"] > header_bottom]
    codes = sorted((w for w in body if CODE.match(w["t"]) and w["x"] < head["CH"]["x"] - 40), key=lambda w: w["y"])
    if not codes:
        return []
    # limites de coluna desta página
    times = [w for w in body if TIME.match(w["t"]) and w["x"] > head["Id."]["x"]]
    sched_left = min((w["x"] for w in times), default=head["Id."]["x"] + 60) - 2
    sched_right = max((w["x2"] for w in times), default=sched_left + 90) + 2
    sit_left = head["Sit."]["x"] - 15
    footer = min((w["y"] for w in body if w["t"] in ("MAPA", "Emitido")), default=10_000)
    rows = []
    for i, code in enumerate(codes):
        top = code["y"] - 3
        bottom = codes[i + 1]["y"] - 3 if i + 1 < len(codes) else footer - 3
        cell = [w for w in body if top <= w["y"] < bottom and w is not code]
        order = lambda col: " ".join(w["t"] for w in sorted(col, key=lambda w: (round(w["y"]), w["x"])))  # noqa: E731
        title = order([w for w in cell if code["x2"] < w["x"] < head["CH"]["x"] - 4])
        klass = order([w for w in cell if head["Id."]["x"] - 10 <= w["x"] < head["Id."]["x"] + 22])
        sched = order([w for w in cell if sched_left <= w["x"] <= sched_right])
        teachers_text = order([w for w in cell if sched_right < w["x"] < sit_left])
        teachers = [" ".join(n.split()).strip(" ,") for n in TEACHER.findall(teachers_text)]
        rows.append({"code": code["t"], "title": " ".join(title.split()), "class": klass or None,
                     "schedule_text": sched, "teachers": teachers})
    return rows


def merge_cross_listed(rows: list[dict], name_key) -> list[dict]:
    """O SIGAA lista uma linha por código; a mesma turma de um tópico aparece como DCC030, DCC049, DCC831…
    Junta linhas da mesma disciplina/tema (chave de nome do sync, que ignora "TÓPICOS EM … -", plural, grafia),
    mesmo horário e mesmos docentes (como nas páginas do DCC: 'DCC030/049/831')."""
    groups: dict[tuple, dict] = {}
    out = []
    for r in rows:
        key = (name_key(r["title"]), r["schedule_text"], tuple(sorted(r["teachers"])))
        if r["teachers"] and r["schedule_text"] and key in groups:
            g = groups[key]
            if r["code"] not in g["codes"]:
                g["codes"].append(r["code"])
            if r["class"] and r["class"] not in g["classes"]:
                g["classes"].append(r["class"])
            continue
        g = {"codes": [r["code"]], "title": r["title"], "classes": [r["class"]] if r["class"] else [],
             "schedule_text": r["schedule_text"], "teachers": r["teachers"]}
        groups[key] = g
        out.append(g)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--term", help="semestre (ex.: 2026.2); por padrão, lido do cabeçalho do relatório")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sync_dcc  # noqa: PLC0415  (mesmo parser de horário do sync)

    pages = words_by_page(args.pdf)
    term = args.term or semester(pages)
    if not term:
        print("Não encontrei o semestre no cabeçalho; use --term 2026.2", file=sys.stderr)
        return 1
    rows = [r for ws in pages for r in parse_page(ws)]
    merged = merge_cross_listed(rows, sync_dcc.name_key)
    offerings, problems = [], []
    for g in merged:
        schedule = sync_dcc.parse_schedule(g["schedule_text"])
        if g["schedule_text"].strip(" -") and not schedule:
            problems.append(f"horário não entendido: {g['codes']} {g['schedule_text']!r}")
        if not g["teachers"]:
            problems.append(f"sem docente: {g['codes']} {g['title']}")
        offerings.append({"codes": g["codes"], "name": g["title"], "class": "/".join(g["classes"]) or None,
                          "schedule": schedule, "teachers": g["teachers"], "plan": None})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{term}.json"
    out.write_text(json.dumps({"term": term, "source": "SIGAA — Mapa de oferta por departamento",
                               "note": "Só código, título, turma, horário e nome dos docentes.",
                               "offerings": offerings}, ensure_ascii=False, indent=1) + "\n")
    print(f"✓ {out}: {len(rows)} linhas do SIGAA -> {len(offerings)} turmas "
          f"({len({c for o in offerings for c in o['codes']})} códigos)", file=sys.stderr)
    for p in problems:
        print("  !", p, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

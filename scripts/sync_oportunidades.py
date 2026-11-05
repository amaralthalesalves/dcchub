#!/usr/bin/env python3
"""Coleta as oportunidades (bolsas de IC/pesquisa, estágios, pós-doc…) de https://dcc.ufmg.br/oportunidades/
para data/oportunidades.json.

A listagem traz as chamadas abertas e fechadas, com título, prazo e categorias (nas classes CSS de cada item);
cada página de detalhe traz o texto da chamada e campos no rodapé ("Início divulgação", "Tipo", "Situação",
"Link para candidatura"). Daqui saem: resumo, valores de bolsa, carga horária, público e os docentes citados,
ligados aos perfis do catálogo. O site mostra só um resumo e sempre aponta para a chamada original.

Uso:
  python3 scripts/sync_oportunidades.py
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_dcc  # noqa: E402  (fetch, tokens, catálogo)

OUT = sync_dcc.OUT.parent / "oportunidades.json"
LIST_PATH = "/oportunidades/"

TYPES = {  # categoria do WordPress -> tipo exibido
    "iniciacao-cientifica-vaga": "Iniciação científica", "bolsa-de-pesquisa-vaga": "Bolsa de pesquisa",
    "estagio-pt-vaga": "Estágio", "pos-doutorado": "Pós-doutorado", "mestrado": "Mestrado",
    "emprego-pt": "Emprego", "monitoria": "Monitoria",
}
AREAS = {
    "inteligencia-artificial": "Inteligência artificial", "ciberseguranca": "Cibersegurança", "cybersecurity": "Cibersegurança",
    "ciencia-de-dados": "Ciência de dados", "aprendizado-de-maquina": "Aprendizado de máquina",
    "internet-das-coisas": "Internet das coisas", "compiladores": "Compiladores",
}
GROUPS = {"verlab": "VeRLab", "speed": "SPEED", "embrapii": "Embrapii", "ciia-saude": "CI-IA Saúde", "inct": "INCT", "tild-iar": "TILD-IAR"}
AUDIENCES = [  # (rótulo, padrões no título/valores)
    ("Graduação", r"gradua(?:ndo|ção|cao)|inicia[çc][ãa]o cient|\bic\b|estudantes? de gradua"),
    ("Mestrado", r"mestrand|mestrado(?! em andamento)|n[íi]vel mestrado"),
    ("Doutorado", r"doutorand"),
    ("Pós-doutorado", r"p[óo]s-?doutor|postdoc"),
    ("Profissionais", r"profissiona|rec[ée]m-(?:mestre|doutor)|desenvolvedor|coordenador|pesquisador/desenvolvedor"),
]
END_MARKERS = ("\nO Departamento\nCursos\n", "\nAcesso por PERFIL")


def text_lines(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    # quebra de linha só em elementos de bloco; negrito, links etc. não podem cortar o parágrafo
    page = re.sub(r"<br\s*/?>|</?(?:p|li|ul|ol|h\d|div|section|article|tr|td|th|table|header|footer|nav|main|aside|figure|blockquote)\b[^>]*>", "\n", page, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", page))
    return re.sub(r"\n\s*\n+", "\n", "\n".join(" ".join(line.split()) for line in text.split("\n")))


def parse_listing(page: str) -> list[dict]:
    items = []
    closed_at = page.find("Chamadas fechadas")
    for m in re.finditer(r'<article class="([^"]+)"[^>]*>(.*?)</article>', page, re.S):
        classes, body = m.group(1).split(), m.group(2)
        link = re.search(r'<a href="([^"]+)"[^>]*>\s*(.*?)\s*</a>', body, re.S)
        if not link:
            continue
        excerpt = re.search(r'elementor-post__excerpt">\s*(.*?)\s*</div>', body, re.S)
        cats = [c[len("category-"):] for c in classes if c.startswith("category-")]
        url = link.group(1) if link.group(1).startswith("http") else sync_dcc.BASE + link.group(1)
        items.append({
            "slug": url.rstrip("/").rsplit("/", 1)[-1],
            "title": " ".join(html.unescape(re.sub(r"<[^>]+>", "", link.group(2))).split()),
            "url": url,
            "open": closed_at < 0 or m.start() < closed_at,
            "deadline": " ".join(html.unescape(re.sub(r"<[^>]+>", " ", excerpt.group(1))).split()) if excerpt else None,
            "categories": cats,
        })
    return items


def parse_detail(page: str, title: str) -> dict:
    text = text_lines(page)
    start = text.find(title[:40], text.find("\nBusca\n"))
    start = text.find("\n", start) + 1 if start >= 0 else 0
    end = min([i for i in (text.find(mk, start) for mk in END_MARKERS) if i > 0] or [len(text)])
    body = text[start:end].strip()
    meta = {}
    for key, label in (("published", "Início divulgação"), ("kind", "Tipo"), ("status", "Situação"),
                       ("apply", "Link para candidatura"), ("until", "Fim divulgação")):
        m = re.search(rf"\n{label}\n?\s*:\s*\n?(.+)", "\n" + body)
        if m:
            meta[key] = m.group(1).strip()
    content = re.split(r"\nInício divulgação\n", "\n" + body)[0].strip()
    def clean(p: str) -> str:
        # "Professores: Fulano, Beltrano (DCC/UFMG)O Programa…" -> "O Programa…"
        return re.sub(r"^Professor(?:es|a|as)?(?: respons[áa]ve(?:l|is))?:.*?\((?:DCC/)?UFMG\)\s*", "", p).strip()

    def is_sentence(p: str) -> bool:
        letters = [ch for ch in p if ch.isalpha()]
        return (len(p) >= 80 and len(p.split()) >= 12 and p[0].isalnum() and bool(letters)
                and sum(ch.isupper() for ch in letters) / len(letters) < 0.5
                and not re.match(r"(?:Processo Seletivo|Professor|Regime|Comunica|Inscri|Prazo|Data\b|Per[íi]odo|Etapa|Cronograma|Link)", p, re.I)
                and not re.search(r"\(DCC/UFMG\)\s*$", p))
    paragraphs = [c for c in (clean(p) for p in content.split("\n")) if is_sentence(c)]
    summary = paragraphs[0] if paragraphs else ""
    if len(summary) > 360:
        summary = summary[:357].rsplit(" ", 1)[0] + "…"
    stipends = []
    for m in re.finditer(r"R\$\s?\d[\d.]*(?:,\d{2})?", content):
        # rótulo: o trecho logo antes do valor, na mesma linha ou na anterior ("Graduandos:\nR$ 1.200,00")
        before = content[max(0, m.start() - 90):m.start()].rstrip(" :\n")
        who = re.split(r"[\n;]|(?<!\d)\.(?!\d)|\s[–-]\s", before)[-1]
        who = re.sub(r":\s*\d+\s*$", "", who.strip(" :–-*"))
        # rótulo só se for um nível/perfil curto ("Graduando", "Mestrandos", "IC", "Recém-mestre (…)")
        if len(who) > 60 or "R$" in who or not re.search(r"gradua|mestr|doutor|\bIC\b|inicia|p[óo]s|rec[ée]m|desenvolved|pesquisad|t[ée]cnic", who, re.I):
            who = ""
        value = re.sub(r"R\$\s?", "R$ ", m.group(0))
        if not any(x["value"] == value and x["who"] == who for x in stipends):
            stipends.append({"who": who, "value": value})
    hours = sorted({int(h) for h in re.findall(r"(\d{1,2})\s*(?:h\b|horas)(?:\s*(?:semanais|semanal|por semana|/semana))?", content, re.I)
                    if 4 <= int(h) <= 44})
    pub = None
    if meta.get("published"):
        d = re.search(r"(\d{2})/(\d{2})/(\d{4})", meta["published"])
        pub = f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else None
    apply = meta.get("apply")
    if apply and not re.match(r"https?://|mailto:", apply):
        mail = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", apply)
        apply = f"mailto:{mail.group(0)}" if mail else None
    return {"summary": summary, "stipends": stipends[:6], "hours": hours, "published": pub,
            "kind": meta.get("kind"), "apply": apply, "text": content}


def audiences(item: dict, detail: dict) -> list[str]:
    hay = sync_dcc.tokens(" ".join([item["title"], " ".join(s["who"] for s in detail["stipends"]), " ".join(item["categories"])]))
    hay = " ".join(hay)
    found = [label for label, pat in AUDIENCES if re.search(pat, hay, re.I)]
    if "Iniciação científica" in [TYPES.get(c) for c in item["categories"]] and "Graduação" not in found:
        found.insert(0, "Graduação")
    return found


def link_professors(text: str, professors: list[dict]) -> list[str]:
    """Docentes citados no texto da chamada: prenome seguido do último sobrenome (até 3 nomes no meio)."""
    folded = " ".join(sync_dcc.tokens(text))
    out = []
    for p in professors:
        if p["status"] not in ("ativo", "voluntario", "substituto", "emerito"):
            continue
        sig = sync_dcc.tokens(p["name"])
        names = [t for t in sig if t not in sync_dcc.STOP]
        if len(names) < 2:
            continue
        first, last = names[0], names[-1] if names[-1] not in ("junior", "filho", "neto") else names[-2]
        if re.search(rf"\b{first}\b(?: \w+){{0,3}} \b{last}\b", folded):
            out.append(p["slug"])
    return out


def build(items: list[dict], details: dict[str, dict], professors: list[dict]) -> list[dict]:
    out = []
    for it in items:
        d = details.get(it["slug"], {"summary": "", "stipends": [], "hours": [], "published": None, "kind": None, "apply": None, "text": ""})
        cats = it["categories"]
        out.append({
            "slug": it["slug"], "title": it["title"], "url": it["url"], "open": it["open"], "deadline": it["deadline"],
            "types": list(dict.fromkeys(TYPES[c] for c in cats if c in TYPES)) or ([d["kind"]] if d["kind"] else []),
            "areas": list(dict.fromkeys(AREAS[c] for c in cats if c in AREAS)),
            "groups": list(dict.fromkeys(GROUPS[c] for c in cats if c in GROUPS)),
            "audiences": audiences(it, d),
            "summary": d["summary"], "stipends": d["stipends"], "hours": d["hours"], "published": d["published"],
            "apply": d["apply"], "professors": link_professors(it["title"] + " " + d["text"], professors),
        })
    opened = sorted([o for o in out if o["open"]], key=lambda o: o["published"] or "", reverse=True)
    closed = sorted([o for o in out if not o["open"]], key=lambda o: o["published"] or "", reverse=True)
    return opened + closed


def catalog_professors() -> list[dict]:
    cat = json.loads(sync_dcc.OUT.read_text())
    return cat["professors"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    log = lambda *a: print(*a, file=sys.stderr)  # noqa: E731

    items = parse_listing(sync_dcc.fetch(LIST_PATH))
    log(f"• {len(items)} oportunidades na listagem ({sum(i['open'] for i in items)} abertas)")

    def detail(it: dict) -> tuple[str, dict]:
        try:
            page = sync_dcc.fetch(it["url"].replace(sync_dcc.BASE, ""))
            time.sleep(0.2)
            return it["slug"], parse_detail(page, it["title"])
        except Exception as exc:  # noqa: BLE001
            log(f"  ! {it['slug']}: {exc}")
            return it["slug"], {"summary": "", "stipends": [], "hours": [], "published": None, "kind": None, "apply": None, "text": ""}

    with ThreadPoolExecutor(max_workers=4) as pool:
        details = dict(pool.map(detail, items))
    result = build(items, details, catalog_professors())
    data = {"generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": sync_dcc.BASE + LIST_PATH, "items": result}
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    for o in result:
        log(f"  {'ABERTA ' if o['open'] else 'fechada'} {o['published'] or '—':10s} {', '.join(o['types']) or '?':28.28s} "
            f"{len(o['stipends'])}R$ {o['hours'] or ''} {o['audiences']} prof={len(o['professors'])}  {o['title'][:60]}")
    log(f"✓ {args.out}: {len(result)} oportunidades ({sum(o['open'] for o in result)} abertas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Sincroniza o catálogo (docentes, disciplinas e ofertas) a partir do site público do DCC/UFMG.

Fontes:
  * https://dcc.ufmg.br/professores/                -> nome, foto, situação (ativo, voluntário…), áreas
  * https://dcc.ufmg.br/professor/<slug>/           -> sala, e-mail, página pessoal, Lattes, Scholar
  * https://dcc.ufmg.br/oferta-de-disciplinas-*     -> código, disciplina, turma, horário, professor, plano de ensino

Uso:
  python3 scripts/sync_dcc.py                 # gera data/catalog.json
  python3 scripts/sync_dcc.py --no-profiles   # pula as páginas individuais (mais rápido)

Só usa a biblioteca padrão. As páginas de oferta mudaram de formato várias vezes desde 2020;
o parser descobre as colunas pelo cabeçalho e entende os três formatos de horário usados.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://dcc.ufmg.br"
OUT = Path(__file__).resolve().parent.parent / "data" / "catalog.json"
SIGAA_DIR = OUT.parent / "ofertas-sigaa"
UA = "DCCHub-sync/2.0 (projeto estudantil; leitura do catalogo publico)"
DAY_NAMES = {"seg": 1, "ter": 2, "qua": 3, "qui": 4, "sex": 5, "sab": 6}
STATUS_ORDER = ["ativo", "voluntario", "emerito", "aposentado"]
NON_AREA = {"ativo", "ativo-en", "aposentado", "emerito", "voluntario", "areas-de-pesquisa", "uncategorized",
            "sem-categoria", "professor", "retired", "emeritus", "active", "volunteer"}
STOP = {"da", "de", "do", "das", "dos", "e"}


# ---------------------------------------------------------------- utilidades
def fetch(path: str, retries: int = 3) -> str:
    url = path if path.startswith("http") else BASE + path
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - rede instável: tenta de novo
            if attempt == retries - 1:
                raise RuntimeError(f"falha ao baixar {url}: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
    raise AssertionError


def text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>|</p>", "\n", fragment, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).replace("\xa0", " ").strip()


def tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return [t for t in re.split(r"[^a-z0-9]+", value) if t]


def slugify(value: str) -> str:
    return "-".join(tokens(value))


def tidy_name(name: str) -> str:
    name = " ".join(name.split())
    if name.isupper() or name.islower():
        name = " ".join(w.lower() if w.lower() in STOP and i else w.capitalize() for i, w in enumerate(name.lower().split()))
    return name


ACCENTS = {w.replace("ç", "c").replace("ã", "a").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o")
           .replace("ú", "u").replace("ê", "e").replace("ô", "o").replace("õ", "o").replace("â", "a"): w for w in (
    "programação", "computação", "computacional", "introdução", "análise", "numérica", "cálculo", "mineração", "informática",
    "informação", "lógica", "álgebra", "ciência", "ciências", "gráfica", "robótica", "organização", "otimização",
    "matemática", "estatística", "estatísticos", "teórica", "prática", "práticas", "heurísticas", "metaheurísticas",
    "técnicas", "tópicos", "interação", "comunicação", "avaliação", "aplicações", "gestão", "sistemas", "ética",
    "segurança", "visualização", "integração", "evolução", "manutenção", "inteligência", "máquina", "graduação",
    "pós-graduação", "seminários", "avançados", "avançada", "avançadas", "distribuídos", "paralelos", "específicos",
    "básico", "básicos", "estágio", "docência", "orientação", "dinâmica", "estruturas", "teoria", "módulos")}


def tidy_course(name: str) -> str:
    small = {"a", "à", "ao", "aos", "e", "em", "de", "da", "do", "das", "dos", "para", "com", "na", "no", "o", "os", "as", "por", "x"}
    roman = re.compile(r"^(i{1,3}|iv|v|vi{1,3}|ix|x)$")
    out = []
    for i, w in enumerate(" ".join(name.split()).lower().split()):
        core = w.strip("()-,:")
        if core in ACCENTS:
            w = w.replace(core, ACCENTS[core])
            core = ACCENTS[core]
        if i and len(core) == 1 and i == len(name.split()) - 1:
            out.append(w.upper())  # "Ciência dos Dados A"
        elif roman.match(core) and i:
            out.append(w.upper())
        elif core in {"ia", "ti", "tcc", "poc", "web", "3d", "ii"}:
            out.append(w.upper() if core != "web" else w.capitalize())
        elif i and core in small:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


# ---------------------------------------------------------------- docentes
def parse_professor_list(page: str) -> list[dict]:
    people = []
    for art in re.findall(r'<article class="elementor-post[^"]*type-professor[^"]*".*?</article>', page, re.S):
        classes = re.search(r'class="([^"]+)"', art).group(1).split()
        link = re.search(r'<h\d class="elementor-post__title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', art, re.S)
        if not link:
            continue
        cats = [c[len("category-"):] for c in classes if c.startswith("category-")]
        status = next((s for s in STATUS_ORDER if s in cats), "ativo")
        img = re.search(r'<img[^>]+src="([^"]+)"', art)
        name = tidy_name(text(link.group(2)))
        people.append({
            "slug": slugify(name),
            "dccSlug": link.group(1).rstrip("/").rsplit("/", 1)[-1],
            "name": name,
            "status": status,
            "photo": urllib.parse.urljoin(BASE, img.group(1)) if img else None,
            "areaSlugs": [c for c in cats if c not in NON_AREA],
        })
    return people


def parse_profile(page: str) -> dict:
    info: dict = {}
    def field(label: str) -> str | None:
        m = re.search(rf"<strong>\s*{label}\s*:?\s*</strong>(.*?)(?=<strong>|<a href|<div class=\"dados-lattes|<!--)", page, re.S | re.I)
        value = " ".join(text(m.group(1)).split()) if m else ""
        return value or None
    areas = field("Áreas de Pesquisa")
    if areas:
        info["areas"] = [a.strip() for a in re.split(r"[,;]", areas) if a.strip()]
    for key, label in (("room", "Sala"), ("phone", "Ramal"), ("education", "Formação Acadêmica")):
        value = field(label)
        if value:
            info[key] = value
    email = re.search(r'href="mailto:([^"]+)"', page[page.find("<header"):] if "<header" in page else page)
    if email and "contato@dcc" not in email.group(1):
        info["email"] = email.group(1).strip()
    for key, pattern in (("homepage", r'<a href="([^"]+)"[^>]*>\s*<img[^>]*logo-www'),
                         ("lattes", r'<a href="(https?://lattes\.cnpq\.br/[^"]+)"'),
                         ("scholar", r'<a href="(https?://scholar\.google[^"]+)"')):
        m = re.search(pattern, page)
        if m:
            info[key] = html.unescape(m.group(1))
    return info


# ---------------------------------------------------------------- ofertas
def offering_pages() -> list[tuple[str, str]]:
    found: dict[str, tuple[bool, str]] = {}
    for kind in ("pages", "posts"):
        page = 1
        while True:
            url = f"/wp-json/wp/v2/{kind}?search=oferta%20de%20disciplinas&per_page=100&page={page}&_fields=link,title"
            try:
                items = json.loads(fetch(url))
            except Exception:  # noqa: BLE001
                break
            if not isinstance(items, list) or not items:
                break
            for item in items:
                title = html.unescape(item["title"]["rendered"])
                m = re.search(r"(\d)º\s*semestre\s*de\s*(\d{4})", title, re.I)
                if "oferta" in title.lower() and m:
                    term = f"{m.group(2)}.{m.group(1)}"
                    remote = "remoto" in title.lower()
                    if term not in found or (remote and not found[term][0]):
                        found[term] = (remote, item["link"])
            if len(items) < 100:
                break
            page += 1
    return sorted((term, link) for term, (_, link) in found.items())


def hm(h: str, m: str) -> str:
    return f"{int(h):02d}:{m}"


def parse_schedule(cell: str) -> list[dict]:
    cell = " ".join(cell.replace("–", "-").replace("—", "-").split())
    slots: list[dict] = []
    # "14:55 16:35 (Seg) 14:55 16:35 (Qua)"
    for m in re.finditer(r"(\d{1,2}):(\d{2})\s*-?\s*(\d{1,2}):(\d{2})\s*\((Seg|Ter|Qua|Qui|Sex|S[áa]b)\w*\)", cell, re.I):
        slots.append({"day": DAY_NAMES[tokens(m.group(5))[0][:3]], "start": hm(m[1], m[2]), "end": hm(m[3], m[4])})
    if slots:
        return slots
    # "Seg 17:00 - 18:40 Qua 17:00 - 18:40"
    for m in re.finditer(r"(Seg|Ter|Qua|Qui|Sex|S[áa]b)\w*\.?\s*(\d{1,2}):(\d{2})\s*(?:-|às|a)\s*(\d{1,2}):(\d{2})", cell, re.I):
        slots.append({"day": DAY_NAMES[tokens(m.group(1))[0][:3]], "start": hm(m[2], m[3]), "end": hm(m[4], m[5])})
    if slots:
        return slots
    # "2a e 4a - 07:30/09:10"  |  "3a - 19:00/20:40 e 5a - 20:55/22:35"
    for m in re.finditer(r"((?:[2-7]\s*[aª]\s*(?:,|e)?\s*)+)-?\s*(\d{1,2}):(\d{2})\s*(?:/|-|às|a)\s*(\d{1,2}):(\d{2})", cell, re.I):
        for d in re.findall(r"([2-7])\s*[aª]", m.group(1)):
            slots.append({"day": int(d) - 1, "start": hm(m[2], m[3]), "end": hm(m[4], m[5])})
    return slots


def column_map(header: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, raw in enumerate(header):
        key = " ".join(tokens(raw))
        if key in ("codigo", "cod", "cod disciplina"):
            cols.setdefault("code", i)
        elif key.startswith("disciplina"):
            cols.setdefault("name", i)
        elif key.startswith("horario"):
            cols.setdefault("schedule", i)
        elif key.startswith("prof"):
            cols.setdefault("teacher", i)
        elif key == "turma":
            cols.setdefault("class", i)
        elif key.startswith("plano"):
            cols.setdefault("plan", i)
    return cols


def parse_codes(cell: str) -> list[str]:
    """'DCC030/049/831' -> ['DCC030', 'DCC049', 'DCC831'];  'DCC057+B29/917' -> ['DCC057', 'DCC917']."""
    parts = re.split(r"[/+,]", cell.upper().replace(" ", ""))
    first = re.fullmatch(r"([A-Z]{3})(\d{3}[A-Z]?)", parts[0])
    if not first:
        return []
    codes = [parts[0]]
    for part in parts[1:]:
        if re.fullmatch(r"\d{3}", part):
            codes.append(first.group(1) + part)
        elif re.fullmatch(r"[A-Z]{3}\d{3}[A-Z]?", part):
            codes.append(part)
    return list(dict.fromkeys(codes))


TOPIC_CODES = {"DCC030", "DCC049", "DCC831", "DCC829", "DCC851", "DCC890", "DCC068", "DCC069", "DCC070", "DCC071"}
TOPIC_PREFIX = re.compile(
    r"^\s*(?:(?:TCC|TSI|TECC|TEEC|TES|TEI\s*[IV]+)(?:\s*/\s*(?:TCC|TSI|TECC|TES))*\s*[:–-]\s*"
    r"|T[OÓ]P(?:ICOS)?\.?\s+ESP(?:ECIAIS)?\.?\s+EM\s+INFORM[AÁ]TICA\s+[IV]+\s*[-–:]\s*"
    r"|T[OÓ]PICOS\s+EM\s+[A-ZÇÃÕÁÉÍÓÚ ]+?\s*[-–:]\s*)", re.I)


def course_identity(codes: list[str], raw_name: str) -> tuple[str, str, str]:
    """Retorna (id, nome, tipo). Disciplinas de tópicos viram uma disciplina por tema."""
    name = " ".join(raw_name.split())
    stripped = TOPIC_PREFIX.sub("", name)
    is_topic = stripped != name or codes[0] in TOPIC_CODES
    if not is_topic:
        return codes[0], tidy_course(name), "regular"
    topic = re.sub(r"\((?:FLEX|em ingl[êe]s|English|[A-Z]{3}\d{3})\)", "", stripped, flags=re.I).strip(" .–-")
    if not topic or re.fullmatch(r"t[óo]picos em .*", topic, re.I):
        return codes[0], tidy_course(name), "topico"
    topic = topic[:1].upper() + topic[1:]
    if topic.isupper():
        topic = tidy_course(topic)
    return "TOP-" + slugify(topic)[:60], topic, "topico"


SUBST_ONLY = re.compile(r"^\s*(?:prof\.?\s*)?(?:ps|substitut[oa])\s*\d*\s*$", re.I)  # "PS", "PS3", "Substituto 4"
SUBST_LABEL = "Professor substituto"


def split_teachers(cell: str) -> list[str]:
    if SUBST_ONLY.match(cell.strip()):
        return [SUBST_LABEL]
    cell = re.sub(r"\(.*?\)", " ", cell)
    out = []
    for part in re.split(r"\n|/|;|,|\+| e (?=[A-ZÁÉÍÓÚ])", cell):
        part = re.sub(r"^\s*(\*|PS\d?|vg)\s*(-|–)?\s*", "", part.strip(), flags=re.I)
        part = re.sub(r"^\s*(vg)\s+", "", part, flags=re.I)
        part = " ".join(part.split()).strip(" -–*")
        if len(part) >= 3 and not re.search(r"a definir|online|^\d|^-+$", part, re.I):
            out.append(part)
    return out


def parse_offerings(page: str) -> list[dict]:
    rows = []
    for table in re.findall(r"<table.*?</table>", page, re.S):
        trs = re.findall(r"<tr.*?</tr>", table, re.S)
        if not trs:
            continue
        header = [text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", trs[0], re.S)]
        cols = column_map(header)
        if "code" not in cols or "name" not in cols:
            continue
        for tr in trs[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
            if len(cells) <= max(cols.values()):
                continue
            codes = parse_codes(text(cells[cols["code"]]))
            if not codes:
                continue
            plan = None
            if "plan" in cols:
                m = re.search(r'href="([^"]+)"', cells[cols["plan"]])
                plan = html.unescape(m.group(1)) if m else None
            rows.append({
                "codes": codes,
                "name": " ".join(text(cells[cols["name"]]).split()),
                "class": " ".join(text(cells[cols["class"]]).split()) if "class" in cols else None,
                "schedule": parse_schedule(text(cells[cols["schedule"]])) if "schedule" in cols else [],
                "teachers": split_teachers(text(cells[cols["teacher"]])) if "teacher" in cols else [],
                "plan": plan,
            })
    return rows


# ---------------------------------------------------------------- substitutos
def parse_substitutes(page: str) -> list[tuple[str, str]]:
    """dcc.ufmg.br/professores-substitutos/: '<h2>2º semestre de 2025</h2>' seguido dos nomes contratados."""
    out: list[tuple[str, str]] = []
    parts = re.split(r"<h[2-4][^>]*>\s*(\d)º\s*semestre\s*de\s*(\d{4})\s*</h[2-4]>", page)
    for i in range(1, len(parts) - 2, 3):
        term = f"{parts[i + 1]}.{parts[i]}"
        body = re.split(r"<h[1-4]|Pesquisar por", parts[i + 2])[0]
        for line in text(body).split("\n"):
            name = " ".join(line.split())
            if 2 <= len(name.split()) <= 7 and not re.search(r"\d|semestre", name):
                out.append((term, name))
    return out


def term_index(term: str) -> int:
    y, s_ = term.split(".")
    return int(y) * 2 + int(s_) - 1


SUBST_WINDOW = 4  # contrato de substituto: até 2 anos a partir da contratação


# ---------------------------------------------------------------- casamento de nomes
def canon(token: str) -> str:
    """Grafias equivalentes: Luis/Luiz, Rodrygo/Rodrigo, Phablo/Fablo."""
    return token.replace("y", "i").replace("z", "s").replace("ph", "f")


def sig(value: str) -> list[str]:
    return [canon(t) for t in tokens(value) if t not in STOP and t not in {"prof", "profa", "dr", "dra"}]


def tok_match(short: str, full: str) -> bool:
    # inicial ("F.") ou abreviação curta ("Ch.", "Pr.")
    return short == full or (len(short) <= 2 and full.startswith(short))


def name_fits(raw: list[str], full: list[str]) -> bool:
    """Todas as partes do nome curto aparecem, em ordem, no nome completo."""
    i = 0
    for r in raw:
        while i < len(full) and not tok_match(r, full[i]):
            i += 1
        if i == len(full):
            return False
        i += 1
    return True


NAME_SUFFIX = {"junior", "jr", "filho", "neto", "sobrinho"}


def surname_compatible(raw: list[str], full: list[str]) -> bool:
    """Nome com sobrenome na oferta ("Ana Paula de Carvalho") não pode casar com outra pessoa só pelo prenome
    ("Ana Paula Couto da Silva"): o último sobrenome de um dos dois tem de aparecer no outro (tolerando erro de digitação)."""
    r = [t for t in raw if t not in NAME_SUFFIX]
    f = [t for t in full if t not in NAME_SUFFIX]
    if len(r) < 3 or len(f) < 2:
        return True
    near = lambda a, bs: any(a == b or (len(a) > 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8) for b in bs)  # noqa: E731
    return near(r[-1], f[1:]) or near(f[-1], r[1:])


def is_placeholder(raw: str) -> bool:
    return bool(re.search(r"substitut|subst\b|a definir|^liga$|cadence|online", raw, re.I))


class Resolver:
    """Liga nomes (completos, abreviados, só o primeiro nome…) aos perfis do DCC.

    Usa apelidos aprendidos e o histórico de quem já lecionou a disciplina para desfazer ambiguidades.
    """

    def __init__(self, professors: dict[str, dict]):
        self.professors = professors
        self.aliases: dict[str, list[list[str]]] = {slug: [sig(p["name"])] for slug, p in professors.items()}
        self.course_teachers: dict[str, Counter] = defaultdict(Counter)
        self.teaching: Counter = Counter()

    def add(self, slug: str, raw: str) -> None:
        r = sig(raw)
        if r and r not in self.aliases.setdefault(slug, []):
            self.aliases[slug].append(r)

    def candidates(self, raw: str) -> list[str]:
        r = sig(raw)
        if not r or is_placeholder(raw):
            return []
        exact = [s for s, names in self.aliases.items() if r in names]
        if exact:
            return exact
        fits = [s for s, names in self.aliases.items() if any(tok_match(r[0], f[0]) and name_fits(r, f) for f in names)]
        if not fits and len(r) == 1:
            fits = [s for s, names in self.aliases.items() if any(r[0] in f for f in names)]  # só o sobrenome ("Loureiro")
        if not fits and len(r) >= 2:
            # nome mais longo que o cadastro ("Clodoveu Augusto Davis Junior" ⊇ "Clodoveu Davis")
            fits = [s for s, names in self.aliases.items() if any(len(f) >= 2 and f[0] == r[0] and name_fits(f, r) for f in names)]
        if not fits and len(r) >= 3:
            # erro de digitação num sobrenome: primeiro nome igual e ≥2 outros tokens em comum
            # (sobrenome quase igual também conta: "Prates" x "Prares")
            near = lambda a, b: a == b or (len(a) > 4 and len(b) > 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8)  # noqa: E731
            fits = [s for s, names in self.aliases.items()
                    if any(f[0] == r[0] and sum(any(near(t, g) for g in f[1:]) for t in r[1:]) >= 2 for f in names)]
        return [s for s in fits if surname_compatible(r, self.aliases[s][0])]  # [0] = nome oficial, não apelido aprendido

    def learn(self, code: str, raw: str) -> None:
        cands = self.candidates(raw)
        if len(cands) == 1:
            self.course_teachers[code][cands[0]] += 1
            self.teaching[cands[0]] += 1
            if len(sig(raw)) >= 2 and all(len(t) > 2 for t in sig(raw)):
                self.add(cands[0], raw)

    def resolve(self, code: str, raw: str) -> str | None:
        cands = self.candidates(raw)
        known = self.course_teachers[code]
        if not cands and not is_placeholder(raw):
            # "Jeroen Antonius" → único docente com esse primeiro nome que já lecionou a disciplina
            first = sig(raw)[:1]
            cands = [s for s in known if first and self.aliases[s][0][:1] == first]
            return cands[0] if len(cands) == 1 else None
        if len(cands) == 1:
            return cands[0]
        for narrowed in ([c for c in cands if known[c]],
                         [c for c in cands if self.teaching[c] and self.professors[c]["status"] in ("ativo", "voluntario", "externo")],
                         [c for c in cands if self.professors[c]["status"] in ("ativo", "voluntario")]):
            if len(narrowed) == 1:
                return narrowed[0]
            if narrowed:
                cands = narrowed
        return None


# ---------------------------------------------------------------- versões de uma mesma disciplina
# Quando o currículo muda, a disciplina ganha um código novo e o antigo continua sendo ofertado por alguns
# semestres para quem está na versão anterior (ex.: Estruturas de Dados DCC205 -> DCC221, currículo 2024).
# A pós e outros cursos (ICE…) também usam códigos próprios para a mesma disciplina. Para quem escolhe turma,
# é tudo a mesma disciplina: as versões são unidas sob um código canônico e os demais viram "aliases".
# Escolha automática do código canônico: o código mais recente ainda ofertado; a lista abaixo só corrige exceções.
CANONICAL = {"introducao a ciencia dos dados": "DCC212"}  # DCC212 é o código da grade 2024 do BCC
# Versões com nomes diferentes que são a mesma disciplina (antigo -> novo). Vazio por enquanto: o casamento
# por nome já cobre os casos do site; acrescente aqui só com fonte (grade curricular oficial).
# Fonte: grade 2024 do BCC (dcc.ufmg.br/estrutura-curricular-do-bacharelado-em-ciencia-da-computacao/), que lista
# DCC222 "Introdução a Bancos de Dados" e DCC053 "Compiladores I"; as versões antigas dividem turma com as novas.
SAME_AS: dict[str, str] = {"DCC011": "DCC222", "DCC010": "DCC053"}
# Erros de digitação das páginas de oferta (geravam disciplinas duplicadas).
NAME_FIXES = {"estatíticos": "estatísticos", "estátisticos": "estatísticos", "theroy": "theory", "pronfundo": "profundo",
              "resolição": "resolução", "dados aplicados": "dados aplicada", "ciências dos dados": "ciência dos dados",
              "seminários em ri e": "seminários em recuperação de informação e", "cyberscurity": "cybersecurity"}
GENERIC_TOKENS = {"dado", "computacao", "computador", "sistema", "ciencia", "introducao", "aplicada", "aplicacao", "fundamento",
                  "topico", "especiai", "avancado", "seminario", "engenharia", "teoria", "aprendizado", "rede"}


def stems(text: str) -> set[str]:
    """Radicais para comparar nome de disciplina com área de pesquisa: 'Numérico' ~ 'Numérica'."""
    out = set()
    for t in tokens(text):
        if t in NAME_STOP or len(t) < 4:
            continue
        t = t[:-1] if t.endswith("s") else t
        t = re.sub(r"[aeo]$", "", t)
        if t not in GENERIC_TOKENS and t + "o" not in GENERIC_TOKENS and t + "a" not in GENERIC_TOKENS:
            out.add(t)
    return out
# Disciplinas extintas na troca de currículo (não são versões da nova: conteúdo diferente) -> sucessora, se houver.
# DCC050 (intro do BCC) e DCC044 (intro do BSI) saíram em 2025.1, quando o tronco comum CC/SI criou a DCC096
# Introdução à Computação (1º período da grade EFTCC); em 2025.1 as três dividiram turma.
# AEDS (DCC003) é do currículo anterior; o conteúdo foi para PDS I/II e Estruturas de Dados. Verificado nas 13 páginas
# de oferta (2020.1–2026.1): só "ALGORITMOS E ESTRUTURAS DE DADOS I" em 2020.1; AEDS II não aparece em nenhuma.
# DCC606 "Computadores e Sociedade" (até 2020.2) -> DCC637 "Computação e Sociedade" (8º período da grade 2024).
RETIRED: dict[str, str | None] = {"DCC050": "DCC096", "DCC044": "DCC096", "DCC003": "DCC221", "DCC606": "DCC637"}
RETIRED_NAMES = re.compile(r"^algoritmos e estruturas de dados\b", re.I)  # qualquer AEDS que reapareça numa fonte
RETIRE_AFTER_TERMS = 4  # sem oferta nas últimas 4 ofertas publicadas (2 anos) = extinta, mesmo fora da lista acima


def mark_retired(courses: dict[str, dict], terms: list[str]) -> None:
    recent = set(terms[-RETIRE_AFTER_TERMS:])
    for cid, c in courses.items():
        c.pop("retired", None)
        c.pop("successor", None)
        ids = {cid, *c.get("aliases", [])}
        listed = next((RETIRED[i] for i in ids if i in RETIRED), "absent") if ids & set(RETIRED) else "absent"
        if listed == "absent" and RETIRED_NAMES.match(c["name"]):
            listed = "DCC221" if "DCC221" in courses else None
        if listed != "absent" and not (set(terms[-1:]) & set(c["terms"])):
            c["retired"] = True
            if listed and listed in courses:
                c["successor"] = listed
        elif not recent & set(c["terms"]):
            c["retired"] = True


NAME_STOP = {"a", "à", "ao", "de", "da", "do", "das", "dos", "e", "em", "para", "o", "os", "as"}


def fix_course_name(name: str) -> str:
    stripped = TOPIC_PREFIX.sub("", name).strip(" .–-") or name
    if stripped != name and stripped.isupper():
        stripped = tidy_course(stripped)
    for wrong, right in NAME_FIXES.items():
        stripped = re.sub(re.escape(wrong), lambda m: right if m.group(0)[0].islower() else right[:1].upper() + right[1:], stripped, flags=re.I)
    return stripped


def name_key(name: str) -> str:
    """Chave tolerante a 'de/dos', acentos e plural: 'Introdução a Bancos de Dados' == 'Introdução a Banco de Dados'.
    Numerais romanos e letras finais (I/II, A/B) continuam distinguindo disciplinas."""
    out = []
    toks = tokens(re.sub(r"(?<=[A-Za-z])(?=\d)", " ", fix_course_name(name)))  # "Startups2" == "Startups 2"
    for i, t in enumerate(toks):
        if t in NAME_STOP and not (i == len(toks) - 1 and len(t) == 1):  # "… de Máquina A": o A final é turma/versão
            continue
        if len(t) > 5 and re.search(r"[rsz]es$", t):  # computadores -> computador
            t = t[:-2]
        elif len(t) > 4 and t.endswith("s"):
            t = t[:-1]
        out.append(t)
    return " ".join(out)


def consolidate(courses: dict[str, dict], offerings: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Une as versões de uma disciplina (mesmo nome; tópicos com o nome de uma disciplina regular) e funde
    turmas repetidas (a mesma turma listada com o código antigo e o novo). Idempotente."""
    courses = {cid: {**c, "name": fix_course_name(c["name"])} for cid, c in courses.items()}

    def key(c: dict) -> str:
        return name_key(c["name"])

    first: dict[str, str] = {}
    last: dict[str, str] = {}
    for o in offerings:
        for code in [o["course"], *o["codes"]]:
            first[code] = min(first.get(code, o["term"]), o["term"])
            last[code] = max(last.get(code, o["term"]), o["term"])

    groups: dict[str, list[dict]] = defaultdict(list)
    keys = {c["id"]: key(c) for c in courses.values()}
    for c in courses.values():
        target = SAME_AS.get(c["id"])
        groups[keys[target] if target in keys else keys[c["id"]]].append(c)
    regular_keys = {k for k, g in groups.items() if any(c["kind"] == "regular" for c in g)}

    def rank(cid: str) -> tuple:
        # DCC primeiro; depois o código ofertado mais recentemente; depois o que surgiu por último (currículo novo); graduação antes da pós
        num = int(re.sub(r"\D", "", cid) or 0)
        return (cid.startswith("DCC"), last.get(cid, ""), first.get(cid, ""), num < 800)

    alias: dict[str, str] = {}
    merged: dict[str, dict] = {}
    for k, group in groups.items():
        if k in regular_keys:
            regulars = [c for c in group if c["kind"] == "regular"]
            ids = [c["id"] for c in regulars]
            pref = next((v for name, v in CANONICAL.items() if name_key(name) == k), None)  # mesma chave tolerante
            canon = pref if pref in ids else max(ids, key=rank)
        else:
            ids = [c["id"] for c in group]
            clean = "TOP-" + slugify(max(group, key=lambda c: len(c["terms"]))["name"])[:60]  # id que bate com o nome corrigido
            canon = clean if clean in ids else max(ids, key=lambda cid: (len(courses[cid]["terms"]), cid))
        base = next(c for c in group if c["id"] == canon)
        codes = [code for c in sorted(group, key=lambda c: c["id"] != canon) for code in c["codes"]]
        codes = sorted(dict.fromkeys(codes), key=lambda code: (code != canon, not code.startswith("DCC"), "" if code == canon else -int(last.get(code, "0").replace(".", "") or 0)))
        if k in regular_keys:  # códigos genéricos de tópicos (DCC030…) não identificam a disciplina
            codes = [code for code in codes if code == canon or code not in TOPIC_CODES]
        out = {**base, "codes": codes,
               "kind": "regular" if k in regular_keys else base["kind"],
               "terms": sorted({t for c in group for t in c["terms"]}),
               "aliases": sorted({a for c in group for a in [c["id"], *c.get("aliases", [])]} - {canon})}
        if not out["aliases"]:
            out.pop("aliases")
        merged[canon] = out
        for c in group:
            alias[c["id"]] = canon
            for a in c.get("aliases", []):
                alias[a] = canon

    seen: dict[tuple, dict] = {}
    result: list[dict] = []
    for o in offerings:
        o = {**o, "course": alias.get(o["course"], o["course"])}
        sig_ = (o["term"], o["course"], tuple(sorted(o["professors"])),
                tuple((s["day"], s["start"], s["end"]) for s in o["schedule"]), o.get("class"))
        if o["professors"] and o["schedule"] and sig_ in seen:  # mesma turma com o código antigo e o novo
            prev = seen[sig_]
            prev["codes"] = list(dict.fromkeys(prev["codes"] + o["codes"]))
            if o.get("unresolved"):
                prev["unresolved"] = list(dict.fromkeys(prev.get("unresolved", []) + o["unresolved"]))
            if o.get("plan") and not prev.get("plan"):
                prev["plan"] = o["plan"]
            continue
        seen[sig_] = o
        result.append(o)
    return merged, result


def archive_path(out: Path) -> Path:
    return out.with_name(out.stem + "-arquivo.json")


def write_catalog(catalog: dict, out: Path) -> tuple[int, int]:
    """catalog.json fica só com o que o site mostra; disciplinas extintas (e suas turmas) vão para
    catalog-arquivo.json, usado apenas para manter visíveis avaliações antigas que já existam."""
    retired = {c["id"] for c in catalog["courses"] if c.get("retired")}
    current = {**catalog, "courses": [c for c in catalog["courses"] if c["id"] not in retired],
               "offerings": [o for o in catalog["offerings"] if o["course"] not in retired]}
    archive = {"generatedAt": catalog.get("generatedAt"),
               "note": "Disciplinas extintas (currículo antigo ou sem oferta nos últimos 2 anos). Não aparecem no site.",
               "courses": [c for c in catalog["courses"] if c["id"] in retired],
               "offerings": [o for o in catalog["offerings"] if o["course"] in retired]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n")
    archive_path(out).write_text(json.dumps(archive, ensure_ascii=False, indent=1) + "\n")
    return len(current["courses"]), len(archive["courses"])


def course_sort_key(c: dict) -> tuple:
    return (c["kind"] != "regular", c["id"] if c["kind"] == "regular" else tokens(c["name"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-profiles", action="store_true")
    parser.add_argument("--no-photos", action="store_true", help="não baixa as fotos para static/photos")
    parser.add_argument("--offline", action="store_true", help="não acessa o site: só reaplica a consolidação ao catálogo existente")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    log = lambda *a: print(*a, file=sys.stderr)  # noqa: E731
    if args.offline:
        catalog = json.loads(args.out.read_text())
        if archive_path(args.out).exists():
            old = json.loads(archive_path(args.out).read_text())
            catalog["courses"] += old["courses"]
            catalog["offerings"] += old["offerings"]
        before = len(catalog["courses"]), len(catalog["offerings"])
        courses, offerings = consolidate({c["id"]: c for c in catalog["courses"]}, catalog["offerings"])
        mark_retired(courses, catalog["terms"])
        catalog["courses"] = sorted(courses.values(), key=course_sort_key)
        catalog["offerings"] = offerings
        shown, archived = write_catalog(catalog, args.out)
        log(f"✓ {args.out}: disciplinas {before[0]} -> {len(courses)} ({shown} em oferta, {archived} extintas no arquivo), turmas {before[1]} -> {len(offerings)}")
        return 0

    log("• docentes…")
    area_names: dict[str, str] = {}
    for page in (1, 2, 3):
        try:
            cats = json.loads(fetch(f"/wp-json/wp/v2/categories?per_page=100&page={page}&_fields=slug,name"))
        except Exception:  # noqa: BLE001
            break
        if not isinstance(cats, list) or not cats:
            break
        area_names.update({c["slug"]: html.unescape(c["name"]) for c in cats})
    professors: dict[str, dict] = {}
    for prof in parse_professor_list(fetch("/professores/")):
        prof["areas"] = [area_names.get(s, s.replace("-", " ").capitalize()) for s in prof.pop("areaSlugs")]
        prof["dccUrl"] = f"{BASE}/professor/{prof.pop('dccSlug')}/"
        professors.setdefault(prof["slug"], prof)
    log(f"  {len(professors)} docentes ({Counter(p['status'] for p in professors.values())})")

    if not args.no_profiles:
        log("• perfis individuais…")
        def load(slug: str) -> tuple[str, dict]:
            try:
                return slug, parse_profile(fetch(professors[slug]["dccUrl"]))
            except Exception as exc:  # noqa: BLE001
                log(f"  ! {slug}: {exc}")
                return slug, {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for slug, info in pool.map(load, list(professors)):
                areas = info.pop("areas", None)
                if areas and not professors[slug]["areas"]:
                    professors[slug]["areas"] = areas
                professors[slug].update(info)

    if not args.no_photos:
        log("• fotos…")
        photo_dir = Path(__file__).resolve().parent.parent / "static" / "photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        def grab(prof: dict) -> None:
            src = prof.get("photo")
            if not src or not src.startswith("http"):
                return
            ext = Path(urllib.parse.urlparse(src).path).suffix.lower() or ".jpg"
            target = photo_dir / f"{prof['slug']}{ext}"
            try:
                if not target.exists():
                    req = urllib.request.Request(src, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                    if len(data) > 3_000_000:
                        return
                    target.write_bytes(data)
                prof["photo"] = f"photos/{target.name}"
            except Exception as exc:  # noqa: BLE001
                log(f"  ! foto {prof['slug']}: {exc}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(grab, professors.values()))

    log("• ofertas…")
    pages = offering_pages()
    raw_terms: list[tuple[str, str, list[dict]]] = []
    for term, url in pages:
        rows = parse_offerings(fetch(url))
        log(f"  {term}: {len(rows):3d} turmas  {url}")
        raw_terms.append((term, url, rows))
    # Semestres sem página do DCC, importados do mapa de oferta do SIGAA (scripts/import_sigaa.py).
    # A página oficial do DCC, quando existir, prevalece.
    term_sources = {term: "DCC" for term, _ in pages}
    for f in sorted(SIGAA_DIR.glob("*.json")):
        extra = json.loads(f.read_text())
        if extra["term"] in term_sources:
            log(f"  {extra['term']}: página do DCC disponível; ignorando {f.name}")
            continue
        log(f"  {extra['term']}: {len(extra['offerings']):3d} turmas  (SIGAA, {f.name})")
        raw_terms.append((extra["term"], "sigaa", extra["offerings"]))
        term_sources[extra["term"]] = "SIGAA"
    raw_terms.sort(key=lambda t: t[0])

    resolver = Resolver(professors)
    for _, _, rows in raw_terms:
        for row in rows:
            row["id"], row["title"], row["kind"] = course_identity(row["codes"], row["name"])
            row["code"] = row["id"]
    all_raw = [(row["code"], raw) for _, _, rows in raw_terms for row in rows for raw in row["teachers"]]
    for code, raw in all_raw:
        resolver.learn(code, raw)
    # Docentes de fora da lista do DCC (substitutos, outros departamentos): criados a partir do nome mais completo.
    last_seen: dict[str, str] = {}
    for term, _, rows in raw_terms:
        for row in rows:
            for raw in row["teachers"]:
                last_seen[raw] = max(last_seen.get(raw, ""), term)
    # nome mais completo primeiro; no empate, a grafia mais recente (ex.: "Prates" corrige o antigo "Prares")
    for code, raw in sorted(set(all_raw), key=lambda cr: (-len(sig(cr[1])), [-ord(ch) for ch in last_seen.get(cr[1], "")])):
        r = sig(raw)
        if len(r) >= 2 and all(len(t) > 2 for t in r) and not is_placeholder(raw) and not resolver.candidates(raw):
            slug = slugify(raw)
            professors[slug] = {"slug": slug, "name": tidy_name(raw), "status": "externo", "photo": None, "areas": [], "dccUrl": None}
            resolver.aliases[slug] = [r]
    for code, raw in all_raw:
        resolver.learn(code, raw)

    courses: dict[str, dict] = {}
    offerings: list[dict] = []
    unresolved: Counter = Counter()
    for term, _, rows in raw_terms:
        for row in rows:
            course = courses.setdefault(row["id"], {"id": row["id"], "codes": [], "name": row["title"], "kind": row["kind"], "terms": []})
            # o nome mais recente vence, mas preferimos a grafia acentuada quando é o mesmo nome
            accents = lambda v: sum(ch in "áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ" for ch in v)  # noqa: E731
            if tokens(course["name"]) != tokens(row["title"]) or accents(row["title"]) >= accents(course["name"]):
                course["name"] = row["title"]
            for code in row["codes"]:
                if code not in course["codes"]:
                    course["codes"].append(code)
            if term not in course["terms"]:
                course["terms"].append(term)
            slugs: list[str] = []
            pending: list[str] = []
            for raw in row["teachers"]:
                slug = resolver.resolve(row["code"], raw)
                if slug is None:
                    pending.append(raw)
                elif slug not in slugs:
                    slugs.append(slug)
            offering = {"term": term, "course": row["id"], "codes": row["codes"], "professors": slugs, "schedule": row["schedule"]}
            if pending:
                offering["unresolved"] = pending
            if row["class"]:
                offering["class"] = row["class"]
            if row["plan"]:
                offering["plan"] = row["plan"]
            offerings.append(offering)

    courses, offerings = consolidate(courses, offerings)
    # Segunda chance, já com as versões unidas: nome ambíguo ("Marcos Augusto") ou só o primeiro nome é atribuído
    # quando exatamente um dos candidatos já deu ESTA disciplina (sob qualquer código) em outro semestre.
    taught: dict[str, set[str]] = defaultdict(set)
    for o in offerings:
        taught[o["course"]].update(o["professors"])
    # disciplinas que dividem turma (mesmo docente, semestre e horário: ex. DCC001 e DCC208) formam uma família
    shared: dict[tuple, set[str]] = defaultdict(set)
    for o in offerings:
        if o["professors"] and o["schedule"]:
            shared[(o["term"], tuple(sorted(o["professors"])), tuple((x["day"], x["start"]) for x in o["schedule"]))].add(o["course"])
    family: dict[str, set[str]] = defaultdict(set)
    for group in shared.values():
        for cid in group:
            family[cid] |= group - {cid}
    for o in offerings:
        left = []
        for raw in o.get("unresolved", []):
            everyone = resolver.candidates(raw)
            cands, why = [c for c in everyone if c in taught[o["course"]]], "histórico da disciplina"
            if len(cands) != 1 and len(everyone) >= 2:
                fam = [c for c in everyone if any(c in taught[f] for f in family[o["course"]])]
                if len(fam) == 1:
                    cands, why = fam, "histórico de disciplina que divide turma com esta"
            if len(cands) != 1 and len(everyone) >= 2:
                # vários docentes do DCC com esse nome: decide a área de pesquisa que aparece no nome da disciplina
                subject = stems(courses[o["course"]]["name"])
                pool = cands or everyone
                cands = [c for c in pool if subject & stems(" ".join(professors[c].get("areas", [])))]
                why = "área de pesquisa"
            if len(cands) == 1:
                if cands[0] not in o["professors"]:
                    o["professors"].append(cands[0])
                log(f"  {o['term']} {o['course']}: {raw!r} -> {cands[0]} ({why})")
            else:
                left.append(raw)
                unresolved[raw] += 1
        if left:
            o["unresolved"] = left
        else:
            o.pop("unresolved", None)

    # Substitutos: a página oficial lista quem foi contratado em cada semestre. "Amanda" ou "Wagner Subst" numa turma
    # viram o substituto contratado com esse nome, se ele for o único possível naquele semestre (nunca um efetivo).
    try:
        hired = parse_substitutes(fetch("/professores-substitutos/"))
    except Exception as exc:  # noqa: BLE001
        log(f"  ! substitutos: {exc}")
        hired = []
    log(f"• substitutos: {len(hired)} contratações em {sorted({t for t, _ in hired})}")
    unresolved.clear()
    for o in offerings:
        left = []
        for raw in o.get("unresolved", []):
            clean = " ".join(re.sub(r"\b(?:subst\w*|prof\.?)", " ", raw, flags=re.I).split())
            r = sig(clean) if clean and not SUBST_ONLY.match(raw) and raw != SUBST_LABEL else []
            cands = sorted({name for t, name in hired if 0 <= term_index(o["term"]) - term_index(t) < SUBST_WINDOW
                            and r and tok_match(r[0], sig(name)[0]) and name_fits(r, sig(name))})
            if len(cands) == 1:
                slug = slugify(cands[0])
                professors.setdefault(slug, {"slug": slug, "name": cands[0], "status": "substituto", "photo": None, "areas": [], "dccUrl": None})
                if slug not in o["professors"]:
                    o["professors"].append(slug)
                log(f"  {o['term']} {o['course']}: {raw!r} -> {slug} (substituto contratado)")
            else:
                left.append(SUBST_LABEL if SUBST_ONLY.match(raw) else raw)
                unresolved[raw] += 1
        if left:
            o["unresolved"] = list(dict.fromkeys(left))
        else:
            o.pop("unresolved", None)
    for t, name in hired:  # quem já existia como "externo" (nome completo na oferta) passa a ser identificado como substituto
        p = professors.get(slugify(name))
        if p and p["status"] in ("externo", "substituto"):
            p["status"] = "substituto"
            p["substituteTerms"] = sorted(set(p.get("substituteTerms", [])) | {t})

    if unresolved:
        log("  nomes não resolvidos (turma fica sem docente):")
        for raw, n in unresolved.most_common():
            log(f"    {raw!r} ×{n}")

    mark_retired(courses, sorted({o["term"] for o in offerings}))

    # Remove perfis 'externo' que só existiram por engano de leitura (sem turma)
    used = {s for o in offerings for s in o["professors"]}
    professors = {s: p for s, p in professors.items() if p["status"] not in ("externo", "substituto") or s in used}

    catalog = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {"professors": BASE + "/professores/", "offerings": [u for _, u in pages]},
        "termSources": term_sources,
        "terms": sorted({o["term"] for o in offerings}),
        "professors": sorted(professors.values(), key=lambda p: tokens(p["name"])),
        "courses": sorted(courses.values(), key=course_sort_key),
        "offerings": offerings,
    }
    write_catalog(catalog, args.out)
    with_prof = sum(1 for o in offerings if o["professors"])
    log(f"✓ {args.out}: {len(catalog['professors'])} docentes, {len(courses)} disciplinas, {len(offerings)} turmas "
        f"({with_prof} com docente identificado), {sum(1 for o in offerings if o.get('plan'))} planos de ensino")
    return 0


if __name__ == "__main__":
    sys.exit(main())

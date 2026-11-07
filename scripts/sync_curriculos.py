#!/usr/bin/env python3
"""Gera data/curriculos.json a partir dos relatórios curriculares oficiais da PROGRAD/UFMG.

Fontes:
  * PDFs "Relatório de versão curricular" (vários percursos na mesma tabela, um por linha) e
    "Relatório de percurso curricular" (um percurso só), extraídos com `pdftotext -layout`;
  * data/curriculos_manual.json (opcional): currículos transcritos à mão, no mesmo formato, anexados como estão.

Formato de saída: docs/curriculos-formato.md.

Uso:
  python3 scripts/sync_curriculos.py                       # baixa os PDFs das URLs oficiais
  python3 scripts/sync_curriculos.py --pdf-dir <pasta>     # usa PDFs já baixados (mesmos nomes de arquivo)
  python3 scripts/sync_curriculos.py --dry-run             # só mostra o resumo, não grava

Só usa a biblioteca padrão (mais o utilitário `pdftotext`, do poppler). O PDF é tratado como dado não confiável:
nada dele é executado; linhas que o parser não entende são listadas no fim, nunca descartadas em silêncio.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "curriculos.json"
MANUAL = ROOT / "data" / "curriculos_manual.json"
CATALOG = ROOT / "data" / "catalog.json"
UA = "DCCHub-sync/1.0 (projeto estudantil; leitura dos relatorios curriculares publicos)"

PERCURSO_GERAL = "Bacharelado (núcleo avançado + núcleo geral)"

# Um item por currículo gerado. `percursoId` None = usar o percurso padrão declarado no cabeçalho do relatório.
FONTES: list[dict] = [
    {
        "id": "cc-2019", "curso": "Ciência da Computação", "versao": "2019",
        "percurso": PERCURSO_GERAL, "rotulo": "Ciência da Computação — entrei até 2024",
        "arquivo": "versao_curricular_bcc_20241208.pdf",
        "url": "https://dcc.ufmg.br/wp-content/uploads/versao_curricular_bcc_20241208.pdf",
        "percursoId": "01-02",
    },
    {
        "id": "cd-2024", "curso": "Ciência de Dados", "versao": "2024",
        "rotulo": "Ciência de Dados — entrei em 2024 ou 2025",
        "arquivo": "versao_curricular_BCD_2025.pdf",
        "url": "https://dcc.ufmg.br/wp-content/uploads/versao_curricular_BCD_2025.pdf",
        "percursoId": None,
    },
    {
        "id": "cd-2026", "curso": "Ciência de Dados", "versao": "2026",
        "percurso": PERCURSO_GERAL, "rotulo": "Ciência de Dados — entrei a partir de 2026",
        "arquivo": "BCD-versao-V2026-1-1.pdf",
        "url": "https://dcc.ufmg.br/wp-content/uploads/BCD-versao-V2026-1-1.pdf",
        "percursoId": None,
    },
    {
        "id": "mc-2025", "curso": "Matemática Computacional", "versao": "2025",
        "percurso": PERCURSO_GERAL, "rotulo": "Matemática Computacional",
        "arquivo": "grade-geral.pdf",
        "url": "https://www.matcomp.dcc.ufmg.br/grade/grade-geral.pdf",
        "percursoId": None,
    },
    {
        "id": "mc-2025-computacao", "curso": "Matemática Computacional", "versao": "2025",
        "percurso": "Bacharelado com perfil em Computação",
        "rotulo": "Matemática Computacional — perfil Computação",
        "arquivo": "grade-computacao.pdf",
        "url": "https://www.matcomp.dcc.ufmg.br/grade/grade-computacao.pdf",
        "percursoId": None,
    },
]

AVISO_OPTATIVAS_CH = ("A carga de optativas de cada período soma toda a carga horária adicional sugerida nele para o percurso "
                      "(optativas, núcleo avançado, núcleo geral/formação livre etc.).")

# ---------------------------------------------------------------- expressões
CODE_RE = re.compile(r"^[A-Z]{3}\d{3}$")
CODE_FIND = re.compile(r"\b[A-Z]{3}\d{3}\b")
ID_RE = re.compile(r"^[a-z0-9-]+$")
PERC_ID = re.compile(r"(?<![\d-])\d\d-\d\d(?![\d-])")
ACTIVITY = re.compile(r"^(\s*)([A-Z0-9]{3}) - ([A-Z0-9]{3,}) - (.*)$")
PERIOD = re.compile(r"^\s*(\d{1,2})\s*º\s+PER[ÍI]ODO\s*$")
DATE = re.compile(r"\b(\d\d)/(\d\d)/(\d{4}) \d\d\.\d\d\.\d\d\b")
# versão: "<números> 01-02 [sugestão] grupo natGrupo natPercurso pré-requisitos"
SEG_TAIL = re.compile(r"^(?:(\S+)\s+)?(\d+)\s+(OB|OP)\s+(OB|OP)\s*(.*)$")
# percurso: "créditos teórica prática total S/N [grupo] natureza [grupo] pré-requisitos adicionais"
PERC_ROW = re.compile(r"^(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([SN])\s+(?:(\d+)\s+)?(OB|OP)\s+(?:(\d+)\s+)?(.*)$")
# carga horária adicional, versão: "[01-02]  02001-D-20192-10 - Optativas   120"
ADIC_VERSAO = re.compile(r"^\s*(?:(\d\d-\d\d)\s+)?(\d{5}-[A-Z]-\d{5}-\d+\s+-\s+.+?)\s{2,}(\d+)\s*$")
ADIC_LABEL = re.compile(r"^\s*(\d\d-\d\d)\s*$")
# carga horária adicional, percurso: "Optativas   60"
ADIC_PERCURSO = re.compile(r"^\s+([^\d\s][^\d]*?)\s{2,}(\d+)\s*$")
PREREQ_JUNK = re.compile(r"[A-Z]{3}\d{3}|\bE\b|\bOU\b|[,()\-]|\s")

BOILERPLATE = ("RELATÓRIO DE VERSÃO CURRICULAR", "RELATÓRIO DE PERCURSO CURRICULAR", "Pró-reitoria de Graduação",
               "UNIVERSIDADE FEDERAL DE MINAS GERAIS", "Configuração Curricular", "Tabela organizada por")


# ---------------------------------------------------------------- texto
def fold(text: str) -> str:
    """Sem acentos, minúsculo e com espaços normalizados (para comparar nomes)."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip().lower()


def has_accent(word: str) -> bool:
    return fold(word) != word.lower()


LOWER_WORDS = {"a", "à", "ao", "aos", "as", "às", "o", "os", "de", "da", "do", "das", "dos", "e", "em", "na", "no",
               "nas", "nos", "para", "por", "pela", "pelo", "pelas", "pelos", "com", "sem", "sob", "ou", "um", "uma"}
ROMAN = re.compile(r"^(?:I|II|III|IV|V|VI|VII|VIII|IX|X)$")
ACRONYMS = {"TGA"}


def build_accent_map(names: list[str]) -> dict[str, str]:
    """Palavra sem acento -> grafia acentuada, aprendida dos próprios nomes (PDFs + catálogo).

    Só entra a palavra que aparece acentuada de UM único jeito nas fontes (ex.: CÁLCULO), assim
    "CALCULO" vira "CÁLCULO" porque outra linha oficial grafa assim, e nada é adivinhado.
    """
    variants: dict[str, set[str]] = {}
    for name in names:
        for word in re.findall(r"[^\W\d_]+", name):
            if len(word) >= 3 and has_accent(word):
                variants.setdefault(fold(word), set()).add(word.upper())
    return {key: next(iter(vals)) for key, vals in variants.items() if len(vals) == 1}


def proper_case(name: str, accents: dict[str, str] | None = None) -> str:
    """CAIXA ALTA -> grafia normal: artigos/preposições minúsculos, romanos e letras finais (Álgebra A) maiúsculos."""
    accents = accents or {}
    words = name.split()
    out = []
    for i, word in enumerate(words):
        pieces = re.split(r"([^\w]+)", word)
        fixed = []
        for piece in pieces:
            if not piece or not re.match(r"\w", piece):
                fixed.append(piece)
                continue
            up = piece.upper()
            if not has_accent(up) and fold(up) in accents:
                up = accents[fold(up)]
            if ROMAN.match(up) or up in ACRONYMS:
                fixed.append(up)
            elif (len(up) == 1 and i > 0 and up != "E"
                  and (i == len(words) - 1 or ROMAN.match(words[i + 1].upper()))):
                fixed.append(up)  # "Equações Diferenciais A", "Economia A I"
            elif i > 0 and len(pieces) == 1 and up.lower() in LOWER_WORDS:
                fixed.append(up.lower())
            else:
                fixed.append(up[:1] + up[1:].lower())
        out.append("".join(fixed))
    return " ".join(out)


def chunks(line: str) -> list[tuple[int, str]]:
    """Divide uma linha do layout em blocos separados por 2+ espaços: [(coluna, texto)]."""
    return [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", line)]


def is_name_text(text: str) -> bool:
    return (bool(re.search(r"[^\W\d_]", text)) and not re.search(r"[a-zà-ÿ]", text)
            and not CODE_FIND.fullmatch(text.strip(" ,")) and not PERC_ID.search(text)
            and text.strip() not in {"OB", "OP", "S", "N", "-"})


def prereq_codes(text: str) -> tuple[list[str], str]:
    """Códigos de pré-requisito (separadores ',', 'E', 'OU' são ignorados) e o que sobrou de estranho."""
    codes: list[str] = []
    for code in CODE_FIND.findall(text):
        if code not in codes:
            codes.append(code)
    return codes, PREREQ_JUNK.sub("", text)


# ---------------------------------------------------------------- catálogo
class Catalog:
    """Nomes bem grafados do data/catalog.json, por código (id > aliases > codes sem ambiguidade)."""

    def __init__(self, data: dict | None) -> None:
        self.by_id: dict[str, dict] = {}
        self.by_alias: dict[str, list[dict]] = {}
        self.by_code: dict[str, list[dict]] = {}
        for course in (data or {}).get("courses", []):
            if course.get("kind", "regular") != "regular":
                continue
            self.by_id[course["id"]] = course
            for alias in course.get("aliases") or []:
                self.by_alias.setdefault(alias, []).append(course)
            for code in course.get("codes") or []:
                self.by_code.setdefault(code, []).append(course)

    def names(self) -> list[str]:
        return [c["name"] for c in self.by_id.values()]

    def name_for(self, code: str, source_name: str) -> str | None:
        """Nome do catálogo, só se for o mesmo nome da fonte a menos de maiúsculas/acentos."""
        if code in self.by_id:
            candidates = [self.by_id[code]]
        elif len(self.by_alias.get(code, [])) == 1:
            candidates = self.by_alias[code]
        else:
            candidates = self.by_code.get(code, [])
        if len(candidates) != 1:
            return None
        name = candidates[0]["name"]
        return name if fold(name) == fold(source_name) else None


# ---------------------------------------------------------------- parser
def parse_header(text: str) -> dict:
    """Metadados do relatório: formato, curso, versão, percurso padrão/escolhido, data e situação."""
    lines = text.splitlines()
    info: dict = {"formato": "percurso" if "Relatório de percurso curricular" in text else "versao",
                  "percursos": {}}
    if "Relatório de versão curricular" not in text and info["formato"] != "percurso":
        raise ValueError("não parece um relatório curricular da PROGRAD")
    m = re.search(r"Curso:\s+(?:\d+\s+-\s+)?(.+?)\s{2,}Versão curricular:\s+(\S+)", text)
    if m:
        info["curso"], info["versaoOficial"] = m.group(1).strip(), m.group(2)
    m = DATE.search(text)
    if m:
        info["data"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"Situação:\s+(\S[^\n]*?)(?:\s{2,}|\n)", text)
    if m:
        info["situacao"] = m.group(1).strip()
    if info["formato"] == "percurso":
        m = re.search(r"Identificador:\s+(\d\d-\d\d)", text)
        n = re.search(r"^Nome:\s+(.+?)\s*$", text, re.M)
        if m:
            info["padrao"] = m.group(1)
            info["percursos"][m.group(1)] = n.group(1).strip() if n else ""
    else:
        section = ""
        for line in lines:
            if line.startswith("Disponibilidade nas subdivisões"):
                section = "disp"
            elif line.startswith("Percursos curriculares que compõem"):
                section = "perc"
            elif line.startswith("Atividades acadêmicas"):
                break
            elif section == "disp" and "padrao" not in info:
                m = re.search(r"\s(\d\d-\d\d)\s{2,}(\S.*?)\s*$", line)
                if m:
                    info["padrao"] = m.group(1)
            elif section == "perc":
                m = re.match(r"^\s+(\d\d-\d\d)\s{2,}-*(\S.*?)\s*$", line)
                if m:
                    info["percursos"][m.group(1)] = m.group(2)
    return info


class Record:
    """Uma atividade acadêmica com suas linhas de continuação."""

    def __init__(self, indent: int, tipo: str, codigo: str, nome: str, data: str, line: str, period: int | None,
                 section: str) -> None:
        self.indent, self.tipo, self.codigo = indent, tipo, codigo
        self.nome_parts = [nome]
        self.data_lines = [data]          # blocos de dados, uma entrada por linha física
        self.raw = [line.rstrip()]
        self.period, self.section = period, section

    @property
    def nome(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.nome_parts)).strip()


def split_name_data(line: str, name_col: int) -> tuple[str, str]:
    """Separa a parte de nome (blocos que começam na coluna do nome) da parte de dados."""
    name, data = [], []
    for col, txt in chunks(line):
        if col <= name_col and not data and is_name_text(txt):
            name.append(txt)
        else:
            data.append(txt)
    return " ".join(name), "  ".join(data)


def scan(text: str) -> tuple[list[Record], dict[int, list[tuple[str, int, str]]], list[str]]:
    """Percorre o texto: atividades (com continuação), carga adicional por período e linhas não entendidas.

    Retorna (registros, adicionais, problemas). `adicionais[n]` = [(kind, posição, texto)] com as linhas cruas
    do quadro "Carga horária adicional do período" (resolvidas por percurso depois).
    """
    records: list[Record] = []
    adicionais: dict[int, list[tuple[str, int, str]]] = {}
    problems: list[str] = []
    period: int | None = None
    section = "pre"       # pre | periodo | adicional | sem_periodo | optativas | estagio | fim
    current: Record | None = None
    for raw in text.splitlines():
        line = raw.rstrip().replace("\f", "")
        stripped = line.strip()
        if not stripped:
            continue
        if any(b in line for b in BOILERPLATE) or DATE.match(stripped):
            continue
        m = PERIOD.match(line)
        if m:
            period, section, current = int(m.group(1)), "periodo", None
            continue
        if stripped.startswith("Atividades acadêmicas e carga horária adicional"):
            section, current = "periodo", None
            continue
        if stripped.startswith("Carga horária adicional do período"):
            section, current = "adicional", None
            continue
        if stripped.startswith("ATIVIDADES SEM PERÍODO SUGERIDO") or stripped == "Atividades optativas no percurso":
            section, period, current = "optativas", None, None
            continue
        if stripped.upper().startswith("ATIVIDADES DE ESTÁGIO CURRICULAR"):
            section, period, current = "estagio", None, None
            continue
        if stripped.startswith(("Quadro de integralização", "Grupos de atividades", "Tipos de atividades",
                                "Regras curriculares")):
            section, current = "fim", None
            continue
        if section in ("pre", "fim"):
            continue
        m = ACTIVITY.match(line)
        if m:
            if section == "adicional":
                section = "periodo"
            indent, tipo, codigo, rest = len(m.group(1)), m.group(2), m.group(3), m.group(4)
            first = re.split(r"\s{2,}", rest, maxsplit=1)
            nome, data = first[0], first[1] if len(first) > 1 else ""
            current = Record(indent, tipo, codigo, nome, data, line, period, section)
            records.append(current)
            continue
        if section == "adicional":
            if period is None:
                problems.append(f"carga adicional fora de período: {stripped!r}")
                continue
            bucket = adicionais.setdefault(period, [])
            if ADIC_LABEL.match(line):
                bucket.append(("label", len(bucket), ADIC_LABEL.match(line).group(1)))
            elif ADIC_VERSAO.match(line) or ADIC_PERCURSO.match(line):
                bucket.append(("row", len(bucket), line))
            elif re.search(r"\d+\s*$", stripped) and not stripped.startswith(("Percurso", "Grupo de")):
                problems.append(f"{period}º período, carga adicional não entendida: {stripped!r}")
            continue
        if current is not None:
            if re.search(r"[a-zà-ÿ]", stripped):
                continue  # cabeçalho de tabela repetido (quebra de página): não encerra a atividade
            name, data = split_name_data(line, current.indent + 3)
            if name:
                current.nome_parts.append(name)
            if data:
                current.data_lines.append(data)
            current.raw.append(line)
            continue
        if CODE_FIND.search(stripped) or PERC_ID.search(stripped):
            problems.append(f"linha não entendida: {stripped!r}")
    return records, adicionais, problems


def resolve_adicionais(entries: list[tuple[str, int, str]], formato: str, percurso: str) -> tuple[int, list[str]]:
    """Carga adicional do período para um percurso.

    No relatório de versão o identificador do percurso aparece centralizado verticalmente no bloco de linhas que
    lhe pertence: na mesma linha (bloco de 1 ou 3 linhas) ou numa linha só dele, entre as linhas do bloco. Assim,
    lendo em ordem, cada rótulo tem k linhas antes e k linhas depois dentro do seu bloco.
    """
    problems: list[str] = []
    if formato == "percurso":
        total = 0
        for kind, _, line in entries:
            m = ADIC_PERCURSO.match(line) if kind == "row" else None
            if m:
                total += int(m.group(2))
        return total, problems
    rows: list[tuple[str | None, str, int]] = []   # (rótulo na mesma linha, grupo, ch)
    labels_at: list[tuple[int, str]] = []          # (índice da próxima linha de dados, rótulo) p/ rótulos soltos
    for kind, _, payload in entries:
        if kind == "label":
            labels_at.append((len(rows), payload))
        else:
            m = ADIC_VERSAO.match(payload)
            if not m:
                problems.append(f"carga adicional não entendida: {payload.strip()!r}")
                continue
            rows.append((m.group(1), m.group(2), int(m.group(3))))
    # sequência de eventos: rótulos (soltos ou na linha) em ordem
    events: list[tuple[str, int, bool]] = []  # (rótulo, índice da linha, inline)
    loose = iter(labels_at)
    nxt = next(loose, None)
    for i, (inline, _, _) in enumerate(rows):
        while nxt is not None and nxt[0] <= i:
            events.append((nxt[1], nxt[0], False))
            nxt = next(loose, None)
        if inline:
            events.append((inline, i, True))
    while nxt is not None:
        events.append((nxt[1], nxt[0], False))
        nxt = next(loose, None)
    owner: list[str | None] = [None] * len(rows)
    start = 0
    for label, idx, inline in events:
        k = idx - start
        end = idx + 1 + k if inline else idx + k
        if end > len(rows) or k < 0:
            problems.append(f"bloco de carga adicional inconsistente para {label}")
            end = min(end, len(rows))
        for j in range(start, end):
            owner[j] = label
        start = end
    if start != len(rows):
        problems.append("linhas de carga adicional sem percurso: " + ", ".join(r[1] for r in rows[start:]))
    total = sum(ch for (_, _, ch), who in zip(rows, owner) if who == percurso)
    return total, problems


def parse_versao_segments(rec: Record) -> tuple[list[dict], list[str]]:
    """Relatório de versão: um segmento por percurso (identificador, carga, grupo, natureza, pré-requisitos)."""
    segments: list[dict] = []
    problems: list[str] = []
    numbers: list[int] = []
    for data in rec.data_lines:
        if not data.strip():
            continue
        m = PERC_ID.search(data)
        if m:
            before, after = data[:m.start()], data[m.end():].strip()
            if before.strip():
                if re.fullmatch(r"[\d\s]+", before.strip()):
                    numbers = [int(x) for x in before.split()]
                else:
                    problems.append(f"{rec.codigo}: dados inesperados {before.strip()!r}")
            t = SEG_TAIL.match(after)
            if not t:
                problems.append(f"{rec.codigo}: segmento não entendido {data.strip()!r}")
                continue
            segments.append({"percurso": m.group(0), "grupo": t.group(2), "natureza": t.group(4),
                             "prereqText": t.group(5), "numbers": numbers})
        elif segments:
            segments[-1]["prereqText"] += " " + data   # pré-requisitos quebrados na linha de baixo
        else:
            problems.append(f"{rec.codigo}: linha antes do primeiro percurso {data.strip()!r}")
    return segments, problems


def total_ch(numbers: list[int]) -> int | None:
    # tabela por período: Mín Máx Total Teórica Prática; sem período: Total Teórica Prática
    if len(numbers) == 5:
        return numbers[2]
    if len(numbers) == 3:
        return numbers[0]
    return None


def parse_report(text: str, percurso_id: str | None = None) -> dict:
    """Extrai de um relatório (texto do pdftotext -layout) o percurso pedido (ou o padrão).

    Retorna {"info", "percurso", "periodos": {n: {"disciplinas", "optativasCh"}}, "optativas", "problemas",
    "ignoradas"} com nomes ainda em CAIXA ALTA (a grafia é ajustada em `build_curriculo`).
    """
    info = parse_header(text)
    percurso = percurso_id or info.get("padrao")
    if not percurso:
        raise ValueError("percurso padrão não encontrado no cabeçalho")
    records, adicionais, problems = scan(text)
    periodos: dict[int, dict] = {}
    optativas: list[dict] = []
    ignoradas: list[str] = []
    seen_opt: set[str] = set()

    for rec in records:
        if info["formato"] == "versao":
            segments, probs = parse_versao_segments(rec)
            problems += probs
            seg = next((s for s in segments if s["percurso"] == percurso), None)
            if not segments:
                problems.append(f"{rec.codigo}: sem dados de percurso: {' / '.join(r.strip() for r in rec.raw)!r}")
                continue
            if seg is None:
                continue  # atividade só de outro percurso
            ch = total_ch(seg["numbers"])
            natureza = seg["natureza"]
            pre_text = seg["prereqText"]
        else:
            data = " ".join(d.strip() for d in rec.data_lines if d.strip())
            m = PERC_ROW.match(data)
            if not m:
                problems.append(f"{rec.codigo}: linha não entendida {' / '.join(r.strip() for r in rec.raw)!r}")
                continue
            ch = int(m.group(4))
            natureza = "OB" if rec.section == "periodo" else "OP"
            rest = m.group(9).split()
            adic = rest[-1] if rest else "-"
            if adic != "-":
                problems.append(f"{rec.codigo}: coluna 'adicionais' não vazia ({adic!r}), ignorada")
            pre_text = " ".join(rest[:-1])
        if rec.tipo != "DIG":
            ignoradas.append(f"{rec.tipo} {rec.codigo} {rec.nome}")
            continue
        prereq, junk = prereq_codes(pre_text)
        if junk:
            problems.append(f"{rec.codigo}: pré-requisito com texto não reconhecido {pre_text.strip()!r}")
        if not CODE_RE.match(rec.codigo):
            problems.append(f"{rec.codigo}: código fora do padrão AAA999")
        item = {"codigo": rec.codigo if CODE_RE.match(rec.codigo) else None, "nome": rec.nome,
                "natureza": natureza, "ch": ch, "depto": rec.codigo[:3] if CODE_RE.match(rec.codigo) else None,
                "prereq": prereq}
        if ch is None:
            problems.append(f"{rec.codigo}: carga horária não encontrada")
        if rec.section == "periodo" and rec.period is not None:
            per = periodos.setdefault(rec.period, {"disciplinas": [], "optativasCh": 0})
            dup = next((d for d in per["disciplinas"] if d["codigo"] == item["codigo"]), None)
            if dup is not None:
                if dup != item:
                    problems.append(f"{rec.codigo}: repetida no {rec.period}º período com dados diferentes")
                continue  # tabela repetida após quebra de página
            per["disciplinas"].append(item)
        elif rec.section == "optativas":
            if natureza == "OB" and info["formato"] == "versao":
                problems.append(f"{rec.codigo}: obrigatória sem período sugerido (listada entre as optativas)")
            if item["codigo"] in seen_opt:
                continue
            seen_opt.add(item["codigo"])
            item.pop("natureza")
            optativas.append(item)
        else:
            problems.append(f"{rec.codigo}: atividade fora de seção conhecida ({rec.section})")

    for n, entries in adicionais.items():
        ch, probs = resolve_adicionais(entries, info["formato"], percurso)
        problems += [f"{n}º período: {p}" for p in probs]
        periodos.setdefault(n, {"disciplinas": [], "optativasCh": 0})["optativasCh"] = ch
    # períodos declarados sem nenhuma atividade obrigatória (ex.: "8º PERÍODO" só com carga adicional)
    for line in text.splitlines():
        m = PERIOD.match(line)
        if m:
            periodos.setdefault(int(m.group(1)), {"disciplinas": [], "optativasCh": 0})
    return {"info": info, "percurso": percurso, "periodos": periodos, "optativas": optativas,
            "problemas": problems, "ignoradas": ignoradas}


# ---------------------------------------------------------------- montagem
def build_curriculo(fonte: dict, parsed: dict, catalog: Catalog, accents: dict[str, str]) -> dict:
    info = parsed["info"]

    def nome(item: dict) -> str:
        source = item["nome"]
        return (catalog.name_for(item["codigo"], source) if item["codigo"] else None) or proper_case(source, accents)

    def disc(item: dict, with_nat: bool) -> dict:
        out = {"codigo": item["codigo"], "nome": nome(item)}
        if with_nat:
            out["natureza"] = item["natureza"]
        out.update({"ch": item["ch"], "depto": item["depto"], "prereq": item["prereq"]})
        return out

    avisos: list[str] = []
    if any(p["optativasCh"] for p in parsed["periodos"].values()):
        avisos.append(AVISO_OPTATIVAS_CH)
    situacao = info.get("situacao", "")
    if situacao and situacao.lower() not in ("vigente", "liberada"):
        avisos.append(f"Versão com situação \"{situacao}\" na PROGRAD: pode mudar até ser liberada.")
    cur: dict = {"id": fonte["id"], "curso": fonte["curso"], "versao": fonte["versao"]}
    if fonte.get("percurso"):
        cur["percurso"] = fonte["percurso"]
    cur["rotulo"] = fonte["rotulo"]
    if fonte.get("nota"):
        cur["nota"] = fonte["nota"]
    cur["fonte"] = {"tipo": "prograd", "url": fonte["url"], "versaoOficial": info.get("versaoOficial"),
                    "data": info.get("data")}
    cur["periodos"] = [
        {"n": n, "disciplinas": [disc(d, True) for d in sorted(p["disciplinas"], key=lambda d: d["codigo"] or "")],
         "optativasCh": p["optativasCh"]}
        for n, p in sorted(parsed["periodos"].items())
    ]
    cur["optativas"] = [disc(d, False) for d in sorted(parsed["optativas"], key=lambda d: d["codigo"] or "")]
    cur["avisos"] = avisos
    return cur


# ---------------------------------------------------------------- validação
def _check_disc(where: str, d: object, with_nat: bool) -> list[str]:
    errs: list[str] = []
    if not isinstance(d, dict):
        return [f"{where}: não é objeto"]
    code = d.get("codigo")
    if code is not None and (not isinstance(code, str) or not CODE_RE.match(code)):
        errs.append(f"{where}: codigo inválido {code!r}")
    if not isinstance(d.get("nome"), str) or not d["nome"].strip():
        errs.append(f"{where}: nome vazio")
    elif d["nome"] == d["nome"].upper() and re.search(r"[A-Z]{4}", d["nome"]):
        errs.append(f"{where}: nome em CAIXA ALTA {d['nome']!r}")
    if with_nat and d.get("natureza") not in ("OB", "OP"):
        errs.append(f"{where}: natureza inválida {d.get('natureza')!r}")
    ch = d.get("ch")
    if ch is not None and (not isinstance(ch, int) or isinstance(ch, bool) or ch <= 0):
        errs.append(f"{where}: ch inválida {ch!r}")
    depto = d.get("depto")
    if depto is not None and (not isinstance(depto, str) or not re.fullmatch(r"[A-Z]{3}", depto)):
        errs.append(f"{where}: depto inválido {depto!r}")
    if isinstance(code, str) and CODE_RE.match(code) and depto != code[:3]:
        errs.append(f"{where}: depto {depto!r} não bate com o código {code}")
    prereq = d.get("prereq")
    if not isinstance(prereq, list) or any(not isinstance(p, str) or not CODE_RE.match(p) for p in prereq):
        errs.append(f"{where}: prereq inválido {prereq!r}")
    return errs


def validate(curriculo: dict) -> list[str]:
    """Erros de formato de UM currículo (lista vazia = ok). Ver docs/curriculos-formato.md."""
    if not isinstance(curriculo, dict):
        return ["currículo não é objeto"]
    cid = curriculo.get("id")
    tag = cid if isinstance(cid, str) else "?"
    errs: list[str] = []
    if not isinstance(cid, str) or not ID_RE.match(cid):
        errs.append(f"{tag}: id inválido {cid!r}")
    for key in ("curso", "versao", "rotulo"):
        if not isinstance(curriculo.get(key), str) or not curriculo[key].strip():
            errs.append(f"{tag}: campo {key!r} vazio")
    for key in ("percurso", "nota"):
        if key in curriculo and curriculo[key] is not None and not isinstance(curriculo[key], str):
            errs.append(f"{tag}: campo {key!r} deve ser texto")
    fonte = curriculo.get("fonte")
    if not isinstance(fonte, dict) or fonte.get("tipo") not in ("prograd", "imagem"):
        errs.append(f"{tag}: fonte.tipo deve ser 'prograd' ou 'imagem'")
    periodos = curriculo.get("periodos")
    if not isinstance(periodos, list) or not periodos:
        errs.append(f"{tag}: periodos vazio")
        periodos = []
    last = 0
    seen: set[str] = set()
    null_code = False
    for p in periodos:
        if not isinstance(p, dict):
            errs.append(f"{tag}: período não é objeto")
            continue
        n = p.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            errs.append(f"{tag}: período com n inválido {n!r}")
        elif n <= last:
            errs.append(f"{tag}: períodos fora de ordem ({n} depois de {last})")
        else:
            last = n
        och = p.get("optativasCh")
        if not isinstance(och, int) or isinstance(och, bool) or och < 0:
            errs.append(f"{tag}: {n}º período com optativasCh inválida {och!r}")
        discs = p.get("disciplinas")
        if not isinstance(discs, list):
            errs.append(f"{tag}: {n}º período sem lista de disciplinas")
            continue
        for d in discs:
            errs += _check_disc(f"{tag} {n}º período", d, True)
            code = d.get("codigo") if isinstance(d, dict) else None
            if code is None:
                null_code = True
            elif code in seen:
                errs.append(f"{tag}: {code} repetida entre as disciplinas dos períodos")
            else:
                seen.add(code)
    optativas = curriculo.get("optativas")
    if not isinstance(optativas, list):
        errs.append(f"{tag}: optativas deve ser lista")
        optativas = []
    seen_opt: set[str] = set()
    for d in optativas:
        errs += _check_disc(f"{tag} optativa", d, False)
        code = d.get("codigo") if isinstance(d, dict) else None
        if code is None:
            null_code = True
        elif code in seen_opt:
            errs.append(f"{tag}: optativa {code} repetida")
        else:
            seen_opt.add(code)
    avisos = curriculo.get("avisos")
    if not isinstance(avisos, list) or any(not isinstance(a, str) for a in avisos):
        errs.append(f"{tag}: avisos deve ser lista de textos")
    elif null_code and not avisos:
        errs.append(f"{tag}: há código null sem aviso explicando")
    return errs


def validate_all(curriculos: list[dict]) -> list[str]:
    errs: list[str] = []
    ids: set[str] = set()
    for c in curriculos:
        errs += validate(c)
        cid = c.get("id") if isinstance(c, dict) else None
        if cid in ids:
            errs.append(f"id repetido: {cid}")
        ids.add(cid)
    return errs


# ---------------------------------------------------------------- E/S
def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def pdf_to_text(pdf: Path) -> str:
    if not shutil.which("pdftotext"):
        raise SystemExit("pdftotext não encontrado (instale o poppler-utils)")
    res = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True,
                         check=True, timeout=120)
    return res.stdout.decode("utf-8", "replace")


def load_manual(path: Path) -> list[dict]:
    """Currículos transcritos à mão: anexados como estão; problemas viram avisos, sem derrubar a execução."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"AVISO: {path.name} ilegível ({exc}); ignorado", file=sys.stderr)
        return []
    items = data.get("curriculos") if isinstance(data, dict) else None
    if not isinstance(items, list):
        print(f"AVISO: {path.name} sem lista 'curriculos'; ignorado", file=sys.stderr)
        return []
    for c in items:
        for err in validate(c):
            print(f"AVISO ({path.name}): {err}", file=sys.stderr)
    return [c for c in items if isinstance(c, dict)]


def summary(cur: dict) -> str:
    lines = [f"{cur['id']}: {cur['rotulo']}  [{cur['fonte'].get('versaoOficial')}, {cur['fonte'].get('data')}]"]
    for p in cur["periodos"]:
        codes = " ".join((d["codigo"] or "?") + ("" if d["natureza"] == "OB" else "(OP)") for d in p["disciplinas"])
        lines.append(f"  {p['n']:>2}º: {codes or '-'}" + (f"  +{p['optativasCh']}h opt" if p["optativasCh"] else ""))
    lines.append(f"  optativas: {len(cur['optativas'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", type=Path, help="pasta com os PDFs já baixados (senão baixa das URLs)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--manual", type=Path, default=MANUAL)
    ap.add_argument("--dry-run", action="store_true", help="não grava, só mostra o resumo")
    args = ap.parse_args(argv)

    catalog_data = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else None
    catalog = Catalog(catalog_data)
    texts: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="curriculos-") as tmp:
        for fonte in FONTES:
            if args.pdf_dir:
                pdf = args.pdf_dir / fonte["arquivo"]
            else:
                pdf = Path(tmp) / fonte["arquivo"]
                print(f"baixando {fonte['url']}", file=sys.stderr)
                download(fonte["url"], pdf)
            texts[fonte["id"]] = pdf_to_text(pdf)

    parsed = {fid: parse_report(texts[fid], f.get("percursoId")) for f in FONTES for fid in [f["id"]]}
    source_names = [d["nome"] for p in parsed.values() for per in p["periodos"].values() for d in per["disciplinas"]]
    source_names += [d["nome"] for p in parsed.values() for d in p["optativas"]]
    accents = build_accent_map(source_names + catalog.names())

    curriculos: list[dict] = []
    had_problems = False
    for fonte in FONTES:
        res = parsed[fonte["id"]]
        info = res["info"]
        if fonte.get("percursoId") and info.get("padrao") and info["padrao"] != fonte["percursoId"]:
            print(f"AVISO {fonte['id']}: percurso escolhido {fonte['percursoId']} difere do padrão {info['padrao']}",
                  file=sys.stderr)
        cur = build_curriculo(fonte, res, catalog, accents)
        curriculos.append(cur)
        print(summary(cur))
        print(f"  percurso {res['percurso']} ({info['percursos'].get(res['percurso'], '?')}); "
              f"{len(res['ignoradas'])} atividades não-disciplina ignoradas")
        for prob in res["problemas"]:
            had_problems = True
            print(f"  PROBLEMA: {prob}", file=sys.stderr)

    errs = validate_all(curriculos)
    for err in errs:
        print(f"ERRO: {err}", file=sys.stderr)
    if errs:
        return 1

    ids = {c["id"] for c in curriculos}
    for c in load_manual(args.manual):
        if c.get("id") in ids:
            print(f"AVISO ({args.manual.name}): id {c.get('id')!r} já existe; entrada manual ignorada",
                  file=sys.stderr)
            continue
        ids.add(c.get("id"))
        curriculos.append(c)
        print(f"{c.get('id')}: anexado de {args.manual.name}")

    curriculos.sort(key=lambda c: (str(c.get("curso", "")), str(c.get("versao", "")), str(c.get("id", ""))))
    out = {"generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "curriculos": curriculos}
    if args.dry_run:
        print("(dry-run: nada gravado)")
    else:
        args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"gravado {args.out} ({len(curriculos)} currículos)")
    if had_problems:
        print("Há linhas não entendidas (ver PROBLEMA acima).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

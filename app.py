#!/usr/bin/env python3
"""DCCHub — avaliações anônimas, oferta, materiais e oportunidades do DCC/UFMG.

Servidor HTTP + API JSON usando apenas a biblioteca padrão do Python (3.10+).

    python3 app.py                       # http://localhost:8000
    PORT=9000 python3 app.py
    python3 scripts/sync_dcc.py          # atualiza docentes, disciplinas e ofertas a partir do site do DCC

Variáveis de ambiente (todas opcionais):
    DCCHUB_SECRET       chave HMAC dos identificadores anônimos (gerada e salva em secret.key, ao lado do banco, se ausente)
    DCCHUB_ADMIN_TOKEN  token do painel de moderação (/#/moderacao); sem ele, a moderação fica desativada
    DCCHUB_DB           caminho do banco SQLite (padrão: data/avaliacoes.db)
    DCCHUB_TRUST_PROXY  "1" para usar X-Forwarded-For como IP (somente atrás de proxy confiável)
    DCCHUB_ACCESS_CODES códigos de acesso da comunidade, separados por vírgula; se definido, os dados só saem
                        para navegadores que digitaram um código válido (trocar o código revoga os acessos antigos)
    DCCHUB_MOD_EMAIL    e-mail(s) da moderação, separados por vírgula: recebem um aviso (só contagens e motivos, no máximo
                        a cada 30 min) quando uma avaliação entra na fila de revisão. Usa o SMTP abaixo.
                        DCCHUB_PUBLIC_URL (ex.: https://seu.dominio) vira o link do painel no aviso.
    DCCHUB_EMAIL_LOGIN  "1" para liberar o acesso por código enviado ao e-mail @ufmg.br (ver email_login.py); o e-mail
                        não é guardado. Configuração do envio: DCCHUB_SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS; sem SMTP,
                        o código aparece no terminal (desenvolvimento). DCCHUB_EMAIL_EPOCH: trocar o valor revoga todos
                        os acessos feitos por e-mail; DCCHUB_EMAIL_DAILY_LIMIT: teto de e-mails por dia (250)
    DCCHUB_DOCENTE_LOGIN "1" (com o acesso por e-mail ligado) para o modo docente: o e-mail publicado de um docente
                        recebe um código que abre só a página dele, reduzida (sem semestre, conceito nem data das
                        avaliações; disciplinas com menos de 3 avaliações não aparecem). Desligado por padrão.
  Os nomes antigos (AVALIA_DCC_*) continuam aceitos.
    HOST / PORT             endereço de escuta (padrão 0.0.0.0:8000)
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import email_login
import mod_notify
import moderation

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
CATALOG_FILE = DATA / "catalog.json"
def env(name: str, default: str | None = None) -> str | None:
    """DCCHUB_<NOME>, com o nome antigo AVALIA_DCC_<NOME> como alternativa (instalações anteriores ao rebranding)."""
    return os.environ.get(f"DCCHUB_{name}", os.environ.get(f"AVALIA_DCC_{name}", default))


DB = Path(env("DB") or DATA / "avaliacoes.db")
COOKIE = "avalia_dcc"  # nome mantido de propósito: trocar desconectaria todo mundo da própria identidade anônima
TRUST_PROXY = env("TRUST_PROXY") == "1"
ADMIN_TOKEN = env("ADMIN_TOKEN", "") or ""
ACCESS_COOKIE = "dcchub_access"
ACCESS_CODES = [c.strip() for c in (env("ACCESS_CODES") or "").split(",") if c.strip()]
EMAIL = email_login.Config(env)
# avisos à moderação quando algo entra na fila (ver mod_notify.py); usa o mesmo SMTP do acesso por e-mail
MOD_EMAILS = [a.strip() for a in (env("MOD_EMAIL") or "").split(",") if a.strip()]
PUBLIC_URL = (env("PUBLIC_URL") or "").rstrip("/")
GATE_FREE = {"/api/access", "/api/access/email", "/api/access/email/verify", "/api/access/logout"}
# modo docente: o e-mail publicado de um docente dá acesso só à própria página, em versão reduzida (ver get_docente)
DOCENTE_LOGIN = EMAIL.enabled and env("DOCENTE_LOGIN") == "1"
DOCENTE_MIN = 3  # avaliações de uma disciplina só aparecem para o docente a partir deste número
DOCENTE_ROUTES = (r"/api/docente", r"/api/docente/reviews/\d+/report")
MAX_BODY = 16_384
ACTIVE_TERMS = 4  # disciplina sem oferta nos últimos 4 semestres publicados (2 anos) = fora de oferta


def load_secret() -> str:
    given = env("SECRET")
    if given:
        return given
    # a chave mora ao lado do banco (dados persistentes); data/secret.key é o local antigo
    path = DB.parent / "secret.key"
    legacy = DATA / "secret.key"
    if not path.exists() and legacy.exists():
        return legacy.read_text().strip()
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    path.write_text(value)
    os.chmod(path, 0o600)
    return value


SECRET = load_secret()

# --------------------------------------------------------------------------- definições do formulário
CRITERIA = [
    {"key": "overall", "label": "Avaliação geral", "short": "Geral", "hint": "Sua experiência como um todo", "low": "Péssima", "high": "Excelente"},
    {"key": "didactics", "label": "Didática", "short": "Didática", "hint": "Clareza das explicações e organização das aulas", "low": "Confusa", "high": "Muito clara"},
    {"key": "fairness", "label": "Avaliações justas", "short": "Justiça", "hint": "Provas e trabalhos coerentes com o que foi ensinado", "low": "Nada justas", "high": "Muito justas"},
    # aprender e passar são coisas diferentes: matéria difícil com avaliação tranquila, ou o contrário
    {"key": "difficulty", "label": "Dificuldade para aprender", "short": "Aprender", "hint": "Quão difícil foi entender o conteúdo",
     "low": "Muito fácil", "high": "Muito difícil",
     "levels": ["Muito fácil", "Fácil", "Média", "Difícil", "Muito difícil"], "neutral": True},
    {"key": "passing", "label": "Dificuldade para passar", "short": "Passar", "hint": "Quão difícil foi tirar nota e ser aprovado",
     "low": "Muito fácil", "high": "Muito difícil",
     "levels": ["Muito fácil", "Fácil", "Média", "Difícil", "Muito difícil"], "neutral": True},
    {"key": "workload", "label": "Carga de trabalho", "short": "Carga", "hint": "Tempo exigido fora da sala de aula", "low": "Muito leve",
     "high": "Muito pesada", "levels": ["Muito leve", "Leve", "Moderada", "Pesada", "Muito pesada"], "neutral": True},
]
# "levels": nome de cada ponto das escalas neutras (1 a 5), mostrado ao lado da média — "3,8" sozinho parece nota boa
CRITERIA_KEYS = [c["key"] for c in CRITERIA]
# avaliações anteriores à separação de "dificuldade" em aprender/passar têm "passing" vazio (NULL): a pergunta antiga
# misturava as duas coisas, então a nota antiga ficou em "aprender" e "passar" não é inventado. Médias ignoram vazios.
OPTIONAL_CRITERIA = {"passing"}
TAGS = {
    "positivo": ["Explica muito bem", "Aulas organizadas", "Bom material de apoio", "Acessível fora da aula", "Dá bons feedbacks",
                 "Exemplos práticos", "Aulas gravadas", "Inspira os alunos", "Respeitoso(a)"],
    "neutro": ["Muitos trabalhos práticos", "Listas de exercícios", "Provas difíceis", "Provas parecidas com as listas",
               "Projeto em grupo", "Seminários", "Muita leitura", "Muita matemática", "Muita programação",
               "Cobra presença", "Dá pontos extras", "Aulas em inglês"],
    "negativo": ["Critérios pouco claros", "Aulas confusas", "Demora a corrigir", "Pouco disponível", "Material desatualizado"],
}
ALL_TAGS = [t for group in TAGS.values() for t in group]
MAX_TAGS = 5
GRADES = ["A", "B", "C", "D", "E", "F", "Trancou", "Cursando", "Prefiro não dizer"]
COMMENT_MIN, COMMENT_MAX = 30, 2000
REPORT_REASONS = ["Ofensivo ou desrespeitoso", "Expõe dados pessoais", "Spam ou sem relação", "Informação falsa", "Outro"]
AUTO_HIDE_REPORTS = 4  # denúncias de REDES distintas (não de cookies) para tirar do ar até a moderação olhar
BURST_REVIEWS = 5  # avaliações do mesmo docente num só dia: destacadas no painel de moderação

def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return text


def moderation_flags(comment: str, professor: str | None = None) -> list[str]:
    """Motivos (mensagens) apontados pelo filtro automático — ver moderation.py. Lista vazia = publica direto."""
    return [i.message for i in moderation.analyze(comment, professor)]


BRT = 3 * 3600  # Brasília (UTC-3, sem horário de verão desde 2019)


def review_day(ts: float | None = None) -> int:
    """Datas guardadas só com precisão de dia (meia-noite de Brasília). Hora e minuto exatos permitiriam cruzar uma
    avaliação (ou um voto, que leva o mesmo hash de quem avalia) com registros externos de quando a pessoa usou o site."""
    ts = int(time.time() if ts is None else ts)
    return (ts - BRT) // 86400 * 86400 + BRT


def token_hash(token: str) -> str:
    return hmac.new(SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def clean_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_comment(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text


def current_term(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year}.{1 if today.month < 8 else 2}"


def valid_term(term: str) -> bool:
    m = re.fullmatch(r"(20\d{2})\.([12])", term)
    return bool(m) and "2010.1" <= term <= current_term()


# --------------------------------------------------------------------------- catálogo
class Catalog:
    """Docentes, disciplinas e ofertas (data/catalog.json), recarregado quando o arquivo muda."""

    def __init__(self, path: Path):
        self.path = path
        self.mtime = -1.0
        self.version = 0
        self.lock = threading.Lock()
        self.refresh()

    def refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
            archive = self.path.with_name(self.path.stem + "-arquivo.json")
            if archive.exists():
                mtime = max(mtime, archive.stat().st_mtime)
        except FileNotFoundError:
            mtime = 0.0
        if mtime == self.mtime:
            return
        with self.lock:
            if mtime == self.mtime:
                return
            raw = json.loads(self.path.read_text()) if mtime else {"professors": [], "courses": [], "offerings": [], "terms": []}
            archive = self.path.with_name(self.path.stem + "-arquivo.json")  # extintas: só para avaliações antigas
            if archive.exists():
                old = json.loads(archive.read_text())
                raw = {**raw, "courses": raw["courses"] + [{**c, "retired": True} for c in old.get("courses", [])],
                       "offerings": raw["offerings"] + old.get("offerings", [])}
            self.raw = raw
            self.professors = {p["slug"]: p for p in raw["professors"]}
            self.courses = {c["id"]: c for c in raw["courses"]}
            # códigos de versões antigas / de outros cursos -> código canônico (ver consolidate() no sync)
            self.alias = {a: c["id"] for c in raw["courses"] for a in c.get("aliases", [])}
            self.offerings = raw["offerings"]
            self.terms = raw.get("terms", [])
            self.latest_term = self.terms[-1] if self.terms else None
            # "em oferta": apareceu em alguma das últimas ACTIVE_TERMS ofertas publicadas; o resto é currículo antigo
            recent = set(self.terms[-ACTIVE_TERMS:])
            self.active = {cid for cid, c in self.courses.items() if recent & set(c["terms"]) and not c.get("retired")}
            self.generated_at = raw.get("generatedAt")
            self.sources = raw.get("sources", {})
            self.term_sources = raw.get("termSources", {})
            self.pairs: dict[tuple[str, str], list[str]] = defaultdict(list)  # (prof, course) -> termos
            self.prof_courses: dict[str, Counter] = defaultdict(Counter)
            self.course_profs: dict[str, Counter] = defaultdict(Counter)
            self.prof_terms: dict[str, set] = defaultdict(set)
            for o in self.offerings:
                for slug in o["professors"]:
                    self.pairs[(slug, o["course"])].append(o["term"])
                    self.prof_courses[slug][o["course"]] += 1
                    self.course_profs[o["course"]][slug] += 1
                    self.prof_terms[slug].add(o["term"])
            self.version += 1
            self.mtime = mtime

    def canonical(self, cid: str) -> str:
        return self.alias.get(cid, cid)

    def taught(self, professor: str, course: str, term: str | None = None) -> bool:
        terms = self.pairs.get((professor, course), [])
        return bool(terms) and (term is None or term in terms)


CATALOG = Catalog(CATALOG_FILE)


class Exams:
    """Acervo de provas antigas (data/provas.json, gerado por scripts/sync_provas.py), recarregado se mudar."""

    def __init__(self, path: Path):
        self.path, self.mtime, self.data, self.by_course = path, -1.0, None, {}

    def refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self.data, self.by_course, self.mtime = None, {}, -1.0
            return
        if mtime == self.mtime:
            return
        data = json.loads(self.path.read_text())
        by_course: dict[str, list] = defaultdict(list)
        for sec in data.get("sections", []):
            for e in sec.get("courses", []):
                if e.get("course"):
                    by_course[CATALOG.canonical(e["course"])].append({**e, "section": sec["title"]})
        self.data, self.by_course, self.mtime = data, dict(by_course), mtime

    def for_course(self, cid: str) -> dict | None:
        if not self.data or cid not in self.by_course:
            return None
        items = sorted(self.by_course[cid], key=lambda e: -e["files"])
        return {"name": self.data.get("name"), "url": self.data.get("url"), "items": items}


EXAMS = Exams(DATA / "provas.json")


class Opportunities:
    """Oportunidades de pesquisa/bolsas do DCC (data/oportunidades.json, gerado por scripts/sync_oportunidades.py)."""

    def __init__(self, path: Path):
        self.path, self.mtime, self.data = path, -1.0, None

    def refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self.data, self.mtime = None, -1.0
            return
        if mtime != self.mtime:
            self.data, self.mtime = json.loads(self.path.read_text()), mtime

    def for_professor(self, slug: str) -> list[dict]:
        if not self.data:
            return []
        return [{k: o[k] for k in ("slug", "title", "url", "deadline", "types", "audiences", "stipends")}
                for o in self.data["items"] if o["open"] and slug in o.get("professors", [])]


OPPORTUNITIES = Opportunities(DATA / "oportunidades.json")


# --------------------------------------------------------------------------- banco de dados
SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY,
  professor TEXT NOT NULL,
  course TEXT NOT NULL,
  term TEXT NOT NULL,
  overall INTEGER NOT NULL CHECK(overall BETWEEN 1 AND 5),
  didactics INTEGER NOT NULL CHECK(didactics BETWEEN 1 AND 5),
  fairness INTEGER NOT NULL CHECK(fairness BETWEEN 1 AND 5),
  difficulty INTEGER NOT NULL CHECK(difficulty BETWEEN 1 AND 5),
  passing INTEGER CHECK(passing BETWEEN 1 AND 5),
  workload INTEGER NOT NULL CHECK(workload BETWEEN 1 AND 5),
  would_retake INTEGER CHECK(would_retake IN (0, 1)),
  grade TEXT,
  tags TEXT NOT NULL DEFAULT '[]',
  comment TEXT NOT NULL,
  comment_key TEXT,
  status TEXT NOT NULL CHECK(status IN ('published', 'held', 'removed')),
  held_reason TEXT,
  reviewer_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER,
  UNIQUE(reviewer_hash, professor, course)
);
CREATE INDEX IF NOT EXISTS idx_reviews_professor ON reviews(professor, status);
CREATE INDEX IF NOT EXISTS idx_reviews_course ON reviews(course, status);
CREATE TABLE IF NOT EXISTS votes (
  review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
  voter_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (review_id, voter_hash)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS class_reports (
  term TEXT NOT NULL,
  professor TEXT NOT NULL,
  course TEXT NOT NULL,
  reporter_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (term, professor, course, reporter_hash)
) WITHOUT ROWID;
-- códigos de acesso por e-mail já usados (nonce do desafio, até expirar): uso único. Nome antigo, de quando era link
CREATE TABLE IF NOT EXISTS used_links (
  nonce TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS reports (
  review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
  reporter_hash TEXT NOT NULL,
  reason TEXT NOT NULL,
  detail TEXT,
  created_at INTEGER NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (review_id, reporter_hash)
) WITHOUT ROWID;
-- feedback sobre o site: anônimo de verdade — sem hash de quem enviou, sem IP, data só com precisão de dia
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
"""
# votos, denúncias, turmas informadas e links usados não têm rowid (WITHOUT ROWID): ficam guardados na ordem da chave,
# não na ordem de chegada — senão a posição no arquivo diria em que ordem cada hash agiu num mesmo dia.
ROWLESS_TABLES = ("votes", "class_reports", "used_links", "reports")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Conexão por requisição: commit ao final (rollback em erro) e sempre fechada."""
    conn = sqlite3.connect(DB, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA secure_delete = ON")  # o que é apagado/reescrito é zerado no arquivo, não fica em páginas livres
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        legacy = conn.execute("SELECT sql FROM sqlite_master WHERE name='reviews'").fetchone()
        if legacy and "overall" not in legacy["sql"]:
            conn.execute("ALTER TABLE reviews RENAME TO reviews_v1")  # versão antiga preservada, não migrada
        conn.executescript(SCHEMA)
        # avaliações antigas, de antes da data por dia: arredonda também (idempotente)
        conn.execute(f"UPDATE reviews SET created_at = (created_at - {BRT}) / 86400 * 86400 + {BRT}, "
                     f"updated_at = CASE WHEN updated_at IS NULL THEN NULL ELSE (updated_at - {BRT}) / 86400 * 86400 + {BRT} END "
                     f"WHERE (created_at - {BRT}) % 86400 != 0 OR (updated_at IS NOT NULL AND (updated_at - {BRT}) % 86400 != 0)")
        # votos, denúncias e turmas informadas levam o MESMO hash de quem avalia: com hora exata, revelariam quando
        # aquela pessoa esteve no site. Também só o dia (idempotente).
        for table in ("votes", "reports", "class_reports"):
            conn.execute(f"UPDATE {table} SET created_at = (created_at - {BRT}) / 86400 * 86400 + {BRT} "
                         f"WHERE (created_at - {BRT}) % 86400 != 0")
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(reviews)")}
        # chave do comentário normalizado, indexada: a checagem de duplicata não varre mais a tabela
        if "comment_key" not in columns:
            conn.execute("ALTER TABLE reviews ADD COLUMN comment_key TEXT")
        # "dificuldade para passar", separada de "para aprender": vazia nas avaliações anteriores
        if "passing" not in columns:
            conn.execute("ALTER TABLE reviews ADD COLUMN passing INTEGER CHECK(passing BETWEEN 1 AND 5)")
        for row in conn.execute("SELECT id, comment FROM reviews WHERE comment_key IS NULL").fetchall():
            conn.execute("UPDATE reviews SET comment_key=? WHERE id=?", (comment_key(row["comment"]), row["id"]))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_comment_key ON reviews(comment_key)")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 3:
        harden_storage()
    migrate_aliases()


def harden_storage() -> None:
    """Uma vez por banco: tira do arquivo a ORDEM em que as coisas aconteceram, que a data só com o dia não esconde.
      - avaliações ganham ids aleatórios (ids sequenciais diziam quem avaliou antes de quem num mesmo dia);
      - votos, denúncias etc. passam a WITHOUT ROWID (o rowid também era sequencial);
      - VACUUM reescreve o arquivo: some o que restava em páginas livres (horários antigos, avaliações apagadas).
    Dali em diante, ids novos já nascem aleatórios e secure_delete zera o que for apagado."""
    conn = sqlite3.connect(DB, timeout=10, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")  # ids mudam em reviews e nos filhos juntos, dentro da transação
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("BEGIN IMMEDIATE")
        for table in ROWLESS_TABLES:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            if "WITHOUT ROWID" not in sql.upper():
                cols = ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({table})"))
                conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
                conn.execute(sql.rstrip().rstrip(";") + " WITHOUT ROWID")
                conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {table}_old")
                conn.execute(f"DROP TABLE {table}_old")
        ids = [r[0] for r in conn.execute("SELECT id FROM reviews")]
        taken: set[int] = set(ids)
        for old in ids:
            new = new_review_id(lambda i: i in taken)
            taken.add(new)
            conn.execute("UPDATE reviews SET id=? WHERE id=?", (new, old))
            for child in ("votes", "reports"):
                conn.execute(f"UPDATE {child} SET review_id=? WHERE review_id=?", (new, old))
        conn.execute("COMMIT")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA user_version = 3")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def new_review_id(taken) -> int:
    """Id aleatório (até 2^53, seguro como número em JavaScript) que ainda não exista."""
    while True:
        candidate = secrets.randbelow(2**53 - 1) + 1
        if not taken(candidate):
            return candidate


def comment_key(comment: str) -> str:
    return hashlib.sha256(" ".join(moderation.fold(comment).split()).encode()).hexdigest()


_migrated_version = -1


def migrate_aliases() -> None:
    """Avaliações feitas com o código antigo de uma disciplina passam para o código canônico.
    Se a mesma pessoa avaliou o mesmo docente nas duas versões, a mais antiga fica com o código antigo (não é apagada)."""
    global _migrated_version
    if _migrated_version == CATALOG.version:
        return
    _migrated_version = CATALOG.version
    if not CATALOG.alias:
        return
    with connect() as conn:
        for old, new in CATALOG.alias.items():
            conn.execute("UPDATE OR IGNORE reviews SET course=? WHERE course=?", (new, old))
            conn.execute("UPDATE OR IGNORE class_reports SET course=? WHERE course=?", (new, old))


REVIEW_COLUMNS = ["professor", "course", "term", *CRITERIA_KEYS, "would_retake", "grade", "tags", "comment", "comment_key"]


def review_out(row: sqlite3.Row, viewer: str | None = None, voted: set[int] | None = None, helpful: dict[int, int] | None = None) -> dict:
    out = {
        "id": row["id"], "professor": row["professor"], "course": row["course"], "term": row["term"],
        "ratings": {k: row[k] for k in CRITERIA_KEYS},
        "wouldRetake": None if row["would_retake"] is None else bool(row["would_retake"]),
        "grade": row["grade"], "tags": json.loads(row["tags"] or "[]"), "comment": row["comment"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "helpful": (helpful or {}).get(row["id"], 0),
        "verified": CATALOG.taught(row["professor"], row["course"], row["term"]),
    }
    if viewer is not None:
        out["mine"] = row["reviewer_hash"] == viewer
        out["voted"] = row["id"] in (voted or set())
        if out["mine"]:
            out["status"] = row["status"]
    return out


def helpful_counts(conn: sqlite3.Connection, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r[0]: r[1] for r in conn.execute(f"SELECT review_id, COUNT(*) FROM votes WHERE review_id IN ({marks}) GROUP BY review_id", ids)}


def mean_of(values: list) -> float | None:
    """Média dos valores respondidos (ignora vazios); None se ninguém respondeu."""
    given = [v for v in values if v is not None]
    return round(sum(given) / len(given), 2) if given else None


def aggregate(rows: list[sqlite3.Row]) -> dict:
    """Médias por critério, % que faria de novo, tags e distribuição de notas."""
    n = len(rows)
    if not n:
        return {"count": 0}
    avg = {k: mean_of([r[k] for r in rows]) for k in CRITERIA_KEYS}
    answered = {k: sum(r[k] is not None for r in rows) for k in OPTIONAL_CRITERIA}
    retake = [r["would_retake"] for r in rows if r["would_retake"] is not None]
    tags = Counter(t for r in rows for t in json.loads(r["tags"] or "[]"))
    grades = Counter(r["grade"] for r in rows if r["grade"] in GRADES[:6])
    dist = Counter(r["overall"] for r in rows)
    # evolução: médias por semestre CURSADO (o informado na avaliação), do mais antigo ao mais recente.
    # Não revela nada novo: o semestre de cada avaliação já aparece no próprio cartão.
    terms: dict[str, list] = defaultdict(list)
    for r in rows:
        terms[r["term"]].append(r)
    by_term = [{"term": t, "count": len(rs), **{k: mean_of([r[k] for r in rs]) for k in CRITERIA_KEYS}}
               for t, rs in sorted(terms.items())]
    return {
        "count": n, "avg": avg, "answered": answered, "byTerm": by_term,
        "retake": round(100 * sum(retake) / len(retake)) if retake else None,
        "tags": [{"tag": t, "count": c} for t, c in tags.most_common(8)],
        "grades": {g: grades.get(g, 0) for g in GRADES[:6]},
        "distribution": {str(i): dist.get(i, 0) for i in range(1, 6)},
    }


def reduced_summary(rows: list[sqlite3.Row]) -> dict:
    """aggregate() sem o que ajuda a identificar quem escreveu: evolução por semestre e conceitos."""
    out = aggregate(rows)
    out.pop("byTerm", None)
    out.pop("grades", None)
    return out


_docente_emails: tuple[str, dict[str, str]] = ("", {})


def docente_by_email() -> dict[str, str]:
    """E-mail publicado (minúsculo) → slug, a partir do catálogo; refeito quando o catálogo muda."""
    global _docente_emails
    if _docente_emails[0] != CATALOG.version:
        emails = {}
        for slug, p in CATALOG.professors.items():
            for e in [p.get("email"), *p.get("emails", [])]:
                if addr := email_login.normalize(e):
                    emails[addr] = slug
        _docente_emails = (CATALOG.version, emails)
    return _docente_emails[1]


def bayes(avg: float | None, n: int, prior: float, weight: int = 3) -> float | None:
    if not n or avg is None:
        return None
    return round((avg * n + prior * weight) / (n + weight), 3)


# --------------------------------------------------------------------------- limite de requisições
class RateLimiter:
    def __init__(self) -> None:
        self.events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self.lock:
            queue = self.events[(bucket, key)]
            while queue and queue[0] <= now - window:
                queue.popleft()
            if len(queue) >= limit:
                return False
            queue.append(now)
            if len(self.events) > 50_000:  # memória limitada
                for k in [k for k, q in self.events.items() if not q][:25_000]:
                    del self.events[k]
            return True


FEEDBACK_KINDS = ("sugestao", "problema", "dados", "outro")
FEEDBACK_MIN, FEEDBACK_MAX = 10, 2000

LIMITS = {"check": (120, 60), "access": (10, 600), "login": (5, 600), "classreport": (20, 600), "review": (6, 600), "vote": (60, 60), "report": (10, 600), "restore": (10, 600), "admin": (30, 60),
          "feedback": (5, 3600),
          # identidade anônima nova sai de graça (basta apagar o cookie), então os limites abaixo valem por rede (IP) e,
          # no acesso por e-mail, por login — nunca guardados: só em memória, como os demais
          "login-day": (20, 86400),       # códigos pedidos por IP por dia (protege a cota diária do serviço de e-mail)
          "review-pair": (3, 86400),      # avaliações do MESMO docente+disciplina por IP por dia (3, não 1: Wi-Fi do campus é NAT)
          "review-access": (1, 86400),    # idem, por login de e-mail
          "vote-pair": (3, 86400)}        # votos "útil" na MESMA avaliação por IP por dia
limiter = RateLimiter()
EMAIL_DAILY_LIMIT = int(env("EMAIL_DAILY_LIMIT") or 250)  # teto global de e-mails por dia (Gmail: ~500; Brevo grátis: 300)


def ip_key(ip: str) -> str:
    """IP só como HMAC truncado, para estruturas em memória que vivem mais que uma requisição."""
    return hmac.new(SECRET.encode(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:24]


class ReportNetworks:
    """Redes (IPs, como HMAC) distintas que denunciaram cada avaliação — só em memória. A retirada automática conta
    redes, não cookies: cookie anônimo novo sai de graça, então 3 cookies de um mesmo computador não tiram nada do ar.
    Num reinício a contagem zera, o que só deixa a retirada mais conservadora (as denúncias seguem no banco e na fila)."""

    def __init__(self) -> None:
        self.by_review: dict[int, set[str]] = defaultdict(set)
        self.lock = threading.Lock()

    def add(self, review_id: int, ip: str) -> int:
        with self.lock:
            if len(self.by_review) > 20_000:
                self.by_review.clear()
            nets = self.by_review[review_id]
            nets.add(ip_key(ip))
            return len(nets)

    def clear(self, review_id: int) -> None:
        with self.lock:
            self.by_review.pop(review_id, None)


report_networks = ReportNetworks()


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: object):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# --------------------------------------------------------------------------- HTTP
_gzip_cache: dict[str, bytes] = {}


def gzip_cached(etag: str, data: bytes) -> bytes:
    if etag not in _gzip_cache:
        if len(_gzip_cache) > 64:
            _gzip_cache.clear()
        _gzip_cache[etag] = gzip.compress(data, 9)
    return _gzip_cache[etag]


CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def gate_methods() -> list[str]:
    return (["email"] if EMAIL.enabled else []) + (["code"] if ACCESS_CODES else []) + (["docente"] if DOCENTE_LOGIN else [])


notifier = mod_notify.Notifier(EMAIL, MOD_EMAILS, lambda: connect(),
                               f"{PUBLIC_URL}/#/moderacao" if PUBLIC_URL else "o painel /#/moderacao do site")


class App(BaseHTTPRequestHandler):
    server_version = "DCCHub/2.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # conexão parada ou lenta demais (slowloris, keep-alive esquecido) é fechada e libera a thread

    # ---- utilidades
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # anonimato: nenhum log de acesso com IP

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")  # projeto da comunidade: fora de buscadores
        if self.secure_request():  # só atrás de HTTPS (proxy confiável): o navegador passa a recusar http:// no domínio
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def wants_gzip(self, size: int) -> bool:
        return size > 1400 and "gzip" in self.headers.get("Accept-Encoding", "")

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if self.wants_gzip(len(body)):
            body = gzip.compress(body, 6)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "new_cookie", None):
            self.send_header("Set-Cookie", self.new_cookie)
        if getattr(self, "access_cookie", None):
            self.send_header("Set-Cookie", self.access_cookie)
        self.security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def client_ip(self) -> str:
        # o ÚLTIMO item do X-Forwarded-For é o que o nosso proxy viu; os anteriores vêm do cliente e podem ser forjados
        # (o proxy do Fly acrescenta ao valor recebido; o Caddy o substitui — nos dois casos o último é o confiável)
        if TRUST_PROXY and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].rsplit(",", 1)[-1].strip() or self.client_address[0]
        return self.client_address[0]

    def secure_request(self) -> bool:
        return self.headers.get("X-Forwarded-Proto") == "https" if TRUST_PROXY else False

    def make_cookie(self, token: str) -> str:
        secure = "; Secure" if self.secure_request() else ""
        return f"{COOKIE}={token}; Path=/; Max-Age=63072000; HttpOnly; SameSite=Strict{secure}"

    def viewer(self, create: bool = False) -> str | None:
        """Hash do identificador anônimo do navegador (cria um novo cookie se pedido)."""
        if not hasattr(self, "_viewer"):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except Exception:  # noqa: BLE001
                pass
            token = cookies[COOKIE].value if COOKIE in cookies else ""
            self._viewer = token_hash(token) if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else None
        if self._viewer is None and create:
            token = secrets.token_urlsafe(32)
            self.new_cookie = self.make_cookie(token)
            self._viewer = token_hash(token)
        return self._viewer

    def read_body(self) -> bytes:
        """Lê (e descarta, se preciso) todo o corpo: com keep-alive, bytes não lidos corrompem a próxima requisição."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            raise ApiError(413 if length > MAX_BODY else 400, "Conteúdo grande demais." if length > MAX_BODY else "Requisição inválida.")
        return self.rfile.read(length) if length else b""

    def read_json(self) -> dict:
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise ApiError(415, "Envie JSON.")
        try:
            data = json.loads(self.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON inválido.") from None
        if not isinstance(data, dict):
            raise ApiError(400, "JSON inválido.")
        return data

    def check_origin(self) -> None:
        """Proteção CSRF: requisições de escrita precisam vir da própria origem."""
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise ApiError(403, "Origem não permitida.")

    def limit(self, bucket: str, key: str | None = None, message: str | None = None) -> None:
        """Limite por IP (ou pela chave dada). As chaves só existem em memória e somem com a janela."""
        count, window = LIMITS[bucket]
        if not limiter.hit(bucket, key or self.client_ip(), count, window):
            raise ApiError(429, message or "Muitas ações em pouco tempo. Aguarde alguns minutos e tente de novo.")

    def cookie(self, name: str) -> str:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001
            return ""
        return cookies[name].value if name in cookies else ""

    def email_login_key(self) -> str | None:
        """Identificador do login por e-mail deste navegador (o nonce do cookie de acesso), para limites em memória.
        Não diz quem é a pessoa nem vai para o banco: serve só para um mesmo login não avaliar a mesma turma em série."""
        given = self.cookie(ACCESS_COOKIE)
        if EMAIL.enabled and given.startswith("e1.") and email_login.check_access(SECRET, EMAIL.epoch, given):
            return given.split(".")[2]
        return None

    # ---- roteamento
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    ROUTES = [
        ("POST", r"/api/access", "post_access"),
        ("POST", r"/api/access/email", "post_access_email"),
        ("POST", r"/api/access/email/verify", "post_access_email_verify"),
        ("POST", r"/api/access/logout", "post_access_logout"),
        ("GET", r"/api/docente", "get_docente"),
        ("POST", r"/api/docente/reviews/(\d+)/report", "post_docente_report"),
        ("GET", r"/api/bootstrap", "get_bootstrap"),
        ("GET", r"/api/offerings", "get_offerings"),
        ("GET", r"/api/professors/([a-z0-9-]+)", "get_professor"),
        ("GET", r"/api/courses/([A-Za-z0-9-]+)", "get_course"),
        ("GET", r"/api/reviews", "get_reviews"),
        ("GET", r"/api/provas", "get_provas"),
        ("POST", r"/api/moderation/check", "post_moderation_check"),
        ("GET", r"/api/oportunidades", "get_oportunidades"),
        ("POST", r"/api/reviews", "post_review"),
        ("PUT", r"/api/reviews/(\d+)", "put_review"),
        ("DELETE", r"/api/reviews/(\d+)", "delete_review"),
        ("POST", r"/api/reviews/(\d+)/helpful", "post_helpful"),
        ("POST", r"/api/reviews/(\d+)/report", "post_report"),
        ("GET", r"/api/me", "get_me"),
        ("POST", r"/api/me/code", "post_me_code"),
        ("POST", r"/api/me/restore", "post_restore"),
        ("POST", r"/api/feedback", "post_feedback"),
        ("POST", r"/api/class-reports", "post_class_report"),
        ("DELETE", r"/api/class-reports", "delete_class_report"),
        ("GET", r"/api/admin/queue", "get_admin_queue"),
        ("GET", r"/api/admin/class-reports", "get_admin_class_reports"),
        ("POST", r"/api/admin/class-reports/delete", "post_admin_class_report_delete"),
        ("POST", r"/api/admin/reviews/(\d+)", "post_admin_review"),
        ("GET", r"/api/admin/feedback", "get_admin_feedback"),
        ("DELETE", r"/api/admin/feedback/(\d+)", "delete_admin_feedback"),
        # compatibilidade com o front-end antigo
        ("GET", r"/api/catalog", "get_bootstrap"),
    ]

    def dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        path = unquote(url.path)
        self.query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.body = b""
        if method not in ("GET", "HEAD"):
            try:
                self.body = self.read_body()
            except ApiError as exc:
                return self.send_json({"error": exc.message}, exc.status)
        CATALOG.refresh()
        EXAMS.refresh()
        OPPORTUNITIES.refresh()
        if CATALOG.version != _migrated_version:
            migrate_aliases()
        if not path.startswith("/api/"):
            if method in ("GET", "HEAD"):
                return self.serve_static(path)
            return self.send_json({"error": "Método não permitido."}, 405)
        if DOCENTE_LOGIN and path not in GATE_FREE and (slug := self.docente()):
            # docente: só a própria página reduzida; o resto da API não existe para ele
            if not any(re.fullmatch(r, path) for r in DOCENTE_ROUTES):
                return self.send_json({"error": "Acesso de docente: só a sua página está disponível.", "docente": slug}, 403)
        elif (ACCESS_CODES or EMAIL.enabled) and path not in GATE_FREE and not self.has_access():
            # nada de dados sem o código da comunidade ou o e-mail @ufmg.br; o front mostra a tela de acesso
            return self.send_json({"error": "Acesso restrito à comunidade.", "gate": True, "methods": gate_methods()}, 401)
        try:
            for route_method, pattern, handler in self.ROUTES:
                m = re.fullmatch(pattern, path)
                if m and route_method == method:
                    if method != "GET":
                        self.check_origin()
                    return getattr(self, handler)(*m.groups())
            raise ApiError(404, "Não encontrado.")
        except ApiError as exc:
            self.send_json({"error": exc.message, **exc.extra}, exc.status)
        except sqlite3.OperationalError:
            self.send_json({"error": "Servidor ocupado, tente de novo em instantes."}, 503)

    def serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if not target.is_relative_to(STATIC.resolve()) or not target.is_file():  # não um prefixo de texto: "static-x" passaria
            # SPA: rotas desconhecidas sem extensão caem no index
            if "." not in path.rsplit("/", 1)[-1]:
                target = STATIC / "index.html"
            else:
                body = b"Not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.security_headers()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
        data = target.read_bytes()
        etag = '"' + hashlib.sha1(data).hexdigest()[:16] + '"'
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml", "application/manifest+json"):
            ctype += "; charset=utf-8"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        compressible = ctype.startswith(("text/", "application/javascript", "application/json", "image/svg", "application/manifest"))
        if compressible and self.wants_gzip(len(data)):
            data = gzip_cached(etag, data)
            self.send_header("Content-Encoding", "gzip")
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        immutable = target.parent.name == "photos"
        self.send_header("Cache-Control", "public, max-age=604800" if immutable else "no-cache")
        self.security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # ---- leitura
    def all_stats(self, conn: sqlite3.Connection) -> tuple[dict, dict, dict]:
        rows = conn.execute(f"""SELECT professor, course, COUNT(*) n, {', '.join(f'AVG({k}) {k}, COUNT({k}) n_{k}' for k in CRITERIA_KEYS)},
                                       AVG(would_retake) retake, COUNT(would_retake) nretake
                                FROM reviews WHERE status='published' GROUP BY professor, course""").fetchall()
        total = sum(r["n"] for r in rows)
        prior = (sum(r["overall"] * r["n"] for r in rows) / total) if total else 3.5
        by_prof: dict[str, dict] = {}
        by_course: dict[str, dict] = {}
        for target, key in ((by_prof, "professor"), (by_course, "course")):
            acc: dict[str, dict] = defaultdict(lambda: {"n": 0, **{k: 0.0 for k in CRITERIA_KEYS}, **{f"n_{k}": 0 for k in CRITERIA_KEYS},
                                                        "retake": 0.0, "nretake": 0})
            for r in rows:
                a = acc[r[key]]
                a["n"] += r["n"]
                for k in CRITERIA_KEYS:
                    if r[f"n_{k}"]:
                        a[k] += r[k] * r[f"n_{k}"]
                        a[f"n_{k}"] += r[f"n_{k}"]
                if r["nretake"]:
                    a["retake"] += r["retake"] * r["nretake"]
                    a["nretake"] += r["nretake"]
            for k, a in acc.items():
                n = a["n"]
                target[k] = {"count": n, **{c: round(a[c] / a[f"n_{c}"], 2) if a[f"n_{c}"] else None for c in CRITERIA_KEYS},
                             "retake": round(100 * a["retake"] / a["nretake"]) if a["nretake"] else None,
                             "score": bayes(a["overall"] / n, n, prior)}
        pair = {(r["professor"], r["course"]): {"count": r["n"], **{c: None if r[c] is None else round(r[c], 2) for c in CRITERIA_KEYS},
                                                 "retake": round(100 * r["retake"]) if r["nretake"] else None} for r in rows}
        return by_prof, by_course, pair

    @staticmethod
    def reviewed_courses(conn: sqlite3.Connection) -> set[str]:
        return {r[0] for r in conn.execute("SELECT DISTINCT course FROM reviews WHERE status='published'")}

    @staticmethod
    def shown(cid: str, reviewed: set[str]) -> bool:
        """Disciplinas extintas (currículo antigo, sem oferta recente) não aparecem no site,
        a menos que já tenham avaliações publicadas — essas nunca somem."""
        return cid in CATALOG.active or cid in reviewed

    def get_bootstrap(self) -> None:
        with connect() as conn:
            by_prof, by_course, _ = self.all_stats(conn)
            totals = conn.execute("SELECT COUNT(*) n, COUNT(DISTINCT reviewer_hash) people FROM reviews WHERE status='published'").fetchone()
            pending = conn.execute("SELECT COUNT(*) FROM reviews WHERE status='held'").fetchone()[0]
            reviewed = self.reviewed_courses(conn)
        latest = CATALOG.latest_term
        current_profs = {s for o in CATALOG.offerings if o["term"] == latest for s in o["professors"]}
        current_courses = {o["course"] for o in CATALOG.offerings if o["term"] == latest}
        professors = []
        for slug, p in CATALOG.professors.items():
            professors.append({
                "slug": slug, "name": p["name"], "status": p["status"], "photo": p.get("photo"), "areas": p.get("areas", []),
                "courses": [c for c, _ in CATALOG.prof_courses[slug].most_common() if self.shown(c, reviewed)],
                "lastTerm": max(CATALOG.prof_terms[slug]) if CATALOG.prof_terms[slug] else None,
                "teachingNow": slug in current_profs, "stats": by_prof.get(slug),
            })
        courses = []
        for cid, c in CATALOG.courses.items():
            if not self.shown(cid, reviewed):
                continue
            courses.append({
                "id": cid, "codes": c["codes"], "name": c["name"], "kind": c["kind"], "terms": c["terms"],
                "professors": [s for s, _ in CATALOG.course_profs[cid].most_common()],
                "offeredNow": cid in current_courses, "active": cid in CATALOG.active, "aliases": c.get("aliases", []),
                "stats": by_course.get(cid),
            })
        self.send_json({
            "professors": professors, "courses": courses, "terms": CATALOG.terms, "latestTerm": latest,
            "currentTerm": current_term(), "offerPublished": latest == current_term(),
            "communityTerm": self.community_term(), "activeTerms": CATALOG.terms[-ACTIVE_TERMS:], "generatedAt": CATALOG.generated_at, "sources": CATALOG.sources, "termSources": CATALOG.term_sources,
            "criteria": CRITERIA, "tags": TAGS, "maxTags": MAX_TAGS, "grades": GRADES, "reportReasons": REPORT_REASONS,
            "comment": {"min": COMMENT_MIN, "max": COMMENT_MAX},
            "totals": {"reviews": totals["n"], "reviewers": totals["people"], "pending": pending},
            "moderation": bool(ADMIN_TOKEN),
            "emailAccess": EMAIL.enabled,
        })

    def get_offerings(self) -> None:
        term = self.query.get("term") or CATALOG.latest_term
        with connect() as conn:
            _, _, pair = self.all_stats(conn)
            reviewed = self.reviewed_courses(conn)
        out = []
        for o in CATALOG.offerings:
            if o["term"] != term or not self.shown(o["course"], reviewed):
                continue
            out.append({**o, "stats": {s: pair.get((s, o["course"])) for s in o["professors"]}})
        self.send_json({"term": term, "offerings": out})

    def reviews_where(self, conn: sqlite3.Connection, where: str, args: list, sort: str = "helpful", limit: int = 500, offset: int = 0) -> list[dict]:
        order = {"recent": "r.created_at DESC, r.id DESC", "helpful": "helpful DESC, r.created_at DESC, r.id DESC",
                 "high": "r.overall DESC, r.created_at DESC, r.id DESC", "low": "r.overall ASC, r.created_at DESC, r.id DESC"}.get(sort, "helpful DESC, r.created_at DESC, r.id DESC")
        rows = conn.execute(f"""SELECT r.*, (SELECT COUNT(*) FROM votes v WHERE v.review_id=r.id) helpful
                                FROM reviews r WHERE r.status='published' AND {where} ORDER BY {order} LIMIT ? OFFSET ?""",
                            [*args, limit, offset]).fetchall()
        viewer = self.viewer()
        voted = set()
        if viewer and rows:
            marks = ",".join("?" * len(rows))
            voted = {r[0] for r in conn.execute(f"SELECT review_id FROM votes WHERE voter_hash=? AND review_id IN ({marks})", [viewer, *[r["id"] for r in rows]])}
        return [review_out(r, viewer or "", voted, {r["id"]: r["helpful"] for r in rows}) for r in rows]

    def get_professor(self, slug: str) -> None:
        prof = CATALOG.professors.get(slug)
        if not prof:
            raise ApiError(404, "Docente não encontrado.")
        with connect() as conn:
            rows = conn.execute("SELECT * FROM reviews WHERE professor=? AND status='published'", (slug,)).fetchall()
            _, _, pair = self.all_stats(conn)
            reviews = self.reviews_where(conn, "r.professor=?", [slug], self.query.get("sort", "helpful"))
            reviewed = self.reviewed_courses(conn)
        course_ids = {c for c in set(CATALOG.prof_courses[slug]) | {r["course"] for r in rows} if self.shown(c, reviewed)}
        courses = []
        for cid in course_ids:
            c = CATALOG.courses.get(cid)
            if not c:
                continue
            terms = sorted(set(CATALOG.pairs.get((slug, cid), [])))
            courses.append({"id": cid, "name": c["name"], "codes": c["codes"], "kind": c["kind"], "terms": terms,
                            "sections": CATALOG.prof_courses[slug][cid], "stats": pair.get((slug, cid))})
        courses.sort(key=lambda c: (c["terms"][-1] if c["terms"] else "", c["sections"]), reverse=True)
        history = [{"term": o["term"], "course": o["course"], "schedule": o["schedule"], "class": o.get("class"), "plan": o.get("plan"),
                    "with": [s for s in o["professors"] if s != slug], "codes": o["codes"]}
                   for o in CATALOG.offerings if slug in o["professors"] and self.shown(o["course"], reviewed)]
        for c in courses:
            c["active"] = c["id"] in CATALOG.active
        self.send_json({"professor": prof, "summary": aggregate(rows), "courses": courses, "history": history, "reviews": reviews,
                        "termsTaught": sorted(CATALOG.prof_terms[slug]),
                        "opportunities": OPPORTUNITIES.for_professor(slug),  # todos os semestres, inclusive em disciplinas extintas
                        "community": self.community_reports(professor=slug)})

    def get_course(self, cid: str) -> None:
        cid = CATALOG.canonical(cid)
        course = CATALOG.courses.get(cid)
        if not course:
            raise ApiError(404, "Disciplina não encontrada.")
        with connect() as conn:
            rows = conn.execute("SELECT * FROM reviews WHERE course=? AND status='published'", (cid,)).fetchall()
            _, _, pair = self.all_stats(conn)
            reviews = self.reviews_where(conn, "r.course=?", [cid], self.query.get("sort", "helpful"))
        if not rows and cid not in CATALOG.active:
            successor = course.get("successor")
            raise ApiError(404, f"{course['name']} é de um currículo antigo e não é mais ofertada pelo DCC"
                           + (f"; o conteúdo hoje está em {CATALOG.courses[successor]['name']}." if successor in CATALOG.courses else "."),
                           successor=successor if successor in CATALOG.courses else None)
        prof_ids = set(CATALOG.course_profs[cid]) | {r["professor"] for r in rows}
        professors = []
        for slug in prof_ids:
            p = CATALOG.professors.get(slug)
            if not p:
                continue
            terms = sorted(set(CATALOG.pairs.get((slug, cid), [])))
            professors.append({"slug": slug, "name": p["name"], "photo": p.get("photo"), "status": p["status"], "terms": terms,
                               "sections": CATALOG.course_profs[cid][slug], "stats": pair.get((slug, cid))})
        professors.sort(key=lambda p: (p["terms"][-1] if p["terms"] else "", p["sections"]), reverse=True)
        history = [{"term": o["term"], "professors": o["professors"], "schedule": o["schedule"], "class": o.get("class"), "plan": o.get("plan"),
                    "codes": o["codes"], "unresolved": o.get("unresolved", [])}
                   for o in CATALOG.offerings if o["course"] == cid]
        self.send_json({"course": {**course, "active": cid in CATALOG.active}, "summary": aggregate(rows), "professors": professors,
                        "history": history, "reviews": reviews, "community": self.community_reports(course=cid),
                        "exams": EXAMS.for_course(cid)})

    def get_provas(self) -> None:
        if not EXAMS.data:
            raise ApiError(404, "O acervo de provas ainda não foi indexado (rode scripts/sync_provas.py).")
        data = EXAMS.data
        sections = [{**sec, "courses": [{**e, "course": CATALOG.canonical(e["course"]) if e.get("course") in CATALOG.courses
                                          or e.get("course") in CATALOG.alias else None} for e in sec["courses"]]}
                    for sec in data["sections"]]
        self.send_json({**data, "sections": sections})

    def get_oportunidades(self) -> None:
        if not OPPORTUNITIES.data:
            raise ApiError(404, "As oportunidades ainda não foram coletadas (rode scripts/sync_oportunidades.py).")
        self.send_json(OPPORTUNITIES.data)

    def get_reviews(self) -> None:
        where, args = ["1=1"], []
        for field in ("professor", "course"):
            if self.query.get(field):
                where.append(f"r.{field}=?")
                args.append(CATALOG.canonical(self.query[field]) if field == "course" else self.query[field])
        try:
            limit = max(1, min(int(self.query.get("limit", 20)), 100))
            offset = max(0, int(self.query.get("offset", 0)))
        except ValueError:
            raise ApiError(400, "Parâmetros inválidos.") from None
        with connect() as conn:
            reviews = self.reviews_where(conn, " AND ".join(where), args, self.query.get("sort", "recent"), limit, offset)
        self.send_json({"reviews": reviews})

    # ---- escrita
    def validate_review(self, data: dict, keep_course: str | None = None) -> dict:
        if data.get("website"):  # campo-armadilha
            raise ApiError(400, "Não foi possível enviar esta avaliação.")
        professor = clean_line(data.get("professor"), 120)
        course = CATALOG.canonical(clean_line(data.get("course"), 120))
        term = clean_line(data.get("term"), 10)
        errors: dict[str, str] = {}
        if professor not in CATALOG.professors:
            errors["professor"] = "Escolha um docente da lista."
        if course not in CATALOG.courses:
            errors["course"] = "Escolha uma disciplina da lista."
        elif course not in CATALOG.active and course != keep_course:  # editar avaliação antiga continua possível
            errors["course"] = f"Esta disciplina não é ofertada desde {CATALOG.courses[course]['terms'][-1]} (currículo antigo)."
        if not valid_term(term):
            errors["term"] = f"Use o formato 2025.2 (entre 2010.1 e {current_term()})."
        ratings = {}
        for key in CRITERIA_KEYS:
            try:
                value = int(data.get("ratings", {}).get(key) if isinstance(data.get("ratings"), dict) else data.get(key))
            except (TypeError, ValueError):
                value = 0
            if not 1 <= value <= 5:
                errors[key] = "Dê uma nota de 1 a 5."
            ratings[key] = value
        retake = data.get("wouldRetake")
        if retake not in (None, True, False):
            errors["wouldRetake"] = "Resposta inválida."
        grade = data.get("grade") or None
        if grade is not None and grade not in GRADES:
            errors["grade"] = "Conceito inválido."
        tags = data.get("tags") or []
        if not isinstance(tags, list) or any(t not in ALL_TAGS for t in tags) or len(set(tags)) > MAX_TAGS:
            errors["tags"] = f"Escolha até {MAX_TAGS} marcadores da lista."
            tags = []
        comment = clean_comment(data.get("comment"))
        if len(comment) < COMMENT_MIN:
            errors["comment"] = f"Escreva pelo menos {COMMENT_MIN} caracteres — detalhes ajudam quem vai escolher a turma."
        elif len(comment) > COMMENT_MAX:
            errors["comment"] = f"Use no máximo {COMMENT_MAX} caracteres."
        issues = []
        if "comment" not in errors:
            issues = moderation.analyze(comment, CATALOG.professors.get(professor, {}).get("name"))
            rejected = [i for i in issues if i.action == moderation.REJECT]
            if rejected:
                errors["comment"] = " ".join(dict.fromkeys(i.message for i in rejected))
        if errors:
            raise ApiError(400, "Revise os campos destacados.", fields=errors,
                           moderation=[{"category": i.category, "message": i.message, "evidence": i.evidence} for i in issues])
        return {"professor": professor, "course": course, "term": term, **ratings,
                "would_retake": None if retake is None else int(retake), "grade": grade,
                "tags": json.dumps(list(dict.fromkeys(tags)), ensure_ascii=False), "comment": comment,
                "comment_key": comment_key(comment), "_hold": [i.message for i in issues if i.action == moderation.HOLD]}

    def post_review(self) -> None:
        data = self.read_json()
        review = self.validate_review(data)
        self.limit("review")
        viewer = self.viewer(create=True)
        flags = review.pop("_hold")
        status = "held" if flags else "published"
        self.reject_duplicate(review["comment_key"], viewer)
        with connect() as conn:  # quem já avaliou recebe o 409 (e o link para editar) sem gastar o limite da turma
            already = conn.execute("SELECT 1 FROM reviews WHERE reviewer_hash=? AND professor=? AND course=?",
                                   (viewer, review["professor"], review["course"])).fetchone()
        if not already:
            self.limit_same_class(review["professor"], review["course"])
        try:
            with connect() as conn:
                # id aleatório, não sequencial: a ordem dos ids diria quem avaliou antes de quem num mesmo dia
                review_id = new_review_id(lambda i: conn.execute("SELECT 1 FROM reviews WHERE id=?", (i,)).fetchone() is not None)
                conn.execute(
                    f"INSERT INTO reviews (id, {', '.join(REVIEW_COLUMNS)}, status, held_reason, reviewer_hash, created_at) "
                    f"VALUES ({', '.join('?' * (len(REVIEW_COLUMNS) + 5))})",
                    [review_id, *(review[c] for c in REVIEW_COLUMNS), status, "; ".join(flags) or None, viewer, review_day()])
            if status == "held":
                notifier.held()
        except sqlite3.IntegrityError:
            with connect() as conn:
                existing = conn.execute("SELECT id FROM reviews WHERE reviewer_hash=? AND professor=? AND course=?",
                                        (viewer, review["professor"], review["course"])).fetchone()
            raise ApiError(409, "Você já avaliou esta disciplina com este docente. Você pode editar a avaliação existente.",
                           reviewId=existing["id"] if existing else None) from None
        message = ("Avaliação publicada. Obrigado por ajudar a comunidade!" if status == "published"
                   else "Recebemos sua avaliação. Por citar uma possível discriminação, uma pessoa da moderação vai ler antes de publicar.")
        self.send_json({"id": review_id, "status": status, "message": message, "held": status == "held"}, 201)

    @staticmethod
    def reject_duplicate(key: str, viewer: str, exclude_id: int | None = None) -> None:
        """Comentário idêntico a outra avaliação (copiar-colar em massa, spam): devolve para reescrever.
        Compara a chave do texto normalizado (comment_key, indexada) — uma busca, não uma varredura da tabela."""
        with connect() as conn:
            # só de OUTRAS pessoas: reenviar a própria avaliação cai no aviso de "você já avaliou" (409)
            if conn.execute("SELECT 1 FROM reviews WHERE comment_key=? AND status!='removed' AND reviewer_hash!=? AND id!=? LIMIT 1",
                            (key, viewer, exclude_id or 0)).fetchone():
                raise ApiError(400, "Revise os campos destacados.",
                               fields={"comment": "Este comentário é idêntico a outra avaliação. Escreva com as suas palavras."})

    def limit_same_class(self, professor: str, course: str) -> None:
        """Contra avaliações em série da mesma turma: cada cookie anônimo novo poderia avaliar de novo o mesmo docente.
        Limita por rede (IP) e por login de e-mail, só em memória — nada disso liga a avaliação a quem a escreveu."""
        msg = "Já recebemos avaliações desta turma a partir desta conexão hoje. Se você ainda não avaliou, tente amanhã."
        self.limit("review-pair", f"{self.client_ip()}|{professor}|{course}", msg)
        login = self.email_login_key()
        if login:
            self.limit("review-access", f"{login}|{professor}|{course}", msg)

    def post_moderation_check(self) -> None:
        """Checagem enquanto a pessoa digita: diz o que seria barrado/segurado, sem guardar nada."""
        self.limit("check")
        data = self.read_json()
        comment = clean_comment(data.get("comment"))[:COMMENT_MAX]
        name = CATALOG.professors.get(clean_line(data.get("professor"), 120), {}).get("name")
        issues = moderation.analyze(comment, name)
        self.send_json({"decision": moderation.decide(issues),
                        "issues": [{"category": i.category, "message": i.message, "evidence": i.evidence, "action": i.action} for i in issues]})

    def own_review(self, conn: sqlite3.Connection, review_id: str) -> sqlite3.Row:
        viewer = self.viewer()
        row = conn.execute("SELECT * FROM reviews WHERE id=? AND status!='removed'", (int(review_id),)).fetchone()
        if not row or not viewer or not hmac.compare_digest(row["reviewer_hash"], viewer):
            raise ApiError(404, "Avaliação não encontrada neste navegador.")
        return row

    def put_review(self, review_id: str) -> None:
        data = self.read_json()
        with connect() as conn:
            row = self.own_review(conn, review_id)
        review = self.validate_review(data, keep_course=row["course"])
        self.limit("review")
        flags = review.pop("_hold")
        self.reject_duplicate(review["comment_key"], row["reviewer_hash"], exclude_id=row["id"])
        with connect() as conn:
            row = self.own_review(conn, review_id)
            if (row["professor"], row["course"]) != (review["professor"], review["course"]):
                clash = conn.execute("SELECT 1 FROM reviews WHERE reviewer_hash=? AND professor=? AND course=? AND id!=?",
                                     (row["reviewer_hash"], review["professor"], review["course"], row["id"])).fetchone()
                if clash:
                    raise ApiError(409, "Você já tem outra avaliação para esta combinação de docente e disciplina.")
            status = "held" if flags else "published"
            conn.execute(f"UPDATE reviews SET {', '.join(f'{c}=?' for c in REVIEW_COLUMNS)}, status=?, held_reason=?, updated_at=? WHERE id=?",
                         [*(review[c] for c in REVIEW_COLUMNS), status, "; ".join(flags) or None, review_day(), row["id"]])
        if status == "held":
            notifier.held()
        self.send_json({"id": row["id"], "status": status, "held": status == "held",
                        "message": "Avaliação atualizada." if status == "published" else "Avaliação atualizada e enviada para revisão."})

    def delete_review(self, review_id: str) -> None:
        with connect() as conn:
            row = self.own_review(conn, review_id)
            conn.execute("DELETE FROM reviews WHERE id=?", (row["id"],))
        self.send_json({"message": "Avaliação apagada."})

    def post_helpful(self, review_id: str) -> None:
        self.limit("vote")
        viewer = self.viewer(create=True)
        with connect() as conn:
            row = conn.execute("SELECT reviewer_hash FROM reviews WHERE id=? AND status='published'", (int(review_id),)).fetchone()
            if not row:
                raise ApiError(404, "Avaliação não encontrada.")
            if row["reviewer_hash"] == viewer:
                raise ApiError(400, "Você não pode votar na sua própria avaliação.")
            removed = conn.execute("DELETE FROM votes WHERE review_id=? AND voter_hash=?", (int(review_id), viewer)).rowcount
            if not removed:
                # tirar o voto é sempre livre; dar voto na mesma avaliação tem teto por rede (cookie novo sai de graça)
                self.limit("vote-pair", f"{self.client_ip()}|{int(review_id)}", "Esta avaliação já recebeu votos desta conexão hoje.")
                conn.execute("INSERT INTO votes (review_id, voter_hash, created_at) VALUES (?, ?, ?)", (int(review_id), viewer, review_day()))
            count = conn.execute("SELECT COUNT(*) FROM votes WHERE review_id=?", (int(review_id),)).fetchone()[0]
        self.send_json({"voted": not removed, "helpful": count})

    def post_report(self, review_id: str) -> None:
        data = self.read_json()
        reason = clean_line(data.get("reason"), 60)
        if reason not in REPORT_REASONS:
            raise ApiError(400, "Escolha um motivo.")
        self.limit("report")
        viewer = self.viewer(create=True)
        with connect() as conn:
            row = conn.execute("SELECT id FROM reviews WHERE id=? AND status='published'", (int(review_id),)).fetchone()
            if not row:
                raise ApiError(404, "Avaliação não encontrada.")
            conn.execute("INSERT OR IGNORE INTO reports (review_id, reporter_hash, reason, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                         (row["id"], viewer, reason, clean_line(data.get("detail"), 300) or None, review_day()))
            # toda denúncia vai para a fila; tirar do ar sozinho exige redes distintas, não cookies (que saem de graça):
            # senão qualquer um apagaria críticas com 3 janelas anônimas
            networks = report_networks.add(row["id"], self.client_ip())
            if networks >= AUTO_HIDE_REPORTS:
                conn.execute("UPDATE reviews SET status='held', held_reason=? WHERE id=?", (f"denúncias de {networks} redes diferentes", row["id"]))
                notifier.held()
        self.send_json({"message": "Obrigado. A denúncia foi registrada e será analisada."})

    # ---- identidade anônima
    def get_me(self) -> None:
        viewer = self.viewer()
        if not viewer:
            return self.send_json({"reviews": [], "hasIdentity": False})
        with connect() as conn:
            rows = conn.execute("SELECT * FROM reviews WHERE reviewer_hash=? AND status!='removed' ORDER BY created_at DESC, id DESC", (viewer,)).fetchall()
            helpful = helpful_counts(conn, [r["id"] for r in rows])
        self.send_json({"reviews": [review_out(r, viewer, set(), helpful) for r in rows], "hasIdentity": True})

    def post_me_code(self) -> None:
        """O código de recuperação É a identidade anônima (o cookie HttpOnly): mostrá-lo à página anula o HttpOnly.
        Por isso só sai por POST JSON da própria origem (passa por check_origin; um formulário de outro site não envia
        JSON), com limite e sem cache — e a CSP sem scripts inline/externos é o que impede uma página injetada de pedi-lo."""
        self.read_json()
        self.limit("restore")
        token = self.cookie(COOKIE)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ApiError(404, "Este navegador ainda não tem um identificador anônimo.")
        self.send_json({"code": token})

    def post_restore(self) -> None:
        self.limit("restore")
        code = clean_line(self.read_json().get("code"), 60)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
            raise ApiError(400, "Código inválido.")
        with connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM reviews WHERE reviewer_hash=? AND status!='removed'", (token_hash(code),)).fetchone()[0]
        if not n:
            raise ApiError(404, "Nenhuma avaliação encontrada para este código.")
        self.new_cookie = self.make_cookie(code)
        self.send_json({"message": f"Pronto! {n} avaliação(ões) vinculada(s) a este navegador.", "count": n})

    # ---- moderação
    # ---- turmas informadas pela comunidade (enquanto a oferta oficial do semestre não sai)
    @staticmethod
    def community_term() -> str | None:
        """Semestre corrente, se o DCC ainda não publicou a oferta dele; senão None (a oferta oficial vale)."""
        term = current_term()
        return term if term not in CATALOG.terms else None

    def community_reports(self, professor: str | None = None, course: str | None = None) -> dict | None:
        term = self.community_term()
        if not term:
            return None
        field, value = ("professor", professor) if professor else ("course", course)
        viewer = self.viewer()
        with connect() as conn:
            rows = conn.execute(f"""SELECT professor, course, COUNT(*) n, MAX(reporter_hash=?) mine FROM class_reports
                                    WHERE term=? AND {field}=? GROUP BY professor, course ORDER BY n DESC""",
                                (viewer or "", term, value)).fetchall()
        items = [{"professor": r["professor"], "course": r["course"], "count": r["n"], "mine": bool(r["mine"])}
                 for r in rows if r["professor"] in CATALOG.professors and r["course"] in CATALOG.courses]
        return {"term": term, "items": items}

    def class_report_input(self) -> tuple[str, str, str]:
        term = self.community_term()
        if not term:
            raise ApiError(409, "A oferta oficial deste semestre já foi publicada; ela é a fonte usada pelo site.")
        data = self.read_json()
        professor = clean_line(data.get("professor"), 120)
        course = CATALOG.canonical(clean_line(data.get("course"), 120))
        if CATALOG.professors.get(professor, {}).get("status") not in ("ativo", "voluntario", "externo", "substituto"):
            raise ApiError(400, "Escolha um docente em atividade.", fields={"professor": "Escolha um docente em atividade."})
        if course not in CATALOG.courses:
            raise ApiError(400, "Escolha uma disciplina da lista.", fields={"course": "Escolha uma disciplina da lista."})
        return term, professor, course

    def post_class_report(self) -> None:
        term, professor, course = self.class_report_input()
        self.limit("classreport")
        viewer = self.viewer(create=True)
        with connect() as conn:
            conn.execute("INSERT OR IGNORE INTO class_reports (term, professor, course, reporter_hash, created_at) VALUES (?,?,?,?,?)",
                         (term, professor, course, viewer, review_day()))
            n = conn.execute("SELECT COUNT(*) FROM class_reports WHERE term=? AND professor=? AND course=?", (term, professor, course)).fetchone()[0]
        self.send_json({"term": term, "count": n, "message": "Obrigado! A turma aparece como informada por estudantes até sair a oferta oficial."}, 201)

    def delete_class_report(self) -> None:
        term, professor, course = self.class_report_input()
        viewer = self.viewer()
        if viewer:
            with connect() as conn:
                conn.execute("DELETE FROM class_reports WHERE term=? AND professor=? AND course=? AND reporter_hash=?",
                             (term, professor, course, viewer))
        self.send_json({"message": "Removido."})

    def get_admin_class_reports(self) -> None:
        self.require_admin()
        with connect() as conn:
            rows = conn.execute("""SELECT term, professor, course, COUNT(*) n, MAX(created_at) last FROM class_reports
                                   GROUP BY term, professor, course ORDER BY term DESC, n DESC""").fetchall()
        self.send_json({"items": [{"term": r["term"], "professor": r["professor"], "course": r["course"], "count": r["n"], "last": r["last"]} for r in rows]})

    def post_admin_class_report_delete(self) -> None:
        self.require_admin()
        data = self.read_json()
        with connect() as conn:
            n = conn.execute("DELETE FROM class_reports WHERE term=? AND professor=? AND course=?",
                             (str(data.get("term")), str(data.get("professor")), str(data.get("course")))).rowcount
        self.send_json({"message": f"{n} relato(s) removido(s)."})

    # ---- feedback sobre o site
    def post_feedback(self) -> None:
        """Sugestões e problemas. Não lê nem cria o cookie anônimo e não guarda IP: nada liga o feedback a quem enviou
        (nem às avaliações dessa pessoa). O IP só limita envios, em memória, como no resto do site."""
        data = self.read_json()
        if data.get("website"):  # campo-armadilha
            raise ApiError(400, "Não foi possível enviar.")
        kind = data.get("kind") if data.get("kind") in FEEDBACK_KINDS else "outro"
        message = clean_comment(data.get("message"))
        if len(message) < FEEDBACK_MIN:
            raise ApiError(400, "Escreva um pouco mais.", fields={"message": f"Escreva pelo menos {FEEDBACK_MIN} caracteres."})
        if len(message) > FEEDBACK_MAX:
            raise ApiError(400, "Texto longo demais.", fields={"message": f"Use no máximo {FEEDBACK_MAX} caracteres."})
        self.limit("feedback", message="Você já mandou vários feedbacks agora há pouco. Tente de novo mais tarde.")
        with connect() as conn:
            conn.execute("INSERT INTO feedback (kind, message, created_at) VALUES (?,?,?)", (kind, message, review_day()))
        notifier.held()
        self.send_json({"message": "Obrigado! Seu feedback chegou à equipe."}, 201)

    def get_admin_feedback(self) -> None:
        self.require_admin()
        with connect() as conn:
            rows = conn.execute("SELECT id, kind, message, created_at FROM feedback ORDER BY id DESC LIMIT 200").fetchall()
        self.send_json({"items": [{"id": r["id"], "kind": r["kind"], "message": r["message"], "day": r["created_at"]} for r in rows]})

    def delete_admin_feedback(self, fid: str) -> None:
        self.require_admin()
        with connect() as conn:
            if not conn.execute("DELETE FROM feedback WHERE id=?", (int(fid),)).rowcount:
                raise ApiError(404, "Feedback não encontrado.")
        self.send_json({"message": "Apagado."})

    # ---- acesso da comunidade
    @staticmethod
    def access_token(code: str) -> str:
        return hmac.new(SECRET.encode(), f"acesso:{code}".encode(), hashlib.sha256).hexdigest()[:40]

    def has_access(self) -> bool:
        given = self.cookie(ACCESS_COOKIE)
        if not given:
            return False
        # entrada por e-mail: token assinado e com validade, sem nada do e-mail (DCCHUB_EMAIL_EPOCH revoga todos)
        if EMAIL.enabled and given.startswith("e1."):
            return email_login.check_access(SECRET, EMAIL.epoch, given)
        # entrada por código: o cookie guarda um HMAC do código; trocar DCCHUB_ACCESS_CODES revoga quem entrou com ele
        return any(hmac.compare_digest(given, self.access_token(c)) for c in ACCESS_CODES)

    def set_access_cookie(self, value: str, max_age: int) -> None:
        secure = "; Secure" if self.secure_request() else ""
        self.access_cookie = f"{ACCESS_COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"

    def post_access(self) -> None:
        if not ACCESS_CODES:
            if EMAIL.enabled:
                raise ApiError(403, "O acesso é pelo e-mail @ufmg.br.")
            return self.send_json({"ok": True})
        self.limit("access")
        code = clean_line(self.read_json().get("code"), 120)
        match = next((c for c in ACCESS_CODES if hmac.compare_digest(code.lower().encode(), c.lower().encode())), None)
        if not match:
            raise ApiError(403, "Código inválido. Peça o código atual nos canais dos estudantes.")
        self.set_access_cookie(self.access_token(match), 15552000)
        self.send_json({"ok": True})

    def post_access_email(self) -> None:
        """Envia o código de acesso e devolve o desafio. O e-mail só passa pela memória: não vai para banco nem log."""
        if not EMAIL.enabled:
            raise ApiError(404, "Acesso por e-mail desativado.")
        self.limit("login")
        address = email_login.normalize(self.read_json().get("email"))
        if not address:
            raise ApiError(400, "Digite um e-mail válido.", fields={"email": ["Digite um e-mail válido."]})
        docente = docente_by_email().get(address) if DOCENTE_LOGIN else None
        if not docente and not email_login.allowed(address, EMAIL.domain):
            msg = f"Use seu e-mail @{EMAIL.domain}."
            raise ApiError(400, msg, fields={"email": [msg]})
        self.limit("login-day", message="Muitos pedidos de código a partir desta conexão hoje. Tente amanhã.")
        # e-mail publicado de um docente (mesmo que seja @ufmg.br): o código abre só a página dele
        code, challenge = email_login.new_docente_code(SECRET, docente) if docente else email_login.new_code(SECRET)
        # no máximo 3 e-mails por endereço por hora (chave HMAC só em memória). Acima disso a resposta é a mesma, com
        # um desafio de verdade, mas nada é enviado: ninguém usa o site para encher a caixa de outra pessoa.
        if limiter.hit("login-addr", email_login.address_key(SECRET, address), 3, 3600):
            # teto global por dia: sem ele, poucas conexões gastariam a cota do serviço de envio e ninguém mais entraria
            if not limiter.hit("login-global", "*", EMAIL_DAILY_LIMIT, 86400):
                raise ApiError(503, "O limite diário de envios de e-mail foi atingido. Tente de novo amanhã"
                               + (" ou entre com o código da comunidade." if ACCESS_CODES else "."))
            if docente:
                email_login.send_async(EMAIL, address, code, email_login.DOCENTE_TTL // 86400)
            else:
                email_login.send_async(EMAIL, address, code)
        self.send_json({"ok": True, "challenge": challenge, "expiresIn": email_login.CODE_TTL,
                        "message": f"Enviamos um código de 6 dígitos para {address}. Ele vale por "
                                   f"{email_login.CODE_TTL // 60} minutos."})

    def post_access_email_verify(self) -> None:
        if not EMAIL.enabled:
            raise ApiError(404, "Acesso por e-mail desativado.")
        self.limit("access")
        body = self.read_json()
        # os últimos desafios pedidos neste navegador (e-mail lento: vale o código de qualquer um deles)
        given = body.get("challenges")
        given = given if isinstance(given, list) else [body.get("challenge")]
        challenges = [(c, n) for c in dict.fromkeys(x for x in given[:email_login.CODE_PENDING] if isinstance(x, str))
                      if (n := email_login.challenge_nonce(c))]
        if not challenges:
            raise ApiError(400, "Código expirado. Peça um novo.")
        code = body.get("code")
        if not email_login.clean_code(code):
            raise ApiError(400, "Digite os 6 dígitos do código.", fields={"code": ["Digite os 6 dígitos do código."]})
        # tentativas por desafio, só em memória: 5 chutes em 1.000.000 por e-mail enviado
        nonce, slug, tried = None, None, False
        for challenge, candidate in challenges:
            if not limiter.hit("login-try", candidate, email_login.CODE_TRIES, email_login.CODE_TTL):
                continue
            tried = True
            if ok := email_login.check_any_code(SECRET, challenge, code):
                nonce, slug = ok
                break
        if slug and (not DOCENTE_LOGIN or slug not in CATALOG.professors):
            nonce = None
        if not tried:
            raise ApiError(429, "Tentativas demais com este código. Peça um novo.")
        if not nonce:
            raise ApiError(400, "Código incorreto ou expirado. Confira os e-mails recebidos.",
                           fields={"code": ["Código incorreto ou expirado."]})
        now = int(time.time())
        with connect() as conn:
            conn.execute("DELETE FROM used_links WHERE expires_at < ?", (now,))
            try:  # uso único
                conn.execute("INSERT INTO used_links (nonce, expires_at) VALUES (?, ?)", (nonce, now + email_login.CODE_TTL))
            except sqlite3.IntegrityError:
                raise ApiError(400, "Este código já foi usado. Peça um novo.") from None
        if slug:
            self.set_access_cookie(email_login.docente_token(SECRET, EMAIL.epoch, slug), email_login.DOCENTE_TTL)
            return self.send_json({"ok": True, "docente": True})
        self.set_access_cookie(email_login.access_token(SECRET, EMAIL.epoch), email_login.ACCESS_TTL)
        self.send_json({"ok": True})

    def post_access_logout(self) -> None:
        self.set_access_cookie("", 0)
        self.send_json({"ok": True})

    # ---- modo docente
    def docente(self) -> str | None:
        """Slug do docente, se este navegador entrou pelo modo docente."""
        given = self.cookie(ACCESS_COOKIE)
        if not given.startswith("d1."):
            return None
        slug = email_login.check_docente(SECRET, EMAIL.epoch, given)
        return slug if slug in CATALOG.professors else None

    def get_docente(self) -> None:
        """A página do docente, reduzida para não ajudar a descobrir quem escreveu:
        - cada avaliação aparece sem semestre, conceito, data, edição e votos — quem dá aula sabe quem cursou quando e
          com que nota, e esses campos cruzados com a turma apontariam a pessoa;
        - disciplinas com menos de DOCENTE_MIN avaliações não mostram notas nem textos, só que existem;
        - sem evolução por semestre e sem distribuição de conceitos;
        - dentro de cada disciplina, ordem pelo id (aleatório), não por data."""
        slug = self.docente()
        if not slug:
            raise ApiError(401, "Acesso de docente expirado. Entre de novo com seu e-mail.", gate=True, methods=gate_methods())
        prof = CATALOG.professors[slug]
        with connect() as conn:
            rows = conn.execute("SELECT * FROM reviews WHERE professor=? AND status='published' ORDER BY course, id",
                                (slug,)).fetchall()
        by_course: dict[str, list] = defaultdict(list)
        for r in rows:
            by_course[r["course"]].append(r)
        courses, shown_rows = [], []
        for cid, rs in sorted(by_course.items(), key=lambda kv: -len(kv[1])):
            c = CATALOG.courses.get(cid, {})
            item = {"id": cid, "name": c.get("name", cid), "codes": c.get("codes", []), "count": len(rs)}
            if len(rs) >= DOCENTE_MIN:
                shown_rows += rs
                item["summary"] = reduced_summary(rs)
                item["reviews"] = [{"id": r["id"], "ratings": {k: r[k] for k in CRITERIA_KEYS},
                                    "wouldRetake": None if r["would_retake"] is None else bool(r["would_retake"]),
                                    "tags": json.loads(r["tags"] or "[]"), "comment": r["comment"]} for r in rs]
            courses.append(item)
        self.send_json({
            "professor": {"slug": slug, "name": prof["name"], "photo": prof.get("photo"), "areas": prof.get("areas", [])},
            "total": len(rows), "summary": reduced_summary(shown_rows) if shown_rows else None, "courses": courses,
            "minPerCourse": DOCENTE_MIN, "criteria": CRITERIA, "tags": TAGS, "reportReasons": REPORT_REASONS,
        })

    def post_docente_report(self, review_id: str) -> None:
        """Denúncia do próprio docente: vai para a fila da moderação, mas nunca tira a avaliação do ar sozinha
        (não conta como rede diferente: senão bastaria ao docente denunciar de casa, do celular e do campus)."""
        slug = self.docente()
        if not slug:
            raise ApiError(401, "Acesso de docente expirado.")
        self.limit("report")
        data = self.read_json()
        reason = data.get("reason")
        if reason not in REPORT_REASONS:
            raise ApiError(400, "Escolha um motivo.")
        detail = clean_line(data.get("detail"), 300) or None
        with connect() as conn:
            row = conn.execute("SELECT professor FROM reviews WHERE id=? AND status='published'", (int(review_id),)).fetchone()
            if not row or row["professor"] != slug:
                raise ApiError(404, "Avaliação não encontrada.")
            conn.execute("""INSERT OR REPLACE INTO reports (review_id, reporter_hash, reason, detail, created_at)
                            VALUES (?,?,?,?,?)""",
                         (int(review_id), token_hash(f"docente:{slug}"), reason,
                          f"[pelo docente] {detail}" if detail else "[pelo docente]", review_day()))
        self.send_json({"message": "Denúncia enviada à moderação. Obrigado."}, 201)

    def require_admin(self) -> None:
        self.limit("admin")
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not ADMIN_TOKEN or not hmac.compare_digest(given.encode(), ADMIN_TOKEN.encode()):
            raise ApiError(401, "Token de moderação inválido.")

    def get_admin_queue(self) -> None:
        self.require_admin()
        with connect() as conn:
            rows = conn.execute("""SELECT r.*, (SELECT COUNT(*) FROM reports p WHERE p.review_id=r.id AND p.resolved=0) nreports
                                   FROM reviews r WHERE r.status='held'
                                      OR EXISTS (SELECT 1 FROM reports p WHERE p.review_id=r.id AND p.resolved=0)
                                   ORDER BY r.created_at DESC, r.id DESC LIMIT 200""").fetchall()
            reasons = defaultdict(list)
            for rep in conn.execute("SELECT review_id, reason, detail FROM reports WHERE resolved=0"):
                reasons[rep["review_id"]].append({"reason": rep["reason"], "detail": rep["detail"]})
            # picos: muitas avaliações do mesmo docente num só dia costumam ser campanha (a favor ou contra)
            bursts = conn.execute("""SELECT professor, created_at day, COUNT(*) n, ROUND(AVG(overall), 1) avg FROM reviews
                                     WHERE status!='removed' AND created_at >= ? GROUP BY professor, created_at
                                     HAVING n >= ? ORDER BY created_at DESC, n DESC""",
                                  (review_day() - 30 * 86400, BURST_REVIEWS)).fetchall()
        out = []
        for r in rows:
            item = review_out(r)
            item.update(status=r["status"], heldReason=r["held_reason"], reports=reasons.get(r["id"], []))
            out.append(item)
        self.send_json({"queue": out, "bursts": [{"professor": b["professor"], "day": b["day"], "count": b["n"], "avg": b["avg"]}
                                                 for b in bursts]})

    def post_admin_review(self, review_id: str) -> None:
        self.require_admin()
        action = self.read_json().get("action")
        status = {"publish": "published", "remove": "removed", "hold": "held"}.get(action)
        if not status:
            raise ApiError(400, "Ação inválida.")
        with connect() as conn:
            if not conn.execute("UPDATE reviews SET status=?, held_reason=NULL WHERE id=?", (status, int(review_id))).rowcount:
                raise ApiError(404, "Avaliação não encontrada.")
            conn.execute("UPDATE reports SET resolved=1 WHERE review_id=?", (int(review_id),))
        report_networks.clear(int(review_id))  # decidido por uma pessoa: denúncias antigas não a tiram do ar de novo
        self.send_json({"message": "Feito.", "status": status})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return  # cliente desconectou: não é um erro do servidor
        super().handle_error(request, client_address)


def make_server(host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    init_db()
    server = Server((host, port), App)
    return server


if __name__ == "__main__":
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    host, port = os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8000"))
    server = make_server(host, port)
    print(f"DCCHub em http://localhost:{port}  ({len(CATALOG.professors)} docentes, {len(CATALOG.courses)} disciplinas)")
    if not ADMIN_TOKEN:
        print("Moderação desativada: defina DCCHUB_ADMIN_TOKEN para habilitar o painel /#/moderacao.")
    if notifier.enabled:
        print(f"Avisos de moderação por e-mail ativos ({len(MOD_EMAILS)} destinatário(s))."
              + ("" if EMAIL.smtp_host else " SMTP não configurado: os avisos aparecem aqui no terminal."))
    if DOCENTE_LOGIN:
        print(f"Modo docente ativo: {len(docente_by_email())} e-mails de docentes abrem só a própria página.")
    if EMAIL.enabled:
        print(f"Acesso por e-mail @{EMAIL.domain} ativo." + ("" if EMAIL.smtp_host else
              " SMTP não configurado: os códigos aparecem aqui no terminal (só para desenvolvimento)."))

    def stop(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)  # docker stop / systemd: encerra limpo
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

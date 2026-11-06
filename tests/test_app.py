"""Testes do Avalie DCC (apenas biblioteca padrão).

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

TMP = tempfile.TemporaryDirectory()


def tearDownModule():
    TMP.cleanup()

os.environ["AVALIA_DCC_DB"] = str(Path(TMP.name) / "test.db")
os.environ["AVALIA_DCC_SECRET"] = "segredo-de-teste"
os.environ["AVALIA_DCC_ADMIN_TOKEN"] = "admin-teste"

CATALOG = {
    "generatedAt": "2026-01-01T00:00:00+00:00",
    "sources": {"professors": "https://dcc.ufmg.br/professores/", "offerings": []},
    "terms": ["2020.1", "2024.1", "2024.2", "2025.1", "2025.2"],
    "professors": [
        {"slug": "ana-silva", "name": "Ana Silva", "status": "ativo", "photo": None, "areas": ["Banco de Dados"], "dccUrl": None},
        {"slug": "bruno-costa", "name": "Bruno Costa", "status": "ativo", "photo": None, "areas": [], "dccUrl": None},
    ],
    "courses": [
        {"id": "DCC206", "codes": ["DCC206"], "name": "Algoritmos I", "kind": "regular", "terms": ["2025.1", "2025.2"]},
        {"id": "DCC011", "codes": ["DCC011"], "name": "Introdução a Banco de Dados", "kind": "regular", "terms": ["2025.2"]},
        {"id": "DCC221", "codes": ["DCC221", "DCC205"], "name": "Estruturas de Dados", "kind": "regular", "terms": ["2024.1", "2025.2"], "aliases": ["DCC205"]},
        {"id": "DCC003", "codes": ["DCC003"], "name": "Algoritmos e Estruturas de Dados I", "kind": "regular", "terms": ["2020.1"], "retired": True, "successor": "DCC221"},
        {"id": "DCC050", "codes": ["DCC050"], "name": "Introdução à Ciência da Computação", "kind": "regular", "terms": ["2025.1"], "retired": True},
    ],
    "offerings": [
        {"term": "2025.1", "course": "DCC206", "codes": ["DCC206"], "professors": ["ana-silva"], "schedule": [{"day": 2, "start": "14:55", "end": "16:35"}]},
        {"term": "2025.2", "course": "DCC206", "codes": ["DCC206"], "professors": ["bruno-costa"], "schedule": []},
        {"term": "2025.2", "course": "DCC011", "codes": ["DCC011"], "professors": ["ana-silva"], "schedule": []},
        {"term": "2024.1", "course": "DCC221", "codes": ["DCC205"], "professors": ["bruno-costa"], "schedule": []},
        {"term": "2025.2", "course": "DCC221", "codes": ["DCC221"], "professors": ["bruno-costa"], "schedule": []},
        {"term": "2020.1", "course": "DCC003", "codes": ["DCC003"], "professors": ["ana-silva"], "schedule": []},
        {"term": "2025.1", "course": "DCC050", "codes": ["DCC050"], "professors": ["bruno-costa"], "schedule": []},
    ],
}
CATALOG_PATH = Path(TMP.name) / "catalog.json"
CATALOG_PATH.write_text(json.dumps(CATALOG))

import app  # noqa: E402  (precisa das variáveis de ambiente acima)
import sync_dcc  # noqa: E402
import sync_provas  # noqa: E402
import import_sigaa  # noqa: E402
import sync_oportunidades  # noqa: E402

PROVAS = {"name": "newCompleaks", "url": "https://drive.google.com/drive/folders/raiz", "extras": [], "sections": [
    {"title": "3° Período", "url": "https://drive.google.com/drive/folders/p3", "folders": [], "courses": [
        {"code": "DCC205", "title": "Estruturas de Dados", "url": "https://drive.google.com/drive/folders/ed", "files": 114, "note": None, "course": "DCC205"},
        {"code": "MAT039", "title": "Cálculo II", "url": "https://drive.google.com/drive/folders/c2", "files": 9, "note": None, "course": None}]}]}
(Path(TMP.name) / "provas.json").write_text(json.dumps(PROVAS))
app.EXAMS = app.Exams(Path(TMP.name) / "provas.json")
OPPS = {"generatedAt": "2026-11-05T00:00:00+00:00", "source": "https://dcc.ufmg.br/oportunidades/", "items": [
    {"slug": "ic-x", "title": "Bolsa de IC – Projeto X", "url": "https://dcc.ufmg.br/oportunidade/ic-x/", "open": True, "deadline": "Até preencher",
     "types": ["Iniciação científica"], "areas": [], "groups": [], "audiences": ["Graduação"], "summary": "", "stipends": [], "hours": [20],
     "published": "2026-10-01", "apply": None, "professors": ["bruno-costa"]},
    {"slug": "velha", "title": "Chamada encerrada", "url": "https://dcc.ufmg.br/oportunidade/velha/", "open": False, "deadline": None,
     "types": [], "areas": [], "groups": [], "audiences": [], "summary": "", "stipends": [], "hours": [], "published": None, "apply": None,
     "professors": ["bruno-costa"]}]}
(Path(TMP.name) / "oportunidades.json").write_text(json.dumps(OPPS))
app.OPPORTUNITIES = app.Opportunities(Path(TMP.name) / "oportunidades.json")

app.CATALOG = app.Catalog(CATALOG_PATH)


def review(**over):
    data = {"professor": "ana-silva", "course": "DCC206", "term": "2025.1",
            "ratings": {"overall": 4, "didactics": 5, "fairness": 4, "difficulty": 3, "passing": 2, "workload": 3},
            "wouldRetake": True, "grade": "B", "tags": ["Listas de exercícios"],
            "comment": "Aulas claras e listas que preparam bem para as provas da disciplina."}
    data.update(over)
    return data


class proxied:
    """Simula o app atrás de um proxy confiável (X-Forwarded-For / X-Forwarded-Proto)."""

    def __enter__(self):
        self.original, app.TRUST_PROXY = app.TRUST_PROXY, True

    def __exit__(self, *exc):
        app.TRUST_PROXY = self.original


class Client:
    """Cliente HTTP com cookies (simula um navegador)."""

    def __init__(self, port: int, ip_header: str | None = None):
        self.port, self.cookie = port, ""

    def request(self, method: str, path: str, body=None, headers=None, raw: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Cookie": self.cookie} if self.cookie else {}
        if body is not None:
            raw = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        conn.request(method, path, body=raw, headers=hdrs)
        res = conn.getresponse()
        data = res.read()
        cookie = res.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";")[0]
        conn.close()
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        return res.status, parsed, res

    get = lambda self, path, **kw: self.request("GET", path, **kw)  # noqa: E731
    post = lambda self, path, body=None, **kw: self.request("POST", path, body if body is not None else {}, **kw)  # noqa: E731


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        with app.connect() as conn:
            conn.executescript("DELETE FROM votes; DELETE FROM reports; DELETE FROM reviews; DELETE FROM class_reports;")
        app.limiter.events.clear()
        app.report_networks.by_review.clear()

    def client(self) -> Client:
        return Client(self.port)

    # ---------------------------------------------------------------- leitura
    def test_bootstrap_lists_catalog_and_form_definitions(self):
        status, data, res = self.client().get("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual({p["slug"] for p in data["professors"]}, {"ana-silva", "bruno-costa"})
        ana = next(p for p in data["professors"] if p["slug"] == "ana-silva")
        self.assertTrue(ana["teachingNow"])
        self.assertEqual(data["latestTerm"], "2025.2")
        self.assertIn("overall", [c["key"] for c in data["criteria"]])
        self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))

    def test_static_files_and_spa_fallback(self):
        c = self.client()
        status, body, res = c.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"DCCHub", body)
        status, _, _ = c.get("/professor/qualquer")  # rota sem extensão -> index
        self.assertEqual(status, 200)
        status, _, _ = c.get("/../app.py")
        self.assertEqual(status, 404)
        status, _, _ = c.get("/nao-existe.js")
        self.assertEqual(status, 404)

    # ---------------------------------------------------------------- versões de disciplina e oferta
    def test_old_course_code_resolves_to_current_version(self):
        c = self.client()
        status, data, _ = c.get("/api/courses/DCC205")
        self.assertEqual(status, 200)
        self.assertEqual(data["course"]["id"], "DCC221")
        self.assertEqual({h["term"] for h in data["history"]}, {"2024.1", "2025.2"})
        status, data, _ = c.post("/api/reviews", review(professor="bruno-costa", course="DCC205", term="2024.1"))
        self.assertEqual(status, 201, data)
        _, course, _ = c.get("/api/courses/DCC221")
        self.assertEqual(course["reviews"][0]["course"], "DCC221")
        self.assertTrue(course["reviews"][0]["verified"])
        _, listed, _ = c.get("/api/reviews?course=DCC205")
        self.assertEqual(len(listed["reviews"]), 1)

    def test_reviews_with_old_code_are_migrated(self):
        with app.connect() as conn:
            conn.execute("""INSERT INTO reviews (professor, course, term, overall, didactics, fairness, difficulty, workload, comment, status, reviewer_hash, created_at)
                            VALUES ('bruno-costa', 'DCC205', '2024.1', 4, 4, 4, 3, 3, 'comentário antigo com código antigo', 'published', 'h', 1)""")
        app._migrated_version = -1
        app.migrate_aliases()
        with app.connect() as conn:
            self.assertEqual(conn.execute("SELECT course FROM reviews").fetchone()[0], "DCC221")

    def test_extinct_courses_disappear_and_point_to_successor(self):
        c = self.client()
        _, boot, _ = c.get("/api/bootstrap")
        ids = {x["id"] for x in boot["courses"]}
        self.assertNotIn("DCC003", ids, "AEDS I é do currículo antigo")
        self.assertNotIn("DCC050", ids, "extinta mesmo tendo sido ofertada em 2025.1")
        self.assertIn("DCC221", ids)
        ana = next(p for p in boot["professors"] if p["slug"] == "ana-silva")
        self.assertNotIn("DCC003", ana["courses"])
        status, data, _ = c.post("/api/reviews", review(course="DCC003", term="2020.1"))
        self.assertEqual(status, 400)
        self.assertIn("course", data["fields"])
        status, data, _ = c.get("/api/courses/DCC003")
        self.assertEqual((status, data["successor"]), (404, "DCC221"))
        _, prof, _ = c.get("/api/professors/ana-silva")
        self.assertNotIn("DCC003", {x["id"] for x in prof["courses"]})
        self.assertNotIn("DCC003", {h["course"] for h in prof["history"]})
        _, offer, _ = c.get("/api/offerings?term=2020.1")
        self.assertEqual(offer["offerings"], [])

    def test_extinct_course_with_old_reviews_stays_visible(self):
        with app.connect() as conn:
            conn.execute("""INSERT INTO reviews (professor, course, term, overall, didactics, fairness, difficulty, workload, comment, status, reviewer_hash, created_at)
                            VALUES ('ana-silva', 'DCC003', '2020.1', 4, 4, 4, 3, 3, 'avaliação antiga que não pode sumir', 'published', 'h', 1)""")
        c = self.client()
        status, page, _ = c.get("/api/courses/DCC003")
        self.assertEqual(status, 200)
        self.assertFalse(page["course"]["active"])
        self.assertIn("DCC003", {x["id"] for x in c.get("/api/bootstrap")[1]["courses"]})

    def test_community_reports_for_unpublished_term(self):
        a, b = self.client(), self.client()
        term = app.current_term()
        status, data, _ = a.post("/api/class-reports", {"professor": "bruno-costa", "course": "DCC205"})
        self.assertEqual(status, 201, data)
        self.assertEqual((data["term"], data["count"]), (term, 1))
        a.post("/api/class-reports", {"professor": "bruno-costa", "course": "DCC221"})  # repetido: não conta de novo
        self.assertEqual(b.post("/api/class-reports", {"professor": "bruno-costa", "course": "DCC221"})[1]["count"], 2)
        _, prof, _ = a.get("/api/professors/bruno-costa")
        self.assertEqual(prof["community"]["term"], term)
        self.assertEqual(prof["community"]["items"], [{"professor": "bruno-costa", "course": "DCC221", "count": 2, "mine": True}])
        _, course, _ = b.get("/api/courses/DCC221")
        self.assertEqual(course["community"]["items"][0]["count"], 2)
        a.request("DELETE", "/api/class-reports", {"professor": "bruno-costa", "course": "DCC221"})
        _, prof, _ = a.get("/api/professors/bruno-costa")
        self.assertEqual(prof["community"]["items"][0]["count"], 1)
        self.assertFalse(prof["community"]["items"][0]["mine"])
        self.assertEqual(a.post("/api/class-reports", {"professor": "fulano", "course": "DCC221"})[0], 400)
        admin = {"Authorization": "Bearer admin-teste"}
        _, items, _ = a.get("/api/admin/class-reports", headers=admin)
        self.assertEqual(items["items"][0]["count"], 1)
        a.post("/api/admin/class-reports/delete", {"term": term, "professor": "bruno-costa", "course": "DCC221"}, headers=admin)
        _, prof, _ = a.get("/api/professors/bruno-costa")
        self.assertEqual(prof["community"]["items"], [])

    def test_community_reports_closed_once_official_offer_exists(self):
        original = app.current_term
        app.current_term = lambda today=None: "2025.2"
        try:
            c = self.client()
            self.assertEqual(c.post("/api/class-reports", {"professor": "bruno-costa", "course": "DCC221"})[0], 409)
            _, prof, _ = c.get("/api/professors/bruno-costa")
            self.assertIsNone(prof["community"])
            self.assertTrue(c.get("/api/bootstrap")[1]["offerPublished"])
        finally:
            app.current_term = original

    def test_exam_archive_links(self):
        c = self.client()
        status, data, _ = c.get("/api/provas")
        self.assertEqual(status, 200)
        self.assertEqual(data["sections"][0]["courses"][0]["course"], "DCC221", "pasta da versão antiga aponta para a atual")
        self.assertIsNone(data["sections"][0]["courses"][1]["course"])
        _, course, _ = c.get("/api/courses/DCC221")
        self.assertEqual([(e["code"], e["files"]) for e in course["exams"]["items"]], [("DCC205", 114)])
        _, other, _ = c.get("/api/courses/DCC206")
        self.assertIsNone(other["exams"])

    def test_opportunities_api_and_professor_block(self):
        c = self.client()
        status, data, _ = c.get("/api/oportunidades")
        self.assertEqual((status, len(data["items"])), (200, 2))
        _, prof, _ = c.get("/api/professors/bruno-costa")
        self.assertEqual([o["slug"] for o in prof["opportunities"]], ["ic-x"], "só chamadas abertas aparecem no perfil")
        _, other, _ = c.get("/api/professors/ana-silva")
        self.assertEqual(other["opportunities"], [])

    def test_env_names_new_and_legacy(self):
        os.environ["DCCHUB_X_TEST"] = "novo"
        os.environ["AVALIA_DCC_Y_TEST"] = "antigo"
        try:
            self.assertEqual(app.env("X_TEST"), "novo")
            self.assertEqual(app.env("Y_TEST"), "antigo", "nome antigo continua aceito")
            self.assertEqual(app.env("Z_TEST", "padrão"), "padrão")
        finally:
            del os.environ["DCCHUB_X_TEST"], os.environ["AVALIA_DCC_Y_TEST"]

    def test_community_access_gate(self):
        original = app.ACCESS_CODES
        app.ACCESS_CODES = ["comunidade-2026"]
        try:
            c = self.client()
            for path in ("/api/bootstrap", "/api/professors/ana-silva", "/api/reviews", "/api/oportunidades", "/api/provas"):
                status, data, _ = c.get(path)
                self.assertEqual((status, data.get("gate")), (401, True), f"{path} não pode vazar dados sem código")
            status, page, res = c.get("/")
            self.assertEqual(status, 200, "a casca do site (sem dados) continua acessível para mostrar a tela de acesso")
            self.assertIn("noindex", res.getheader("X-Robots-Tag"))
            self.assertEqual(c.post("/api/access", {"code": "errado"})[0], 403)
            status, _, res = c.post("/api/access", {"code": "Comunidade-2026"})
            self.assertEqual(status, 200)
            cookie = [h for h in res.getheaders() if h[0] == "Set-Cookie" and "dcchub_access=" in h[1]][0][1]
            self.assertIn("HttpOnly", cookie)
            self.assertNotIn("comunidade-2026", cookie.lower(), "o cookie guarda um HMAC, não o código")
            c.cookie = cookie.split(";")[0]
            self.assertEqual(c.get("/api/bootstrap")[0], 200)
            app.ACCESS_CODES = ["novo-codigo"]  # código vazou: troca
            self.assertEqual(c.get("/api/bootstrap")[0], 401, "trocar o código revoga quem entrou com o antigo")
        finally:
            app.ACCESS_CODES = original

    def test_email_login_ufmg(self):
        """Link mágico só para @ufmg.br: sem conta, sem guardar o e-mail, uso único, revogável."""
        import email_login
        original = (app.ACCESS_CODES, app.EMAIL.enabled, app.EMAIL.epoch, app.EMAIL.public_url, app.email_login.send_async)
        sent = []
        app.ACCESS_CODES, app.EMAIL.enabled, app.EMAIL.public_url = [], True, "https://dcchub.exemplo"
        app.email_login.send_async = lambda cfg, to, url: sent.append((to, url))
        try:
            c = self.client()
            status, data, _ = c.get("/api/bootstrap")
            self.assertEqual((status, data.get("gate"), data.get("methods")), (401, True, ["email"]))
            self.assertEqual(c.post("/api/access", {"code": "qualquer"})[0], 403, "sem códigos, só e-mail")
            for bad in ("nao-e-email", "ana@gmail.com", "ana@dcc.ufmg.br", "ana@ufmg.br.evil.com", "ana@xufmg.br",
                        "ana@ufmg.br\r\nBcc: x@ufmg.br", "", None, 42):
                app.limiter.events.clear()
                status, data, _ = c.post("/api/access/email", {"email": bad})
                self.assertEqual(status, 400, f"{bad!r} deveria ser recusado")
            self.assertEqual(sent, [], "domínio errado (inclusive @dcc.ufmg.br, dos docentes) não dispara e-mail")
            app.limiter.events.clear()

            status, data, _ = c.post("/api/access/email", {"email": "  Aluna.Exemplo@UFMG.br "})
            self.assertEqual(status, 200)
            self.assertEqual(sent[0][0], "aluna.exemplo@ufmg.br")
            url = sent[0][1]
            self.assertTrue(url.startswith("https://dcchub.exemplo/#/entrar?t="), "base fixa, nunca do cabeçalho Host")
            token = url.split("t=", 1)[1]
            self.assertNotIn("aluna", token)
            self.assertEqual(c.get("/api/bootstrap")[0], 401, "pedir o link não dá acesso")

            for forged in ("", "l1.1.a.b", token[:-1] + ("0" if token[-1] != "0" else "1"),
                           email_login._token("l1", app.SECRET, -10), email_login._token("l1", "outra-chave", 900),
                           email_login.access_token(app.SECRET, app.EMAIL.epoch)):
                self.assertEqual(c.post("/api/access/email/verify", {"token": forged})[0], 400, forged)
            status, _, res = c.post("/api/access/email/verify", {"token": token})
            self.assertEqual(status, 200)
            cookie = [h for h in res.getheaders() if h[0] == "Set-Cookie" and "dcchub_access=" in h[1]][0][1]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            self.assertNotIn("aluna", cookie.lower())
            c.cookie = cookie.split(";")[0]
            self.assertEqual(c.get("/api/bootstrap")[0], 200)
            self.assertEqual(self.client().post("/api/access/email/verify", {"token": token})[0], 400, "link de uso único")

            # o e-mail não fica em lugar nenhum do banco
            with app.connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(FULL)")
            for f in app.DB.parent.glob(app.DB.name + "*"):
                self.assertNotIn(b"aluna", f.read_bytes().lower(), f.name)

            # limite por endereço: a resposta é igual, mas nada além de 3 envios por hora
            app.limiter.events.clear()
            for _ in range(4):
                self.assertEqual(self.client().post("/api/access/email", {"email": "outra@ufmg.br"})[0], 200)
            self.assertEqual(sum(1 for to, _ in sent if to == "outra@ufmg.br"), 3)
            # limite por IP
            statuses = [self.client().post("/api/access/email", {"email": f"p{i}@ufmg.br"})[0] for i in range(6)]
            self.assertIn(429, statuses)

            app.EMAIL.epoch = "2"  # revoga todos os acessos por e-mail
            self.assertEqual(c.get("/api/bootstrap")[0], 401)

            app.ACCESS_CODES = ["codigo"]  # os dois caminhos juntos
            self.assertEqual(self.client().get("/api/bootstrap")[1]["methods"], ["email", "code"])
        finally:
            app.ACCESS_CODES, app.EMAIL.enabled, app.EMAIL.epoch, app.EMAIL.public_url, app.email_login.send_async = original

    def test_email_login_daily_caps(self):
        """Poucas conexões não podem gastar a cota diária do serviço de e-mail (e travar o acesso de todo mundo)."""
        original = (app.EMAIL.enabled, app.email_login.send_async, app.EMAIL_DAILY_LIMIT, app.LIMITS["login"])
        sent = []
        app.EMAIL.enabled, app.EMAIL_DAILY_LIMIT, app.LIMITS["login"] = True, 4, (100, 600)
        app.email_login.send_async = lambda cfg, to, url: sent.append(to)
        try:
            per_ip = app.LIMITS["login-day"][0]
            statuses = [self.client().post("/api/access/email", {"email": f"a{i}@ufmg.br"})[0] for i in range(per_ip + 1)]
            self.assertEqual(statuses[-1], 429, "teto diário por conexão")
            self.assertEqual(len(sent), 4, "teto global por dia")
            self.assertIn(503, statuses)
        finally:
            app.EMAIL.enabled, app.email_login.send_async, app.EMAIL_DAILY_LIMIT, app.LIMITS["login"] = original

    def test_email_login_disabled_by_default(self):
        self.assertEqual(self.client().post("/api/access/email", {"email": "a@ufmg.br"})[0], 404)
        self.assertEqual(self.client().post("/api/access/email/verify", {"token": "x"})[0], 404)

    def test_unknown_professor_is_404(self):
        status, data, _ = self.client().get("/api/professors/fulano")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    # ---------------------------------------------------------------- criação
    def test_review_dates_are_stored_by_day(self):
        """Hora exata permitiria cruzar a avaliação com o horário de envio do link de acesso."""
        c = self.client()
        self.assertEqual(c.post("/api/reviews", review())[0], 201)
        with app.connect() as conn:
            created = conn.execute("SELECT created_at FROM reviews").fetchone()[0]
            self.assertEqual((created - app.BRT) % 86400, 0, "só o dia (meia-noite de Brasília)")
            self.assertEqual(created, app.review_day())
            conn.execute("UPDATE reviews SET created_at = created_at + 51234, updated_at = created_at + 777")  # banco antigo
        app.init_db()
        with app.connect() as conn:
            created, updated = conn.execute("SELECT created_at, updated_at FROM reviews").fetchone()
        self.assertEqual(((created - app.BRT) % 86400, (updated - app.BRT) % 86400), (0, 0), "datas antigas arredondadas")
        self.assertEqual(app.review_day(1_767_236_400 + 23 * 3600), 1_767_236_400, "23h em Brasília ainda é o mesmo dia")

    def test_publish_review_and_aggregate(self):
        c = self.client()
        status, data, _ = c.post("/api/reviews", review())
        self.assertEqual(status, 201, data)
        self.assertEqual(data["status"], "published")
        self.assertTrue(c.cookie.startswith("avalia_dcc="))
        status, prof, _ = c.get("/api/professors/ana-silva")
        self.assertEqual(prof["summary"]["count"], 1)
        self.assertEqual(prof["summary"]["avg"]["didactics"], 5)
        self.assertEqual(prof["summary"]["retake"], 100)
        r = prof["reviews"][0]
        self.assertTrue(r["verified"], "Ana deu DCC206 em 2025.1 segundo a oferta")
        self.assertTrue(r["mine"])
        _, course, _ = c.get("/api/courses/DCC206")
        stats = next(p for p in course["professors"] if p["slug"] == "ana-silva")["stats"]
        self.assertEqual(stats["count"], 1)

    def test_learning_and_passing_difficulty_are_separate(self):
        """Dificuldade para aprender ≠ para passar. Avaliações antigas (pergunta única) ficam com "passar" vazio."""
        crit = {c["key"]: c for c in self.client().get("/api/bootstrap")[1]["criteria"]}
        self.assertEqual((crit["difficulty"]["label"], crit["passing"]["label"]), ("Dificuldade para aprender", "Dificuldade para passar"))
        status, data, _ = self.client().post("/api/reviews", review(ratings={"overall": 4, "didactics": 4, "fairness": 4, "difficulty": 3, "workload": 3}))
        self.assertEqual((status, list(data["fields"])), (400, ["passing"]), "obrigatória nas avaliações novas")

        with app.connect() as conn:  # avaliação antiga, de antes da separação
            conn.execute("""INSERT INTO reviews (id, professor, course, term, overall, didactics, fairness, difficulty, workload, comment, status, reviewer_hash, created_at)
                            VALUES (777, 'ana-silva', 'DCC206', '2025.1', 2, 2, 2, 5, 4, 'Avaliação antiga com a pergunta de dificuldade única.', 'published', 'antiga', ?)""",
                         (app.review_day(),))
        self.assertEqual(self.client().post("/api/reviews", review(ratings={"overall": 4, "didactics": 4, "fairness": 4, "difficulty": 1, "passing": 4, "workload": 2}))[0], 201)

        _, prof, _ = self.client().get("/api/professors/ana-silva")
        summary = prof["summary"]
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["avg"]["difficulty"], 3.0, "aprender: média das duas")
        self.assertEqual(summary["avg"]["passing"], 4.0, "passar: só quem respondeu")
        self.assertEqual(summary["answered"]["passing"], 1)
        old = next(r for r in prof["reviews"] if r["id"] == 777)
        self.assertIsNone(old["ratings"]["passing"])
        course = next(c for c in prof["courses"] if c["id"] == "DCC206")
        self.assertEqual((course["stats"]["difficulty"], course["stats"]["passing"]), (3.0, 4.0))
        _, boot, _ = self.client().get("/api/bootstrap")
        stats = next(p for p in boot["professors"] if p["slug"] == "ana-silva")["stats"]
        self.assertEqual((stats["count"], stats["difficulty"], stats["passing"]), (2, 3.0, 4.0))

        with app.connect() as conn:  # só avaliações antigas: "passar" fica sem média, sem quebrar nada
            conn.execute("DELETE FROM reviews WHERE id != 777")
        _, prof, _ = self.client().get("/api/professors/ana-silva")
        self.assertIsNone(prof["summary"]["avg"]["passing"])
        self.assertEqual(prof["summary"]["avg"]["difficulty"], 5.0)
        self.assertEqual(self.client().get("/api/offerings?term=2025.1")[0], 200)

    def test_evolution_by_term(self):
        """Médias por semestre cursado, em ordem, no resumo do docente e da disciplina."""
        def r(term, overall, passing, **kw):
            return review(term=term, ratings={"overall": overall, "didactics": overall, "fairness": 3, "difficulty": 3, "passing": passing, "workload": 3}, **kw)
        self.assertEqual(self.client().post("/api/reviews", r("2025.1", 2, 5, comment="Primeira turma avaliada, semestre bem puxado e provas difíceis."))[0], 201)
        self.assertEqual(self.client().post("/api/reviews", r("2024.2", 4, 2, comment="Turma anterior, aulas boas e avaliação tranquila no fim do semestre."))[0], 201)
        self.assertEqual(self.client().post("/api/reviews", r("2025.1", 4, 3, comment="Mesmo semestre de outra pessoa, achei as aulas melhores que o esperado."))[0], 201)
        with app.connect() as conn:  # avaliação antiga, sem "passar"
            conn.execute("""INSERT INTO reviews (id, professor, course, term, overall, didactics, fairness, difficulty, workload, comment, status, reviewer_hash, created_at)
                            VALUES (991, 'ana-silva', 'DCC206', '2025.1', 3, 3, 3, 3, 3, 'Avaliação antiga de 2025.1 sem a pergunta separada.', 'published', 'antiga', ?)""", (app.review_day(),))
            conn.execute("""INSERT INTO reviews (id, professor, course, term, overall, didactics, fairness, difficulty, workload, comment, status, reviewer_hash, created_at)
                            VALUES (992, 'ana-silva', 'DCC206', '2023.2', 1, 1, 1, 1, 1, 'Segurada pela moderação, não entra nas médias públicas.', 'held', 'segurada', ?)""", (app.review_day(),))
        _, prof, _ = self.client().get("/api/professors/ana-silva")
        self.assertEqual([(t["term"], t["count"], t["overall"], t["passing"]) for t in prof["summary"]["byTerm"]],
                         [("2024.2", 1, 4.0, 2.0), ("2025.1", 3, 3.0, 4.0)], "ordem cronológica; vazios e seguradas fora")
        _, course, _ = self.client().get("/api/courses/DCC206")
        self.assertEqual([t["term"] for t in course["summary"]["byTerm"]], ["2024.2", "2025.1"])
        self.assertNotIn("byTerm", self.client().get("/api/professors/bruno-costa")[1]["summary"], "sem avaliações, sem série")

    def test_unverified_pair_is_accepted_but_not_marked(self):
        c = self.client()
        status, data, _ = c.post("/api/reviews", review(professor="bruno-costa", course="DCC011", term="2024.2"))
        self.assertEqual(status, 201, data)
        _, prof, _ = c.get("/api/professors/bruno-costa")
        self.assertFalse(prof["reviews"][0]["verified"])

    def test_validation_reports_each_field(self):
        status, data, _ = self.client().post("/api/reviews", review(professor="x", course="y", term="1999.1", ratings={"overall": 9}, comment="curto", tags=["inventada"]))
        self.assertEqual(status, 400)
        for field in ("professor", "course", "term", "overall", "didactics", "comment", "tags"):
            self.assertIn(field, data["fields"])

    def test_future_term_rejected(self):
        status, data, _ = self.client().post("/api/reviews", review(term="2099.1"))
        self.assertEqual(status, 400)
        self.assertIn("term", data["fields"])

    def test_one_review_per_person_per_pair(self):
        c = self.client()
        self.assertEqual(c.post("/api/reviews", review())[0], 201)
        status, data, _ = c.post("/api/reviews", review(term="2025.2"))
        self.assertEqual(status, 409)
        self.assertIsNotNone(data["reviewId"])
        # outro navegador pode avaliar a mesma turma (com as próprias palavras: cópia idêntica é recusada)
        self.assertEqual(self.client().post("/api/reviews", review(comment="Gostei bastante: exemplos práticos e provas bem alinhadas às listas."))[0], 201)

    def test_honeypot_blocks_bots(self):
        status, _, _ = self.client().post("/api/reviews", review(website="http://spam"))
        self.assertEqual(status, 400)

    def test_offensive_comment_is_returned_for_rewrite(self):
        c = self.client()
        status, data, _ = c.post("/api/reviews", review(comment="Professor idiota, não aprendi nada com essas aulas horríveis."))
        self.assertEqual(status, 400, "xingamento não é guardado: volta para reescrever")
        self.assertIn("comment", data["fields"])
        self.assertEqual(data["moderation"][0]["category"], "xingamento")
        with app.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_discrimination_accusation_is_held_for_a_human(self):
        c = self.client()
        status, data, _ = c.post("/api/reviews", review(comment="Ele é machista: só chama os meninos para responder no quadro."))
        self.assertEqual((status, data["status"]), (201, "held"))
        _, prof, _ = c.get("/api/professors/ana-silva")
        self.assertEqual(prof["reviews"], [])
        _, me, _ = c.get("/api/me")
        self.assertEqual(me["reviews"][0]["status"], "held")

    def test_live_check_endpoint(self):
        c = self.client()
        _, data, _ = c.post("/api/moderation/check", {"comment": "A Ana é uma anta, não sabe explicar nada.", "professor": "ana-silva"})
        self.assertEqual(data["decision"], "reject")
        self.assertEqual(data["issues"][0]["category"], "xingamento")
        _, ok, _ = c.post("/api/moderation/check", {"comment": "A prova foi um lixo, nada a ver com as listas da disciplina.", "professor": "ana-silva"})
        self.assertEqual((ok["decision"], ok["issues"]), ("publish", []))

    def test_duplicate_comment_rejected(self):
        text = "Aulas muito boas, listas semanais e provas coerentes com o conteúdo."
        self.assertEqual(self.client().post("/api/reviews", review(comment=text))[0], 201)
        status, data, _ = self.client().post("/api/reviews", review(professor="bruno-costa", comment="  " + text.upper().lower() + " "))
        self.assertEqual(status, 400)
        self.assertIn("idêntico", data["fields"]["comment"])

    def test_moderation_flags(self):
        flags = app.moderation_flags
        self.assertTrue(flags("que id1ota, sério, não sabe dar aula nenhuma"))
        self.assertTrue(flags("vai tomar no cu com essa prova"))
        self.assertTrue(flags("MERDAAA de disciplina, que aula ruim"))
        self.assertTrue(flags("fale com fulano@gmail.com sobre a matéria"))
        self.assertTrue(flags("liga (31) 99876-5432 que ele atende"))
        self.assertEqual(flags("As aulas eram rápidas; mais exemplos ajudariam. Estudei de 2024 2025."), [])
        self.assertEqual(flags("Exige muito, a gente se mata de estudar, mas aprende."), [])

    # ---------------------------------------------------------------- edição/remoção
    def test_owner_can_edit_and_delete_others_cannot(self):
        owner, other = self.client(), self.client()
        _, created, _ = owner.post("/api/reviews", review())
        rid = created["id"]
        status, _, _ = other.request("PUT", f"/api/reviews/{rid}", review(comment="Tentando alterar a avaliação de outra pessoa aqui."))
        self.assertEqual(status, 404)
        status, data, _ = owner.request("PUT", f"/api/reviews/{rid}", review(ratings={"overall": 2, "didactics": 2, "fairness": 2, "difficulty": 5, "passing": 5, "workload": 5}, comment="Mudei de ideia depois da prova final da disciplina."))
        self.assertEqual(status, 200, data)
        _, prof, _ = owner.get("/api/professors/ana-silva")
        self.assertEqual(prof["reviews"][0]["ratings"]["overall"], 2)
        self.assertIsNotNone(prof["reviews"][0]["updatedAt"])
        self.assertEqual(other.request("DELETE", f"/api/reviews/{rid}")[0], 404)
        self.assertEqual(owner.request("DELETE", f"/api/reviews/{rid}")[0], 200)
        _, prof, _ = owner.get("/api/professors/ana-silva")
        self.assertEqual(prof["summary"]["count"], 0)

    def test_recovery_code_moves_identity(self):
        a = self.client()
        a.post("/api/reviews", review())
        self.assertEqual(a.get("/api/me/code")[0], 404, "não sai por GET")
        _, code, _ = a.post("/api/me/code")
        b = self.client()
        self.assertEqual(b.post("/api/me/restore", {"code": "x" * 43})[0], 404)
        status, data, _ = b.post("/api/me/restore", {"code": code["code"]})
        self.assertEqual(status, 200, data)
        _, me, _ = b.get("/api/me")
        self.assertEqual(len(me["reviews"]), 1)

    # ---------------------------------------------------------------- votos e denúncias
    def test_helpful_toggle_and_no_self_vote(self):
        author, reader = self.client(), self.client()
        _, created, _ = author.post("/api/reviews", review())
        rid = created["id"]
        self.assertEqual(author.post(f"/api/reviews/{rid}/helpful")[0], 400)
        _, v1, _ = reader.post(f"/api/reviews/{rid}/helpful")
        self.assertEqual(v1, {"voted": True, "helpful": 1})
        _, v2, _ = reader.post(f"/api/reviews/{rid}/helpful")
        self.assertEqual(v2, {"voted": False, "helpful": 0})

    def test_keep_alive_connection_survives_post_with_body(self):
        """Regressão: corpo não lido de um POST corrompia a próxima requisição na mesma conexão."""
        author, reader = self.client(), self.client()
        _, created, _ = author.post("/api/reviews", review())
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", f"/api/reviews/{created['id']}/helpful", body=b"{}", headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().read() and 200, 200)
        conn.request("GET", "/api/offerings")
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertIn("offerings", json.loads(res.read()))
        conn.close()

    def test_reports_hide_review_only_from_distinct_networks(self):
        """Cookie anônimo novo sai de graça: denúncias de muitas janelas na mesma rede não tiram a avaliação do ar."""
        author = self.client()
        _, created, _ = author.post("/api/reviews", review())
        rid = created["id"]
        self.assertEqual(self.client().post(f"/api/reviews/{rid}/report", {"reason": "inventado"})[0], 400)
        for _ in range(app.AUTO_HIDE_REPORTS + 2):  # mesma rede, cookies novos
            self.assertEqual(self.client().post(f"/api/reviews/{rid}/report", {"reason": app.REPORT_REASONS[0]})[0], 200)
        self.assertEqual(len(author.get("/api/professors/ana-silva")[1]["reviews"]), 1, "uma rede só não censura")
        status, queue, _ = author.get("/api/admin/queue", headers={"Authorization": "Bearer admin-teste"})
        self.assertEqual([r["id"] for r in queue["queue"]], [rid], "mas a denúncia chega à moderação")

        with proxied():
            for i in range(app.AUTO_HIDE_REPORTS - 2):  # forjar o 1º item do X-Forwarded-For não cria redes novas
                self.client().post(f"/api/reviews/{rid}/report", {"reason": app.REPORT_REASONS[0]},
                                   headers={"X-Forwarded-For": f"10.9.9.{i}, 127.0.0.1"})
            self.assertEqual(len(author.get("/api/professors/ana-silva")[1]["reviews"]), 1)
            for i in range(app.AUTO_HIDE_REPORTS - 1):  # redes realmente distintas (a 1ª já veio de 127.0.0.1)
                self.assertEqual(self.client().post(f"/api/reviews/{rid}/report", {"reason": app.REPORT_REASONS[1]},
                                                    headers={"X-Forwarded-For": f"200.0.0.{i}"})[0], 200)
        self.assertEqual(author.get("/api/professors/ana-silva")[1]["reviews"], [])
        with app.connect() as conn:
            self.assertIn("redes", conn.execute("SELECT held_reason FROM reviews WHERE id=?", (rid,)).fetchone()[0])

        # depois da decisão humana, as denúncias antigas não a derrubam de novo
        self.assertEqual(author.post(f"/api/admin/reviews/{rid}", {"action": "publish"}, headers={"Authorization": "Bearer admin-teste"})[0], 200)
        with proxied():
            self.client().post(f"/api/reviews/{rid}/report", {"reason": app.REPORT_REASONS[0]}, headers={"X-Forwarded-For": "200.0.0.99"})
        self.assertEqual(len(author.get("/api/professors/ana-silva")[1]["reviews"]), 1)

    def test_same_class_reviews_limited_per_network_and_login(self):
        """Contra avaliações em série da mesma turma com cookies novos (identidade anônima é de graça)."""
        n = app.LIMITS["review-pair"][0]
        statuses = [self.client().post("/api/reviews", review(comment=f"Aulas claras e listas que preparam bem para a prova, versão {i}."))[0]
                    for i in range(n + 1)]
        self.assertEqual(statuses, [201] * n + [429])
        self.assertEqual(self.client().post("/api/reviews", review(course="DCC011", term="2025.2"))[0], 201, "outra turma segue livre")
        app.limiter.events.clear()  # o limite geral (6 a cada 10 min) não é o assunto aqui
        author = self.client()
        author.post("/api/reviews", review(course="DCC221", term="2025.2", comment="Primeira avaliação desta pessoa para a turma de estruturas."))
        for _ in range(n):  # reenviar a própria avaliação sempre devolve "você já avaliou", nunca 429
            self.assertEqual(author.post("/api/reviews", review(course="DCC221", term="2025.2", comment="Reenvio da mesma pessoa para a mesma turma de novo."))[0], 409)
        with proxied():
            self.assertEqual(self.client().post("/api/reviews", review(comment="Outra rede, outra pessoa: aulas boas e provas justas no semestre."),
                                                headers={"X-Forwarded-For": "200.1.1.1"})[0], 201)

        # login por e-mail: uma avaliação por turma por login, mesmo trocando de rede e de cookie
        app.limiter.events.clear()
        import email_login
        original = app.EMAIL.enabled
        app.EMAIL.enabled = True
        try:
            access = f"dcchub_access={email_login.access_token(app.SECRET, app.EMAIL.epoch)}"
            with proxied():
                first = self.client(); first.cookie = access
                self.assertEqual(first.post("/api/reviews", review(professor="bruno-costa", comment="Didática ótima e provas coerentes com as listas da disciplina."),
                                            headers={"X-Forwarded-For": "200.2.2.2"})[0], 201)
                again = self.client(); again.cookie = access
                self.assertEqual(again.post("/api/reviews", review(professor="bruno-costa", comment="Mesma turma de novo, com outro cookie anônimo e outra rede."),
                                            headers={"X-Forwarded-For": "200.3.3.3"})[0], 429)
        finally:
            app.EMAIL.enabled = original

    def test_helpful_votes_limited_per_network(self):
        _, created, _ = self.client().post("/api/reviews", review())
        rid, n = created["id"], app.LIMITS["vote-pair"][0]
        statuses = [self.client().post(f"/api/reviews/{rid}/helpful")[0] for _ in range(n + 1)]
        self.assertEqual(statuses, [200] * n + [429])
        voter = self.client()
        with proxied():
            self.assertEqual(voter.post(f"/api/reviews/{rid}/helpful", headers={"X-Forwarded-For": "200.4.4.4"})[1], {"voted": True, "helpful": n + 1})
        self.assertEqual(voter.post(f"/api/reviews/{rid}/helpful")[1]["voted"], False, "tirar o voto é sempre possível")

    def test_activity_dates_are_stored_by_day(self):
        """Votos, denúncias e turmas informadas levam o mesmo hash de quem avalia: hora exata revelaria quando a pessoa usou o site."""
        author, other = self.client(), self.client()
        _, created, _ = author.post("/api/reviews", review())
        _, mine, _ = other.post("/api/reviews", review(professor="bruno-costa", comment="Boa didática e listas alinhadas com a prova final."))
        author.post(f"/api/reviews/{mine['id']}/helpful")
        author.post(f"/api/reviews/{mine['id']}/report", {"reason": app.REPORT_REASONS[0]})
        with app.connect() as conn:
            conn.execute("INSERT INTO class_reports VALUES ('2099.1', 'ana-silva', 'DCC206', 'x', ?)", (app.review_day() + 4321,))
            conn.execute("UPDATE votes SET created_at = created_at + 999")
        app.init_db()
        with app.connect() as conn:
            for table in ("votes", "reports", "class_reports"):
                for (ts,) in conn.execute(f"SELECT created_at FROM {table}"):
                    self.assertEqual((ts - app.BRT) % 86400, 0, table)
            self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], 1)
            self.assertGreaterEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2, "VACUUM único já feito")

    def test_review_ids_are_random_and_tables_keep_no_arrival_order(self):
        """Ids sequenciais (ou rowid) diriam quem avaliou/votou antes de quem num mesmo dia, mesmo com a data só por dia."""
        ids = [self.client().post("/api/reviews", review(professor=p, course=c, term=t, comment=f"Aulas claras, listas boas e provas justas, turma {i}."))[1]["id"]
               for i, (p, c, t) in enumerate([("ana-silva", "DCC206", "2025.1"), ("bruno-costa", "DCC206", "2025.2"), ("ana-silva", "DCC011", "2025.2")])]
        self.assertGreater(min(ids), 1_000_000, "ids aleatórios grandes")
        self.assertLessEqual(max(ids), 2**53, "seguros como número em JavaScript")
        self.assertFalse(ids[1] - ids[0] == 1 and ids[2] - ids[1] == 1, "não sequenciais")
        with app.connect() as conn:
            for table in app.ROWLESS_TABLES:
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
                self.assertIn("WITHOUT ROWID", sql.upper(), table)

    def test_old_database_is_migrated_to_random_ids(self):
        """Banco antigo (ids sequenciais, tabelas com rowid): renumera mantendo votos e denúncias ligados à avaliação certa."""
        import sqlite3
        old_db = Path(TMP.name) / "antigo.db"
        old_db.unlink(missing_ok=True)
        schema = (app.SCHEMA.replace(" WITHOUT ROWID", "").replace("  comment_key TEXT,\n", "")
                  .replace("  passing INTEGER CHECK(passing BETWEEN 1 AND 5),\n", ""))
        self.assertNotIn("passing", schema, "banco antigo, sem a coluna nova")
        conn = sqlite3.connect(old_db)
        conn.executescript(schema)
        cols = "professor, course, term, overall, didactics, fairness, difficulty, workload, would_retake, grade, tags, comment, status, reviewer_hash, created_at"
        for i in (1, 2):
            conn.execute(f"INSERT INTO reviews (id, {cols}) VALUES (?, 'ana-silva', 'DCC206', '2025.1', 4, 4, 4, 3, 3, 1, NULL, '[]', ?, 'published', ?, ?)",
                         (i, f"Comentário antigo número {i} sobre a disciplina inteira.", f"autor{i}", app.review_day()))
        conn.execute("INSERT INTO votes VALUES (2, 'leitor', ?)", (app.review_day(),))
        conn.execute("INSERT INTO reports (review_id, reporter_hash, reason, created_at) VALUES (1, 'leitor', 'Outro', ?)", (app.review_day(),))
        conn.commit()
        conn.close()
        original = app.DB
        app.DB = old_db
        try:
            app.init_db()
            with app.connect() as conn:
                rows = {r["comment"][-40:]: r for r in conn.execute("SELECT id, comment, comment_key FROM reviews")}
                ids = [r["id"] for r in rows.values()]
                self.assertTrue(all(i > 1_000_000 for i in ids), ids)
                voted = conn.execute("SELECT review_id FROM votes").fetchone()[0]
                reported = conn.execute("SELECT review_id FROM reports").fetchone()[0]
                by_id = {r["id"]: r["comment"] for r in rows.values()}
                self.assertIn("número 2", by_id[voted], "o voto segue na avaliação certa")
                self.assertIn("número 1", by_id[reported], "a denúncia segue na avaliação certa")
                self.assertTrue(all(r["comment_key"] for r in rows.values()), "chave do comentário preenchida")
                self.assertEqual([r[0] for r in conn.execute("SELECT passing FROM reviews")], [None, None], "\"passar\" não é inventado")
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            app.init_db()  # idempotente: não renumera de novo
            with app.connect() as conn:
                self.assertEqual(sorted(r[0] for r in conn.execute("SELECT id FROM reviews")), sorted(ids))
        finally:
            app.DB = original

    def test_duplicate_check_uses_normalized_key(self):
        text = "Professora explica muito bem e as listas preparam para a prova."
        self.assertEqual(self.client().post("/api/reviews", review(comment=text))[0], 201)
        variant = "  PROFESSORA  explica muito bem e as listas preparam para a prova. "
        status, data, _ = self.client().post("/api/reviews", review(professor="bruno-costa", comment=variant.lower().replace("professora", "Professora")))
        self.assertEqual(status, 400)
        self.assertIn("idêntico", data["fields"]["comment"])
        with app.connect() as conn:
            plan = " ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN SELECT 1 FROM reviews WHERE comment_key=? AND status!='removed' AND reviewer_hash!=? AND id!=?", ("x", "y", 0)))
        self.assertIn("idx_reviews_comment_key", plan, "busca pelo índice, sem varrer a tabela")

    def test_recovery_code_only_by_same_origin_post(self):
        c = self.client()
        c.post("/api/reviews", review())
        self.assertEqual(c.get("/api/me/code")[0], 404)
        self.assertEqual(c.post("/api/me/code", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(c.request("POST", "/api/me/code", raw=b"x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        status, data, res = c.post("/api/me/code")
        self.assertEqual((status, len(data["code"])), (200, 43))
        self.assertEqual(res.getheader("Cache-Control"), "no-store")

    def test_client_ip_uses_last_forwarded_hop(self):
        handler = app.App.__new__(app.App)
        handler.client_address = ("10.0.0.2", 1234)
        handler.headers = {"X-Forwarded-For": "6.6.6.6, 200.5.5.5"}
        with proxied():
            self.assertEqual(handler.client_ip(), "200.5.5.5", "o 1º item vem do cliente e pode ser forjado")
        self.assertEqual(handler.client_ip(), "10.0.0.2", "sem proxy confiável, o cabeçalho é ignorado")

    def test_admin_sees_review_bursts(self):
        with app.connect() as conn:
            for i in range(app.BURST_REVIEWS):
                conn.execute(f"INSERT INTO reviews ({', '.join(app.REVIEW_COLUMNS)}, status, reviewer_hash, created_at) "
                             "VALUES ('ana-silva', 'DCC206', '2025.1', 1, 1, 1, 3, 3, 3, 0, NULL, '[]', 'x', NULL, 'published', ?, ?)",
                             (f"h{i}", app.review_day()))
        _, data, _ = self.client().get("/api/admin/queue", headers={"Authorization": "Bearer admin-teste"})
        self.assertEqual(data["bursts"], [{"professor": "ana-silva", "day": app.review_day(), "count": app.BURST_REVIEWS, "avg": 1.0}])

    def test_static_path_cannot_escape_to_sibling_folder(self):
        base = Path(TMP.name) / "site"
        (base / "static").mkdir(parents=True, exist_ok=True)
        (base / "static-x").mkdir(exist_ok=True)
        (base / "static-x" / "segredo.txt").write_text("não deveria sair")
        (base / "static" / "ok.txt").write_text("público")
        original = app.STATIC
        app.STATIC = base / "static"
        try:
            self.assertEqual(self.client().get("/ok.txt")[0], 200)
            self.assertEqual(self.client().get("/../static-x/segredo.txt")[0], 404)
            self.assertEqual(self.client().get("/%2e%2e/static-x/segredo.txt")[0], 404)
        finally:
            app.STATIC = original

    def test_hsts_only_behind_https_proxy(self):
        self.assertIsNone(self.client().get("/api/bootstrap")[2].getheader("Strict-Transport-Security"))
        with proxied():
            res = self.client().get("/api/bootstrap", headers={"X-Forwarded-Proto": "https"})[2]
        self.assertIn("max-age=", res.getheader("Strict-Transport-Security"))

    def test_admin_queue_and_actions(self):
        c = self.client()
        _, created, _ = c.post("/api/reviews", review(comment="Achei o professor machista com as alunas, mas a prova foi justa no final."))
        self.assertEqual(c.get("/api/admin/queue")[0], 401)
        auth = {"Authorization": "Bearer admin-teste"}
        status, queue, _ = c.get("/api/admin/queue", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(queue["queue"][0]["id"], created["id"])
        status, _, _ = c.post(f"/api/admin/reviews/{created['id']}", {"action": "publish"}, headers=auth)
        self.assertEqual(status, 200)
        _, prof, _ = c.get("/api/professors/ana-silva")
        self.assertEqual(len(prof["reviews"]), 1)

    # ---------------------------------------------------------------- segurança
    def test_rejects_cross_origin_writes(self):
        status, data, _ = self.client().post("/api/reviews", review(), headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_rejects_non_json_and_huge_bodies(self):
        c = self.client()
        status, _, _ = c.request("POST", "/api/reviews", raw=b"professor=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        status, _, _ = c.request("POST", "/api/reviews", raw=b"{" + b" " * (app.MAX_BODY + 10) + b"}", headers={"Content-Type": "application/json"})
        self.assertEqual(status, 413)

    def test_rate_limit_on_reviews(self):
        limit = app.LIMITS["review"][0]
        pairs = [(p, c, t) for p in ("ana-silva", "bruno-costa") for c, t in (("DCC206", "2025.1"), ("DCC011", "2025.2"), ("DCC221", "2025.2"))]
        statuses = []
        for i in range(limit + 1):
            prof, course, term = pairs[i % len(pairs)]
            statuses.append(self.client().post("/api/reviews", review(professor=prof, course=course, term=term,
                                                                      comment=f"Aulas claras e listas que preparam bem para a prova, turma {i}."))[0])
        self.assertEqual(statuses[:limit], [201] * limit)
        self.assertEqual(statuses[-1], 429)

    def test_token_is_hashed_with_secret(self):
        self.assertNotEqual(app.token_hash("abc"), "abc")
        self.assertEqual(app.token_hash("abc"), app.token_hash("abc"))
        self.assertEqual(len(app.token_hash("abc")), 64)


class SyncParserTest(unittest.TestCase):
    """O site do DCC mudou de formato várias vezes; estes casos vêm das páginas reais."""

    def test_schedule_formats(self):
        p = sync_dcc.parse_schedule
        self.assertEqual(p("Seg 17:00 – 18:40\nQua 17:00 – 18:40"), [{"day": 1, "start": "17:00", "end": "18:40"}, {"day": 3, "start": "17:00", "end": "18:40"}])
        self.assertEqual(p("2a e 4a – 07:30/09:10"), [{"day": 1, "start": "07:30", "end": "09:10"}, {"day": 3, "start": "07:30", "end": "09:10"}])
        self.assertEqual(p("3a – 19:00/20:40 e 5a – 20:55/22:35"), [{"day": 2, "start": "19:00", "end": "20:40"}, {"day": 4, "start": "20:55", "end": "22:35"}])
        self.assertEqual(p("14:55 16:35 (Seg) 14:55 16:35 (Qua)"), [{"day": 1, "start": "14:55", "end": "16:35"}, {"day": 3, "start": "14:55", "end": "16:35"}])
        self.assertEqual(p("a definir"), [])

    def test_codes_and_topics(self):
        self.assertEqual(sync_dcc.parse_codes("DCC030/049/831"), ["DCC030", "DCC049", "DCC831"])
        self.assertEqual(sync_dcc.parse_codes("DCC001/DCC208"), ["DCC001", "DCC208"])
        self.assertEqual(sync_dcc.parse_codes("Código"), [])
        cid, name, kind = sync_dcc.course_identity(["DCC030", "DCC049", "DCC831"], "TCC/TSI/TECC: Static Program Analysis (em inglês)")
        self.assertEqual((cid, name, kind), ("TOP-static-program-analysis", "Static Program Analysis", "topico"))
        cid, name, kind = sync_dcc.course_identity(["DCC049"], "TOPICOS EM SISTEMAS DE INFORMACAO -Métodos Formais")
        self.assertEqual((cid, kind), ("TOP-metodos-formais", "topico"))
        self.assertEqual(sync_dcc.course_identity(["DCC206"], "ALGORITMOS I"), ("DCC206", "Algoritmos I", "regular"))
        self.assertEqual(sync_dcc.tidy_course("PROGRAMACAO E DESENVOLVIMENTO DE SOFTWARE II"), "Programação e Desenvolvimento de Software II")

    def test_name_resolution(self):
        profs = {s: {"slug": s, "name": n, "status": st} for s, n, st in [
            ("frederico-ferreira-campos-filho", "Frederico Ferreira Campos Filho", "ativo"),
            ("frederico-gadelha-guimaraes", "Frederico Gadelha Guimarães", "ativo"),
            ("luiz-filipe-menezes-vieira", "Luiz Filipe Menezes Vieira", "ativo"),
            ("clodoveu-davis", "Clodoveu Davis", "ativo"),
            ("joao-guilherme-maia-de-menezes", "João Guilherme Maia de Menezes", "ativo"),
            ("loic-pascal-gilles-cerf", "Loïc Pascal Gilles Cerf", "ativo"),
        ]}
        r = sync_dcc.Resolver(profs)
        self.assertEqual(r.resolve("DCC001", "J. Guilherme"), "joao-guilherme-maia-de-menezes")
        self.assertEqual(r.resolve("DCC001", "Luis Filipe"), "luiz-filipe-menezes-vieira")
        self.assertEqual(r.resolve("DCC001", "Loic Pascal Gilles Cerf"), "loic-pascal-gilles-cerf")
        self.assertEqual(r.resolve("DCC001", "Clodoveu Augusto Davis Junior"), "clodoveu-davis")
        self.assertIsNone(r.resolve("DCC034", "Frederico"), "ambíguo sem contexto")
        r.learn("DCC034", "FREDERICO GADELHA GUIMARAES")
        self.assertEqual(r.resolve("DCC034", "Frederico"), "frederico-gadelha-guimaraes", "o histórico da disciplina desfaz a ambiguidade")
        self.assertIsNone(r.resolve("DCC034", "Prof. Substituto 3"))

    def test_offering_table_by_header(self):
        html = """<table><tr><td>Código</td><td>Turma</td><td>Disciplina</td><td>Horário</td><td>Sala</td><td>Professor</td></tr>
        <tr><td>DCC639</td><td>TZ</td><td>ÁLGEBRA LINEAR COMPUTACIONAL</td><td>17:00 18:40 (Ter) 17:00 18:40 (Qui)</td><td>DCC-2008</td><td>ALEXANDRE SALLES DA CUNHA</td></tr></table>"""
        rows = sync_dcc.parse_offerings(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["codes"], ["DCC639"])
        self.assertEqual(rows[0]["class"], "TZ")
        self.assertEqual(rows[0]["teachers"], ["ALEXANDRE SALLES DA CUNHA"])
        self.assertEqual(len(rows[0]["schedule"]), 2)


class ConsolidateTest(unittest.TestCase):
    def test_versions_and_topics_merge_under_newest_code(self):
        courses = {
            "DCC205": {"id": "DCC205", "codes": ["DCC205"], "name": "Estruturas de Dados", "kind": "regular", "terms": ["2024.1", "2025.1"]},
            "DCC221": {"id": "DCC221", "codes": ["DCC221"], "name": "Estruturas de Dados", "kind": "regular", "terms": ["2025.1"]},
            "TOP-teste-de-software": {"id": "TOP-teste-de-software", "codes": ["DCC030"], "name": "Teste de Software", "kind": "topico", "terms": ["2024.1"]},
            "DCC089": {"id": "DCC089", "codes": ["DCC089"], "name": "Teste de Software", "kind": "regular", "terms": ["2025.1"]},
            "TOP-redes-neurais": {"id": "TOP-redes-neurais", "codes": ["DCC030"], "name": "Redes Neurais", "kind": "topico", "terms": ["2025.1"]},
        }
        slot = [{"day": 2, "start": "14:55", "end": "16:35"}]
        offerings = [
            {"term": "2024.1", "course": "DCC205", "codes": ["DCC205"], "professors": ["w"], "schedule": slot},
            {"term": "2025.1", "course": "DCC205", "codes": ["DCC205"], "professors": ["w"], "schedule": slot},
            {"term": "2025.1", "course": "DCC221", "codes": ["DCC221"], "professors": ["w"], "schedule": slot},  # mesma turma, dois códigos
            {"term": "2025.1", "course": "DCC221", "codes": ["DCC221"], "professors": ["m"], "schedule": slot},
            {"term": "2024.1", "course": "TOP-teste-de-software", "codes": ["DCC030"], "professors": ["e"], "schedule": []},
            {"term": "2025.1", "course": "DCC089", "codes": ["DCC089"], "professors": ["e"], "schedule": []},
            {"term": "2025.1", "course": "TOP-redes-neurais", "codes": ["DCC030"], "professors": ["x"], "schedule": []},
        ]
        merged, offs = sync_dcc.consolidate(courses, offerings)
        self.assertEqual(set(merged), {"DCC221", "DCC089", "TOP-redes-neurais"})
        self.assertEqual(merged["DCC221"]["codes"], ["DCC221", "DCC205"])
        self.assertEqual(merged["DCC221"]["aliases"], ["DCC205"])
        self.assertEqual(merged["DCC089"]["codes"], ["DCC089"], "código genérico de tópico não entra")
        self.assertEqual(merged["DCC089"]["kind"], "regular")
        ed_2025 = [o for o in offs if o["course"] == "DCC221" and o["term"] == "2025.1"]
        self.assertEqual(len(ed_2025), 2)
        self.assertEqual(sorted(next(o for o in ed_2025 if o["professors"] == ["w"])["codes"]), ["DCC205", "DCC221"])
        again, offs2 = sync_dcc.consolidate(merged, offs)
        self.assertEqual((again, offs2), (merged, offs), "idempotente")


class NameNormalizationTest(unittest.TestCase):
    def test_spelling_variants_match_but_versions_and_letters_do_not(self):
        k = sync_dcc.name_key
        self.assertEqual(k("Introdução a Bancos de Dados"), k("Introdução a Banco de Dados"))
        self.assertEqual(k("Introdução à Ciência de Dados"), k("Introdução à Ciência dos Dados"))
        self.assertEqual(k("Information Theroy"), k("Information Theory"))
        self.assertEqual(k("TOPICOS ESPECIAIS EM INFORMATICA II – Introdução à Inteligência Artificial"), k("Introdução à Inteligência Artificial"))
        self.assertEqual(k("TOP. ESP. EM INFORMATICA II – Fundamentos de Sistemas Paralelos e Distribuídos"), k("Fundamentos de Sistemas Paralelos e Distribuídos"))
        self.assertNotEqual(k("Algoritmos I"), k("Algoritmos II"))
        self.assertNotEqual(k("Aprendizado de Máquina A"), k("Aprendizado de Máquina"))
        self.assertNotEqual(k("Aprendizado de Máquina A"), k("Aprendizado de Máquina B"))
        self.assertEqual(sync_dcc.fix_course_name("Fundamentos Estatíticos de Ciência dos Dados B"), "Fundamentos Estatísticos de Ciência dos Dados B")

    def test_surname_must_be_compatible(self):
        sig, ok = sync_dcc.sig, sync_dcc.surname_compatible
        self.assertFalse(ok(sig("Ana Paula de Carvalho"), sig("Ana Paula Couto da Silva")), "outra pessoa, mesmo prenome")
        for raw, full in [("Hector Ignacio Azpurua Perez Imaz", "Héctor Ignacio Azpúrua"), ("Clodoveu Augusto Davis Junior", "Clodoveu Davis"),
                          ("Eduardo Magno Lages Fiugueiredo", "Eduardo Figueiredo"), ("Jussara Marques de Almeida Gonçalves", "Jussara Marques de Almeida")]:
            self.assertTrue(ok(sig(raw), sig(full)), raw)

    def test_substitutes_page_and_placeholders(self):
        page = ("<h2 class='x'>2º semestre de 2025</h2><p>Amanda Damasceno Santana<br>Roselene Henrique Pereira Costa</p>"
                "<h2>2º semestre de 2024</h2><ul><li>Wagner Cipriano da Silva</li></ul><h2>Pesquisar por</h2>")
        self.assertEqual(sync_dcc.parse_substitutes(page), [("2025.2", "Amanda Damasceno Santana"), ("2025.2", "Roselene Henrique Pereira Costa"),
                                                            ("2024.2", "Wagner Cipriano da Silva")])
        for cell in ("PS", "PS3", "Substituto 4", "Prof. Substituto 3"):
            self.assertEqual(sync_dcc.split_teachers(cell) if cell.startswith("PS") else [sync_dcc.SUBST_LABEL] * bool(sync_dcc.SUBST_ONLY.match(cell)), [sync_dcc.SUBST_LABEL])

    def test_area_stems(self):
        self.assertTrue(sync_dcc.stems("Cálculo Numérico") & sync_dcc.stems("Álgebra Linear Computacional Análise Numérica"))
        self.assertFalse(sync_dcc.stems("Cálculo Numérico") & sync_dcc.stems("Aprendizado de Máquina Ciência de Dados"))
        self.assertFalse(sync_dcc.stems("Introdução à Ciência dos Dados") & sync_dcc.stems("Ciência de Dados"), "termos genéricos não decidem")

    def test_curated_equivalence_from_curriculum(self):
        courses = {
            "DCC010": {"id": "DCC010", "codes": ["DCC010"], "name": "Compiladores", "kind": "regular", "terms": ["2024.1"]},
            "DCC053": {"id": "DCC053", "codes": ["DCC053"], "name": "Compiladores I", "kind": "regular", "terms": ["2025.1"]},
        }
        offerings = [{"term": "2024.1", "course": "DCC010", "codes": ["DCC010"], "professors": ["r"], "schedule": []},
                     {"term": "2025.1", "course": "DCC053", "codes": ["DCC053"], "professors": ["r"], "schedule": []}]
        merged, offs = sync_dcc.consolidate(courses, offerings)
        self.assertEqual(list(merged), ["DCC053"])
        self.assertEqual(merged["DCC053"]["aliases"], ["DCC010"])
        self.assertEqual({o["course"] for o in offs}, {"DCC053"})


class ProvasParserTest(unittest.TestCase):
    def test_folder_names(self):
        m = sync_provas.CODE.match("(vazio + 🦕) CAD004 - Administração da Produção")
        self.assertEqual((m.group(1), sync_provas.clean_title(m.group(2))), ("CAD004", "Administração da Produção"))
        self.assertEqual(sync_provas.CODE.match("DCC205 - Estruturas de Dados").group(1), "DCC205")
        self.assertIsNone(sync_provas.CODE.match("Corrida CompLeaks_ 2026"))
        self.assertEqual(sync_provas.clean_title("Outros e Antigo Compleaks 🦕"), "Outros e Antigo Compleaks")


class SigaaImportTest(unittest.TestCase):
    def test_teacher_names_without_registration_numbers(self):
        text = "JOAO GUILHERME MAIA DE MENEZES, 310581, 60 ADRIANO CESAR MACHADO PEREIRA, 24385X, 30"
        self.assertEqual(import_sigaa.TEACHER.findall(text), ["JOAO GUILHERME MAIA DE MENEZES", "ADRIANO CESAR MACHADO PEREIRA"])

    def test_cross_listed_rows_merge_by_subject(self):
        rows = [
            {"code": "DCC030", "title": "TOPICOS EM CIENCIA DA COMPUTACAO - Robótica Móvel", "class": "TM3", "schedule_text": "13:00 14:40 (Ter)", "teachers": ["D M"]},
            {"code": "DCC071", "title": "TÓPICOS ESPECIAIS EM INFORMÁTICA IV - Robótica Móvel", "class": "TL17", "schedule_text": "13:00 14:40 (Ter)", "teachers": ["D M"]},
            {"code": "DCC042", "title": "ROBÓTICA MÓVEL", "class": "TL2", "schedule_text": "13:00 14:40 (Ter)", "teachers": ["D M"]},
            {"code": "DCC001", "title": "PROGRAMACAO DE COMPUTADORES", "class": "TB", "schedule_text": "13:00 14:40 (Ter)", "teachers": ["D M"]},
        ]
        merged = import_sigaa.merge_cross_listed(rows, sync_dcc.name_key)
        self.assertEqual([m["codes"] for m in merged], [["DCC030", "DCC071", "DCC042"], ["DCC001"]], "outra disciplina no mesmo horário não junta")
        self.assertEqual(merged[0]["classes"], ["TM3", "TL17", "TL2"])


class OportunidadesParserTest(unittest.TestCase):
    LISTING = """<h2>Chamadas abertas</h2>
      <article class="elementor-post oportunidade category-ativo category-iniciacao-cientifica-vaga category-inteligencia-artificial category-verlab">
        <h3 class="elementor-post__title"><a href="/oportunidade/ic-ia/">Bolsa de IC &#8211; IA</a></h3>
        <div class="elementor-post__excerpt"><p>Até a vaga ser preenchida</p></div></article>
      <h2>Chamadas fechadas</h2>
      <article class="elementor-post oportunidade category-concluido category-estagio-pt-vaga">
        <h3 class="elementor-post__title"><a href="/oportunidade/estagio/">Estágio – Graduandos</a></h3></article>"""
    DETAIL = """<p>Busca</p><h1>Bolsa de IC – IA</h1>
      <p>O laboratório está selecionando um <strong>estudante de graduação</strong> para atuar em um projeto de pesquisa em aprendizado de máquina com o professor Renato Vimieiro.</p>
      <h3>Valor da Bolsa</h3><p><strong>Graduandos:</strong> R$ 1.200,00</p><p>Dedicação de 20 horas semanais.</p>
      <p>Início divulgação</p><p>: 10/02/2026</p><p>Link para candidatura</p><p>: https://forms.gle/abc</p>
      <p>O Departamento</p><p>Cursos</p>"""

    def test_listing_and_detail(self):
        items = sync_oportunidades.parse_listing(self.LISTING)
        self.assertEqual([(i["slug"], i["open"]) for i in items], [("ic-ia", True), ("estagio", False)])
        self.assertEqual(items[0]["title"], "Bolsa de IC – IA")
        d = sync_oportunidades.parse_detail(self.DETAIL, items[0]["title"])
        self.assertTrue(d["summary"].startswith("O laboratório está selecionando um estudante de graduação"), d["summary"])
        self.assertEqual(d["stipends"], [{"who": "Graduandos", "value": "R$\u00a01.200,00"}])
        self.assertEqual((d["hours"], d["published"], d["apply"]), ([20], "2026-02-10", "https://forms.gle/abc"))
        built = sync_oportunidades.build(items, {"ic-ia": d}, [{"slug": "renato-vimieiro", "name": "Renato Vimieiro", "status": "ativo"},
                                                               {"slug": "renato-ferreira", "name": "Renato Antônio Celso Ferreira", "status": "ativo"}])
        ic = built[0]
        self.assertEqual((ic["types"], ic["areas"], ic["groups"], ic["audiences"]), (["Iniciação científica"], ["Inteligência artificial"], ["VeRLab"], ["Graduação"]))
        self.assertEqual(ic["professors"], ["renato-vimieiro"], "só quem é citado pelo nome")


class RealCatalogTest(unittest.TestCase):
    """Sanidade do data/catalog.json versionado."""

    def test_catalog_consistency(self):
        path = ROOT / "data" / "catalog.json"
        if not path.exists():
            self.skipTest("catálogo não gerado")
        cat = json.loads(path.read_text())
        slugs = {p["slug"] for p in cat["professors"]}
        courses = {c["id"] for c in cat["courses"]}
        self.assertGreater(len(slugs), 60)
        self.assertGreater(len(courses), 50)
        self.assertEqual(len(slugs), len(cat["professors"]), "slugs únicos")
        for o in cat["offerings"]:
            self.assertIn(o["course"], courses)
            self.assertTrue(set(o["professors"]) <= slugs)
            self.assertIn(o["term"], cat["terms"])

    def test_catalog_has_one_page_per_subject(self):
        path = ROOT / "data" / "catalog.json"
        if not path.exists():
            self.skipTest("catálogo não gerado")
        cat = json.loads(path.read_text())
        keys = [sync_dcc.name_key(c["name"]) for c in cat["courses"]]
        dups = {k for k in keys if keys.count(k) > 1}
        self.assertFalse(dups, "disciplinas duplicadas por versão/grafia")
        by_id = {c["id"]: c for c in cat["courses"]}
        self.assertEqual([c["id"] for c in cat["courses"] if c["name"] == "Estruturas de Dados"], ["DCC221"])
        self.assertIn("DCC212", by_id, "código da grade 2024 (CANONICAL) é o canônico de Introdução à Ciência dos Dados")
        self.assertIn("DCC205", by_id["DCC221"]["aliases"])
        wagner = {(o["term"], o["course"]) for o in cat["offerings"] if "wagner-meira-junior" in o["professors"]}
        self.assertIn(("2026.1", "DCC221"), wagner)
        if cat.get("termSources", {}).get("2026.2") == "SIGAA":
            self.assertIn(("2026.2", "DCC221"), wagner, "SIGAA 2026.2: Wagner em Estruturas de Dados")
        sigaa = ROOT / "data" / "ofertas-sigaa"
        for f in sigaa.glob("*.json"):
            self.assertNotRegex(f.read_text(), r"\b\d{5}[\dX]\b", f"{f.name}: nº de inscrição não pode ser gravado")
        # nomes ambíguos resolvidos pelo histórico / área de pesquisa; substituto nunca vira o titular
        bio_id = next(c["id"] for c in cat["courses"] if "DCC852" in [c["id"], *c.get("aliases", [])])  # canônico muda com o código mais recente
        bio2 = [o for o in cat["offerings"] if o["course"] == bio_id and o["term"] == "2025.2"]
        self.assertTrue(bio2 and all(o["professors"] == ["marcos-augusto-dos-santos"] for o in bio2))
        profs = {p["slug"]: p for p in cat["professors"]}
        archive_offs = json.loads((ROOT / "data" / "catalog-arquivo.json").read_text())["offerings"]
        every = cat["offerings"] + archive_offs
        # "Wagner Subst" (2025.1) é o substituto Wagner Cipriano da Silva, nunca o Wagner Meira
        subst = [o for o in every if o["term"] == "2025.1" and o["course"] == "DCC219" and "wagner-cipriano-da-silva" in o["professors"]]
        self.assertEqual(len(subst), 2)
        self.assertEqual(profs["wagner-cipriano-da-silva"]["status"], "substituto")
        self.assertEqual(profs["amanda-damasceno-santana"]["status"], "substituto")
        # "ANA PAULA DE CARVALHO" (2020.1) não é a Ana Paula Couto da Silva
        ap = [o for o in every if o["term"] == "2020.1" and o["course"] == "DCC001" and o.get("class") in ("TA", "TE")]
        self.assertTrue(ap and all(o["professors"] == ["ana-paula-de-carvalho"] for o in ap))
        self.assertFalse([c for c in cat["courses"] if c["name"].startswith("Algoritmos e Estruturas de Dados")], "AEDS fora do catálogo")
        self.assertFalse([o for o in cat["offerings"] if "DCC003" in o["codes"]])
        archive = json.loads((ROOT / "data" / "catalog-arquivo.json").read_text())
        old = {c["id"]: c for c in archive["courses"]}
        self.assertEqual(old["DCC003"].get("successor"), "DCC221")
        for cid in ("DCC050", "DCC044"):
            self.assertNotIn(cid, by_id)
            self.assertEqual((old[cid].get("retired"), old[cid].get("successor")), (True, "DCC096"))
        recent = set(cat["terms"][-4:])
        for c in cat["courses"]:
            self.assertFalse(c.get("retired"), c["id"])
            self.assertTrue(recent & set(c["terms"]), f"{c['id']} sem oferta recente no catálogo")


if __name__ == "__main__":
    unittest.main()

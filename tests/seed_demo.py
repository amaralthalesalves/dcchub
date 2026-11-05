#!/usr/bin/env python3
"""Cria um banco de DEMONSTRAÇÃO com avaliações fictícias (nunca use em produção).

    python3 tests/seed_demo.py /tmp/demo.db
    DCCHUB_DB=/tmp/demo.db python3 app.py
"""
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/avalie-demo.db").resolve()
if target == (ROOT / "data" / "avaliacoes.db").resolve():
    sys.exit("Recusado: este script não escreve no banco real.")
os.environ["AVALIA_DCC_DB"] = str(target)
os.environ.setdefault("AVALIA_DCC_SECRET", "demo")
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

app.init_db()
catalog = json.loads((ROOT / "data" / "catalog.json").read_text())
pairs = sorted({(p, o["course"], o["term"]) for o in catalog["offerings"] for p in o["professors"] if o["term"] >= "2023.1"})
rng = random.Random(4)
comments = [
    "Aulas muito bem organizadas e o material no Moodle ajuda bastante. As listas são essenciais para a prova.",
    "Conteúdo pesado, mas a explicação é calma e as dúvidas por e-mail são respondidas rapidamente.",
    "Os trabalhos práticos tomaram muito tempo, porém aprendi muito. Recomendo começar cedo.",
    "Provas bem diferentes das listas, senti falta de exemplos resolvidos em aula.",
    "Excelente didática, uma das melhores disciplinas do curso. Faria de novo sem pensar.",
    "Aulas um pouco confusas no começo, mas melhoraram ao longo do semestre. Critérios das provas foram claros.",
]
clamp = lambda v: max(1, min(5, v))  # noqa: E731
with app.connect() as conn:
    for i in range(int(os.environ.get("N", 150))):
        prof, course, term = rng.choice(pairs[:80])
        overall = rng.randint(2, 5)
        comment = rng.choice(comments)
        conn.execute(  # id aleatório e chave do comentário, como o app grava
            "INSERT OR IGNORE INTO reviews (id, professor, course, term, overall, didactics, fairness, difficulty, passing, workload, would_retake,"
            " grade, tags, comment, comment_key, status, reviewer_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (app.new_review_id(lambda n: conn.execute("SELECT 1 FROM reviews WHERE id=?", (n,)).fetchone() is not None),
             prof, course, term, overall, clamp(overall + rng.randint(-1, 1)), clamp(overall + rng.randint(-1, 1)), rng.randint(1, 5),
             rng.choice([None, *range(1, 6)]),  # vazio = avaliação de antes de separar aprender/passar
             rng.randint(1, 5), rng.choice([1, 1, 0, None]), rng.choice(["A", "B", "C", "D", None]),
             json.dumps(rng.sample(app.ALL_TAGS, rng.randint(0, 3)), ensure_ascii=False), comment, app.comment_key(comment), "published",
             f"demo-{i}", app.review_day(time.time() - rng.randint(0, 90 * 86400))))
print(f"Banco de demonstração em {target}")

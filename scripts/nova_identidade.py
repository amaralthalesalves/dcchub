#!/usr/bin/env python3
"""Devolve avaliações a quem as escreveu depois de uma troca de geração da identidade anônima (DCCHUB_IDENTITY_EPOCH).

Trocar a geração desliga todos os cookies antigos: as avaliações continuam publicadas, mas ninguém mais consegue
editá-las. Se a autora pedir à moderação (e a moderação tiver certeza de que é ela), este script liga as avaliações
indicadas a uma identidade NOVA e imprime o código de recuperação dela. A pessoa cola o código em "Minhas avaliações >
Tenho um código" no próprio navegador; nenhum outro navegador fica com a identidade.

Roda dentro do container (mesmo banco e mesma chave secreta do site):
    docker compose exec -T app python3 scripts/nova_identidade.py 6360828466624800 [outro_id ...]

Entregue o código só à autora, por um canal privado: quem tiver o código edita e apaga as avaliações.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402


def rekey(review_ids: list[int]) -> str:
    token = app.new_identity_token()
    with app.connect() as conn:
        found = [r["id"] for r in conn.execute(
            f"SELECT id FROM reviews WHERE id IN ({','.join('?' * len(review_ids))})", review_ids)]
        missing = sorted(set(review_ids) - set(found))
        if missing:
            raise SystemExit(f"Avaliação não encontrada: {', '.join(map(str, missing))}")
        conn.executemany("UPDATE reviews SET reviewer_hash=? WHERE id=?", [(app.token_hash(token), rid) for rid in found])
    return token


def main(argv: list[str]) -> None:
    if not argv or not all(a.isdigit() for a in argv):
        raise SystemExit(__doc__)
    app.init_db()
    token = rekey([int(a) for a in argv])
    print(f"{len(argv)} avaliação(ões) ligada(s) a uma identidade nova. Código de recuperação (entregue só à autora):")
    print(token)


if __name__ == "__main__":
    main(sys.argv[1:])

"""Avisos por e-mail para a moderação quando algo entra na fila de revisão.

Quando avisa: uma avaliação nova ou editada é segurada pelo filtro, uma publicada sai do ar por denúncias, ou chega
feedback sobre o site.

Sem encher a caixa: o primeiro aviso espera alguns minutos (agrupa o que chegar junto) e depois vem no máximo um a cada
30 minutos. O resumo é montado na hora do envio, a partir do banco: se a moderação já resolveu tudo, nada é enviado.

O e-mail traz só contagens e motivos — nunca docente, disciplina, texto (nem o do feedback) ou qualquer dado de quem
escreveu. Uma caixa de
e-mail não é lugar de guardar acusações contra pessoas nomeadas; o conteúdo se lê no painel.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import time
from typing import Callable

import email_login

FIRST_DELAY = 2 * 60      # agrupa o que chegar junto (ex.: várias denúncias em sequência)
MIN_INTERVAL = 30 * 60    # no máximo um e-mail a cada 30 min


def summary(conn: sqlite3.Connection) -> dict:
    """Contagens da fila, sem identificar avaliações."""
    held = conn.execute("SELECT held_reason FROM reviews WHERE status='held'").fetchall()
    by_reports = sum(1 for (r,) in held if (r or "").startswith("denúncias"))
    reported = conn.execute("""SELECT COUNT(DISTINCT p.review_id) FROM reports p JOIN reviews r ON r.id=p.review_id
                               WHERE p.resolved=0 AND r.status='published'""").fetchone()[0]
    feedback = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    return {"held": len(held), "filter": len(held) - by_reports, "reports": by_reports, "reported": reported,
            "feedback": feedback}


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def render(counts: dict, panel_url: str) -> tuple[str, str]:
    parts = []
    if counts["held"]:
        parts.append(f"{plural(counts['held'], 'avaliação aguardando', 'avaliações aguardando')} revisão")
    if counts.get("feedback"):
        parts.append(f"{plural(counts['feedback'], 'feedback', 'feedbacks')} para ler")
    subject = "DCCHub: " + (" e ".join(parts) or "fila de moderação")
    lines = ["Na fila de moderação agora:", ""]
    if counts["filter"]:
        lines.append(f"- {plural(counts['filter'], 'segurada', 'seguradas')} pelo filtro (possível acusação de discriminação)")
    if counts["reports"]:
        lines.append(f"- {plural(counts['reports'], 'retirada', 'retiradas')} do ar por denúncias de redes diferentes")
    if counts["reported"]:
        lines.append(f"- {plural(counts['reported'], 'publicada', 'publicadas')} com denúncias pendentes (ainda no ar)")
    if counts.get("feedback"):
        lines.append(f"- {plural(counts['feedback'], 'feedback anônimo', 'feedbacks anônimos')} sobre o site")
    lines += ["", f"Abra o painel para revisar: {panel_url}", "",
              "Este aviso não traz docente, disciplina nem texto: o conteúdo fica só no painel.", "", "— DCCHub"]
    return subject, "\n".join(lines) + "\n"


class Notifier:
    def __init__(self, cfg: email_login.Config, recipients: list[str], connect: Callable[[], sqlite3.Connection],
                 panel_url: str) -> None:
        self.cfg, self.recipients, self.connect, self.panel_url = cfg, recipients, connect, panel_url
        self.lock = threading.Lock()
        self.timer: threading.Timer | None = None
        self.last_sent = 0.0
        self.send = self._send  # substituível nos testes

    @property
    def enabled(self) -> bool:
        return bool(self.recipients)

    def held(self) -> None:
        """Algo entrou na fila (avaliação segurada ou feedback): agenda um aviso, se ainda não houver um agendado."""
        if not self.enabled:
            return
        with self.lock:
            if self.timer:
                return
            delay = max(FIRST_DELAY, self.last_sent + MIN_INTERVAL - time.monotonic())
            self.timer = threading.Timer(delay, self.flush)
            self.timer.daemon = True
            self.timer.start()

    def flush(self) -> None:
        with self.lock:
            self.timer = None
        try:
            with self.connect() as conn:
                counts = summary(conn)
            if not counts["held"] and not counts["feedback"]:
                return  # a moderação já resolveu
            with self.lock:
                self.last_sent = time.monotonic()
            self.send(*render(counts, self.panel_url))
        except Exception as exc:  # noqa: BLE001
            print(f"[moderação] falha ao avisar por e-mail: {email_login.describe_error(exc)}", file=sys.stderr, flush=True)

    def _send(self, subject: str, body: str) -> None:
        if not self.cfg.smtp_host:
            print(f"[moderação] SMTP não configurado; aviso (desenvolvimento): {subject}", file=sys.stderr, flush=True)
            return
        email_login.smtp_send(self.cfg, email_login.text_message(self.cfg, self.recipients, subject, body))

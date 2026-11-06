#!/usr/bin/env python3
"""Manda um backup JÁ CRIPTOGRAFADO por e-mail (cópia fora da VM). Usa o mesmo SMTP do acesso por e-mail.

Roda dentro do container (que já tem DCCHUB_SMTP_* e DCCHUB_BACKUP_EMAIL), lendo o arquivo da entrada padrão:
    docker compose exec -T app python3 scripts/send_backup.py dcchub-20261106-0300.tar.gz.enc < arquivo.enc

Quem criptografa é o deploy/backup.sh, na VM, com DCCHUB_BACKUP_PASSPHRASE (openssl AES-256, PBKDF2): a caixa de
e-mail só vê um arquivo cifrado. O banco tem textos de avaliações e o hash de quem escreveu, e a chave secreta junto
permitiria ligar códigos de recuperação a avaliações — nada disso pode ficar legível num e-mail.
"""
from __future__ import annotations

import os
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import email_login  # noqa: E402

# limite do Gmail é 25 MB por mensagem, e o anexo cresce ~33% em base64
MAX_BYTES = 18 * 1024 * 1024


def env(name: str) -> str:
    return os.environ.get(f"DCCHUB_{name}", "")


def build(cfg: email_login.Config, to: list[str], name: str, data: bytes) -> EmailMessage:
    msg = email_login.text_message(
        cfg, to, f"DCCHub: backup {name}",
        "Backup diário do DCCHub (banco + chave secreta), criptografado com a senha de backup.\n\n"
        "Para restaurar (veja docs/deploy.md, \"Restaurando um backup\"):\n"
        f"    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:SENHA -in {name} | tar xz\n\n"
        "— DCCHub\n")
    msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].endswith(".enc") or "/" in sys.argv[1]:
        print("uso: send_backup.py NOME.enc < arquivo", file=sys.stderr)
        return 2
    name = sys.argv[1]
    to = [a.strip() for a in env("BACKUP_EMAIL").split(",") if a.strip()]
    cfg = email_login.Config(lambda n: os.environ.get(f"DCCHUB_{n}"))
    if not to or not cfg.smtp_host:
        print("backup externo desligado: defina DCCHUB_BACKUP_EMAIL e o SMTP", file=sys.stderr)
        return 1
    data = sys.stdin.buffer.read()
    if not data.startswith(b"Salted__"):  # cabeçalho do openssl enc: nunca mandar um arquivo em claro
        print("recusado: o arquivo não parece criptografado pelo openssl", file=sys.stderr)
        return 1
    if len(data) > MAX_BYTES:
        print(f"recusado: {len(data) // 1024 // 1024} MB passa do limite do e-mail; troque por rclone/bucket", file=sys.stderr)
        return 1
    try:
        email_login.smtp_send(cfg, build(cfg, to, name, data))
    except Exception as exc:  # noqa: BLE001
        print(f"falha ao enviar o backup: {email_login.describe_error(exc)}", file=sys.stderr)
        return 1
    print(f"backup externo enviado: {name} ({len(data) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Acesso por e-mail institucional (@ufmg.br) com link mágico — sem conta e sem guardar o e-mail.

Fluxo:
  1. a pessoa digita o e-mail; se for exatamente @ufmg.br, o servidor envia um link de uso único (15 min);
  2. o link abre a SPA (#/entrar?t=…), que troca o token por um cookie de acesso de 180 dias.

O que NÃO existe aqui, de propósito:
  - o e-mail não vai para o banco nem para o log; só passa pela memória durante o envio. O limite de envios por
    endereço usa um HMAC do e-mail que fica só em memória e some com a janela do limite;
  - o token do link e o cookie de acesso não carregam o e-mail nem nada derivado dele;
  - o cookie de acesso é independente da identidade anônima de quem avalia (cookie `avalia_dcc`). Saber quem pediu
    um link não diz nada sobre quais avaliações essa pessoa escreveu.

`@dcc.ufmg.br` (e qualquer outro subdomínio) é recusado: é o domínio que os docentes do DCC divulgam. Assim, digitar
o endereço público de um docente não dispara e-mail nenhum para ele.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import smtplib
import ssl
import sys
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

LINK_TTL = 15 * 60
ACCESS_TTL = 180 * 86400
EMAIL = re.compile(r"[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?@([a-z0-9.-]+)")
TOKEN = re.compile(r"[a-z]\d\.\d{10}\.[A-Za-z0-9_-]{22}\.[0-9a-f]{40}")


class Config:
    def __init__(self, env) -> None:
        self.enabled = (env("EMAIL_LOGIN") or "") == "1"
        self.domain = (env("EMAIL_DOMAIN") or "ufmg.br").lower().strip()
        self.public_url = (env("PUBLIC_URL") or "").rstrip("/")
        self.epoch = env("EMAIL_EPOCH") or "1"  # trocar revoga todos os acessos feitos por e-mail
        self.smtp_host = env("SMTP_HOST") or ""
        self.smtp_port = int(env("SMTP_PORT") or 587)
        self.smtp_user = env("SMTP_USER") or ""
        self.smtp_password = env("SMTP_PASSWORD") or ""
        self.smtp_from = env("SMTP_FROM") or ""
        self.smtp_tls = (env("SMTP_TLS") or ("ssl" if self.smtp_port == 465 else "starttls")).lower()


def normalize(address: object) -> str | None:
    """E-mail em minúsculas se tiver formato válido; None caso contrário."""
    if not isinstance(address, str):
        return None
    address = address.strip().lower()
    if len(address) > 254 or not EMAIL.fullmatch(address) or ".." in address:
        return None
    return address


def allowed(address: str, domain: str) -> bool:
    """Só o domínio exato (ufmg.br): subdomínios como dcc.ufmg.br ficam de fora."""
    return address.rsplit("@", 1)[-1] == domain


def address_key(secret: str, address: str) -> str:
    """Chave do limite de envios por endereço (só em memória)."""
    return hmac.new(secret.encode(), f"limite-email:{address}".encode(), hashlib.sha256).hexdigest()[:32]


def _sign(secret: str, *parts: str) -> str:
    return hmac.new(secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()[:40]


def _token(kind: str, secret: str, ttl: int, *scope: str) -> str:
    exp = str(int(time.time()) + ttl)
    nonce = secrets.token_urlsafe(16)
    return f"{kind}.{exp}.{nonce}.{_sign(secret, kind, *scope, exp, nonce)}"


def _check(kind: str, secret: str, token: object, *scope: str) -> str | None:
    """Nonce do token se a assinatura confere e não expirou."""
    if not isinstance(token, str) or not TOKEN.fullmatch(token) or not token.startswith(kind + "."):
        return None
    _, exp, nonce, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(secret, kind, *scope, exp, nonce)) or int(exp) < time.time():
        return None
    return nonce


def link_token(secret: str) -> str:
    return _token("l1", secret, LINK_TTL)


def check_link(secret: str, token: object) -> str | None:
    return _check("l1", secret, token)


def access_token(secret: str, epoch: str) -> str:
    return _token("e1", secret, ACCESS_TTL, epoch)


def check_access(secret: str, epoch: str, token: object) -> bool:
    return _check("e1", secret, token, epoch) is not None


def message(cfg: Config, to: str, url: str) -> EmailMessage:
    """E-mail neutro: não fala em avaliações nem em docentes."""
    msg = EmailMessage()
    msg["Subject"] = "Seu link de acesso"
    msg["From"] = cfg.smtp_from or formataddr(("DCCHub", cfg.smtp_user))
    msg["To"] = to
    sender_domain = cfg.smtp_from.rsplit("@", 1)[-1].strip("> ") if "@" in cfg.smtp_from else None
    msg["Message-ID"] = make_msgid(domain=sender_domain or None)
    msg.set_content(
        "Olá!\n\n"
        "Use o link abaixo para entrar:\n\n"
        f"{url}\n\n"
        f"Ele vale por {LINK_TTL // 60} minutos e só funciona uma vez. Depois de entrar, o navegador continua com acesso "
        f"por {ACCESS_TTL // 86400} dias.\n\n"
        "Se você não pediu este link, é só ignorar este e-mail.\n\n"
        "— DCCHub\n"
    )
    return msg


def deliver(cfg: Config, to: str, url: str) -> None:
    """Envia o e-mail (chamado numa thread). Sem SMTP configurado, mostra o link no terminal (desenvolvimento)."""
    if not cfg.smtp_host:
        print(f"[login] SMTP não configurado; link de acesso (desenvolvimento): {url}", file=sys.stderr, flush=True)
        return
    try:
        msg = message(cfg, to, url)
        context = ssl.create_default_context()
        if cfg.smtp_tls == "ssl":
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
            if cfg.smtp_tls == "starttls":
                server.starttls(context=context)
        with server:
            if cfg.smtp_user:
                server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        # sem o endereço no log
        print(f"[login] falha ao enviar e-mail: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def send_async(cfg: Config, to: str, url: str) -> None:
    threading.Thread(target=deliver, args=(cfg, to, url), daemon=True).start()

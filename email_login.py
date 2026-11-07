"""Acesso por e-mail institucional (@ufmg.br) com código de 6 dígitos — sem conta e sem guardar o e-mail.

Fluxo:
  1. a pessoa digita o e-mail; se for exatamente @ufmg.br, o servidor envia um código (30 min) e devolve ao navegador
     um "desafio": token assinado que amarra o código sem contê-lo (HMAC com a chave do servidor);
  2. a pessoa digita o código no mesmo navegador — pode ter lido o e-mail no celular — e o servidor troca desafio +
     código por um cookie de acesso de 180 dias. Uso único, no máximo 5 tentativas por desafio. O navegador guarda os
     últimos 3 desafios: se o e-mail demorar e a pessoa pedir outro, o código que chegar primeiro serve.

Sem a chave do servidor não há como testar códigos fora do ar: o único ataque é tentar online, e as 5 tentativas
dão 5 em 1.000.000 de chance por e-mail enviado (que já é limitado por endereço, por rede e por dia).

O que NÃO existe aqui, de propósito:
  - o e-mail não vai para o banco nem para o log; só passa pela memória durante o envio. O limite de envios por
    endereço usa um HMAC do e-mail que fica só em memória e some com a janela do limite;
  - o desafio e o cookie de acesso não carregam o e-mail nem nada derivado dele;
  - o cookie de acesso é independente da identidade anônima de quem avalia (cookie `avalia_dcc`). Saber quem pediu
    um código não diz nada sobre quais avaliações essa pessoa escreveu.

`@dcc.ufmg.br` (e qualquer outro subdomínio) é recusado: é o domínio que os docentes do DCC divulgam. Assim, digitar
o endereço público de um docente não dispara e-mail nenhum para ele.

Modo docente (opcional, DCCHUB_DOCENTE_LOGIN=1): o e-mail publicado de um docente no site do DCC recebe um código que
dá acesso SÓ à página dele, em versão reduzida. O desafio e o cookie levam o slug do docente (`<token>:<slug>`), que
entra na assinatura — trocar o slug invalida o token.
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

# 30 min, não 10: o correio da UFMG (e o redirecionamento para outras caixas) chega a levar vários minutos. O tempo não
# muda a chance de chute — quem limita isso são as tentativas por código
CODE_TTL = 30 * 60
CODE_TRIES = 5
# códigos pedidos em seguida continuam valendo (até este número): com e-mail lento, a pessoa pede outro e o primeiro chega
CODE_PENDING = 3
ACCESS_TTL = 180 * 86400
DOCENTE_TTL = 30 * 86400
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
EMAIL = re.compile(r"[a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?@([a-z0-9.-]+)")
TOKEN = re.compile(r"[a-z]\d\.\d{10}\.[A-Za-z0-9_-]{22}\.[0-9a-f]{40}")
CODE = re.compile(r"\d{6}")


class Config:
    def __init__(self, env) -> None:
        self.enabled = (env("EMAIL_LOGIN") or "") == "1"
        self.domain = (env("EMAIL_DOMAIN") or "ufmg.br").lower().strip()
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


def new_code(secret: str) -> tuple[str, str]:
    """(código de 6 dígitos para o e-mail, desafio para o navegador). O desafio assina o código mas não o contém."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    return code, _token("c1", secret, CODE_TTL, code)


def new_docente_code(secret: str, slug: str) -> tuple[str, str]:
    """Como new_code, mas o desafio só vale para a página deste docente."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    return code, f"{_token('c2', secret, CODE_TTL, code, slug)}:{slug}"


def _split_slug(token: object) -> tuple[str, str | None]:
    """'<token>:<slug>' → (token, slug); sem slug → (token, None)."""
    if isinstance(token, str) and ":" in token:
        token, slug = token.split(":", 1)
        return token, slug if SLUG.fullmatch(slug) else ""
    return token, None  # type: ignore[return-value]


def challenge_nonce(challenge: object) -> str | None:
    """Nonce de um desafio bem formado (sem checar a assinatura): chave do limite de tentativas."""
    token, slug = _split_slug(challenge)
    kind = "c2." if slug else "c1."
    if isinstance(token, str) and TOKEN.fullmatch(token) and token.startswith(kind):
        return token.split(".")[2]
    return None


def clean_code(code: object) -> str | None:
    """Código digitado sem espaços nem traços ("123 456" → "123456"); None se não tiver 6 dígitos."""
    if not isinstance(code, (str, int)):
        return None
    code = re.sub(r"[\s-]", "", str(code))
    return code if CODE.fullmatch(code) else None


def check_code(secret: str, challenge: object, code: object) -> str | None:
    """Nonce do desafio se o código confere e não expirou."""
    code = clean_code(code)
    return _check("c1", secret, challenge, code) if code else None


def check_any_code(secret: str, challenge: object, code: object) -> tuple[str, str | None] | None:
    """(nonce, slug do docente ou None) se o código confere com um desafio de estudante ou de docente."""
    code = clean_code(code)
    if not code:
        return None
    token, slug = _split_slug(challenge)
    if slug is None:
        nonce = _check("c1", secret, token, code)
        return (nonce, None) if nonce else None
    nonce = _check("c2", secret, token, code, slug) if slug else None
    return (nonce, slug) if nonce else None


def docente_token(secret: str, epoch: str, slug: str) -> str:
    return f"{_token('d1', secret, DOCENTE_TTL, epoch, slug)}:{slug}"


def check_docente(secret: str, epoch: str, token: object) -> str | None:
    """Slug do docente se o cookie de docente é válido."""
    token, slug = _split_slug(token)
    if not slug or not isinstance(token, str) or not token.startswith("d1."):
        return None
    return slug if _check("d1", secret, token, epoch, slug) else None


def access_token(secret: str, epoch: str) -> str:
    return _token("e1", secret, ACCESS_TTL, epoch)


def check_access(secret: str, epoch: str, token: object) -> bool:
    return _check("e1", secret, token, epoch) is not None


def message(cfg: Config, to: str, code: str, access_days: int = ACCESS_TTL // 86400) -> EmailMessage:
    """E-mail neutro: não fala em avaliações nem em docentes. O código vai no assunto para aparecer na notificação."""
    msg = EmailMessage()
    msg["Subject"] = f"{code} é seu código de acesso ao DCCHub"
    msg["From"] = cfg.smtp_from or formataddr(("DCCHub", cfg.smtp_user))
    msg["To"] = to
    sender_domain = cfg.smtp_from.rsplit("@", 1)[-1].strip("> ") if "@" in cfg.smtp_from else None
    msg["Message-ID"] = make_msgid(domain=sender_domain or None)
    msg.set_content(
        "Olá!\n\n"
        "Seu código de acesso é:\n\n"
        f"    {code[:3]} {code[3:]}\n\n"
        f"Digite-o na página onde você pediu o código. Ele vale por {CODE_TTL // 60} minutos e só funciona uma vez. "
        f"Depois de entrar, o navegador continua com acesso por {access_days} dias.\n\n"
        "Ninguém do DCCHub vai pedir este código. Se você não pediu, é só ignorar este e-mail.\n\n"
        "— DCCHub\n"
    )
    return msg


def smtp_send(cfg: Config, msg: EmailMessage) -> None:
    """Envia pelo SMTP configurado. Exceções sobem para quem chamou (que registra sem o endereço)."""
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


def deliver(cfg: Config, to: str, code: str, access_days: int = ACCESS_TTL // 86400) -> None:
    """Envia o e-mail (chamado numa thread). Sem SMTP configurado, mostra o código no terminal (desenvolvimento)."""
    if not cfg.smtp_host:
        print(f"[login] SMTP não configurado; código de acesso (desenvolvimento): {code}", file=sys.stderr, flush=True)
        return
    try:
        smtp_send(cfg, message(cfg, to, code, access_days))
    except Exception as exc:  # noqa: BLE001
        print(f"[login] falha ao enviar e-mail: {describe_error(exc)}", file=sys.stderr, flush=True)


def text_message(cfg: Config, to: list[str], subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_from or formataddr(("DCCHub", cfg.smtp_user))
    msg["To"] = ", ".join(to)
    sender_domain = cfg.smtp_from.rsplit("@", 1)[-1].strip("> ") if "@" in cfg.smtp_from else None
    msg["Message-ID"] = make_msgid(domain=sender_domain or None)
    msg.set_content(body)
    return msg


def describe_error(exc: BaseException) -> str:
    """Erro de envio SEM o texto da exceção: SMTPRecipientsRefused e afins trazem o endereço (e respostas do servidor
    costumam repeti-lo). Fica só o tipo e o código SMTP, o bastante para diagnosticar."""
    code = getattr(exc, "smtp_code", None)
    if code is None and isinstance(exc, smtplib.SMTPRecipientsRefused):
        code = next((c for c, _ in exc.recipients.values()), None)
    return type(exc).__name__ + (f" (código {code})" if code is not None else "")


def send_async(cfg: Config, to: str, code: str, access_days: int = ACCESS_TTL // 86400) -> None:
    threading.Thread(target=deliver, args=(cfg, to, code, access_days), daemon=True).start()

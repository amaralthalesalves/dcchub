"""Testes do acesso por e-mail (email_login.py), incluindo o envio por SMTP contra um servidor falso local.

    python3 -m unittest tests.test_email_login -v
"""
from __future__ import annotations

import contextlib
import io
import socketserver
import sys
import threading
import time
import unittest
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import email_login  # noqa: E402


def config(**values) -> email_login.Config:
    return email_login.Config(lambda name, default=None: values.get(name, default))


class FakeSMTP(socketserver.StreamRequestHandler):
    """SMTP mínimo (sem TLS) que guarda as mensagens recebidas."""
    inbox: list[dict] = []

    def reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        self.reply("220 fake")
        mail = {"rcpt": [], "auth": None}
        while line := self.rfile.readline():
            cmd = line.decode().strip()
            upper = cmd.upper()
            if upper.startswith("EHLO"):
                self.wfile.write(b"250-fake\r\n250 AUTH PLAIN\r\n")
            elif upper.startswith("AUTH PLAIN"):
                mail["auth"] = cmd.split()[-1]
                self.reply("235 ok")
            elif upper.startswith("MAIL FROM"):
                mail["from"] = cmd
                self.reply("250 ok")
            elif upper.startswith("RCPT TO"):
                mail["rcpt"].append(cmd.split(":", 1)[1].strip("<> "))
                self.reply("250 ok")
            elif upper == "DATA":
                self.reply("354 go")
                data = b""
                while (chunk := self.rfile.readline()) != b".\r\n":
                    data += chunk
                mail["data"] = data
                FakeSMTP.inbox.append(mail)
                self.reply("250 queued")
            elif upper == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class EmailLoginTest(unittest.TestCase):
    SECRET = "segredo"

    def test_only_exact_ufmg_domain(self):
        ok = ["aluna@ufmg.br", "a.b-c_d+tag@ufmg.br", " Aluna@UFMG.BR "]
        bad = ["aluna@dcc.ufmg.br", "aluna@icex.ufmg.br", "aluna@ufmg.br.evil.com", "aluna@gmail.com", "aluna@xufmg.br"]
        for addr in ok:
            norm = email_login.normalize(addr)
            self.assertTrue(norm and email_login.allowed(norm, "ufmg.br"), addr)
        for addr in bad:
            norm = email_login.normalize(addr)
            self.assertFalse(norm and email_login.allowed(norm, "ufmg.br"), addr)
        for junk in ("", "sem-arroba", "a@@ufmg.br", ".a@ufmg.br", "a..b@ufmg.br", "a@ufmg.br\nBcc: x@ufmg.br",
                     "ação@ufmg.br", "a" * 300 + "@ufmg.br", None, 3):
            self.assertIsNone(email_login.normalize(junk), repr(junk))

    def test_tokens(self):
        code, challenge = email_login.new_code(self.SECRET)
        self.assertRegex(code, r"^\d{6}$")
        self.assertNotIn(code, challenge.split(".", 1)[1], "o desafio não traz o código")
        self.assertTrue(email_login.check_code(self.SECRET, challenge, code))
        self.assertTrue(email_login.check_code(self.SECRET, challenge, f"{code[:3]} {code[3:]}"), "aceita espaço")
        self.assertTrue(email_login.check_code(self.SECRET, challenge, f"{code[:3]}-{code[3:]}"), "aceita traço")
        self.assertIsNone(email_login.check_code(self.SECRET, challenge, f"{(int(code) + 1) % 10 ** 6:06d}"))
        self.assertIsNone(email_login.check_code("outra", challenge, code))
        for junk in ("", "12345", "1234567", "abcdef", None, 3.5):
            self.assertIsNone(email_login.check_code(self.SECRET, challenge, junk), repr(junk))
        expired = email_login._token("c1", self.SECRET, -1, code)
        self.assertIsNone(email_login.check_code(self.SECRET, expired, code), "expirado")
        self.assertEqual(email_login.challenge_nonce(challenge), challenge.split(".")[2])
        self.assertIsNone(email_login.challenge_nonce("x"))
        access = email_login.access_token(self.SECRET, "1")
        self.assertTrue(email_login.check_access(self.SECRET, "1", access))
        self.assertFalse(email_login.check_access(self.SECRET, "2", access), "trocar a época revoga")
        self.assertFalse(email_login.check_access(self.SECRET, "1", challenge), "desafio não serve como cookie")
        self.assertIsNone(email_login.challenge_nonce(access), "cookie não serve como desafio")
        self.assertNotEqual(email_login.new_code(self.SECRET)[1], challenge)
        codes = {email_login.new_code(self.SECRET)[0] for _ in range(200)}
        self.assertGreater(len(codes), 190, "códigos aleatórios")

    def test_message_is_neutral(self):
        cfg = config(SMTP_FROM="DCCHub <acesso@dcchub.exemplo>")
        msg = email_login.message(cfg, "aluna@ufmg.br", "042917")
        text = (msg.as_string() + msg.get_content()).lower()
        self.assertIn("042 917", msg.get_content())
        self.assertTrue(msg["Subject"].startswith("042917 "), "código no assunto, para aparecer na notificação")
        self.assertNotIn("http", msg.get_content(), "sem links: ninguém aprende a clicar em link de login por e-mail")
        for word in ("avalia", "professor", "docente", "disciplina"):
            self.assertNotIn(word, text, "um print do e-mail não deve dizer do que se trata o site")
        self.assertEqual(msg["To"], "aluna@ufmg.br")

    def test_without_smtp_prints_code_but_not_address(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            email_login.deliver(config(), "aluna@ufmg.br", "123456")
        self.assertIn("123456", err.getvalue())
        self.assertNotIn("aluna", err.getvalue())

    def test_smtp_delivery(self):
        FakeSMTP.inbox.clear()
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeSMTP)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            cfg = config(EMAIL_LOGIN="1", SMTP_HOST="127.0.0.1", SMTP_PORT=str(server.server_address[1]), SMTP_TLS="none",
                         SMTP_USER="usuario", SMTP_PASSWORD="senha", SMTP_FROM="DCCHub <acesso@dcchub.exemplo>")
            email_login.send_async(cfg, "aluna@ufmg.br", "654321")
            for _ in range(100):
                if FakeSMTP.inbox:
                    break
                time.sleep(0.02)
            self.assertEqual(len(FakeSMTP.inbox), 1)
            mail = FakeSMTP.inbox[0]
            self.assertEqual(mail["rcpt"], ["aluna@ufmg.br"])
            self.assertIsNotNone(mail["auth"], "autentica no SMTP")
            parsed = message_from_bytes(mail["data"])
            self.assertEqual(str(make_header(decode_header(parsed["Subject"]))), "654321 é seu código de acesso ao DCCHub")
            self.assertIn("654 321", parsed.get_payload(decode=True).decode())
        finally:
            server.shutdown()
            server.server_close()

    def test_smtp_failure_is_logged_without_address(self):
        err = io.StringIO()
        cfg = config(SMTP_HOST="127.0.0.1", SMTP_PORT="1", SMTP_TLS="none")
        with contextlib.redirect_stderr(err):
            email_login.deliver(cfg, "aluna@ufmg.br", "123456")
        self.assertIn("falha ao enviar", err.getvalue())
        self.assertNotIn("aluna", err.getvalue())

    def test_recipient_refused_is_logged_without_address(self):
        """Regressão: o texto de SMTPRecipientsRefused (e de respostas do servidor) traz o endereço."""
        import smtplib
        from unittest import mock

        class Refusing:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def starttls(self, **kw): pass
            def login(self, *a): pass
            def send_message(self, msg):
                raise smtplib.SMTPRecipientsRefused({"aluna@ufmg.br": (550, b"5.1.1 <aluna@ufmg.br>: user unknown")})

        err = io.StringIO()
        with mock.patch.object(email_login.smtplib, "SMTP", Refusing), contextlib.redirect_stderr(err):
            email_login.deliver(config(SMTP_HOST="smtp.exemplo"), "aluna@ufmg.br", "123456")
        self.assertIn("SMTPRecipientsRefused (código 550)", err.getvalue())
        self.assertNotIn("aluna", err.getvalue())
        data_error = smtplib.SMTPDataError(554, b"rejected for aluna@ufmg.br")
        self.assertEqual(email_login.describe_error(data_error), "SMTPDataError (código 554)")


if __name__ == "__main__":
    unittest.main()

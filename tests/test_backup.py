"""Backup externo: deploy/backup.sh cifra banco + chave e entrega ao scripts/send_backup.py; nada sai em claro.

O teste roda o backup.sh de verdade, com um `docker` falso no PATH (cópias de arquivos no lugar do container), e
decifra o que seria enviado com a senha — como faria quem restaura.
"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import send_backup  # noqa: E402

FAKE_DOCKER = r"""#!/bin/sh
# docker compose falso: o "container" é a pasta $FAKE_VAR
shift  # compose
case "$1" in
  exec)
    shift 3  # -T app
    case "$*" in
      *send_backup.py*) cat > "$FAKE_SENT"; exit "${FAKE_SEND_STATUS:-0}" ;;
      rm*) rm -f "$FAKE_VAR/backup.db" ;;
      *) cp "$FAKE_VAR/dcchub.db" "$FAKE_VAR/backup.db" ;;
    esac ;;
  cp) cp "$FAKE_VAR/${2#app:/app/var/}" "$3" ;;
esac
"""


@unittest.skipUnless(shutil.which("openssl") and shutil.which("tar"), "precisa de openssl e tar")
class BackupScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "deploy").mkdir()
        shutil.copy(ROOT / "deploy" / "backup.sh", self.tmp / "deploy" / "backup.sh")
        bin_dir, var = self.tmp / "bin", self.tmp / "var"
        bin_dir.mkdir(), var.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        with sqlite3.connect(var / "dcchub.db") as conn:
            conn.execute("CREATE TABLE reviews (comment TEXT)")
            conn.execute("INSERT INTO reviews VALUES ('texto que não pode sair em claro')")
        (var / "secret.key").write_text("chave-secreta-de-teste")
        self.sent = self.tmp / "sent.enc"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_VAR": str(var), "FAKE_SENT": str(self.sent)}

    def run_backup(self, env_file: str, **extra):
        (self.tmp / "deploy" / ".env").write_text(env_file)
        return subprocess.run(["sh", str(self.tmp / "deploy" / "backup.sh")], env={**self.env, **extra},
                              capture_output=True, text=True, timeout=60)

    def test_offsite_copy_is_encrypted_and_restorable(self):
        res = self.run_backup("DOMAIN=x\nDCCHUB_SMTP_FROM=DCCHub <a@b.c>\nDCCHUB_BACKUP_PASSPHRASE=senha forte\n"
                              "DCCHUB_BACKUP_EMAIL=backup@exemplo.org\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        data = self.sent.read_bytes()
        self.assertTrue(data.startswith(b"Salted__"))
        self.assertNotIn(b"texto que n", data)
        self.assertNotIn(b"chave-secreta", data)
        self.assertEqual(list((self.tmp / "deploy" / "backups").glob("*.enc")), [], "enviado: o cifrado local é apagado")
        plain = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-pass", "pass:senha forte"],
                               input=data, capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
            names = sorted(tar.getnames())
            self.assertEqual(names[1], "secret.key")
            self.assertTrue(names[0].startswith("dcchub-") and names[0].endswith(".db"))
            self.assertEqual(tar.extractfile("secret.key").read(), b"chave-secreta-de-teste")
        wrong = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-pass", "pass:errada"],
                               input=data, capture_output=True)
        # AES-CBC sem autenticação: com senha errada o openssl às vezes (~1/256) aceita o padding e devolve lixo, com
        # código 0. O que importa é que o conteúdo não sai (e o gzip/tar recusa o lixo na restauração)
        self.assertTrue(wrong.returncode != 0 or wrong.stdout != plain)

    def test_offsite_off_without_settings(self):
        res = self.run_backup("DOMAIN=x\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("backup externo: desligado", res.stdout)
        self.assertFalse(self.sent.exists())
        self.assertEqual(len(list((self.tmp / "deploy" / "backups").glob("dcchub-*.db"))), 1, "o backup local continua")

    def test_failed_send_keeps_encrypted_file(self):
        res = self.run_backup("DCCHUB_BACKUP_PASSPHRASE=s\nDCCHUB_BACKUP_EMAIL=b@e.org\n", FAKE_SEND_STATUS="1")
        self.assertEqual(res.returncode, 0)
        self.assertIn("FALHOU", res.stderr)
        self.assertEqual(len(list((self.tmp / "deploy" / "backups").glob("*.enc"))), 1)


class SendBackupTest(unittest.TestCase):
    ENV = {"DCCHUB_BACKUP_EMAIL": "backup@exemplo.org", "DCCHUB_SMTP_HOST": "smtp.exemplo.org", "DCCHUB_SMTP_USER": "u@exemplo.org"}

    def run_main(self, data: bytes, name="dcchub-1.tar.gz.enc", env=None):
        sent = []
        stdin = mock.Mock(buffer=io.BytesIO(data))
        with mock.patch.dict(os.environ, env or self.ENV, clear=False), mock.patch.object(sys, "argv", ["x", name]), \
             mock.patch.object(sys, "stdin", stdin), mock.patch.object(send_backup.email_login, "smtp_send", lambda cfg, msg: sent.append(msg)), \
             redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            return send_backup.main(), sent

    def test_sends_encrypted_attachment(self):
        code, sent = self.run_main(b"Salted__" + b"\x00" * 100)
        self.assertEqual(code, 0)
        [msg] = sent
        self.assertEqual(msg["To"], "backup@exemplo.org")
        [att] = list(msg.iter_attachments())
        self.assertEqual(att.get_filename(), "dcchub-1.tar.gz.enc")
        self.assertTrue(att.get_content().startswith(b"Salted__"))

    def test_refuses_plaintext_and_bad_names(self):
        self.assertEqual(self.run_main(b"SQLite format 3\x00")[0], 1, "arquivo em claro nunca sai")
        self.assertEqual(self.run_main(b"Salted__x", name="../etc/passwd.enc")[0], 2)
        self.assertEqual(self.run_main(b"Salted__x", name="backup.db")[0], 2)
        self.assertEqual(self.run_main(b"Salted__x", env={"DCCHUB_BACKUP_EMAIL": "", "DCCHUB_SMTP_HOST": ""})[0], 1)


if __name__ == "__main__":
    unittest.main()

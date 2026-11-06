#!/bin/sh
# Cópia consistente do banco (API de backup do SQLite, segura com o servidor rodando) + chave secreta.
# Guarda 14 dias em ./backups. Com DCCHUB_BACKUP_PASSPHRASE e DCCHUB_BACKUP_EMAIL no .env, também manda uma cópia
# CRIPTOGRAFADA por e-mail (fora da VM): se a VM, o disco ou a assinatura acabarem, o backup sobrevive.
set -e
cd "$(dirname "$0")"
mkdir -p backups
stamp=$(date +%Y%m%d-%H%M)
docker compose exec -T app python3 -c "
import sqlite3; src = sqlite3.connect('/app/var/dcchub.db'); dst = sqlite3.connect('/app/var/backup.db'); src.backup(dst); dst.close()"
docker compose cp app:/app/var/backup.db "backups/dcchub-$stamp.db"
docker compose cp app:/app/var/secret.key backups/secret.key
docker compose exec -T app rm -f /app/var/backup.db
find backups -name 'dcchub-*.db' -mtime +14 -delete
echo "backup ok: backups/dcchub-$stamp.db"

# ---- cópia fora da VM (opcional). Lê só as duas variáveis do .env, sem "source" (valores com espaço quebrariam)
setting() { { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -1; } || true; }
pass=$(setting DCCHUB_BACKUP_PASSPHRASE)
if [ -z "$pass" ] || [ -z "$(setting DCCHUB_BACKUP_EMAIL)" ]; then
  echo "backup externo: desligado (defina DCCHUB_BACKUP_PASSPHRASE e DCCHUB_BACKUP_EMAIL no .env)"
  exit 0
fi
enc="dcchub-$stamp.tar.gz.enc"
umask 077
tar czf - -C backups "dcchub-$stamp.db" secret.key \
  | DCCHUB_BACKUP_PASSPHRASE="$pass" openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
      -pass env:DCCHUB_BACKUP_PASSPHRASE -out "backups/$enc"
# o local já está salvo: falha no envio fica no log, mas não apaga nem invalida o backup local
if docker compose exec -T app python3 scripts/send_backup.py "$enc" < "backups/$enc"; then
  rm -f "backups/$enc"
else
  echo "backup externo: FALHOU (o arquivo cifrado ficou em backups/$enc)" >&2
  find backups -name 'dcchub-*.tar.gz.enc' -mtime +14 -delete
fi

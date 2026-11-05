#!/bin/sh
# Cópia consistente do banco (API de backup do SQLite, segura com o servidor rodando) + chave secreta.
# Guarda 14 dias em ./backups. Copie essa pasta para fora da VM de vez em quando (ex.: rclone para o Drive).
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

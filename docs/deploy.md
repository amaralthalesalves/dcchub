# Deploy

[← README](../README.md)

O DCCHub é um único processo Python com SQLite (~30 MB de RAM). Qualquer VM mínima serve; o essencial é **disco persistente** para o banco — plataformas com disco efêmero (ex.: plano grátis do Render) não servem.

`deploy/` tem tudo:

| Arquivo | Para quê |
|---|---|
| `docker-compose.yml` | DCCHub + [Caddy](https://caddyserver.com) com HTTPS automático (Let's Encrypt) |
| `Caddyfile` | Proxy reverso para o app |
| `.env.example` | `DOMAIN`, `DCCHUB_ADMIN_TOKEN`, `DCCHUB_ACCESS_CODES`, acesso por e-mail e SMTP |
| `cron.example` | Atualização diária/semanal dos dados e backup diário |
| `backup.sh` | Cópia consistente do banco + chave, guarda 14 dias |
| `fly.toml` | Alternativa no Fly.io (pago, ~US$ 2–3/mês) |

```mermaid
flowchart LR
  U[Navegador] -- HTTPS --> C[Caddy :443]
  C -- HTTP --> A[DCCHub :8000]
  A --> V[(volume dcchub-var<br>dcchub.db + secret.key)]
  A --> D[data/ na imagem<br>catálogo, materiais, oportunidades]
```

## Onde hospedar

| Opção | Custo | Observação |
|---|---|---|
| **Azure for Students** | grátis (US$ 100/ano de crédito) | Só o e-mail `@ufmg.br`, sem cartão. VM **B1s** cabe no crédito. Recomendado. |
| **Oracle Cloud Always Free** | grátis | VM ARM generosa, mas o cadastro pode exigir dados que pessoa física não tem e falta capacidade com frequência. |
| **GitHub Student Pack** | crédito na DigitalOcean | Também com o e-mail da UFMG. |
| **VPS barata** (ex.: Hetzner CAX11) | ~€ 4/mês | Sem burocracia, permanente. |

## Passo a passo (VM Ubuntu)

### 1. Criar a VM
Ubuntu 24.04, 1 vCPU e 1 GB de RAM bastam. Use sua chave SSH pública e anote o **IP público**.

### 2. Abrir as portas 80 e 443
- **Azure:** em *Networking / Network security group*, libere HTTP (80) e HTTPS (443).
- **Oracle:** libere na *Security List* da VCN **e** dentro da VM (a imagem vem com iptables fechado):
  ```bash
  sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
  sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
  sudo netfilter-persistent save
  ```

### 3. Domínio
Crie um registro **A** apontando para o IP da VM. Sem domínio, use `IP-COM-TRACOS.sslip.io` (ex.: `20-1-2-3.sslip.io`) ou um subdomínio do DuckDNS — o Caddy obtém o certificado normalmente.

Com domínio próprio, crie também o **A** de `www` (o Caddy redireciona `www` para o domínio principal). Ao trocar de endereço, ponha o antigo em `REDIRECT_FROM` no `.env`: ele continua com HTTPS e passa a redirecionar para o novo, com o mesmo caminho, e nenhum link quebra. Só troque o `DOMAIN` depois que o DNS novo já responder com o IP da VM: o certificado do domínio novo depende disso.

### 4. Docker e código
Na VM:
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exit        # entre de novo no SSH
sudo mkdir -p /opt/dcchub && sudo chown $USER /opt/dcchub
git clone git@github.com:amaralthalesalves/dcchub.git /opt/dcchub
```

> Repositório privado: adicione uma *deploy key* (chave SSH da VM, só leitura) em **Settings → Deploy keys** do repositório.

### 5. Configurar e subir
```bash
cd /opt/dcchub/deploy
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # → DCCHUB_ADMIN_TOKEN
nano .env                                                        # DOMAIN, token, código de acesso e/ou e-mail
docker compose up -d --build
docker compose logs -f caddy                                     # espere "certificate obtained"
```
Abra `https://seu.dominio` — deve aparecer a tela do código de acesso.

### Envio de e-mail (acesso por @ufmg.br)
Para o login por e-mail, configure um serviço de envio — VMs na nuvem normalmente bloqueiam o envio direto (porta 25), e e-mail sem SPF/DKIM cai no spam.

**Sem domínio próprio: Gmail.** Crie uma conta Gmail só do projeto, ative a verificação em duas etapas e gere uma senha de app em [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). No `.env`:
```bash
DCCHUB_EMAIL_LOGIN=1
DCCHUB_SMTP_HOST=smtp.gmail.com
DCCHUB_SMTP_PORT=587
DCCHUB_SMTP_USER=conta.do.projeto@gmail.com
DCCHUB_SMTP_PASSWORD=abcdefghijklmnop   # senha de app, 16 letras sem espaços
DCCHUB_SMTP_FROM=DCCHub <conta.do.projeto@gmail.com>
```
O Gmail aceita ~500 envios por dia; o padrão de `DCCHUB_EMAIL_DAILY_LIMIT` (250) fica abaixo disso. Conta nova que de repente manda muito pode ser limitada pelo Google por alguns dias: crie a conta antes de divulgar o site.

**Com domínio próprio: um serviço de envio**, para mandar de `acesso@seu.dominio`:

1. Crie uma conta num serviço com plano grátis, por exemplo o **Brevo** (~300 e-mails/dia).
2. No serviço, **verifique o seu domínio**: ele mostra registros DNS (SPF, DKIM, às vezes DMARC) para você criar onde o domínio está registrado.
3. Gere uma credencial SMTP e preencha no `.env`:
   ```bash
   DCCHUB_EMAIL_LOGIN=1
   DCCHUB_SMTP_HOST=smtp-relay.brevo.com
   DCCHUB_SMTP_PORT=587
   DCCHUB_SMTP_USER=...            # login SMTP do serviço
   DCCHUB_SMTP_PASSWORD=...        # chave SMTP do serviço
   DCCHUB_SMTP_FROM=DCCHub <acesso@seu.dominio>
   ```
4. `docker compose up -d` e teste com o seu `@ufmg.br`. Se não chegar, veja `docker compose logs app` — falhas de envio aparecem lá (sem o endereço).


### 6. Atualização dos dados e backup
```bash
chmod +x backup.sh
crontab -e        # cole as linhas de deploy/cron.example
```

| Quando | O quê |
|---|---|
| todo dia, 6h | oportunidades |
| segunda, 6h30 | catálogo/oferta + materiais |
| todo dia, 3h | backup do banco e da chave em `deploy/backups/` (14 dias) e, se configurado, cópia cifrada por e-mail |

### 7. Backup fora da VM

Backup só na VM não protege contra perder a VM (ou o crédito da nuvem acabar). Com duas linhas no `.env`, o
`backup.sh` diário também manda uma cópia **criptografada** (banco + chave secreta, `openssl` AES-256 com PBKDF2) por
e-mail, pelo mesmo SMTP do acesso:

```bash
openssl rand -base64 32          # gere a senha; GUARDE-A TAMBÉM FORA DA VM (gerenciador de senhas)
# no .env:
DCCHUB_BACKUP_EMAIL=conta-do-projeto@gmail.com
DCCHUB_BACKUP_PASSPHRASE=<a senha gerada, sem aspas>
docker compose up -d             # o container passa a conhecer DCCHUB_BACKUP_EMAIL
./backup.sh                      # teste: deve terminar com "backup externo enviado"
```

A caixa de e-mail só recebe um arquivo cifrado (`dcchub-AAAAMMDD-HHMM.tar.gz.enc`, alguns KB por dia); o
`send_backup.py` recusa mandar qualquer coisa que não esteja cifrada. **Sem a senha, o backup não abre** — e a senha
só existe no `.env` da VM e onde você a guardou. Se o envio falhar, o backup local continua valendo e o arquivo cifrado
fica em `deploy/backups/` (o motivo vai para o log do cron).

### 8. Aviso quando o site cai

O workflow `.github/workflows/uptime.yml` consulta `https://SEU.DOMINIO/api/health` a cada 15 min (o endpoint só diz
"ok": servidor no ar e banco abrindo, sem precisar de acesso). Fora do ar em 3 tentativas: abre a issue **"Site fora do
ar"** e a execução falha (o GitHub manda e-mail); quando volta, a issue é fechada. Para ligar, cadastre a URL como
secret — ela não fica no código, que é público:

```bash
gh secret set SITE_URL --body "https://seu.dominio"
```

O GitHub pausa agendamentos de repositórios públicos sem nenhum commit por 60 dias; se isso acontecer, reative em
*Actions → Disponibilidade*.

## Atualizando o código

```bash
cd /opt/dcchub && git pull
cd deploy && docker compose up -d --build
```

O banco e a chave ficam no volume `dcchub-var` e sobrevivem a rebuilds. O catálogo vem na imagem; os crons atualizam a cópia dentro do container, e o próximo rebuild volta à versão do repositório até o cron seguinte — rode os syncs localmente e faça commit de `data/` para manter o repositório em dia.

## Restaurando um backup

Da cópia por e-mail (baixe o anexo `.enc`; precisa da senha de backup):

```bash
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:senha.txt -in dcchub-AAAAMMDD-HHMM.tar.gz.enc | tar xz
# extrai dcchub-AAAAMMDD-HHMM.db e secret.key; copie-os para deploy/backups/ da VM nova e siga abaixo
```

Do backup local:

```bash
cd /opt/dcchub/deploy
docker compose stop app
docker compose cp backups/dcchub-AAAAMMDD-HHMM.db app:/app/var/dcchub.db
docker compose cp backups/secret.key app:/app/var/secret.key
docker compose run --rm -u root app chown -R nobody /app/var   # o app roda como nobody
docker compose start app
```

## Sem Docker

```bash
DCCHUB_DB=/var/lib/dcchub/dcchub.db DCCHUB_TRUST_PROXY=1 DCCHUB_ADMIN_TOKEN=... \
DCCHUB_ACCESS_CODES=... HOST=127.0.0.1 PORT=8000 python3 app.py
```

Coloque atrás de um proxy com HTTPS (Caddy/nginx) e mantenha `DCCHUB_TRUST_PROXY=1` — o app passa a usar o último item de `X-Forwarded-For` para o limite de abusos, marca o cookie como `Secure` e envia HSTS. No nginx, use `proxy_set_header X-Forwarded-For $remote_addr;` (não `$proxy_add_x_forwarded_for`). Um serviço `systemd` com `Restart=always` cuida de reinícios.

## Limites em memória

Os limites de abuso (avaliações, votos e denúncias por rede, códigos por e-mail por dia, tentativas por código) e a contagem de denúncias por rede vivem **só na memória** do processo, de propósito: nada disso vai para o banco. Consequências:

- **Rode uma única instância.** Com duas, cada uma conta pela metade. No Fly, mantenha uma máquina (`fly scale count 1`).
- **Reiniciar zera os contadores.** No Fly com `auto_stop_machines`, cada parada também zera. Isso deixa a retirada automática por denúncias mais conservadora (as denúncias seguem no banco e na fila), mas reabre por um momento os limites diários.
- **Aviso à moderação agendado se perde num reinício.** A fila continua no painel, e o próximo item que entrar nela agenda um aviso novo, que traz a contagem completa.

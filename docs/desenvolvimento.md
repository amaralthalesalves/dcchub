# Desenvolvimento

[← README](../README.md)

## Arquitetura

Sem dependências e sem etapa de build: **Python 3.10+ (biblioteca padrão)**, **SQLite** e uma **SPA em JavaScript puro**.

```mermaid
flowchart LR
  subgraph Navegador
    SPA[static/app.js<br>rotas por hash]
  end
  subgraph Servidor["app.py (http.server)"]
    API[API JSON] --> MOD[moderation.py]
    API --> DB[(SQLite WAL<br>avaliações)]
    API --> CAT[data/*.json<br>catálogo · materiais · oportunidades]
  end
  SPA -- fetch /api/* --> API
  SYNC[scripts/sync_*.py] -- páginas públicas --> CAT
```

- **`app.py`** serve os arquivos estáticos e a API, valida e grava avaliações, aplica limites de abuso, código de acesso e cabeçalhos de segurança. Recarrega `data/*.json` sozinho quando os arquivos mudam. Respostas com gzip (~20 KB para carregar o catálogo).
- **`email_login.py`** valida o domínio, gera o código e o desafio assinado e envia o código por SMTP (numa thread, sem guardar o e-mail).
- **`moderation.py`** decide publicar, devolver ou segurar cada comentário. Veja [Moderação](moderacao.md).
- **`mod_notify.py`** avisa a moderação por e-mail quando algo entra na fila (só contagens, no máximo a cada 30 min).
- **`static/`** é a interface: `index.html`, `app.js` (rotas como `#/professor/…`, `#/oferta`), `select.js` (seletores estilizados), `style.css` (tokens de cor com modo claro/escuro), `theme.js` e as fotos.
- **`scripts/`** geram os dados a partir das páginas públicas. Veja [Dados](dados.md).

### Estrutura

```
app.py                       servidor HTTP + API JSON
moderation.py                filtro automático de comentários
email_login.py               acesso por código enviado ao e-mail @ufmg.br
mod_notify.py                avisos por e-mail à moderação
scripts/
  sync_dcc.py                docentes, disciplinas e ofertas do site do DCC
  audit_catalog.py           confere o catálogo contra todas as ofertas oficiais
  import_sigaa.py            oferta pelo mapa do SIGAA (PDF)
  sync_oportunidades.py      chamadas de dcc.ufmg.br/oportunidades
  sync_provas.py             índice do acervo de materiais (Google Drive)
  sync_curriculos.py         currículos dos cursos (relatórios da PROGRAD + transcrições)
  send_backup.py             envia o backup cifrado por e-mail (usado pelo deploy/backup.sh)
data/
  catalog.json               catálogo gerado (versionado)
  catalog-arquivo.json       disciplinas extintas
  ofertas-sigaa/             ofertas importadas do SIGAA (só campos mínimos)
  oportunidades.json, provas.json
  curriculos.json            currículos por curso/período (gerado; formato em docs/curriculos-formato.md)
  curriculos_manual.json     currículos que só existem em imagem, transcritos à mão
static/                      SPA sem build + fotos
tests/                       unitários, integração, moderação, seed de demonstração, e2e
deploy/                      docker compose, Caddy, cron, backup
docs/                        esta documentação
```

### Design da interface

Azul-escuro e preto, fundo liso e superfícies sólidas; vidro translúcido só no que flutua (navegação em pílula, filtros, busca). Títulos grandes na Bricolage Grotesque (OFL, servida de `static/fonts/`), texto corrido na fonte do sistema — nada é carregado de terceiros. Cor forte só onde carrega informação (notas, avisos, conflitos). Todas as cores são variáveis em `:root` no `style.css`, redefinidas para o modo escuro.

### Currículos (grade por curso)

A grade pode começar pelas disciplinas do período da pessoa (curso, currículo e período ficam só no navegador). Os dados vêm de `data/curriculos.json`, gerado por:

```bash
python3 scripts/sync_curriculos.py            # baixa os relatórios oficiais da PROGRAD e junta data/curriculos_manual.json
python3 scripts/sync_curriculos.py --dry-run  # só mostra o resumo
```

Precisa do `pdftotext` (poppler) na máquina que roda o script; o servidor só lê o JSON. Currículos que só existem como imagem (Ciência da Computação 2024, Sistemas de Informação, Engenharia de Computação) ficam em `data/curriculos_manual.json`, no mesmo formato, e precisam ser revistos à mão quando o colegiado publicar uma nova versão. Disciplinas de fora do DCC aparecem na lista do período sem turmas; a pessoa pode pôr o horário dela à mão na grade.

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `DCCHUB_DB` | `data/avaliacoes.db` | Banco SQLite (modo WAL). |
| `DCCHUB_SECRET` | gerado em `secret.key`, ao lado do banco | Chave do HMAC. **Faça backup**: trocá-la desliga as pessoas das próprias avaliações. |
| `DCCHUB_ADMIN_TOKEN` | — | Ativa o painel de moderação em `/#/moderacao`. |
| `DCCHUB_ACCESS_CODES` | — (site aberto) | Código(s) de acesso da comunidade, separados por vírgula. Veja [Comunidade e privacidade](comunidade-e-privacidade.md#o-código-de-acesso). |
| `DCCHUB_EMAIL_LOGIN` | — | `1` libera o acesso por código de 6 dígitos enviado ao e-mail `@ufmg.br`. Veja [Comunidade e privacidade](comunidade-e-privacidade.md#acesso-pelo-e-mail-ufmgbr). |
| `DCCHUB_SMTP_HOST` · `_PORT` · `_USER` · `_PASSWORD` · `_FROM` · `_TLS` | porta 587, `starttls` (`ssl` na 465) | Envio dos códigos. Sem `SMTP_HOST`, o código aparece no terminal (desenvolvimento). Gmail: `smtp.gmail.com` com senha de app ([Deploy](deploy.md)). |
| `DCCHUB_EMAIL_EPOCH` | `1` | Trocar o valor revoga todos os acessos feitos por e-mail. |
| `DCCHUB_MOD_EMAIL` | — | E-mail(s) da moderação, separados por vírgula: recebem um aviso quando algo entra na fila de revisão. Usa o SMTP acima. Veja [Moderação](moderacao.md#aviso-por-e-mail-à-moderação). |
| `DCCHUB_PUBLIC_URL` | — | Endereço do site (ex.: `https://dcchub.com.br`), para o link do painel no aviso à moderação. |
| `DCCHUB_EMAIL_DAILY_LIMIT` | `250` | Teto global de e-mails enviados por dia (abaixo da cota do serviço de envio: Gmail ~500, Brevo grátis 300). Cada conexão pede no máximo 20 por dia. |
| `DCCHUB_TRUST_PROXY` | — | `1` atrás de um proxy confiável: usa o **último** item de `X-Forwarded-For` (o que o proxy viu; os anteriores podem ser forjados), marca o cookie como `Secure` e envia HSTS. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Endereço de escuta. |

Os nomes antigos (`AVALIA_DCC_*`) continuam aceitos. O cookie (`avalia_dcc`) e as chaves do navegador (`avalie-*`) mantêm o nome antigo **de propósito**: trocá-los desconectaria todo mundo da própria identidade anônima e apagaria rascunhos e grades salvos.

## API

Todas as rotas respondem JSON. Com `DCCHUB_ACCESS_CODES` ou `DCCHUB_EMAIL_LOGIN` definidos, tudo exceto as rotas `/api/access*` responde `401 {"gate": true, "methods": ["email", "code"]}` sem o cookie de acesso.

| Método | Rota | |
|---|---|---|
| POST | `/api/access` | Troca um código de acesso pelo cookie |
| POST | `/api/access/email` · `/api/access/email/verify` | Envia o código para um e-mail `@ufmg.br` e devolve o desafio · troca desafio + código pelo cookie (5 tentativas por código) |
| GET | `/api/bootstrap` | Catálogo, estatísticas e definições do formulário |
| GET | `/api/offerings?term=2026.2` | Turmas do semestre com notas por par docente × disciplina |
| GET | `/api/professors/{slug}` · `/api/courses/{id}` | Detalhes, resumo, histórico e avaliações |
| GET | `/api/reviews?professor=&course=&sort=recent&limit=&offset=` | Lista paginada |
| POST · PUT · DELETE | `/api/reviews` · `/api/reviews/{id}` | Criar · editar · apagar (só quem escreveu) |
| POST | `/api/reviews/{id}/helpful` · `/api/reviews/{id}/report` | Voto útil (alterna) · denúncia |
| POST | `/api/moderation/check` | Avisos de moderação para um rascunho de comentário |
| GET · POST · POST | `/api/me` · `/api/me/code` · `/api/me/restore` | Minhas avaliações · mostrar o código de recuperação (só POST JSON da própria origem, com limite) · recuperar |
| POST · DELETE | `/api/class-reports` | Informar/desfazer "docente X dá a disciplina Y" (só sem oferta oficial) |
| GET | `/api/provas` · `/api/oportunidades` | Materiais e oportunidades |
| GET · POST | `/api/admin/queue` · `/api/admin/reviews/{id}` · `/api/admin/class-reports` · `/api/admin/class-reports/delete` | Moderação (`Authorization: Bearer …`) |

Para testar o acesso por e-mail localmente, sem SMTP — o código aparece no terminal:

```bash
DCCHUB_EMAIL_LOGIN=1 python3 app.py
```

Uma avaliação recusada pela moderação responde `400` com `fields.comment` (mensagens) e `moderation` (categoria, decisão e trecho).

## Testes

```bash
python3 -m unittest discover -s tests -v    # API, acesso, moderação, segurança, parsers, versões de disciplina
node --test tests/test_ics.mjs              # gerador de .ics da grade (static/ics.js)
```

Teste de ponta a ponta no Chrome headless (Node 22+, sem dependências), **sempre num servidor temporário** — ele cria e apaga avaliações:

```bash
python3 tests/seed_demo.py /tmp/demo.db
DCCHUB_DB=/tmp/demo.db PORT=8766 python3 app.py &
node tests/e2e_browser.mjs http://localhost:8766
```

> [!CAUTION]
> `tests/seed_demo.py` cria avaliações **fictícias** atribuídas a docentes reais. Use só em bancos temporários — o script se recusa a escrever no banco padrão.

### CI (GitHub Actions)

| Workflow | Quando | O que faz |
|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | todo PR e push no `main` | Testes Python em 3.10 e 3.13; sintaxe do JS; testes do `.ics`; e2e no Chrome com banco de demonstração |
| [`sync.yml`](../.github/workflows/sync.yml) | segunda 6h30 (Brasília) ou manual | Roda os syncs de catálogo, oportunidades e materiais; se `data/` mudou e os testes passam, abre um PR. Falha (e avisa) se algum site mudou de formato |

O `sync.yml` precisa de **Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"**. PRs abertos por ele não disparam o `ci.yml` (limite do `GITHUB_TOKEN`), por isso os testes rodam dentro do próprio sync.

## Convenções

- Sem dependências externas no servidor e no navegador. Se algo precisar de uma, discuta antes.
- Textos da interface em português, com acentuação.
- Mudou uma regra de moderação ou do sync? Adicione um caso de teste — o ataque/erro **e** o caso legítimo parecido que não pode quebrar.
- Nunca versione relatórios do SIGAA (`*.pdf`), bancos (`*.db`), `secret.key` ou `.env`.

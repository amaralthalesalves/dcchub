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
- **`email_login.py`** valida o domínio, assina os tokens e envia o link por SMTP (numa thread, sem guardar o e-mail).
- **`moderation.py`** decide publicar, devolver ou segurar cada comentário. Veja [Moderação](moderacao.md).
- **`static/`** é a interface: `index.html`, `app.js` (rotas como `#/professor/…`, `#/oferta`), `select.js` (seletores estilizados), `style.css` (tokens de cor com modo claro/escuro), `theme.js` e as fotos.
- **`scripts/`** geram os dados a partir das páginas públicas. Veja [Dados](dados.md).

### Estrutura

```
app.py                       servidor HTTP + API JSON
moderation.py                filtro automático de comentários
email_login.py               acesso por link enviado ao e-mail @ufmg.br
scripts/
  sync_dcc.py                docentes, disciplinas e ofertas do site do DCC
  audit_catalog.py           confere o catálogo contra todas as ofertas oficiais
  import_sigaa.py            oferta pelo mapa do SIGAA (PDF)
  sync_oportunidades.py      chamadas de dcc.ufmg.br/oportunidades
  sync_provas.py             índice do acervo de materiais (Google Drive)
data/
  catalog.json               catálogo gerado (versionado)
  catalog-arquivo.json       disciplinas extintas
  ofertas-sigaa/             ofertas importadas do SIGAA (só campos mínimos)
  oportunidades.json, provas.json
static/                      SPA sem build + fotos
tests/                       unitários, integração, moderação, seed de demonstração, e2e
deploy/                      docker compose, Caddy, cron, backup
docs/                        esta documentação
```

### Design da interface

Linguagem visual sóbria e institucional: superfícies neutras, **um** azul de destaque (`#1f56a0`), bordas finas em vez de sombras pesadas, sem gradientes, e cor só onde carrega informação (notas, avisos, conflitos). Todas as cores são variáveis em `:root` no `style.css`, redefinidas para o modo escuro. Fontes do sistema — nada é carregado de terceiros.

## Configuração

| Variável | Padrão | Uso |
|---|---|---|
| `DCCHUB_DB` | `data/avaliacoes.db` | Banco SQLite (modo WAL). |
| `DCCHUB_SECRET` | gerado em `secret.key`, ao lado do banco | Chave do HMAC. **Faça backup**: trocá-la desliga as pessoas das próprias avaliações. |
| `DCCHUB_ADMIN_TOKEN` | — | Ativa o painel de moderação em `/#/moderacao`. |
| `DCCHUB_ACCESS_CODES` | — (site aberto) | Código(s) de acesso da comunidade, separados por vírgula. Veja [Comunidade e privacidade](comunidade-e-privacidade.md#o-código-de-acesso). |
| `DCCHUB_EMAIL_LOGIN` | — | `1` libera o acesso por link enviado ao e-mail `@ufmg.br`. Veja [Comunidade e privacidade](comunidade-e-privacidade.md#acesso-pelo-e-mail-ufmgbr). |
| `DCCHUB_PUBLIC_URL` | `http://localhost:PORT` | Endereço público usado nos links enviados (ex.: `https://dcchub.com.br`). Nunca vem do cabeçalho `Host`. |
| `DCCHUB_SMTP_HOST` · `_PORT` · `_USER` · `_PASSWORD` · `_FROM` · `_TLS` | porta 587, `starttls` (`ssl` na 465) | Envio dos links. Sem `SMTP_HOST`, o link aparece no terminal (desenvolvimento). |
| `DCCHUB_EMAIL_EPOCH` | `1` | Trocar o valor revoga todos os acessos feitos por e-mail. |
| `DCCHUB_EMAIL_DAILY_LIMIT` | `250` | Teto global de links enviados por dia (abaixo da cota do serviço de envio; o Brevo grátis dá 300). Cada conexão pede no máximo 20 por dia. |
| `DCCHUB_TRUST_PROXY` | — | `1` atrás de um proxy confiável: usa o **último** item de `X-Forwarded-For` (o que o proxy viu; os anteriores podem ser forjados), marca o cookie como `Secure` e envia HSTS. |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Endereço de escuta. |

Os nomes antigos (`AVALIA_DCC_*`) continuam aceitos. O cookie (`avalia_dcc`) e as chaves do navegador (`avalie-*`) mantêm o nome antigo **de propósito**: trocá-los desconectaria todo mundo da própria identidade anônima e apagaria rascunhos e grades salvos.

## API

Todas as rotas respondem JSON. Com `DCCHUB_ACCESS_CODES` ou `DCCHUB_EMAIL_LOGIN` definidos, tudo exceto as rotas `/api/access*` responde `401 {"gate": true, "methods": ["email", "code"]}` sem o cookie de acesso.

| Método | Rota | |
|---|---|---|
| POST | `/api/access` | Troca um código de acesso pelo cookie |
| POST | `/api/access/email` · `/api/access/email/verify` | Envia o link para um e-mail `@ufmg.br` · troca o token do link pelo cookie |
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

Para testar o acesso por e-mail localmente, sem SMTP — o link aparece no terminal:

```bash
DCCHUB_EMAIL_LOGIN=1 python3 app.py
```

Uma avaliação recusada pela moderação responde `400` com `fields.comment` (mensagens) e `moderation` (categoria, decisão e trecho).

## Testes

```bash
python3 -m unittest discover -s tests -v    # 64 testes: API, acesso, moderação, segurança, parsers, versões de disciplina
```

Teste de ponta a ponta no Chrome headless (Node 22+, sem dependências), **sempre num servidor temporário** — ele cria e apaga avaliações:

```bash
python3 tests/seed_demo.py /tmp/demo.db
DCCHUB_DB=/tmp/demo.db PORT=8766 python3 app.py &
node tests/e2e_browser.mjs http://localhost:8766
```

> [!CAUTION]
> `tests/seed_demo.py` cria avaliações **fictícias** atribuídas a docentes reais. Use só em bancos temporários — o script se recusa a escrever no banco padrão.

## Convenções

- Sem dependências externas no servidor e no navegador. Se algo precisar de uma, discuta antes.
- Textos da interface em português, com acentuação.
- Mudou uma regra de moderação ou do sync? Adicione um caso de teste — o ataque/erro **e** o caso legítimo parecido que não pode quebrar.
- Nunca versione relatórios do SIGAA (`*.pdf`), bancos (`*.db`), `secret.key` ou `.env`.

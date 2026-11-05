# Comunidade e privacidade

[← README](../README.md)

O DCCHub é um projeto **independente** de estudantes, sem vínculo com o DCC ou a UFMG — isso aparece no rodapé, na tela de acesso e em "Como funciona". Ele existe para a comunidade estudantil e é desenhado para ficar com ela.

## Fora do alcance público

| Medida | Como |
|---|---|
| **Código de acesso** | Com `DCCHUB_ACCESS_CODES` definido, a API não entrega nenhum dado sem um código válido; o site mostra só a tela de acesso. |
| **Fora de buscadores** | `robots.txt` bloqueia tudo; toda resposta leva `X-Robots-Tag: noindex` e a página tem `<meta name="robots" content="noindex">`. O nome de um docente numa busca não leva ao site. |
| **Sem marca institucional** | Nome, logo e textos não imitam DCC/UFMG e deixam claro que é um projeto independente. |

### O código de acesso
- Divulgue **só nos canais dos estudantes** (grupos de turma, DA, Discord…).
- Aceita vários códigos separados por vírgula — por exemplo, um por semestre.
- O navegador guarda um **HMAC do código** (cookie `HttpOnly`, 180 dias), nunca o código em si. **Trocar o código revoga** todo mundo que entrou com o antigo.
- O código não identifica ninguém: o anonimato continua igual.

> [!NOTE]
> Não existe garantia absoluta: qualquer pessoa com o código (monitores, pós-graduandos…) pode ver ou repassar. A melhor proteção continua sendo o **conteúdo ser defensável** — regras claras, [moderação automática](moderacao.md) e denúncias.

## Anonimato

- **Sem cadastro.** O navegador recebe um token aleatório em cookie `HttpOnly`, `SameSite=Strict`.
- O banco guarda só `HMAC-SHA256(segredo, token)` — nem o token, nem nada que identifique a pessoa.
- **Data só com o dia.** A data de cada avaliação — e também de votos, denúncias e turmas informadas, que levam o mesmo hash de quem avalia — é guardada sem hora ("hoje", "ontem", "há 3 dias").
- **Sem ordem de chegada.** As avaliações têm ids aleatórios, e votos, denúncias e turmas informadas ficam em tabelas sem `rowid` (guardadas na ordem da chave). Com ids sequenciais, daria para saber quem avaliou ou votou antes de quem num mesmo dia, o que anularia a data só com o dia.
- **Apagar é apagar.** O banco roda com `secure_delete`: avaliações apagadas e horários antigos são zerados no arquivo, não ficam em páginas livres.
- **Nenhum log de acesso com IP.** O IP só é usado em memória para limitar abusos (ex.: 6 avaliações a cada 10 min; no máximo 3 avaliações da mesma turma e 3 votos na mesma avaliação por rede por dia; denúncias contadas por rede) e é descartado.
- **Contra contas falsas sem criar contas.** Como um cookie anônimo novo sai de graça, os limites acima valem por rede — sempre só em memória, sem ligar a avaliação a quem a escreveu.
- Quem escreveu pode **editar e apagar** as próprias avaliações. O **código de recuperação** leva a identidade anônima para outro navegador.

### O que o banco guarda

| Tabela | Conteúdo |
|---|---|
| `reviews` | Docente, disciplina, semestre, notas, marcadores, comentário, situação (publicada/segurada/removida), hash do autor, datas |
| `votes` | Voto "útil": avaliação + hash de quem votou |
| `reports` | Denúncias: avaliação + hash de quem denunciou + motivo |
| `class_reports` | Relatos "docente X dá a disciplina Y neste semestre" + hash de quem relatou |

Comentários recusados pela moderação **não são guardados**.

## Segurança

- Content-Security-Policy restritiva, sem nenhum recurso de terceiros (fontes, scripts, analytics); HSTS quando atrás de HTTPS.
- Links vindos de dados raspados só viram `href` se forem `http(s)`/`mailto` (segunda barreira, além da CSP).
- Checagem de `Origin` em toda escrita; limite de tamanho do corpo; campo-armadilha contra bots.
- O código de recuperação **é** a identidade anônima (o mesmo valor do cookie `HttpOnly`). Por isso só sai por `POST` JSON da própria origem, com limite e sem cache. Uma página com script injetado ainda conseguiria pedi-lo, e é a CSP (nenhum script inline ou de terceiros) que impede isso.
- Consultas SQL sempre parametrizadas.
- Painel de moderação protegido por `DCCHUB_ADMIN_TOKEN` (`Authorization: Bearer …`).

## A chave secreta

O segredo do HMAC fica em `secret.key`, ao lado do banco (ou em `DCCHUB_SECRET`). **Faça backup dele junto com o banco**: com uma chave nova, ninguém consegue mais editar ou apagar as próprias avaliações, e os códigos de acesso salvos nos navegadores deixam de valer. O `deploy/backup.sh` já copia os dois.

## Dados de terceiros

- O catálogo vem só de **páginas públicas** do DCC. As fotos são baixadas no sync e servidas localmente, para que o navegador de quem usa não acesse terceiros.
- Da oferta do SIGAA, guardam-se só código, título, turma, horário e nome dos docentes — veja [Dados → SIGAA](dados.md#oferta-pelo-sigaa--import_sigaapy). O PDF nunca entra no repositório nem na imagem Docker.

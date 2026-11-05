# Dados

[← README](../README.md)

Tudo o que o DCCHub mostra além das avaliações vem de **páginas públicas** — do DCC e do acervo de materiais dos estudantes — e é gravado em arquivos JSON versionados em `data/`. O site nunca acessa terceiros a partir do navegador de quem usa: fotos são baixadas no sync e materiais/chamadas são só links.

| Arquivo | Gerado por | Conteúdo |
|---|---|---|
| `data/catalog.json` | `scripts/sync_dcc.py` | Docentes, disciplinas em oferta e turmas desde 2020.1 |
| `data/catalog-arquivo.json` | `scripts/sync_dcc.py` | Disciplinas extintas (não aparecem no site) |
| `data/ofertas-sigaa/<semestre>.json` | `scripts/import_sigaa.py` | Oferta de um semestre a partir do mapa do SIGAA |
| `data/oportunidades.json` | `scripts/sync_oportunidades.py` | Chamadas de IC, bolsas e estágios |
| `data/provas.json` | `scripts/sync_provas.py` | Índice do acervo de materiais |
| `static/photos/` | `scripts/sync_dcc.py` | Fotos dos docentes (servidas localmente) |

## Catálogo — `sync_dcc.py`

```bash
python3 scripts/sync_dcc.py            # baixa e regenera o catálogo
python3 scripts/sync_dcc.py --offline  # reaplica as regras sem acessar o site
```

### Docentes
Lidos de `dcc.ufmg.br/professores/` (situação, áreas, foto) e do perfil de cada um (sala, e-mail, Lattes, Scholar, página pessoal).

### Ofertas
Páginas "Oferta de disciplinas — Nº semestre de AAAA", descobertas pela API do WordPress do site. O formato das tabelas mudou várias vezes desde 2020; o parser identifica as colunas pelo cabeçalho e entende os três formatos de horário:

```
Seg 17:00 – 18:40      2a e 4a – 07:30/09:10      14:55 16:35 (Seg)
```

### Nomes abreviados
Ofertas antigas trazem "Ana Paula", "J. Guilherme", "F. Murai". Um resolvedor casa iniciais, grafias (Luis/Luiz) e nomes mais longos que o cadastro, e usa o histórico da disciplina para desfazer ambiguidades. Regras:

- Nome com sobrenome só casa com quem tem sobrenome compatível ("Ana Paula de Carvalho" ≠ "Ana Paula Couto da Silva").
- Nomes ambíguos ("Marcos Augusto", "Frederico") são resolvidos pelo histórico da disciplina, por uma disciplina que divide turma com ela ou pela área de pesquisa do docente.
- Quem não está na lista do DCC (outros departamentos) vira **externo**.
- O que não dá para identificar com segurança fica como **"docente não informado"** — nunca é chutado.

O log do sync mostra cada decisão.

### Substitutos
Vêm da página [Professores Substitutos](https://dcc.ufmg.br/professores-substitutos/). "Amanda" ou "Wagner Subst" numa turma viram o substituto contratado com esse nome naquela janela (até 2 anos), nunca um efetivo. "PS", "PS3" e "Substituto 4" aparecem como *Professor substituto (não identificado)*. Turmas sem nome na fonte (a oferta de 2022.2 não tem coluna de docente) mostram *docente não informado pelo DCC*.

### Versões de currículo
Quando o currículo muda, a disciplina ganha código novo e o antigo segue ofertado por um tempo:

| Disciplina | Antigo → novo |
|---|---|
| Estruturas de Dados | DCC205 → DCC221 |
| PDS II | DCC204 → DCC219 |
| Redes de Computadores | DCC023 → DCC095 |
| Engenharia de Software | DCC603 → DCC094 |

O sync une versões com o mesmo nome — tolerando grafia ("Banco"/"Bancos", "de"/"dos"), erros de digitação e prefixos como "TÓP. ESP. EM INFORMÁTICA II –" — sob o código mais recente; os outros viram `aliases`. Versões com nomes diferentes vêm da grade curricular (`SAME_AS`: DCC011 → DCC222, DCC010 → DCC053). Disciplinas distintas que só dividem sala (DCC006/DCC017, DCC033/DCC034) continuam separadas. Links, avaliações e rascunhos com o código antigo são redirecionados automaticamente. Exceções ficam em `CANONICAL` e `SAME_AS` no `sync_dcc.py`.

### Tópicos especiais
DCC030/049/831… mudam de tema a cada semestre; cada tema vira uma disciplina própria (`TOP-…`). Um tópico com o nome de uma disciplina regular é unido a ela.

### Disciplinas extintas
Currículos antigos (AEDS I — DCC003; AEDS II não aparece em nenhuma oferta desde 2020) e disciplinas substituídas (DCC050 e DCC044 → DCC096; DCC606 → DCC637) estão em `RETIRED`, com a sucessora. Também é extinta qualquer disciplina "Algoritmos e Estruturas de Dados…" e qualquer uma sem oferta nas 4 últimas ofertas publicadas. Extintas vão para `catalog-arquivo.json`, não aparecem no site, e links antigos levam à sucessora; o arquivo só mantém visíveis avaliações antigas que já existam.

### Semestre sem oferta publicada
O site avisa que as turmas "atuais" são da última oferta oficial e abre **relatos da comunidade**: estudantes informam quem está dando cada disciplina (um relato por pessoa, com contagem e moderação). Os relatos somem quando a oferta oficial sai.

## Auditoria — `audit_catalog.py`

```bash
python3 scripts/audit_catalog.py
```

Relê todas as páginas oficiais de oferta e confere, linha a linha, disciplina, turma e docente contra o catálogo. Sai com erro se algo divergir. Último resultado: **1888 linhas, 1660 docentes compatíveis, 21 não identificáveis, 0 divergências**.

## Oferta pelo SIGAA — `import_sigaa.py`

Quando o DCC ainda não publicou a página do semestre, dá para usar o *Mapa de oferta por departamento* do SIGAA (PDF):

```bash
python3 scripts/import_sigaa.py mapa.pdf   # requer pdftotext (poppler-utils)
python3 scripts/sync_dcc.py                # incorpora o semestre
```

> [!CAUTION]
> O relatório do SIGAA é de **uso restrito** e traz o número de inscrição dos docentes. O importador grava **só** código, título, turma, horário e nome dos docentes — inscrição, vagas e cursos demandantes são descartados na leitura. O PDF **nunca** deve ser versionado nem empacotado (`*.pdf` está no `.gitignore` e no `.dockerignore`).

Linhas do mesmo tema/turma com códigos diferentes (DCC030/DCC071…) são unidas. O semestre aparece no site como *fonte: SIGAA*; a página oficial do DCC, quando sair, prevalece.

## Oportunidades — `sync_oportunidades.py`

Lê [dcc.ufmg.br/oportunidades](https://dcc.ufmg.br/oportunidades/) (chamadas abertas e encerradas) e cada chamada, gravando título, link, prazo, tipo (IC, bolsa de pesquisa, estágio, pós-doc…), público, área/laboratório, resumo, valores, carga horária, link de candidatura e os docentes citados (ligados aos perfis). O site mostra a aba **Oportunidades** e um bloco "Oportunidades abertas" no perfil de cada docente citado; a chamada original é sempre a referência.

## Materiais — `sync_provas.py`

Indexa o [acervo de provas antigas](https://drive.google.com/drive/folders/11qr5vHPEckWX-TjPq8f7D6DfMUrAfElW) mantido pelos estudantes no Google Drive (pastas por período e por disciplina, como "DCC205 - Estruturas de Dados"). Grava só nomes, links e quantidade de arquivos, e liga cada pasta à disciplina do catálogo pelo código — incluindo versões antigas (a pasta DCC205 aparece em Estruturas de Dados, DCC221) — ou, para tópicos, pelo nome. `--offline` religa as pastas sem acessar o Drive.

## Agenda sugerida

```cron
0 6 * * *   python3 scripts/sync_oportunidades.py
30 6 * * 1  python3 scripts/sync_dcc.py && python3 scripts/sync_provas.py
```

Em produção com Docker, use `deploy/cron.example` (roda os scripts dentro do container). O servidor recarrega os arquivos sozinho quando mudam.

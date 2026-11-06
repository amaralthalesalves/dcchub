# Moderação

[← README](../README.md)

O filtro (`moderation.py`) foi feito para que **quase ninguém precise revisar comentários**. Em vez de segurar tudo o que parece suspeito — o que vira fila e trabalho humano —, ele resolve na hora a maioria dos casos e só pede revisão quando a decisão realmente exige julgamento.

## As três decisões

| Decisão | Quando | O que acontece |
|---|---|---|
| ✅ **Publica** | O comentário está limpo | Vai ao ar na hora. |
| ↩️ **Devolve para reescrever** | Fere uma regra clara | **Nada é guardado.** A pessoa vê o motivo e o trecho e corrige antes de enviar. |
| ⏸️ **Segura para revisão** | Ambíguo de verdade | Fica na fila do painel (`/#/moderacao`) até alguém aprovar ou remover. |

Hoje só **acusações de discriminação** ("ele é machista", "racista") são seguradas: podem ser uma crítica legítima e importante ou difamação, e isso uma regra não decide.

### Aviso por e-mail à moderação
Com `DCCHUB_MOD_EMAIL` definido (um ou mais endereços, separados por vírgula), a moderação recebe um e-mail quando algo entra na fila: avaliação nova ou editada segurada pelo filtro, ou avaliação retirada do ar por denúncias. Usa o mesmo SMTP do acesso por e-mail (`mod_notify.py`).

- **Sem encher a caixa:** o primeiro aviso espera 2 minutos para agrupar o que chegar junto; depois, no máximo um e-mail a cada 30 minutos. O resumo é montado na hora do envio: se a fila já foi resolvida, nada é enviado.
- **Só contagens e motivos:** "2 seguradas pelo filtro", "1 retirada do ar por denúncias", "3 publicadas com denúncias pendentes", e o link do painel (`DCCHUB_PUBLIC_URL` + `/#/moderacao`). Nunca docente, disciplina, texto ou dado de quem escreveu — acusações contra pessoas nomeadas não ficam guardadas numa caixa de e-mail; o conteúdo se lê no painel.

### Aviso enquanto digita
O formulário consulta `POST /api/moderation/check` alguns instantes depois que a pessoa para de digitar e mostra, abaixo do comentário, o motivo e o trecho exato que bateu na regra. Assim quase ninguém chega a ser recusado no envio.

## Categorias

| Categoria | Exemplos do que pega | Decisão |
|---|---|---|
| `palavrao` | palavrões, inclusive disfarçados | devolve |
| `xingamento` | "idiota", "o professor é um palhaço" | devolve |
| `odio` | ofensas a grupos, generalizações | devolve |
| `ameaca` | desejar mal à pessoa | devolve |
| `aparencia` | aparência, idade, cheiro ("fede a cigarro") | devolve |
| `vida_pessoal` | relacionamentos, religião, política, bebida | devolve |
| `boato` | "dizem que…", "ouvi falar que…" | devolve |
| `denuncia` | acusações de assédio ou crime — orienta a procurar a **Ouvidoria da UFMG** ou o **DCE**, que têm canais sigilosos | devolve |
| `dado_pessoal` | e-mail não institucional, telefone, CPF, matrícula, placa, @ de rede social, endereço | devolve |
| `link` | encurtadores, excesso de links | devolve |
| `maiusculas` | texto majoritariamente em CAIXA ALTA | devolve |
| `sem_conteudo` | texto vazio de conteúdo, teclado aleatório | devolve |
| — | cópia de outra avaliação | devolve |
| `discriminacao` | acusação de machismo, racismo… | **segura** |

E-mails institucionais (`@*.ufmg.br`) são permitidos.

## Como funciona

### Normalização contra burlas
Antes de comparar, o texto passa por várias formas canônicas:

| Burla | Exemplo | Como é desfeita |
|---|---|---|
| Acentos | `imbécil` | remoção de acentos |
| Leet | `1d10t4` | `0→o 1→i 3→e 4→a 5→s 7→t @→a $→s` |
| Letras separadas | `b u r r o`, `m.e.r.d.a` | letras isoladas são unidas no próprio texto |
| Coringas | `p*ta` | `* # _ %` casam qualquer letra |
| Repetição | `buuurro` | letras repetidas comprimidas |
| Grau | `burrão`, `idiotinha` | sufixos removidos — só quando há um sufixo real |
| Grafia | `imbessil`, `kralho` | chave fonética do português (só igualdade exata) e lista de erros comuns |

### Contexto: dito *sobre a pessoa*
Palavras como "burro", "velho", "lixo", "palhaço" ou "fede" são comuns numa crítica legítima. Elas só contam quando ditas **sobre a pessoa** — por um pronome ("ele", "ela"), por "professor(a)", ou pelo **nome do docente avaliado**:

| Texto | Resultado |
|---|---|
| "o algoritmo que ele passou é burro" | ✅ publica |
| "o material é velho e a prova foi um lixo" | ✅ publica |
| "o código fede" | ✅ publica |
| "ele é muito burro" | ↩️ xingamento |
| "o Renato é um palhaço" | ↩️ xingamento |
| "gordo, feio e fede" | ↩️ aparência (vários termos próximos) |

### Por que não *fuzzy matching*
Aproximação por uma letra de diferença foi testada e descartada: barrava `horário` (≈ otário), `decente` (≈ demente), `escolha` (≈ escória), `carvalho` (≈ caralho). O filtro usa regras precisas — fonética exata e uma lista de erros de grafia — e uma lista de exceções (`ALLOWLIST`: porrada, corpo, porta, tcc, cassete, palha…).

## Avaliação

| Teste | Resultado |
|---|---|
| Dicionários pt_BR inteiros (290 mil palavras, isoladas) | nenhuma palavra comum barrada |
| ~50 mil palavras mais usadas em frases como "Ele é muito ___" | nenhuma barrada indevidamente |
| Críticas legítimas, incluindo jargão de computação | 68 de 68 publicadas |
| Ataques diversos, cada um na categoria certa | 81 de 81 barrados ou segurados |
| Desempenho | ~0,3 ms por comentário |

Parte das baterias (28 críticas e 24 ataques) foi escrita **depois** dos ajustes e usada como conjunto separado. Na primeira rodada ele acertou todas as críticas e 22 dos 24 ataques; os dois casos foram corrigidos e viraram teste. Todas as baterias estão em `tests/test_moderation.py`.

## Limites

- **Casos novos vão escapar de vez em quando** — gírias novas, sarcasmo, ataques bem construídos sem nenhuma palavra suspeita.
- **Rede de segurança:** toda denúncia vai para a fila do painel. Uma avaliação só sai do ar sozinha com denúncias de **4 redes (IPs) diferentes** — contar cookies não serve, porque um cookie anônimo novo sai de graça e 3 janelas anônimas bastariam para apagar qualquer crítica. A contagem por rede fica só em memória; depois que a moderação decide, as denúncias antigas não derrubam a avaliação de novo.
- **Campanhas:** o painel destaca dias com 5 ou mais avaliações do mesmo docente (últimos 30 dias), a favor ou contra.
- **Fricção:** quem quiser dizer algo que esbarra numa regra precisa reescrever. É o preço de não depender de revisão humana.
- Um classificador com IA poderia pegar sarcasmo, mas mandaria os comentários a um serviço externo. Não está implementado.

## Ajustando

Os léxicos e regras ficam no topo de `moderation.py`:

| O que | Onde |
|---|---|
| Palavrões, xingamentos, ofensas | `PROFANITY`, `INSULTS`, `SLURS` |
| Palavras que só contam ditas sobre a pessoa | `DIRECTED` |
| Frases por categoria (regex) | `PHRASES` |
| Erros de grafia comuns | `MISSPELLINGS` |
| Palavras que nunca devem ser barradas | `ALLOWLIST` |
| Textos mostrados à pessoa | `MESSAGES` |
| Decisão por categoria | `ACTION` |

Toda mudança deve vir com um caso em `tests/test_moderation.py` — tanto o ataque que passou quanto a crítica legítima parecida que **não** pode ser barrada:

```bash
python3 -m unittest tests.test_moderation -v
```

Para testar um texto à mão:

```bash
python3 -c "import moderation as m; print(m.analyze('ele é um b u r r o', professor='Fulano'))"
```

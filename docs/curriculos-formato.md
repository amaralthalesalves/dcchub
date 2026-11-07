# Formato de `data/curriculos.json`

Currículos dos cursos atendidos pelo DCC, para a grade sugerir "as disciplinas do seu período". Gerado por
`scripts/sync_curriculos.py`, que junta:

- os **relatórios de versão/percurso curricular da PROGRAD** (PDF oficial, extraído automaticamente);
- `data/curriculos_manual.json`: currículos que só existem como **imagem** no site do colegiado, transcritos à mão.

```jsonc
{
  "generatedAt": "2026-11-07T15:00:00+00:00",
  "curriculos": [
    {
      "id": "cc-2019",                       // único, minúsculo, [a-z0-9-]
      "curso": "Ciência da Computação",      // nome do curso, como a pessoa reconhece
      "versao": "2019",                      // rótulo curto da versão
      "percurso": "Bacharelado (núcleo avançado + núcleo geral)",  // opcional; quando o curso tem mais de um
      "rotulo": "Ciência da Computação — currículo 2019",           // texto do seletor
      "nota": "Para quem entrou antes de 2024.",                      // opcional, uma frase
      "fonte": { "tipo": "prograd", "url": "https://…pdf", "versaoOficial": "D-20192", "data": "2024-12-08" },
      //        tipo "imagem": transcrito de imagem do colegiado (url da imagem quando houver)
      "periodos": [
        {
          "n": 1,
          "disciplinas": [
            { "codigo": "DCC203", "nome": "Programação e Desenvolvimento de Software I", "natureza": "OB",
              "ch": 60, "depto": "DCC", "prereq": [] }
          ],
          "optativasCh": 0                   // carga de optativas sugerida neste período (0 se nenhuma)
        }
      ],
      "optativas": [                         // optativas do curso (listas oficiais); [] se a fonte não lista
        { "codigo": "DCC007", "nome": "Organização de Computadores II", "ch": 60, "depto": "DCC", "prereq": ["DCC006"] }
      ],
      "avisos": []                           // frases sobre limitações dos dados (ex.: códigos provisórios)
    }
  ]
}
```

Regras:

- `codigo`: o código oficial (ex. `MAT001`). Quando a fonte só tem um código provisório (`DCCXXX`, `INUxxx`), usar o código
  real do catálogo (`data/catalog.json`) **se o nome bater sem ambiguidade**; senão `null`, com o nome preenchido.
- `natureza`: `"OB"` obrigatória ou `"OP"` optativa.
- `depto`: prefixo do código (`DCC`, `MAT`, `EST`, `ICE`, `CAD`…); `null` se o código for `null` e não houver como saber.
- `nome`: em grafia normal (maiúsculas/minúsculas e acentos corretos), não em CAIXA ALTA.
- `prereq`: lista de códigos (pode ser vazia). Só pré-requisitos de disciplina, não "carga horária mínima".
- Nada inventado: se a fonte não diz, o campo fica vazio/`null` e um aviso explica.

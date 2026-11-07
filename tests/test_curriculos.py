"""Testes do conversor de relatórios curriculares da PROGRAD (scripts/sync_curriculos.py).

    python3 -m unittest tests.test_curriculos -v
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sync_curriculos as sc  # noqa: E402

# Trechos reais (encurtados) do `pdftotext -layout` do relatório de VERSÃO curricular de Ciência da Computação:
# dois percursos por atividade, nome quebrado, pré-requisitos quebrados, quebra de página com cabeçalho repetido
# e o quadro de carga adicional com o rótulo do percurso centralizado.
VERSAO = """\
                                                                                     Relatório de versão curricular


Curso:         02001 - CIÊNCIA DA COMPUTAÇÃO                                                                           Versão curricular:        D-20192


Situação:      Liberada

Disponibilidade nas subdivisões do curso:

    02001PD001/Presencial/DIURNO                                          Sim         Presencial           01-02       BACH./NÚCLEO AVANÇADO/NÚCLEO GERAL

Percursos curriculares que compõem a versão:

    01                                            CIENCIA DA COMPUTACAO-BACHARELADO - (M)
    01-02                                         ---BACH./NÚCLEO AVANÇADO/NÚCLEO GERAL
    01-01                                         ---BACH./NÚCLEO COMP./NÚCLEO AVANÇADO/NÚCLEO GERAL

Atividades acadêmicas:
                                                                                             RELATÓRIO DE VERSÃO CURRICULAR

08/12/2024 10.42.20                                                                                                                                                              Página 1 de 11
Pró-reitoria de Graduação
Tabela organizada por sugestão de período definido na versão curricular

  1º PERÍODO

                                                                          Créditos       Carga Horária
                                  Atividade Acadêmica                                                           Percurso   específica   Grupo      no       no      Pré-requisitos
                                                                          Mín. Máx.   Total   Teórica Prática
   DIG - CAD011 - ADMINISTRACAO                                            4    4      60       30      30       01-01         -          2        OB       OB             -
                                                                                                                 01-02         -          2        OB       OB             -
   DIG - DCC203 - PROGRAMAÇÃO E DESENVOLVIMENTO DE SOFTWARE I              4    4      60       60       0       01-01         -          2        OB       OB             -
                                                                                                                 01-02         -          2        OB       OB             -

                                                                                    RELATÓRIO DE VERSÃO CURRICULAR

08/12/2024 10.42.20                                                                                                                                                  Página 2 de 11
Pró-reitoria de Graduação
  4º PERÍODO

                                                                Créditos        Carga Horária
                                   Atividade Acadêmica                                                 Percurso   específica   Grupo      no       no            Pré-requisitos
                                                                Mín. Máx.    Total   Teórica Prática
   DIG - DCC212 - INTRODUÇÃO À CIÊNCIA DOS DADOS                 4    4        60      60       0       01-01         -          2        OB       OB           MAT001, DCC216,
                                                                                                                                                                DCC639, EST032
                                                                                                        01-02         -          2        OB       OB           MAT001, DCC216,
                                                                                                                                                                DCC639, EST032
   DIG - MAT034 - ÁLGEBRA A                                      4    4        60      60       0       01-01         -          2        OB       OB                  -
                                                                                                        01-02         -          2        OB       OB                   -

                                                                       Carga horária adicional do período

             Percurso                                                       Grupo de atividades                                                     Carga Horária
                                     02001-D-20192-7 - Núcleo Geral                                                                                        60
   01-01
                                     02001-D-20192-6 - Núcleo de Formação Complementar                                                                     120
                                     02001-D-20192-7 - Núcleo Geral                                                                                        60
   01-02
                                     02001-D-20192-10 - Optativas                                                                                         120

ATIVIDADES SEM PERÍODO SUGERIDO:
                                                                                     no Grupo
 DIG - DCC058 - PROJETOS E CONCEITOS DE SISTEMAS PARALELOS E          60      60       0       01-01        5        OP      OP              -
 DISTRIBUÍDOS                                                                                  01-02        11       OP      OP              -
 DIG - DCC072 - ENGENHARIA DE SOFTWARE II                             60      60       0       01-01        5        OP      OP           DCC603
                                                                                               01-02        11       OP      OP           DCC603
 DIG - DCC999 - SÓ DO OUTRO PERCURSO                                  60      60       0       01-01        5        OP      OP              -
 04G - ICE131 - PARTICIPAÇÃO EM EVENTOS I                             15      15       0       01-01        4        OP      OP              -
                                                                                               01-02        4        OP      OP              -
Quadro de integralização
"""

# Relatório de PERCURSO curricular (Ciência de Dados): uma linha por atividade; quando o nome quebra, o grupo
# desce para a linha de baixo e o resto do nome vem depois; a tabela do período continua após a quebra de página.
PERCURSO = """\
                                                                       Relatório de percurso curricular
Curso:                      CIÊNCIA DE DADOS                                                                                          Versão curricular:        V-20241
Identificador:              01-01                                                                                                     Situação:                 Vigente
Nome:                       BACHARELADO/NÚCLEO AVANÇADO/NÚCLEO GERAL

Atividades acadêmicas e carga horária adicional por período

26/02/2025 10.24.04                                                                                                                                                                                 Página 1 de 10
Pró-reitoria de Graduação
   1º PERÍODO

                                                                               Atividades acadêmicas obrigatórias no percurso
                             Atividade Acadêmica                    Créditos                                                                                                    Pré-requisitos
                                                                               Teórica Prática    Total                         Grupo
      DIG - DCC203 - PROGRAMAÇÃO E DESENVOLVIMENTO DE SOFTWARE I          4         60        0          60      N                5                 OB                     -                        -
      DIG - DCC011 - INTRODUÇÃO A BANCO DE DADOS                          4         60        0          60      N                5                 OB               DCC204, DCC638                 -

                                                                                         RELATÓRIO DE PERCURSO CURRICULAR

26/02/2025 10.24.04                                                                                                                                                                                 Página 2 de 10
Pró-reitoria de Graduação
                                                                               Atividades acadêmicas obrigatórias no percurso
                             Atividade Acadêmica                    Créditos                                                                                                    Pré-requisitos
      DIG - DCC011 - INTRODUÇÃO A BANCO DE DADOS                          4         60        0          60      N                5                 OB               DCC204, DCC638                 -
      DIG - MAT034 - ÁLGEBRA A                                            4         60        0          60      N                5                 OB                     -                        -

   7º PERÍODO
      DIG - ICE148 - APRENDIZADO DE MÁQUINA NÃO SUPERVISIONADO            4         45       15          60      N                5                 OB                  ICE147                      -

                                                                         Carga horária adicional do período

                                                                   Grupo de Atividades:                                Carga Horária
                                                                   Núcleo Geral                                             60
                                                                   Optativas                                                270

Atividades optativas no percurso
                                                                              Atividades acadêmicas optativas no percurso
      DIG - EST067 - ANÁLISE DE DADOS LONGITUDINAIS E EM                  4         60        0          60      N                                  OB
                                                                                                                                  10                               EST035, EST231                    -
      CONGLOMERADOS
      DIG - EST089 - ANÁLISE DE DADOS CATEGÓRICOS                         4         60        0          60      N                10                OB             EST035, EST231                    -
      PGG - ICE053 - MONITORIA I                                          2          0       30          30      S                9                 OB                     -                        -
Quadro de integralização
"""


def by_code(periodo: dict) -> dict:
    return {d["codigo"]: d for d in periodo["disciplinas"]}


class VersaoTest(unittest.TestCase):
    def setUp(self):
        self.res = sc.parse_report(VERSAO, "01-02")

    def test_cabecalho(self):
        info = self.res["info"]
        self.assertEqual(info["formato"], "versao")
        self.assertEqual(info["versaoOficial"], "D-20192")
        self.assertEqual(info["padrao"], "01-02")
        self.assertEqual(info["data"], "2024-12-08")
        self.assertEqual(info["percursos"]["01-02"], "BACH./NÚCLEO AVANÇADO/NÚCLEO GERAL")

    def test_periodos(self):
        per = self.res["periodos"]
        self.assertEqual(sorted(per), [1, 4])
        self.assertEqual(list(by_code(per[1])), ["CAD011", "DCC203"])
        cad = by_code(per[1])["CAD011"]
        self.assertEqual((cad["ch"], cad["natureza"], cad["depto"], cad["prereq"]), (60, "OB", "CAD", []))
        # pré-requisitos quebrados em duas linhas, após a quebra de página
        self.assertEqual(by_code(per[4])["DCC212"]["prereq"], ["MAT001", "DCC216", "DCC639", "EST032"])
        self.assertEqual(self.res["problemas"], [])

    def test_carga_adicional_por_percurso(self):
        self.assertEqual(self.res["periodos"][4]["optativasCh"], 180)               # 60 + 120 do 01-02
        self.assertEqual(sc.parse_report(VERSAO, "01-01")["periodos"][4]["optativasCh"], 180)

    def test_rotulo_centralizado_assimetrico(self):
        # Ciência de Dados V-20261, 8º período: 01-01 tem 1 linha (rótulo na linha), 01-02 tem 2 (rótulo entre elas)
        linhas = [
            "   01-01                      02011-V-20261-3 - Optativas                              300",
            "                              02011-V-20261-8 - Núcleo Complementar                    180",
            "   01-02",
            "                              02011-V-20261-10 - Optativas                             120",
        ]
        entries = [("label", i, sc.ADIC_LABEL.match(l).group(1)) if sc.ADIC_LABEL.match(l) else ("row", i, l)
                   for i, l in enumerate(linhas)]
        self.assertEqual(sc.resolve_adicionais(entries, "versao", "01-01"), (300, []))
        self.assertEqual(sc.resolve_adicionais(entries, "versao", "01-02"), (300, []))
        self.assertEqual(sc.resolve_adicionais(entries[:1], "versao", "01-02"), (0, []))

    def test_optativas(self):
        opt = {d["codigo"]: d for d in self.res["optativas"]}
        # DCC999 é só do 01-01; ICE131 não é disciplina (04G)
        self.assertEqual(sorted(opt), ["DCC058", "DCC072"])
        self.assertEqual(opt["DCC058"]["nome"], "PROJETOS E CONCEITOS DE SISTEMAS PARALELOS E DISTRIBUÍDOS")
        self.assertEqual(opt["DCC072"]["prereq"], ["DCC603"])
        self.assertIn("DCC999", [d["codigo"] for d in sc.parse_report(VERSAO, "01-01")["optativas"]])
        self.assertEqual(len(self.res["ignoradas"]), 1)


class PercursoTest(unittest.TestCase):
    def setUp(self):
        self.res = sc.parse_report(PERCURSO)

    def test_periodos(self):
        self.assertEqual(self.res["percurso"], "01-01")
        per = self.res["periodos"]
        # DCC011 aparece de novo depois da quebra de página: não duplica
        self.assertEqual(list(by_code(per[1])), ["DCC203", "DCC011", "MAT034"])
        self.assertEqual(by_code(per[1])["DCC011"]["prereq"], ["DCC204", "DCC638"])
        self.assertEqual(by_code(per[7])["ICE148"]["prereq"], ["ICE147"])
        self.assertEqual(per[7]["optativasCh"], 330)
        self.assertEqual(per[1]["optativasCh"], 0)
        self.assertEqual(self.res["problemas"], [])

    def test_nome_quebrado_e_optativas(self):
        opt = {d["codigo"]: d for d in self.res["optativas"]}
        self.assertEqual(sorted(opt), ["EST067", "EST089"])
        self.assertEqual(opt["EST067"]["nome"], "ANÁLISE DE DADOS LONGITUDINAIS E EM CONGLOMERADOS")
        self.assertEqual(opt["EST067"]["prereq"], ["EST035", "EST231"])
        self.assertEqual(opt["EST067"]["ch"], 60)


class NomesTest(unittest.TestCase):
    def test_proper_case(self):
        acc = sc.build_accent_map(["CÁLCULO DIFERENCIAL E INTEGRAL II"])
        self.assertEqual(sc.proper_case("CALCULO DIFERENCIAL E INTEGRAL I", acc), "Cálculo Diferencial e Integral I")
        self.assertEqual(sc.proper_case("INTRODUÇÃO À LÓGICA COMPUTACIONAL"), "Introdução à Lógica Computacional")
        self.assertEqual(sc.proper_case("ÁLGEBRA A"), "Álgebra A")
        self.assertEqual(sc.proper_case("INTERAÇÃO HUMANO-COMPUTADOR"), "Interação Humano-Computador")
        self.assertEqual(sc.proper_case("VISAO COMPUTACIONAL"), "Visao Computacional")  # não adivinha acento

    def test_nome_do_catalogo(self):
        cat = sc.Catalog({"courses": [
            {"id": "DCC212", "codes": ["DCC212", "ICE072"], "aliases": ["ICE072"], "kind": "regular",
             "name": "Introdução à Ciência dos Dados"}]})
        self.assertEqual(cat.name_for("DCC212", "INTRODUCAO A CIENCIA DOS DADOS"), "Introdução à Ciência dos Dados")
        # nome oficial diferente ("de" x "dos"): fica o da fonte
        self.assertIsNone(cat.name_for("ICE072", "INTRODUÇÃO À CIÊNCIA DE DADOS"))

    def test_build_curriculo(self):
        fonte = {"id": "cc-2019", "curso": "Ciência da Computação", "versao": "2019", "rotulo": "CC 2019",
                 "url": "https://exemplo/x.pdf"}
        cur = sc.build_curriculo(fonte, sc.parse_report(VERSAO, "01-02"), sc.Catalog(None), {})
        self.assertEqual(sc.validate(cur), [])
        self.assertEqual(cur["fonte"]["versaoOficial"], "D-20192")
        self.assertEqual([p["n"] for p in cur["periodos"]], [1, 4])
        self.assertEqual(cur["periodos"][0]["disciplinas"][0]["nome"], "Administracao")


VALIDO = {
    "id": "cc-2019", "curso": "Ciência da Computação", "versao": "2019", "rotulo": "CC 2019",
    "fonte": {"tipo": "prograd", "url": "https://x", "versaoOficial": "D-20192", "data": "2024-12-08"},
    "periodos": [
        {"n": 1, "disciplinas": [{"codigo": "DCC203", "nome": "Programação e Desenvolvimento de Software I",
                                  "natureza": "OB", "ch": 60, "depto": "DCC", "prereq": []}], "optativasCh": 0},
        {"n": 2, "disciplinas": [{"codigo": "DCC204", "nome": "Programação e Desenvolvimento de Software II",
                                  "natureza": "OB", "ch": 60, "depto": "DCC", "prereq": ["DCC203"]}],
         "optativasCh": 60},
    ],
    "optativas": [{"codigo": "DCC007", "nome": "Organização de Computadores II", "ch": 60, "depto": "DCC",
                   "prereq": ["DCC006"]}],
    "avisos": [],
}


class ValidateTest(unittest.TestCase):
    def broken(self, mutate) -> list[str]:
        cur = copy.deepcopy(VALIDO)
        mutate(cur)
        return sc.validate(cur)

    def test_valido(self):
        self.assertEqual(sc.validate(VALIDO), [])
        self.assertEqual(sc.validate_all([VALIDO]), [])

    def test_erros(self):
        def disc(c, i=0):
            return c["periodos"][i]["disciplinas"][0]
        casos = {
            "id inválido": lambda c: c.update(id="CC 2019"),
            "natureza": lambda c: disc(c).update(natureza="XX"),
            "codigo inválido": lambda c: disc(c).update(codigo="DCC20"),
            "fora de ordem": lambda c: c["periodos"].reverse(),
            "repetida": lambda c: disc(c, 1).update(codigo="DCC203", prereq=[]),
            "depto": lambda c: disc(c).update(depto="MAT"),
            "prereq inválido": lambda c: disc(c, 1).update(prereq=["DCC 203"]),
            "optativasCh": lambda c: c["periodos"][0].update(optativasCh=-1),
            "CAIXA ALTA": lambda c: disc(c).update(nome="PROGRAMAÇÃO"),
            "fonte.tipo": lambda c: c["fonte"].update(tipo="site"),
            "sem aviso": lambda c: disc(c).update(codigo=None, depto=None),
        }
        for trecho, mutate in casos.items():
            with self.subTest(trecho):
                errs = self.broken(mutate)
                self.assertTrue(any(trecho in e for e in errs), errs)

    def test_codigo_null_com_aviso(self):
        def mutate(c):
            c["periodos"][0]["disciplinas"][0].update(codigo=None, depto=None)
            c["avisos"] = ["Código provisório na fonte."]
        self.assertEqual(self.broken(mutate), [])

    def test_ids_repetidos(self):
        self.assertTrue(any("id repetido" in e for e in sc.validate_all([VALIDO, copy.deepcopy(VALIDO)])))


if __name__ == "__main__":
    unittest.main()

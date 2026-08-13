"""Guards de coerencia entre o que o projeto DIZ e o que ele FAZ.

Documentacao errada nao quebra nada — e por isso apodrece calada. Dois casos
concretos vinham da revisao de branch inteira (2026-08-13):

- `README.md` e `docs/extending.md` prometiam "82 tests" com 95 na suite, e a
  divisao por arquivo estava tres refactors atras;
- `render.py` precisa saber quais coletores EXISTEM pra poder dizer "este nem
  foi tentado", e a lista e duplicada la (o render nao importa o coletor de
  proposito). Duas listas sem guard divergem.

A regra que estes testes travam e a mesma da esteira inteira: nao afirmar o que
nao se mediu — inclusive quando a afirmacao esta num arquivo .md.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _fake_repo import fake_repo  # noqa: F401  (garante o sys.path dos scripts)

import collect
import render

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVOS_DE_TESTE = ("test_caminhos.py", "test_docs.py", "test_nao_medido.py",
                     "test_redacao.py", "test_render_hostil.py")


def contagem_real():
    """{arquivo: numero de testes} pela mesma descoberta que o CI usa."""
    carregador = unittest.TestLoader()
    return {nome: carregador.loadTestsFromName(nome[:-3]).countTestCases()
            for nome in ARQUIVOS_DE_TESTE}


class TestContagemDeTestesNaDoc(unittest.TestCase):

    def test_readme_declara_o_numero_real_de_testes(self):
        texto = (RAIZ / "README.md").read_text(encoding="utf-8")
        declarado = re.search(r"(\d+) tests", texto)
        self.assertIsNotNone(declarado, "README.md nao declara mais a contagem")
        self.assertEqual(int(declarado.group(1)), sum(contagem_real().values()))

    def test_extending_declara_o_numero_real_de_testes(self):
        texto = (RAIZ / "docs" / "extending.md").read_text(encoding="utf-8")
        declarado = re.search(r"(\d+) tests", texto)
        self.assertIsNotNone(declarado, "docs/extending.md nao declara mais a contagem")
        self.assertEqual(int(declarado.group(1)), sum(contagem_real().values()))

    def test_extending_declara_a_divisao_por_arquivo(self):
        """A divisao por arquivo tambem estava velha (doc: 6/18/28/30, real:
        6/20/37/32). Ela mora numa tabela justamente pra poder ser conferida
        aqui — prosa com numeros soltos nao da pra travar."""
        texto = (RAIZ / "docs" / "extending.md").read_text(encoding="utf-8")
        na_doc = {m.group(1): int(m.group(2)) for m in
                  re.finditer(r"\|\s*`(test_\w+\.py)`\s*\|\s*(\d+)\s*\|", texto)}
        self.assertEqual(na_doc, contagem_real())


class TestListaDeColetores(unittest.TestCase):

    def test_render_conhece_os_mesmos_coletores_que_o_collect(self):
        """`render.COLETORES_ESPERADOS` e o que permite a tela dizer "coletor
        fora desta coleta". Se o collect ganhar um coletor novo e o render nao
        souber, a ausencia dele volta a ser invisivel."""
        self.assertEqual(list(render.COLETORES_ESPERADOS), list(collect.COLLECTORS))

    def test_todo_coletor_da_lista_esta_no_registry(self):
        for nome in collect.COLLECTORS:
            self.assertIn(nome, collect.REGISTRY)


if __name__ == "__main__":
    unittest.main()

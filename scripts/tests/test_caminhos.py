"""Guards da contencao de caminho.

O ruch-x.toml mora DENTRO do repositorio auditado. Caminho que sai da raiz
transforma configuracao em leitura (ou execucao) de arquivo arbitrario da
maquina de quem esta auditando.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fake_repo import fake_repo

import collect


class TestCaminhoContido(unittest.TestCase):

    def test_relativo_dentro_da_raiz_passa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"scripts/build.sh": "echo oi\n"})
            self.assertIsNotNone(collect.caminho_contido(root, "scripts/build.sh"))

    def test_absoluto_e_recusado(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"a.py": "x = 1\n"})
            self.assertIsNone(collect.caminho_contido(root, "/etc/passwd"))

    def test_subir_de_diretorio_e_recusado(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"a.py": "x = 1\n"})
            Path(tmp, "vizinho.txt").write_text("x", encoding="utf-8")
            self.assertIsNone(collect.caminho_contido(root / "a.py", "../vizinho.txt"))

    def test_inexistente_e_recusado(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"a.py": "x = 1\n"})
            self.assertIsNone(collect.caminho_contido(root, "nao_existe.py"))

    def test_manage_py_fora_da_raiz_nao_e_executado(self):
        """python = /bin/bash + manage_py = /caminho/absoluto era execucao arbitraria."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"manage.py": "import sys; sys.exit(0)\n"})
            out = collect.collect_django(root, {"manage_py": "/etc/passwd"})
        # `apps is None` prova que o retorno cedo aconteceu (a contagem de
        # models/apps roda depois). E o campo sai como NAO MEDIDO: `[]` aqui
        # o painel leria como "medi e nao ha migration pendente" — credito de
        # graca em cima de uma configuracao recusada.
        self.assertIsNone(out["apps"])
        self.assertIsNone(out["pending_migrations"])
        self.assertIn("/etc/passwd", out.get("nao_medido", {}).get("pending_migrations", ""))


class TestFlagPFechaSysPath(unittest.TestCase):
    """`python -m X` poe o cwd (raiz do repo auditado) na frente do sys.path -
    um `radon.py`/`pip.py`/`pytest.py` na raiz do projeto medido rodaria como
    __main__ na maquina de quem audita (provado). `-P` fecha essa porta. O
    `manage.py` fica de fora porque precisa importar o proprio projeto
    auditado.

    Guard permanente da decisao: nao depende de radon/pip/pytest estarem
    instalados porque `collect.run` e mockado — o teste so confere o
    COMANDO que seria executado.
    """

    def test_dash_P_antes_do_m_em_radon_pip_pytest_mas_nao_no_manage(self):
        comandos = []

        def fake_run(cmd, *args, **kwargs):
            comandos.append(list(cmd))
            if cmd[0] == "git" and "log" in cmd and "--name-only" in cmd:
                return 0, "a.py\n", ""
            if "pip" in cmd:
                return 0, "[]", ""
            if "radon" in cmd:
                return 0, "{}", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{
                "a.py": "x = 1\n",
                "manage.py": "import sys\nsys.exit(0)\n",
                "requirements.txt": "django==5.0\n",
            })
            with mock.patch.object(collect, "run", side_effect=fake_run):
                collect.collect_quality(root, {})
                collect.hotspots(root, {})
                collect._deps_desatualizadas(root, {})
                collect.collect_django(root, {"python": sys.executable})
                collect.collect_tests(root, {"run_tests": True})

        chamadas_m = [c for c in comandos if "-m" in c]
        self.assertTrue(chamadas_m, "nenhuma chamada -m foi capturada")
        modulos_vistos = set()
        for cmd in chamadas_m:
            idx = cmd.index("-m")
            modulo = cmd[idx + 1]
            if modulo in ("radon", "pip", "pytest"):
                modulos_vistos.add(modulo)
                # A propriedade e "o repositorio auditado nao entra no
                # sys.path", nao "o flag chama -P". Sao dois os flags que
                # entregam isso, e qual deles aparece depende da versao do
                # interpretador (ver isolar_sys_path): afirmar o flag fazia
                # o teste passar em 3.11 e o coletor quebrar em 3.9.
                self.assertTrue(
                    {"-P", "-I"} & set(cmd[:idx]),
                    f"falta isolamento de sys.path antes de -m {modulo}: {cmd}")
        # Sem isto, remover a chamada de collect_tests() acima faria o
        # teste continuar OK mesmo que o guard do pytest tivesse sumido.
        self.assertEqual(modulos_vistos, {"radon", "pip", "pytest"})

        chamadas_manage = [c for c in comandos
                            if any(str(p).endswith("manage.py") for p in c)]
        self.assertTrue(chamadas_manage, "nenhuma chamada ao manage.py foi capturada")
        for cmd in chamadas_manage:
            self.assertNotIn("-P", cmd)
            self.assertNotIn("-I", cmd)


class TestIsolamentoPorVersaoDoPython(unittest.TestCase):
    """`-P` so existe no 3.11+, e o 3.9 ainda e o python de sistema no macOS.

    Regressao real (2026-08-26): rodando em 3.9, o interpretador abortava com
    `Unknown option: -P` e o texto do erro virava o motivo do "nao auditado"
    no painel — o relatorio perdia a medicao E explicava errado.
    """

    def test_python_novo_usa_P_e_python_velho_usa_I(self):
        with mock.patch.object(collect.sys, "version_info", (3, 11, 0)):
            self.assertEqual(collect.isolar_sys_path(), ["-P"])
        with mock.patch.object(collect.sys, "version_info", (3, 9, 6)):
            self.assertEqual(collect.isolar_sys_path(), ["-I"])

    def test_interpretador_de_outro_projeto_nunca_recebe_P(self):
        """A versao de `sys` e a NOSSA; o python do projeto auditado pode ser
        qualquer uma. `-I` funciona desde o 3.4, entao e o unico seguro ali."""
        with mock.patch.object(collect.sys, "version_info", (3, 12, 0)):
            self.assertEqual(collect.isolar_sys_path("/venv/do/projeto/bin/python"),
                             ["-I"])


class TestRelFalaALinguaDoGit(unittest.TestCase):
    """hotspots() cruza DOIS produtores de caminho: o churn do `git log
    --name-only` (separador "/" em qualquer plataforma) e o radon via
    rel() (str(Path) — "\\" no Windows). No Win o per_file.get(path)
    nunca casava: todo .py caia na heuristica e o painel declarava "nao
    auditado: sem radon" com o radon rodando (achado 2026-08-29, coleta
    real do ion no Win11). A propriedade: rel() devolve separador "/"
    em qualquer plataforma — a lingua do git, que e quem produz as
    chaves com que o resto do snapshot cruza."""

    def test_rel_devolve_barra_posix_em_qualquer_plataforma(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"apps/web/views.py": "x = 1\n"})
            nativo = str(Path(root) / "apps" / "web" / "views.py")
            self.assertEqual(collect.rel(nativo, root), "apps/web/views.py")

    def test_hotspot_py_sai_radon_mesmo_com_separador_nativo(self):
        """O radon devolve caminho no separador NATIVO do SO; o churn vem
        do git com "/". Arquivo .py medido pelo radon tem que sair
        metodo="radon" nas duas plataformas — cair na heuristica aqui e
        exatamente o falso-negativo do achado."""
        import json as _json
        import os

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "git":
                return 0, "apps/web/views.py\n", ""
            if "radon" in cmd:
                chave = os.path.join(str(fake_root), "apps", "web", "views.py")
                return 0, _json.dumps({chave: [{"complexity": 7}]}), ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as tmp:
            fake_root = fake_repo(tmp, **{"apps/web/views.py": "x = 1\n"})
            with mock.patch.object(collect, "run", side_effect=fake_run):
                rows = collect.hotspots(fake_root, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metodo"], "radon")
        self.assertEqual(rows[0]["complexity"], 7)


if __name__ == "__main__":
    unittest.main()

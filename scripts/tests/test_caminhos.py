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
        self.assertEqual(out["pending_migrations"], [])
        self.assertIsNone(out["apps"])


class TestFlagPFechaSysPath(unittest.TestCase):
    """`python -m X` poe o cwd (raiz do repo auditado) na frente do sys.path -
    um `radon.py`/`pip.py` na raiz do projeto medido rodaria como __main__ na
    maquina de quem audita (provado). `-P` fecha essa porta. O `manage.py`
    fica de fora porque precisa importar o proprio projeto auditado.

    Guard permanente da decisao: nao depende de radon/pip estarem
    instalados porque `collect.run` e mockado — o teste so confere o
    COMANDO que seria executado.
    """

    def test_dash_P_antes_do_m_em_radon_e_pip_mas_nao_no_manage(self):
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

        chamadas_m = [c for c in comandos if "-m" in c]
        self.assertTrue(chamadas_m, "nenhuma chamada -m foi capturada")
        for cmd in chamadas_m:
            idx = cmd.index("-m")
            modulo = cmd[idx + 1]
            if modulo in ("radon", "pip"):
                self.assertIn("-P", cmd[:idx],
                              f"falta -P antes de -m {modulo}: {cmd}")

        chamadas_manage = [c for c in comandos
                            if any(str(p).endswith("manage.py") for p in c)]
        self.assertTrue(chamadas_manage, "nenhuma chamada ao manage.py foi capturada")
        for cmd in chamadas_manage:
            self.assertNotIn("-P", cmd)


if __name__ == "__main__":
    unittest.main()

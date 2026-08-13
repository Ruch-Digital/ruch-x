"""Guards do contrato: null = nao medi; [] = medi e esta vazio.

Esta e a classe de bug que originou a frente (2026-08-13): comando que falha
sendo interpretado como medicao bem-sucedida. Um relatorio de auditoria que
afirma "0 avisos de seguranca" por causa de um traceback vale menos que
nenhum relatorio.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from _fake_repo import MANAGE_QUE_EXPLODE, MANAGE_QUE_MEDE, fake_repo

import collect


class TestCheckDjango(unittest.TestCase):

    def test_check_que_nao_sobe_vira_nao_medido(self):
        """ModuleNotFoundError no settings != 'zero avisos de seguranca'."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"manage.py": MANAGE_QUE_EXPLODE})
            out = collect.collect_django(root, {"python": sys.executable})
        self.assertIsNone(out["deploy_issues"])
        self.assertIn("deploy_issues", out.get("nao_medido", {}))
        self.assertTrue(out["nao_medido"]["deploy_issues"])

    def test_check_que_roda_com_exit_1_continua_reportando(self):
        """Guard contra sobrecorrecao.

        O check do proprio ion sai com exit 1 (drf_spectacular.E001) tendo
        medido tudo certo. Se o fix usar o returncode como sinal, este teste
        pega — e ele tem que continuar contando o security.W009.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"manage.py": MANAGE_QUE_MEDE})
            out = collect.collect_django(root, {"python": sys.executable})
        self.assertIsNotNone(out["deploy_issues"])
        self.assertEqual(len(out["deploy_issues"]), 1)
        self.assertEqual(out["deploy_issues"][0]["code"], "security.W009")
        self.assertEqual(len(out["other_issues"]), 1)
        self.assertNotIn("deploy_issues", out.get("nao_medido", {}))

    def test_migrations_que_falham_viram_nao_medido(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"manage.py": MANAGE_QUE_EXPLODE})
            out = collect.collect_django(root, {"python": sys.executable})
        self.assertIsNone(out["pending_migrations"])
        self.assertIn("pending_migrations", out.get("nao_medido", {}))

    def test_migrations_que_rodam_listam_pendentes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"manage.py": MANAGE_QUE_MEDE})
            out = collect.collect_django(root, {"python": sys.executable})
        self.assertEqual(out["pending_migrations"], ["app.0002_novo"])


class TestGit(unittest.TestCase):

    def test_diretorio_sem_git_nao_reporta_zero_commits(self):
        """Sem repositorio git, "0 commits em 30 dias" e mentira: nao houve medicao."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            out = collect.collect_git(root, {})
        self.assertIsNone(out["commits_30d"])
        self.assertIsNone(out["commits_90d"])
        self.assertIsNone(out["authors_30d"])
        self.assertIsNone(out["hotspots"])
        self.assertIn("commits_30d", out.get("nao_medido", {}))


if __name__ == "__main__":
    unittest.main()

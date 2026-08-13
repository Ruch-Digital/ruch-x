"""Guards do contrato: null = nao medi; [] = medi e esta vazio.

Esta e a classe de bug que originou a frente (2026-08-13): comando que falha
sendo interpretado como medicao bem-sucedida. Um relatorio de auditoria que
afirma "0 avisos de seguranca" por causa de um traceback vale menos que
nenhum relatorio.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


@unittest.skipUnless(shutil.which("git"), "git ausente")
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

    @staticmethod
    def _repo_git_real(tmp, **arquivos):
        """fake_repo() + git init/commit de verdade, pra passar do rev-parse
        e chegar em hotspots() — a "segunda porta" do contrato."""
        root = fake_repo(tmp, **arquivos)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
            cwd=root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "inicial"],
            cwd=root, check=True, capture_output=True,
        )
        return root

    def test_git_log_que_falha_marca_hotspots_nao_medido(self):
        """A "segunda porta": rev-parse passa, mas o `git log` do hotspots falha.

        Sem este teste, um refactor futuro pode trocar o `return None` de
        dentro de hotspots() por `return []` sem quebrar suite nenhuma.
        """
        original_run = collect.run

        def fake_run(cmd, *args, **kwargs):
            if "log" in cmd and "--name-only" in cmd:
                return 128, "", "fatal: bad object"
            return original_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_git_real(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "run", side_effect=fake_run):
                out = collect.collect_git(root, {})

        self.assertIsNone(out["hotspots"])
        self.assertIn("hotspots", out.get("nao_medido", {}))
        # so a porta do hotspots foi afetada — o resto do coletor segue medindo.
        self.assertIsInstance(out["commits_30d"], int)

    def test_churn_vazio_de_verdade_continua_lista_vazia(self):
        """Repo real, sem nenhum arquivo de SOURCE_EXTS commitado: `[]` e
        medicao valida, nao ausencia de medicao."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo_git_real(tmp, **{"README.md": "so documentacao\n"})
            out = collect.collect_git(root, {})

        self.assertEqual(out["hotspots"], [])
        self.assertNotIn("hotspots", out.get("nao_medido", {}))


class TestSeguranca(unittest.TestCase):

    def test_sem_git_nao_afirma_zero_segredos(self):
        """O achado mais caro do relatorio nao pode nascer de um comando que falhou."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            gov = collect.collect_governance(root, {})
        self.assertIsNone(gov["segredos_commitados"])
        self.assertIn("segredos_commitados", gov.get("nao_medido", {}))

    @staticmethod
    def _fake_run_branch_protection(protection_rc, protection_se):
        """Simula `gh repo view` (sempre sucesso) seguido de `gh api .../protection`
        com o retorno dado. Mesmo padrao do TestGit: patch em collect.run,
        distinguindo o comando pelo conteudo do argv."""
        repo_view_json = json.dumps({
            "defaultBranchRef": {"name": "main"},
            "nameWithOwner": "org/repo",
            "visibility": "PUBLIC",
        })

        def fake_run(cmd, *args, **kwargs):
            if "repo" in cmd and "view" in cmd:
                return 0, repo_view_json, ""
            if "api" in cmd and "protection" in cmd[-1]:
                return protection_rc, "", protection_se
            raise AssertionError(f"comando inesperado em _branch_protection: {cmd}")

        return fake_run

    def test_404_e_a_unica_resposta_que_confirma_branch_desprotegida(self):
        """404 quer dizer que a branch REALMENTE nao tem protecao."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(
                     collect, "run",
                     side_effect=self._fake_run_branch_protection(
                         1, "gh: Not Found (HTTP 404)")):
                out = collect._branch_protection(root)
        self.assertTrue(out["disponivel"])
        self.assertFalse(out["protegido"])
        self.assertNotIn("motivo", out)

    def test_403_rate_limit_nao_vira_branch_desprotegida(self):
        """403/rate-limit != 'olhei e nao tem protecao' — vira nao-medido."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(
                     collect, "run",
                     side_effect=self._fake_run_branch_protection(
                         1, "HTTP 403: API rate limit exceeded")):
                out = collect._branch_protection(root)
        self.assertFalse(out["disponivel"])
        self.assertIsNone(out["protegido"])
        self.assertTrue(out.get("motivo"))


if __name__ == "__main__":
    unittest.main()

"""Guards do contrato: null = nao medi; [] = medi e esta vazio.

Esta e a classe de bug que originou a frente (2026-08-13): comando que falha
sendo interpretado como medicao bem-sucedida. Um relatorio de auditoria que
afirma "0 avisos de seguranca" por causa de um traceback vale menos que
nenhum relatorio.
"""

from __future__ import annotations

import contextlib
import io
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

    def test_projeto_sem_manage_py_nao_ganha_migrations_aplicadas(self):
        """`[]` no retorno cedo virava CREDITO.

        O criterio "migrations aplicadas" le `len(pend) == 0` e dava o ponto
        por atendido em repositorio que nem Django e — sozinho, tirava o eixo
        Confiabilidade de 0%/F pra 40%/D. Sem manage.py nao houve medicao.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"main.go": "package main\n"})
            out = collect.collect_django(root, {})
        self.assertIsNone(out["pending_migrations"])
        motivo = out.get("nao_medido", {}).get("pending_migrations", "")
        self.assertIn("sem manage.py", motivo)

    def test_manage_py_configurado_que_nao_resolve_se_distingue_de_sem_django(self):
        """Typo no toml nao pode se parecer com "projeto sem Django".

        O manage.py da raiz EXISTE aqui: se a coleta caisse no default em
        silencio, a configuracao quebrada ficaria invisivel. O motivo tem que
        nomear a chave e o valor que nao resolveu.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"manage.py": MANAGE_QUE_MEDE})
            out = collect.collect_django(root, {"manage_py": "backend/manage.py"})
        self.assertIsNone(out["pending_migrations"])
        motivo = out.get("nao_medido", {}).get("pending_migrations", "")
        self.assertIn("manage_py configurado", motivo)
        self.assertIn("backend/manage.py", motivo)


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

    def test_404_embutido_em_numero_maior_nao_vira_branch_desprotegida(self):
        """"404" solto dentro de outro numero (epoch, id) nao e o HTTP 404.

        Sem ancora de palavra no regex, `re.search(r"404|not found", se)`
        casa em qualquer substring — inclusive "404" no meio de
        "1754049600" (epoch de reset de rate limit). Isso reintroduz
        "acusar sem ter olhado" pelo lado do match POSITIVO frouxo, em vez
        de pelo lado do "qualquer falha vira desprotegida" (o bug original).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(
                     collect, "run",
                     side_effect=self._fake_run_branch_protection(
                         1, "API rate limit exceeded; resets at 1754049600")):
                out = collect._branch_protection(root)
        self.assertFalse(out["disponivel"])
        self.assertIsNone(out["protegido"])
        self.assertTrue(out.get("motivo"))


class TestCobertura(unittest.TestCase):

    def test_cobertura_declara_idade_do_arquivo_lido(self):
        """Cobertura de 3 meses atras nao pode ser reportada como o estado de hoje."""
        import os
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{
                "coverage.json": json.dumps({"totals": {"percent_covered": 42.0}}),
            })
            velho = time.time() - 90 * 86400
            os.utime(root / "coverage.json", (velho, velho))
            out = collect.collect_tests(root, {})
        self.assertEqual(out["coverage_pct"], 42.0)
        self.assertIsNotNone(out["coverage_age_days"])
        self.assertGreaterEqual(out["coverage_age_days"], 89)


class TestComplexidade(unittest.TestCase):

    def test_complexidade_declara_o_metodo(self):
        """Sem radon o numero vem de contagem de ramificacao — o painel tem que dizer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.js": "if (a) { b(); } else { c(); }\n"})
            out = collect.collect_quality(root, {})
        if out["complexity"] is not None:
            self.assertIn(out["complexity"].get("metodo"), ("radon", "heuristica"))

    @unittest.skipUnless(shutil.which("git"), "git ausente")
    def test_hotspots_sem_radon_marca_metodo_heuristica(self):
        """Forca o caminho da heuristica (radon falhando) num repo git real,
        pra nao depender do radon estar ou nao instalado na maquina que roda
        a suite. Cada linha do mapa tem que dizer de onde veio o numero."""
        original_run = collect.run

        def fake_run(cmd, *args, **kwargs):
            if "radon" in cmd:
                return 1, "", "radon nao encontrado"
            return original_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = TestGit._repo_git_real(tmp, **{
                "app.js": "if (a) { b(); } else if (c) { d(); } else { e(); }\n",
            })
            with mock.patch.object(collect, "run", side_effect=fake_run):
                rows = collect.hotspots(root, {})

        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["metodo"], "heuristica")

    def test_radon_que_falha_marca_complexity_nao_medido(self):
        """Radon ausente/quebrado (rc 127) nao pode deixar "complexity" em
        None silencioso — mesma classe de bug do total_loc do scc/cloc."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "run",
                                    return_value=(127, "", "comando nao encontrado")):
                out = collect.collect_quality(root, {})
        self.assertIsNone(out["complexity"])
        self.assertIn("complexity", out.get("nao_medido", {}))


class TestContagemCodigo(unittest.TestCase):

    def test_sem_scc_nem_cloc_o_contador_proprio_ainda_mede(self):
        """Se o scc nao existir na maquina, o fallback e o unico caminho que
        mede alguma coisa — ele nao pode ficar inalcancavel."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "a = 1\nb = 2\nc = 3\n"})
            with mock.patch.object(collect, "has", return_value=False):
                out = collect.collect_code(root, {})
        self.assertEqual(out["tool"], "builtin")
        self.assertIsNotNone(out["total_loc"])
        self.assertGreater(out["total_loc"], 0)

    def test_scc_instalado_que_falha_nao_vira_projeto_com_zero_linhas(self):
        """scc presente mas quebrado (crash, rc != 0) nao e um projeto vazio —
        e uma medicao que nao aconteceu."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "a = 1\n"})
            with mock.patch.object(collect, "has", side_effect=lambda b: b == "scc"), \
                 mock.patch.object(collect, "run",
                                    return_value=(1, "", "scc: panic: runtime error")):
                out = collect.collect_code(root, {})
        self.assertIsNone(out["total_loc"])
        self.assertIn("total_loc", out.get("nao_medido", {}))
        self.assertIsNone(out["total_files"])
        self.assertIn("total_files", out.get("nao_medido", {}))

    def test_cloc_instalado_que_falha_nao_vira_projeto_com_zero_linhas(self):
        """Mesma classe de bug do scc, so que no fallback: sem scc mas com
        cloc quebrado (rc != 0), o relatorio nao pode afirmar "0 linhas"."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "a = 1\n"})
            with mock.patch.object(collect, "has", side_effect=lambda b: b == "cloc"), \
                 mock.patch.object(collect, "run",
                                    return_value=(1, "", "cloc: erro")):
                out = collect.collect_code(root, {})
        self.assertIsNone(out["total_loc"])
        self.assertIn("total_loc", out.get("nao_medido", {}))
        self.assertIsNone(out["total_files"])
        self.assertIn("total_files", out.get("nao_medido", {}))


# ----------------------------------------------------------------------
# Fix wave final: achados da revisao de branch inteira. Aqui ficam os que
# moram no coletor.
# ----------------------------------------------------------------------

class TestCritical2TotalDeDependenciasNode(unittest.TestCase):
    """O ramo npm gravava `desatualizadas` e nunca `total`.

    Sem denominador, `pct_velhas` era sempre None em `render.py` e o criterio
    inteiro saia do calculo: um projeto Node com 37 dependencias velhas
    tirava a MESMA nota de Seguranca de um com tudo em dia. Falta de medicao
    PREMIANDO — o contrato da ferramenta invertido. O ramo pip ja fazia a
    segunda chamada; o npm parou no meio.
    """

    PACKAGE_JSON = json.dumps({
        "name": "alvo",
        "dependencies": {f"dep{i}": "^1.0.0" for i in range(30)},
        "devDependencies": {f"dev{i}": "^1.0.0" for i in range(10)},
    })

    @staticmethod
    def _npm_outdated(quantas):
        return json.dumps({f"dep{i}": {"current": "1.0.0", "wanted": "2.0.0",
                                       "latest": "2.0.0"} for i in range(quantas)})

    def _medir(self, quantas_velhas):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"package.json": self.PACKAGE_JSON})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run",
                                    return_value=(1, self._npm_outdated(quantas_velhas), "")):
                return collect._deps_desatualizadas(root, {})

    def test_npm_grava_o_total_de_dependencias_diretas(self):
        deps = self._medir(37)
        self.assertEqual(deps["ferramenta"], "npm")
        self.assertEqual(deps["desatualizadas"], 37)
        self.assertEqual(deps["total"], 40, "30 dependencies + 10 devDependencies")

    def test_projeto_em_dia_tambem_tem_denominador(self):
        """Mutacao inversa: `{}` do npm e medicao valida (nada desatualizado),
        e o total tem que vir junto — senao o criterio some justamente no
        caso em que ele PASSARIA."""
        deps = self._medir(0)
        self.assertEqual(deps["desatualizadas"], 0)
        self.assertEqual(deps["total"], 40)
        self.assertNotIn("total", deps.get("nao_medido", {}))

    def test_package_json_ilegivel_marca_total_nao_medido(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"package.json": "{ isto nao e json"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run",
                                    return_value=(1, self._npm_outdated(3), "")):
                deps = collect._deps_desatualizadas(root, {})
        self.assertEqual(deps["desatualizadas"], 3)
        self.assertIsNone(deps["total"])
        self.assertIn("total", deps.get("nao_medido", {}))

    def test_npm_que_falha_nao_vira_zero_dependencias_velhas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"package.json": self.PACKAGE_JSON})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run",
                                    return_value=(127, "", "npm: comando nao encontrado")):
                deps = collect._deps_desatualizadas(root, {})
        self.assertIsNone(deps["desatualizadas"])
        self.assertIn("desatualizadas", deps.get("nao_medido", {}))

    def test_sem_manifesto_nenhum_o_criterio_diz_por_que(self):
        """Repositorio sem requirements/pyproject/package.json: o painel
        mostrava um "(—/—)" pelado, sem o leitor descobrir se ninguem olhou
        ou se nao havia o que olhar."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"main.go": "package main\n"})
            deps = collect._deps_desatualizadas(root, {})
        self.assertIsNone(deps["desatualizadas"])
        self.assertIn("manifesto", deps.get("nao_medido", {}).get("desatualizadas", ""))


class TestImportant6MotivoOndeFaltava(unittest.TestCase):
    """Os unicos pontos do arquivo que nao seguiam a regra que o arquivo
    documenta: comando presente que falha ou devolve saida ilegivel deixava
    o campo em `None` MUDO, sem entrada em `nao_medido`."""

    def test_ruff_que_falha_deixa_motivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", side_effect=lambda b: b == "ruff"), \
                 mock.patch.object(collect, "run",
                                    return_value=(2, "", "ruff: panic: unexpected")):
                out = collect.collect_quality(root, {})
        self.assertIsNone(out["ruff"])
        self.assertIn("ruff", out.get("nao_medido", {}))
        self.assertIn("panic", out["nao_medido"]["ruff"])

    def test_ruff_com_zero_violacoes_continua_sendo_medicao(self):
        """Mutacao inversa: `[]` e "medi e esta limpo", nao "nao medi"."""

        def fake_run(cmd, *args, **kwargs):
            # so o ruff responde JSON aqui; o radon do mesmo coletor cai no
            # caminho de ausencia (o que este teste nao esta medindo).
            if "ruff" in cmd:
                return 0, "[]", ""
            return 127, "", "comando nao encontrado"

        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", side_effect=lambda b: b == "ruff"), \
                 mock.patch.object(collect, "run", side_effect=fake_run):
                out = collect.collect_quality(root, {})
        self.assertEqual(out["ruff"]["total"], 0)
        self.assertNotIn("ruff", out.get("nao_medido", {}))

    def test_radon_com_json_de_forma_errada_nao_derruba_o_coletor(self):
        """JSON valido com a FORMA errada (lista onde se espera objeto)
        estourava `.items()` e matava o coletor `quality` inteiro — o campo
        virava um `errors[quality]`, e nao um `nao_medido` com motivo."""

        def fake_run(cmd, *args, **kwargs):
            if "radon" in cmd:
                return 0, "[1, 2, 3]", ""
            return 127, "", "comando nao encontrado"

        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", return_value=False), \
                 mock.patch.object(collect, "run", side_effect=fake_run):
                out = collect.collect_quality(root, {})
        self.assertIsNone(out["complexity"])
        self.assertIn("complexity", out.get("nao_medido", {}))

    def test_sem_linter_nenhum_tambem_e_nao_medido(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "has", return_value=False):
                out = collect.collect_quality(root, {})
        self.assertIsNone(out["ruff"])
        self.assertIn("ruff", out.get("nao_medido", {}))

    def test_suite_que_nem_roda_deixa_motivo(self):
        """`rc`/`se` do pytest eram descartados: suite que crasha na coleta
        (import quebrado, timeout) nao deixava rastro nenhum."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(
                    collect, "run",
                    return_value=(2, "ERROR collecting", "ImportError: no module named x")):
                out = collect.collect_tests(root, {"run_tests": True})
        self.assertIsNone(out["test_count"])
        self.assertIn("test_count", out.get("nao_medido", {}))
        self.assertIn("ImportError", out["nao_medido"]["test_count"])

    def test_suite_que_roda_com_teste_falhando_continua_contando(self):
        """Guard contra sobrecorrecao: pytest sai 1 com teste FALHANDO, e ali
        o "N passed" existe — o returncode nao pode virar o sinal."""
        saida = "F...\n1 failed, 12 passed in 3.20s\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "run", return_value=(1, saida, "")):
                out = collect.collect_tests(root, {"run_tests": True})
        self.assertEqual(out["test_count"], 12)
        self.assertNotIn("test_count", out.get("nao_medido", {}))

    @unittest.skipUnless(shutil.which("git"), "git ausente")
    def test_rev_parse_do_commit_que_falha_deixa_motivo(self):
        """`commit: ""` era campo vazio sem motivo — igual a "nao perguntei"."""
        original_run = collect.run

        def fake_run(cmd, *args, **kwargs):
            if "rev-parse" in cmd and "--short" in cmd:
                return 128, "", "fatal: ambiguous argument 'HEAD'"
            return original_run(cmd, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            root = TestGit._repo_git_real(tmp, **{"app.py": "x = 1\n"})
            with mock.patch.object(collect, "run", side_effect=fake_run):
                out = collect.collect_git(root, {})
        self.assertIsNone(out["commit"])
        self.assertIn("commit", out.get("nao_medido", {}))
        self.assertIsNotNone(out["branch"], "so a porta do commit foi afetada")


class TestMinor6DjangoAusente(unittest.TestCase):
    """`collect_django` saia cedo sem manage.py deixando `deploy_issues: []`
    e `other_issues: []` sem marcar — e o rotulo do painel entao culpava o
    motivo errado ("settings de dev") num repositorio que nem Django e."""

    def test_repo_sem_manage_py_nao_afirma_zero_avisos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"main.go": "package main\n"})
            out = collect.collect_django(root, {})
        self.assertIsNone(out["deploy_issues"])
        self.assertIsNone(out["other_issues"])
        for campo in ("pending_migrations", "deploy_issues", "other_issues"):
            self.assertIn("sem manage.py", out["nao_medido"][campo])


class TestMinor2SaidaDaFerramentaIgnorada(unittest.TestCase):
    """`.ruch-x` nao estava em IGNORE_DIRS (so o legado `.metricas`): o
    `dashboard.html` que a propria ferramenta gera virava o stack detectado
    — o snapshot commitado deste repositorio dizia `stack: ['HTML']` pra uma
    ferramenta escrita em Python."""

    def test_dashboard_gerado_nao_vira_o_stack_do_projeto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{
                "scripts/app.py": "x = 1\n",
                ".ruch-x/dashboard.html": "<html><body>painel</body></html>\n",
                ".ruch-x/2026-01-01.json": "{}\n",
            })
            out = collect.collect_stack(root, {})
        self.assertEqual(out["detected"], ["Python"])
        self.assertNotIn("HTML", out["detected"])

    def test_html_de_verdade_do_projeto_continua_contando(self):
        """Mutacao inversa: so a pasta da ferramenta e ignorada — template do
        projeto continua sendo codigo-fonte."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"templates/index.html": "<h1>oi</h1>\n"})
            out = collect.collect_stack(root, {})
        self.assertEqual(out["detected"], ["HTML"])


class TestImportant2OnlyComTypo(unittest.TestCase):
    """`--only gouvernance,cod` filtrava os nomes invalidos em silencio e
    saia com exit 0: `collectors_run: []`, `errors: {}` — e o dashboard desse
    snapshot dizia "ok — Nenhum alerta nos limiares configurados"."""

    def test_only_sem_nenhum_nome_valido_falha(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["collect.py", "--root", tmp, "--only", "gouvernance,cod"]
            with mock.patch.object(sys, "argv", argv), \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit):
                    collect.main()
        self.assertIn("gouvernance", err.getvalue())
        self.assertIn("cod", err.getvalue())

    def test_nome_desconhecido_avisa_mesmo_com_um_valido(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["collect.py", "--root", tmp, "--only", "gouvernance,stack"]
            with mock.patch.object(sys, "argv", argv), \
                 contextlib.redirect_stderr(io.StringIO()) as err, \
                 contextlib.redirect_stdout(io.StringIO()):
                collect.main()  # nao pode falhar: "stack" e valido
        self.assertIn("gouvernance", err.getvalue())

    def test_skip_com_typo_tambem_avisa(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["collect.py", "--root", tmp, "--only", "stack", "--skip", "gitt"]
            with mock.patch.object(sys, "argv", argv), \
                 contextlib.redirect_stderr(io.StringIO()) as err, \
                 contextlib.redirect_stdout(io.StringIO()):
                collect.main()
        self.assertIn("gitt", err.getvalue())


# ----------------------------------------------------------------------
# Calibragem de criterios (2026-08-13): 5 pontos onde o auditor media certo
# e rotulava errado, achados de uma verificacao independente linha a linha.
# ----------------------------------------------------------------------

class TestAjuste3PermissionsNoJob(unittest.TestCase):
    """`_analisa_workflows` registra tambem quem restringe o JOB, mesmo sem
    bloco no topo — o criterio (bloco no topo) nao muda, so o dado extra que
    o achado usa pra nao pintar "sem permissions" como "sem nada"."""

    SEM_NADA = "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"

    COM_JOB_RESTRITO = (
        "name: Publish\n"
        "on: push\n"
        "jobs:\n"
        "  publish:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      packages: write\n"
        "    steps:\n"
        "      - run: echo hi\n"
    )

    COM_TOPO = (
        "permissions:\n"
        "  contents: read\n"
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
    )

    def test_job_restrito_entra_em_permissions_no_job_e_continua_em_sem_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{".github/workflows/publish.yml": self.COM_JOB_RESTRITO})
            out = collect._analisa_workflows(root)
        self.assertIn("publish.yml", out["sem_permissions"])
        self.assertIn("publish.yml", out["permissions_no_job"])

    def test_workflow_sem_permissions_nenhuma_nao_entra_em_permissions_no_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{".github/workflows/ci.yml": self.SEM_NADA})
            out = collect._analisa_workflows(root)
        self.assertIn("ci.yml", out["sem_permissions"])
        self.assertNotIn("ci.yml", out["permissions_no_job"])

    def test_workflow_com_bloco_no_topo_nao_entra_em_nenhuma_lista(self):
        """O criterio continua sendo o bloco no topo — job restrito nao
        substitui isso, so complementa o achado quando falta."""
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{".github/workflows/ci.yml": self.COM_TOPO})
            out = collect._analisa_workflows(root)
        self.assertNotIn("ci.yml", out["sem_permissions"])
        self.assertNotIn("ci.yml", out["permissions_no_job"])


class TestAjuste5CIComCancelado(unittest.TestCase):
    """`success_rate` continua sucesso/concluido (cancelar nao e falhar) —
    mas o coletor passa a gravar quantos runs foram cancelados na janela, e
    quais deles tinham um job de deploy ja bem-sucedido no MESMO run (o caso
    mais caro: "CI verde" escondendo que o job de teste foi cancelado mas o
    deploy subiu do mesmo jeito)."""

    @staticmethod
    def _run_list_json(runs):
        import json as _json
        return _json.dumps(runs)

    def _fake_run(self, runs, jobs_por_id=None):
        jobs_por_id = jobs_por_id or {}

        def fake(cmd, *args, **kwargs):
            if "run" in cmd and "list" in cmd:
                return 0, self._run_list_json(runs), ""
            if "run" in cmd and "view" in cmd:
                run_id = int(cmd[cmd.index("view") + 1])
                jobs = jobs_por_id.get(run_id)
                if jobs is None:
                    return 1, "", "gh: not found"
                return 0, json.dumps({"jobs": jobs}), ""
            raise AssertionError(f"comando inesperado: {cmd}")
        return fake

    def _runs(self, *conclusoes, workflow="CI"):
        base = "2026-08-13T09:00:00Z"
        return [
            {"conclusion": c, "createdAt": base, "updatedAt": base,
             "displayTitle": f"run {i}", "workflowName": workflow,
             "headBranch": "main", "event": "push", "databaseId": 1000 + i}
            for i, c in enumerate(conclusoes)
        ]

    def test_success_rate_ignora_cancelado_no_calculo(self):
        """Guard contra sobrecorrecao: a formula NAO muda quando ha cancelado
        — continua sucesso sobre concluido (sucesso+falha)."""
        runs = self._runs("success", "success", "success", "failure", "cancelled")
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"x.txt": "x"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run", side_effect=self._fake_run(runs)):
                out = collect.collect_ci(root, {})
        # 3 sucesso, 1 falha, 1 cancelado: taxa e 3/4 = 75.0, nao 3/5 = 60.0
        self.assertEqual(out["success_rate"], 75.0)
        self.assertEqual(out["cancelados"], 1)

    def test_sem_cancelado_a_taxa_e_identica_a_de_antes(self):
        """Mutacao inversa do guard: sem cancelado, `cancelados` e 0 e a taxa
        nao muda nada."""
        runs = self._runs("success", "success", "failure")
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"x.txt": "x"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run", side_effect=self._fake_run(runs)):
                out = collect.collect_ci(root, {})
        self.assertEqual(out["success_rate"], round(200 / 3, 1))
        self.assertEqual(out["cancelados"], 0)
        self.assertEqual(out["cancelados_com_deploy_ok"], [])

    def test_cancelado_com_deploy_bem_sucedido_no_mesmo_run_e_sinalizado(self):
        """O caso que mais importa: job de teste cancelado, job de deploy do
        MESMO run ja tinha terminado com sucesso."""
        runs = self._runs("success", "cancelled")
        jobs = {1001: [{"name": "test", "conclusion": "cancelled"},
                       {"name": "deploy", "conclusion": "success"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"x.txt": "x"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run", side_effect=self._fake_run(runs, jobs)):
                out = collect.collect_ci(root, {})
        self.assertEqual(out["cancelados"], 1)
        self.assertEqual(len(out["cancelados_com_deploy_ok"]), 1)

    def test_cancelado_sem_job_de_deploy_nao_e_sinalizado(self):
        """Guard contra sobrecorrecao: cancelado comum (sem deploy no mesmo
        run) nao entra na lista de risco."""
        runs = self._runs("cancelled")
        jobs = {1000: [{"name": "test", "conclusion": "cancelled"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"x.txt": "x"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run", side_effect=self._fake_run(runs, jobs)):
                out = collect.collect_ci(root, {})
        self.assertEqual(out["cancelados"], 1)
        self.assertEqual(out["cancelados_com_deploy_ok"], [])

    def test_job_view_que_falha_nao_vira_falso_negativo(self):
        """`gh run view` fora do ar nao pode virar "nao tinha deploy" — vira
        omissao (o run simplesmente nao aparece na lista de risco), nunca
        uma afirmacao negativa que ninguem apurou."""
        runs = self._runs("cancelled")
        with tempfile.TemporaryDirectory() as tmp:
            root = fake_repo(tmp, **{"x.txt": "x"})
            with mock.patch.object(collect, "has", return_value=True), \
                 mock.patch.object(collect, "run", side_effect=self._fake_run(runs, {})):
                out = collect.collect_ci(root, {})
        self.assertEqual(out["cancelados"], 1)
        self.assertEqual(out["cancelados_com_deploy_ok"], [])


if __name__ == "__main__":
    unittest.main()

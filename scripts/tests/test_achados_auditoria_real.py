"""Os dois falsos negativos achados auditando um projeto real (2026-08-26).

Nenhum dos dois derrubava o script: os dois faziam o relatorio AFIRMAR o que
nao era verdade, que numa auditoria custa mais caro que quebrar.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collect  # noqa: E402
import render  # noqa: E402


class TestRunbookForaDePastaDedicada(unittest.TestCase):
    """Projeto pequeno guarda o runbook como ARQUIVO, nao como pasta.

    O criterio existe pra saber se o passo a passo do incidente esta escrito.
    Cobrar a arvore de diretorios fazia um projeto com dois runbooks completos
    em docs/ sair como "sem runbooks", com uma linha P1 mandando escrever o
    que ja existia.
    """

    def _governanca(self, arquivos):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for caminho, conteudo in arquivos.items():
                alvo = root / caminho
                alvo.parent.mkdir(parents=True, exist_ok=True)
                alvo.write_text(conteudo)
            return collect.collect_governance(root, {})

    def test_arquivo_de_deploy_em_docs_conta_como_runbook(self):
        g = self._governanca({"docs/deploy-easypanel.md": "# Deploy\n1. ...\n"})
        self.assertEqual(g["docs"]["runbooks"], "docs/deploy-easypanel.md")

    def test_runbook_na_raiz_tambem_conta(self):
        g = self._governanca({"RUNBOOK.md": "# Incidentes\n"})
        self.assertEqual(g["docs"]["runbooks"], "RUNBOOK.md")

    def test_pasta_dedicada_continua_valendo_e_tem_precedencia(self):
        g = self._governanca({"docs/runbooks/api-fora.md": "# API fora\n",
                              "docs/deploy.md": "# Deploy\n"})
        self.assertEqual(g["docs"]["runbooks"], "docs/runbooks")

    def test_doc_qualquer_nao_vira_runbook(self):
        """Falso POSITIVO aqui e tao ruim quanto o negativo: dizer que ha
        runbook onde nao ha manda o time dormir tranquilo sem ter."""
        g = self._governanca({"docs/api-integracao.md": "# API\n",
                              "README.md": "# Projeto\n"})
        self.assertIsNone(g["docs"]["runbooks"])


class TestDoraSemDeployNoCI(unittest.TestCase):
    """Deploy por botao no painel (Easypanel/Coolify) nao e ambiente faltando.

    `workflows_de_deploy: []` = o coletor RODOU e o projeto nao publica pelo
    CI. Pela regra da propria skill isso e "nada a auditar" e sai da nota. O
    tratamento antigo (NAO_MEDIDO) punia duas vezes: o eixo virava faixa com
    pior caso F, e o plano ganhava quatro linhas P2 mandando "restaurar a
    medicao", conselho que nao resolve porque nao falta ambiente.
    """

    def _eixo_entrega(self, dora):
        snap = {"project": "p", "generated_at": "2026-08-26T00:00:00",
                "dora": dora, "collectors_run": ["dora"], "errors": {}}
        eixos, plano = render.auditoria(snap)
        eixo = next(e for e in eixos if e["nome"] == "Entrega")
        # linha do plano = (prioridade, eixo, o_que, como)
        eixo["_plano"] = [l for l in plano if l[1] == "Entrega"]
        return eixo

    def test_sem_workflow_de_deploy_os_criterios_saem_da_nota(self):
        eixo = self._eixo_entrega({
            "workflows_de_deploy": [], "deploys_por_semana": None,
            "lead_time_p50_h": None, "change_failure_rate": None,
            "mttr_h": None, "deploys_analisados": 0,
        })
        # `medidos: 0` de `criterios: 4` = os quatro sairam da nota, e a
        # letra vira NA em vez de uma faixa com pior caso F.
        self.assertEqual(eixo["medidos"], 0)
        self.assertEqual(eixo["letra"], "NA")
        texto = " ".join(t for t, _ok in eixo["checados"])
        self.assertIn("nada a auditar", texto)
        # E o plano nao ganha as quatro linhas P2 mandando "restaurar a
        # medicao": nao falta ambiente, falta deploy no CI.
        self.assertEqual(eixo["_plano"], [])

    def test_coletor_ausente_continua_sendo_faixa(self):
        """`None` (o coletor nao rodou) e outra coisa: ai o ambiente faltou
        mesmo, e nao afirmar vale mais que afirmar errado."""
        eixo = self._eixo_entrega({
            "workflows_de_deploy": None, "deploys_por_semana": None,
            "lead_time_p50_h": None, "change_failure_rate": None,
            "mttr_h": None, "deploys_analisados": None,
        })
        texto = " ".join(t for t, _ok in eixo["checados"])
        self.assertNotIn("deploy não passa pelo CI", texto)
        self.assertEqual(eixo["medidos"], 0)
        # Faixa: o eixo continua no denominador, com pior caso reprovado.
        self.assertEqual(eixo["criterios"], 4)


if __name__ == "__main__":
    unittest.main()

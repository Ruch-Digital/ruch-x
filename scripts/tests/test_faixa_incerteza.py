"""FU-RUCHX-NAO-AUDITADO-PREMIA (2026-08-22): um projeto podia melhorar de
nota desligando o banco — criterio nao-auditado saia do denominador e os
restantes valiam 100%. Provado no ion: Confiabilidade B/75 (15/08, banco de
pe) -> A/100 (21/08, banco FORA) -> B/80 (22/08, banco de pe de novo).

Contrato novo (spec docs/superpowers/specs/2026-08-22-faixa-de-incerteza-design.md):
- NAO_MEDIDO (ambiente limitou a coleta) fica NO denominador e produz faixa:
  pct/letra = ponta pessimista (reprova), pct_max/letra_max = otimista (passa).
- None ("nada a auditar") segue fora de tudo, como sempre.
- Toda comparacao/tendencia ancora na ponta pessimista.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import render  # noqa: E402


def _snap(**extra):
    base = {
        "schema": 2, "project": "alvo",
        "generated_at": "2026-08-22T11:00:00-03:00",
        "collectors_run": ["git"], "errors": {},
    }
    base.update(extra)
    return base


def _confiabilidade(snap):
    return {x["nome"]: x for x in render.auditoria(snap)[0]}["Confiabilidade"]


def _snap_confiabilidade(migrations):
    """Confiabilidade com CI, runbook e observabilidade medidos e aprovados.

    `migrations` controla o 4o criterio: lista = medido (vazia aprova,
    cheia reprova); a string "timeout" = nao medido por ambiente.
    """
    django = ({"pending_migrations": None, "nao_medido": {"pending_migrations": "timeout"}}
              if migrations == "timeout" else {"pending_migrations": migrations})
    return _snap(
        ci={"success_rate": 97.0},
        django=django,
        governance={
            "docs": {"readme": "README.md", "licenca": None, "adr": None,
                     "docs_dir": None, "runbooks": "runbooks/", "changelog": None},
            "segredos_commitados": [], "workflows": {}, "dependencias": {},
            "observabilidade": {"alertas": 45, "stack": ["prometheus"], "arquivos": 3},
        },
    )


class TestFaixaNoCalculo(unittest.TestCase):

    @unittest.expectedFailure  # a classificacao chega na Task 2, que REMOVE este decorator
    def test_caso_da_fu_migrations_timeout_vira_faixa_e_nao_100(self):
        """O caso real de 21/08: timeout nas migrations NAO pode dar A/100."""
        x = _confiabilidade(_snap_confiabilidade("timeout"))
        # pesos: CI 3 (True) + runbook 3 (True) + migrations 2 (NAO_MEDIDO)
        # + observabilidade 2 (True) => denominador 10.
        self.assertEqual(x["pct"], 80, "pessimista: nao-medido reprova")
        self.assertEqual(x["letra"], "B")
        self.assertEqual(x["pct_max"], 100, "otimista: nao-medido passa")
        self.assertEqual(x["letra_max"], "A")

    def test_eixo_pleno_nao_tem_faixa_e_e_identico_ao_contrato_antigo(self):
        x = _confiabilidade(_snap_confiabilidade([]))
        self.assertEqual(x["pct"], 100)
        self.assertEqual(x["letra"], "A")
        self.assertEqual(x["pct_max"], x["pct"])
        self.assertEqual(x["letra_max"], x["letra"])

    def test_eixo_pleno_reprovado_tambem_sem_faixa(self):
        x = _confiabilidade(_snap_confiabilidade(["app.0001_x"]))
        self.assertEqual(x["pct"], 80)
        self.assertEqual(x["pct_max"], 80)

    def test_medidos_nao_conta_nao_medido(self):
        """O card diz "N de M criterios auditados" — NAO_MEDIDO nao e auditado."""
        x = _confiabilidade(_snap_confiabilidade("timeout"))
        self.assertEqual(x["medidos"], 3)
        self.assertEqual(x["criterios"], 4)

    def test_nada_a_auditar_continua_fora_de_tudo(self):
        """Guard de sobrecorrecao: None (nada a auditar) NAO entra na faixa.

        Biblioteca sem workflow de deploy e sem nada declarado: o criterio de
        observabilidade sai como None e NAO pode abrir faixa (senao todo repo
        sem deploy viraria D–A)."""
        snap = _snap_confiabilidade(["app.0001_x"])
        snap["governance"]["observabilidade"] = {"alertas": 0, "stack": [], "arquivos": 0}
        snap["dora"] = {"workflows_de_deploy": []}
        x = _confiabilidade(snap)
        # denominador 8 (CI 3 + runbook 3 + migrations 2); migrations reprova.
        self.assertEqual(x["pct"], 75)
        self.assertEqual(x["pct_max"], 75, "None abriu faixa — sobrecorrecao")
        obs_ok = next(ok for rot, ok in x["checados"] if rot.startswith("infraestrutura"))
        self.assertIsNone(obs_ok)

    def test_eixo_todo_nao_medido_segue_na(self):
        """Zero criterios com medicao: letra NA de hoje nao muda."""
        eixos = {x["nome"]: x for x in render.auditoria(_snap())[0]}
        self.assertEqual(eixos["Entrega"]["letra"], "NA")


if __name__ == "__main__":
    unittest.main()

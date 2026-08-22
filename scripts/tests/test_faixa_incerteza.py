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


class TestClassificacaoDosCriterios(unittest.TestCase):
    """Ambiente → NAO_MEDIDO; semantico → None. Um exemplo por familia."""

    def _criterio(self, snap, eixo, prefixo):
        eixos = {x["nome"]: x for x in render.auditoria(snap)[0]}
        return next(ok for rot, ok in eixos[eixo]["checados"] if rot.startswith(prefixo))

    def test_segredos_com_coletor_caido_e_nao_medido(self):
        snap = _snap(governance={"segredos_commitados": None,
                                 "nao_medido": {"segredos_commitados": "git falhou"},
                                 "workflows": {}, "dependencias": {}, "dependabot": False})
        self.assertIs(self._criterio(snap, "Segurança", "nenhum segredo"), render.NAO_MEDIDO)

    def test_governance_inteiro_caido_e_nao_medido_em_processo(self):
        snap = _snap(errors={"governance": "boom"})
        self.assertIs(self._criterio(snap, "Processo", "README"), render.NAO_MEDIDO)

    def test_sem_workflow_nenhum_segue_nada_a_auditar(self):
        """Decisao de 2026-08-20 nao regride: zero workflows = None, sem faixa."""
        snap = _snap(governance={"segredos_commitados": [], "dependencias": {},
                                 "workflows": {"count": 0, "sem_pin": [],
                                               "sem_permissions": [], "permissions_no_job": []}})
        self.assertIsNone(self._criterio(snap, "Segurança", "actions com versão"))

    def test_licenca_em_repo_privado_segue_nada_a_auditar(self):
        snap = _snap(governance={
            "docs": {"readme": "README.md", "licenca": None, "adr": None,
                     "docs_dir": None, "runbooks": None, "changelog": None},
            "branch_protection": {"disponivel": True, "branch": "main",
                                  "visibility": "PRIVATE"},
            "segredos_commitados": [], "workflows": {}, "dependencias": {}})
        self.assertIsNone(self._criterio(snap, "Processo", "licença"))

    def test_branch_protection_sem_gh_e_nao_medido(self):
        snap = _snap(governance={
            "docs": {"readme": "README.md", "licenca": None, "adr": None,
                     "docs_dir": None, "runbooks": None, "changelog": None},
            "branch_protection": {"disponivel": False, "motivo": "gh não instalado"},
            "segredos_commitados": [], "workflows": {}, "dependencias": {}})
        self.assertIs(self._criterio(snap, "Processo", "branch de produção"), render.NAO_MEDIDO)

    def test_avisos_de_framework_em_settings_de_dev_e_nao_medido(self):
        snap = _snap(django={"deploy_issues": [], "ambiente_de_producao": False,
                             "pending_migrations": []})
        self.assertIs(self._criterio(snap, "Segurança", "avisos de segurança"), render.NAO_MEDIDO)

    def test_cobertura_ausente_continua_reprovando(self):
        """Nao medir cobertura e escolha do projeto, nao do ambiente."""
        self.assertIs(self._criterio(_snap(), "Qualidade", "cobertura"), False)

    def test_dora_mttr_sem_falha_na_janela_segue_nada_a_auditar(self):
        snap = _snap(dora={"deploys_por_semana": 10.0, "lead_time_p50_h": 1.0,
                           "change_failure_rate": 0.0, "mttr_h": None,
                           "deploys_analisados": 40, "workflows_de_deploy": ["deploy"]})
        self.assertIsNone(self._criterio(snap, "Entrega", "tempo de recuperação"))
        x = {e["nome"]: e for e in render.auditoria(snap)[0]}["Entrega"]
        self.assertEqual(x["pct"], x["pct_max"], "mttr sem falha abriu faixa")

    def test_dora_mttr_com_falha_aberta_e_nao_medido(self):
        """Falha sem deploy verde depois: nao e boa noticia — entra na faixa
        (a ponta pessimista conta a recuperacao pendente como reprovada)."""
        snap = _snap(dora={"deploys_por_semana": 10.0, "lead_time_p50_h": 1.0,
                           "change_failure_rate": 5.0, "mttr_h": None,
                           "deploys_analisados": 40, "workflows_de_deploy": ["deploy"]})
        self.assertIs(self._criterio(snap, "Entrega", "tempo de recuperação"), render.NAO_MEDIDO)

    def test_dora_ausente_inteiro_e_nao_medido(self):
        snap = _snap(errors={"dora": "gh não instalado"})
        self.assertIs(self._criterio(snap, "Entrega", "frequência de deploy"), render.NAO_MEDIDO)

    def test_hotspot_sem_radon_e_nao_medido(self):
        snap = _snap(git={"hotspots": [{"file": "a.py", "churn": 9, "complexity": 40,
                                        "loc": 100, "metodo": "heuristica"}]})
        self.assertIs(self._criterio(snap, "Qualidade", "arquivo de maior atrito"), render.NAO_MEDIDO)

    def test_observabilidade_com_coletor_fora_e_nao_medido(self):
        snap = _snap(errors={"governance": "boom"})
        self.assertIs(self._criterio(snap, "Confiabilidade", "infraestrutura"), render.NAO_MEDIDO)


class TestPlanoDeAcao(unittest.TestCase):

    def test_nao_medido_vira_linha_p2_com_motivo(self):
        achados = render.auditoria(_snap_confiabilidade("timeout"))[1]
        linha = next((a for a in achados
                      if a[0] == "P2" and a[1] == "Confiabilidade"
                      and "limitação do ambiente" in a[2]), None)
        self.assertIsNotNone(linha, "criterio nao-medido nao apareceu no plano")
        self.assertIn("timeout", linha[2], "o motivo tem que viajar junto")
        self.assertIn("restaurar", linha[3].lower())

    def test_nada_a_auditar_nao_vira_achado(self):
        snap = _snap_confiabilidade([])
        snap["governance"]["observabilidade"] = {"alertas": 0, "stack": [], "arquivos": 0}
        snap["dora"] = {"workflows_de_deploy": []}
        achados = render.auditoria(snap)[1]
        # Observabilidade é None (nada a auditar) em Confiabilidade e não deve gerar achado.
        # Outros eixos legitimamente produzem NAO_MEDIDO (dora presente mas sem campos,
        # quality/git ausentes, etc.), então a verificação é scoped apenas a Confiabilidade.
        self.assertFalse([a for a in achados
                          if a[1] == "Confiabilidade"
                          and "limitação do ambiente" in a[2]])


class TestCardComFaixa(unittest.TestCase):

    def test_card_com_faixa_mostra_so_a_letra_pessimista_e_explica(self):
        """Decisao do dono (2026-08-22 tarde): a letra grande e SO o pior
        caso — o par "B–A" lia como nota quebrada, e usar a media reabriria
        a FU (desligar o banco voltaria a subir nota). O teto e o motivo
        ficam explicitos na linha de base do card."""
        html = render.build_veredito(_snap_confiabilidade("timeout"),
                                     [_snap_confiabilidade("timeout")])
        # `class="eixo nota-B"` cru varre as 5 cartas: neste fixture o eixo
        # Segurança tambem tem letra_max "B" (D->B), entao um mutante que
        # trocasse `x["letra"]` por `x["letra_max"]` na classe ou na letra
        # sobreviveria escondido atras da carta errada. Isola a abertura do
        # card de Confiabilidade pelo titulo (`<h3>Confiabilidade`).
        tag_confiabilidade = html.split('<h3>Confiabilidade')[0].rsplit('<div class="eixo ', 1)[1]
        self.assertTrue(tag_confiabilidade.startswith('nota-B"'),
                         "a cor ancora na ponta pessimista")
        letra_conf = tag_confiabilidade.split('class="letra"')[1][:30]
        self.assertIn(">B<", letra_conf, "a letra grande e o pior caso, sozinho")
        self.assertNotIn("–", letra_conf,
                         "par de letras saiu do card por decisao do dono")
        self.assertIn("nota de pior caso", html)
        self.assertIn("chega a A (80–100%)", html)

    def test_card_pleno_identico_ao_de_hoje(self):
        html = render.build_veredito(_snap_confiabilidade([]),
                                     [_snap_confiabilidade([])])
        self.assertIn('class="eixo nota-A"', html)
        # `_snap_confiabilidade` so garante o eixo Confiabilidade pleno; os
        # outros eixos (Qualidade/Segurança/Processo) legitimamente carregam
        # NAO_MEDIDO por campos que este fixture nunca populou (tests,
        # quality, branch_protection) e mostram faixa deles mesmos — nao e
        # regressao desta task. Os dois asserts abaixo isolam o card de
        # Confiabilidade (unico "nota-A" deste fixture, confirmado por
        # inspecao direta de render.auditoria) pra checar so o que a task
        # controla — NAO o primeiro "class=\"letra\"" do HTML inteiro
        # (que seria o card de Entrega, NA, e passaria vazio de qualquer jeito).
        card_confiabilidade = html.split('<div class="eixo nota-A">')[1].split('<div class="eixo ')[0]
        self.assertNotIn("–", card_confiabilidade.split('class="letra"')[1][:30],
                         "letra plena nao pode virar faixa")
        self.assertNotIn("nota de pior caso", card_confiabilidade)

    def test_criterio_nao_medido_aparece_como_na_nunca_sim(self):
        html = render.build_veredito(_snap_confiabilidade("timeout"),
                                     [_snap_confiabilidade("timeout")])
        self.assertIn('class="na">migrations aplicadas', html)
        self.assertNotIn('class="sim">migrations aplicadas', html)


class TestTendenciaPorEixo(unittest.TestCase):

    def test_seta_compara_a_ponta_pessimista(self):
        """Anterior com timeout: pessimista 50 (runbook False + migrations
        NAO_MEDIDO), otimista 70. Atual: medicao plena com migrations
        reprovando = 80. A seta certa e +30 (50->80); comparar a otimista
        daria +10 (70->80) — e o teste morre se alguem regredir."""
        anterior = _snap_confiabilidade("timeout")
        anterior["governance"]["docs"]["runbooks"] = None
        atual = _snap_confiabilidade(["app.0001_x"])
        html = render.build_veredito(atual, [anterior, atual])
        self.assertIn("▲ 30", html)
        self.assertNotIn("▲ 10", html)

    def test_snapshot_unico_sem_seta(self):
        snap = _snap_confiabilidade([])
        html = render.build_veredito(snap, [snap])
        self.assertNotIn('class="delta', html)

    def test_desligar_o_banco_nunca_sobe_a_seta(self):
        """O proprio enredo da FU, agora como guard permanente: coleta plena
        (80) seguida de coleta com o banco fora (pessimista 80, otimista 100)
        tem que sair 'estável' — nunca seta pra cima."""
        pleno = _snap_confiabilidade(["app.0001_x"])   # 8/10 = 80
        quebrado = _snap_confiabilidade("timeout")     # 80-100
        html = render.build_veredito(quebrado, [pleno, quebrado])
        self.assertIn("estável", html)
        self.assertNotIn("▲", html)


if __name__ == "__main__":
    unittest.main()

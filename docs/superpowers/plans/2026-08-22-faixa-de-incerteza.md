# Faixa de Incerteza na Nota do Eixo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eixo com criterio nao-medido por limitacao de AMBIENTE passa a mostrar faixa pessimista-otimista (ex: `B–A · 60–100%`) em vez de nota inflada; toda comparacao ancora na ponta pessimista.

**Architecture:** Todo o trabalho e no `render.py` (a faixa nasce no render; snapshot e collect nao mudam). Uma sentinela `NAO_MEDIDO` distingue "ambiente limitou" de `None` "nada a auditar" dentro de `auditoria()`; `eixo()` calcula as duas pontas; o card, o plano de acao e a seta de tendencia consomem.

**Tech Stack:** Python 3.14, stdlib pura (sem dependencia nova), unittest.

**Spec:** `docs/superpowers/specs/2026-08-22-faixa-de-incerteza-design.md` — ler antes de comecar.

## Global Constraints

- Repo de trabalho: `C:\Users\User\projetos\ruch-x` (o publico). A copia instalada (`C:\Users\User\.claude\skills\ruch-x`, versionada no repo `claude-setup`) sincroniza na Task 7 — a frente so fecha nos DOIS repos.
- Runner: `python -m unittest discover -s scripts/tests -t scripts/tests` (rodar de `C:\Users\User\projetos\ruch-x`). Suite atual: 207 testes, todos verdes antes de comecar.
- Texto exibido no dashboard e user-facing: pt-BR **com acento** ("não auditado", "medição"). Codigo, commits e docstrings: sem acento.
- NUNCA escrever arquivo via `Path.write_text()`/heredoc no Windows (converte LF→CRLF e explode o diff — incidente 2026-08-21). Usar as tools Edit/Write do harness.
- Mensagem de commit vai pra arquivo e `git commit -F <arquivo>` (aspas no PS 5.1 mordem).
- Snapshot/collect.py NAO mudam. Nenhum campo novo no JSON.
- Comportamento de eixo 100% medido NAO muda (guard de regressao na Task 1).

---

### Task 1: Sentinela NAO_MEDIDO e faixa no calculo do eixo

**Files:**
- Modify: `scripts/render.py` (funcao `_nota` ~linha 480, closure `eixo()` dentro de `auditoria()` ~linhas 498-514)
- Create: `scripts/tests/test_faixa_incerteza.py`

**Interfaces:**
- Produces: `render.NAO_MEDIDO` (sentinela module-level, identidade via `is`); dict de eixo ganha as chaves `pct_max` (int|None) e `letra_max` (str) ao lado de `pct`/`letra` — `pct`/`letra` passam a ser a ponta PESSIMISTA (nao-medido reprova) e `pct_max`/`letra_max` a otimista (nao-medido passa). Eixo sem NAO_MEDIDO: `pct == pct_max` e `letra == letra_max` com os valores identicos aos de hoje.
- Consumes: nada de task anterior.

- [ ] **Step 1: Write the failing tests**

Criar `scripts/tests/test_faixa_incerteza.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `C:\Users\User\projetos\ruch-x`):
`python -m unittest scripts.tests.test_faixa_incerteza -v`
Expected: os testes de faixa falham com `KeyError: 'pct_max'`; o do caso da FU falha antes disso porque hoje migrations-timeout produz `pct == 100` (o proprio defeito). `test_eixo_todo_nao_medido_segue_na` ja passa (guard de regressao).

- [ ] **Step 3: Implement — sentinela + duas pontas**

Em `scripts/render.py`, logo apos `LIMIAR_HOTSPOT = 150` (~linha 461), adicionar:

```python
class _NaoMedido:
    """Sentinela: criterio que o AMBIENTE da coleta impediu de medir (banco
    fora, gh mudo, radon ausente, timeout, coletor caido, snapshot anterior
    ao campo). Diferente de None ("nada a auditar": o projeto genuinamente
    nao tem aquilo). A distincao existe porque tratar os dois igual deixava
    um projeto MELHORAR de nota desligando o banco — o criterio que reprovava
    saia do denominador e os restantes passavam a valer 100% (FU-RUCHX-
    NAO-AUDITADO-PREMIA, 2026-08-22; spec em docs/superpowers/specs/).

    Identidade via `is`. Truthiness proibida: `bool()` estoura de proposito,
    porque `if ok:` num criterio classificaria a sentinela como aprovada.
    """
    __slots__ = ()

    def __bool__(self):
        raise TypeError("NAO_MEDIDO nao tem truthiness — compare com `is`")

    def __repr__(self):
        return "NAO_MEDIDO"


NAO_MEDIDO = _NaoMedido()
```

No closure `eixo()` dentro de `auditoria()` (~linhas 498-514), substituir o calculo:

```python
    def eixo(nome, itens, resumo):
        """itens = [(peso, ok, rotulo, prioridade, achado, acao)]

        ok: True/False (medido) · NAO_MEDIDO (ambiente limitou — fica no
        denominador e abre a faixa pessimista-otimista) · None (nada a
        auditar — fora de tudo).
        """
        total = sum(i[0] for i in itens if i[1] is not None)
        ganhos = sum(i[0] for i in itens if i[1] is True)
        incerto = sum(i[0] for i in itens if i[1] is NAO_MEDIDO)
        for peso, ok, rotulo, prio, txt, acao in itens:
            if ok is False:
                achados.append((prio, nome, txt, acao))
        pct, letra = _nota(ganhos, total)
        pct_max, letra_max = _nota(ganhos + incerto, total)
        eixos.append({"nome": nome, "pct": pct, "letra": letra,
                      "pct_max": pct_max, "letra_max": letra_max,
                      "resumo": resumo,
                      "medidos": sum(1 for i in itens if i[1] is True or i[1] is False),
                      "criterios": len(itens),
                      "checados": [(i[2], i[1]) for i in itens]})
```

(`total` ja inclui NAO_MEDIDO por `is not None`; `pct`/`letra` viram a ponta pessimista sem mudanca de formula. O comentario longo sobre "N de M" que esta ali hoje pode ser encurtado — a docstring nova ja explica os estados.)

`_nota` nao muda.

**Nesta task ainda nenhum criterio produz NAO_MEDIDO** — a classificacao e a Task 2. Os testes de Step 1 que dependem dela (caso da FU, medidos, nada-a-auditar) SO passam na Task 2; o objetivo do Step 4 e ver `pct_max`/`letra_max` existirem e os guards de regressao passarem.

- [ ] **Step 4: Run tests**

Run: `python -m unittest scripts.tests.test_faixa_incerteza -v`
Expected: `test_eixo_pleno_*`, `test_eixo_todo_nao_medido_segue_na` PASSAM. `test_caso_da_fu_*`, `test_medidos_*`, `test_nada_a_auditar_*` ainda FALHAM (pct==100 — classificacao vem na Task 2). Anotar e seguir.

Run: `python -m unittest discover -s scripts/tests -t scripts/tests 2>&1 | Select-String "Ran |OK|FAILED"`
Expected: os 207 antigos continuam OK (nada classifica NAO_MEDIDO ainda).

- [ ] **Step 5: Commit**

```
git add scripts/render.py scripts/tests/test_faixa_incerteza.py
git commit -F <msg>   # "faixa de incerteza: sentinela NAO_MEDIDO e as duas pontas no eixo()"
```

---

### Task 2: Classificar cada criterio — NAO_MEDIDO vs None

**Files:**
- Modify: `scripts/render.py` — corpo de `auditoria()` (todos os 5 eixos, ~linhas 516-835), helpers `_observabilidade` (~379-417) e `_motivo_do_mttr_vazio` (~420-436)
- Modify: `scripts/tests/test_render_hostil.py:62-84` (`test_criterio_nao_medido_sai_do_denominador`) e `:97-145` (`test_html_nao_afirma_seguranca_sem_medir`)
- Test: `scripts/tests/test_faixa_incerteza.py` (novos casos)

**Interfaces:**
- Consumes: `render.NAO_MEDIDO` (Task 1).
- Produces: todos os criterios de `auditoria()` classificados. Regra: onde o `ok` hoje vira `None` porque o VALOR nao chegou (coletor caiu, campo `nao_medido`, gh mudo, radon ausente, settings de dev, snapshot antigo sem o campo) → `NAO_MEDIDO`. Onde `None` e decisao semantica ("nada a auditar") → continua `None`. Cobertura ausente continua `False` (escolha do projeto).

- [ ] **Step 1: Write the failing tests**

Adicionar em `scripts/tests/test_faixa_incerteza.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest scripts.tests.test_faixa_incerteza -v`
Expected: os novos falham com `AssertionError: None is not NAO_MEDIDO` (hoje tudo e None); os semanticos (`sem_workflow`, `licenca_privada`, `mttr_sem_falha`, `cobertura`) ja passam.

- [ ] **Step 3: Implement — trocar None de ambiente por NAO_MEDIDO em cada criterio**

Em `auditoria()` e helpers, criterio a criterio. O padrao e mecanico: `None if <valor ausente> else <bool>` vira `NAO_MEDIDO if <valor ausente> else <bool>` quando a causa e ambiente. Lista completa (conferir com o codigo aberto; a ancora e o rotulo):

| Eixo · criterio | Hoje | Vira |
|---|---|---|
| Entrega · frequencia, lead, cfr | `None if X is None` | `NAO_MEDIDO if X is None` |
| Entrega · mttr | `None if mttr is None` | `None` SO quando `cfr == 0` e `deploys_analisados` truthy (nada a auditar); `NAO_MEDIDO` nos demais (inclui falha sem deploy verde e snapshot sem `deploys_analisados`) |
| Qualidade · cobertura | `False` quando None | **nao muda** |
| Qualidade · complexidade | `None if pct_cx is None` | `NAO_MEDIDO if pct_cx is None` |
| Qualidade · hotspot | `hot_ok = ... if metodo == "radon" else None` | `else NAO_MEDIDO` (heuristica, metodo ausente e `hot0 is None`) |
| Seguranca · segredos | `None if seg is None` | `NAO_MEDIDO if seg is None` |
| Seguranca · pin / permissions | `None if (wf_raw is None or wf_sem_nada)` | `NAO_MEDIDO if wf_raw is None` (coletor caiu) — `wf_sem_nada` continua `None` |
| Seguranca · deps | `None if pct_velhas is None` | `NAO_MEDIDO if pct_velhas is None` |
| Seguranca · dependabot | `gov.get("dependabot")` via item direto | usar `_do_gov` ja existente; `_do_gov` retorna `NAO_MEDIDO` no lugar de None (ver abaixo) |
| Seguranca · avisos framework | `None if (sec is None or not producao)` | `NAO_MEDIDO` nos dois casos (falta de toml e restauravel como instalar radon) |
| Confiabilidade · CI | `None if ci_ok is None` | `NAO_MEDIDO if ci_ok is None` |
| Confiabilidade · runbook, Processo · README/ADR/pre-commit/changelog | `_do_gov` → None | `_do_gov` → `NAO_MEDIDO` |
| Confiabilidade · migrations | `None if pend is None` | `NAO_MEDIDO if pend is None` |
| Confiabilidade · observabilidade | `_observabilidade` retorna None em 3 casos | obs ausente/coletor fora → `NAO_MEDIDO`; dora ausente → `NAO_MEDIDO`; "nenhum deploy identificado" → `None` (nada a auditar) |
| Processo · branch protegida | `protegido = ... if disponivel else None` | `NAO_MEDIDO` quando `not disponivel` ou governance caido |
| Processo · licenca | PRIVATE → None; resto `_do_gov` | PRIVATE → `None` (**nao muda**); `_do_gov` cobre o caso do coletor caido |

Mudancas pontuais em helpers:

```python
    def _do_gov(valor):
        """NAO_MEDIDO (ambiente: coletor governance nao rodou) ou bool(valor)."""
        return NAO_MEDIDO if gov_raw is None else bool(valor)
```

Em `_observabilidade`: os dois `return None, _nao_auditado(...)` viram `return NAO_MEDIDO, _nao_auditado(...)`; o `return None, " (nenhum deploy identificado — nada a auditar)"` fica.

No mttr (Entrega), o estado e derivado dos mesmos dados que `_motivo_do_mttr_vazio` usa:

```python
    analisados_mttr = _seguro(dig(snap, "dora", "deploys_analisados"), int)
    mttr_ok = (None if mttr is None and cfr == 0 and analisados_mttr
               else NAO_MEDIDO if mttr is None
               else n_mttr in bom)
```

e o item usa `mttr_ok` no lugar da expressao inline.

Atencao aos rotulos: os sufixos `_nao_auditado(...)`/`" (não auditado: ...)"` NAO mudam — so o estado. `licenca_ok`/`protegido` que alimentam `_sim_nao` no resumo: `_sim_nao` compara `ok is None` → ajustar para tratar `NAO_MEDIDO` igual a None (`na if ok is None or ok is NAO_MEDIDO else ...`), senao o resumo de Processo estoura o `__bool__` da sentinela.

**Varredura obrigatoria ao fim do step:** `Grep "if ok" e "if i[1]" e todo uso dos valores classificados em render.py` — qualquer `if <estado>:` truthy sobre um criterio agora ESTOURA TypeError pela sentinela (e proposital: falha ruidosa > classificacao silenciosa errada). Rodar a suite completa acha os que escaparem.

- [ ] **Step 4: Atualizar os 2 testes que afirmam a regra velha**

`scripts/tests/test_render_hostil.py`:

1. `test_criterio_nao_medido_sai_do_denominador` (linha ~62): renomear para `test_criterio_nao_medido_por_ambiente_entra_na_faixa` e reescrever o contrato: com governance nao-medido, `pct` (pessimista) do snapshot nao-medido tem que ser MENOR que o do medido-limpo, e `pct_max` maior que `pct`. Docstring aponta a spec de 2026-08-22.
2. `test_html_nao_afirma_seguranca_sem_medir` item (3) (linha ~140): `assertIsNone(ok_segredo)` vira `self.assertIs(ok_segredo, render.NAO_MEDIDO)`; o texto do assert atualiza ("nem sim, nem nao — nao medido por ambiente"). Itens (1), (2) e a checagem de `class="na"` nao mudam.

- [ ] **Step 5: Run tests**

Run: `python -m unittest scripts.tests.test_faixa_incerteza scripts.tests.test_render_hostil -v`
Expected: PASS. Em seguida a suite completa:
`python -m unittest discover -s scripts/tests -t scripts/tests 2>&1 | Select-String "Ran |OK|FAILED"`
Expected: `OK` — qualquer TypeError de truthiness da sentinela e um criterio mal classificado: corrigir na hora.

- [ ] **Step 6: Commit**

```
git add scripts/render.py scripts/tests/test_faixa_incerteza.py scripts/tests/test_render_hostil.py
git commit -F <msg>   # "faixa de incerteza: classifica ambiente vs nada-a-auditar nos 5 eixos"
```

---

### Task 3: Criterio NAO_MEDIDO gera linha P2 no plano de acao

**Files:**
- Modify: `scripts/render.py` — closure `eixo()` (o laco de achados)
- Test: `scripts/tests/test_faixa_incerteza.py`

**Interfaces:**
- Consumes: `NAO_MEDIDO`, classificacao da Task 2.
- Produces: para cada criterio NAO_MEDIDO, um achado `("P2", nome_do_eixo, texto, acao)` em `auditoria()[1]`. O texto usa o rotulo do criterio (que ja carrega o motivo entre parenteses).

- [ ] **Step 1: Write the failing test**

```python
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
        self.assertFalse([a for a in achados if "limitação do ambiente" in a[2]])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest scripts.tests.test_faixa_incerteza.TestPlanoDeAcao -v`
Expected: FAIL — nenhum achado com "limitação do ambiente".

- [ ] **Step 3: Implement**

No laco de achados dentro de `eixo()`:

```python
        for peso, ok, rotulo, prio, txt, acao in itens:
            if ok is False:
                achados.append((prio, nome, txt, acao))
            elif ok is NAO_MEDIDO:
                # Sem esta linha o buraco de medicao fica invisivel — que e
                # como a FU passou despercebida por 6 dias.
                achados.append((
                    "P2", nome,
                    f"Critério «{rotulo}» não auditado por limitação do ambiente da coleta.",
                    "Restaurar a medição (subir o serviço, instalar a ferramenta, "
                    "autenticar o gh) — enquanto isso a nota do eixo fica em faixa."))
```

(`rotulo` ja inclui o sufixo "(não auditado: timeout)" montado pelos criterios.)

- [ ] **Step 4: Run tests**

Run: `python -m unittest scripts.tests.test_faixa_incerteza -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```
git add scripts/render.py scripts/tests/test_faixa_incerteza.py
git commit -F <msg>   # "faixa de incerteza: criterio nao-medido entra no plano de acao como P2"
```

---

### Task 4: Card do eixo — letra em faixa, classe pessimista, truthiness segura

**Files:**
- Modify: `scripts/render.py` — `build_veredito` (~linhas 1000-1043)
- Test: `scripts/tests/test_faixa_incerteza.py`

**Interfaces:**
- Consumes: `pct_max`/`letra_max` (Task 1), classificacao (Task 2).
- Produces: card HTML com `<div class="letra">B–A</div>` quando ha faixa (en-dash), classe CSS `nota-<letra pessimista>`, criterio NAO_MEDIDO com `class="na"` na checklist, e a linha base ganhando o aviso de faixa.

- [ ] **Step 1: Write the failing tests**

```python
class TestCardComFaixa(unittest.TestCase):

    def test_card_mostra_faixa_e_cor_pessimista(self):
        html = render.build_veredito(_snap_confiabilidade("timeout"),
                                     [_snap_confiabilidade("timeout")])
        self.assertIn("B–A", html)
        self.assertIn('class="eixo nota-B"', html, "a cor ancora na ponta pessimista")
        self.assertIn("o ambiente da coleta limitou a medição", html)

    def test_card_pleno_identico_ao_de_hoje(self):
        html = render.build_veredito(_snap_confiabilidade([]),
                                     [_snap_confiabilidade([])])
        self.assertIn('class="eixo nota-A"', html)
        self.assertNotIn("–", html.split('class="letra"')[1][:30],
                         "letra plena nao pode virar faixa")
        self.assertNotIn("o ambiente da coleta limitou a medição", html)

    def test_criterio_nao_medido_aparece_como_na_nunca_sim(self):
        html = render.build_veredito(_snap_confiabilidade("timeout"),
                                     [_snap_confiabilidade("timeout")])
        self.assertIn('class="na">migrations aplicadas', html)
        self.assertNotIn('class="sim">migrations aplicadas', html)
```

(assinatura nova `build_veredito(snap, snaps)` ja entra aqui — a Task 5 usa a serie; nesta task ela so precisa existir com `snaps` ignorado alem do ultimo.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest scripts.tests.test_faixa_incerteza.TestCardComFaixa -v`
Expected: FAIL — `build_veredito` nem aceita 2 argumentos ainda (TypeError).

- [ ] **Step 3: Implement**

Em `build_veredito`, assinatura vira `def build_veredito(snap, snaps):` (call site atualiza na Task 5 — nesta task, atualizar a chamada em `render()` linha ~1430 para `build_veredito(snap, snaps)` ja).

No laco de cards:

```python
    for x in eixos:
        checados = "".join(
            f'<li class="{"sim" if ok is True else "nao" if ok is False else "na"}">{e(rot)}</li>'
            for rot, ok in x["checados"]
        )
        na = x["letra"] == "NA"
        # pct pode divergir com letra igual (ex: 80–85, B–B) — e faixa do
        # mesmo jeito; a letra so exibe par quando as letras diferem.
        faixa = (not na) and x["pct_max"] != x["pct"]
        letra_display = "—" if na else (
            f'{x["letra"]}–{x["letra_max"]}' if x["letra_max"] != x["letra"] else x["letra"])
        base = ('' if na else
                f'<p class="eixo-base">{x["medidos"]} de {x["criterios"]} '
                f'critérios auditados'
                + (f' · nota em faixa ({x["pct"]}–{x["pct_max"]}%) — '
                   f'o ambiente da coleta limitou a medição' if faixa else '')
                + '</p>')
        cards.append(
            f'<div class="eixo nota-{x["letra"]}">'
            f'<div class="letra">{letra_display}</div>'
            ...  # resto identico ao atual
```

Atencao obrigatoria: o `"sim" if ok else ...` atual usa truthiness — com a sentinela isso ESTOURA TypeError (por design). A troca por `ok is True` e parte do contrato, e o teste 3 cobre.

CSS: nenhuma classe nova (`nota-B` pessimista ja tem cor); a letra "B–A" em font 30px cabe no card de 232px — conferir visualmente no Step 5 da Task 7.

- [ ] **Step 4: Run tests**

Run: `python -m unittest scripts.tests.test_faixa_incerteza -v` e depois a suite completa.
Expected: PASS (os testes de `test_render_hostil` que chamam `render.render([snap])` passam porque o call site foi atualizado junto).

- [ ] **Step 5: Commit**

```
git add scripts/render.py scripts/tests/test_faixa_incerteza.py
git commit -F <msg>   # "faixa de incerteza: card mostra B–A com cor e contagem pessimistas"
```

---

### Task 5: Seta de tendencia por eixo, ancorada na ponta pessimista

**Files:**
- Modify: `scripts/render.py` — `build_veredito`
- Test: `scripts/tests/test_faixa_incerteza.py`

**Interfaces:**
- Consumes: `build_veredito(snap, snaps)` (Task 4), `delta()` existente (~linha 184).
- Produces: card de eixo com `<span class="delta ...">` comparando `pct` (pessimista) do snapshot atual vs anterior. Eixo NA ou serie com < 2 pontos: sem seta (contrato do `delta()` de hoje).

- [ ] **Step 1: Write the failing tests**

```python
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
        quebrado = _snap_confiabilidade("timeout")     # 80–100
        html = render.build_veredito(quebrado, [pleno, quebrado])
        self.assertIn("estável", html)
        self.assertNotIn("▲", html)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest scripts.tests.test_faixa_incerteza.TestTendenciaPorEixo -v`
Expected: FAIL — sem seta nenhuma no card hoje.

- [ ] **Step 3: Implement**

Em `build_veredito`, antes do laco de cards:

```python
    # Serie do pct PESSIMISTA por eixo, snapshot a snapshot. A ancora e a
    # decisao de produto da spec: desligar o banco nunca sobe a ponta
    # pessimista, entao a seta nunca melhora por falta de medicao.
    historico = {}
    for s in snaps:
        for x_h in auditoria(s)[0]:
            historico.setdefault(x_h["nome"], []).append(x_h["pct"])
```

No card, montar a seta e anexar ao `base` (so quando nao-NA):

```python
        seta = delta(historico.get(x["nome"], []), atual=x["pct"])
        base = ('' if na else
                f'<p class="eixo-base">{seta}{" · " if seta else ""}'
                f'{x["medidos"]} de {x["criterios"]} critérios auditados'
                ...)
```

(`delta()` ja filtra nao-numeros — eixo NA em snapshot antigo entra como None e sai da serie — e ja devolve "" com < 2 pontos.)

- [ ] **Step 4: Run tests**

Run: `python -m unittest scripts.tests.test_faixa_incerteza -v`, depois a suite completa com `Select-String "Ran |OK|FAILED"`.
Expected: PASS / OK.

- [ ] **Step 5: Commit**

```
git add scripts/render.py scripts/tests/test_faixa_incerteza.py
git commit -F <msg>   # "faixa de incerteza: seta de tendencia por eixo ancora no pior caso"
```

---

### Task 6: Documentar a faixa no criteria.md

**Files:**
- Modify: `docs/criteria.md` (secao sobre "not audited" / scoring — localizar com Grep por "denominator" ou "not audited")

**Interfaces:**
- Consumes: contrato final das Tasks 1-5.
- Produces: doc publico em INGLES (padrao do docs/) explicando: os 4 estados do criterio; a faixa pessimista-otimista com o exemplo real (migrations timeout → B–A 60–100 [usar os numeros do exemplo da propria pagina, se houver]); a ancora pessimista em toda comparacao; "nothing to audit" continua fora da nota; e o limite — a faixa diz o que o AMBIENTE da coleta deixou de ver, nao audita o ambiente de producao de ninguem.

- [ ] **Step 1: Ler a secao atual** (`Grep -n "not audited" docs/criteria.md` e ler em volta) e reescrever o paragrafo que afirma "leaves the denominator" — ele passa a descrever a faixa. Adicionar subsecao curta "Uncertainty band" com a tabela dos 4 estados.

- [ ] **Step 2: Conferir que nenhum teste de docs quebrou**

Run: `python -m unittest scripts.tests.test_docs -v`
Expected: PASS (se `test_docs` travar frase exata do texto antigo, atualizar o teste junto — ele e guard de sincronia doc↔codigo).

- [ ] **Step 3: Commit**

```
git add docs/criteria.md scripts/tests/test_docs.py
git commit -F <msg>   # "docs: a nota em faixa entra no criteria.md"
```

---

### Task 7: Gate — mutacao, suite completa, sync dos dois repos, smoke real no ion

**Files:**
- Modify: nenhum (gate) + sync de `scripts/render.py`, `scripts/tests/*`, `docs/criteria.md`, `docs/superpowers/**` para `C:\Users\User\.claude\skills\ruch-x\`
- Commit no repo `claude-setup` (`C:\Users\User\.claude`)

**Interfaces:**
- Consumes: tudo.
- Produces: frente fechada nos dois repos + dashboard do ion re-renderizado com a faixa retroativa.

- [ ] **Step 1: Mutacao (metodo da casa — cada mutacao aplicada deve matar >= 1 teste; reverter apos cada uma)**

1. Em `eixo()`: `pct_max, letra_max = _nota(ganhos + incerto, total)` → `_nota(ganhos, total)` (mata `test_caso_da_fu...`).
2. Em `eixo()`: `incerto = sum(... is NAO_MEDIDO)` → incluir tambem `is None` (mata `test_nada_a_auditar_continua_fora_de_tudo`).
3. Em `_do_gov`: `NAO_MEDIDO if gov_raw is None` → `None if gov_raw is None` (mata `test_governance_inteiro_caido...`).
4. No card: `nota-{x["letra"]}` → `nota-{x["letra_max"]}` (mata `test_card_mostra_faixa_e_cor_pessimista`).
5. Na tendencia: `.append(x_h["pct"])` → `.append(x_h["pct_max"])` (mata `test_seta_compara_a_ponta_pessimista` e/ou `test_desligar_o_banco_nunca_sobe_a_seta`).
6. No plano de acao: remover o `elif ok is NAO_MEDIDO` (mata `test_nao_medido_vira_linha_p2_com_motivo`).

Registrar o placar (aplicadas/mortas) pra sessao. Sobreviveu? O teste que falta e escrito ANTES de seguir.

- [ ] **Step 2: Suite completa final**

Run: `python -m unittest discover -s scripts/tests -t scripts/tests 2>&1 | Select-String "Ran |OK|FAILED"`
Expected: `OK`, contagem > 207 (anotar o numero novo pro commit).

- [ ] **Step 3: Sincronizar a copia instalada**

Copiar (Copy-Item, preservando LF) os arquivos tocados para `C:\Users\User\.claude\skills\ruch-x\`; rodar o comparador de hash da sessao (fonte × instalada, ignorando `__pycache__`/`.git`/`.ruch-x`) e exigir ZERO divergencia de conteudo. Rodar a suite TAMBEM na copia instalada:
`python -m unittest discover -s C:\Users\User\.claude\skills\ruch-x\scripts\tests -t C:\Users\User\.claude\skills\ruch-x\scripts\tests`
Expected: mesmo `OK`.

- [ ] **Step 4: Commit nos dois repos**

- `ruch-x`: ja commitado por task; conferir `git log --oneline origin/main..HEAD` (spec + plano + 5-6 commits de task).
- `claude-setup` (`git -C C:\Users\User\.claude ...`): um commit com a skill sincronizada, mensagem citando o commit correspondente do ruch-x. Push e do dono, nos DOIS.

- [ ] **Step 5: Smoke real — o dashboard do ion com a faixa retroativa**

No repo do ion (`C:\Users\User\projetos\Ruchion`):
`venv\Scripts\python.exe C:\Users\User\.claude\skills\ruch-x\scripts\render.py`
Abrir `.ruch-x/dashboard.html` e conferir com o dono: o snapshot de 21/08 (banco fora) agora mostra Confiabilidade **B–A** com o aviso de faixa e a linha P2 no plano; o de 22/08 (banco de pe) mostra **B/80** pleno; a seta entre eles obedece a ponta pessimista. E o teste de aceitacao da FU inteira, nos dados reais que a originaram.

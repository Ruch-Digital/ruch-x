# Faixa de incerteza na nota do eixo — design

> **FU-RUCHX-NAO-AUDITADO-PREMIA** · spec aprovada em 2026-08-22 (Wilkerson + Claude, Win11).
> Decisao de produto tomada em chat; este doc registra o desenho pra implementacao.
> ✅ **EXECUTADA no mesmo dia** — commits `d25e507..cb5c9b1` + display de pior caso `a521ee3`.

## O defeito

"Criterio nao auditado sai do denominador" esta certo na intencao — nao premiar nem punir
o que nao se mediu — mas tem um efeito colateral: **remover um criterio que estava
reprovando aumenta o percentual dos que sobraram.** Quanto mais quebrado o ambiente da
coleta, melhor a nota.

Prova empirica, neste repositorio, com o projeto ion:

| Coleta | Ambiente | `migrations aplicadas` | Confiabilidade |
|---|---|---|---|
| 2026-08-15 | banco de pe | reprova (36 pendentes) | **B/75%** |
| 2026-08-21 | banco FORA (containers parados) | nao auditado (timeout) | **A/100%** |
| 2026-08-22 | banco de pe | reprova (36 pendentes) | **B/80%** |

Nada mudou no projeto entre 21 e 22/08 — so o ambiente da coleta. Um projeto podia
melhorar de nota desligando o banco. E a terceira aparicao da mesma familia em tres dias
(depois dos "5 pontos de graca" e do DORA contando run vermelho como deploy falho); as
duas anteriores eram no dado, esta e na formula.

## As decisoes de produto (tomadas pelo dono, 2026-08-22)

1. **Faixa de incerteza.** Eixo com criterio nao-medido POR AMBIENTE mostra o intervalo
   do que se sabe: pior caso (nao-medido reprova) ate melhor caso (nao-medido passa).
   Nao pune o projeto pela maquina do auditor, nao premia o ambiente quebrado.
2. **Tendencia ancora no pior caso.** Setas e comparacoes entre snapshots usam sempre a
   ponta pessimista (`pct_min`). Desligar o banco nunca sobe a ponta pessimista, entao a
   nota nunca melhora por falta de medicao.

Alternativas descartadas: (a) so sinalizar "N de M" — ja existia desde 13/08 e nao
impediu a FU; (b) nao-medido reprova — pune o projeto pela maquina do auditor, o espelho
do defeito original; (d) eixo parcial sem letra — joga fora a informacao dos criterios
que mediram.

## O desenho

### 1. Quatro estados por criterio

O `ok` de cada item de `eixo()` em `render.py` passa a distinguir DOIS tipos de `None`:

| Estado | Significado | Na nota |
|---|---|---|
| `True` | medido, passou | soma no numerador e no denominador |
| `False` | medido, reprovou | soma so no denominador |
| `NAO_MEDIDO` (sentinela nova) | **ambiente**: banco fora, `gh` mudo, radon ausente, timeout, coletor caiu, snapshot anterior ao campo | denominador sempre; numerador so na ponta otimista |
| `None` | **nada a auditar**: o projeto genuinamente nao tem aquilo | fora de tudo (como hoje) |

A classificacao e **declarada item a item** no `auditoria()`, nunca inferida do texto do
rotulo. Regra pratica: onde o rotulo hoje vem de `_nao_auditado()`/`_nao_medido()` com
motivo (o motivo mora em `nao_medido`/`errors` do snapshot), o estado e `NAO_MEDIDO`;
os "nada a auditar" semanticos — biblioteca sem workflow de deploy (`_observabilidade`),
CFR 0 sem falha na janela (`_motivo_do_mttr_vazio`) — seguem `None`.

Casos ja resolvidos que NAO mudam:

- **Cobertura ausente continua `False`** — nao medir cobertura e escolha do projeto, nao
  do ambiente (comentario existente no criterio).
- **`NA` de eixo com zero criterios** medidos continua igual (`_nota(0, 0)`).
- **Snapshot anterior a um campo** (ex.: `metodo` do hotspot ausente) e procedencia
  desconhecida → `NAO_MEDIDO` (entra na faixa como incerteza; conservador).

**Ruling pos-review-final (2026-08-22):** projeto sem `manage.py` na raiz (repo Go/Node/etc,
sem stack Django nenhuma) e `None` ("nada a auditar"), NAO `NAO_MEDIDO`, nas duas pontas que
o campo `django.nao_medido` alimenta — "migrations aplicadas" (Confiabilidade) e "avisos de
seguranca do framework" (Seguranca). E o espelho exato da alternativa (b) rejeitada na secao
de decisoes: nao ter uma stack nao e o AMBIENTE que impediu a medicao, e o projeto genuinamente
nao ter aquilo — mesmo principio de zero-workflows (`_observabilidade`, workflows sem_pin) e
sem-deploy-identificado. Sem este ruling um repositorio que nem Django e ficava BANDADO pra
sempre (Seguranca/Confiabilidade B-A eternos) com um P2 permanente cuja acao ("subir o servico,
instalar a ferramenta...") nunca podia ser cumprida. Outras causas do mesmo campo — timeout,
settings quebrado, `manage_py` configurado no toml que nao resolve dentro do repositorio —
continuam `NAO_MEDIDO`: a distincao e pelo texto EXATO do motivo que `collect_django` grava,
nunca por inferencia.

### 2. Formula

```
denominador = soma dos pesos com estado em {True, False, NAO_MEDIDO}
pct_min     = 100 * pesos(True) / denominador                # nao-medido reprova
pct_max     = 100 * (pesos(True) + pesos(NAO_MEDIDO)) / denominador   # nao-medido passa
```

Tudo medido → `pct_min == pct_max` e o comportamento e IDENTICO ao atual (guard de
regressao: nenhum eixo plenamente medido muda de nota com este fix).

Letra: calculada nas duas pontas (`letra_min`, `letra_max`) pela mesma tabela de hoje.

**Faixa exige pelo menos UM criterio medido (True ou False).** Eixo com zero medidos —
tudo NAO_MEDIDO e/ou nada-a-auditar — segue **NA**, como hoje: `F–A · 0–100%` seria
tecnicamente verdadeiro e inutil (nenhuma informacao), alem de quebrar o contrato
existente do NA (ex.: Processo com o coletor governance inteiro caido).

### 3. Tela

- Card com faixa: `B–A · 60–100%` no lugar de `A · 100%`. Faixa degenerada (min == max)
  renderiza exatamente como hoje. A contagem "N de M criterios" continua no card.
- **Adendo (2026-08-22 tarde, decisao do dono apos ver o painel real):** a LETRA grande
  do card mostra so o pior caso ("C", nao "C–A") — o par lia como nota quebrada, e usar
  a media reabriria a FU (desligar o banco voltaria a subir nota). O teto e o intervalo
  ficam explicitos na linha de base do card ("nota de pior caso ... medindo tudo, chega
  a A (70–100%)"). Formula, ancora e contrato do dict (pct/pct_max/letra/letra_max)
  inalterados.
- Tendencia, resumo e qualquer comparacao entre snapshots usam **`pct_min`**.
- A faixa nasce no `render.py`, entao **snapshots historicos ganham a faixa
  retroativamente** — o A/100% versionado de 21/08 passa a exibir `B–A` sem recoleta.

### 4. Plano de acao

Criterio `NAO_MEDIDO` gera linha propria no plano (hoje so `ok is False` gera achado):

> "criterio X nao auditado por limitacao do ambiente: _motivo_" + como restaurar a
> medicao (subir o banco, instalar radon, `gh auth login`...).

Prioridade **P2** — nao e defeito do projeto auditado, e divida do ambiente de coleta;
mas sem a linha o buraco fica invisivel, que e como esta FU passou despercebida.

### 5. Guardas e testes

- Os testes que travam "nao auditado sai do denominador" mudam junto — sao intencao
  executavel da regra velha, e a regra mudou por decisao do dono (este doc e o registro).
- Testes novos provados por **mutacao** (metodo da casa), cobrindo no minimo:
  - eixo parcial produz faixa; eixo pleno produz ponto identico ao comportamento atual;
  - tendencia compara `pct_min` × `pct_min`;
  - `NAO_MEDIDO` vira linha P2 no plano de acao com o motivo;
  - **guard de sobrecorrecao**: "nada a auditar" (`None`) NAO entra na faixa — biblioteca
    sem deploy nao pode virar `D–A`;
  - **guard de regressao**: snapshot com tudo medido renderiza byte-identico na nota.
- `docs/criteria.md` documenta a faixa e a ancora pessimista (os limiares continuam
  explicitos, discutiveis com o cliente).

## Fora de escopo (YAGNI)

- **Contrato do snapshot / collect.py**: nada muda. A distincao ambiente × nada-a-auditar
  ja chega ao render via `nao_medido`/`errors` + logica semantica propria; o snapshot nao
  precisa de campo novo.
- Comparacao de faixas inteiras (sobreposicao = inconclusivo) — descartada na decisao 2.
- Qualquer mudanca nos coletores.

## Entrega

A skill mora em **dois repositorios**: o publico (`Ruch-Digital/ruch-x`) e a copia
instalada versionada no `claude-setup` (`~/.claude/skills/ruch-x`). A frente so fecha
com os dois sincronizados na mesma sessao (`diff -rq` limpo fora `__pycache__`) — licao
de 2026-08-21, quando o publico ficou 6 dias defasado em silencio.

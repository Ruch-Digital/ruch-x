---
name: ruch-x
description: "Ruch-X é o raio-x e o auditor de engenharia de um repositório, em qualquer linguagem: dá nota (A-F) em cinco eixos — Entrega (métricas DORA: frequência de deploy, lead time, taxa de falha, tempo de recuperação), Qualidade (cobertura, complexidade, mapa de atrito), Segurança (segredo commitado, actions sem pin, dependências desatualizadas, supply chain), Confiabilidade (CI, runbooks, migrations, observabilidade) e Processo (branch protegida, docs, ADR) — e devolve um plano de ação priorizado (P0/P1/P2) num dashboard HTML offline com histórico. Use sempre que o usuário pedir métricas, indicadores, analytics, dashboard, \"como está o projeto\", saúde do código, débito técnico, cobertura de testes, onde refatorar, ou quiser comparar o estado atual com semanas anteriores — mesmo que ele não use a palavra \"dashboard\". Use também quando pedirem auditoria técnica, raio-x, diagnóstico, due diligence, \"tirar uma chapa\" de um repositório, avaliação de maturidade ou boas práticas, o que falta pro projeto estar no padrão do mercado, relatório de status técnico pra sócio ou cliente, métricas DORA, ou quando perguntarem o que medir num projeto. Use igualmente quando pedirem o Ruch-X pelo nome, ou falarem em \"rodar o raio-x\" de um sistema."
---

# Ruch-X

O raio-x de um repositório: **auditoria de engenharia com veredito**, entregue como um dashboard
HTML que abre offline e compara com as coletas anteriores.

Ele responde o que um cliente paga pra ouvir — *"vocês entregam rápido e com segurança?"*,
*"o que acontece quando quebra?"*, *"dá pra manter esse código no ano que vem?"* — e diz, com
evidência, o que está fora do padrão da engenharia atual e o que fazer a respeito.

## O veredito

Cinco eixos, cada um com nota (A–F) e plano de ação priorizado. Os critérios seguem as referências
correntes: **DORA** (Accelerate / State of DevOps) para entrega, **OWASP** e **SLSA** para supply
chain, **Google SRE** para confiabilidade.

| Eixo | O que audita |
|---|---|
| **Entrega** | as 4 métricas DORA: frequência de deploy, lead time do commit até produção, taxa de falha de mudança, tempo de recuperação |
| **Qualidade** | cobertura, complexidade e o arquivo de maior atrito |
| **Segurança** | segredo commitado, action sem pin, `permissions` no workflow, dependência velha, atualização automática, avisos do framework |
| **Confiabilidade** | CI verde, runbook de operação, migrations aplicadas, infraestrutura observável |
| **Processo** | branch protegida, README, decisões documentadas, licença, pre-commit, changelog |

Cada desconto de nota vira uma linha do plano com **prioridade (P0/P1/P2)**, o que está errado e
**como corrigir** — nota sem caminho é só nota baixa.

Os limiares vivem em `auditoria()` no `render.py`, explícitos de propósito: numa auditoria o
cliente pode discutir o critério, não receber veredito de caixa-preta.

O resto do painel segue respondendo as três perguntas de sempre: **o que mudou desde a última
vez**, **onde dói mais agora**, e **o que fazer a respeito**.

## Onde é seguro rodar

O Ruch-X **executa código do projeto auditado** — inclusive o `manage.py`, que
importa settings, apps e `.env` e abre conexão de banco. Rode em repositório
que você confia. Auditar código de terceiro (due diligence de repo alheio) pede
container ou VM descartável. Detalhe em `docs/security.md`.

Critério que a ferramenta não conseguiu medir sai como **não auditado** e
**não entra na nota** — nem premia nem pune. Se o painel disser "não auditado",
diga isso ao usuário em vez de tratar como aprovado.

## Como apresentar o veredito

Não leia os cinco eixos em voz alta — eles estão na tela. Diga **a nota mais baixa, por que ela é
baixa e qual a primeira coisa a fazer**. Se houver P0, ele vem antes de tudo: P0 é segredo
commitado ou equivalente, e não espera a próxima sprint.

Duas armadilhas que corroem a confiança no relatório:

- **Falso positivo mata auditoria.** Antes de afirmar "vocês têm segredo no repositório", abra o
  arquivo. O coletor já filtra fixture e placeholder, mas a palavra final é sua.
- **Nota baixa não é bronca.** "Sem cobertura medida" quer dizer que ninguém sabe o que a suíte
  protege — não que o time é ruim. O tom é de quem mostra o caminho, não de quem dá nota na prova.

## Primeira vez neste projeto: conduza o passo a passo

Quando não existir `.ruch-x/` no repositório, o usuário nunca rodou isso aqui.
Não despeje comandos — conduza, um passo por vez, esperando o resultado de cada
um antes de seguir. A primeira execução é onde a pessoa decide se a ferramenta
vale o espaço que ocupa.

**Passo 1 — diagnóstico.** Rode e mostre a saída:

```bash
python scripts/doctor.py
```

Ele detecta o stack, lista o que dá pra medir agora, o que falta instalar e o
que cada ausência custa. Termina imprimindo o comando exato de coleta para este
ambiente, já com os `--skip` necessários.

**Passo 2 — decida o que instalar agora.** Não peça pra instalar tudo. Pergunte
o que interessa: se a pessoa quer olhar código, `scc` e o linter bastam; se a
dor é banco ou servidor, o `psycopg` e o `RUCHX_DOCKER_HOST` importam mais.
Deixe claro que nada é obrigatório.

**Passo 3 — primeira coleta**, com o comando que o doctor imprimiu.

**Passo 4 — dashboard**: `python scripts/render.py --open`.

**Passo 5 — explique o que ele está vendo.** Três coisas, não mais: o achado de
severidade alta mais caro de ignorar, como ler o mapa de atrito, e por que rodar
de novo semana que vem muda o valor do que está na tela. Diga que nesta primeira
vez não há tendência — os números são ponto de partida, não diagnóstico.

**Passo 6 — combine a repetição.** Ofereça criar o cron ou o job de CI. Sem
segunda coleta, metade do que a skill faz nunca aparece.

Nas execuções seguintes, pule tudo isso: rode os dois comandos e comente o que
mudou desde a última vez.

## Fluxo normal

Duas etapas, sempre nessa ordem:

```bash
python scripts/collect.py            # grava .ruch-x/<data>.json
python scripts/render.py --open      # gera .ruch-x/dashboard.html
```

O `collect.py` roda na raiz do repositório. Cada coletor é independente: se o
Postgres estiver fora ou o `gh` não estiver instalado, os outros continuam e a
falha aparece no dashboard como aviso, não como erro fatal.

O `render.py` lê **todos** os snapshots de `.ruch-x/`, então quanto mais vezes
rodar, mais úteis ficam as tendências. Um snapshot só já gera o dashboard — só
não tem seta de variação.

## Qualquer linguagem

Contagem de linhas, mapa de atrito, git, banco, infraestrutura e CI funcionam em
qualquer stack. O que varia por linguagem é a profundidade de duas seções:
cobertura (a skill lê o relatório que sua suíte exporta — coverage.py, Istanbul,
lcov, Cobertura, Go, JaCoCo) e lint/complexidade (`ruff` e `radon` no Python,
`eslint` no JS, vazio nos demais).

Detalhes e como estender: `references/linguagens.md`.

## Ferramentas opcionais

O `doctor.py` já checa tudo isso e diz o que fazer. Nada é obrigatório, mas cada
ausência apaga uma parte do painel:

| Ferramenta | O que habilita | Instalação |
|---|---|---|
| `scc` ou `cloc` | contagem de linhas precisa por linguagem | `brew install scc` / `apt install cloc` |
| `radon` | complexidade real das funções Python | `pip install radon` |
| `ruff` | violações de lint agrupadas por regra (Python) | `pip install ruff` |
| `eslint` | violações de lint (JS/TS), se já estiver no projeto | `npm install -D eslint` |
| `psycopg[binary]` | seção inteira de banco | `pip install "psycopg[binary]"` |
| `gh` | seção de CI | `gh auth login` depois de instalar |
| `docker` | seção de infraestrutura | já existe se o projeto usa Docker |
| `gh` autenticado | **eixos Entrega e Processo** (DORA, branch protection) | `gh auth login` |

Sem nenhuma delas o script ainda roda — cai num contador de linhas próprio e
numa aproximação de complexidade por contagem de ramificações. Diga isso em vez
de travar pedindo instalação.

## Compatibilidade com a versão anterior

A skill já se chamou `metricas-projeto`. Se um projeto tem `.metricas/` ou
`metricas.toml`, tudo continua funcionando sem renomear nada: a coleta segue
gravando na pasta que já existe e as variáveis `METRICAS_*` ainda são lidas.
Isso é deliberado — o valor da ferramenta está na série acumulada, e um
rebatismo não pode custar o histórico de ninguém.

Em projeto novo, o padrão é `.ruch-x/` e `ruch-x.toml`.

## Configuração

Crie `ruch-x.toml` na raiz do projeto. Tudo é opcional; veja
`assets/ruch-x.toml.exemplo` para o arquivo completo comentado.

```toml
project = "meuprojeto"
modules_dir = "apps"       # onde ficam os módulos; detecta sozinho se omitir
run_tests = false          # true faz o collect rodar pytest --cov (demora)

[infra]
docker_host = "ssh://root@meu-vps"   # Coolify e Easypanel: aponta pro host
project_prefix = "meuprojeto"           # OBRIGATORIO pro coletor infra: e o vinculo repo<->containers
```

Coolify e Easypanel são ambos Docker por baixo, então a mesma configuração serve
aos dois — muda só o padrão de nome dos containers, o que importa no
`project_prefix`. **Sem `project_prefix` declarado, o coletor `infra` sai
como "não auditado"** — ele lê o Docker do host e não adivinha o que é do
projeto. Veja `references/infra-ci.md`.

**Segredo nunca entra no toml.** O DSN do banco vem de variável de ambiente:

```bash
export RUCHX_DATABASE_URL="postgresql://leitor:senha@host:5432/banco"
```

Use um usuário **somente leitura**. As consultas só tocam catálogo e estatística
(`pg_stat_*`, `pg_settings`), nunca tabela de negócio — mas o princípio do menor
privilégio evita que um erro futuro vire incidente.

## Como ler o resultado com o usuário

Depois de gerar, não descreva o dashboard seção por seção — ele já está na tela.
Aponte o caminho do arquivo e comente **no máximo três coisas**: o que piorou
desde a última coleta, o achado de severidade alta mais caro de ignorar, e um
próximo passo concreto.

Se for a primeira coleta, diga isso: sem histórico, os números são um ponto de
partida, não um diagnóstico. Tendência é o que dá sentido a eles.

### O mapa de atrito

É o gráfico que justifica a skill existir. Cada bolha é um arquivo, posicionado
por quantas vezes mudou (X) e quão complexo é (Y).

Linhas de código sozinhas não dizem nada: um arquivo de 2000 linhas que ninguém
abre há um ano não custa nada. O que custa é o arquivo que muda toda semana e
que ninguém entende — cada mudança ali é lenta e arriscada. Esses ficam no
quadrante destacado, e são os únicos onde refatoração se paga.

Quando o usuário perguntar "por onde começo a limpar", a resposta vem daqui, não
da lista de funções complexas.

### Limiares usados nos achados

Estão em `findings()` dentro de `render.py`. Ajuste se o contexto do projeto
pedir, mas explique a mudança:

- cobertura < 50% alto, < 70% médio
- cache hit do Postgres < 95% alto (normalmente `shared_buffers` curto)
- tabela com > 20% de linhas mortas e > 10k mortas: candidata a vacuum
- tabela > 5k linhas com seq_scan 10x maior que idx_scan: falta índice
- índice não-único com < 50 leituras e > 512 KB: ocioso
- CI verde < 85%: pipeline instável
- complexidade ciclomática > 10 por função

## Rodando periodicamente

O valor aparece na série temporal. Sugira ao usuário um agendamento semanal —
cron local, ou um job no CI que roda o `collect.py` e commita o snapshot:

```bash
0 8 * * 1 cd /caminho/do/projeto && python scripts/collect.py --skip infra
```

Versione os snapshots datados `.ruch-x/<data>.json` (são pequenos e viram
histórico). Deixe `.ruch-x/dashboard.html` e `.ruch-x/latest.json` no
`.gitignore` — o primeiro é derivado e regenera em um segundo; o segundo é só um
ponteiro pro snapshot mais recente, e versioná-lo duplica o arquivo datado a
cada coleta.

## Vários projetos

Para comparar repositórios diferentes, rode a coleta em cada um e gere um
dashboard por projeto. Não misture snapshots de projetos distintos na mesma
pasta: o histórico assume um único projeto e as tendências ficam sem sentido.

## Referências

- `references/postgres.md` — o que cada consulta do banco mede e como agir sobre
  o resultado. Leia quando o usuário perguntar sobre índice, vacuum, bloat ou
  query lenta.
- `references/linguagens.md` — o que funciona em cada linguagem, formatos de
  cobertura reconhecidos e como estender. Leia quando o projeto não for Python.
- `references/infra-ci.md` — como apontar o Docker para um host remoto
  (Coolify, Easypanel, VPS) e o que fazer quando a coleta de CI falha.
- `references/extensao.md` — como adicionar um coletor novo ou uma seção nova no
  dashboard. Leia antes de editar os scripts.

A pasta `docs/` é a documentação pública, em inglês (`security.md`, `criteria.md`,
`configuration.md`, `languages.md`, `extending.md`). Mande o usuário pra lá quando
ele quiser o detalhe de um critério, a lista completa de chaves do toml ou o
modelo de ameaça — não traduza o conteúdo de cabeça.

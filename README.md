# Ruch-X

Auditoria de engenharia de um repositório, em **qualquer linguagem**: dá nota de
A a F em cinco eixos, entrega um plano de ação priorizado e gera um **dashboard
HTML que abre offline**, comparando com as coletas anteriores.

Funciona como [skill do Claude Code](https://docs.claude.com/en/docs/claude-code/skills)
ou como dois scripts Python soltos — não precisa instalar nada no projeto medido.

Ele responde o que um cliente paga pra ouvir: *"vocês entregam rápido e com
segurança?"*, *"o que acontece quando quebra?"*, *"dá pra manter esse código no
ano que vem?"*.

## O veredito

Cinco eixos, cada um com nota e os critérios que a engenharia atual considera
padrão — **DORA** (Accelerate / State of DevOps) para entrega, **OWASP** e
**SLSA** para supply chain, **Google SRE** para confiabilidade:

| Eixo | O que audita |
|---|---|
| **Entrega** | as 4 métricas DORA: frequência de deploy, lead time do commit até produção, taxa de falha de mudança, tempo de recuperação |
| **Qualidade** | cobertura, complexidade e o arquivo de maior atrito |
| **Segurança** | segredo commitado, action sem pin, `permissions` no workflow, dependência velha, atualização automática, avisos do framework |
| **Confiabilidade** | CI verde, runbook de operação, migrations aplicadas, infraestrutura observável |
| **Processo** | branch protegida, README, decisões documentadas, licença, pre-commit, changelog |

Cada desconto de nota vira uma linha do plano com **prioridade (P0/P1/P2)**, o
que está errado e **como corrigir** — nota sem caminho é só nota baixa. Os
limiares ficam explícitos em `auditoria()` no `render.py`, de propósito: numa
auditoria o critério se discute, não se recebe de caixa-preta.

Dois cuidados que estão no código porque nasceram de uso real: **segredo em
arquivo de teste ou com placeholder não é vazamento** (senão o relatório perde a
credibilidade no primeiro alarme falso), e **cobertura ausente conta como
achado**, não como "não se aplica" — não medir é uma escolha com consequência.

## O que mais ele mede

| Área | O que sai |
|---|---|
| **Código** | linhas por linguagem e por módulo, proporção de teste, comentários |
| **Qualidade** | violações de lint agrupadas por regra, complexidade ciclomática por função |
| **Atrito** | mapa churn × complexidade — os arquivos que custam caro a cada mudança |
| **Testes** | cobertura por módulo, contagem, duração, testes mais lentos |
| **Git** | ritmo de commits, autores, idade do repositório |
| **Banco** | tamanho, cache hit, índices ociosos, tabelas sem índice, bloat (Postgres) |
| **Infra** | CPU e memória dos containers Docker (local ou host remoto por SSH) |
| **CI** | taxa de sucesso e duração das execuções do GitHub Actions |
| **Django** | migrations pendentes, avisos de segurança do `check --deploy`, models |

Cada coletor é independente: se o Postgres estiver fora ou o `gh` não existir,
os outros continuam e a falha vira um aviso no dashboard, não um erro fatal.

## O mapa de atrito

É o gráfico que justifica a ferramenta existir. Cada bolha é um arquivo,
posicionado por **quantas vezes mudou** (X) e **quão complexo é** (Y).

Linhas de código sozinhas não dizem nada: um arquivo de 2000 linhas que ninguém
abre há um ano não custa nada. O que custa é o arquivo que muda toda semana e
que ninguém entende — cada mudança ali é lenta e arriscada. Esses ficam no
quadrante destacado, e são os únicos onde refatoração se paga.

## Uso

```bash
python scripts/doctor.py     # diagnóstico: o que dá pra medir aqui e o que falta
python scripts/collect.py    # grava .ruch-x/<data>.json
python scripts/render.py --open   # gera e abre .ruch-x/dashboard.html
```

Rode na raiz do repositório que você quer medir. O `render.py` lê **todos** os
snapshots da pasta, então quanto mais vezes rodar, mais úteis ficam as
tendências — um snapshot só já gera o dashboard, apenas sem as setas de variação.

Versione os `.ruch-x/*.json` (são pequenos e viram histórico) e mande o
`dashboard.html` pro `.gitignore` — ele é derivado e regenera em um segundo.

## Qualquer linguagem

Contagem de linhas, mapa de atrito, git, banco, infraestrutura e CI funcionam em
qualquer stack. O que varia por linguagem é a profundidade de duas seções:

- **cobertura** — lê o relatório que sua suíte já exporta (coverage.py, Istanbul,
  lcov, Cobertura, Go, JaCoCo);
- **lint/complexidade** — `ruff` e `radon` no Python, `eslint` no JS.

Sem nenhuma ferramenta externa instalada ele ainda roda: cai num contador de
linhas próprio e numa aproximação de complexidade por contagem de ramificações.

## Ferramentas opcionais

Nada é obrigatório, mas cada ausência apaga um pedaço do painel — e o
`doctor.py` diz exatamente qual:

| Ferramenta | O que habilita |
|---|---|
| `scc` ou `cloc` | contagem de linhas precisa por linguagem |
| `radon` | complexidade real das funções Python |
| `ruff` / `eslint` | violações de lint agrupadas por regra |
| `psycopg[binary]` | seção inteira de banco |
| `gh` | seção de CI |
| `docker` | seção de infraestrutura |

## Configuração

Opcional. Crie `ruch-x.toml` na raiz do projeto medido — veja
[`assets/ruch-x.toml.exemplo`](assets/ruch-x.toml.exemplo) comentado.

```toml
project = "meuprojeto"
modules_dir = "apps"

[infra]
docker_host = "ssh://root@meu-vps"   # Coolify, Easypanel ou VPS pura
project_prefix = "meuprojeto"

[django]
settings_module = "meuprojeto.settings.production"
```

**Segredo nunca entra no toml.** O DSN do banco vem de variável de ambiente, e
com usuário **somente leitura** — as consultas só tocam catálogo e estatística
(`pg_stat_*`, `pg_settings`), nunca tabela de negócio:

```bash
export RUCHX_DATABASE_URL="postgresql://leitor:senha@host:5432/banco"
```

## Instalação como skill do Claude Code

```bash
git clone https://github.com/Ruch-Digital/ruch-x ~/.claude/skills/ruch-x
```

Depois é só pedir: *"roda o raio-x deste projeto"*.

## Rodando periodicamente

O valor aparece na série temporal. Um cron semanal ou um job de CI que roda a
coleta e commita o snapshot bastam:

```bash
0 8 * * 1 cd /caminho/do/projeto && python scripts/collect.py --skip infra
```

## Licença

MIT — veja [LICENSE](LICENSE).

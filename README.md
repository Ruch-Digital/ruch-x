# Ruch-X

O raio-x de um repositório. Coleta métricas de saúde do projeto e gera um
**dashboard HTML que abre offline**, com comparação contra as coletas anteriores.

Funciona como [skill do Claude Code](https://docs.claude.com/en/docs/claude-code/skills)
ou como dois scripts Python soltos — não precisa instalar nada no projeto medido.

O objetivo não é encher a tela de número. É responder três perguntas:
**o que mudou desde a última vez**, **onde dói mais agora**, e **o que fazer a respeito**.

## O que ele mede

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

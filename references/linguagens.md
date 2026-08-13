# Linguagens e stacks

A skill funciona em qualquer repositório. O que muda entre linguagens é a
**profundidade** de algumas seções, não a existência delas.

## O que funciona igual em toda linguagem

Não dependem de ferramenta específica de ecossistema:

- **Linhas de código por linguagem e por módulo** — via `scc`/`cloc`, ou pelo
  contador próprio quando nenhum dos dois existe.
- **Mapa de atrito** — o churn vem do git, que não sabe o que é Python. Fora do
  Python a complexidade é aproximada contando ramificações (`if`, `for`,
  `switch`, `&&`, `catch`...). É uma medida grosseira, mas o mapa só precisa
  ordenar os arquivos entre si, não produzir um número absoluto comparável.
- **Ritmo de commits, autores, idade do repositório.**
- **Banco de dados** — as consultas são no Postgres, não no seu código.
- **Infraestrutura** — containers Docker.
- **CI** — GitHub Actions.

## O que muda por linguagem

### Detecção de módulos

O coletor procura, nesta ordem: a pasta que você configurou em `ruch-x.toml`,
apps Django (pastas com `apps.py`), e pastas convencionais (`src`, `packages`,
`cmd`, `internal`, `pkg`, `lib`, `app`, `services`, `modules`). O rótulo no
dashboard muda junto — "App" em Django, "Pacote" em Go, "Módulo" no resto.

Em monorepo ou estrutura fora do convencional, aponte explicitamente:

```toml
modules_dir = "services"
```

### Cobertura de testes

Nenhum relatório é gerado pela skill — ela lê o que sua suíte já exporta. Os
formatos reconhecidos, na ordem de tentativa:

| Formato | Arquivo procurado | Como gerar |
|---|---|---|
| coverage.py | `coverage.json` | `pytest --cov --cov-report=json` |
| Istanbul (jest, vitest) | `coverage/coverage-summary.json` | `vitest run --coverage` |
| Istanbul detalhado | `coverage/coverage-final.json` | idem |
| lcov | `coverage/lcov.info`, `lcov.info` | padrão em muitos runners JS e Rust |
| Cobertura XML | `coverage.xml` | PHPUnit, .NET, pytest-cov `--cov-report=xml` |
| Go | `coverage.out` | `go test ./... -coverprofile=coverage.out` |
| JaCoCo | `target/site/jacoco/jacoco.xml` | `mvn test jacoco:report` |

Se o seu arquivo está em outro lugar, aponte no toml:

```toml
coverage_file = "build/reports/cobertura.xml"
```

A quebra de cobertura **por módulo** só existe hoje no formato do coverage.py.
Nos demais, o dashboard mostra o total. É a maior lacuna atual — os outros
formatos trazem o dado por arquivo, e agrupar por módulo é trabalho de parsing
que ainda não foi feito.

### Lint

- **Python**: `ruff`, agrupado por regra.
- **JavaScript/TypeScript**: `eslint`, só se já estiver instalado no projeto
  (a chamada usa `--no-install` justamente para não baixar pacote na máquina de
  quem está apenas medindo).
- **Demais linguagens**: a seção fica vazia com a instrução de como ligar.
  Adicionar um linter novo é pequeno — veja `extensao.md`.

### Complexidade por função

Só o Python tem número real, via `radon`. Nas outras linguagens a lista de
"funções mais complexas" fica vazia, mas o **mapa de atrito continua
funcionando** com a aproximação por ramificações.

### Migrations e checks de framework

Só Django. Em outros frameworks a seção some sozinha do dashboard em vez de
aparecer zerada.

## Adicionando suporte melhor para o seu stack

Vale o esforço quando você tem vários projetos na mesma linguagem. Os dois
pontos de extensão são `parse_coverage()` para um formato novo de cobertura e
`collect_quality()` para um linter novo. Ambos em `scripts/collect.py`, e o
padrão de cada um está em `extensao.md`.

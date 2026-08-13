# Languages and stacks

Ruch-X runs on any repository. What changes between languages is the **depth**
of a few sections, not whether they exist.

## Identical in every language

None of these depend on an ecosystem-specific tool:

- **Lines of code, per language and per module** — via `scc` or `cloc`, or via
  the built-in counter when neither is installed.
- **Friction map** — churn comes from git, which does not know what Python is.
  Outside Python, complexity is approximated by counting branch keywords (`if`,
  `for`, `while`, `case`, `catch`, `switch`, `&&`, `||`, `??`). It is a coarse
  measure, and the snapshot labels it as such (`metodo: "heuristica"`), because
  the map only has to rank files against each other.
- **Commit rhythm, authors, repository age.**
- **Governance** — documentation, `.gitignore`, workflow pinning and
  `permissions`, branch protection, committed secrets.
- **DORA** — GitHub Actions history plus git.
- **Database** — the queries run against PostgreSQL, not against your code.
- **Infrastructure** — Docker containers.
- **CI** — GitHub Actions.

## What varies

### Module detection

The collector tries, in order: the directory configured as
`apps_dir`/`modules_dir`; Django apps (any directory containing `apps.py`);
conventional source directories (`apps`, `src`, `packages`, `cmd`, `internal`,
`pkg`, `lib`, `app`, `services`, `modules`); and finally the root's own
directories.

The dashboard label follows: **App** for Django, **Pacote** for Go-style
`cmd`/`internal`/`pkg`/`packages`, **Módulo** otherwise, **Pasta** as the last
resort. In a monorepo or an unconventional layout, say it explicitly:

```toml
modules_dir = "services"
```

### Test coverage

Ruch-X generates no coverage report. It reads what your suite already exports.
Recognised formats, in the order they are tried:

| # | File | Format | How to generate |
|---|---|---|---|
| 1 | `coverage.json` | coverage.py | `pytest --cov --cov-report=json` |
| 2 | `coverage/coverage-summary.json` | Istanbul (jest, vitest) | `vitest run --coverage` |
| 3 | `coverage/coverage-final.json` | Istanbul, detailed | same |
| 4 | `coverage/lcov.info` | lcov | default in many JS and Rust runners |
| 5 | `lcov.info` | lcov | same |
| 6 | `coverage.xml` | Cobertura | PHPUnit, .NET, `pytest-cov --cov-report=xml` |
| 7 | `coverage.out` | Go | `go test ./... -coverprofile=coverage.out` |
| 8 | `cobertura.xml` | Cobertura | same as 6 |
| 9 | `target/site/jacoco/jacoco.xml` | JaCoCo | `mvn test jacoco:report` |

The first entry that exists **and parses** wins; one that exists but cannot be
read is skipped and the search continues. `coverage_file`, when set, is tried
before all nine.

The report's **age** is measured from the file's mtime and shown in the
dashboard when it is older than 14 days. Coverage read off a three-month-old
file is not today's coverage.

If your report lives somewhere else, `coverage_file` in the toml adds one extra
candidate — but **only JSON is parsed through that key**, in the coverage.py or
Istanbul shape. For an XML or lcov report, write it to one of the standard paths
above; that is the supported route today.

Per-module coverage breakdown exists only for the coverage.py format. The other
formats show the total. It is the largest current gap: they all carry per-file
data, and grouping it by module is parsing work nobody has done yet.

### Lint

- **Python** — `ruff`, grouped by rule.
- **JavaScript / TypeScript** — `eslint`, only when `npx` is available,
  `package.json` exists, and `ruff` produced nothing. The call is `npx --no-install eslint`, so nothing is
  downloaded onto the auditing machine. It does run the binary already present
  in the repository's `node_modules` (see [security.md](security.md)).
- **Everything else** — the section is empty, with instructions instead of
  numbers.

### Per-function complexity

Only Python gets a real number, from `radon`. Dependency directories are
excluded — without that, the "most complex functions" list fills up with
`site-packages`. In other languages the list is empty while the **friction map
keeps working** through the branch-counting approximation.

### Migrations and framework checks

Django only. In other frameworks the fields stay empty rather than reporting a
false zero, and the corresponding criteria drop out of the grade.

## Better support for your stack

Worth the effort when you have several projects in the same language. The two
extension points are `parse_coverage()` for a new coverage format and
`collect_quality()` for a new linter, both in `scripts/collect.py`. See
[extending.md](extending.md).

# Ruch-X

Engineering audit of a repository, in **any language**: a grade from A to F on
five axes, a prioritised action plan, and an **HTML dashboard that opens
offline**, compared against previous collections.

Runs as a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills)
or as two plain Python scripts — nothing is installed into the project being
measured.

It answers what a client is paying to hear: *"do you ship fast and safely?"*,
*"what happens when it breaks?"*, *"can this code still be maintained next
year?"*.

> **Security.** Ruch-X runs code from the project it audits — including its
> `manage.py`. Only run it on repositories you trust; auditing third-party code
> requires a container or a disposable VM. Full model, including what is *not*
> protected: [`docs/security.md`](docs/security.md).

## The verdict

Five axes, each with a grade and the criteria that current engineering practice
treats as standard — **DORA** (Accelerate / State of DevOps) for delivery,
**OWASP** and **SLSA** for supply chain, **Google SRE** for reliability:

| Axis | What it audits |
|---|---|
| **Delivery** | the 4 DORA metrics: deploy frequency, lead time from commit to production, change failure rate, time to restore |
| **Quality** | coverage, complexity, and the file with the most friction |
| **Security** | committed secrets, unpinned actions, workflow `permissions`, outdated dependencies, automated updates, framework warnings |
| **Reliability** | CI success, operational runbooks, applied migrations, observable infrastructure |
| **Process** | protected branch, README, documented decisions, license, pre-commit, changelog |

Every lost point becomes a line in the plan with a **priority (P0/P1/P2)**, what
is wrong, and **how to fix it** — a grade with no path is just a low grade. The
thresholds are explicit in `auditoria()` in `render.py`, deliberately: in an
audit the criterion is up for discussion, not handed down by a black box.

**A criterion the tool could not measure is reported as `null` — "not audited" —
and drops out of the grade's denominator.** It neither rewards nor punishes, and
an axis with nothing measurable gets no letter instead of an `F`. `0` and `[]`
mean the opposite: measured, and empty. A report that cannot tell "we scanned
and found no secrets" from "the scan never ran" is not an audit.

Two decisions that came out of real use: **a secret in a test file or with a
placeholder value is not a leak** (one false alarm and the whole report loses
credibility), and **missing coverage counts as a finding**, not as "not
applicable" — choosing not to measure is a choice with a consequence.

## What else it measures

| Area | Output |
|---|---|
| **Code** | lines per language and per module, test ratio, comments |
| **Quality** | lint violations grouped by rule, cyclomatic complexity per function |
| **Friction** | churn × complexity map — the files that cost you on every change |
| **Tests** | coverage per module, count, duration, slowest tests |
| **Git** | commit rhythm, authors, repository age |
| **Database** | size, cache hit, unused indexes, tables missing indexes, bloat (PostgreSQL) |
| **Infra** | CPU and memory of Docker containers (local or a remote host over SSH) |
| **CI** | success rate and duration of GitHub Actions runs |
| **Django** | pending migrations, `check --deploy` security warnings, models |

Each collector is independent: if PostgreSQL is down or `gh` is missing, the
others carry on and the failure becomes a warning in the dashboard, not a fatal
error.

## The friction map

This is the chart that justifies the tool. Each bubble is a file, placed by **how
often it changed** (X) and **how complex it is** (Y).

Lines of code alone say nothing: a 2000-line file nobody has opened in a year
costs nothing. What costs is the file that changes every week and that nobody
understands — every change there is slow and risky. Those sit in the highlighted
quadrant, and they are the only place where refactoring pays for itself.

## Usage

```bash
python scripts/doctor.py          # diagnosis: what can be measured here, and what is missing
python scripts/collect.py         # writes .ruch-x/<date>.json
python scripts/render.py --open   # builds and opens .ruch-x/dashboard.html
```

Run it from the root of the repository you want to measure. `render.py` reads
**every** snapshot in the directory, so the more often you run it, the more
useful the trends get — a single snapshot still produces the dashboard, just
without the deltas.

Commit the dated snapshots `.ruch-x/<date>.json` — they are small, and they
are the history. Keep `dashboard.html` and `latest.json` out of git: the first
is derived and regenerates in a second, the second is a convenience pointer to
the newest snapshot and would duplicate a file you already committed.

```gitignore
.ruch-x/dashboard.html
.ruch-x/latest.json
```

## Any language

Line counting, the friction map, git, database, infrastructure and CI work on
any stack. Two sections vary in depth by language:

- **coverage** — reads the report your suite already exports (coverage.py,
  Istanbul, lcov, Cobertura, Go, JaCoCo);
- **lint / complexity** — `ruff` and `radon` for Python, `eslint` for JS.

With no external tool installed at all it still runs: it falls back to its own
line counter and to a branch-counting approximation of complexity.

## Optional tools

Nothing is required, but each absence blanks out part of the panel — and
`doctor.py` tells you exactly which:

| Tool | What it enables |
|---|---|
| `scc` or `cloc` | accurate line counting per language |
| `radon` | real complexity of Python functions |
| `ruff` / `eslint` | lint violations grouped by rule |
| `psycopg[binary]` | the entire database section |
| `gh` | CI, DORA and branch protection |
| `docker` | the infrastructure section |

## Configuration

Optional. Create `ruch-x.toml` in the root of the project being measured — see
the commented [`assets/ruch-x.toml.exemplo`](assets/ruch-x.toml.exemplo), and
[`docs/configuration.md`](docs/configuration.md) for every key.

```toml
project = "myproject"
modules_dir = "apps"

[infra]
docker_host = "ssh://root@my-vps"   # Coolify, Easypanel or a bare VPS

[django]
settings_module = "myproject.settings.production"
```

Path keys (`manage_py`, `python`, `apps_dir`/`modules_dir`, `coverage_file`,
`coverage_json`) are resolved inside the repository root; absolute paths and
`../` are refused. That bounds where Ruch-X can reach, not what it runs:
`manage_py` and `python` name binaries that get executed, and the accepted value
is by definition a file from the repository ([`docs/security.md`](docs/security.md)).

**Secrets never go in the toml.** The database DSN comes from an environment
variable, and from a **read-only** user — the queries only touch catalogue and
statistics (`pg_stat_*`, `pg_settings`), never a business table:

```bash
export RUCHX_DATABASE_URL="postgresql://reader:<password>@host:5432/db"
```

## Security

Ruch-X executes `git`, `scc`/`cloc`, `ruff`, `radon`, `npx --no-install eslint`,
`gh`, `docker`, `pip`, and — in a Django project — **the project's own
`manage.py`**, which imports its settings, its apps and its `.env`, and opens a
database connection. There is no isolation, and there is no way to audit a
Django project without executing it.

Run it on repositories you trust. For third-party code, use a container or a
disposable VM.

The snapshot is meant to be committed, so credentials are redacted recursively
before it is written, and the secret scanner records only file, line and label —
never the matched value. The dashboard is offline in the strict sense: no
script tag, no remote font, no network request.

[`docs/security.md`](docs/security.md) states all of it, including the honest
list of what is **not** protected.

## Documentation

| Document | Contents |
|---|---|
| [`docs/security.md`](docs/security.md) | what gets executed, the threat model, what goes into the snapshot, what is not protected |
| [`docs/criteria.md`](docs/criteria.md) | every criterion, weight, threshold and source; how the grade is computed |
| [`docs/configuration.md`](docs/configuration.md) | complete `ruch-x.toml` and environment reference |
| [`docs/languages.md`](docs/languages.md) | what runs on each stack, recognised coverage formats |
| [`docs/extending.md`](docs/extending.md) | how to write a collector, and the contract it must honour |

`SKILL.md` and `references/` are written in Portuguese: they are the instructions
the agent reads while conducting the audit with the repository's owner.

## Installing as a Claude Code skill

```bash
git clone https://github.com/Ruch-Digital/ruch-x ~/.claude/skills/ruch-x
```

Then just ask for it: *"run the x-ray on this project"*.

## Running it periodically

The value shows up in the time series. A weekly cron job, or a CI job that
collects and commits the snapshot, is enough:

```bash
0 8 * * 1 cd /path/to/project && python scripts/collect.py --skip infra
```

## Tests

```bash
python3 -m unittest discover -s scripts/tests -t scripts/tests
```

136 tests, standard library only. The count is asserted by the suite itself
(`scripts/tests/test_docs.py`), so this line cannot drift from the code again.

## License

MIT — see [LICENSE](LICENSE).

# Configuration

Configuration is optional. With no config file at all, every collector uses its
default and the audit runs.

`collect.py` and `doctor.py` read `ruch-x.toml` from the repository root, and
fall back to `metricas.toml` (the name used before the tool was renamed).
`collect.py --config <file>` overrides both. An unreadable file produces a
warning on stderr and is treated as empty.

A commented example lives in
[`assets/ruch-x.toml.exemplo`](../assets/ruch-x.toml.exemplo).

## Path containment

`ruch-x.toml` is content of the repository being audited. Five keys are paths,
and all five are resolved **inside the repository root**:

`manage_py` · `python` · `apps_dir` / `modules_dir` · `coverage_file` ·
`coverage_json`

A value is accepted only if, after resolution, it is inside the root **and it
exists**. Absolute paths and anything escaping through `../` are refused, and so
is a path that simply is not there. A refused value falls back to the default —
it is never passed to a subprocess.

Without this, `manage_py = "/etc/passwd"` becomes a command argument and
`apps_dir = "/"` makes the scan walk the whole machine.

## Top level

| Key | Default | What it does |
|---|---|---|
| `project` | the root directory's name | Title of the dashboard and the `project` field of the snapshot. |
| `apps_dir` / `modules_dir` | auto-detected | Directory whose children are the project's modules. Path key. When unset, detection tries Django apps (`apps.py`), then `apps`, `src`, `packages`, `cmd`, `internal`, `pkg`, `lib`, `app`, `services`, `modules`, then the root's own directories. The two names are equivalent; `apps_dir` wins if both are set. |
| `manage_py` | `manage.py` | Django entry point. Path key. If it does not resolve, the whole `django` collector returns empty and runs nothing. |
| `python` | the interpreter running `collect.py` | Interpreter used for `manage.py` and for `pip list --outdated` — i.e. the project's virtualenv (`venv/bin/python`, `venv/Scripts/python.exe`). Path key: an interpreter outside the repository is refused and the auditor's own interpreter is used instead — which also means an accepted value is always a file **from the repository**, and Ruch-X executes it. `pip list --outdated` belongs to the `governance` collector, so this survives `--skip django`. See [security.md](security.md) §6. |
| `run_tests` | `false` | When `true` **and** the coverage JSON is missing, collection runs the project's test suite. See the warning below. |
| `pytest_args` | `["-q", "--cov", "--cov-report=json", "--durations=10"]` | Arguments for that run. Passed verbatim after `python -P -m pytest`. |
| `test_timeout` | `900` | Seconds before that run is killed. On timeout the collector records the timeout instead of a result. |
| `coverage_json` | `coverage.json` | Where the coverage.py JSON report is expected. Path key. Also decides whether `run_tests` has anything to do: if the file already exists, the suite is not run. |
| `coverage_file` | — | An extra coverage report, tried before the standard candidates. Path key. **JSON only:** a file pointed at here is parsed only if it has a `.json` suffix and a coverage.py (`totals`) or Istanbul (`total`) shape. An XML or lcov path is silently skipped — put those at one of the standard locations listed in [languages.md](languages.md) instead. |
| `hotspot_window` | `"180 days ago"` | Churn window of the friction map, passed to `git log --since=`. Any expression git accepts works. A young project deserves a shorter window; a legacy one, a longer. |

> **`run_tests = true` runs the repository's test suite**, including its
> `conftest.py`, which is arbitrary code. Leave it `false` for any repository you
> do not fully trust, and generate the coverage report in your normal workflow
> instead. See [security.md](security.md).

## `[django]`

| Key | Default | What it does |
|---|---|---|
| `settings_module` | unset | Exported as `DJANGO_SETTINGS_MODULE` for the `check --deploy` call only. |

Without it, `check --deploy` runs against whatever settings the current
environment selects — on a developer machine that means warnings about `DEBUG`
and SSL that only apply to production. Ruch-X does not treat those as findings:
with no `settings_module`, the "framework security warnings" criterion reports as
*não auditado* instead of failing the axis. Point it at production settings to
get an actual verdict, and export whatever variables those settings require
(`SECRET_KEY` and so on) before running.

## `[infra]`

| Key | Default | What it does |
|---|---|---|
| `docker_host` | the local Docker socket | Value of `DOCKER_HOST` for the three `docker` calls. `ssh://user@host` reads a remote machine (Coolify, Easypanel, a bare VPS) over SSH, which requires a passphrase-less key. |
| `project_prefix` | unset | Keeps only containers whose name contains this string — useful when one host runs several projects. |

Precedence: `RUCHX_DOCKER_HOST` → `[infra] docker_host` → `METRICAS_DOCKER_HOST`
→ `DOCKER_HOST`.

Only the scheme reaches the snapshot: `ssh://root@1.2.3.4` is stored as
`ssh://***`.

> `docker_host` is an **outbound connection to a host chosen by the config
> file**. If you are auditing a repository you did not write, read its
> `ruch-x.toml` before running, or use `--skip infra`.

## `[db]`

| Key | Default | What it does |
|---|---|---|
| `dsn` | unset | PostgreSQL connection string. Prefer the environment variable. |

Precedence: `RUCHX_DATABASE_URL` → `[db] dsn` → `METRICAS_DATABASE_URL` →
`DATABASE_URL`. With none of them the `db` collector raises and the section is
reported as not collected.

**Use a read-only user.** The queries only touch the catalogue and statistics
(`pg_stat_*`, `pg_settings`, `pg_stat_statements`) and never read a business
table — but least privilege is what keeps a future mistake from becoming an
incident:

```sql
CREATE USER ruchx WITH PASSWORD 'change-me';
GRANT pg_monitor TO ruchx;
GRANT CONNECT ON DATABASE mydb TO ruchx;
```

```bash
export RUCHX_DATABASE_URL="postgresql://ruchx:senha@host:5432/mydb"
```

Schema-per-tenant setups can pin a schema with
`?options=-csearch_path%3Dtenant_x`.

Passwords in a DSN are masked before the snapshot is written, including
passwords containing `@` or `:`. `[db] dsn` is still the wrong place for one:
the toml is committed, the environment is not.

## `[ci]`

| Key | Default | What it does |
|---|---|---|
| `limit` | `40` | How many GitHub Actions runs `gh run list` fetches for the CI section. |

## `[dora]`

| Key | Default | What it does |
|---|---|---|
| `limit` | `120` | How many runs are fetched for the DORA metrics. Deploys are a subset of these, so a low limit shortens the measurement window rather than biasing it. |
| `branch` | the repository's default branch, from `gh repo view` | Only runs on this branch count as deploys. Without the filter, pull-request CI inflates deploy frequency. |
| `deploy_keywords` | `["deploy", "release", "publish", "cd"]` | A run counts as a deploy when its workflow name contains one of these (case-insensitive substring). |

## Environment variables

| Variable | Read by | Purpose |
|---|---|---|
| `RUCHX_DATABASE_URL` | `collect.py`, `doctor.py` | PostgreSQL DSN. Highest precedence. |
| `METRICAS_DATABASE_URL`, `DATABASE_URL` | `collect.py` | Legacy and generic fallbacks. |
| `RUCHX_DOCKER_HOST` | `collect.py`, `doctor.py` | Docker host. Highest precedence. |
| `METRICAS_DOCKER_HOST`, `DOCKER_HOST` | `collect.py` | Fallbacks. |
| `DJANGO_SETTINGS_MODULE` | `collect.py` | Recorded in the snapshot when `[django] settings_module` is unset. Note that it is *reported*, not *trusted*: the "production settings" flag is only set by the toml key. |
| `NO_COLOR` | `doctor.py` | Disables ANSI colour. Output is also plain when stdout is not a TTY. |

Every one of these — and the rest of your environment — is inherited by every
subprocess Ruch-X starts, including the audited project's `manage.py`.

## Command-line flags

```bash
python scripts/doctor.py   [--root DIR] [--json]
python scripts/collect.py  [--root DIR] [--config FILE] [--only a,b] [--skip a,b] [--out FILE]
python scripts/render.py   [--dir DIR] [--out FILE] [--open]
```

Collector names for `--only` / `--skip`: `stack`, `code`, `quality`, `tests`,
`django`, `git`, `db`, `infra`, `ci`, `governance`, `dora`.

`render.py --out` is also the escape hatch for writing the dashboard outside the
snapshot directory; without it, a symlinked path is refused (see
[security.md](security.md)).

## Where output goes

`.ruch-x/<YYYY-MM-DD>.json` plus `.ruch-x/latest.json`, and
`.ruch-x/dashboard.html` from the render. If a `.metricas/` directory already
exists, collection keeps writing there instead — a rename must not cost anyone
their history.

Commit the `*.json` files; they are small and they are the time series. Put
`dashboard.html` in `.gitignore`; it is derived.

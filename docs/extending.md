# Extending Ruch-X

## The contract every collector must honour

A collector is a function `collect_x(root, cfg) -> dict` in
`scripts/collect.py`. Five rules keep the set trustworthy.

### 1. `None` means "not measured". `0` and `[]` mean "measured, and empty"

This is the rule the whole report rests on. A field that could not be measured
must be set to `None`, with the reason recorded:

```python
rc, so, se = run(["mytool", "--json"], cwd=root)
if rc != 0:
    nao_medido(out, "campo", _motivo(rc, se))   # out["campo"] = None
else:
    out["campo"] = parse(so)
```

`nao_medido(out, campo, motivo)` sets `out[campo] = None` and writes the reason
into `out["nao_medido"][campo]` (truncated to 200 characters). `_motivo(rc, se)`
turns a return code and stderr into a short reason: `comando nao encontrado`,
`timeout`, or the last line of stderr.

Never let a failed command become a zero. `[]` from a secret scanner reads as
"the repository is clean" — the strongest claim in the report — and a tool that
makes that claim because `git ls-files` failed is worse than no tool. The
dashboard already knows what to do with `None`: it labels the criterion *não
auditado*, shows the reason, and removes it from the grade's denominator.

Do not use the return code alone to decide whether a command measured anything.
`manage.py check --deploy` exits 1 both when it found problems and when it
crashed. The collector checks that the output has the *shape* of a check report
instead.

### 2. Never write a secret into the snapshot

Snapshots are committed. Redaction runs over the whole structure before it is
written (`redigir_estrutura` in `gravar_snapshot`), and it will mask a DSN
password, a `PASSWORD '...'`, a `key=value` assignment, a GitHub/OpenAI/AWS/Slack
token, a `Bearer` header and a PEM private key body.

That is a safety net, not a licence. A collector that has a credential in hand
keeps it out of its return value: store the shape, not the value. Two examples
already in the code — the Docker host is reduced to its scheme (`ssh://***`),
and the secret scanner records file, line and label, never the matched text.

### 3. Raise when the collector cannot run at all

The orchestrator catches the exception and records it in `errors[name]`, which
surfaces in the dashboard as an info-level finding. Use this for "there is
nothing to collect here" (no DSN, no `docker` binary). Use rule 1 for "I ran and
one measurement failed".

### 4. Read only, and always with a timeout

Collection never writes to the project, the database or the Docker host. Use the
`run()` helper: it takes a list argv (never a shell string), enforces a timeout,
and never raises — a missing binary comes back as `rc = 127`, a timeout as
`rc = 124`.

If you invoke a Python module, use `python -P -m <module>` so a same-named file
in the audited repository's root cannot shadow the real one.

### 5. Register it

Add the function to `REGISTRY` and its name to `COLLECTORS`, so `--only` and
`--skip` can address it.

### Example

```python
def collect_filas(root, cfg):
    import redis
    url = env_or(cfg.get("filas", {}), "url", "RUCHX_REDIS_URL")
    if not url:
        raise RuntimeError("set RUCHX_REDIS_URL")          # rule 3
    out = {"filas": None}
    try:
        r = redis.from_url(url, socket_timeout=5)
        out["filas"] = [{"nome": n.decode(), "pendentes": r.llen(n)}
                        for n in r.keys(b"celery*")]
    except Exception as exc:                                # rule 1
        nao_medido(out, "filas", str(exc))
    return out
```

## Adding a section to the dashboard

In `scripts/render.py`:

1. Write `build_x(snap)` returning HTML. Use the existing helpers — `table()`,
   `bar()`, `stat()`, `human_bytes()` — instead of hand-rolling markup; they
   already handle empty states and number formatting.
2. Add it to the `body` list inside `render()`, wrapped in
   `section(title, note, body)`.
3. If the metric deserves a trend, add a `stat()` in `build_signals()` with
   `sparkline(series(snaps, lambda s: ...))`.
4. If it deserves a recommendation, add the rule to `findings()`. A good rule
   says what to do, not just that something is bad.

### Treat the snapshot as untrusted input

A snapshot is a versioned file that may have been written by someone else, by an
older version of the tool, or by hand. The render never assumes a field's type:

- free text goes through `e()` (`html.escape`);
- anything interpolated as a number goes through `num()` or `milhar()`, which
  escape the value when it is not actually a number;
- `_seguro(v, tipos, default)` coerces a wrong-typed field before any
  arithmetic, indexing or `.get()`;
- `dig(obj, *keys)` walks nested dicts without raising on a missing level.

Interpolating a raw `{field}` into markup — even one you are sure is an integer
— is the bug class these helpers exist to prevent.

## Adding a coverage format or a linter

`parse_coverage()` takes the first candidate that exists and parses successfully;
add a `(path, kind)` entry to `COVERAGE_FILES` and a branch for the new `kind`.
Return `{"pct": float, "source": str}`, optionally with `by_app`.

`collect_quality()` is the place for a new linter. Follow the `ruff`/`eslint`
shape: `{"total": int, "by_rule": [...], "worst_files": [...]}`, plus a `tool`
key when it is not ruff, so the dashboard can label it.

## Empty states

Every section must say what to do when there is no data. `table()` takes that
text as its third argument. "no data" helps nobody; "no coverage.json — run your
suite with `--cov-report=json`" does.

## Schema changes

The snapshot carries `schema: 2`. When you change the structure incompatibly,
increment it and handle the older shape in `render.py`. Old snapshots are the
history — deleting them to simplify the code destroys the only thing the tool
accumulates over time.

## Tests

```bash
python3 -m unittest discover -s scripts/tests -t scripts/tests
```

82 tests, standard library only, no project dependencies. They cover the four
things that are easy to break by accident: path containment (6), the
`None`/`nao_medido` contract (18), redaction (28), and the render against
forged or malformed snapshots (30). `scripts/tests/_fake_repo.py` builds a
throwaway repository on a temp directory — no test touches a real project.

Add a test with the change, in the file that matches its class. A new redaction
pattern without a test asserting the secret is gone is a pattern nobody can
trust.

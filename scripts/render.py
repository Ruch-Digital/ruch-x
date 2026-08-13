#!/usr/bin/env python3
"""
Ruch-X - gera o dashboard HTML a partir dos snapshots em .ruch-x/.

Sem dependencia externa e sem rede: o HTML final abre offline, com fonte do
sistema e SVG inline. Da pra mandar por WhatsApp que abre.

Uso:
    python render.py                       # le .metricas/, escreve .metricas/dashboard.html
    python render.py --open                # e abre no navegador
"""

from __future__ import annotations

import argparse
import html
import json
import webbrowser
from datetime import datetime
from pathlib import Path

SNAPSHOT_DIR = ".ruch-x"
SNAPSHOT_DIR_LEGADO = ".metricas"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load_snapshots(dirpath):
    """Todos os snapshots ordenados do mais antigo pro mais novo."""
    snaps = []
    for f in sorted(Path(dirpath).glob("*.json")):
        if f.name == "latest.json":
            continue
        try:
            snaps.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    snaps.sort(key=lambda s: s.get("generated_at", ""))
    return snaps


def e(x):
    return html.escape(str(x if x is not None else "—"))


def human_bytes(n):
    if not isinstance(n, (int, float)) or n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def dig(obj, *keys, default=None):
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k)
        if obj is None:
            return default
    return obj


def series(snaps, getter):
    vals = []
    for s in snaps:
        try:
            v = getter(s)
        except Exception:  # noqa: BLE001
            v = None
        if isinstance(v, (int, float)):
            vals.append(v)
    return vals


def sparkline(values, w=118, h=24):
    """Sparkline SVG. Menos de 2 pontos nao vira grafico, vira ruido."""
    pts = [v for v in values if isinstance(v, (int, float))]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    step = w / (len(pts) - 1)
    coords = [(i * step, h - 3 - ((v - lo) / span) * (h - 6)) for i, v in enumerate(pts)]
    d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    lx, ly = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<path d="{d}" fill="none" stroke="currentColor" stroke-width="1.5" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.5" fill="currentColor"/></svg>')


def delta(values, invert=False, suffix="", atual=None):
    """
    Variacao vs snapshot anterior. invert=True quando subir eh ruim.

    Se o valor atual nao existe (coletor nao rodou desta vez), nao ha variacao
    a mostrar - exibir a seta antiga sugeriria um dado que nao foi medido.
    """
    if atual is None:
        return ""
    pts = [v for v in values if isinstance(v, (int, float))]
    if len(pts) < 2:
        return ""
    d = pts[-1] - pts[-2]
    if abs(d) < 1e-9:
        return '<span class="delta flat">estável</span>'
    good = (d < 0) if invert else (d > 0)
    arrow = "▲" if d > 0 else "▼"
    cls = "up" if good else "down"
    val = f"{abs(d):,.1f}".rstrip("0").rstrip(".").replace(",", ".")
    return f'<span class="delta {cls}">{arrow} {val}{suffix}</span>'


def stat(label, value, sub="", spark="", delta_html="", tone=""):
    foot = f'<div class="stat-foot">{delta_html}<span class="stat-sub">{sub}</span></div>' if (sub or delta_html) else ""
    trend = f'<div class="stat-trend">{spark}</div>' if spark else '<div class="stat-trend"></div>'
    return f"""
    <div class="stat {tone}">
      <div class="stat-label">{e(label)}</div>
      <div class="stat-value">{value}</div>
      {foot}{trend}
    </div>"""


def table(headers, rows, empty="nada a reportar"):
    if not rows:
        return f'<p class="empty">{e(empty)}</p>'
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def bar(pct, tone=""):
    pct = max(0, min(100, pct or 0))
    return (f'<div class="bar {tone}"><span style="width:{pct:.1f}%"></span></div>')


# --------------------------------------------------------------------------
# o grafico assinatura: churn x complexidade
# --------------------------------------------------------------------------

def hotspot_plot(hotspots, w=760, h=400):
    """
    Cada ponto eh um arquivo. Eixo X = quantas vezes mudou, Y = complexidade.
    O quadrante superior direito eh o unico que importa: codigo dificil que
    voce mexe toda hora. Refatorar canto inferior esquerdo eh perder tempo.
    """
    pts = [p for p in (hotspots or []) if p.get("churn") and p.get("complexity")]
    if len(pts) < 3:
        return '<p class="empty">histórico de git insuficiente para o mapa</p>'

    pad_l, pad_r, pad_t, pad_b = 54, 18, 18, 44
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    max_x = max(p["churn"] for p in pts) * 1.08
    max_y = max(p["complexity"] for p in pts) * 1.08
    med_x = sorted(p["churn"] for p in pts)[len(pts) // 2]
    med_y = sorted(p["complexity"] for p in pts)[len(pts) // 2]

    def sx(v):
        return pad_l + (v / max_x) * plot_w

    def sy(v):
        return pad_t + plot_h - (v / max_y) * plot_h

    danger_x, danger_y = sx(med_x), sy(med_y)
    parts = [
        f'<svg class="hotspot" viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="Mapa de arquivos por frequência de alteração e complexidade">',
        f'<rect x="{danger_x:.0f}" y="{pad_t}" width="{w - pad_r - danger_x:.0f}" '
        f'height="{danger_y - pad_t:.0f}" class="danger-zone"/>',
        f'<text x="{w - pad_r - 8:.0f}" y="{pad_t + 18}" class="zone-label" '
        f'text-anchor="end">zona de atrito</text>',
    ]

    for i in range(5):
        gy = pad_t + (plot_h / 4) * i
        parts.append(f'<line x1="{pad_l}" y1="{gy:.0f}" x2="{w - pad_r}" y2="{gy:.0f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{gy + 4:.0f}" class="axis" text-anchor="end">'
                     f'{max_y - (max_y / 4) * i:.0f}</text>')
    for i in range(5):
        gx = pad_l + (plot_w / 4) * i
        parts.append(f'<text x="{gx:.0f}" y="{h - pad_b + 20}" class="axis" text-anchor="middle">'
                     f'{(max_x / 4) * i:.0f}</text>')

    for p in pts[:60]:
        x, y = sx(p["churn"]), sy(p["complexity"])
        r = max(3.0, min(11.0, (p.get("loc", 0) / 90) ** 0.72))
        hot = p["churn"] >= med_x and p["complexity"] >= med_y
        cls = "dot hot" if hot else "dot"
        label = f'{p["file"]} · {p["churn"]}x alterações · complexidade {p["complexity"]} · {p.get("loc", 0)} linhas'
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" class="{cls}">'
                     f'<title>{e(label)}</title></circle>')

    parts.append(f'<text x="{pad_l + plot_w / 2:.0f}" y="{h - 6}" class="axis-title" '
                 f'text-anchor="middle">alterações nos últimos 6 meses →</text>')
    parts.append(f'<text x="14" y="{pad_t + plot_h / 2:.0f}" class="axis-title" '
                 f'text-anchor="middle" transform="rotate(-90 14 {pad_t + plot_h / 2:.0f})">'
                 f'complexidade →</text>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------
# leitura automatica: o que olhar primeiro
# --------------------------------------------------------------------------

def module_label(snap):
    return dig(snap, "code", "module_label") or "Módulo"


def pluralize_label(snap):
    base = module_label(snap)
    return {"App": "Apps", "Módulo": "Módulos", "Pacote": "Pacotes",
            "Pasta": "Pastas"}.get(base, base + "s")


def findings(snap):
    """
    Regras simples que transformam numero em recomendacao. Um dashboard que so
    mostra numero obriga a pessoa a lembrar dos limiares toda vez.
    """
    out = []

    cov = dig(snap, "tests", "coverage_pct")
    if isinstance(cov, (int, float)):
        if cov < 50:
            out.append(("alto", f"Cobertura de testes em {cov}%. Abaixo de 50% o teste não segura refactor."))
        elif cov < 70:
            unidade = pluralize_label(snap).lower()
            out.append(("medio", f"Cobertura em {cov}%. {'Os' if unidade.endswith('s') else 'O'} "
                                 f"{unidade} no fim da lista abaixo são o caminho mais curto pra subir."))

    if dig(snap, "quality", "ruff", "tool") == "eslint":
        pass  # rotulo tratado na secao de qualidade

    pend = dig(snap, "django", "pending_migrations") or []
    if pend:
        # Medio, nao alto: o check roda contra o banco que ESTE ambiente
        # aponta — em maquina de dev, migration pendente costuma significar
        # "esqueci de rodar migrate aqui", nao incidente de producao.
        out.append(("medio", f"{len(pend)} migration(s) pendente(s) no banco deste ambiente — "
                             f"rode o migrate ou confirme que o alvo está certo."))

    # security.* = configuracao insegura de verdade. O resto do `check
    # --deploy` (drf_spectacular, staticfiles) e recado de biblioteca e nao
    # merece o mesmo tom — separado no coletor desde 2026-08-12.
    issues = dig(snap, "django", "deploy_issues") or []
    if issues:
        settings_mod = dig(snap, "django", "settings_module") or "settings do ambiente"
        out.append(("alto", f"{len(issues)} aviso(s) de segurança do check --deploy "
                            f"(rodado com {settings_mod})."))

    outros = dig(snap, "django", "other_issues") or []
    if outros:
        erros = [i for i in outros if i.get("code", "").split(".")[-1].startswith("E")]
        if erros:
            out.append(("medio", f"{len(erros)} erro(s) de configuração de biblioteca no check: "
                                 f"{erros[0].get('code')} — {erros[0].get('message', '')[:90]}"))

    hot = (dig(snap, "git", "hotspots") or [])[:3]
    if hot:
        nomes = ", ".join(h["file"] for h in hot)
        out.append(("medio", f"Arquivos de maior atrito: {nomes}. São os que mais consomem tempo por mudança."))

    ratio = dig(snap, "db", "cache_hit_ratio")
    if isinstance(ratio, (int, float)) and ratio < 95:
        out.append(("alto", f"Cache hit do Postgres em {ratio}%. Abaixo de 95% normalmente é shared_buffers curto."))

    bloat = dig(snap, "db", "bloat_suspects") or []
    if bloat:
        pior = bloat[0]
        out.append(("medio", f"Tabela {pior['table']} com {pior['dead_pct']}% de linhas mortas. "
                             f"Candidata a VACUUM / revisão de autovacuum."))

    seqs = dig(snap, "db", "seq_scan_suspects") or []
    if seqs:
        out.append(("medio", f"{len(seqs)} tabela(s) grandes sendo lidas por varredura completa. Faltando índice."))

    unused = dig(snap, "db", "unused_indexes") or []
    if isinstance(unused, list) and unused:
        total = sum(u.get("bytes", 0) for u in unused)
        out.append(("baixo", f"{len(unused)} índice(s) quase nunca usados ocupando {human_bytes(total)}. "
                             f"Cada um custa em toda escrita."))

    sucesso = dig(snap, "ci", "success_rate")
    if isinstance(sucesso, (int, float)) and sucesso < 85:
        out.append(("medio", f"CI verde em {sucesso}% das execuções. Pipeline instável treina o time a ignorar falha."))

    cx = dig(snap, "quality", "complexity", "above_10")
    if isinstance(cx, int) and cx > 0:
        out.append(("baixo", f"{cx} função(ões) com complexidade acima de 10."))

    ruff = dig(snap, "quality", "ruff", "total")
    if isinstance(ruff, int) and ruff > 200:
        tool = dig(snap, "quality", "ruff", "tool") or "ruff"
        out.append(("baixo", f"{ruff} violações do {tool}. Vale rodar o autofix "
                             f"antes de olhar uma por uma."))

    for name, msg in (snap.get("errors") or {}).items():
        out.append(("info", f"Coletor '{name}' não rodou: {msg}"))

    ordem = {"alto": 0, "medio": 1, "baixo": 2, "info": 3}
    out.sort(key=lambda x: ordem.get(x[0], 9))
    return out


# --------------------------------------------------------------------------
# secoes
# --------------------------------------------------------------------------

def section(title, note, body, ident=None):
    anchor = f' id="{ident}"' if ident else ""
    sub = f'<p class="note">{note}</p>' if note else ""
    return f'<section{anchor}><h2>{e(title)}</h2>{sub}{body}</section>'


def build_signals(snap, snaps):
    cards = []

    loc = dig(snap, "code", "total_loc")
    nfiles = dig(snap, "code", "total_files")
    cards.append(stat("Linhas de código", f"{loc:,}".replace(",", ".") if loc else "—",
                      sub=f"{nfiles:,} arquivos".replace(",", ".") if nfiles else "",
                      spark=sparkline(series(snaps, lambda s: dig(s, "code", "total_loc")))))

    cov = dig(snap, "tests", "coverage_pct")
    cov_series = series(snaps, lambda s: dig(s, "tests", "coverage_pct"))
    tone = "" if not isinstance(cov, (int, float)) else ("bad" if cov < 50 else "warn" if cov < 70 else "good")
    ntests = dig(snap, "tests", "test_count")
    cards.append(stat("Cobertura", f"{cov}%" if cov is not None else "—",
                      sub=f"{ntests:,} testes".replace(",", ".") if ntests else "",
                      spark=sparkline(cov_series),
                      delta_html=delta(cov_series, suffix="pp", atual=cov), tone=tone))

    models = dig(snap, "django", "models")
    napps = dig(snap, "django", "apps")
    if models:
        cards.append(stat("Models", models,
                          sub=f"em {napps} apps" if napps else "",
                          spark=sparkline(series(snaps, lambda s: dig(s, "django", "models")))))
    else:
        mods = dig(snap, "code", "by_app") or []
        deps = dig(snap, "stack", "dependencies")
        cards.append(stat(pluralize_label(snap), len(mods) if mods else "—",
                          sub=("1 dependência" if deps == 1
                               else f"{deps} dependências" if deps else ""),
                          spark=sparkline(series(
                              snaps, lambda s: len(dig(s, "code", "by_app") or []) or None))))

    size = dig(snap, "db", "size")
    size_b = size[0]["bytes"] if isinstance(size, list) and size else None
    chr_ = dig(snap, "db", "cache_hit_ratio")
    cards.append(stat("Banco", human_bytes(size_b),
                      sub=f"cache hit {chr_}%" if chr_ else "",
                      spark=sparkline(series(
                          snaps, lambda s: (dig(s, "db", "size") or [{}])[0].get("bytes")))))

    commits = dig(snap, "git", "commits_30d")
    nautores = len(dig(snap, "git", "authors_30d") or [])
    cards.append(stat("Commits (30d)", commits if commits is not None else "—",
                      sub=("1 pessoa" if nautores == 1 else f"{nautores} pessoas") if nautores else "",
                      spark=sparkline(series(snaps, lambda s: dig(s, "git", "commits_30d")))))

    ci = dig(snap, "ci", "success_rate")
    ci_tone = "" if not isinstance(ci, (int, float)) else ("bad" if ci < 70 else "warn" if ci < 85 else "good")
    avg = dig(snap, "ci", "avg_duration_s")
    cards.append(stat("CI verde", f"{ci}%" if ci is not None else "—",
                      sub=f"{avg / 60:.0f} min em média" if avg else "",
                      spark=sparkline(series(snaps, lambda s: dig(s, "ci", "success_rate"))),
                      tone=ci_tone))

    return f'<div class="stats">{"".join(cards)}</div>'


def build_coverage(snap):
    rows = []
    for a in (dig(snap, "tests", "by_app") or [])[:20]:
        pct = a["coverage_pct"]
        tone = "bad" if pct < 50 else "warn" if pct < 70 else "good"
        rows.append([f'<code>{e(a["app"])}</code>', bar(pct, tone),
                     f'<span class="num">{pct}%</span>',
                     f'<span class="num dim">{a["statements"]:,}</span>'.replace(",", ".")])
    return table([module_label(snap), "", "Cobertura", "Linhas medidas"], rows,
                 "nenhum relatório de cobertura encontrado — veja references/linguagens.md "
                 "para o comando do seu stack")


def build_apps(snap):
    rows = []
    for a in (dig(snap, "code", "by_app") or [])[:20]:
        ratio = a.get("test_ratio")
        tone = "bad" if (ratio or 0) < 0.2 else "warn" if (ratio or 0) < 0.5 else "good"
        rows.append([f'<code>{e(a["app"])}</code>',
                     f'<span class="num">{a["code"]:,}</span>'.replace(",", "."),
                     f'<span class="num">{a["tests"]:,}</span>'.replace(",", "."),
                     f'<span class="num tag {tone}">{ratio if ratio is not None else "—"}</span>'])
    return table([module_label(snap), "Linhas", "Linhas de teste", "Razão teste/código"], rows)


def build_db(snap):
    db = snap.get("db") or {}
    blocks = []

    tables = db.get("tables")
    if isinstance(tables, list):
        rows = [[f'<code>{e(t["table"])}</code>',
                 human_bytes(t.get("total_bytes")),
                 human_bytes(t.get("index_bytes")),
                 f'<span class="num">{(t.get("live_rows") or 0):,}</span>'.replace(",", "."),
                 f'<span class="num dim">{(t.get("seq_scan") or 0):,}</span>'.replace(",", ".")]
                for t in tables[:15]]
        blocks.append("<h3>Maiores tabelas</h3>" + table(
            ["Tabela", "Total", "Índices", "Linhas", "Seq scans"], rows))

    unused = db.get("unused_indexes")
    if isinstance(unused, list):
        rows = [[f'<code>{e(u["index"])}</code>', f'<code class="dim">{e(u["table"])}</code>',
                 human_bytes(u.get("bytes")), f'<span class="num">{u.get("idx_scan")}</span>']
                for u in unused]
        blocks.append("<h3>Índices ociosos</h3>"
                      '<p class="note">Índice pouco lido continua sendo escrito em todo INSERT e UPDATE.</p>'
                      + table(["Índice", "Tabela", "Tamanho", "Leituras"], rows,
                              "nenhum índice ocioso relevante"))

    slow = db.get("slow_queries")
    if isinstance(slow, list) and slow:
        rows = [[f'<code class="sql">{e(q.get("query"))}</code>',
                 f'<span class="num">{(q.get("calls") or 0):,}</span>'.replace(",", "."),
                 f'<span class="num">{q.get("mean_ms")} ms</span>',
                 f'<span class="num">{q.get("total_s")} s</span>']
                for q in slow]
        blocks.append("<h3>Queries por tempo total</h3>" + table(
            ["Query", "Chamadas", "Média", "Total"], rows))
    elif isinstance(slow, dict):
        blocks.append('<h3>Queries por tempo total</h3><p class="empty">'
                      'pg_stat_statements não está habilitado neste banco</p>')

    return "".join(blocks) or '<p class="empty">banco não coletado</p>'


def build_infra(snap):
    conts = dig(snap, "infra", "containers") or []
    rows = []
    for c in conts:
        estado = c.get("state") or ""
        tone = "good" if estado == "running" else "warn"
        rows.append([f'<code>{e(c.get("name"))}</code>',
                     f'<span class="num">{e(c.get("cpu"))}</span>',
                     f'<span class="num">{e(c.get("mem"))}</span>',
                     f'<span class="num">{e(c.get("mem_pct"))}</span>',
                     f'<span class="tag {tone}">{e(c.get("status"))}</span>'])
    host = dig(snap, "infra", "host") or "—"
    head = f'<p class="note">Host: <code>{e(host)}</code></p>'
    return head + table(["Container", "CPU", "Memória", "Mem %", "Status"], rows,
                        "docker não acessível — veja METRICAS_DOCKER_HOST")


def build_ci(snap):
    rows = []
    for r in dig(snap, "ci", "recent") or []:
        conc = r.get("conclusion") or "em execução"
        tone = "good" if conc == "success" else "bad" if conc == "failure" else ""
        dur = f'{r["duration_s"] / 60:.1f} min' if r.get("duration_s") else "—"
        rows.append([f'<span class="tag {tone}">{e(conc)}</span>',
                     e(r.get("workflow")), f'<code class="dim">{e(r.get("branch"))}</code>',
                     e(r.get("title")), f'<span class="num">{dur}</span>'])
    return table(["Resultado", "Workflow", "Branch", "Commit", "Duração"], rows,
                 "sem dados do GitHub Actions")


def build_quality(snap):
    blocks = []
    worst = dig(snap, "quality", "complexity", "worst") or []
    if worst:
        rows = [[f'<code>{e(b["file"])}</code>', f'<code class="dim">{e(b["name"])}</code>',
                 f'<span class="num tag {"bad" if b["complexity"] > 15 else "warn"}">{b["complexity"]}</span>',
                 f'<span class="num dim">linha {b.get("line")}</span>']
                for b in worst[:12]]
        blocks.append("<h3>Funções mais complexas</h3>" + table(
            ["Arquivo", "Função", "Complexidade", ""], rows))

    rules = dig(snap, "quality", "ruff", "by_rule") or []
    if rules:
        rows = [[f'<code>{e(r["rule"])}</code>',
                 bar(100 * r["count"] / max(rules[0]["count"], 1)),
                 f'<span class="num">{r["count"]}</span>'] for r in rules]
        blocks.append("<h3>Violações do ruff por regra</h3>" + table(
            ["Regra", "", "Ocorrências"], rows))

    return "".join(blocks) or '<p class="empty">ruff/radon não instalados neste ambiente</p>'


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------

CSS = """
:root{
  --paper:#EDEDE7; --card:#F6F6F2; --ink:#14161A; --soft:#5C6470;
  --rule:#D2D3CA; --accent:#2B34C4; --good:#1F6E4A; --warn:#9A5B00; --bad:#A32020;
  --danger-bg:rgba(163,32,32,.07);
  --mono:ui-monospace,"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;
  --sans:"Inter","Helvetica Neue",-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{--paper:#101317;--card:#171B21;--ink:#E8EAED;--soft:#8A93A0;--rule:#272D36;
        --accent:#7C86FF;--good:#4FBF8B;--warn:#D9A03C;--bad:#E2685F;
        --danger-bg:rgba(226,104,95,.09);}
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
     font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:40px 24px 96px}

header{border-bottom:2px solid var(--ink);padding-bottom:20px;margin-bottom:36px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
         color:var(--soft);margin:0 0 6px}
.eyebrow b{color:var(--accent);font-weight:700}
h1{font-size:clamp(30px,5vw,46px);line-height:1.02;letter-spacing:-.03em;font-weight:800;margin:0}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:14px;font-family:var(--mono);
      font-size:12px;color:var(--soft)}
.meta b{color:var(--ink);font-weight:600}

section{margin-top:52px}
h2{font-size:13px;font-family:var(--mono);letter-spacing:.13em;text-transform:uppercase;
   font-weight:600;color:var(--soft);margin:0 0 4px;padding-bottom:8px;border-bottom:1px solid var(--rule)}
h3{font-size:13px;font-weight:700;letter-spacing:-.01em;margin:28px 0 8px}
.note{font-size:13px;color:var(--soft);margin:8px 0 14px;max-width:66ch}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(184px,1fr));
       background:var(--card);border:1px solid var(--rule);border-width:1px 0 0 1px;margin-top:18px}
.stat{background:var(--card);padding:16px 16px 13px;border:1px solid var(--rule);
      border-width:0 1px 1px 0}
.stat-label{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
            color:var(--soft)}
.stat-value{font-family:var(--mono);font-size:30px;font-weight:600;letter-spacing:-.035em;
            line-height:1.15;margin:6px 0 2px;font-variant-numeric:tabular-nums}
.stat.good .stat-value{color:var(--good)} .stat.warn .stat-value{color:var(--warn)}
.stat.bad .stat-value{color:var(--bad)}
.stat-foot{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}
.stat-sub{font-size:11.5px;color:var(--soft);font-family:var(--mono)}
.stat-trend{height:26px;margin-top:6px}
.spark{color:var(--accent);opacity:.75;display:block}
.delta{font-family:var(--mono);font-size:11px;font-weight:600}
.delta.up{color:var(--good)} .delta.down{color:var(--bad)} .delta.flat{color:var(--soft)}

.findings{list-style:none;padding:0;margin:16px 0 0;border-top:1px solid var(--rule)}
.findings li{display:flex;gap:14px;align-items:baseline;padding:11px 0;border-bottom:1px solid var(--rule)}
.sev{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
     font-weight:700;padding:2px 7px;border:1px solid currentColor;flex:none;min-width:64px;text-align:center}
.sev.alto{color:var(--bad)} .sev.medio{color:var(--warn)}
.sev.baixo{color:var(--accent)} .sev.info{color:var(--soft)}

.hotspot{width:100%;height:auto;display:block;margin-top:12px}
.hotspot .grid{stroke:var(--rule);stroke-width:1}
.hotspot .danger-zone{fill:var(--danger-bg)}
.hotspot .median{stroke:var(--rule);stroke-dasharray:3 3}
.hotspot .zone-label{fill:var(--bad);font-family:var(--mono);font-size:10px;
                     letter-spacing:.12em;text-transform:uppercase;opacity:.75}
.hotspot .axis{fill:var(--soft);font-family:var(--mono);font-size:10px}
.hotspot .axis-title{fill:var(--soft);font-family:var(--mono);font-size:10px;letter-spacing:.08em}
.hotspot .dot{fill:var(--accent);opacity:.42;cursor:default;transition:opacity .15s}
.hotspot .dot.hot{fill:var(--bad);opacity:.82}
.hotspot .dot:hover{opacity:1}

table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13.5px}
th{text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
   color:var(--soft);font-weight:600;padding:0 10px 7px 0;border-bottom:1px solid var(--rule)}
td{padding:8px 10px 8px 0;border-bottom:1px solid var(--rule);vertical-align:middle}
tbody tr:hover td{background:var(--card)}
code{font-family:var(--mono);font-size:12.5px}
code.sql{font-size:11.5px;color:var(--soft);display:block;max-width:52ch;overflow:hidden;
         text-overflow:ellipsis;white-space:nowrap}
.dim{color:var(--soft)}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}
.tag{font-family:var(--mono);font-size:11px;padding:1px 7px;border:1px solid currentColor;
     display:inline-block}
.tag.good{color:var(--good)} .tag.warn{color:var(--warn)} .tag.bad{color:var(--bad)}
.bar{display:block;width:100%;min-width:80px;height:6px;background:var(--rule)}
.bar span{display:block;height:100%;background:var(--accent)}
.bar.good span{background:var(--good)} .bar.warn span{background:var(--warn)}
.bar.bad span{background:var(--bad)}
.empty{font-family:var(--mono);font-size:12.5px;color:var(--soft);padding:14px 0;margin:0}

footer{margin-top:72px;padding-top:16px;border-top:1px solid var(--rule);
       font-family:var(--mono);font-size:11px;color:var(--soft)}
@media (max-width:640px){
  .wrap{padding:24px 14px 64px}
  table{font-size:12.5px} td,th{padding-right:6px}
  code.sql{max-width:26ch}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def render(snaps):
    snap = snaps[-1]
    when = snap.get("generated_at", "")
    try:
        when_fmt = datetime.fromisoformat(when).strftime("%d/%m/%Y às %H:%M")
    except ValueError:
        when_fmt = when

    fnd = findings(snap)
    rotulo = {"alto": "alto", "medio": "médio", "baixo": "baixo", "info": "info"}
    findings_html = "".join(
        f'<li><span class="sev {sev}">{rotulo.get(sev, sev)}</span><span>{e(msg)}</span></li>'
        for sev, msg in fnd
    ) or '<li><span class="sev baixo">ok</span><span>Nenhum alerta nos limiares configurados.</span></li>'

    meta = [
        f'<span>branch <b>{e(dig(snap, "git", "branch"))}</b></span>',
        f'<span>commit <b>{e(dig(snap, "git", "commit"))}</b></span>',
        f'<span>coletado em <b>{e(when_fmt)}</b></span>',
        f'<span><b>{len(snaps)}</b> snapshot(s) no histórico</span>',
    ]
    age = dig(snap, "git", "age_days")
    if age:
        meta.append(f'<span>repo com <b>{age}</b> dias</span>')
    stack = dig(snap, "stack", "detected") or []
    if stack:
        meta.append(f'<span>stack <b>{e(" · ".join(stack[:4]))}</b></span>')

    body = [
        '<div class="wrap">',
        '<header>',
        '<p class="eyebrow"><b>Ruch-X</b> · raio-x do sistema</p>',
        f'<h1>{e(snap.get("project"))}</h1>',
        f'<div class="meta">{"".join(meta)}</div>',
        '</header>',
        build_signals(snap, snaps),
        section("O que olhar primeiro",
                "Ordenado por impacto, não por seção. Se estiver tudo vazio, o projeto passou nos limiares.",
                f'<ul class="findings">{findings_html}</ul>'),
        section("Mapa de atrito",
                "Cada bolha é um arquivo. Quanto mais à direita, mais você mexe nele; quanto mais alto, "
                "mais difícil ele é. O tamanho é o número de linhas. Refatorar fora da zona destacada "
                "raramente devolve o tempo investido.",
                hotspot_plot(dig(snap, "git", "hotspots")), ident="atrito"),
        section(f"Cobertura por {module_label(snap).lower()}",
                "Ordenado do pior pro melhor — o topo da lista é onde uma hora de teste rende mais.",
                build_coverage(snap)),
        section("Distribuição do código", "", build_apps(snap)),
        section("Qualidade", "", build_quality(snap)),
        section("Banco de dados", "Somente estatísticas do catálogo. Nenhum dado de negócio é lido.",
                build_db(snap)),
        section("Infraestrutura", "", build_infra(snap)),
        section("Integração contínua", "", build_ci(snap)),
        f'<footer>Ruch-X · {e(when_fmt)} · '
        f'coletores: {e(", ".join(snap.get("collectors_run") or []))}</footer>',
        '</div>',
    ]

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(snap.get("project"))} · Ruch-X</title>
<style>{CSS}</style>
</head>
<body>{"".join(body)}</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Gera o dashboard HTML")
    ap.add_argument("--dir", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--open", action="store_true", dest="open_browser")
    args = ap.parse_args()

    # Aceita as duas pastas: quem coletou antes do rebatismo nao perde historico.
    dirs = [args.dir] if args.dir else [SNAPSHOT_DIR, SNAPSHOT_DIR_LEGADO]
    dirpath = next((d for d in dirs if Path(d).is_dir()), dirs[0])

    snaps = load_snapshots(dirpath)
    if not snaps:
        raise SystemExit(f"nenhum snapshot em {dirpath}/ — rode collect.py primeiro")

    out = Path(args.out or Path(dirpath) / "dashboard.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snaps), encoding="utf-8")
    print(str(out.resolve()))

    if args.open_browser:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()

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
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

SNAPSHOT_DIR = ".ruch-x"
SNAPSHOT_DIR_LEGADO = ".metricas"

# Espelho de `collect.COLLECTORS`. O render nao importa o coletor de proposito
# — os dois rodam sozinhos e o snapshot pode ter vindo de outra maquina, ou de
# uma versao diferente da ferramenta. A duplicacao existe pra que a tela
# consiga dizer "este coletor nem foi tentado"; um guard na suite trava as
# duas listas contra divergencia.
COLETORES_ESPERADOS = ("stack", "code", "quality", "tests", "django", "git",
                       "db", "infra", "ci", "governance", "dora")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def load_snapshots(dirpath):
    """Todos os snapshots ordenados do mais antigo pro mais novo.

    Snapshot e arquivo versionado — pode ter vindo de outra pessoa, de outra
    versao da ferramenta ou de um editor. Arquivo com forma errada e pulado
    com aviso; derrubar o render com traceback nao ajuda ninguem.
    """
    snaps = []
    for f in sorted(Path(dirpath).glob("*.json")):
        if f.name == "latest.json":
            continue
        try:
            dado = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(dado, dict):
            print(f"aviso: {f.name} ignorado (nao e um objeto JSON)", file=sys.stderr)
            continue
        snaps.append(dado)
    snaps.sort(key=lambda s: str(s.get("generated_at") or ""))
    return snaps


def e(x):
    return html.escape(str(x if x is not None else "—"))


def num(v, sufixo=""):
    """Valor que o painel trata como numero.

    O snapshot pode ter sido escrito por outra pessoa (ele e versionado e
    viaja junto do repositorio). Interpolar cru o que se supoe numero era
    a via mais obvia de XSS do dashboard (achada em 9 pontos) — mas nao a
    unica: qualquer `{campo}` cru dentro de uma tag, vindo de snapshot
    forjado, e a mesma classe de bug. `num()` cobre os pontos numericos;
    os de texto livre sempre passaram por `e()`.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return e(v)
    return f"{v}{sufixo}"


def milhar(v):
    """Numero com separador de milhar (ponto, padrao BR), ou None se `v`
    nao for numero (snapshot forjado/malformado). O resultado so tem digitos
    e ponto — nunca precisa passar por `e()`/`num()` depois, o formato em si
    ja e seguro (e nunca lanca excecao).

    `int` usa formatacao inteira nativa (`:,` sem `.0f`) — Python nao limita
    o tamanho de um int, e JSON tambem nao limita a precisao de um numero
    sem ponto/expoente, entao um snapshot forjado (ou so muito errado) pode
    trazer um inteiro maior que o float aguenta. `:,.0f` converteria pra
    float primeiro: acima de ~1.8e308 isso e `OverflowError`; abaixo disso
    mas ainda grande, e perda de precisao silenciosa (digito de lixo no
    dashboard). `float` continua pelo caminho antigo — um float ja e um
    float, `.0f` nele so arredonda, nao reconverte nada.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, int):
        return f"{v:,}".replace(",", ".")
    return f"{v:,.0f}".replace(",", ".")


def _seguro(v, tipos, default=None):
    """`v` se `isinstance(v, tipos)`, senao `default`.

    Peca central contra snapshot forjado/malformado: um campo que deveria
    ser numero/lista/dict mas chegou como outra coisa (string onde
    esperava lista, int onde esperava dict etc) vira o default aqui —
    ANTES de qualquer conta, indexacao ou `.get()` que assumiria a forma
    certa e estouraria TypeError/AttributeError/KeyError la na frente.
    """
    return v if isinstance(v, tipos) else default


def _dentro_de(caminho, base):
    """True se `caminho` (resolvido, symlinks seguidos) estiver dentro de
    `base` (tambem resolvido) — ou for o proprio `base`. Defesa contra o
    destino final do dashboard escapar do diretorio de snapshots."""
    caminho, base = caminho.resolve(), base.resolve()
    return caminho == base or base in caminho.parents


def _arquivos(itens, chave="file", limite=3):
    """Nomes de arquivo de uma lista de achados, pra texto de achado.

    Tolera item que nao e dict e item sem a chave — nao propaga
    KeyError/TypeError pro render por causa de um `{"file": ...}` faltando
    num snapshot malformado ou forjado.
    """
    nomes = [i.get(chave) for i in (itens or [])[:limite]
             if isinstance(i, dict) and i.get(chave)]
    return ", ".join(str(n) for n in nomes)


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
    # isinstance duplo: item tem que ser dict, E churn/complexity tem que ser
    # numero de verdade — senao o `max(...) * 1.08` la embaixo estoura
    # TypeError pra string, e `loc` negativo vira `complex` na conta do raio.
    pts = [p for p in (hotspots or []) if isinstance(p, dict)
           and isinstance(p.get("churn"), (int, float))
           and isinstance(p.get("complexity"), (int, float))]
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
        # loc negativo (snapshot forjado) elevado a 0.72 vira `complex` em
        # Python — `max()`/`min()` contra complex estoura TypeError. loc
        # nao-numerico ou negativo vira 0 (raio minimo), nunca crasha.
        loc = p.get("loc", 0)
        loc = loc if isinstance(loc, (int, float)) and loc >= 0 else 0
        r = max(3.0, min(11.0, (loc / 90) ** 0.72))
        hot = p["churn"] >= med_x and p["complexity"] >= med_y
        cls = "dot hot" if hot else "dot"
        label = (f'{p.get("file", "?")} · {p["churn"]}x alterações · '
                 f'complexidade {p["complexity"]} · {loc} linhas')
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


def _nao_medido(snap, *caminho):
    """Sufixo do rotulo quando o criterio nao pode ser medido.

    Criterio nao auditado ja sai do denominador da nota (`eixo()` so soma os
    itens com ok is not None). O que faltava era a tela DIZER isso — sem o
    motivo, o leitor supoe que passou.

    `caminho` e (coletor, [subchave...], campo): o mapa `nao_medido` fica ao
    lado do campo que ele explica, e nem todo campo mora na raiz do coletor
    (`governance.dependencias.total`, por exemplo, tem o proprio mapa dentro
    do sub-dict). O ultimo elemento e sempre o nome do campo.
    """
    *pais, campo = caminho
    motivo = dig(snap, *pais, "nao_medido", campo)
    return f" (não auditado: {motivo})" if motivo else ""


def _valor(v, sufixo=""):
    """Texto de um valor que pode nao ter sido medido.

    Interpolar `{v}` cru num f-string de rotulo escreve a string "None" na
    tela quando o coletor nao rodou — o painel afirmando, em portugues de
    Python, um numero que ninguem apurou (dez vezes no dashboard do proprio
    ruch-x, 2026-08-13). Nao-numero — inclusive `None` — vira travessao.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return "—"
    return f"{v}{sufixo}"


def _coletor_ausente(snap, coletor):
    """Motivo de um coletor inteiro nao ter produzido dado, ou "" se produziu.

    Coletor que levanta excecao nao deixa a propria chave no snapshot — so
    uma entrada em `errors`. Sem olhar ali, o criterio sai como "não
    auditado" pelado, e quem le nao descobre por que.
    """
    if isinstance(snap.get(coletor), dict):
        return ""
    erro = _seguro(snap.get("errors"), dict, {}).get(coletor)
    if erro:
        return str(erro)
    return f"coletor {coletor} não rodou"


def _nao_auditado(snap, coletor, *campo):
    """`_nao_medido` com fallback pro motivo do coletor inteiro.

    Ordem: o motivo especifico do campo (o coletor rodou e uma medicao
    falhou) ganha do motivo generico do coletor (nem rodou). Sem `campo`,
    so o generico — pro criterio que depende do coletor inteiro.
    """
    especifico = _nao_medido(snap, coletor, *campo) if campo else ""
    if especifico:
        return especifico
    generico = _coletor_ausente(snap, coletor)
    return f" (não auditado: {generico})" if generico else ""


def _observabilidade(snap, gov):
    """`(ok, sufixo_do_rotulo)` do criterio de observabilidade.

    Trocou de sujeito em 2026-08-21. Media `tem_alerta = bool(containers) or
    bool(db)`: a variavel prometia alerta e olhava container do HOST, entao um
    projeto com 45 alertas versionados tirava a mesma nota de um com zero — e
    um projeto sem nada passava se houvesse qualquer container de pe na
    maquina de quem rodou a auditoria. Agora o sinal vem do REPOSITORIO
    (`governance.observabilidade`), que e onde regra de alerta e config de
    stack de fato moram.

    Reprova, mas so de quem ENTREGA (decisao do dono): se o DORA identificou
    workflow de deploy, o projeto sobe pra algum lugar e precisa saber quando
    aquilo quebra. Biblioteca que nao deploya nao tem o que observar, e cai em
    "nada a auditar" — mesmo tratamento do fim dos 5 pontos de graca.
    """
    obs = _seguro(gov.get("observabilidade"), dict)
    if obs is None:
        # Snapshot anterior ao campo, ou coletor fora do ar. Ausencia de dado
        # nao vira acusacao — mas e o AMBIENTE que impediu a medicao, entao
        # entra na faixa (NAO_MEDIDO), nunca "nada a auditar".
        return NAO_MEDIDO, _nao_auditado(snap, "governance")

    alertas = _seguro(obs.get("alertas"), int) or 0
    stack = _seguro(obs.get("stack"), list) or []
    if alertas:
        return True, f" ({alertas} alerta(s) declarado(s) no repositório)"
    if stack:
        return False, (" (coleta métrica — %s — e nenhum alerta declarado)"
                       % ", ".join(stack[:3]))

    # Nada declarado: so e cobrado de quem entrega.
    wf_deploy = _seguro(dig(snap, "dora", "workflows_de_deploy"), list)
    if wf_deploy is None:
        # Sem o coletor `dora` nao da pra saber se o projeto entrega. Nao
        # afirmar vale mais que afirmar errado — e o ambiente que faltou,
        # entao entra na faixa.
        return NAO_MEDIDO, _nao_auditado(snap, "dora")
    if not wf_deploy:
        return None, " (nenhum deploy identificado — nada a auditar)"
    return False, " (nada declarado no repositório)"


def _motivo_do_mttr_vazio(snap, cfr):
    """Por que `mttr_h` esta vazio — e sao dois motivos opostos.

    Nenhuma falha na janela e BOA noticia (nada a auditar). Uma falha que
    ainda nao teve deploy verde depois dela e o contrario, e produz o MESMO
    `None`. O painel nao pode dar a mesma frase pros dois: dizer "nenhum
    deploy falhou" no segundo caso e absolver quem esta quebrado agora.

    Sem `deploys_analisados` (snapshot anterior ao fix de 2026-08-21) nao da
    pra afirmar nem um nem outro — segue mudo, que e a direcao conservadora.
    """
    analisados = _seguro(dig(snap, "dora", "deploys_analisados"), int)
    if not analisados or cfr is None:
        return ""
    if cfr == 0:
        return " (nenhum deploy falhou na janela — nada a auditar)"
    return " (falha sem deploy verde depois na janela)"


def _sim_nao(ok, sim="sim", nao="não", na="não auditado"):
    """Tres estados no resumo do eixo. `None`/`NAO_MEDIDO` NAO sao "não" —
    e nenhum dos dois tem truthiness (a sentinela estoura de proposito), por
    isso a comparacao explicita em vez de `if ok:`."""
    return na if ok is None or ok is NAO_MEDIDO else (sim if ok is True else nao)


# --------------------------------------------------------------------------
# auditoria: 5 eixos com nota, evidencia e o que fazer
# --------------------------------------------------------------------------
#
# A diferenca entre "painel de metricas" e "auditoria" e o veredito: numero
# sozinho deixa a conclusao por conta de quem le, e cada leitor tira uma. Cada
# criterio abaixo vale pontos de um total, e todo desconto vem com o que fazer
# pra recuperar — do contrario e so nota baixa sem caminho.
#
# As referencias sao as praticas correntes da industria: DORA (Accelerate /
# State of DevOps) pros numeros de entrega, OWASP e SLSA pra supply chain,
# Google SRE pra confiabilidade. Os limiares estao explicitos pra poderem ser
# discutidos com o cliente em vez de saírem de uma caixa-preta.

# Complexidade acumulada acima da qual o arquivo mais mexido do projeto deixa
# de ser "sob controle". E um numero ABSOLUTO — so pode ser aplicado a uma
# medicao absoluta (radon). Ver o criterio do arquivo de maior atrito.
LIMIAR_HOTSPOT = 150


class _NaoMedido:
    """Sentinela: criterio que o AMBIENTE da coleta impediu de medir (banco
    fora, gh mudo, radon ausente, timeout, coletor caido, snapshot anterior
    ao campo). Diferente de None ("nada a auditar": o projeto genuinamente
    nao tem aquilo). A distincao existe porque tratar os dois igual deixava
    um projeto MELHORAR de nota desligando o banco — o criterio que reprovava
    saia do denominador e os restantes passavam a valer 100% (FU-RUCHX-
    NAO-AUDITADO-PREMIA, 2026-08-22; spec em docs/superpowers/specs/).

    Identidade via `is`. Truthiness proibida: `bool()` estoura de proposito,
    porque `if ok:` num criterio classificaria a sentinela como aprovada.
    """
    __slots__ = ()

    def __bool__(self):
        raise TypeError("NAO_MEDIDO nao tem truthiness — compare com `is`")

    def __repr__(self):
        return "NAO_MEDIDO"


NAO_MEDIDO = _NaoMedido()

NIVEIS_DORA = {  # (elite, alto, medio) — abaixo disso e baixo
    "deploys_por_semana": (7, 1, 0.25),
    "lead_time_p50_h": (24, 168, 720),      # menor e melhor
    "change_failure_rate": (15, 30, 45),     # menor e melhor
    "mttr_h": (1, 24, 168),                  # menor e melhor
}


def _nivel_dora(chave, valor):
    if valor is None:
        return None
    elite, alto, medio = NIVEIS_DORA[chave]
    if chave == "deploys_por_semana":
        return "elite" if valor >= elite else "alto" if valor >= alto else "medio" if valor >= medio else "baixo"
    return "elite" if valor <= elite else "alto" if valor <= alto else "medio" if valor <= medio else "baixo"


def _nota(pontos, total):
    """(pct, letra). `total == 0` significa "nenhum criterio deste eixo pode
    ser medido" — nao pode virar 0% "F" (reprovacao falsa: o mesmo pecado do
    achado parqueado, so que na direcao oposta). Eixo sem nenhum criterio
    medido nao recebe letra nenhuma; "NA" e um marcador, tratado a parte na
    tela (nunca nota-F)."""
    if not total:
        return None, "NA"
    pct = 100 * pontos / total
    letra = ("A" if pct >= 90 else "B" if pct >= 75 else
             "C" if pct >= 60 else "D" if pct >= 40 else "F")
    return round(pct), letra


def auditoria(snap):
    """Retorna (eixos, achados). Achado = (prioridade, eixo, texto, acao)."""
    eixos, achados = [], []

    def eixo(nome, itens, resumo):
        """itens = [(peso, ok, rotulo, prioridade, achado, acao)]

        ok: True/False (medido) · NAO_MEDIDO (ambiente limitou — fica no
        denominador e abre a faixa pessimista-otimista) · None (nada a
        auditar — fora de tudo).
        """
        n_medidos = sum(1 for i in itens if i[1] is True or i[1] is False)
        total = sum(i[0] for i in itens if i[1] is not None)
        ganhos = sum(i[0] for i in itens if i[1] is True)
        incerto = sum(i[0] for i in itens if i[1] is NAO_MEDIDO)
        for peso, ok, rotulo, prio, txt, acao in itens:
            if ok is False:
                achados.append((prio, nome, txt, acao))
            elif ok is NAO_MEDIDO:
                # Sem esta linha o buraco de medicao fica invisivel — que e
                # como a FU passou despercebida por 6 dias.
                achados.append((
                    "P2", nome,
                    f"Critério «{rotulo}» não auditado por limitação do ambiente da coleta.",
                    "Restaurar a medição (subir o serviço, instalar a ferramenta, "
                    "autenticar o gh) — enquanto isso a nota do eixo fica em faixa."))
        if n_medidos == 0:
            # Faixa exige pelo menos UMA medicao real. Eixo 100% nao-medido
            # nao tem informacao nenhuma: "F–A 0–100%" seria tecnicamente
            # verdadeiro e inutil — segue NA, o contrato de sempre.
            pct, letra = _nota(0, 0)
            pct_max, letra_max = pct, letra
        else:
            pct, letra = _nota(ganhos, total)
            pct_max, letra_max = _nota(ganhos + incerto, total)
        eixos.append({"nome": nome, "pct": pct, "letra": letra,
                      "pct_max": pct_max, "letra_max": letra_max,
                      "resumo": resumo,
                      "medidos": n_medidos,
                      "criterios": len(itens),
                      "checados": [(i[2], i[1]) for i in itens]})

    # ---------------- Entrega (DORA) ----------------
    # extracao via dig() + _seguro (sem intermediario `d`): se "dora" no
    # snapshot for uma string em vez de dict, dig() ja devolve None em vez
    # de deixar um `.get()` cru estourar AttributeError; _seguro barra o
    # campo que existe mas nao e numero (a comparacao em _nivel_dora
    # estouraria TypeError contra string).
    freq = _seguro(dig(snap, "dora", "deploys_por_semana"), (int, float))
    lead = _seguro(dig(snap, "dora", "lead_time_p50_h"), (int, float))
    cfr = _seguro(dig(snap, "dora", "change_failure_rate"), (int, float))
    mttr = _seguro(dig(snap, "dora", "mttr_h"), (int, float))
    n_freq, n_lead = _nivel_dora("deploys_por_semana", freq), _nivel_dora("lead_time_p50_h", lead)
    n_cfr, n_mttr = _nivel_dora("change_failure_rate", cfr), _nivel_dora("mttr_h", mttr)
    bom = {"elite", "alto"}
    # Rotulo e resumo passam por `_valor`: sem medicao o numero vira travessao,
    # nunca a string "None". O texto do achado pode interpolar cru — ele so e
    # exibido quando `ok is False`, o que exige o valor existir.
    # `deploys_analisados` decide o 3o estado do mttr (ver comentario no item
    # abaixo): falta desse dado e ambiente, entao NAO_MEDIDO — nunca "nada a
    # auditar" de graca so porque o snapshot e anterior ao campo.
    analisados_mttr = _seguro(dig(snap, "dora", "deploys_analisados"), int)
    mttr_ok = (None if mttr is None and cfr == 0 and analisados_mttr
               else NAO_MEDIDO if mttr is None
               else n_mttr in bom)
    eixo("Entrega", [
        (3, NAO_MEDIDO if freq is None else n_freq in bom,
         f"frequência de deploy ({_valor(freq, '/semana')})"
         + (_nao_auditado(snap, "dora", "deploys_por_semana") if freq is None else ""), "P2",
         f"Deploy {freq}/semana — abaixo do patamar de time de alta performance (1+/semana).",
         "Encurtar o ciclo: integrar na main com mais frequência e automatizar o caminho até produção."),
        (3, NAO_MEDIDO if lead is None else n_lead in bom,
         f"lead time p50 ({_valor(lead, 'h')})"
         + (_nao_auditado(snap, "dora", "lead_time_p50_h") if lead is None else ""), "P1",
         f"Lead time de {lead}h entre commit e produção.",
         "Reduzir fila e etapas manuais entre merge e deploy."),
        (3, NAO_MEDIDO if cfr is None else n_cfr in bom,
         f"taxa de falha ({_valor(cfr, '%')})"
         + (_nao_auditado(snap, "dora", "change_failure_rate") if cfr is None else ""), "P1",
         f"{cfr}% das mudanças que chegam na branch de produção falham no pipeline.",
         "Gatear o merge com a suíte e rodar o teste do módulo tocado antes do push."),
        # Dois motivos diferentes produzem `mttr_h: None` — o coletor nao
        # ter medido, ou nao haver falha pra recuperar. `_nao_auditado` cobre
        # o primeiro; `_motivo_do_mttr_vazio`, o segundo (ver o helper). So o
        # primeiro (e a falha sem deploy verde depois) entra na faixa — nao
        # haver falha na janela e "nada a auditar" de verdade.
        (2, mttr_ok,
         f"tempo de recuperação ({_valor(mttr, 'h')})"
         + ((_nao_auditado(snap, "dora", "mttr_h")
             or _motivo_do_mttr_vazio(snap, cfr)) if mttr is None else ""), "P1",
         f"Leva {mttr}h em média pra recuperar de uma falha de deploy.",
         "Rollback documentado e imagem anterior sempre disponível encurtam isso pra minutos."),
    ], f"{_valor(freq, '/sem')} · lead {_valor(lead, 'h')} · "
       f"falha {_valor(cfr, '%')} · recuperação {_valor(mttr, 'h')}")

    # ---------------- Qualidade ----------------
    cov = _seguro(dig(snap, "tests", "coverage_pct"), (int, float))
    cov_idade = _seguro(dig(snap, "tests", "coverage_age_days"), int)
    cx = _seguro(dig(snap, "quality", "complexity", "above_10"), (int, float))
    blocos = _seguro(dig(snap, "quality", "complexity", "blocks_analyzed"), (int, float)) or 0
    pct_cx = (100 * cx / blocos) if (cx is not None and blocos) else None
    # so o primeiro item que E dict (com churn/complexity numericos ja
    # filtrados por hotspot_plot em outro lugar, mas aqui e um caminho
    # separado) conta como "o hotspot" — item malformado no topo da lista
    # nao pode derrubar o render, so e ignorado.
    hotspots_raw = _seguro(dig(snap, "git", "hotspots"), list) or []
    hot0 = next((h for h in hotspots_raw if isinstance(h, dict)), None)
    hot0_cx = _seguro(hot0.get("complexity"), (int, float), 0) if hot0 else 0
    hot0_metodo = hot0.get("metodo") if hot0 else None
    # `LIMIAR_HOTSPOT` e absoluto, entao so pode julgar numero absoluto. Sem
    # radon a complexidade do mapa vem de contagem de palavras de ramificacao,
    # e o proprio coletor declara que ela serve "pro que o mapa precisa:
    # ordenar arquivos entre si, nao produzir um numero absoluto". Medido
    # nos arquivos deste repositorio, a heuristica fica 25%-33% ABAIXO do
    # radon (collect.py 473 -> 354; render.py 337 -> 225): um arquivo que o
    # radon poe em 180 (reprova) a heuristica poe em 120 (aprova) — o
    # veredito passaria a depender de o auditor ter radon instalado, e nao do
    # codigo auditado. Sem a procedencia do numero o criterio nao e avaliado:
    # sai do denominador com o motivo na tela, que e a regra da casa.
    # `metodo` ausente e snapshot anterior ao campo (Task 4): procedencia
    # desconhecida vale o mesmo que heuristica — nao se julga o que nao se sabe.
    hot_ok = hot0_cx < LIMIAR_HOTSPOT if hot0_metodo == "radon" else NAO_MEDIDO
    if hot0 is None:
        hot_rotulo = _nao_auditado(snap, "git", "hotspots")
    elif hot0_metodo == "radon":
        hot_rotulo = ""
    elif hot0_metodo == "heuristica":
        hot_rotulo = " (não auditado: complexidade estimada sem radon)"
    else:
        hot_rotulo = " (não auditado: método de medição da complexidade não registrado)"
    eixo("Qualidade", [
        # Cobertura ausente e ACHADO, nao "nao se aplica": nao medir e uma
        # escolha com consequencia — ninguem sabe o que a suite protege.
        (4, cov >= 70 if cov is not None else False,
         f"cobertura ({cov}%)" + (f" — relatório de {cov_idade} dias atrás"
                                  if isinstance(cov_idade, int) and cov_idade > 14 else "")
         if cov is not None else "cobertura (não medida)", "P1",
         "Cobertura de testes não é medida — não há como saber o que a suíte protege."
         if cov is None else f"Cobertura em {cov}%.",
         "Rodar a suíte com relatório de cobertura e versionar o resultado a cada coleta."),
        (3, NAO_MEDIDO if pct_cx is None else pct_cx < 5,
         f"complexidade ({_valor(cx)} funções acima de 10)"
         + (_nao_auditado(snap, "quality", "complexity") if pct_cx is None else ""), "P2",
         f"{cx} funções com complexidade acima de 10 ({pct_cx:.1f}% do total)." if pct_cx else "",
         "Quebrar as piores em funções menores — começar pelas que aparecem no mapa de atrito."),
        (3, hot_ok, "arquivo de maior atrito sob controle" + hot_rotulo, "P1",
         f"O arquivo mais mexido do projeto ({hot0.get('file', '—') if hot0 else '—'}) acumula "
         f"complexidade {hot0_cx} em {hot0.get('loc', '—') if hot0 else '—'} linhas." if hot0 else "",
         "Dividir em partes menores: cada mudança nele hoje é lenta e arriscada."),
    ], f"cobertura {_valor(cov, '%')} · {_valor(cx)} funções complexas")

    # ---------------- Segurança ----------------
    # `gov`/`wf` sanitizados via `_seguro`: se "governance" (ou seu campo
    # "workflows") vier como string no snapshot, um `.get()` cru estourava
    # AttributeError — `_seguro` converte em {} ANTES do `.get()`.
    gov_raw = _seguro(snap.get("governance"), dict)
    gov = gov_raw or {}
    # Criterio que so existe dentro do `governance` (README, licenca, ADR,
    # runbook) e lido por EXISTENCIA de arquivo: `bool(docs.get("readme"))`.
    # Quando o coletor nao roda, `gov` vira {} e a leitura responde "nao tem" —
    # que e a mesma resposta de um arquivo realmente ausente. Sem separar os
    # dois, uma excecao no coletor virava 4 REPROVACOES no eixo Processo (e o
    # runbook na Confiabilidade). E a mesma classe do credito falso de
    # migrations, na direcao oposta: ali a falta de medicao premiava, aqui
    # punia. `_do_gov` decide o `ok`; `gov_rotulo` diz o motivo na tela.
    gov_erro = _seguro(snap.get("errors"), dict, {}).get("governance")
    gov_rotulo = "" if gov_raw is not None else (
        f" (não auditado: {gov_erro or 'coletor governance não rodou'})")

    def _do_gov(valor):
        """NAO_MEDIDO (ambiente: coletor governance nao rodou) ou bool(valor)."""
        return NAO_MEDIDO if gov_raw is None else bool(valor)

    # `seg` preserva o None de "nao medido" (`_seguro` com lista tambem cai
    # em None se o campo vier malformado — malformado converge com "nao
    # medido", nunca vira credito de seguranca de graca).
    seg = _seguro(gov.get("segredos_commitados"), list)
    # `wf_raw` preserva o None de "governance inteiro nao rodou" (o unico jeito
    # de wf ser None — o coletor individual sempre devolve dict, mesmo vazio).
    # `wf` (com `or {}`) e so pra leitura de texto/contagem, nunca pra decidir
    # ok — senao a falta de medicao vira "actions com pin" de graca, o mesmo
    # efeito que o achado parqueado tinha em segredos_commitados.
    wf_raw = _seguro(gov.get("workflows"), dict)
    wf = wf_raw or {}
    sem_pin = _seguro(wf.get("sem_pin"), list) or []
    sem_perm = _seguro(wf.get("sem_permissions"), list) or []
    # Dos workflows sem `permissions` no TOPO, quantos ja restringem o JOB que
    # escreve (ex: `packages: write` no job de publish) — o criterio nao muda
    # (o que protege TODOS os jobs e o bloco raiz), mas o achado nao pode ler
    # "sem permissions" como "sem nada": 2 workflows com o job ja restrito e
    # 1 sem restricao nenhuma sao problemas de tamanho bem diferente.
    perm_no_job = _seguro(wf.get("permissions_no_job"), list) or []
    # Decisao do dono (2026-08-20): repo SEM workflow nenhum nao "passa" nos
    # criterios de pin/permissions — `sem_pin == []` com zero workflows e
    # verdade vacua (nao ha o que proteger), e valia 5 pontos de graca em
    # Seguranca sobre quem tem 1 workflow imperfeito. Zero workflows =>
    # "nada a auditar" (None): nem premia nem pune, e o rotulo explica.
    # `count` existe em snapshot novo; num snapshot antigo sem o campo, um
    # repo COM workflow aparece pelas listas nao-vazias — e o antigo "limpo"
    # cai em "nada a auditar" (conservador: nunca infla a nota).
    n_wf = _seguro(wf.get("count"), (int, float))
    tem_wf = bool(n_wf) or bool(sem_pin) or bool(sem_perm) or bool(perm_no_job)
    wf_sem_nada = wf_raw is not None and not tem_wf
    rotulo_sem_wf = " (sem workflow no repositório — nada a auditar)" if wf_sem_nada else ""
    deps = _seguro(gov.get("dependencias"), dict, {})
    desatual = _seguro(deps.get("desatualizadas"), (int, float))
    total_deps = _seguro(deps.get("total"), (int, float))
    pct_velhas = (100 * desatual / total_deps) if (desatual is not None and total_deps) else None
    sec_django = _seguro(dig(snap, "django", "deploy_issues"), list)
    # A linha de resumo do eixo e a unica coisa que muita gente le. Ela nao
    # pode afirmar "0 segredo(s) · 0 action(s) sem pin" com o coletor
    # `governance` inteiro caido — era o achado parqueado da Task 3
    # reconstruido uma linha abaixo do criterio que a Task 7 consertou.
    n_seg = len(seg) if seg is not None else None
    # `wf_sem_nada` tambem zera o resumo: "0 action(s) sem pin" num repo sem
    # workflow e a mesma afirmacao vacua do criterio, uma linha acima.
    n_pin = len(sem_pin) if (wf_raw is not None and not wf_sem_nada) else None
    eixo("Segurança", [
        (5, NAO_MEDIDO if seg is None else len(seg) == 0,
         "nenhum segredo commitado"
         + (_nao_medido(snap, "governance", "segredos_commitados") or gov_rotulo), "P0",
         f"{len(seg or [])} possível(is) segredo(s) em arquivo versionado: {_arquivos(seg)}.",
         "Revogar a credencial (o histórico do git guarda para sempre) e mover para variável de ambiente."),
        # `wf_raw is None` = coletor caiu (ambiente) -> NAO_MEDIDO. `wf_sem_nada`
        # = coletor rodou e nao ha workflow nenhum pra proteger (nada a
        # auditar) -> continua None. Os dois produziam o mesmo `ok=None` antes
        # da faixa; agora tem que se distinguir.
        (3, NAO_MEDIDO if wf_raw is None else None if wf_sem_nada else len(sem_pin) == 0,
         "actions com versão fixada" + gov_rotulo + rotulo_sem_wf, "P1",
         f"{len(sem_pin)} action(s) referenciada(s) por tag móvel "
         f"(ex: {sem_pin[0] if sem_pin else '—'}).",
         "Fixar no SHA do commit: tag pode ser reapontada e roda código novo dentro do seu CI, com seus secrets."),
        (2, NAO_MEDIDO if wf_raw is None else None if wf_sem_nada else len(sem_perm) == 0,
         "workflows com permissions declarado" + gov_rotulo + rotulo_sem_wf, "P2",
         f"{len(sem_perm)} workflow(s) sem bloco `permissions` no topo — herdam token amplo "
         f"nos jobs que não o declaram."
         + (f" {len(perm_no_job)} já restringe(m) o job que escreve (ex: `packages: write`), "
            f"mas isso não protege os demais jobs do mesmo workflow." if perm_no_job else ""),
         "Declarar `permissions:` com o mínimo necessário no topo do workflow — "
         "o bloco no job protege só aquele job."),
        (2, NAO_MEDIDO if pct_velhas is None else pct_velhas < 25,
         f"dependências atualizadas ({_valor(desatual)}/{_valor(total_deps)})"
         + ((_nao_medido(snap, "governance", "dependencias", "total")
             or _nao_medido(snap, "governance", "dependencias", "desatualizadas")
             or gov_rotulo) if pct_velhas is None else ""), "P2",
         f"{desatual} de {total_deps} dependências desatualizadas ({pct_velhas:.0f}%)." if pct_velhas else "",
         "Dependência velha é dívida com juros: quanto mais espera, mais caro e arriscado o upgrade."),
        (2, _do_gov(gov.get("dependabot")), "atualização automática de dependências" + gov_rotulo, "P2",
         "Sem Dependabot/Renovate configurado.",
         "Ligar o Dependabot: ele abre PR de bump e avisa de CVE sem ninguém precisar lembrar."),
        # Aviso de seguranca medido no settings de DEV nao vale como achado:
        # DEBUG=True e falta de HSTS sao o esperado ali. Sem `[django]
        # settings_module` apontando producao, o criterio sai como "não
        # auditado" em vez de reprovar um sistema que pode estar correto.
        #
        # Mas apontar o settings_module de producao NAO torna a medicao uma
        # foto da producao: o `check --deploy` sobe com as VARIAVEIS DE
        # AMBIENTE da maquina de quem audita — a SECRET_KEY, senha e host
        # reais vivem no gerenciador de segredos do deploy. O modulo e de
        # producao; o ambiente e local. Sem dizer isso, um W009 (SECRET_KEY
        # fraca) medido na maquina de quem audita lia como "producao insegura"
        # quando pode ser so a maquina de dev sem a variavel exportada.
        (3, NAO_MEDIDO if (sec_django is None or not dig(snap, "django", "ambiente_de_producao"))
            else len(sec_django) == 0,
         "avisos de segurança do framework"
         + (_nao_medido(snap, "django", "deploy_issues")
            or ("" if dig(snap, "django", "ambiente_de_producao")
                else " (não auditado: settings de dev)")), "P1",
         f"{len(sec_django or [])} aviso(s) de segurança no check de deploy "
         f"(módulo: {dig(snap, 'django', 'settings_module') or 'do ambiente'} — é o módulo de "
         f"PRODUÇÃO, mas o AMBIENTE em que o check rodou é o local de quem auditou; avisos "
         f"ligados a valor de variável de ambiente — chave, senha, host — precisam ser "
         f"reconferidos no ambiente real, onde vive o segredo de verdade).",
         "Revisar HSTS, cookies seguros e redirect de SSL — e reconferir no AMBIENTE REAL de "
         "produção os avisos que dependem de valor de variável (ex: SECRET_KEY), já que este "
         "check rodou com as variáveis de ambiente desta máquina, não as do deploy."),
    ], f"{_valor(n_seg)} segredo(s) · {_valor(n_pin)} action(s) sem pin")

    # ---------------- Confiabilidade ----------------
    obs_ok, obs_rotulo = _observabilidade(snap, gov)
    runbooks = dig(snap, "governance", "docs", "runbooks")
    pend = _seguro(dig(snap, "django", "pending_migrations"), list)
    ci_ok = _seguro(dig(snap, "ci", "success_rate"), (int, float))
    # A taxa CONTINUA sendo sucesso sobre concluido (sucesso+falha) — cancelar
    # nao e falhar, as vezes e run substituido por um push seguinte. O que
    # faltava e o rotulo nao deixar "CI verde 100%" esconder um cancelamento:
    # run cancelado nao confirma que o pipeline passou (medido: job de teste
    # cancelado aos 26min, job de deploy do MESMO run subiu do mesmo jeito).
    ci_cancelados = _seguro(dig(snap, "ci", "cancelados"), (int, float))
    ci_cancel_sufixo = (f" · {int(ci_cancelados)} cancelado(s)"
                        if ci_cancelados else "")
    eixo("Confiabilidade", [
        (3, NAO_MEDIDO if ci_ok is None else ci_ok >= 85,
         f"CI verde ({_valor(ci_ok, '%')}{ci_cancel_sufixo})"
         + (_nao_auditado(snap, "ci", "success_rate") if ci_ok is None else ""), "P1",
         f"Pipeline verde em apenas {ci_ok}% das execuções.",
         "Pipeline instável faz o time ignorar vermelho — estabilizar antes de adicionar teste novo."),
        (3, _do_gov(runbooks), "runbook de operação" + gov_rotulo, "P1",
         "Sem runbooks: quando algo quebrar às 3h, a resposta está na cabeça de alguém.",
         "Escrever o passo a passo dos incidentes que já aconteceram — um arquivo por alerta."),
        (2, NAO_MEDIDO if pend is None else len(pend) == 0,
         "migrations aplicadas"
         + (_nao_auditado(snap, "django", "pending_migrations") if pend is None else ""), "P2",
         f"{len(pend or [])} migration(s) pendente(s) no ambiente medido.",
         "Aplicar ou confirmar que o alvo da medição é o ambiente certo."),
        (2, obs_ok, "infraestrutura observável" + obs_rotulo, "P2",
         "O repositório não declara como se descobre que a aplicação quebrou.",
         "Declarar regra de alerta versionada e um runbook por alerta — sem isso, "
         "incidente vira adivinhação."),
    ], f"CI {_valor(ci_ok, '%')} · runbooks {_sim_nao(_do_gov(runbooks))}")

    # ---------------- Processo ----------------
    bp = _seguro(gov.get("branch_protection"), dict, {})
    docs = _seguro(gov.get("docs"), dict, {})
    # `disponivel` falso cobre os dois casos de ambiente: gh mudo/rate-limit
    # (bp["motivo"] presente) ou o coletor `governance` inteiro fora do ar
    # (bp vira {}, "disponivel" ausente) — os dois viram NAO_MEDIDO.
    protegido = bp.get("protegido") if bp.get("disponivel") else NAO_MEDIDO
    # `docs/criteria.md` e explicito: reportar "desprotegida" por falha de
    # requisicao "seria uma acusacao sem ter olhado". O criterio ja saia como
    # None — faltava a LINHA DE RESUMO parar de afirmar, e o motivo aparecer
    # junto do criterio quando ele existe (`bp["motivo"]`: gh ausente, repo
    # sem remote, rate limit).
    bp_rotulo = ""
    if protegido is NAO_MEDIDO:
        bp_rotulo = (f" (não auditado: {bp['motivo']})"
                     if bp.get("motivo") else gov_rotulo)

    # Licenca ausente so e achado em repositorio PUBLICO — o proprio texto do
    # criterio ja dizia isso ("em repositorio privado e aceitavel") sem o
    # codigo respeitar. `visibility` vem do mesmo `gh repo view` que alimenta
    # `branch_protection` (`bp`), entao "nao apurei a visibilidade" e um
    # estado real: sem `gh`, repo sem remote, ou API que falhou.
    visibilidade = bp.get("visibility")
    if visibilidade == "PRIVATE":
        licenca_ok = None
        licenca_rotulo = "licença (não se aplica: repositório privado)"
    elif visibilidade == "PUBLIC":
        licenca_ok = _do_gov(docs.get("licenca"))
        licenca_rotulo = "licença" + gov_rotulo
    else:
        # Visibilidade desconhecida (sem gh, repo sem remote, API que falhou,
        # ou o coletor `governance` nem rodou): mantem o comportamento de
        # hoje — reprova arquivo ausente como sempre reprovou — mas o rotulo
        # diz que a visibilidade nao foi apurada, pra nao ler como "publico
        # confirmado". Quando `governance` nem rodou, `gov_rotulo` ja carrega
        # o motivo generico; nao duplica a mensagem.
        licenca_ok = _do_gov(docs.get("licenca"))
        licenca_rotulo = "licença" + gov_rotulo + (
            " (visibilidade do repositório não apurada)" if gov_raw is not None else "")

    eixo("Processo", [
        (4, protegido, "branch de produção protegida" + bp_rotulo, "P1",
         f"A branch `{bp.get('branch', 'main')}` aceita push direto, sem revisão nem check obrigatório.",
         "Ligar proteção exigindo status check verde — é a única barreira que não depende de disciplina."),
        (2, _do_gov(docs.get("readme")), "README" + gov_rotulo, "P2",
         "Sem README: quem chega no repositório não sabe rodar o projeto.",
         "Descrever o que é, como rodar e como testar."),
        (2, _do_gov(docs.get("adr") or docs.get("docs_dir")),
         "decisões documentadas" + gov_rotulo, "P2",
         "Sem registro de decisões técnicas (ADR ou pasta de docs).",
         "Registrar por que cada escolha estrutural foi feita — evita re-litigar decisão antiga."),
        (1, licenca_ok, licenca_rotulo, "P2",
         "Sem arquivo de licença — em repositório privado é aceitável; público, não.",
         "Adicionar LICENSE se o código for distribuído."),
        # `pre_commit`/`changelog` sao medidos por EXISTENCIA de arquivo, do
        # mesmo jeito que README/LICENSE/ADR: o coletor rodou e procurou.
        # `_do_gov` ja cobre a unica excecao real (coletor `governance`
        # inteiro fora do ar vira `None`/nao-auditado) — antes disso o
        # `... or None` reconstruia "medi e nao existe" em "nao medido",
        # tirando o criterio do denominador exatamente quando ele reprovaria.
        (1, _do_gov(gov.get("pre_commit")), "hooks de pre-commit" + gov_rotulo, "P2",
         "Sem pre-commit: lint e formatação dependem de lembrete humano.",
         "Configurar pre-commit com o linter que já está no projeto."),
        (2, _do_gov(bool(docs.get("changelog"))), "histórico de mudanças" + gov_rotulo, "P2",
         "Sem CHANGELOG.",
         "Gerar a partir dos commits se eles seguirem convenção."),
    ], f"branch {_sim_nao(protegido, 'protegida', 'desprotegida', 'com proteção não auditada')}")

    ordem = {"P0": 0, "P1": 1, "P2": 2}
    achados.sort(key=lambda a: ordem.get(a[0], 9))
    return eixos, achados


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
        prod = dig(snap, "django", "ambiente_de_producao")
        settings_mod = dig(snap, "django", "settings_module") or "settings do ambiente"
        if prod:
            out.append(("alto", f"{len(issues)} aviso(s) de segurança do check --deploy "
                                f"(módulo de produção: {settings_mod} — mas o AMBIENTE em que "
                                f"rodou é o local de quem auditou; reconferir no ambiente real "
                                f"os avisos ligados a valor de variável, como chave/senha/host)."))
        else:
            # Em dev, DEBUG e a falta de HSTS sao esperados — reportar como
            # falha de seguranca seria alarme falso.
            out.append(("info", f"{len(issues)} aviso(s) do check --deploy no settings de "
                                f"DESENVOLVIMENTO — normal aqui. Configure "
                                f"[django] settings_module pra auditar produção."))

    outros = _seguro(dig(snap, "django", "other_issues"), list) or []
    outros = [i for i in outros if isinstance(i, dict)]
    if outros:
        erros = [i for i in outros
                 if isinstance(i.get("code"), str) and i.get("code").split(".")[-1].startswith("E")]
        if erros:
            out.append(("medio", f"{len(erros)} erro(s) de configuração de biblioteca no check: "
                                 f"{erros[0].get('code')} — {erros[0].get('message', '')[:90]}"))

    hot = (_seguro(dig(snap, "git", "hotspots"), list) or [])[:3]
    nomes = _arquivos(hot, limite=3)
    if nomes:
        out.append(("medio", f"Arquivos de maior atrito: {nomes}. São os que mais consomem tempo por mudança."))

    ratio = dig(snap, "db", "cache_hit_ratio")
    if isinstance(ratio, (int, float)) and ratio < 95:
        out.append(("alto", f"Cache hit do Postgres em {ratio}%. Abaixo de 95% normalmente é shared_buffers curto."))

    bloat = _seguro(dig(snap, "db", "bloat_suspects"), list) or []
    pior = bloat[0] if bloat and isinstance(bloat[0], dict) else None
    if pior:
        out.append(("medio", f"Tabela {pior.get('table', '?')} com {pior.get('dead_pct', '?')}% de linhas mortas. "
                             f"Candidata a VACUUM / revisão de autovacuum."))

    seqs = _seguro(dig(snap, "db", "seq_scan_suspects"), list) or []
    if seqs:
        out.append(("medio", f"{len(seqs)} tabela(s) grandes sendo lidas por varredura completa. Faltando índice."))

    unused = _seguro(dig(snap, "db", "unused_indexes"), list) or []
    unused = [u for u in unused if isinstance(u, dict)]
    if unused:
        total = sum(_seguro(u.get("bytes"), (int, float), 0) for u in unused)
        out.append(("baixo", f"{len(unused)} índice(s) quase nunca usados ocupando {human_bytes(total)}. "
                             f"Cada um custa em toda escrita."))

    sucesso = dig(snap, "ci", "success_rate")
    if isinstance(sucesso, (int, float)) and sucesso < 85:
        out.append(("medio", f"CI verde em {sucesso}% das execuções. Pipeline instável treina o time a ignorar falha."))

    # A taxa de CI verde ja exclui cancelado do calculo (cancelar nao e
    # falhar) — mas "excluido do calculo" nao pode virar "nunca aconteceu".
    # Um run cancelado nao confirma que o pipeline passou; o caso mais caro e
    # o job de deploy do MESMO run ter subido antes do cancelamento acontecer
    # (medido: job de teste cancelado aos 26min, deploy do mesmo run no ar).
    cancelados = dig(snap, "ci", "cancelados")
    if isinstance(cancelados, int) and cancelados > 0:
        deploy_ok = _seguro(dig(snap, "ci", "cancelados_com_deploy_ok"), list) or []
        if deploy_ok:
            # `conclusion == "success"` e medido; "e um job de DEPLOY" e
            # inferido do NOME (`_job_parece_deploy` no coletor, que ja
            # filtra job de gate/check/lint/test — achado real no ion: um
            # job "Checks rapidos (gate de deploy)" batia com "deploy" no
            # nome sem ter executado deploy nenhum). O texto nao pode
            # afirmar "um job de deploy concluiu" como fato apurado — so o
            # que foi medido: sucesso + nome que sugere deploy.
            #
            # Severidade reavaliada e mantida ALTA mesmo com o hedge: o
            # filtro de portao ja fecha a classe de falso positivo que a
            # revisao achou, e o que sobra — job com nome limpo de deploy,
            # concluido com sucesso, no MESMO run de um teste cancelado — e
            # estruturalmente o incidente real que motivou este ajuste
            # (deploy subiu, teste nunca confirmou nada). Rebaixar pra medio
            # esconderia justamente o caso que esta frente existe pra expor;
            # o residual de falso positivo (nome que nao e portao nem deploy
            # de verdade) e raro e so gera uma sugestao de conferencia.
            out.append(("alto", f"{len(deploy_ok)} run(s) de CI cancelado(s) tinham, no mesmo "
                                f"run, um job cujo NOME sugere deploy com conclusão "
                                f"\"success\" — o pipeline foi tratado como \"verde\" sem o "
                                f"job de teste confirmar nada. Vale conferir se aquele job "
                                f"realmente implantou algo."))
        else:
            out.append(("medio", f"{cancelados} run(s) de CI cancelado(s) na janela medida. "
                                 f"Cancelamento não é falha, mas também não confirma que o "
                                 f"pipeline passou."))

    cx = dig(snap, "quality", "complexity", "above_10")
    if isinstance(cx, int) and cx > 0:
        out.append(("baixo", f"{cx} função(ões) com complexidade acima de 10."))

    ruff = dig(snap, "quality", "ruff", "total")
    if isinstance(ruff, int) and ruff > 200:
        tool = dig(snap, "quality", "ruff", "tool") or "ruff"
        out.append(("baixo", f"{ruff} violações do {tool}. Vale rodar o autofix "
                             f"antes de olhar uma por uma."))

    # `errors` como lista (nao dict) — snapshot malformado — nao pode
    # estourar `.items()`; `_seguro` converte em {} e o loop simplesmente
    # nao acha nada pra reportar.
    for name, msg in _seguro(snap.get("errors"), dict, {}).items():
        out.append(("info", f"Coletor '{name}' não rodou: {msg}"))

    # Coletor que nem chegou a ser TENTADO nao deixa rastro nenhum: nao esta
    # em `collectors_run` e nao esta em `errors`. Um `--only gouvernance`
    # (typo) produzia um laudo dizendo "Nenhum alerta nos limiares
    # configurados" em cima de uma coleta que nao aconteceu.
    rodaram = {str(c) for c in (_seguro(snap.get("collectors_run"), list) or [])}
    falharam = set(_seguro(snap.get("errors"), dict, {}))
    ausentes = [c for c in COLETORES_ESPERADOS if c not in rodaram and c not in falharam]
    if ausentes:
        out.append(("info", f"{len(ausentes)} coletor(es) fora desta coleta: "
                            f"{', '.join(ausentes)}. Os critérios que dependem "
                            f"deles saem como não auditados, e não como atendidos."))

    ordem = {"alto": 0, "medio": 1, "baixo": 2, "info": 3}
    out.sort(key=lambda x: ordem.get(x[0], 9))
    return out


# --------------------------------------------------------------------------
# secoes
# --------------------------------------------------------------------------

def build_veredito(snap, snaps):
    """Notas por eixo + plano de ação. E a primeira coisa que o cliente vê."""
    # `snaps` (a serie historica) ainda nao e usada aqui — entra na Task 5
    # (seta de tendencia). Aceita ja pra nao trocar a assinatura de novo.
    eixos, achados = auditoria(snap)
    if not eixos:
        return '<p class="empty">sem dados suficientes para o veredito</p>'

    cards = []
    for x in eixos:
        checados = "".join(
            f'<li class="{"sim" if ok is True else "nao" if ok is False else "na"}">{e(rot)}</li>'
            for rot, ok in x["checados"]
        )
        # letra "NA" = nenhum criterio do eixo pode ser medido (total == 0
        # em `_nota`). Nao e nota F (reprovado) nem letra nenhuma de verdade
        # — mostra travessao no lugar da letra e um aviso proprio, com
        # classe CSS propria (nunca nota-F, que e vermelho).
        na = x["letra"] == "NA"
        # pct pode divergir com letra igual (ex: 80-85, B-B) — e faixa do
        # mesmo jeito; a letra so exibe par quando as letras diferem.
        faixa = (not na) and x["pct_max"] != x["pct"]
        letra_display = "—" if na else (
            f'{x["letra"]}–{x["letra_max"]}' if x["letra_max"] != x["letra"] else x["letra"])
        # Quantos criterios sustentam a letra. Sem isso, "F" com 1 de 4
        # medidos e "F" com 4 de 4 sao a mesma imagem — e o primeiro e um
        # veredito com 25% de base. No eixo NA a frase abaixo ja diz que
        # nada foi medido; repetir "0 de N" seria ruido.
        base = ('' if na else
                f'<p class="eixo-base">{x["medidos"]} de {x["criterios"]} '
                f'critérios auditados'
                + (f' · nota em faixa ({x["pct"]}–{x["pct_max"]}%) — '
                   f'o ambiente da coleta limitou a medição' if faixa else '')
                + '</p>')
        cards.append(
            f'<div class="eixo nota-{x["letra"]}">'
            f'<div class="letra">{letra_display}</div>'
            f'<div class="eixo-corpo"><h3>{e(x["nome"])}</h3>'
            f'<p class="eixo-resumo">{e(x["resumo"])}</p>'
            + ('<p class="eixo-na">não auditado — nenhum critério deste eixo pôde ser medido</p>'
               if na else '')
            + base
            + f'<ul class="checklist">{checados}</ul></div></div>'
        )

    linhas = "".join(
        f'<tr><td><span class="prio {p}">{p}</span></td><td>{e(eixo)}</td>'
        f'<td><b>{e(txt)}</b><br><span class="acao">{e(acao)}</span></td></tr>'
        for p, eixo, txt, acao in achados[:12]
    ) or '<tr><td colspan="3">Nenhum desvio nos critérios auditados.</td></tr>'

    return (f'<div class="eixos">{"".join(cards)}</div>'
            f'<table class="plano"><thead><tr><th></th><th>eixo</th>'
            f'<th>o que corrigir e como</th></tr></thead><tbody>{linhas}</tbody></table>')


def section(title, note, body, ident=None):
    anchor = f' id="{ident}"' if ident else ""
    sub = f'<p class="note">{note}</p>' if note else ""
    return f'<section{anchor}><h2>{e(title)}</h2>{sub}{body}</section>'


def build_signals(snap, snaps):
    cards = []

    loc = _seguro(dig(snap, "code", "total_loc"), (int, float))
    nfiles = _seguro(dig(snap, "code", "total_files"), (int, float))
    cards.append(stat("Linhas de código", milhar(loc) or "—",
                      sub=f"{milhar(nfiles)} arquivos" if nfiles else "",
                      spark=sparkline(series(snaps, lambda s: dig(s, "code", "total_loc")))))

    cov = _seguro(dig(snap, "tests", "coverage_pct"), (int, float))
    cov_series = series(snaps, lambda s: dig(s, "tests", "coverage_pct"))
    tone = "" if cov is None else ("bad" if cov < 50 else "warn" if cov < 70 else "good")
    ntests = _seguro(dig(snap, "tests", "test_count"), (int, float))
    cards.append(stat("Cobertura", num(cov, "%") if cov is not None else "—",
                      sub=f"{milhar(ntests)} testes" if ntests else "",
                      spark=sparkline(cov_series),
                      delta_html=delta(cov_series, suffix="pp", atual=cov), tone=tone))

    models = dig(snap, "django", "models")
    napps = dig(snap, "django", "apps")
    if models:
        cards.append(stat("Models", num(models),
                          sub=f"em {num(napps)} apps" if napps else "",
                          spark=sparkline(series(snaps, lambda s: dig(s, "django", "models")))))
    else:
        mods = _seguro(dig(snap, "code", "by_app"), list) or []
        deps = dig(snap, "stack", "dependencies")
        cards.append(stat(pluralize_label(snap), len(mods) if mods else "—",
                          sub=("1 dependência" if deps == 1
                               else f"{num(deps)} dependências" if deps else ""),
                          spark=sparkline(series(
                              snaps, lambda s: len(_seguro(dig(s, "code", "by_app"), list) or []) or None))))

    size = _seguro(dig(snap, "db", "size"), list)
    size0 = size[0] if size and isinstance(size[0], dict) else None
    size_b = size0.get("bytes") if size0 else None
    chr_ = dig(snap, "db", "cache_hit_ratio")
    cards.append(stat("Banco", human_bytes(size_b),
                      sub=f"cache hit {num(chr_)}%" if chr_ else "",
                      spark=sparkline(series(
                          snaps, lambda s: (_seguro(dig(s, "db", "size"), list) or [{}])[0].get("bytes")))))

    commits = dig(snap, "git", "commits_30d")
    nautores = len(_seguro(dig(snap, "git", "authors_30d"), list) or [])
    cards.append(stat("Commits (30d)", num(commits) if commits is not None else "—",
                      sub=("1 pessoa" if nautores == 1 else f"{nautores} pessoas") if nautores else "",
                      spark=sparkline(series(snaps, lambda s: dig(s, "git", "commits_30d")))))

    ci = _seguro(dig(snap, "ci", "success_rate"), (int, float))
    ci_tone = "" if ci is None else ("bad" if ci < 70 else "warn" if ci < 85 else "good")
    avg = _seguro(dig(snap, "ci", "avg_duration_s"), (int, float))
    cards.append(stat("CI verde", num(ci, "%") if ci is not None else "—",
                      sub=f"{avg / 60:.0f} min em média" if avg else "",
                      spark=sparkline(series(snaps, lambda s: dig(s, "ci", "success_rate"))),
                      tone=ci_tone))

    return f'<div class="stats">{"".join(cards)}</div>'


def build_coverage(snap):
    rows = []
    for a in (_seguro(dig(snap, "tests", "by_app"), list) or [])[:20]:
        if not isinstance(a, dict):
            continue
        pct = _seguro(a.get("coverage_pct"), (int, float))
        tone = "bad" if pct is None or pct < 50 else "warn" if pct < 70 else "good"
        rows.append([f'<code>{e(a.get("app"))}</code>', bar(pct, tone),
                     f'<span class="num">{num(pct)}%</span>' if pct is not None else '<span class="num">—</span>',
                     f'<span class="num dim">{milhar(a.get("statements")) or "—"}</span>'])
    return table([module_label(snap), "", "Cobertura", "Linhas medidas"], rows,
                 "nenhum relatório de cobertura encontrado — veja references/linguagens.md "
                 "para o comando do seu stack")


def build_apps(snap):
    rows = []
    for a in (_seguro(dig(snap, "code", "by_app"), list) or [])[:20]:
        if not isinstance(a, dict):
            continue
        ratio = _seguro(a.get("test_ratio"), (int, float))
        tone = "bad" if (ratio or 0) < 0.2 else "warn" if (ratio or 0) < 0.5 else "good"
        rows.append([f'<code>{e(a.get("app"))}</code>',
                     f'<span class="num">{milhar(a.get("code")) or "—"}</span>',
                     f'<span class="num">{milhar(a.get("tests")) or "—"}</span>',
                     f'<span class="num tag {tone}">{num(ratio) if ratio is not None else "—"}</span>'])
    return table([module_label(snap), "Linhas", "Linhas de teste", "Razão teste/código"], rows)


def build_db(snap):
    db = _seguro(snap.get("db"), dict, {})
    blocks = []

    tables = db.get("tables")
    if isinstance(tables, list):
        rows = [[f'<code>{e(t.get("table"))}</code>',
                 human_bytes(t.get("total_bytes")),
                 human_bytes(t.get("index_bytes")),
                 f'<span class="num">{milhar(t.get("live_rows")) or "0"}</span>',
                 f'<span class="num dim">{milhar(t.get("seq_scan")) or "0"}</span>']
                for t in tables[:15] if isinstance(t, dict)]
        blocks.append("<h3>Maiores tabelas</h3>" + table(
            ["Tabela", "Total", "Índices", "Linhas", "Seq scans"], rows))

    unused = db.get("unused_indexes")
    if isinstance(unused, list):
        rows = [[f'<code>{e(u.get("index"))}</code>', f'<code class="dim">{e(u.get("table"))}</code>',
                 human_bytes(u.get("bytes")), f'<span class="num">{num(u.get("idx_scan"))}</span>']
                for u in unused if isinstance(u, dict)]
        blocks.append("<h3>Índices ociosos</h3>"
                      '<p class="note">Índice pouco lido continua sendo escrito em todo INSERT e UPDATE.</p>'
                      + table(["Índice", "Tabela", "Tamanho", "Leituras"], rows,
                              "nenhum índice ocioso relevante"))

    slow = db.get("slow_queries")
    if isinstance(slow, list) and slow:
        rows = [[f'<code class="sql">{e(q.get("query"))}</code>',
                 f'<span class="num">{milhar(q.get("calls")) or "0"}</span>',
                 f'<span class="num">{num(q.get("mean_ms"), " ms")}</span>',
                 f'<span class="num">{num(q.get("total_s"), " s")}</span>']
                for q in slow if isinstance(q, dict)]
        blocks.append("<h3>Queries por tempo total</h3>" + table(
            ["Query", "Chamadas", "Média", "Total"], rows))
    elif isinstance(slow, dict):
        blocks.append('<h3>Queries por tempo total</h3><p class="empty">'
                      'pg_stat_statements não está habilitado neste banco</p>')

    return "".join(blocks) or '<p class="empty">banco não coletado</p>'


def build_infra(snap):
    conts = _seguro(dig(snap, "infra", "containers"), list) or []
    rows = []
    for c in conts:
        if not isinstance(c, dict):
            continue
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
    for r in _seguro(dig(snap, "ci", "recent"), list) or []:
        if not isinstance(r, dict):
            continue
        conc = r.get("conclusion") or "em execução"
        tone = "good" if conc == "success" else "bad" if conc == "failure" else ""
        dur_s = _seguro(r.get("duration_s"), (int, float))
        dur = f'{dur_s / 60:.1f} min' if dur_s else "—"
        rows.append([f'<span class="tag {tone}">{e(conc)}</span>',
                     e(r.get("workflow")), f'<code class="dim">{e(r.get("branch"))}</code>',
                     e(r.get("title")), f'<span class="num">{dur}</span>'])
    return table(["Resultado", "Workflow", "Branch", "Commit", "Duração"], rows,
                 "sem dados do GitHub Actions")


def build_quality(snap):
    blocks = []
    worst = _seguro(dig(snap, "quality", "complexity", "worst"), list) or []
    worst = [b for b in worst if isinstance(b, dict)]
    if worst:
        rows = [[f'<code>{e(b.get("file"))}</code>', f'<code class="dim">{e(b.get("name"))}</code>',
                 f'<span class="num tag '
                 f'{"bad" if _seguro(b.get("complexity"), (int, float), 0) > 15 else "warn"}">'
                 f'{num(b.get("complexity"))}</span>',
                 f'<span class="num dim">linha {num(b.get("line"))}</span>']
                for b in worst[:12]]
        blocks.append("<h3>Funções mais complexas</h3>" + table(
            ["Arquivo", "Função", "Complexidade", ""], rows))

    rules = _seguro(dig(snap, "quality", "ruff", "by_rule"), list) or []
    rules = [r for r in rules if isinstance(r, dict)
             and isinstance(_seguro(r.get("count"), (int, float)), (int, float))]
    if rules:
        maior = max(_seguro(r.get("count"), (int, float), 0) for r in rules) or 1
        rows = [[f'<code>{e(r.get("rule"))}</code>',
                 bar(100 * r.get("count") / maior),
                 f'<span class="num">{num(r.get("count"))}</span>'] for r in rules]
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

/* veredito: notas por eixo + plano de acao */
.eixos{display:grid;grid-template-columns:repeat(auto-fit,minmax(232px,1fr));gap:12px;margin-top:18px}
.eixo{display:flex;gap:13px;background:var(--card);border:1px solid var(--rule);padding:15px 16px}
.eixo .letra{font-family:var(--mono);font-size:30px;font-weight:700;line-height:1;
             letter-spacing:-.04em;min-width:36px}
.nota-A .letra,.nota-B .letra{color:var(--good)}
.nota-C .letra{color:var(--warn)}
.nota-D .letra,.nota-F .letra{color:var(--bad)}
.nota-NA .letra{color:var(--soft)}
.eixo h3{margin:0 0 3px;font-size:12.5px}
.eixo-resumo{font-size:11.5px;color:var(--soft);font-family:var(--mono);margin:0 0 8px;
             line-height:1.5}
.eixo-na{font-size:11px;color:var(--soft);font-family:var(--mono);margin:0 0 8px;
         font-style:italic}
.eixo-base{font-size:10.5px;color:var(--soft);font-family:var(--mono);margin:0 0 8px;
           letter-spacing:.04em;opacity:.85}
.checklist{list-style:none;padding:0;margin:0;font-size:11.5px;line-height:1.75}
.checklist li{color:var(--soft);padding-left:17px;position:relative}
.checklist li::before{position:absolute;left:0;font-family:var(--mono)}
.checklist .sim::before{content:"✓";color:var(--good)}
.checklist .nao::before{content:"✗";color:var(--bad)}
.checklist .na::before{content:"–"}
.checklist .nao{color:var(--ink)}
.plano{width:100%;border-collapse:collapse;margin-top:20px;font-size:13px}
.plano th{text-align:left;font-family:var(--mono);font-size:10px;letter-spacing:.1em;
          text-transform:uppercase;color:var(--soft);padding:0 10px 8px 0;font-weight:600}
.plano td{padding:11px 10px 11px 0;border-top:1px solid var(--rule);vertical-align:top}
.plano .acao{color:var(--soft);font-size:12px;line-height:1.55}
.prio{font-family:var(--mono);font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;
      letter-spacing:.06em;white-space:nowrap}
.prio.P0{background:rgba(220,38,38,.14);color:var(--bad)}
.prio.P1{background:rgba(217,119,6,.14);color:var(--warn)}
.prio.P2{background:rgba(100,116,139,.14);color:var(--soft)}

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
    except (ValueError, TypeError):
        # ValueError = string mas formato invalido. TypeError = nem string
        # (None, numero, lista...) — `fromisoformat` so aceita str, e
        # `generated_at: null` no JSON (coletor que falhou) e exatamente
        # isso. Os dois casos caem pro mesmo fallback: mostra o valor cru
        # (via `e()` mais na frente, que aceita qualquer tipo).
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
        meta.append(f'<span>repo com <b>{num(age)}</b> dias</span>')
    stack = _seguro(dig(snap, "stack", "detected"), list) or []
    if stack:
        meta.append(f'<span>stack <b>{e(" · ".join(str(s) for s in stack[:4]))}</b></span>')

    body = [
        '<div class="wrap">',
        '<header>',
        '<p class="eyebrow"><b>Ruch-X</b> · raio-x do sistema</p>',
        f'<h1>{e(snap.get("project"))}</h1>',
        f'<div class="meta">{"".join(meta)}</div>',
        '</header>',
        build_signals(snap, snaps),
        section("Veredito da auditoria",
                "Cinco eixos, cada um com os critérios que a engenharia atual considera padrão. "
                "A nota é a fração dos critérios atendidos; a tabela abaixo é o plano de ação, "
                "da prioridade mais alta para a mais baixa.",
                build_veredito(snap, snaps), ident="veredito"),
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
        f'coletores: {e(", ".join(str(c) for c in (_seguro(snap.get("collectors_run"), list) or [])))}</footer>',
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
    if not args.out:
        # git versiona symlink de diretorio OU de arquivo: um `.ruch-x ->
        # ../alvo` (pasta) faria o dashboard ser escrito fora do repositorio;
        # um `.ruch-x/dashboard.html -> ../../alvo` (SO o arquivo final e o
        # link) e mais sutil — a pasta e real, so o destino da escrita e que
        # segue o link, `write_text()` segue transparente e sobrescreve o
        # que estiver do outro lado, exit 0, silencioso. Os dois casos: a
        # pasta e symlink, OU o arquivo final e symlink, OU o caminho
        # resolvido cai fora do diretorio de snapshots. `--out` explicito
        # continua sendo o jeito de escolher outro destino de proposito —
        # nenhuma dessas checagens roda quando o Wilkerson pediu por fora.
        dirpath_p = Path(dirpath)
        if dirpath_p.is_symlink() or out.is_symlink() or not _dentro_de(out, dirpath_p):
            raise SystemExit(f"{out} escaparia de {dirpath}/ (symlink no caminho) — "
                             f"recusando escrever fora do repositorio. Use --out "
                             f"para escolher o destino.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snaps), encoding="utf-8")
    print(str(out.resolve()))

    if args.open_browser:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()

# Estendendo a skill

Versão em português de `docs/extending.md` — as duas dizem a mesma coisa; ao
mudar uma, mude a outra.

## O contrato que todo coletor honra

Um coletor é uma função `collect_x(root, cfg) -> dict` em `scripts/collect.py`.
Cinco regras mantêm o conjunto confiável.

### 1. `None` é "não medido"; `0` e `[]` são "medi e está vazio"

É a regra em que o relatório inteiro se apoia. Campo que não pôde ser medido vai
a `None`, com o motivo registrado:

```python
rc, so, se = run(["minha-ferramenta", "--json"], cwd=root)
if rc != 0:
    nao_medido(out, "campo", _motivo(rc, se))   # out["campo"] = None
else:
    out["campo"] = parse(so)
```

`nao_medido(out, campo, motivo)` põe `out[campo] = None` e grava a razão em
`out["nao_medido"][campo]` (truncada em 200 caracteres). `_motivo(rc, se)`
transforma código de retorno e stderr num motivo curto: `comando nao
encontrado`, `timeout`, ou a última linha do stderr.

Comando que falhou nunca vira zero. Um `[]` vindo de varredura de segredo se lê
como "o repositório está limpo" — a afirmação mais forte do relatório — e uma
ferramenta que afirma isso porque o `git ls-files` morreu é pior que ferramenta
nenhuma. O painel já sabe o que fazer com `None`: marca o critério como *não
auditado*, **mostra o motivo** e o tira do denominador da nota.

Não use o código de retorno sozinho pra decidir se o comando mediu alguma coisa.
`manage.py check --deploy` sai 1 tanto quando achou problema quanto quando
quebrou; `ruff check` e `npm outdated` saem 1 justamente quando a medição deu
certo e encontrou algo. O sinal é a saída ter a **forma** esperada (JSON que
parseia, resumo de check), não o `rc`.

O mesmo vale pro que o painel exibe: valor não medido nunca é interpolado cru
num rótulo ou num resumo — vira travessão (`_valor()` no `render.py`). Linha de
resumo de eixo não afirma número que ninguém apurou.

### 2. Nunca escreva segredo no snapshot

Snapshot é versionado. A redação roda sobre a estrutura inteira antes da
gravação (`redigir_estrutura`, dentro de `gravar_snapshot`) e mascara senha de
DSN, `PASSWORD '...'`, atribuição `chave=valor`, token de GitHub/OpenAI/AWS/
Slack, header `Bearer` e corpo de chave PEM.

Isso é rede de segurança, não licença. Coletor que tem credencial na mão a
mantém fora do retorno: guarda a forma, não o valor. Dois exemplos já no código
— o host do Docker é reduzido ao esquema (`ssh://***`) e a varredura de segredo
registra arquivo, linha e rótulo, nunca o texto casado.

### 3. Levante exceção quando o coletor inteiro não tem o que fazer

O orquestrador captura e grava em `errors[nome]`, que aparece no dashboard como
achado de nível info. Use isso pra "não há nada a coletar aqui" (sem DSN, sem
binário do `docker`). Use a regra 1 pra "rodei e uma das medições falhou".

### 4. Só leitura, e sempre com timeout

Coletor não escreve no projeto, no banco nem no host de Docker. Use o helper
`run()`: recebe argv em lista (nunca string de shell), impõe timeout e não
levanta — binário ausente volta como `rc = 127`, timeout como `rc = 124`.

Existe uma exceção, e ela é opt-in: com `run_tests = true` o coletor `tests`
roda a suíte do próprio projeto, que escreve o que quer que ela escreva
(`coverage.json` e `.coverage` na raiz, no mínimo). Não crie uma segunda. Se um
coletor novo precisar de arquivo, ele vai em `.ruch-x/`, não na árvore auditada.

Ao invocar um módulo Python, use `python -P -m <modulo>`: sem o `-P`, um arquivo
de mesmo nome na raiz do repositório auditado sombreia o módulo de verdade e
roda como `__main__` na máquina de quem está auditando.

### 5. Registre

Acrescente a função ao `REGISTRY` e o nome ao `COLLECTORS`, pra que `--only` e
`--skip` consigam endereçá-la. `render.COLETORES_ESPERADOS` espelha essa lista
(é o que permite a tela dizer "este coletor nem foi tentado") e um guard da
suíte trava as duas contra divergência.

### Exemplo

```python
def collect_filas(root, cfg):
    import redis
    url = env_or(cfg.get("filas", {}), "url", "RUCHX_REDIS_URL")
    if not url:
        raise RuntimeError("defina RUCHX_REDIS_URL")        # regra 3
    out = {"filas": None}
    try:
        r = redis.from_url(url, socket_timeout=5)
        out["filas"] = [{"nome": n.decode(), "pendentes": r.llen(n)}
                        for n in r.keys(b"celery*")]
    except Exception as exc:                                 # regra 1
        nao_medido(out, "filas", str(exc))
    return out
```

## Adicionar uma seção no dashboard

Em `scripts/render.py`:

1. Escreva `build_x(snap)` devolvendo HTML. Use os helpers `table()`, `bar()`,
   `stat()` e `human_bytes()` em vez de montar markup do zero — eles já tratam
   estado vazio e formatação numérica em português.
2. Inclua na lista `body` dentro de `render()`, via `section(titulo, nota, corpo)`.
3. Se a métrica merece acompanhamento no tempo, acrescente um `stat()` em
   `build_signals()` com `sparkline(series(snaps, lambda s: ...))`.
4. Se ela merece virar recomendação, adicione a regra em `findings()` com a
   severidade adequada. Regra boa diz o que fazer, não só que está ruim.

### Trate o snapshot como entrada não-confiável

Snapshot é arquivo versionado que pode ter sido escrito por outra pessoa, por
uma versão antiga da ferramenta ou na mão. O render nunca supõe o tipo de um
campo:

- texto livre passa por `e()` (`html.escape`);
- o que é interpolado como número passa por `num()` ou `milhar()`, que escapam
  o valor quando ele não é número de verdade;
- `_seguro(v, tipos, default)` corrige o campo de tipo errado ANTES de qualquer
  conta, indexação ou `.get()`;
- `dig(obj, *chaves)` percorre dicionários aninhados sem estourar num nível
  que não existe;
- `_valor(v, sufixo)` é o que impede um valor não medido de virar a string
  `None` na tela.

Interpolar um `{campo}` cru dentro de markup — mesmo um que você tem certeza que
é inteiro — é a classe de bug que esses helpers existem pra evitar.

## Adicionar formato de cobertura ou linter

`parse_coverage()` pega o primeiro candidato que existe e parseia com sucesso;
acrescente uma entrada `(caminho, tipo)` em `COVERAGE_FILES` e um ramo pro novo
tipo. Devolva `{"pct": float, "source": str}`, opcionalmente com `by_app`.

`collect_quality()` é o lugar de um linter novo. Siga a forma do `ruff`/`eslint`:
`{"total": int, "by_rule": [...], "worst_files": [...]}`, mais a chave `tool`
quando não for ruff, pra o dashboard conseguir rotular.

## Estados vazios

Toda seção precisa dizer o que fazer quando não há dado. `table()` recebe esse
texto como terceiro argumento. "sem dados" não ajuda ninguém; "sem coverage.json
— rode os testes com --cov-report=json" ajuda.

## Mudança de schema

O snapshot carrega `schema: 2`. Ao mudar a estrutura de forma incompatível,
incremente o número e trate a versão antiga no `render.py`. Snapshots velhos são
o histórico — apagá-los para simplificar o código destrói a única coisa que a
skill acumula com o tempo.

## Testes

```bash
python3 -m unittest discover -s scripts/tests -t scripts/tests
```

Só biblioteca padrão, nenhuma dependência do projeto (e nunca `pytest` aqui —
a suíte da própria skill roda em `unittest`). A divisão por arquivo e a
contagem estão em `docs/extending.md`, e são conferidas pela própria suíte
(`test_docs.py`) contra o código.

Todo commit leva teste junto, no arquivo da classe correspondente. Padrão novo
de redação sem um teste afirmando que o segredo sumiu é um padrão em que
ninguém pode confiar.

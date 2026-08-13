# Estendendo a skill

## Adicionar um coletor

Um coletor é uma função `collect_x(root, cfg) -> dict` em `scripts/collect.py`.
Quatro regras mantêm o conjunto confiável:

1. **Levante exceção quando falhar.** O orquestrador captura e grava em
   `errors[nome]`. Não devolva dicionário vazio em silêncio — o usuário precisa
   distinguir "medi e deu zero" de "não consegui medir".
2. **Nunca escreva nada.** Coleta é leitura, no banco, no Docker e no repositório.
3. **Respeite timeout.** Use o helper `run()`, que já limita tempo e não levanta
   por comando ausente.
4. Registre em `REGISTRY` e acrescente o nome em `COLLECTORS`.

Exemplo — profundidade das filas do Celery via Redis:

```python
def collect_filas(root, cfg):
    import redis
    url = env_or(cfg.get("filas", {}), "url", "METRICAS_REDIS_URL")
    if not url:
        raise RuntimeError("defina METRICAS_REDIS_URL")
    r = redis.from_url(url, socket_timeout=5)
    return {"filas": [{"nome": n.decode(), "pendentes": r.llen(n)}
                      for n in r.keys(b"celery*")]}
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

## Estados vazios

Toda seção precisa dizer o que fazer quando não há dado. `table()` recebe esse
texto como terceiro argumento. "sem dados" não ajuda ninguém; "sem coverage.json
— rode os testes com --cov-report=json" ajuda.

## Mudança de schema

O snapshot carrega `schema: 1`. Ao mudar a estrutura de forma incompatível,
incremente o número e trate a versão antiga no `render.py`. Snapshots velhos são
o histórico — apagá-los para simplificar o código destrói a única coisa que a
skill acumula com o tempo.

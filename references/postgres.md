# Banco de dados: o que cada número significa

Todas as consultas são somente-leitura sobre catálogo e estatísticas do
Postgres. Nenhuma lê dado de negócio.

## Cache hit ratio

`heap_blks_hit / (heap_blks_hit + heap_blks_read)` do `pg_statio_user_tables`.

Mede quanto das leituras foi servido da memória. Acima de 99% é saudável, entre
95 e 99% é aceitável, abaixo de 95% quer dizer que o banco está indo ao disco
com frequência.

Causa mais comum: `shared_buffers` pequeno demais para o working set. O padrão
do Postgres (128 MB) é conservador; em VPS dedicado, 25% da RAM é o ponto de
partida usual. Antes de mexer, confirme que não é um relatório pesado rodando
uma vez por dia e distorcendo a estatística acumulada.

Importante: a estatística é cumulativa desde o último `pg_stat_reset()`. Um
número ruim pode ser resquício de uma carga antiga.

## Linhas mortas e vacuum

`n_dead_tup` são versões antigas de linha que o MVCC deixou para trás após
UPDATE ou DELETE. O autovacuum recolhe, mas por padrão só dispara quando a
tabela acumula 20% de mortas — em tabela muito grande isso é muito espaço e
muito tempo de scan desperdiçado.

Quando uma tabela aparece na lista de suspeitas:

1. Confirme se o autovacuum está passando:
   `SELECT relname, last_autovacuum, autovacuum_count FROM pg_stat_user_tables WHERE relname = 'x';`
2. Se nunca passou, geralmente há transação antiga segurando o horizonte:
   `SELECT pid, state, xact_start FROM pg_stat_activity ORDER BY xact_start;`
3. Para tabelas grandes com escrita pesada, baixe o gatilho por tabela:
   `ALTER TABLE x SET (autovacuum_vacuum_scale_factor = 0.05);`

`VACUUM FULL` reescreve a tabela inteira e trava escrita — só em janela de
manutenção. Na maioria dos casos o vacuum normal resolve.

## Índices ociosos

Índice com `idx_scan` baixo e tamanho relevante. Índice não é grátis: toda
escrita atualiza todos os índices da tabela, e cada um ocupa cache que poderia
guardar dado quente.

Antes de remover, confirme três coisas: que a estatística cobre um período
representativo (nem toda funcionalidade roda todo dia), que o índice não serve a
uma constraint, e que não é usado só no fechamento mensal. Em réplica de leitura
a contagem é separada — um índice ocioso no primário pode ser muito usado lá.

## Seq scan vs index scan

Tabela grande com muito mais varredura completa que leitura por índice
normalmente significa índice faltando — em Django, tipicamente um `filter()`
sobre campo sem `db_index=True`, ou um `ORDER BY` sem índice composto que o
acompanhe.

Cuidado com o falso positivo: em tabela pequena o planejador escolhe seq scan de
propósito, porque ler tudo é mais barato que ler o índice e depois a tabela. Por
isso o coletor só sinaliza tabelas acima de 5 mil linhas.

Para achar a query responsável, ative `pg_stat_statements` e olhe a seção de
queries por tempo total.

## pg_stat_statements

Não vem ligado por padrão. Para habilitar:

```
# postgresql.conf
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.max = 10000
pg_stat_statements.track = top
```

Exige reinício, depois `CREATE EXTENSION pg_stat_statements;` no banco.

Ordene sempre por **tempo total**, não por tempo médio. Uma query de 3 ms
chamada um milhão de vezes custa muito mais que uma de 2 segundos chamada dez
vezes — e é a primeira que aparece como "o sistema está lento".

## Multi-tenant com schema por tenant

Em django-tenants, `pg_stat_user_tables` mostra as tabelas do `search_path` da
conexão. Para medir um tenant específico, aponte o DSN com o schema:

```
postgresql://leitor:senha@host:5432/banco?options=-csearch_path%3Dtenant_x
```

Para o tamanho total por schema, rode à parte:

```sql
SELECT schemaname, pg_size_pretty(sum(pg_total_relation_size(relid))) AS tamanho
FROM pg_stat_user_tables GROUP BY schemaname ORDER BY sum(pg_total_relation_size(relid)) DESC;
```

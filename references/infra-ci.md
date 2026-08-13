# Infraestrutura e CI

## Docker em host remoto (Coolify, Easypanel, VPS)

Coolify e Easypanel são painéis diferentes, mas ambos orquestram containers
Docker comuns por baixo. Isso é o que permite a mesma coleta servir aos dois: em
vez de depender da API de cada painel — que muda entre versões, exige token
próprio e quebra em atualização — aponte o cliente Docker direto para o host
por SSH:

```bash
export RUCHX_DOCKER_HOST="ssh://root@meu-vps"
# ou no ruch-x.toml: [infra] docker_host = "ssh://root@meu-vps"
```

Requisitos: chave SSH sem senha configurada (`ssh-copy-id`) e cliente Docker
local instalado. Teste antes com `DOCKER_HOST=ssh://root@meu-vps docker ps`.

Isso lê o mesmo que `docker stats` leria no servidor, sem abrir porta nenhuma e
sem acoplar a skill à versão do painel.

Se o servidor hospeda vários projetos, use `project_prefix` no toml para filtrar
só os containers que interessam.

### Diferenças de nomenclatura entre os painéis

O que muda entre Coolify e Easypanel é como cada um nomeia os containers, o que
importa na hora de configurar `project_prefix`:

- **Easypanel** nomeia como `<projeto>_<serviço>` — por exemplo `meuprojeto_web`,
  `meuprojeto_redis`. O prefixo é o nome do projeto no painel.
- **Coolify** usa o UUID do recurso no nome, algo como `web-a1b2c3d4-...`. O
  prefixo estável costuma ser o nome do serviço, não do projeto. Rode
  `docker ps --format '{{.Names}}'` no host uma vez e escolha o pedaço comum.

Se você usa os dois painéis em servidores diferentes, mantenha um
`ruch-x.toml` por projeto com o `docker_host` de cada um. Não existe conflito:
a skill lê um projeto de cada vez.

### Coolify com vários servidores

Coolify gerencia servidores remotos além do próprio host. Cada servidor tem seu
IP, então `docker_host` aponta para onde a aplicação daquele projeto roda de
fato — não para o servidor onde o painel do Coolify está instalado. É um erro
comum e o sintoma é claro: a lista vem cheia de containers do próprio painel e
nenhum da aplicação.

### O que olhar

`docker stats` é uma foto instantânea, não uma média. Um pico de CPU no momento
exato da coleta não significa nada; um container consistentemente acima de 80%
de memória em coletas seguidas significa.

Worker Celery com memória sempre maior a cada coleta costuma ser vazamento —
`worker_max_tasks_per_child` recicla o processo e resolve o sintoma enquanto a
causa não aparece.

## GitHub Actions

A coleta usa o `gh` CLI, que já resolve autenticação:

```bash
gh auth login          # uma vez por máquina
gh run list --limit 5  # confirma que funciona neste repositório
```

Precisa rodar dentro do repositório, com remote apontando para o GitHub.

Se o repositório é privado e o `gh` está autenticado com outra conta, o comando
retorna lista vazia sem erro claro — confirme com `gh auth status`.

### Taxa de sucesso

Considera apenas execuções concluídas (`success` ou `failure`). Canceladas e em
andamento ficam de fora para não distorcer a média.

Abaixo de 85% o problema raramente é o código. Costuma ser teste que depende de
ordem de execução, timeout apertado em serviço externo, ou cache de dependência
mal configurado. Pipeline que falha à toa treina o time a apertar "re-run" sem
ler — e aí a falha real passa batido.

### Duração

Compare a média com o pico. Diferença grande entre os dois normalmente é cache
intermitente: quando o cache de dependências acerta, o build voa; quando erra,
reinstala tudo.

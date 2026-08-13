---
title: Empacotamento em imagem Docker única
type: decision
tags: [docker, deployment, capella, xvfb]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# Empacotamento em imagem Docker única

## Contexto

O Capella não estava instalado no ambiente de desenvolvimento no momento do
planejamento. Instalar Capella + plugin `python4capella` + Xvfb diretamente
no host (ou WSL2) amarraria o projeto a uma máquina específica — ruim para
um projeto de pesquisa que precisa ser reproduzível por outras pessoas
(orientador, colaboradores) e não deve depender de setup manual frágil.

Docker está disponível no ambiente (`docker 29.6.2`), confirmado antes da
decisão.

## Decisão

Empacotar **tudo numa única imagem Docker** (base `debian:bookworm-slim`):
Eclipse Capella **7.0.1** (build `7.0.1.202503211540`, JRE embutido no
produto — não precisa de JDK à parte), plugin `python4capella` **1.4.1**
(instalado via `p2 director` direto no `update.zip` da release, no build da
imagem), `Xvfb`/`xvfb-run`, Python 3.10+ com `mcp[cli]` via `uv`, e o
código do servidor MCP. Ver `Dockerfile` na raiz do projeto.

Versão do Capella pinada em 7.0.1 (não a 7.1.0, mais recente) porque é a
exatamente testada pela release 1.4.1 do `python4capella` (asset
`report-7.0.1.xml` da release confirma), dentro da faixa suportada
declarada no README do plugin (1.4.2–7.x). Debian bookworm escolhido em vez
de Ubuntu 25.04+ porque o pacote `libwebkit2gtk-4.0-*` (exigido pelo
browser embutido do RCP) foi removido em versões mais novas do Ubuntu (ver
issue `eclipse-capella/capella#2946`).

Modelos `.aird` vivem no host e entram via bind mount
(`-v <host_dir>:/workspace/models`); o `model_path` recebido pelas tools é
relativo a esse diretório dentro do container.

Distribuição: o cliente MCP (Claude Code/Desktop) invoca o servidor via

```json
{
  "command": "docker",
  "args": ["run", "-i", "--rm", "-v", "<host>:/workspace/models", "capella-mcp"]
}
```

O servidor fala MCP por stdio de dentro do container. O bridge headless
(ver [[0002-headless-por-chamada]]) roda `xvfb-run -a capellac ...` **no
mesmo container** — sem Docker-in-Docker.

## Consequências

- Imagem grande (JDK + Capella + Xvfb) — build depende de rede para baixar
  o Capella (~1GB+); pode exigir passo manual se o ambiente de build não
  tiver acesso de rede suficiente.
- Ambiente 100% reprodutível: qualquer pessoa com Docker roda o servidor
  sem instalar Capella manualmente.
- Fixa a versão do Capella e do plugin no `Dockerfile`, evitando "funciona
  na minha máquina" por divergência de versão.

## Atualização (2026-08-12)

O pressuposto original — "Capella não estava instalado no ambiente de
desenvolvimento" — deixou de valer: Capella **7.1.0** + `python4capella`
**1.4.1** foram instalados em `/home/flv/projetos_ita/capella` na máquina
de dev. Isso não muda a decisão: Docker continua sendo o único caminho
suportado para deploy/CI e para outros colaboradores rodarem o servidor
sem setup manual.

O install local passou a ser usado como **inner loop** opcional — rodar
`bridge.py` e a suíte de integração (`scripts/run_integration_tests_local.sh`)
sem `docker build` a cada iteração, apontando `CAPELLA_BIN`/`CAPELLA_MODELS_ROOT`
(já env-var driven em `bridge.py`) pro install local em vez do
`/opt/capella` do container. Nota lateral: no Linux não existe um binário
`capellac` separado do `capella` (diferente do Windows, onde
`capellac.exe` é a variante console) — o mesmo executável `./capella`
serve GUI e headless; a menção a `capellac` acima na seção "Distribuição"
reflete a nomenclatura do Windows/documentação genérica do produto, não
o binário real no Linux.

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

Empacotar **tudo numa única imagem Docker**: JDK, Eclipse Capella, plugin
`python4capella` (instalado via update site/p2 director no build),
`Xvfb`/`xvfb-run`, Python 3.10+ com `mcp[cli]`, e o código do servidor MCP.

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

---
title: Capella MCP — Visão Geral
type: project
tags: [mcp, capella, mbse, arcadia, python4capella, agentic-ai]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# Capella MCP

O intuito aqui é desenvolver um MCP (Model Context Protocol) que dará
capacidades de ler e escrever dentro de um modelo Capella, para que a LLM
seja capaz de representar os sistemas descritos a serem desenvolvidos dentro
da notação de system engineering do modelo Capella MBSE.

Vamos utilizar a lib [python4capella](https://github.com/labs4capella/python4capella).
A ideia da lib é que ela faça a parte de interface para mexer com o modelo
dentro do Capella.

## Motivação

Este projeto é a prova de conceito experimental da proposta de pesquisa de
mestrado *"MBSE com Agentic AI"* (PG-EEC-D, ITA — ver
`docs/second_brain/40_Projects/Mestrado_ITA/Proposta_Pesquisa_MBSE_AI.md`):
usar agentes de IA para traduzir especificações em linguagem natural para a
sintaxe formal de modelos MBSE, com loops de feedback iterativos de
co-design humano-IA, mantendo rastreabilidade e integridade do projeto. O
Capella MCP é o mecanismo concreto que permite a uma LLM ler e escrever num
modelo Capella real (Arcadia) em vez de apenas gerar texto ou diagramas
soltos.

## Objetivo

Expor, via MCP, tools e resources que permitam a uma LLM:
- Navegar um modelo Capella existente pelas camadas Arcadia (OA/SA/LA/PA/EPBS).
- Ler elementos e seus atributos.
- Criar e editar elementos, respeitando o modelo de transações do Capella.

## Stack

- **Lib de interface com o Capella**: `python4capella` (roda dentro do
  processo Eclipse/Capella via EASE+Py4J — ver
  [[0001-python4capella-nao-e-lib-externa]]).
- **SDK MCP**: `mcp[cli]` (`MCPServer`, `mcp.server.mcpserver`), Python ≥3.10.
- **Empacotamento**: imagem Docker única (Capella + plugin + Xvfb + servidor
  MCP) — ver [[0003-empacotamento-docker]].
- **Gerenciador de dependências**: `uv`.

## Documentação

- [[architecture]] — mapa geral do fluxo e das decisões.
- Decisões: [[0001-python4capella-nao-e-lib-externa]],
  [[0002-headless-por-chamada]], [[0003-empacotamento-docker]],
  [[0004-escopo-v1-leitura-e-escrita]].
- Conceitos: [[arcadia-layers]], [[python4capella-api]].
- Segundo cérebro: `docs/second_brain/` (submodule) — ver notas
  `Capella MBSE`, `MBSE com Agentic AI`, `Proposta_Pesquisa_MBSE_AI` para o
  contexto acadêmico completo.

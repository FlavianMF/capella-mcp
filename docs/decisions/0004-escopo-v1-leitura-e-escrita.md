---
title: Escopo v1 inclui leitura e escrita
type: decision
tags: [scope, mcp, roadmap]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# Escopo v1 inclui leitura e escrita

## Contexto

A alternativa mais conservadora seria entregar a v1 só com navegação
read-only do modelo (resources), deixando criação/edição de elementos
(tools de escrita) para uma v2, depois de validar a leitura.

Mas o objetivo de pesquisa por trás deste projeto (ver
`docs/second_brain/40_Projects/Mestrado_ITA/Proposta_Pesquisa_MBSE_AI.md`)
é especificamente demonstrar um **mecanismo de tradução de linguagem
natural para sintaxe formal de modelo**, com **loops de feedback
iterativos de co-design humano-IA** — isso exige a LLM conseguir criar e
editar elementos no modelo, não só lê-lo.

## Decisão

A v1 já inclui tools de escrita (`create_element`, `update_element`) além
das de leitura (`list_layers`, `list_elements`, `get_element`). Escolha
explícita do usuário.

## Consequências

- Maior superfície de risco desde o início: escrita usa
  `start_transaction()`/`commit_transaction()`/`rollback_transaction()` do
  `python4capella` (ver [[python4capella-api]]), com `save()` ao final —
  precisa de validação de input rigorosa e rollback confiável em erro, já
  na v1.
- Erros de validação/execução seguem a convenção MCP de `isError: true`
  no resultado da tool, com mensagem acionável, para a LLM poder se
  autocorrigir (ex.: tipo de elemento inválido para a camada, atributo
  desconhecido).
- Sem "modo seguro" read-only para testes iniciais — os primeiros testes
  de integração (ver plano, Fase 6) já precisam cobrir escrita.

---
title: Arquitetura headless por chamada
type: decision
tags: [architecture, python4capella, capella, mcp]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# Arquitetura headless por chamada

## Contexto

Dado que `python4capella` só roda dentro do processo Eclipse/Capella (ver
[[0001-python4capella-nao-e-lib-externa]]), o servidor MCP precisa de uma
estratégia para acionar o Capella a partir de fora. Três opções foram
consideradas:

1. **Headless por chamada** — cada tool call do MCP sobe um processo
   Capella headless do zero, roda um script, lê o resultado, encerra.
2. **Processo persistente + bridge custom** — sobe o Capella headless uma
   vez, com um script EASE expondo um listener (socket/HTTP) dentro do
   processo; o servidor MCP vira client desse listener. Mais rápido e
   stateful, mas exige construir e manter um bridge que não existe pronto.
3. **Abandonar python4capella** — parsear `.aird`/`.capella` direto (já
   descartado em [[0001-python4capella-nao-e-lib-externa]]).

## Decisão

Opção 1 — **headless por chamada**. Escolha do usuário, priorizando
simplicidade de implementação e desacoplamento sobre performance: cada
tool call é um subprocess independente (`xvfb-run -a capellac ...`), sem
infraestrutura de bridge para manter.

## Consequências

- Cada tool call de escrita é um ciclo completo: abrir modelo →
  `start_transaction()` → modificar → `commit_transaction()` (ou
  `rollback_transaction()` em erro) → `save()` → encerrar processo. Não há
  estado (handle de sessão) reaproveitado entre chamadas.
- Latência por chamada é alta (startup do Capella: segundos a ~1 min) —
  aceito como tradeoff.
- Timeout do subprocess precisa ser generoso e configurável.
- Se a latência se provar inaceitável na prática, a Opção 2 (bridge
  persistente) fica como evolução futura, sem precisar mudar a API MCP
  exposta ao cliente — só a implementação de `bridge.py`.

---
title: Capella MCP — Arquitetura
type: moc
tags: [mcp, capella, docker, architecture]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# Capella MCP — Arquitetura

Mapa de conteúdo das decisões e conceitos que definem a arquitetura deste
projeto. Ver [[index]] para a visão geral.

## Fluxo

```
Cliente MCP (Claude Code/Desktop)
    │  stdio (docker run -i --rm)
    ▼
Container Docker (imagem única: JDK + Capella + python4capella + Xvfb + servidor MCP)
    │
    │  list_layers / list_elements / get_element:
    ├──────────────────────────────┐
    ▼ (tenta primeiro)             │ (fallback automático, ver 0005)
fast_reader.py                     ▼
capellambse.MelodyModel      xvfb-run -a capellac -application org.polarsys.capella.core.commandline.core
(sem JVM, ~ms)                        -appid org.eclipse.python4capella.commandline
    │                           script gerado a partir de template fixo, usa simplified_api/capella.py
    │  demais tools (escrita, diagramas): sempre headless ──┘
    ▼
Modelo Capella (.aird) — bind mount do host em /workspace/models
```

Toda tool de escrita/diagrama é um ciclo completo e isolado: sobe o
Capella headless → abre o modelo → lê/modifica (com transaction) → salva
→ encerra o processo. Não há estado persistente entre chamadas (ver
[[0002-headless-por-chamada]]). As 3 tools de leitura pura passam primeiro
pelo caminho rápido `capellambse` (mesmo processo do servidor MCP, sem
subir Capella) e só caem no caminho acima se o `capellambse` não cobrir o
caso (ver [[0005-camada-leitura-capellambse]]). Um `bridge.model_lock()`
por `model_path` evita que uma leitura rápida aconteça no meio de um
`save()` headless.

## Decisões

- [[0001-python4capella-nao-e-lib-externa]] — por que a integração não é um
  simples `pip install`.
- [[0002-headless-por-chamada]] — por que cada tool call sobe um processo
  Capella do zero, em vez de um bridge persistente.
- [[0003-empacotamento-docker]] — por que tudo (Capella incluso) vai numa
  única imagem Docker.
- [[0004-escopo-v1-leitura-e-escrita]] — por que a v1 já inclui escrita, não
  só leitura.
- [[0005-camada-leitura-capellambse]] — por que as 3 tools de leitura pura
  ganharam um caminho rápido via `capellambse`, e por que ele mora dentro
  do próprio servidor MCP em vez de um bridge no lado do cliente.

## Conceitos

- [[arcadia-layers]] — camadas Arcadia (OA/SA/LA/PA/EPBS) e como mapeiam
  para os resources MCP.
- [[python4capella-api]] — API simplificada usada dentro dos scripts do
  bridge.

## Superfície MCP (resumo)

Resources (leitura, navegação):
- `capella://{model_path}/layers`
- `capella://{model_path}/layer/{layer}`
- `capella://{model_path}/element/{element_id}`

Tools (ações, leitura + escrita):
- `list_layers(model_path)` — fast-path `capellambse` (ver [[0005-camada-leitura-capellambse]])
- `list_elements(model_path, layer, type_filter=None)` — fast-path só com `type_filter`
- `get_element(model_path, element_id)` — fast-path `capellambse`
- `create_element(model_path, layer, type, name, parent_id, attributes={})`
- `update_element(model_path, element_id, attributes)`
- `create_diagram(model_path, layer, type_name, root_id, include_relations, diagram_name, max_depth)`
  — diagramas "breakdown" (árvore, Sirius `NodeMapping`); inclui OABD (9
  combos originais) e Mode State Machine (`("la","Region")`, PNG renderiza
  corretamente headless — confirmado ao vivo, ver `BREAKDOWN_DIAGRAMS`)
- `create_container_diagram(model_path, layer, type_name, diagram_name, max_depth)`
  — diagramas "Blank" (Sirius `ContainerMapping`): Operational Entity Blank
  (OAB) e Operational Activity Interaction Blank (OAIB, com edges de
  functional exchange); PNG renderiza corretamente headless — bug de PNG
  em branco raiz-causado (debugger ao vivo na JVM headless) e corrigido via
  `DiagramServices.createContainer()`, ver comentário de
  `CONTAINER_DIAGRAMS` em `bridge.py`
- `create_class_diagram(model_path, layer, diagram_name, max_depth)`
  — Class Diagram Blank (CDB), árvore heterogênea DataPkg+Class; mesmo fix
  de `create_container_diagram`, PNG renderiza corretamente
- `create_capability_diagram(model_path, diagram_name)`
  — Operational Capabilities Blank (OCB), só camada OA; entidades como
  container + capacidades envolvidas aninhadas; mesmo fix, PNG renderiza
  corretamente
- `create_scenario_diagram(model_path, scenario_id, scenario_kind="OES", diagram_name=None)`
  — diagramas de sequência/cenário (Sirius `SequenceDiagramDescription`):
  OES "Operational Interaction Scenario" e OAS "Activity Interaction
  Scenario", só camada OA; requer um `Scenario` existente (com
  `InstanceRole`s e `SequenceMessage`s já criados via `create_element`);
  PNG renderiza corretamente headless — fix diferente de todo o resto
  (chain de reparo de ordering do próprio Sirius, ver comentário de
  `_SCENARIO_DIAGRAM_MAPPINGS`/`create_scenario_diagram` em `bridge.py`)
- `delete_diagram(model_path, diagram_uid)`
- `export_diagram(model_path, image_format="PNG")`

Resources adicionais (diagramas):
- `capella://{model_path}/diagrams`
- `capella://{model_path}/diagram/{diagram_uid}`

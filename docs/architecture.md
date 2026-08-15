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
    │  cada tool call:
    ▼
xvfb-run -a capellac -application org.polarsys.capella.core.commandline.core
         -appid org.eclipse.python4capella.commandline
    │  script gerado a partir de template fixo, usa simplified_api/capella.py
    ▼
Modelo Capella (.aird) — bind mount do host em /workspace/models
```

Cada tool call é um ciclo completo e isolado: sobe o Capella headless →
abre o modelo → lê/modifica (com transaction) → salva → encerra o
processo. Não há estado persistente entre chamadas (ver
[[0002-headless-por-chamada]]).

## Decisões

- [[0001-python4capella-nao-e-lib-externa]] — por que a integração não é um
  simples `pip install`.
- [[0002-headless-por-chamada]] — por que cada tool call sobe um processo
  Capella do zero, em vez de um bridge persistente.
- [[0003-empacotamento-docker]] — por que tudo (Capella incluso) vai numa
  única imagem Docker.
- [[0004-escopo-v1-leitura-e-escrita]] — por que a v1 já inclui escrita, não
  só leitura.

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
- `list_layers(model_path)`
- `list_elements(model_path, layer, type_filter=None)`
- `get_element(model_path, element_id)`
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
- `delete_diagram(model_path, diagram_uid)`
- `export_diagram(model_path, image_format="PNG")`

Resources adicionais (diagramas):
- `capella://{model_path}/diagrams`
- `capella://{model_path}/diagram/{diagram_uid}`

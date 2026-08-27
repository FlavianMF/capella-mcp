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

Esse é sempre o caminho usado em Docker/CI. Em dev local, se o usuário
tem o Capella GUI aberto no mesmo modelo (com `attach_listener.py`
registrado -- ver [[0006-attach-mode-gui-aberta]]), `list_layers`/
`list_elements`/`get_element`/`create_element`/`update_element` detectam
isso sozinhos e conversam com essa sessão já aberta em vez de subir um
Capella novo -- a GUI reflete a escrita do MCP na hora, e o MCP enxerga o
que o usuário editou na tela mesmo antes de ele salvar. Sem GUI aberta
(ou fora dessas 5 tools), nada muda.

## Decisões

- [[0001-python4capella-nao-e-lib-externa]] — por que a integração não é um
  simples `pip install`.
- [[0002-headless-por-chamada]] — por que cada tool call sobe um processo
  Capella do zero, em vez de um bridge persistente.
- [[0003-empacotamento-docker]] — por que tudo (Capella incluso) vai numa
  única imagem Docker.
- [[0004-escopo-v1-leitura-e-escrita]] — por que a v1 já inclui escrita, não
  só leitura.
- [[0006-attach-mode-gui-aberta]] — por que (e como) o MCP passa a falar
  direto com um Capella GUI já aberto do usuário, em vez de só spawnar
  headless, em dev local.

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

## Attach mode -- registrar `attach_listener.py` na sua GUI local

Opcional, só faz sentido rodando o Capella GUI localmente (não em
Docker/CI). Ver [[0006-attach-mode-gui-aberta]] pro design completo.

1. Abra o Capella GUI normalmente, no workspace onde você mantém seus
   modelos de trabalho.
2. `Window > Preferences > Scripting > Script Locations > Add...` e
   aponte para a pasta `src/capella_mcp/` deste repo (onde está
   `attach_listener.py`). Engine: "Python (Py4J)".
3. Feche e reabra o Capella (pra `# onStartup` ter chance de disparar) --
   se depois de ~10s não aparecer `~/.capella-mcp/attach/` criado, o
   autostart não pegou nesta instalação; rode manualmente uma vez:
   clique com o botão direito num projeto no Project Explorer > "Capella
   MCP -- Start Attach Listener" (entrada de menu que o próprio script
   registra). Depois desse primeiro start manual, ele fica rodando pelo
   resto da sessão da GUI sem precisar repetir.
4. Abra o `.aird` que você quer editar via MCP (duplo-clique, do jeito
   normal).
5. Chame qualquer tool MCP (`get_element`, `create_element`, etc.) com o
   mesmo `model_path` -- o bridge detecta a GUI sozinho (não precisa de
   env var). Pra confirmar que pegou o attach e não spawnou um Capella
   novo: a chamada volta bem mais rápido que o normal (segundos, não o
   ~1min de subir um Capella do zero).

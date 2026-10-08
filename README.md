# capella-mcp

Servidor MCP que dá a uma LLM capacidade de ler e escrever modelos Capella
(Arcadia/MBSE), usando `python4capella` como camada de interface com o
Capella. Ver `docs/index.md` para a visão geral e `docs/architecture.md`
para a arquitetura completa.

## Tools

- Leitura: `list_layers`, `list_elements`, `get_element` (com `relations`
  limitadas quando servido pelo fast-path `capellambse`), `list_diagrams`,
  `get_diagram`
- Consulta de modelo (só leitura, resultados limitados por `max_results`
  50/200 e `max_depth` 2/4): `find_references`, `trace_element`,
  `list_exchanges`, `impact_analysis`
- Escrita: `create_element`, `update_element`
- Diagramas: `create_diagram`, `create_container_diagram`,
  `create_class_diagram`, `create_capability_diagram`,
  `create_scenario_diagram`, `add_to_diagram`, `remove_from_diagram`,
  `delete_diagram`, `layout_diagram`, `export_diagram`

Detalhes de cada tool em `docs/architecture.md` ("Superfície MCP").

## Desenvolvimento

```bash
uv sync
uv run pytest tests/ -k "not integration"
```

## Build da imagem

```bash
docker build -t capella-mcp .
```

## Uso (cliente MCP)

```json
{
  "mcpServers": {
    "capella": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "<host_models_dir>:/workspace/models", "capella-mcp"]
    }
  }
}
```

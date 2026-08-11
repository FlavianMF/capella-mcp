# capella-mcp

Servidor MCP que dá a uma LLM capacidade de ler e escrever modelos Capella
(Arcadia/MBSE), usando `python4capella` como camada de interface com o
Capella. Ver `docs/index.md` para a visão geral e `docs/architecture.md`
para a arquitetura completa.

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

# capella-mcp

Servidor MCP (`mcp[cli]`, classe `MCPServer` de `mcp.server.mcpserver`) que dá a uma LLM acesso de leitura e
escrita a modelos Capella (Arcadia/MBSE), via `python4capella`.

**Antes de mexer na arquitetura, leia `docs/architecture.md` e as decisions
em `docs/decisions/`** — elas explicam por que o design é do jeito que é
(em especial `docs/decisions/0001-python4capella-nao-e-lib-externa.md`:
`python4capella` não é uma lib Python importável, é um addon que roda
dentro do processo Eclipse/Capella).

## Pontos-chave

- **Headless por chamada**: cada tool call sobe um processo Capella
  headless (`xvfb-run -a capellac ...`) do zero, roda um script gerado a
  partir de um template fixo, lê o resultado (JSON em arquivo, não stdout)
  e encerra. Sem estado entre chamadas.
- **Tudo roda em Docker**: uma única imagem com JDK + Capella + plugin
  `python4capella` + Xvfb + o servidor MCP. Sem Docker-in-Docker — o
  subprocess do bridge roda no mesmo container do servidor.
- **Não gerar/injetar código Python arbitrário** para o Capella executar —
  só os templates fixos em `bridge.py`, por segurança e determinismo.
- **Segundo cérebro conectado** em `docs/second_brain/` (submodule, branch
  `master`). Ver `docs/second_brain/00_META/manifests/INDEX.md` antes de
  aprofundar em MBSE/Capella/Arcadia — já existe contexto acadêmico lá
  (a proposta de mestrado que motiva este projeto).

## Comandos

```bash
uv sync
uv run pytest tests/ -k "not integration"
uv run mcp dev src/capella_mcp/server.py   # MCP Inspector
docker build -t capella-mcp .
```

## Layout

```
src/capella_mcp/
  server.py      # entry point, registra tools/resources (MCPServer)
  bridge.py       # invoca o Capella headless, templates de script, parsing de resultado
  tools/          # tools MCP (list_layers, list_elements, get_element, create_element, update_element)
  resources/      # resources MCP (capella://{model_path}/...)
tests/
  fixtures/       # modelo .aird de teste
```

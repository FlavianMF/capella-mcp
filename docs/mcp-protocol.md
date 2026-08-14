# Como funciona o MCP (Model Context Protocol)

Nota técnica sobre o protocolo em si — não a arquitetura específica deste
projeto (isso está em [`architecture.md`](architecture.md) e nas
[decisions](decisions/)). Objetivo aqui é entender o mecanismo que
`capella-mcp` implementa, pra usá-lo melhor.

## O que é

MCP é um protocolo aberto, baseado em mensagens **JSON-RPC 2.0**, que
padroniza como aplicações LLM (**hosts**) se conectam a fontes de
contexto/ferramentas externas (**servers**) através de um **client**
embutido no host. Inspirado no [Language Server Protocol
(LSP)](https://microsoft.github.io/language-server-protocol/): assim como o
LSP padronizou "como um editor fala com um servidor de linguagem", o MCP
padroniza "como uma app de LLM fala com uma fonte de contexto/ferramentas" —
em vez de N integrações ad hoc, uma interface comum.

Anunciado pela Anthropic em 25/11/2024 (SORIA PARRA; SPAHR-SUMMERS, 2024),
criado por David Soria Parra e Justin Spahr-Summers. Hoje é projeto aberto
hospedado pela Linux Foundation, org `modelcontextprotocol` no GitHub,
mantido por David Soria Parra e Den Delimarsky.

## Handshake e transporte

Toda sessão MCP começa com uma negociação:

```
Client → Server : initialize (versão do protocolo, capabilities do client)
Server → Client : initialize response (capabilities do server, campo "instructions")
Client → Server : notifications/initialized
```

Depois disso as duas pontas só usam as capabilities que foram negociadas —
se o client não declarou suporte a `sampling`, o server não pode pedir
`sampling` naquela sessão, por exemplo.

`capella-mcp` usa o transporte **stdio** (client sobe o processo do servidor
e fala com ele via stdin/stdout) — ver [ADR
0003](decisions/0003-empacotamento-docker.md). O protocolo também define
transporte HTTP (streamable HTTP/SSE) para servers remotos, não usado aqui.

## Primitivas

| Primitiva | Lado | Quem controla | Papel |
|---|---|---|---|
| `resources` | server | aplicação | dados/contexto que o client decide expor (arquivo, registro, etc.) |
| `tools` | server | **modelo** | funções que o modelo escolhe chamar sozinho, com base no prompt do usuário |
| `prompts` | server | **usuário** | templates de mensagem prontos, acionados explicitamente (ex.: slash command) |
| `sampling` | client | server pede, usuário aprova | server pede pro client rodar uma completion LLM — comportamento agentic recursivo |
| `roots` | client | — | limites de filesystem/URI em que o server pode operar |
| `elicitation` | client | server pede, usuário responde | server pede informação adicional ao usuário durante uma operação |

`capella-mcp` hoje só usa **resources** (`capella://{model_path}/layers`,
`.../layer/{layer}`, `.../element/{element_id}`) e **tools**
(`list_layers`, `list_elements`, `get_element`, `create_element`,
`update_element`) — ver [architecture.md](architecture.md#superfície-mcp-resumo).
Não usa `prompts`, `sampling`, `roots` nem `elicitation`.

## MCP e system prompts

O ponto de contato mais direto entre MCP e "system prompt" é o campo
**`instructions`**, devolvido pelo server na resposta do `initialize`: texto
livre pensado para o client incorporar como guia de uso do servidor — na
prática, um trecho de contexto que molda como o modelo entende e usa aquele
server, análogo a uma seção de system prompt.

O SDK Python usado aqui (`mcp[cli]`, pacote `mcp` — pin `>=1.2.0` em
`pyproject.toml:9`, resolvido para `2.0.0` no lock) expõe isso direto no
construtor:

```python
MCPServer(
    name: str | None = None,
    ...
    instructions: str | None = None,
    ...
)
```

`capella-mcp` instancia só com o nome — `MCPServer("capella-mcp")`
(`src/capella_mcp/server.py:12`) — sem passar `instructions=`. É uma
oportunidade concreta e barata: descrever ali, por exemplo, a convenção de
`layer` (OA/SA/LA/PA/EPBS) ou o fluxo esperado
list_layers → list_elements → get_element → create_element/update_element,
economizando isso do prompt de sistema do host.

A segunda via, mais indireta, é a `description` de cada tool e resource —
isso também vira contexto que o modelo lê antes de decidir uma ação, só que
por tool/resource em vez de uma vez só no handshake.

## Como direcionar (steering) o modelo via MCP

Formas com que um server MCP influencia o comportamento do modelo, do mais
amplo ao mais pontual:

1. **`instructions` no handshake** — contexto de sessão inteira, ver acima.
2. **`tools[].description` e `tools[].annotations`** (`title`,
   `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) —
   funcionam como prompt engineering implícito: é o texto que o modelo lê
   pra decidir quando e como chamar a tool. A spec alerta que annotations
   devem ser tratadas como **untrusted** a menos que venham de um server
   confiável (risco de tool description injection).
3. **`isError: true` no resultado de uma tool call** — convenção que dá
   feedback acionável pro modelo se autocorrigir (parâmetro errado, tipo
   inválido, etc.) em vez de um erro de protocolo genérico. `capella-mcp` já
   segue essa convenção — ver [ADR
   0004](decisions/0004-escopo-v1-leitura-e-escrita.md).
4. **`prompts` primitive** — templates prontos que o *usuário* aciona (não
   o modelo), útil pra padronizar um fluxo de trabalho comum sem depender do
   modelo "lembrar" de fazer certos passos.
5. **`sampling`** — o server pede pro client rodar uma chamada LLM adicional
   (ex.: um sub-agente); o protocolo garante que o usuário controla se isso
   acontece, qual prompt exato é enviado, e o que o server vê do resultado —
   desenhado assim de propósito, pra o server não conseguir extrair prompts
   arbitrários do usuário via sampling.

## Referências

SORIA PARRA, David; SPAHR-SUMMERS, Justin. **Introducing the Model Context
Protocol**. Anthropic, 25 nov. 2024. Disponível em:
<https://www.anthropic.com/news/model-context-protocol>. Acesso em: 14 ago. 2026.

MODEL CONTEXT PROTOCOL. **Specification (2025-11-25)**. Linux Foundation /
`modelcontextprotocol` org, 2025. Disponível em:
<https://modelcontextprotocol.io/specification/2025-11-25>. Acesso em: 14 ago. 2026.

MODEL CONTEXT PROTOCOL. **python-sdk**. Linux Foundation /
`modelcontextprotocol` org. Disponível em:
<https://github.com/modelcontextprotocol/python-sdk>. Acesso em: 14 ago. 2026.

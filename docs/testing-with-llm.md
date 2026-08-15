# Testar o capella-mcp com Claude Code contra um Capella real

Guia pra conectar o Claude Code (CLI) no `capella-mcp` rodando local
(sem Docker), contra um Capella instalado de verdade na máquina, e
testar as tools via LLM em vez de só `pytest`. Ver
`docs/decisions/0003-empacotamento-docker.md` (seção "Atualização") pro
porquê do caminho local existir ao lado do Docker.

## Pré-requisitos

- Capella + `python4capella` instalados local (ex.
  `/home/flv/projetos_ita/capella`, binário `capella` na raiz do
  install — no Linux não existe `capellac` separado, ver
  `docs/decisions/0003-empacotamento-docker.md`).
- `xvfb` instalado (`which xvfb-run` não pode dar vazio — Capella
  precisa de display mesmo headless).
- `uv` instalado.
- Repo em `master`, commit `1695f55` ou mais recente (é onde
  `bridge.py` passou a funcionar de verdade contra Capella real — antes
  disso nenhuma tool funcionava, nem local nem Docker).

## Variáveis de ambiente

Todas lidas em `src/capella_mcp/bridge.py:28-31`, sem arquivo de config
separado.

| Variável | Valor pra este setup | Pra quê |
| --- | --- | --- |
| `CAPELLA_BIN` | `/home/flv/projetos_ita/capella/capella` | binário do Capella |
| `CAPELLA_MODELS_ROOT` | `/home/flv/projetos_ita/capella_mcp/tests/fixtures` | raiz onde `model_path` das tools é resolvido |
| `CAPELLA_WORKSPACE_ROOT` | `/tmp/capella-mcp-workspaces` | workspace efêmero por chamada (padrão já serve) |
| `CAPELLA_TIMEOUT_SECONDS` | `180` | timeout por chamada (padrão já serve) |

Com `CAPELLA_MODELS_ROOT` apontando pro fixture, toda tool usa
`model_path="demo.aird"`.

## Registrar o servidor no Claude Code

Duas formas, escolha uma.

**`claude mcp add`** (usa `uv run --directory <path>`, flag do `uv`
pra rodar num diretório de projeto sem precisar `cd`/wrapper script):

```bash
claude mcp add capella --env CAPELLA_BIN=/home/flv/projetos_ita/capella/capella \
  --env CAPELLA_MODELS_ROOT=/home/flv/projetos_ita/capella_mcp/tests/fixtures \
  --env CAPELLA_WORKSPACE_ROOT=/tmp/capella-mcp-workspaces \
  --env CAPELLA_TIMEOUT_SECONDS=180 \
  -- uv run --directory /home/flv/projetos_ita/capella_mcp capella-mcp
```

**`.mcp.json`** (mais legível com várias env vars, escopo por projeto):

```json
{
  "mcpServers": {
    "capella": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/home/flv/projetos_ita/capella_mcp", "capella-mcp"],
      "env": {
        "CAPELLA_BIN": "/home/flv/projetos_ita/capella/capella",
        "CAPELLA_MODELS_ROOT": "/home/flv/projetos_ita/capella_mcp/tests/fixtures",
        "CAPELLA_WORKSPACE_ROOT": "/tmp/capella-mcp-workspaces",
        "CAPELLA_TIMEOUT_SECONDS": "180"
      }
    }
  }
}
```

Pra rodar via Docker em vez disso, ver o exemplo no `README.md` da raiz
(build `docker build -t capella-mcp .` primeiro).

## Verificar

```bash
claude mcp list
claude mcp get capella
```

Numa sessão do Claude Code, `/mcp` deve listar o servidor `capella`
conectado, com 5 tools (`list_layers`, `list_elements`, `get_element`,
`create_element`, `update_element`) e 3 resources
(`capella://{+model_path}/layers`, `capella://{+model_path}/layer/{layer}`,
`capella://{+model_path}/element/{element_id}`).

## Roteiro de teste sugerido

Em ordem crescente de risco — pare antes do último ponto de "evitar" se
só quer confirmar que a conexão funciona:

1. "liste as camadas do modelo demo.aird" — `list_layers`, só leitura,
   validado.
2. "liste os elementos da camada la do demo.aird" — `list_elements`
   sem `type_filter`, validado.
3. "crie um LogicalComponent chamado X na camada la do demo.aird, depois
   busque esse elemento pelo id que voltou" — `create_element` +
   `get_element`, validado (é exatamente o que
   `tests/test_integration.py` cobre).
4. **Evitar por enquanto**: pedir pra criar elemento em qualquer
   camada/tipo que não seja `LogicalComponent`/`la`,
   `SystemFunction`/`sa`, `LogicalFunction`/`la`,
   `OperationalActivity`/`oa`, `OperationalActor`/`oa`,
   `OperationalEntity`/`oa` ou `OperationalCapability`/`oa` — ver
   limitações abaixo.

## Limitações conhecidas (estado atual — não esconder isso da LLM/usuário)

- **`create_element`/`update_element` só têm containment real validado
  pra sete combinações: `type_name="LogicalComponent"` sob `"la"`,
  `type_name="SystemFunction"` sob `"sa"`, `type_name="LogicalFunction"`
  sob `"la"`, `type_name="OperationalActivity"` sob `"oa"`,
  `type_name="OperationalActor"`/`"OperationalEntity"` sob `"oa"`, e
  `type_name="OperationalCapability"` sob `"oa"`**
  (`src/capella_mcp/bridge.py`, função `create_element`, cadeia de
  branches `if`/`elif` por `type_name`). Qualquer outro tipo cai no
  fallback `container.get_contents().append(el)`, que **não persiste**
  — a tool retorna sucesso com um `id` válido, mas numa chamada
  separada esse elemento nunca aparece de novo. É perda de dado
  silenciosa, não erro visível pra LLM nem pro usuário.
  - `OperationalActor`/`OperationalEntity` compartilham o mesmo
    container raiz (`EntityPkg.get_owned_entities()`) — são duas views
    Python da mesma classe EMF `Entity`, diferindo só pela flag
    `actor`. Só `OperationalEntity` aceita `parent_id` de aninhamento
    (ator não pode ter sub-entidades, por design do metamodelo).
  - `OperationalCapability` é sempre flat — passar `parent_id` levanta
    erro explícito (`ValueError`), não existe accessor de
    auto-aninhamento pra capacidades.
- **`create_diagram`/`BREAKDOWN_DIAGRAMS` cobre 9 combinações**
  (`LogicalComponent`/`la`, `LogicalFunction`/`la`, `SystemFunction`/`sa`,
  `OperationalActivity`/`oa`, `OperationalEntity`/`oa`,
  `OperationalActor`/`oa`, `PhysicalFunction`/`pa`,
  `PhysicalComponent`/`pa`, `ConfigurationItem`/`epbs`) —
  `PhysicalFunction`/`PhysicalComponent`/`ConfigurationItem` têm entrada no
  dict mas nenhuma branch em `create_element` ainda, então só dá pra
  diagramar dados criados manualmente no Capella, não pela MCP.
  - `("oa", "OperationalEntity")`/`("oa", "OperationalActor")`
    (adicionadas 2026-08-13, "Operational Entity Breakdown" — extraído do
    `.odesign` real dentro do jar
    `org.polarsys.capella.core.sirius.analysis`, não adivinhado — ver
    `docs/second_brain` pra método de extração) são as únicas cujo
    `edge_mapping` (`"containedIn Mapping"`) tem espaço embutido e não
    segue o padrão `<Prefixo>_sub<Plural>` dos outros 7 — funciona igual
    (`get_representation_mapping_by_name` casa por string exata), só foge
    do padrão visual.
  - **`("oa", "OperationalEntity")`/`("oa", "OperationalActor")` renderiza
    em branco no export** (testado 2026-08-13: nó criado sem erro
    — `create_diagram` retorna `node_count=1`, sem exceção — mas o PNG
    exportado sai quase vazio, ~125 bytes contra ~1800 bytes dos outros
    diagramas). Comparando o `.aird` bruto, o `notation:Node` desse
    mapeamento não tem o filho `type="3003"` (compartimento visual) que
    todo `notation:Node` dos outros 6 mapeamentos tem — só o `type="5002"`
    (label). Hipótese testada e **descartada** (2026-08-13): tentei
    adicionar `org.eclipse.sirius.business.api.dialect.DialectManager.
    INSTANCE.refresh(java_diag, True, monitor)` no início da transação do
    pass 2 de `create_diagram` (bridge.py), pra forçar o canonical
    synchronizer do Sirius a completar a view GMF — nenhum outro lugar do
    `Python4Capella` chama isso hoje (grep completo em toda a árvore,
    zero ocorrência de refresh/synchron/canonical/arrangeAll relacionado a
    diagrama). A chamada rodou **sem erro nenhum** (prova de que executou
    de verdade, não foi engolida por exceção), mas o XML gerado ficou
    **byte-a-byte idêntico** ao caso sem o refresh — `type="3003"`
    continuou faltando, PNG continuou em branco. Revertido (não sobrou
    rastro no bridge.py). Isso descarta "falta uma chamada de
    refresh/sync" como causa — a diferença é mais provável de estar na
    própria definição de estilo (`<styles>`) do mapeamento
    `OEB_OperationalEntities` no `.odesign`, genuinamente diferente da dos
    outros 6 (não investigado a esse nível — precisaria comparar as
    definições de `styles`/`subNodeMappings` desse mapeamento contra
    `OAB_OperationalActivity` no `oa.odesign` diretamente). `create_element`/
    containment em si está confirmado funcionando (dado persiste,
    `list_elements` mostra certo); é só a representação visual do
    diagrama de entidades especificamente que fica quebrada.
  - O `_walk` genérico de `create_diagram` só cobre **uma raiz + sua
    subárvore** por chamada. O serviço real do Capella pro diagrama de
    Entidades (`OAServices.getOEBScopeBreakdown`) aceita o `EntityPkg`
    inteiro como raiz e devolve uma **floresta** (todas as entidades-raiz
    de uma vez) — `create_diagram` não suporta isso, então múltiplas
    entidades-raiz sem ancestral comum (ex: um ator e uma entidade
    separados) nunca aparecem no mesmo diagrama; é preciso um `root_id`
    por entidade-raiz.
- `attributes` além de `name` em `create_element`/`update_element`
  usam convenção assumida `set_<key>`, nunca validada contra nomes
  reais de atributos do metamodelo Arcadia além de `name`.
- Cada chamada de tool sobe um processo Capella headless do zero —
  ~15-25s por chamada (duração observada nos testes de integração
  locais). Latência esperada, não timeout/erro.
- As 3 resources não têm docstring em
  `src/capella_mcp/resources/model_resources.py` — o cliente MCP não
  mostra descrição pra elas, só o URI template.

## Próximos passos de dev

- Generalizar a resolução de container em `create_element` pra outras
  combinações camada/tipo (ex: `PhysicalComponent`/`pa`,
  `ConfigurationItem`/`epbs`) — cada uma precisa de um accessor
  `get_owned_<...>()` próprio, mesmo padrão já usado pra
  `LogicalComponent`, `SystemFunction`, `LogicalFunction`,
  `OperationalActivity`, `OperationalActor`/`OperationalEntity` e
  `OperationalCapability`.
- Interfaces/exchanges (`ComponentExchange`, `FunctionalExchange`,
  `Interface`) e alocação funcional/operacional (`LogicalFunction` ↔
  `LogicalComponent`, `OperationalActivity` ↔
  `OperationalActor`/`OperationalEntity`) ainda não têm nenhuma branch
  — escopo maior (portas, interface pkg, exchange items).
- Validar a convenção `set_<key>` de `attributes` contra atributos
  reais do metamodelo além de `name`.
- Adicionar docstrings nas 3 funções de resource.

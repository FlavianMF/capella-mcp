---
title: Camada de leitura rápida via capellambse
type: decision
tags: [architecture, capellambse, performance, mcp]
created: 2026-08-19
provenance: capella_mcp
project: capella_mcp
---

# Camada de leitura rápida via capellambse

## Contexto

[[0002-headless-por-chamada]] já previa esta evolução como possibilidade
futura ("Opção 2... fica como evolução futura, sem precisar mudar a API
MCP exposta ao cliente"). Na prática, `list_layers`/`list_elements`/
`get_element` — leitura pura — pagam o mesmo custo de subir um processo
Capella headless (segundos a ~1min) que uma escrita complexa paga,
mesmo quando nada precisa ser modificado nem existe diagrama envolvido.

`capellambse` (PyPI, lib Python pura) lê `.aird`/fragmentos `.capella`
diretamente, sem JVM/Eclipse/Xvfb, em milissegundos. Não sincroniza com
Sirius (diagramas GMF), então não é opção para escrita nem para as tools
de diagrama — mas para leitura de elementos semânticos é uma
substituição direta e correta.

O trabalho de pesquisa por trás deste projeto (`notes/
arquitetura_hibrida_mcp_capella.md` e `notes/rag_mbse_pipeline.md`, no
diretório de notas da dissertação de mestrado) já mapeava essa
combinação como "arquitetura híbrida" — a decisão original nessas notas
cogitava colocar o roteamento num `HybridBridge.java` dentro do plugin
Eclipse `capella_llm_window`. Isso foi descartado aqui: exigiria o
usuário instalar Python + `capellambse` no host além do Docker
existente, contrariando o objetivo central deste projeto de fricção de
setup zero (ver [[0003-empacotamento-docker]]).

## Decisão

O roteamento híbrido mora dentro do próprio `capella_mcp` server
(`bridge.py`), não em nenhum client. `list_layers`/`list_elements`/
`get_element` viram dispatchers: tentam `fast_reader.py` (capellambse)
primeiro, com fallback automático para o template headless já existente
(agora `_list_layers_headless`/etc, corpo inalterado) em qualquer
exceção que não seja um "não encontrado" genuíno.

Detalhes:

- **Só leitura.** Escrita (`create_element`, `update_element`, todas as
  tools de diagrama) continua 100% headless — `capellambse` não
  sincroniza com Sirius, escrever por ele arriscaria dessincronizar
  diagramas existentes do modelo semântico.
- **Fallback automático, com uma exceção.** Qualquer erro do
  `fast_reader` (tipo desconhecido pelo metamodelo capellambse, atributo
  ausente, layer sem estrutura esperada) dispara o fallback headless. A
  única exceção que NÃO dispara fallback é `fast_reader.NotFound`
  (elemento/layer genuinamente inexistente) — cair no headless só pra
  confirmar o mesmo "não encontrado" custaria ~1min à toa.
- **`list_elements` sem `type_filter` não tem fast-path.** O
  `get_contents()` do lado headless retorna os filhos EMF diretos da
  layer (os `*Pkg`, não os elementos de domínio dentro deles) — não há
  equivalente limpo de uma chamada só no `capellambse` pra essa forma
  rasa, e replicar via força bruta arriscaria um resultado sutilmente
  errado. `fast_reader.list_elements` levanta `NotImplementedError`
  nesse caso, delegando pro headless (ver comentário no próprio módulo).
- **Lock de arquivo.** Como o processo do MCP server agora é
  persistente (ao contrário do processo Capella, que sobe/derruba por
  chamada), existe janela real de uma leitura via `capellambse`
  acontecer no meio de um `model.save()` headless. `bridge.model_lock()`
  (um `filelock.FileLock` por `model_path`) protege as 3 leituras
  rápidas e as 9 tools de escrita (`create_element`, `update_element` e
  as 7 tools de diagrama) — leituras headless (fallback) não precisam,
  já são consistentes por natureza. O lock das escritas vive em
  `tools/model_tools.py` (envolvendo a chamada a `bridge.*`), não dentro
  de `bridge.py`, porque várias tools de diagrama são multi-pass (várias
  chamadas `_run_script()` em sequência — ver `create_diagram`) e o lock
  precisa cobrir a sequência inteira como uma seção crítica atômica, não
  cada passo isolado.

## Consequências

- Nova dependência pip pura: `capellambse` (+ `filelock` pro lock).
  Nenhuma mudança de setup para quem já usa a imagem Docker (ADR 0003) —
  só mais um pacote resolvido por `uv sync`.
- `tests/test_fast_reader.py` roda contra os fixtures reais
  (`tests/fixtures/demo.aird`) sem precisar de Capella/Docker instalado
  — `capellambse` é Python puro. Isso passa a valer como "inner loop"
  mais rápido ainda que o já existente
  `scripts/run_integration_tests_local.sh` ([[0003-empacotamento-docker]],
  seção "Atualização") para qualquer mudança na camada de leitura.
- `element.uuid` do `capellambse` é o mesmo `xmi:id` que
  `_element_id()`/`getId()` já usa no lado headless — não é coincidência
  de implementação, é o identificador persistente XMI do próprio EMF.
  Confirmado ao vivo (2026-08-19, `scripts/run_integration_tests_local.sh`,
  15/15 passando, ~13min contra o Capella local real): `test_create_then_
  get_element_round_trips` e `test_update_element_persists_across_
  separate_calls` criam/atualizam um elemento via headless e leem de
  volta via `get_element` -- com o dispatcher novo essa releitura passa
  pelo `fast_reader` primeiro, então um id ou atributo incompatível entre
  os dois caminhos teria quebrado esses testes já existentes (e não
  quebrou).
- Efeito colateral no valor da suíte de integração: `test_list_layers_
  returns_five_arcadia_layers` e as chamadas de `list_elements`/
  `get_element` usadas como setup dentro de outros testes agora resolvem
  via `fast_reader` (modelo real, sem stub) em vez de exercitar de fato o
  processo Capella headless para essas 3 operações — a cobertura do
  *template* headless de leitura em si (estrutura do script gerado)
  continua garantida por `tests/test_bridge.py::TestGeneratedScripts`
  (mockado, sem subir Capella), só deixou de ser exercitada via processo
  real nesse caminho específico.
- `label` no fast-path usa o atributo `name` bruto do `capellambse`, não
  o `get_label()` do lado headless (que passa pelo serviço de label
  provider do próprio Capella). Para a grande maioria dos elementos
  nomeados isso é idêntico, mas já existe um caso documentado onde
  diverge (ver o bug de nome-placeholder de `InstanceRole`/`Part` no
  branch `InstanceRole` de `create_element`, em `bridge.py`) — se
  `list_elements`/`get_element` via fast-path algum dia mostrarem um
  label suspeito, esse é o primeiro lugar a olhar.
- Abre o caminho pro pipeline RAG (2º passo da pesquisa, ver
  `notes/rag_mbse_pipeline.md`): o extrator de chunks do modelo
  (`rag/ingestion/model_extractor.py`, ainda não implementado) reusa
  exatamente este `fast_reader.py` e a mesma disciplina de lock, em vez
  de reabrir `capellambse` do zero com lógica própria.

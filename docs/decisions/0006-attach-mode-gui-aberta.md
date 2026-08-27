---
title: Attach mode -- sync com Capella GUI já aberto
type: decision
tags: [architecture, python4capella, capella, sirius, mcp]
created: 2026-08-27
provenance: capella_mcp
project: capella_mcp
---

# Attach mode -- sync com Capella GUI já aberto

## Contexto

[[0002-headless-por-chamada]] sobe um Capella headless novo por tool
call, cada um com seu próprio workspace Eclipse efêmero
(`bridge.py::_run_script`, `-data <uuid>`, importa o `.aird` do disco).
Isso nunca considerou o caso de o usuário ter o Capella GUI aberto
olhando o mesmo modelo em paralelo: as mudanças do MCP só apareciam lá
depois de F5 manual + reabrir o editor -- dois `IWorkspaceRoot`/JVMs
isolados, sem canal nenhum entre eles.

## Alternativas consideradas

1. **Nada, F5 manual.** Zero esforço, mas o usuário pediu algo mais
   interativo.
2. **Refresh passivo, só do lado da GUI.** Um plugin/preferência que só
   percebe "o arquivo mudou no disco" e recarrega -- unidirecional (MCP
   -> GUI), não dá ao MCP visibilidade de edições do usuário ainda não
   salvas. Descartada porque o usuário quer os dois lados cientes um do
   outro, não só a GUI reagindo ao MCP.
3. **Rota A -- socket persistente.** Um script EASE `# onStartup`
   rodando no engine "Python (Py4J)" sobe (via
   `py4j.ClientServer$ClientServerBuilder`, mecanismo confirmado em
   `org.eclipse.ease.lang.python.py4j`) um processo Python filho de vida
   longa que abre `socket.listen()` própria e atende requisições TCP.
   Menor latência que arquivo, mas: (a) processo filho novo por GUI
   aberta, porta a gerenciar; (b) mais superfície de erro (porta em uso,
   firewall); (c) nenhuma vantagem de latência real para o caso de uso
   (tool calls de uma LLM, não um loop de UI a 60fps). Documentada aqui,
   **não construída**.
4. **Rota B -- arquivo, bidirecional. ADOTADA.** Sem processo novo, sem
   porta -- um script `# onStartup` já rodando dentro do processo da GUI
   faz polling direto em `java.io.File`/`os.path` (não via EASE
   `onResourceChange`, que depende da preferência "refresh nativo ou
   polling do workspace" estar ligada do lado do usuário -- exatamente a
   mesma causa-raiz do problema original; polling próprio não depende de
   preferência nenhuma). `bridge.py` detecta essa GUI sozinho (heartbeat)
   e passa a falar com ela em vez de spawnar um Capella novo, nos dois
   sentidos (leitura E escrita).

## Decisão

Rota B, como descrito no design abaixo. Full write-up do protocolo em
`src/capella_mcp/attach_listener.py` (a metade que roda dentro da GUI do
usuário) e nos comentários de `bridge.py` em torno de `_attach_target`/
`_run_script_attach`/`_dispatch`.

### Risco técnico verificado ao vivo antes de construir

Maior risco do desenho inteiro: **abrir a mesma sessão duas vezes dentro
do mesmo processo JVM reaproveita ou duplica?** Confirmado via um script
headless-descartável (mesma técnica de `_run_script`, sem GUI real
envolvida -- `SessionManager` não distingue "sessão aberta via clique do
usuário" de "sessão aberta via chamada de API", é a mesma JVM/registro):

```python
SessionManager = org.eclipse.sirius.business.api.session.SessionManager
ArrayList = java.util.ArrayList   # workaround abaixo

model1 = CapellaModel(); model1.open(workspace_path)
sessions_after_first = ArrayList(SessionManager.INSTANCE.getSessions()).size()   # 1

model2 = CapellaModel(); model2.open(workspace_path)   # mesmo path, mesma JVM
sessions_after_second = ArrayList(SessionManager.INSTANCE.getSessions()).size()  # 1, não 2

session1 == session2   # True
```

**Reaproveita, não duplica.** `CapellaModel().open()` pode ser chamado
normalmente dentro do listener attach, sem precisar localizar a sessão
existente via `SessionManager.INSTANCE.getExistingSession(uri)`
explicitamente -- o comportamento já é o desejado por padrão.

Achado colateral durante o mesmo spike: `SessionManager.INSTANCE
.getSessions()` retorna um `Collections$UnmodifiableCollection` (classe
interna não-pública do JDK); Py4J tentando refletir `.size()` direto nela
estoura `InaccessibleObjectException` (JPMS -- "module java.base does not
open java.util to unnamed module"). Workaround: envolver em
`java.util.ArrayList(colecao)` primeiro (classe pública concreta, sem
`setAccessible` necessário) -- usado tanto no spike quanto em
`attach_listener.py::_open_model_paths()`.

### Design

**Descoberta (auto-detecção, sem env var/flag):** o listener escreve um
heartbeat a cada ~3s em `~/.capella-mcp/attach/<hash-do-workspace>/
heartbeat.json` -- `{pid, workspace_path, open_models, heartbeat_ts}`.
`open_models` é uma lista de **caminhos absolutos de filesystem**, não de
paths relativos a projeto Eclipse -- resolvidos via
`session.getSessionResource().getURI()` +
`IWorkspaceRoot.getFile(new Path(uri.toPlatformString(true))).getLocation()
.toOSString()` (confirmado ao vivo: para `platform:/resource/models/
demo.aird`, retorna exatamente `/home/.../tests/fixtures/demo.aird`, o
mesmo `abs_path` que `bridge.resolve_model_path()` já produz). Path
absoluto foi escolhido em vez de comparar strings de path Eclipse-relativo
porque o workspace real e de longa duração do usuário quase certamente
usa um layout de projeto diferente do projeto `mcp_script`/`models`
efêmero que o spawn mode inventa por chamada -- comparar pelo arquivo real
no disco é a única forma correta de "é o mesmo modelo?" que independe de
como cada lado organiza seu workspace Eclipse.

`bridge.py::_attach_target(abs_path)` varre
`~/.capella-mcp/attach/*/heartbeat.json`, aceita heartbeats com até ~10s
(`ATTACH_HEARTBEAT_MAX_AGE_SECONDS`) e cujo `open_models` contenha
`str(abs_path)`. Sem match -> `None`, tudo segue exatamente como hoje
(spawn).

**Protocolo de request/response**, por arquivo, dentro de
`<heartbeat-dir>/requests/`: `bridge.py` escreve `<id>.request.json`
(`{"body": "<mesmo texto de script que _run_script já usaria>"}`); o
listener (polling a cada ~500ms) executa esse `body` via
`exec(compile(body, ...), local_ns)`, com `local_ns` pré-populado com
`CapellaModel`/`_element_id`/`_serialize` (duplicados à mão de
`bridge.py::_preamble()` -- o listener roda num processo Python
totalmente separado, não pode importar o pacote do servidor MCP) e um
`_write_result` que grava `<id>.result.json`. `bridge.py::
_run_script_attach` escreve o pedido, faz *polling* do arquivo de
resultado com timeout (`ATTACH_REQUEST_TIMEOUT_SECONDS`, default 30s) e
limpa os dois arquivos ao final -- mesmo contrato de erro que
`_run_script` (`{"error": ...}` no JSON -> `BridgeError`).

**Reaproveitamento de código, não duplicação de lógica de negócio:** o
`body` passado pro attach é literalmente o mesmo texto que os
dispatchers já montam para spawn mode -- só muda o transporte
(`_dispatch()` escolhe attach-se-disponível, senão spawn). A única
adaptação textual necessária no `body` em si é o guard de save (abaixo).

**Sem save forçado.** `_preamble()` (spawn) define `_ATTACH_MODE = False`;
`attach_listener.py` define `_ATTACH_MODE = True` no seu próprio
namespace de execução. Os dois dispatchers de escrita do MVP
(`create_element`, `update_element`) trocaram `model.save()` por
`if not _ATTACH_MODE: model.save()`. Em attach, o commit da transaction
já é visível na GUI na hora (mesma sessão), mas o arquivo só é salvo
quando o usuário decidir (Ctrl+S) -- exatamente como uma edição manual
dele. Spawn mode continua salvando a cada call, sem mudança de
comportamento. Mecanismo explícito de "salvar quando eu quiser" via MCP
fica **fora de escopo** desta rodada -- decisão consciente do usuário,
revisar depois.

**Sem guarda de concorrência.** Não checar dirty, foco, ou qualquer
sinal de "edição em progresso" antes de escrever via attach -- decisão
explícita do usuário: como não há save forçado, a sessão fica dirty
permanentemente após a primeira escrita do MCP, então qualquer guarda
baseada em "tem algo não-salvo" bloquearia toda chamada seguinte para
sempre. E o objetivo é justamente o MCP **trabalhar em cima** do que o
usuário tem editado na tela, não recusar por causa disso.

### Registro sem clicar em Preferences

O passo manual "Script Locations > Add..." incomodava -- pesquisado se
dava pra eliminar. `org.eclipse.ease.ui.scripts.preferences
.PreferencesHelper.addLocation()` (bytecode inspecionado em
`org.eclipse.ease.ui.scripts_0.8.0.*.jar`) só grava 3 linhas num
preference store `InstanceScope` comum -- confirmado ao vivo (não
suposição): rodei um script headless-descartável chamando essa API Java
de verdade (`IRepositoryFactory.eINSTANCE.createScriptLocation()` +
`PreferencesHelper.addLocation(loc)` + `node.flush()`) e li de volta o
arquivo gerado:

```
eclipse.preferences.version=1
file\:|home|...|src|capella_mcp/default=false
file\:|home|...|src|capella_mcp/location=file\:/home/.../src/capella_mcp
file\:|home|...|src|capella_mcp/recursive=false
```

em `<workspace>/.metadata/.plugins/org.eclipse.core.runtime/.settings/
org.eclipse.ease.ui.scripts.prefs`. Encoding do node name: valor da URI
com `:` escapado (`\:`, sintaxe de `.properties`) e `/` trocado por `|`
(separador de node de preference não pode aparecer no nome do node).
`scripts/register_attach_listener.py` reproduz esse mesmo arquivo
diretamente (só I/O de arquivo, sem subir Capella) -- testado
byte-a-byte idêntico ao gerado pela API real (`tests/
test_register_attach_listener.py`).

**Limite honesto dessa verificação:** confirmei o *formato* do arquivo
ao vivo, mas não confirmei que uma entrada pré-semeada assim (sem nunca
ter passado pela UI) é de fato tratada pelo EASE como uma Script
Location válida numa **GUI interativa real** -- testei rodando Capella
via `-appid org.eclipse.python4capella.commandline` (mesmo modo do
bridge.py) com o arquivo pré-escrito, e `PreferencesHelper.getLocations()`
voltou vazio mesmo. Isso é inconclusivo, não uma prova de que não
funciona: a mesma chamada `getLocations()` também voltou vazia
imediatamente depois de um `addLocation()`+`flush()` bem-sucedido, na
mesma sessão -- sugere que `getLocations()` (ou o que popula seu cache
interno, `RepositoryService`/`UpdateRepositoryJob`) depende de algum
evento de lifecycle que o modo commandline headless não dispara (ex:
`IStartup` do workbench de verdade), não necessariamente que o arquivo
em si esteja errado. Documentado como caminho A (tente primeiro) com
fallback pro clique manual (caminho B, sempre funciona) em
`docs/architecture.md` -- sem afirmar "resolvido" além do que foi de
fato verificado.

### Correção: gatilho manual errado (`# menu` não é botão-direito)

Usuário testou (script E clique manual em Preferences) e o item de
"botão direito > Capella MCP -- Start Attach Listener" não apareceu no
Project Explorer. Causa raiz, inspecionando o bytecode real de
`org.eclipse.ease.ui.scripts_0.8.0.*.jar` (`plugin.xml`'s extension
point `org.eclipse.ease.ui.scripts.keyword` + `MenuHandler`/
`PopupHandler`/`ToolbarHandler`.class):

- `# menu : <viewID>` contribui pro **menu-dropdown da própria view**
  (o ícone de seta no canto do toolbar de uma view) -- não é um menu de
  contexto/botão-direito. A doc do python4capella de onde copiei esse
  exemplo até chama a seção de "**View Menu**"; interpretação errada
  minha na hora de escrever o header original.
- Botão-direito de verdade é o keyword `popup`
  (`PopupHandler extends ToolbarHandler`), registrado numa location fixa
  `popup:org.eclipse.ui.popup.any?after=additions` (não recebe viewID
  como `# menu`/`# toolbar` -- o valor depois do `:` é, pelo único
  exemplo documentado encontrado, uma expressão
  `enableFor(<Tipo totalmente qualificado>)`). Não confirmado ao vivo se
  aceita um tipo Eclipse comum (`IProject`) e não só tipos Capella
  (`CapellaElement`, o único exemplo visto) -- sem GUI interativa
  disponível aqui pra testar (o `-appid ...commandline` headless não
  sobe workbench/menus de verdade).

Corrigido: `attach_listener.py` ganhou `# popup :
enableFor(org.eclipse.core.resources.IProject)` como bônus best-effort
(não confirmado), e `# script-type : Python` (presente no único exemplo
de header completo e funcional da doc, faltava aqui). Mas o fallback
manual **documentado como confiável** deixou de depender de qualquer
header custom: é a view **Script Explorer** do próprio EASE
(`org.eclipse.ease.ui.views.scriptExplorerView`, confirmado via
`plugin.xml` -- toolbar próprio com botão Run/Edit/Refresh sempre
presente pra qualquer script de uma Script Location registrada,
independente de `# menu`/`# popup`/`# toolbar` estarem certos).

## Consequências

- Leitura (`list_layers`/`list_elements`/`get_element`) e escrita
  (`create_element`/`update_element`) passam por `_dispatch()`, que tenta
  attach antes de spawn. As demais tools (diagramas, `delete_diagram`,
  `layout_diagram`, `export_diagram`) continuam só em spawn nesta
  rodada -- extensão é mecânica (mesmo padrão, function por function),
  fica para depois.
- Risco de concorrência real e aceito: duas fontes (usuário na GUI, MCP
  via attach) editando o mesmo `TransactionalEditingDomain` podem
  misturar undo/redo. EMF serializa transactions (não corrompe dado),
  mas não coordena intenção -- comportamento análogo a dois humanos
  editando a mesma sessão. Revisitar só se virar problema real
  observado na prática, não preventivamente.
- Auto-detecção é heurística (heartbeat de até ~10s de idade) -- uma
  janela pequena onde o MCP ainda tenta attach num listener que acabou
  de cair existe; `_run_script_attach` trata isso como timeout
  (`_AttachUnavailable`) e cai para spawn automaticamente, nunca falha a
  call por causa disso.
- `attach_listener.py` precisa ser registrado manualmente pelo usuário
  como Script Location na instalação da GUI dele (não é algo que o
  Docker/CI precisem saber -- é puramente inner loop local, não conflita
  com [[0003-empacotamento-docker]]). Ver docs/architecture.md para o
  passo a passo e o roteiro de verificação manual (não automatizável em
  CI -- exige uma GUI Capella real na tela).
- O header `# onStartup : 2` depende do keyword handler genérico do EASE
  (`org.eclipse.ease.ui.scripts.keywordhandler.StartupHandler`,
  confirmado existir via plugin.xml real) disparar também para scripts
  python4capella especificamente -- não confirmado ao vivo ainda (só
  visto documentado para `# popup`/`# toolbar`/`# menu`/
  `# onResourceChange`/`# onSave`). Por isso o script também carrega um
  header `# menu` como gatilho manual de fallback -- se o autostart não
  disparar sozinho, o usuário aciona uma vez pelo menu e o listener fica
  rodando pelo resto da sessão da GUI.

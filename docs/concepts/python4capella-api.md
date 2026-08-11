---
title: API simplificada do python4capella
type: concept
tags: [python4capella, capella, api, py4j]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# API simplificada do python4capella

Fonte: `plugins/Python4Capella/simplified_api/capella.py` no repositório
[labs4capella/python4capella](https://github.com/labs4capella/python4capella)
(EPL-2.0, ativamente mantido). Importada dentro dos scripts do bridge via:

```python
include('workspace://Python4Capella/simplified_api/capella.py')
```

## Abrir e navegar

```python
model = CapellaModel().open(path_do_aird)          # Sirius.load_session(...)
se = model.get_system_engineering()                # SystemEngineering

oa = se.get_operational_analysis()
sa = se.get_system_analysis()
la = se.get_logical_architecture()
pa = se.get_physical_architecture()
epbs = se.get_e_p_b_s_architecture()
```

Elementos genéricos herdam de `EObject`: `get_contents()`,
`get_all_contents_by_type()`, `get_container()`, `get_label()`.

## Criar e editar (transactions)

```python
model.start_transaction()
try:
    comp = LogicalComponent()
    # ... setar atributos, adicionar em algum container ...
    model.commit_transaction()
except Exception:
    model.rollback_transaction()
    raise
model.save()   # session.save(...)
```

Toda operação de escrita do bridge segue esse padrão: `start_transaction`
→ modificar → `commit_transaction` (ou `rollback_transaction` em erro) →
`save()`.

## Limitações conhecidas (documentadas pelo próprio projeto)

- Nem todas as features do metamodelo Capella estão expostas na API
  simplificada — casos não cobertos exigem `get_java_object()` para acessar
  o objeto Java bruto.
- Operações em listas não-modificáveis do metamodelo Java geram erro.
- Nenhuma menção explícita a thread-safety — reforça a decisão de não
  paralelizar chamadas dentro do mesmo processo Capella (ver
  [[0002-headless-por-chamada]], cada chamada é um processo isolado).

## Execução headless

```
xvfb-run -a capellac \
  -application org.polarsys.capella.core.commandline.core \
  -appid org.eclipse.python4capella.commandline \
  -data <workspace> workspace:/<script>.py
```

Requer Capella instalado com o plugin `python4capella` e, no Linux, um
display virtual (Xvfb) — mesmo em modo linha de comando o Capella exige
GUI/window manager. Ver [[0003-empacotamento-docker]] para como isso é
empacotado neste projeto.

## Instalação do plugin (referência)

Versão pinada: `python4capella` 1.4.1, instalado via `p2 director` direto
no zip da release (sem precisar abrir a GUI do Capella):

```
xvfb-run -a /opt/capella/capella \
  -application org.eclipse.equinox.p2.director \
  -repository "jar:file:/tmp/python4capella.zip!/" \
  -installIU org.eclipse.python4capella.feature.feature.group,org.eclipse.python4capella.commandline.feature.feature.group \
  -destination /opt/capella \
  -profile DefaultProfile \
  -nosplash -consoleLog
```

`org.eclipse.python4capella.commandline.feature.feature.group` é o feature
que registra o `-appid org.eclipse.python4capella.commandline` usado na
invocação headless.

---
title: python4capella não é lib externa importável
type: decision
tags: [python4capella, capella, eclipse, py4j, ease]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# python4capella não é lib externa importável

## Contexto

O plano original (`docs/index.md`) previa usar `python4capella` como uma
lib Python comum, importada dentro do processo do servidor MCP para ler e
escrever o modelo Capella.

Investigação do repositório (labs4capella/python4capella, licença EPL-2.0,
ativamente mantido) mostrou que isso não é possível: `python4capella` é um
**addon Eclipse/Capella**. Ele roda *dentro* do processo do Capella, via
**Eclipse EASE** (ambiente de scripting) + **Py4J** (bridge Python↔Java) +
**PyDev**. Não existe leitura/escrita offline de `.aird`/`.capella` — o
modelo é carregado via sessão Sirius dentro do próprio Eclipse
(`Sirius.load_session`).

Acionamento é possível de duas formas: pelo menu do Capella (GUI) ou via
linha de comando headless:

```
capellac -application org.polarsys.capella.core.commandline.core
         -appid org.eclipse.python4capella.commandline
         -data <workspace> workspace:/<script>.py
```

Mesmo em modo linha de comando, a documentação do projeto confirma que o
Capella exige GUI/window manager — em Linux, precisa de um display virtual
(Xvfb).

## Decisão

Tratar `python4capella` como uma **integração via processo externo**, não
como dependência pip. O servidor MCP invoca o Capella em modo headless
(via `xvfb-run` + `capellac`) para cada operação, em vez de importar
qualquer coisa do `python4capella` diretamente no processo Python do
servidor.

## Alternativas descartadas

- **Parsear `.aird`/`.capella` (XMI/EMF) diretamente em Python** (ex.:
  `pyecore`), sem depender do Eclipse/Capella rodando. Descartado: perderia
  a validação e o sincronismo de diagrama (Sirius) que o Capella garante, e
  contraria a intenção original de usar `python4capella` como camada de
  interface confiável.

## Consequências

- Toda operação de leitura/escrita passa a depender de subir um processo
  Capella (lento — ver [[0002-headless-por-chamada]]).
- O ambiente de execução precisa do Capella instalado com o plugin
  `python4capella`, mais Xvfb — decisão de empacotamento em
  [[0003-empacotamento-docker]].
- A API disponível para o servidor MCP é a API simplificada do
  `python4capella` (ver [[python4capella-api]]), que não cobre 100% do
  metamodelo Capella.

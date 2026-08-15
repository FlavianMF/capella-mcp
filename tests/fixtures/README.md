# Fixture: demo.aird

Os testes de integração (`tests/test_integration.py`) esperam um modelo
Capella mínimo em `tests/fixtures/demo.aird` (mais os arquivos irmãos que o
Capella gera junto: `demo.capella`, `demo.afm`).

Esse fixture já existe (gerado via Capella local, GUI, projeto Enterprise
Architecture com as 5 camadas Arcadia OA/SA/LA/PA/EPBS) e está commitado
neste diretório. Os testes de integração passam contra ele, local (ver
`scripts/run_integration_tests_local.sh`) e via Docker (ver
`scripts/run_integration_tests.sh`).

Pra usar esse mesmo fixture testando o servidor MCP com um cliente/LLM
real (não só `pytest`), ver `docs/testing-with-llm.md`.

Se precisar regenerar o fixture do zero (ex. corrompeu, ou quer um
modelo maior/diferente): abrir o Capella (local ou dentro do container
via VNC/X forwarding), criar um projeto novo ("Empty Project" ou
"Enterprise Architecture"), garantir que as 5 camadas Arcadia existem
(o template padrão já cria a estrutura), e copiar os arquivos gerados
(`.aird`, `.capella`, `.afm`) pra esta pasta, renomeando a base pra
`demo`.

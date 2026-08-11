# Fixture: demo.aird

Os testes de integração (`tests/test_integration.py`) esperam um modelo
Capella mínimo em `tests/fixtures/demo.aird` (mais os arquivos irmãos que o
Capella gera junto, ex. `demo.capella`, `demo.afm` etc.).

Ainda não existe um fixture aqui — nenhum modelo de teste estava disponível
no momento do scaffolding deste projeto (ver
`docs/decisions/0002-headless-por-chamada.md`). Passos para gerar um, assim
que a imagem Docker (`docker build -t capella-mcp .`) estiver funcionando:

1. Rodar o Capella dentro do container com um servidor VNC/X forwarding (ou
   localmente, se instalado) e criar um projeto novo vazio ("Empty Project"
   ou o template "Enterprise Architecture") pelo assistente do Capella.
2. As 5 camadas Arcadia (OA/SA/LA/PA/EPBS) precisam existir no modelo — o
   template padrão do Capella já cria a estrutura.
3. Copiar os arquivos gerados (`.aird`, `.capella`, `.afm`, ...) para esta
   pasta, renomeando a base para `demo`.

Até esse fixture existir, `tests/test_integration.py` pula automaticamente
(skip), sem quebrar o restante da suíte.

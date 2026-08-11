---
title: Camadas Arcadia no Capella MCP
type: concept
tags: [arcadia, capella, mbse]
created: 2026-08-11
provenance: capella_mcp
project: capella_mcp
---

# Camadas Arcadia no Capella MCP

Definição completa da metodologia Arcadia já existe no segundo cérebro —
ver `Capella MBSE` em `docs/second_brain/20_Permanent_Notes/Capella MBSE.md`
(nota `[[Capella MBSE]]`). Este documento cobre só o que é específico deste
projeto: como as camadas mapeiam para a API do `python4capella` e para os
resources MCP expostos.

## Camadas → API python4capella

| Camada | Sigla | Método `python4capella` |
| --- | --- | --- |
| Operational Analysis | OA | `model.get_operational_analysis()` |
| System Analysis | SA | `model.get_system_analysis()` |
| Logical Architecture | LA | `model.get_logical_architecture()` |
| Physical Architecture | PA | `model.get_physical_architecture()` |
| End Product Breakdown Structure | EPBS | `model.get_e_p_b_s_architecture()` |

Todas acessadas a partir de `model.get_system_engineering()`.

## Camadas → Resources MCP

| Resource URI | Retorna |
| --- | --- |
| `capella://{model_path}/layers` | Lista as 5 camadas disponíveis no modelo |
| `capella://{model_path}/layer/{layer}` | Elementos de uma camada (`{layer}` = `oa\|sa\|la\|pa\|epbs`) |
| `capella://{model_path}/element/{element_id}` | Detalhe de um elemento específico |

`{layer}` no resource URI é o identificador curto (minúsculo); o bridge
traduz para o método `get_*` correspondente da tabela acima.

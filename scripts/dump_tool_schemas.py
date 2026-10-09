"""Gera schemas/model-tools.json a partir das tools registradas no servidor.

Fonte única de nomes, descrições e input schemas para consumidores externos
(ex.: capella_llm_window, PRD-08 fase 2). Não sobe Capella nem precisa de
CAPELLA_BIN: só importa o servidor e lista as tools via MCPServer.

    uv run python scripts/dump_tool_schemas.py          # reescreve o arquivo
    uv run python scripts/dump_tool_schemas.py --check  # falha se estiver velho
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "model-tools.json"


def build_document() -> dict:
    from capella_mcp import server

    tools = []
    for tool in asyncio.run(server.mcp.list_tools()):
        entry = {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }
        if tool.annotations is not None:
            entry["annotations"] = tool.annotations.model_dump(exclude_none=True)
        tools.append(entry)
    tools.sort(key=lambda t: t["name"])
    return {
        "schemaVersion": 1,
        "generator": "scripts/dump_tool_schemas.py",
        "tools": tools,
    }


def render(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render(build_document())
    if "--check" in argv:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text(encoding="utf-8") != text:
            print(f"{SCHEMA_PATH} desatualizado; rode: uv run python scripts/dump_tool_schemas.py")
            return 1
        return 0
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(text, encoding="utf-8")
    print(f"escreveu {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

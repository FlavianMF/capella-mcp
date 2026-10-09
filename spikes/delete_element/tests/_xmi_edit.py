"""Text edits on a car_hmi copy that mimic what a delete leaves on disk."""

from __future__ import annotations

import re
from pathlib import Path


def drop_capella_element(model_dir: Path, element_id: str) -> None:
    """What a raw EcoreUtil-style delete leaves on disk: the element is gone
    from .capella, the .aird still points at it."""
    p = model_dir / "car_hmi.capella"
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(r'<ownedFunctions[^>]*id="%s"[^>]*/>\s*' % re.escape(element_id), "", text)
    assert n == 1
    p.write_text(new, encoding="utf-8")


def drop_aird_node(model_dir: Path, element_id: str) -> None:
    """What a clean delete does to a diagram: the DNode that targets the
    element goes (GMF notation node left alone: it only references the
    DNode's uid, which we also drop, so remove that line too)."""
    p = model_dir / "car_hmi.aird"
    text = p.read_text(encoding="utf-8")
    block = re.search(
        r'\s*<ownedDiagramElements xmi:type="diagram:DNode" uid="(_[^"]+)"[^>]*>(?:(?!</ownedDiagramElements>).)*?'
        + re.escape(element_id) + r".*?</ownedDiagramElements>",
        text, re.S)
    assert block is not None
    node_uid = block.group(1)
    text = text.replace(block.group(0), "")
    # notation children whose element= is the dropped DNode or its style
    text = re.sub(r'\s*<children xmi:type="notation:Node"[^>]*element="%s">.*?\n        </children>' % node_uid,
                  "", text, flags=re.S)
    p.write_text(text, encoding="utf-8")

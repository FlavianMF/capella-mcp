"""Shared fixtures for the capellambse-backed read tests.

`rich_model` loads the car_hmi fixture and adds, in memory only (never
saved), the links the fixture itself lacks -- functional allocations, a
functional exchange, a component exchange, a physical link, capability
involvements and an activity allocation -- so relation/query code can be
tested against real capellambse objects without a Capella install.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import capellambse
import pytest

CAR_HMI = Path(__file__).parent / "fixtures" / "car_hmi" / "car_hmi.aird"


@pytest.fixture
def rich_model():
    m = capellambse.MelodyModel(str(CAR_HMI))

    lcs = m.search("LogicalComponent")
    ihm = lcs.by_name("IHM")
    fonte = lcs.by_name("Fonte de Dados do Veículo")
    lfs = m.search("LogicalFunction")
    capturar = lfs.by_name("Capturar velocidade do veículo")
    exibir = lfs.by_name("Exibir velocidade do veículo")

    fonte.allocated_functions.append(capturar)
    ihm.allocated_functions.append(exibir)

    fe = m.la.root_function.exchanges.create(
        name="velocidade",
        source=capturar.outputs.create(name="vel_out"),
        target=exibir.inputs.create(name="vel_in"),
    )
    ce = m.la.root_component.component_exchanges.create(
        name="dados de velocidade",
        source=fonte.ports.create(name="cp_out"),
        target=ihm.ports.create(name="cp_in"),
    )
    pl = m.la.root_component.physical_links.create(
        name="cabo CAN",
        ends=[fonte.physical_ports.create(name="pp1"), ihm.physical_ports.create(name="pp2")],
    )

    entities = m.search("Entity")
    motorista = entities.by_name("Motorista")
    veiculo = entities.by_name("Veículo")
    capability = m.search("OperationalCapability")[0]
    monitorar = m.search("OperationalActivity").by_name("Monitorar velocidade do veículo")
    capability.involved_entities.append(motorista)
    capability.involved_entities.append(veiculo)
    capability.involved_activities.append(monitorar)
    motorista.activities.append(monitorar)

    return SimpleNamespace(
        model=m,
        ihm=ihm,
        fonte=fonte,
        logical_system=lcs.by_name("Logical System"),
        capturar=capturar,
        exibir=exibir,
        fe=fe,
        ce=ce,
        pl=pl,
        motorista=motorista,
        veiculo=veiculo,
        capability=capability,
        monitorar=monitorar,
        root_lf=m.la.root_function,
    )

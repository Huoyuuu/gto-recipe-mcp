"""Spec §9 (v1.1) and §10.3 (v2) acceptance, runtime dump only."""
import pytest

from gto_mcp import server as s
from gto_mcp.db import DB, FILES
from gto_mcp.plan import overclock

pytestmark = pytest.mark.skipif(not FILES["runtime"][0].exists(), reason="needs runtime dump")
FRONT = ["fluoro_carbon_lanthanide_cerium_solution47", "steam_cracked_fluoro_carbon_lanthanide_slurry48",
         "mixer/fluoro_carbon_lanthanide_cerium_solution49", "diluted_fluoro_carbon_lanthanide_slurry51",
         "filtered_fluoro_carbon_lanthanide_slurry52", "calcined_rare_earth_oxide_powder55",
         "samarium_rare_earth_concentrate_powder61", "fluorinated_samarium_concentrate_powder62"]


@pytest.fixture(autouse=True)
def runtime():
    s.db = DB("runtime")
    yield
    s.db = None


def test_overclock():
    assert overclock(1920, 200, "IV") == (7680, 100, 1)
    assert overclock(1920, 200, "IV", perfect=True) == (7680, 50, 1)
    assert overclock(7680, 400, "EV") == (7680, 400, 0)


def test_plan_first_step():
    p = s._plan(s._feed({"dust:Bastnasite": 1}), s._steps(["fluoro_carbon_lanthanide_cerium_solution47"]), "IV")
    st = p["steps"][0]
    assert st["runs"] == pytest.approx(0.5) and st["ticks"] == 100 and st["machines"] == pytest.approx(2.5)
    assert st["machine"] == "煮解池" and p["need"]["fluid:gtceu:nitric_acid"] == pytest.approx(200)


def test_plan_fluorite_loop():
    steps = FRONT + ["samarium_terbium_mixture_powder63", "decomposition_electrolyzing_fluorite",
                     {"id": "hydrofluoric_acid_from_elements", "mode": "make", "target": "氢氟酸"}]
    p = s._plan(s._feed({"dust:Bastnasite": 1}), s._steps(steps), "IV")
    assert "fluid:gtceu:hydrofluoric_acid" not in p["need"] and not p["warnings"]
    assert p["need"].get("item:gtceu:calcium_dust", 0) < 1e-6
    assert "净外部需求" in s.plan({"dust:Bastnasite": 1}, steps)


def test_v11_fixes():
    assert s.resolve_one("dust:TerbiumNitratePowder")[0]
    assert "自己下单" in s.machine("me_requestable_input_hatch_machine")
    na = s.producers("钠粉")
    assert "cosmos_simulation" not in na and "隐藏" in na
    assert "chemical_plant" in s.machines_for("chemical_reactor")
    assert s.mcp._tool_manager.get_tool("plan").fn_metadata.output_schema is None

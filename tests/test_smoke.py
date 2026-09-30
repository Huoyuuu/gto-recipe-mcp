"""Spec §6 acceptance cases, run against both sources."""
import pytest

from gto_mcp import server as s
from gto_mcp.db import DB, FILES

MODES = ["static"] + (["runtime"] if FILES["runtime"][0].exists() else [])


@pytest.fixture(params=MODES)
def mode(request):
    s.db = DB(request.param)
    yield request.param
    s.db = None


def test_spec_cases(mode):
    out = s.consumers("dust:Bastnasite")
    assert "fluoro_carbon_lanthanide_cerium_solution47" in out and "1920EU/t" in out
    ms = s.machines_for("gtceu:dissolution_treatment")
    assert "dissolving_tank" in ms and "dissolution_core" in ms
    p = s.producers("dust:SodiumOxide")
    assert "sodium_oxide_dust" in p and "3×" in p
    b = s.balance([{"fluoro_carbon_lanthanide_cerium_solution47": 0.5}]).split("净产出")[0]
    assert "200mB" in b and "1×" in b
    m = s.search("稀土离心", kind="machine")
    assert "rare_earth_centrifugal" in m and "comprehensive_tombarthite_processing_facility" in m


def test_runtime_only(mode):
    f, la = s.consumers("dust:Fluorite"), s.consumers("dust:LanthanumChloride")
    if mode == "static":
        assert "共 0 条" in f and "运行时导出" in f and "共 0 条" in la
    else:
        assert "decomposition_electrolyzing_fluorite" in f and "钙粉" in f and "2000mB 气态氟" in f
        assert "decomposition_electrolyzing_lanthanum_chloride" in la and "镧粉" in la


def test_boost_formula():
    from gto_mcp.balance import boosted
    assert boosted(5000, 1000, 2, 4) == 7000     # +2 tiers
    assert boosted(5000, 1000, 0, 2) == 6000     # ULV recipe loses one tier
    assert boosted(9500, 1000, 1, 5) == 10000    # capped


def test_name_resolution_and_cap(mode):
    assert s.resolve_one("dust:Bastnasite")[0]
    assert len(s.consumers("dust:Iron", limit=500).encode()) < 4600

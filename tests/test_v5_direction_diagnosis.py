"""Test diagnostic hypotheses with synthetic reports; never run live modules."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.v5 import ensemble  # noqa: E402
from src.v5.contract import ModuleReport, insufficient  # noqa: E402


@pytest.fixture(autouse=True)
def no_state_readers(monkeypatch):
    monkeypatch.setattr(ensemble, "_benched", lambda: set())
    monkeypatch.setattr(ensemble, "_multipliers", lambda: {})
    monkeypatch.setattr(ensemble, "_family_size", lambda family: 1)
    monkeypatch.setattr(ensemble, "_version", lambda: 1)


@pytest.mark.parametrize("net,direction", [(-10.01, "bear"), (-10, "neutral"), (0, "neutral"), (10, "neutral"), (10.01, "bull")])
def test_direction_band_is_symmetric_and_a_negative_net_is_not_automatically_bear(net, direction):
    report = ModuleReport("fixture", "price", "TEST", bull=(80 + net) / 2, bear=(80 - net) / 2,
                          neutral=20, ci_low=0.4, ci_high=0.6)
    result = ensemble.synthesise([report], "TEST")
    assert result.direction == direction


@pytest.mark.parametrize("p,expected", [(0.2, "bear"), (0.8, "bull")])
def test_wide_intervals_withhold_both_directions_through_neutral_mass(p, expected):
    def module(ci):
        return ModuleReport.from_probability("fixture", "price", "TEST", p, ci,
                                             thesis="Synthetic diagnostic", evidence=[], weaknesses=[])
    tight = ensemble.synthesise([module((p - 0.05, p + 0.05))], "TEST")
    wide = ensemble.synthesise([module((0, 1))], "TEST")
    assert tight.direction == expected
    assert wide.direction == "neutral"
    assert abs(wide.net_score) == 9


def test_low_confidence_is_not_a_directional_bear_veto():
    report = ModuleReport.from_probability("fixture", "price", "TEST", 0.05, (0, 0.85),
                                           thesis="Synthetic bearish view", evidence=[], weaknesses=[])
    missing = [insufficient(f"missing-{i}", "price", "TEST", "Synthetic absent input") for i in range(8)]
    result = ensemble.synthesise([report, *missing], "TEST")
    assert result.direction == "bear"
    assert result.net_score == -13.5
    assert 0.5 < result.confidence < 0.55

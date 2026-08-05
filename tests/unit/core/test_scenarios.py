from __future__ import annotations

from stealth_prompt.core.contracts import Objective
from stealth_prompt.core.scenarios import SCENARIOS, objective_catalog, scenario_for


def test_every_objective_has_one_product_scenario() -> None:
    assert set(SCENARIOS) == set(Objective)

    for objective in Objective:
        scenario = scenario_for(objective)
        assert scenario.title
        assert scenario.description
        assert scenario.guidance
        assert scenario.standards
        assert scenario.remediation


def test_catalog_is_safe_public_metadata_without_generation_guidance() -> None:
    catalog = objective_catalog()

    assert {entry["id"] for entry in catalog} == {objective.value for objective in Objective}
    assert all("guidance" not in entry for entry in catalog)

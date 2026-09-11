"""Where a scheduled build writes, asserted against the workflow file itself.

The cutover moves the live target out of `build-week.yml` and into a repo
variable, `SCHEDULED_TARGET`. That buys a rollback that is a dropdown rather
than a commit -- but it means four separate expressions have to agree about
which environment a run belongs to, and GitHub will not complain if they
drift.

Three of the four decide behaviour. `concurrency.group` and the job's
`environment:` disagreeing is the failure worth having a test for: the group
is what makes a manual staging run and the production cron mutually
exclusive, so if it says `staging` while `environment:` says `production`,
two builds interleave writes into one week's rows. That corrupts data rather
than erroring, which is why it gets a test and not a code review.

The fourth is the payload artifact's name. It is cosmetic -- a mislabelled
download -- but a production run uploading `payload-staging` is misleading in
exactly the window where the runbook has someone inspecting payloads by hand,
so it moves with the others.
"""

from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = (
    pathlib.Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "build-week.yml"
)


@pytest.fixture(scope="module")
def raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(raw: str) -> dict:
    # `on:` parses as the boolean True under YAML 1.1, which is why the
    # dispatch inputs are reached through that key rather than the string.
    return yaml.safe_load(raw)


def _build_step(workflow: dict, name: str) -> dict:
    for step in workflow["jobs"]["build"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r}")


def test_every_target_expression_is_the_same_string(raw: str):
    """The one failure here that corrupts rows instead of raising."""
    expressions = re.findall(r"\$\{\{\s*(inputs\.environment[^}]*?)\s*\}\}", raw)

    assert len(expressions) == 4, f"expected 4 target expressions, found {expressions}"
    assert len(set(expressions)) == 1, f"target expressions have drifted: {set(expressions)}"


def test_the_target_expression_consults_the_repo_variable(raw: str):
    """Without `vars.SCHEDULED_TARGET` the cron can never leave staging."""
    expressions = set(re.findall(r"\$\{\{\s*(inputs\.environment[^}]*?)\s*\}\}", raw))

    assert expressions == {"inputs.environment || vars.SCHEDULED_TARGET || 'staging'"}


def test_an_unset_variable_still_falls_back_to_staging(raw: str):
    """The edit stays inert until step 12 of the runbook sets the variable."""
    expression = re.search(r"\$\{\{\s*(inputs\.environment[^}]*?)\s*\}\}", raw).group(1)

    assert expression.endswith("|| 'staging'")


def test_production_takes_live_odds_on_a_schedule(workflow: dict):
    script = _build_step(workflow, "Build and push")["run"]

    assert 'if [ "$TARGET" = "production" ]; then MODE=live;' in script


def test_the_scheduled_fallback_is_not_a_fixture(workflow: dict):
    """A staging cron re-enabled later must degrade, not die on a missing file.

    `--replay-odds` resolves its fixture path from season and week, so a
    fallback of `replay` starts failing the hour Week 2 opens.
    """
    script = _build_step(workflow, "Build and push")["run"]
    fallback = re.search(r'then MODE=live; else MODE=(\w+); fi', script)

    assert fallback is not None, "the odds-mode fallback block has moved"
    assert fallback.group(1) == "nflverse"


def test_the_dispatch_odds_default_is_not_a_fixture(workflow: dict):
    """After the week rolls over, `replay` names a fixture nobody recorded."""
    odds = workflow[True]["workflow_dispatch"]["inputs"]["odds"]

    assert odds["default"] != "replay"
    assert odds["default"] == "nflverse"


def test_replay_is_still_selectable_by_hand(workflow: dict):
    """Reproducing a Week 1 result exactly is still worth being able to do."""
    odds = workflow[True]["workflow_dispatch"]["inputs"]["odds"]

    assert "replay" in odds["options"]


def test_cron_enabled_remains_the_master_switch(raw: str):
    """Two knobs, one job each: CRON_ENABLED stops everything, the variable steers."""
    assert "vars.CRON_ENABLED == 'true'" in raw

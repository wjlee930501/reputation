"""Build a machine-readable index from the plan's canonical QA scenarios."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Final, Literal, TypedDict

_TASK: Final = re.compile(r"^- \[ \] (\d+)\.")
_SCENARIO: Final = re.compile(r"^\s*Scenario:\s*(.+)$")
_FIELD: Final = re.compile(r"^\s*(Tool|Steps|Expected|Evidence):\s*(.*)$")


class ScenarioRecord(TypedDict):
    id: str
    task: int
    name: str
    command_or_channel: str
    steps: str
    expected_http_or_state: str
    evidence_path: str
    cleanup_receipt: str


ScenarioTextField = Literal[
    "command_or_channel", "steps", "expected_http_or_state", "evidence_path"
]
_FIELD_NAMES: Final[dict[str, ScenarioTextField]] = {
    "Tool": "command_or_channel",
    "Steps": "steps",
    "Expected": "expected_http_or_state",
    "Evidence": "evidence_path",
}


def scenario_index(plan_text: str) -> list[ScenarioRecord]:
    current_task: int | None = None
    ordinal_by_task: dict[int, int] = {}
    scenarios: list[ScenarioRecord] = []
    current: ScenarioRecord | None = None
    active_field: ScenarioTextField | None = None

    for line in plan_text.splitlines():
        task_match = _TASK.match(line)
        if task_match:
            current_task = int(task_match.group(1))
            current = None
            active_field = None
            continue
        scenario_match = _SCENARIO.match(line)
        if scenario_match and current_task is not None and current_task <= 26:
            ordinal = ordinal_by_task.get(current_task, 0) + 1
            ordinal_by_task[current_task] = ordinal
            current = {
                "id": f"task-{current_task:02d}-scenario-{ordinal:02d}",
                "task": current_task,
                "name": scenario_match.group(1).strip(),
                "command_or_channel": "",
                "steps": "",
                "expected_http_or_state": "",
                "evidence_path": "",
                "cleanup_receipt": f"task-{current_task:02d}-cleanup.json",
            }
            scenarios.append(current)
            active_field = None
            continue
        field_match = _FIELD.match(line)
        if current is not None and field_match:
            key = _FIELD_NAMES[field_match.group(1)]
            current[key] = field_match.group(2).strip()
            active_field = key
            continue
        if current is not None and active_field and line.startswith("    ") and line.strip() != "```":
            current[active_field] = f"{current[active_field]} {line.strip()}".strip()

    return scenarios


def write_scenario_index(plan_path: Path, output_path: Path) -> list[ScenarioRecord]:
    scenarios = scenario_index(plan_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"scenario_count": len(scenarios), "scenarios": scenarios}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    scenarios = write_scenario_index(args.plan, args.output)
    print(json.dumps({"scenario_count": len(scenarios), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

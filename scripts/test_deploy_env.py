import json

import pytest

from scripts.deploy_env import diff, main, plain_env, read_dotenv, render

_DROP = frozenset({"SERVICE", "APP_ENV", "REPUTATION_RELEASE_REVISION"})


def _service(*env: dict) -> dict:
    return {"spec": {"template": {"spec": {"containers": [{"env": list(env)}]}}}}


def test_plain_env_keeps_values_and_drops_secrets_and_per_release_keys() -> None:
    service = _service(
        {"name": "CLAUDE_MODEL", "value": "anthropic/claude-sonnet-5.5"},
        {"name": "SENTRY_DSN"},
        {"name": "SLACK_WEBHOOK_URL", "valueFrom": {"secretKeyRef": {"name": "SLACK_WEBHOOK_URL", "key": "latest"}}},
        {"name": "SERVICE", "value": "api"},
        {"name": "REPUTATION_RELEASE_REVISION", "value": "old-sha"},
    )
    assert plain_env(service, _DROP) == {"CLAUDE_MODEL": "anthropic/claude-sonnet-5.5", "SENTRY_DSN": ""}


def test_plain_env_rejects_a_service_without_containers() -> None:
    with pytest.raises(ValueError):
        plain_env({"spec": {"template": {"spec": {}}}}, _DROP)


def test_render_round_trips_through_read_dotenv() -> None:
    env = {"B": "Re:putation <noreply@x.test>", "A": "1"}
    assert render(env) == "A=1\nB=Re:putation <noreply@x.test>\n"
    assert read_dotenv(render(env)) == env


def test_render_refuses_multiline_values() -> None:
    with pytest.raises(ValueError):
        render({"A": "line1\nline2"})


def test_read_dotenv_matches_deploy_sh_parsing() -> None:
    text = '# comment\n\nexport A="quoted"\nB=\'single\'\nC=\nD=a=b\n'
    assert read_dotenv(text) == {"A": "quoted", "B": "single", "C": "", "D": "a=b"}


def test_diff_reports_names_only_and_ignores_empty_and_listed_keys() -> None:
    local = {"A": "1", "B": "new", "C": "", "SECRET": "x", "LOCAL_ONLY": "v"}
    live = {"A": "1", "B": "old", "LIVE_ONLY": "v", "SECRET": "y"}
    assert diff(local, live, frozenset({"SECRET"})) == [
        "added: LOCAL_ONLY",
        "removed: LIVE_ONLY",
        "changed: B",
    ]
    assert diff({"A": "1", "C": ""}, {"A": "1"}, frozenset()) == []


def test_cli_render_then_diff(tmp_path, capsys) -> None:
    service_json = tmp_path / "service.json"
    service_json.write_text(json.dumps(_service({"name": "A", "value": "1"}, {"name": "SERVICE", "value": "api"})))
    live = tmp_path / "live.env"
    assert main(["render", "--service-json", str(service_json), "--out", str(live),
                 "--drop", "SERVICE", "APP_ENV", "REPUTATION_RELEASE_REVISION"]) == 0
    assert live.read_text() == "A=1\n"

    local = tmp_path / "local.env"
    local.write_text("A=2\n")
    assert main(["diff", "--local", str(local), "--live", str(live)]) == 1
    assert "changed: A" in capsys.readouterr().out
    local.write_text("A=1\n")
    assert main(["diff", "--local", str(local), "--live", str(live)]) == 0

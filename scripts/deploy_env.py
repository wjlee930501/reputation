#!/usr/bin/env python3
"""배포 env 정본 — 운영 Cloud Run 서비스의 현재 env를 배포 입력(dotenv)으로 옮기고 로컬 파일과 비교한다.

`scripts/deploy.sh`가 부른다. `--env-vars-file` 배포는 서비스 env를 통째로 바꾸므로, 작업 PC의 오래된
`.env.production`이 운영 값을 덮지 않도록 운영 env를 정본으로 쓴다. 차이는 **키 이름과 종류만** 출력한다.

  render  --service-json FILE|- --out FILE --drop KEY...   운영 서비스 JSON → dotenv
  diff    --local FILE --live FILE --ignore KEY...           0 = 같다, 1 = 다르다(목록 출력)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def plain_env(service: dict, drop: frozenset[str]) -> dict[str, str]:
    """비밀이 아닌 env(`value`)만. 시크릿 참조(`valueFrom`)와 배포가 매번 넣는 키는 뺀다."""
    template = ((service.get("spec") or {}).get("template") or {}).get("spec") or {}
    containers = template.get("containers") or []
    if not containers:
        raise ValueError("service has no containers")
    env: dict[str, str] = {}
    for item in containers[0].get("env") or []:
        name = str(item.get("name") or "")
        if not name or name in drop or "valueFrom" in item:
            continue
        env[name] = str(item.get("value") or "")
    return env


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_dotenv(text: str) -> dict[str, str]:
    """deploy.sh(`prepare_non_secret_env_file`, `read_env_file_value`)와 같은 규칙으로 읽는다."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = raw[len("export "):] if raw.startswith("export ") else raw
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = _unquote(value)
    return values


def render(env: dict[str, str]) -> str:
    for key, value in env.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"multiline env value is not supported: {key}")
    return "".join(f"{key}={value}\n" for key, value in sorted(env.items()))


def diff(local: dict[str, str], live: dict[str, str], ignore: frozenset[str]) -> list[str]:
    """배포하면 바뀌는 키. deploy.sh는 빈 값을 주입하지 않으므로 빈 값은 '없음'과 같다."""

    def effective(values: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in values.items() if key not in ignore and value != ""}

    a, b = effective(local), effective(live)
    lines = [f"added: {key}" for key in sorted(a.keys() - b.keys())]
    lines += [f"removed: {key}" for key in sorted(b.keys() - a.keys())]
    lines += [f"changed: {key}" for key in sorted(k for k in a.keys() & b.keys() if a[k] != b[k])]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    render_cmd = sub.add_parser("render")
    render_cmd.add_argument("--service-json", required=True)
    render_cmd.add_argument("--out", required=True)
    render_cmd.add_argument("--drop", nargs="*", default=[])
    diff_cmd = sub.add_parser("diff")
    diff_cmd.add_argument("--local", required=True)
    diff_cmd.add_argument("--live", required=True)
    diff_cmd.add_argument("--ignore", nargs="*", default=[])
    args = parser.parse_args(argv)

    if args.command == "render":
        raw = sys.stdin.read() if args.service_json == "-" else Path(args.service_json).read_text()
        try:
            env = plain_env(json.loads(raw), frozenset(args.drop))
            text = render(env)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"deploy_env: {exc}", file=sys.stderr)
            return 2
        Path(args.out).write_text(text, encoding="utf-8")
        return 0

    changes = diff(
        read_dotenv(Path(args.local).read_text(encoding="utf-8")),
        read_dotenv(Path(args.live).read_text(encoding="utf-8")),
        frozenset(args.ignore),
    )
    for line in changes:
        print(line)
    return 1 if changes else 0


if __name__ == "__main__":
    raise SystemExit(main())

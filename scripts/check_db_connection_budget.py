#!/usr/bin/env python3
"""Cloud SQL 연결 예산 불변식 검증기 (배포 preflight / CI 가드).

worst-case 동시 연결 합계가 Cloud SQL max_connections의 80%를 넘지 않는지 확인한다.
config.py 풀 기본값, terraform 인스턴스 수/CELERY_CONCURRENCY, cloudsql.tf max_connections를
소스에서 직접 파싱하므로, 어느 한쪽만 바꾸면 여기서 잡힌다.

불변식:
    api    = api_max_instances × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
    worker = worker_max_instances × CELERY_CONCURRENCY
             × (DB_WORKER_POOL_SIZE + DB_WORKER_MAX_OVERFLOW)
    api + worker ≤ max_connections × 0.8

위반 시 stderr에 상세를 찍고 exit 1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = PROJECT_ROOT / "backend" / "app" / "core" / "config.py"
VARIABLES_TF = PROJECT_ROOT / "terraform" / "variables.tf"
CLOUDSQL_TF = PROJECT_ROOT / "terraform" / "cloudsql.tf"
CLOUDRUN_TF = PROJECT_ROOT / "terraform" / "cloudrun.tf"

# max_connections 대비 사용할 수 있는 안전 비율 (나머지 20%는 운영/롤아웃/유지보수 여유).
BUDGET_HEADROOM_RATIO = 0.8


class BudgetError(Exception):
    """예산 소스 파싱 실패 또는 불변식 위반."""


def _parse_int_setting(text: str, name: str) -> int:
    """config.py의 `NAME: int = 123` 기본값을 파싱."""
    match = re.search(
        rf"^\s*{re.escape(name)}\s*:\s*int\s*=\s*(\d+)", text, re.MULTILINE
    )
    if not match:
        raise BudgetError(f"config.py에서 {name} 기본값을 찾지 못했습니다.")
    return int(match.group(1))


def _parse_tf_variable_default(text: str, variable: str) -> int:
    """terraform variables.tf의 `variable "name" { ... default = N ... }`를 파싱."""
    header = f'variable "{variable}"'
    start = text.find(header)
    if start == -1:
        raise BudgetError(f'variables.tf에서 variable "{variable}"를 찾지 못했습니다.')
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                block = text[start : index + 1]
                match = re.search(r"default\s*=\s*(\d+)", block)
                if not match:
                    raise BudgetError(
                        f'variable "{variable}"에 default 정수가 없습니다.'
                    )
                return int(match.group(1))
    raise BudgetError(f'variable "{variable}" 블록이 닫히지 않았습니다.')


def _parse_max_connections(text: str) -> int:
    """cloudsql.tf의 max_connections database_flag 값을 파싱."""
    match = re.search(r'name\s*=\s*"max_connections"\s*\n\s*value\s*=\s*"(\d+)"', text)
    if not match:
        raise BudgetError("cloudsql.tf에서 max_connections 값을 찾지 못했습니다.")
    return int(match.group(1))


def _parse_celery_concurrency(text: str) -> int:
    """cloudrun.tf worker 서비스의 CELERY_CONCURRENCY env 값을 파싱."""
    match = re.search(
        r'name\s*=\s*"CELERY_CONCURRENCY"\s*\n\s*value\s*=\s*"(\d+)"', text
    )
    if not match:
        raise BudgetError("cloudrun.tf에서 CELERY_CONCURRENCY 값을 찾지 못했습니다.")
    return int(match.group(1))


def compute_budget() -> dict[str, int]:
    """소스에서 파싱한 값으로 연결 예산을 계산해 dict로 반환."""
    config_text = CONFIG_PY.read_text()
    variables_text = VARIABLES_TF.read_text()
    cloudsql_text = CLOUDSQL_TF.read_text()
    cloudrun_text = CLOUDRUN_TF.read_text()

    api_pool = _parse_int_setting(config_text, "DB_POOL_SIZE")
    api_overflow = _parse_int_setting(config_text, "DB_MAX_OVERFLOW")
    worker_pool = _parse_int_setting(config_text, "DB_WORKER_POOL_SIZE")
    worker_overflow = _parse_int_setting(config_text, "DB_WORKER_MAX_OVERFLOW")

    api_max_instances = _parse_tf_variable_default(variables_text, "api_max_instances")
    worker_max_instances = _parse_tf_variable_default(
        variables_text, "worker_max_instances"
    )
    celery_concurrency = _parse_celery_concurrency(cloudrun_text)
    max_connections = _parse_max_connections(cloudsql_text)

    api_conns = api_max_instances * (api_pool + api_overflow)
    worker_conns = (
        worker_max_instances * celery_concurrency * (worker_pool + worker_overflow)
    )
    total = api_conns + worker_conns
    limit = int(max_connections * BUDGET_HEADROOM_RATIO)

    return {
        "api_max_instances": api_max_instances,
        "api_pool": api_pool,
        "api_overflow": api_overflow,
        "api_conns": api_conns,
        "worker_max_instances": worker_max_instances,
        "celery_concurrency": celery_concurrency,
        "worker_pool": worker_pool,
        "worker_overflow": worker_overflow,
        "worker_conns": worker_conns,
        "total": total,
        "max_connections": max_connections,
        "limit": limit,
    }


def main() -> int:
    try:
        b = compute_budget()
    except BudgetError as exc:
        print(f"✗ DB 연결 예산 검증 실패: {exc}", file=sys.stderr)
        return 1

    summary = (
        f"API    : {b['api_max_instances']} inst × "
        f"({b['api_pool']}+{b['api_overflow']}) = {b['api_conns']}\n"
        f"Worker : {b['worker_max_instances']} inst × {b['celery_concurrency']} conc × "
        f"({b['worker_pool']}+{b['worker_overflow']}) = {b['worker_conns']}\n"
        f"합계    : {b['total']} / 한도 {b['limit']} "
        f"(max_connections {b['max_connections']} × {BUDGET_HEADROOM_RATIO})"
    )

    if b["total"] > b["limit"]:
        print(
            "✗ DB 연결 예산 초과 — 인스턴스/풀/concurrency를 낮추거나 "
            "max_connections 상향(또는 pgbouncer 도입)이 필요합니다.\n" + summary,
            file=sys.stderr,
        )
        return 1

    print("✓ DB 연결 예산 통과\n" + summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

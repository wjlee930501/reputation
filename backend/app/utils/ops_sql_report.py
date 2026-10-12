"""읽기 전용 운영 조회: 자율 운영 지표(Q1~Q6)를 JSON 한 줄씩 출력한다.

새 세션의 트랜잭션에서 첫 문장으로 `SET TRANSACTION READ ONLY`를 실행해 DB 수준에서 쓰기를 막고,
끝나면 롤백한다. 받은 세션에 이미 열린 트랜잭션이 있으면 먼저 롤백해 새로 시작한다::

    python -m app.utils.ops_sql_report                          # 전부
    python -m app.utils.ops_sql_report --only=q1,q3             # 고른 것만(쉼표 목록)
    python -m app.utils.ops_sql_report --only=q1 --only=q3      # 반복도 된다

운영에서는 VPC 안의 migrate Job으로 돌린다. gcloud `--args`는 쉼표로 인자를 나누므로 쿼리마다
`--only`를 따로 넘긴다::

    gcloud run jobs execute reputation-migrate --wait \
      --args=python,-m,app.utils.ops_sql_report,--only=q1,--only=q3

출력: 쿼리마다 `{"q": "q1", "rows": [...]}` 한 줄. 보조 쿼리는 `q1b`·`q3b`로 같은 이름 아래 나온다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from sqlalchemy import text

# 이름 → 순서대로 실행할 SELECT. 두 번째 이후 문장은 이름 뒤에 b, c…를 붙여 출력한다.
QUERIES: dict[str, tuple[str, ...]] = {
    # 열린 사고 유형 × 상태, 나이. 보조: 기한 없는 RETRYING 목록.
    "q1": (
        """
        SELECT incident_type, state, count(*) AS n,
               min(now()-first_seen_at) AS min_age, avg(now()-first_seen_at) AS avg_age,
               max(now()-first_seen_at) AS max_age,
               count(*) FILTER (WHERE state='RETRYING' AND sla_due_at IS NULL) AS retrying_no_sla
        FROM incidents
        WHERE state IN ('OPEN','RETRYING')
        GROUP BY incident_type, state
        ORDER BY n DESC, incident_type
        """,
        """
        SELECT id, incident_type, hospital_id, first_seen_at, last_seen_at, safe_error_code
        FROM incidents
        WHERE state='RETRYING' AND sla_due_at IS NULL
        ORDER BY first_seen_at
        """,
    ),
    # 최근 14일 알림 종류 × 상태. 라벨은 `notification_labels.EVENT_LABELS`로 후처리한다.
    "q2": (
        """
        SELECT notification_type, channel, state, count(*) AS n,
               min(created_at) AS first_at, max(created_at) AS last_at,
               count(*) FILTER (WHERE state IN ('HOLD','FAILED')) AS stuck,
               max(attempt_count) AS max_attempts,
               array_agg(DISTINCT safe_error_code)
                 FILTER (WHERE safe_error_code IS NOT NULL) AS error_codes
        FROM notification_outbox
        WHERE created_at >= now() - interval '14 days'
        GROUP BY notification_type, channel, state
        ORDER BY notification_type, state
        """,
    ),
    # 작업 실행 유형 × 상태, 나이. 보조: 24시간 넘은 RUNNING 목록.
    "q3": (
        """
        SELECT operation_type, state, count(*) AS n,
               min(now()-requested_at) AS min_age, avg(now()-requested_at) AS avg_age,
               max(now()-requested_at) AS max_age
        FROM operation_runs
        GROUP BY operation_type, state
        ORDER BY operation_type, state
        """,
        """
        SELECT id, operation_type, hospital_id, requested_at, started_at, heartbeat_at,
               lease_expires_at, attempt_count, task_id
        FROM operation_runs
        WHERE state='RUNNING' AND coalesce(started_at, requested_at) < now() - interval '24 hours'
        ORDER BY coalesce(started_at, requested_at)
        """,
    ),
    # 최신 생성 시도가 OPERATOR_REQUIRED / INPUT_CHANGE_REQUIRED인 글, 원인 코드별.
    "q4": (
        """
        SELECT essence_check_summary->'generation_attempt'->>'retry_class' AS retry_class,
               essence_check_summary->'generation_attempt'->>'reason' AS reason,
               status, count(*) AS n, count(DISTINCT hospital_id) AS hospitals,
               min(essence_check_summary->'generation_attempt'->>'first_observed_at')
                 AS oldest_first_observed,
               max(essence_check_summary->'generation_attempt'->>'observed_at')
                 AS latest_observed
        FROM content_items
        WHERE essence_check_summary->'generation_attempt'->>'retry_class'
                IN ('OPERATOR_REQUIRED','INPUT_CHANGE_REQUIRED')
          AND status NOT IN ('PUBLISHED','CANCELLED','WITHHELD')
        GROUP BY 1, 2, 3
        ORDER BY n DESC
        """,
    ),
    # ACTIVE 병원 중 일정 미설정이거나 발행 0건.
    "q5": (
        """
        SELECT h.id, h.name, h.status, h.profile_complete, h.site_built, h.schedule_set,
               count(c.id) FILTER (WHERE c.first_published_at IS NOT NULL OR c.status='PUBLISHED')
                 AS published_total,
               count(c.id) FILTER (WHERE c.status IN ('DRAFT','READY')) AS pending_slots
        FROM hospitals h
        LEFT JOIN content_items c ON c.hospital_id = h.id
        WHERE h.status='ACTIVE'
        GROUP BY h.id
        HAVING h.schedule_set = false
            OR count(c.id) FILTER (
                 WHERE c.first_published_at IS NOT NULL OR c.status='PUBLISHED'
               ) = 0
        ORDER BY h.name
        """,
    ),
    # 주제 교체된 슬롯과 최신 시도(교체 뒤인지). 시도 기록은 글당 한 조각이라 '교체 직후 첫
    # 시도'는 복원할 수 없다 — 최신 시도가 교체 이후인지까지만 안다.
    "q6": (
        """
        SELECT c.id AS content_id, c.hospital_id, c.status, c.scheduled_date, e.ord AS swap_no,
               e.entry->>'swapped_at' AS swapped_at, e.entry->>'reason_code' AS swap_reason,
               e.entry->>'from_title' AS from_title,
               c.essence_check_summary->'generation_attempt'->>'reason' AS latest_attempt_reason,
               c.essence_check_summary->'generation_attempt'->>'retry_class'
                 AS latest_retry_class,
               c.essence_check_summary->'generation_attempt'->>'observed_at'
                 AS latest_observed_at,
               (c.essence_check_summary->'generation_attempt'->>'observed_at')::timestamptz
                 > (e.entry->>'swapped_at')::timestamptz AS attempt_is_after_swap
        FROM content_items c
        CROSS JOIN LATERAL jsonb_array_elements(c.topic_swap_history)
          WITH ORDINALITY AS e(entry, ord)
        WHERE jsonb_typeof(c.topic_swap_history)='array'
          AND jsonb_array_length(c.topic_swap_history) > 0
        ORDER BY (e.entry->>'swapped_at') DESC
        """,
    ),
}


def _labels(name: str, count: int) -> list[str]:
    return [name if index == 0 else f"{name}{chr(ord('a') + index)}" for index in range(count)]


def run_report(db: Any, names: Sequence[str], *, out: TextIO = sys.stdout) -> None:
    """고른 쿼리를 읽기 전용 트랜잭션 하나에서 실행하고 언제나 롤백한다."""

    # SET TRANSACTION은 트랜잭션의 첫 문장이어야 한다. 세션이 이미 무언가를 실행했다면 그
    # 트랜잭션을 버리고 새로 시작한다.
    db.rollback()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        for name in names:
            statements = QUERIES[name]
            for label, sql in zip(_labels(name, len(statements)), statements, strict=True):
                rows = db.execute(text(sql)).mappings().all()
                print(
                    json.dumps(
                        {"q": label, "rows": [dict(row) for row in rows]},
                        default=str,
                        ensure_ascii=False,
                    ),
                    file=out,
                    flush=True,
                )
    finally:
        db.rollback()


def _query_names(raw: str) -> list[str]:
    names = [name.strip() for name in raw.split(",") if name.strip()]
    unknown = [name for name in names if name not in QUERIES]
    if unknown or not names:
        raise argparse.ArgumentTypeError(
            f"알 수 없는 쿼리: {', '.join(unknown) or raw!r} (가능: {', '.join(QUERIES)})"
        )
    return names


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.utils.ops_sql_report",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--only",
        type=_query_names,
        action="append",
        default=None,
        help="쿼리 이름. 반복하거나 쉼표로 묶는다(--only=q1 --only=q3, --only=q1,q3). 생략하면 전부.",
    )
    args = parser.parse_args(argv)
    if args.only is None:
        args.only = list(QUERIES)
    else:
        # 반복과 쉼표 목록을 한 줄로 펴고, 같은 쿼리는 처음 나온 자리에서 한 번만 돈다.
        args.only = list(dict.fromkeys(name for names in args.only for name in names))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from app.core.database import SyncSessionLocal

    with SyncSessionLocal() as db:
        run_report(db, args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

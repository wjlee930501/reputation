"""예약 작업(Beat 항목) 하나를 지금 바로 한 번 보낸다.

배포로 고친 예약 작업을 다음 예약 시각(예: 매일 03:10)까지 기다리지 않고 즉시 적용할 때 쓴다.
Beat와 같은 작업 이름·인자·큐·서명 헤더로 큐에 넣기만 하므로 실행 경로와 서명 검증, 작업 자체의
멱등·상한·비용 가드는 예약 실행과 똑같다. 운영 DB가 사설 IP라 `reputation-migrate` Job을 실행
단위 override로 돌린다:

    gcloud run jobs execute reputation-migrate --project mso-platform-481505 \
      --region asia-northeast3 --wait \
      --args=python,-m,app.utils.run_beat_entry,post-publish-ai-review
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from app.core.celery_app import celery_app


def send_now(name: str) -> str:
    entry = celery_app.conf.beat_schedule[name]
    result = celery_app.send_task(
        entry["task"],
        args=tuple(entry.get("args") or ()),
        kwargs=dict(entry.get("kwargs") or {}),
        **dict(entry.get("options") or {}),
    )
    return str(result.id)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    known = sorted(celery_app.conf.beat_schedule)
    if len(args) != 1 or args[0] not in celery_app.conf.beat_schedule:
        raise SystemExit("사용법: run_beat_entry <예약 항목 이름>\n가능한 이름: " + ", ".join(known))
    task_id = send_now(args[0])
    print(f"sent entry={args[0]} task_id={task_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

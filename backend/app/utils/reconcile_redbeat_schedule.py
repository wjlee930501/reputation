"""배포 이미지의 Celery Beat 선언과 Redis RedBeat 상태를 명시적으로 동기화한다.

RedBeat 자체도 이전 ``redbeat::statics`` 항목을 정리하지만, 과거에 동적으로 저장됐거나
statics 집합에서 이탈한 항목은 남을 수 있다. 이 도구는 Re:putation 소유 태스크만
allowlist 방식으로 검사/삭제하며 다른 애플리케이션의 동적 RedBeat 항목은 보존한다.

사용법::

    python -m app.utils.reconcile_redbeat_schedule --check
    python -m app.utils.reconcile_redbeat_schedule --apply

``--check``는 stale/orphan/version drift가 있으면 exit 2, ``--apply``는 정리 후 현재
스케줄 버전을 기록하고 다시 검사한다. 배포 스크립트는 새 이미지로 --apply Job을 실행한다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from redbeat.schedulers import RedBeatSchedulerEntry, ensure_conf, get_redis

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app

APP_TASK_PREFIX = "app.workers.tasks."


@dataclass(frozen=True)
class ScheduleInspection:
    desired_names: tuple[str, ...]
    stale_keys: tuple[str, ...]
    orphan_keys: tuple[str, ...]
    stale_static_names: tuple[str, ...]
    stored_version: str | None
    expected_version: str

    @property
    def clean(self) -> bool:
        return not (
            self.stale_keys
            or self.orphan_keys
            or self.stale_static_names
            or self.stored_version != self.expected_version
        )


def _version_key(app: Any = celery_app) -> str:
    ensure_conf(app)
    return f"{app.redbeat_conf.key_prefix}:reputation:schedule-version"


def inspect_schedule(client: Any, app: Any = celery_app) -> ScheduleInspection:
    """현재 Redis 상태를 읽어 앱 소유 stale/orphan 항목과 버전 drift를 분류한다."""
    ensure_conf(app)
    desired_names = frozenset(app.conf.beat_schedule.keys())
    schedule_key = app.redbeat_conf.schedule_key
    statics_key = app.redbeat_conf.statics_key

    stale_keys: set[str] = set()
    orphan_keys: set[str] = set()

    for raw_key in client.zrange(schedule_key, 0, -1):
        key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
        try:
            entry = RedBeatSchedulerEntry.from_key(key, app=app)
        except (KeyError, TypeError, ValueError):
            # zset에는 있으나 본문 hash가 없는 손상된 인덱스는 어떤 태스크도 실행할 수 없다.
            orphan_keys.add(key)
            continue

        if entry.name not in desired_names and str(entry.task).startswith(APP_TASK_PREFIX):
            stale_keys.add(key)

    raw_statics = client.smembers(statics_key)
    static_names = {
        value.decode() if isinstance(value, bytes) else str(value) for value in raw_statics
    }
    stale_static_names = static_names.difference(desired_names)

    stored_version = client.get(_version_key(app))
    if isinstance(stored_version, bytes):
        stored_version = stored_version.decode()

    return ScheduleInspection(
        desired_names=tuple(sorted(desired_names)),
        stale_keys=tuple(sorted(stale_keys)),
        orphan_keys=tuple(sorted(orphan_keys)),
        stale_static_names=tuple(sorted(stale_static_names)),
        stored_version=str(stored_version) if stored_version is not None else None,
        expected_version=REDBEAT_SCHEDULE_VERSION,
    )


def apply_reconciliation(client: Any, app: Any = celery_app) -> ScheduleInspection:
    """검사된 앱 소유 stale 상태만 삭제하고 현재 명시적 버전을 기록한다."""
    ensure_conf(app)
    inspection = inspect_schedule(client, app)
    schedule_key = app.redbeat_conf.schedule_key
    statics_key = app.redbeat_conf.statics_key

    keys_to_delete = set(inspection.stale_keys)
    for name in inspection.stale_static_names:
        keys_to_delete.add(RedBeatSchedulerEntry.generate_key(app, name))

    for key in sorted(keys_to_delete):
        try:
            RedBeatSchedulerEntry.from_key(key, app=app).delete()
        except (KeyError, TypeError, ValueError):
            client.zrem(schedule_key, key)
            client.delete(key)

    for key in inspection.orphan_keys:
        client.zrem(schedule_key, key)
        client.delete(key)

    if inspection.stale_static_names:
        client.srem(statics_key, *inspection.stale_static_names)

    client.set(_version_key(app), REDBEAT_SCHEDULE_VERSION)
    return inspect_schedule(client, app)


def _render(inspection: ScheduleInspection) -> str:
    payload = asdict(inspection)
    payload["clean"] = inspection.clean
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="drift를 읽기 전용으로 검사")
    mode.add_argument(
        "--apply", action="store_true", help="앱 소유 stale 상태를 정리하고 버전 기록"
    )
    args = parser.parse_args(argv)

    client = get_redis(celery_app)
    inspection = (
        apply_reconciliation(client, celery_app)
        if args.apply
        else inspect_schedule(client, celery_app)
    )
    print(_render(inspection))
    return 0 if inspection.clean else 2


if __name__ == "__main__":
    raise SystemExit(main())

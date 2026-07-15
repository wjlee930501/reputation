from dataclasses import dataclass

from app.core.celery_app import REDBEAT_SCHEDULE_VERSION, celery_app
from app.utils import reconcile_redbeat_schedule as reconcile


class FakeClient:
    def __init__(self):
        self.schedule = {
            "redbeat:nightly-content-generation",
            "redbeat:legacy-overdue-notification",
            "redbeat:orphan",
            "redbeat:foreign-dynamic",
        }
        self.statics = {"nightly-content-generation", "legacy-overdue-notification"}
        self.values = {reconcile._version_key(celery_app): "old-version"}
        self.deleted: set[str] = set()

    def zrange(self, _key, _start, _end):
        return sorted(self.schedule)

    def smembers(self, _key):
        return set(self.statics)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def zrem(self, _key, key):
        self.schedule.discard(key)

    def delete(self, key):
        self.deleted.add(key)

    def srem(self, _key, *names):
        self.statics.difference_update(names)


@dataclass
class FakeEntry:
    name: str
    task: str
    key: str
    client: FakeClient

    def delete(self):
        self.client.schedule.discard(self.key)
        self.client.deleted.add(self.key)


def _entry_loader(client: FakeClient):
    entries = {
        "redbeat:nightly-content-generation": FakeEntry(
            "nightly-content-generation",
            "app.workers.tasks.nightly_content_generation",
            "redbeat:nightly-content-generation",
            client,
        ),
        "redbeat:legacy-overdue-notification": FakeEntry(
            "legacy-overdue-notification",
            "app.workers.tasks.overdue_content_notification",
            "redbeat:legacy-overdue-notification",
            client,
        ),
        "redbeat:foreign-dynamic": FakeEntry(
            "foreign-dynamic",
            "other.application.periodic_task",
            "redbeat:foreign-dynamic",
            client,
        ),
    }

    def load(key, app=None):
        del app
        if key not in entries or key not in client.schedule:
            raise KeyError(key)
        return entries[key]

    return load


def test_inspection_reports_version_stale_app_entry_and_orphan(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(reconcile.RedBeatSchedulerEntry, "from_key", _entry_loader(client))

    result = reconcile.inspect_schedule(client)

    assert result.clean is False
    assert result.stale_keys == ("redbeat:legacy-overdue-notification",)
    assert result.orphan_keys == ("redbeat:orphan",)
    assert result.stale_static_names == ("legacy-overdue-notification",)
    assert "redbeat:foreign-dynamic" not in result.stale_keys


def test_apply_removes_only_app_owned_stale_state_and_records_version(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(reconcile.RedBeatSchedulerEntry, "from_key", _entry_loader(client))

    result = reconcile.apply_reconciliation(client)

    assert result.clean is True
    assert client.values[reconcile._version_key(celery_app)] == REDBEAT_SCHEDULE_VERSION
    assert "redbeat:legacy-overdue-notification" not in client.schedule
    assert "redbeat:orphan" not in client.schedule
    assert "redbeat:foreign-dynamic" in client.schedule
    assert "nightly-content-generation" in client.statics

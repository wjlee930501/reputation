"""P2-9b — revalidate 경로 커버리지(treatment pillar + 루트 llms.txt) + post-commit 안전 강등."""
import pytest

from app.services import site_revalidate
from app.services.site_revalidation_control import RevalidationRetryPlan


def test_hospital_site_paths_include_root_llms_and_treatment_pillars():
    treatments = [
        {"name": "허리디스크 치료", "description": "비수술 우선"},
        {"name": "도수치료"},
    ]

    paths = site_revalidate.hospital_site_paths("test-clinic", treatments)

    assert "/llms.txt" in paths  # 루트 llms.txt (P2-9b)
    assert "/test-clinic/llms.txt" in paths
    assert "/test-clinic/treatments" in paths
    assert "/test-clinic/treatments/허리디스크-치료" in paths
    assert "/test-clinic/treatments/도수치료" in paths
    # Next.js dynamic segment는 percent-encoded 캐시 키로도 잡힐 수 있음
    assert any(p.startswith("/test-clinic/treatments/%") for p in paths)


def test_hospital_site_paths_without_treatments_keeps_legacy_shape():
    paths = site_revalidate.hospital_site_paths("test-clinic")

    assert "/" in paths  # custom-domain root cache
    assert "/test-clinic" in paths
    assert "/sitemap.xml" in paths
    assert not any("/treatments/" in p for p in paths)


def test_build_treatment_slug_matches_site_rules():
    assert site_revalidate.build_treatment_slug("  허리디스크   치료 ") == "허리디스크-치료"
    assert site_revalidate.build_treatment_slug("A/B?C#D") == "a-b-c-d"
    assert site_revalidate.build_treatment_slug(None) == ""


async def test_trigger_content_site_revalidate_safe_never_raises(monkeypatch):
    """발행 커밋 이후 실패는 발행을 되돌리지 않고 내구성 있는 재시도로 넘긴다."""
    scheduled = []

    async def boom(*, paths):
        raise RuntimeError("revalidate endpoint down")

    async def fake_start(slug, content_id):
        assert slug == "test-clinic"
        return RevalidationRetryPlan(content_id, 60, False, True)

    def fake_send_task(name, *, args, queue, countdown):
        scheduled.append((name, args, queue, countdown))

    monkeypatch.setattr(site_revalidate, "trigger_site_revalidate", boom)
    monkeypatch.setattr(site_revalidate, "start_revalidation_failure", fake_start)
    from app.core.celery_app import celery_app

    monkeypatch.setattr(celery_app, "send_task", fake_send_task)

    ok = await site_revalidate.trigger_content_site_revalidate_safe(
        "test-clinic", "f5aa8f49-fc76-46b6-b6d5-d372dad2522a", hospital_name="테스트의원"
    )

    assert ok is False
    assert scheduled == [
        (
            "app.workers.tasks.retry_site_revalidation",
            ["f5aa8f49-fc76-46b6-b6d5-d372dad2522a"],
            "default",
            60,
        )
    ]


async def test_trigger_content_site_revalidate_safe_returns_true_on_success(monkeypatch):
    async def fine(*, paths):
        assert "/test-clinic/contents/content-1" in paths
        return True

    monkeypatch.setattr(site_revalidate, "trigger_site_revalidate", fine)

    assert await site_revalidate.trigger_content_site_revalidate_safe("test-clinic", "content-1") is True


@pytest.mark.parametrize("path", ["", "no-slash", None])
def test_normalize_paths_drops_invalid(path):
    assert site_revalidate._normalize_paths([path, "/ok"]) == ["/ok"]


def test_publication_revalidation_has_bounded_operator_escalation_schedule():
    assert site_revalidate.REVALIDATION_RETRY_DELAYS_SECONDS == (60, 300, 900)

"""P2-9b — revalidate 경로 커버리지(treatment pillar + 루트 llms.txt) + post-commit 안전 강등."""
import pytest

from app.services import site_revalidate


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
    """발행 커밋 이후 revalidate 실패는 500 대신 경고+Slack 운영 알림 (P2-9b)."""
    alerts = []

    async def boom(*, paths):
        raise RuntimeError("revalidate endpoint down")

    async def fake_ops_alert(*, title, message):
        alerts.append({"title": title, "message": message})
        return True

    from app.services import notifier

    monkeypatch.setattr(site_revalidate, "trigger_site_revalidate", boom)
    monkeypatch.setattr(notifier, "notify_ops_alert", fake_ops_alert)

    ok = await site_revalidate.trigger_content_site_revalidate_safe(
        "test-clinic", "content-1", hospital_name="테스트의원"
    )

    assert ok is False
    assert len(alerts) == 1
    assert "캐시 무효화 실패" in alerts[0]["title"]
    assert "테스트의원" in alerts[0]["message"]


async def test_trigger_content_site_revalidate_safe_returns_true_on_success(monkeypatch):
    async def fine(*, paths):
        assert "/test-clinic/contents/content-1" in paths
        return True

    monkeypatch.setattr(site_revalidate, "trigger_site_revalidate", fine)

    assert await site_revalidate.trigger_content_site_revalidate_safe("test-clinic", "content-1") is True


@pytest.mark.parametrize("path", ["", "no-slash", None])
def test_normalize_paths_drops_invalid(path):
    assert site_revalidate._normalize_paths([path, "/ok"]) == ["/ok"]

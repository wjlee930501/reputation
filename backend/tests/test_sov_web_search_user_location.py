"""측정 검색의 대략적 위치(`SOV_WEB_SEARCH_USER_LOCATION_COUNTRY`)는 켤 때만 조건이 바뀐다."""

from app.core.config import settings
from app.services import openrouter, sov_engine


def test_off_by_default_keeps_the_tool_and_policy_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "SOV_WEB_SEARCH_USER_LOCATION_COUNTRY", "")

    assert openrouter.measurement_web_search_tool() == openrouter.WEB_SEARCH_TOOL
    protocol = sov_engine.measurement_protocol()
    assert "web_search_user_location" not in protocol
    # 끈 상태의 지문은 이 키가 없던 시절과 같은 재료로 만든다.
    legacy_keys = [
        key
        for key in sov_engine._execution_policy_keys_for_platform("chatgpt")
        if key != "web_search_user_location"
    ]
    import hashlib

    legacy = hashlib.sha256(
        "|".join(f"{key}={protocol[key]}" for key in legacy_keys).encode()
    ).hexdigest()[:16]
    assert sov_engine.protocol_fingerprint(platform="chatgpt") == legacy


def test_country_setting_adds_location_and_breaks_comparability(monkeypatch):
    monkeypatch.setattr(settings, "SOV_WEB_SEARCH_USER_LOCATION_COUNTRY", "")
    before = sov_engine.measurement_protocol()
    before_fingerprint = sov_engine.protocol_fingerprint(platform="gemini")

    monkeypatch.setattr(settings, "SOV_WEB_SEARCH_USER_LOCATION_COUNTRY", " kr ")
    monkeypatch.setattr(settings, "SOV_WEB_SEARCH_USER_LOCATION_TIMEZONE", "Asia/Seoul")

    assert openrouter.measurement_web_search_tool() == {
        "type": "openrouter:web_search",
        "parameters": {
            "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"}
        },
    }
    after = sov_engine.measurement_protocol()
    assert after["web_search_user_location"] == "country=KR|timezone=Asia/Seoul|type=approximate"
    assert sov_engine.protocol_fingerprint(platform="gemini") != before_fingerprint
    assert not sov_engine.same_execution_policy(before, after, platforms=("chatgpt", "gemini"))
    assert sov_engine.same_execution_policy(after, dict(after), platforms=("chatgpt",))
    # 위치를 켜도 모듈 상수는 그대로다(다른 호출부가 공유한다).
    assert openrouter.WEB_SEARCH_TOOL == {"type": "openrouter:web_search"}

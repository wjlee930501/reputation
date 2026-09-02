"""프로파일 자동 채우기 — 네이버 플레이스 스크래퍼 + 추출 오케스트레이터 단위/통합 테스트.

네트워크/LLM 경계를 monkeypatch로 막아 hermetic하게 검증한다.
"""
import json
import types

from app.services import hospital_profile_autofill as af
from app.services import naver_place


# ── naver_place ──────────────────────────────────────────────────
def test_place_id_regex_extracts_first_match():
    sample = (
        "x [예약](https://m.place.naver.com/hospital/1595672233?entry=ple) "
        "y https://m.place.naver.com/hospital/999"
    )
    match = naver_place._PLACE_ID_RE.search(sample)
    assert match is not None
    assert match.group(1) == "1595672233"


async def test_scrape_naver_place_success(monkeypatch):
    async def fake_jina(url: str):
        if "list?query" in url:
            return ("[a](https://m.place.naver.com/hospital/1595672233)", None)
        return ("=== 홈 ===\n수원시 팔달구 · 031-8067-8114", None)

    monkeypatch.setattr(naver_place, "fetch_via_jina", fake_jina)
    res = await naver_place.scrape_naver_place("장편한외과의원")
    assert res.place_id == "1595672233"
    assert res.markdown
    assert res.reason is None


async def test_scrape_naver_place_not_found(monkeypatch):
    async def fake_jina(url: str):
        return ("검색 결과 없음", None)

    monkeypatch.setattr(naver_place, "fetch_via_jina", fake_jina)
    res = await naver_place.scrape_naver_place("없는병원")
    assert res.place_id is None
    assert res.markdown == ""
    assert res.reason


# ── 순수 변환 로직 ────────────────────────────────────────────────
def test_clamp_confidence():
    assert af._clamp_confidence(1.5) == 1.0
    assert af._clamp_confidence(-1) == 0.0
    assert af._clamp_confidence("nope") == 0.5
    assert af._clamp_confidence(0.7) == 0.7


def test_normalize_fields_filters_disallowed_empty_and_clamps():
    fields = {
        "director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9},
        "address": {"value": "수원", "source": "naver", "confidence": 2.0},
        "bogus_field": {"value": "x", "source": "naver", "confidence": 0.5},  # 허용목록 밖
        "keywords": {"value": [], "source": "blog", "confidence": 0.5},       # 빈 값
        "phone": {"source": "naver", "confidence": 0.9},                      # value 없음
    }
    draft, meta, rejected = af._normalize_fields(fields)
    assert set(draft) == {"director_name", "address"}
    assert meta["address"]["confidence"] == 1.0
    assert meta["director_name"]["source"] == "homepage"
    assert rejected == []


def test_collect_violations_flags_forbidden_fields():
    draft = {
        "director_career": "국내 최초, 완치율 100%",
        "director_philosophy": "환자 중심으로 진료합니다",  # clean
        "keywords": ["최고", "치질"],
        "treatments": [{"name": "치질", "description": "부작용 없는 시술"}],
    }
    flagged = {v["field"] for v in af._collect_violations(draft)}
    assert "director_career" in flagged
    assert "keywords" in flagged
    assert "treatments" in flagged
    assert "director_philosophy" not in flagged


# ── autofill_profile 통합 ─────────────────────────────────────────
def _fake_claude_response(fields: dict):
    payload = json.dumps({"fields": fields}, ensure_ascii=False)
    return types.SimpleNamespace(content=[types.SimpleNamespace(text=payload)])


async def test_autofill_profile_happy_path(monkeypatch):
    async def fake_fetch_url_text(url: str):
        return "", "직접 fetch 실패", None  # Jina 폴백 강제 (3-튜플: text, error, quality)

    async def fake_jina(url: str):
        return "김원장 완치율 최고 보장 수원시 팔달구", None

    async def fake_naver(name: str):
        return naver_place.NaverPlaceResult("1595672233", "네이버 본문", None)

    monkeypatch.setattr(af, "fetch_url_text", fake_fetch_url_text)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", fake_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)

    fields = {
        "director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9, "evidence": "김원장"},
        "address": {"value": "수원시 팔달구", "source": "naver", "confidence": 0.95, "evidence": "수원시 팔달구"},
        "director_career": {"value": "완치율 최고 보장", "source": "homepage", "confidence": 0.6, "evidence": "완치율 최고 보장"},
    }
    monkeypatch.setattr(
        af._client.messages, "create", lambda **kwargs: _fake_claude_response(fields)
    )

    res = await af.autofill_profile("장편한외과의원", "http://hp", "http://blog")

    assert res.naver_place_id == "1595672233"
    assert res.draft["director_name"] == "김원장"
    assert res.draft["address"] == "수원시 팔달구"
    # place_id는 스크랩 값으로 직접 주입(신뢰도 0.95)
    assert res.draft["naver_place_id"] == "1595672233"
    assert res.draft["naver_place_url"] == "https://m.place.naver.com/hospital/1595672233/home"
    assert res.field_meta["naver_place_id"]["source"] == "naver"
    # 의료광고 위반 플래그
    assert any(v["field"] == "director_career" for v in res.violations)
    # 3개 소스 모두 ok (홈/블로그는 jina 폴백, 네이버 직접)
    assert all(s.ok for s in res.sources)


async def test_autofill_profile_all_sources_fail_returns_empty(monkeypatch):
    async def fail_fetch(url: str):
        return "", "fail", None

    async def fail_jina(url: str):
        return "", "fail"

    async def fail_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", fail_fetch)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", fail_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fail_naver)

    # Claude는 호출되면 안 되지만, 호출돼도 빈 결과를 보장.
    called = {"n": 0}

    def _should_not_call(**kwargs):
        called["n"] += 1
        return _fake_claude_response({})

    monkeypatch.setattr(af._client.messages, "create", _should_not_call)

    res = await af.autofill_profile("없는병원", "http://hp", None)
    assert res.draft == {}
    assert res.violations == []
    assert all(not s.ok for s in res.sources)
    assert called["n"] == 0  # 소스가 없으면 LLM 호출 생략


async def test_autofill_profile_skips_llm_when_no_sources(monkeypatch):
    async def fail_fetch(url: str):
        return "", "fail", None

    async def fail_jina(url: str):
        return "", "fail"

    async def fail_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", fail_fetch)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", fail_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fail_naver)

    res = await af.autofill_profile("x", None, None)
    assert res.draft == {}
    assert res.naver_place_id is None


async def test_autofill_profile_direct_fetch_unpacks_three_tuple(monkeypatch):
    """fetch_url_text의 실제 반환(text, error, quality) 3-튜플을 그대로 언패킹해야 한다.

    회귀 방지: 과거 2-튜플로 언패킹해 website_url/blog_url 입력 시 항상 ValueError → 500이었음.
    Jina 폴백을 타지 않고 직접 fetch가 성공하는 경로를 강제한다.
    """
    async def ok_fetch(url: str):
        return f"홈페이지 본문 김원장 for {url}", None, None  # 실제 시그니처: (text, error, quality)

    async def _should_not_jina(url: str):
        raise AssertionError("직접 fetch 성공 시 Jina 폴백을 타면 안 된다")

    async def fake_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", _should_not_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)

    fields = {"director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9, "evidence": "김원장"}}
    monkeypatch.setattr(
        af._client.messages, "create", lambda **kwargs: _fake_claude_response(fields)
    )

    res = await af.autofill_profile("장편한외과의원", "http://hp", "http://blog")

    assert res.draft["director_name"] == "김원장"
    # 홈페이지·블로그 소스는 직접 fetch로 ok, 네이버만 실패
    assert [s.ok for s in res.sources] == [True, True, False]


async def test_autofill_rejects_ungrounded_specialty_and_hours(monkeypatch):
    async def ok_fetch(url: str):
        return "내과 진료 월요일 09:00-18:00", None, None

    async def fake_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    fields = {
        "specialties": {"value": ["소아청소년과"], "source": "homepage", "confidence": 0.9, "evidence": "내과 진료"},
        "business_hours": {"value": {"mon": "09:00-20:00"}, "source": "homepage", "confidence": 0.9, "evidence": "월요일 09:00-18:00"},
    }
    monkeypatch.setattr(af._client.messages, "create", lambda **kwargs: _fake_claude_response(fields))

    res = await af.autofill_profile("병원", "http://hp", None)

    assert "specialties" not in res.draft
    assert "business_hours" not in res.draft
    assert {item["field"] for item in res.rejected_fields} == {"specialties", "business_hours"}


def test_business_hours_cannot_borrow_late_time_from_another_day():
    evidence = "월요일 09:00-18:00 화요일 09:00-20:00"
    fields = {
        "business_hours": {
            "value": {"mon": "09:00-20:00"},
            "source": "homepage",
            "confidence": 0.9,
            "evidence": evidence,
        }
    }

    draft, _meta, rejected = af._normalize_fields(fields, evidence)

    assert "business_hours" not in draft
    assert rejected == [
        {"field": "business_hours", "reason": "제안 값이 출처 원문과 일치하지 않습니다."}
    ]


async def test_autofill_endpoint_end_to_end_with_real_signature(monkeypatch):
    """엔드포인트가 실제 fetch_url_text 시그니처로 저장 없이 초안을 반환한다."""
    from app.api.admin import hospitals as hospitals_api

    class _FakeDB:
        def __init__(self, hospital):
            self.hospital = hospital
            self.added = []
            self.committed = False

        async def get(self, model, object_id):
            return self.hospital if self.hospital.id == object_id else None

        def add(self, item):
            self.added.append(item)

        async def commit(self):
            self.committed = True

    async def ok_fetch(url: str):
        return f"본문 김원장 {url}", None, None

    async def fake_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    fields = {"director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9, "evidence": "김원장"}}
    monkeypatch.setattr(
        af._client.messages, "create", lambda **kwargs: _fake_claude_response(fields)
    )

    import uuid as _uuid
    from types import SimpleNamespace

    hospital = SimpleNamespace(
        id=_uuid.uuid4(), name="장편한외과의원", website_url="http://hp", blog_url=None
    )
    db = _FakeDB(hospital)
    body = hospitals_api.ProfileAutofillRequest(name=None, website_url=None, blog_url=None)

    response = await hospitals_api.autofill_hospital_profile(hospital.id, body, db=db)

    assert response["draft"]["director_name"] == "김원장"
    # autofill 감사 로그가 기록되고 커밋됐다.
    assert db.committed is True
    assert db.added and db.added[0].action == "autofill_profile"


# ── 모델 선택 / 재시도 필터 ───────────────────────────────────────
def test_autofill_uses_the_fast_model_by_default(monkeypatch):
    """구조화 추출 + 결정론적 grounding 검증이라 Sonnet급 추론을 살 이유가 없다."""
    monkeypatch.setattr(af.settings, "AUTOFILL_MODEL", "")
    monkeypatch.setattr(af.settings, "CLAUDE_MODEL_FAST", "fast-model")
    monkeypatch.setattr(af.settings, "CLAUDE_MODEL", "expensive-model")

    assert af._autofill_model() == "fast-model"


def test_autofill_model_is_revertible_by_config(monkeypatch):
    monkeypatch.setattr(af.settings, "AUTOFILL_MODEL", "expensive-model")
    monkeypatch.setattr(af.settings, "CLAUDE_MODEL_FAST", "fast-model")

    assert af._autofill_model() == "expensive-model"


async def test_autofill_request_sends_the_configured_model(monkeypatch):
    async def ok_fetch(url: str):
        return "김원장", None, None

    async def fake_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "없음")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    monkeypatch.setattr(af.settings, "AUTOFILL_MODEL", "")
    monkeypatch.setattr(af.settings, "CLAUDE_MODEL_FAST", "fast-model")

    captured: dict = {}

    def create(**kwargs):
        captured.update(kwargs)
        fields = {
            "director_name": {
                "value": "김원장",
                "source": "homepage",
                "confidence": 0.9,
                "evidence": "김원장",
            }
        }
        return _fake_claude_response(fields)

    monkeypatch.setattr(af._client.messages, "create", create)

    await af.autofill_profile("장편한외과의원", "http://hp", None)

    assert captured["model"] == "fast-model"


async def test_autofill_does_not_retry_deterministic_client_error(monkeypatch):
    """결정적 4xx를 3회 재시도하면 유료 호출만 3배가 된다."""
    import anthropic
    import httpx

    async def ok_fetch(url: str):
        return "김원장", None, None

    async def fake_naver(name: str):
        return naver_place.NaverPlaceResult(None, "", "없음")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)

    http_response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    calls = {"n": 0}

    def create(**_kwargs):
        calls["n"] += 1
        raise anthropic.BadRequestError("bad request", response=http_response, body=None)

    monkeypatch.setattr(af._client.messages, "create", create)

    result = await af.autofill_profile("장편한외과의원", "http://hp", None)

    assert calls["n"] == 1
    assert result.draft == {}

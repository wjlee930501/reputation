"""프로파일 자동 채우기 — 네이버 플레이스 스크래퍼 + 추출 오케스트레이터 단위/통합 테스트.

네트워크/LLM 경계를 monkeypatch로 막아 hermetic하게 검증한다.
"""
import json
import types

import pytest

from app.services import hospital_profile_autofill as af
from app.services import naver_place, provider_usage


@pytest.fixture(autouse=True)
def _capture_provider_attempts(monkeypatch):
    attempts = []

    async def capture(**kwargs):
        attempts.append(kwargs)
        return True

    monkeypatch.setattr(provider_usage, "record_attempt", capture)
    return attempts


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
        return ("=== 홈 ===\n장편한외과의원\n수원시 팔달구 · 031-8067-8114", None)

    monkeypatch.setattr(naver_place, "fetch_via_jina", fake_jina)
    res = await naver_place.scrape_naver_place("장편한외과의원", regions=["수원시 팔달구"])
    assert res.place_id == "1595672233"
    assert res.markdown
    assert res.reason is None
    assert res.identity == naver_place.IDENTITY_VERIFIED_SEARCH and res.verified


async def test_stored_place_id_skips_the_search(monkeypatch):
    urls = []

    async def fake_jina(url: str):
        urls.append(url)
        return ("다른 상호 본문", None)

    monkeypatch.setattr(naver_place, "fetch_via_jina", fake_jina)
    res = await naver_place.scrape_naver_place("장편한외과의원", place_id="1595672233")
    assert res.identity == naver_place.IDENTITY_STORED_ID and res.verified
    assert not any("list?query" in url for url in urls)


@pytest.mark.parametrize(
    ("detail", "regions"),
    [
        ("다온의원 세종점\n세종시 보람동", []),  # 지점명이 붙은 다른 상호
        ("세종다온의원\n대전 서구", ["세종시"]),  # 같은 상호, 다른 지역
    ],
)
async def test_search_hit_that_is_not_this_hospital_stays_unverified(monkeypatch, detail, regions):
    async def fake_jina(url: str):
        if "list?query" in url:
            return ("[a](https://m.place.naver.com/hospital/1234567)", None)
        return (detail, None)

    monkeypatch.setattr(naver_place, "fetch_via_jina", fake_jina)
    res = await naver_place.scrape_naver_place("세종다온의원", regions=regions)
    assert res.identity == naver_place.IDENTITY_UNVERIFIED and not res.verified
    assert res.markdown  # 초안에는 남는다


@pytest.mark.parametrize(
    ("detail", "name", "regions"),
    [
        # 리뷰 재현: 지점명 붙은 다른 상호 + 다른 줄의 '서울' — 본문 부분 문자열로는 맞아 보였다.
        ("다온의원 세종점 서울에서 오시는 길", "다온의원", ["서울"]),
        # 상호는 같지만 지역이 주소가 아니라 안내 문장에만 있다.
        ("Title: 다온의원 : 네이버\n주소 대전 서구 둔산로 1\n서울에서 오시는 길", "다온의원", ["서울"]),
        # 병원 행에 지역이 없다 — 저장 id도 없으니 확인할 수 없다.
        ("Title: 다온의원 : 네이버\n주소 서울 성동구 왕십리로 1", "다온의원", []),
        # 주소를 읽지 못했다.
        ("Title: 다온의원 : 네이버\n진료과목 내과", "다온의원", ["서울"]),
    ],
)
async def test_search_identity_needs_exact_name_and_region_in_the_address(
    monkeypatch, detail, name, regions
):
    async def fake_jina(url: str):
        if "list?query" in url:
            return ("[a](https://m.place.naver.com/hospital/1234567)", None)
        return (detail, None)

    monkeypatch.setattr(naver_place, "fetch_via_jina", fake_jina)
    res = await naver_place.scrape_naver_place(name, regions=regions)
    assert res.identity == naver_place.IDENTITY_UNVERIFIED and not res.verified


def test_place_identity_reads_title_and_address_separately():
    markdown = (
        "=== 네이버 플레이스 (홈) ===\nTitle: 세종다온신경과의원 : 네이버\n\n"
        "URL Source: https://m.place.naver.com/hospital/1/home\n\nMarkdown Content:\n"
        "세종다온신경과의원\n==========\n신경과\n주소\n세종 보람동 호려울로 1 2층\n"
    )
    assert naver_place.parse_place_identity(markdown) == (
        "세종다온신경과의원",
        "세종 보람동 호려울로 1 2층",
    )
    # 지역 태그의 행정 단위 표기 차이("세종시" ↔ "세종")는 같은 지역이다.
    assert naver_place.place_matches_hospital(markdown, "세종다온 신경과의원", ["세종시"])
    assert not naver_place.place_matches_hospital(markdown, "세종다온신경과의원", ["대전"])
    # 지점명은 병원 행의 이름에 있을 때만 맞는다.
    branch = "Title: 다온의원 세종점 : 네이버\n주소 세종특별자치시 보람동 1"
    assert naver_place.place_matches_hospital(branch, "다온의원 세종점", ["세종시"])
    assert not naver_place.place_matches_hospital(branch, "다온의원", ["세종시"])


def test_stored_place_id_reads_the_url_when_the_id_column_is_empty():
    assert naver_place.stored_place_id(None, "https://m.place.naver.com/hospital/1595672233/home") == "1595672233"
    assert naver_place.stored_place_id("98765", None) == "98765"
    assert naver_place.stored_place_id(None, "https://naver.me/xyz") is None


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
    return types.SimpleNamespace(
        id="msg-autofill-test",
        usage=types.SimpleNamespace(input_tokens=11, output_tokens=3),
        content=[types.SimpleNamespace(text=payload)],
    )


async def test_autofill_profile_happy_path(monkeypatch):
    async def fake_fetch_url_text(url: str):
        return "", "직접 fetch 실패", None  # Jina 폴백 강제 (3-튜플: text, error, quality)

    async def fake_jina(url: str):
        return "김원장 완치율 최고 보장 수원시 팔달구", None

    async def fake_naver(name: str, **_kwargs):
        return naver_place.NaverPlaceResult(
            "1595672233", "네이버 본문 수원시 팔달구", None, naver_place.IDENTITY_VERIFIED_SEARCH
        )

    monkeypatch.setattr(af, "fetch_url_text", fake_fetch_url_text)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", fake_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)

    fields = {
        "director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9, "evidence": "김원장"},
        "address": {"value": "수원시 팔달구", "source": "naver", "confidence": 0.95, "evidence": "수원시 팔달구"},
        "director_career": {"value": "완치율 최고 보장", "source": "homepage", "confidence": 0.6, "evidence": "완치율 최고 보장"},
    }
    monkeypatch.setattr(
        af._client.chat.completions, "create", lambda **kwargs: _fake_claude_response(fields)
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

    async def fail_naver(name: str, **_kwargs):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", fail_fetch)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", fail_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fail_naver)

    # Claude는 호출되면 안 되지만, 호출돼도 빈 결과를 보장.
    called = {"n": 0}

    def _should_not_call(**kwargs):
        called["n"] += 1
        return _fake_claude_response({})

    monkeypatch.setattr(af._client.chat.completions, "create", _should_not_call)

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

    async def fail_naver(name: str, **_kwargs):
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

    async def fake_naver(name: str, **_kwargs):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", _should_not_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)

    fields = {"director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9, "evidence": "김원장"}}
    monkeypatch.setattr(
        af._client.chat.completions, "create", lambda **kwargs: _fake_claude_response(fields)
    )

    res = await af.autofill_profile("장편한외과의원", "http://hp", "http://blog")

    assert res.draft["director_name"] == "김원장"
    # 홈페이지·블로그 소스는 직접 fetch로 ok, 네이버만 실패
    assert [s.ok for s in res.sources] == [True, True, False]


async def test_autofill_rejects_ungrounded_specialty_and_hours(monkeypatch):
    async def ok_fetch(url: str):
        return "내과 진료 월요일 09:00-18:00", None, None

    async def fake_naver(name: str, **_kwargs):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    fields = {
        "specialties": {"value": ["소아청소년과"], "source": "homepage", "confidence": 0.9, "evidence": "내과 진료"},
        "business_hours": {"value": {"mon": "09:00-20:00"}, "source": "homepage", "confidence": 0.9, "evidence": "월요일 09:00-18:00"},
    }
    monkeypatch.setattr(af._client.chat.completions, "create", lambda **kwargs: _fake_claude_response(fields))

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

    async def fake_naver(name: str, **_kwargs):
        return naver_place.NaverPlaceResult(None, "", "찾지 못함")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    fields = {"director_name": {"value": "김원장", "source": "homepage", "confidence": 0.9, "evidence": "김원장"}}
    monkeypatch.setattr(
        af._client.chat.completions, "create", lambda **kwargs: _fake_claude_response(fields)
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

    async def fake_naver(name: str, **_kwargs):
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

    monkeypatch.setattr(af._client.chat.completions, "create", create)

    await af.autofill_profile("장편한외과의원", "http://hp", None)

    assert captured["model"] == "fast-model"


async def test_autofill_does_not_retry_deterministic_client_error(monkeypatch):
    """결정적 4xx를 3회 재시도하면 유료 호출만 3배가 된다."""
    import httpx
    import openai

    async def ok_fetch(url: str):
        return "김원장", None, None

    async def fake_naver(name: str, **_kwargs):
        return naver_place.NaverPlaceResult(None, "", "없음")

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)

    http_response = httpx.Response(
        400, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    )
    calls = {"n": 0}

    def create(**_kwargs):
        calls["n"] += 1
        raise openai.BadRequestError("bad request", response=http_response, body=None)

    monkeypatch.setattr(af._client.chat.completions, "create", create)

    result = await af.autofill_profile("장편한외과의원", "http://hp", None)

    assert calls["n"] == 1
    assert result.draft == {}


async def test_autofill_records_each_http_retry_with_one_logical_identity(
    monkeypatch, _capture_provider_attempts
):
    import httpx
    import openai
    from tenacity import wait_none

    async def ok_fetch(_url: str):
        return "김원장", None, None

    async def fake_naver(_name: str, **_kwargs):
        return naver_place.NaverPlaceResult(None, "", "없음")

    async def no_cost_counter(*_args, **_kwargs):
        return None

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    monkeypatch.setattr("app.services.cost_guard.record_provider_call", no_cost_counter)
    monkeypatch.setattr(af._extract_with_claude.retry, "wait", wait_none())

    http_response = httpx.Response(
        429, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    )
    calls = 0

    def create(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise openai.RateLimitError(
                "retry", response=http_response, body=None
            )
        return _fake_claude_response(
            {
                "director_name": {
                    "value": "김원장",
                    "source": "homepage",
                    "confidence": 0.9,
                    "evidence": "김원장",
                }
            }
        )

    monkeypatch.setattr(af._client.chat.completions, "create", create)
    hospital_id = "00000000-0000-0000-0000-000000000123"

    result = await af.autofill_profile(
        "장편한외과의원", "http://hp", None, hospital_id=hospital_id
    )

    assert result.draft["director_name"] == "김원장"
    assert [event["http_attempt"] for event in _capture_provider_attempts] == [1, 2]
    assert len({event["logical_call_id"] for event in _capture_provider_attempts}) == 1
    assert _capture_provider_attempts[0]["usage_known"] is False
    assert "usage_known" not in _capture_provider_attempts[1]
    assert _capture_provider_attempts[1]["provider_request_id"] == "msg-autofill-test"


# ── D34 출처별 후보와 결정적 우선순위 ─────────────────────────────────
_SOURCES = {
    "naver": "=== 네이버 플레이스 ===\n월 10:00-19:00 서울 성동구 왕십리로 1 외과 치질",
    "survey": "=== 원장 인터뷰 설문 ===\n월 09:00-18:00 환자의 이야기를 끝까지 듣습니다",
    "homepage": "=== 병원 홈페이지 ===\n월 09:00-18:00 서울 성동구 왕십리로 1 친절한 진료",
    "blog": "=== 병원 블로그 ===\n서울 성동구 왕십리로 2",
}


def _cand(value, source, evidence, confidence=0.8):
    return {"value": value, "source": source, "confidence": confidence, "evidence": evidence}


_PHONES = {
    "naver": "02-111-1111",
    "survey": "02-222-2222",
    "homepage": "02-333-3333",
    "blog": "02-444-4444",
}
_PHONE_TEXTS = {source: f"=== {source} ===\n대표번호 {phone}" for source, phone in _PHONES.items()}


@pytest.mark.parametrize(
    ("present", "expected"),
    [
        (("blog", "homepage", "survey", "naver"), "naver"),
        (("blog", "homepage", "survey"), "survey"),
        (("blog", "homepage"), "homepage"),
        (("blog",), "blog"),
    ],
)
def test_fact_fields_follow_naver_survey_homepage_blog(present, expected):
    candidates = [_cand(_PHONES[source], source, f"대표번호 {_PHONES[source]}") for source in present]
    resolution = af._resolve_fields({"phone": {"candidates": candidates}}, _PHONE_TEXTS)
    assert resolution.meta["phone"]["source"] == expected
    assert resolution.draft["phone"] == _PHONES[expected]
    assert len(resolution.conflicts[0]["others"]) == len(present) - 1 if len(present) > 1 else not resolution.conflicts


def test_business_hours_prefer_naver_and_record_the_conflict():
    fields = {
        "business_hours": {
            "candidates": [
                _cand({"mon": "09:00-18:00"}, "homepage", "월 09:00-18:00"),
                _cand({"mon": "10:00-19:00"}, "naver", "월 10:00-19:00"),
            ]
        }
    }
    resolution = af._resolve_fields(fields, _SOURCES)
    assert resolution.draft["business_hours"] == {"mon": "10:00-19:00"}
    assert resolution.conflicts == [
        {
            "field": "business_hours",
            "chosen": {"source": "naver", "value": {"mon": "10:00-19:00"}},
            "others": [{"source": "homepage", "value": {"mon": "09:00-18:00"}}],
        }
    ]


def test_survey_wins_for_the_doctor_philosophy():
    fields = {
        "director_philosophy": {
            "candidates": [
                _cand("친절한 진료", "homepage", "친절한 진료"),
                _cand("환자의 이야기를 끝까지 듣습니다", "survey", "환자의 이야기를 끝까지 듣습니다"),
            ]
        }
    }
    resolution = af._resolve_fields(fields, _SOURCES)
    assert resolution.meta["director_philosophy"]["source"] == "survey"
    assert resolution.draft["director_philosophy"] == "환자의 이야기를 끝까지 듣습니다"


def test_candidate_quoting_another_sources_text_drops_out_of_the_ranking():
    """네이버라고 적힌 값의 발췌가 네이버 본문에 없으면 그 후보는 순위에서 빠진다."""

    fields = {
        "business_hours": {
            "candidates": [
                _cand({"mon": "09:00-18:00"}, "naver", "월 09:00-18:00"),
                _cand({"mon": "09:00-18:00"}, "homepage", "월 09:00-18:00"),
            ]
        }
    }
    resolution = af._resolve_fields(fields, _SOURCES)
    assert resolution.meta["business_hours"]["source"] == "homepage"
    assert resolution.conflicts == []


def test_unverified_naver_loses_to_any_other_source_and_alone_is_draft_only():
    fields = {
        "business_hours": {
            "candidates": [
                _cand({"mon": "10:00-19:00"}, "naver", "월 10:00-19:00"),
                _cand({"mon": "09:00-18:00"}, "homepage", "월 09:00-18:00"),
            ]
        },
        "specialties": {"candidates": [_cand(["외과"], "naver", "외과 치질")]},
    }
    resolution = af._resolve_fields(fields, _SOURCES, naver_verified=False)
    assert resolution.meta["business_hours"]["source"] == "homepage"
    assert resolution.meta["business_hours"]["verified"] is True
    assert resolution.meta["specialties"] == {
        "source": "naver",
        "confidence": 0.8,
        "evidence_excerpt": "외과 치질",
        "verified": False,
    }


def test_treatment_names_are_facts_but_descriptions_follow_the_narrative_order():
    sources = {
        "naver": "=== 네이버 ===\n치질 치루",
        "survey": "=== 설문 ===\n치질 수술은 당일 퇴원을 원칙으로 합니다",
    }
    fields = {
        "treatments": {
            "candidates": [
                _cand([{"name": "치질", "description": "네이버 설명"}, {"name": "치루"}], "naver", "치질 치루"),
                _cand(
                    [{"name": "치질", "description": "당일 퇴원을 원칙으로 합니다"}],
                    "survey",
                    "치질 수술은 당일 퇴원을 원칙으로 합니다",
                ),
            ]
        }
    }
    resolution = af._resolve_fields(fields, sources)
    assert resolution.meta["treatments"]["source"] == "naver"
    assert resolution.draft["treatments"] == [
        {"name": "치질", "description": "당일 퇴원을 원칙으로 합니다"},
        {"name": "치루", "description": None},
    ]


def test_legacy_single_shape_is_still_read_as_one_candidate():
    fields = {"address": {"value": "서울 성동구 왕십리로 1", "source": "naver", "evidence": "서울 성동구 왕십리로 1"}}
    draft, meta, rejected = af._normalize_fields(fields, _SOURCES)
    assert draft == {"address": "서울 성동구 왕십리로 1"} and rejected == []
    assert meta["address"]["verified"] is True


async def test_autofill_passes_the_stored_place_and_survey_and_reports_identity(monkeypatch):
    seen = {}

    async def ok_fetch(url: str):
        return "", "fail", None

    async def no_jina(url: str):
        return "", "fail"

    async def fake_naver(name, **kwargs):
        seen.update(kwargs)
        return naver_place.NaverPlaceResult(
            kwargs.get("place_id"), "세종다온의원 월 10:00-19:00", None, naver_place.IDENTITY_STORED_ID
        )

    captured = {}

    def create(**kwargs):
        captured["user"] = kwargs["messages"][1]["content"]
        return _fake_claude_response(
            {
                "business_hours": {
                    "candidates": [_cand({"mon": "10:00-19:00"}, "naver", "월 10:00-19:00")]
                }
            }
        )

    monkeypatch.setattr(af, "fetch_url_text", ok_fetch)
    monkeypatch.setattr(af.naver_place, "fetch_via_jina", no_jina)
    monkeypatch.setattr(af.naver_place, "scrape_naver_place", fake_naver)
    monkeypatch.setattr(af._client.chat.completions, "create", create)

    result = await af.autofill_profile(
        "세종다온의원",
        naver_place_url="https://m.place.naver.com/hospital/7654321/home",
        regions=["세종시"],
        survey_text="원장 설문: 환자의 이야기를 끝까지 듣습니다",
    )

    assert seen == {"place_id": "7654321", "regions": ("세종시",)}
    assert "원장 인터뷰 설문" in captured["user"]
    assert result.naver_identity == naver_place.IDENTITY_STORED_ID
    assert result.draft["business_hours"] == {"mon": "10:00-19:00"}
    assert result.field_meta["naver_place_id"]["verified"] is True


def test_unverified_naver_descriptions_never_ride_on_a_verified_homepage_list():
    # 리뷰 재현: 홈페이지에는 진료명만 있고 미확인 네이버에만 설명이 있다. 고른 목록은 홈페이지(확인됨)라
    # 필드 전체가 verified=True를 받는데, 설명이 네이버에서 오면 미확인 값이 자동 적용된다.
    sources = {
        "naver": "=== 네이버 ===\n치질 수술 당일 퇴원",
        "homepage": "=== 홈페이지 ===\n진료 항목: 치질",
    }
    fields = {
        "treatments": {
            "candidates": [
                _cand([{"name": "치질", "description": "수술 당일 퇴원"}], "naver", "치질 수술 당일 퇴원"),
                _cand([{"name": "치질"}], "homepage", "진료 항목: 치질"),
            ]
        }
    }
    resolution = af._resolve_fields(fields, sources, naver_verified=False)
    assert resolution.meta["treatments"]["source"] == "homepage"
    assert resolution.meta["treatments"]["verified"] is True
    assert resolution.draft["treatments"] == [{"name": "치질", "description": None}]
    assert "descriptions" not in resolution.meta["treatments"]

    verified = af._resolve_fields(fields, sources, naver_verified=True)
    assert verified.draft["treatments"] == [{"name": "치질", "description": "수술 당일 퇴원"}]
    assert verified.meta["treatments"]["descriptions"] == [
        {"name": "치질", "source": "naver", "verified": True}
    ]


def test_building_and_road_names_are_never_region_evidence():
    # 리뷰 재현(2차): 부산 주소의 건물명 '서울빌딩'이 지역 '서울'을 인증했다.
    busan = "Title: 다온의원 : 네이버\n주소 부산 해운대구 센텀중앙로 97 서울빌딩"
    assert naver_place.address_admin_tokens("부산 해운대구 센텀중앙로 97 서울빌딩") == ["부산", "해운대구"]
    assert not naver_place.place_matches_hospital(busan, "다온의원", ["서울"])
    assert not naver_place.place_matches_hospital(busan, "다온의원", ["서울 해운대구"])
    assert naver_place.place_matches_hospital(busan, "다온의원", ["부산 해운대구"])
    assert naver_place.place_matches_hospital(busan, "다온의원", ["부산광역시"])
    # 도로명 '성동로'는 '성동구'가 아니다.
    road = "Title: 다온의원 : 네이버\n주소 경기 수원시 성동로 1"
    assert not naver_place.place_matches_hospital(road, "다온의원", ["성동구"])


def test_hospital_names_compare_after_unicode_normalization():
    import unicodedata

    nfd = unicodedata.normalize("NFD", "세종다온의원")
    markdown = "Title: 세종다온의원 : 네이버\n주소 세종 보람동 1"
    assert naver_place.place_matches_hospital(markdown, nfd, ["세종시"])


async def test_a_busan_phone_never_reaches_a_seoul_hospital_through_collect_select_plan(monkeypatch):
    from types import SimpleNamespace

    from app.services.profile_autofill_apply import plan_autofill

    detail = (
        "Title: 다온의원 : 네이버\n주소 부산 해운대구 센텀중앙로 97 서울빌딩\n전화 051-777-7777\n진료 내과"
    )

    async def fake_jina(url: str):
        if "list?query" in url:
            return ("[a](https://m.place.naver.com/hospital/7654321)", None)
        return (detail, None)

    monkeypatch.setattr(af.naver_place, "fetch_via_jina", fake_jina)
    fields = {
        "phone": {"candidates": [_cand("051-777-7777", "naver", "051-777-7777", 0.95)]},
    }
    monkeypatch.setattr(
        af._client.chat.completions, "create", lambda **kwargs: _fake_claude_response(fields)
    )

    result = await af.autofill_profile("다온의원", None, None, regions=["서울"])

    assert result.naver_identity == naver_place.IDENTITY_UNVERIFIED
    hospital = SimpleNamespace(
        name="다온의원", region=["서울"], phone=None, profile_field_provenance={},
        naver_place_id=None, naver_place_url=None,
    )
    summary = {
        "draft": result.draft,
        "field_meta": result.field_meta,
        "naver_identity": result.naver_identity,
    }
    plan = plan_autofill(hospital, summary, has_physicians=False)
    assert "phone" not in plan.updates
    assert "phone" in plan.skipped_unverified_fields

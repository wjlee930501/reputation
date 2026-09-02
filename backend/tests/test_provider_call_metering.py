"""실제 공급자 호출 계수 — 비용 가드가 '예약'이 아니라 '실제 지출'을 볼 수 있는가.

비용 가드의 예약 카운터(check_and_increment)는 작업 1건을 1회로 센다. 그런데 실제
호출은 재시도와 폴백으로 훨씬 많아질 수 있다 — 이미지 1건은 OpenAI 최대 3회 + Google
1회까지 간다. 그 차이가 기록되지 않으면 상한이 실제 지출의 몇 분의 일만 보고 있게 되고,
운영자가 상한을 조정할 근거 자체가 사라진다.

여기서 검증하는 것은 "재시도·폴백이 각각 실제 호출로 잡히는가"다.
"""
import asyncio

import pytest

from app.models.content import ContentType
from app.services import content_engine, image_engine, sov_engine


class RecordedCalls:
    """cost_guard.record_provider_call 대역 — 카테고리별 누적."""

    def __init__(self):
        self.by_category: dict[str, int] = {}

    async def __call__(self, category: str, *, count: int = 1, **_kwargs):
        self.by_category[category] = self.by_category.get(category, 0) + count

    @property
    def total(self) -> int:
        return sum(self.by_category.values())


@pytest.fixture
def recorded(monkeypatch):
    calls = RecordedCalls()
    from app.services import cost_guard

    monkeypatch.setattr(cost_guard, "record_provider_call", calls)
    return calls


# ── 이미지: 재시도 3회 + 폴백이 각각 잡혀야 한다 ──────────────────────────


def test_openai_image_retries_each_count_as_a_paid_call(monkeypatch):
    """tenacity 재시도 3회 = 유료 호출 3회. 시도가 조용히 사라지면 안 된다."""
    import openai

    counter = image_engine._CallCounter()
    requests = {"n": 0}

    class FakeImages:
        def generate(self, **_kwargs):
            requests["n"] += 1
            # APIStatusError가 아닌 예외 → _is_transient_openai_error가 재시도 대상으로 본다.
            raise RuntimeError("upstream 503")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.images = FakeImages()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(image_engine.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(image_engine.settings, "OPENAI_IMAGE_MODEL", "gpt-image-2")
    # 백오프 대기는 이 테스트의 관심사가 아니다.
    monkeypatch.setattr(image_engine._openai_generate_and_upload.retry, "sleep", lambda _s: None)

    with pytest.raises(Exception):
        image_engine._openai_generate_and_upload("prompt", "병원", counter=counter)

    assert requests["n"] == 3, "tenacity가 3회 시도해야 하는 전제 확인"
    assert counter.count == requests["n"], "실제 요청 수와 계수가 같아야 한다"


def test_image_generation_records_every_attempt_including_the_fallback(monkeypatch, recorded):
    """OpenAI 3회 전부 실패 후 Google 1회 성공 → 실제 호출 4회로 기록된다."""

    async def allowed(*_a, **_k):
        from app.services.cost_guard import CostGuardDecision

        return CostGuardDecision(True, None)

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", allowed)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "openai")
    monkeypatch.setattr(image_engine.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")

    def failing_openai(_prompt, _hospital, *, counter=None):
        for _ in range(3):  # tenacity가 소진한 시도 수
            if counter is not None:
                counter.tick()
        raise RuntimeError("all openai attempts failed")

    def succeeding_google(_prompt, _hospital, *, counter=None):
        if counter is not None:
            counter.tick()
        return "gs://bucket/image.png"

    monkeypatch.setattr(image_engine, "_openai_generate_and_upload", failing_openai)
    monkeypatch.setattr(image_engine, "_generate_and_upload", succeeding_google)

    url, _prompt = asyncio.run(image_engine.generate_image(ContentType.FAQ, "병원"))

    assert url == "gs://bucket/image.png"
    assert recorded.by_category["image"] == 4, "예약은 1건이지만 실제 호출은 4회다"


def test_google_topic_safety_failure_uses_neutral_fallback(monkeypatch, recorded):
    async def allowed(*_a, **_k):
        from app.services.cost_guard import CostGuardDecision

        return CostGuardDecision(True, None)

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", allowed)
    monkeypatch.setattr(image_engine.settings, "IMAGE_PROVIDER", "google")
    monkeypatch.setattr(image_engine.settings, "GCP_PROJECT_ID", "test-project")
    prompts = []

    def topic_then_fallback(prompt, _hospital, *, counter=None):
        prompts.append(prompt)
        if counter is not None:
            counter.tick()
        if len(prompts) == 1:
            raise ValueError("IMAGE_SAFETY")
        return "gs://bucket/neutral.png"

    monkeypatch.setattr(image_engine, "_generate_and_upload", topic_then_fallback)

    url, prompt = asyncio.run(
        image_engine.generate_image(ContentType.LOCAL, "병원", topic="간질환 진료 흐름")
    )

    assert url == "gs://bucket/neutral.png"
    assert prompt == image_engine.GOOGLE_SAFETY_FALLBACK_PROMPT
    assert prompts[1] == image_engine.GOOGLE_SAFETY_FALLBACK_PROMPT
    assert recorded.by_category["image"] == 2


def test_blocked_image_generation_records_no_provider_call(monkeypatch, recorded):
    """가드에 막히면 공급자에 아무것도 나가지 않는다 — 계수도 0이어야 한다."""

    async def blocked(*_a, **_k):
        from app.services.cost_guard import CostGuardDecision

        return CostGuardDecision(False, "일일 상한 도달")

    monkeypatch.setattr("app.services.cost_guard.check_and_increment", blocked)

    url, prompt = asyncio.run(image_engine.generate_image(ContentType.FAQ, "병원"))

    assert (url, prompt) == ("", "")
    assert recorded.total == 0


# ── 콘텐츠: 재시도마다 Anthropic 호출 1회 ────────────────────────────────


def test_content_generation_records_a_call_before_each_anthropic_request(monkeypatch, recorded):
    """Anthropic 클라이언트는 max_retries=0이라 본문 1회 실행 = HTTP 요청 1회다."""
    attempts = {"n": 0}

    class FakeResponse:
        content = [type("Block", (), {"text": "{}"})()]

    def fake_create(**_kwargs):
        attempts["n"] += 1
        raise RuntimeError("anthropic 5xx")

    monkeypatch.setattr(content_engine.client.messages, "create", fake_create)
    # 프롬프트 조립은 이 테스트의 관심사가 아니다 — 계수 지점만 본다.
    monkeypatch.setattr(content_engine, "_build_profile_context", lambda _h: "프로파일")
    monkeypatch.setattr(content_engine, "_build_philosophy_context", lambda _p: "")
    monkeypatch.setattr(content_engine, "_build_content_brief_context", lambda _b, _p=None: "")
    monkeypatch.setattr(content_engine, "_fill_type_prompt", lambda _t, _h, _b=None: "유형")
    monkeypatch.setattr(content_engine.generate_content.retry, "sleep", lambda _s: None)
    assert FakeResponse  # 성공 경로 없이 재시도만 보므로 사용하지 않는다

    with pytest.raises(Exception):
        asyncio.run(content_engine.generate_content(object(), ContentType.FAQ))

    assert attempts["n"] == recorded.by_category.get("content"), (
        "실제 Anthropic 요청 수와 기록된 호출 수가 같아야 한다"
    )
    assert recorded.by_category.get("content", 0) >= 1


# ── SoV: 무료 진단과 유료 측정의 예산이 섞이지 않아야 한다 ────────────────


def test_sov_calls_are_counted_against_the_pool_that_made_them(recorded):
    """leadgen 풀에서 나간 호출이 sov(유료) 예산으로 잡히면 상한 판단이 무너진다."""

    async def scenario():
        sov_engine._provider_cost_category.set(sov_engine.POOL_LEADGEN)
        await sov_engine._record_sov_provider_call()
        sov_engine._provider_cost_category.set(sov_engine.POOL_SOV)
        await sov_engine._record_sov_provider_call(count=2)

    asyncio.run(scenario())

    assert recorded.by_category == {"leadgen": 1, "sov": 2}


def test_sov_defaults_to_the_paid_budget_when_no_pool_was_set(recorded):
    """기본값이 leadgen이면 유료 측정 비용이 무료 예산을 잠식한다."""

    asyncio.run(sov_engine._record_sov_provider_call())

    assert recorded.by_category == {"sov": 1}


# ── 운영 기준(essence): 동기 LLM 호출이 스레드 경계를 넘어 계수되는가 ────────


def test_essence_llm_calls_are_counted_across_the_thread_boundary(monkeypatch, recorded):
    """근거 추출·철학 합성은 동기 SDK 호출을 asyncio.to_thread로 돌린다.

    카운터를 ContextVar로 전달하므로, to_thread가 컨텍스트를 복사한다는 전제가 깨지면
    호출이 조용히 계수되지 않는다 — 그 전제를 여기서 고정한다.
    """
    from app.services import essence_engine

    class FakeMessages:
        def create(self, **_kwargs):
            block = type("Block", (), {"text": '{"ok": true}'})()
            return type("Resp", (), {"content": [block]})()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(essence_engine, "_anthropic_client", lambda: FakeClient())

    async def scenario():
        async with essence_engine.metered_llm_calls() as counter:
            for _ in range(2):
                await asyncio.to_thread(
                    essence_engine._call_anthropic_json, "sys", "msg", max_tokens=100
                )
            return counter.count

    counted = asyncio.run(scenario())

    assert counted == 2
    assert recorded.by_category == {"content": 2}


def test_essence_records_nothing_when_the_deterministic_fallback_runs(recorded):
    """LLM을 한 번도 부르지 않으면 유료 호출도 0 — 빈 기록을 남기지 않는다."""
    from app.services import essence_engine

    async def scenario():
        async with essence_engine.metered_llm_calls():
            pass

    asyncio.run(scenario())

    assert recorded.total == 0

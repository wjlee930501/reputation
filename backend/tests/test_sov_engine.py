import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import provider_usage, sov_engine


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


class _QuotaError(RuntimeError):
    status_code = 429
    code = "credit_balance_exhausted"


class _TransientRateLimitError(RuntimeError):
    status_code = 429


def test_provider_failure_reason_preserves_safe_quota_cause():
    reason = sov_engine.provider_failure_reason(_QuotaError("secret provider detail"))

    assert reason == (
        "provider_query_failed:_QuotaError:http_429:credit_balance_exhausted"
    )
    assert "secret provider detail" not in reason
    assert sov_engine.is_terminal_provider_failure(reason) is True
    assert sov_engine._should_retry_provider_exception(_QuotaError("quota")) is False


def test_provider_retry_policy_keeps_transient_failures_retryable():
    assert sov_engine._should_retry_provider_exception(TimeoutError("temporary")) is True
    assert sov_engine._should_retry_provider_exception(_TransientRateLimitError("slow down")) is True
    assert sov_engine._should_retry_provider_exception(RuntimeError("deterministic")) is False


def test_answer_and_judge_clients_disable_hidden_sdk_retries():
    assert sov_engine.openai_query_client.max_retries == 0
    assert sov_engine.openai_client.max_retries == 0


# 규칙 대조가 '다른 지역'으로 본 같은 이름 — 보조 판정(두 번째 눈)에 한 번 더 묻는 답변.
SECOND_LOOK_NAME = "장편한외과의원"
SECOND_LOOK_REGION = "수원시 팔달구"
SECOND_LOOK_ANSWER = "부산 해운대구의 장편한외과의원을 추천합니다."


@pytest.mark.asyncio
async def test_resolver_match_needs_no_provider_call(monkeypatch):
    class _Unexpected:
        async def create(self, **_kwargs):
            raise AssertionError("규칙이 확정한 언급에 모델을 부르지 않는다")

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _Unexpected())

    parsed = await sov_engine._parse_mention(
        "마포성모탑정형외과",
        "* **성모탑정형외과의원** — 마포구 큰우물로 76, 전화 02-711-8538",
        "마포구",
    )

    assert parsed["verdict"] == "MATCHED"
    assert parsed["is_mentioned"] is True
    assert parsed["matched_text"] == "성모탑정형외과의원"
    assert sov_engine.estimate_judgment_provider_calls(
        "마포성모탑정형외과",
        "* **성모탑정형외과의원** — 마포구 큰우물로 76",
        region="마포구",
    ) == 0


@pytest.mark.asyncio
async def test_judge_transient_retry_is_explicit_and_each_http_attempt_is_metered(monkeypatch):
    calls = 0
    metered: list[dict] = []

    class _Flaky:
        async def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _TransientRateLimitError("slow down")
            return SimpleNamespace(
                choices=[_FakeChoice(json.dumps({
                    "verdict": "MATCHED",
                    "matched_text": "장편한외과의원",
                    "evidence": None,
                }))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

    async def _meter(**kwargs):
        metered.append(kwargs)

    async def _actual(_count=1):
        return None

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _Flaky())
    monkeypatch.setattr(sov_engine, "_record_provider_attempt", _meter)
    monkeypatch.setattr(sov_engine, "_record_sov_provider_call", _actual)
    monkeypatch.setattr(sov_engine._request_judge_completion.retry, "sleep", _no_sleep)

    result = await sov_engine._parse_mention(
        SECOND_LOOK_NAME, SECOND_LOOK_ANSWER, SECOND_LOOK_REGION
    )

    # 규칙은 다른 지역이라 했고 보조 판정은 언급이라 했다 — 확정하지 않고 보류로 둔다.
    assert result["verdict"] == "AMBIGUOUS"
    assert result["is_mentioned"] is None
    assert calls == 2
    assert len(metered) == 2
    assert metered[0].get("response") is None
    assert metered[1]["input_tokens"] == 3


@pytest.mark.asyncio
async def test_empty_sdk_usage_object_is_not_marked_known(monkeypatch):
    recorded: list[dict] = []

    async def _capture(**kwargs):
        recorded.append(kwargs)
        return True

    monkeypatch.setattr(provider_usage, "record_attempt", _capture)

    with sov_engine.provider_execution_context(pool=sov_engine.POOL_SOV):
        await sov_engine._record_provider_attempt(
            provider="openai",
            model="judge-model",
            logical_call_id="judgment:self",
            usage=SimpleNamespace(),
        )

    assert len(recorded) == 1
    assert recorded[0]["usage_known"] is None
    assert provider_usage.normalize_usage("openai", SimpleNamespace())["usage_known"] is False


@pytest.mark.asyncio
async def test_missing_responses_capability_is_not_counted_as_an_http_attempt(monkeypatch):
    actual_calls = 0
    usage_events = 0

    async def _actual(_count=1):
        nonlocal actual_calls
        actual_calls += 1

    async def _usage(**_kwargs):
        nonlocal usage_events
        usage_events += 1

    monkeypatch.setattr(
        sov_engine,
        "openai_query_client",
        SimpleNamespace(responses=None),
    )
    monkeypatch.setattr(sov_engine, "_record_sov_provider_call", _actual)
    monkeypatch.setattr(sov_engine, "_record_provider_attempt", _usage)

    result = await sov_engine._query_chatgpt_with_search_result("query")

    assert result["text"] == ""
    assert actual_calls == 0
    assert usage_events == 0


def test_judgment_fingerprint_binds_identity_answer_and_policy():
    base = sov_engine.judgment_input_fingerprint(
        hospital_identity="hospital-1",
        hospital_name="가나의원",
        region="수서역",
        response_text="가나의원을 추천합니다.",
    )
    assert base == sov_engine.judgment_input_fingerprint(
        hospital_identity="hospital-1",
        hospital_name="가나의원",
        region="수서역",
        response_text="가나의원을 추천합니다.",
    )
    assert base != sov_engine.judgment_input_fingerprint(
        hospital_identity="hospital-2",
        hospital_name="가나의원",
        region="수서역",
        response_text="가나의원을 추천합니다.",
    )
    assert base != sov_engine.judgment_input_fingerprint(
        hospital_identity="hospital-1",
        hospital_name="가나의원",
        region="수서역",
        response_text="가나의원은 언급되지 않았습니다.",
    )


def test_judgment_reservation_estimate_counts_only_second_look_calls():
    response = "가나의원과 다라병원을 비교해 주세요."

    # 규칙이 확정한 언급·경쟁 병원 판정은 공급자를 부르지 않는다.
    assert sov_engine.estimate_judgment_provider_calls("가나의원", response) == 0
    assert sov_engine.estimate_judgment_provider_calls(
        "가나의원", response, ["다라병원", "마바사의원"]
    ) == 0
    assert sov_engine.estimate_judgment_provider_calls(
        "없는의원", response, ["마바사의원"]
    ) == 0
    # 이름 조각은 있는데 규칙이 아니라고 본 답변만 보조 판정 1회를 예약한다.
    assert sov_engine.estimate_judgment_provider_calls(
        SECOND_LOOK_NAME, SECOND_LOOK_ANSWER, region=SECOND_LOOK_REGION
    ) == 1


@pytest.mark.asyncio
async def test_lead_gemini_is_serialized_without_blocking_lead_openai(monkeypatch):
    gemini_entered = asyncio.Event()
    release_gemini = asyncio.Event()
    gemini_in_flight = 0
    gemini_max = 0

    async def gemini(_query):
        nonlocal gemini_in_flight, gemini_max
        gemini_in_flight += 1
        gemini_max = max(gemini_max, gemini_in_flight)
        gemini_entered.set()
        await release_gemini.wait()
        gemini_in_flight -= 1
        return {
            "text": "Gemini answer",
            "source_urls": [],
            "measurement_method": "GEMINI_GOOGLE_SEARCH",
        }

    async def openai(_query):
        return {
            "text": "OpenAI answer",
            "source_urls": [],
            "measurement_method": "OPENAI_RESPONSES_WEB_SEARCH",
        }

    monkeypatch.setattr(sov_engine, "_query_gemini_result", gemini)
    monkeypatch.setattr(sov_engine, "_query_chatgpt", openai)

    gemini_tasks = [
        asyncio.create_task(
            sov_engine.fetch_answer("query", "gemini", pool=sov_engine.POOL_LEADGEN)
        )
        for _ in range(4)
    ]
    await gemini_entered.wait()

    # Provider pools are independent: an occupied Gemini slot cannot hold OpenAI answers.
    openai_result = await asyncio.wait_for(
        sov_engine.fetch_answer("query", "chatgpt", pool=sov_engine.POOL_LEADGEN),
        timeout=0.5,
    )
    assert openai_result["measurement_status"] == "SUCCESS"

    release_gemini.set()
    await asyncio.gather(*gemini_tasks)
    assert gemini_max == 1


@pytest.mark.asyncio
async def test_parse_competitors_uses_the_same_resolver_without_provider_calls(monkeypatch):
    class _Unexpected:
        async def create(self, **_kwargs):
            raise AssertionError("경쟁 병원 판정은 공급자를 부르지 않는다")

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _Unexpected())
    home = sov_engine.mention_identity_for("우리의원", "수원시 팔달구")

    parsed = await sov_engine._parse_competitors(
        ["경쟁병원", "모두탑365정형외과", "멀리내과의원"],
        "1. **경쟁병원** (팔달구)\n2. 노원모두탑365정형외과의원\n3. **멀리내과의원** (부산 해운대구)",
        home,
    )

    assert parsed == [
        {"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1},
        # 더 긴 다른 이름의 일부는 언급이 아니다.
        {"name": "모두탑365정형외과", "is_mentioned": False, "mention_rank": None},
        # 같은 이름이라도 다른 시·군·구로 적혔으면 다른 지점이다.
        {"name": "멀리내과의원", "is_mentioned": False, "mention_rank": None},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"verdict": "MAYBE", "matched_text": "장편한외과의원"},
        {"verdict": "MATCHED", "matched_text": 123},
        {"verdict": "MATCHED", "matched_text": "장편한외과의원", "evidence": []},
        "not json",
    ],
)
async def test_second_look_rejects_malformed_payload(monkeypatch, payload):
    class _Raw:
        async def create(self, **_kwargs):
            content = payload if isinstance(payload, str) else json.dumps(payload)
            return SimpleNamespace(
                choices=[_FakeChoice(content)],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _Raw())

    with pytest.raises(ValueError, match="^mention_parse_failed$"):
        await sov_engine._parse_mention(SECOND_LOOK_NAME, SECOND_LOOK_ANSWER, SECOND_LOOK_REGION)


async def test_second_look_reads_json_wrapped_in_prose(monkeypatch):
    class _Prose:
        async def create(self, **kwargs):
            # Anthropic 모델은 json_object 형식 강제를 받지 않는다 — 형식 인자를 보내지 않는다.
            assert "response_format" not in kwargs
            assert kwargs["model"].startswith("anthropic/")
            return SimpleNamespace(
                choices=[_FakeChoice('판정입니다.\n```json\n{"verdict": "NOT_MATCHED", "matched_text": null, "evidence": null}\n```')],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _Prose())

    parsed = await sov_engine._parse_mention(
        SECOND_LOOK_NAME, SECOND_LOOK_ANSWER, SECOND_LOOK_REGION
    )

    assert parsed["verdict"] == "NOT_MATCHED"
    assert parsed["is_mentioned"] is False


# ── calculate_sov: 성공 측정 0건이면 None (측정 안 됨 ≠ 실제 0% 언급) ──


def test_calculate_sov_returns_none_when_no_records():
    assert sov_engine.calculate_sov([]) is None


def test_calculate_sov_returns_none_when_all_failed():
    records = [
        {"is_mentioned": False, "measurement_status": "FAILED"},
        {"is_mentioned": False, "measurement_status": "FAILED"},
    ]
    assert sov_engine.calculate_sov(records) is None


def test_calculate_sov_returns_none_when_all_empty_raw_response():
    # measurement_status 미존재 + raw_response 비어있음 = 네트워크 실패 추정 → 분모 제외
    records = [{"is_mentioned": False, "raw_response": ""}]
    assert sov_engine.calculate_sov(records) is None


def test_calculate_sov_zero_percent_is_distinct_from_none():
    records = [
        {"is_mentioned": False, "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "measurement_status": "SUCCESS"},
    ]
    assert sov_engine.calculate_sov(records) == 0.0


def test_calculate_sov_excludes_failures_from_denominator():
    records = [
        {"is_mentioned": True, "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "measurement_status": "FAILED"},
    ]
    # 성공 2건 중 1건 언급 → 50.0 (실패는 분모 제외)
    assert sov_engine.calculate_sov(records) == 50.0


# ── prefilter 정규화: 표기 변형(띄어쓰기)에 강건 ──


def test_normalize_for_prefilter_strips_whitespace_and_symbols():
    assert sov_engine._normalize_for_prefilter("장편한 외과") == "장편한외과"
    assert sov_engine._normalize_for_prefilter("장편한-외과!") == "장편한외과"
    assert sov_engine._normalize_for_prefilter("") == ""


class _MentionedCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                _FakeChoice(
                    json.dumps(
                        {
                            "verdict": "MATCHED",
                            # 근거 인용은 답변에서 그대로 잘라낸 것이어야 승인된다.
                            "matched_text": "장편한 외과",
                            "mention_rank": 1,
                            "sentiment": "positive",
                            "mention_context": "언급됨",
                        }
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_parse_mention_does_not_prefilter_out_spacing_variant(monkeypatch):
    # 병원명 "장편한외과" ↔ 응답 "장편한 외과" 처럼 공백만 다른 경우에도 사전 필터가
    # 걸러내지 않고 LLM 판정까지 도달해야 한다.
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _MentionedCompletions())

    parsed = await sov_engine._parse_mention("장편한외과", "이 지역은 장편한 외과가 유명합니다.")

    assert parsed["is_mentioned"] is True


class _SearchResponses:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text="검색 기반 답변")


@pytest.mark.asyncio
async def test_chatgpt_web_search_is_offered_but_not_forced(monkeypatch):
    """측정 정책 v2 — 도구는 제공하되 강제하지 않는다.

    v1은 `tool_choice="required"`로 매 요청 검색을 강제했다. "{지역} 근처 {진료과}
    병원 추천해줘"에 강제 검색을 걸면 지역 병원 디렉터리를 긁어와 나열하게 되어,
    환자가 실제로 받는 답변보다 병원명이 구조적으로 많이 등장했다.
    """
    responses = _SearchResponses()
    monkeypatch.setattr(sov_engine.openai_query_client, "responses", responses)

    result = await sov_engine._query_chatgpt_with_search("수원 외과 추천")

    assert result == "검색 기반 답변"
    assert responses.kwargs["tools"] == [sov_engine.openrouter.WEB_SEARCH_TOOL]
    assert responses.kwargs["tool_choice"] == "auto"


class TestProviderTelemetry:
    """검색 사용·응답 모델을 실제로 기록하는가.

    `search_calls` 컬럼은 오래전부터 있었지만 아무도 채우지 않았다. 그래서
    tool_choice를 auto로 바꾼 뒤 "그래도 검색이 매번 도니까 높다"는 설명이 나왔을 때
    확인도 반박도 못 했다. 이 테스트가 그 공백의 재발을 막는다.
    """

    class _Responses:
        def __init__(self, search_items: int):
            self.kwargs = None
            self._search_items = search_items

        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                output_text="검색 기반 답변",
                model="gpt-5.6-luna-2026-07-01",
                output=[
                    SimpleNamespace(type="web_search_call")
                    for _ in range(self._search_items)
                ],
                usage=SimpleNamespace(input_tokens=1200, output_tokens=340),
            )

    @pytest.mark.asyncio
    async def test_openai_records_search_calls_and_resolved_model(self, monkeypatch):
        responses = self._Responses(search_items=2)
        monkeypatch.setattr(sov_engine.openai_query_client, "responses", responses)

        result = await sov_engine._query_chatgpt_with_search_result("수원 외과 추천")

        assert result["search_calls"] == 2
        assert result["answer_model"] == "gpt-5.6-luna-2026-07-01"
        assert result["input_tokens"] == 1200
        assert result["output_tokens"] == 340

    @pytest.mark.asyncio
    async def test_no_search_is_recorded_as_zero_not_missing(self, monkeypatch):
        """0(검색 안 씀)과 None(계측 없음)은 다른 사실이다."""
        responses = self._Responses(search_items=0)
        monkeypatch.setattr(sov_engine.openai_query_client, "responses", responses)

        result = await sov_engine._query_chatgpt_with_search_result("수원 외과 추천")

        assert result["search_calls"] == 0

    @pytest.mark.asyncio
    async def test_fetch_answer_passes_telemetry_through(self, monkeypatch):
        """공급자가 잰 값이 측정 레코드까지 도달해야 저장된다."""
        responses = self._Responses(search_items=1)
        monkeypatch.setattr(sov_engine.openai_query_client, "responses", responses)
        monkeypatch.setattr(sov_engine.settings, "OPENAI_CHATGPT_USE_WEB_SEARCH", True)

        answer = await sov_engine.fetch_answer("수원 외과 추천", "chatgpt")

        assert answer["measurement_status"] == "SUCCESS"
        assert answer["search_calls"] == 1
        assert answer["answer_model"] == "gpt-5.6-luna-2026-07-01"

    @pytest.mark.asyncio
    async def test_fetch_answer_does_not_swallow_worker_soft_timeout(self, monkeypatch):
        async def timed_out(_query):
            raise sov_engine.SoftTimeLimitExceeded()

        monkeypatch.setattr(sov_engine, "_query_chatgpt", timed_out)

        with pytest.raises(sov_engine.SoftTimeLimitExceeded):
            await sov_engine.fetch_answer("수원 외과 추천", "chatgpt")

    @pytest.mark.asyncio
    async def test_judge_answer_does_not_swallow_worker_soft_timeout(self, monkeypatch):
        async def timed_out(*_args, **_kwargs):
            raise sov_engine.SoftTimeLimitExceeded()

        monkeypatch.setattr(sov_engine, "_parse_mention", timed_out)

        with pytest.raises(sov_engine.SoftTimeLimitExceeded):
            await sov_engine.judge_answer("병원", "병원 답변")


def test_system_prompt_does_not_instruct_the_model_to_name_hospitals():
    """지시문이 병원명을 시키면, 우리가 세는 대상(병원명 등장)을 우리가 만든 것이다."""
    assert "병원 이름을 포함" not in sov_engine.SYSTEM_PROMPT_SOV


class TestMeasurementPolicy:
    def test_protocol_snapshot_carries_the_conditions_that_change_answers(self):
        protocol = sov_engine.measurement_protocol()
        assert protocol["policy_version"] == sov_engine.MEASUREMENT_POLICY_VERSION
        assert protocol["system_prompt"] == sov_engine.SYSTEM_PROMPT_SOV
        assert protocol["openai_tool_choice"] == "auto"
        assert protocol["openai_use_web_search"] == sov_engine.settings.OPENAI_CHATGPT_USE_WEB_SEARCH
        assert protocol["openai_model_query"] == sov_engine.settings.OPENAI_MODEL_QUERY
        assert protocol["gemini_model"] == sov_engine.settings.GEMINI_MODEL

    def test_same_policy_requires_both_snapshots(self):
        """한쪽이라도 기록이 없으면 다르다고 본다 — '모르겠다'는 '같다'가 아니다."""
        protocol = sov_engine.measurement_protocol()
        assert sov_engine.same_execution_policy(protocol, dict(protocol))
        assert not sov_engine.same_execution_policy(protocol, None)
        assert not sov_engine.same_execution_policy(None, protocol)
        assert not sov_engine.same_execution_policy(None, None)

    def test_changing_the_tool_choice_changes_the_fingerprint(self):
        protocol = sov_engine.measurement_protocol()
        drifted = {**protocol, "openai_tool_choice": "required"}
        assert not sov_engine.same_execution_policy(protocol, drifted)

    def test_changing_requested_model_or_search_path_changes_the_policy(self):
        protocol = sov_engine.measurement_protocol()

        assert not sov_engine.same_execution_policy(
            protocol,
            {**protocol, "openai_model_query": "different-model"},
        )
        assert not sov_engine.same_execution_policy(
            protocol,
            {**protocol, "openai_use_web_search": not protocol["openai_use_web_search"]},
        )

    def test_query_design_blocks_comparison_but_not_execution(self):
        """질의 설계 변경은 기간 비교를 막되 측정 실행은 막지 않는다.

        접수 시점에 질의 원문이 이미 저장되므로, 생성기가 바뀌어도 그 진단은 저장된
        질의로 잴 수 있다. 둘을 한 덩어리로 두면 생성기 배포가 대기 중인 진단을
        전부 죽인다.
        """
        protocol = sov_engine.measurement_protocol()
        older_design = {**protocol, "query_design_version": "lead-local-v1"}

        assert sov_engine.same_execution_policy(protocol, older_design)
        assert not sov_engine.same_measurement_basis(protocol, older_design)

    def test_query_design_is_snapshotted(self):
        protocol = sov_engine.measurement_protocol()
        assert protocol["query_design_version"]
        assert protocol["template_fingerprint"]
        assert protocol["lexicon_fingerprint"]
        assert protocol["slot_count"] == 3


# ── 자사/경쟁사 판정 대칭성 (PRD F4) ──
# 과거 자사 판정은 "접미사 제거 후 3글자 이상" 사전 필터 + "앞글자 2~3자만으로는 동일
# 기관으로 보지 않는다" 프롬프트를 썼는데, 경쟁사 판정은 "앞 2글자" 사전 필터 + "앞 2~3글자
# 일치 시 동일 병원" 프롬프트를 썼다. 같은 근거가 이름 주인에 따라 반대 판정을 받아
# 경쟁사 언급이 구조적으로 부풀려졌고, 원장 보고서의 "우리 병원 vs 경쟁 병원" 비교가
# 성립하지 않았다. 아래 테스트가 그 비대칭의 회귀를 막는다.


class _ExplodingCompletions:
    """사전 필터에서 걸러졌다면 LLM 판정은 호출되지 않아야 한다."""

    async def create(self, **kwargs):
        raise AssertionError("prefilter가 걸렀어야 하는데 LLM 판정까지 도달했다")


def test_prefilter_key_strips_suffix_and_requires_three_chars():
    assert sov_engine.prefilter_key("장편한외과의원") == "장편한외과"
    # 접미사를 떼면 3글자 미만 → 접미사를 포함한 원래 이름을 키로 쓴다.
    assert sov_engine.prefilter_key("서울병원") == "서울병원"
    assert sov_engine.prefilter_key("연세 클리닉") == "연세클리닉"


@pytest.mark.asyncio
async def test_competitor_prefilter_rejects_two_char_prefix(monkeypatch):
    # "강남"까지만 겹치는 무관한 답변. 과거에는 앞 2글자 일치로 통과해 LLM까지 갔다.
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _ExplodingCompletions())

    parsed = await sov_engine._parse_competitors(
        ["강남세브란스병원"], "강남역 인근 병원을 안내합니다."
    )

    assert parsed == [{"name": "강남세브란스병원", "is_mentioned": False, "mention_rank": None}]


@pytest.mark.asyncio
async def test_self_and_competitor_prefilter_make_the_same_decision(monkeypatch):
    # 같은 이름·같은 답변이면 자사 경로와 경쟁사 경로가 같은 판단을 내려야 한다.
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _ExplodingCompletions())
    name, answer = "강남세브란스병원", "강남역 인근 병원을 안내합니다."

    mention = await sov_engine._parse_mention(name, answer)
    competitors = await sov_engine._parse_competitors([name], answer)

    assert mention["is_mentioned"] is False
    assert competitors[0]["is_mentioned"] is False


class _VerdictCompletions:
    """판정기가 임의의 verdict/근거를 돌려주는 상황."""

    def __init__(self, verdict: str, matched_text):
        self.verdict = verdict
        self.matched_text = matched_text

    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                _FakeChoice(
                    json.dumps(
                        {
                            "verdict": self.verdict,
                            "matched_text": self.matched_text,
                            "mention_rank": 1,
                            "sentiment": None,
                            "mention_context": None,
                        }
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_matched_without_the_full_name_in_the_quote_is_downgraded(monkeypatch):
    """"행복한의원"의 핵심 "행복한"은 "행복한 진료를 위해" 같은 평범한 문장에도 걸린다.

    규칙 대조는 접미사 없는 맨 이름을 위치 증거 없이 인정하지 않는다. 보조 판정이 그 조각을
    근거로 MATCHED를 주더라도 확정하지 않고 보류로 둔다 — 0으로도, 언급으로도 세지 않는다.
    """
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _VerdictCompletions("MATCHED", "행복한 진료"),
    )

    parsed = await sov_engine._parse_mention("행복한의원", "행복한 진료를 위해 노력합니다.")

    assert parsed["verdict"] == "AMBIGUOUS"
    # 미언급으로 내리지도 않는다 — 답변이 약칭만 쓴 경우를 0으로 깎지 않기 위해서다.
    assert parsed["is_mentioned"] is None


@pytest.mark.asyncio
async def test_matched_with_a_fabricated_quote_is_not_counted(monkeypatch):
    """보조 판정이 답변에 없는 문자열을 근거랍시고 지어내면 근거가 아니다 — 규칙의 판정(미언급)이 남는다."""
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _VerdictCompletions("MATCHED", "행복한의원"),
    )

    parsed = await sov_engine._parse_mention("행복한의원", "행복한 진료를 위해 노력합니다.")

    assert parsed["verdict"] == "NOT_MATCHED"
    assert parsed["is_mentioned"] is False


@pytest.mark.asyncio
async def test_matched_with_the_suffix_stripped_trade_name_is_approved(monkeypatch):
    """"군자성모정형외과의원"을 "군자성모정형외과"로 부르는 것은 정상 언급이다.

    동일성 규칙이 접미사 차이를 명시적으로 인정하는데 corroboration이 전체 명칭만
    요구하면, AI가 상호로 부르는 게 보통이라 정상 언급이 대량 보류로 빠진다 —
    실측에서 플랫폼당 3~4건이 그렇게 빠져 하한 미달까지 갔다.
    """
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _VerdictCompletions("MATCHED", "군자성모정형외과"),
    )

    parsed = await sov_engine._parse_mention(
        "군자성모정형외과의원", "군자역 근처는 군자성모정형외과를 추천합니다."
    )

    assert parsed["verdict"] == "MATCHED"
    assert parsed["is_mentioned"] is True


class TestRecordConfirmation:
    """모든 유료 집계가 공유하는 확정 판정 기준."""

    class _Row:
        def __init__(self, status="SUCCESS", verdict=None, mentioned=True):
            self.measurement_status = status
            self.mention_verdict = verdict
            self.is_mentioned = mentioned

    def test_legacy_binary_rows_stay_confirmed(self):
        legacy = SimpleNamespace(mention_verdict=None, is_mentioned=False)
        assert sov_engine.record_is_confirmed(legacy)

    def test_success_without_boolean_is_not_confirmed(self):
        row = self._Row(verdict=None, mentioned=None)
        assert not sov_engine.record_is_confirmed(row)

    def test_ambiguous_rows_are_not_confirmed_and_not_failed(self):
        row = self._Row(verdict="AMBIGUOUS", mentioned=None)
        assert not sov_engine.record_is_confirmed(row)
        assert sov_engine.record_is_ambiguous(row)

    def test_failed_rows_are_neither(self):
        row = self._Row(status="FAILED", mentioned=None)
        assert not sov_engine.record_is_confirmed(row)
        assert not sov_engine.record_is_ambiguous(row)


@pytest.mark.asyncio
async def test_matched_with_the_full_name_quoted_is_approved(monkeypatch):
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _VerdictCompletions("MATCHED", "행복한의원"),
    )

    parsed = await sov_engine._parse_mention("행복한의원", "수서역 근처 행복한의원을 추천합니다.")

    assert parsed["verdict"] == "MATCHED"
    assert parsed["is_mentioned"] is True


def test_ambiguous_records_leave_the_mention_rate_denominator():
    """확정하지 못한 판정을 미언급으로 세면 하향, 언급으로 세면 상향 편향이다."""
    records = [
        {"is_mentioned": True, "verdict": "MATCHED", "measurement_status": "SUCCESS"},
        {"is_mentioned": False, "verdict": "NOT_MATCHED", "measurement_status": "SUCCESS"},
        {"is_mentioned": None, "verdict": "AMBIGUOUS", "measurement_status": "SUCCESS"},
    ]

    assert len(sov_engine.successful_records(records)) == 2
    assert sov_engine.calculate_sov(records, intents=None) == 50.0


def test_records_without_a_mention_key_are_still_counted():
    """집계용으로 조립된 요약 dict까지 보류로 오인해 분모에서 빼면 안 된다."""
    records = [{"measurement_status": "SUCCESS", "raw_response": "답변"}]

    assert len(sov_engine.successful_records(records)) == 1


def test_self_and_competitor_share_the_same_identity_rule():
    """자사만 넓게 잡고 경쟁사를 좁게 잡으면 '우리 vs 경쟁 병원' 비교가 기운다 — 같은 규칙을 쓴다."""
    home = sov_engine.mention_identity_for("성모탑정형외과의원", "마포구")
    answer = "1. **성모탑정형외과의원** (마포역)\n2. **바른정형외과** (부산 해운대구)"

    self_res = sov_engine.mention_identity.resolve(home, answer)
    as_competitor = sov_engine.mention_identity.resolve(
        home.for_competitor("성모탑정형외과의원"), answer
    )

    assert self_res.matched and as_competitor.matched
    assert self_res.matched_text == as_competitor.matched_text


class _MalformedCompletions:
    """판정기가 JSON을 깨뜨려 돌려주는 상황."""

    async def create(self, **kwargs):
        return SimpleNamespace(choices=[_FakeChoice("이건 JSON이 아니다")])


@pytest.mark.asyncio
async def test_second_look_failure_raises_while_competitors_need_no_judge(monkeypatch):
    # 보조 판정 장애는 미언급으로 삼키지 않는다(측정 실패로 올라가 다시 판정한다). 경쟁 병원은
    # 규칙 대조만 쓰므로 판정기 장애의 영향을 받지 않는다.
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _MalformedCompletions())

    with pytest.raises(ValueError):
        await sov_engine._parse_mention(SECOND_LOOK_NAME, SECOND_LOOK_ANSWER, SECOND_LOOK_REGION)
    parsed = await sov_engine._parse_competitors(
        ["장편한외과의원"], "장편한외과의원이 언급되었습니다."
    )
    assert parsed[0]["is_mentioned"] is True


@pytest.mark.asyncio
async def test_competitor_prefilter_skip_is_a_verdict_not_a_failure(monkeypatch):
    """사전 필터로 거른 all-false는 실패가 아니라 판정 결과다 — 예외를 던지면 안 된다."""
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _ExplodingCompletions())

    parsed = await sov_engine._parse_competitors(
        ["강남세브란스병원"], "강남역 인근 병원을 안내합니다."
    )

    assert parsed == [{"name": "강남세브란스병원", "is_mentioned": False, "mention_rank": None}]


def test_measurement_models_are_pinned_to_dated_snapshots():
    # 부동 별칭은 공급자가 갱신하면 측정 기준선을 조용히 이동시킨다(PRD F3-1).
    # OpenRouter 슬러그(`vendor/model`)로 고정한다.
    from app.core.config import Settings

    defaults = Settings.model_fields
    assert defaults["OPENAI_MODEL_QUERY"].default == "openai/gpt-6-luna"
    assert defaults["OPENAI_MODEL_PARSE"].default == "openai/gpt-4o-mini-2024-07-18"
    # Gemini도 답변 모델이므로 동일하게 고정한다. `-latest` 별칭은 기준선을 이동시킨다.
    assert defaults["GEMINI_MODEL"].default == "google/gemini-3.6-flash"
    for field in ("OPENAI_MODEL_QUERY", "OPENAI_MODEL_PARSE", "GEMINI_MODEL"):
        assert not defaults[field].default.endswith("-latest"), field


class _FlakyAnswerCompletions:
    """Chat-completions answer client failing transiently `failures` times, then answering."""

    def __init__(self, failures: int, *, error: type[Exception] = _TransientRateLimitError):
        self.failures = failures
        self.error = error
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error("provider hiccup")
        return SimpleNamespace(
            choices=[_FakeChoice("가나의원을 추천합니다.")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
            model="answer-model",
        )


def _patch_attempt_plumbing(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    # Metering hooks are stubbed out; the attempt count must not depend on them.
    monkeypatch.setattr(sov_engine, "_record_provider_attempt", _noop)
    monkeypatch.setattr(sov_engine, "_record_sov_provider_call", _noop)
    monkeypatch.setattr(sov_engine._query_chatgpt.retry, "sleep", _noop)
    monkeypatch.setattr(sov_engine._request_judge_completion.retry, "sleep", _noop)


def _patch_answer_client(monkeypatch, completions):
    monkeypatch.setattr(sov_engine.settings, "OPENAI_CHATGPT_USE_WEB_SEARCH", False)
    monkeypatch.setattr(
        sov_engine,
        "openai_query_client",
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [0, 1, 2])
async def test_fetch_answer_reports_every_tenacity_attempt_on_success(monkeypatch, failures):
    _patch_attempt_plumbing(monkeypatch)
    completions = _FlakyAnswerCompletions(failures)
    _patch_answer_client(monkeypatch, completions)

    answer = await sov_engine.fetch_answer("강남 내과 추천", "chatgpt")

    assert answer["measurement_status"] == "SUCCESS"
    assert completions.calls == failures + 1
    assert answer["provider_calls"] == failures + 1


@pytest.mark.asyncio
async def test_fetch_answer_reports_all_attempts_when_retries_are_exhausted(monkeypatch):
    _patch_attempt_plumbing(monkeypatch)
    completions = _FlakyAnswerCompletions(failures=10)
    _patch_answer_client(monkeypatch, completions)

    answer = await sov_engine.fetch_answer("강남 내과 추천", "chatgpt")

    assert answer["measurement_status"] == "FAILED"
    assert completions.calls == 3
    assert answer["provider_calls"] == 3


@pytest.mark.asyncio
async def test_fetch_answer_non_retryable_failure_is_one_attempt(monkeypatch):
    _patch_attempt_plumbing(monkeypatch)
    completions = _FlakyAnswerCompletions(failures=10, error=_QuotaError)
    _patch_answer_client(monkeypatch, completions)

    answer = await sov_engine.fetch_answer("강남 내과 추천", "chatgpt")

    assert answer["measurement_status"] == "FAILED"
    assert completions.calls == 1
    assert answer["provider_calls"] == 1


@pytest.mark.asyncio
async def test_fetch_answer_without_any_http_attempt_reports_zero(monkeypatch):
    _patch_attempt_plumbing(monkeypatch)
    monkeypatch.setattr(sov_engine.settings, "OPENAI_CHATGPT_USE_WEB_SEARCH", True)
    monkeypatch.setattr(sov_engine, "openai_query_client", SimpleNamespace(responses=None))

    answer = await sov_engine.fetch_answer("강남 내과 추천", "chatgpt")

    assert answer["measurement_status"] == "FAILED"
    assert answer["provider_calls"] == 0


class _ScriptedJudgeCompletions:
    """Second-look client: transient failures first, then a NOT_MATCHED verdict."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise _TransientRateLimitError("judge hiccup")
        payload = {"verdict": "NOT_MATCHED", "matched_text": None, "evidence": None}
        return SimpleNamespace(
            choices=[_FakeChoice(json.dumps(payload))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


@pytest.mark.asyncio
async def test_judge_answer_counts_retried_second_look_attempts(monkeypatch):
    _patch_attempt_plumbing(monkeypatch)
    completions = _ScriptedJudgeCompletions(failures=2)
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", completions)

    judgment = await sov_engine.judge_answer(
        SECOND_LOOK_NAME,
        SECOND_LOOK_ANSWER,
        region=SECOND_LOOK_REGION,
        competitors=["다라병원"],
    )

    assert judgment["measurement_status"] == "SUCCESS"
    assert judgment["verdict"] == "NOT_MATCHED"
    # 보조 판정: 일시 장애 2회 + 성공 1회. 경쟁 병원은 규칙 대조라 호출이 없다.
    assert completions.calls == 3
    assert judgment["provider_calls"] == 3


@pytest.mark.asyncio
async def test_judge_answer_resolver_match_makes_no_provider_call(monkeypatch):
    _patch_attempt_plumbing(monkeypatch)
    completions = _ScriptedJudgeCompletions(failures=10)
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", completions)

    judgment = await sov_engine.judge_answer(
        "가나의원", "가나의원과 다라병원을 추천합니다.", competitors=["다라병원"]
    )

    assert judgment["measurement_status"] == "SUCCESS"
    assert judgment["verdict"] == "MATCHED"
    assert judgment["competitor_mentions"] == [
        {"name": "다라병원", "is_mentioned": True, "mention_rank": 2}
    ]
    assert completions.calls == 0
    assert judgment["provider_calls"] == 0


@pytest.mark.asyncio
async def test_judge_answer_hard_failure_reports_exhausted_attempts(monkeypatch):
    _patch_attempt_plumbing(monkeypatch)
    completions = _ScriptedJudgeCompletions(failures=10)
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", completions)

    judgment = await sov_engine.judge_answer(
        SECOND_LOOK_NAME, SECOND_LOOK_ANSWER, region=SECOND_LOOK_REGION, competitors=["다라병원"]
    )

    assert judgment["measurement_status"] == "FAILED"
    assert completions.calls == 3
    assert judgment["provider_calls"] == 3


# ── 자사 판정/경쟁사 판정 분리 — 보조 분석 실패가 판정을 지우지 않는다 ──

def _good_own(verdict: str):
    async def _mention(_name, _text, _region="", identity=None):
        return {
            "verdict": verdict,
            "is_mentioned": None if verdict == "AMBIGUOUS" else verdict == "MATCHED",
            "matched_text": "가나의원" if verdict == "MATCHED" else None,
            "mention_rank": 1 if verdict == "MATCHED" else None,
        }

    return _mention


async def _always_broken_competitors(_comp, _text, _home=None):
    raise ValueError("competitor_parse_failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["MATCHED", "NOT_MATCHED", "AMBIGUOUS"])
async def test_own_verdict_survives_a_failed_competitor_parse(monkeypatch, verdict):
    # 판정 입력 fingerprint·자사 판정은 그대로, 경쟁사만 FAILED 미확정으로 남는다.
    monkeypatch.setattr(sov_engine, "_parse_mention", _good_own(verdict))
    monkeypatch.setattr(sov_engine, "_parse_competitors", _always_broken_competitors)

    judgment = await sov_engine.judge_answer(
        "가나의원",
        "가나의원을 추천합니다.",
        competitors=["다라병원", "마병원"],
        policy={"judge_model": "m", "judge_prompt_fingerprint": "fp"},
    )

    assert judgment["measurement_status"] == "SUCCESS"
    assert judgment["verdict"] == verdict
    expected_mention = None if verdict == "AMBIGUOUS" else verdict == "MATCHED"
    assert judgment["is_mentioned"] is expected_mention
    assert judgment["failure_reason"] is None
    assert "judgment_input_fingerprint" in judgment
    mentions = judgment["competitor_mentions"]
    assert len(mentions) == 2
    for entry in mentions:
        assert entry["is_mentioned"] is None
        assert entry["mention_rank"] is None
        assert entry["measurement_status"] == "FAILED"
        assert entry["failure_reason"]
    assert isinstance(judgment["provider_calls"], int)


@pytest.mark.asyncio
async def test_self_parse_failure_still_fails_the_whole_judgment(monkeypatch):
    async def _broken(_name, _text, _region="", identity=None):
        raise ValueError("mention_parse_failed")

    competitor_calls = {"n": 0}

    async def _competitors(_comp, _text, _home=None):
        competitor_calls["n"] += 1
        return [{"name": "다라병원", "is_mentioned": True, "mention_rank": 1}]

    monkeypatch.setattr(sov_engine, "_parse_mention", _broken)
    monkeypatch.setattr(sov_engine, "_parse_competitors", _competitors)

    judgment = await sov_engine.judge_answer(
        "가나의원", "가나의원을 추천합니다.", competitors=["다라병원"]
    )

    assert judgment["measurement_status"] == "FAILED"
    assert judgment["failure_reason"] == "mention_parse_failed"
    assert competitor_calls["n"] == 0  # 자사 판정이 실패하면 보조 분석은 시작도 안 한다.


@pytest.mark.asyncio
async def test_competitor_soft_time_limit_still_propagates(monkeypatch):
    from billiard.exceptions import SoftTimeLimitExceeded

    monkeypatch.setattr(sov_engine, "_parse_mention", _good_own("MATCHED"))

    async def _timeout(_comp, _text, _home=None):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(sov_engine, "_parse_competitors", _timeout)
    with pytest.raises(SoftTimeLimitExceeded):
        await sov_engine.judge_answer(
            "가나의원", "가나의원을 추천합니다.", competitors=["다라병원"]
        )

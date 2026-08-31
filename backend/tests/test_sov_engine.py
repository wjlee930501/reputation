import json
from types import SimpleNamespace

import pytest

from app.services import sov_engine


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


class _PayloadCompletions:
    def __init__(self, payload):
        self.payload = payload

    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[_FakeChoice(json.dumps(self.payload))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


def _mock_parse_response(monkeypatch, payload):
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _PayloadCompletions(payload),
    )


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


def test_measurement_client_disables_sdk_retry_without_weakening_judge_client():
    assert sov_engine.openai_query_client.max_retries == 0
    assert sov_engine.openai_client.max_retries > 0


class _FakeCompletions:
    async def create(self, **kwargs):
        assert kwargs["response_format"] == {"type": "json_object"}
        return SimpleNamespace(
            choices=[
                _FakeChoice(
                    json.dumps(
                        {
                            "competitors": [
                                {"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1},
                            ]
                        }
                    )
                )
            ]
        )


@pytest.mark.asyncio
async def test_parse_competitors_accepts_json_object_wrapper(monkeypatch):
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _FakeCompletions(),
    )

    parsed = await sov_engine._parse_competitors(["경쟁병원"], "경쟁병원이 먼저 언급되었습니다.")

    assert parsed == [{"name": "경쟁병원", "is_mentioned": True, "mention_rank": 1}]


@pytest.mark.parametrize(
    "metadata",
    [
        {"mention_rank": 0},
        {"mention_rank": True},
        {"sentiment": "mixed"},
        {"sentiment": []},
        {"matched_text": 123},
        {"mention_context": []},
    ],
)
async def test_parse_mention_rejects_malformed_metadata(monkeypatch, metadata):
    payload = {
        "verdict": "MATCHED",
        "matched_text": "장편한외과의원",
        "mention_rank": 1,
        "sentiment": "positive",
        "mention_context": "추천 목록",
        **metadata,
    }
    _mock_parse_response(monkeypatch, payload)

    with pytest.raises(ValueError, match="^mention_parse_failed$"):
        await sov_engine._parse_mention("장편한외과의원", "장편한외과의원을 추천합니다.")


async def test_parse_mention_accepts_valid_metadata(monkeypatch):
    payload = {
        "verdict": "MATCHED",
        "matched_text": "장편한외과의원",
        "mention_rank": 2,
        "sentiment": "neutral",
        "mention_context": "지역 병원 목록",
    }
    _mock_parse_response(monkeypatch, payload)

    parsed = await sov_engine._parse_mention(
        "장편한외과의원", "장편한외과의원이 지역 병원 목록에 있습니다."
    )

    assert parsed["mention_rank"] == 2
    assert parsed["sentiment"] == "neutral"


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "", "is_mentioned": True, "mention_rank": 1},
        {"name": 123, "is_mentioned": True, "mention_rank": 1},
        {"name": "경쟁병원", "is_mentioned": 1, "mention_rank": 1},
        {"name": "경쟁병원", "is_mentioned": True, "mention_rank": 0},
        {"name": "경쟁병원", "is_mentioned": True, "mention_rank": False},
    ],
)
async def test_parse_competitors_rejects_malformed_metadata(monkeypatch, entry):
    _mock_parse_response(monkeypatch, {"competitors": [entry]})

    with pytest.raises(ValueError, match="^competitor_parse_failed$"):
        await sov_engine._parse_competitors(["경쟁병원"], "경쟁병원이 언급되었습니다.")


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
    assert responses.kwargs["tools"] == [{"type": "web_search"}]
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
    """사전 필터는 접미사를 뗀 핵심의 substring 일치라 느슨하다.

    "행복한의원"의 핵심 "행복한"은 "행복한 진료를 위해" 같은 평범한 문장에도 걸린다.
    판정기가 그 조각을 근거로 MATCHED를 주면 오탐이 그대로 언급률이 되므로,
    근거 인용이 병원명 전체를 담지 않으면 승격시키지 않는다.
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
async def test_matched_with_a_fabricated_quote_is_downgraded(monkeypatch):
    """답변에 없는 문자열을 근거랍시고 지어내면 근거가 아니다."""
    monkeypatch.setattr(
        sov_engine.openai_client.chat,
        "completions",
        _VerdictCompletions("MATCHED", "행복한의원"),
    )

    parsed = await sov_engine._parse_mention("행복한의원", "행복한 진료를 위해 노력합니다.")

    assert parsed["verdict"] == "AMBIGUOUS"


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


def test_both_parse_prompts_share_the_same_identity_rule():
    assert sov_engine._IDENTITY_RULE in sov_engine.PARSE_PROMPT
    assert sov_engine._IDENTITY_RULE in sov_engine.COMPETITOR_PARSE_PROMPT
    # 경쟁사에만 적용되던 느슨한 기준이 남아 있으면 안 된다.
    assert "앞 2~3글자 일치 시 동일 병원" not in sov_engine.COMPETITOR_PARSE_PROMPT


class _MalformedCompletions:
    """판정기가 JSON을 깨뜨려 돌려주는 상황."""

    async def create(self, **kwargs):
        return SimpleNamespace(choices=[_FakeChoice("이건 JSON이 아니다")])


@pytest.mark.asyncio
async def test_competitor_parse_failure_raises_like_self_judgment(monkeypatch):
    # 같은 판정기 장애인데 자사는 FAILED(분모 제외), 경쟁사는 "미언급 SUCCESS"로
    # 집계되면 경쟁사 언급률이 구조적으로 낮게 나온다. 양쪽 다 실패로 올라와야 한다.
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _MalformedCompletions())

    with pytest.raises(ValueError):
        await sov_engine._parse_mention("장편한외과의원", "장편한외과의원이 언급되었습니다.")
    with pytest.raises(ValueError):
        await sov_engine._parse_competitors(
            ["장편한외과의원"], "장편한외과의원이 언급되었습니다."
        )


@pytest.mark.asyncio
async def test_competitor_parse_rejects_wrong_typed_items(monkeypatch):
    class _WrongType:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    _FakeChoice(
                        json.dumps({"competitors": [{"name": "경쟁병원", "is_mentioned": "yes"}]})
                    )
                ]
            )

    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _WrongType())

    with pytest.raises(ValueError):
        await sov_engine._parse_competitors(["경쟁병원"], "경쟁병원이 언급되었습니다.")


@pytest.mark.asyncio
async def test_competitor_prefilter_skip_is_a_verdict_not_a_failure(monkeypatch):
    """사전 필터로 거른 all-false는 실패가 아니라 판정 결과다 — 예외를 던지면 안 된다."""
    monkeypatch.setattr(sov_engine.openai_client.chat, "completions", _ExplodingCompletions())

    parsed = await sov_engine._parse_competitors(
        ["강남세브란스병원"], "강남역 인근 병원을 안내합니다."
    )

    assert parsed == [{"name": "강남세브란스병원", "is_mentioned": False, "mention_rank": None}]


def test_measurement_models_are_pinned_to_dated_snapshots():
    # 부동 별칭은 OpenAI가 갱신하면 측정 기준선을 조용히 이동시킨다(PRD F3-1).
    from app.core.config import Settings

    defaults = Settings.model_fields
    assert defaults["OPENAI_MODEL_QUERY"].default == "gpt-5.6-luna"
    assert defaults["OPENAI_MODEL_PARSE"].default == "gpt-4o-mini-2024-07-18"
    # Gemini도 답변 모델이므로 동일하게 고정한다. `-latest` 별칭은 기준선을 이동시킨다.
    assert defaults["GEMINI_MODEL"].default == "gemini-3.6-flash"
    for field in ("OPENAI_MODEL_QUERY", "OPENAI_MODEL_PARSE", "GEMINI_MODEL"):
        assert not defaults[field].default.endswith("-latest"), field

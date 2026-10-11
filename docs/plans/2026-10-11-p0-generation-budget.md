# Phase 0 — 생성 예산(0-1·0-2) 구현 계획

작성: 2026-10-11 · 대상: 감사 계획 §4 Phase 0의 0-1, 0-2 · 기준 커밋 `8c8f6e60`(main)

검증 방식: 두 수정 모두 저장소 사본(스크래치 복사본)에 적용해 **수정 전 실패 → 수정 후 통과**를 실제로 돌려 확인했다.
- 0-1 새 단위 테스트 10건: 원본 코드에서 10 FAILED, 수정본에서 71/71 PASS(`tests/test_generation_retry_policy.py` 전체).
- 0-2 새 단위 테스트 2건: 원본 코드에서 `assert 2 == 4`로 1 FAILED(재배달 테스트는 회귀 방지라 원본에서도 통과), 수정본에서 2/2 PASS.
- 관련 단위 묶음(`test_tasks_nightly`·`test_topic_swap_fallback`·`test_swapped_slot_retry_record`·`test_specialty_compatibility`·`test_generation_retry_policy`·`test_question_wellformed`·`test_content_yield`·`test_generation_lease_retry`·`test_operator_retry_gate_record`·`test_reference_operator_decides`): 수정본 584 passed, 3 failed — 그 3건(`test_overnight_recovery_records_failure_without_individual_notification`, `test_generate_single_content_item_stays_draft_until_manual_publish`, `test_regeneration_discards_its_result_when_the_slot_was_cancelled`)은 **원본에서도 똑같이 실패**한다(`REDIS_URL is not set` — 이 작업 PC에 테스트 Redis env가 없음). 이번 변경과 무관.
- 통합 테스트(`*_postgres.py`)는 이 PC에 Postgres(5432/5434)가 떠 있지 않아 **수집(collect)만 확인**했다(20 tests collected). 실행은 CI 또는 DB를 띄운 뒤 아래 명령으로 한다.
- `ruff check` 수정 파일 6개 통과.

실행 전제: 단위 테스트는 `cd backend && .venv/bin/python -m pytest ...`(conftest가 env를 깐다). 통합 테스트는 `INTEGRATION_DATABASE_URL`(예: `postgresql://postgres:postgres@localhost:5432/reputation_test`, CI backend 잡 env와 같음)을 export하고 `alembic upgrade head`가 된 DB가 필요하다.

---

### Task 0-1: 주제 교체 뒤 새 주제가 작가·검수·본문 전송 예산을 새로 받는다

**Files:**
- Modify: `backend/app/workers/generation_attempt_state.py:42-49`(상수), `:277-280`(검증), `:336-346` 뒤(주제별 사용량 도우미 신설), `:366-384`(`reserve_acquisition` 상한 판정), `:485-502`(`record_topic_swap`)
- Test: `backend/tests/test_generation_retry_policy.py`(단위 7종·10건 추가), `backend/tests/test_topic_swap_fallback.py:241`(단언 추가), `backend/tests/integration/test_topic_swap_fallback_postgres.py`(통합 1건 추가)

**조사 결과:**
- 결함 재현 확인. 원본 코드에서 `WRITER` 6회 RESULT → `record_topic_swap` → `reserve_acquisition(kind="WRITER")`가 `GenerationBudgetExceeded("WRITER_RESULTS_EXHAUSTED")`. 작가·검수 6회씩(전송 3회씩) 쓴 뒤 교체하면 `WRITER`·`REVIEWER` 둘 다 즉시 소진이다.
- `record_topic_swap`(`generation_attempt_state.py:485-502`)은 `topics`·`topic_swaps`만 바꾼다. 상한 판정(`:367-384`)은 평생 계수 `writer_results`·`reviewer_results`·`text_http_attempts`를 그대로 쓴다.
- **감사 문구("0으로 리셋")를 그대로 하면 안 된다.** `_validate_versioned_budget`(`:267-278`)는 `writer_results == legacy_baseline + acquisitions에서 다시 센 값`을 강제한다. 계수를 0으로 되돌리면 그 등식이 깨져 **모든 교체 슬롯이 `LEGACY_SPEND_UNKNOWN`**(사람의 감사 재설정이 필요한 상태)이 된다. 그래서 평생 계수는 그대로 두고, 교체 시점의 획득 위치(`acquisition_count`)를 `prior_topic_spend`에 남겨 **그 위치 뒤의 획득만 현재 주제의 지출로 센다.** 효과는 "0으로 리셋"과 같고 지출 사실·검증 불변식은 보존된다.
- **`GENERATION_BUDGET_VERSION`(=2)은 올리지 않는다.** `:178`이 `version != GENERATION_BUDGET_VERSION`이면 `LEGACY_SPEND_UNKNOWN`을 던지므로, 올리는 순간 운영의 모든 v2 예산이 자동 생성 불가가 된다. 새 키는 선택 필드로 추가하고, 기존 검증은 모르는 키를 무시하므로(`:288` `return dict(raw)`) 롤백한 옛 코드도 그대로 읽는다(옛 코드는 교체 주제를 다시 소진으로 읽을 뿐 — 보수적).
- **이미 교체된 운영 슬롯(표시 없음)의 migration-on-read:** 획득 목록은 append-only·시간순이고 작가 획득 id에 주제가 들어간다(`tasks.py:2328` `writer:{context_id}:{topic_id}:…`, `topic_id = str(item.query_target_id)`, `topics`도 `str(query_target_id)` — `topic_swap_fallback.py:309-313`). 그래서 `topic_swaps>=1`인데 표시가 없으면 "새 주제(`topics[-1]`)의 첫 WRITER 획득 위치"를 경계로, 없으면 목록 끝을 경계로 본다. 지금 교체 뒤 3일 공회전 중인 슬롯은 배포 뒤 다음 적격 스윕에서 실제로 작가를 산다. 이미 `OPERATOR_REQUIRED`로 굳은 슬롯은 `retry_class`가 그대로라 자동으로 되살아나지 않는다(그건 Phase 1 백필 범위).
- `text_http_attempts` 이월 확인: 주제당 `TEXT_HTTP_ATTEMPT_LIMIT=36` = 작가 6 + 검수 6 획득 × 전송 3. 옛 주제가 다 쓰면 새 주제의 첫 예약이 `TEXT_HTTP_ATTEMPTS_EXHAUSTED`가 될 수 있으므로 **본문 전송도 주제별**로 센다(평생 72).
- **이미지 전송(`IMAGE_HTTP_ATTEMPT_LIMIT=12`)은 슬롯 평생 그대로 둔다.** 교체는 옛 이미지를 비우지만(`topic_swap_fallback.py:331-339`), 이미지 부재는 발행을 막지 않는다(CLAUDE.md 이미지 정책). 비용 상한을 키우지 않는 쪽을 택했다 — 열린 질문 Q2.
- 예산을 보존하는 다른 경로 확인: `_remember_generation_attempt`(`tasks.py:1398-1399`)·`_release_generation_attempt_for_repair`(`tasks.py:1540-1542`)·`released_generation_attempt`(`generation_attempt_state.py:727-744`) 모두 `budget` dict를 통째로 옮기므로 새 키가 따라간다. `topic_swap_budget_left`(`topic_swap_fallback.py:164-175`)는 `topic_swaps`·`topics`만 보므로 영향 없음. Admin `operations.py:999`는 스냅샷만 읽는다.
- **감사 가정과 다른 점(남는 공회전):** 주제당 작가 상한 6은 하루 예산(`SAMPLE_BODY_DAILY_BUDGET=2` 세션 × `MAX_GENERATIONS_PER_SESSION=3`)으로 **첫날에 다 쓴다.** 이 수정 뒤에도 각 주제의 2·3일째는 `GENERATION_BUDGET_EXHAUSTED`(SAMPLE_BODY_CODES, `generation_retry_policy.py:103`)로 지출 없이 소진 일수만 쌓는다. 즉 사다리는 "1일 실구매 + 2일 공회전 → 교체 → 1일 실구매 + 2일 공회전 → 사람"이 된다. 교체 주제가 실제로 구매된다는 F1의 핵심은 고치지만, 공회전 자체를 없애려면 Q1의 결정이 필요하다.

- [ ] **Step 1: 실패하는 단위 테스트 작성**

`backend/tests/test_generation_retry_policy.py` 상단 import를 아래로 바꾼다(`LIFETIME_WRITER_RESULT_LIMIT`·`TEXT_HTTP_ATTEMPT_LIMIT`·`record_topic_swap` 추가):

```python
from app.workers.generation_attempt_state import (
    LIFETIME_WRITER_RESULT_LIMIT,
    TEXT_HTTP_ATTEMPT_LIMIT,
    GenerationBudgetExceeded,
    GenerationBudgetUnknown,
    acquisition_completed,
    fresh_generation_attempt,
    read_generation_budget,
    record_topic_swap,
    replace_unknown_legacy_budget,
    reserve_acquisition,
    transport_attempt_started,
)
```

파일 끝에 추가:

```python
# ── 주제 교체 뒤 예산(0-1) ───────────────────────────────────────────────────


def _buy(attempt, *, kind, topic, count, transports=1, start=0):
    for index in range(start, start + count):
        acquisition_id = f"{kind.lower()}:ctx:{topic}:{index}"
        attempt = reserve_acquisition(attempt, acquisition_id=acquisition_id, kind=kind)
        attempt = acquisition_completed(
            attempt,
            acquisition_id=acquisition_id,
            transport_attempts=transports,
            outcome="RESULT",
            payload={"index": index},
        )
    return attempt


def test_swapped_topic_can_buy_a_writer_after_the_old_topic_spent_all_six() -> None:
    # Given: 옛 주제가 작가 결과 6회를 다 썼다(실패 세션 2개면 첫날에 닿는다).
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-a:7", kind="WRITER")

    # When: 슬롯 평생 한 번의 주제 교체를 기록한다.
    swapped = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")

    # Then: 새 주제의 첫 작가 호출이 실제로 예약된다.
    reserved = reserve_acquisition(
        swapped, acquisition_id="writer:ctx:topic-b:0", kind="WRITER"
    )
    assert reserved["budget"]["acquisitions"][-1]["status"] == "RESERVED"


def test_swapped_topic_has_its_own_six_and_the_slot_lifetime_stays_twelve() -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=6)

    budget = read_generation_budget(attempt)
    assert budget.writer_results == LIFETIME_WRITER_RESULT_LIMIT == 12
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-b:7", kind="WRITER")


def test_swap_reopens_reviewer_and_text_transport_but_not_image_transport() -> None:
    # Given: 옛 주제가 작가·검수 6회씩, 본문 전송 36회, 이미지 전송 12회를 다 썼다.
    attempt = fresh_generation_attempt(topic_id="topic-a")
    attempt = _buy(attempt, kind="WRITER", topic="topic-a", count=6, transports=3)
    attempt = _buy(attempt, kind="REVIEWER", topic="topic-a", count=6, transports=3)
    attempt = _buy(attempt, kind="IMAGE", topic="topic-a", count=4, transports=3)
    assert read_generation_budget(attempt).text_http_attempts == TEXT_HTTP_ATTEMPT_LIMIT

    swapped = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")

    reserve_acquisition(swapped, acquisition_id="writer:ctx:topic-b:0", kind="WRITER")
    reserve_acquisition(swapped, acquisition_id="reviewer:ctx:new", kind="REVIEWER")
    # 이미지는 주제가 아니라 슬롯의 지출이다 — 교체로 다시 열리지 않는다.
    with pytest.raises(GenerationBudgetExceeded, match="IMAGE_HTTP_ATTEMPTS_EXHAUSTED"):
        reserve_acquisition(swapped, acquisition_id="image:new", kind="IMAGE")


def test_swap_records_the_old_topic_spend_without_erasing_lifetime_counters() -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"),
        kind="WRITER",
        topic="topic-a",
        count=6,
        transports=2,
    )

    swapped = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")

    assert swapped["budget"]["prior_topic_spend"] == {
        "acquisition_count": 6,
        "writer_results": 6,
        "reviewer_results": 0,
        "text_http_attempts": 12,
    }
    budget = read_generation_budget(swapped)
    assert (budget.writer_results, budget.text_http_attempts, budget.topic_swaps) == (6, 12, 1)


def test_pre_fix_swapped_budget_without_marker_derives_the_new_topic_boundary() -> None:
    # Given: 이 수정 이전에 교체된 슬롯 — topic_swaps=1이지만 표시가 없다.
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt["budget"]["topics"] = ["topic-a", "topic-b"]
    attempt["budget"]["topic_swaps"] = 1
    assert "prior_topic_spend" not in attempt["budget"]

    # Then: 새 주제의 작가 획득이 아직 없으면 목록 전체를 옛 주제의 지출로 본다.
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=2)
    # 새 주제가 2회를 썼으므로 4회가 더 남는다.
    attempt = _buy(attempt, kind="WRITER", topic="topic-b", count=4, start=2)
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-b:9", kind="WRITER")


def test_unswapped_budget_keeps_the_existing_six_result_ceiling() -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    assert "prior_topic_spend" not in attempt["budget"]
    with pytest.raises(GenerationBudgetExceeded, match="WRITER_RESULTS_EXHAUSTED"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-a:6", kind="WRITER")


@pytest.mark.parametrize(
    "malformation",
    ["not_object", "count_past_end", "without_swap", "string_counter", "counter_above_lifetime"],
)
def test_malformed_prior_topic_spend_never_mints_allowance(malformation: str) -> None:
    attempt = _buy(
        fresh_generation_attempt(topic_id="topic-a"), kind="WRITER", topic="topic-a", count=6
    )
    attempt = record_topic_swap(attempt, from_topic_id="topic-a", to_topic_id="topic-b")
    prior = attempt["budget"]["prior_topic_spend"]
    if malformation == "not_object":
        attempt["budget"]["prior_topic_spend"] = "bad"
    elif malformation == "count_past_end":
        prior["acquisition_count"] = 7
    elif malformation == "without_swap":
        attempt["budget"]["topic_swaps"] = 0
    elif malformation == "string_counter":
        prior["writer_results"] = "6"
    elif malformation == "counter_above_lifetime":
        prior["writer_results"] = 7

    with pytest.raises(GenerationBudgetUnknown, match="LEGACY_SPEND_UNKNOWN"):
        reserve_acquisition(attempt, acquisition_id="writer:ctx:topic-b:0", kind="WRITER")
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_generation_retry_policy.py -q`
Expected: 수집 단계에서 `ImportError: cannot import name 'LIFETIME_WRITER_RESULT_LIMIT'`. (import 한 줄을 잠시 빼고 돌리면 새 테스트 10건이 FAIL — 대표 메시지 `GenerationBudgetExceeded: WRITER_RESULTS_EXHAUSTED`, `KeyError: 'prior_topic_spend'`. `test_unswapped_budget_keeps_the_existing_six_result_ceiling`은 회귀 방지라 수정 전에도 통과.)

- [ ] **Step 3: 최소 구현** — 아래 diff를 그대로 적용한다.

```diff
--- a/backend/app/workers/generation_attempt_state.py
+++ b/backend/app/workers/generation_attempt_state.py
@@ -47,6 +47,14 @@
 SEMANTIC_TRANSPORT_LIMIT = 3
 TEXT_HTTP_ATTEMPT_LIMIT = 36
 IMAGE_HTTP_ATTEMPT_LIMIT = 12
+# 주제 교체는 새 주제에 작가·검수·본문 전송 예산을 처음부터 준다. 슬롯 평생 상한은
+# 주제 수(TOPIC_LIMIT)만큼이다 — 교체가 평생 한 번뿐이므로 이 곱이 실제 최대 지출이다.
+# 이미지 전송은 주제가 아니라 슬롯의 것이라 평생 IMAGE_HTTP_ATTEMPT_LIMIT 그대로다.
+LIFETIME_WRITER_RESULT_LIMIT = TOPIC_LIMIT * WRITER_RESULT_LIMIT
+LIFETIME_REVIEWER_RESULT_LIMIT = TOPIC_LIMIT * REVIEWER_RESULT_LIMIT
+LIFETIME_TEXT_HTTP_ATTEMPT_LIMIT = TOPIC_LIMIT * TEXT_HTTP_ATTEMPT_LIMIT
+PRIOR_TOPIC_SPEND_KEY = "prior_topic_spend"
+_PRIOR_TOPIC_COUNTERS = ("writer_results", "reviewer_results", "text_http_attempts")
 
 AcquisitionKind = Literal["WRITER", "REVIEWER", "IMAGE"]
 AcquisitionOutcome = Literal["RESULT", "ERROR", "UNKNOWN"]
@@ -277,6 +285,25 @@
     ):
         raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
 
+    prior = raw.get(PRIOR_TOPIC_SPEND_KEY)
+    if prior is not None:
+        count = (
+            _strict_nonnegative_int(prior.get("acquisition_count"))
+            if isinstance(prior, dict)
+            else None
+        )
+        if (
+            count is None
+            or count > len(acquisitions)
+            or raw["topic_swaps"] < 1
+            or any(
+                _strict_nonnegative_int(prior.get(key)) is None
+                or prior[key] > raw[key]
+                for key in _PRIOR_TOPIC_COUNTERS
+            )
+        ):
+            raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
+
     reset_record = raw.get("reset_record")
     if reset_record is not None and not isinstance(reset_record, dict):
         raise GenerationBudgetUnknown("LEGACY_SPEND_UNKNOWN")
@@ -344,6 +371,79 @@
         and entry.get("kind") == kind
         and entry.get("status") == "RESERVED"
     )
+
+
+@dataclass(frozen=True, slots=True)
+class _TopicUsage:
+    writer_results: int
+    reviewer_results: int
+    active_writers: int
+    active_reviewers: int
+    text_http_attempts: int
+    text_http_reserved: int
+
+
+def _entries(budget: dict[str, Any]) -> list[dict[str, Any]]:
+    raw = budget.get("acquisitions")
+    return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []
+
+
+def _topic_boundary(budget: dict[str, Any]) -> int | None:
+    """현재 주제의 첫 획득 위치. 교체 전이면 None — 평생 계수가 곧 현재 주제의 계수다.
+
+    교체 표시(`prior_topic_spend`)가 있으면 그 위치를 쓴다. 표시 없이 교체만 기록된 옛
+    예산(이 수정 이전의 교체)은 획득 목록이 시간순으로만 늘어난다는 사실로 경계를 찾는다 —
+    작가 획득 id에는 주제가 들어가므로(`writer:{context}:{topic}:…`) 새 주제의 첫 작가
+    획득이 경계다. 아직 없으면(교체 직후 곧바로 소진된 경우) 목록 전체가 옛 주제의 것이다.
+    """
+
+    prior = budget.get(PRIOR_TOPIC_SPEND_KEY)
+    if isinstance(prior, dict):
+        return int(prior["acquisition_count"])
+    if int(budget.get("topic_swaps") or 0) < 1:
+        return None
+    entries = _entries(budget)
+    topics = budget.get("topics") or []
+    if topics:
+        marker = f":{topics[-1]}:"
+        for index, entry in enumerate(entries):
+            if entry.get("kind") == "WRITER" and marker in str(entry.get("id") or ""):
+                return index
+    return len(entries)
+
+
+def _current_topic_usage(budget: dict[str, Any]) -> _TopicUsage:
+    boundary = _topic_boundary(budget)
+    if boundary is None:
+        return _TopicUsage(
+            writer_results=budget["writer_results"],
+            reviewer_results=budget["reviewer_results"],
+            active_writers=_active_acquisitions(budget, "WRITER"),
+            active_reviewers=_active_acquisitions(budget, "REVIEWER"),
+            text_http_attempts=budget["text_http_attempts"],
+            text_http_reserved=budget["text_http_reserved"],
+        )
+    writer = reviewer = active_writers = active_reviewers = attempts = reserved = 0
+    for entry in _entries(budget)[boundary:]:
+        kind = entry.get("kind")
+        if kind == "IMAGE":
+            continue
+        used = _nonnegative_int(entry.get("transport_attempts")) or 0
+        held = _nonnegative_int(entry.get("transport_reserved")) or 0
+        attempts += used
+        status = entry.get("status")
+        if status == "RESERVED":
+            reserved += max(0, held - used)
+            if kind == "WRITER":
+                active_writers += 1
+            else:
+                active_reviewers += 1
+        elif status in {"RESULT", "ERROR"}:
+            if kind == "WRITER":
+                writer += 1
+            else:
+                reviewer += 1
+    return _TopicUsage(writer, reviewer, active_writers, active_reviewers, attempts, reserved)
 
 
 def reserve_acquisition(
@@ -364,21 +464,32 @@
     if prior is not None:
         return {**attempt, GENERATION_BUDGET_KEY: budget}
     snapshot = _snapshot_from_budget(budget)
+    usage = _current_topic_usage(budget)
     if kind == "WRITER" and (
-        snapshot.writer_results + _active_acquisitions(budget, kind) >= WRITER_RESULT_LIMIT
+        usage.writer_results + usage.active_writers >= WRITER_RESULT_LIMIT
+        or snapshot.writer_results + _active_acquisitions(budget, kind)
+        >= LIFETIME_WRITER_RESULT_LIMIT
     ):
         raise GenerationBudgetExceeded("WRITER_RESULTS_EXHAUSTED")
     if kind == "REVIEWER" and (
-        snapshot.reviewer_results + _active_acquisitions(budget, kind)
-        >= REVIEWER_RESULT_LIMIT
+        usage.reviewer_results + usage.active_reviewers >= REVIEWER_RESULT_LIMIT
+        or snapshot.reviewer_results + _active_acquisitions(budget, kind)
+        >= LIFETIME_REVIEWER_RESULT_LIMIT
     ):
         raise GenerationBudgetExceeded("REVIEWER_RESULTS_EXHAUSTED")
     attempt_key = "image_http_reserved" if kind == "IMAGE" else "text_http_reserved"
     spent_key = "image_http_attempts" if kind == "IMAGE" else "text_http_attempts"
-    limit = IMAGE_HTTP_ATTEMPT_LIMIT if kind == "IMAGE" else TEXT_HTTP_ATTEMPT_LIMIT
     spent = _nonnegative_int(budget.get(spent_key)) or 0
     reserved = _nonnegative_int(budget.get(attempt_key)) or 0
-    if spent + reserved + SEMANTIC_TRANSPORT_LIMIT > limit:
+    if kind == "IMAGE":
+        exhausted = spent + reserved + SEMANTIC_TRANSPORT_LIMIT > IMAGE_HTTP_ATTEMPT_LIMIT
+    else:
+        exhausted = (
+            usage.text_http_attempts + usage.text_http_reserved + SEMANTIC_TRANSPORT_LIMIT
+            > TEXT_HTTP_ATTEMPT_LIMIT
+            or spent + reserved + SEMANTIC_TRANSPORT_LIMIT > LIFETIME_TEXT_HTTP_ATTEMPT_LIMIT
+        )
+    if exhausted:
         raise GenerationBudgetExceeded(
             "IMAGE_HTTP_ATTEMPTS_EXHAUSTED" if kind == "IMAGE" else "TEXT_HTTP_ATTEMPTS_EXHAUSTED"
         )
@@ -485,7 +596,12 @@
 def record_topic_swap(
     attempt: dict[str, Any], *, from_topic_id: str | None, to_topic_id: str
 ) -> dict[str, Any]:
-    """Record the sole lifetime topic swap while preserving all provider spend."""
+    """Record the sole lifetime topic swap; the new topic gets a fresh per-topic budget.
+
+    평생 계수(writer_results 등)는 그대로 두고 — 지출 사실은 지우지 않는다 — 교체 시점의
+    획득 위치와 계수를 `prior_topic_spend`로 남긴다. `reserve_acquisition`은 그 위치 뒤의
+    획득만 현재 주제의 지출로 센다. 평생 상한은 TOPIC_LIMIT × 주제당 상한이다.
+    """
 
     budget = _budget_dict(attempt)
     snapshot = _snapshot_from_budget(budget)
@@ -499,6 +615,12 @@
         raise GenerationBudgetExceeded("TOPIC_LIMIT_EXHAUSTED")
     budget["topics"] = topics
     budget["topic_swaps"] = snapshot.topic_swaps + 1
+    budget[PRIOR_TOPIC_SPEND_KEY] = {
+        "acquisition_count": len(_entries(budget)),
+        "writer_results": snapshot.writer_results,
+        "reviewer_results": snapshot.reviewer_results,
+        "text_http_attempts": snapshot.text_http_attempts,
+    }
     return {**attempt, GENERATION_BUDGET_KEY: budget}
 
 
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_generation_retry_policy.py -q`
Expected: `71 passed`

- [ ] **Step 5: 교체 단위 테스트에 표시 단언 추가**

`backend/tests/test_topic_swap_fallback.py:241`의 `assert swapped_attempt["budget"]["topic_swaps"] == 1` 바로 아래에:

```python
    # 새 주제의 예산은 이 위치 뒤의 획득만 센다(평생 계수는 그대로 남는다).
    assert swapped_attempt["budget"]["prior_topic_spend"] == {
        "acquisition_count": 0,
        "writer_results": 0,
        "reviewer_results": 0,
        "text_http_attempts": 0,
    }
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_topic_swap_fallback.py tests/test_swapped_slot_retry_record.py tests/test_specialty_compatibility.py -q`
Expected: 전부 passed.

- [ ] **Step 6: 통합 테스트 — "교체 뒤 첫 작가 호출이 실제로 구매됨"**

`backend/tests/integration/test_topic_swap_fallback_postgres.py:28-32` import에 `GenerationBudgetLedger`를 추가한다:

```python
from app.workers.generation_attempt_state import (
    GENERATION_ATTEMPT_KEY,
    WRITER_RESULT_LIMIT,
    GenerationBudgetLedger,
    fresh_generation_attempt,
)
```

파일 끝에 추가(시드 `_exhausted_summary`는 작가 6회 RESULT로 이미 소진된 예산이다, `:42-66`):

```python
def test_the_swapped_topic_actually_buys_its_first_writer_call(pg_conn, pg_session):
    """교체된 주제의 첫 작가 호출이 옛 주제의 소진으로 막히지 않는다(0-1).

    옛 주제가 작가 결과 6회를 다 쓴 슬롯을 교체한 뒤, 정본 원장(`GenerationBudgetLedger`)이
    새 주제의 작가 획득을 실제로 예약하는지 본다. 종전에는 교체가 `topics`만 바꾸고 예산을
    이월해 첫 `reserve`가 곧바로 WRITER_RESULTS_EXHAUSTED였다.
    """

    hospital_id = _seed_hospital(pg_conn)
    item_id = _seed_item(pg_conn, hospital_id, scheduled_date=SLOT - timedelta(days=1))

    assert _swap(pg_session).swapped == 1
    row = pg_session.get(ContentItem, item_id)
    pg_session.refresh(row)

    ledger = GenerationBudgetLedger(pg_session, row)
    reservation = ledger.reserve(
        acquisition_id=f"writer:ctx:{row.query_target_id}:claim:first", kind="WRITER"
    )

    assert reservation.cached_payload is None
    pg_session.refresh(row)
    budget = row.essence_check_summary[GENERATION_ATTEMPT_KEY]["budget"]
    # 지출 사실은 지우지 않는다 — 평생 계수는 옛 주제의 6회 그대로다.
    assert budget["writer_results"] == WRITER_RESULT_LIMIT
    assert budget["prior_topic_spend"]["writer_results"] == WRITER_RESULT_LIMIT
    assert budget["prior_topic_spend"]["acquisition_count"] == WRITER_RESULT_LIMIT
    assert budget["acquisitions"][-1]["status"] == "RESERVED"
```

Run: `cd backend && INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/reputation_test .venv/bin/python -m pytest tests/integration/test_topic_swap_fallback_postgres.py -q`
Expected: 수정 전에는 새 테스트가 `GenerationBudgetExceeded: WRITER_RESULTS_EXHAUSTED`로 FAIL, 수정 뒤 `20 passed`. (Step 3 전에 이 테스트를 먼저 돌려 실패를 보고 싶으면 Step 6을 Step 2 뒤로 당긴다.)

- [ ] **Step 7: 커밋**

```bash
git add backend/app/workers/generation_attempt_state.py backend/tests/test_generation_retry_policy.py backend/tests/test_topic_swap_fallback.py backend/tests/integration/test_topic_swap_fallback_postgres.py
git commit -m "fix(generation): 주제 교체 뒤 새 주제가 작가·검수·본문 전송 예산을 새로 받는다"
```

**계약 영향:** CLAUDE.md "LLM 출력은 확률적이므로 …" 문단의 주제 교체 문장 뒤에 한 문장을 더한다:
"교체된 주제는 작가·검수 결과 각 6회와 본문 전송 36회를 새로 받는다 — 평생 계수는 지우지 않고 교체 시점의 획득 위치(`prior_topic_spend`)부터 센다. 슬롯 평생 상한은 주제 수 × 주제당 상한(작가 12회)이고, 이미지 전송 상한(12회)은 슬롯 단위로 유지한다."
의료·사실·tenant·공개 권한 검사, 비용 가드, 교체 1회 상한은 바뀌지 않는다.

**위험·롤백:**
- 비용: 교체된 슬롯의 최악 텍스트 지출이 작가 6→12, 검수 6→12, 전송 36→72회로 늘어난다(감사 계획의 "슬롯당 비용 상한 2배"). 교체는 슬롯 평생 1회이고 Redis 비용 가드(`cost_guard.check_and_increment("content")`)가 세션마다 그대로 막는다.
- 운영 데이터 쓰기 없음(읽을 때 해석). 교체 공회전 중이던 슬롯이 배포 직후 스윕에서 한꺼번에 작가를 살 수 있다 — 배포 전 `topic_swaps=1`·`retry_class=SAMPLE_RECOVERABLE` 슬롯 수를 세어 둔다:
  `SELECT count(*) FROM content_items WHERE (essence_check_summary->'generation_attempt'->'budget'->>'topic_swaps')::int >= 1 AND essence_check_summary->'generation_attempt'->>'retry_class' = 'SAMPLE_RECOVERABLE';`
- 롤백: 커밋 revert 후 재배포. 새로 저장된 `prior_topic_spend`는 옛 코드가 무시하므로 남겨도 안전하다(그 슬롯은 다시 소진으로 읽힌다).

---

### Task 0-2: 작가 결과 재생 범위를 claim(세션) 하나로 묶는다

**Files:**
- Modify: `backend/app/workers/tasks.py:2320-2328`(`_generate_with_auto_review` 안 `_budgeted_generate`의 키)
- Test: `backend/tests/test_tasks_nightly.py`(단위 2건 + import 1줄)

**조사 결과:**
- 결함 재현 확인. `generation_claim_token`이 다른 두 세션에서 작가가 매번 검사 제목에 '치료'를 붙이면(`content_generation_review.py:202-216`의 `ValueError("Exam title …")`, 본문 미저장) 첫 세션 작가 2회, **두 번째 세션 작가 0회**(초안·보완 키가 같아 `ledger.latest_payload` 캐시 재생, 검수도 같은 후보 sha라 재생). 표본 예산·소진 일수만 오른다.
- 키 `writer:{context_id}:{topic_id}:{sha(findings)}`(`tasks.py:2328`)에 세션 식별자가 없다. 감사의 "검수 차단 후보는 DRAFT 저장 뒤 지적이 바뀌어 재생되지 않는다"는 정정도 맞다 — 재생은 본문이 저장되지 않는 실패에서만 생긴다.
- 쓸 수 있는 식별자: `item.generation_claim_token`. 두 호출부 모두 lease를 잡은 뒤 들어온다 — 야간·복구 디스패치는 claim 시 `uuid.uuid4()`(`nightly_generation_batch.py:281`, `:563`)를 행에 쓰고 그 토큰을 서명 봉투 인자로 넘기며(`tasks.py:5259-5263`), 운영자 재생성(`regenerate_content_item`)도 `claim_generation_lease`(`tasks.py:6101`, `nightly_generation_batch.py:281-285`)로 새 토큰을 받는다. 같은 claim의 재배달(서명 봉투 재배달·cold shutdown 반납·자율 복구 재배달)은 같은 토큰을 가지므로 같은 키 → 멱등 재생이 유지된다.
- OperationRun id를 쓰지 않는 이유: 자율 복구 재배달은 같은 실행을 다시 배달하므로 의미는 같지만, `_generate_with_auto_review`까지 run id가 전달되지 않는다(시그니처 `tasks.py:2286-2294`). claim 토큰은 이미 `item`에 있다.
- 저장된 시도 계수(`provider_attempt_count`)를 키에 넣는 대안도 검토했다 — 원인이 바뀌면 계수가 0부터 다시 시작해(`tasks.py:1335-1352`) 서로 다른 세션이 같은 키를 갖는 충돌이 생긴다. 기각.
- 다른 `latest_payload` 사용처 없음(`grep latest_payload app` → `tasks.py:2329`뿐). 이미지 키 `image:{item.id}:{n}`(`tasks.py:1951-1952`)와 검수 키 `reviewer:{context_id}:{candidate_sha}`(`tasks.py:2373`)는 바꾸지 않는다 — 같은 후보의 검수 재생은 결정적이고 정당하다.
- 토큰을 `topic_id` **뒤**에 둔다: Task 0-1의 옛 교체 슬롯 경계 판정이 `:{topic}:` 표식을 찾으므로 그 모양을 유지한다.
- claim이 없는 직접 호출(단위·통합 테스트의 `_generate_with_known_zero_budget`, `test_generation_budget_postgres.py:286-357`)은 `"unclaimed"` 하나의 범위를 공유해 종전 재생 동작 그대로다 — 기존 통합 테스트의 "같은 입력 재호출은 공급자 요청 수 불변" 단언이 그대로 성립한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_tasks_nightly.py`의 import에 한 줄 추가(`from app.services.essence_engine import compute_sources_snapshot_hash` 바로 위, `:38`):

```python
from app.services.content_engine import EXAM_TREATMENT_TITLE_FINDING_PREFIX
```

파일 끝에 추가:

```python
# ── 작가 재생 범위는 claim 하나(0-2) ────────────────────────────────────────

_EXAM_TITLE_REJECTED = {
    "title": "갑상선초음파 치료 비용",
    "body": "본문",
    "target_alignment_findings": [
        f"{EXAM_TREATMENT_TITLE_FINDING_PREFIX}: 제목 '갑상선초음파 치료 비용'"
    ],
}


def _patch_exam_title_rejection(monkeypatch) -> list:
    """작가가 매번 검사 제목에 '치료'를 붙인다 — 본문이 저장되지 않는 ValueError 경로."""

    calls: list = []

    async def fake_generate(*_args, **kwargs):
        calls.append(kwargs.get("remediation_findings"))
        return dict(_EXAM_TITLE_REJECTED)

    async def allow_cost(*_args, **_kwargs):
        return SimpleNamespace(allowed=True)

    async def reviewer_pass(**_kwargs):
        return ContentAiReview(
            status=ContentAiReviewStatus.PASS,
            confidence=0.98,
            findings=(),
            summary="통과",
            model="reviewer-test",
        )

    monkeypatch.setattr(tasks, "generate_content", fake_generate)
    monkeypatch.setattr(
        tasks,
        "screen_content_against_philosophy",
        lambda *_args: SimpleNamespace(
            status="ALIGNED", summary={"blocking": False, "findings": []}
        ),
    )
    monkeypatch.setattr(tasks, "review_generated_content", reviewer_pass)
    monkeypatch.setattr(tasks.cost_guard, "check_and_increment", allow_cost)
    return calls


def _claimed_item(claim_token):
    return SimpleNamespace(
        content_type="FAQ", essence_check_summary=None, generation_claim_token=claim_token
    )


async def _session(item) -> None:
    with pytest.raises(ValueError, match="Exam title"):
        await _generate_with_known_zero_budget(
            hospital=SimpleNamespace(id=uuid.UUID(int=7)),
            item=item,
            existing_titles=[],
            philosophy=SimpleNamespace(),
            approved_brief=None,
        )


@pytest.mark.asyncio
async def test_a_new_claim_resamples_the_writer_after_an_unsaved_rejection(monkeypatch):
    # Given: 한 세션이 작가 2회(초안 + 보완)를 사고 제목 거절로 끝났다. 본문은 저장되지 않는다.
    calls = _patch_exam_title_rejection(monkeypatch)
    item = _claimed_item(uuid.uuid4())
    await _session(item)
    assert len(calls) == 2

    # When: 다음 스윕이 새 claim으로 같은 슬롯을 다시 집는다.
    item.generation_claim_token = uuid.uuid4()
    await _session(item)

    # Then: 저장된 후보를 0원에 재생하지 않고 작가를 실제로 다시 산다.
    assert len(calls) == 4
    writer_ids = [
        entry["id"]
        for entry in item.essence_check_summary["generation_attempt"]["budget"]["acquisitions"]
        if entry["kind"] == "WRITER"
    ]
    assert len(writer_ids) == len(set(writer_ids)) == 4


@pytest.mark.asyncio
async def test_same_claim_redelivery_replays_without_buying_again(monkeypatch):
    calls = _patch_exam_title_rejection(monkeypatch)
    item = _claimed_item(uuid.uuid4())
    await _session(item)

    # 같은 claim의 재배달(서명 봉투 재배달·자율 복구)은 저장된 결과를 다시 쓴다.
    await _session(item)

    assert len(calls) == 2
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tasks_nightly.py -q -k "new_claim_resamples or same_claim_redelivery"`
Expected: `test_a_new_claim_resamples_the_writer_after_an_unsaved_rejection` FAIL — `assert 2 == 4`. `test_same_claim_redelivery_replays_without_buying_again`은 PASS(회귀 방지).

- [ ] **Step 3: 최소 구현**

```diff
--- a/backend/app/workers/tasks.py
+++ b/backend/app/workers/tasks.py
@@ -2318,6 +2318,11 @@
             default=str,
         ).encode("utf-8")
     ).hexdigest()[:16]
+    # 작가 결과의 재생 범위는 한 claim(세션)이다. 같은 claim의 재배달은 저장된 결과를 다시
+    # 쓰고(중복 구매 없음), 새 claim은 새 표본을 산다. 본문이 저장되지 않은 실패(제목 거절
+    # 등)가 다음 세션에서 같은 후보를 0원에 재생해 표본 예산만 태우지 않게 한다.
+    # claim이 없는 직접 호출(테스트·도구)은 종전처럼 하나의 범위를 공유한다.
+    session_scope = str(getattr(item, "generation_claim_token", None) or "unclaimed")
 
     async def _budgeted_generate(*args, **kwargs):
         findings = [str(value) for value in (kwargs.get("remediation_findings") or [])]
@@ -2325,7 +2330,7 @@
             "\n".join(findings).encode("utf-8")
         ).hexdigest()[:16]
         topic_id = str(getattr(item, "query_target_id", "") or "unassigned")
-        acquisition_prefix = f"writer:{context_id}:{topic_id}:{fingerprint}"
+        acquisition_prefix = f"writer:{context_id}:{topic_id}:{session_scope}:{fingerprint}"
         cached = ledger.latest_payload(acquisition_prefix)
         if cached is not None:
             return cached
```

- [ ] **Step 4: 통과 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_tasks_nightly.py tests/test_question_wellformed.py tests/test_generation_lease_retry.py tests/test_operator_retry_gate_record.py -q`
Expected: 새 2건 포함 통과. 단, 테스트 Redis env가 없으면 기존 3건(`…without_individual_notification`, `…stays_draft_until_manual_publish`, `…discards_its_result_when_the_slot_was_cancelled`)이 `REDIS_URL is not set`으로 실패한다 — 수정 전에도 같다. CI env 목록(`.github/workflows/ci.yml` backend 잡)을 export하면 통과.
통합: `cd backend && INTEGRATION_DATABASE_URL=… .venv/bin/python -m pytest tests/integration/test_generation_budget_postgres.py -q` Expected: 기존 테스트 전부 통과(claim 없는 호출은 종전 범위).

- [ ] **Step 5: 커밋**

```bash
git add backend/app/workers/tasks.py backend/tests/test_tasks_nightly.py
git commit -m "fix(generation): 작가 결과 재생을 같은 claim 안으로 한정해 새 세션이 다시 표본을 산다"
```

**계약 영향:** CLAUDE.md "23:00과 복구 스윕 … 디스패처다" 문단에 한 문장 추가:
"작가 결과의 저장 재생은 같은 claim 안에서만 한다 — 같은 claim의 재배달은 산 결과를 다시 쓰고, 새 claim은 새 표본을 산다(검수는 후보 hash 단위로 재생한다)."

**위험·롤백:**
- claim 간 크래시: 작가 호출 도중 워커가 죽고 lease(30분)가 만료돼 **다른 claim**이 슬롯을 다시 집으면, 옛 claim의 `RESERVED` 획득이 주제 예산의 작가 1칸을 계속 차지한다(기존에도 크래시 예약은 보수적으로 소비로 센다 — `test_lifetime_budget_counts_crashed_reservation_conservatively`). 종전에는 같은 키라 그 예약을 재사용했다. 같은 lease 안의 재배달(cold shutdown 반납 포함)은 같은 토큰이라 영향 없다. 빈도는 낮고 상한(주제당 6)이 지출을 묶는다.
- 배포 전환: 배포 시점에 진행 중이던 세션의 옛 형식 키는 새 코드가 재생하지 않아 최대 1회 재구매. 배포는 배치 시간대 밖에서 하므로 사실상 0.
- 롤백: 커밋 revert. 저장된 새 형식 키는 옛 코드가 다시 집지 않을 뿐(재구매 1회) 데이터 문제 없음.

---

## 열린 질문(대표 결정)

- **Q1. 주제당 작가 6회 vs 3일 사다리.** 하루 2세션 × 3회 = 6이라 각 주제의 2·3일째는 지출 없는 공회전이다(Task 0-1 조사 결과). 선택지: (a) 현재 주제의 작가 예산이 소진되면(`GENERATION_BUDGET_EXHAUSTED`) 3일을 기다리지 않고 곧바로 교체 후보로 본다 — 비용 불변, 사람에게 가는 시간 4~6일 단축, `topic_swap_fallback.exhausted_body_sample_reason` 수정(S). (b) 주제당 작가 상한을 18(2세션×3회×3일)로 올린다 — 슬롯 최악 비용 3배. 권장은 (a).
- **Q2. 이미지 전송 예산을 교체 때 다시 열 것인가.** 이번 계획은 열지 않는다(이미지 없이도 안전한 본문은 발행된다). 옛 주제에서 이미지 12회를 다 쓴 슬롯은 새 주제가 이미지 없이(또는 병원 공용 이미지로) 나간다.

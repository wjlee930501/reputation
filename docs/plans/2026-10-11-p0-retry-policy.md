# Phase 0 — 재시도 사다리(0-3 · 0-4 · 0-5 · 0-6) 구현 계획

작성: 2026-10-11, 기준 커밋 main `8c8f6e60`. 모든 패치는 드래프팅 중 저장소 사본에 순서대로 적용해(`git apply`) 단위 테스트로 검증했다. 저장소 파일은 건드리지 않았다.

## 공통 준비

- 패치 위치(절대 경로): `/private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/`
  - `task-0-3a.diff` → `task-0-3b.diff` → `task-0-4.diff` → `task-0-5.diff` → `task-0-6a.diff` → `task-0-6b.diff` (**이 순서로만** 적용된다. `0-3-0-4-existing-tests.diff`는 폐기본이며 적용하지 않는다.)
  - 패치 경로는 저장소 루트 기준(`a/backend/...`)이다. 저장소 루트에서 `git apply --include='backend/tests/*' <patch>`로 테스트만, `git apply --include='backend/app/*' <patch>`로 구현만 적용할 수 있다.
- 단위 테스트는 DB·Redis 환경 변수 없이 돈다(`tests/conftest.py`가 `APP_ENV=test`를 세팅). 통합 테스트(`tests/integration/*_postgres.py`)는 `INTEGRATION_DATABASE_URL`(마이그레이션된 `_test` DB)이 필요하다.
- **기존 실패(기준선)**: main에서 이미 `tests/test_tasks_nightly.py`의 3건(`test_overnight_recovery_records_failure_without_individual_notification`, `test_generate_single_content_item_stays_draft_until_manual_publish`, `test_regeneration_discards_its_result_when_the_slot_was_cancelled`), `tests/test_sov_engine.py` 다수, `tests/test_incident_service.py` 17 errors(DB 필요), `tests/test_migration_upgrade_0064_to_head_postgres.py`가 실패한다. 이 계획은 그 수를 늘리지 않는다(드래프팅 중 49개 관련 파일 1,700여 건을 패치 전·후로 돌려 차이가 0건임을 확인했다 — 아래 "검증 기록").
- ruff: `cd backend && .venv/bin/python -m ruff check app/workers app/core tests/<바꾼 파일>` — 모든 패치 적용 뒤 통과 확인.

## 감사 가정과 다른 점(요약)

1. **0-3**: 감사의 "REVIEW_UNAVAILABLE처럼 백오프"는 그대로 옮기면 안 된다. 검수 장애의 지수 백오프는 *같은 후보의 연속 재검수*를 위한 것이고, 공급자 장애에는 스윕 간격(01·04·07·12·18·22·23시)이 이미 몇 시간 단위 백오프다. 하루 4회 → 다음 KST 일 초기화가 일 단위 백오프다. 대신 **3일 창**을 새 키 `environment_since`로 둔다 — 원인 문자열이 바뀌거나(PROVIDER_TIMEOUT→GENERATION_FAILED) 게이트가 덮어도 창이 새로 열리지 않게 하기 위해서다(그냥 `first_observed_at`을 쓰면 같은 문맥의 표본 실패 시각을 물려받아 즉시 소진되는 버그가 난다).
2. **0-3 분류**: OpenRouter 오류는 `content_engine._generate_content_attempt`의 tenacity(`reraise=True`, 3회)가 원래 `openai` 예외를 다시 던진다. `openai.APIConnectionError`(시간 초과가 아닌 연결 오류)도 `ConnectionError`의 하위가 아니어서 지금은 `GENERATION_FAILED`다 — 감사가 말한 5xx/429와 함께 고친다. 402(크레딧 부족)는 사람이 충전해야 하므로 `GENERATION_FAILED`로 남긴다.
3. **0-3 숨은 상한**: 슬롯 평생 예산(`generation_attempt_state.TEXT_HTTP_ATTEMPT_LIMIT=36`, `WRITER_RESULT_LIMIT=6`)이 따로 있다. 3일×4회×tenacity 3회 = 36 HTTP 시도라, 사흘 내내 실제 공급자 호출이 실패하면 3일 창보다 먼저 `GENERATION_BUDGET_EXHAUSTED`(표본 실패 → 주제 교체 계단)로 갈 수 있다. 의도된 이중 상한으로 두고 바꾸지 않는다.
4. **0-4**: 게이트가 `CONTENT_NOT_GENERATED`를 무조건 복구 가능으로 바꾸면 **회귀**가 생긴다. 게이트는 소진된 표본 실패(`CONTENT_AI_HARD_FINDING`·`INPUT_CHANGE_REQUIRED` 등 종착 기록)도 증상으로 덮는다(`_publication_block_details`는 `_STORED_EMPTY_CONTENT_BLOCK_CODES` 밖의 원인을 `CONTENT_NOT_GENERATED`로 보고). 그 기록을 ENVIRONMENT로 바꾸면 표본 사다리·주제 교체 상한을 우회해 작가 세션을 다시 산다. 그래서 **저장 기록이 없거나 환경 기록일 때만** 복구 가능으로, 종착 기록을 덮을 때는 종전처럼 OPERATOR_REQUIRED·`next_retry_at=None`으로 남긴다.
5. **0-4 `_terminal_attempt_is_unchanged`**: `attempt_is_terminal`은 `CONTENT_NOT_GENERATED`를 종착으로 보지 않는다(OPERATOR_REQUIRED는 SAMPLE_BODY_CODES·operator_decides만). 게이트 기록을 빼던 것은 `_generation_attempt_is_unchanged`(같은 문맥 + `retry_is_due` 거짓)였다 — 그 경로를 `retry_is_due`가 참이 되게 바꾸는 것이 고침의 핵심이다.
6. **0-4 기존 동결분은 배포만으로 풀리지 않는다**: 운영 DB에 이미 있는 `CONTENT_NOT_GENERATED`/OPERATOR_REQUIRED 기록(작가 0회)은 게이트가 같은 원인이면 다시 쓰지 않으므로 그대로다. 자동 해제를 넣으면 종착 기록을 덮은 옛 기록까지 풀려 비용이 새므로 넣지 않았다. 배포 뒤 1회 조치(아래 0-4 "위험·롤백")로 처리한다.
7. **0-5**: `_choose_target`이 이미 `target_fits_hospital`로 진료과 불일치를 거른다(`content_target_planner.py:209`). 그래서 `_swap_one`의 `incompatible_specialty` 분기는 사실상 죽은 방어선이고, 실제로 교체를 포기시키는 것은 "수기 목록 문서 없음"과 "유사 주제"다. 후보 순회는 그 둘에 실효가 있다.
8. **0-6 (b)**: `attempt_is_terminal`(generation_retry_policy.py:594)은 **고칠 필요가 없다**. 교체 pass(`swap_exhausted_topics`)는 로더보다 먼저, 로더의 술어와 무관한 자기 SQL로 후보를 고르므로 로더가 operator_decides 슬롯을 건너뛰어도 교체는 일어난다. 교체가 시도 기록을 `TOPIC_SWAPPED`로 바꾸면 로더도 다시 집는다.
9. **0-6 (b) 대상 코드 범위**: 수리 코드 5개 중 `FORBIDDEN_EXPRESSION`·`CONTENT_AI_REVIEW_STALE`은 `retry_class_for`가 INPUT_CHANGE_REQUIRED로 분류하는 종착이라 교체하지 않는다. 교체는 `MISSING_REFERENCES`·`FAQ_FIELDS_MISSING`·`ESSENCE_NOT_ALIGNED` 중 수리 세션 예산 3일 소진(`automatic_body_repair.exhausted_days >= 3`)인 것만이다.

## 대표 결정이 필요한 항목

- **진료비·병원 선택 참고자료 보류의 주제 교체(0-6b 옵션)**: CLAUDE.md는 "비면 그 보류는 자동 본문 수리·주제 교체 없이 곧바로 `OPERATOR_REQUIRED`(사람이 정함)다"라고 정한다. 이 계획은 코드를 넣되 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES=False`(기본 꺼짐)로 둔다. 켜면 그 슬롯이 진료비 질문 대신 수기 목록 문서가 있는 의료 주제로 바뀐다 — 진료비 질문의 노출 기회가 줄어든다. 켤지 대표가 정한다.
- **인용 없는 모델 HARD의 주제 교체(0-6a)**: CLAUDE.md "인용 없는 HARD, 승인 기준 없음, 저장 본문의 금지 표현은 `INPUT_CHANGE_REQUIRED`로 남긴다"를 인용 없는 HARD에 한해 바꾼다. 의료·사실 검사를 느슨하게 하지 않는다(그 글은 버려지고, 새 주제의 글은 같은 독립 검수를 처음부터 받는다). 감사 계획에 포함된 변경이므로 플래그 없이 넣되, 계약 문구 변경으로 대표 확인을 요청한다.

---

### Task 0-3a: 공급자 장애 예산을 KST 일 단위로 초기화하고 3일 창으로 닫기

**Files:**
- Modify: `backend/app/workers/generation_retry_policy.py:31-37`(상수), `:120-135`(새 함수 `environment_window_last_day`·`environment_window_closed`), `:357-382`(`_earliest_eligible_date` 환경 분기), `:385-430`(`next_recovery_deadline`), `:555-590`(`retry_is_due`)
- Modify: `backend/app/workers/tasks.py:521-540`(import), `:1306-1460`(`_remember_generation_attempt` 계수·`environment_since`), `:1499-1518`(`_record_gate_blocker_decision`의 스윕 소유 보존 분기)
- Test(new): `backend/tests/test_environment_daily_budget.py`
- Test(modify): `backend/tests/test_generation_retry_policy.py`, `test_gate_record_stale_context.py`, `test_swapped_slot_retry_record.py`, `test_generation_incident_copy.py`, `test_generation_incident_copy_retry.py`, `test_generation_incident_recovery_time.py`

**조사 결과:**
- `generation_retry_policy.py:31` `ENVIRONMENT_ATTEMPT_BUDGET = 4`, `:32` `DAILY_RESET_ENVIRONMENT_CODES = frozenset({"CONTENT_AI_REVIEW_UNAVAILABLE"})`. `_earliest_eligible_date`(:357-382)은 4회를 쓰면 일일 초기화 코드가 아니면 `None`(종착), `retry_is_due`(:555-590)도 같은 규칙. 즉 PROVIDER_TIMEOUT·PROVIDER_UNAVAILABLE·GENERATION_FAILED는 **슬롯 평생 4회**다 — 감사 F9 확인.
- `tasks._remember_generation_attempt`(:1306-1460)는 같은 문맥이면 `provider_attempt_count`를 날을 넘어 누적한다(:1366 `provider_attempt_count += 1`). 검수 장애만 :1328-1334에서 날이 바뀌면 `same_context=False`로 전부(최초 관측 시각 포함) 지운다.
- 검수 장애의 끝은 `review_unavailable_total` 한도(`CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES=6`)가 소유한다 → 검수 장애에는 3일 창을 넣지 않는다(`WINDOWED_ENVIRONMENT_CODES`에서 제외).
- 게이트의 스윕 소유 보존 분기(tasks.py:1499-1518)는 `next_retry_at`이 문자열이면 무조건 보존한다. 3일 창이 끝난 기록의 낡은 문자열을 보존하면 사람에게 넘어가지 않으므로 `and not environment_window_closed(stored)`를 더한다.
- `generation_incident_control.environment_recovery_exhausted`(:270-288)·`recovery_is_abandoned`는 `next_retry_at=None` + `retry_is_due` 거짓을 본다 — 3일 창이 끝나면 `next_recovery_deadline`이 `None`을 저장하므로 기존 "자동 재시도 횟수를 모두 사용" 문구(`ENVIRONMENT_EXHAUSTED_OPERATOR_ACTION`)가 그대로 쓰인다. 문구 변경 없음.

- [ ] **Step 1: 실패하는 테스트 적용**

저장소 루트에서:
```bash
git apply --include='backend/tests/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-3a.diff
```
새 파일 `backend/tests/test_environment_daily_budget.py` 전문:
```python
"""공급자 장애 예산은 하루 단위로 다시 열리고 3일 뒤에 사람의 일이 된다.

종전에는 PROVIDER_TIMEOUT·PROVIDER_UNAVAILABLE·GENERATION_FAILED의 4회 예산이 슬롯 평생
한 번이라, 하루짜리 OpenRouter 장애가 끝나도 그 슬롯을 어떤 스윕도 다시 쓰지 않았다(감사 F9).
이제 검수 장애(CONTENT_AI_REVIEW_UNAVAILABLE)처럼 KST 날마다 4회가 다시 열리고, 같은 환경
장애 구간을 처음 관측한 날부터 3일(`ENVIRONMENT_EXHAUSTED_DAY_LIMIT`)이 지나면 끝난다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from test_gate_record_stale_context import _empty_slot, _loader_takes, _remember
from test_topic_swap_fallback import _kst

from app.workers.generation_retry_policy import (
    ENVIRONMENT_ATTEMPT_BUDGET,
    ENVIRONMENT_SINCE_KEY,
    GenerationRetryClass,
    environment_window_closed,
    next_recovery_deadline,
    recovery_is_abandoned,
    retry_is_due,
)

_SLOT = date(2026, 9, 16)
_DAY1 = (_kst(2026, 9, 16, 1), _kst(2026, 9, 16, 4), _kst(2026, 9, 16, 7), _kst(2026, 9, 16, 12))


def _env(reason: str, count: int, *, period: str, since: datetime, **extra) -> dict:
    return {
        "reason": reason,
        "retry_class": GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value,
        "provider_attempt_count": count,
        "attempt_period": period,
        ENVIRONMENT_SINCE_KEY: since.isoformat(),
        **extra,
    }


@pytest.mark.parametrize("reason", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "GENERATION_FAILED"])
def test_a_spent_provider_budget_reopens_the_next_kst_day(reason):
    spent = _env(reason, ENVIRONMENT_ATTEMPT_BUDGET, period="2026-09-16", since=_kst(2026, 9, 16, 1))

    due = next_recovery_deadline(spent, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5))

    assert due == _kst(2026, 9, 17, 1).astimezone(UTC)


def test_the_window_ends_after_three_kst_days():
    since = _kst(2026, 9, 14, 1)
    spent_on_last_day = _env(
        "PROVIDER_TIMEOUT", ENVIRONMENT_ATTEMPT_BUDGET, period="2026-09-16", since=since
    )

    assert (
        next_recovery_deadline(spent_on_last_day, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5))
        is None
    )
    # 마지막 날에 예산이 남았으면 그날 안의 스윕까지만 준다.
    one_left = _env("PROVIDER_TIMEOUT", 1, period="2026-09-16", since=since)
    assert next_recovery_deadline(
        one_left, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5)
    ) == _kst(2026, 9, 16, 18).astimezone(UTC)
    assert next_recovery_deadline(one_left, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 22, 5)) is None


def test_a_closed_window_is_not_revived_by_the_daily_reset():
    abandoned = _env(
        "PROVIDER_UNAVAILABLE",
        ENVIRONMENT_ATTEMPT_BUDGET,
        period="2026-09-16",
        since=_kst(2026, 9, 14, 1),
        next_retry_at=None,
    )

    assert environment_window_closed(abandoned, _kst(2026, 9, 17, 1)) is True
    assert retry_is_due(abandoned, _kst(2026, 9, 17, 1)) is False
    assert recovery_is_abandoned(abandoned, _kst(2026, 9, 17, 1)) is True


def test_the_review_outage_keeps_its_own_cap_and_has_no_three_day_window():
    review = _env(
        "CONTENT_AI_REVIEW_UNAVAILABLE",
        ENVIRONMENT_ATTEMPT_BUDGET,
        period="2026-09-16",
        since=_kst(2026, 9, 1, 1),
    )

    assert environment_window_closed(review, _kst(2026, 9, 17, 1)) is False
    assert next_recovery_deadline(
        review, scheduled_date=_SLOT, now=_kst(2026, 9, 16, 12, 5)
    ) == _kst(2026, 9, 17, 1).astimezone(UTC)


def test_the_worker_counts_per_day_and_carries_the_window_start(monkeypatch):
    philosophy, item = _empty_slot(monkeypatch)
    for moment in _DAY1:
        day1 = _remember(monkeypatch, item, philosophy, moment, "PROVIDER_TIMEOUT")
    assert day1["provider_attempt_count"] == ENVIRONMENT_ATTEMPT_BUDGET
    assert day1["next_retry_at"] == _kst(2026, 9, 17, 1).astimezone(UTC).isoformat()
    assert day1[ENVIRONMENT_SINCE_KEY] == _DAY1[0].astimezone(UTC).isoformat()

    # 다음 날 첫 실패는 1회차다. 원인이 바뀌어도 창의 시작은 그대로 잇는다.
    day2 = _remember(monkeypatch, item, philosophy, _kst(2026, 9, 17, 1), "GENERATION_FAILED")
    assert day2["provider_attempt_count"] == 1
    assert day2[ENVIRONMENT_SINCE_KEY] == day1[ENVIRONMENT_SINCE_KEY]
    assert _loader_takes(monkeypatch, item, _kst(2026, 9, 17, 4))


def test_the_worker_hands_over_after_three_days(monkeypatch):
    philosophy, item = _empty_slot(monkeypatch)
    for day in (16, 17, 18):
        for hour in (1, 4, 7, 12):
            last = _remember(monkeypatch, item, philosophy, _kst(2026, 9, day, hour), "PROVIDER_TIMEOUT")

    assert last["next_retry_at"] is None
    assert not _loader_takes(monkeypatch, item, _kst(2026, 9, 19, 1))
    assert recovery_is_abandoned(last, _kst(2026, 9, 19, 1)) is True
```
같은 패치가 기존 테스트 6개를 고친다(전부 "하루 안의 4회 = 소진"을 "3일 창 소진"으로 바꾼 것이다 — 정확한 diff는 패치 파일):
- `test_generation_retry_policy.py::test_environment_budget_is_finite_unless_the_code_resets_daily` — 초기화 없는 코드 예로 `PROVIDER_TIMEOUT` 대신 `COST_BLOCKED`, `PROVIDER_TIMEOUT`은 검수 장애처럼 다음 날 01:00.
- `test_gate_record_stale_context.py`의 `test_stale_context_record_from_real_attempts_is_also_left_for_the_sweep`, `test_a_current_context_exhausted_failure_stays_excluded` — 소진 루프를 `for day in (13, 14, 15): for hour in (1, 4, 7, 12)`로.
- `test_swapped_slot_retry_record.py::test_an_exhausted_environment_budget_is_handled_as_before` — 같은 3일 루프.
- `test_generation_incident_copy.py::_exhaust_provider` 헬퍼 — 같은 3일 루프.
- `test_generation_incident_copy_retry.py::test_an_exhausted_provider_failure_says_press_now_and_pressing_writes_it` — 같은 3일 루프.
- `test_generation_incident_recovery_time.py`의 `ENV-budget-spent` 매개변수 — `environment_since=_D-2 01:00`을 넣어 3일 창 마지막 날의 소진으로.

- [ ] **Step 2: 실패 확인**

Run: `cd backend && .venv/bin/python -m pytest tests/test_environment_daily_budget.py -v`
Expected: 수집 오류 `ImportError: cannot import name 'ENVIRONMENT_SINCE_KEY' from 'app.workers.generation_retry_policy'`.

Run: `cd backend && .venv/bin/python -m pytest tests/test_generation_retry_policy.py -k finite -v`
Expected: FAIL — `assert next_recovery_deadline(attempt("PROVIDER_TIMEOUT"), ...) == ...` 가 `None`.

- [ ] **Step 3: 최소 구현**

```bash
git apply --include='backend/app/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-3a.diff
```
적용되는 코드(전문):
```diff
--- a/backend/app/workers/generation_retry_policy.py
+++ b/backend/app/workers/generation_retry_policy.py
@@ -29,11 +29,26 @@
 BACKLOG_RECOVERY_MINUTE = 30
 BACKLOG_RECOVERY_GRACE = timedelta(hours=1)
 ENVIRONMENT_ATTEMPT_BUDGET = 4
-DAILY_RESET_ENVIRONMENT_CODES = frozenset({"CONTENT_AI_REVIEW_UNAVAILABLE"})
 # 같은 후보의 독립 검수 장애가 이어지면 다음 재검수를 물린다. n번째 연속 실패 뒤에는
 # `관측 + min(1시간·2^(n-1), 24시간)` 이전에 다시 사지 않는다. 계수(`review_unavailable_total`)는
-# KST 날을 넘어 누적되므로 위의 일일 초기화가 이 물러서기를 지우지 않는다.
+# KST 날을 넘어 누적되므로 아래의 일일 초기화가 이 물러서기를 지우지 않는다.
 REVIEW_UNAVAILABLE_CODE = "CONTENT_AI_REVIEW_UNAVAILABLE"
+# 하루 4회(`ENVIRONMENT_ATTEMPT_BUDGET`) 예산이 다음 KST 일에 다시 열리는 환경 원인. 공급자
+# 장애는 몇 시간~하루면 풀리는데 평생 4회로 끝내면 하루짜리 장애가 사람의 일로 굳는다.
+DAILY_RESET_ENVIRONMENT_CODES = frozenset(
+    {
+        REVIEW_UNAVAILABLE_CODE,
+        "PROVIDER_TIMEOUT",
+        "PROVIDER_UNAVAILABLE",
+        "GENERATION_FAILED",
+    }
+)
+# 일일 초기화 코드의 끝. 처음 관측한 KST 날부터 이 일수가 지나면 어떤 스윕도 다시 사지 않고
+# 사람의 일이다(표본 실패의 `SAMPLE_EXHAUSTED_DAY_LIMIT`와 같은 3일). 검수 장애는 후보별 연속
+# 실패 한도(`CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES`)가 따로 끝을 소유하므로 뺀다.
+ENVIRONMENT_EXHAUSTED_DAY_LIMIT = 3
+ENVIRONMENT_SINCE_KEY = "environment_since"
+WINDOWED_ENVIRONMENT_CODES = DAILY_RESET_ENVIRONMENT_CODES - {REVIEW_UNAVAILABLE_CODE}
 REVIEW_UNAVAILABLE_BACKOFF_BASE = timedelta(hours=1)
 REVIEW_UNAVAILABLE_BACKOFF_MAX = timedelta(hours=24)
 
@@ -133,6 +148,40 @@
     return environment_attempt_period(observed)
 
 
+def environment_window_last_day(attempt: Mapping) -> date | None:
+    """일일 초기화 환경 원인이 자동 재시도를 살 수 있는 마지막 KST 날. 상한이 없으면 `None`.
+
+    시작은 그 환경 장애 연속 구간을 처음 관측한 시각(`environment_since`)이다. 그 키가 없는
+    레거시 기록은 `first_observed_at`, 그것도 없으면 `observed_at`으로 본다.
+    """
+
+    if attempt.get("reason") not in WINDOWED_ENVIRONMENT_CODES:
+        return None
+    for key in (ENVIRONMENT_SINCE_KEY, "first_observed_at", "observed_at"):
+        raw = attempt.get(key)
+        if not isinstance(raw, str):
+            continue
+        try:
+            started = datetime.fromisoformat(raw)
+        except ValueError:
+            continue
+        if started.tzinfo is None:
+            started = started.replace(tzinfo=UTC)
+        return started.astimezone(KST).date() + timedelta(
+            days=ENVIRONMENT_EXHAUSTED_DAY_LIMIT - 1
+        )
+    return None
+
+
+def environment_window_closed(attempt: Mapping, now: datetime | None = None) -> bool:
+    """일일 초기화 환경 원인의 3일 창이 끝났는가."""
+
+    last_day = environment_window_last_day(attempt)
+    if last_day is None:
+        return False
+    return (now or datetime.now(UTC)).astimezone(KST).date() > last_day
+
+
 def review_unavailable_backoff(failures: int) -> timedelta:
     """n번째 연속 검수 장애 뒤 다음 재검수까지의 최소 간격."""
 
@@ -372,14 +421,20 @@
     except (TypeError, ValueError):
         return None
     if count < ENVIRONMENT_ATTEMPT_BUDGET:
-        return today
-    if attempt.get("reason") in DAILY_RESET_ENVIRONMENT_CODES:
+        earliest = today
+    elif attempt.get("reason") in DAILY_RESET_ENVIRONMENT_CODES:
         # 소진된 예산이 어제 것이면 오늘 이미 초기화됐다. 하루를 더 미루면 새 예산의
         # 하루를 통째로 버린다.
         if stored_attempt_period(attempt) != environment_attempt_period(observed):
-            return today
-        return today + timedelta(days=1)
-    return None
+            earliest = today
+        else:
+            earliest = today + timedelta(days=1)
+    else:
+        return None
+    last_day = environment_window_last_day(attempt)
+    if last_day is not None and earliest > last_day:
+        return None
+    return earliest
 
 
 def next_recovery_deadline(
@@ -408,9 +463,14 @@
     if earliest_date is None:
         return None
     not_before = review_unavailable_not_before(attempt)
+    last_day = environment_window_last_day(attempt)
     for candidate in _candidate_sweeps(observed):
         if candidate.date() < earliest_date:
             continue
+        if last_day is not None and candidate.date() > last_day:
+            # 3일 창 밖의 스윕은 이 기록을 집지 않는다(`retry_is_due`). catch-up 창보다 오래된
+            # 슬롯의 백로그 복구 판정은 아래에서 그대로 준다.
+            break
         if not_before is not None and candidate < not_before:
             continue
         window_start, window_end = _sweep_window(candidate)
@@ -562,6 +622,9 @@
         return _sample_retry_is_due(attempt, observed)
     if retry_class != GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value:
         return False
+    if environment_window_closed(attempt, observed):
+        # 3일 창이 끝났다. 아래의 일일 초기화 되살리기보다 앞선다.
+        return False
     legacy_count = (
         0 if attempt.get("reason") == "COST_BLOCKED" else attempt.get("attempt_count")
     )
--- a/backend/app/workers/tasks.py
+++ b/backend/app/workers/tasks.py
@@ -522,13 +522,17 @@
     AUTO_CORRECTION_EXHAUSTED_KEY,
     BODY_REPAIR_CODES,
     BODY_REPAIR_STATE_KEY,
+    DAILY_RESET_ENVIRONMENT_CODES,
+    ENVIRONMENT_SINCE_KEY,
     OPERATOR_DECIDES_KEY,
     REVIEW_UNAVAILABLE_CODE,
     SAMPLE_EXHAUSTED_DAY_LIMIT,
     SAMPLE_IMAGE_DAILY_BUDGET,
+    WINDOWED_ENVIRONMENT_CODES,
     GenerationRetryClass,
     attempt_is_terminal,
     environment_attempt_period,
+    environment_window_closed,
     has_model_declared_hard_finding,
     next_recovery_deadline,
     repair_session_is_available,
@@ -1363,6 +1367,13 @@
             previous if same_context else None, reason, observed_at
         )
     elif retry_class == GenerationRetryClass.ENVIRONMENT_RECOVERABLE:
+        if (
+            reason in DAILY_RESET_ENVIRONMENT_CODES
+            and stored_attempt_period(previous) != attempt_period
+        ):
+            # 하루 예산은 KST 날마다 다시 열린다. 3일 창의 시작(`environment_since`)은 아래에서
+            # 따로 잇는다.
+            provider_attempt_count = 0
         provider_attempt_count += 1
     if (
         reason == "IMAGE_GENERATION_FAILED"
@@ -1397,6 +1408,23 @@
     }
     if GENERATION_BUDGET_KEY in previous:
         attempt[GENERATION_BUDGET_KEY] = previous[GENERATION_BUDGET_KEY]
+    if (
+        retry_class == GenerationRetryClass.ENVIRONMENT_RECOVERABLE
+        and reason in WINDOWED_ENVIRONMENT_CODES
+    ):
+        # 환경 장애 연속 구간의 시작. 원인 문자열이 바뀌어도(PROVIDER_TIMEOUT → GENERATION_FAILED,
+        # 게이트의 CONTENT_NOT_GENERATED 덮어쓰기) 같은 문맥의 환경 기록이 이어지면 시작을 잇는다 —
+        # 원인을 갈아타며 3일 창을 새로 여는 일이 없게 한다.
+        carried = (
+            previous.get(ENVIRONMENT_SINCE_KEY)
+            if previous.get("context") == context
+            and previous.get("retry_class")
+            == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
+            else None
+        )
+        attempt[ENVIRONMENT_SINCE_KEY] = (
+            carried if isinstance(carried, str) else observed_at.isoformat()
+        )
     if reason == "GENERATION_REJECTED":
         attempt["message"] = safe_generation_rejection_message(message)
     # 이 차단이 어떤 승인 사실 위에서 내려졌는지 남긴다. 사람이 그 자료를 채우면 스윕이
@@ -1504,6 +1532,7 @@
             GenerationRetryClass.SAMPLE_RECOVERABLE,
         )
         and (retry_is_due(stored) or isinstance(stored.get("next_retry_at"), str))
+        and not environment_window_closed(stored)
     ):
         # 스윕이 아직 소유한 실패(PROVIDER_TIMEOUT 등)도 같다. 덮으면 로더가 다시 집지
         # 않고, CONTENT_NOT_GENERATED는 교체 후보 코드가 아니라 교체 이력과 무관하게 영영
```

- [ ] **Step 4: 통과 확인**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/test_environment_daily_budget.py tests/test_generation_retry_policy.py tests/test_gate_record_stale_context.py tests/test_operator_retry_gate_record.py tests/test_generation_lease_retry.py tests/test_swapped_slot_retry_record.py tests/test_generation_incident_copy.py tests/test_generation_incident_copy_retry.py tests/test_generation_incident_recovery_time.py -q
.venv/bin/python -m ruff check app/workers/generation_retry_policy.py app/workers/tasks.py tests/test_environment_daily_budget.py
```
Expected: `407 passed, 12 skipped`(드래프팅 실측), ruff `All checks passed!`.

- [ ] **Step 5: 커밋**
```bash
git add backend/app/workers/generation_retry_policy.py backend/app/workers/tasks.py backend/tests/test_environment_daily_budget.py backend/tests/test_generation_retry_policy.py backend/tests/test_gate_record_stale_context.py backend/tests/test_swapped_slot_retry_record.py backend/tests/test_generation_incident_copy.py backend/tests/test_generation_incident_copy_retry.py backend/tests/test_generation_incident_recovery_time.py
git commit -m "fix(generation): 공급자 장애 예산을 KST 일 단위로 다시 열고 3일 뒤 사람에게 넘긴다"
```

**계약 영향:** CLAUDE.md "재시도는 일시 오류와 표본 실패에 한정하고 업무별 최대 횟수를 따른다" 절에 한 문장 추가 — "공급자 장애(PROVIDER_TIMEOUT·PROVIDER_UNAVAILABLE·GENERATION_FAILED)는 KST 하루 4회 예산이 날마다 다시 열리고, 같은 환경 장애 구간을 처음 관측한 날(`environment_since`)부터 3일이 지나면 사람의 일이다. 검수 장애는 후보별 연속 실패 한도가 따로 끝을 소유한다."
**위험·롤백:** (1) 배포 직후 기존에 평생 4회를 다 써 `next_retry_at=None`으로 굳은 공급자 기록은 `environment_since`가 없어 `first_observed_at`으로 창을 판정한다 — 3일보다 오래된 것은 그대로 사람의 일로 남고(대량 부활 없음), 3일 안의 것은 다음 날부터 다시 시도된다. (2) 공급자 호출 비용이 슬롯당 최대 3일×4회로 늘 수 있다 — 슬롯 평생 HTTP 상한(36)이 그 위의 안전장치다. (3) 롤백은 커밋 revert. 새 키 `environment_since`는 옛 코드가 무시하므로 데이터 정리 불필요.

---

### Task 0-3b: OpenRouter 429·5xx·연결 오류를 `PROVIDER_UNAVAILABLE`로 분류

**Files:**
- Modify: `backend/app/workers/generation_run_control.py:172-193`(`classify_generation_failure`), 새 helper `_provider_side_status`
- Test(new): `backend/tests/test_generation_failure_classification.py`

**조사 결과:**
- `services/openrouter.py:48-72` 클라이언트는 `max_retries=0`, 복원력은 호출부 tenacity가 소유. `content_engine.py:1115-1130` `@retry(stop_after_attempt(3), retry=retry_if_not_exception_type((ValueError, *NON_RETRYABLE_LLM_ERRORS)), reraise=True)` → 3회 뒤 **원래 openai 예외**가 워커로 올라온다(래핑 없음). `NON_RETRYABLE_LLM_ERRORS`는 400/401/403/404(`openrouter.py:226-233`).
- openai 2.33.0 MRO 실측: `RateLimitError`·`InternalServerError` → `APIStatusError` → `APIError`; `APITimeoutError` → `APIConnectionError` → `APIError`. 어느 것도 내장 `ConnectionError`가 아니다 → 지금은 429·5xx·연결 오류 모두 `GENERATION_FAILED`(`classify_generation_failure`의 `case _`). 실측: main에서 502·`APIConnectionError` 모두 `GENERATION_FAILED`.
- `grep -rn "APIStatusError\|APIConnectionError\|RateLimitError" backend/app` → `generation_run_control.py:181`의 `APITimeoutError` 한 곳뿐. 다른 경로의 분류는 이 함수 하나(tasks.py 5964·6177·6383·6563·6761 호출).
- 0-3a 뒤로 `GENERATION_FAILED`도 일일 초기화라 재시도 횟수 차이는 없다. 이 작업의 효과는 인시던트·요약의 원인 문구(“일시적인 응답 지연/공급자 장애” vs “원인 불명”)와 `_ENVIRONMENT_WAIT_CODES`(PROVIDER_*만) 기반의 “예약 복구 소진” 문구가 맞게 나오는 것이다.

- [ ] **Step 1: 실패하는 테스트 작성** — `git apply --include='backend/tests/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-3b.diff` (새 파일 전문은 아래 Step 3의 diff에 포함)
- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_generation_failure_classification.py -v` Expected: `502`·`429`·`503`·`connection` 4건 FAIL(`assert 'GENERATION_FAILED' == 'PROVIDER_UNAVAILABLE'`), 나머지 3건 PASS.
- [ ] **Step 3: 최소 구현** — `git apply --include='backend/app/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-3b.diff`. 패치 전문:
```diff
--- a/backend/app/workers/generation_run_control.py
+++ b/backend/app/workers/generation_run_control.py
@@ -181,9 +181,14 @@
         case TimeoutError() | openai.APITimeoutError():
             code = "PROVIDER_TIMEOUT"
             message = _SAFE_FAILURE_MESSAGE
-        case ConnectionError():
+        case ConnectionError() | openai.APIConnectionError():
             code = "PROVIDER_UNAVAILABLE"
             message = _SAFE_FAILURE_MESSAGE
+        case openai.APIStatusError() if _provider_side_status(error.status_code):
+            # OpenRouter·상위 공급자의 과부하(429)·서버 오류(5xx). 기다리면 풀리는 환경 장애다.
+            # 결정적 4xx(400·401·403·404)와 크레딧 부족(402)은 여기 들지 않는다.
+            code = "PROVIDER_UNAVAILABLE"
+            message = _SAFE_FAILURE_MESSAGE
         case ValueError():
             code = "GENERATION_REJECTED"
             message = _safe_rejection_message(error)
@@ -193,6 +198,10 @@
     return code, message
 
 
+def _provider_side_status(status_code: int) -> bool:
+    return status_code == 429 or status_code >= 500
+
+
 def safe_generation_rejection_message(message: str | None) -> str:
     """Accept only classifier-owned rejection copy at persistence boundaries."""
 
--- /dev/null
+++ b/backend/tests/test_generation_failure_classification.py
@@ -0,0 +1,37 @@
+"""OpenRouter 오류는 상태 코드로 분류한다 — 과부하·서버 오류는 환경 장애(감사 F9).
+
+`content_engine`의 tenacity는 `reraise=True`로 3회 뒤 원래 openai 예외를 다시 던진다. 종전에는
+429·5xx(`APIStatusError` 하위)와 연결 오류(`APIConnectionError`)가 모두 `GENERATION_FAILED`로
+떨어져, 기다리면 풀리는 공급자 장애를 원인 불명 실패로 보고했다.
+"""
+
+from __future__ import annotations
+
+import httpx
+import openai
+import pytest
+
+from app.workers.generation_run_control import classify_generation_failure
+
+_REQUEST = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
+
+
+def _status(cls, status: int):
+    return cls("x", response=httpx.Response(status, request=_REQUEST), body=None)
+
+
+@pytest.mark.parametrize(
+    ("error", "code"),
+    [
+        (_status(openai.InternalServerError, 502), "PROVIDER_UNAVAILABLE"),
+        (_status(openai.RateLimitError, 429), "PROVIDER_UNAVAILABLE"),
+        (_status(openai.APIStatusError, 503), "PROVIDER_UNAVAILABLE"),
+        (openai.APIConnectionError(request=_REQUEST), "PROVIDER_UNAVAILABLE"),
+        (openai.APITimeoutError(request=_REQUEST), "PROVIDER_TIMEOUT"),
+        (_status(openai.BadRequestError, 400), "GENERATION_FAILED"),
+        (_status(openai.APIStatusError, 402), "GENERATION_FAILED"),
+    ],
+    ids=["502", "429", "503", "connection", "timeout", "400", "402-credits"],
+)
+def test_openrouter_errors_are_classified_by_status(error, code):
+    assert classify_generation_failure(error)[0] == code
```
- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_generation_failure_classification.py tests/test_content_engine_must_use_verbatim.py -q && .venv/bin/python -m ruff check app/workers/generation_run_control.py tests/test_generation_failure_classification.py` Expected: `21 passed`, `All checks passed!`.
- [ ] **Step 5: 커밋** `git add backend/app/workers/generation_run_control.py backend/tests/test_generation_failure_classification.py && git commit -m "fix(generation): OpenRouter 429·5xx·연결 오류를 공급자 장애로 분류한다"`

**계약 영향:** 없음(분류 정밀화). 
**위험·롤백:** 402(크레딧 부족)는 의도적으로 `GENERATION_FAILED` — 크레딧 소진이 "일시 장애"로 보이지 않게. 롤백은 revert.

---

### Task 0-4: 게이트의 `CONTENT_NOT_GENERATED`를 복구 가능한 증상(3일 창)으로 재분류

**Files:**
- Modify: `backend/app/workers/generation_retry_policy.py:35-46`(`DAILY_RESET_ENVIRONMENT_CODES`), `:98-107`(`_ENVIRONMENT_CODES`)
- Modify: `backend/app/workers/tasks.py:1463-1531`(`_record_gate_blocker_decision`)
- Modify: `backend/app/workers/generation_incident_control.py:197-224`(`scheduled_recovery_owns_blocker`)
- Test(new 추가): `backend/tests/test_environment_daily_budget.py`(0-4 절 4건)
- Test(modify): `test_gate_record_stale_context.py`, `test_operator_retry_gate_record.py`, `test_generation_lease_retry.py`, `test_swapped_slot_retry_record.py`, `test_generation_incident_copy.py`, `test_generation_incident_copy_retry.py`

**조사 결과:**
- 게이트(07:45·08:00·매시 발행기)는 `_record_gate_blocker_decision(..., "CONTENT_NOT_GENERATED")` → `_remember_generation_attempt(count_attempt=False)`. `retry_class_for("CONTENT_NOT_GENERATED")`는 기본값 `OPERATOR_REQUIRED`(generation_retry_policy.py:200-210), 분류가 ENV/SAMPLE이 아니라 `next_retry_at`을 쓰지 않는다(tasks.py:1440-1448).
- 로더 술어 `_generation_retry_is_eligible`(tasks.py:1244-1276): 본문이 없으면 `_generation_attempt_is_unchanged`(:1212-1231) — 같은 문맥이고 `retry_is_due` 거짓이면 뺀다. OPERATOR_REQUIRED는 `retry_is_due`가 항상 거짓 → **영구 제외**. `_terminal_attempt_is_unchanged`(:1234-1258)는 `attempt_is_terminal`이 CNG를 종착으로 보지 않아 관여하지 않는다(감사 가정 정정).
- 이미 있는 예외 3가지: 주제 교체 직후(:1493-1497), 스윕 소유 ENV/SAMPLE 기록(:1499-1518), 다른 생성 문맥(:1519-1530). 남은 동결 경로는 **기록이 없는 빈 슬롯**(유실된 배달 — claim 후 태스크가 한 번도 기록을 못 남김)과 **3일 창이 끝난/레거시 환경 기록**이다.
- 기존 테스트 `test_gate_record_stale_context.py::test_a_slot_without_any_record_is_still_recorded_by_the_gate`가 현재 동작("사람의 몫")을 명시적으로 고정하고 있다 → 이 테스트를 새 동작으로 바꾼다.
- 회귀 위험(정정): 게이트는 종착 기록(소진 표본 `CONTENT_AI_HARD_FINDING`·INPUT_CHANGE 등)도 CNG로 덮는다(`test_swapped_slot_retry_record.py::test_a_terminal_record_on_a_swapped_slot_is_handled_as_before`, `test_a_terminal_class_is_overwritten_even_with_a_stored_schedule`). 이 덮어쓰기는 종전대로 OPERATOR_REQUIRED·`next_retry_at=None`으로 남겨야 표본 사다리·주제 교체 상한을 우회하지 않는다 → 별도 분기.
- 인시던트: `scheduled_recovery_owns_blocker`(generation_incident_control.py:197-224)는 CNG를 소유 코드로 보지 않아 기록을 고쳐도 OPEN(사람의 일)으로 열린다 → 저장 원인이 CNG이고 분류가 ENVIRONMENT면 소유(RETRYING, 기한=저장된 `next_retry_at`). 문구는 기존 `CONTENT_NOT_GENERATED_SCHEDULED_RELEASABLE_ACTION`(“자동 복구가 {due}에 … 지금 바로 다시 시도할 수 있습니다”)이 그대로 선택된다(`operator_retry_releases`가 CNG를 해제 대상으로 이미 본다) — 새 문구 없음.
- 유실된 배달이 사흘 내내 반복되는 경우의 끝: 같은 원인이면 게이트가 다시 쓰지 않으므로(:1487-1491) 3일 창이 끝난 뒤 한 번은 다시 써서 `next_retry_at=None`으로 굳히는 예외를 둔다.

- [ ] **Step 1: 실패하는 테스트 적용** — `git apply --include='backend/tests/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-4.diff`. 새 테스트(파일 끝에 추가되는 부분 전문):
```python
# ── 0-4: 게이트의 원고 미생성은 복구 가능한 증상이다 ─────────────────────────────


def test_the_gate_record_of_an_empty_slot_is_picked_up_by_the_next_sweep(monkeypatch):
    """배달이 유실돼 시도 기록이 없는 빈 슬롯 — 07:45 게이트 뒤 12:00 스윕이 다시 집는다."""

    philosophy, item = _empty_slot(monkeypatch)

    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 16, 7, 45))

    record = _record(item)
    assert record["reason"] == "CONTENT_NOT_GENERATED"
    assert record["retry_class"] == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
    assert record["provider_attempt_count"] == 0
    assert record["next_retry_at"] == _kst(2026, 9, 16, 12).astimezone(UTC).isoformat()
    assert not _loader_takes(monkeypatch, item, _kst(2026, 9, 16, 11, 59))
    assert _loader_takes(monkeypatch, item, _kst(2026, 9, 16, 12))


def test_a_slot_whose_dispatch_keeps_getting_lost_reaches_the_operator_after_three_days(monkeypatch):
    philosophy, item = _empty_slot(monkeypatch)
    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 16, 7, 45))
    # 매일 아침 게이트가 같은 증상을 본다. 창 안에서는 기록을 바꾸지 않는다.
    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 18, 7, 45))
    assert isinstance(_record(item)["next_retry_at"], str)
    assert _loader_takes(monkeypatch, item, _kst(2026, 9, 18, 12))

    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 19, 7, 45))

    record = _record(item)
    assert record["next_retry_at"] is None
    assert not _loader_takes(monkeypatch, item, _kst(2026, 9, 19, 12))
    assert recovery_is_abandoned(record, _kst(2026, 9, 19, 12)) is True


def test_the_gate_overwrite_of_an_abandoned_provider_record_keeps_the_window(monkeypatch):
    """3일을 다 쓴 공급자 기록을 게이트가 원고 미생성으로 덮어도 새 3일 창이 열리지 않는다."""

    philosophy, item = _empty_slot(monkeypatch)
    for day in (16, 17, 18):
        for hour in (1, 4, 7, 12):
            exhausted = _remember(monkeypatch, item, philosophy, _kst(2026, 9, day, hour), "PROVIDER_TIMEOUT")
    assert exhausted["next_retry_at"] is None

    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 19, 7, 45))

    record = _record(item)
    assert record["reason"] == "CONTENT_NOT_GENERATED"
    assert record[ENVIRONMENT_SINCE_KEY] == exhausted[ENVIRONMENT_SINCE_KEY]
    assert record["next_retry_at"] is None
    assert not _loader_takes(monkeypatch, item, _kst(2026, 9, 19, 12))


def test_a_gate_recorded_empty_slot_is_owned_by_the_sweep_until_the_window_ends(monkeypatch):
    from app.workers.generation_incident_control import scheduled_recovery_owns_blocker

    philosophy, item = _empty_slot(monkeypatch)
    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 16, 7, 45))
    assert scheduled_recovery_owns_blocker("CONTENT_NOT_GENERATED", item) is True

    _gate(monkeypatch, item, philosophy, _kst(2026, 9, 19, 7, 45))
    assert scheduled_recovery_owns_blocker("CONTENT_NOT_GENERATED", item) is False
```
기존 테스트 수정(정확한 diff는 패치):
- `test_gate_record_stale_context.py`: `test_a_slot_without_any_record_is_still_recorded_by_the_gate` → `test_a_slot_without_any_record_is_recorded_as_a_recoverable_symptom`(ENV·`next_retry_at`=12:00·12:00 스윕이 집음). `test_an_already_frozen_current_context_record_is_not_released_by_this_deploy`는 배포 전 모양(OPERATOR_REQUIRED·기한 키 없음)을 직접 만들어 **여전히 풀리지 않음**을 고정. `test_a_current_context_exhausted_failure_stays_excluded`에 `next_retry_at is None` 단언 추가.
- `test_operator_retry_gate_record.py`: `_gate_recorded_slot` 헬퍼의 기대 분류를 ENV로. `test_dispatch_without_an_operator_run_keeps_the_suppression`·`test_recovery_sweep_keeps_the_suppression`의 `next_day` 매개변수를 제거하고 `_until_the_stored_time`으로 이름 변경(같은 날은 여전히 억제), 새 `test_the_next_recovery_sweep_writes_a_gate_recorded_empty_slot`(다음 날 스윕이 작가 1회로 씀). `test_operator_retry_keeps_the_suppression_for_other_stored_reasons`는 저장 기록 분류를 OPERATOR_REQUIRED로 명시. `test_a_legacy_sample_record_the_gate_overwrites_is_retried_by_the_operator`의 덮인 분류 기대를 ENV로.
- `test_generation_lease_retry.py::test_other_content_prefixed_reasons_are_not_released` — 저장 기록 분류를 OPERATOR_REQUIRED로 명시.
- `test_swapped_slot_retry_record.py`: `test_an_exhausted_environment_budget_is_handled_as_before`의 덮인 기록 기대를 ENV·`next_retry_at=None`·`recovery_is_abandoned`로(3일 창을 이어받음). `test_a_legacy_record_without_a_schedule_that_is_not_due_is_overwritten`의 덮인 기록 기대를 ENV·12:00으로.
- `test_generation_incident_copy.py`: 헬퍼 `_operator_gate_record`(종착 기록을 덮은 모양)를 추가해 `test_a_gate_recorded_not_generated_slot_says_no_sweep_will_write_it`·`test_a_reused_retrying_cause_drops_its_stale_deadline_once_no_sweep_owns_it`·`test_a_reused_open_cause_keeps_its_deadline`이 그 모양을 쓰게 한다. 새 `test_a_gate_recorded_empty_slot_without_a_prior_cause_is_retrying`(RETRYING·기한 12:00·문구 “자동 복구가”).
- `test_generation_incident_copy_retry.py::test_a_gate_recorded_slot_says_press_and_pressing_writes_it` — 기대 문구를 시각+버튼 문구(`SCHEDULED_RELEASABLE`)로, 미사용 import `CONTENT_NOT_GENERATED_OPERATOR_ACTION` 제거.

- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_environment_daily_budget.py -v` Expected: 0-4 절 4건 FAIL — 예: `test_the_gate_record_of_an_empty_slot_is_picked_up_by_the_next_sweep`에서 `assert 'OPERATOR_REQUIRED' == 'ENVIRONMENT_RECOVERABLE'`. 나머지 8건 PASS.
- [ ] **Step 3: 최소 구현** — `git apply --include='backend/app/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-4.diff`. 적용 코드 전문:
```diff
--- a/backend/app/workers/generation_retry_policy.py
+++ b/backend/app/workers/generation_retry_policy.py
@@ -35,12 +35,15 @@
 REVIEW_UNAVAILABLE_CODE = "CONTENT_AI_REVIEW_UNAVAILABLE"
 # 하루 4회(`ENVIRONMENT_ATTEMPT_BUDGET`) 예산이 다음 KST 일에 다시 열리는 환경 원인. 공급자
 # 장애는 몇 시간~하루면 풀리는데 평생 4회로 끝내면 하루짜리 장애가 사람의 일로 굳는다.
+# 원고 미생성(CONTENT_NOT_GENERATED)은 게이트가 예산 없이 남긴 증상이다 — 유실된 배달을
+# 다음 스윕이 다시 집게 한다.
 DAILY_RESET_ENVIRONMENT_CODES = frozenset(
     {
         REVIEW_UNAVAILABLE_CODE,
         "PROVIDER_TIMEOUT",
         "PROVIDER_UNAVAILABLE",
         "GENERATION_FAILED",
+        "CONTENT_NOT_GENERATED",
     }
 )
 # 일일 초기화 코드의 끝. 처음 관측한 KST 날부터 이 일수가 지나면 어떤 스윕도 다시 사지 않고
@@ -101,6 +104,7 @@
         "PROVIDER_UNAVAILABLE",
         "CONTENT_AI_REVIEW_UNAVAILABLE",
         "GENERATION_FAILED",
+        "CONTENT_NOT_GENERATED",
     }
 )
 # 이미지 표본 실패. 하루 4회(야간 스윕 수)까지 재시도하고 다음 KST 일에 초기화된다.
--- a/backend/app/workers/tasks.py
+++ b/backend/app/workers/tasks.py
@@ -1512,9 +1512,17 @@
 
     stored = _stored_generation_attempt(item)
     stored_reason = stored.get("reason")
-    if stored_reason == code and bool(stored.get(OPERATOR_DECIDES_KEY)) == (
-        operator_decides_references(code, item)
+    if (
+        stored_reason == code
+        and bool(stored.get(OPERATOR_DECIDES_KEY)) == operator_decides_references(code, item)
+        and not (
+            code == "CONTENT_NOT_GENERATED"
+            and isinstance(stored.get("next_retry_at"), str)
+            and environment_window_closed(stored)
+        )
     ):
+        # 같은 증상을 이미 기록했다. 예외: 원고 미생성의 3일 창이 끝났는데 저장된 기한이
+        # 아직 문자열이면 한 번 다시 써서 `next_retry_at=None`(사람의 일)으로 굳힌다.
         return
     if code in _IMAGE_SYMPTOM_CODES and stored_reason in _STORED_IMAGE_CAUSE_CODES:
         return
@@ -1557,6 +1565,25 @@
         # 작가 0회로 사라진다(2026-10-02 gate_catalog 갱신 뒤 09:00 발행기가 4건을 그렇게
         # 굳혔다). 기록이 아예 없는 빈 슬롯은 종전대로 아래에서 기록한다.
         return
+    if code == "CONTENT_NOT_GENERATED" and stored.get("retry_class") in (
+        GenerationRetryClass.OPERATOR_REQUIRED.value,
+        GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
+    ):
+        # 사람·입력 변경이 소유한 종착 기록(소진된 본문 표본·인용 없는 HARD 등)을 증상이 덮을 때는
+        # 종전처럼 사람의 몫으로 남긴다. 원고 미생성이 복구 가능한 증상이 됐다고 종착 기록을
+        # 자동 재시도로 되살리면 표본 사다리·주제 교체 상한을 우회해 작가 세션을 다시 산다.
+        _remember_generation_attempt(
+            db,
+            item,
+            philosophy,
+            code,
+            count_attempt=False,
+            extra={
+                "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
+                "next_retry_at": None,
+            },
+        )
+        return
     _remember_generation_attempt(db, item, philosophy, code, count_attempt=False)
 
 
--- a/backend/app/workers/generation_incident_control.py
+++ b/backend/app/workers/generation_incident_control.py
@@ -217,6 +217,14 @@
         # 으로 부르면 아무도 소유하지 않은 차단이 기한 없는 "재시도 중"으로 숨는다.
         return False
     if code in _AUTOMATIC_RECOVERY_CODES:
+        return True
+    if (
+        code == "CONTENT_NOT_GENERATED"
+        and attempt.get("reason") == code
+        and attempt.get("retry_class") == GenerationRetryClass.ENVIRONMENT_RECOVERABLE.value
+    ):
+        # 게이트가 남긴 원고 미생성 증상은 3일 창 안에서 스윕이 다시 집는다(유실된 배달).
+        # 창이 끝나면 위의 `recovery_is_abandoned`가 먼저 사람의 일로 돌린다.
         return True
     return (
         attempt.get("reason") == code
```
그리고 `generation_incident_control.py:521-525`의 주석(“분류는 OPERATOR_REQUIRED이고 다음 시도 시각이 없어 … 다시 쓰지 않는다”)을 손으로 고친다:
```python
# 원고 미생성은 07:45·08:00 게이트가 기록한다(`tasks._record_gate_blocker_decision`). 기록이 없던
# 빈 슬롯(유실된 배달)·환경 기록은 ENVIRONMENT_RECOVERABLE·3일 창으로 남아 다음 스윕이 다시 쓴다.
# 이 조치 문구는 그 창이 끝났거나, 게이트가 종착 기록(소진된 표본 등)을 덮어 OPERATOR_REQUIRED로
# 남긴 경우의 것이다. 운영센터의 “작업 다시 시도”는 이 기록의 억제를 풀고 원고를 만든다
# (`tasks.regenerate_content_item`).
```
- [ ] **Step 4: 통과 확인** Run:
```bash
cd backend && .venv/bin/python -m pytest tests/test_environment_daily_budget.py tests/test_generation_failure_classification.py tests/test_generation_retry_policy.py tests/test_gate_record_stale_context.py tests/test_operator_retry_gate_record.py tests/test_generation_lease_retry.py tests/test_swapped_slot_retry_record.py tests/test_generation_incident_copy.py tests/test_generation_incident_copy_retry.py tests/test_generation_incident_recovery_time.py -q
.venv/bin/python -m ruff check app/workers tests/test_environment_daily_budget.py tests/test_generation_incident_copy_retry.py
```
Expected: `420 passed, 12 skipped`, ruff 통과.
- [ ] **Step 5: 커밋**
```bash
git add backend/app/workers/generation_retry_policy.py backend/app/workers/tasks.py backend/app/workers/generation_incident_control.py backend/tests/test_environment_daily_budget.py backend/tests/test_gate_record_stale_context.py backend/tests/test_operator_retry_gate_record.py backend/tests/test_generation_lease_retry.py backend/tests/test_swapped_slot_retry_record.py backend/tests/test_generation_incident_copy.py backend/tests/test_generation_incident_copy_retry.py
git commit -m "fix(generation): 게이트의 원고 미생성을 3일 창 안에서 스윕이 다시 쓰는 증상으로 남긴다"
```

**계약 영향:** CLAUDE.md "23:00과 복구 스윕 … 디스패처다" 문단 끝에 추가 — "07:45·08:00·매시 게이트가 본 빈 슬롯의 원고 미생성(CONTENT_NOT_GENERATED)은 저장된 원인이 없거나 환경 장애면 복구 가능한 증상(ENVIRONMENT_RECOVERABLE·3일 창)이라 다음 스윕이 다시 쓰고 그동안 인시던트는 RETRYING이다. 소진된 표본·입력 변경 같은 종착 기록을 덮을 때는 종전처럼 사람의 일이다." `operator_action.is_operator_todo` 규칙 자체는 바뀌지 않는다(기한 안의 RETRYING은 사람의 일이 아님).
**위험·롤백:**
- 배포만으로는 **운영 DB의 기존 동결분이 풀리지 않는다**(같은 원인이면 게이트가 다시 쓰지 않음). 배포 직후 1회 확인 쿼리(읽기 전용):
  ```sql
  SELECT id, hospital_id, scheduled_date
  FROM content_items
  WHERE status IN ('DRAFT','READY') AND coalesce(btrim(body),'') = ''
    AND essence_check_summary->'generation_attempt'->>'reason' = 'CONTENT_NOT_GENERATED'
    AND essence_check_summary->'generation_attempt'->>'retry_class' = 'OPERATOR_REQUIRED'
  ORDER BY scheduled_date;
  ```
  나온 슬롯은 운영센터 “작업 다시 시도”(기존 `operator_retry_releases` 경로)로 한 번 푼다. 자동 일괄 해제는 종착 기록을 덮은 옛 기록까지 되살려 작가 세션을 사므로 넣지 않았다. (상태 값은 `GENERATION_WRITE_BACK_STATUSES`와 맞춰 실행 전에 확인.)
- 유실된 배달이 반복돼도 작가 호출은 일어나지 않는다(배달만 반복) — 상한은 3일 창.
- 롤백: revert. 새로 쓴 ENV 기록은 옛 코드에서 `retry_is_due`가 `next_retry_at` 시각까지 기다렸다가 참이 되어 한 번 더 시도될 뿐이다(무해).

---

### Task 0-5: 주제 교체가 후보 질문을 최대 5개까지 살펴본다

**Files:**
- Modify: `backend/app/workers/topic_swap_fallback.py:84-87`(상수 `TOPIC_SWAP_CANDIDATE_LIMIT`), `:341-386`(`_swap_one` → `_target_rejection`·`_swap_target` 분리)
- Test: `backend/tests/test_topic_swap_fallback.py`(파일 끝 2건 추가)

**조사 결과:**
- `_swap_one`(topic_swap_fallback.py:341-386)은 `_choose_target(..., exclude_target_ids={item.query_target_id})`로 **한 번** 고르고, 진료과 불일치·수기 목록 문서 없음·유사 주제면 그대로 포기한다. `_choose_target`(content_target_planner.py:181-264)은 결정적(`min(... key=...)`) → 다음 스윕도 같은 질문을 골라 같은 이유로 포기를 반복한다.
- `_choose_target`은 이미 `target_fits_hospital`로 진료과 불일치를 거르므로(:209) `incompatible_specialty` 분기는 실제로 거의 오지 않는다(감사 가정 정정 — 실효는 문서 없음·유사).
- `_lock_target_planning`은 `pg_advisory_xact_lock`(utils/db_locks.py:30-34)이라 같은 트랜잭션에서 여러 번 잡아도 된다(재진입).
- 기존 테스트 `test_no_candidate_target_leaves_the_slot_alone`·`test_similar_topic_is_not_worth_swapping`은 제외 집합을 무시하는 가짜 `_choose_target`을 써서 새 구현에서도 그대로 통과한다(유사 후보 5개를 본 뒤 `similar_topic` 1건).

- [ ] **Step 1: 실패하는 테스트 작성** — `git apply --include='backend/tests/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-5.diff`. 추가되는 테스트 전문:
```python
# ── 0-5: 후보를 여러 개 살펴본다 ──────────────────────────────────────────────


def test_a_rejected_candidate_is_excluded_and_the_next_one_is_taken(monkeypatch):
    similar = _target("허리디스크 초기증상 자가진단")
    good = _target()
    excluded_per_call: list[set] = []

    def choose(db, *, exclude_target_ids, **kwargs):
        excluded_per_call.append(set(exclude_target_ids))
        for candidate in (similar, good):
            if candidate.id not in exclude_target_ids:
                return candidate
        return None

    monkeypatch.setattr(topic_swap_fallback, "_choose_target", choose)
    item = _item()
    db = _FakeDB([item])

    report = _run(db)

    assert (report.considered, report.swapped, report.similar_topic) == (1, 1, 0)
    assert excluded_per_call == [{item.query_target_id}, {item.query_target_id, similar.id}]
    assert _values(db)["query_target_id"] == good.id


def test_the_candidate_search_is_bounded(monkeypatch):
    calls: list[int] = []

    def choose(db, **kwargs):
        calls.append(1)
        return _target("허리디스크 초기증상 자가진단")  # 매번 새 id, 매번 유사

    monkeypatch.setattr(topic_swap_fallback, "_choose_target", choose)
    db = _FakeDB([_item()])

    report = _run(db)

    assert len(calls) == topic_swap_fallback.TOPIC_SWAP_CANDIDATE_LIMIT
    assert (report.swapped, report.similar_topic) == (0, 1)
    assert db.updates == []
```
- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_topic_swap_fallback.py -k "rejected_candidate or candidate_search" -v` Expected: 2건 FAIL — `assert (1, 0, 1) == (1, 1, 0)`, `assert 1 == 5`.
- [ ] **Step 3: 최소 구현** — `git apply --include='backend/app/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-5.diff`. 적용 코드:
```diff
--- a/backend/app/workers/topic_swap_fallback.py
+++ b/backend/app/workers/topic_swap_fallback.py
@@ -81,6 +81,10 @@
 # 못하는 비후보가 한 페이지를 통째로 채울 수 있으므로 정렬 키로 그 뒤를 이어 읽는다.
 TOPIC_SWAP_SELECT_LIMIT = 50
 TOPIC_SWAP_MAX_PAGES = 5
+# 한 슬롯을 교체할 때 살펴보는 측정 질문 후보 수. 고른 질문이 진료과 불일치·수기 목록 문서 없음·
+# 기존 주제와 유사로 맞지 않으면 그 질문을 빼고 다음 후보를 고른다(`_choose_target`은 결정적이라
+# 빼지 않으면 같은 질문을 다시 고른다).
+TOPIC_SWAP_CANDIDATE_LIMIT = 5
 # 인시던트 종결 수렴은 창 전체가 아니라 미완료 이력만 읽으므로 더 넉넉히 둔다. 남은 행은
 # 다음 pass가 이어서 닫는다(멱등).
 TOPIC_SWAP_RECONCILE_LIMIT = 200
@@ -360,31 +364,54 @@
     ).scalar_one_or_none()
 
 
-def _swap_one(db, item: ContentItem, reason: str, *, now: datetime) -> tuple[dict | None, str]:
-    """한 슬롯의 주제를 바꾼다. `(history 항목 또는 None, 결과 라벨)`."""
+def _target_rejection(target, item: ContentItem, hospital) -> str | None:
+    """이 측정 질문으로 바꾸면 안 되는 이유(결과 라벨). 바꿔도 되면 ``None``."""
 
     # 병원 대표 진료과와 어울리지 않는 질문(내과 병원의 영상의학과 질문 등)은 교체 후보에서
     # 뺀다. 61810ef4 → d7a5603e처럼 같은 계열로 바꿔 봐야 같은 이유로 다시 소진된다.
-    hospital = getattr(item, "hospital", None)
-    target = _choose_target(
-        db,
-        item=item,
-        hospital_id=item.hospital_id,
-        exclude_target_ids={item.query_target_id},
-        hospital=hospital,
-    )
-    if target is None:
-        return None, "no_candidate_target"
-    if target_conflicts_with_hospital(target, hospital):
-        return None, "incompatible_specialty"
+    if target_conflicts_with_hospital(target, hospital):
+        return "incompatible_specialty"
     new_topic = str(target.name or "")
     if references_required_for(item.content_type, query_target_id=target.id) and not (
         curated_sources_for_topic([new_topic])
     ):
-        return None, "no_candidate_target"
+        return "no_candidate_target"
     if _topic_is_too_similar(new_topic, item):
-        return None, "similar_topic"
+        return "similar_topic"
+    return None
 
+
+def _swap_target(db, item: ContentItem) -> tuple[Any, str]:
+    """교체할 측정 질문과 결과 라벨. 맞는 질문이 없으면 `(None, 마지막 거절 라벨)`."""
+
+    hospital = getattr(item, "hospital", None)
+    excluded: set = {item.query_target_id}
+    label = "no_candidate_target"
+    for _attempt in range(TOPIC_SWAP_CANDIDATE_LIMIT):
+        target = _choose_target(
+            db,
+            item=item,
+            hospital_id=item.hospital_id,
+            exclude_target_ids=excluded,
+            hospital=hospital,
+        )
+        if target is None:
+            return None, label
+        rejection = _target_rejection(target, item, hospital)
+        if rejection is None:
+            return target, "swapped"
+        label = rejection
+        excluded.add(target.id)
+    return None, label
+
+
+def _swap_one(db, item: ContentItem, reason: str, *, now: datetime) -> tuple[dict | None, str]:
+    """한 슬롯의 주제를 바꾼다. `(history 항목 또는 None, 결과 라벨)`."""
+
+    target, label = _swap_target(db, item)
+    if target is None:
+        return None, label
+
     superseded = _superseded_incident(db, item, reason)
     revision_before = int(item.content_revision or 1)
     entry = {
```
- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_topic_swap_fallback.py tests/test_publish_block_auto_resolve.py -q && .venv/bin/python -m ruff check app/workers/topic_swap_fallback.py tests/test_topic_swap_fallback.py` Expected: `112 passed`, ruff 통과. 통합: `INTEGRATION_DATABASE_URL=... .venv/bin/python -m pytest tests/integration/test_topic_swap_fallback_postgres.py -q` 전부 통과(특히 `test_internal_medicine_slot_is_swapped_to_a_compatible_topic_not_radiology`).
- [ ] **Step 5: 커밋** `git add backend/app/workers/topic_swap_fallback.py backend/tests/test_topic_swap_fallback.py && git commit -m "fix(topic-swap): 맞지 않는 후보 질문을 빼고 다음 후보를 최대 5개까지 고른다"`

**계약 영향:** 없음(교체 횟수 상한 `CONTENT_AUTO_TOPIC_SWAP_MAX`·평생 `TOPIC_SWAP_LIMIT=1`은 그대로, 한 번의 교체에서 후보만 더 본다).
**위험·롤백:** 교체 pass 한 슬롯당 `_choose_target` 쿼리가 최대 5배(병원 질문·이번 달 사용·노출 행동·격차 4쿼리 × 5). 교체 후보는 하루 몇 건 수준이라 무시 가능. revert로 롤백.

---

### Task 0-6a: 인용 없는 모델 단정 HARD도 교정 이력 없이 주제 교체 계단으로

**Files:**
- Modify: `backend/app/workers/tasks.py:1061-1069`(`_auto_correct_blocked_body`의 `not plan.applicable` 분기)
- Test: `backend/tests/test_publish_block_auto_resolve.py:1209-1240`, `backend/tests/test_tasks_nightly.py:2164-2280`

**조사 결과:**
- tasks.py:1061-1069: `plan.applicable`이 거짓이고 교정 이력(`passes`·`exhausted`·`swap_requested_at`)이 **있을 때만** `_escalate_exhausted_correction`. 이력이 없으면 `None` → 호출부의 기존 경로 → `retry_class_for("CONTENT_AI_HARD_FINDING", model_declared_hard=True)` = INPUT_CHANGE_REQUIRED(generation_retry_policy.py:200-210)로 굳는다.
- `topic_swap_fallback.exhausted_body_sample_reason`(:237-262)은 `AUTO_CORRECTION_EXHAUSTED_KEY`가 있으면 모델 HARD 여부와 무관하게 후보로 본다 — `_escalate_exhausted_correction`이 그 키를 쓰므로 교체 pass가 집는다. 교체가 안 되면(기한 없음·후보 없음) 같은 함수가 OPERATOR_REQUIRED로 굳힌다(기존 계단).
- `plan_corrections`가 거절하는 HARD: 인용 없음, 필수 문구 문장 인용, 제목 인용 등(`test_must_use_sentence_and_unquoted_hard_are_not_taken_by_the_pass`).
- `test_tasks_nightly.py::test_style_rewrite_to_fact_hard_skips_image_spend`가 인용 없는 HOSPITAL_FACT HARD 경로를 거친다 — 이미지 예산을 쓰지 않는다는 핵심 단언은 그대로, 메시지 단언만 교체 계단 문구로 바뀐다.
- 알려진 문구 한계(기존): `_escalate_exhausted_correction`은 교체 기한이 없으면(예정일이 스윕 창 밖) 교체를 시도하지 않고 곧바로 `_AUTO_CORRECTION_OPERATOR_MESSAGE`(“…주제 교체를 모두 거쳤지만…”)를 쓴다. 이번 변경으로 이 문구가 더 자주 보일 수 있다 — 별도 후속(문구) 대상.

- [ ] **Step 1: 실패하는 테스트 작성** — `git apply --include='backend/tests/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-6a.diff`
- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_publish_block_auto_resolve.py -k uncorrectable_hard -v` Expected: `[fresh]` FAIL(`TypeError: cannot unpack non-iterable NoneType`), `[corrected]` PASS.
- [ ] **Step 3: 최소 구현** — `git apply --include='backend/app/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-6a.diff`. 패치 전문:
```diff
--- a/backend/app/workers/tasks.py
+++ b/backend/app/workers/tasks.py
@@ -1063,12 +1063,11 @@
     plan = plan_corrections(candidate, review, must_use_messages=must_use)
     state = _stored_auto_correction_state(item)
     if not plan.applicable:
-        if (
-            state.get("passes") or state.get("exhausted") or state.get("swap_requested_at")
-        ) and _stored_model_declared_hard(item):
-            # 이 주제는 이미 교정을 거쳤는데 남은 HARD를 문장 단위로 고칠 수 없다(인용 없음·제목
-            # 인용 등). 기존 경로로 돌려보내면 교체 pass가 바꾸지 못한 슬롯이 기한 지난
-            # SAMPLE_RECOVERABLE로 남는다 — 같은 계단(교체, 그다음 사람)으로 넘긴다.
+        if _stored_model_declared_hard(item):
+            # 모델이 HARD로 단정했지만 문장 단위로 고칠 수 없다(인용 없음·제목·필수 문구 인용 등).
+            # 교정 이력이 없어도 승인 자료 변경을 기다리며 INPUT_CHANGE_REQUIRED로 멈추지 않고 같은
+            # 계단(주제 교체, 그다음 사람)으로 넘긴다. 이 글은 고치지 않고 버려지며 새 주제의 글은
+            # 처음부터 같은 독립 검수를 다시 받는다.
             return _escalate_exhausted_correction(db, item, philosophy, state)
         return None
     limits = correction_limits()
--- a/backend/tests/test_publish_block_auto_resolve.py
+++ b/backend/tests/test_publish_block_auto_resolve.py
@@ -1206,21 +1206,19 @@
     assert not target_fits_hospital(_target("골밀도검사 가능한 정형외과"), _ortho())
 
 
-def test_corrected_topic_left_with_uncorrectable_hard_reaches_operator(worker, monkeypatch):
-    """교정 뒤 재검수가 인용 없는 HARD를 남기고 교체 pass도 바꾸지 못하면 사람의 일로 굳는다."""
+@pytest.mark.parametrize("history", [None, {"passes": 1, "rereviews": 1}], ids=["fresh", "corrected"])
+def test_uncorrectable_hard_goes_to_swap_then_operator(worker, monkeypatch, history):
+    """인용 없는 HARD는 교정 이력과 무관하게 주제 교체 계단을 타고, 교체도 못 하면 사람의 일이다."""
 
     philosophy, calls = worker
     body = _body(NEUTRAL_A, MAPO_PROFILE_SENTENCE)
     finding = _finding("HARD", "HOSPITAL_FACT", "승인 자료에서 심장 초음파를 확인할 수 없습니다.")
     hospital = _hospital()
     _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)
-
-    # 교정을 거치지 않은 주제의 인용 없는 HARD는 이 패스가 맡지 않는다(기존 경로).
-    fresh = _blocked_item(philosophy, body, finding)
-    assert tasks._auto_correct_blocked_body(_DB(), fresh, hospital, philosophy) is None
 
     item = _blocked_item(philosophy, body, finding)
-    item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = {"passes": 1, "rereviews": 1}
+    if history is not None:
+        item.essence_check_summary[mc.AUTO_CORRECTION_KEY] = dict(history)
     calls["item"] = item
 
     state, code, message = tasks._auto_correct_blocked_body(_DB(), item, hospital, philosophy)
--- a/backend/tests/test_tasks_nightly.py
+++ b/backend/tests/test_tasks_nightly.py
@@ -2274,8 +2274,12 @@
 
     assert state == tasks.GenerationItemState.FAILED
     assert code == "CONTENT_AI_HARD_FINDING"
-    assert "근거" in message
-    assert tasks._stored_generation_attempt(item)["reason"] == code
+    # 인용 없는 사실 HARD는 교정 이력 없이도 주제 교체 계단으로 넘어간다(감사 0-6). 예정일이
+    # 스윕 창 밖이라 교체 기한이 없어 곧바로 사람의 일이다.
+    assert "주제" in message
+    attempt = tasks._stored_generation_attempt(item)
+    assert attempt["reason"] == code
+    assert attempt["auto_correction_exhausted"] is True
 
 
 def test_same_context_style_soft_finding_regenerates_body(monkeypatch):
```
- [ ] **Step 4: 통과 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_publish_block_auto_resolve.py tests/test_topic_swap_fallback.py tests/test_must_use_approved_only_sweep.py "tests/test_tasks_nightly.py::test_style_rewrite_to_fact_hard_skips_image_spend" -q` Expected: 모두 통과. `tests/test_tasks_nightly.py` 전체는 기준선 3건만 실패.
- [ ] **Step 5: 커밋** `git add backend/app/workers/tasks.py backend/tests/test_publish_block_auto_resolve.py backend/tests/test_tasks_nightly.py && git commit -m "fix(generation): 문장 단위로 고칠 수 없는 모델 HARD도 주제 교체 계단으로 넘긴다"`

**계약 영향:** CLAUDE.md "LLM 출력은 확률적이므로 …" 문단의 "인용 없는 HARD, 승인 기준 없음, 저장 본문의 금지 표현은 `INPUT_CHANGE_REQUIRED`로 남긴다"를 "승인 기준 없음, 저장 본문의 금지 표현은 `INPUT_CHANGE_REQUIRED`로 남긴다. 문장 단위로 고칠 수 없는 모델 HARD(인용 없음·제목·필수 문구 인용)는 교정 이력과 무관하게 주제 교체 계단(교체, 그다음 사람)으로 넘긴다"로 바꾼다. **대표 확인 필요**(위 "대표 결정" 참조).
**위험·롤백:** 승인 자료가 실제로 틀린 경우(예: 병원 프로필에 없는 장비를 모든 주제가 언급) 교체해도 같은 HARD가 나올 수 있다 — 교체는 슬롯 평생 1회라 비용은 작가 1~2세션으로 묶이고 그 뒤 사람의 일이 된다. revert로 롤백.

---

### Task 0-6b: 수리 예산을 다 쓴 참고자료·FAQ·운영 기준 차단을 교체 계단에 넣고, 진료비·병원 선택 보류는 플래그 뒤에 둔다

**Files:**
- Modify: `backend/app/core/config.py:555`(새 설정 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES: bool = False`)
- Modify: `backend/app/workers/topic_swap_fallback.py:44`(import), `:57-66`(import), `:84-87`(상수), `:130-152`(`_exhausted_body_sample_filter`), `:237-262`(`exhausted_body_sample_reason`), `_target_rejection`·`_swap_target`(0-5에서 만든 함수)에 `reason` 인자
- Test: `backend/tests/test_topic_swap_fallback.py`(파일 끝 0-6b 절), `backend/tests/integration/test_topic_swap_fallback_postgres.py`(파일 끝 1개 매개변수 테스트)

**조사 결과:**
- 교체 후보 SQL(`_exhausted_body_sample_filter`, :130-152)과 파이썬 술어(`exhausted_body_sample_reason`, :237-262)는 `SAMPLE_BODY_CODES`의 OPERATOR_REQUIRED와 교정 소진 HARD만 본다. 수리 코드(`BODY_REPAIR_CODES`)는 수리 세션 예산(`automatic_body_repair.exhausted_days`, `generation_retry_policy.repair_recovery_remains`)이 3일 뒤 끝나면 `next_retry_at=None`으로 멈추고 어느 계단에도 이어지지 않는다 — 감사 확인.
- 수리 코드의 저장 분류: `MISSING_REFERENCES`·`FAQ_FIELDS_MISSING`·`ESSENCE_NOT_ALIGNED`는 `retry_class_for` 기본값 OPERATOR_REQUIRED, `FORBIDDEN_EXPRESSION`·`CONTENT_AI_REVIEW_STALE`은 INPUT_CHANGE_REQUIRED(`_INPUT_CODES`) → 뒤 둘은 교체 대상에서 뺀다.
- 교체는 행의 `essence_check_summary`를 `{generation_attempt: TOPIC_SWAPPED}`로 통째로 바꾸므로(`_reset_values`) `automatic_body_repair`도 사라져 새 주제는 새 수리 예산을 받는다.
- 진료비·병원 선택 보류(`operator_decides`)는 `attempt_is_terminal`이 종착으로 보고 로더가 건너뛰지만, 교체 pass는 로더와 무관하게 자기 SQL로 후보를 고르므로 `attempt_is_terminal`은 고칠 필요가 없다(감사 가정 정정).
- 기존 수기 목록 검사: `_swap_one`의 `references_required_for(...) and not curated_sources_for_topic([new_topic])`(이제 `_target_rejection`). 다만 "대장내시경 비용"처럼 비용 질문도 질환 키워드로 수기 문서가 잡힌다(실측 `curated_sources_for_topic(['대장내시경 비용'])` 참). 참고자료 때문에 막힌 슬롯을 그런 질문으로 바꾸면 같은 보류로 돌아오므로 `reason == "MISSING_REFERENCES"`일 때 `reference_requirement.topic_without_authoritative_source(new_topic)`가 참인 질문을 거른다.
- `ContentItem.essence_check_summary`는 JSONB. 새 SQL 조건의 PostgreSQL 컴파일 결과: `CAST(((content_items.essence_check_summary -> 'automatic_body_repair') ->> 'exhausted_days') AS INTEGER) >= 3` 및 `CAST((... ->> 'operator_decides') AS BOOLEAN) IS NOT true`(NULL 포함).

- [ ] **Step 1: 실패하는 테스트 작성** — `git apply --include='backend/tests/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-6b.diff`. 추가 단위 테스트 전문:
```python
# ── 0-6b: 수리 예산 소진·사람이 정하는 참고자료 보류도 교체 계단에 ──────────────────


def _repair_exhausted(reason="MISSING_REFERENCES", *, exhausted_days=3, **attempt_fields):
    summary = _attempt(reason=reason)
    summary[GENERATION_ATTEMPT_KEY].update(attempt_fields)
    summary[generation_retry_policy.BODY_REPAIR_STATE_KEY] = {
        "period": "2026-09-15",
        "count": 2,
        "exhausted_days": exhausted_days,
    }
    return summary


@pytest.mark.parametrize("reason", ["MISSING_REFERENCES", "FAQ_FIELDS_MISSING", "ESSENCE_NOT_ALIGNED"])
def test_a_body_repair_code_is_a_candidate_once_its_repair_budget_is_spent(reason):
    item = _item(essence_check_summary=_repair_exhausted(reason))

    assert topic_swap_fallback.exhausted_body_sample_reason(item) == reason


@pytest.mark.parametrize(
    "summary",
    [
        _repair_exhausted(exhausted_days=2),  # 수리 예산이 아직 남았다(내일 또 고친다)
        _repair_exhausted(retry_class=GenerationRetryClass.INPUT_CHANGE_REQUIRED.value),
        _repair_exhausted("FORBIDDEN_EXPRESSION"),  # 입력 변경 종착 — 교체 대상 아님
        _repair_exhausted(**{generation_retry_policy.OPERATOR_DECIDES_KEY: True}),
        _attempt(reason="MISSING_REFERENCES"),  # 수리 기록이 없다
    ],
    ids=["budget_left", "input_change", "forbidden", "operator_decides", "no_repair_state"],
)
def test_body_repair_records_that_are_not_swapped(summary):
    assert topic_swap_fallback.exhausted_body_sample_reason(_item(essence_check_summary=summary)) is None


def test_the_operator_decides_hold_is_swapped_only_behind_the_flag(monkeypatch):
    summary = _attempt(reason="MISSING_REFERENCES")
    summary[GENERATION_ATTEMPT_KEY][generation_retry_policy.OPERATOR_DECIDES_KEY] = True
    item = _item(essence_check_summary=summary)

    monkeypatch.setattr(topic_swap_fallback.settings, "CONTENT_TOPIC_SWAP_OPERATOR_DECIDES", False)
    assert topic_swap_fallback.exhausted_body_sample_reason(item) is None

    monkeypatch.setattr(topic_swap_fallback.settings, "CONTENT_TOPIC_SWAP_OPERATOR_DECIDES", True)
    assert topic_swap_fallback.exhausted_body_sample_reason(item) == "MISSING_REFERENCES"


def test_a_reference_blocked_slot_is_not_swapped_into_a_cost_question(monkeypatch):
    cost = _target("대장내시경 비용")
    good = _target()

    def choose(db, *, exclude_target_ids, **kwargs):
        for candidate in (cost, good):
            if candidate.id not in exclude_target_ids:
                return candidate
        return None

    monkeypatch.setattr(topic_swap_fallback, "_choose_target", choose)
    db = _FakeDB([_item(essence_check_summary=_repair_exhausted())])

    report = _run(db)

    assert report.swapped == 1
    assert _values(db)["query_target_id"] == good.id
```
통합 테스트(파일 끝 추가) 전문:
```python
# ── 0-6b: 수리 예산 소진·사람이 정하는 참고자료 보류의 후보 SQL ─────────────────────


def _repair_blocked_summary(topic_id, *, exhausted_days: int, operator_decides: bool) -> str:
    attempt = fresh_generation_attempt(topic_id=str(topic_id))
    attempt.update(
        {
            "reason": "MISSING_REFERENCES",
            "retry_class": GenerationRetryClass.OPERATOR_REQUIRED.value,
        }
    )
    if operator_decides:
        attempt[generation_retry_policy.OPERATOR_DECIDES_KEY] = True
    return json.dumps(
        {
            GENERATION_ATTEMPT_KEY: attempt,
            generation_retry_policy.BODY_REPAIR_STATE_KEY: {
                "period": "2026-09-15",
                "count": 2,
                "exhausted_days": exhausted_days,
            },
        }
    )


@pytest.mark.parametrize(
    ("exhausted_days", "operator_decides", "flag", "swapped"),
    [
        (3, False, False, 1),  # 수리 3일 소진 → 교체
        (2, False, False, 0),  # 수리 예산이 남았다
        (3, True, False, 0),  # 사람이 정하는 보류 — 기본은 교체하지 않는다
        (0, True, True, 1),  # 대표가 켠 경우에만 보류도 교체
    ],
    ids=["repair_exhausted", "repair_left", "operator_decides_off", "operator_decides_on"],
)
def test_the_candidate_sql_selects_reference_blocks_by_their_ladder(
    pg_conn, pg_session, monkeypatch, exhausted_days, operator_decides, flag, swapped
):
    monkeypatch.setattr(
        topic_swap_fallback.settings, "CONTENT_TOPIC_SWAP_OPERATOR_DECIDES", flag
    )
    hospital_id = _seed_hospital(pg_conn)
    target_id = pg_conn.execute(
        text("SELECT id FROM ai_query_targets WHERE hospital_id=:h AND name=:name"),
        {"h": hospital_id, "name": "대장내시경 수면 여부"},
    ).scalar_one()
    _seed_item(
        pg_conn,
        hospital_id,
        scheduled_date=SLOT - timedelta(days=1),
        summary=_repair_blocked_summary(
            target_id, exhausted_days=exhausted_days, operator_decides=operator_decides
        ),
    )

    assert _swap(pg_session).swapped == swapped
```
- [ ] **Step 2: 실패 확인** Run: `cd backend && .venv/bin/python -m pytest tests/test_topic_swap_fallback.py -v -k "repair or operator_decides or cost_question"` Expected: 5건 FAIL(수리 코드 3매개변수 `assert None == 'MISSING_REFERENCES'` 등, 플래그 테스트 `AttributeError: ... has no attribute 'CONTENT_TOPIC_SWAP_OPERATOR_DECIDES'`, 비용 질문 테스트 `assert 0 == 1`), `test_body_repair_records_that_are_not_swapped` 5건 PASS.
- [ ] **Step 3: 최소 구현** — `git apply --include='backend/app/*' /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/p0-retry-patches/task-0-6b.diff`. 적용 코드 전문:
```diff
--- a/backend/app/core/config.py
+++ b/backend/app/core/config.py
@@ -553,6 +553,10 @@
     CONTENT_AUTO_CORRECTION_MAX_PASSES: int = 2
     CONTENT_AUTO_CORRECTION_MAX_REREVIEWS: int = 2
     CONTENT_AUTO_TOPIC_SWAP_MAX: int = 1
+    # 진료비·병원 선택 글의 참고자료 보류(사람이 정함, `operator_decides`)도 주제 교체 계단에
+    # 넣는가. 켜면 수기 목록 문서가 있는 의료 주제의 다른 측정 질문으로 한 번 바꿔 쓴다.
+    # 기본은 꺼짐 — 계약(CLAUDE.md "곧바로 OPERATOR_REQUIRED(사람이 정함)") 변경이라 대표 결정 뒤에 켠다.
+    CONTENT_TOPIC_SWAP_OPERATOR_DECIDES: bool = False
     # 같은 원고 후보의 독립 검수가 공급자 장애·잘린 응답으로 연속 실패할 때 자동 재검수를
     # 사는 최대 횟수(KST 날을 넘어 누적). 닿으면 사람의 일(OPERATOR_REQUIRED)로 넘긴다.
     CONTENT_AI_REVIEW_UNAVAILABLE_MAX_RETRIES: int = 6
--- a/backend/app/workers/topic_swap_fallback.py
+++ b/backend/app/workers/topic_swap_fallback.py
@@ -41,7 +41,10 @@
 from app.services.content_similarity import DUPLICATE_TITLE_THRESHOLD, topic_similarity
 from app.services.content_target_planner import _choose_target
 from app.services.incidents import mark_recovered, mark_retrying
-from app.services.reference_requirement import references_required_for
+from app.services.reference_requirement import (
+    references_required_for,
+    topic_without_authoritative_source,
+)
 from app.services.reference_verification import curated_sources_for_topic
 from app.services.specialty_compatibility import target_conflicts_with_hospital
 from app.services.sync_async_bridge import SyncAsyncBridge
@@ -56,12 +59,16 @@
 from app.workers.generation_incident_control import generation_incident_dedupe_key
 from app.workers.generation_retry_policy import (
     AUTO_CORRECTION_EXHAUSTED_KEY,
+    BODY_REPAIR_STATE_KEY,
+    OPERATOR_DECIDES_KEY,
     SAMPLE_BODY_CODES,
     SAMPLE_BODY_DAILY_BUDGET,
+    SAMPLE_EXHAUSTED_DAY_LIMIT,
     GenerationRetryClass,
     environment_attempt_period,
     has_model_declared_hard_finding,
     next_recovery_deadline,
+    repair_recovery_remains,
 )
 from app.workers.nightly_generation_batch import (
     GENERATION_WRITE_BACK_STATUSES,
@@ -81,6 +88,11 @@
 # 못하는 비후보가 한 페이지를 통째로 채울 수 있으므로 정렬 키로 그 뒤를 이어 읽는다.
 TOPIC_SWAP_SELECT_LIMIT = 50
 TOPIC_SWAP_MAX_PAGES = 5
+# 저장 본문 수리 세션 예산(3일)을 다 쓴 뒤 주제 교체 계단을 타는 코드. 같은 주제로 작가가 매일
+# 다시 써도 풀리지 않은 참고자료·FAQ 필드·운영 기준 불일치다. 금지 표현(FORBIDDEN_EXPRESSION)과
+# 검수 지문 불일치(CONTENT_AI_REVIEW_STALE)는 입력 변경 종착(INPUT_CHANGE_REQUIRED)이라 뺀다.
+BODY_REPAIR_SWAP_CODES = frozenset({"MISSING_REFERENCES", "FAQ_FIELDS_MISSING", "ESSENCE_NOT_ALIGNED"})
+REFERENCE_SWAP_REASON = "MISSING_REFERENCES"
 # 한 슬롯을 교체할 때 살펴보는 측정 질문 후보 수. 고른 질문이 진료과 불일치·수기 목록 문서 없음·
 # 기존 주제와 유사로 맞지 않으면 그 질문을 빼고 다음 후보를 고른다(`_choose_target`은 결정적이라
 # 빼지 않으면 같은 질문을 다시 고른다).
@@ -147,7 +159,8 @@
     """
 
     attempt = _stored_attempt_json()
-    return or_(
+    repair_state = ContentItem.essence_check_summary[BODY_REPAIR_STATE_KEY]
+    clauses = [
         and_(
             attempt["retry_class"].as_string() == GenerationRetryClass.OPERATOR_REQUIRED.value,
             attempt["reason"].as_string().in_(sorted(SAMPLE_BODY_CODES)),
@@ -156,7 +169,23 @@
             attempt["reason"].as_string() == "CONTENT_AI_HARD_FINDING",
             attempt[AUTO_CORRECTION_EXHAUSTED_KEY].as_boolean().is_(True),
         ),
-    )
+        # 저장 본문 수리 예산을 3일 다 쓴 참고자료·FAQ·운영 기준 차단(사람이 정하는 보류 제외).
+        and_(
+            attempt["reason"].as_string().in_(sorted(BODY_REPAIR_SWAP_CODES)),
+            attempt["retry_class"].as_string()
+            != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value,
+            attempt[OPERATOR_DECIDES_KEY].as_boolean().is_not(True),
+            repair_state["exhausted_days"].as_integer() >= SAMPLE_EXHAUSTED_DAY_LIMIT,
+        ),
+    ]
+    if settings.CONTENT_TOPIC_SWAP_OPERATOR_DECIDES:
+        clauses.append(
+            and_(
+                attempt["reason"].as_string() == REFERENCE_SWAP_REASON,
+                attempt[OPERATOR_DECIDES_KEY].as_boolean().is_(True),
+            )
+        )
+    return or_(*clauses)
 
 
 def topic_swap_limit() -> int:
@@ -253,6 +282,21 @@
         return None
     attempt = read_generation_attempt(item)
     reason = str(attempt.get("reason") or "")
+    if attempt.get(OPERATOR_DECIDES_KEY):
+        # 진료비·병원 선택 글의 참고자료 보류는 사람이 정한다. 대표가 켠 경우에만 교체한다.
+        if reason == REFERENCE_SWAP_REASON and settings.CONTENT_TOPIC_SWAP_OPERATOR_DECIDES:
+            return reason
+        return None
+    if (
+        reason in BODY_REPAIR_SWAP_CODES
+        and attempt.get("retry_class") != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
+    ):
+        summary = getattr(item, "essence_check_summary", None)
+        repair_state = summary.get(BODY_REPAIR_STATE_KEY) if isinstance(summary, dict) else None
+        # 수리 예산이 남았으면(오늘 또는 내일) 아직 작가가 고친다. 3일을 다 쓴 뒤에만 바꾼다.
+        if isinstance(repair_state, dict) and not repair_recovery_remains(repair_state):
+            return reason
+        return None
     if reason == "CONTENT_AI_HARD_FINDING" and attempt.get(AUTO_CORRECTION_EXHAUSTED_KEY):
         # 지적 문장 최소 교정을 상한까지 거친 사실·안전 차단이다. 승인 자료 변경만 기다리지
         # 않고 같은 슬롯·예정일로 다른 질문을 답하게 한다(`tasks._escalate_exhausted_correction`).
@@ -364,7 +408,7 @@
     ).scalar_one_or_none()
 
 
-def _target_rejection(target, item: ContentItem, hospital) -> str | None:
+def _target_rejection(target, item: ContentItem, hospital, reason: str) -> str | None:
     """이 측정 질문으로 바꾸면 안 되는 이유(결과 라벨). 바꿔도 되면 ``None``."""
 
     # 병원 대표 진료과와 어울리지 않는 질문(내과 병원의 영상의학과 질문 등)은 교체 후보에서
@@ -372,6 +416,9 @@
     if target_conflicts_with_hospital(target, hospital):
         return "incompatible_specialty"
     new_topic = str(target.name or "")
+    if reason == REFERENCE_SWAP_REASON and topic_without_authoritative_source(new_topic):
+        # 참고자료 때문에 막힌 슬롯을 다시 진료비·병원 선택 질문으로 바꾸면 같은 보류로 돌아온다.
+        return "no_candidate_target"
     if references_required_for(item.content_type, query_target_id=target.id) and not (
         curated_sources_for_topic([new_topic])
     ):
@@ -381,7 +428,7 @@
     return None
 
 
-def _swap_target(db, item: ContentItem) -> tuple[Any, str]:
+def _swap_target(db, item: ContentItem, reason: str) -> tuple[Any, str]:
     """교체할 측정 질문과 결과 라벨. 맞는 질문이 없으면 `(None, 마지막 거절 라벨)`."""
 
     hospital = getattr(item, "hospital", None)
@@ -397,7 +444,7 @@
         )
         if target is None:
             return None, label
-        rejection = _target_rejection(target, item, hospital)
+        rejection = _target_rejection(target, item, hospital, reason)
         if rejection is None:
             return target, "swapped"
         label = rejection
@@ -408,7 +455,7 @@
 def _swap_one(db, item: ContentItem, reason: str, *, now: datetime) -> tuple[dict | None, str]:
     """한 슬롯의 주제를 바꾼다. `(history 항목 또는 None, 결과 라벨)`."""
 
-    target, label = _swap_target(db, item)
+    target, label = _swap_target(db, item, reason)
     if target is None:
         return None, label
 
```
그리고 `exhausted_body_sample_reason`의 독스트링(:238-243) 끝에 한 줄 추가: "저장 본문 수리 예산(3일)을 다 쓴 참고자료·FAQ·운영 기준 차단도 후보다. 진료비·병원 선택 참고자료 보류는 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES`가 켜졌을 때만 후보다."
- [ ] **Step 4: 통과 확인**
  - Run: `cd backend && .venv/bin/python -m pytest tests/test_topic_swap_fallback.py tests/test_publish_block_auto_resolve.py tests/test_reference_operator_decides.py tests/test_terminal_slot_reclaim.py -q && .venv/bin/python -m ruff check app/core/config.py app/workers/topic_swap_fallback.py tests/test_topic_swap_fallback.py tests/integration/test_topic_swap_fallback_postgres.py` Expected: 전부 통과(드래프팅 실측 test_topic_swap_fallback+publish_block 123 passed), ruff 통과.
  - Run(DB 필요, **드래프팅 중 미실행**): `cd backend && INTEGRATION_DATABASE_URL=postgresql+psycopg2://…/reputation_test .venv/bin/python -m pytest tests/integration/test_topic_swap_fallback_postgres.py -v` Expected: 새 4건(`repair_exhausted`·`operator_decides_on` swapped=1, `repair_left`·`operator_decides_off` swapped=0)과 기존 테스트 전부 PASS.
- [ ] **Step 5: 커밋** `git add backend/app/core/config.py backend/app/workers/topic_swap_fallback.py backend/tests/test_topic_swap_fallback.py backend/tests/integration/test_topic_swap_fallback_postgres.py && git commit -m "fix(topic-swap): 수리 예산을 다 쓴 참고자료·FAQ 차단을 주제 교체 계단에 넣는다(진료비 보류는 플래그)"`

**계약 영향:**
- CLAUDE.md "LLM 출력은 확률적이므로…" 문단에 추가: "저장 본문 수리 세션 예산(3일)을 다 쓴 `MISSING_REFERENCES`·`FAQ_FIELDS_MISSING`·`ESSENCE_NOT_ALIGNED`도 같은 주제 교체 계단(교체 1회, 그다음 사람)을 탄다. 참고자료 때문에 막힌 슬롯은 진료비·병원 선택 질문으로 바꾸지 않는다."
- 진료비·병원 선택 문장("비면 그 보류는 자동 본문 수리·주제 교체 없이 곧바로 `OPERATOR_REQUIRED`(사람이 정함)다")은 **플래그를 켜기 전까지 그대로 참**이다. 대표가 켜기로 하면 "…곧바로 OPERATOR_REQUIRED다. 단 `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES`가 켜져 있으면 먼저 수기 목록 문서가 있는 의료 주제 질문으로 한 번 교체한다"로 고친다.
**위험·롤백:** (1) `CAST(... AS INTEGER)`는 `automatic_body_repair.exhausted_days`가 정수가 아닌 문자열인 행이 있으면 쿼리 전체가 실패할 수 있다(Postgres는 AND 단락 평가를 보장하지 않음) — 이 값은 `spend_repair_session`만 쓰고 항상 int다. 배포 전 확인 쿼리: `SELECT count(*) FROM content_items WHERE essence_check_summary->'automatic_body_repair' ? 'exhausted_days' AND jsonb_typeof(essence_check_summary->'automatic_body_repair'->'exhausted_days') <> 'number';` → 0이어야 한다. (2) 플래그 기본 꺼짐이라 진료비 보류 동작은 그대로. (3) revert로 롤백, 데이터 정리 불필요.

---

### 후속(이 계획 범위 밖, 기록만)

- PROVIDER_*로 굳은 빈 슬롯이 게이트에서 `CONTENT_NOT_GENERATED`로 보고될 때 `scheduled_recovery_owns_blocker`는 저장 원인이 증상 코드와 달라 소유를 인정하지 않아 OPEN(“자동 복구가 {due}에 다시 시도합니다” 문구)으로 열린다(tasks.py:1517 주석이 의도된 판정이라 밝힘). 기한 안의 자동 복구를 사람의 일로 세지 않으려면 인시던트 소유 판정을 "저장 분류가 ENV/SAMPLE이고 기한 문자열이 있으면 소유"로 넓혀야 한다 — `draft-p1-incidents` 범위와 겹치므로 그쪽에서 판단.
- `_escalate_exhausted_correction`이 교체 기한이 없을 때(예정일이 스윕 창 밖) "주제 교체를 모두 거쳤지만" 문구를 쓰는 부정확함(0-6a 조사 결과 참조).


## 검증 기록(드래프팅 중 실행)

- 6개 패치를 main `8c8f6e60` 사본에 순서대로 `git apply` — 모두 성공.
- 관련 49개 단위 테스트 파일(generation_attempt·retry_policy·topic_swap·게이트·인시던트·교정 경로를 언급하는 파일)을 패치 전/후로 실행: 전 `28 failed, 1649 passed, 17 errors`(기존 실패), 후 동일 실패 목록 + 새 테스트 전부 통과. 차이 0건(`test_tasks_nightly.py::test_style_rewrite_to_fact_hard_skips_image_spend`는 0-6a 패치에 수정이 포함돼 있다).
- 각 단계의 새 테스트가 이전 단계 코드에서 실패하는 것(빨강)을 확인: 0-3a는 ImportError(`ENVIRONMENT_SINCE_KEY`), 0-3b는 502·연결 오류가 `GENERATION_FAILED`, 0-4는 새 4건 실패, 0-5는 새 2건 실패, 0-6a는 `fresh` 매개변수 실패, 0-6b는 새 단위 테스트 실패.
- `_exhausted_body_sample_filter()`의 PostgreSQL 컴파일 결과를 확인했다(`CAST(((essence_check_summary -> 'automatic_body_repair') ->> 'exhausted_days') AS INTEGER) >= 3` 등). 통합 테스트(0-6b Step 4)는 로컬 DB가 없어 **실행하지 못했다** — 실행자가 반드시 돌린다.
- 전체 단위 스위트(`tests/ --ignore=tests/integration`, 6,996건)는 이 머신에서 DB·Redis 없이 돌리면 패치 전·후 모두 12% 지점의 async 테스트에서 멈춘다(환경 문제, 기준선 동일). 실행자는 README/CI env를 export하고 `make test-backend-local`로 전체를 돌려 기준선과 비교한다.

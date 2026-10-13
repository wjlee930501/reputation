# Phase 3 — 콘텐츠 게이트 조정

작성: 2026-10-13 · 기준 커밋 `31751a88`(main, Phase 0 머지·배포본) · 대상: 대표 결정 D23·D24(2026-10-13)와 승인된 엔지니어링 기본값 E1~E5

입력 자료: `scratchpad/masterplan/gate-strictness-review.md`(게이트 엄격도 검토), `gate-stats.md`·`gate-stats.jsonl`(운영 DB 30일 집계, 호출자가 말한 `gate-block-stats.md`는 이 파일이다), `PREAMBLE.md`.

## 0. 검증 방식과 결과

모든 태스크를 저장소 사본 두 벌(`scratchpad/p3base` = main 그대로, `scratchpad/p3proto` = 이 계획을 적용한 상태)에 실제로 적용해 돌렸다. 저장소 파일은 건드리지 않았다.

- **최종 상태 패치**: `scratchpad/masterplan/p3-patches/p3-all.diff`(앱 16파일 + 테스트 22파일). `git apply --check`가 main `31751a88`에서 통과하고, 새 main 사본에 `patch -p1`로 적용한 뒤 핵심 13파일이 865 passed, `ruff check app tests` 통과. 각 태스크의 코드는 아래 본문에 그대로 싣고, **기존 테스트를 새 계약에 맞게 고친 부분**(D23 30여 건 등)은 이 패치의 해당 파일 hunk를 정본으로 쓴다.
- **빨강 확인**: 새 테스트만 main 코드 위에 얹어(`scratchpad/p3red`) 돌렸다. 새 기능 테스트 43건 FAILED, 새 모듈 import 테스트 2파일 수집 ERROR. 나머지 24건은 회귀 가드(광고 문형이 계속 막히는지 등)라 main에서도 통과하는 것이 맞다.
- **초록 확인**: `p3proto`에서 CI와 같은 env(`scratchpad/masterplan/testenv.sh`, 로컬 컨테이너 rep-pg-main·rep-pg-aux·rep-redis)로 전체 백엔드 테스트(`pytest tests --ignore=tests/mutation`)를 돌렸다: **8040 passed, 42 skipped, 12 failed**. 실패 12건(`test_admin_deep_links`·`test_celery_routing`·`test_fable5_contracts`·`test_migration_orm_drift_postgres`·`test_reserved_slugs` 4·`test_user_facing_terms_guard` 4)은 저장소 밖 파일(`admin/`·`site/`·`docker-compose`·`scripts/`)을 읽는 테스트라 `backend/`만 있는 스크래치 사본에서 생긴다 — **main 사본(`p3base`)에서도 같은 12건이 똑같이 실패**함을 확인했다. 실제 저장소에서는 0 failed여야 한다. 관련 묶음 10파일(content_engine·tasks_nightly·topic_swap·recovery_ladder·incident_copy 등)은 main 601 passed / 적용본 602 passed.
- `ruff check app tests` 통과(main에도 있던 `tasks.py`의 `# noqa` 경고 3건만 남는다).
- 주의: DB·Redis env 없이 `tests/test_content_engine.py`를 돌리면 main에서도 `test_forbidden_response_is_discarded_and_second_complete_content_is_returned`가 멈추고 12건이 실패한다(공급자 사용량 기록이 DB에 붙으려다 기다린다). 이 계획과 무관하다. **반드시 `source scratchpad/masterplan/testenv.sh` 뒤에 돌린다.**

실행 전제:

```bash
source /private/tmp/claude-501/-Users-woojinlee-Documents-projects-reputation/284bb7f0-8347-42a7-8ab8-f3eda1bb3276/scratchpad/masterplan/testenv.sh
cd /Users/woojinlee/Documents/projects/reputation/backend
```

줄 번호는 main `31751a88` 기준이다. 앞 태스크를 적용하면 같은 파일의 줄이 밀리므로, 각 Step의 **앵커 문자열**(인용한 원문 줄)로 위치를 찾는다. Phase 1 브랜치(`claude/autonomy-p1`, 미머지)는 `tasks.py`의 V0 재실행 부분(2880~3070줄대)과 `generation_run_control.defer_operation_run`만 바꾸므로 이 계획의 hunk와 겹치지 않는다. 먼저 머지되면 줄 번호만 밀린다.

## 1. 이미 바뀐 것(다시 하지 않는다)

| 단계 | 내용 | 위치 |
|---|---|---|
| Phase 0 (배포됨) | 주제당 작가 6회 소진 시 3일 대기 없이 즉시 교체(D1) | `topic_swap_fallback.py:286-303` |
| Phase 0 | 교체된 주제가 작가·검수·본문 전송 예산을 새로 받음(`prior_topic_spend`) | `generation_attempt_state.py:45-55` |
| Phase 0 | 인용 없는 모델 단정 HARD → 교체 계단(0-6a) | `tasks.py:1061-1069` (`_stored_model_declared_hard` → `_escalate_exhausted_correction`) |
| Phase 0 | 환경 장애 하루 4회 + 3일 창 | `generation_retry_policy.py:31-54` |
| Phase 0 | 진료비 참고자료 보류를 교체에 넣는 플래그(기본 꺼짐) | `config.py:556-559` `CONTENT_TOPIC_SWAP_OPERATOR_DECIDES` |
| 10/07 `4807f566`(10/08 배포) | 종착 기록(입력 그대로)을 로더·워커가 다시 집지 않음 | `tasks.py:1239-1297` `_terminal_attempt_is_unchanged` |
| Phase 1 (미머지) | UNCERTAIN만 남은 **공개** 글을 상위 모델이 재검수해 자동으로 닫음(1-10b) | `claude/autonomy-p1`의 `post_publish_ai_review.py` |

## 2. 감사·검토 가정과 다른 점(코드로 확인)

1. **E1-d("HARD 대기 재집기 하루 7회")의 대부분은 이미 고쳐졌다.** 검토의 근거인 30일 `FAILED/CONTENT_AI_HARD_FINDING` 269회는 9/13~10/13 창이다. 종착 재집기 수정 `4807f566`(테스트 머리말: "30일 GENERATE_CONTENT_ITEM FAILED 267건 … 모두 CONTENT_AI_HARD_FINDING 종착 슬롯의 재집기")이 10/08 배포본(`c5ada5a1`)부터 들어 있다. `CONTENT_AI_HARD_FINDING`은 `SAMPLE_BODY_CODES`(`generation_retry_policy.py:105-107`)라 `OPERATOR_REQUIRED`로 굳은 행은 이미 종착이다. 교체 대기 행(`SAMPLE_RECOVERABLE`+`next_retry_at`)은 교체 pass가 같은 스윕에서 로더보다 **먼저** 돈다(`tasks.py:5480`, `:6133`) — 기한이 "그 슬롯을 집는 다음 스윕"이라 교체 전에 로더가 공짜로 집는 경우는 운영자 재시도 정도다. 남은 공회전은 **모델 단정 HARD(`INPUT_CHANGE_REQUIRED`)인데 교정 패스가 맡지 못하는 행**(사람이 편집한 DRAFT 등 `correction_allowed_for`가 거짓, `tasks.py:1056`)뿐이다. Task 3-5는 이것만 고치고, 배포 전 측정으로 필요 여부를 다시 본다.
2. **`FORBIDDEN_EXPRESSIONS`(표시용 21개)에 새 어휘를 넣으면 안 된다.** `essence_engine.MANDATORY_AVOID_MESSAGES`(`essence_engine.py:55-58`)가 그 목록을 문자열로 이어 붙이고, `mandatory_safety_findings`(`:92-101`)가 모든 Essence 초안·승인본에 **그 문자열 그대로**가 있는지 본다(승인 API `api/admin/essence.py:1988`, 자동 검수 `essence_auto_review.py:754`). 목록을 바꾸면 기존 초안 전부가 "필수 안전 규칙 없음"으로 승인·자동 검수에서 막힌다. 새 어휘는 작가 프롬프트용 `FORBIDDEN_PATTERN_VARIANTS`(`medical_filter.py:81-95`)에만 넣는다.
3. **필터에 패턴을 더하면 생성·발행만이 아니라 공개 표면이 배포 즉시 바뀐다.** `check_forbidden`은 공개 가시성(`content_visibility.py:135`, 걸리면 공개 글이 숨는다), 병원 공개 문구(`api/public/site.py:439` about, `:593` 진료 항목, `:681` 히어로), Essence 자동 검수(`essence_auto_review.py:760`, 걸리면 보류), 승인 필수 문구 제외(`must_use_verbatim.py:211`, 걸리면 작가 요구에서 빠지고 승인본당 운영자 기록 1건), 프로필 자동 입력 표시(`hospital_profile_autofill.py:388`), 관리자 병원 PATCH(`api/admin/hospitals.py:311`), 리포트 줄 필터(`report_engine.py:901`)에도 쓰인다. 그래서 Task 3-2(배포 전 영향 조회)를 둔다.
4. **D23은 공개 가시성도 고쳐야 한다.** 발행 게이트만 풀면, 참고자료 0개로 발행된 진료비 글을 `public_surface_has_required_references`(`content_publication.py:95-114`, 유형만 본다)가 공개 표면에서 숨긴다.
5. **GENERATION_REJECTED의 원인은 일부 저장되고 있다.** 분류기(`generation_run_control.py:214-236`)가 원인별 운영자 문구를 만들고, 그 문구가 시도 기록 `message`(`tasks.py:1430-1431`)와 OperationRun `safe_error_message`에 남는다. 없는 것은 (a) 기계 라벨, (b) **치유되거나 재작성으로 넘어간 중간 거절**(최종 실패만 남는다), (c) 검사+'치료' 제목 분류(기본 문구로 떨어진다)다. Task 3-13이 이 셋을 채운다.
6. **E4를 문자 그대로(24h→7일) 하면 장애 재사용 경로가 죽는다.** 재사용 창(`REFERENCE_CHECK_REUSE_WINDOW`, `reference_verification.py:86`)도 7일이라, 신선하지 않은(7일 초과) 판정은 언제나 재사용 창도 지나 있다. 재사용 창을 14일로 함께 늘린다(열린 질문 Q4).
7. **분량은 지금도 SOFT다**(`content_engine.py:1841-1854`는 목록만 돌려준다). "저장되지 않습니다"는 프롬프트 4곳(`:306-307`, `:344-345`, `:493`, `:1751`)의 거짓 문구다. 진짜 하한은 최소 교정(`content_minimal_correction.py:836-842`)만 지킨다. 이 계획의 금지 표현 삭제 치유(Task 3-8)도 같은 하한을 지킨다 — 프로토타입에서 하한 없이 지웠더니 본문이 한 줄짜리로 저장되는 경우가 실제로 나왔다(`test_a_rewrite_round_carries_every_deterministic_rejection_so_far`의 2회차).
8. **결정적 수락 기준을 바꾸면 게이트 카탈로그 판을 올리는 기존 장치가 있다**(`tasks.py:641-645` `GENERATION_GATE_CATALOG_VERSION`, 주석: "medical-ad filters, price/coverage rules … 바뀌면 올린다"). 판을 올리면 이미 막힌 슬롯이 새 기준으로 한 번 다시 평가받는다(지문 `context`가 바뀐다). D23 이전에 생긴 진료비 보류(`operator_decides`) 기록 3건도 이 경로로 풀린다. Task 3-15.

## 3. 태스크 목록과 순서

일정 영향 순(대표 지시): D24 → E1 → D23 → E2 → E3 → E4 → E5 → 릴리스.

| # | 태스크 | 구분 | 파일 |
|---|---|---|---|
| 3-1 | 공통 필터에 가격 우위·할인·이벤트·무료 유인(라벨 `할인·유인`) 하드 추가 | D24 | `utils/medical_filter.py` |
| 3-2 | 배포 전 영향 조회 모듈(공개 글·병원 문구·승인 필수 문구) | D24 | `utils/forbidden_impact_scan.py`(신규) |
| 3-3 | HARD와 함께 나온 인용 UNCERTAIN을 발행 전 교정·삭제형 재작성에 함께 싣기 | E1 | `workers/tasks.py`, `services/content_review_feedback.py` |
| 3-4 | 교정본 재검수에 "직전 지적과 교정 전·후 문장" 싣기 | E1 | `services/content_ai_review.py`, `services/content_minimal_correction.py` |
| 3-5 | 아무것도 맡지 못한 모델 단정 HARD 차단을 입력이 바뀔 때까지 종착으로 | E1 | `workers/generation_retry_policy.py`, `workers/tasks.py` |
| 3-6 | 진료비·병원 선택 글 참고자료 선택 사항 | D23 | `services/reference_requirement.py`, `content_publication.py`, `reference_publication.py`, `content_engine.py` |
| 3-7 | 결정적 치유 사슬 + 병원명 덧붙이기 + 필수 문구 원문 덧붙이기 | E2 | `services/content_engine.py` |
| 3-8 | 본문 문장의 금지 표현은 그 문장 삭제 | E2 | `services/content_engine.py` |
| 3-9 | 검사 이름 뒤 낱말 '치료' 제목 결정적 교정 | E2 | `services/question_wellformed.py`, `content_engine.py` |
| 3-10 | 확인된 필터 오탐 6종을 필터 안 문맥 예외로(양방향 테스트) | E3 | `utils/medical_filter.py` |
| 3-11 | 분량 "저장되지 않습니다" 문구를 SOFT 실제 동작에 맞춤 | E3 | `services/content_engine.py` |
| 3-12 | 발행 재검증 신선도 24h → 7일(재사용 창 14일) | E4 | `services/reference_verification.py` |
| 3-13 | 결정적 거절 사유 라벨·세션별 거절/치유 횟수 저장 | E5 | `workers/generation_retry_policy.py`, `generation_run_control.py`, `content_engine.py`, `content_generation_review.py`, `content_publication.py`, `tasks.py` |
| 3-14 | 운영 조회 q9(사후 검수 FLAGGED 분포)·q10(거절 사유) — main 병합 때 q7·q8에서 옮김 | E5 | `utils/ops_sql_report.py` |
| 3-15 | 게이트 카탈로그 판 올리기 + 배포 전후 확인 + CLAUDE.md | 릴리스 | `workers/tasks.py`, `CLAUDE.md` |

배포 단위: 3-1~3-15를 한 PR·한 배포로 낸다(카탈로그 판을 한 번만 올리기 위해). 나눠 낸다면 각 배포가 3-15의 판 올리기를 함께 가져간다.

---

### Task 3-1: 공통 필터에 가격 우위·할인·이벤트·무료 유인을 하드로 추가 (D24)

**Files:**
- Modify: `backend/app/utils/medical_filter.py:71`(`"흉터 없는"` 뒤에 새 라벨), `:94`(`FORBIDDEN_PATTERN_VARIANTS` 끝), `:114-134`(`_ContextExemption`에 `unless_preceded_by`), `:137`(`_ALLOWED_CONTEXTS` 앞에 `_HOSPITAL_SELF`, 사전 끝에 `"할인·유인"` 예외)
- Test: `backend/tests/test_medical_filter.py`(끝에 4개 테스트)

**조사 결과:**
- 대표 범주 ③(최저가·가격 우위·노골적 할인·이벤트·무료 유인)은 공통 필터 21개 패턴(`medical_filter.py:26-72`)에 **없다**. 가격·무료는 생성 단계 전용 `UNVERIFIED_PRICE_PATTERNS`·`_HOSPITAL_*_FREE_*`(`content_engine.py:136-171`, 검사 `:1857-1891`)만 본다. 발행 게이트(`content_publication.py:306-326`)·공개 가시성(`content_visibility.py:135`)·관리자 PATCH(`api/admin/content.py:756`)는 모두 `check_forbidden_content_fields`만 부르므로 **새 라벨 하나를 `FORBIDDEN_PATTERNS`에 넣으면 네 경로가 한 번에 막는다**(호출부 수정 없음). 생성 단계의 가격 검사는 그대로 둔다(구체 금액·부담률은 범주 ③이 아니라 '미검증 사실'이라 생성 전용 그대로가 맞다).
- 라벨 이름은 `"할인·유인"`이다. 라벨은 발행 차단 사유 문구(`"의료광고 금지 표현: 할인·유인"`, `content_publication.py:315`)와 로그에만 보인다.
- 의학 용어와의 충돌을 프로토타입에서 확인했다: `이벤트`는 '심혈관 이벤트가 발생'(의학), `최저`는 '최저혈압'·'최저 기온', `무료 검진`은 '국가에서 시행하는 무료 검진'(공적 제도), `본인부담금이 면제`는 의료급여 안내(조사 '이'가 붙는다)다. 패턴 모양(`이벤트\s*(?:가격|기간|진행\s*중|…)`, `최저가|최저\s*(?:가격|비용|수가)`, `본인\s*부담금?\s*(?:면제|…)` — 조사 없는 광고 문형만)과 문맥 예외 하나(공적 검진, 단 '본원·저희'가 가까이 있으면 예외 아님)로 비켜 간다.
- **`FORBIDDEN_EXPRESSIONS`는 바꾸지 않는다**(§2-2). 작가 프롬프트(`content_engine.py:466-468`이 `forbidden_vocabulary_for_prompt()`를 정적 시스템 블록에 렌더)에는 `FORBIDDEN_PATTERN_VARIANTS`로 보인다. 정적 블록이 바뀌므로 배포 직후 첫 호출에서 프롬프트 캐시 접두어가 한 번 새로 만들어진다(비용 영향 무시할 수준).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_medical_filter.py` 끝에 추가(상단 import는 그대로 — `FORBIDDEN_EXPRESSIONS`·`check_forbidden`·`check_forbidden_content_fields`·`forbidden_vocabulary_for_prompt`·`FORBIDDEN_CHECK_FIELDS`가 이미 있다):

```python
# ── D24(2026-10-13 대표 결정): 가격 우위·할인·이벤트·무료 유인은 모든 경로에서 하드 차단 ──────


@pytest.mark.parametrize(
    "text",
    [
        "지역 최저가로 임플란트를 제공합니다.",
        "업계 최저 비용으로 모십니다.",
        "타 병원보다 30% 저렴한 비용으로 시술합니다.",
        "가장 저렴한 비용으로 받을 수 있습니다.",
        "이번 달 20% 할인 이벤트를 진행합니다.",
        "할인가로 제공합니다.",
        "１０％ 할인",  # 전각 우회
        "개원 이벤트 특가로 모십니다.",
        "이벤트 기간에만 드리는 혜택입니다.",
        "첫 방문 무료 상담을 드립니다.",
        "도수치료 시술 전액 무료입니다.",
        "본인부담금 면제 혜택을 드립니다.",
        "선착순 10명 사은품 증정",
        "체험단을 모집합니다.",
        "페이백 혜택을 드립니다.",
        # 공적 검진 문맥이 가까이 있어도 병원 자기 주장이면 계속 막는다.
        "본원은 국가 검진과 함께 무료 검진을 해 드립니다.",
    ],
)
def test_price_discount_and_inducement_claims_are_blocked(text):
    assert "할인·유인" in check_forbidden(text)


@pytest.mark.parametrize(
    "text",
    [
        "최저혈압이 90 이상이면 진료가 필요합니다.",
        "최저 기온이 영하로 내려가면 혈압이 오릅니다.",
        "심혈관 이벤트가 발생할 위험이 커집니다.",
        "심혈관 이벤트 중 가장 흔한 것은 심근경색입니다.",
        "국가건강검진은 무료로 받을 수 있습니다.",
        "국가에서 시행하는 무료 검진 대상인지 확인하세요.",
        "의료급여 수급자는 본인부담금이 면제되는 경우가 있습니다.",
        "비용은 의료기관마다 다릅니다.",
        "무료 주차가 가능합니다.",
    ],
)
def test_public_programs_and_clinical_terms_are_not_inducement(text):
    assert check_forbidden(text) == []


def test_inducement_label_is_enforced_by_every_publication_field_and_the_markdown_body():
    """생성·발행 게이트·공개 가시성·PATCH가 쓰는 같은 필드 검사기에서 걸린다."""

    fields = {
        "title": "허리 통증 안내",
        "body": "## 안내\n이번 달 **무료 상담**을 드립니다.",
        "meta_description": "허리 통증 안내",
        "faq_question": None,
        "faq_answer_summary": None,
    }
    assert "할인·유인" in check_forbidden_content_fields(fields, FORBIDDEN_CHECK_FIELDS)
    title_only = dict(fields, body="## 안내\n본문", title="개원 이벤트 — 허리 통증 안내")
    assert "할인·유인" in check_forbidden_content_fields(title_only, FORBIDDEN_CHECK_FIELDS)


def test_writer_sees_the_inducement_vocabulary_but_the_essence_safety_text_is_unchanged():
    """작가 프롬프트에는 새 어휘를 보이되, Essence 필수 안전 문구의 원문은 바꾸지 않는다."""

    from app.services.essence_engine import MANDATORY_AVOID_MESSAGES

    vocabulary = forbidden_vocabulary_for_prompt()
    for expression in ("최저가", "무료 상담", "할인 이벤트", "본인부담금 면제", "선착순"):
        assert expression in vocabulary
    assert "최저가" not in FORBIDDEN_EXPRESSIONS
    assert MANDATORY_AVOID_MESSAGES == (
        "의료광고 공통 금지 표현을 홍보 문구로 사용하지 않습니다: " + ", ".join(FORBIDDEN_EXPRESSIONS),
    )
    assert len(FORBIDDEN_EXPRESSIONS) == 21
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_medical_filter.py -q -k "inducement or price_discount or public_programs"` Expected: `test_price_discount_and_inducement_claims_are_blocked` 16건, `..._enforced_by_every_publication_field...`, `test_writer_sees_the_inducement_vocabulary...` FAILED(`assert '할인·유인' in []`). `test_public_programs...`는 main에서도 통과한다(회귀 가드).

- [ ] **Step 3: 최소 구현** — `backend/app/utils/medical_filter.py`

(a) `:71` `"흉터 없는": re.compile(...),` 줄 바로 뒤(사전 닫는 `}` 앞)에 추가:

```python
    # 2026-10-13 대표 결정(D24) — 가격 우위·할인·이벤트·무료 유인(의료법 제56조 제2항 제13호,
    # 제27조 제3항). 생성 단계 전용 가격 검사(`content_engine.UNVERIFIED_PRICE_PATTERNS`)와 달리
    # 발행 게이트·공개 가시성·관리자 PATCH가 모두 이 패턴을 쓴다. 의학 용어와 겹치는 낱말
    # ('심혈관 이벤트가', '최저혈압', '국가 암검진은 무료')은 패턴 모양과 아래 문맥 예외로 비켜 간다.
    "할인·유인": re.compile(
        r"최저가|최저\s*(?:가격|비용|수가)|업계\s*최저|(?:가장|제일)\s*(?:저렴|싸)"
        r"|(?:타|다른)\s*(?:병원|의원|곳)\s*(?:보다|대비)[^.?!\n]{0,12}(?:저렴|싸|낮은\s*(?:가격|비용))"
        r"|\d+(?:\.\d+)?\s*%\s*할인|할인가|할인\s*(?:이벤트|행사|혜택|쿠폰|가격|율|중)"
        r"|특가|반값|파격\s*(?:가|할인)|땡처리|페이백"
        r"|(?:오픈|개원|런칭|시즌|기념|감사|할인|봄맞이|여름맞이|가을맞이|겨울맞이)\s*이벤트"
        r"|이벤트\s*(?:가격|기간|진행\s*중|혜택|참여|신청|특가)"
        r"|무료\s*(?:상담|시술|진료|검사|검진|체험|시술권|이용권|쿠폰|이벤트)"
        r"|(?:상담|시술|진료|검사)\s*(?:비\s*|비용\s*)?(?:전액\s*)?무료"
        r"|본인\s*부담금?\s*(?:면제|할인|감면|대납|환급|0\s*원)"
        r"|사은품|경품|선착순|체험단"
    ),
```

(b) `:94` `"국내 최초", "아시아 최초", "특허 보유",` 줄 뒤(튜플 닫는 `)` 앞)에 추가:

```python
    # 할인·유인(D24). 표시용 FORBIDDEN_EXPRESSIONS에는 넣지 않는다 — 그 목록은 Essence의
    # 필수 안전 문구(`essence_engine.MANDATORY_AVOID_MESSAGES`)에 문자열로 이어 붙어 있어,
    # 바꾸면 기존 초안·승인본이 모두 "필수 안전 규칙 없음"으로 승인·자동 검수에서 막힌다.
    "최저가", "가장 저렴", "% 할인", "할인 이벤트", "특가", "반값", "페이백",
    "개원 이벤트", "무료 상담", "무료 시술", "무료 검사", "본인부담금 면제",
    "사은품", "경품", "선착순", "체험단",
```

(c) `_ContextExemption`(`:121` `followed_by` 필드 뒤)에 필드를, `applies`(`:133` `return True` 앞)에 검사를 추가:

```python
    # 매치 앞 window 자 안에 이 표현이 **있으면** 예외가 아니다(병원 자기 주장 문맥).
    unless_preceded_by: re.Pattern | None = None
```

```python
        if self.unless_preceded_by is not None:
            head = text[max(0, match.start() - self.window) : match.start()]
            if self.unless_preceded_by.search(head):
                return False
```

(d) `:137` `_ALLOWED_CONTEXTS: dict[...] = {` 바로 위에:

```python
# 병원이 스스로를 가리키는 말. 이 말이 가까이 있으면 문맥 예외를 주지 않는다.
_HOSPITAL_SELF = re.compile(r"저희|우리\s*(?:병원|의원)|본원|당원|병원에서|의원에서")
```

(e) `_ALLOWED_CONTEXTS`의 `"통증 없는": (` 항목 바로 앞(`:218` 주석 줄 앞)에:

```python
    # "국가에서 시행하는 무료 검진" — 공적 검진 제도 안내는 병원의 무료 유인이 아니다.
    # 같은 문장이라도 병원이 주는 무료 상담·시술·진료는 계속 막는다.
    "할인·유인": (
        _ContextExemption(
            reason="공적 검진 제도의 무료 안내",
            trigger=re.compile(r"무료\s*검진"),
            preceded_by=re.compile(r"(?:국가|공단|보건소|국민\s*건강\s*보험|일반\s*건강\s*검진)"),
            unless_preceded_by=_HOSPITAL_SELF,
            window=20,
        ),
    ),
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_medical_filter.py tests/test_content_engine_must_use_verbatim.py tests/test_must_use_verbatim.py tests/test_publish_block_auto_resolve.py -q` Expected: 전부 passed(프로토타입, 3-10까지 적용한 최종 상태: test_medical_filter 106 passed). `EMERGENCY_TEMPLATES`가 새 라벨에 걸리지 않음도 `test_every_emergency_template_is_a_fixed_safe_sentence`가 확인한다.

- [ ] **Step 5: 커밋** `git add backend/app/utils/medical_filter.py backend/tests/test_medical_filter.py && git commit -m "feat(medical-filter): 가격 우위·할인·이벤트·무료 유인을 공통 필터의 하드 규칙으로 막는다(D24)"`

**계약 영향:** CLAUDE.md "콘텐츠와 월간 계약"의 의료광고 문단 첫 문장 "의료광고 금지 표현은 `utils/medical_filter.py`를 정본으로 삼는다." 뒤에 추가 — "가격 우위·할인·이벤트·무료 유인(라벨 `할인·유인`, 2026-10-13 대표 결정)도 같은 필터가 막으므로 생성·발행 게이트·공개 가시성·관리자 PATCH가 모두 같은 판정을 쓴다. 공적 검진 제도 안내만 문맥 예외다. 작가 프롬프트용 어휘는 `FORBIDDEN_PATTERN_VARIANTS`에 두고, Essence 필수 안전 문구에 이어 붙는 표시용 `FORBIDDEN_EXPRESSIONS`는 바꾸지 않는다." (3-15에서 일괄 반영)

**위험·롤백:**
- 배포 즉시 새 라벨에 걸리는 **공개 글이 숨겨지고**(fail-closed), 병원 히어로·진료 항목·about 문장이 공개 표면에서 빠지며, 승인 필수 문구가 작가 요구에서 빠진다(승인본당 운영자 기록 1건). Task 3-2 조회를 배포 전에 새 이미지로 돌려 0건이거나 대표가 확인한 목록일 때만 진행한다.
- Essence 자동 검수(`essence_auto_review.py:760`)가 병원 자료에 '무료 상담' 같은 문구가 있으면 보류한다 — 의도된 결과이며 사람이 승인본 문구를 고친다.
- 롤백: 이 커밋 revert(패턴 제거). 숨겨졌던 공개 글은 다음 조회부터 다시 보인다(가시성은 매 조회 판정).

### Task 3-2: 배포 전 영향 조회 — 새 필터가 숨길 공개 글·병원 문구 (D24)

**Files:**
- Create: `backend/app/utils/forbidden_impact_scan.py`
- Test: `backend/tests/test_forbidden_impact_scan.py`(신규)

**조사 결과:**
- 운영 DB 조회는 `reputation-migrate` Job으로 돈다(`app/utils/ops_sql_report.py:1-16` 머리말과 같은 방식). SQL로는 파이썬 정규식·마크다운 렌더 기준 검사를 재현할 수 없으므로 같은 함수(`check_forbidden_content_fields`, `check_forbidden`)를 부르는 읽기 전용 모듈을 둔다. `ops_sql_report.run_report`처럼 첫 문장 `SET TRANSACTION READ ONLY`, 끝에 롤백.
- 공개 글은 행이 아니라 활성 판(`approved_public_view`, `content_visibility.py:142-175`)의 텍스트로 검사한다 — 가시성 판정과 같은 입력이다. `active_revision`은 `ContentItem` 관계(`models/content.py:286`)라 `selectinload`로 읽는다.
- 병원 문구는 공개 API가 거르는 필드(`hero_headline`·`hero_description`·`treatments`, `api/public/site.py:535-543`, `:570-594`)와 승인 운영 기준의 about 문장(`positioning_statement`·`patient_promise`, `:426-440`), 그리고 승인 필수 문구(`must_use_messages`, `must_use_verbatim.py:187-225`)다.
- 로컬 test DB에서 `python -m app.utils.forbidden_impact_scan`이 오류 없이 `{"summary": {}}`를 낸다(쿼리 실행 확인).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_forbidden_impact_scan.py`:

```python
"""배포 전 영향 조회: 새 필터가 숨길 공개 글·병원 문구를 읽기 전용으로 찾는다(2026-10-13 D24)."""

from __future__ import annotations

import io
import json
import uuid
from types import SimpleNamespace

from app.utils import forbidden_impact_scan


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, items, hospitals, philosophies):
        self.batches = [items, hospitals, philosophies]
        self.events: list[str] = []
        self.commits = 0

    def rollback(self):
        self.events.append("ROLLBACK")

    def commit(self):
        self.commits += 1

    def execute(self, statement):
        self.events.append(str(statement).split()[0])
        if str(statement).startswith("SET TRANSACTION"):
            return None
        return _Rows(self.batches.pop(0))


def _published(body: str):
    # active_revision_id가 없는 옛 모양이면 approved_public_view가 행 그대로를 본다.
    return SimpleNamespace(
        id=uuid.uuid4(),
        hospital_id=uuid.uuid4(),
        title="허리 통증 안내",
        body=body,
        meta_description="안내",
        faq_question=None,
        faq_answer_summary=None,
        references_list=[],
    )


def test_scan_reports_only_flagged_public_text_and_never_writes():
    flagged = _published("## 안내\n개원 이벤트로 무료 상담을 드립니다.")
    clean = _published("## 안내\n허리 통증은 원인이 다양합니다.")
    hospital = SimpleNamespace(
        id=uuid.uuid4(),
        hero_headline="허리 통증 전문",
        hero_description="첫 방문 무료 상담",
        treatments=["도수치료", {"name": "체외충격파", "description": "선착순 할인"}],
    )
    philosophy = SimpleNamespace(
        id=uuid.uuid4(), hospital_id=hospital.id,
        positioning_statement="과잉진료를 피합니다.", patient_promise="",
        must_use_messages=["정확한 진단은 전문의 진료로 확인합니다.", "첫 진료 무료 상담을 드립니다."],
    )
    db = _FakeSession([flagged, clean], [hospital], [philosophy])
    out = io.StringIO()

    labels = forbidden_impact_scan.scan(db, out=out)

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line.get("kind") for line in lines[:-1]] == [
        "content", "hospital", "hospital", "philosophy"
    ]
    assert lines[0]["id"] == str(flagged.id)
    assert [line.get("field") for line in lines[1:4]] == [
        "hero_description", "treatments[1]", "must_use_messages[1]"
    ]
    assert lines[-1] == {"summary": {"할인·유인": 4}}
    assert labels["할인·유인"] == 4
    assert db.events[:2] == ["ROLLBACK", "SET"]
    assert db.events[-1] == "ROLLBACK"
    assert db.commits == 0
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_forbidden_impact_scan.py -q` Expected: 수집 ERROR `ImportError: cannot import name 'forbidden_impact_scan' from 'app.utils'`.

- [ ] **Step 3: 최소 구현** — `backend/app/utils/forbidden_impact_scan.py`:

```python
"""읽기 전용 운영 조회: 지금 공개 중인 글·병원 공개 문구 가운데 금지 표현 필터에 걸리는 것.

필터(`utils/medical_filter.py`)는 공개 가시성(`content_visibility`)과 공개 병원 문구
(`api/public/site.py`)에도 쓰인다. 패턴을 늘리는 배포는 **배포 즉시** 걸리는 공개 글을 숨긴다.
새 이미지로 이 조회를 먼저 돌려, 숨겨질 글·문구를 배포 전에 본다::

    gcloud run jobs execute reputation-migrate --wait \
      --args=python,-m,app.utils.forbidden_impact_scan

출력: 걸린 항목마다 `{"kind": ..., "id": ..., "labels": [...]}` 한 줄, 끝에 `{"summary": ...}` 한 줄.
쓰기는 하지 않는다 — 첫 문장이 `SET TRANSACTION READ ONLY`이고 끝나면 롤백한다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any, TextIO

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.content import ContentItem, ContentStatus
from app.models.essence import HospitalContentPhilosophy, PhilosophyStatus
from app.models.hospital import Hospital
from app.services.content_publication import PUBLICATION_CHECK_FIELDS, publication_field_values
from app.services.content_visibility import approved_public_view
from app.utils.medical_filter import check_forbidden, check_forbidden_content_fields


def _emit(out: TextIO, payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), file=out, flush=True)


def _treatment_text(entry: object) -> str:
    if isinstance(entry, dict):
        return " ".join(str(value) for value in entry.values())
    return str(entry or "")


def scan(db: Any, *, out: TextIO = sys.stdout) -> Counter:
    """공개 중인 글과 병원 공개 문구를 검사한다. 라벨별 건수를 돌려준다."""

    labels_seen: Counter = Counter()
    db.rollback()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        items = db.execute(
            select(ContentItem)
            .where(ContentItem.status == ContentStatus.PUBLISHED)
            .options(selectinload(ContentItem.active_revision))
        ).scalars()
        for item in items:
            view = approved_public_view(item)
            if view is None:
                continue
            labels = check_forbidden_content_fields(
                publication_field_values(view), PUBLICATION_CHECK_FIELDS
            )
            if labels:
                labels_seen.update(labels)
                _emit(out, {"kind": "content", "id": item.id, "hospital_id": item.hospital_id,
                            "labels": labels})
        for hospital in db.execute(select(Hospital)).scalars():
            fields = {
                "hero_headline": hospital.hero_headline or "",
                "hero_description": hospital.hero_description or "",
                **{
                    f"treatments[{index}]": _treatment_text(entry)
                    for index, entry in enumerate(hospital.treatments or [])
                },
            }
            for field, value in fields.items():
                labels = check_forbidden(value)
                if labels:
                    labels_seen.update(labels)
                    _emit(out, {"kind": "hospital", "id": hospital.id, "field": field,
                                "labels": labels})
        philosophies = db.execute(
            select(HospitalContentPhilosophy).where(
                HospitalContentPhilosophy.status == PhilosophyStatus.APPROVED
            )
        ).scalars()
        for philosophy in philosophies:
            # 공개 about 문장(api/public/site.py)과, 걸리면 작가 요구에서 빠지고 운영자 기록이 남는
            # 필수 문구(`must_use_verbatim`).
            fields = {
                "positioning_statement": philosophy.positioning_statement or "",
                "patient_promise": philosophy.patient_promise or "",
                **{
                    f"must_use_messages[{index}]": str(message or "")
                    for index, message in enumerate(philosophy.must_use_messages or [])
                },
            }
            for field, value in fields.items():
                labels = check_forbidden(value)
                if labels:
                    labels_seen.update(labels)
                    _emit(out, {"kind": "philosophy", "id": philosophy.id,
                                "hospital_id": philosophy.hospital_id, "field": field,
                                "labels": labels})
        _emit(out, {"summary": dict(labels_seen)})
    finally:
        db.rollback()
    return labels_seen


def main() -> int:
    from app.core.database import SyncSessionLocal

    with SyncSessionLocal() as db:
        scan(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_forbidden_impact_scan.py -q` Expected: `1 passed`. 그리고 실제 스키마에서 한 번: `.venv/bin/python -m app.utils.forbidden_impact_scan` Expected: 마지막 줄 `{"summary": {...}}`(로컬 test DB는 `{"summary": {}}`).

- [ ] **Step 5: 커밋** `git add backend/app/utils/forbidden_impact_scan.py backend/tests/test_forbidden_impact_scan.py && git commit -m "feat(ops): 새 금지 표현이 숨길 공개 글·병원 문구를 배포 전에 읽기 전용으로 찾는다"`

**계약 영향:** 없음(운영 도구). 배포 절차 문서(`docs/ops/deployment-runbook.md`)의 "필터를 바꾸는 배포" 항목에 이 조회를 추가하는 한 줄을 3-15에서 넣는다.

**위험·롤백:** 읽기 전용(READ ONLY 트랜잭션·롤백). 운영 데이터 변경 없음. 공개 글 수(수백 건)를 메모리에 한 번씩 읽는다 — migrate Job 메모리로 충분하다. 롤백은 파일 삭제.

### Task 3-3: HARD와 함께 나온 인용 UNCERTAIN을 발행 전 교정·삭제형 재작성에 함께 싣는다 (E1)

**Files:**
- Modify: `backend/app/workers/tasks.py:1065`(교정 계획), `:1088-1096`(`run_minimal_correction` 호출)
- Modify: `backend/app/services/content_review_feedback.py:37-52`(`hard_removal_findings`)
- Test: `backend/tests/test_publish_block_auto_resolve.py`(끝에 2개), `backend/tests/test_content_ai_review_must_use.py`(끝에 2개)

**조사 결과:**
- 발행 전 교정은 HARD만 고친다: `tasks.py:1065` `plan_corrections(candidate, review, must_use_messages=must_use)`와 `:1083-1097` `run_minimal_correction(...)` 모두 `include_uncertain`을 넘기지 않아 기본값 False(`content_minimal_correction.py:429`, `:1065`). 공개 모드는 이미 True다(`:1226-1236`, `:1189`).
- `plan_corrections`는 `include_uncertain=False`일 때 인용 있는 UNCERTAIN을 `uncorrectable`로만 남기고 `hard_left`는 올리지 않는다(`:518-520`) — 그래서 HARD는 고쳐지고 UNCERTAIN은 그대로 남아 교정본 재검수가 다시 막는다. 운영 차단 2편(10/7·10/9)이 정확히 이 모양이다(`gate-stats.md` Q3: 둘 다 HARD MEDICAL_SAFETY + UNCERTAIN HOSPITAL_FACT).
- 삭제형 재작성 지시 `hard_removal_findings`(`content_review_feedback.py:43-48`)도 HARD만 싣는다. 이 함수는 생성 세션 안(`content_generation_review.py:192`)에서만 쓰인다.
- **범위 결정(대표 결정 문구 "co-occurring"을 따른다):** `include_uncertain`은 저장된 판정에 **모델이 단정한 HARD가 있을 때만** 켠다(`_stored_model_declared_hard`, `tasks.py:752-755`). UNCERTAIN만 남은 차단은 지금처럼 상위 모델 재검수(`tasks.py:6927-6940`)와 표본 사다리가 맡는다 — 무조건 켜면 그 행들이 재검수 전에 문장 삭제로 가로채진다(행동 변화, 열린 질문 Q3).
- 재작성 지시에 싣는 UNCERTAIN은 **인용(quote)이 있는 사실·안전 지적**만이다. 확신도 부족으로 붙은 합성 UNCERTAIN(`content_ai_review.py:43-48`, 인용 없음)은 고칠 문장이 없어 싣지 않는다.
- 교정본은 여전히 그 hash에 묶인 재검수 PASS가 있어야만 통과한다(`content_publication.py:155-170`). 의료·사실 차단을 완화하는 변경이 아니다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_publish_block_auto_resolve.py` 끝에(파일의 `worker` 픽스처·`_blocked_item`·`_install`·`_DB`·`_review_payload`를 그대로 쓴다):

```python
# ── E1(2026-10-13): HARD와 함께 나온 인용 UNCERTAIN도 같은 교정 패스가 고친다 ─────────────


def test_quoted_uncertain_next_to_a_hard_is_corrected_in_the_same_pass(worker, monkeypatch):
    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING, UNAPPROVED_PHILOSOPHY)
    hard = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    uncertain = _finding(
        "UNCERTAIN", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙입니다.", UNAPPROVED_PHILOSOPHY
    )
    item = _blocked_item(philosophy, body, hard)
    item.essence_check_summary["ai_review"] = _review_payload(
        tasks._stored_candidate(item), hard, uncertain
    )
    calls["item"] = item
    seen_contexts = []

    async def propose(*, targets, **_kwargs):
        return {target.key: ("DELETE", "") for target in targets}

    async def review(**kwargs):
        seen_contexts.append(kwargs.get("correction_context"))
        return _review(kwargs["content"])

    _install(monkeypatch, calls, propose=propose, review=review)

    state, code, _message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.SUCCEEDED, None)
    assert WRONG_TRAINING not in item.body and UNAPPROVED_PHILOSOPHY not in item.body
    assert calls["llm"] == 1 and len(calls["reviews"]) == 1, "한 패스·한 재검수로 끝난다"
    [context] = seen_contexts
    assert {entry["before"] for entry in context["addressed"]} == {
        WRONG_TRAINING,
        UNAPPROVED_PHILOSOPHY,
    }


def test_uncertain_only_block_is_left_to_the_rereview_ladder(worker, monkeypatch):
    """모델이 HARD로 단정하지 않은 차단은 이 패스가 맡지 않는다(종전 경로 그대로)."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, UNAPPROVED_PHILOSOPHY)
    uncertain = _finding(
        "UNCERTAIN", "HOSPITAL_FACT", "승인 자료에 없는 진료 원칙입니다.", UNAPPROVED_PHILOSOPHY
    )
    item = _blocked_item(philosophy, body, uncertain)
    plan = mc.plan_corrections(
        tasks._stored_candidate(item),
        tasks._stored_ai_review(item),
        include_uncertain=tasks._stored_model_declared_hard(item),
    )
    assert not plan.applicable
```

(첫 테스트의 `correction_context` 단언은 Task 3-4가 채운다. 3-3만 적용한 상태에서는 그 두 줄 — `[context] = seen_contexts`와 그 아래 `assert` — 을 잠시 빼고 돌린 뒤 3-4에서 되살린다. 한 PR로 낼 것이므로 3-3·3-4를 연달아 적용해도 된다.)

`backend/tests/test_content_ai_review_must_use.py` 끝에:

```python
# ── E1(2026-10-13): HARD와 함께 나온 인용 UNCERTAIN도 삭제형 재작성 지시에 싣는다 ───────────


def _typed(severity: str, kind: str, message: str, quote: str = ""):
    return content_ai_review.ContentAiFinding(
        severity=content_ai_review.ContentAiFindingSeverity(severity),
        kind=content_ai_review.ContentAiFindingKind(kind),
        message=message,
        quote=quote,
    )


def _blocked(*findings):
    return SimpleNamespace(blocking_findings=tuple(findings))


def test_quoted_uncertain_co_occurring_with_hard_joins_the_removal_rewrite() -> None:
    hard = _typed("HARD", "MEDICAL_SAFETY", "응급 안내 없이 출혈을 다뤘습니다.", "피가 나면 지켜보세요.")
    uncertain = _typed(
        "UNCERTAIN", "HOSPITAL_FACT", "승인 자료에 없는 진료 철학입니다.", "내 가족처럼 진료합니다."
    )
    synthetic = _typed("UNCERTAIN", "MEDICAL_SAFETY", "독립 AI 검수의 확신이 충분하지 않아 자동 재검수가 필요합니다.")

    removal = hard_removal_findings(_blocked(hard, uncertain, synthetic))

    assert removal[1:] == [hard.message, uncertain.message]


def test_uncertain_alone_does_not_buy_a_removal_rewrite() -> None:
    uncertain = _typed(
        "UNCERTAIN", "HOSPITAL_FACT", "승인 자료에 없는 진료 철학입니다.", "내 가족처럼 진료합니다."
    )
    assert hard_removal_findings(_blocked(uncertain)) == []
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_publish_block_auto_resolve.py tests/test_content_ai_review_must_use.py -q -k "co_occurring or next_to_a_hard or uncertain_alone or uncertain_only_block"` Expected: `test_quoted_uncertain_next_to_a_hard_is_corrected_in_the_same_pass`가 `assert ... == (SUCCEEDED, None)` 실패(UNCERTAIN이 남아 재검수 뒤에도 차단 — 프로토타입에서는 두 번째 패스로 가거나 FAILED), `test_quoted_uncertain_co_occurring_with_hard_joins_the_removal_rewrite`가 `assert [hard.message] == [hard.message, uncertain.message]` 실패. 나머지 둘은 main에서도 통과(가드).

- [ ] **Step 3: 최소 구현**

`tasks.py:1065`의 `    plan = plan_corrections(candidate, review, must_use_messages=must_use)`를 다음으로 바꾼다:

```python
    # 모델이 HARD로 단정한 판정에 함께 나온 인용 UNCERTAIN도 같은 패스가 고친다 — HARD만 고치면
    # 남은 UNCERTAIN이 교정본의 재검수를 다시 막는다. UNCERTAIN만 남은 차단은 종전대로 재검수·
    # 표본 사다리가 맡는다(공개 모드는 이미 늘 켠다).
    include_uncertain = _stored_model_declared_hard(item)
    plan = plan_corrections(
        candidate, review, must_use_messages=must_use, include_uncertain=include_uncertain
    )
```

`tasks.py:1092-1096`의 `run_minimal_correction(...)` 호출 인자 끝(`dependencies=CorrectionDependencies(...),` 다음 줄)에 `include_uncertain=include_uncertain,`을 추가한다.

`content_review_feedback.py:37-52`의 `hard_removal_findings` 전체를 다음으로 바꾼다:

```python
def hard_removal_findings(review: Any) -> list[str]:
    """Turn model-declared fact/safety HARD findings into one removal instruction.

    HARD 사실·안전 지적이 있을 때만 만든다. 그때 같은 판정에 함께 나온 UNCERTAIN 사실·안전
    지적도 **문장을 인용했으면** 같은 지시에 싣는다 — 빼면 재작성이 HARD만 고치고, 그대로 남은
    UNCERTAIN이 다음 검수에서 다시 발행을 막는다(2026-10 운영 차단 2편). 인용 없는 UNCERTAIN
    (확신도 부족으로 붙은 합성 지적 포함)은 고칠 문장이 없어 싣지 않는다.
    """

    def _label(value: object) -> str:
        return str(getattr(value, "value", value) or "").upper()

    def _fact_or_safety(finding: Any) -> bool:
        return _label(getattr(finding, "kind", None)) in _HARD_REMOVAL_KINDS

    blocking = tuple(getattr(review, "blocking_findings", ()) or ())
    hard = [
        finding
        for finding in blocking
        if _label(getattr(finding, "severity", None)) == "HARD" and _fact_or_safety(finding)
    ]
    if not hard:
        return []
    co_occurring = [
        finding
        for finding in blocking
        if _label(getattr(finding, "severity", None)) == "UNCERTAIN"
        and _fact_or_safety(finding)
        and str(getattr(finding, "quote", "") or "").strip()
    ]
    messages = [str(getattr(finding, "message", "")).strip() for finding in (*hard, *co_occurring)]
    messages = [message for message in messages if message]
    if not messages:
        return []
    return [_HARD_REMOVAL_INSTRUCTION, *messages]
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_publish_block_auto_resolve.py tests/test_content_ai_review_must_use.py tests/test_published_auto_correction.py tests/test_content_engine.py -q` Expected: 전부 passed(프로토타입 최종 상태: publish_block 78 + must_use 22 passed). 기존 `test_must_use_finding_is_recorded_but_not_sent_to_the_writer_when_another_hard_blocks`가 필수 문구 지적이 여전히 빠지는 것을 확인한다(`blocking_findings`는 필수 문구 대상 지적을 담지 않는다).

- [ ] **Step 5: 커밋** `git add backend/app/workers/tasks.py backend/app/services/content_review_feedback.py backend/tests/test_publish_block_auto_resolve.py backend/tests/test_content_ai_review_must_use.py && git commit -m "fix(content-review): HARD와 함께 나온 인용 UNCERTAIN도 발행 전 교정·삭제형 재작성이 함께 고친다"`

**계약 영향:** CLAUDE.md 최소 교정 문장 "모델이 HARD로 단정한 사실·안전 지적 중 인용(`quote`)으로 문장을 짚은 것과 응급 안내 누락(HARD/UNCERTAIN)은 사람의 PATCH를 기다리지 않고 최소 교정 패스(…)가 푼다" 뒤에 "— 같은 판정에 함께 나온 인용 UNCERTAIN 사실·안전 지적도 같은 패스가 고친다(UNCERTAIN만 남은 차단은 종전대로 상위 모델 재검수가 맡는다)"를 더한다. "HARD 사실·안전 지적은 삭제형 재작성 1회 뒤 재검수를 거친다"에 "(함께 나온 인용 UNCERTAIN 포함)"을 더한다.

**위험·롤백:** 교정이 지우는 문장이 늘어 본문이 짧아질 수 있다 — 기존 분량 하한 거절(`content_minimal_correction.py:836-842`)이 그대로 막는다. 교정 제안 호출 1회에 지적 문장이 1~2개 더 실릴 뿐 호출 수는 늘지 않는다. 롤백: 커밋 revert.

### Task 3-4: 교정본 재검수에 "직전 지적과 교정 전·후 문장"을 싣는다 (E1)

**Files:**
- Modify: `backend/app/services/content_ai_review.py:14`(import), `:94` 앞(시스템 프롬프트 단락), `:616-675`(`content_review_input_payload`), `:993-1046`(`review_generated_content`)
- Modify: `backend/app/services/content_minimal_correction.py:1074-1077`(누적 목록), `:1123-1130`(재검수 호출)
- Test: `backend/tests/test_content_ai_review.py`(끝에 1개), Task 3-3의 `test_quoted_uncertain_next_to_a_hard_is_corrected_in_the_same_pass`(맥락 단언)

**조사 결과:**
- 교정 루프는 패스마다 `dependencies.review(hospital, philosophy, content=corrected, content_brief)`만 부른다(`content_minimal_correction.py:1125-1130`). 재검수는 직전에 무엇을 지적했고 무엇을 고쳤는지 모른 채 새 표본으로 글 전체를 다시 본다 — 운영 교정 기록에서 1차가 지운 뒤 2차가 **다른 문장**을 새로 지적하는 '두더지 잡기'가 확인됐다(`gate-strictness-review.md` §2, `q7_autocorr_sample`).
- 재검수 의존성은 발행 전 `review_generated_content`(`tasks.py:1094`), 공개 모드 `post_publish_review(**kwargs)`(`post_publish_ai_review.py:453-454`) 둘 다 `**kwargs`로 넘기므로 키워드 인자 하나를 더해도 호출부 수정이 없다. 테스트 더블(`test_publish_block_auto_resolve.py`의 `reviewer(**kwargs)`)도 `**kwargs`다.
- 검수 입력의 `candidate_sha256`은 후보 필드만으로 계산한다(`content_review_input_payload`의 `candidate_sha256(candidate)`) — 맥락을 실어도 hash 결합 PASS 규칙은 그대로다. 맥락은 교정 재검수에서만 싣고, 없으면 입력 바이트가 종전과 같다(백필 재시도 지문 보존, `content_ai_review.py:676-679` 주석).
- **판정 기준은 바꾸지 않는다.** 시스템 프롬프트에 "교정한 문장과 그 주변에 같은 문제가 남았는지 확인하고, 교정하지 않은 문장은 직전 검수가 같은 기준으로 이미 본 문장이니 그 문장 자체의 사실·안전 문제를 구체적으로 짚을 때만 지적하라"는 **일관성 지시**만 더한다. HARD·UNCERTAIN 차단 계약(`blocks_publication`, `:211-217`)과 hash 결합 PASS는 손대지 않는다. "삭제만 한 교정 뒤의 새 지적은 비차단"(검토 A-3)은 대표가 고르지 않았으므로 하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_content_ai_review.py` 끝에:

```python
# ── E1(2026-10-13): 최소 교정의 재검수는 직전 지적과 교정 전·후 문장을 함께 본다 ──────────


def test_correction_context_is_carried_only_when_given() -> None:
    kwargs = dict(
        hospital=SimpleNamespace(name="병원"),
        philosophy=SimpleNamespace(),
        content={"title": "안내", "body": "본문"},
        content_brief=None,
    )
    plain = content_ai_review.content_review_input_payload(**kwargs)
    assert "correction_context" not in plain  # 교정 밖의 검수 입력 바이트는 종전과 같다

    addressed = [
        {"finding": f"지적 {index}", "before": "가" * 400, "after": ""} for index in range(12)
    ]
    data = content_ai_review.content_review_input_payload(
        **kwargs, correction_context={"addressed": addressed}
    )
    context = data["correction_context"]["addressed"]
    assert len(context) == 10 and context[-1]["finding"] == "지적 11"
    assert len(context[0]["before"]) == 300
    assert data["candidate_sha256"] == plain["candidate_sha256"]
    assert "correction_context" in content_ai_review._SYSTEM_PROMPT
    assert "판정을 바꿀 근거가 없으면 다시 지적하지 마세요" in content_ai_review._SYSTEM_PROMPT
```

Task 3-3에서 잠시 뺀 두 줄(`[context] = seen_contexts` 이하)을 되살린다.

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_content_ai_review.py::test_correction_context_is_carried_only_when_given tests/test_publish_block_auto_resolve.py::test_quoted_uncertain_next_to_a_hard_is_corrected_in_the_same_pass -q` Expected: 첫째 `TypeError: content_review_input_payload() got an unexpected keyword argument 'correction_context'`, 둘째 `[context] = seen_contexts`에서 `context`가 `None`이라 `TypeError: 'NoneType' object is not subscriptable`.

- [ ] **Step 3: 최소 구현**

`content_ai_review.py:14` `from collections.abc import Callable` → `from collections.abc import Callable, Mapping`.

`content_ai_review.py:94` "각 finding은 후보의 문장 하나만 다루고, …" 줄 **바로 위**에 단락을 넣는다:

```text
DATA_BLOCK에 correction_context가 있으면 이 후보는 직전 검수의 지적을 문장 단위로 교정한
결과입니다. addressed의 각 항목은 직전 지적(finding)과 그 문장의 교정 전(before)·후(after)
입니다(after가 빈 문자열이면 그 문장을 지웠습니다). 판정 기준은 위와 같습니다. 교정한 문장과
그 주변에 같은 문제가 남았는지 확인하세요. 교정하지 않은 문장은 직전 검수가 같은 기준으로
이미 본 문장입니다 — 그 문장 자체에서 사실·안전 문제를 구체적으로 짚을 수 있을 때만
지적하고, 같은 기준에서 판정을 바꿀 근거가 없으면 다시 지적하지 마세요.

```

`content_ai_review.py:616` `def content_review_input_payload(` 바로 위에:

```python
# 재검수에 싣는 직전 교정 기록의 상한. 교정은 글(주제)당 패스 2회·패스당 지적 5개 안이다.
_CORRECTION_CONTEXT_LIMIT = 10


def _bounded_correction_context(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    addressed = [
        {
            "finding": _bounded_text(entry.get("finding"), 300),
            "before": _bounded_text(entry.get("before"), 300),
            "after": _bounded_text(entry.get("after"), 300),
        }
        for entry in (value.get("addressed") or [])
        if isinstance(entry, Mapping)
    ][-_CORRECTION_CONTEXT_LIMIT:]
    return {"addressed": addressed} if addressed else None
```

`content_review_input_payload` 시그니처 끝(`content_brief: dict[str, Any] | None,` 다음)에 `correction_context: Mapping[str, Any] | None = None,`을 더하고, 본문 끝 `return {` (`:650`)을 `payload: dict[str, Any] = {`로 바꾼 뒤 사전을 닫는 `}`(`:669`) 다음에:

```python
    # 최소 교정의 재검수에서만 싣는다. 없으면 입력 바이트가 종전과 같다(백필 재시도 지문 보존).
    bounded_context = _bounded_correction_context(correction_context)
    if bounded_context is not None:
        payload["correction_context"] = bounded_context
    return payload
```

`review_generated_content` 시그니처(`:1005` `transport_observer: …,` 다음)에 `correction_context: Mapping[str, Any] | None = None,`을, docstring 끝에 "`correction_context`는 최소 교정 패스의 재검수만 넘긴다(직전 지적과 교정 전·후 문장)."를 더하고, `:1036-1041`의 `content_review_input_payload(...)` 호출 인자에 `correction_context=correction_context,`를 더한다.

`content_minimal_correction.py:1077` `history: list[dict[str, Any]] = []` 다음 줄에:

```python
    # 재검수에 넘기는 "직전 지적과 그 교정" — 새 표본이 교정하지 않은 문장을 같은 기준으로
    # 다시 뒤져 매 회차 다른 문장을 지적하는 일(2026-10 운영 '두더지 잡기')을 줄인다.
    addressed: list[dict[str, str]] = []
```

`:1123-1130`의 `passes += 1` / `rereviews += 1` / `last_review = await dependencies.review(...)`를 다음으로 바꾼다:

```python
        passes += 1
        rereviews += 1
        addressed.extend(
            {
                "finding": " / ".join(target.messages),
                "before": target.sentence,
                "after": decisions[target.key].replacement or "",
            }
            for target in plan.targets
        )
        addressed.extend(
            {"finding": "응급 안내 누락", "before": "", "after": insertion.text}
            for insertion in plan.insertions
        )
        last_review = await dependencies.review(
            hospital=hospital,
            philosophy=philosophy,
            content=corrected,
            content_brief=content_brief,
            correction_context={"addressed": list(addressed)},
        )
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_content_ai_review.py tests/test_publish_block_auto_resolve.py tests/test_published_auto_correction.py tests/test_published_auto_correction_sweep.py -q` Expected: 전부 passed.

- [ ] **Step 5: 커밋** `git add backend/app/services/content_ai_review.py backend/app/services/content_minimal_correction.py backend/tests/test_content_ai_review.py backend/tests/test_publish_block_auto_resolve.py && git commit -m "fix(content-review): 교정본 재검수에 직전 지적과 교정 전·후 문장을 함께 넘긴다"`

**계약 영향:** CLAUDE.md 최소 교정 문장 "교정본은 반드시 독립 재검수를 받고, 게이트는 … 교정본 hash에 묶인 PASS 없이는 통과시키지 않는다" 뒤에 "재검수에는 직전 지적과 교정 전·후 문장(`correction_context`)을 함께 넘겨 같은 기준으로 판정하게 한다 — 차단 기준·hash 결합은 그대로다"를 더한다.

**위험·롤백:** 재검수가 직전 판정에 기대어 실제 위험을 놓칠 위험(앵커링). 지시는 "구체적으로 짚을 수 있는 사실·안전 문제는 지적하라"를 유지하고, 공개 뒤 사후 검수(03:10)가 같은 글을 다시 본다. 입력 토큰이 항목당 최대 900자 늘어난다(교정 재검수에서만, 글당 최대 2회). 롤백: revert(프롬프트·인자 제거).

### Task 3-5: 아무것도 맡지 못한 모델 단정 HARD 차단은 입력이 바뀔 때까지 다시 집지 않는다 (E1)

**Files:**
- Modify: `backend/app/workers/generation_retry_policy.py:90` 뒤(상수), `:699-702`(`attempt_is_terminal`)
- Modify: `backend/app/workers/tasks.py:527`(import), `:1239-1268`(`_terminal_attempt_is_unchanged`), `:5695`(워커 호출), `:7021-7036`(교정 실행 여부), `:7069-7080`(fail-closed 분기)
- Test: `backend/tests/test_terminal_slot_reclaim.py`(끝에 2개), `backend/tests/test_publish_block_auto_resolve.py`(끝에 1개)

**조사 결과(§2-1 참조 — 감사 가정 대부분은 이미 해결):**
- 남은 공회전: 저장 판정이 모델 단정 HARD(`retry_class_for(..., model_declared_hard=True)` → `INPUT_CHANGE_REQUIRED`, `generation_retry_policy.py:268-270`)인데 `_auto_correct_blocked_body`가 `None`을 돌려주는 행 — 사람이 편집한 DRAFT(`correction_allowed_for`가 거짓, `content_minimal_correction.py:366-376`)가 대표적이다. 이 행은 `attempt_is_terminal`에서 빠져 있어(`:699-702`) 매 스윕(하루 7회) 로더가 집고, 워커는 돈을 쓰지 않고 `tasks.py:7074-7080`의 fail-closed `FAILED`를 돌려준다(OperationRun·사고 touch만 남는다).
- 고치는 방법: 그 분기에서 **이번 실행이 교정을 돌리지 않았을 때만** 시도 기록에 `hard_finding_idle=True`를 남기고(예산 계수는 올리지 않는다, `count_attempt=False`), `attempt_is_terminal`이 그 표시를 종착으로 읽는다. 다시 집는 조건은 기존 종착과 같다 — 지문(`context`: 승인 운영 기준·유형·질문·게이트 카탈로그), 판정 뒤 사람 편집. 여기에 **승인된 병원 사실 지문 변화**(`_approved_facts_changed_since_block`, `tasks.py:778-792`)를 더한다: 사실 HARD는 사람이 프로필을 채워야 풀리기 때문이다. 로더(`_generation_retry_is_eligible`)는 병원을 넘기지 않으므로 `item.hospital` 지연 로드로 지문을 읽는다(`_hospital_review_facts`, `:773-775`) — 표시가 있는 행에서만 일어난다.
- 교정이 이번 실행에 돌았으면(`corrected is not None`) 표시하지 않는다 — 다음 스윕이 교정 상한 안에서 다시 고칠 수 있다.
- **배포 전 측정으로 필요 여부를 확인한다**(Step 0). 10/10 이후 이 모양의 FAILED가 하루 1건 미만이면 이 태스크는 건너뛴다(열린 질문 Q6).

- [ ] **Step 0: 측정(운영, 읽기 전용)** — 10/10 이후 돈을 쓰지 않은 HARD 실패 실행 수:

```sql
SELECT date_trunc('day', r.requested_at AT TIME ZONE 'Asia/Seoul') AS day, count(*) AS runs,
       count(DISTINCT r.request_payload->>'source_id') AS items
FROM operation_runs r
WHERE r.operation_type = 'GENERATE_CONTENT_ITEM' AND r.state = 'FAILED'
  AND r.safe_error_code = 'CONTENT_AI_HARD_FINDING'
  AND r.requested_at >= '2026-10-10'
GROUP BY 1 ORDER BY 1;
```

(글 id는 `request_payload->>'source_id'`다 — `operation_run_payloads.build_request_payload`, `generation_run_control.create_dispatched_item_run`. 이 SQL은 `ops_sql_report`에 넣지 않고 한 번만 본다. 같은 날 같은 글이 여러 번이면 교정 패스가 돈 실행과 공회전이 섞여 있으니, 의심되는 글 몇 개의 `essence_check_summary->'auto_correction'->'history'` 길이가 늘지 않았는지로 공회전을 가른다.)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_terminal_slot_reclaim.py` 끝에:

```python
# ── E1(2026-10-13): 아무것도 맡지 못한 HARD 차단은 입력이 바뀔 때까지 다시 집지 않는다 ─────


_IDLE_HARD = {
    "reason": "CONTENT_AI_HARD_FINDING",
    "retry_class": "INPUT_CHANGE_REQUIRED",
    "hard_finding_idle": True,
    "approved_facts": "facts-v1",
}


def test_idle_model_declared_hard_is_terminal_until_its_input_changes(monkeypatch):
    philosophy = _philosophy()
    item = _item(_IDLE_HARD, philosophy=philosophy)
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v1")

    assert attempt_is_terminal(_IDLE_HARD) is True
    assert _eligible(monkeypatch, item, philosophy) is False
    # 사람이 승인 자료(병원 사실)를 채우면 다시 집는다.
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v2")
    assert _eligible(monkeypatch, item, philosophy) is True
    # 운영 기준이 새로 승인돼도 다시 집는다.
    monkeypatch.setattr(tasks, "_hospital_review_facts", lambda *_a: "facts-v1")
    assert _eligible(monkeypatch, item, _philosophy("p2")) is True


def test_hard_without_the_idle_mark_is_still_picked_for_correction(monkeypatch):
    philosophy = _philosophy()
    attempt = {k: v for k, v in _IDLE_HARD.items() if k != "hard_finding_idle"}
    assert attempt_is_terminal(attempt) is False
    assert _eligible(monkeypatch, _item(attempt, philosophy=philosophy), philosophy) is True
```

`backend/tests/test_publish_block_auto_resolve.py` 끝에:

```python
def test_human_edited_hard_block_is_marked_idle_and_skipped_next_sweep(worker, monkeypatch):
    """교정할 수 없는(사람이 편집한) HARD 차단은 돈을 쓰지 않고 종착 표시만 남긴다."""

    philosophy, calls = worker
    body = _body(NEUTRAL_A, WRONG_TRAINING)
    finding = _finding("HARD", "HOSPITAL_FACT", "수련기관이 프로필과 다릅니다.", WRONG_TRAINING)
    item = _blocked_item(philosophy, body, finding)
    item.human_edited_at = datetime.now(UTC) - timedelta(days=1)
    calls["item"] = item
    _install(monkeypatch, calls, propose=_no_provider, review=_no_provider)

    state, code, _message = tasks._generate_single_content_item(
        _DB(), item, _hospital(id=item.hospital_id)
    )

    assert (state, code) == (tasks.GenerationItemState.FAILED, "CONTENT_AI_HARD_FINDING")
    attempt = tasks._stored_generation_attempt(item)
    assert attempt["hard_finding_idle"] is True
    assert attempt["retry_class"] == GenerationRetryClass.INPUT_CHANGE_REQUIRED.value
    assert tasks._terminal_attempt_is_unchanged(item, philosophy) is True
    assert calls["llm"] == 0 and calls["reviews"] == [] and calls["writes"] == []
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_terminal_slot_reclaim.py tests/test_publish_block_auto_resolve.py -q -k "idle"` Expected: `test_idle_model_declared_hard_is_terminal_until_its_input_changes`가 `assert False is True`(attempt_is_terminal), `test_human_edited_hard_block_is_marked_idle...`가 `KeyError: 'hard_finding_idle'`로 실패. `test_hard_without_the_idle_mark...`는 main에서도 통과(가드).

- [ ] **Step 3: 최소 구현**

`generation_retry_policy.py:90` `AUTO_CORRECTION_EXHAUSTED_KEY = "auto_correction_exhausted"` 다음에:

```python

# 모델이 HARD로 단정한 차단(`INPUT_CHANGE_REQUIRED`)인데 이번 실행에서 교정·재작성 어느 것도
# 맡지 못해 돈을 쓰지 않고 끝난 표시. 같은 입력으로 다시 집어도 같은 결과뿐이라 종착으로
# 본다 — 입력(지문)·사람 편집·승인된 병원 사실이 바뀌면 호출부가 다시 집는다.
HARD_FINDING_IDLE_KEY = "hard_finding_idle"
```

`attempt_is_terminal` 끝(`:699-702`)의 `return (retry_class == …INPUT_CHANGE_REQUIRED.value and reason == "MISSING_APPROVED_ESSENCE")`를:

```python
    if retry_class != GenerationRetryClass.INPUT_CHANGE_REQUIRED.value:
        return False
    return reason == "MISSING_APPROVED_ESSENCE" or (
        reason == "CONTENT_AI_HARD_FINDING" and bool(attempt.get(HARD_FINDING_IDLE_KEY))
    )
```

docstring의 "`INPUT_CHANGE_REQUIRED`인 `CONTENT_AI_HARD_FINDING`은 일부러 뺀다 — 최소 교정 패스가 아직 그 지적을 풀 수 있는 단계이고," 두 줄을 "`INPUT_CHANGE_REQUIRED`인 `CONTENT_AI_HARD_FINDING`은 뺀다 — 최소 교정 패스가 아직 그 지적을 풀 수 있는 단계다. 단 직전 실행이 아무것도 맡지 못하고 끝났다는 표시(`HARD_FINDING_IDLE_KEY`)가 있으면 종착이다. 그 밖에"로 바꾼다.

`tasks.py:527` `    ENVIRONMENT_SINCE_KEY,` 다음에 `    HARD_FINDING_IDLE_KEY,`.

`tasks.py:1239-1241` 시그니처를:

```python
def _terminal_attempt_is_unchanged(
    item: ContentItem,
    philosophy: HospitalContentPhilosophy | None,
    hospital: Hospital | None = None,
) -> bool:
```

`:1252-1253` `if previous.get("context") != _generation_attempt_context(item, philosophy): return False` 다음에:

```python
    if previous.get(HARD_FINDING_IDLE_KEY) and _approved_facts_changed_since_block(item, hospital):
        # 사실·안전 HARD는 승인 자료가 풀므로, 사람이 그 자료를 채웠으면 다시 집는다.
        return False
```

`:5695` `if _terminal_attempt_is_unchanged(item, _generation_philosophy_sync(db, hospital.id)):`를:

```python
        if _terminal_attempt_is_unchanged(
            item, _generation_philosophy_sync(db, hospital.id), hospital
        ):
```

`:7021`(앵커 `        if (\n            stored_assessment.code == "CONTENT_AI_HARD_FINDING"\n            # 운영자 재검수 전용 실행은 교정(공급자 호출)을 하지 않는다.`) 바로 위 줄에 `        correction_ran = False`를 넣고, 같은 블록의 `            if corrected is not None:` 아래 첫 줄로 `                correction_ran = True`를 넣는다.

`:7074-7075` 주석(`# Fact/medical-safety findings remain fail-closed …`) 다음, `return (` 앞에:

```python
            if (
                stored_assessment.code == "CONTENT_AI_HARD_FINDING"
                and not review_only
                and not correction_ran
                and _stored_model_declared_hard(item)
            ):
                # 이번 실행은 아무것도 맡지 못했다(사람 편집으로 교정 불가 등). 같은 입력으로는
                # 다음 스윕도 같은 결과이므로 종착으로 표시한다 — 입력·사람 편집·승인 사실이
                # 바뀌면 로더가 다시 집는다(`_terminal_attempt_is_unchanged`).
                _remember_generation_attempt(
                    db,
                    item,
                    philosophy,
                    stored_assessment.code,
                    count_attempt=False,
                    extra={HARD_FINDING_IDLE_KEY: True},
                )
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_terminal_slot_reclaim.py tests/test_publish_block_auto_resolve.py tests/test_tasks_nightly.py tests/test_generation_recovery_ladder.py tests/test_topic_swap_fallback.py -q` Expected: 전부 passed(기존 `test_repair_owned_codes_are_never_terminal` — 표시 없는 HARD는 종착 아님 — 도 그대로 통과).

- [ ] **Step 5: 커밋** `git add backend/app/workers/generation_retry_policy.py backend/app/workers/tasks.py backend/tests/test_terminal_slot_reclaim.py backend/tests/test_publish_block_auto_resolve.py && git commit -m "fix(generation): 아무것도 맡지 못한 HARD 차단은 입력이 바뀔 때까지 스윕이 다시 집지 않는다"`

**계약 영향:** CLAUDE.md 23:00·복구 스윕 문단의 "종착 기록(`generation_retry_policy.attempt_is_terminal` — 소진된 본문 표본 실패 `OPERATOR_REQUIRED`·사람이 정하는 참고자료 보류·승인 Essence 없음)" 괄호에 "·교정도 재작성도 맡지 못한 모델 단정 HARD(`hard_finding_idle`)"를 더하고, "입력(승인 자료·유형·질문)이 바뀌거나 사람이 고치면 다시 시도하고"에 "승인된 병원 사실이 바뀌어도"를 더한다.

**위험·롤백:** 표시를 남긴 뒤 다시 집히지 않으므로, 풀리는 조건(지문·편집·병원 사실)을 놓치면 글이 사람의 일로 멈춰 있다 — 이미 인시던트는 OPEN(사람의 일)이고, 운영자 재시도 경로는 시도 기록을 비우고 다시 보낸다(`operator_retry_releases`, `generation_incident_control.py:312`). 롤백: revert(표시 키는 남아도 옛 코드가 무시한다).

### Task 3-6: 진료비·병원 선택 글은 참고자료가 선택 사항 (D23)

**Files:**
- Modify: `backend/app/services/reference_requirement.py:221-233`(`references_left_to_operator` 설명) 뒤에 판정 두 개 추가
- Modify: `backend/app/services/content_publication.py:25-28`(import), `:90`(`has_required_references`), `:106-107`(`public_surface_has_required_references`)
- Modify: `backend/app/services/reference_publication.py:54-57`(import), `:174`(`publication_references_missing`), `:187`(`bind_reference_checks_to_revision`), `:301`(`refresh_publication_references`)
- Modify: `backend/app/services/content_engine.py:44-47`(import), `:1419-1425`(`_validate_generated_result`의 `_validate_geo` 호출)
- Test(신규): `backend/tests/test_reference_requirement.py` 끝에 2개
- Test(기존, 새 계약으로 고침 — 정본은 `p3-patches/p3-all.diff`의 해당 hunk): `tests/test_reference_operator_decides.py`(5개 함수), `tests/test_reference_publication_gate.py`(9개), `tests/test_reference_curated_alias.py`(1개), `tests/test_tasks_nightly.py`(8개), `tests/test_content_brief.py`(1개), `tests/integration/test_operator_decides_digest_postgres.py`(2개), `tests/integration/test_reference_claim_last_run_postgres.py`(4개), `tests/integration/test_publisher_live_claim_postgres.py`(1개)

**조사 결과:**
- 오늘의 규칙: 진료비·병원 선택 제목(`topic_without_authoritative_source`, `reference_requirement.py:197-218`)이면 수기 목록 치유를 하지 않고, 참고자료가 비면 `MISSING_REFERENCES` + `operator_decides`(사람의 결정, 기한 없음)로 막는다 — 발행(`content_publication.py:296-305` → `has_required_references`), 발행 직전 재검증(`reference_publication.py:345` `operator_decides`), 생성(GEO 거절 `content_engine.py:2071-2081` → 워커 `tasks.py:6069-6077`이 `operator_decides` 기록). 30일 운영: 이 보류로 영구 미공개 3편, 사고 8건(해소 평균 27.9h).
- 바꾸는 점은 **"비어도 막지 않는다"** 하나다. 그래서 '필수 유형' 판정(`references_required_for`)은 그대로 두고 — 이것이 프롬프트·도구 스키마(`content_engine.py:443`, `:770`)와 **참고자료 검증·수기 목록 문서 제외**(`_verify_generated_references`의 `required` 인자, `:2236` `no_source_topic = required and …`)를 이끈다 — "비면 막는가"만 새 판정 `references_block_when_empty_for`/`references_block_when_empty`로 가른다. 대표 결정의 "참고자료가 있으면 검증·치유는 그대로"를 이렇게 지킨다: 실제 문서 GET 검증·주제 대조·제외 목록·발행 전 수기 목록 문서 제외(`_strips_curated_references`, `reference_publication.py:197-207`)·관리자 PATCH 422 `CURATED_REFERENCE_NOT_ALLOWED`는 모두 남는다. **수기 목록으로 채우는 치유는 이 글들에 계속 하지 않는다**(2026-09-29 결정 — 주제만 겹치는 질환 문서가 가짜 근거가 된다; 열린 질문 Q2).
- 막는 지점 5곳을 새 판정으로 바꾼다: 발행 게이트 `has_required_references`, 재검증 '안정' 판정 `publication_references_missing`(비면 치유를 다시 시도하게 만드는 함수), 판 결합 `bind_reference_checks_to_revision`(비면 `None` → 발행 거절), 재검증 즉시 반환 조건(`refresh_publication_references:301`), 생성 GEO 하드 거절(작가가 쓴 제목으로 판정).
- **공개 가시성도 고친다**(§2-4): `public_surface_has_required_references`가 유형만 보므로 참고자료 0개로 발행된 진료비 글을 숨긴다. 진료비·병원 선택 제목이면 0개도 보이게 한다. 이미 공개된 글을 새로 숨기는 변경이 아니다.
- `references_left_to_operator`·`operator_decides_references`·`OPERATOR_DECIDES_KEY`·운영자 요약 줄(#181) 경로는 **지우지 않는다**. 새로 생기지 않을 뿐, D23 전에 저장된 기록(운영 3건)을 Task 3-15의 카탈로그 판 올리기가 한 번 다시 평가해 풀 때까지 읽는 코드가 필요하다. 이 죽은 경로의 정리는 후속(열린 질문 Q7).
- 야간 로더 SQL(`nightly_generation_batch.py:369-379` `references_need_repair`)은 유형만 보므로 참고자료 0개인 진료비 DRAFT를 매 스윕 "수리 필요"로 집는다. 워커는 게이트가 통과라 돈을 쓰지 않고(이미지·준비 판정만) 끝나지만, 하루 7번 lease·`GENERATE_CONTENT_ITEM` 실행을 남기고 정렬(이월 → 예정일)상 내일 슬롯보다 먼저 상한 50의 자리를 차지한다. 그래서 로더(claim 전)와 워커가 같은 파이썬 술어 `stored_body_awaits_only_optional_references`(`nightly_generation_batch.py`)로 거른다 — 본문이 있고 참고자료가 비었으며 `references_block_when_empty`가 거짓이고 이미지·FAQ·검수·Essence 수리 이유가 없으면 집지 않는다. 다른 수리 이유가 있으면 종전대로 집는다(2026-10-13 검토 반영).

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_reference_requirement.py` 끝에(이 파일은 `pytest`·`SimpleNamespace`·`ContentType`을 이미 import한다):

```python
# ── D23(2026-10-13 대표 결정): 진료비·병원 선택 글은 참고자료가 선택이다 ─────────────────


@pytest.mark.parametrize(
    ("content_type", "title", "blocks"),
    [
        (ContentType.FAQ, "허리 디스크 초기 증상과 진료 시점", True),
        (ContentType.FAQ, "도수치료 비용 — 시간·횟수·보험에 따라 어떻게 달라지나요?", False),
        (ContentType.DISEASE, "경산 내과 병원 추천 — 증상별 진료 흐름과 판단 기준", False),
        (ContentType.FAQ, "경산 경동맥초음파 검사, 어느 병원에서 받아야 할까요?", True),
        (ContentType.FAQ, None, True),  # 아직 쓰이지 않은 슬롯은 유형대로 필수
        (ContentType.NOTICE, "추석 연휴 휴진 안내", False),  # 순수 운영 공지는 종전대로 면제
    ],
)
def test_empty_references_block_only_articles_that_have_an_authoritative_source(
    content_type, title, blocks
):
    from app.services.reference_requirement import references_block_when_empty_for

    assert references_block_when_empty_for(content_type, title=title) is blocks


def test_a_published_cost_post_without_references_stays_visible():
    from app.services.content_publication import (
        has_required_references,
        public_surface_has_required_references,
    )

    cost = SimpleNamespace(
        content_type=ContentType.FAQ,
        title="도수치료 비용 — 시간·횟수·보험에 따라 어떻게 달라지나요?",
        content_brief=None,
        query_target_id=None,
        references_list=[],
    )
    medical = SimpleNamespace(**{**vars(cost), "title": "허리 디스크 초기 증상과 진료 시점"})

    assert has_required_references(cost) and public_surface_has_required_references(cost)
    assert not has_required_references(medical)
    assert not public_surface_has_required_references(medical)
```

기존 테스트 중 새 계약과 어긋나는 것은 패치의 해당 hunk대로 고친다. 원칙은 둘이다.
1. **"진료비·병원 선택 글의 보류" 자체가 주제인 테스트**는 새 기대값으로 바꾼다: 참고자료 0개로 발행(또는 게이트 통과)·수기 목록 GET 0회·`operator_decides` 기록 없음·인시던트 없음. 대표 예(`tests/test_reference_operator_decides.py`):

```python
@pytest.mark.parametrize("title", [COST_TITLE, CHOICE_TITLE], ids=["cost", "provider_choice"])
def test_scheduled_post_is_published_without_references_and_without_a_curated_fill(
    monkeypatch, title
):
    """D23(2026-10-13 대표 결정): 진료비·병원 선택 글은 참고자료가 선택이다 — 0개로 발행한다."""

    item, _db, _effects = _publish_setup(monkeypatch, references=[], checks=None, title=title)
    fetcher = _hemorrhoid_fetcher()
    _freeze(monkeypatch, _kst(2026, 6, 10, 8, 0))

    payload = tasks._auto_publish_one(
        item.id, reference_verifier=ReferenceVerifier(fetcher, domain_spacing=0)
    )

    assert payload is not None and payload.get("kind") != "blocked"
    assert item.status is tasks.ContentStatus.PUBLISHED
    assert item.references_list == []  # 수기 목록으로 채우지 않는다
    assert fetcher.calls == []  # 치유 후보를 열지도 않는다
```

  이렇게 바뀌는 것: `test_scheduled_post_is_held_for_the_operator_without_a_curated_fill`(위로 교체), `test_seven_forty_five_removes_a_dead_reference_but_never_fills_it`(`(paged, incidents) == (0, [])`), `test_scheduled_post_drops_a_cited_curated_document_even_when_it_passes`(PUBLISHED·참고자료 `[]`), `test_generation_does_not_accept_a_cited_curated_document`(예외 대신 `result["references"] == []`), `test_unwritten_slot_whose_writer_cited_a_curated_document_goes_to_the_operator`(삭제 — 앞 테스트가 같은 사실을 고정), `test_reference_curated_alias.py::test_an_unwritten_cost_slot_citing_an_alias_goes_to_the_operator`(→ `..._is_saved_without_it`), `test_reference_publication_gate.py::test_last_run_reports_a_claimed_cost_post_without_touching_it`(→ `test_last_run_neither_reports_nor_touches_a_claimed_cost_post`, payload `None`), `::test_an_unclaimed_cost_post_citing_a_catalog_document_is_held_for_the_operator`(→ `..._is_published_without_it`), `::test_last_run_falls_back_to_the_stored_state_without_a_prefetched_refresh`의 `catalog_fresh_pass` 기대값 `"blocked"`→`None`, `test_tasks_nightly.py::test_seven_forty_five_names_a_same_day_cost_post_left_to_the_operator`(→ `test_seven_forty_five_does_not_hold_a_cost_post_whose_references_drop_out`, `_od_run(db) == 0`), `test_eight_oclock_payload_keeps_operator_line_a_bool_and_carries_the_copy_key`의 `written_cost_post` 매개변수 삭제, 통합 `test_operator_decides_digest_postgres.py::test_seven_forty_five_names_only_the_same_day_operator_hold_once`(→ `test_seven_forty_five_holds_only_the_medical_post_after_d23`), `test_reference_claim_last_run_postgres.py`의 S2 두 개(보류 없음)·A2 두 개(의료 글로 바꾸고, 대조군 매개변수 테스트는 진료비 글이 더는 보류되지 않아 "옛 기록이 있어도 인시던트 없음"으로 교체).
2. **진료비 제목을 '치유 후보 없는 글'로 빌려 쓴 테스트**는 제목만 수기 목록 치유 후보가 없는 의료 글 `"귀지가 막혔을 때 — 집에서 해도 되는 관리"`(파일에 이미 `NO_CATALOG_TITLE`로 있다)로 바꾸고 기대값(보류·`MISSING_REFERENCES`)은 그대로 둔다: `test_reference_publication_gate.py`의 `test_still_nothing_holds_publication_with_the_real_cause`, `test_seven_forty_five_holds_and_pages_the_real_cause_when_nothing_survives`, `test_admin_manual_publish_runs_the_same_reference_gate`, `test_manual_publish_fetches_before_taking_the_hospital_lock`, `test_last_run_uses_the_prefetched_refresh_when_the_claim_is_taken_during_the_get`(운영자 줄 `True`→`False`), `test_a_claimed_row_past_its_date_is_judged_like_the_last_run`, `test_content_brief.py::test_publish_content_rejects_generated_content_without_references`, 통합 `test_publisher_live_claim_postgres.py::_three_shapes`(운영자 줄 1→0). `test_tasks_nightly.py`의 운영자 보류 요약 테스트 6개는 작성된 진료비 글 대신 **D23 전에 남은 기록을 가진 쓰이지 않은 슬롯**(`_od_unwritten`)으로 바꾼다(그 기록이 풀리기 전까지 요약 줄 경로가 여전히 쓰인다).

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_reference_requirement.py -q -k "authoritative_source or stays_visible"` Expected: 7건 FAILED(`ImportError: cannot import name 'references_block_when_empty_for'`, 마지막은 `assert False`).

- [ ] **Step 3: 최소 구현**

`reference_requirement.py:221-233`의 `references_left_to_operator`를 설명만 바꾸고(본문 그대로), 그 뒤에 두 함수를 추가한다:

```python
def references_left_to_operator(item: object, *, title: object = None) -> bool:
    """참고자료 필수 유형인데 공신력 있는 문서가 본질적으로 없는 주제(진료비·병원 선택)의 글인가.

    이 글은 수기 목록에서 채우지 않고, 작가가 인용한 수기 목록 문서도 발행 전에 뺀다. 2026-10-13
    대표 결정(D23) 뒤로 참고자료는 **선택**이다 — 비어도 생성·발행을 막지 않는다
    (`references_block_when_empty`). 이름은 그 전의 "보류는 사람이 정한다" 판정에서 왔다.
    `title`은 아직 쓰이지 않은 슬롯에서 작가가 만든 제목이다(행의 제목 대신 판정한다).
    """

    return references_required(item) and (
        topic_without_authoritative_source(
            title if title is not None else getattr(item, "title", None)
        )
        is not None
    )


def references_block_when_empty_for(
    content_type: object,
    *,
    content_brief: object = None,
    query_target_id: object = None,
    title: object = None,
) -> bool:
    """참고자료가 비면 이 글의 생성·발행을 막는가(D23).

    참고자료 필수 유형(`references_required_for`)이되 제목이 진료비·병원 선택 글이 아니어야 한다.
    진료비·병원 선택 글은 붙일 공신력 문서가 본질적으로 없어 참고자료가 선택이다. 참고자료가
    있으면 다른 글과 똑같이 실제 문서 검증·주제 대조·제외 목록을 거친다.
    """

    return (
        references_required_for(
            content_type, content_brief=content_brief, query_target_id=query_target_id
        )
        and topic_without_authoritative_source(title) is None
    )


def references_block_when_empty(item: object, *, title: object = None) -> bool:
    """행(또는 같은 속성을 가진 보기)에 대한 `references_block_when_empty_for`."""

    return references_block_when_empty_for(
        getattr(item, "content_type", None),
        content_brief=getattr(item, "content_brief", None),
        query_target_id=getattr(item, "query_target_id", None),
        title=title if title is not None else getattr(item, "title", None),
    )
```

모듈 머리말(`:26-29`)의 "…전부 빠져도 수기 목록 치유를 하지 않고, 발행 보류를 자동 본문 수리가 아니라 사람의 결정(`OPERATOR_REQUIRED`)으로 보낸다(`references_left_to_operator`). 필수 여부는 그대로다 — 통과한 참고자료가 있으면 그대로 발행한다."를 "…전부 빠져도 수기 목록 치유를 하지 않는다(`references_left_to_operator`). 2026-10-13 대표 결정(D23)으로 참고자료가 비어도 생성·발행을 막지 않는다(`references_block_when_empty`). 통과한 참고자료가 있으면 그대로 발행한다."로 바꾼다.

`content_publication.py:25-28` import를:

```python
from app.services.reference_requirement import (
    REFERENCES_REQUIRED_TYPES,
    references_block_when_empty,
    topic_without_authoritative_source,
)
```

`:90-91` `if not references_required(item): return True`를:

```python
    if not references_block_when_empty(item):
        # 참고자료가 선택인 글 — 순수 운영 공지와 진료비·병원 선택 글(D23).
        return True
```

`:106-107` `if count_citable_references(item) <= 0: return False`를:

```python
    if count_citable_references(item) <= 0:
        # 진료비·병원 선택 글은 참고자료 0개로 발행될 수 있다(D23) — 그 글을 숨기지 않는다.
        return topic_without_authoritative_source(getattr(item, "title", None)) is not None
```

`reference_publication.py:54-57` import에 `references_block_when_empty,`를 더하고(`references_left_to_operator`·`references_required`는 그대로), `:174` `if not references_required(item):`를 `if not references_block_when_empty(item):`로, `:187` `return None if references_required(item) else []`를 `return None if references_block_when_empty(item) else []`로, `:301` `and not (required and not references)`를 `and not (references_block_when_empty(item) and not references)`로 바꾼다(같은 함수의 `required = references_required(item)`(`:292`)은 치유 여부 판정에 그대로 쓰인다).

`content_engine.py:44-47` import에 `references_block_when_empty_for,`를 더하고, `:1419-1425`의 `_validate_geo(...)` 호출에서 `references_required=references_required_for(content_type, content_brief=content_brief),`를:

```python
        # 진료비·병원 선택 글(작가의 제목으로 판정)은 참고자료가 선택이다(D23). 검증·수기 목록
        # 문서 제외는 위 `_verify_generated_references`가 유형 기준으로 그대로 한다.
        references_required=references_block_when_empty_for(
            content_type, content_brief=content_brief, title=result.get("title")
        ),
```

로 바꾼다.

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_reference_requirement.py tests/test_reference_operator_decides.py tests/test_reference_publication_gate.py tests/test_reference_curated_alias.py tests/test_reference_operator_copy.py tests/test_tasks_nightly.py tests/test_content_brief.py tests/integration/test_operator_decides_digest_postgres.py tests/integration/test_reference_claim_last_run_postgres.py tests/integration/test_publisher_live_claim_postgres.py -q` Expected: 전부 passed(프로토타입: 참고자료 4파일 723 passed·8 skipped, nightly 205 passed, 통합 3파일 14 passed).

- [ ] **Step 5: 커밋** `git add backend/app/services/reference_requirement.py backend/app/services/content_publication.py backend/app/services/reference_publication.py backend/app/services/content_engine.py backend/tests && git commit -m "feat(references): 진료비·병원 선택 글은 참고자료가 비어도 생성·발행을 막지 않는다(D23)"`

**계약 영향:** CLAUDE.md "콘텐츠와 월간 계약" 참고자료 문단을 고친다.
- "참고자료 필수 판정은 `services/reference_requirement.py` 한 규칙이다 — … 필수 글이 비면 수기 목록 치유를 먼저 한 뒤 `MISSING_REFERENCES`로 보류한다." 뒤에 "진료비·병원 선택 글(제목 기준)은 필수 유형이어도 참고자료가 **선택**이다(2026-10-13 대표 결정) — 비어도 생성·발행·공개를 막지 않고(`references_block_when_empty`), 있으면 같은 실제 문서 검증을 거친다."를 더한다.
- 긴 참고자료 문단의 "진료비·병원 선택 글(…)은 공신력 있는 문서가 본질적으로 없어 생성·발행 모두 수기 목록으로 채우지 않고, 비면 그 보류는 자동 본문 수리·주제 교체 없이 곧바로 `OPERATOR_REQUIRED`(사람이 정함)다." → "…수기 목록으로 채우지 않으며, 비어도 보류하지 않는다(D23)."로, "아직 쓰이지 않은 슬롯은 작가가 만든 제목으로 판정해, 작가 회차 뒤에도 참고자료가 0개면 표본 사다리 없이 `MISSING_REFERENCES`+`operator_decides` 기록과 기한 없는 OPEN이 된다." → "아직 쓰이지 않은 슬롯은 작가가 만든 제목으로 판정해, 참고자료 0개로도 저장한다. D23 이전에 남은 `operator_decides` 기록은 게이트 카탈로그 판 올리기로 한 번 다시 평가받는다."로 바꾼다. 수기 목록 문서 제외·PATCH 422 문장은 그대로 둔다.

**위험·롤백:**
- 참고자료 0개인 진료비·병원 선택 글이 공개된다 — 법적 요구가 아니라 GEO 신뢰도 문제다(검토 §3). 구체 금액·무료 주장은 생성 가격 검사와 Task 3-1이 계속 막는다.
- 야간 로더·워커의 파이썬 술어(`stored_body_awaits_only_optional_references`)가 SQL 복구 술어의 참고자료 밖 조건을 옮겨 적는다. SQL에 조건을 더하고 파이썬 짝을 빠뜨리면 그 이유로 고쳐야 할 진료비 글을 집지 않는다 — `test_optional_reference_reclaim.py`가 SQL 갈래 수를 고정해 알린다.
- 롤백: revert. D23 이전 규칙으로 돌아가면 아직 공개 안 된 0개 진료비 글은 다시 `MISSING_REFERENCES`로 막히고, 이미 공개된 0개 진료비 글은 공개 가시성이 다시 숨긴다(롤백 전에 해당 글 목록을 q 조회로 확인).

### Task 3-7: 결정적 치유 사슬 — 병원명·필수 문구 원문 덧붙이기 (E2)

**Files:**
- Modify: `backend/app/services/content_engine.py:196-210`(예외 클래스), `:283`(필수 문구 프롬프트 줄), `:1484-1501`(`_validate_must_use_verbatim`), `:1474`(호출), `:1603-1623`(생성 루프의 원장명 분기), `:1692-1714`(`_heal_missing_director_name`), `:1717` 앞(새 치유·사슬), `:2084-2086`(병원명 GEO 거절)
- Test(신규): `backend/tests/test_generation_deterministic_heals.py`
- Test(기존, 새 계약으로 고침): `backend/tests/test_content_engine_must_use_verbatim.py`(3개 함수 — 패치 hunk)

**조사 결과:**
- 원장명 누락만 결정적으로 고친다(`content_engine.py:1603-1622`, `_heal_missing_director_name` `:1692-1714`, "{병원명}의 원장은 {원장}입니다." 한 줄). 병원명 누락(`:2084-2086` 평범한 `ValueError("GEO hard-fail: 병원명 … body 미포함")`)과 필수 문구 누락(`MissingMustUseMessagesError`, `:1484-1501`)은 작가 재작성 회차(최대 3회, `GENERATION_REMEDIATION_ROUNDS` `:119`)를 산다. 작가 1회 약 $0.08, 재작성 뒤 독립 검수까지 약 $0.18(검토 §8).
- 필수 문구는 이미 "현재 APPROVED 승인본의 문구, 금지 표현이 든 문구는 제외"로 정해진 집합이다(`approved_must_use_messages_with_record`, `content_engine.py:1566`; 제외 규칙 `must_use_verbatim.py:187-225`). 그 원문을 본문 끝에 한 문단씩 덧붙이면 `appears_as_standalone_sentence`(독립 문장 판정)를 그대로 만족한다. 독립 검수는 필수 문구 원문을 인용한 지적을 면제한다(CLAUDE.md 필수 문구 문단) — 덧붙인 문장이 새 차단을 만들지 않는다.
- 한 치유가 다른 치유 대상을 드러낸다(프로토타입에서 확인): 금지 표현 문장을 지웠더니 병원명이 빠짐, 병원명을 붙였더니 필수 문구가 없음. 그래서 치유 함수는 **결과만 고쳐 돌려주고**, 검증은 사슬 함수 `_apply_deterministic_heals`가 한 번에 한다(치유 종류 수 = 4회 상한). 원장명 치유도 이 계약으로 바꾼다(검증을 사슬로 옮김 — 동작은 같다).
- 병원명이 빠졌고 원장명도 빠졌으면 원장명 줄 하나("{병원명}의 원장은 {원장}입니다.")가 둘 다 채운다. 원장명은 있으면 "이 글은 {병원명}에서 안내합니다."를 붙인다(승인된 병원명 외 새 사실 없음).
- 사슬이 실패하면 다음 회차에 넘길 거절은 원래 거절이다 — 단 문장을 지운 치유(Task 3-8)가 끼었으면 그 뒤 거절은 우리가 만든 것이므로 원래 거절을 넘긴다(작가가 "병원명을 빼먹었다"는 틀린 지적을 듣지 않게). 원장명 치유의 종전 동작("붙였더니 다른 게이트가 걸리면 그 사유로 다시 쓴다")은 그대로다.
- 분류기 문구는 메시지 머리("GEO hard-fail", "must_use messages missing verbatim")로 판정하므로(`generation_run_control.py:214-236`) 새 예외 클래스도 같은 메시지를 쓴다 — 운영자 문구가 바뀌지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/tests/test_generation_deterministic_heals.py`(새 파일. Task 3-8·3-9·3-13이 같은 파일에 테스트를 더한다):

```python
"""E2(2026-10-13): 결정적으로 고칠 수 있는 거절은 작가를 다시 사지 않고 같은 회차에서 고친다.

- 본문 문장에만 든 금지 표현 → 그 문장을 통째로 지운다(고쳐 쓰지 않는다).
- 빠진 승인 필수 문구 → 본문 끝에 원문 그대로 덧붙인다.
- 빠진 병원명 → 승인된 병원명 한 줄을 덧붙인다(원장명 치유와 같은 방식).
- 검사 이름 뒤 낱말 '치료' 제목 → 그 낱말만 지운다.

제목·메타·FAQ의 금지 표현, 문장 하나로 짚을 수 없는 위반, 지울 문장이 너무 많은 응답은 종전대로
직전 사유를 넘겨 다시 쓰게 한다. 공급자 호출은 모두 가짜다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import content_engine
from app.services.question_wellformed import drop_treatment_after_exam
from tests.test_content_engine_must_use_verbatim import (
    MUST_USE_2CM,
    MUST_USE_SCREENING,
    _generate,
    _install_writer,
    _payload,
    _Writer,
)

AD_SENTENCE = "저희 병원은 최고의 의료진이 진료합니다."


async def test_a_missing_must_use_message_is_appended_verbatim(monkeypatch):
    writer = _Writer([_payload(MUST_USE_SCREENING)])
    _install_writer(monkeypatch, writer)

    saved = await _generate([MUST_USE_2CM, MUST_USE_SCREENING])

    assert len(writer.calls) == 1
    assert saved["body"].rstrip().endswith(MUST_USE_2CM)
    assert saved["body"].count(MUST_USE_SCREENING) == 1


async def test_a_missing_hospital_name_is_appended(monkeypatch):
    payload = _payload("검사 전날 식단을 조절합니다.")
    payload["body"] = payload["body"].replace("테스트의원 ", "")
    writer = _Writer([payload])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert saved["body"].rstrip().endswith("이 글은 테스트의원에서 안내합니다.")


```

(`AD_SENTENCE`·`pytest`·`SimpleNamespace`·`drop_treatment_after_exam` import는 3-8·3-9 테스트가 쓴다. 3-7만 적용한 상태에서 ruff가 unused를 말하면 3-9까지 한꺼번에 적용한 뒤 돌린다 — 한 PR이다.)

기존 `tests/test_content_engine_must_use_verbatim.py`는 새 계약으로 고친다(패치 hunk): `test_paraphrased_must_use_is_fed_back_and_rewritten_within_the_existing_loop` → `test_paraphrased_must_use_gets_the_original_appended_without_a_rewrite`(작가 1회, 본문이 승인 원문으로 끝남, 이미 든 문구는 다시 붙지 않음), `test_must_use_still_missing_after_the_existing_budget_is_a_sample_rejection` 삭제(분류 문구는 `test_must_use_rejection_has_its_own_operator_message`가 계속 고정), `test_paraphrase_variants_are_rejected` → `test_paraphrase_variants_get_the_original_appended`. 모듈 머리말의 "빠지면 기존 재작성 루프·호출 예산 안에서만 다시 쓰고, 끝내 빠지면 GENERATION_REJECTED"를 "빠지면 승인 원문을 본문 끝에 결정적으로 덧붙인다(2026-10-13 E2) — 작가를 다시 사지 않는다"로 바꾼다.

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py -q -k "must_use or hospital_name"` Expected: `test_a_missing_must_use_message_is_appended_verbatim`이 `assert 2 == 1`(재작성 1회 더), `test_a_missing_hospital_name_is_appended`가 `assert 2 == 1`(또는 소진 시 `ValueError`).

- [ ] **Step 3: 최소 구현** — `backend/app/services/content_engine.py`

(a) `:196-210`의 `MissingMustUseMessagesError`를 바꾸고, `DirectorNameMissingError` 뒤에 두 예외를 더한다:

```python
class MissingMustUseMessagesError(ValueError):
    """승인된 필수 문구가 본문에 원문 그대로 들어가지 않았다.

    생성 루프는 빠진 문구를 본문 끝에 원문 그대로 덧붙이는 결정적 치유를 먼저 한다
    (`_heal_missing_must_use`). 치유 뒤 다른 게이트가 걸리면 기존 재작성 루프가 그 사유로
    다시 쓰게 하고, 끝내 실패하면 GENERATION_REJECTED(본문 표본 실패) 경로를 탄다.
    """

    def __init__(
        self, message: str, result: dict | None = None, missing: tuple[str, ...] = ()
    ):
        super().__init__(message)
        self.result = result
        self.missing = missing
```

```python
class HospitalNameMissingError(ValueError):
    """병원명이 본문에 없다 — 승인된 병원명 한 줄로 결정적으로 고친다(작가를 다시 사지 않는다)."""

    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


class ForbiddenExpressionError(ValueError):
    """금지 표현이 공개 필드에 있다. 본문 문장에만 있으면 그 문장을 지우는 결정적 치유 대상이다.

    메시지는 종전 그대로("Forbidden medical expressions …")다 — 실행 분류기가 이 문구로 거절
    사유를 가른다(`generation_run_control._safe_rejection_message`).
    """

    def __init__(self, message: str, result: dict, *, body_only: bool):
        super().__init__(message)
        self.result = result
        self.body_only = body_only
```

(`ForbiddenExpressionError`를 던지는 곳과 그 치유·레지스트리 등록은 Task 3-8이다. 사슬 함수가 이 클래스를 참조하므로 클래스는 여기서 만든다.)

(b) `:283`의 "  다음 문장에서 이어 씁니다. 시스템이 생성 후 원문 포함 여부를 검사하며 빠지면 저장되지 않습니다."를:

```text
  다음 문장에서 이어 씁니다. 시스템이 생성 후 원문 포함 여부를 검사하며, 빠지면 원문을 글 끝에
  따로 덧붙이므로 문맥이 어색해집니다 — 본문 흐름 안에 넣으세요.
```

(c) `_validate_must_use_verbatim`(`:1484`) 시그니처를 `def _validate_must_use_verbatim(body: object, must_use_messages: list[str] | None, *, result: dict | None = None) -> None:`로, 끝의 `raise MissingMustUseMessagesError(...)`에 인자 `result, tuple(missing),`을 더하고(메시지 문자열 뒤), `_validate_generated_result`(`:1474`)의 호출을 `_validate_must_use_verbatim(result.get("body"), must_use_messages, result=result)`로 바꾼다.

(d) `:2084-2086` 병원명 거절을 `raise HospitalNameMissingError(f"GEO hard-fail: 병원명 '{hospital_name}' body 미포함", result)`로 바꾼다.

(e) `_heal_missing_director_name`(`:1692-1714`)을 "결과만 고쳐 돌려준다"로 바꾼다 — 시그니처 `def _heal_missing_director_name(error: DirectorNameMissingError, hospital: Hospital, *, must_use_messages: list[str]) -> dict | None:`, 마지막 `return _validate_generated_result(...)`를 `return result`로. 그 뒤(`:1717` `# Keep the transport retry controller…` 주석 앞)에 다음을 추가한다(금지 표현 치유 `_heal_forbidden_sentences`와 그 레지스트리 항목은 Task 3-8이 넣는다):

```python
def _heal_missing_hospital_name(
    error: HospitalNameMissingError, hospital: Hospital, *, must_use_messages: list[str]
) -> dict | None:
    """승인된 병원명 한 줄을 덧붙인다. 원장명도 빠졌으면 두 이름이 함께 든 원장명 줄로 붙인다."""

    result = error.result
    name = (hospital.name or "").strip()
    body = (result.get("body") or "").rstrip()
    if not name or name in body:
        return None
    director = (hospital.director_name or "").strip()
    line = (
        f"{name}의 원장은 {director}입니다."
        if director and director not in body
        else f"이 글은 {name}에서 안내합니다."
    )
    result["body"] = f"{body}\n\n{line}"
    return result


def _heal_missing_must_use(
    error: MissingMustUseMessagesError, hospital: Hospital, *, must_use_messages: list[str]
) -> dict | None:
    """빠진 승인 필수 문구를 본문 끝에 원문 그대로, 한 문단에 한 문구씩 덧붙인다.

    필수 문구 집합은 작가 검증과 같다(현재 APPROVED 승인본, 금지 표현이 든 문구는 이미 제외).
    """

    result = error.result
    if result is None or not error.missing:
        return None
    body = (result.get("body") or "").rstrip()
    appended = "\n\n".join(message.strip() for message in error.missing)
    result["body"] = f"{body}\n\n{appended}"
    return result


_DETERMINISTIC_HEALS = {
    DirectorNameMissingError: _heal_missing_director_name,
    HospitalNameMissingError: _heal_missing_hospital_name,
    MissingMustUseMessagesError: _heal_missing_must_use,
}


def _apply_deterministic_heals(
    error: ValueError,
    hospital: Hospital,
    content_type: ContentType,
    content_brief: dict | None,
    *,
    must_use_messages: list[str],
) -> tuple[dict | None, ValueError]:
    """결정적 치유를 이어서 적용하고 같은 게이트 전체로 다시 검증한다(공급자 호출 없음).

    한 치유가 다른 치유 대상을 드러낼 수 있다 — 금지 표현 문장을 지웠더니 병원명이 빠졌다, 병원명을
    붙였더니 필수 문구가 없다. 치유 종류 수만큼만 잇는다. 고치지 못하면 (None, 다음 회차에 넘길
    거절)을 돌려준다. 문장을 지운 치유가 끼었으면 그 뒤의 거절은 우리가 만든 것이므로 원래 거절을
    넘긴다(작가가 지운 적 없는 병원명을 빼먹었다고 듣지 않게).
    """

    current = error
    deleted = False
    for _ in range(len(_DETERMINISTIC_HEALS)):
        healer = _DETERMINISTIC_HEALS.get(type(current))
        patched = (
            healer(current, hospital, must_use_messages=must_use_messages)
            if healer is not None
            else None
        )
        if patched is None:
            break
        deleted = deleted or isinstance(current, ForbiddenExpressionError)
        try:
            return (
                _validate_generated_result(
                    patched,
                    hospital,
                    content_type,
                    content_brief,
                    must_use_messages=must_use_messages,
                ),
                error,
            )
        except ValueError as next_error:
            current = next_error
    return None, (error if deleted else current)
```

(f) 생성 루프(`:1603-1623`)의 `except DirectorNameMissingError as exc:` 분기 전체를:

```python
        except (
            DirectorNameMissingError,
            HospitalNameMissingError,
            ForbiddenExpressionError,
            MissingMustUseMessagesError,
        ) as exc:
            # 승인된 원장명·병원명 한 줄, 금지 표현이 든 본문 문장 삭제, 빠진 필수 문구 원문
            # 덧붙이기는 결정적으로 고칠 수 있다. 본문이 그 한 가지만 빼고 완전한데도 재작성을
            # 두 번 더 사면 정상 글 한 편에 세 번 결제한다.
            healed, last_error = _apply_deterministic_heals(
                exc,
                hospital,
                content_type,
                content_brief,
                must_use_messages=must_use_messages,
            )
            if healed is not None:
                healed["_provider_http_attempts"] = int(
                    attempt_context.get("http_attempt") or 0
                )
                return healed
```

로 바꾼다(Task 3-13이 이 분기에 거절·치유 횟수를 더한다).

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py tests/test_content_engine_must_use_verbatim.py tests/test_content_engine.py -q` Expected: 전부 passed(프로토타입 최종 상태: content_engine 140 + must_use 13 + heals 26 passed, DB·Redis env 필요). 원장명 치유 회귀 `test_generate_content_heals_missing_approved_director_name_in_the_same_round`가 그대로 통과한다.

- [ ] **Step 5: 커밋** `git add backend/app/services/content_engine.py backend/tests/test_generation_deterministic_heals.py backend/tests/test_content_engine_must_use_verbatim.py && git commit -m "feat(generation): 빠진 병원명·승인 필수 문구는 작가를 다시 사지 않고 결정적으로 덧붙인다"`

**계약 영향:** CLAUDE.md 필수 문구 문단 "생성 후 `services/must_use_verbatim.py`가 확인하고, 빠지면 기존 재작성 루프·호출 예산 안에서 다시 쓰며 끝내 빠지면 `GENERATION_REJECTED`(본문 표본 실패)다."를 "생성 후 `services/must_use_verbatim.py`가 확인하고, 빠지면 승인 원문을 본문 끝에 한 문단씩 결정적으로 덧붙인다(작가를 다시 사지 않는다). 덧붙인 뒤에도 다른 게이트가 걸리면 그 사유로 재작성한다."로 바꾼다.

**위험·롤백:** 덧붙인 필수 문구·병원명 문장이 글 끝에 어색하게 놓일 수 있다(내용은 승인 자료 그대로). 프롬프트가 "본문 흐름 안에 넣으라"고 계속 요구하므로 치유는 예외 경로다. 저장 전 독립 검수가 최종 본문을 본다. 롤백: revert.

### Task 3-8: 본문 문장의 금지 표현은 그 문장을 통째로 지운다 (E2)

**Files:**
- Modify: `backend/app/services/content_engine.py:65-69`(import), `:1441-1465`(금지 표현 거절), Task 3-7의 레지스트리 앞(`_heal_forbidden_sentences`)
- Test: `backend/tests/test_generation_deterministic_heals.py`(4개 더)

**조사 결과:**
- 지금은 공개 필드 어디든 금지 표현이 하나라도 있으면 응답 전체를 버리고 재작성한다(`content_engine.py:1441-1465`, 주석 "문장 일부를 삭제·치환하면 문법과 의료 의미가 달라질 수 있으므로"). 금지 표현 거절은 재시도 분류상 본문 수리 코드이기도 해서(`generation_retry_policy.py:71-79`) 하루 2세션의 작가를 쓴다.
- 삭제 치유의 범위를 좁힌다: (1) 위반이 **본문에만** 있을 때(제목·메타·FAQ·참고자료 제목은 재작성), (2) 줄 단위로 `check_forbidden_markdown`이 걸린 줄을 문장(`(?<=[.?!。])\s+`)으로 나눠 걸린 문장만 뺀다, (3) 제목(`#`)·표(`|`) 줄이거나, 문장으로 나눠도 걸린 문장이 없거나(강조가 문장 경계를 넘는 우회 등), 지울 문장에 필수 문구가 들었거나, 지운 문장이 3개를 넘거나, **지운 뒤 본문이 분량 하한(1,800자) 아래면** 맡지 않는다. 마지막 조건은 프로토타입에서 실제로 필요했다 — 없으면 한 줄짜리 본문에서 그 줄을 지우고 병원명을 덧붙여 30자 글이 저장됐다(§2-7). 최소 교정 패스의 하한 규칙(`content_minimal_correction.py:836-842`)과 같은 선이다.
- 고쳐 쓰지 않고 지우기만 하므로 새 주장이 생기지 않는다. 결과는 `_validate_generated_result` 전체(금지 표현 재검사 포함)를 다시 지나고(Task 3-7 사슬), 저장 전에 독립 검수가 최종 본문을 다시 본다.
- 목록 표지(`- `, `1. `, `> `)는 남은 문장 앞에 그대로 둔다(`_HEAL_LINE_PREFIX`).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_generation_deterministic_heals.py`에 추가:

```python
async def test_a_forbidden_sentence_in_the_body_is_deleted_without_a_rewrite(monkeypatch):
    writer = _Writer([_payload(f"검사 전날 식단을 조절합니다. {AD_SENTENCE} 물을 충분히 드세요.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert AD_SENTENCE not in saved["body"]
    assert "검사 전날 식단을 조절합니다. 물을 충분히 드세요." in saved["body"]


async def test_a_forbidden_title_still_buys_a_complete_rewrite(monkeypatch):
    bad = _payload("검사 전날 식단을 조절합니다.")
    bad["title"] = "최고의 대장내시경 준비 안내"
    writer = _Writer([bad, _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2
    assert saved["title"] == "대장내시경 검사 전 준비 안내"


async def test_too_many_forbidden_sentences_are_not_deleted(monkeypatch):
    promotional = " ".join(
        [AD_SENTENCE, "부작용 없는 검사입니다.", "100% 안전합니다.", "국내 최초 장비입니다."]
    )
    writer = _Writer([_payload(promotional), _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2
    assert content_engine.FORBIDDEN_SENTENCE_HEAL_LIMIT == 3
    assert AD_SENTENCE not in saved["body"]


async def test_deletion_that_drops_the_body_below_the_floor_is_not_applied(monkeypatch):
    short = _payload("")
    short["body"] = "## 안내\n테스트의원 김의사 원장이 노원에서 안내합니다. " + AD_SENTENCE + "\n\n## 주의\n짧습니다."
    writer = _Writer([short, _payload("검사 전날 식단을 조절합니다.")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 2  # 하한 아래로 줄이는 삭제 대신 작가가 다시 썼다
    assert "Forbidden medical expressions" in writer.calls[1]["messages"][1]["content"]
    assert AD_SENTENCE not in saved["body"]
```

레지스트리 테스트도 여기서 더한다:

```python
def test_heal_registry_covers_every_deterministic_error():
    assert set(content_engine._DETERMINISTIC_HEALS) == {
        content_engine.DirectorNameMissingError,
        content_engine.HospitalNameMissingError,
        content_engine.ForbiddenExpressionError,
        content_engine.MissingMustUseMessagesError,
    }
    # 분류기는 종전 문구로 사유를 가른다 — 새 예외도 같은 메시지 머리를 쓴다.
    error = content_engine.ForbiddenExpressionError(
        "Forbidden medical expressions require complete regeneration: ['최고']",
        SimpleNamespace(),
        body_only=True,
    )
    assert isinstance(error, ValueError)
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py -q -k "forbidden or floor or registry"` Expected: `test_a_forbidden_sentence_in_the_body_is_deleted_without_a_rewrite`가 `assert 2 == 1`, 레지스트리 테스트가 집합 불일치(`ForbiddenExpressionError` 없음). 나머지 셋은 3-7까지만 적용한 상태에서 통과한다(재작성 경로 가드).

- [ ] **Step 3: 최소 구현**

`:65-69` import에 `check_forbidden_markdown,`을 더한다(`check_forbidden_content_fields` 다음).

`:1441-1465`의 주석과 `if violations:` 블록을:

```python
    # 금지 표현이 하나라도 있으면 이 결과는 이대로 저장하지 않는다. 본문 문장에만 있으면
    # 생성 루프가 그 문장을 통째로 지우는 결정적 치유를 먼저 한다(`_heal_forbidden_sentences`,
    # 문장 일부를 고쳐 쓰지 않는다 — 지우기만 하므로 새 주장이 생기지 않는다). 제목·메타·FAQ·
    # 참고자료 제목에 있거나 치유 뒤에도 남으면 직전 사유로 다시 쓰게 하고, 소진 시 호출자는
    # 결과를 저장하지 않고 기존 생성 실패 incident/outbox 경로를 연다.
```

```python
    if violations:
        outside_body = check_forbidden_content_fields(
            result, tuple(field for field in FORBIDDEN_CHECK_FIELDS if field != "body")
        ) or bool(reference_titles and check_forbidden_content_fields(
            {REFERENCE_TITLES_FIELD: reference_titles}, (REFERENCE_TITLES_FIELD,)
        ))
        logger.warning(
            "Forbidden expressions found labels=%s body_only=%s",
            ",".join(violations),
            not outside_body,
        )
        raise ForbiddenExpressionError(
            f"Forbidden medical expressions require complete regeneration: {violations}",
            result,
            body_only=not outside_body,
        )
```

로 바꾼다. Task 3-7의 `_DETERMINISTIC_HEALS` 정의 **앞**에:

```python
# 금지 표현 치유가 한 응답에서 지울 수 있는 문장 수. 이보다 많으면 글의 논조 자체가 광고라 다시 쓴다.
FORBIDDEN_SENTENCE_HEAL_LIMIT = 3
_HEAL_SENTENCE_SPLIT = re.compile(r"(?<=[.?!。])\s+")
_HEAL_LINE_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)?")


def _heal_forbidden_sentences(
    error: ForbiddenExpressionError, hospital: Hospital, *, must_use_messages: list[str]
) -> dict | None:
    """본문에서 금지 표현이 든 문장만 통째로 지운다. 고칠 수 없으면 None(재작성으로 간다).

    - 금지 표현이 제목·메타·FAQ·참고자료 제목에 있으면 맡지 않는다.
    - 제목(#)·표(|) 줄, 필수 문구가 든 문장, 문장 경계를 넘는 강조처럼 문장 하나로 짚을 수 없는
      위반은 맡지 않는다.
    - 지운 문장이 `FORBIDDEN_SENTENCE_HEAL_LIMIT`를 넘거나, 지운 뒤 본문이 분량 하한
      (`CONTENT_BODY_MIN_CHARS`) 아래면 맡지 않는다.
    문장을 고쳐 쓰지 않고 지우기만 하므로 새 사실·주장이 생기지 않는다. 결과는 같은 게이트 전체
    (`_validate_generated_result`)를 다시 지나고, 저장 전 독립 검수가 최종 본문을 다시 본다.
    """

    if not error.body_only:
        return None
    result = error.result
    must_use = [normalize_verbatim(message) for message in must_use_messages or []]
    kept_lines: list[str] = []
    removed = 0
    for line in str(result.get("body") or "").split("\n"):
        if not check_forbidden_markdown(line):
            kept_lines.append(line)
            continue
        if line.lstrip().startswith(("#", "|")):
            return None
        prefix = _HEAL_LINE_PREFIX.match(line).group(0)
        sentences = _HEAL_SENTENCE_SPLIT.split(line[len(prefix):])
        kept = [sentence for sentence in sentences if not check_forbidden_markdown(sentence)]
        dropped = [sentence for sentence in sentences if check_forbidden_markdown(sentence)]
        if not dropped:
            return None  # 위반이 문장 경계를 넘는다 — 문장 하나로 짚을 수 없다
        if any(
            key and key in normalize_verbatim(sentence) for sentence in dropped for key in must_use
        ):
            return None
        removed += len(dropped)
        if kept:
            kept_lines.append(prefix + " ".join(kept))
    if removed == 0 or removed > FORBIDDEN_SENTENCE_HEAL_LIMIT:
        return None
    healed_body = "\n".join(kept_lines)
    if body_plain_length(healed_body) < CONTENT_BODY_MIN_CHARS:
        # 지우고 나니 분량 하한 아래다 — 최소 교정 패스와 같은 선이다. 작가가 다시 쓴다.
        return None
    logger.info("Deleted %d sentence(s) carrying forbidden expressions", removed)
    result["body"] = healed_body
    return result
```

(`normalize_verbatim`은 `content_engine.py:35-40`에서 이미 import한다. `body_plain_length`·`CONTENT_BODY_MIN_CHARS`는 같은 모듈에 있다.) 그리고 Task 3-7의 `_DETERMINISTIC_HEALS`에 `    ForbiddenExpressionError: _heal_forbidden_sentences,`를 넣는다(`HospitalNameMissingError` 줄 다음).

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py tests/test_content_engine.py tests/test_medical_filter.py -q` Expected: 전부 passed. 특히 기존 `test_a_rewrite_round_carries_every_deterministic_rejection_so_far`(2회차 본문이 한 줄짜리 금지 표현 문장)가 하한 규칙 덕에 종전처럼 3회 작가 호출로 통과하고, `test_forbidden_response_is_discarded_and_second_complete_content_is_returned`(제목 금지 표현)도 재작성으로 통과한다.

- [ ] **Step 5: 커밋** `git add backend/app/services/content_engine.py backend/tests/test_generation_deterministic_heals.py && git commit -m "feat(generation): 본문 문장에만 든 금지 표현은 그 문장을 지워 같은 회차에서 고친다"`

**계약 영향:** CLAUDE.md 의료광고 문단에 "본문 문장에만 든 금지 표현은 생성 단계가 그 문장을 통째로 지우는 결정적 치유를 먼저 한다(문장 3개 이하·분량 하한 유지·필수 문구 문장 제외). 제목·메타·FAQ·참고자료 제목에 있으면 종전대로 재작성한다. 발행·공개 게이트는 그대로 모든 필드를 검사한다."를 더한다.

**위험·롤백:** 문장을 지워 문단 흐름이 끊길 수 있다 — 독립 검수가 최종 본문을 본다(문체 지적은 SOFT). 의료 의미가 바뀌는 경우(예: "~지만 완치를 보장하지 않습니다"의 앞 문장만 남음)는 걸린 문장을 통째로 지우므로 부정·한정 문장은 남지 않는다. 롤백: revert(재작성 경로로 돌아간다).

### Task 3-9: 검사 이름 뒤 낱말 '치료' 제목은 결정적으로 고친다 (E2)

**Files:**
- Modify: `backend/app/services/question_wellformed.py:55` 앞(새 함수)
- Modify: `backend/app/services/content_engine.py:42`(import), `:1316`(응답 추출 직후)
- Test: `backend/tests/test_generation_deterministic_heals.py`(2개 더)

**조사 결과:**
- 검사 이름에 '치료'가 붙은 제목("갑상선초음파 치료 비용")은 작가 보완 재작성 1회 뒤에도 남으면 `ValueError`로 **이미 결제한 후보를 버린다**(`content_generation_review.py:207-215`). 판정은 `question_wellformed.treatment_applied_to_exam`(`:46-52`, 토큰 단위 "검사어 다음 토큰이 '치료'로 시작").
- 결정적으로 고칠 수 있는 것은 **'치료'가 낱말로 떨어진 꼴**뿐이다("…초음파 치료 비용" → "…초음파 비용"). '치료가'·'치료비'·'치료법'처럼 조사·어미가 붙으면 지울 때 문장이 깨지므로 종전대로 작가 보완에 맡긴다.
- 고치는 위치는 **참고자료 주제 대조 전**(응답 추출 직후, `content_engine.py:1316`)이다. 참고자료 검증 기록의 주제 지문(`topic_fingerprint`)은 제목을 포함하므로(`reference_verification.article_topic_terms`), 대조 뒤에 제목을 바꾸면 발행 때 지문이 달라 GET 재검증이 한 번 더 일어난다.
- 고친 제목도 같은 게이트를 지난다(SEO 제목 검사는 짧아질 뿐, 금지 표현 검사는 그 뒤에 돈다).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_generation_deterministic_heals.py`에 추가:

```python
async def test_an_exam_title_with_a_standalone_treatment_word_is_fixed_before_review(monkeypatch):
    payload = _payload("검사 전날 식단을 조절합니다.")
    payload["title"] = "대장내시경 치료 전 준비 안내"
    writer = _Writer([payload])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert len(writer.calls) == 1
    assert saved["title"] == "대장내시경 전 준비 안내"


@pytest.mark.parametrize(
    ("title", "fixed"),
    [
        ("갑상선초음파 치료 비용, 얼마나 드나요?", "갑상선초음파 비용, 얼마나 드나요?"),
        ("대장내시경 치료 전 준비 안내", "대장내시경 전 준비 안내"),
        ("심장 MRI 치료 안내", "심장 MRI 안내"),
        # 조사·어미가 붙은 꼴은 결정적으로 고치지 않는다(작가 보완 재작성 몫).
        ("위내시경 치료가 필요한 경우", None),
        ("갑상선초음파 치료비 안내", None),
        # 시술은 치료와 자연스럽게 붙는다 — 고칠 것이 없다.
        ("임플란트 치료 비용 안내", None),
        ("허리 디스크 치료 방법", None),
    ],
)
def test_drop_treatment_after_exam_only_touches_a_standalone_word(title, fixed):
    assert drop_treatment_after_exam(title) == fixed
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py -q -k "exam"` Expected: 수집 ERROR `ImportError: cannot import name 'drop_treatment_after_exam'`.

- [ ] **Step 3: 최소 구현**

`question_wellformed.py:55` `def question_problems(` 바로 위에:

```python
# 검사 이름 뒤에 **낱말로 떨어져** 붙은 '치료'("갑상선초음파 치료 비용"). '치료가'·'치료비'처럼
# 조사·어미가 붙은 꼴은 지우면 문장이 깨지므로 결정적으로 고치지 않는다(작가 보완 재작성 몫).
_EXAM_STANDALONE_TREATMENT_RE = re.compile(
    r"(?P<exam>[0-9A-Za-z가-힣]*(?:내시경|초음파|촬영|조영술|검사|검진|골밀도|심전도|"
    r"[Cc][Tt]|[Mm][Rr][Ii]|엑스레이))\s+치료(?=\s|$|[,.?!·:—\-~)])"
)


def drop_treatment_after_exam(text: object) -> str | None:
    """검사 이름 바로 뒤의 낱말 '치료'를 지운 제목. 고칠 수 없거나 고칠 것이 없으면 None.

    "갑상선초음파 치료 비용, 얼마나 드나요?" → "갑상선초음파 비용, 얼마나 드나요?". 지운 뒤에도
    `treatment_applied_to_exam`이 참이면(다른 꼴이 남았다) None이다.
    """

    value = str(text or "")
    if not treatment_applied_to_exam(value):
        return None
    fixed = re.sub(r"\s{2,}", " ", _EXAM_STANDALONE_TREATMENT_RE.sub(r"\g<exam>", value)).strip()
    if fixed == value.strip() or treatment_applied_to_exam(fixed):
        return None
    return fixed
```

`content_engine.py:42` import를:

```python
from app.services.question_wellformed import (
    drop_treatment_after_exam,
    question_is_wellformed,
    treatment_applied_to_exam,
)
```

`:1316` `    result = _extract_generated_result(response)` 다음에:

```python
    # 검사 이름 뒤에 낱말로 붙은 '치료'("갑상선초음파 치료 비용")는 참고자료 주제 대조 전에
    # 결정적으로 지운다 — 보완 재작성·결제한 후보 폐기로 가지 않는다.
    fixed_title = drop_treatment_after_exam(result.get("title"))
    if fixed_title:
        result["title"] = fixed_title
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py tests/test_question_wellformed.py tests/test_content_engine.py -q` Expected: 전부 passed.

- [ ] **Step 5: 커밋** `git add backend/app/services/question_wellformed.py backend/app/services/content_engine.py backend/tests/test_generation_deterministic_heals.py && git commit -m "feat(generation): 검사 이름 뒤 낱말 '치료' 제목은 작가 응답 직후 결정적으로 지운다"`

**계약 영향:** CLAUDE.md 측정·리포트 문단의 "검사 이름에 '치료'가 붙은 제목은 보완 재작성 한 번 뒤에도 남으면 `GENERATION_REJECTED`(표본 실패)로 거절한다."를 "검사 이름 뒤에 낱말로 떨어진 '치료'는 작가 응답 직후 결정적으로 지우고, 조사·어미가 붙은 꼴만 보완 재작성 한 번 뒤에도 남으면 `GENERATION_REJECTED`(표본 실패)로 거절한다."로 바꾼다.

**위험·롤백:** "치료 전/후"처럼 '치료'가 다른 뜻으로 쓰인 제목에서 그 낱말이 빠진다("대장내시경 치료 전 준비" → "대장내시경 전 준비") — 검사에 '치료'를 붙인 것이 이미 비문이라 결과가 더 정확하다. 롤백: revert.

### Task 3-10: 확인된 필터 오탐 6종을 필터 안의 문맥 예외로 푼다 (E3)

**Files:**
- Modify: `backend/app/utils/medical_filter.py` — `_ContextExemption`(Task 3-1에서 더한 `unless_preceded_by` 옆에 `unless_followed_by`), `_HOSPITAL_SELF` 아래 상수 2개, `_ALLOWED_CONTEXTS`의 `"최고"`(`:153`)·`"완치"`(`:174`)·`"100%"`(`:185`)·`"최첨단"`(`:210`)·`"통증 없는"`(`:220`)에 예외 하나씩, 새 `"검증된"` 항목
- Test: `backend/tests/test_medical_filter.py`(끝에 2개 매개변수 테스트), 기존 `test_link_syntax_that_is_not_a_link_stays_visible_and_is_checked`의 예시 문장 1개 교체

**조사 결과(검토 §1 표와 `scratchpad/fp_probe.py`로 확인한 오탐):**
- "고혈압은 초기에 **통증 없이** 진행", "결절은 **통증 없는** 혹으로 발견" — `"통증 없는"` 패턴 `통증\s*없[는이]`(`:67`)이 무증상 질환 설명을 잡는다. 질환 글에 아주 흔하다. → 앞뒤 10자 안에 시술·진료·병원 낱말(`시술|수술|치료|진료|마취|주사|레이저|내시경|검사|교정|발치|임플란트|저희|본원|병원`)이 **없을 때만** 예외.
- "수술 후 **완전 회복**까지 3개월" — `"완치"` 패턴의 `완전\s*회복`(`:45`). → 바로 뒤가 "까지(되기까지·하기까지) [약/보통/대개/대략] N(~M) 일·주·개월·달·년"일 때만 예외. "완전 회복됩니다"·"완전 회복을 보장"은 계속 막는다. (검토 표의 "6~8주면 완전 회복"은 결과 단정에 가까워 예외로 두지 않는다 — 대표 지시 목록에도 "완전 회복까지 N개월"만 있다.)
- "증상이 **최고조**", "혈당이 **최고치**" — `"최고"` 패턴(`:32-35`). → 바로 뒤가 '조' 또는 '치'(단 '치료'는 아님)일 때 예외. '최고점'은 "만족도 최고점" 같은 광고가 있어 넣지 않는다.
- "비급여는 **100%** 본인부담" — `"100%"`(`:46`). → 바로 뒤가 본인·자기·환자 부담일 때 예외. 생성 단계는 이 문장을 여전히 가격 검사(`UNVERIFIED_PRICE_PATTERNS` 셋째 패턴, `content_engine.py:139-142`)로 막는다 — 이 예외는 발행·공개·PATCH의 오탐만 푼다.
- "임상시험에서 **확인된 효과**는 제한적" — `"검증된"`의 `확인[된]\s*효과`(`:54`). → 바로 앞(12자)이 임상·연구·시험·논문·문헌·진료 지침·학회(+에서/으로/를 통해/결과)이고, 병원 자기 말(`저희|본원|당원|우리 병원|병원에서|의원에서`)이 앞에 없을 때 예외.
- "**첨단 장비**로 정밀하게 촬영" — `"최첨단"`의 `첨단[의]?\s*(기술|장비|시술)`(`:64`). → '첨단 기술·장비'(시술 제외)가 앞뒤 20자 안에 병원 자기 말·병원·의원·시술·수술·치료와 함께 있지 않을 때 예외. '최첨단'은 정규식 대안 순서상 '최첨단'으로 잡혀 예외 트리거와 맞지 않으므로 계속 막힌다.
- CLAUDE.md 계약("문맥 예외는 필터 안에서만 정의하고 양방향 테스트를 함께 둔다. 경로별 허용 목록이나 우회를 만들지 않는다")을 그대로 따른다. 필터는 공개 가시성에도 쓰이므로 이 변경은 **숨김을 풀 수만 있고 새로 숨기지 않는다**.
- 기존 테스트 `test_link_syntax_that_is_not_a_link_stays_visible_and_is_checked`(`tests/test_medical_filter.py:217-226`)의 예시 "(통증 없는 경우도 있습니다)"는 이제 무증상 설명 예외라 걸리지 않는다. 테스트의 목적(링크가 아닌 괄호 텍스트도 검사된다)은 그대로 두고 예시를 계속 막히는 문형 "(통증 없이 시술하는 경우도 있습니다)"로 바꾼다.
- 작가 프롬프트 어휘(`FORBIDDEN_PATTERN_VARIANTS`의 "완전 회복"·"확인된 효과"·"첨단 장비")는 그대로 둔다 — 작가는 계속 피하고, 필터는 오탐만 덜 낸다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_medical_filter.py` 끝에:

```python
# ── E3(2026-10-13): 확인된 오탐을 필터 안의 문맥 예외로 푼다(양방향) ─────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "고혈압은 초기에 통증 없이 진행합니다.",
        "갑상선 결절은 대부분 통증 없는 혹으로 발견됩니다.",
        "수술 후 완전 회복까지 3개월이 걸립니다.",
        "완전 회복까지 약 6~8주가 필요합니다.",
        "증상이 최고조에 이릅니다.",
        "식후 혈당이 최고치를 기록합니다.",
        "비급여 항목은 100% 본인부담입니다.",
        "임상시험에서 확인된 효과는 아직 제한적입니다.",
        "첨단 장비로 정밀하게 촬영합니다.",
    ],
)
def test_confirmed_false_positives_are_not_medical_ads(text):
    assert check_forbidden(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("통증 없이 시술합니다.", "통증 없는"),
        ("시술 후 통증 없이 회복합니다.", "통증 없는"),
        ("저희 병원은 통증 없는 진료를 합니다.", "통증 없는"),
        ("수술 후 완전 회복됩니다.", "완치"),
        ("완전 회복을 보장합니다.", "완치"),
        ("최고치료법으로 진료합니다.", "최고"),
        ("100% 효과가 있습니다.", "100%"),
        ("저희 병원 임상에서 확인된 효과입니다.", "검증된"),
        ("확인된 효과로 통증을 줄입니다.", "검증된"),
        ("본원은 첨단 장비를 갖췄습니다.", "최첨단"),
        ("첨단 장비를 갖춘 본원에서 검사합니다.", "최첨단"),
        ("첨단 시술로 치료합니다.", "최첨단"),
    ],
)
def test_the_same_words_stay_blocked_as_claims(text, expected):
    assert expected in check_forbidden(text)
```

기존 `:222`의 `check_forbidden_markdown("회복 기간[개인차 있음](통증 없는 경우도 있습니다)")`를 다음으로 바꾼다:

```python
    # 예시는 계속 막히는 문형이어야 한다 — 무증상 설명("통증 없는 경우")은 E3 뒤로 예외다.
    assert check_forbidden_markdown("회복 기간[개인차 있음](통증 없이 시술하는 경우도 있습니다)") == [
        "통증 없는"
    ]
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_medical_filter.py -q -k "false_positives or stay_blocked_as_claims"` Expected: `test_confirmed_false_positives_are_not_medical_ads` 9건 FAILED(예: `assert ['통증 없는'] == []`). `test_the_same_words_stay_blocked_as_claims`는 main에서도 통과(가드). 기존 `test_advertising_phrasings_stay_blocked_after_the_exemptions`("첨단 기술로 시술합니다" 등)도 계속 통과해야 한다.

- [ ] **Step 3: 최소 구현** — `backend/app/utils/medical_filter.py`

`_ContextExemption`의 `unless_preceded_by` 필드 아래에 `unless_followed_by: re.Pattern | None = None`을 더하고(필드 주석을 "매치 앞·뒤 window 자 안에 이 표현이 **있으면** 예외가 아니다(시술·병원 자기 주장 문맥)."로), `applies`의 `unless_preceded_by` 검사 뒤에:

```python
        if self.unless_followed_by is not None:
            tail = text[match.end() : match.end() + self.window]
            if self.unless_followed_by.search(tail):
                return False
```

`_HOSPITAL_SELF` 정의 아래에:

```python
# 병원 자기 주장 또는 시술 결과 주장의 문맥. '첨단 장비'가 이 말 가까이 있으면 광고다.
_CLAIM_CONTEXT = re.compile(_HOSPITAL_SELF.pattern + r"|병원|의원|시술|수술|치료")
# 시술·진료 행위. '통증 없이'가 이 말 가까이 있으면 시술 결과를 보장하는 문장이다.
_PROCEDURE = re.compile(
    r"시술|수술|치료|진료|마취|주사|레이저|내시경|검사|교정|발치|임플란트|저희|본원|병원"
)
```

`_ALLOWED_CONTEXTS`에 각 예외를 **기존 항목의 맨 앞**에 넣는다:

```python
    "최고": (
        # "증상이 최고조에 이릅니다", "혈당이 최고치를 기록" — 정도의 정점이지 우위 주장이 아니다.
        # "최고치료"처럼 붙여 쓴 광고는 계속 막는다.
        _ContextExemption(
            reason="정점을 뜻하는 최고조·최고치",
            trigger=re.compile(r"최고"),
            followed_by=re.compile(r"^(?:조|치(?!료))"),
            window=2,
        ),
        # (기존 "수치 상한" 예외 그대로)
```

```python
    # "임상시험에서 확인된 효과는 제한적입니다" — 문헌·연구의 결과를 옮기는 문장. 병원이 스스로
    # 확인했다는 주장("저희 병원 임상에서 확인된 효과")은 계속 막는다.
    "검증된": (
        _ContextExemption(
            reason="연구·문헌 결과의 인용",
            trigger=re.compile(r"확인[된]\s*효과"),
            preceded_by=re.compile(
                r"(?:임상|연구|시험|논문|문헌|진료\s*지침|학회)(?:에서|으로|를\s*통해|결과)?\s*$"
            ),
            unless_preceded_by=_HOSPITAL_SELF,
            window=12,
        ),
    ),
```

(`"검증된"` 항목은 `"최고"` 항목 바로 다음, `"최우수"` 앞에 둔다.)

```python
    "완치": (
        # "수술 후 완전 회복까지 3개월이 걸립니다" — 회복 기간 안내. "완전 회복됩니다"·"완전 회복을
        # 보장" 같은 결과 단정은 계속 막는다.
        _ContextExemption(
            reason="완전 회복까지 걸리는 기간 안내",
            trigger=re.compile(r"완전\s*회복"),
            followed_by=re.compile(
                r"^\s*(?:까지|되기까지|하기까지)\s*(?:보통\s*|대개\s*|약\s*|대략\s*)?"
                r"\d+(?:\s*[~～-]\s*\d+)?\s*(?:일|주|개월|달|년)"
            ),
            window=24,
        ),
        # (기존 "완치 주장을 바로 부정·한정" 예외 그대로)
```

```python
    "100%": (
        # "비급여 항목은 100% 본인부담입니다" — 부담 비율 안내이지 효과·안전 보장이 아니다.
        _ContextExemption(
            reason="본인부담 비율 안내",
            followed_by=re.compile(r"^\s*(?:본인\s*부담|자기\s*부담|환자\s*부담|전액\s*본인)"),
            window=10,
        ),
        # (기존 "100% 주장을 바로 부정" 예외 그대로)
```

```python
    "최첨단": (
        # "첨단 장비로 정밀하게 촬영합니다" — 병원 자기 주장이 아닌 일반 설명. "최첨단", "첨단 시술",
        # 병원·시술을 가리키는 말이 가까이 있는 "본원은 첨단 장비를 갖췄습니다"·"첨단 기술로
        # 시술합니다"는 계속 막는다.
        _ContextExemption(
            reason="병원 자기 주장이 아닌 장비·기술 설명",
            trigger=re.compile(r"첨단[의]?\s*(?:기술|장비)"),
            unless_preceded_by=_CLAIM_CONTEXT,
            unless_followed_by=_CLAIM_CONTEXT,
            window=20,
        ),
        # (기존 "첨단 기술 주장을 부정" 예외 그대로)
```

```python
    "통증 없는": (
        # "고혈압은 초기에 통증 없이 진행합니다", "결절은 통증 없는 혹으로 발견됩니다" — 무증상
        # 질환 설명. 가까이에 시술·진료·병원을 가리키는 말이 있으면 결과 보장으로 보고 막는다.
        _ContextExemption(
            reason="무증상 질환의 경과 설명",
            trigger=re.compile(r"통증\s*없[는이]"),
            unless_preceded_by=_PROCEDURE,
            unless_followed_by=_PROCEDURE,
            window=10,
        ),
        # (기존 "환자의 의문·걱정 표현" 예외 그대로)
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_medical_filter.py tests/test_publish_block_auto_resolve.py tests/test_must_use_verbatim.py tests/test_content_compliance_faq.py -q` Expected: 전부 passed(프로토타입 test_medical_filter 106 passed).

- [ ] **Step 5: 커밋** `git add backend/app/utils/medical_filter.py backend/tests/test_medical_filter.py && git commit -m "fix(medical-filter): 무증상 경과·회복 기간·정점·부담률·연구 인용·장비 설명 오탐을 필터 안 문맥 예외로 푼다"`

**계약 영향:** CLAUDE.md 의료광고 문단의 "문맥 예외(역학 통계의 1위, 부정·한정이 바로 뒤따르는 완치·100%·성공률 등)는 필터 안에서만 정의하고 양방향 테스트를 함께 둔다." 괄호에 "·무증상 질환의 '통증 없이'·'완전 회복까지 N개월'·최고조/최고치·'100% 본인부담'·연구 인용의 '확인된 효과'·병원 주장이 아닌 '첨단 장비'"를 더한다.

**위험·롤백:** 예외가 넓으면 광고 문형이 새어 나간다 — 각 예외에 병원 자기 말·시술 낱말 근접 조건을 두고 양방향 테스트로 고정했다. 공개 가시성에서는 숨김이 풀리기만 한다. 롤백: revert.

### Task 3-11: 분량 "저장되지 않습니다" 문구를 SOFT 실제 동작에 맞춘다 (E3)

**Files:**
- Modify: `backend/app/services/content_engine.py:306-307`(`_BODY_LENGTH_TARGETS`), `:344-345`(도구 스키마 본문 설명), `:493`(`TYPE_PROMPT_BODY_LENGTH_RULE`), `:1750-1751`(`_build_remediation_context`)
- Test: `backend/tests/test_content_engine.py:1470`, `:1527`(문구 단언 2곳), `:1519-1520`(docstring), 끝에 1개

**조사 결과:**
- 분량 검사 `_validate_body_length`(`content_engine.py:1841-1854`)는 지적 목록만 돌려주는 SOFT다(테스트 `test_editorial_length_advice_does_not_buy_a_rewrite`가 "분량 조언은 재작성을 사지 않는다"를 고정). 그런데 프롬프트 4곳은 "1,800자 미만이거나 5,200자를 넘으면 저장되지 않습니다"라고 말한다. 실제로 하한을 지키는 것은 최소 교정(`content_minimal_correction.py:836-842`)과 Task 3-8의 삭제 치유뿐이다.
- 프롬프트의 분량 압력 자체는 유지한다("반드시 이 범위 안에서 쓰세요") — 2026-09 FAQ 미달(1,567~1,680자) 대응으로 넣은 절 단위 하한 문구(`_BODY_LENGTH_TARGETS` 셋째 줄)는 그대로다.
- FAQ 필드 문구 "비우거나 null로 두면 글 전체가 저장되지 않습니다"(`:515`)는 실제로 하드(`_validate_generated_result` `:1410-1411`)라 그대로 둔다(`test_faq_prompt_marks_the_json_ld_fields_as_required_output`가 고정).
- 정적 시스템 블록이 바뀌므로 배포 직후 프롬프트 캐시 접두어가 한 번 새로 만들어진다(Task 3-1과 같은 배포라 한 번으로 끝난다).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_content_engine.py` 끝에(`_build_remediation_context`·`_validate_body_length`는 파일 상단에서 이미 import):

```python
def test_length_wording_matches_the_soft_gate():
    """분량은 SOFT 지적이다(`_validate_body_length`가 목록만 돌려준다). 프롬프트가 "저장되지
    않는다"고 말하면 작가·운영자 모두 틀린 계약을 읽는다(2026-10-13 E3)."""

    surfaces = (
        content_engine.SYSTEM_PROMPT,
        content_engine.TYPE_PROMPT_BODY_LENGTH_RULE,
        content_engine.ARTICLE_TOOL["input_schema"]["properties"]["body"]["description"],
        _build_remediation_context(["지적"]),
    )
    for surface in surfaces:
        assert "분량 기준" in surface
        assert f"{content_engine.CONTENT_BODY_MIN_CHARS:,}자 미만은 저장되지 않" not in surface
        assert "넘으면 저장되지 않" not in surface
    body = "## 안내\n" + "짧은 본문입니다. " * 20
    assert _validate_body_length(body)  # 지적만 돌려주고 예외를 던지지 않는다
```

`:1470`과 `:1527`의 `assert f"{content_engine.CONTENT_BODY_MIN_CHARS:,}자 미만은 저장되지 않습니다" in context`를 `assert f"{content_engine.CONTENT_BODY_MIN_CHARS:,}자 미만은 분량 기준 미달로 기록됩니다" in context`로, `:1520` docstring "분량은 어떤 지적을 받았든 모든 회차가 지켜야 하는 저장 조건이다."를 "분량은 어떤 지적을 받았든 모든 회차가 지켜야 하는 품질 기준이다(SOFT — 저장은 막지 않는다)."로 바꾼다.

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_content_engine.py -q -k "length_wording or keeps_deletions or every_rewrite_round"` Expected: 3건 FAILED(`assert '분량 기준' in …`, `assert '1,800자 미만은 분량 기준 미달로 기록됩니다' in …`).

- [ ] **Step 3: 최소 구현** — `content_engine.py`

`:306-307`:

```python
    f"   순수 글자 수 {CONTENT_BODY_MIN_CHARS:,}자 미만이거나 "
    f"{CONTENT_BODY_MAX_CHARS:,}자를 넘으면 분량 기준을 벗어난 글로 기록됩니다 — "
    "반드시 이 범위 안에서 쓰세요.\n"
```

`:344-345`: `f"{CONTENT_BODY_MAX_CHARS:,}자를 넘으면 저장되지 않는다."` → `f"{CONTENT_BODY_MAX_CHARS:,}자를 넘으면 분량 기준을 벗어난 글로 기록된다."`

`:493`: `f"{CONTENT_BODY_MIN_CHARS:,}자 미만은 저장되지 않으므로 각 H2 절을 고르게 채우세요."` → `f"{CONTENT_BODY_MIN_CHARS:,}자 미만은 분량 기준 미달로 기록되므로 각 H2 절을 고르게 채우세요."`

`:1751`: `f"(순수 글자 수 {CONTENT_BODY_MIN_CHARS:,}자 미만은 저장되지 않습니다). 지적된 "` → `f"(순수 글자 수 {CONTENT_BODY_MIN_CHARS:,}자 미만은 분량 기준 미달로 기록됩니다). 지적된 "`

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_content_engine.py -q` Expected: 전부 passed(프로토타입 140 passed, DB·Redis env 필요 — §0).

- [ ] **Step 5: 커밋** `git add backend/app/services/content_engine.py backend/tests/test_content_engine.py && git commit -m "docs(prompt): 분량 문구를 실제 SOFT 동작에 맞춘다 — '저장되지 않습니다'를 '분량 기준 미달로 기록'으로"`

**계약 영향:** CLAUDE.md "현재 생성 분량 검사는 평문 1,800~5,200자다."에 "(SOFT 지적 — 저장은 막지 않는다. 최소 교정과 금지 표현 삭제 치유만 하한 아래로 줄이지 않는다)"를 더한다.

**위험·롤백:** "저장되지 않는다"는 압력이 사라져 작가가 짧게 쓸 수 있다 — 문구는 "반드시 이 범위 안에서"를 유지하고 절 단위 하한도 그대로다. 배포 뒤 1주 `seo_geo_findings`의 "본문 순수 글자 수 … (권고 1800자 이상)" 비율을 배포 전 1주와 비교한다(q 조회 없이 `essence_check_summary->'seo_geo_findings'` 문자열 검색). 롤백: revert.

### Task 3-12: 발행 재검증 신선도 24시간 → 7일, 장애 재사용 창 7일 → 14일 (E4)

**Files:**
- Modify: `backend/app/services/reference_verification.py:80-86`
- Test: `backend/tests/test_reference_verification.py`(끝에 1개, 기존 3곳 나이 상수), `backend/tests/test_reference_publication_gate.py:374`, `:396`(나이 상수), `backend/tests/test_reference_curated_alias.py:885`(나이 상수)

**조사 결과:**
- 발행 게이트는 같은 URL·같은 주제 지문의 **24시간 안** 통과를 요구한다(`REFERENCE_CHECK_MAX_AGE`, `:83`, 판정 `check_is_fresh_pass` `:964-976`). 23:00 D-2 생성분은 발행(D 08:00)까지 약 33시간이라 07:45에 반드시 GET을 다시 한다 — 기관 사이트 장애면 '미룸'이 되어 발행이 시간대마다 밀린다(`reference_publication.py` 머리말).
- 장애 재사용 창(`REFERENCE_CHECK_REUSE_WINDOW`, `:86`)이 7일이라, 신선도를 7일로 올리면 **신선하지 않은 판정은 언제나 재사용 창도 지나 있다** — 재사용 경로가 죽는다(§2-6). 재사용 창을 14일로 올려 "7일 넘은 판정 + 기관 장애 → 직전 통과 재사용"을 살린다. 기준은 마지막 실제 확인 시각(`verified_at`)이라 재사용을 거듭해도 14일을 넘지 않는다.
- 판(revision)에 묶인 승인 증거는 나이를 보지 않는다(`check_is_revision_bound_pass`, `:979-990`) — 이미 공개된 글에는 영향이 없다.
- 기존 테스트 중 "오래된 판정"을 `timedelta(hours=30)`·`timedelta(days=2)`·`timedelta(hours=33)`처럼 **24시간 기준의 하드코딩**으로 만든 곳이 있다. 7일 기준에서는 신선해져 테스트 의도(재검증·장애 재사용)가 깨진다. `REFERENCE_CHECK_MAX_AGE`에 대한 상대값으로 바꾼다: `test_reference_verification.py`의 `checked_at=NOW - timedelta(hours=30)` 3곳 → `NOW - REFERENCE_CHECK_MAX_AGE - timedelta(hours=6)`, `test_domain_outage_reuses_the_previous_pass_within_the_window`의 `NOW - timedelta(days=2)` 두 값 → `NOW - REFERENCE_CHECK_MAX_AGE - timedelta(days=1)`, `test_reference_publication_gate.py:374,:396`의 `age=timedelta(hours=33)` → `age=REFERENCE_CHECK_MAX_AGE + timedelta(hours=9)`, `test_reference_curated_alias.py:885`의 `_now() - timedelta(days=2)` → `_now() - REFERENCE_CHECK_MAX_AGE - timedelta(days=1)`(함수 안 import에 `REFERENCE_CHECK_MAX_AGE` 추가). 이미 `REFERENCE_CHECK_MAX_AGE + …`로 쓴 테스트(`test_reference_publication_gate.py:151`, `:186`, `:909`)는 그대로 통과한다.
- 변이 시험 목록(`tests/mutation/reference_guard_mutants.py:216`)이 가리키는 줄(`return _age_within(…, limit=REFERENCE_CHECK_MAX_AGE)`)은 바뀌지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_reference_verification.py` 끝에(이 파일은 `REFERENCE_CHECK_MAX_AGE`·`REFERENCE_CHECK_REUSE_WINDOW`·`reference_gate_status`·`KDCA_VIEW`·`HEMORRHOID_TOPIC`·`NOW`·`_pass_record`를 이미 import/정의한다):

```python
def test_publication_freshness_is_seven_days_and_reuse_outlives_it():
    """E4(2026-10-13): 생성 때 검증한 판정을 발행까지 그대로 쓴다. 장애 재사용은 그보다 길다."""

    url = KDCA_VIEW.format(310)
    references = [{"title": "치핵", "url": url}]
    six_days = [_pass_record(url, checked_at=NOW - timedelta(days=6, hours=23))]
    eight_days = [_pass_record(url, checked_at=NOW - timedelta(days=8))]

    assert REFERENCE_CHECK_MAX_AGE == timedelta(days=7)
    assert REFERENCE_CHECK_REUSE_WINDOW > REFERENCE_CHECK_MAX_AGE
    assert reference_gate_status(references, six_days, topic_terms=HEMORRHOID_TOPIC, now=NOW).current
    assert not reference_gate_status(
        references, eight_days, topic_terms=HEMORRHOID_TOPIC, now=NOW
    ).current
```

그리고 위 "조사 결과"의 나이 상수 5곳을 상대값으로 바꾼다(이 변경은 main에서도 통과한다 — 24시간보다 훨씬 오래된 값이 된다).

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_reference_verification.py::test_publication_freshness_is_seven_days_and_reuse_outlives_it -q` Expected: `assert datetime.timedelta(days=1) == datetime.timedelta(days=7)` FAILED.

- [ ] **Step 3: 최소 구현** — `reference_verification.py:80-86`을:

```python
# 발행 게이트가 받아들이는 통과 판정의 최대 나이(2026-10-13 E4: 24시간 → 7일). 생성 시 검증한
# 판정이 발행(보통 D-2 23:00 생성 → D 08:00)까지 그대로 쓰여 07:45·08:00에 GET·기관 장애 미룸이
# 생기지 않는다. 일주일 안에 문서가 사라지는 위험은 신뢰도 문제이며, 사후 검수·다음 편집 때 다시 본다.
REFERENCE_CHECK_MAX_AGE = timedelta(days=7)
# 도메인 장애 시 직전 통과 판정을 재사용할 수 있는 기간. 기준은 마지막 **실제** 확인 시각
# (`verified_at`)이라 재사용을 거듭해도 이 기간을 넘겨 늘어나지 않는다. 신선도(7일)보다 길어야
# 재사용이 뜻을 가진다 — 같으면 신선하지 않은 판정은 언제나 재사용 기간도 지나 있다.
REFERENCE_CHECK_REUSE_WINDOW = timedelta(days=14)
```

`reference_publication.py:5` 머리말의 "**신선한**(`REFERENCE_CHECK_MAX_AGE` 이내, …)" 문장은 상수 이름만 말하므로 그대로 둔다.

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_reference_verification.py tests/test_reference_publication_gate.py tests/test_reference_curated_alias.py tests/test_reference_operator_decides.py -q` Expected: 전부 passed(프로토타입 953 passed).

- [ ] **Step 5: 커밋** `git add backend/app/services/reference_verification.py backend/tests/test_reference_verification.py backend/tests/test_reference_publication_gate.py backend/tests/test_reference_curated_alias.py && git commit -m "feat(references): 발행 재검증 신선도를 7일로, 장애 재사용 창을 14일로 늘린다"`

**계약 영향:** CLAUDE.md 참고자료 문단의 "07:45·08:00·수동 발행은 같은 URL·같은 글 주제 지문(…)의 24시간 내 통과 기록을 요구해" → "7일 내 통과 기록", "기관 장애는 같은 URL·같은 주제의 7일 내 직전 통과를 재사용하고" → "14일 내 직전 통과". `docs/ops/`에 같은 숫자가 있으면 함께(`grep -rn "24시간 내 통과\|7일 내 직전" docs`로 확인).

**위험·롤백:** 일주일 사이 사라지거나 바뀐 문서가 참고자료로 공개될 수 있다(신뢰도 문제, 법적 문제 아님). 주제 지문이 바뀌면(제목·첫 H2·FAQ 편집) 나이와 무관하게 다시 검증한다. 롤백: 상수 되돌리기.

### Task 3-13: 결정적 거절 사유를 라벨로 남기고, 생성 세션의 거절·치유 횟수를 센다 (E5)

**Files:**
- Modify: `backend/app/workers/generation_retry_policy.py`(`AUTO_CORRECTION_EXHAUSTED_KEY` 다음 — Task 3-5의 `HARD_FINDING_IDLE_KEY` 뒤)
- Modify: `backend/app/workers/generation_run_control.py:33`(import), `:79-82`(`_REJECTION_MESSAGES`에 `exam_title`), `:214-236`(`_safe_rejection_message`), 새 `rejection_kind_for_message`
- Modify: `backend/app/workers/tasks.py:560`(import), `:1430-1431`(시도 기록)
- Modify: `backend/app/services/content_engine.py`(import, 거절 횟수 상수, 생성 루프)
- Modify: `backend/app/services/content_generation_review.py:17-20`(import), `:41` 앞(합치기 함수), `:87`, `:130-131`, `:269-270`
- Modify: `backend/app/services/content_publication.py:414-416`, `:469-471`(요약 보존 키)
- Test: `backend/tests/test_generation_deterministic_heals.py`(3개 더)

**조사 결과(§2-5):**
- 최종 실패의 원인 문구는 이미 남는다: 분류기 `_safe_rejection_message`(`generation_run_control.py:214-236`)가 원인별 운영자 문구를 고르고, 시도 기록 `message`(`tasks.py:1430-1431`)와 실패 OperationRun의 `safe_error_message`에 저장된다. 없는 것은 (a) 기계 라벨(문구는 바뀔 수 있다), (b) **치유되거나 재작성으로 넘어가 결국 성공한 회차의 거절**(가장 많은 경우인데 어디에도 남지 않는다), (c) 검사+'치료' 제목 거절(`content_generation_review.py:212-215`의 "Exam title with treatment wording …")이 기본 문구로 떨어지는 것.
- 라벨 판정은 한 곳(`generation_retry_policy.generation_rejection_kind`, 모델을 import하지 않는 잎 모듈)에 두고 분류기·생성 루프가 같이 쓴다. 순서가 판정이다 — 참고자료 거절 문구가 "GEO hard-fail: references is empty"라 `references`를 `geo`보다 먼저 본다(지금 분류기 순서 그대로).
- 횟수는 생성 루프가 회차마다 `rejected[kind]`, 치유로 끝나면 `healed[kind]`를 세어 결과 사전의 `_generation_rejections`에 싣고, 생성 검수 세션이 회차들을 합쳐 `essence_check_summary["generation_rejections"]`에 남긴다. 발행 게이트 기록(`apply_publication_assessment`)과 Essence 재검사(`apply_essence_revalidation`)는 보존 키 목록에 있는 것만 남기므로(`content_publication.py:410-433`, `:466-480`) 새 키를 그 목록에 더한다 — 빼면 발행 때 지워져 측정할 수 없다.
- 결과 사전은 작가 획득 원장(`_budgeted_generate`, `tasks.py:2420-2445`)에 캐시된다. 세션은 키를 `pop`하지 않고 `get`으로 읽는다(원장에 들어간 같은 사전을 바꾸지 않는다). 저장은 명시한 필드만 쓰므로(`tasks.py:5868-5890`) 남은 키는 행에 들어가지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_generation_deterministic_heals.py`에 추가:

```python
# ── E5(2026-10-13): 결정적 거절의 사유를 센다 ───────────────────────────────────────


async def test_rejections_and_heals_are_counted_by_kind(monkeypatch):
    bad_title = _payload("검사 전날 식단을 조절합니다.")
    bad_title["title"] = "최고의 대장내시경 준비 안내"
    writer = _Writer([bad_title, _payload(f"검사 전날 식단을 조절합니다. {AD_SENTENCE}")])
    _install_writer(monkeypatch, writer)

    saved = await _generate([])

    assert saved[content_engine.GENERATION_REJECTIONS_KEY] == {
        "rejected": {"forbidden": 2},
        "healed": {"forbidden": 1},
    }


@pytest.mark.parametrize(
    ("detail", "kind"),
    [
        ("Provider output was truncated before completion", "truncated"),
        ("Generated content contains an unverified fixed price or coverage claim: 3만원", "price"),
        ("GEO hard-fail: references is empty for FAQ", "references"),
        ("GEO hard-fail: 병원명 '테스트의원' body 미포함", "geo"),
        ("Forbidden medical expressions require complete regeneration: ['최고']", "forbidden"),
        ("FAQ output requires faq_question and faq_answer_summary", "faq"),
        ("Required must_use messages missing verbatim (1): ...", "must_use"),
        ("Exam title with treatment wording survived rewrite; regenerate the title", "exam_title"),
        ("something else", "other"),
    ],
)
def test_rejection_kind_matches_the_operator_message(detail, kind):
    from app.workers.generation_retry_policy import generation_rejection_kind
    from app.workers.generation_run_control import (
        classify_generation_failure,
        rejection_kind_for_message,
    )

    assert generation_rejection_kind(ValueError(detail)) == kind
    code, message = classify_generation_failure(ValueError(detail))
    assert code == "GENERATION_REJECTED"
    assert rejection_kind_for_message(message) == kind


def test_review_session_keeps_the_counts_in_the_saved_summary():
    from app.services.content_generation_review import (
        GENERATION_REJECTIONS_SUMMARY_KEY,
        _merge_rejection_counts,
    )

    total: dict = {}
    _merge_rejection_counts(total, {"rejected": {"forbidden": 1}, "healed": {"forbidden": 1}})
    _merge_rejection_counts(total, {"rejected": {"forbidden": 1, "price": 1}})
    _merge_rejection_counts(total, None)
    assert total == {"rejected": {"forbidden": 2, "price": 1}, "healed": {"forbidden": 1}}
    assert GENERATION_REJECTIONS_SUMMARY_KEY == "generation_rejections"
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py -q -k "counted or rejection_kind or saved_summary"` Expected: `AttributeError: … has no attribute 'GENERATION_REJECTIONS_KEY'`, `ImportError: cannot import name 'generation_rejection_kind'`, `ImportError: cannot import name 'GENERATION_REJECTIONS_SUMMARY_KEY'`.

- [ ] **Step 3: 최소 구현**

`generation_retry_policy.py` — `HARD_FINDING_IDLE_KEY` 정의 다음(`class GenerationRetryClass` 앞)에:

```python
# 결정적 생성 거절의 기계 라벨(2026-10-13 E5). 운영자 문구(`generation_run_control`)와 측정
# (`content_generation_review`의 `generation_rejections`)이 같은 판정을 쓴다. **순서가 판정이다** —
# 참고자료 거절 문구는 "GEO hard-fail: references is empty"라 `references`가 `geo`보다 먼저다.
GENERATION_REJECTION_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("truncated", ("truncated",)),
    ("price", ("unverified fixed price or coverage",)),
    ("references", ("references is empty",)),
    ("geo", ("geo hard-fail", "citable reference")),
    ("seo", ("seo hard-fail",)),
    ("forbidden", ("forbidden medical expressions",)),
    ("faq", ("faq output requires", "faq question must")),
    ("must_use", ("must_use messages missing verbatim",)),
    ("exam_title", ("exam title with treatment wording",)),
)
GENERATION_REJECTION_OTHER = "other"


def generation_rejection_kind(error: object) -> str:
    """결정적 거절 하나의 기계 라벨. 모르는 사유는 `other`다."""

    detail = str(error).casefold()
    for kind, markers in GENERATION_REJECTION_KINDS:
        if any(marker in detail for marker in markers):
            return kind
    return GENERATION_REJECTION_OTHER
```

`generation_run_control.py:33` `from app.workers.generation_retry_policy import (` 다음 줄들에 `GENERATION_REJECTION_OTHER,`·`generation_rejection_kind,`를 더한다. `_REJECTION_MESSAGES`의 `"must_use": (…),` 다음에:

```python
    "exam_title": (
        "검사 이름에 '치료'를 붙인 제목이 재작성 후에도 남았습니다. "
        "다음 자동 재시도가 제목을 다시 씁니다."
    ),
```

`_safe_rejection_message`(`:214-236`) 전체를:

```python
def _safe_rejection_message(error: ValueError) -> str:
    """Classify known hard gates without persisting provider or candidate text.

    판정은 `generation_retry_policy.generation_rejection_kind` 한 곳이다 — 잘린 응답을 가격·지역
    게이트 실패로 보여 주면 승인 자료를 고치라는 엉뚱한 조치로 이어진다.
    """

    return _REJECTION_MESSAGES.get(generation_rejection_kind(error), _DEFAULT_REJECTION_MESSAGE)


def rejection_kind_for_message(message: str | None) -> str:
    """저장된 운영자 문구에서 거절 라벨을 되짚는다(분류기가 만든 문구만 안다)."""

    for kind, safe_message in _REJECTION_MESSAGES.items():
        if message == safe_message:
            return kind
    return GENERATION_REJECTION_OTHER
```

`tasks.py:560` `    safe_generation_rejection_message,` 앞에 `    rejection_kind_for_message,`를 더하고, `:1430-1431`을:

```python
    if reason == "GENERATION_REJECTED":
        attempt["message"] = safe_generation_rejection_message(message)
        # 거절 사유의 기계 라벨(E5) — 금지 표현·참고자료·제목 등 몫을 숫자로 센다.
        attempt["rejection_kind"] = rejection_kind_for_message(attempt["message"])
```

`content_engine.py` — import 블록 끝(`from app.utils.medical_filter import (…)` 앞, ruff isort 순서)에 `from app.workers.generation_retry_policy import generation_rejection_kind`, `GENERATION_FAILURE_DETAIL_MAX_CHARS` 정의(`:1506`) 위에:

```python
# 생성 결과에 붙여 보내는 결정적 거절 횟수의 열쇠(E5). 생성 검수 세션이 꺼내 요약에 합친다.
GENERATION_REJECTIONS_KEY = "_generation_rejections"
```

`generate_content`의 `last_error: ValueError | None = None`(`:1561`) 다음에:

```python
    # 결정적 거절의 사유별 횟수(E5). 치유로 끝난 거절도 센다 — 금지 표현 필터가 작가 응답을 몇 번
    # 막았는지가 측정 대상이다.
    rejection_counts: dict[str, dict[str, int]] = {"rejected": {}, "healed": {}}

    def _count(bucket: str, error: BaseException) -> None:
        kind = generation_rejection_kind(error)
        rejection_counts[bucket][kind] = rejection_counts[bucket].get(kind, 0) + 1

    def _finish(generated: dict) -> dict:
        generated["_provider_http_attempts"] = int(attempt_context.get("http_attempt") or 0)
        if rejection_counts["rejected"]:
            generated[GENERATION_REJECTIONS_KEY] = {
                bucket: dict(counts) for bucket, counts in rejection_counts.items() if counts
            }
        return generated
```

그리고 루프 안에서: 정상 반환 `generated["_provider_http_attempts"] = …; return generated`를 `return _finish(generated)`로, `except MissingCitableReferencesError as exc:`의 첫 줄 `last_error = exc` 다음에 `_count("rejected", exc)`, 그 분기의 치유 성공 반환을 `_count("healed", exc); return _finish(healed)`로, Task 3-7의 사슬 분기 첫 줄에 `_count("rejected", exc)`, 치유 성공 반환을 `_count("healed", exc); return _finish(healed)`로, 마지막 `except ValueError as exc: last_error = exc` 다음에 `_count("rejected", exc)`를 넣는다.

`content_generation_review.py` — import에 `GENERATION_REJECTIONS_KEY,`(`EXAM_TREATMENT_TITLE_FINDING_PREFIX` 다음), `:41` `@dataclass … class ContentReviewDependencies` 앞에:

```python
# 요약(`essence_check_summary`)에 남기는 결정적 거절 횟수의 열쇠. 게이트 기록이 보존한다.
GENERATION_REJECTIONS_SUMMARY_KEY = "generation_rejections"


def _merge_rejection_counts(total: dict[str, dict[str, int]], counts: object) -> None:
    if not isinstance(counts, dict):
        return
    for bucket, kinds in counts.items():
        if not isinstance(kinds, dict):
            continue
        merged = total.setdefault(str(bucket), {})
        for kind, count in kinds.items():
            if isinstance(count, int) and count > 0:
                merged[str(kind)] = merged.get(str(kind), 0) + count
```

`:87` `last_generation_error: Exception | None = None` 다음에 `rejection_counts: dict[str, dict[str, int]] = {}`(주석 "세션 안 모든 작가 회차의 결정적 거절 횟수(E5, 사유별)."), `:130-131` 성공 회차의 `last_generation_error = None` 다음에 `_merge_rejection_counts(rejection_counts, candidate.get(GENERATION_REJECTIONS_KEY))`, `:269-270` `summary["hard_removal_rewrites"] = removal_rewrites` 블록 다음에:

```python
    if rejection_counts:
        summary[GENERATION_REJECTIONS_SUMMARY_KEY] = rejection_counts
```

`content_publication.py:414-416`과 `:469-471`의 보존 키 목록에서 `"reviewer_driven_rewrites",` 다음에 `"generation_rejections",`(첫 곳에는 주석 `# 생성 세션의 결정적 거절 횟수(E5) — 측정용 기록이다.`)를 더한다.

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_generation_deterministic_heals.py tests/test_generation_failure_classification.py tests/test_content_engine_must_use_verbatim.py tests/test_content_engine.py tests/test_tasks_nightly.py -q` Expected: 전부 passed. 운영자 문구 회귀(`test_must_use_rejection_has_its_own_operator_message`, `test_generation_failure_classification.py`)가 그대로 통과 — 분류 결과 문구는 `exam_title` 하나만 새로 생긴다.

- [ ] **Step 5: 커밋** `git add backend/app/workers/generation_retry_policy.py backend/app/workers/generation_run_control.py backend/app/workers/tasks.py backend/app/services/content_engine.py backend/app/services/content_generation_review.py backend/app/services/content_publication.py backend/tests/test_generation_deterministic_heals.py && git commit -m "feat(measure): 생성 거절 사유를 라벨로 남기고 세션별 거절·치유 횟수를 요약에 기록한다"`

**계약 영향:** 없음(측정 기록). CLAUDE.md "생성 당시 Essence와 최근 재검사 Essence를 분리한다" 근처에 한 줄을 더해도 좋다: "생성 세션의 결정적 거절·치유 횟수는 `generation_rejections`(사유 라벨별)로 남고 게이트 기록이 보존한다." (3-15에서 판단)

**위험·롤백:** `essence_check_summary`에 작은 사전 하나가 더해진다. 운영자 문구에 `exam_title`이 새로 생긴다(Task 3-9 뒤로 드물다). 롤백: revert — 남은 키는 옛 코드가 무시한다.

### Task 3-14: 운영 조회 q7(사후 검수 FLAGGED 분포)·q8(생성 거절 사유) (E5)

> **main 병합(2026-10-13) 뒤 이름:** main이 q7(배포 전 outbox 확인)·q8(Phase 2 롤백 점검)을 먼저 썼으므로 이 태스크의 조회는 **q9**(FLAGGED 분포)·**q10**(생성 거절 사유)로 옮겼다. 아래 본문의 q7·q8은 작성 당시 이름이다.

**Files:**
- Modify: `backend/app/utils/ops_sql_report.py:1`(머리말), `:138` 뒤(`QUERIES`에 q7·q8)
- Test: `backend/tests/test_ops_sql_report.py`(끝에 1개), `backend/tests/integration/test_ops_sql_report_postgres.py:19-21`(출력 이름 목록)

**조사 결과:**
- `ops_sql_report`는 이름 → SELECT 튜플이며 둘째 이후 문장은 `q7b`처럼 나온다(`:142-144` `_labels`). 통합 테스트가 이름 목록을 고정한다(`test_ops_sql_report_postgres.py:19-21`).
- 사후 검수 FLAGGED 표시 모양: `essence_check_summary->'post_publish_ai_review'` = `{"status": "FLAGGED", "structured_findings": [{severity, kind, target, quote?, message}], "correction": {...}?}`(`post_publish_ai_review.py:181-204`, 상수 `POST_PUBLISH_FLAGGED = "FLAGGED"` `post_publish_review_policy.py:86`). 검토가 대표 결정 전에 원한 "FLAGGED 53편의 심각도·종류 분포"(검토 부록)가 q7이다.
- q8 첫 문장은 Task 3-13의 `rejection_kind`(없으면 `unlabelled` — 배포 전 기록), 둘째는 `generation_rejections`의 사유별 합(최근 30일 예정 글).
- 로컬 Postgres(head)에서 통합 테스트로 두 쿼리가 오류 없이 도는 것을 확인했다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_ops_sql_report.py` 끝에:

```python
def test_gate_measurement_queries_are_registered_read_only_selects() -> None:
    """E5(2026-10-13): 사후 검수 FLAGGED 분포(q7)와 생성 거절 사유(q8)."""

    assert [len(ops_sql_report.QUERIES[name]) for name in ("q7", "q8")] == [2, 2]
    for name in ("q7", "q8"):
        for sql in ops_sql_report.QUERIES[name]:
            assert sql.strip().upper().startswith("SELECT")
    assert "structured_findings" in ops_sql_report.QUERIES["q7"][0]
    assert "rejection_kind" in ops_sql_report.QUERIES["q8"][0]
    assert "generation_rejections" in ops_sql_report.QUERIES["q8"][1]
    assert ops_sql_report.parse_args(["--only=q7", "--only=q8"]).only == ["q7", "q8"]
```

`tests/integration/test_ops_sql_report_postgres.py:19-21`의 목록을 `"q1", "q1b", "q2", "q3", "q3b", "q4", "q5", "q6", "q7", "q7b", "q8", "q8b"`로 바꾼다.

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_ops_sql_report.py -q` Expected: `KeyError: 'q7'`.

- [ ] **Step 3: 최소 구현** — `ops_sql_report.py` 머리말 "(Q1~Q6)"을 "(Q1~Q8)"로, `QUERIES`의 `"q6": (…),` 다음(사전 닫는 `}` 앞)에:

```python
    # 사후 검수 FLAGGED 공개 글의 지적 분포 — 유형 × 심각도 × 종류. 보조: 교정 결과 사유 분포.
    "q7": (
        """
        SELECT c.content_type,
               f.finding->>'severity' AS severity,
               f.finding->>'kind' AS kind,
               count(*) AS findings,
               count(DISTINCT c.id) AS items,
               count(*) FILTER (WHERE coalesce(f.finding->>'quote', '') <> '') AS quoted
        FROM content_items c
        CROSS JOIN LATERAL jsonb_array_elements(
          CASE WHEN jsonb_typeof(c.essence_check_summary->'post_publish_ai_review'
                                 ->'structured_findings') = 'array'
               THEN c.essence_check_summary->'post_publish_ai_review'->'structured_findings'
               ELSE '[]'::jsonb END
        ) AS f(finding)
        WHERE c.status = 'PUBLISHED'
          AND c.essence_check_summary->'post_publish_ai_review'->>'status' = 'FLAGGED'
        GROUP BY 1, 2, 3
        ORDER BY findings DESC, 1, 2, 3
        """,
        """
        SELECT c.content_type,
               c.essence_check_summary->'post_publish_ai_review'->'correction'->>'reason'
                 AS correction_reason,
               count(*) AS items
        FROM content_items c
        WHERE c.status = 'PUBLISHED'
          AND c.essence_check_summary->'post_publish_ai_review'->>'status' = 'FLAGGED'
        GROUP BY 1, 2
        ORDER BY items DESC, 1, 2
        """,
    ),
    # 생성 거절의 사유 분포(E5). 첫째: 최신 시도 기록이 GENERATION_REJECTED인 글의 사유 라벨.
    # 둘째: 최근 30일 예정 글의 생성 세션이 겪은 결정적 거절·치유 횟수(사유별 합).
    "q8": (
        """
        SELECT coalesce(essence_check_summary->'generation_attempt'->>'rejection_kind', 'unlabelled')
                 AS rejection_kind,
               essence_check_summary->'generation_attempt'->>'retry_class' AS retry_class,
               count(*) AS n
        FROM content_items
        WHERE essence_check_summary->'generation_attempt'->>'reason' = 'GENERATION_REJECTED'
        GROUP BY 1, 2
        ORDER BY n DESC, 1, 2
        """,
        """
        SELECT b.bucket, k.kind, sum(k.n::int) AS total, count(DISTINCT c.id) AS items
        FROM content_items c
        CROSS JOIN LATERAL jsonb_each(
          CASE WHEN jsonb_typeof(c.essence_check_summary->'generation_rejections') = 'object'
               THEN c.essence_check_summary->'generation_rejections' ELSE '{}'::jsonb END
        ) AS b(bucket, counts)
        CROSS JOIN LATERAL jsonb_each_text(
          CASE WHEN jsonb_typeof(b.counts) = 'object' THEN b.counts ELSE '{}'::jsonb END
        ) AS k(kind, n)
        WHERE c.scheduled_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - 30
        GROUP BY 1, 2
        ORDER BY 1, total DESC, 2
        """,
    ),
```

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests/test_ops_sql_report.py tests/integration/test_ops_sql_report_postgres.py -q` Expected: `12 passed`, 통합 `1 passed`(DB env 필요).

- [ ] **Step 5: 커밋** `git add backend/app/utils/ops_sql_report.py backend/tests/test_ops_sql_report.py backend/tests/integration/test_ops_sql_report_postgres.py && git commit -m "feat(ops): 사후 검수 FLAGGED 분포(q7)와 생성 거절 사유(q8) 운영 조회를 더한다"`

**계약 영향:** 없음.

**위험·롤백:** 읽기 전용 SELECT. `content_items` 전체를 JSONB로 펼친다 — 수천 행 규모라 migrate Job에서 수 초. 롤백: revert.

### Task 3-15: 게이트 카탈로그 판 올리기 · 배포 전후 확인 · CLAUDE.md

**Files:**
- Modify: `backend/app/workers/tasks.py:645`
- Modify: `CLAUDE.md`(아래 문장들), `docs/ops/deployment-runbook.md`(필터 변경 배포 항목 한 줄)
- Test: `backend/tests/test_gate_record_stale_context.py`(끝에 1개)

**조사 결과:**
- `GENERATION_GATE_CATALOG_VERSION`(`tasks.py:641-645`)은 "의료광고 필터·가격 규칙·수기 목록 선택·GEO 의미가 바뀌면 올린다 — 이미 거절된 슬롯이 배포 뒤 한 번 다시 평가받는다"는 기존 장치다. 지문(`context`, `:681-699`)에 들어가므로 판이 바뀌면 종착·같은 문맥 판정(`_terminal_attempt_is_unchanged`, `_generation_attempt_is_unchanged`)이 모두 "입력이 바뀌었다"가 된다. 2026-10-02 판 올리기 때의 회귀(게이트가 새 문맥으로 사람 몫을 굳힘)는 이미 고쳐져 있다(`test_gate_record_stale_context.py` 머리말).
- 이 계획의 D23·D24·E2·E3가 모두 결정적 수락 기준을 바꾸므로 한 번 올린다. 효과: D23 이전에 남은 진료비 `operator_decides` 보류(운영 3건)와 3일 소진된 `GENERATION_REJECTED`·교정 소진 HARD 슬롯이 7일 창 안이면 한 번씩 다시 시도된다. 비용 상한은 슬롯당 작가 세션 1회분(약 $0.30) × 창 안의 종착 슬롯 수(운영 q4로 배포 전 센다).
- `LEGACY_SPEND_UNKNOWN` 행도 문맥이 바뀌어 다시 집히지만 예산 판독에서 같은 예외로 끝난다(지출 없음, 실행 기록만). 그 23건의 처리는 Phase 0 후속(레거시 예산 재설정)의 몫이다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_gate_record_stale_context.py` 끝에:

```python
def test_phase3_gate_changes_bump_the_catalog():
    """2026-10-13 Phase 3(D23·D24·E2·E3)이 결정적 수락 기준을 바꿨다 — 이미 막힌 슬롯이 새 기준으로
    한 번 다시 평가받도록 지문의 카탈로그 판이 바뀌어야 한다."""

    assert tasks.GENERATION_GATE_CATALOG_VERSION == "2026-10-13.1"
```

- [ ] **Step 2: 실패 확인** Run: `.venv/bin/python -m pytest tests/test_gate_record_stale_context.py -q` Expected: `assert '2026-10-02.1' == '2026-10-13.1'`.

- [ ] **Step 3: 최소 구현** — `tasks.py:645` `GENERATION_GATE_CATALOG_VERSION = "2026-10-02.1"` → `"2026-10-13.1"`(배포일이 다르면 그 날짜로 바꾸고 테스트도 같이).

CLAUDE.md는 각 태스크의 "계약 영향"에 적은 문장을 이 커밋에서 한 번에 반영한다(문서 버전 2.21, 갱신일, 맨 위 "최신 운영 배포" 절은 배포 뒤에만). `docs/ops/deployment-runbook.md`의 배포 전 확인 목록에 "금지 표현 필터(`utils/medical_filter.py`)를 바꾼 배포는 새 이미지로 `python -m app.utils.forbidden_impact_scan`을 migrate Job으로 먼저 돌려, 숨겨질 공개 글·병원 문구가 0건이거나 대표가 확인한 목록일 때만 진행한다"를 더한다.

- [ ] **Step 4: 통과 확인** Run: `.venv/bin/python -m pytest tests -q --ignore=tests/mutation` (DB·Redis env) Expected: 프로토타입 기준 `8040 passed, 42 skipped`, 실패 12건은 모두 저장소 밖 파일(`site/`, `admin/`, `docker-compose`, `scripts/`)을 읽는 테스트로 **main 사본에서도 똑같이 실패**한다(스크래치 사본에 `backend/`만 있어서). 실제 저장소에서는 0 failed여야 한다. `make copy-guard`, `ruff check app tests`도 돌린다.

- [ ] **Step 5: 커밋** `git add backend/app/workers/tasks.py backend/tests/test_gate_record_stale_context.py CLAUDE.md docs/ops/deployment-runbook.md && git commit -m "chore(generation): Phase 3 게이트 변경으로 생성 게이트 카탈로그 판을 올리고 계약 문서를 맞춘다"`

**배포 전후 확인(운영, 읽기 전용):**
1. 배포 전, 새 이미지로 `gcloud run jobs execute reputation-migrate --wait --args=python,-m,app.utils.forbidden_impact_scan` → `summary`의 `할인·유인` 건수와 목록을 대표에게 보고. 0이 아니면 해당 공개 글·병원 문구를 사람이 고친 뒤 배포(또는 대표가 숨김을 수용).
2. 배포 전, `--args=python,-m,app.utils.ops_sql_report,--only=q4` → 재평가될 종착 슬롯 수(비용 상한 계산).
3. 배포 직후 첫 07:45·08:00 뒤, 진료비 보류 3건(10/6~10/7 예정)의 상태: 22:30 백로그 복구가 새 날짜를 주고 다음 발행기가 참고자료 0개로 발행하는지(사고 `MISSING_REFERENCES`가 `recover_generation_incidents`로 닫히는지).
4. 배포 1주 뒤 `--only=q10`으로 `forbidden` 거절·치유 몫, `--only=q9`로 FLAGGED 분포를 기록해 Phase 3 효과를 확인한다(검토 부록의 측정 공백 해소).

**위험·롤백:** 판 올리기는 종착 슬롯에 한 번의 유료 재시도를 준다(상한은 Step 2 조회로 미리 센다). 롤백: 판을 되돌리면 같은 슬롯이 또 한 번 재평가된다 — 되돌리지 말고 앞 태스크만 revert한다.

---

## 4. 요약

Phase 3는 15개 태스크다. D24(3-1·3-2)로 대표 범주 ③을 공통 필터의 하드 규칙으로 올려 생성·발행·공개·PATCH가 모두 막게 했고, 배포 전 영향 조회를 붙였다. E1(3-3~3-5)은 HARD와 함께 나온 인용 UNCERTAIN을 같은 교정·재작성에 싣고, 교정본 재검수에 직전 지적과 교정 전·후 문장을 넘기며, 아무것도 맡지 못한 HARD 행의 공회전을 멈춘다. D23(3-6)은 진료비·병원 선택 글의 참고자료를 선택 사항으로 바꿨다. 공개 가시성도 함께 고쳐야 했다. E2(3-7~3-9)는 병원명·필수 문구 덧붙이기, 금지 표현 문장 삭제, 검사+'치료' 제목 교정을 결정적 치유 사슬로 묶었다. 삭제 치유는 분량 하한을 지킨다. E3(3-10·3-11)는 오탐 6종을 필터 안 예외로 풀고 분량 문구를 실제 SOFT 동작에 맞췄다. E4(3-12)는 신선도를 7일로 올렸고, 재사용 창을 14일로 함께 늘렸다. E5(3-13·3-14)는 거절 사유 라벨·횟수와 q7·q8 조회를 더했다. 3-15는 카탈로그 판을 올리고 문서를 맞춘다. 모든 변경은 스크래치 사본에서 빨강→초록을 확인했다(전체 8040 passed). 감사·검토와 다른 점은 §2에 정리했다(E1-d 대부분 이미 해결, 표시용 금지어 목록 불변, 가시성 수정 필요, 재사용 창 사망 등).

## 5. 열린 질문(대표·법무)

- **Q1 (D24 어휘 확정, 법무):** `할인·유인` 패턴의 경계를 확인해 주십시오. 지금은 '무료 상담/시술/진료/검사/검진', '본인부담금 면제/할인/감면/대납/환급/0원'(조사 없는 광고 문형), '가장 저렴', '타 병원보다 … 저렴', '선착순·체험단·사은품·경품·페이백·특가·반값'을 막습니다. 반면 '무료 주차', 공적 검진의 '무료'(국가·공단·보건소 문맥), '본인부담금이 면제되는 경우'(의료급여 안내)는 허용합니다. 배포 전 영향 조회(3-2) 결과도 함께 판단해 주십시오.
  - 검토 반영(2026-10-13): 진료비 글에 흔한 제도·비교 문장은 막지 않게 좁혔습니다. 예외는 같은 문장의 **양성 근거**가 있을 때만입니다 — '본인부담금 면제/감면/환급/0원'은 상한제·산정특례·의료급여·차상위·건강보험·공단·국가·보건소·제도 중 하나가 같은 문장에 있거나 바로 뒤에 자격을 말하는 '대상'이 와야 하고('본인부담금 면제 대상입니다'는 허용, '… 대상으로 모십니다'·'저희 병원은 … 대상을 늘렸습니다'·예약 권유는 막음, 할인·대납은 늘 막음), 무료 검진·검사·상담은 공적 기관·제도 명사(국가·공단·보건소·건강검진·암검진·제도 등 — '대상'만으로는 근거가 아님)가 있거나 연령·주기와 함께 쓴 '무료 검진 대상'('만 40세 이상은 2년마다 무료 검진 대상입니다')일 때만 허용합니다. 같은 문장 안 어디든(앞뒤 방향·거리와 무관) 병원 자기 지칭·혜택을 주겠다는 말(해 드립니다·드립니다·혜택·이벤트·모십니다)·예약 권유·제도와 거리 두는 말이 있으면 막고(2회차 교차 검토 반영, 아래), '제공'은 병원 자기 지칭과 같은 문장일 때만 막습니다('보건소에서 무료 검사를 제공합니다'는 통과). '최저가 되는'(주격)·'비싸다'는 가격 주장이 아닙니다.
  - 교차 검토 반영(2026-10-13, 오케스트레이터 결정): '할인해 드립니다', '할인을 제공', '할인 적용', '무료로 상담해 드립니다'는 노골적 유인으로 보고 **HARD에 넣었습니다**(조사·활용형 포함, `할인(?:을|도|이)?\s*(?:해\s*드|제공|적용|받으)`, `무료(?:로)?\s*(?:(?:상담|검사|검진|진료|시술)(?:을|를)?\s*)?(?:해\s*드|드립니다|드려요|제공|이벤트)`). 할인·무료·면제와 같은 문장의 예약 권유('… 예약하세요/예약을 받습니다')도 막습니다('무료 주차'는 제외).
  - 같은 문장이 결과를 보장하면(보장·확실·반드시·무조건·틀림없·장담·약속·100%) 어떤 문맥 예외도 적용하지 않습니다 — "완전 회복까지 1주를 보장합니다"는 기준 커밋처럼 막힙니다.
  - 2회차 교차 검토 반영(2026-10-13): 같은 문장 검사(결과 보장·병원 자기 지칭·혜택·예약·공적 근거)는 200자·40자 창이 아니라 **문장 전체에서 방향과 무관하게** 봅니다(문장 경계는 `.?!…。`와 줄바꿈, 양쪽이 숫자인 소수점 제외). 보장 표지에 책임지고·100프로·백프로·100 퍼센트·완벽하게/완벽히를 더했고, 권고형 '반드시 …상담하세요'는 '반드시' 뒤 같은 문장에 결과 술어(낫다·사라지다·회복·완치·해결·호전)가 오면 보장으로 봅니다("반드시 낫습니다, 상담하세요"). 예약 권유는 '예약 바랍니다·예약을 권합니다·예약하시면·예약해 보세요·예약 신청·접수'와 '무료 상담을 받으세요'까지, 본인부담금은 조사형('본인부담금을 면제해 드립니다', '본인부담금은 전액 면제')까지 막습니다. 공적 제도 명사가 있어도 '아니어도·관계없이·별도로·옆에서·상품·고객·서비스'로 제도와 거리를 두는 문장은 근거가 되지 않습니다. 반대로 '지정 병원에서'(국가검진 지정 기관)는 병원 자기 지칭이 아니고, 보험·비용이 주어인 '보장·확실'과 일정 '약속'은 결과 보장이 아닙니다 — '국가 건강검진 대상자는 지정 병원에서 무료 검사를 받을 수 있습니다', '건강보험의 보장은 항목마다 다르며 비급여는 100% 본인부담입니다', '비급여는 100% 본인부담이 확실합니다', '비급여는 100% 본인부담이며 상담 약속을 잡으세요'는 통과합니다. **대표 정책 확인 필요:** 혜택·예약 문형은 공적 기관 문장이어도 HARD라 '보건소에서 무료 검사를 제공해 드립니다', '보건소는 무료 검사 예약을 받습니다', '보건소 무료 검사 대상 여부는 예약하세요가 아니라 공식 공고로 확인합니다'는 계속 막힙니다(테스트로 고정). 풀려면 이 정책을 바꾸는 결정이 먼저입니다.
- **Q2 (D23 "치유 유지"의 뜻):** 진료비·병원 선택 글에 참고자료가 있으면 실제 문서 검증과 수기 목록 문서 제외는 유지합니다. 다만 **비었을 때 수기 목록으로 채우는 치유**는 9/29 결정대로 계속 하지 않게 했습니다. 주제만 겹치는 질환 문서가 가짜 근거가 되기 때문입니다. 채우기를 원하시면 알려 주십시오.
- **Q3 (E1 범위):** 발행 전 교정에 UNCERTAIN을 싣는 것은 "모델이 HARD로 단정한 판정과 함께 나왔을 때"로 한정했습니다(문구 "co-occurring"). 공개 모드처럼 UNCERTAIN만 남은 차단에도 켜면, 상위 모델 재검수보다 문장 삭제가 먼저 일어납니다.
- **Q4 (E4):** 장애 재사용 창을 7일에서 14일로 함께 늘렸습니다. 그대로 두면 재사용 경로가 사라집니다(§2-6). 7일 유지를 원하시면 재사용 테스트를 지우는 쪽으로 바꿉니다.
- **Q5 (E3 '첨단 장비'):** 검토 반영(2026-10-13)으로 촬영·판독 과정을 설명하는 문장("첨단 장비로 정밀하게 촬영")만 허용하고, 병원·시술 낱말이 앞뒤 20자 안에 있으면 막습니다. 주어가 생략된 "첨단 장비를 갖추고 있습니다"는 막습니다. '통증 없이'도 같은 방식으로 질환·증상 주어가 바로 앞에 있을 때만 허용합니다. 병원 블로그 글에서는 촬영 문장도 장비 홍보로 읽힐 수 있습니다. 허용을 유지할지 결정해 주십시오.
- **Q6 (E1-d 필요성):** 3-5는 10/10 이후 측정(Step 0)에서 공회전이 하루 1건 미만이면 건너뛰는 것을 권합니다.
- **Q7 (후속 정리):** D23 뒤로 진료비 보류 경로(`operator_decides`, 운영자 요약 줄 #181, 관련 문구)는 새로 생기지 않습니다. 옛 기록이 모두 풀린 뒤(배포 +1주) 그 코드를 걷어내는 별도 작업을 제안합니다. 대상에는 읽는 곳이 없는 `PublicationReferenceRefresh.operator_decides` 필드(`reference_publication.py`)와 진료비 제목에서 닿지 않는 워커의 `MISSING_REFERENCES` + `REFERENCES_OPERATOR_DECIDES_CAUSE` 갈래(`tasks.py` 생성 실패 처리)가 포함됩니다 — 두 곳 모두 코드 주석으로 표시했습니다. 수기 목록으로 채우지 않는 판정(Q2)은 남깁니다.

## 6. 후속

2회차 교차 검토(2026-10-13)의 P3 항목과 이번에 의식적으로 하지 않은 것. 배포를 막지 않는다.

- **예외 정규식의 직접 실행 시간:** 문장 전체를 받는 같은 문장 검사는 위치마다 한 번만 시도되는 모양으로 바꿨다(`_earlier_in_sentence`·`_later_in_sentence`의 `[^.]*$` 꼬리 제거, 연령·주기 숫자에 토큰 경계·자릿수 상한, 문장·패턴마다 한 번만 검색). 매치 바로 앞뒤 창(`preceded_by`·`followed_by`, 최대 60자)에만 쓰는 인접 문형(`…\s*$`, `^\s*\d+…`)은 운영 경로에서 창 길이로 묶여 있어 그대로 두었다 — 16,000자 입력에 **직접** 실행하면 여전히 느린 모양이 있을 수 있다. 창 밖에서 재사용하게 되면 먼저 `{0,N}` 상한을 단다.
- **'100프로'·'백프로' 단독 표현:** 이번에는 결과 보장 표지로만 더했다. `100%` 라벨 자체(단독 '100프로 효과')로 막을지는 Q1(법무)과 함께 정한다.
- **권고형 '반드시'의 범위:** 결과 술어가 '반드시' **뒤** 같은 문장에 올 때만 보장으로 본다("증상이 사라지지 않으면 반드시 상담하세요"는 권고로 통과). 결과 술어가 앞에 있는 문장까지 막을지는 영향 조회 결과를 보고 정한다.
- **제도와 거리 두는 말의 오탐:** '서비스·고객·상품'은 공적 문장에도 쓰일 수 있다("보건소 서비스로 무료 검사를 받을 수 있습니다"는 막힌다). 배포 전 영향 조회에서 새로 막히는 공개 글이 이 말 때문이면 목록을 좁힌다.
- **대표 정책(Q1):** 보건소 문장의 '제공해 드립니다'·'예약을 받습니다'는 혜택·예약 HARD 정책상 막힌 채로 둔다.
- **삭제 치유의 남은 경계:** 들여쓴 코드 블록(4칸)은 따로 보호하지 않고(내용이 화면에 그대로 보이며 지우면 코드만 줄어든다), 목록 컨테이너 안 펜스의 닫힘은 목록 깊이를 추적하지 않고 올바른 닫는 줄에서만 닫는다(보호 쪽으로 기운다).
- **3회차 교차 검토 반영(2026-10-13):** 결과 술어에 치유·건강해지다·정상으로 돌아오다·재발 없이·문제없이를, 예약 권유에 예약하면·예약해 주십시오·예약 가능을, 거리 두는 말에 아니라도를 더했다('없어도'는 "증상이 없어도 국가 무료 검진을 받을 수 있습니다" 같은 공적 안내를 막아 넣지 않았다). '예약 가능'은 대표 정책(예약 문형 HARD)상 유지하지만, 공적 기관이 주어인 문장("국가검진은 지정 병원에서 무료 검사 예약 가능합니다", "보건소 무료 검사는 예약 가능합니다")도 막는다 — 영향 조회에서 새로 막히는 공개 글이 이 말 때문이면 공적 주어 문장만 푸는 예외를 검토한다. 보장 표지 뒤 부정 문형의 공백은 `\s{0,3}`으로 묶어 긴 공백 입력의 이차 시간을 없앴고, 삭제 치유는 여러 줄에 걸친 인라인 코드 스팬(같은 길이 백틱 짝)이 덮는 줄도 보호한다.

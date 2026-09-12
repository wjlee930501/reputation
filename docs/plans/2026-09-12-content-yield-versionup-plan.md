# 2026-09-12 콘텐츠 수율 버전업 계획 (v2.7)

문서 버전: 1.0 · 작성일: 2026-09-12 (Asia/Seoul)
소스 기준선: `ca48ee8` (main = PR #103 머지). 운영 배포본은 `a774851`(체크포인트 2)이며 그 뒤 31개 커밋이 미배포 상태다.
작업 브랜치: `claude/system-performance-review-x6vtn4`

## 1. 왜 글이 나오지 않는가

운영 7개 병원의 누적 발행 글은 2026-09-07 기준 115편이다. 계약은 병원당 월 12~20편이므로 수율은 계약의 절반 아래에 머문다. 원인은 특정 버그 하나가 아니라 설계의 전제가 어긋난 데 있다. 이 시스템은 모든 게이트를 fail-closed로 두고, 실패의 재시도 여부를 "입력이 바뀌었는가"로 판정한다. 그런데 글을 만드는 것은 LLM이고 LLM의 출력은 같은 입력에서도 매번 다르다. 그래서 확률적으로 한 번 실패한 슬롯이 "입력 변경 필요"로 분류되어 배포로 게이트 카탈로그 버전을 올릴 때까지 영구히 비어 있게 된다. 실제로 `GENERATION_GATE_CATALOG_VERSION`이 오늘(2026-09-12) 다시 올라갔다. 배포가 재시도 장치로 쓰이고 있다는 뜻이다.

진단은 네 갈래로 독립 감사했고 아래 사실은 코드로 재확인했다.

### 1.1 작성 단계에서 정상 글이 버려지는 지점

- 프롬프트가 참고자료를 비워 두라고 지시하고 검증기는 비어 있으면 버린다. `utils/authority_sources.py:397`은 "정확한 URL을 모르면 references를 비워 두세요. 시스템이 보완합니다"라고 말하지만 `content_engine._validate_geo`는 FAQ·DISEASE·TREATMENT·COLUMN·HEALTH·LOCAL 6개 유형에서 빈 참고자료를 hard-fail로 던진다. 보완 장치는 29개 URL의 수동 카탈로그이며 COLUMN·HEALTH 프롬프트는 참고자료를 언급조차 하지 않는다. 이 예외는 tenacity 재시도에서도 제외되어 있어 제공자 호출 1회로 끝난다.
- 분량 단위가 다르다. 프롬프트는 "2200~4200자"라고만 말하고 검증기는 공백과 마크다운 기호를 제거한 평문 1,800~5,200자를 잰다. 평문/원문 비율은 약 0.65~0.80이므로 프롬프트의 하한을 지킨 글이 검증기에서는 1,430~1,760자로 잡혀 버려진다.
- `max_tokens=5500`에 잘림 감지가 없다. 한국어 4,200자 본문과 JSON 봉투는 5,500 토큰을 넘기 쉽다. 잘린 응답은 JSON 파싱 실패가 되고, 같은 프롬프트로 3회 blind 재시도한 뒤 `GENERATION_REJECTED`로 기록되며 운영자에게는 "가격·지역·검색 구조 게이트"라는 틀린 원인이 표시된다. `essence_engine.py:498`은 `stop_reason`을 검사하는데 본문 경로만 빠져 있다.
- 금지 표현 필터가 일상 임상 문장을 잡는다. 실행으로 확인한 오탐: "사망원인 1위"(1등), "완치가 어렵고"(완치), "성공률은 개인차가 커 단정할 수 없습니다"(성공률), "부작용 없는 약은 없습니다", "정확도가 100%는 아닙니다". 프롬프트에 보여 주는 목록은 표시용 21개이고 실제 매칭 패턴은 그보다 넓어 작가는 자기가 무엇을 피해야 하는지 모른다. 외부 기관의 참고자료 제목도 같은 필터를 통과해야 하므로 우리가 주입한 큐레이션 출처 제목이 글 전체를 버리게 만들 수 있다.
- 독립 AI 검수(Haiku 4.5)의 확신도가 0.85 미만이면 UNCERTAIN finding이 자동으로 붙고 UNCERTAIN은 발행을 막는다. 재검수 경로는 없다. 메시지는 "자동 재검수가 필요합니다"라고 말하지만 코드는 그 글을 `CONTENT_AI_HARD_FINDING`으로 영구 차단한다. HARD finding도 재작성이 금지되어 있어 "근거 없는 주장을 삭제하라"는 가장 단순한 수정조차 시도되지 않는다. 검수자는 작가가 받은 Essence의 일부(treatment_narratives 등)를 받지 못해 승인된 병원 사실을 근거 없음으로 판정할 수 있다.
- 보완 재작성 예산 2회를 문체성 키워드 배치 지적이 먼저 소모한다.

### 1.2 재시도 정책이 슬롯을 죽이는 지점

- `GENERATION_REJECTED`, `CONTENT_IMAGE_POLICY_REJECTED`, `CONTENT_AI_HARD_FINDING`은 `INPUT_CHANGE_REQUIRED`다. `retry_is_due`는 이 클래스에 대해 무조건 False를 돌려준다. Essence나 질문 타깃이 바뀌지 않으면 재시도가 없다.
- 이미지 실패 예산 4회는 23·01·04·07 스윕 4회와 정확히 같다. 하룻밤 공급자 장애로 예산이 소진되면 `IMAGE_GENERATION_RETRIES_EXHAUSTED`가 되고 이 코드는 어느 재시도 클래스에도 없어 영구 종착이다. 일일 초기화는 `CONTENT_AI_REVIEW_UNAVAILABLE`에만 있다.
- 반대로 `_AUTOMATIC_BODY_REPAIR_CODES` 5개는 저장된 본문에 대해 시도 기록을 무조건 지우고 다시 작가를 부르므로, 결정적으로 고칠 수 없는 글이 하루 4회 유료 재생성을 무한 반복하며 공용 `content` 예산(일 250회)을 소진한다. 그 결과 정상 슬롯이 `COST_BLOCKED`가 된다.
- 22:30 지연 복구는 나이 조건 없이 오늘까지의 미완성 슬롯을 매일 미래 날짜로 옮긴다. 날짜는 시도 지문에 없으므로 재시도는 풀리지 않는데, 차단 run의 멱등 키와 Slack 요약 식별자에는 날짜가 들어가 있어 같은 글이 매일 새 FAILED run과 새 요약 줄을 만든다.

### 1.3 발행 게이트가 정상 본문을 막는 지점

- 대표 이미지는 발행의 절대 선행조건이다. 본문이 완벽해도 이미지가 없으면 `CONTENT_IMAGE_NOT_READY`로 막힌다. 이미지 대체·재사용·무이미지 발행 경로는 없다. 공개 사이트의 `ContentCover`는 이미지가 없을 때 유형 모티프를 그리도록 이미 설계되어 있다.
- 07:45 투영은 증상 코드를 보고한다. 이미지 예산이 소진된 글이 `CONTENT_IMAGE_NOT_READY`로 잡혀 RETRYING이 되고 "다음 배치가 다시 생성합니다"라는 문구가 붙지만 실제로는 아무것도 재시도하지 않는다.
- 자동 복구가 소유한 본문 수리 코드가 07:45에 OPEN 인시던트로 열려 `requires_operator_action=true`가 된다. 사람이 볼 필요 없는 일이 운영센터에 쌓인다.

### 1.4 Essence 단계

- 운영 배포본에는 새 자료 1건 도착이나 노트 노이즈 토글로 `current`가 사라지는 결함이 있다. main의 `8c59141`(stable base)이 이를 고쳤으나 미배포다.
- 자동 검수가 한 번 ESCALATE하면 그 초안에 복구 사이클 8을 박아 자동 재시도 대상에서 스스로 빠진다. 1회 보류가 영구 정지다.
- 필수 자료 1건이 ERROR면 병원 전체의 Essence가 만들어지지 않는다. ERROR는 재시도 없는 종착이다.
- 근거 노트 80건 초과 시 샤드별 검수에서 2번째 샤드부터 후보의 근거가 0개로 보여 거짓 보류가 난다.
- Essence 합성·검수가 야간 생성과 같은 `content` 비용 예산을 쓴다.

## 2. 버전업의 원칙

1. LLM 출력은 확률적이다. 작가·검수·이미지의 실패는 입력 문제가 아니라 표본 문제일 수 있으므로 유한하고 계수되는 재시도 예산을 준다. 무제한이 아니라 일 단위 초기화와 총 일수 상한을 둔다. H-08의 동일 입력 억제는 유지하되 그 위에 예산을 얹는다.
2. 게이트는 유지하고 게이트와 프롬프트의 불일치를 없앤다. `medical_filter`는 정본으로 남기고 우회를 만들지 않는다. 문맥상 광고가 아닌 부정·통계 문장만 좁게 예외로 둔다.
3. 본문과 이미지를 분리한다. 이미지는 발행을 하루 넘게 막을 수 없다. 인증되지 않은 이미지는 절대 공개하지 않되, 이미지가 없는 글은 공개할 수 있다.
4. 자동 복구가 소유한 상태는 사람의 일이 아니다. 예산이 소진된 뒤에만 원인별 1건으로 올린다.
5. 관측 가능해야 한다. 수율(계약 대비 발행)이 주간 요약 한 건에 숫자로 보이게 한다.

## 3. 작업 패키지

### WP-1 작성 엔진 정합화 (`content_engine.py`, `utils/authority_sources.py`)

- `max_tokens` 5500 → 12000. 호출 직후 `stop_reason in {"max_tokens","refusal"}`이면 잘림 전용 ValueError를 던지고, 보완 재작성 지시에 "분량을 줄이라"는 문구를 넣는다.
- 프롬프트의 분량 규칙을 검증기 단위로 고친다. "공백과 마크다운 기호를 제외한 순수 글자 수 기준 2,400~4,500자(최소 1,800, 최대 5,200)".
- 참고자료 지시 충돌 제거. `render_source_hint_block`의 "비워 두세요"를 "화이트리스트 도메인의 실제 문서 URL을 최소 1개 반드시 넣으세요. 확신이 없는 URL은 넣지 마세요"로 바꾸고 COLUMN·HEALTH 유형 프롬프트에 같은 요구를 추가한다. 생성과 발행의 `REFERENCES_REQUIRED_TYPES`는 그대로 같은 값을 쓴다.
- 프롬프트에 보여 주는 금지 표현을 표시용 목록이 아니라 실제 매칭 패턴이 잡는 어휘 전부로 렌더한다. "부정문·인용문·통계 문맥에서도 이 어휘 자체를 쓰지 말고 다른 표현으로 바꾸라"고 명시한다.
- 화이트리스트 도메인의 참고자료 제목은 금지 표현 검사에서 제외한다. 모델이 지은 제목(비화이트리스트)은 계속 검사한다. 발행 게이트 `content_publication.publication_field_values`도 같은 규칙을 쓴다.
- 결정적 검증기의 ValueError는 blind 재시도가 아니라 `remediation_findings`로 작가에게 전달한다. tenacity는 전송 오류에만 남긴다.
- `TARGET_STEERED_TYPES` 밖 유형(COLUMN·HEALTH·NOTICE)에는 `_validate_target_alignment`를 적용하지 않는다.
- `GENERATION_GATE_CATALOG_VERSION`을 올린다.

### WP-2 금지 표현 필터 오탐 축소 (`utils/medical_filter.py`)

정본 위치는 그대로다. 아래 문맥만 좁게 예외로 두고 양방향 테스트를 함께 넣는다.
- 1등: "1등급" 제외. "1위"는 앞 30자 안에 발생·사망·유병·발병·원인이 있는 통계 문맥만 제외. "국내 1위 병원"류는 계속 차단.
- 완치: "완치가 어렵/되지 않/보장할 수 없/을 의미하지 않"처럼 바로 뒤에 부정·한정이 오는 경우 제외. "완치율"과 "완치 가능/됩니다"는 계속 차단.
- 100%: "100%는 아니/100%가 아니/100%로 예방할 수 없"처럼 부정이 뒤따르는 경우 제외.
- 부작용 없는: "부작용 없는 약은 없"처럼 존재 부정으로 끝나는 경우 제외.
- 성공률: 뒤 20자 안에 "다릅니다/개인차/단정할 수 없/달라질 수" 같은 한정이 오는 경우 제외.
- 최고: 체온·용량·연령·회 등 수치 단위가 뒤따르는 경우 제외. 광고 최상급은 계속 차단.
- 탁월·첨단 기술: 단독 출현은 유지 차단하되 "기대하기 어렵/보장하지 않"이 뒤따르면 제외.
- 통증 없는: "아프지 않을까"처럼 의문형·걱정 표현은 제외.

### WP-3 독립 AI 검수 (`content_ai_review.py`)

- 확신도 부족만으로 생긴 합성 UNCERTAIN은 같은 호출 안에서 1회 재검수한다. 재검수는 `CLAUDE_MODEL`(Sonnet)로 승격해 수행하고 두 번째 판정이 PASS면 PASS다. 여전히 확신이 없으면 UNCERTAIN을 유지한다. HARD·UNCERTAIN이 발행을 막는 계약은 그대로다.
- `_PASS_CONFIDENCE`는 0.85에서 0.70으로 낮춘다. 모델이 명시한 HARD/UNCERTAIN finding은 확신도와 무관하게 그대로 막는다.
- SOFT 라벨에 HOSPITAL_FACT/MEDICAL_SAFETY kind가 붙은 finding을 UNCERTAIN으로 승격하는 규칙은 유지한다(안전 측). 대신 검수자 입력에 `treatment_narratives`, `content_principles`, `doctor_voice`, 승인된 병원 사실을 추가해 근거 부족 오판을 줄인다.
- 검수자 입력에 결정적 게이트가 이미 통과시킨 항목(가격·무료 예외, 화이트리스트 참고자료, 금지 표현 통과)을 명시해 재지적을 막는다.
- 모든 finding이 비차단이면 status는 PASS다.

### WP-4 재시도 정책과 야간 스윕 (`generation_retry_policy.py`, `tasks.py` 생성·07:45·08:00 구간, `generation_incident_control.py`, `content_backlog_recovery.py`)

- 새 재시도 클래스 `SAMPLE_RECOVERABLE`(확률적 실패): `GENERATION_REJECTED`, `CONTENT_IMAGE_POLICY_REJECTED`, `CONTENT_AI_HARD_FINDING`(UNCERTAIN 또는 SOFT만 있는 경우), `IMAGE_GENERATION_FAILED`, `CONTENT_IMAGE_NOT_READY/NOT_VERIFIED`. 예산은 KST 일 단위 초기화, 하루 최대 2회 작가 세션(이미지는 4회), 총 3일. 3일 소진 시 `OPERATOR_REQUIRED`로 전이하고 그때 원인별 인시던트 1건을 OPEN으로 올린다. 그 전에는 RETRYING이며 운영자 큐에 나타나지 않는다.
- `_generation_attempt_context`에서 날짜를 빼는 H-08 규칙은 유지한다. 예산은 지문 안에서 계수한다.
- `_AUTOMATIC_BODY_REPAIR_CODES`의 무조건 `_clear_generation_attempt`를 예산 계수로 바꾼다. `automatic_remediation_attempts`가 상한이면 fail-closed 분기로 보낸다.
- HARD finding 재작성: kind가 HOSPITAL_FACT/MEDICAL_SAFETY인 HARD finding에 대해 "지적된 주장을 삭제하거나 개인차·의료기관 확인으로 hedge하고 새 사실을 추가하지 말라"는 삭제형 재작성을 1회 허용한다. 재작성 후 반드시 독립 검수를 다시 받는다. 두 번째도 HARD면 종착.
- 보완 재작성 순서: Essence 스크린과 독립 검수를 먼저 통과시키고 키워드·계절 보완은 별도 1회 예산으로 뒤에 둔다.
- 이미지 정책 거절은 `policy_repair=True` 프롬프트로 1회 더 시도한다(`content_image_certification._replace_unsafe`가 이미 구현한 경로를 신규 DRAFT에 연결).
- 22:30 지연 복구의 생성 분기에 `scheduled_date < auto_publish_catchup_start(today)` 조건을 추가한다. 7일 catch-up 안의 슬롯은 옮기지 않는다.
- `_publication_block_details`가 `CONTENT_IMAGE_NOT_READY/NOT_VERIFIED`일 때 저장된 원인(`IMAGE_GENERATION_RETRIES_EXHAUSTED`, `CONTENT_IMAGE_POLICY_REJECTED`)을 대신 보고한다.
- 본문 수리 코드가 예산 안에 있으면 `_AUTOMATIC_RECOVERY_CODES`처럼 RETRYING(`sla_due_at=next sweep`)으로 열고, 소진 시 OPEN으로 바꾼다.
- 같은 글의 다른 원인 인시던트는 새 원인이 기록될 때 recover한다. `CONTENT_AI_REVIEW_CONFIG_ERROR`는 즉시 알림에 넣는다.
- 인시던트 딥링크 `/hospitals/{id}/essence`를 `/hospitals/{id}/info`(병원 정보 탭 실제 경로 확인 후)로 바꾼다.

### WP-5 이미지 실패 시 인증된 기존 이미지 재사용 (`content_publication.py`, `content_visibility.py`, 새 `content_image_reuse.py`, 새 워커 모듈, `celery_app.py`, 마이그레이션 0074)

대표 결정(2026-09-12): 이미지가 실패해도 글을 빈 이미지로 내보내지 않는다. 그 시점 기준 같은 병원에서 가장 예전에 인증된 이미지를 재사용해 발행하고, Slack으로 이미지 생성 크레딧·할당량 확인을 안내한다.

- 재사용 선택: 같은 병원의 PUBLISHED 글 중 인증이 현재이고 자기 자신이 재사용 이미지가 아닌 것 가운데 `image_policy_verified_at`이 가장 오래된 이미지를 고른다. 없으면(신규 병원) 종전처럼 `CONTENT_IMAGE_NOT_READY`로 막고 다음 스윕이 다시 시도한다.
- 재사용 기록: 대상 글에 원본의 `image_url`·`image_content_hash`·`image_subject_hash`·`image_policy_version`·`image_policy_verified_at`을 그대로 복사하고 새 컬럼 `image_reused_from_content_id`에 원본 글 ID를 남긴다. 새 제목에 맞춘 주제 hash를 만들어 넣지 않는다. 합성 인증값을 만들지 않기 위해 결합 대상은 원본 글이라고 명시적으로 기록한다.
- 인증 판정 `image_certification_current`: (a) 기존 완전 인증 또는 (b) 재사용 인증(`image_reused_from_content_id`가 있고 내용 hash가 URL과 일치하며 정책 버전이 현재) 둘 중 하나를 통과로 본다. 내용 hash 없는 이미지는 어떤 경우에도 통과하지 않는다. 공개 가시성도 같은 함수를 쓴다.
- 호출 지점: `_recover_missing_content_image`에서 이미지 생성이 실패하고 당일 이미지 예산이 소진됐거나 원인이 종착(예산 소진·정책 거절)일 때 재사용을 적용한다. 01·04·07 스윕의 저장 본문 분기에서도 같은 경로를 타서 예정일 07:45 전에 재사용이 끝난다.
- 교체 스윕: `image_reused_from_content_id`가 있는 PUBLISHED 글을 01:20·04:20·07:20에 최대 50건씩 골라 주제에 맞는 새 이미지를 생성·인증하고, 상태 PUBLISHED·제목·revision 일치 조건으로 저장한 뒤 마커를 지우고 Site 재검증을 건다. `content_revision`은 올리지 않는다. 예산은 WP-4의 이미지 예산과 공유한다.
- Slack: 08:00 발행 요약(배치당 1건) 안에 "대표 이미지 재사용 발행" 절을 추가한다. 병원명·건수·지배적 실패 분류(비용 가드 한도 / 공급자 한도·크레딧 오류 / 정책 검사 거절 / 공급자 오류)와 고정 안내 "이미지 생성 공급자 크레딧·할당량과 비용 가드 한도를 확인해 주세요. 새 이미지가 생성되면 재사용 이미지는 자동으로 교체됩니다."를 넣는다. 별도 메시지를 만들지 않는다. 재사용할 이미지가 없어 막힌 글의 요약 줄에도 같은 안내를 붙인다. 실패 분류는 시도 기록의 `image_failure_class`(COST_GUARD / PROVIDER_QUOTA / POLICY_REJECTED / PROVIDER_ERROR)로 저장한다.
- 계약 영향: "새 발행은 이미지 내용 hash·주제 hash·정책 버전에 연결된 인증을 요구한다"에서 주제 hash 결합이 재사용 인증에서는 원본 글 결합으로 대체된다. 이 예외는 마커가 있는 행에만 적용되며 CLAUDE.md에 명시한다.

### WP-6 Essence 자율 복구 (`essence_auto_review.py`, `essence_readiness.py`, `cost_guard.py`, `tasks.py` Essence 구간)

- ESCALATE 초안의 복구 마커를 8 고정 대신 `cycles+1`로 저장하고 `24h × 2^cycle` 백오프로 최대 4회 자동 재시도한다. 소진 시에만 ESCALATED 인시던트가 사람의 일이 된다. 사람이 손댄 초안(`reviewed_by`가 있거나 `updated_at != created_at`)에는 적용하지 않는다.
- ERROR 상태가 72시간 이상 지속된 필수 자료는 합성 입력에서 제외하고 `unsupported_gaps`에 남긴다. 자료 상태 자체는 바꾸지 않는다.
- 샤드 검수 프롬프트에 "shard_count>1이면 이 요청은 근거 일부만 본다. 이 범위에 근거가 없는 주장은 blocking이 아니라 advisory로 적는다"를 추가하고, 필터된 근거 ID를 `evidence_elsewhere`로 남긴다.
- `cost_guard` 카테고리에 `essence`를 추가하고 합성·검수가 그것을 쓴다. 기본 한도는 일 60회, 월 600회.
- `auto_review_essence_snapshot` 연속 실패는 `attempt_count`를 읽어 다음 claim을 지수 백오프한다.
- `_base_candidate_predicate`와 `_approved()`를 한 함수로 합친다.

### WP-7 수율 관측 (`tasks.py` 주간 롤업, `content_publish_notifications.py`, 새 `services/content_yield.py`)

- 병원별·주별 사실을 계산한다: 계약 예정 슬롯, 발행, 이미지 없이 발행, 차단(코드별), 재시도 중. `first_published_at` 기준.
- 월요일 주간 롤업 메시지 맨 위에 병원별 한 줄 "발행 n/예정 m"을 추가한다. 새 Slack 메시지를 만들지 않는다.
- 같은 사실을 `GET /admin/operations/content-yield`로 노출한다.

## 4. 보존하는 계약

- `medical_filter`는 정본이며 경로별 허용 목록이나 우회를 만들지 않는다.
- 미해결 HARD·UNCERTAIN finding은 발행을 막는다. UNAVAILABLE은 PASS를 주지 않는다.
- 생성·발행의 참고자료 필수 유형 집합은 같다.
- 이미지를 붙여 공개할 때는 byte-bound 인증이 필요하다. 합성 인증값을 만들지 않는다.
- DB PUBLISHED는 공개 성공이 아니다. 공개 API 필터는 유지한다.
- 정상 발행은 Slack에 알리지 않는다. outbox 실패는 도메인 트랜잭션을 되돌리지 않는다.
- `current`와 `public_philosophy`의 목적을 섞지 않는다.
- 재시도 예산은 업무별로 분리한다. 일률적 3회 규칙으로 바꾸지 않는다.
- `scheduled_date`는 시도 지문에 넣지 않는다.

## 5. 검증

- `make test-backend-local` 동등 환경(로컬 Postgres 5432/5434 UTF8, Redis)에서 전체 스위트, ruff, copy-guard, db-budget-guard.
- 새 테스트: 잘림 감지, 분량 단위, 참고자료 프롬프트, 필터 양방향 케이스, 재검수 승격, SAMPLE_RECOVERABLE 예산과 3일 소진, 22:30 나이 조건, 무이미지 발행 게이트와 공개 가시성, 사후 이미지 부착, Essence 백오프, essence 비용 카테고리, 수율 집계.
- 배포는 대표 확인 뒤 런북에 따라 진행한다. 이 브랜치의 변경은 미배포 31개 커밋 위에 쌓이므로 배포 시 함께 나간다.

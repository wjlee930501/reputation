# Phase 4 — 온보딩·Essence 자율화 계획 (2026-10-14)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development(권장) 또는 superpowers:executing-plans. 이 문서는 계획이다 — 코드 변경·배포·운영 데이터 변경의 증거가 아니다. 태스크는 §9의 단위로 fresh 서브에이전트에 넘기고, 태스크마다 스펙 준수 검토 + 코드 품질 검토를 거친다.

**Goal:** 계약 등록 뒤 병원이 사람의 확인 없이 (1) 공개 정보가 채워지고 (2) 프로필 완료·공개 활성화까지 가며 (3) 콘텐츠 운영 기준(Essence)이 승인 단계 없이 자동 요약·즉시 적용되어 콘텐츠 생성이 멈추지 않게 한다. 사람이 하는 일은 자동 수집이 못 채운 칸(원장 약력 등)과 원장 인터뷰 설문 업로드뿐이다.

**Architecture:** 자동 입력 초안을 "저장만"에서 "빈 칸 자동 적용 + 완료 재판정"으로 바꾸고(D30), 완료 술어를 줄이며(D31), 설문을 근거 자료 한 종류로 받고(D32), Essence 파이프라인에서 AI 검수·사람 승인·보류 사고를 걷어내 "합성 → 결정적 정리(근거 없는 주장·금지 표현·오염 입력 제거) → 즉시 적용"으로 만든다(D33). 사실 충돌은 고정 우선순위로 조용히 풀고 메타데이터에만 남긴다(D34). 글 단위 안전(의료광고 필터, 글마다 독립 AI 검수, 참고자료 게이트)은 그대로다.

**기준 소스:** `origin/main` `b06b9c42`(PR #251, DB head `0090_allow_fallback_treatment_narrative`). 로컬 main `558982c7`은 #251만큼 뒤에 있다 — 구현 브랜치는 `origin/main`에서 딴다. 아래 file:line은 모두 `b06b9c42` 기준이다.

**관련 문서:** [자율 운영 MasterPlan](2026-10-11-autonomy-masterplan.md)(D1~D29), [Phase 2 온보딩](2026-10-11-p2-onboarding.md)(2-3 Essence 자동 재검수, 2-4b 등록 직후 자동 입력 초안, 2-4c 게이트 축소 플래그), [Phase 3 콘텐츠 게이트](2026-10-13-p3-content-gates.md).

---

## 1. 결정 (2026-10-14 대표 결정)

| # | 결정(의도 그대로) |
|---|---|
| D30 | 자동 수집 프로필 값(네이버 플레이스·홈페이지 자동 입력 초안)은 **사람의 확인 없이 적용·공개**한다. 빈 칸만 채우고 마케터가 입력한 값은 절대 덮지 않는다. 적용 뒤 `profile_complete`를 자동 재판정한다. 기능 이전에 등록된 병원(예: 세종다온)도 초안을 만든다(백필·재시도). 자동 적용되는 모든 공개 텍스트에 의료광고 필터를 적용한다. |
| D31 | 공개 활성화 필수 항목: **진료 철학·Google URL만 선택**, 나머지(원장 약력 포함)는 필수. 자동 수집이 못 채운 칸은 마케터가 채운다. `hospital_lifecycle` 술어를 바꾸고 `PROFILE_GATE_OPTIONAL_ENRICHMENT` 플래그를 정리한다. Admin 문구가 선택 항목을 반영한다. |
| D32 | 계약 때 받는 **원장 인터뷰 설문**을 근거 자료로 올린다 — 링크와 문서 파일(PDF·DOCX·TXT, HWP는 별도 판단) 둘 다. 추출한 본문은 Essence 합성에서 원장 목소리·철학·진료 서술의 **최우선 근거**다. |
| D33 | Essence는 **승인 단계 없는 자동 내부 요약**이다 — 마케터 입력 프로필 + 연결된 홈페이지/블로그 + 설문에서 자동 합성·즉시 적용, 생성·발행은 사람·AI 검수자 승인 게이트 없이 쓴다. 근거 노트에 묶이지 않는 주장·금지 표현은 **버리고**(보류 아님), 프롬프트 주입·차단 페이지 잔재 입력은 **입력에서 뺀다**. Essence가 콘텐츠 생성을 멈추는 일은 없다. `ESSENCE_AUTO_REVIEW_*` 보류 사고는 이 경로에서 사라진다. 글 단위 안전은 유지. |
| D34 | 자료 간 사실 충돌(예: 진료시간 9:00 vs 10:00)은 **네이버 플레이스가 언제나 최우선**. 사실 필드와 서술 필드의 우선순위를 정하고, 충돌은 조용히 푼다(요약 메타데이터에 기록, 사고·Slack 없음). |

### 1.1 엔지니어링 기본값(이 계획이 정함, 반대 의견 없으면 진행)

- **E1 상태 enum 유지.** 자동 Essence도 `PhilosophyStatus.APPROVED` + `is_base=True`로 저장한다. "APPROVED"의 뜻을 "현재 적용 중인 판"으로 바꾸고, 만든 방식은 `created_by/reviewed_by = "system:essence-auto"`와 새 `synthesis_metadata.mode="AUTOMATIC"`으로 구분한다. 그래서 생성 게이트·발행 게이트·SQL 판 조정(`reconcile_content_revisions_0090.sql`)·`must_use_messages`·`EssenceReadiness`·공개 about 서사가 **코드 변경 없이** 그대로 동작한다(§3.4 표). 상태명 변경은 마이그레이션·SQL·롤백 위험만 늘린다.
- **E2 새 enum 값을 만들지 않는다.** 설문은 기존 `SourceType.INTERVIEW`(`models/essence.py:40`, Admin 라벨 "원장 인터뷰지")를 재사용하고 라벨만 "원장 인터뷰 설문"으로 바꾼다. 프로필 사실 자료는 `SourceType.INTERNAL_NOTE` + `source_metadata.system_kind="PROFILE_FACTS"` + `created_by="system:profile-facts"`로 표시한다. 이유: 새 enum 값을 넣으면 롤백한 옛 코드가 그 행을 읽다 `LookupError`로 자료 목록·Essence readiness 조회가 깨진다.
- **E3 RedBeat 버전을 올리지 않는다.** 새 주기 작업은 기존 15분 `reconcile-essence-snapshots`(`tasks.py:4706`) 틱에 붙인다(프로필 완료 승격·자동 입력 백필·PROFILE_FACTS 갱신).
- **E4 네이버 플레이스 신원 확인.** 지금은 병원명 검색의 첫 결과를 쓴다(`naver_place.py:79` `search_place_id`). 동명 병원 오적용을 막기 위해 (a) 병원 행의 `naver_place_id`/`naver_place_url`이 있으면 검색하지 않고 그 id를 쓰고, (b) 없으면 검색 결과의 상호가 병원명과 정규화 일치하고, 지역(`region`)이나 리드의 지역이 있으면 주소에 그 지역이 들어 있을 때만 "확인된 네이버"로 본다. 확인되지 않은 네이버 값은 초안에만 남기고 **자동 적용하지 않는다**(사고 없음).
- **E5 Essence에는 운영 사실(주소·전화·진료시간)을 싣지 않는다.** 이 값은 생성 때 병원 행에서 읽는다(`content_engine.py:667-676`). 그래서 사실 충돌(D34)은 병원 행 한 곳에서만 풀리고 Essence 서술과 어긋날 수 없다.
- **E6 설문 개인정보 가림.** INTERVIEW 본문은 저장 전 휴대전화·이메일·주민번호 형태를 `[가림]`으로 바꾼다. Essence의 `positioning_statement`·`patient_promise`는 공개 about 서사로 나가기 때문이다(`api/public/site.py:419-440`).
- **E7 판 교체가 공개 글을 숨기지 않게.** 자동 판 교체 때 이미 PUBLISHED인 글이 **병원별 avoid 문구 하나 때문에만** NEEDS_REVIEW가 되면 공개를 유지하고 사실만 `essence_check_summary.avoid_message_drift`에 남긴다. 의료광고 금지 표현·authority_change·오류 잔재 차단은 그대로 숨긴다(`content_visibility.py:105-125`의 `ESSENCE_NOT_ALIGNED`·`PHILOSOPHY_MISMATCH`).
- **E8 자동 판의 `must_use_messages`는 설문 원문 그대로인 문장만**(원문 발췌에 문자 그대로 있는 문장, 금지 표현 없음, 최대 3개). 그 밖의 합성 문구는 버린다. 필수 문구는 모든 글에 원문 그대로 들어가므로(CLAUDE.md 필수 문구 계약) 근거 없는 문구가 필수가 되면 안 된다. → §10 Q2에서 확인.

---

## 2. 현재 상태 (touchpoint, `b06b9c42`)

### 2.1 D30 자동 입력

| 위치 | 현재 |
|---|---|
| `backend/app/services/hospital_profile_autofill.py:1-9` | 불변식 "**자동 커밋하지 않는다**". 초안 + 필드별 출처/신뢰도 + 위반 표시만 반환 |
| `hospital_profile_autofill.py:50-68` | `_FORBIDDEN_TEXT_FIELDS=(director_career, director_philosophy)`, `_FORBIDDEN_LIST_FIELDS=(keywords,)`, `_ALLOWED_FIELDS` 15개 |
| `hospital_profile_autofill.py:152-198` | `_gather_sources` 홈페이지·블로그(직접 fetch→Jina 폴백) + 네이버(병원명 검색) 병렬 |
| `hospital_profile_autofill.py:202-269` | `_extract_with_claude` — 필드마다 출처 하나("여러 출처면 가장 신뢰 높은 것", 프롬프트 규칙 4) |
| `hospital_profile_autofill.py:276-371` | 발췌 근거 대조(`_field_value_is_grounded`·`_normalize_fields`) |
| `hospital_profile_autofill.py:382-405` | `_collect_violations` — 위반을 **표시만** 하고 지우지 않음 |
| `hospital_profile_autofill.py:408-452` | `autofill_profile` — 네이버 `place_id`/`canonical_url`을 초안에 덮어씀 |
| `backend/app/services/naver_place.py:79,107` | `search_place_id(name)` 첫 결과, `scrape_naver_place(name)` |
| `backend/app/workers/profile_autofill_tasks.py:34-95` | 등록 직후 1회. `idempotency_key="registration"` 실행이 있으면 `ALREADY_DONE`(빈 초안이어도), 병원 행에는 쓰지 않음, 감사 `stored_as_draft: True` |
| `backend/app/api/admin/handoffs.py:83-99, 674` | 등록 커밋 뒤 디스패치. 유실 회수 없음("편의 기능") |
| `backend/app/api/admin/hospitals.py:1343-1403` | `POST /profile/autofill` 미리보기(저장 안 함) |
| `hospitals.py:1406-1440` | `GET /profile/autofill-draft` 저장된 초안 + 리드 전화 제안 |
| `hospitals.py:926-1250` | `PATCH /profile` — 사람 경로만 `profile_complete`를 다시 판정. 지역 섞인 키워드 422(:943-967), 주소 지오코딩(:1052-1065), 완료 파생(:1126-1170), 인수 수락 차단(:1171-1178, `profile_completion_handoff_blocker` :437-446), 감사(:1180-1198), 채널 자료 등록(:1207, `_register_channel_sources` :527-606), 전환 시 `build_aeo_site`+V0(:1219-1245) |
| `admin/app/hospitals/[id]/info/page.tsx:271-307, 311-400, 438-450` | "자동 입력"(모달)·"저장된 초안 불러오기" 버튼 → 폼에 채운 뒤 사람이 저장 |
| `admin/app/hospitals/[id]/info/AutofillModal.tsx` | 수동 자동 입력 모달 |
| `backend/app/services/onboarding_stalls.py:79-84` | `PROFILE_INCOMPLETE` 정체 사고(인수 뒤 기한 경과·미완료) |

### 2.2 D31 완료 술어

| 위치 | 현재 |
|---|---|
| `backend/app/services/hospital_lifecycle.py:132-190` | `profile_requirements` 8개 키. `optional=PROFILE_GATE_OPTIONAL_ENRICHMENT`이면 약력·철학·Google을 뺌(:154) |
| `backend/app/core/config.py:704-706` | `PROFILE_GATE_OPTIONAL_ENRICHMENT: bool = False` |
| `.env.example:248`, `.env.production.example:226` | `PROFILE_GATE_OPTIONAL_ENRICHMENT=false` |
| `backend/tests/test_hospital_lifecycle_gate.py` | 플래그 두 모드 테스트 |
| `admin/lib/info-sections.ts:32-41` | 요구 키 → 섹션 앵커 |
| `admin/app/hospitals/[id]/info/FactsSection.tsx:296-303` | 진료 철학 입력(필수/선택 표시 없음) |
| `backend/app/services/readiness_operator_copy.py:25-28` | `local_entity` 문구가 Google 입력을 요구하는 투 |

### 2.3 D32 자료 시스템(이미 있는 것)

| 위치 | 현재 |
|---|---|
| `backend/app/models/essence.py:35-51, 128-191` | `SourceType.INTERVIEW` 있음. `HospitalSourceAsset`에 `url`·`raw_text`·`file_url`·`mime_type`·`source_metadata` |
| `backend/app/api/admin/essence.py:1060-1215` | `POST /essence/sources/upload` — PDF·DOCX만(UNKNOWN 422), GCS 저장(`store_asset_bytes`), 본문 추출 뒤 처리 자동 시작, 12MB 상한(:132) |
| `essence.py:1218-1241` | `POST /essence/sources/crawl` — URL fetch → raw_text |
| `essence.py:466-470` | 라벨 `INTERVIEW: "원장 인터뷰"` |
| `backend/app/services/asset_extractor.py:156-189` | `extract_pdf_text`(pypdf, 텍스트 PDF만), `extract_docx_text`(**문단만 — 표 안 글은 빠짐**) |
| `asset_extractor.py:193-256` | `fetch_url_text` — SSRF 검사, 리다이렉트 4회, `html`/`text` content-type만, 오류 페이지·깨진 인코딩 거절 |
| `asset_extractor.py:660-672` | `detect_extractor_for` IMAGE/PDF/DOCX/UNKNOWN (TXT·HWP·HWPX 없음) |
| `backend/app/services/essence_engine.py:222-247` | `process_source_asset` LLM 노트 추출 → 결정적 폴백 |
| `admin/lib/info-sections.ts:136-145` | `TEXT_SOURCE_TYPE_OPTIONS`(INTERVIEW "원장 인터뷰지") |
| `admin/app/hospitals/[id]/info/SourcesSection.tsx:380-470` | 문서 업로드 폼(accept PDF·DOCX) |

### 2.4 D33 Essence 승인에 기대는 곳 (전수)

| # | 위치 | 지금 하는 일 |
|---|---|---|
| A1 | `services/essence_auto_review.py:1539-2015` `refresh_essence_snapshot` | 기존 base가 있으면 drift 흡수(UP_TO_DATE, :1562-1577). 없거나 authority change면 합성 → 결정적 검사 → **독립 AI 검수(+재정)** → 통과 시 APPROVED(:1858-1912), 아니면 DRAFT 보류 + 복구 사이클(:1914-2003) |
| A2 | `essence_auto_review.py:395-416, 808-840` | 주입 의심·`conflict_notes` → 사람에게 즉시 보류. 근거 미연결·금지 표현·오류 잔재·필드 손실 → 보류 사유 |
| A3 | `essence_auto_review.py:1502-1536` `essence_refresh_needed` | base 있으면 False(재합성 안 함) |
| A4 | `workers/tasks.py:4253-4440` `auto_review_essence_snapshot` | `ESSENCE_AUTO_REVIEW_COST_BLOCKED`(:4300-4318), `_FAILED`(:4330-4350), `_ESCALATED`(:4376-4401), 회복(:4403-4419), 승인 직후 첫 일정(:4427-4431) |
| A5 | `tasks.py:4706-4738` `reconcile_essence_snapshots` 15분 | 자료 fetch·처리 재개 + 대상 병원 큐잉 + 첫 일정 |
| A6 | `tasks.py:630-636` `_generation_philosophy_sync` → `essence_readiness.current` | 생성 게이트. 없으면 `MISSING_APPROVED_ESSENCE`(:5982-6010, :7147-7163) |
| A7 | `services/essence_readiness.py:65-100` | `current=None if authority_refresh_required(approved)` — 근거 철회 시 생성 보류 |
| A8 | `essence_readiness.py:104-114` `base_candidate_predicate` | `status == APPROVED` |
| A9 | `services/content_publication.py:301-437` `assess_content_publication` → `essence_engine.screen_content_against_philosophy`(:1472-1536) | 발행 게이트: APPROVED 판이 없으면 `MISSING_APPROVED_ESSENCE`, 금지 표현·avoid 문구면 `ESSENCE_NOT_ALIGNED` |
| A10 | `essence_engine.py:1435-1470` `approved_philosophy_issues` | 판의 오류 잔재 검사 |
| A11 | `alembic/revision_sql/reconcile_content_revisions_0090.sql` | `LEFT JOIN ... p.status='APPROVED'`, `essence_status='ALIGNED'`, `content_philosophy_id`·`approved_source_snapshot_hash` NOT NULL, `source_snapshot.source_asset_ids` 비어 있지 않음, provenance evidence id 존재 |
| A12 | `services/must_use_verbatim.py:196-205` | 필수 문구는 "현재 APPROVED 승인본"만 |
| A13 | `services/content_visibility.py:105-125` | 공개 글: `PHILOSOPHY_MISMATCH`, `ESSENCE_NOT_ALIGNED` 숨김 |
| A14 | `api/public/site.py:419-440` | 공개 about 서사는 APPROVED 판의 positioning + promise(금지 표현 문장 폐기) |
| A15 | `workers/nightly_generation_batch.py:388-393` | APPROVED가 아닌 판으로 쓴 본문은 재생성 대상 |
| A16 | `services/initial_schedule.py:110-121`, `services/hospital_states.py:57-85` | 첫 일정·일정 가능 판정은 `readiness.current` |
| A17 | `api/admin/essence.py:1772, 1851, 1904, 1957-2075` | 판 PATCH·보관·재검수·**사람 승인** |
| A18 | `admin/app/hospitals/[id]/status/EscalatedDraftCard.tsx`, `ExceptionCards.tsx:20`, `admin/lib/essence-actions.ts` | 예외 초안 카드(편집·재검수·예외 승인) |
| A19 | `api/admin/hospital_overview.py:77, 172-200` | 현황 `essence_review` 예외 카드 |
| A20 | `services/readiness_operator_copy.py:33-47`, `hospitals.py:1782-1800` | readiness `essence_sources`·`essence_philosophy`·`essence_freshness` 문구("자동 승인", "예외 카드") |
| A21 | `services/incident_types.py:305` | `ESSENCE_AUTO_REVIEW_ESCALATED` 스윕 소유 |
| A22 | `services/notification_copy.py:46-55, 188-190`, `notification_messages.py:111` | ESCALATED·MUST_USE_EXCLUDED·COST_BLOCKED·FAILED 문구 |
| A23 | `services/content_publish_notifications.py:40-50, 109-150, 465`, `notification_labels.py:44`, `tasks.py:7572-7593`, `workers/generation_incident_control.py:113, 485, 710, 833, 1134, 1202, 1412` | `MISSING_APPROVED_ESSENCE` 아침 요약 Slack(Error 라벨) |
| A24 | `services/must_use_exclusions.py:33`, `workers/incident_backlog.py:650` | `ESSENCE_MUST_USE_EXCLUDED` 사고(승인본 문구에 금지 표현) |
| A25 | `services/onboarding_stalls.py:188-191` | `SCHEDULE_MISSING` 판정이 `current.approved_at`을 씀 |
| A26 | `utils/production_readiness.py:150-160` | readiness 사실 `null_noise_hash_approvals` |
| A27 | `services/essence_engine.py:910-990` | 합성 프롬프트에 노트 id·type·발췌 160자만, 자료 종류 없음. 병원 프로필 값은 입력 아님 |

### 2.5 D34

- 자동 입력 추출이 필드당 출처 하나만 고른다(`hospital_profile_autofill.py:79-80` 규칙 4). 우선순위 규칙이 코드에 없다.
- Essence 합성은 충돌을 `conflict_notes`로 남기고 그 자체가 보류 사유다(A2).
- 생성은 사실을 병원 행에서 읽는다(`content_engine.py:667-676`).

---

## 3. 설계

### 3.1 D30 — 자동 입력 자동 적용

**(a) "마케터가 입력한 값"의 판정 — 필드 출처 원장.** 새 컬럼 `hospitals.profile_field_provenance JSONB NOT NULL DEFAULT '{}'`(0091). 모양:

```json
{"business_hours": {"origin": "OPERATOR", "actor": "ae@…", "at": "2026-10-14T…"},
 "address": {"origin": "AUTOFILL", "source": "naver", "run_id": "…", "at": "…",
             "conflict": {"source": "naver", "value": "…", "observed_at": "…"}}}
```

- `PATCH /profile`(사람 경로)은 `changed_fields`마다 `origin=OPERATOR`를 쓴다(`hospitals.py:1180` 감사행과 같은 자리). `default_actor()`가 `system:`으로 시작하는 배치·CLI 호출은 OPERATOR로 쓰지 않는다. `physicians` 저장으로 파생된 `director_*`도 OPERATOR다.
- 판정 함수 `profile_provenance.is_operator_owned(hospital, field)`: 원장에 `OPERATOR`가 있으면 참. 값이 비어 있어도 참 — 사람이 일부러 지운 칸을 다시 채우지 않는다.
- 백필(§5.1): 원장이 비어 있는 기존 병원은 감사 로그 `action="update_profile"`의 사람 actor(`system:` 아님) `detail.changed_fields`에서 OPERATOR를 만든다. 감사 로그가 없는 옛 값(값은 있는데 출처 모름)은 `origin=LEGACY` — 덮지 않는다(값이 있으니 어차피 "빈 칸만" 규칙에 걸린다).

**(b) 적용 서비스 `services/profile_autofill_apply.py` (async, API·워커 공용).** 병원 advisory 잠금(`acquire_hospital_advisory_lock`, PATCH와 같은 잠금) 아래서 병원 행을 다시 읽고:

1. 대상 필드 = 초안 키 ∩ 적용 허용 목록(`_ALLOWED_FIELDS` + `naver_place_id`) 중 **현재 값이 비어 있고**(`None`·공백·`[]`·`{}`) `is_operator_owned`가 아닌 것. 예외: 원장이 `AUTOFILL`인 필드는 네이버 값이 바뀌면 갱신 가능(§3.5, 이번 범위에서는 후속 태스크 P4-18).
2. 의료광고 필터: 적용할 모든 문자열(리스트·`treatments[].name/description`·`director_*`·`keywords`·`competitors` 포함)에 `check_forbidden`. 걸린 단일 필드는 **적용하지 않는다**. `treatments`는 걸린 항목의 `description`만 비우고 이름은 이름이 통과할 때 유지, `keywords`·`specialties`·`region`은 걸린 원소만 뺀다. 버린 사실은 실행 요약 `dropped_fields`에 남긴다(사고 없음).
3. 키워드: PATCH의 지역 섞임 검사(`hospitals.py:943-967`)를 공용 함수로 빼서 걸린 키워드는 빼고 적용(422 대신 제외).
4. 원장: 의료진 행이 0개이고 `director_name`이 비어 있으면 `replace_physicians`(`api/admin/physicians.py:158`)를 대표 1명(이름·약력)으로 호출하고 `derive_director_fields`로 병원 단위 값을 맞춘다. 의료진 행이 이미 있으면 `director_*`를 건드리지 않는다.
5. 주소를 채우면 `geocode_address`로 좌표를 채운다(실패는 좌표만 비워 둠, PATCH와 같은 ADM-05).
6. `website_url`·`blog_url`을 채우면 채널 자료 등록(현재 `hospitals.py:503-606`)을 같은 트랜잭션에서 한다 → 이 세 함수(`_submitted_channel_urls` 대응·`_register_channel_sources`·`_dispatch_channel_source_fetches`)를 `services/profile_channel_sources.py`로 옮긴다(라우트·워커 공용, 동작 그대로).
7. 원장에 `origin=AUTOFILL, source, run_id, at`을 쓰고, 감사행 `action="apply_profile_autofill"`(actor `system:profile-autofill`, `applied_fields`, `dropped_fields`, `skipped_operator_fields`, `conflicts`).
8. 완료 재판정은 (c)의 공용 서비스로.
9. 커밋 뒤: 채널 fetch 디스패치, 완료 전환이면 사이트 준비 + V0 디스패치, 공개 중이면 `trigger_hospital_site_revalidate_safe`.

**(c) 완료 재판정 공용 서비스 `services/profile_completion.py`.** `PATCH /profile`의 `:1126-1178`(파생·`was_complete`/`newly_missing` 규칙·인수 수락 차단)과 `:1219-1245`(전환 부수효과)를 함수로 뽑는다: `evaluate_profile_completion(db, hospital, *, missing_before) -> CompletionDecision`과 `dispatch_completion_side_effects(...)`. PATCH는 이 함수를 쓰고 동작은 같다(기존 테스트 그대로 통과가 기준). 자동 경로는 **승격만**(False→True) 한다 — 강등은 사람이 비운 경우(PATCH)뿐이다. 인수 수락이 없으면(`HANDOFF_NOT_ACCEPTED`) 조용히 건너뛰고 로그만 남긴다.

**(d) 완료 승격 스윕.** 15분 `reconcile_essence_snapshots` 틱(E3)에 `_promote_completed_profiles(limit=20)`: `profile_complete=False`이고 상태가 PAUSED가 아니며 `missing_profile_requirement_keys`가 비고 인수 수락이 된 병원을 (c)로 승격한다. D31로 술어가 줄어 지금 막혀 있던 병원도 이 스윕이 연다(§5.3에서 사전 목록 확인).

**(e) 워커.** `profile_autofill_tasks._autofill_once`를 "수집 → 저장 → 적용"으로 바꾼다. 실행 키:
- `registration`(등록 직후, 지금과 같음), `backfill`(§5.1 백필), `retry:{n}`(n=1..3), `followup`(아래).
- 재시도: 마지막 실행의 초안이 비었거나 소스가 모두 실패했으면 1·3·7일 뒤 다시(최대 3회). 비용 가드 보류는 실행을 남기지 않으므로 다음 틱이 다시 집는다(지금과 같음).
- `followup`: 등록 때 URL이 없어 네이버만 읽었고, 적용으로 `website_url`/`blog_url`이 새로 생겼으면 1회 더(홈페이지의 약력·진료 항목을 읽기 위해).
- 스윕(E3 틱): 상태가 PAUSED가 아니고 `PROFILE_AUTOFILL` 성공 실행이 없는 병원(= 기능 이전 등록, 디스패치 유실)과 재시도 기한이 된 병원을 틱당 최대 5곳 디스패치. 등록 디스패치 유실도 이것으로 회수된다(`handoffs.py:83-99` 주석 갱신).
- 실행 요약은 지금 키에 `applied_fields`·`dropped_fields`·`skipped_operator_fields`·`conflicts`·`naver_identity`("STORED_ID"/"VERIFIED_SEARCH"/"UNVERIFIED")를 더한다.

**(f) 수동 경로.** `POST /profile/autofill`(미리보기)은 남긴다 — 사람이 필드를 덮어쓰고 싶을 때 쓰는 도구. `GET /profile/autofill-draft`와 "저장된 초안 불러오기" 버튼은 지운다(이미 적용됐으므로). 정보 화면은 원장의 `AUTOFILL` 필드에 "자동 입력 · 네이버" 배지, `conflict`가 있으면 회색 한 줄("네이버에는 10:00–19:00으로 표시됨")을 보인다 — 사람의 일로 세지 않는다.

**리드 전화번호.** 2-4a/2-4b의 PII-2·PII-3 판단(리드 `clinic_phone`을 병원 행에 복사하지 않음)은 유지한다. 자동 적용 대상은 네이버·홈페이지에서 읽은 공식 번호뿐이다.

### 3.2 D31 — 완료 술어

`hospital_lifecycle.profile_requirements`(`:132-190`)를 플래그 없이 고정한다:

| 키 | 라벨 | 통과 |
|---|---|---|
| `director_basic` | 원장명·약력 | `director_name` **and** `director_career` |
| `director_philosophy` | 진료 철학 (선택) | **항상 통과** — 키는 남긴다(Admin 앵커·옛 응답 호환). 목록에서 빼면 `info-sections.ts`·현황 집계의 키 집합이 달라지므로 "항상 passed"가 덜 깨진다 |
| `contact` | 주소·전화번호·진료시간 | 그대로 |
| `web_channels` | 홈페이지 또는 블로그 | 그대로 |
| `ai_channels` | 네이버 플레이스 | `naver_place_url`만(Google 제외) |
| `geo`·`targeting`·`treatments` | 그대로 | 그대로 |

- `PROFILE_GATE_OPTIONAL_ENRICHMENT`를 `config.py`·`.env.example`·`.env.production.example`에서 지우고, 운영 env에 있으면 배포 때 지운다(남아 있어도 `Settings.model_config`가 `extra="ignore"`(`config.py:133-135`)라 무시된다 — 정리 목적). `test_hospital_lifecycle_gate.py`를 새 고정 술어로 다시 쓴다.
- `profile_publication_verdict`(최소 공개 사실, `:68-84`)는 바꾸지 않는다.
- Admin: `FactsSection`의 진료 철학 라벨에 "(선택)", 채널 섹션의 Google 두 칸에 "(선택)". `readiness_operator_copy.local_entity`를 "Google 병원 정보는 선택이지만 있으면 지도 이해에 도움" 투로. 문구는 `ADMIN_COPY`에만 두고 `make copy-guard` 통과.
- CLAUDE.md 공개 활성화 문단(`PROFILE_GATE_OPTIONAL_ENRICHMENT` 설명)을 새 고정 술어로 바꾼다(P4-17).

### 3.3 D32 — 원장 인터뷰 설문

**자료 종류.** `SourceType.INTERVIEW` 재사용(E2). 라벨: 백엔드 `essence.py:470` "원장 인터뷰 설문", Admin `info-sections.ts:140` 같은 값.

**파일.** `asset_extractor.detect_extractor_for`에 `TXT`(`text/plain`, `.txt`), `HWPX`(`.hwpx`, `application/hwp+zip`·`application/vnd.hancom.hwpx`) 추가. 추출기:
- TXT: UTF-8 → CP949/EUC-KR 순으로 디코드(`decode_html_bytes`의 판정 재사용), `MAX_RAW_TEXT_LENGTH` 자름.
- DOCX: `extract_docx_text`가 표 셀 글도 읽게 한다(설문은 보통 표다). 문서 순서대로 문단·표 행을 이어 붙인다.
- HWPX: zip 안 `Contents/section*.xml`의 `<hp:t>` 텍스트를 순서대로 모은다(stdlib `zipfile`+`xml.etree`, 새 의존성 없음). zip bomb 방지(압축 해제 총량 상한 20MB).
- HWP(바이너리 5.0): **이번 범위에서 지원하지 않는다.** `pyhwp`는 AGPL이라 쓰지 않는다. `olefile`(BSD) + BodyText 스트림 zlib 해제·레코드 파싱으로 가능하지만 레코드 형식 파서를 직접 유지해야 한다(약 150줄 + 표본 파일 테스트). 지금은 422 "한글 파일은 'HWPX' 또는 PDF로 저장해 올려 주세요". 실제 설문이 HWP로만 온다면 후속 태스크로 olefile 추출기를 만든다(§10 Q3).
- 스캔 PDF(이미지뿐)는 본문이 비어 `raw_text=None`으로 저장되고 처리되지 않는다 — 업로드 응답과 자료 행에 "본문을 읽지 못함 (스캔 문서)"을 보인다. OCR은 범위 밖.
- 업로드 상한 12MB(`essence.py:132`) 그대로.

**링크.** `essence_sources.register_url_source`(크롤 라우트) 앞에 `normalize_survey_url`:
- Google 문서 `docs.google.com/document/d/{id}/…` → `…/document/d/{id}/export?format=txt`.
- Google 스프레드시트 `…/spreadsheets/d/{id}/…` → `…/export?format=csv`(첫 시트; `gid`가 있으면 유지).
- 둘 다 "링크가 있는 모든 사용자 보기" 공유일 때만 된다. 비공개면 Google이 `accounts.google.com` 로그인으로 리다이렉트한다 → `fetch_url_text`의 리다이렉트 루프에서 최종 호스트가 `accounts.google.com`이면 "공유 설정을 '링크가 있는 모든 사용자'로 바꿔 주세요"로 실패(로그인 페이지 HTML을 본문으로 저장하지 않는다).
- Google 설문지(`docs.google.com/forms/…`)는 **응답을 링크로 읽을 수 없다**(응답은 소유자 인증 필요, 공개 링크는 질문지뿐). 422 "설문 응답은 PDF(응답 → 인쇄 → PDF 저장)나 연결된 스프레드시트 링크로 올려 주세요".
- 그 밖의 링크(노션 공개 페이지 등)는 일반 URL fetch. JS 렌더 페이지는 본문이 짧아 `looks_like_shell` 품질 신호로 실패할 수 있다 — 그 경우 PDF로 안내.

**개인정보.** INTERVIEW 본문은 저장 전 E6 가림(업로드·크롤 공통 지점: `upload_source_file`의 `raw_text = _clean_optional(raw_text)` 뒤, `register_url_source`의 raw_text 저장 전).

**Admin.** `SourcesSection`에 "원장 인터뷰 설문" 카드: 링크 입력(크롤 라우트, `source_type=INTERVIEW`) + 파일 선택(accept PDF·DOCX·TXT·HWPX). 일반 문서 업로드 폼의 accept도 같은 목록으로. 문구는 `ADMIN_COPY`.

**Essence 우선순위.** §3.4 (c)·(d).

### 3.4 D33 — 승인 없는 자동 Essence

**(a) 입력.**
1. **PROFILE_FACTS 자료**(E2): 병원마다 시스템 소유 자료 1행. `raw_text`는 서술 관련 프로필 값만 줄 단위로 렌더한다 — 원장명, 원장 약력, 진료 철학, 전문과목, 진료 항목(이름·설명), 지역, 핵심 키워드. 줄마다 출처 태그(`[마케터 입력]`/`[자동 수집·네이버]`/`[자동 수집·홈페이지]`)를 원장(§3.1a)에서 붙인다. 주소·전화·진료시간은 넣지 않는다(E5). 프로필 저장·자동 적용 커밋 뒤 갱신(내용 hash가 같으면 no-op). 처리는 **결정적**(줄 → 노트 1개, LLM 호출 없음): 철학→`DOCTOR_PHILOSOPHY`, 약력→`PROOF_POINT`, 진료 항목→`TREATMENT_SIGNAL`, 지역→`LOCAL_CONTEXT`, 나머지→`KEY_MESSAGE`. 자료 화면에는 읽기 전용 "병원 정보(자동)"로 보이고 PATCH·제외·재처리는 409.
2. 홈페이지·블로그 채널 자료(지금과 같음).
3. INTERVIEW 자료(설문).
4. 그 밖에 사람이 올린 자료(지금과 같음).

**(b) 입력 제외(보류 아님).** 합성 직전에 자료 단위로 뺀다: `_has_prompt_injection` 패턴(`essence_auto_review.py:107-113`)이 원문·메모에 걸린 자료, `looks_like_error_page_text`가 참인 자료. 노트 단위로는 지금처럼 오류 잔재 발췌를 뺀다(`essence_engine.py:717-722`). 뺀 자료는 `synthesis_metadata.excluded_inputs=[{source_id, reason}]`에만 남는다. 자료 상태는 바꾸지 않는다(사람이 고치면 새 hash로 다시 들어온다).

**(c) 합성.** `essence_engine._synthesize_philosophy_llm`:
- 노트 줄에 자료 종류를 싣는다: `- id=… | src=INTERVIEW | type=… | excerpt=…`. INTERVIEW 노트는 발췌 400자, 나머지 160자. 노트 순서는 INTERVIEW → PROFILE_FACTS(마케터 입력 줄) → HOMEPAGE → NAVER_BLOG → 기타.
- 시스템 프롬프트 규칙 추가: "doctor_voice·positioning_statement·patient_promise·treatment_narratives는 src=INTERVIEW 근거를 최우선으로 쓴다. 같은 주제에 INTERVIEW와 다른 자료가 다르면 INTERVIEW를 따르고 conflict_note로 남긴다." "주소·전화·진료시간 같은 운영 사실은 어떤 필드에도 쓰지 않는다."(E5)
- 공급자 실패·비용 가드 보류·빈 결과: 결정적 폴백(`_synthesize_philosophy_deterministic`, `:1144`)으로 내려간다 — 지금처럼. 단 **base가 이미 있고 이번 합성이 재합성 트리거**라면 비용 보류 시 기존 판을 유지하고 다음 틱에 다시 시도한다(폴백으로 좋은 판을 덮지 않는다). base가 없을 때는 폴백 판이라도 즉시 적용한다 — 생성이 멈추지 않는 것이 우선(D33).

**(d) 결정적 정리 `essence_engine.prune_automatic_candidate(payload, notes, sources) -> (payload, dropped)`.** 지금 보류 사유가 되는 것을 모두 "버림"으로:
- 근거 미연결: 필드/항목의 `evidence_note_ids`가 비었거나 현재 노트 집합 밖이면 그 항목을 지운다(단일 필드는 None). `validate_philosophy_grounding`(`:1316`)이 지적할 항목과 같은 집합을 지우는 것이 기준 — 정리 뒤 `validate_philosophy_grounding(payload, notes) == []`가 불변식.
- 금지 표현: 긍정 운영 필드(`_positive_candidate_text`의 필드들)에서 `check_forbidden`에 걸린 항목을 지운다(`_without_forbidden_positive_items` `essence_auto_review.py:716` 재사용). 필수 안전 정책(`apply_mandatory_safety_policy`)은 그대로 덧붙인다.
- 오류 잔재: `find_error_marker_fields`가 지목한 필드의 항목을 지운다.
- 운영 사실(E5): 시간·전화번호 패턴이 든 항목을 지운다.
- `must_use_messages`: E8 — INTERVIEW 노트 발췌에 문자 그대로 있는 문장만, 금지 표현 없음, 최대 3개.
- `conflict_notes`: 보류 사유가 아니라 메타데이터. D34 규칙으로 고른 결과와 함께 `synthesis_metadata.conflicts`로 옮긴다.
- 지운 것은 `synthesis_metadata.dropped=[{field, text(앞 80자), reason}]`(상한 50개).
- 기존 base가 있으면 `_carry_forward_grounded_baseline`(`:726`)은 그대로 쓴다. `_critical_losses`는 보류 사유가 아니다(정보로만 `synthesis_metadata.lost_fields`).

**(e) 적용.** `refresh_essence_snapshot`(A1)을 다시 쓴다(이름·태스크 이름·디스패치 목적 `auto-review-essence-snapshot`은 유지 — 봉투·readiness 등록을 바꾸지 않는다):
1. 잠금·claim(`_claim_essence_refresh`, single-flight)은 유지.
2. 합성 → (d) 정리 → **AI 검수 없음** → 같은 잠금 재획득·snapshot CAS(지금 `:1720-1760`과 같음) → 이전 APPROVED를 ARCHIVED·`is_base=False`, 새 판 `APPROVED`·`is_base=True`·`created_by=reviewed_by="system:essence-auto"`·`approved_at=now`·`approval_note="automatic summary (D33)"`·`evidence_noise_hash`·`synthesis_metadata`(mode, trigger, input_hashes{source_id: content_hash}, excluded_inputs, dropped, conflicts, lost_fields, fallback 여부) → `_rescreen_content`(E7 반영) → 공개면 재검증 intent → 감사행 `auto_apply_philosophy`.
3. 같은 snapshot의 남은 DRAFT(옛 보류 초안)는 ARCHIVED.
4. 결과 상태는 `APPLIED`/`UP_TO_DATE`/`WAITING_FOR_SOURCES`/`DEFERRED`/`SNAPSHOT_CHANGED`뿐 — `ESCALATED`가 없다.

**(f) 재합성 트리거(`essence_refresh_needed`, A3).** 기본값(대표 질문에 대한 제안):
- T1 적용 중인 판이 없다(장보고·뉴플랜·세종다온, 신규 병원).
- T2 근거 철회·수정(`authority_refresh_required`, 지금 계약 그대로) — 즉시.
- T3 PROCESSED INTERVIEW 자료 중 판의 `synthesis_metadata.input_hashes`에 없거나 hash가 다른 것이 있다(설문 업로드·교체).
- T4 판이 PROFILE_FACTS 자료를 입력으로 썼고(`input_hashes`에 있음) 그 hash가 바뀌었다(마케터가 원장·진료 정보를 고침).
- 홈페이지·블로그 일반 drift는 재합성하지 않는다(stable base 계약 유지 — 공개 글의 판 교체 churn 방지).
- 9곳 기존 승인본: **그대로 둔다.** `synthesis_metadata`가 비어 있으므로 T4가 걸리지 않는다(PROFILE_FACTS를 입력으로 쓴 적이 없음). 설문이 올라오거나(T3) 근거가 철회되면(T2) 그때 자동 판으로 넘어간다.
- 디바운스: 같은 병원 재합성은 최소 30분 간격(설문 여러 파일을 연달아 올릴 때 한 번만).

**(g) 생성이 멈추지 않게.**
- 자료가 하나도 처리되지 않은 신규 병원도 PROFILE_FACTS(결정적 처리)가 있으므로 프로필이 채워지는 순간 T1으로 판이 생긴다 → 첫 일정(`_ensure_initial_schedule_safe`)으로 이어진다(지금 승인 직후 경로 `tasks.py:4427-4431`).
- `required_text_source_predicate`로 PENDING 자료가 있으면 지금은 WAITING_FOR_SOURCES다. T1(판 없음)에서는 **PROCESSED 자료만으로** 합성해 적용하고, 나머지 자료가 처리되면 그 자료가 INTERVIEW면 T3로 다시 합성한다(홈페이지·블로그는 다음 판에서 흡수). `_split_stale_error_sources`의 ERROR 제외 규칙(D26)은 그대로.
- 생성 게이트의 `MISSING_APPROVED_ESSENCE`(A6)는 방어 코드로 남기되, 만나면 그 자리에서 `auto_review_essence_snapshot`을 디스패치하고(지금 `_heal_missing_essence_for_digest`가 하는 일) 시도 기록은 종착이 아닌 재시도로 둔다 — `generation_retry_policy.attempt_is_terminal`(`:719-740`)에서 `MISSING_APPROVED_ESSENCE`를 종착에서 뺀다. 아침 요약 Slack(A23)은 없앤다.
- T2 철회 직후 재합성이 끝날 때까지(한 태스크 실행, 보통 수 분)만 `current=None`이다. 철회된 사실로 글을 쓰지 않는 것이 우선이라 이 짧은 창은 둔다(다음 복구 스윕이 생성).

**(h) 각 touchpoint가 어떻게 바뀌나**

| # | 바뀜 |
|---|---|
| A1~A3 | (d)(e)(f)로 다시 씀. AI 검수(`review_essence_candidate`·재정 프롬프트)는 호출하지 않는다 — 코드는 P4-14에서 지운다 |
| A4 | 보류·실패 사고 3종을 열지 않는다. 공급자 실패는 폴백으로 끝나고, 폴백까지 실패(DB 등)는 Celery 재시도 → `BACKGROUND_TASK_FAILED` 일반 경로 |
| A5 | 틱에 프로필 완료 승격·자동 입력 스윕·PROFILE_FACTS 동기화(누락분)를 더함(E3) |
| A6 | 그대로(판이 언제나 있으므로 사실상 통과). `MISSING_APPROVED_ESSENCE`는 재시도 코드 |
| A7 | 그대로(T2 짧은 창) |
| A8·A9·A10·A11·A12·A14·A15·A16 | **코드 변경 없음** — 자동 판도 APPROVED(E1). A11 SQL의 조건(`source_snapshot_hash`, `source_asset_ids` 배열, provenance evidence id)은 자동 판·PROFILE_FACTS 노트로 모두 채워진다 — P4-12b의 Postgres 테스트로 확인 |
| A12 | 코드 그대로, 내용은 E8로 좁혀진 문구만 들어온다 |
| A13 | E7: avoid 문구만으로 생긴 drift는 공개 글을 숨기지 않음 |
| A17 | 사람 승인·재검수·판 PATCH 라우트는 410 `ESSENCE_APPROVAL_RETIRED`. 판 목록·현재 판 GET은 남김 |
| A18·A19 | 예외 초안 카드·`essence_review` 예외 카드 삭제. 콘텐츠 탭에 읽기 전용 "운영 요약(자동)" 패널(판 번호·만든 방식·입력 자료·버린 주장 수·충돌·제외 입력) |
| A20 | 문구 교체: "자동 승인"·"예외 카드" → "자동으로 만들어 바로 적용" |
| A21~A24 | §6 |
| A25 | 그대로(자동 판도 `approved_at`이 있다) |
| A26 | `null_noise_hash_approvals`는 그대로(자동 판도 noise hash를 쓴다) |
| A27 | (c) |

### 3.5 D34 — 사실 충돌

**우선순위.**

| 종류 | 필드 | 순서(앞이 이김) |
|---|---|---|
| 사실 | 주소, 전화, 진료시간, 네이버 플레이스 URL/ID, 홈페이지·블로그·카카오 URL, 원장명, 전문과목, 지역, 진료 항목 이름 | **네이버 플레이스(E4 확인된 것)** > 마케터 입력 > 설문 > 홈페이지 > 블로그 |
| 사실(약력) | 원장 약력 | 네이버 > 마케터 > 설문 > 홈페이지 > 블로그 (약력도 학력·경력 사실로 본다) |
| 서술 | 진료 철학, doctor_voice, positioning, patient_promise, 진료 서술(treatment_narratives), 진료 항목 설명 | **설문** > 마케터 입력 > 홈페이지 > 블로그 > 네이버 |

**어디서 푸나.**
1. **자동 입력 추출(사실 필드):** 프롬프트를 "필드마다 출처별 후보 `candidates: [{source, value, evidence}]`"로 바꾸고, 출처별로 발췌 근거 대조(`_field_value_is_grounded`)를 통과한 후보 중 위 순서로 고른다(결정적, 모델이 고르지 않음). 값이 다른 후보는 실행 요약 `conflicts=[{field, chosen:{source,value}, others:[…]}]`. 네이버가 E4 미확인이면 네이버 후보는 순위에서 빠진다.
2. **병원 행(자동 입력 vs 마케터 입력):** D30이 우선한다는 해석이 기본값 — 마케터 값은 덮지 않고, 확인된 네이버 값이 다르면 원장 `conflict`에만 기록(§3.1f 회색 한 줄). **D30과 D34가 여기서 정면으로 부딪친다 — §10 Q1.**
3. **Essence 합성(서술):** (c)의 프롬프트 규칙 + `conflict_notes` → `synthesis_metadata.conflicts`(사고·Slack 없음). 사실은 E5로 Essence에 들어오지 않는다.
4. 설문의 사실(예: 설문에 "토요일 휴진")은 병원 행에 자동 반영하지 않는다(설문은 서술 근거). 사실 순서상 설문이 홈페이지보다 앞이므로, 설문이 사실의 유일한 자료가 될 일은 실제로 드물고, 필요하면 마케터가 입력한다.

**(후속, P4-18) 네이버 정기 재확인:** 월 1회 자동 입력을 다시 돌려 원장이 `AUTOFILL`인 사실 필드를 네이버 값으로 갱신(마케터 필드는 `conflict`만). 이번 배포 범위에 넣지 않아도 D30·D34는 성립한다.

---

## 4. 마이그레이션 — `0091_add_profile_provenance_and_essence_metadata`

- `down_revision = "0090_allow_fallback_treatment_narrative"`.
- `hospitals.profile_field_provenance JSONB NOT NULL DEFAULT '{}'::jsonb`.
- `hospital_content_philosophies.synthesis_metadata JSONB NOT NULL DEFAULT '{}'::jsonb`.
- 둘 다 추가형·기본값 있음 → 옛 코드가 무시한다(롤백 안전). enum 추가 없음(E2). 데이터 채우기는 마이그레이션이 아니라 CLI(§5)가 한다.
- 함께 고칠 곳: `tests/test_migration_chain_linearity.py:60` 근처 head 상수, `scripts/integrity_rehearsal/check.py:182`의 기대 head, `models/hospital.py`·`models/essence.py` 매핑. `production_readiness`는 alembic head를 읽으므로 그대로.

---

## 5. 기존 병원 데이터 백필

모든 CLI는 `python -m app.utils.<name>` 형식, **dry-run 기본**, `--apply`일 때만 쓰고, 결과를 JSON으로 출력한다(`legacy_budget_reset` 선례). 운영 실행은 migrate Job override.

### 5.0 사전 조회 (P4-0, 읽기 전용)
`ops_sql_report`에 `q12`~`q14` 추가:
- `q12` 병원별 `status`·`profile_complete`·현재 술어/새 술어(D31)의 누락 키·인수 수락 여부 → **D31 배포로 새로 완료·공개되는 병원 목록**.
- `q13` 병원별 APPROVED 판 유무·`is_base`·DRAFT 보류 초안 수·자료 수(종류·상태별, INTERVIEW 포함)·`PROFILE_AUTOFILL` 실행 유무와 초안 키 수.
- `q14` 열린 `ESSENCE_AUTO_REVIEW_*`·`ESSENCE_MUST_USE_EXCLUDED`·`MISSING_APPROVED_ESSENCE` 사고 수와 대기 중인 `MISSING_APPROVED_ESSENCE_DIGEST` outbox 행.

### 5.1 프로필 출처 원장 + 자동 입력 백필 (`app.utils.profile_autofill_backfill`)
1. 원장 백필: 감사 로그 `update_profile` 사람 actor의 `changed_fields` → `OPERATOR`. 값이 있는데 기록이 없는 필드 → `LEGACY`.
2. `PROFILE_AUTOFILL` 성공 실행이 없는 병원(세종다온 등)에 `backfill` 키로 수집+적용을 디스패치. `--hospital` 반복 인자로 대상 지정, `--include-live`가 없으면 ACTIVE·`site_live` 병원은 **수집만**(초안 저장, 적용 안 함)하고 적용 예정 필드를 출력한다 → 대표가 결과를 본 뒤 `--include-live --apply`. 라이브 병원의 빈 칸이 공개 페이지에 새로 나타나기 때문이다.
3. 이미 초안이 있는 병원은 그 초안으로 **적용만**(재수집 없음, 비용 0) — 단 초안이 E4 이전에 만든 것이면 네이버 값은 `UNVERIFIED`로 보고 적용하지 않는다(초안의 `naver_place_id`가 병원 행의 저장 id와 같을 때만 예외).

### 5.2 Essence (`app.utils.essence_auto_backfill`)
1. 모든 병원에 PROFILE_FACTS 자료 행을 만들고 결정적 처리(노트 생성). 9곳 기존 승인본의 판은 건드리지 않는다(T4는 `input_hashes`에 PROFILE_FACTS가 없으므로 걸리지 않음).
2. APPROVED 판이 없는 병원(장보고·뉴플랜·세종다온 — q13으로 확정)의 보류 DRAFT를 ARCHIVED로 내리고 자동 파이프라인을 즉시 실행(`auto_review_essence_snapshot` 디스패치). 결과 판 번호·`synthesis_metadata` 요약을 출력.
3. 그 병원들의 첫 일정은 기존 경로(`_ensure_initial_schedule_safe`)가 만든다 — D3 첫 달 안분 그대로.

### 5.3 프로필 완료 승격
배포 뒤 첫 15분 틱이 §3.1d로 승격한다. q12의 목록과 실제 승격 감사행(`profile_complete_transition`)을 대조한다. 승격 → 사이트 준비 → 기본 주소 자동 활성화까지 이어지므로 **공개되는 병원 목록을 배포 기록에 남긴다.**

---

## 6. 사라지거나 조용해지는 사고·Slack

| 대상 | 처리 | 위치 |
|---|---|---|
| `ESSENCE_AUTO_REVIEW_ESCALATED` | 더 이상 열지 않음. 레지스트리 항목·문구 삭제. 열린 행은 resolver(`incident_backlog.RESOLVERS`에 "APPROVED 판이 있으면 RECOVERED, 알림 안 보냄")가 닫음 | `tasks.py:4376-4419`, `incident_types.py:305`, `notification_copy.py:46-50`, `notification_messages.py:111` |
| `ESSENCE_AUTO_REVIEW_FAILED` | 열지 않음(폴백). 같은 resolver | `tasks.py:4330-4350`, `notification_copy.py:190` |
| `ESSENCE_AUTO_REVIEW_COST_BLOCKED` | 열지 않음(base 없음 → 폴백 적용, base 있음 → 다음 틱 재시도). 같은 resolver | `tasks.py:4300-4318`, `notification_copy.py:188` |
| `MISSING_APPROVED_ESSENCE_DIGEST`(아침 요약 Slack, Error) | 만들지 않음. 쌓인 PENDING outbox 행은 발송기가 집지 않게 종류 제외(조용함 규칙과 같은 입구 차단) 뒤 CLI로 `SUPPRESSED` 종결 | `content_publish_notifications.py:40-50, 109-150, 465`, `notification_labels.py:44`, `generation_incident_control.py:113, 1202`, `tasks.py:7572-7593` |
| `MISSING_APPROVED_ESSENCE` 생성 보류 | 종착 → 재시도(§3.4g). 사람의 일 예외 문구(`incident_backlog.py:760-772`, `incidents.py:268`)의 "별도 ESCALATED 사고가 가진다" 설명 갱신 | `generation_retry_policy.py:165, 719-740`, `generation_incident_control.py:485, 710, 833, 936, 1134, 1412` |
| `ESSENCE_MUST_USE_EXCLUDED` | 자동 판은 금지 표현 문구를 애초에 버리므로 새로 생기지 않는다. 기존 9곳 승인본에서 생기는 것은 `INCIDENT_TYPE_POLICIES`에 `quiet=True`로 등록(사고 행은 남고 Slack·할 일 없음). 제외 로직(작가 프롬프트에서 빼기)은 그대로 | `must_use_exclusions.py:33`, `notification_copy.py:51-55`, `incident_backlog.py:650` |
| 예외 카드 `essence_review` | 삭제 | `hospital_overview.py:77, 172-200`, `EscalatedDraftCard.tsx`, `ExceptionCards.tsx:20` |
| `ESSENCE_NOT_ALIGNED`(글 단위) | **유지** — 글 본문의 금지 표현·avoid 문구 검사다(글 단위 안전) | `content_publication.py:398-416` |
| `SOURCE_PROCESSING_FAILED` | 유지(D7) | — |
| `PROFILE_INCOMPLETE` 정체 | 유지 — 자동 입력 뒤에도 남은 필수 칸은 마케터의 일. 문구에 남은 항목 이름을 싣는다 | `onboarding_stalls.py:79-84` |

새로 생기는 사고 종류는 없다. 자동 입력 적용·충돌·버린 주장·제외 입력은 모두 실행 요약·원장·`synthesis_metadata`·감사행에만 남는다(D34 "사고·Slack 없음").

---

## 7. 롤백

- **배포 A(온보딩, §9 웨이브 1~3의 P4-1~P4-10)**: 코드 롤백 가능. 0091 컬럼은 남는다(옛 코드는 무시). 남는 효과: 자동 적용된 프로필 값, 그로 인한 `profile_complete=True` 전환과 공개 활성화 — **되돌아가지 않는다**(공개를 내리려면 사람이 일시정지). 옛 코드의 완료 술어는 더 엄격하지만 PATCH는 "이 저장이 비운 항목"만 강등하므로 이미 완료된 병원은 그대로다. `PROFILE_GATE_OPTIONAL_ENRICHMENT`를 env에서 지웠으면 옛 코드 기본값(False)이 적용된다. INTERVIEW 라벨 변경·TXT/HWPX 자료는 옛 코드에서도 읽힌다(enum 같음; TXT 자료는 `mime_type`만 다르다).
- **배포 B(Essence, P4-11~P4-16)**: 자동 판은 APPROVED라 옛 코드가 그대로 승인본으로 쓴다. PROFILE_FACTS 자료는 옛 코드에서 일반 내부 메모로 보이고(편집 가능해짐), 옛 drift 흡수 규칙상 재합성을 일으키지 않는다. 옛 코드로 돌아가면 보류 사고·아침 요약이 다시 생길 수 있다(보류 초안이 없으니 실제로는 판 없는 신규 병원에서만). 버린 주장·종결한 outbox 행은 되살아나지 않는다. RedBeat 변경 없음.
- 마이그레이션 다운그레이드는 두 컬럼 삭제뿐이지만, 롤백 때 굳이 내리지 않는다(추가형).

---

## 8. 테스트 계획

기본 진입점 `make test-backend-local`·`make test-frontend`·`make copy-guard`·`make db-budget-guard`, 로컬 Postgres×3 + Redis(MasterPlan §4). 태스크마다 새 실패 0.

- **D31** `test_hospital_lifecycle_gate.py` 재작성: 약력 없으면 `director_basic` 누락, 철학·Google 없어도 통과, 키 집합 불변. `test_admin_hospitals_profile.py`의 완료 전환 케이스 회귀.
- **D30** 단위: 빈 칸만 채움 / OPERATOR 필드(값 있음·비움 둘 다) 건너뜀 / 금지 표현 필드·원소·설명 단위 제외 / 지역 섞인 키워드 제외 / 의료진 0명일 때만 대표 생성 / E4 미확인 네이버 값 미적용 / 실행 키(registration·retry·followup·backfill) 멱등. Postgres: 적용과 사람 PATCH가 같은 advisory 잠금으로 직렬화(적용 뒤 PATCH가 이기고 OPERATOR가 남음, PATCH 뒤 적용은 그 필드를 건너뜀), 승격 → `build_aeo_site`·V0 디스패치, 인수 미수락 시 승격 안 함, 공개 중 병원의 재검증 트리거. PATCH 기존 테스트가 공용 서비스 추출 뒤에도 그대로 통과(`test_profile_workflow.py`, `test_profile_sources_autoregister.py`).
- **D34** 단위: 출처별 후보 → 순서 선택 표 테스트(네이버 > 마케터 > 설문 > 홈페이지 > 블로그, 서술은 설문 우선), 근거 대조 실패 후보는 순위에서 빠짐, 충돌 기록 모양.
- **D32** `test_asset_extractor.py`: TXT(UTF-8·CP949), DOCX 표, HWPX(표본 생성), HWP 422, 스캔 PDF 빈 본문. URL 정규화: Docs·Sheets export, Forms 422, `accounts.google.com` 리다이렉트 실패. 가림(E6) 양방향(전화 형태만, 진료시간 숫자는 그대로). 업로드 라우트 `source_type=INTERVIEW` + 처리 자동 시작.
- **D33** 단위: `prune_automatic_candidate` 뒤 `validate_philosophy_grounding == []`·금지 표현 0·오류 잔재 0·운영 사실 0, must_use E8, 주입·차단 자료 입력 제외, 트리거 T1~T4 표(9곳 레거시 판은 PROFILE_FACTS 생성만으로 재합성 안 됨), 비용 보류 시 base 유무 분기, 디바운스. Postgres: 판 없음 → 자동 판 APPLIED → 첫 일정 생성, 자동 판에서 생성한 글이 `reconcile_content_revisions` 0090 SQL에서 eligible(PROFILE_FACTS 노트만 있는 병원 포함), E7(avoid 문구만의 drift가 공개 글을 숨기지 않음, 금지 표현은 숨김), 판 교체 후 `content_philosophy_id` 갱신. 회귀: `test_essence_auto_review*.py`의 보류 기대값을 새 계약으로 고친다(보류 → 적용·버림), `test_must_use_*`는 그대로 통과.
- **사고·Slack** 4종이 더는 열리지 않음, resolver가 열린 행을 알림 없이 닫음, outbox 종류 제외, `ESSENCE_MUST_USE_EXCLUDED` 조용함(사람의 일 0·Slack 0).
- **Admin** vitest: 정보 화면 계약 테스트(`info-page-contract.test.ts`)에서 저장된 초안 버튼 제거·배지·선택 라벨, 설문 카드 accept 목록, 예외 카드 제거. `scripts/check_user_facing_terms.py` 통과.
- **리허설** `scripts/integrity_rehearsal`: head 0091, 백필 CLI dry-run 출력 모양.

---

## 9. 태스크 분해

크기: 한 에이전트가 한 번에 끝낼 수 있는 단위(파일 겹침 최소). 브랜치 `claude/autonomy-p4`(origin/main `b06b9c42`). **배포 A = P4-0~P4-10, 배포 B = P4-11~P4-16**, P4-17은 각 배포의 문서 커밋.

| ID | 결정 | 내용 | 주요 파일 | 선행 |
|---|---|---|---|---|
| P4-0 | 공통 | `ops_sql_report` q12~q14(읽기 전용) + 테스트 | `utils/ops_sql_report.py`, `tests/test_ops_sql_report.py` | — |
| P4-1 | 공통 | 마이그레이션 0091 + 모델 매핑 + head 기대값 | `alembic/versions/0091_*.py`, `models/hospital.py`, `models/essence.py`, `test_migration_chain_linearity.py`, `scripts/integrity_rehearsal/check.py` | — |
| P4-2 | D31 | 완료 술어 고정, 플래그 삭제, 테스트, Admin 선택 라벨·`local_entity` 문구 | `hospital_lifecycle.py`, `config.py`, `.env*.example`, `test_hospital_lifecycle_gate.py`, `FactsSection.tsx`, `admin-copy.ts`, `readiness_operator_copy.py` | — |
| P4-3 | D30 | 완료 재판정·채널 자료 등록을 공용 서비스로 추출(`profile_completion.py`, `profile_channel_sources.py`), PATCH가 사용(동작 동일) | `api/admin/hospitals.py`, 새 서비스 2개 | — (P4-2와 병렬 가능: P4-2는 `hospitals.py`를 안 건드림) |
| P4-4 | D30 | 출처 원장: PATCH에서 OPERATOR 기록, `is_operator_owned`, HospitalDetail 직렬화에 원장 노출, 감사 로그 기반 원장 백필 함수 | 새 `services/profile_provenance.py`, `hospitals.py`(P4-3 뒤), `schemas/hospital.py` | P4-1, P4-3 |
| P4-5 | D34·E4 | 자동 입력 추출을 출처별 후보 + 결정적 우선순위 선택 + 충돌 기록으로, 네이버 신원 확인(저장 id 우선·이름/지역 일치) | `hospital_profile_autofill.py`, `naver_place.py`, `test_hospital_autofill.py` | — |
| P4-6 | D30 | 적용 서비스(§3.1b) + 워커 수집→적용, 실행 키·재시도·followup | 새 `services/profile_autofill_apply.py`, `workers/profile_autofill_tasks.py`, `test_profile_autofill_tasks.py` | P4-3, P4-4, P4-5 |
| P4-7 | D30 | 15분 틱: 완료 승격 스윕 + 자동 입력 스윕(백필·재시도·유실 회수), 백필 CLI `profile_autofill_backfill`(원장 백필·`--include-live`) | `workers/tasks.py`(reconcile 함수만), 새 `utils/profile_autofill_backfill.py`, `api/admin/handoffs.py` 주석 | P4-6 |
| P4-8 | D30 | Admin 정보 화면: 저장된 초안 불러오기·`GET autofill-draft` 제거, 자동 입력 배지·충돌 회색 줄, 수동 미리보기 유지 | `admin/app/hospitals/[id]/info/page.tsx`, `FactsSection.tsx`, `admin/lib/api.ts`, `admin-proxy.ts`, 계약 테스트, `hospitals.py:1406-1440` 삭제 | P4-4 |
| P4-9 | D32 | 추출기 TXT·DOCX 표·HWPX·HWP 거절, 스캔 PDF 표시, 설문 URL 정규화(Docs/Sheets/Forms·로그인 리다이렉트), E6 가림, INTERVIEW 라벨 | `asset_extractor.py`, `essence_sources.py`, `api/admin/essence.py`(업로드 분기·라벨), 테스트 | — |
| P4-10 | D32 | Admin 설문 카드(링크+파일), accept 목록, 라벨 | `SourcesSection.tsx`, `info-sections.ts`, `admin-copy.ts` | P4-9 |
| P4-11 | D33 | PROFILE_FACTS 자료: 렌더(출처 태그), 결정적 처리, 프로필 저장·자동 적용 뒤 동기화, 자료 API에서 읽기 전용(409) | 새 `services/profile_facts_source.py`, `essence_engine.process_source_asset` 분기, `api/admin/essence.py` 가드, `profile_autofill_apply.py`·`hospitals.py` 훅 | P4-1, P4-4 (P4-6과 같은 훅 지점 — P4-6 뒤) |
| P4-12a | D33·D34 | 합성 프롬프트(자료 종류·설문 우선·운영 사실 금지)와 `prune_automatic_candidate`(근거 미연결·금지·잔재·운영 사실·must_use E8·충돌 메타) 순수 함수 + 단위 테스트 | `essence_engine.py` | P4-1 |
| P4-12b | D33 | `refresh_essence_snapshot` 재작성(검수 제거·즉시 적용·`synthesis_metadata`·폴백 분기·DRAFT 정리), `essence_refresh_needed` 트리거 T1~T4·디바운스, PENDING 자료 있어도 T1 적용, 워커의 사고 3종 제거, Postgres 테스트(0090 SQL eligible 포함) | `essence_auto_review.py`, `workers/tasks.py`(auto_review 태스크·reconcile), 테스트 | P4-11, P4-12a |
| P4-13 | D33 | 하류: E7(판 교체 rescreen이 avoid-only drift로 공개 글을 숨기지 않음), 생성 `MISSING_APPROVED_ESSENCE` 재시도화 + 즉석 디스패치, readiness·hospital_states 문구, Essence 승인 라우트 410 | `essence_auto_review._rescreen_content`·`content_publication.apply_essence_revalidation`, `generation_retry_policy.py`, `tasks.py`(생성 경로), `readiness_operator_copy.py`, `hospital_states.py`, `api/admin/essence.py` | P4-12b |
| P4-14 | D33 | 사고·Slack 정리(§6): 아침 요약 삭제, 레지스트리·문구·라벨, resolver, `ESSENCE_MUST_USE_EXCLUDED` quiet, outbox 종류 제외, 쓰지 않게 된 검수 코드(`review_essence_candidate`·재정) 삭제 | `incident_types.py`, `incident_backlog.py`, `notification_copy.py`, `notification_labels.py`, `notification_messages.py`, `content_publish_notifications.py`, `generation_incident_control.py`, `notification_store.py`, `essence_auto_review.py` | P4-13 (`tasks.py`·`generation_*` 겹침 → 직렬) |
| P4-15 | D33 | Admin: 예외 초안 카드·`essence_review` 카드 삭제, 읽기 전용 "운영 요약(자동)" 패널, 판 상태 라벨 "적용 중" | `EscalatedDraftCard.tsx`, `ExceptionCards.tsx`, `essence-actions.ts`, `hospital_overview.py`, `content/page.tsx`, `admin-copy.ts`, `essence.py:486-487` | P4-12b(API 직렬화) |
| P4-16 | D33 | 백필 CLI `essence_auto_backfill`(PROFILE_FACTS 생성·처리, 판 없는 병원 DRAFT 보관 + 자동 실행, outbox SUPPRESSED 종결) | 새 `utils/essence_auto_backfill.py` | P4-12b, P4-14 |
| P4-17 | 문서 | CLAUDE.md 계약 문단(공개와 콘텐츠 게이트·필수 문구 "현재 APPROVED 승인본"의 뜻·Essence 보류 사다리·알림 정책), MasterPlan 결정표 D30~D34·E1~E8, 배포 안내(사전 조회·CLI 순서·공개될 병원 목록·env에서 플래그 삭제) | `CLAUDE.md`, `docs/plans/2026-10-11-autonomy-masterplan.md`, `docs/ops/deployment-runbook.md`, `docs/ops/slack-notification-policy.md` | 각 배포 마지막 |
| P4-18 (후속) | D34 | 네이버 월 1회 재확인(AUTOFILL 필드 갱신·마케터 필드 conflict) | `profile_autofill_tasks.py`, 틱 | P4-7 |

**병렬 웨이브**
- 웨이브 1(병렬 worktree): P4-0, P4-1, P4-2, P4-3, P4-5, P4-9.
- 웨이브 2: P4-4(← P4-1·P4-3), P4-10(← P4-9), P4-12a(← P4-1).
- 웨이브 3: P4-6(← P4-3·P4-4·P4-5), P4-8(← P4-4) — 병렬.
- 웨이브 4: P4-7(← P4-6) → **배포 A**(+ P4-17 A문서).
- 웨이브 5: P4-11(← P4-6) → P4-12b → P4-13 → P4-14(직렬, `tasks.py` 겹침). P4-15는 P4-12b 뒤 P4-13과 병렬.
- 웨이브 6: P4-16 → **배포 B**(+ P4-17 B문서).

**배포 완료 기준**
- A: q12 목록 저장 → 배포 → 백필 CLI dry-run 결과 대표 확인 → `--apply`(라이브 제외) → 15분 틱 뒤 승격·공개 병원이 q12 예측과 같음, 세종다온 초안·적용 감사행, 새 리비전 ERROR 0, readiness 통과.
- B: q13·q14 저장 → 배포 → `essence_auto_backfill --apply` → 장보고·뉴플랜·세종다온 APPROVED 자동 판 + 첫 일정, 9곳 판 번호 불변, 열린 `ESSENCE_AUTO_REVIEW_*` 0, 다음 23:00 생성에서 `MISSING_APPROVED_ESSENCE` 0, 0090 SQL로 판 받은 공개 글 수 감소 없음.

---

## 10. 위험과 미결 질문

### 위험
- **R1 사람 없이 공개된다.** D30+D31로 신규 병원은 자동 입력만으로 완료 → 사이트 준비 → 기본 주소 자동 공개까지 간다. 잘못 매칭된 네이버 값이 공개되는 것이 최대 위험 → E4(저장 id 우선·이름·지역 일치, 미확인은 적용 안 함)가 막는다. 배포 A 전에 q12로 공개될 병원을 확정한다.
- **R2 자동 판의 품질 바닥.** AI 검수가 빠지고 공급자 장애 시 결정적 폴백 판이 적용된다. 글 단위 독립 AI 검수·의료광고 필터·참고자료 게이트가 그대로라 공개 안전은 유지되지만 문체 품질은 떨어질 수 있다. 폴백 판은 `synthesis_metadata.fallback=true`로 남기고, 다음 트리거(또는 P4-13에서 "폴백 판은 24시간 뒤 LLM 재합성 1회" 규칙 추가 여부)로 회복 — 구현 때 결정.
- **R3 판 교체 churn.** 재합성마다 `_rescreen_content`가 병원의 모든 글을 다시 판정한다. T3·T4 + 30분 디바운스로 빈도를 묶고 E7로 공개 글이 이유 없이 숨지 않게 한다.
- **R4 설문 개인정보.** 원장 개인 연락처가 근거 노트·공개 about으로 새는 것 → E6 가림 + 공개 about은 positioning·promise만(기존).
- **R5 Jina 의존.** 네이버·폴백 fetch가 외부 Jina Reader에 의존한다(기존). 장애 시 자동 입력이 비고 재시도(1·3·7일)로 회복 — 사고는 만들지 않는다. 그동안 남은 칸은 `PROFILE_INCOMPLETE` 정체가 마케터에게 알린다.

### 미결 질문 (대표 결정 필요)
- **Q1 (D30 × D34 충돌):** 마케터가 입력한 사실(예: 진료시간 9:00)과 확인된 네이버 값(10:00)이 다를 때 병원 행(=공개 페이지)은? **기본값: 마케터 값 유지(D30 "덮지 않는다"), 네이버 값은 충돌로만 기록(화면 회색 한 줄, 사고·Slack 없음).** 대안: 네이버가 언제나 이긴다(D34 문자 그대로) — 마케터가 일부러 고친 값(네이버가 오래된 경우)도 덮인다.
- **Q2 (자동 판의 필수 문구):** 필수 문구는 모든 새 글에 원문 그대로 들어간다. **기본값 E8: 설문 원문에 문자 그대로 있는 문장만, 최대 3개.** 대안: 자동 판은 필수 문구를 비운다(가장 보수적) / 합성 문구도 허용(근거 노트만 있으면).
- **Q3 (HWP 바이너리):** 실제 설문이 `.hwp`로 오는가? 오면 olefile 기반 추출기를 후속으로 만든다(약 1태스크). 기본값: 이번에는 HWPX·PDF·DOCX·TXT만, HWP는 "HWPX/PDF로 저장" 안내.

(재합성 트리거 T1~T4와 "9곳 기존 승인본 유지"는 §3.4f의 기본값으로 진행한다 — 이견이 있으면 Q4로 올린다.)

### 10.1 대표 답변 (2026-10-14)

- **Q1 확정:** 마케터가 입력·수정한 값이 언제나 우선한다(덮지 않는다). 마케터가 손대지 않은 칸 안에서는 네이버 플레이스가 최우선이다(그다음 설문·홈페이지·블로그). 마케터 값과 네이버 값의 차이는 충돌로만 기록한다(사고·Slack 없음).
- **Q2 확정:** 필수 문구는 설문 원문에 문자 그대로 있는 원장 문장만, 최대 3개(E8)를 기본으로 한다. 설문에 쓸 만한 원장 문장이 없으면(설문 없음·짧은 응답만) 근거 노트에 연결된 합성 문구를 최대 3개까지 허용한다. 두 경우 모두 금지 표현은 제외하고, 합성 문구는 근거 노트 id를 메타데이터에 남긴다.
- **Q3 확정:** 원장 설문은 PDF로 온다. HWP 추출기는 만들지 않는다(기존 PDF 경로 사용). HWPX·DOCX·TXT 지원은 범위에서 줄여도 된다 — PDF와 링크를 우선한다.

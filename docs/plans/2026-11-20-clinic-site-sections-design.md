# 병원 공개 페이지 섹션 설정(레이아웃 편집기) — 백엔드·공개 계약 설계

작성 2026-11-20 · 병원 페이지 세션 · 기준 `origin/main` `c1d6e179`(DB head `0099`) · 상태: **설계(구현 전, 사용자 확인 대기)**

출처: 병원 홈페이지 수정 요청서 4장, 어드민 개선 요청서 5장(5-1~5-9)·6장, 준비 문서
`docs/plans/2026-11-20-revamp/README.md` 3절 3단계. 레이아웃 A/B/C 결정은
[2026-11-13 레이아웃](2026-11-13-clinic-site-layouts-abc.md).

이 문서는 **계약**(DB 칸, 쓰기 API, 검증, 캐시 갱신, 공개 응답, 렌더링 규칙)만 정한다. 편집 화면은
이 계약이 병합된 뒤 어드민 세션이 만든다. 결정이 필요한 곳은 **[결정]** 으로 표시했다.

---

## 1. 목표와 지키는 것

목표: 계약 때 고른 틀(A·B·C) 위에서 운영팀이 병원마다 섹션 켜기·끄기·순서·제목·배경·정렬, 첫 화면
배치·글자 크기·줄바꿈·사진 초점·덮개, FAQ 선택·개수, 메뉴·모바일 하단 바, 공유 이미지·설명을
조절하고, 임시 저장 → 미리보기 → 공개 반영 → 이력 → 복원/기본으로 되돌리기를 한다.

바꾸지 않는 계약(테스트로 고정):

| # | 계약 | 근거 |
|---|---|---|
| I1 | 병원 사실(이름·주소·전화·진료시간·진료 항목·의료진)의 **구조화 정보(JSON-LD)·llms.txt는 섹션 표시와 무관하게 같다** | 요청서 5-9, README 5절 |
| I2 | **화면 FAQ = FAQPage** — FAQPage의 질문은 화면에 보이는 FAQ와 같은 배열이다. FAQ를 끄면 FAQPage도 없다 | `clinic-layout-contract.test.ts`, `schema.test.ts` |
| I3 | 질문 목록과 FAQ는 같은 글·같은 질문을 겹쳐 싣지 않는다 | 같은 테스트 + PR #325 |
| I4 | 의료진은 공개 조건을 통과한 **전원**을 보인다(한 사람만 숨기기 없음) | 11-13 결정 |
| I5 | 편집기는 **입력 데이터를 지우지 않는다**(병원 정보·문구·사진·글). 섹션을 꺼도 값은 남는다 | 요청서 5-9 |
| I6 | 설정이 없는 병원은 지금 화면 그대로다(기존 NULL·A·B·C 병원은 배포만으로 바뀌지 않는다) | 11-13 결정 |
| I7 | 모든 문구 칸은 의료광고 필터를 거친다 — 저장은 거절(문제 단어·칸 경로 표시), 공개는 걸린 문장만 비운다 | `clinic_page_copy.py`와 같은 이중 방어 |
| I8 | 승인된 질문·답(`faq_question`·`faq_answer_summary`)과 추적 측정 질문 문장은 편집기에서 바꾸지 않는다 | 검수 hash 결합, 측정 비교 가능성 |

## 2. 데이터 모델

### 2.1 새 표 `hospital_site_configs` (마이그레이션 `0100_add_hospital_site_configs`, 추가형)

| 칸 | 형 | 설명 |
|---|---|---|
| `id` | UUID PK | |
| `hospital_id` | UUID FK → hospitals, `ON DELETE CASCADE`, index | |
| `version` | int | 병원 안에서 1부터. 공개 반영할 때만 오른다(초안은 `NULL`) |
| `state` | enum `DRAFT`·`PUBLISHED`·`SUPERSEDED` | 병원당 `DRAFT` 하나·`PUBLISHED` 하나(부분 유니크 인덱스 둘) |
| `layout` | `String(1)` `A`/`B`/`C` | 이 판의 기본 틀. 공개 반영 때 `hospitals.site_layout`에 같이 쓴다 |
| `config` | JSONB | 3절 스키마. 기본값과 **다른 칸만** 담는다(덮어쓰기 모양) — 편집·비교용 |
| `resolved` | JSONB | 저장 시점의 기본값과 합친 **완성본 스냅샷**. 공개 응답·이력 미리보기·복원은 이것을 쓴다 — 나중에 `site_defaults.py`가 바뀌어도 옛 공개 판이 그대로 재현된다 |
| `defaults_version` | String | 합칠 때 쓴 기본값 판(`site_defaults.DEFAULTS_VERSION`). 복원 때 지금 판과 다르면 덮어쓰기 원본을 지금 기본값에 다시 합치고 바뀐 칸을 경고로 보인다 |
| `schema_version` | int | 지금 1 |
| `revision` | int | 초안 낙관적 잠금 — 저장마다 +1, 클라이언트가 `If-Match`로 보낸다 |
| `based_on_id` | UUID null | 이 초안을 만든 공개 판(복원이면 복원한 판) |
| `origin` | enum `DEFAULT`·`EDIT`·`RESTORE`·`RESET` | 만든 방식 |
| `created_by` / `created_at` | actor 문자열 / ts | 초안을 처음 만든 사람 |
| `updated_by` / `updated_at` | | 마지막 저장 |
| `published_by` / `published_at` | | 공개 반영 |
| `change_summary` | JSONB | 직전 공개 판과 다른 섹션 키 목록(이력 화면용, 서버 계산) |

- 이력 = `PUBLISHED`·`SUPERSEDED` 판(행은 지우지 않는다, append-only). 복원은 옛 판의 `layout`·`config`를 **새 초안**으로 복사하고(`origin=RESTORE`), 공개 반영을 거쳐야 화면에 나간다.
- `hospitals.site_layout`은 그대로 둔다 — 계약 등록·지역 통계·하위 페이지 셸·옛 코드 롤백이 이 칸을 읽는다. 공개 반영 트랜잭션이 두 곳을 같이 쓴다. 설정 행이 없는 병원은 `site_layout`만으로 지금처럼 그린다(I6).
- **이중 쓰기 금지**: 공개 판이 있는 병원은 기존 `PATCH /admin/hospitals/{id}`의 `site_layout` 변경·비우기를 409 `SITE_LAYOUT_OWNED_BY_SITE_CONFIG`로 거절한다(편집기 `reset`/공개 반영으로만 바꾼다). 공개 판이 없는 병원은 지금처럼 PATCH로 바꾼다. 두 값이 갈라졌는지는 readiness 참고 사실로 센다(Codex 검토 4).
- 왜 `hospitals`의 JSON 칸 하나가 아닌가: 임시 저장·이력·복원·작업자 기록이 요청 범위이고, `hospitals` 행 PATCH(`profile-patch.ts` 보호·provenance)와 저장 경로를 섞지 않기 위해서다.

### 2.2 이미 있는 칸과의 관계

| 칸 | 편집기에서 | 비고 |
|---|---|---|
| `hero_headline`·`hero_description` | 문구는 병원 정보 그대로, 편집기는 **PC·모바일 줄바꿈 위치**만 덧입힌다(3.3) | 문구 원본을 둘로 만들지 않는다 |
| `care_principles`(0096) | 문구는 기존 칸, 편집기는 카드 수(2~4)·기본 카드 사용 여부만 | 4번째 카드 문구는 `care_principles.cards`를 4칸으로 늘리는 별도 변경 **[결정 S1]** |
| `parking_note`·`transit_note` | 문구는 기존 칸, 편집기는 오시는 길 항목 순서·지하철 안내 끄기 | |
| `hero_image_url`·`hero_media_kind` | 대표 이미지는 기존 칸(사진 목록의 대표 지정), 편집기는 **초점·덮개·배치** | |
| 사진·로고·대표색 | 기존 칸. 편집기는 미리보기에서 바로 바꿀 수 있게 기존 API를 부른다 | 편집기 초안과 묶이지 않음 — 즉시 저장(요청서 공통 규칙) |

## 3. `config` 스키마 v1

기본값과 다른 칸만 저장한다. 서버가 레이아웃별 기본값(`site_defaults.py`)과 합친 **완성본**을 공개
응답으로 낸다. 모르는 키는 저장 거절(`extra=forbid`), 공개 응답에서는 버린다.

```jsonc
{
  "sections": [                        // 홈 섹션 순서 = 배열 순서. 빠진 섹션은 기본 위치·기본값
    { "id": "treatments", "enabled": true, "title": "진료 과목",
      "background": "default",          // default | white | tint | band(짙은 띠)
      "align": "left",                  // left | center (기본 left — 요청서 4장)
      "label": false }                  // 영문 소제목(MEDICAL 등) 표시
  ],
  "hero": {
    "arrangement": "overlay",           // overlay(사진 위 글자) | split(좌우) | text(글자만)
    "title_size": "m",                  // s | m | l  (m = 지금 48px)
    "line_breaks": { "hero.headline": { "pc": "365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료", "mobile": null } },
    "blocks": ["today", "phone", "location", "hours"],   // 정보 블록 선택·순서: today·phone·location·booking·hours
    "image": { "focus": { "x": 0.5, "y": 0.35 }, "veil": { "opacity": 0.6, "extent": "text" } }  // extent: text | full | none
  },
  "copy": {                             // 고정 문구 덮어쓰기(비우면 공통 문구)
    "treatments.note": "…", "doctor.title": "…", "care_flow.title": "…",
    "hours.weekend_label": "토요일 진료"
  },
  "treatments": { "show_description": true, "style": "cards" },          // cards | list
  "doctor":     { "photo_ratio": "portrait", "photo_focus": { "x": 0.5, "y": 0.2 },
                  "title_position": "above", "show_philosophy": false },
  "care_principles": { "card_count": 3, "use_default_cards": true },
  "care_flow":  { "steps": [ { "title": "상담·문진", "body": "…" } ] },   // 2~6단계, 비우면 기본 4단계
  "visit":      { "order": ["parking", "transit"], "show_transit": true },   // [결정 S7]
  "faq":        { "mode": "auto", "limit": 6, "content_ids": [], "merge_questions": false },  // [결정 S2]
  "region_tags": { "show": true, "separator": "comma" },
  "nav":        { "items": ["treatments", "visit", "doctor", "contents", "website", "call"],
                  "mobile_bar": ["call", "hours", "guide", "map"], "section_index": false },
  "share":      { "image_asset_id": null, "description": null },
  "type":       { "body_size": "normal" },                              // normal | large
  "article":    { "aside": "bottom" }                                   // bottom | right_sticky [결정 12]
}
```

### 3.1 섹션 id(홈)

`hero` · `section_index`(B 목차·기본형 바로가기) · `contents`(기본형·B 먼저 정리한 글, A 콘텐츠 영역) ·
`news`(C 최근 소식 — C의 `#contents`) · `hours` · `treatments` · `doctor` · `care_principles` · `care_flow` ·
`gallery` · `questions`(자주 확인하는 질문) · `faq` · `facts`(기본 정보) · `visit`(오시는 길).

레이아웃마다 쓸 수 있는 섹션과 기본 순서·제목·배경은 `site_defaults.py` 한 곳(백엔드)과 같은 값을
`site/lib/clinic-section-defaults.ts`가 읽는다(공개 응답에 완성본이 오므로 사이트는 기본값을 따로
갖지 않아도 된다 — 테스트용 고정값만).

### 3.2 필수 고정 섹션 — 끌 수 없고 위치만 바꾼다

| 섹션 | 이유 |
|---|---|
| `hero` | 첫 화면·h1 하나 |
| `facts`(기본 정보) | AI 답변의 근거가 되는 사실 패널(요청서 5-3) |
| `hours`(진료시간) | 같은 이유(요청서 5-3). 레이아웃에 독립 섹션이 없으면 첫 화면·기본 정보가 대신한다 |
| `doctor`(의료진) — **[결정 S3]** | I4(의료진 전원). 권장: 끌 수 없음. 대안: 끄면 홈에서 빠지고 `/doctor`·메뉴는 남는다 |

### 3.3 줄바꿈 지정(PC·모바일 따로)

- 저장 값은 **줄 구분된 같은 문장**이다. `pc`·`mobile` **둘 다** 줄을 이으면(공백 하나, 정규화 후) 지금
  원문과 같아야 한다 — 글자를 바꾸는 통로가 되지 않게 서버가 비교해 거절한다(`LINES_TEXT_MISMATCH`).
- 줄바꿈을 걸 수 있는 칸은 **고정 목록**이다: `hero.headline`·`hero.description`·`care_principles.title`·
  `care_principles.lede`·`care_principles.cards[i].title|body`. 임의 경로는 받지 않는다(FAQ 질문·측정 질문
  같은 승인 문장에 쓰일 수 없게 — I8). 스키마는 `line_breaks: { "<고정 경로>": {pc, mobile} }`.
- 원문이 나중에 바뀌면(병원 정보 PATCH) 그 칸의 줄바꿈 지정은 공개 응답에서 버린다(원문과 다르면 무시)
  — 저장값은 남겨 편집기가 '원문이 바뀌어 줄바꿈 지정이 꺼짐' 경고를 보인다(Codex 검토 2).
- 의료광고 필터는 줄마다가 아니라 **이어 붙인 문장**으로 돈다. `check_forbidden`은 줄바꿈을 넘는 표현을
  보지 못한다('완\n치 보장'은 통과, '완치 보장'은 거절 — Codex 교차검토에서 재현). 모든 여러 줄 칸
  (`care_flow.steps[].body`·`copy.*` 포함)도 저장·공개 둘 다 줄바꿈을 공백으로 바꾼 글로 검사한다.
- 사이트는 줄마다 `<span class="lines-pc">`/`lines-mobile`로 그리고 CSS가 화면 폭에 맞는 쪽만 줄을
  나눈다(h1 글자는 하나 — 추출 문장 불변).
- 줄바꿈 지정이 없으면 PR #327의 자동 묶음(시간·괄호·가운뎃점)이 그대로 동작한다.
- 대상 칸: 첫 화면 카피·첫 화면 설명·진료 원칙 제목·소개·카드 문구(요청서 2-1). 편집 단위는 같다.

## 4. 쓰기 API (`backend/app/api/admin/site_config.py`)

모든 쓰기는 BFF 서명 actor 단언(`X-Admin-Actor-Assertion`) 필수, 병원 advisory 잠금 안, 감사 로그
(`site_config_save`·`_publish`·`_restore`·`_reset`·`_discard`, detail에 판 id·바뀐 섹션 키).

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/admin/hospitals/{id}/site-config` | `{published, draft, defaults[layout], history[≤50], warnings}` — `published`·`draft`는 완성본(기본값과 합친 것)과 덮어쓰기 원본 둘 다 |
| PUT | `…/site-config/draft` (`If-Match: <revision>`) | 초안 저장(없으면 공개 판에서 만든다). 검증 실패 422 `{errors:[{path, code, message, words?}]}`. 화면 영향 없음 — **재검증 intent 없음** |
| POST | `…/site-config/draft/publish` (`Idempotency-Key`) | 초안 → 새 `PUBLISHED`(version+1), 옛 공개 판 `SUPERSEDED`, `hospitals.site_layout` 갱신, **같은 트랜잭션에 `enqueue_public_surface_intent`(SITE_REVALIDATION)**. 커밋 뒤 `trigger_hospital_site_revalidate_safe`(기존 PATCH와 같은 순서). 공개 중이 아닌 병원은 intent 없이 저장만 |
| DELETE | `…/site-config/draft` | 초안 버리기 |
| POST | `…/site-config/versions/{vid}/restore` | 그 판으로 새 초안(`origin=RESTORE`). 공개는 별도 |
| POST | `…/site-config/reset` | 5-1 기본 세팅으로 새 초안(`origin=RESET`, config `{}`). 레이아웃 바꾸기도 여기서 `layout` 인자 **[결정 9·10]** |
| POST | `…/site-config/preview-token` | 미리보기 토큰(5절) |

### 4.1 검증(저장·공개 반영 둘 다 같은 함수 `validate_site_config(hospital, layout, config)`)

1. 형식: pydantic strict, 모르는 키 거절, enum·범위(초점 0~1, 덮개 0~0.9, limit 3~10, 카드 2~4, 단계 2~6).
2. 섹션: 레이아웃이 쓰지 않는 id 거절, 같은 id 두 번 거절, 필수 고정 섹션 `enabled=false` 거절(`FIXED_SECTION`).
3. 문구: 모든 사람 입력 글(섹션 제목·`copy.*`·`care_flow.steps`·`share.description`·`line_breaks`)에
   길이 상한과 `check_forbidden` — 걸리면 칸 경로와 문제 단어를 돌려준다(I7). 줄바꿈 문장 일치(3.3).
   **검사 단위는 칸 하나가 아니라 화면에 이어 보이는 묶음**이다: 섹션 제목+설명, 단계 제목+설명, 카드
   제목+설명, 첫 화면 제목+설명을 기본값과 덮어쓰기를 합친 **최종 표시 문장**으로 이어(줄바꿈은 공백으로)
   한 번 더 검사한다. 실제 필터에서 '타 병원보다' + '저렴합니다'는 각각 통과하지만 합치면 '할인·유인'으로
   걸린다(Codex 검토 1).
4. 참조: `share.image_asset_id`는 그 병원의 공개 사진이고 사람 얼굴이면 실인물·신원 승인 조건을 넘어야 한다
   (`public/physicians.py`와 같은 판정). `faq.content_ids`는 그 병원의 **공개 FAQ 글 중 승인 질문·답이 있는 것**.
5. 메뉴: `nav.items`가 가리키는 섹션이 꺼져 있으면 경고(저장은 됨) — 공개 응답이 그 항목을 뺀다.
6. 실패는 422, 경고는 200 + `warnings`.

### 4.2 동시성·멱등

- 초안을 바꾸거나 쓰는 **모든** 요청(저장·공개 반영·버리기·복원·초기화)은 `{draft_id, revision}` 전제조건을
  받는다(복원·초기화는 '초안 없음'도 명시 전제조건). 다르면 409 `DRAFT_CHANGED`(최신 초안 동봉). A가 revision
  5를 미리보고 B가 6을 저장한 뒤 A가 공개를 누르면 6이 공개되던 경로를 막는다(Codex 검토 3).
- 공개 반영은 `Idempotency-Key` → `OperationRun`(`PUBLISH_SITE_CONFIG`) 영수증. 같은 키 재요청은 같은 결과.
- 공개 반영 직전 검증을 **다시** 돈다(초안 저장 뒤 사진이 비공개가 됐거나 FAQ 글이 내려간 경우).
- 공개 중인 병원이면 **커밋 전에** `ensure_site_revalidate_configured()`(기존 PATCH와 같은 사전 검사)로 재검증
  설정 누락을 거절하고, 같은 트랜잭션에 `enqueue_public_surface_intent`를 남긴 뒤 커밋 후
  `trigger_hospital_site_revalidate_safe`를 부른다. 기존 프로필 PATCH에는 트랜잭션 intent가 없으므로 이것은
  새로 세우는 규칙이다(PATCH를 선례로 삼지 않는다, Codex 검토 14).

## 5. 공개 응답·미리보기

- `GET /public/hospitals/{slug}`에 `site_config` 추가: 공개 판 완성본을 **공개용으로 다시 정리**한 값
  (`public_site_config`) — 금지 표현이 든 문구는 비우고, 공개가 끝난 사진·내려간 FAQ id는 빼고, 꺼진
  섹션을 가리키는 메뉴 항목은 뺀다. 설정 행이 없으면 `null`(I6). `schema_version` 동봉.
- 미리보기 토큰: `POST …/preview-token`이 새 비밀 `SITE_PREVIEW_TOKEN_SECRET`로 서명한 토큰(10분)을 준다.
  토큰은 **병원 id + 초안 UUID + revision**에 묶이고, 미리보기 API가 그 초안이 지금도 DRAFT이고 revision이
  같은지 확인한다 — 버리고 다시 만든 초안에 옛 토큰이 열리지 않는다. 공개 반영·버리기·복원·초기화·저장 뒤
  옛 토큰은 모두 무효(revision이나 초안 id가 달라짐)(Codex 검토 5). **[결정 S4]** 비밀 추가는 운영 env 변경.
- 미리보기 데이터: `GET /public/hospitals/{slug}/preview?token=`이 병원 공개 응답과 **같은 모양 전체**(병원 사실 +
  초안 `site_config`)를 준다 — 공개 전 병원(공개 조회가 404)도 미리볼 수 있게. 공개 상태 검사 대신 토큰이 권한이다.
  응답 `Cache-Control: no-store`, 실패 응답도 같다.
- 미리보기 화면: 사이트의 **별도 경로** `/preview/{token}`(예약 slug `preview`)에서 그린다 — 홈 경로(`revalidate=1800`,
  공유 fetch 캐시)를 재사용하지 않는다. 이 경로는 `dynamic = 'force-dynamic'`, 모든 fetch `no-store`, 메타데이터도
  같은 미리보기 판정으로 만들고 `robots: noindex,nofollow`, `X-Robots-Tag`, 응답 `no-store`(실패 포함)(Codex 검토 7).
- 분석 제외: `Analytics.tsx`·`lib/analytics.ts`의 제외 경로에 `/preview`를 넣는다(지금은 `/survey`만). 토큰이
  `page_location`으로 GA·픽셀에 나가지 않게, 토큰 검증 결과와 무관하게 제외한다(Codex 검토 6).
- iframe: 지금 Site는 `frame-ancestors 'none'`·`X-Frame-Options: DENY`, Admin은 `frame-src 'self' blob:`이다.
  `/preview/*` 경로에만 `frame-ancestors <Admin origin>`을 주고(X-Frame-Options는 그 경로에서 뺀다), Admin CSP의
  `frame-src`에 Site origin을 더한다. 다른 경로의 정책은 그대로(Codex 검토 8).

## 6. 사이트 렌더링 규칙

- `page.tsx`는 지금처럼 데이터·JSON-LD를 **한 번** 만든다. `site_config`는 표현만 바꾼다.
- 홈 섹션 목록 = `site_config.sections`(완성본)에서 `enabled`인 것을 순서대로. 레이아웃 컴포넌트는 섹션
  id → 컴포넌트 표를 갖고 그리기만 한다. `site_config`가 `null`이면 지금 코드 경로 그대로(I6, 테스트로 고정).
- **I1**: `clinicJsonLd`(이름·주소·전화·진료시간·진료 항목·`employee` 전원)·`BreadcrumbList`·llms.txt는
  `site_config`를 읽지 않는다(소스 테스트로 고정 — `site_config`가 그 함수들에 들어가지 않음).
- **I2·I3 FAQ**:
  - `faq.mode=auto`: 지금 규칙(`selectFaqEntries`, PR #325의 같은 문장 1회) + `limit`.
  - `faq.mode=manual`: `content_ids` 순서대로, 승인 질문·답이 있는 공개 FAQ만. 질문 **문구 수정은 없다**(I8) — 문구가 문제면 글 쪽에서 고친다(결정 14).
  - 데이터 경로: 홈은 지금 `fetchContents(slug, 60)`만 받으므로 60건 밖의 선택 FAQ가 빠질 수 있다. 공개 응답의
    `site_config.faq`에 **선택된 FAQ의 승인 질문·답·id를 함께 실어** 보낸다(공개 FAQ 글에서 서버가 다시 읽은 값).
    사이트는 그 배열 하나를 화면·FAQPage·질문 목록 중복 제거에 그대로 넘긴다 — `buildFaqPageJsonLd()`가 안에서
    `selectFaqEntries`를 다시 부르지 않게, 이미 고른 배열을 받는 `buildFaqPageJsonLdFromEntries(entries)`로 바꾼다
    (auto 모드도 같은 함수, Codex 검토 9).
  - FAQ 섹션이 꺼지면 홈 FAQPage도 없다(화면 = FAQPage). 각 FAQ 글 상세의 FAQPage는 그대로다.
  - `merge_questions=true`(결정 S2 = README 11번): 한 섹션 '자주 묻는 질문' 안에 위쪽은 답이 있는 FAQ(이것만 FAQPage), 아래쪽은 나머지 질문 링크. 같은 글·같은 문장 중복 금지(I3).
- **I4 의료진**: 꺼지지 않는다. 사진 비율·초점·직함 위치·진료 철학 표시만 조절. (S3에서 '끌 수 있음'을 고르면
  I4와 `clinic-layout-contract.test.ts`의 홈 의료진 전원 계약을 바꾸는 결정이 된다 — Codex 검토 15.)
- **콘텐츠 영역**: 섹션 id `contents`를 둔다(기본형·B `FeaturedContent`, A 콘텐츠 영역, C는 `news`가 같은 역할 —
  C에서는 `news`가 `#contents`를 갖는다). 레이아웃별 섹션 표가 이 대응을 명시한다(Codex 검토 10).
- 섹션 안의 고정 링크(`CarePrinciples`의 '오시는 길 → #contact' 등)는 가리키는 섹션이 꺼지면 하위 페이지
  주소(`/visit`)로 바꿔 건다.
- 앵커: 꺼진 섹션의 `id`(`#treatments`·`#doctor`·`#faq`·`#facts`·`#contact`·`#contents`)는 사라지고,
  그 섹션을 가리키는 메뉴·바로가기·하단 바 항목도 함께 빠진다. 백엔드·Admin 코드에서 공개 사이트
  섹션 앵커로 가는 링크는 없다(2026-11-20 grep, 구현 PR에서 테스트로 고정). 사이트 안의 링크(A안
  '진료시간 전체 보기 → #facts' 등)는 가리키는 섹션이 필수 섹션이거나, 꺼지면 링크도 같이 뺀다.
- `/treatments`·`/doctor`·`/visit`·`/contents` 하위 페이지와 sitemap은 섹션 표시와 무관하다.
- 공유 이미지·설명(`share`)은 OG/트위터 메타만 바꾼다. JSON-LD `image`는 그대로(I1).
- 글 상세 보조 칸(`article.aside`)은 결정 12에 따른다.

## 7. 지역 통계·기본 세팅

- 지역 통계는 기본 틀(`layout`) 기준으로 세고, 설정이 공개된 병원 수를 「직접 조절됨 N곳」으로 따로 보인다(요청서 5-9). 자기 병원 포함 여부는 결정 19.
- 5-1 기본 세팅(계약 등록 직후 자동 틀 선택: 지역 최소 사용 → 첫 화면 정보 우선순위 → 사진 유무)은 결정 9·10 뒤에 `site_defaults.pick_default_layout()`으로 넣는다. 기본 세팅 결과 = `layout` + `config {}`.

## 8. 테스트 계약(구현 PR에 같이 들어갈 것)

- 백엔드: 스키마 거절(모르는 키·enum·범위), 필수 섹션 끄기 거절, 금지 표현 경로·단어, 줄바꿈 문장 불일치,
  다른 병원 사진·내려간 FAQ id 거절, 초안 revision 409, 공개 반영 멱등, 공개 반영 트랜잭션에 intent 하나,
  공개 응답 재정리(필터 변경 뒤 옛 문구 비움), 설정 없는 병원 `site_config: null`, 이력 append-only.
- 사이트: `site_config=null`이면 지금 마크업과 같은 섹션 순서(스냅샷), JSON-LD·llms.txt 소스가 `site_config`를
  읽지 않음, FAQ 꺼짐 → FAQPage 없음, manual FAQ = FAQPage, 의료진 전원, 꺼진 섹션의 메뉴 항목 없음,
  미리보기 `no-store`·`noindex`.

## 9. 단계

| 단계 | 내용 | 소유 |
|---|---|---|
| 3a | 표·모델·`site_defaults.py`·검증·쓰기 API·공개 응답(`site_config`)·미리보기 토큰 | 병원 페이지 세션 |
| 3b | 사이트: 섹션 목록 렌더링, 첫 화면 배치·크기·줄바꿈·초점·덮개, FAQ 모드, 메뉴·하단 바, 공유 | 병원 페이지 세션 |
| 3c | Admin 편집 화면(왼쪽 섹션 목록·가운데 미리보기·오른쪽 설정, 임시 저장·공개 반영·이력·복원) + 6장 표시 위치 안내 | 어드민 세션 |
| 3d | 5-1 기본 세팅 자동 적용 | 결정 9·10 뒤 |

마이그레이션 번호는 병합 직전 `origin/main` head를 보고 맞춘다(어드민 세션과 공유).

## 10. 결정이 필요한 것

| # | 질문 | 권장 |
|---|---|---|
| S1 | 진료 원칙 카드 4개 — `care_principles` 칸을 4칸으로 넓힐까 | 넓힌다(추가형, 비우면 기본 카드) |
| S2 | FAQ·질문 목록 통합(README 11번) | 한 섹션 안 두 단(답 있는 FAQ = FAQPage / 나머지 질문 링크) |
| S3 | 의료진 섹션을 끌 수 있게 할까 | 끄지 못함(위치·모양만) |
| S4 | 미리보기용 새 비밀 `SITE_PREVIEW_TOKEN_SECRET` 추가(운영 env 변경) | 추가 |
| S5 | 편집기 FAQ에서 질문 문구 고치기 | 하지 않음 — 글 쪽(결정 14) |
| S6 | 설정 변경 이력 보존 기간 | 무기한(행 작음) |
| S7 | 오시는 길 '버스·지하철 순서·지하철만 끄기' — 지금 대중교통 안내는 자유 문장 하나(`transit_note`)라 버스·지하철을 나눌 수 없다 | v1은 주차·대중교통 순서와 대중교통 줄 끄기만. 나누려면 `transit_note`를 버스·지하철 두 칸으로 쪼개는 별도 변경(Codex 검토 13) |
| S8 | 진료 원칙 카드 4개(S1)의 배포·롤백 | 지금 코드는 3개만 저장·공개(구버전 롤백 시 4번째 카드 버려짐·재저장 거절). 사이트 먼저(4개 읽기) → 백엔드(4개 저장) 순서로 배포하고, 롤백 시 4번째 카드는 공개에서만 빠지고 DB에는 남는다고 명시(Codex 검토 12) |

## 11. Codex 교차검토(2026-11-20) 반영 기록

gpt-6.1-sol high로 이 문서를 코드와 대조했다. 15건 모두 받아들여 위 본문에 반영했다(반박 없음).
1 이어 보이는 칸 조합 필터 우회 → 4.1(3) · 2 줄바꿈 원문 일치를 pc·mobile 둘 다, 고정 경로 → 3.3 ·
3 초안 revision 전제조건 → 4.2 · 4 `site_layout` 이중 쓰기 → 2.1 · 5 미리보기 토큰에 초안 UUID → 5 ·
6 분석 도구 토큰 유출 → 5 · 7 미리보기 캐시·noindex·공개 전 병원 → 5 · 8 iframe CSP → 5 ·
9 수동 FAQ 데이터 경로·FAQPage 함수 → 6 · 10 `contents` 섹션·고정 링크 → 3.1·6 · 11 이력 재현 →
2.1 `resolved`·`defaults_version` · 12 카드 4개 롤백 → S8 · 13 필드 부족(의료진 초점·교통 분리) → 3·S7 ·
14 재검증 사전 검사·intent 선례 → 4.2 · 15 의료진 끄기 대안의 계약 충돌 → 6.

## 12. 3a 구현에서 정한 것(2026-11-21, 브랜치 `claude/site-config-3a`)

구현 중 본문과 달라졌거나 본문이 비워 둔 곳을 정했다. 본문보다 이 절이 우선한다.

| 항목 | 정한 것 | 이유 |
|---|---|---|
| 레이아웃별 섹션 표 | B = 도입 전 화면 순서(첫 화면·바로가기(기본 꺼짐)·진료·의료진·원칙·글·질문·FAQ·과정·사진·기본 정보·오시는 길, 독립 진료시간 없음). A = 첫 화면·진료·의료진·원칙·오늘 진료(`hours`)·질문 목록(`contents`)·FAQ·과정·사진·기본 정보(오시는 길 포함). C = 첫 화면·최근 소식(`news`)·진료시간(`hours`)·진료·의료진·원칙·과정·질문·FAQ·사진·기본 정보 | 2026-11-21 사이트 마크업 그대로 |
| 붙은 섹션(`attached_to`) | C의 `news`·`hours`는 첫 화면 안, C의 `care_flow`는 원칙 섹션 안 — 끄기·제목은 되지만 따로 옮기면 `SECTION_ATTACHED` | 한 마크업 블록이다 |
| 필수 섹션 | `hero`·`facts`·`hours`(있는 레이아웃만)·`doctor`(S3 권장안). 첫 화면은 언제나 맨 앞(`FIRST_SECTION`). 첫 화면·바로가기·기본 정보는 제목을 바꾸지 않는다 | h1 하나, 기본 정보 제목은 병원 이름에서 만든다 |
| 진료 과정 안내 문장 | `copy.care_flow.note`가 아니라 `care_flow.note`. `resolved`에는 당시 기본 4단계·안내 문장을 그대로 담는다 | 옛 공개 판 재현(Codex 3a 검토 5) |
| 진료 원칙 카드 수(S1·S8) | `card_count`는 지금 저장 칸 수(3)까지만. 4칸 확장은 사이트가 먼저 4개를 읽도록 배포한 뒤 | S8 배포 순서 |
| 줄바꿈 원문 비교 | 줄 경계는 원문의 공백 한 칸 자리 또는 단어 중간만. 줄 안 띄어쓰기는 원문과 같아야 한다 | 띄어쓰기를 바꾸는 통로 차단(검토 6) |
| 줄바꿈 원문 | 병원 정보에 **저장된** 문장만. 사이트가 기본 문구로 채운 빈 칸(자동 첫 화면 문장 등)에는 걸 수 없다(`LINES_SOURCE_EMPTY`) | 백엔드가 사이트 자동 문장을 재현하지 못한다 |
| 필터 | 줄바꿈을 공백으로 이은 글과 **붙여** 이은 글 둘 다 검사('완\n치 보장'). 이어 보이는 묶음에 병원 원문을 넣는다(원칙 제목+병원 소개 문장, 첫 화면 제목+설명, 카드 제목+설명) — 병원 원문끼리 걸리면 '병원 정보의 문장을 고쳐 주세요' | 검토 1·2 |
| FAQ 직접 고르기 | FAQ 유형 공개 글만. 검증은 고른 id를 직접 조회한다. 고른 글이 모두 내려가면 빈 목록(자동 선택으로 바꾸지 않는다) | 검토 7·8·9 |
| 쓰기 권한 | 모든 쓰기에 사람 계정(`require_active_account`) — 시스템 actor는 화면 설정을 못 바꾼다 | 검토 4 |
| 미리보기 경로 | `GET /public/site-preview/{token}`(병원 slug 없음 — 토큰이 병원·초안·revision을 갖는다), 실패 포함 no-store·noindex. 비밀값 `SITE_PREVIEW_TOKEN_SECRET`이 없으면 발급 503·확인 실패(fail-closed) | 사이트 `/preview/{token}`이 slug를 모른다 |
| 레이아웃 갈라짐 | 공개 판 레이아웃 ≠ `hospitals.site_layout`이면 공개 응답 `site_config: null`, Admin GET 경고 `SITE_LAYOUT_DIVERGED` | 안전한 쪽(지금 화면) |
| 복원 | 지금 사실로 검증해 실패하면 422(초안을 만들지 않는다). 기본값 판이 바뀌었으면 경고 `DEFAULTS_CHANGED` | 초안은 언제나 유효 |
| 공개 반영 요청 키 | `Idempotency-Key` 필수(없으면 400) | 3c 화면이 키를 만든다 |

3a에서 하지 않은 것: 기존 `PATCH /admin/hospitals/{id}`의 `site_layout` 409와 예약 slug `preview`(공유 파일 `admin/hospitals.py` — 어드민 PR 병합 뒤 같은 PR에 더한다), readiness 갈라짐 참고 사실, Terraform 비밀 리소스(운영 비밀 추가는 배포 때 확인), **공개 전 병원 미리보기의 사진·로고**(공개 자산 경로가 공개 병원만 열어 404 — 3b 미리보기 경로와 함께 토큰 결합 자산 경로로).

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

## 13. 3b ① 구현에서 정한 것(2026-11-21, 브랜치 `claude/site-config-3b`)

3b를 셋으로 나눈다: ① 섹션 순서·켜기·제목·안내, FAQ, 메뉴·하단 바, 공유(이 절) · ② 미리보기 화면 `/preview/[token]`(CSP·분석 제외·토큰 자산) · ③ 첫 화면·배경·정렬·영문 소제목·진료 항목 모양·의료진 사진 같은 모양 칸과 줄바꿈. 본문·12절보다 이 절이 우선한다.

| 항목 | 정한 것 | 이유 |
|---|---|---|
| 기본값 = 지금 화면 | 3a 기본값 중 지금 마크업과 다른 칸을 고쳤다 — FAQ 6 → **10**(사이트 `FAQ_MAX_ITEMS`), C안 영문 소제목(최근 소식·진료시간·원칙·과정·기본 정보) 켬, 첫 화면 세 안 모두 **좌우 분할·덮개 없음**, A·C 진료 항목 설명 없음, B 섹션 머리 가운데 정렬(질문 목록·진료 원칙 제외)·A 오늘 진료 가운데, 진료 지역 구분자 가운뎃점. `DEFAULTS_VERSION` `2026-11-21.2`. 첫 화면 정보 블록·의료진 사진 칸은 ③에서 맞추고 판을 한 번 더 올린다 | 공개 판 `resolved`는 스냅샷이라 틀린 기본값이 영구히 남는다 — 그 전에는 운영에서 공개 반영하지 않는다 |
| 기본값 대조 | `site/lib/site-config-defaults.json` = `site_defaults.defaults()`(백엔드 테스트), 사이트 기본 순서·제목·메뉴·FAQ 상한 = 그 파일(사이트 테스트) | 사이트는 기본값을 따로 갖지 않는다는 원칙의 대조 고리 |
| 같은 화면 확인 | main과 이 브랜치를 같은 가짜 API로 빌드해 홈 HTML을 비교 — 레이아웃 없음·A·B·C × 보통·빈 병원·글 적은 병원, 설정 없음과 기본값 공개 판 모두 HTML 주석까지 **바이트 동일**(21개 조합) | I6 |
| 제목·안내 | 설정 제목이 기본 제목과 같으면 '바꾼 것'이 아니다 — 지금 제목(병원 정보의 진료 원칙 제목, A안 '오늘(월) … 진료')을 그대로 쓴다. 안내 문장이 원래 없는 섹션(의료진·사진 등)은 설정에 문장이 있을 때만 그 레이아웃의 안내 줄 모양으로 더한다 | 백엔드 `section_title`과 같은 규칙 |
| '켜짐' | '보여도 됨'이다 — 진료 항목 없음·글 적음·사진 부족 같은 지금의 데이터 조건은 그대로 숨긴다 | 빈 자리를 지어내지 않는다 |
| 끌 수 없는 섹션 | 사이트도 첫 화면·기본 정보·진료시간·의료진을 켠 채로 그린다(설정이 꺼도). 첫 화면은 맨 앞 | 백엔드 검증과 이중 방어(I4) |
| 묶음 앵커 | A안 질문 목록(`contents`)+FAQ는 기본 순서처럼 바로 이어지면 지금처럼 한 `#contents`로 감싸고, 떨어지면 `#contents`는 질문 목록 쪽에 둔다. A·C `#contact`는 기본 정보(끌 수 없음)를 따라간다. B '공식 채널 보기 → #contact'는 오시는 길이 꺼지면 `/visit` | 링크가 빈 곳을 가리키지 않게 |
| 붙은 섹션 | C 최근 소식·진료시간은 첫 화면 아래 줄, 진료 과정은 원칙 섹션 안에서 켜기·제목만 따른다. 원칙을 끄고 과정만 켜면 같은 띠에 과정만 그린다 | 12절 |
| FAQ | 홈이 배열 하나를 만든다(`resolveHomeFaqEntries`): 설정 없음 = 지금 규칙, 섹션 꺼짐 = 빈 배열(FAQPage 없음), 자동 = 지금 규칙 + 개수, 직접 = 공개 응답의 `faq.entries`(서버가 지금 공개된 글에서 다시 읽은 승인 질문·답) 순서 그대로. 화면·`buildFaqPageJsonLdFromEntries`·질문 목록 중복 제거가 같은 배열을 쓴다. `merge_questions`는 ③ | I2·I3 |
| 진료 과정 | 설정 단계가 기본 단계와 글자까지 같으면 지금 단계(보조 문구 '첫 방문 시' 등 포함), 바꾼 단계는 번호·제목·설명만. 목록 이름은 '진료 N단계 흐름'. 제목 '…진료 4단계'는 단계 수를 바꾸면 편집기에서 제목도 고쳐야 한다(③에서 경고) | 보조 문구는 저장 칸이 없다 |
| 메뉴·하단 바 | 모든 병원 페이지의 머리가 같은 설정을 읽는다. 메뉴 `call` = 오른쪽 전화 상담 버튼, `faq` = 홈 `#faq`(FAQ 섹션이 꺼졌거나 직접 고른 글이 모두 내려가면 뺀다). 하단 바는 항목 수에 맞춰 칸을 나눈다(4개면 지금 그대로) | 메뉴는 하위 페이지로 간다 |
| 공유 | `share.image_asset_id`·`description`은 OG·트위터만 바꾼다. 검색 설명·JSON-LD `image`는 그대로 | I1 |
| 받은 값 좁히기 | 형식이 틀리거나 판(`schema_version`)이 다르거나 병원의 지금 레이아웃과 다르면 설정 전체를 버리고 지금 화면 | 병원 응답 전체를 막지 않는다 |

①에서 하지 않은 것: 바로가기 메뉴(`section_index`)를 켰을 때의 마크업(지금은 켜도 그리지 않는다), `hours.weekend_label`, 진료 원칙 `use_default_cards` — 모두 ③.

### 13-②. 미리보기 화면(2026-11-21, 브랜치 `claude/site-config-3b-preview`)

| 항목 | 정한 것 | 이유 |
|---|---|---|
| 주소 | 플랫폼 호스트 `https://reputation.motionlabs.kr/preview/{token}`(`NEXT_PUBLIC_SITE_URL`). 병원 호스트에서도 `/preview`는 예약 경로(설문 `/survey`와 같다) | 토큰이 병원·초안·revision을 갖는다. 병원 주소는 공개 병원만 열린다 |
| 그리기 | 홈 본문을 `_layouts/HospitalHome.tsx`로 옮겨 공개 홈과 미리보기가 같은 코드로 그린다(옮긴 뒤 main 대비 홈 HTML 21개 조합 바이트 동일). 공개 전 병원은 글 목록이 404라 글 없이 그린다. 위에 '미리보기' 띠 | 공개 반영 뒤 화면과 갈라지지 않게 |
| 응답 | `force-dynamic`, 병원·글 조회 모두 `no-store`, `Cache-Control: no-store`, `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer`, 분석 태그 없음. 토큰이 틀리거나 만료되면 안내 화면 | 토큰이 주소에 있다 |
| iframe | `/preview/*`만 `frame-ancestors`에 어드민 주소(`SITE_PREVIEW_FRAME_ANCESTORS`, 기본 `https://admin.reputation.motionlabs.kr`), 그 경로는 `X-Frame-Options`를 싣지 않는다. 다른 경로는 그대로 `'none'`+`DENY`(빌드한 서버에서 확인) | 설계 5절 |
| 사진·로고 | 미리보기 응답의 공개 자산 주소를 토큰 자산 경로(`/public/site-preview/{token}/assets/{id}`·`/logo`)로 바꾼다 — 공개 자산과 같은 판정, 권한은 토큰. GCS 서명 주소는 10분(공개 자산은 24시간). Next 이미지 최적화기를 거치지 않고(`unoptimized`), 프록시가 최적화기 요청에 이 주소가 들면 404 | 최적화 결과는 공용 캐시 하루라 토큰 만료 뒤에도 사진이 나간다(Codex) |
| 로그 | 백엔드 접근 로그에서 `/site-preview/{token}`을 가린다(설문과 같은 필터) | Codex |

②에서 하지 않은 것: 로드밸런서·Cloud Run 요청 로그의 경로 마스킹(설문 `/survey/{token}`과 같은 상태 — 토큰 10분으로 완화), 미리보기 안 링크를 누르면 iframe 안에서 공개 사이트로 가며 그 사이트는 iframe을 막는다(어드민이 새 탭 열기를 함께 둔다).

## 14. ③-1 기본값 맞춤·옛 판 보호(2026-11-21, 브랜치 `claude/site-config-3c1-defaults`)

③을 둘로 나눴다. ③-1은 사이트가 아직 읽지 않는 칸의 기본값을 지금 화면에 맞추고 옛 판을 보호한다(화면 변화 없음).
③-2(모양 설정을 화면에 반영)는 레퍼런스·디자인 결정 뒤다.

| 항목 | 정한 것 | 근거 |
|---|---|---|
| 의료진 사진 | 비율 A **가로**(`.doctorPhoto` 4:3), B 세로(4:5)·C 세로(3:4). 초점은 세 안 모두 **가운데** — B 사진에 붙는 규칙(`.clinic-curator-portrait img`)에 지정이 없다(`.clinic-curator-portrait-image`의 위 14%는 쓰이지 않는 클래스) | 이전 공통값(세로·위 20%)은 세 안 모두 틀렸다 |
| 첫 화면 사진 초점 | B **위 48%**(`.clinic-hero-editorial-image`), A·C 가운데. B는 모바일에서 54%·가운데라 한 점으로 모든 폭을 맞출 수 없다 — ③-2는 기본값과 같으면 아무것도 출력하지 않는다 | 기본값 = 지금 화면 |
| B 배경 | 진료 원칙·진료 과정 **tint**(연한 대표색 띠). 나머지는 `default` = 레이아웃 고유 배경 | |
| 그대로 맞는 칸 | 제목 크기 `m`(= 각 레이아웃의 지금 크기), 직함 위치 위, 진료 철학 표시 꺼짐, 오시는 길 주차 → 대중교통, 글 보조 칸 아래, 본문 글자 보통, 질문 합치기 꺼짐 | |
| **결정됨 — ③-2에서 B에만 열기** | 첫 화면 정보 블록(`hero.blocks`)과 진료 항목 모양(`treatments.style`)은 B만 맞다. A 첫 화면에는 정보 블록 띠가 없고(전화 버튼·오늘 진료 한 줄), C는 오늘 진료 위젯 하나다. A 알약·C 번호 타일은 `cards`도 `list`도 아니다. ③-2에서 두 칸을 B에만 열고 A·C는 숨긴다(어드민 숨김과 검증 거절 422를 같이 — 어드민이 지금 A·C에도 그 칸을 보이므로 ③-1에 거절만 넣으면 편집 순간 422가 난다). ③-1은 기본값을 손대지 않고 옛 판 보호 대상에만 넣는다. A·C용 값은 나중에 앞으로만 넓힌다 | 2026-11-21 상위 세션 결정. 지금 화면을 바꾸지 않는 보수적 선택 |
| 판 | `DEFAULTS_VERSION` `2026-11-21.3`, 사이트 고정값(`site-config-defaults.json`)도 같이 | |
| 옛 판 보호 | 공개·미리보기 응답(`site_config_refs.public_site_config_for`)이 `site_defaults.refresh_stale_defaults`를 지난다. 스냅샷 판이 칸별 기준 판(`REFRESHED_SINCE`)보다 오래됐으면(판이 없거나 깨져도) 그 칸만 그 판의 설정(바꾼 칸)을 지금 기본값에 다시 합친다. 대상: 섹션 배경·정렬·소제목, 첫 화면, 진료 항목, 의료진, 진료 원칙 기본 카드, 오시는 길, 질문 합치기, 진료 지역 표기, 바로가기 메뉴, 글자, 글 칸(③-1 판). 사이트가 이미 읽는 칸(순서·켜기·제목, 문구, 진료 과정, 카드 수, FAQ 방식·개수·글, 메뉴, 공유)은 스냅샷 그대로다 — FAQ 개수는 `.2`에서 기본값이 6 → 10으로 바뀌었지만 지금 화면에 보이는 칸이라 넣지 않는다(옛 `.1` 공개 판은 6 그대로). 설정 칸 하나가 깨져 있으면 그 칸만 기본값으로 낸다. 저장된 스냅샷은 바꾸지 않는다 | 그 칸들은 사이트가 읽기 전에 저장돼 화면에 보인 적 없는 값이다 — '옛 판은 그대로 재현'의 예외 |
| 판 비교 | 날짜·번호를 나눠 비교한다(`.10` > `.9`) | 문자열 비교는 틀린다 |
| 어드민 응답 | 어드민 GET의 공개 판·초안 `resolved`와 변경 요약(`changed_sections`)도 같은 보호를 거친 값이다(`served_resolved`) | 편집기는 받은 완성본에서 기본값과의 차이만 저장한다 — 옛 스냅샷을 그대로 주면 옛 기본값이 '직접 바꾼 칸'으로 굳는다 |
| 앞으로 | 사이트가 새 칸을 읽기 시작하거나 아직 읽지 않는 칸의 기본값을 다시 바꾸면 `REFRESHED_SINCE`에 그 칸과 판을 적는다 | 이번 결정 필요 두 칸도 같다 |
| 같은 화면 | 사이트 실행 코드는 바뀌지 않았다(고정값 파일은 테스트만 읽는다). 옛(`.2`)·새(`.3`) 기본값 공개 판을 사이트 정규화에 넣으면 세 안 모두 같은 설정·순서가 된다 | I6 |

## 15. ③-2a B안 정보 띠 칸·진료 항목 모양(2026-11-21, 브랜치 `claude/site-config-3c2-look`)

③-2를 넷으로 나눴다: ③-2a(B안 정보 띠 칸·진료 항목 모양) → ③-2b(의료진 사진 비율·직함 위치, A·B·C) →
③-2c(섹션 배경·정렬·영문 소제목) → ③-2d(첫 화면 배치·덮개·제목 크기 — 세 레이아웃의 첫 화면이 달라 마지막).
레퍼런스·고른 번호는 `docs/plans/2026-11-21-site-config-3-2-references.html`(상위 세션)과 시안(사용자 "권하는 쪽으로 다 하자",
예약은 "네이버 예약으로 연결").

| 항목 | 정한 것 |
|---|---|
| 기본값 = 지금 마크업 | 설정의 정보 띠 칸이 지금 띠(오늘 진료·전화·위치·진료시간)와 같거나, 진료 항목이 카드·설명 보임이면 지금 마크업을 그대로 그린다(`ClinicHero`·`TreatmentGrid`의 기본 분기). 레이아웃 도입 전 병원은 설정이 언제나 null이다 |
| 정보 띠 칸(B만) | 칸 내용·마크업은 지금 띠의 같은 칸과 같고 순서·개수만 다르다. 칸마다 지금 너비 비율(오늘 1.2 · 전화 1 · 위치 1.55 · 진료시간 1.1 · 예약 1). 920px 아래 두 칸씩, 홀수면 마지막 칸이 한 줄. 당일·야간형의 맨 앞 칸 강조색(`--clinic-brand-action`)은 쓰지 않는다 — B 띠 배경(`--clinic-brand-strong`)과 같은 색이라 글자가 묻힌다(Codex). 지금 기본 띠에도 같은 문제가 있다(후속) |
| 예약 칸 | 네이버 플레이스(병원 정보의 플레이스 주소, 없으면 플레이스 번호로 만든 주소)를 새 창으로 연다. 글자는 '네이버에서 예약·문의' — 예약 화면 주소는 병원마다 달라 만들지 않는다(플레이스 화면의 예약·문의 버튼으로 이어짐). 플레이스가 없으면 칸을 빼고(빼고 나니 지금 띠와 같으면 지금 띠, 남은 칸이 없으면 띠 없음 — 고르지 않은 칸을 대신 보이지 않는다), 저장 때 경고 `BOOKING_NO_PLACE`(저장은 된다) |
| 진료 항목 목록(B만) | 한 줄에 이름(명조)과 설명 한 줄(말줄임), 줄 사이 점선, 두 열(720px 아래 한 열), 8개까지 + '전체 N개 보기'. 설명 숨김은 카드·목록 모두 |
| A·C | 두 칸을 읽지 않는다(A 알약·C 타일). A·C 편집 화면에서 숨기고 저장 검증이 거절하는 것은 어드민 숨김과 함께 나가야 해 별도 PR이다 |
| 판 | 기본값을 바꾸지 않았다 — `DEFAULTS_VERSION`·`REFRESHED_SINCE` 그대로 |
| 공개 반영 막음 | Admin `SITE_RENDERS_SITE_CONFIG`는 **`REFRESHED_SINCE`의 모든 칸을 사이트가 그리거나, 어드민에서 숨기고 서버가 거절할 때까지** 닫아 둔다. ③-2a~d가 다루는 칸은 `hero`·`treatments`·`doctor`·`sections.*`뿐이라 ③-2d 뒤에도 `care_principles.use_default_cards`·`visit`·`faq.merge_questions`·`region_tags`·`nav.section_index`·`type`·`article`, 그리고 `hero.line_breaks`·`doctor.show_philosophy`가 남는다. 그 전에 열면 사이트가 아직 그리지 않는 칸을 공개할 수 있고, 다음 사이트 배포가 재공개 없이 화면을 바꾼다 |

## 16. ③-2b 의료진 사진 비율·초점, 직함 위치(2026-11-21, 브랜치 `claude/site-config-3c2b-doctor`)

| 항목 | 정한 것 |
|---|---|
| 범위 | A·B·C 모두. `doctor.photo_ratio`·`photo_focus`·`title_position`. 진료 철학(`show_philosophy`)은 뒤로 미룬다(공개 응답에 검수된 철학 문장이 있는지 먼저 확인 — 없으면 숨기고 거절) |
| 기본값 = 지금 마크업 | 레이아웃의 지금 비율(A 가로·B·C 세로)과 같은 비율, 가운데 초점, 직함 위는 '바꾼 것이 아니다' — 사진 상자에 아무 스타일도 더하지 않는다 |
| 비율 | 바꾼 비율만 사진 상자에 `aspect-ratio`로 준다: 세로 4:5(B와 같음), 정사각 1:1, 가로 4:3. 상자 너비 규칙(레이아웃별 최대 폭)은 그대로다. **사진이 있을 때만** — 사진이 없거나 못 불러오면 지금의 이름 첫 글자 타일 모양 그대로(`ClinicAvatar look`) |
| 초점 | 가운데가 아니면 사진의 `object-position`(x%·y%). 0~1 밖·형식이 틀린 값은 버린다 |
| 직함 위치 | '아래'면 직함(`physician.title`)을 이름 바로 아래에 둔다(B `clinic-curator-eyebrow--below`, A·C `[data-role=eyebrow][data-position=below]`). 직함이 없을 때의 역할 라벨은 원래부터 이름 아래다 |
| 판 | 기본값을 바꾸지 않았다 — `DEFAULTS_VERSION`·`REFRESHED_SINCE` 그대로 |

## 17. ③-2c-1 섹션 제목 정렬·영문 소제목(2026-11-21, 브랜치 `claude/site-config-3c2c-heads`)

| 항목 | 정한 것 |
|---|---|
| 범위 | A·B·C의 `sections[].align`·`label`. 배경(`background`)·진료 철학은 다음 단계 |
| 머리가 없는 섹션 | 첫 화면(`hero`)·바로가기(`section_index`)는 두 칸을 읽지 않는다(정렬·소제목을 그릴 머리가 없다). 어드민 숨김·서버 거절은 다음 짝 PR |
| 기본값 = 지금 마크업 | 레이아웃 기본(`DEFAULT_SECTION_LOOK` — 백엔드 섹션 표와 같다는 테스트)과 같으면 정렬 틀이 없고, 소제목은 레이아웃 기본대로(C만 보임, 낱말도 지금과 같음) |
| 영문 소제목 | 제목 h2 **밖**의 실제 요소(`aria-hidden`, 클래스 `.clinic-look-label` — 하위 페이지의 `.clinic-section-label`과 다르다) — h2 글자는 그대로다. A안의 가로 머리 줄(질문 목록·병원 공간)에서는 제목과 한 묶음으로 제목 위에 둔다. 낱말은 C안 낱말(MEDICAL·DOCTOR·POLICY·FLOW·Q&A·FAQ·CLINIC·ACCESS·NEWS·INFORMATION) + A·B에만 있는 섹션(B 먼저 정리한 글 ARTICLES, A 질문 목록 Q&A, 오시는 길 LOCATION). 모양은 C안 라벨과 같다. 진료 원칙은 'POLICY / 진료 원칙 / 제목'(레퍼런스 3-라) |
| 정렬 | 바꾼 섹션만 `.clinic-look[data-look-align]` 틀로 감싸고 머리(제목·안내 문장·소제목)에만 건다 — B는 h2의 부모 머리, A·C는 `.clinic-look-head`/`.clinic-look-title`/`.clinic-look-note`. 본문 배치는 그대로. `.clinic-shell.clinic-shell`로 레이아웃 모듈의 `.shell.shell` 머리 규칙보다 앞선다 |
| 다른 섹션 안의 섹션 | C 최근 소식·진료시간(첫 화면 아래 줄)·진료 과정(진료 원칙 띠 안), A 질문 목록 바로 뒤 FAQ는 `.clinic-look-nested` 틀 — 바깥 섹션의 정렬이 들어오지 않고 자기 정렬만 건다(바깥이 정렬을 바꿨으면 자기 정렬이 없어도 경계 틀) |
| 질문 목록(B) | 옆 칸(`aside`)에 본문(처음 오시나요·전체 보기)이 함께 있어, 머리 묶음이 아니라 글줄(머리글·소제목·제목·안내)만 맞춘다 |
| 틀 | 정렬 틀은 `display: contents`라 상자를 만들지 않는다 — 감싼 섹션이 그대로 부모(C 첫 화면 아래 줄의 flex 등)의 자식으로 배치된다 |
| 가로 머리 줄 | 제목과 '전체 보기'가 한 줄인 머리(`.clinic-look-row` — A 질문 목록·병원 공간, C 최근 소식·질문·병원 공간)는 가운데일 때 세로로 쌓아 제목이 섹션 가운데에 온다 |
| A 진한 띠 | 오늘 진료 띠 위 소제목은 띠 글자색 |

## 18. ③-2c-2 섹션 배경(2026-11-21, 브랜치 `claude/site-config-3c2c2-bg`)

| 항목 | 정한 것 |
|---|---|
| 기본값 = 지금 마크업 | 레이아웃 기본(`DEFAULT_SECTION_BACKGROUND` — B 진료 원칙·진료 과정 tint, A 오늘 진료 band, C 진료 영역 tint·진료 원칙·진료 과정 band, 나머지 default)과 같으면 틀이 없다 |
| 바꿀 수 없는 섹션 | 지금 진한 띠인 섹션(A 오늘 진료, C 진료 원칙)·붙은 섹션(C 최근 소식·진료시간·진료 과정)·머리 없는 섹션(첫 화면·바로가기) — `BACKGROUND_FIXED`. 진한 바탕의 흰 글자를 밝은 바탕에 남기지 않기 위해서다. 어드민 숨김·서버 거절은 짝 PR |
| 칠하는 법 | 배경을 바꾼 섹션만 틀(`.clinic-look-bg`)이 화면 폭 상자가 되어 배경을 칠하고, 감싼 섹션의 자기 배경은 걷는다. default = 레이아웃 고유 바탕(덧칠 없음), white, tint(연한 대표색) |
| 진한 띠(레퍼런스 3-나) | 진한 대표색 띠 위에 머리(제목·안내·소제목·머리글)는 흰 글자, 머리 뒤의 본문 덩어리는 밝은 판(`--clinic-paper`, 둥근 모서리), 머리 뒤 링크·문단과 머리 안 버튼은 흰 글자. 본문 글자는 어두운 색 그대로 밝은 판 위에 있다 |
| 안쪽 섹션 | 진한 띠 규칙은 안쪽 섹션 틀(A 질문 목록 안 FAQ)에 들어가지 않고, 안쪽 틀은 자기 배경 규칙을 따로 갖는다. FAQ 배경 설정이 없으면 질문 목록의 배경을 따른다(한 덩어리로 보이므로). 진한 띠 안의 밝은 안쪽 틀은 띠의 흰 글자색을 물려받지 않는다. 질문 목록(B)은 오른쪽 본문·옆 칸 안내 상자를 밝은 판으로, 옆 칸 링크를 흰 글자로 |
| 대비 확인 | 로컬에서 B·A·C의 바꾼 섹션마다 글자와 실제 바탕의 대비 최솟값을 쟀다 — 모두 4.5:1 이상(가장 낮은 곳 4.77) |

## 19. ③-2d 첫 화면 배치·덮개·제목 크기·사진 초점(2026-11-21, 브랜치 `claude/site-config-3c2d-hero`)

| 항목 | 정한 것 |
|---|---|
| 기본값 = 지금 마크업 | 배치 좌우 분할·제목 보통·사진 초점 레이아웃 기본(B 위 48%, A·C 가운데)이면 첫 화면 섹션에 아무 표지도 붙지 않는다(`hero.look` null). 바꾼 칸만 표지(`data-hero-arrangement`·`-title`·`-veil`·`-focus`)와 변수(`--hero-title-scale`·`--hero-focus`·`--hero-veil-opacity`) |
| 사진 위에 글(레퍼런스 1-나) | 넓은 화면(B 921px~, A·C 901px~)만. 좁은 화면은 지금처럼 사진·글을 위아래로 쌓아 글이 사진 위에 놓이지 않는다. B는 사진과 글을 같은 칸에 겹친 둥근 사진 판, A·C는 첫 화면 전체에 사진을 깐다. 덮개 색은 레이아웃의 글 바탕(B 종이색, A 연한 대표색, C 진한 대표색 — C는 흰 글자). **글이 놓이는 왼쪽(50%와 글 칸 오른쪽 끝 중 큰 곳까지 — B 사진 판 왼쪽+580px, A `(100%-1280px)/2+640px`, C `(100%-1320px)/2+720px`)은 설정 농도와 무관하게 불투명한 글 바탕색**, 거기서 22% 동안 사라진다(Codex — 농도 0.6에서 어두운 사진 위 B 안내문 2.06:1, 0.9에서도 대표색 `#20A0A0` 병원의 지역·진료과 줄 3.64:1이었다. 불투명이면 지금 첫 화면과 같은 바탕). 설정 농도는 '사진 전체' 덮개(글 쪽 바탕 위에 한 겹 더)에만 쓴다 |
| 덮개 '없음' | 사진 위에 글인데 덮개가 '없음'이면 글 쪽 덮개를 기본 농도(0.6)로 깐다 — 편집기의 덮개 기본값이 '없음'이라 배치만 바꾸면 글이 사진에 맨바닥으로 놓인다 |
| 글만(레퍼런스 1-사) | 사진을 그리지 않는다(B 글 칸이 한 줄 폭, A 사진 칸 숨김, C 사진 없는 병원과 같은 대표색 바탕) |
| 제목 크기 | 작게 0.85배·크게 1.15배(제목과 강조 줄). 표지가 있을 때만 크기 규칙을 덧씌운다 — 기본 선언은 그대로 |
| 사진 초점 | 바뀌었을 때만 사진 `object-position` |

## 20. ③-2e 진료 원칙 기본 카드·오시는 길 항목(2026-11-21, 브랜치 `claude/site-config-3c2e-rest`)

| 항목 | 정한 것 |
|---|---|
| 진료 원칙 기본 카드(A·B·C) | `care_principles.use_default_cards=false`면 병원이 쓴 자리(`care_principles.cards[i]`가 있는 자리)만 남기고 빈 자리를 공통 기본 카드로 채우지 않는다. 그다음 카드 수로 자른다. 남는 카드가 0장이면 진료 원칙 섹션을 그리지 않는다(Codex — 제목뿐인 섹션·빈 카드 칸이 남았다; C는 진료 과정이 켜져 있으면 과정만 자기 띠에). 기본(true)은 지금 그대로 |
| 오시는 길(B) | 방문 전 확인의 주차·대중교통 순서(`visit.order`)와 대중교통 보이기(`show_transit`). 초진 준비물은 늘 마지막. 지금 순서·보임이면 null(지금 마크업). 오시는 길 섹션은 B에만 있다 — A·C에서는 읽지 않는다 |
| 그리지 않고 숨김·거절로 정리할 칸 | 진료 철학(`doctor.show_philosophy` — 공개 응답에 검수된 철학 문장이 없다, 자유 입력은 공개하지 않는다), 본문 글자 크기(`type.body_size` — A·C 글자가 고정 px라 일부만 커진다), FAQ 질문 합치기(`faq.merge_questions`), 진료 지역 표기(`region_tags`), 바로가기(`nav.section_index`), 글 보조 칸 오른쪽(`article.aside`), 머리 없는 섹션·바꿀 수 없는 배경 — 어드민 숨김 + 서버 거절 짝 PR(#358·#359 병합 뒤) |

## 21. ③-2f 줄바꿈(2026-11-21, 브랜치 `claude/site-config-3c2f-linebreaks`)

20절에서 숨김·거절 후보로 적은 줄바꿈(`hero.line_breaks`)은 거절하지 않고 그린다(#367 막음 목록에서도 뺐다).

| 항목 | 정한 것 |
|---|---|
| 그리는 칸 | 첫 화면 제목(`hero.headline`)·설명(`hero.description`), 진료 원칙 제목·안내(`care_principles.title`·`.lede`), 카드 제목·본문(`care_principles.cards[i].title`·`.body`, i는 카드 자리 — 기본 카드를 끄거나 카드 수를 줄여도 그 자리 번호). A·B·C 모두 |
| 글은 한 벌 | 원문을 한 번만 그리고 줄이 바뀌는 자리에 그 기기에서만 보이는 `<br>`(`clinic-br--pc` 721px~, `clinic-br--mobile` ~720px, 두 기기 같은 자리면 표지 없는 `<br>`)을 둔다. PC·휴대폰 글을 두 벌 그리면 답변 엔진·복사·화면 낭독이 같은 글을 두 번 읽는다 |
| 자리 계산 | 백엔드 `lines_match`와 같은 규칙(`site/lib/clinic-line-breaks.ts`) — 줄 경계가 원문 공백이면 공백 뒤, 단어 중간이면 그 자리. 공백은 지우지 않는다(그 기기에서 줄을 나누지 않으면 띄어쓰기로 남는다) |
| 맞지 않으면 지금 그대로 | 보이는 글이 지정한 원문과 다르면(섹션 제목을 바꿨거나 병원 정보가 바뀐 뒤 공개 판이 남은 경우) 그 칸은 줄바꿈 없이 지금 마크업이다. 공개 응답도 원문과 맞는 줄만 싣는다(`site_config_public`) |
| 첫 화면 제목 조각 | 제목이 앞 조각·강조 조각으로 나뉘면 조각마다 자기 시작 위치로 자리를 나눈다. 조각 경계의 줄바꿈은 그 조각이 앞 조각과 같은 줄에 이어질 때만 넣는다(A·C의 둘째 이후 앞 조각, B에서 병원이 줄을 정하지 않은 제목의 모든 조각 — Codex). 새 줄에서 시작하는 조각(A·C 강조 조각, B에서 병원이 줄을 정한 제목)은 이미 다른 줄이다. 지정이 없는 칸은 지금처럼 복합어 묶음(`KeepTogether`) |
| 알려진 한계 | 새 줄에서 시작하는 조각의 경계를 넘는 줄(A·C에서 앞 조각 끝과 강조 조각 앞을 한 줄로 지정)은 한 줄로 그릴 수 없어 두 줄로 보인다. 서버는 이 지정을 거절하지 않는다(줄을 이은 글은 원문과 같다) |
| 공백·줄 경계 | 백엔드(Python `split()`·`splitlines()`)와 같은 글자를 공백·줄 경계로 본다 — NEL·`\x1c`~`\x1f`는 공백, 끝 줄 경계 뒤에는 빈 줄이 없다. 원문의 줄바꿈·겹공백은 이은 모양(공백 하나)으로 그린다 |

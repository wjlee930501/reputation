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
| `config` | JSONB | 3절 스키마. 기본값과 **다른 칸만** 담는다(덮어쓰기 모양) |
| `schema_version` | int | 지금 1 |
| `revision` | int | 초안 낙관적 잠금 — 저장마다 +1, 클라이언트가 `If-Match`로 보낸다 |
| `based_on_id` | UUID null | 이 초안을 만든 공개 판(복원이면 복원한 판) |
| `origin` | enum `DEFAULT`·`EDIT`·`RESTORE`·`RESET` | 만든 방식 |
| `created_by` / `created_at` | actor 문자열 / ts | 초안을 처음 만든 사람 |
| `updated_by` / `updated_at` | | 마지막 저장 |
| `published_by` / `published_at` | | 공개 반영 |
| `change_summary` | JSONB | 직전 공개 판과 다른 섹션 키 목록(이력 화면용, 서버 계산) |

- 이력 = `PUBLISHED`·`SUPERSEDED` 판(행은 지우지 않는다, append-only). 복원은 옛 판의 `layout`·`config`를 **새 초안**으로 복사하고(`origin=RESTORE`), 공개 반영을 거쳐야 화면에 나간다.
- `hospitals.site_layout`은 그대로 둔다 — 계약 등록·지역 통계·옛 코드 롤백이 이 칸을 읽는다. 공개 반영 트랜잭션이 두 곳을 같이 쓴다. 설정 행이 없는 병원은 `site_layout`만으로 지금처럼 그린다(I6).
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
    "lines": { "pc": "365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료", "mobile": null },
    "blocks": ["today", "phone", "location", "hours"],   // 정보 블록 선택·순서: today·phone·location·booking·hours
    "image": { "focus": { "x": 0.5, "y": 0.35 }, "veil": { "opacity": 0.6, "extent": "text" } }  // extent: text | full | none
  },
  "copy": {                             // 고정 문구 덮어쓰기(비우면 공통 문구)
    "treatments.note": "…", "doctor.title": "…", "care_flow.title": "…",
    "hours.weekend_label": "토요일 진료"
  },
  "treatments": { "show_description": true, "style": "cards" },          // cards | list
  "doctor":     { "photo_ratio": "portrait", "title_position": "above", "show_philosophy": false },
  "care_principles": { "card_count": 3, "use_default_cards": true },
  "care_flow":  { "steps": [ { "title": "상담·문진", "body": "…" } ] },   // 2~6단계, 비우면 기본 4단계
  "visit":      { "order": ["parking", "bus", "subway"], "show_subway": false },
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

`hero` · `section_index`(B 목차·기본형 바로가기) · `news`(C 최근 소식) · `hours` · `treatments` ·
`doctor` · `care_principles` · `care_flow` · `gallery` · `questions`(자주 확인하는 질문) · `faq` ·
`facts`(기본 정보) · `visit`(오시는 길).

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

- 저장 값은 **줄 구분된 같은 문장**이다. `lines.pc`의 줄을 이으면(공백 하나) 원문 `hero_headline`과
  같아야 한다 — 글자를 바꾸는 통로가 되지 않게 서버가 비교해 거절한다(`LINES_TEXT_MISMATCH`).
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
3. 문구: 모든 사람 입력 글(섹션 제목·`copy.*`·`care_flow.steps`·`share.description`·`lines`)에
   길이 상한과 `check_forbidden` — 걸리면 칸 경로와 문제 단어를 돌려준다(I7). 줄바꿈 문장 일치(3.3).
4. 참조: `share.image_asset_id`는 그 병원의 공개 사진이고 사람 얼굴이면 실인물·신원 승인 조건을 넘어야 한다
   (`public/physicians.py`와 같은 판정). `faq.content_ids`는 그 병원의 **공개 FAQ 글 중 승인 질문·답이 있는 것**.
5. 메뉴: `nav.items`가 가리키는 섹션이 꺼져 있으면 경고(저장은 됨) — 공개 응답이 그 항목을 뺀다.
6. 실패는 422, 경고는 200 + `warnings`.

### 4.2 동시성·멱등

- 초안 저장은 `revision` 비교(다르면 409 `DRAFT_CHANGED`, 최신 초안 동봉).
- 공개 반영은 `Idempotency-Key` → `OperationRun`(`PUBLISH_SITE_CONFIG`) 영수증. 같은 키 재요청은 같은 결과.
- 공개 반영 직전 검증을 **다시** 돈다(초안 저장 뒤 사진이 비공개가 됐거나 FAQ 글이 내려간 경우).

## 5. 공개 응답·미리보기

- `GET /public/hospitals/{slug}`에 `site_config` 추가: 공개 판 완성본을 **공개용으로 다시 정리**한 값
  (`public_site_config`) — 금지 표현이 든 문구는 비우고, 공개가 끝난 사진·내려간 FAQ id는 빼고, 꺼진
  섹션을 가리키는 메뉴 항목은 뺀다. 설정 행이 없으면 `null`(I6). `schema_version` 동봉.
- 미리보기: `POST …/preview-token`이 `LEAD_REPORT_TOKEN_SECRET` 계열이 아닌 **새 비밀**
  (`SITE_PREVIEW_TOKEN_SECRET`, 10분, 병원·초안 revision 결합) 서명 토큰을 준다. 사이트
  `/{slug}?preview=<token>`은 `GET /public/hospitals/{slug}/site-config/preview?token=`에서 초안 완성본을
  받아 그린다 — 응답·페이지 `Cache-Control: no-store`, `noindex`, GA·픽셀 없음(설문 페이지와 같은 규칙),
  ISR 캐시를 쓰지 않는다. Admin은 iframe(390·1440)으로 띄운다. **[결정 S4]** 비밀 추가는 운영 env 변경.

## 6. 사이트 렌더링 규칙

- `page.tsx`는 지금처럼 데이터·JSON-LD를 **한 번** 만든다. `site_config`는 표현만 바꾼다.
- 홈 섹션 목록 = `site_config.sections`(완성본)에서 `enabled`인 것을 순서대로. 레이아웃 컴포넌트는 섹션
  id → 컴포넌트 표를 갖고 그리기만 한다. `site_config`가 `null`이면 지금 코드 경로 그대로(I6, 테스트로 고정).
- **I1**: `clinicJsonLd`(이름·주소·전화·진료시간·진료 항목·`employee` 전원)·`BreadcrumbList`·llms.txt는
  `site_config`를 읽지 않는다(소스 테스트로 고정 — `site_config`가 그 함수들에 들어가지 않음).
- **I2·I3 FAQ**:
  - `faq.mode=auto`: 지금 규칙(`selectFaqEntries`, PR #325의 같은 문장 1회) + `limit`.
  - `faq.mode=manual`: `content_ids` 순서대로, 승인 질문·답이 있는 공개 FAQ만. 질문 **문구 수정은 없다**(I8) — 문구가 문제면 글 쪽에서 고친다(결정 14).
  - FAQ 섹션이 꺼지면 홈 FAQPage도 없다(화면 = FAQPage). 각 FAQ 글 상세의 FAQPage는 그대로다.
  - `merge_questions=true`(결정 S2 = README 11번): 한 섹션 '자주 묻는 질문' 안에 위쪽은 답이 있는 FAQ(이것만 FAQPage), 아래쪽은 나머지 질문 링크. 같은 글·같은 문장 중복 금지(I3).
- **I4 의료진**: 꺼지지 않는다(결정 S3 권장). 사진 비율·초점·직함 위치·진료 철학 표시만 조절.
- 앵커: 꺼진 섹션의 `id`(`#treatments`·`#doctor`·`#faq`·`#facts`·`#contact`·`#contents`)는 사라지고,
  그 섹션을 가리키는 메뉴·바로가기·하단 바 항목도 함께 빠진다. 백엔드가 만드는 공개 사이트 링크는
  섹션 앵커를 쓰지 않는다(확인 필요 — 구현 PR에서 grep 테스트).
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

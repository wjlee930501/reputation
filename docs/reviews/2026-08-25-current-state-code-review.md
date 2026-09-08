# 현재 기준 전체 코드 리뷰 — 2026-08-25

> PRD 제외본. 이 문서는 현재 저장소에서 실제로 유효한 계약과 실행 증거만으로
> 기능 배선, 기능 누락, 프론트엔드 명료성을 다시 선별한 결과다.

## 리뷰 기준

이번 리뷰의 기준 우선순위는 다음과 같다.

1. 이번 리뷰에 대한 사용자 지시
2. 현재 프로젝트 가이드 `AGENTS.md`
3. 활성 디자인 계약 `DESIGN.md` (`Last refreshed: 2026-08-21`)
4. HEAD `e53b22d5c94475050576b6c3314921c981e40c65`의 코드, 테스트, 실제 실행 결과

아래 자료는 판단 근거에서 제외했다.

- `docs/prd/**`
- 과거 구현 계획과 deferred 항목
- 과거 PR 또는 커밋 메시지만으로 추정한 요구사항
- 현재 코드·현재 계약에서 다시 확인되지 않는 TODO성 아이디어

따라서 이 문서는 “예전에 만들기로 했지만 아직 없는 기능” 목록이 아니다. 현재 계약과
현재 구현이 충돌하거나, 이미 존재하는 기능의 배선이 끊겼거나, 실제 화면에서 다음 행동이
불명확한 문제만 다룬다.

## 결론

**판정: FAIL — 운영 승인 보류**

현재 가장 큰 위험은 기능 개수 부족이 아니라 다음 세 가지다.

1. 사람 승인으로 정의된 상태를 자동화가 대신 변경하는 운영 계약 충돌
2. `alembic head`와 실제 DB 구조가 달라지는 기존 설치 업그레이드 경로
3. 의료광고 금지 표현을 재생성하지 않고 문장에서 삭제하는 콘텐츠 안전 경로

이 세 가지를 먼저 정리한 뒤 API 응답 누락, 구조화 데이터, Admin 반응형과 접근성을
수정하는 순서가 안전하다.

---

## 1. 먼저 결정해야 하는 운영 계약

### C-1. 운영 기준 승인과 콘텐츠 발행의 주체가 계약과 다르다

**등급: Decision Blocker**

현재 `AGENTS.md`는 다음 상태 변경을 사람에게 소유시킨다.

- STEP 5: AE가 콘텐츠 운영 기준을 승인해 `APPROVED`로 전환
- STEP 8: AE가 초안을 확인하고 `[발행]`을 눌러 `PUBLISHED`로 전환

하지만 정상 실행 경로는 자동화가 두 상태를 직접 변경한다.

- `backend/app/services/essence_auto_review.py:965-1035`
  - 시스템 검토자가 운영 기준을 곧바로 `APPROVED` 처리
- `backend/app/workers/tasks.py:2377-2588`
  - 08:00 작업이 콘텐츠를 `SYSTEM_AUTO_PUBLISH`로 발행
- `admin/app/hospitals/[id]/essence/page.tsx:875-882`
  - 자동 승인 동작을 정상 흐름으로 안내
- `admin/app/hospitals/[id]/content/page.tsx:1599-1601`
  - 사전 승인 없이 자동 발행된다는 문구를 표시

수동 승인·수동 발행 API는 남아 있지만 정상 자동화가 우회한다.

**현재 기준에서 필요한 결정**

- `AGENTS.md`의 사람 승인 흐름을 유지한다면 자동화는 평가·차단·초안 작성까지만 하고
  `APPROVED`와 `PUBLISHED`는 AE actor만 기록해야 한다.
- 자동 승인·자동 발행이 현재 제품 정책이라면, 먼저 `AGENTS.md`를 현재 정책으로 갱신하고
  actor 필드와 Admin 문구를 “사람 승인”과 혼동되지 않는 상태 모델로 다시 정의해야 한다.

이 결정 전에는 한쪽 구현만 부분적으로 고치지 않는다.

### C-2. 병원 활성화 게이트가 현재 프로젝트 가이드와 다르다

**등급: Decision Blocker**

`AGENTS.md`의 STEP 7은 다음 네 가지와, 자체 도메인 사용 시 DNS 확인을 요구한다.

- `profile_complete`
- `v0_report_done`
- `site_built`
- `schedule_set`
- 자체 도메인 DNS 확인

현재 활성화 경로는 아래 세 가지만 검사한다.

- `backend/app/services/hospital_lifecycle.py:128-147`
- `backend/app/api/admin/hospitals.py:961-990`
- `admin/lib/hospital-activation.ts:1-42`

`schedule_set`은 의도적으로 제외되어 있고, `aeo_domain`이 있어도 DNS 확인 없이
`ACTIVE/site_live`가 될 수 있다. 테스트 역시 이 동작을 정상으로 고정한다.

- `backend/tests/test_hospital_lifecycle.py:40-43`
- `admin/lib/hospital-activation.test.ts:24-26`
- `backend/tests/test_default_address_activation.py:83-103`

이 항목도 C-1과 같이 먼저 현재 운영 정책을 확정한 후 Backend, Admin, 테스트를 함께
변경해야 한다.

---

## 2. 즉시 수정해야 하는 기능 배선

### W-1. 기존 설치에서는 `alembic head`여도 월간 리포트 스키마가 구버전으로 남는다

**등급: P0**

`0041_add_monthly_delivery_control.py`가 처음 적용된 뒤 같은 migration 파일에 다음 변경이
추가됐다.

- `monthly_measurement_manifests.closes_at` 추가
- `uq_monthly_reports_hospital_period_type` 제거

이미 `0041`을 적용한 DB는 Alembic revision만 보고 이 변경을 다시 실행하지 않는다.
실제 리뷰 DB는 `0057_mark_operations_test_accounts`인데도 다음 상태였다.

- `closes_at` 컬럼 없음
- 구 유니크 제약이 그대로 존재
- 새 `uq_monthly_reports_period_version`과 구 제약이 동시에 존재

그 결과 version 2 월간 리포트를 생성하는 경로가 `UniqueViolation`으로 실패한다.

관련 코드:

- `backend/alembic/versions/0041_add_monthly_delivery_control.py:41`
- `backend/alembic/versions/0041_add_monthly_delivery_control.py:192-214`
- `backend/app/models/report.py:26-46`

**수정 원칙**

1. 적용된 `0041`을 더 수정하지 않는다.
2. 기존 설치의 컬럼·제약 상태를 감지하고 보정하는 새 forward migration을 추가한다.
3. `0041` 적용 직후 상태에서 최신 head까지 올리는 업그레이드 회귀 테스트를 추가한다.
4. fresh DB와 upgraded DB의 핵심 제약·컬럼이 같은지 비교한다.

### W-2. 금지 표현을 재생성하지 않고 삭제한다

**등급: P0**

현재 콘텐츠 생성기는 금지 표현을 발견하면 문장을 자동 치환·삭제하고, 삭제 후에도 표현이
남은 경우에만 재생성한다.

- `backend/app/services/content_engine.py:505-525`
- `backend/app/services/content_engine.py:794-817`
- `backend/tests/test_content_engine.py:187-220`

이는 `AGENTS.md`의 “포함 시 재생성 트리거” 규칙과 다르다. 표현 일부를 제거하면 문법,
의학적 의미, 주장의 강도가 예상하지 못한 방식으로 바뀔 수 있다.

**기대 동작**

1. 최초 결과에서 금지 표현이 발견되면 위반 표현과 필드를 재생성 컨텍스트로 전달한다.
2. 새로 생성된 완전한 결과만 저장한다.
3. 재시도 소진 시 발행 가능한 콘텐츠를 만들지 않고 incident/outbox로 넘긴다.
4. 테스트는 “문자열이 사라졌다”가 아니라 “두 번째 완전한 콘텐츠가 저장됐다”를 검증한다.

### W-3. 콘텐츠 상세 FAQ JSON-LD가 실제로 없는 Q&A를 만들 수 있다

**등급: P1**

`site/app/[slug]/contents/[contentId]/page.tsx:298-314`는 FAQ 전용 필드가 없을 때
다음을 fallback으로 사용한다.

- 질문: 콘텐츠 제목
- 답변: 메타 설명

반면 같은 페이지의 화면상 FAQ는 실제 `faq_question`과 `faq_answer_summary`가 있을 때만
렌더링된다 (`:466-470`). 따라서 사용자에게 보이지 않는 Q&A가 JSON-LD에만 존재할 수 있다.

공유 헬퍼 `site/lib/schema.ts:43-73`는 이미 이 fallback을 금지하므로 콘텐츠 상세도 같은
헬퍼 또는 같은 계약을 사용해야 한다.

### W-4. 도메인 실시간 확인 결과가 응답 모델에서 제거된다

**등급: P1**

도메인 확인 값은 DB와 serializer에는 있지만 FastAPI 응답 모델에는 없다.

- 저장: `backend/app/models/hospital.py:132-134`
- serializer: `backend/app/api/admin/hospitals.py:1427-1434`
- 누락된 응답 모델: `backend/app/schemas/hospital.py:6-68`
- response model 적용: `backend/app/api/admin/hospitals.py:575-592`

Admin은 이 값을 소비해 헤더와 도메인 상태를 표시하려고 한다.

- `admin/app/hospitals/[id]/layout.tsx:55`
- `admin/app/hospitals/[id]/layout.tsx:237-240`

현재는 `domain_last_checked_at`, `domain_last_check_ok`, 실패 사유가 응답 필터링 단계에서
제거되므로 실제 확인 결과가 UI에 도달하지 않는다.

### W-5. Flower가 기본 비밀번호로 전체 인터페이스에 노출된다

**등급: P1 / Security**

- `docker-compose.yml:80` — `admin:changeme` fallback
- `docker-compose.yml:82` — `5555:5555` 전체 인터페이스 바인딩

기본 compose를 네트워크에 연결된 개발 장비에서 실행하면 알려진 계정으로 Celery 메타데이터와
제어 표면에 접근할 수 있다.

**기대 동작**

- 기본 바인딩을 `127.0.0.1:5555:5555`로 제한
- placeholder credential fallback 제거
- 명시적 credential이 없으면 Flower 시작 실패
- 운영에서는 VPN/IAP 등 별도 접근 계층 사용

---

## 3. 프론트엔드 명료성

### UI-1. 768px 병원 목록에서 핵심 열과 상세 진입 액션이 잘린다

**등급: P1**

유효한 HEAD 캡처 `admin-hospital-list-768.png`에서 오른쪽 열이 `병…`에서 끊기고 병원
정보 허브, 스케줄, 상세 진입 액션이 화면 밖에 있다. 수평 스크롤 affordance도 보이지 않는다.

원인:

- `admin/app/hospitals/page.tsx:235-247` — 테이블 `min-w-[980px]`
- `admin/app/globals.css:108-188` — 카드 재구성은 639px 이하에서만 적용

`DESIGN.md`는 768px 태블릿에서 테이블을 카드/키-값 행으로 재구성하고, 중요 데이터와
컨트롤을 가로로 잘라내지 않도록 요구한다.

**기대 동작** — 카드 전환 범위를 768px까지 넓히거나, 열 우선순위를 재구성하고 명시적인
스크롤 힌트를 제공한다.

### UI-2. 병원 상세 URL의 404가 환자를 B2B 랜딩으로 보낸다

**등급: P1**

없는 콘텐츠·진료 상세는 `notFound()`를 호출하지만 병원 slug 범위의 404가 없다.

- `site/app/[slug]/contents/[contentId]/page.tsx:163`
- `site/app/[slug]/treatments/[treatmentSlug]/page.tsx:117`
- `site/app/not-found.tsx:12-23`

전역 404의 유일한 복구 링크는 `/`이며, 이 경로는 병원을 찾는 환자가 아니라 병원 고객용
Re:putation 랜딩이다. 검색 결과의 오래된 상세 URL로 들어온 환자가 병원명, 전화, 진료시간,
길찾기 맥락을 모두 잃는다.

**기대 동작** — `site/app/[slug]/not-found.tsx`에서 병원 홈, 진료시간·오시는 길, 전화
연결을 제공한다. 알 수 없는 병원 slug에는 기존 전역 404를 유지한다.

### UI-3. 공개 주요 페이지의 skip link 목적지가 없다

**등급: P1 / Accessibility**

루트 layout은 항상 `#main-content` 링크를 제공하지만 아래 페이지에는 해당 ID가 없다.

- `site/app/layout.tsx:95-100`
- `site/app/ai-diagnosis/page.tsx:18`
- `site/app/ai-diagnosis/status/[token]/page.tsx:19`
- `site/app/error.tsx:7`
- `site/app/not-found.tsx:12`

키보드 사용자가 “본문으로 바로가기”를 실행해도 포커스가 이동하지 않는다. 각 상태에 하나의
`main id="main-content"`를 보장해야 한다.

### UI-4. 계정 관리의 모바일 액션과 비밀번호 모달이 Admin 계약을 충족하지 않는다

**등급: P1 / Accessibility**

- `admin/app/accounts/page.tsx:279-398` — 5열 테이블을 일반 가로 스크롤로 유지
- `admin/app/accounts/page.tsx:344-357` — 중요 액션 높이가 약 28px
- `admin/app/accounts/page.tsx:402-442` — dialog role, label 연결, 초기 포커스,
  focus trap, Escape/닫기 후 포커스 복원 없음

`DESIGN.md`의 Admin 최소 컨트롤 높이는 44px다. 계정 비활성화 이유도 hover `title`에만
있어 터치 환경에서는 확인하기 어렵다.

### UI-5. 오류와 로딩 상태가 서로 다른 사실을 말한다

**등급: P2**

실제 캡처에서 일부 병원 데이터 요청이 429로 실패했지만 공통 헤더는 계속
“병원 불러오는 중 / 공개 주소 확인 중”으로 남고, 본문은 원시 영문 `Too many requests`를
표시했다.

- `admin/app/hospitals/[id]/layout.tsx:133-145`
- `admin/app/hospitals/[id]/essence/page.tsx:242`

실패를 로딩으로 표시하지 말고, 한국어로 제한 범위와 재시도 시점·다음 행동을 안내해야 한다.

### UI-6. 모바일 노출 보완 화면의 버튼 라벨이 한 글자씩 접힌다

**등급: P2**

`admin-exposure-actions-375.png`에서 “새로고침”이 한글 한 음절씩 세로로 접힌다.

- `admin/app/hospitals/[id]/exposure-actions/page.tsx:398-412`

모바일에서는 제목 아래 독립 행으로 옮기거나 `white-space: nowrap`과 충분한 최소 폭을
소유한 컨트롤로 배치한다.

---

## 4. 데이터 계약 정리 대상

### D-1. PostgreSQL enum과 ORM enum이 다르다

**등급: P2**

`0052_add_photo_asset_provenance.py:21`은 `hospital_source_type`에 `PHOTO_BRAND`를
추가하지만 `backend/app/models/essence.py:34`의 `SourceType`에는 없다.

DB에 해당 값이 한 건이라도 들어오면 SQLAlchemy가 enum을 역직렬화하지 못해
`backend/app/api/admin/essence.py:439`의 자료 목록 조회가 실패할 수 있다.

사용하지 않는 값이라면 forward migration으로 제거하고, 사용할 값이라면 ORM·스키마·Admin
생성/표시 경로까지 함께 연결한다.

### D-2. ORM `create_all()` 스키마가 migration 스키마보다 약하다

**등급: P2 / Test fidelity**

리드 전환 제약, 월간 리포트 품질·버전 체인 제약과 트리거 중 일부가 migration에만 있고
ORM metadata에는 없다. `Base.metadata.create_all()`로 DB를 만드는 테스트는 운영 DB가
거부할 상태를 허용할 수 있다.

대표 경로:

- `backend/app/models/lead_diagnosis.py:94`
- `backend/app/models/report.py:26`
- `backend/tests/test_monthly_period_postgres.py:57`

실제 제약을 검증하는 통합 테스트는 Alembic으로 만든 DB를 사용하고, ORM metadata는 표현할
수 있는 제약을 migration과 맞춘다.

---

## 5. 테스트 결과와 남은 공백

### 실행 결과

- Admin: 500 tests 통과, lint/typecheck/production build 통과
- Site: 295 tests 통과, lint/typecheck/production build 통과
- Backend 전체: **2,171 passed / 8 failed / 8 skipped**
- Backend 잔여 실패의 주원인: W-1의 기존 설치 migration drift
- Admin 유효 반응형 캡처: 375/768/1280 기준 45장
- 공개 Site 시각 QA: pre-existing stale build가 섞여 HEAD 판정 불가, **INCONCLUSIVE**

### 테스트가 강한 영역

- Admin BFF session/CSRF/backend header 경계
- 공개 tenant와 published content 필터
- Celery route/include/queue 설정
- idempotency, outbox, recovery의 개별 경로
- Admin/Site TypeScript 및 helper 단위 계약

### 보완이 필요한 영역

1. 프로필 저장 → V0 → site 준비 → 일정 → 활성화 → 생성 → 발행 → 월간 리포트를 한 번에
   검증하는 CI lifecycle 테스트가 없다.
2. Backend와 Site는 각자 mock된 응답을 검사하지만 같은 실제 계약을 함께 검증하지 않는다.
3. Slack onboarding 테스트 일부는 실행 결과가 아니라 `tasks.py` 문자열 위치를 비교한다.
4. 활성화와 금지 표현 테스트가 현재 프로젝트 가이드와 다른 동작을 정답으로 고정한다.
5. 태블릿·모바일의 실제 렌더링과 404/empty/error 상태가 CI에 포함되지 않는다.

---

## 6. 현재 잘 연결된 부분

다음 영역은 이번 리뷰에서 유지 대상으로 판단했다.

- Admin BFF의 signed session, same-origin/CSRF, backend-only admin header 경계
- 공개 병원의 `ACTIVE + site_live` 기본 tenant 차단과 published content 필터
- Worker의 task include/route/queue, dispatch 인증, idempotency와 outbox 구조
- 보고서 Admin 목록·상세·다운로드·전달 액션의 API 매핑
- Site의 공개 API runtime parse와 ISR/fail-on-5xx 처리
- Admin 모바일의 현재 병원·현재 작업 우선 구조
- 공개 clinic shell의 전화·진료시간·진료안내·길찾기 모바일 접근 경로
- 실제 인물 사진이 없을 때 가짜 의료인 이미지를 사용하지 않는 fallback

---

## 7. 이전 리뷰에서 제외한 항목

다음은 현재 코드의 결함이라기보다 PRD·과거 계획에서만 요구한 기능이므로 이번 수정 목록에서
제외했다.

- 고객 전달용 onboarding preview/share URL
- “Webblog Coverage” 이름의 별도 운영 기능
- 공개 사실·JSON-LD·`llms.txt`·sitemap을 한 화면에서 비교하는 신규 detector
- 공식 외부 링크 health checker 신규 기능
- hero focal point Admin 조절 기능
- 과거 PR의 미병합 변경 자체를 현재 요구사항으로 간주한 항목

이 기능들은 별도의 현재 요구가 다시 확정되기 전까지 누락 기능으로 취급하지 않는다.

---

## 8. 권장 처리 순서

1. **운영 정책 확정** — C-1, C-2
2. **운영 데이터 안전성** — W-1 forward migration
3. **의료 콘텐츠 안전성** — W-2 재생성 경로
4. **공개 사실 정확성** — W-3 FAQ JSON-LD
5. **Admin 배선** — W-4 도메인 응답 모델
6. **보안 기본값** — W-5 Flower
7. **프론트엔드 P1** — UI-1부터 UI-4
8. **데이터 계약·오류 문구·모바일 세부** — UI-5, UI-6, D-1, D-2
9. **CI lifecycle/contract/browser 테스트**

각 단계는 현재 동작을 재현하는 회귀 테스트를 먼저 추가하고, 관련 Backend·Admin·Site를 한
작업 단위로 함께 변경한다.

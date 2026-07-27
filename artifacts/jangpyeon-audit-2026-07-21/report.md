# 장편한외과의원 온보딩 시스템 감사

감사일: 2026-07-21  
대상: `https://jangclinic.kr`, `https://reputation.motionlabs.kr/jangpyeonhanoegwayiweon`, 관리자 온보딩 코드

## 결론

공개 사이트와 커스텀 도메인은 정상 서비스 중이며 홈, 의료진, 콘텐츠 목록·상세, 방문 안내, robots.txt, llms.txt, 404 처리는 정상 동작한다. 다만 검색엔진 정합성 1건과 사용자 기능 오류 1건을 우선 수정해야 한다. 콘텐츠 상세의 문서 구조와 날짜 표기도 함께 정리하는 것이 좋다.

## 흐름별 상태

### 1. 커스텀 도메인 홈 — 양호

![커스텀 도메인 홈](01-public-custom-domain.png)

- `https://jangclinic.kr/`가 정상 응답한다.
- 병원명, 진료 영역, 원장 사진, 전화·오시는 길 CTA가 첫 화면에 안정적으로 표시된다.
- 플랫폼 주소의 canonical 및 Open Graph URL도 `https://jangclinic.kr`로 설정되어 있다.

### 2. 의료 정보 목록 — 양호

![의료 정보 목록](04-public-content-list.png)

- 유형별 필터와 10개 콘텐츠가 정상 노출된다.
- 제목, 요약, 발행일, 유형 구분이 명확하다.
- 상단 소개 영역의 세로 여백이 커서 첫 콘텐츠가 900px 높이 화면에서 일부만 보이지만 기능 문제는 아니다.

### 3. 의료 정보 상세 — 주의 필요

![의료 정보 상세](05-public-content-detail.png)

![본문에서 반복되는 제목](10-public-content-duplicate-h1.png)

- canonical 상세 URL과 직접 접근은 정상이다.
- 동일 제목의 `h1`이 페이지 헤더와 Markdown 본문에 두 번 존재한다. 화면상 두 번째 제목은 작게 보이지만 문서 구조상 두 개의 `h1`이다.
- `발행 2026년 7월 20일` 뒤에 `최근 업데이트 2026년 7월 19일`이 표시된다. 업데이트가 발행보다 과거이면 숨기거나 발행일을 사용해야 한다.
- 참고자료와 의료진 직접 감수 여부 고지는 명확하다.

### 4. 의료진 — 양호

![의료진 페이지](09-public-doctor-after-load.png)

- 실제 원장 사진은 지연 로딩 후 정상 표시된다.
- 로딩 전 모노그램 fallback이 있어 빈 이미지나 레이아웃 이동을 피한다.
- 약력, 자격, 학회, 관련 콘텐츠 연결이 정상이다.

### 5. 방문 안내 — 수정 필요

![방문 안내](08-public-visit.png)

- 전화, 지도, 공식 채널 링크는 정상이다.
- `진료시간 보기` 카드가 현재 `/visit` 페이지 자기 자신으로 연결된다.
- 페이지에 요일별 진료시간 표나 대상 앵커가 없다. 전달된 `businessHours`도 컴포넌트에서 사용되지 않는다.

### 6. 관리자 진입 — 로그인으로 차단

![관리자 로그인](03-admin-entry.png)

- 로그인 화면은 정상 렌더링된다.
- 현재 Aside 세션에는 관리자 로그인이 없어 장편한외과의원 온보딩, 프로필, 콘텐츠, 도메인 화면의 실사용 상태를 캡처하지 못했다.
- 코드상 온보딩의 URL·블로그·파일 입력은 placeholder에 의존하며 연결된 label이 없다. 처리 완료·실패 메시지도 live region이 아니어서 스크린리더가 상태 변화를 놓칠 수 있다.
- 프로필 자동 채우기 모달은 `role="dialog"`, `aria-modal`, 포커스 이동·복귀가 보이지 않는다.

## 우선순위별 발견사항

1. 높음 — 커스텀 도메인 사이트맵이 비정규 URL을 제출한다.
   - `https://jangclinic.kr/sitemap.xml`의 28개 URL이 `/jangpyeonhanoegwayiweon/...`을 포함한다.
   - 확인한 URL 대부분이 308로 깨끗한 canonical 경로에 다시 이동한다.
   - `appendHospitalEntries`가 host scope에서도 항상 병원 slug를 경로에 붙이고, 현재 단위 테스트도 그 잘못된 계약을 기대한다.

2. 중간 — 방문 페이지의 진료시간 CTA가 무동작이다.
   - 버튼은 `/visit` 자기 자신으로 연결된다.
   - `businessHours` prop은 선언·전달되지만 `ContactCard`에서 구조 분해하거나 렌더링하지 않는다.

3. 중간 — 콘텐츠 상세에 중복 `h1`이 있다.
   - 페이지 템플릿이 제목 `h1`을 렌더링하고, Markdown의 `# 제목`도 기본 `h1`으로 렌더링한다.
   - 생성·저장 시 선두 H1을 제거하거나 ReactMarkdown의 `h1`을 `h2`로 내리는 방어가 필요하다.

4. 낮음 — 업데이트 날짜가 발행일보다 과거로 표시된다.
   - `body_updated_at`이 존재하고 날짜 문자열이 발행일과 다르기만 하면 무조건 노출한다.
   - `body_updated_at > published_at`일 때만 “최근 업데이트”로 표시해야 한다.

5. 접근성 위험 — 관리자 온보딩 입력과 자동 채우기 모달의 이름·상태 전달이 부족하다.
   - 실화면은 로그인으로 검증하지 못했으며 코드 기반 판단이다.

## 코드·테스트 결과

- Admin 단위 테스트: 139/139 통과
- Site 단위 테스트: 99/99 통과
- Admin/Site TypeScript 검사: 통과
- Admin/Site ESLint: 통과
- Backend 공개 사이트·도메인 조회·Essence readiness·production readiness: 47/47 통과
- Backend Ruff: 통과
- 도메인/병원 lifecycle 관련 backend 테스트 3개는 pull 후 로컬 `.venv`에 `google-cloud-certificate-manager`가 설치되지 않아 수집되지 않았다. 의존성은 `pyproject.toml`과 `uv.lock`에는 존재하므로 코드 실패가 아니라 로컬 환경 동기화 문제다.

## 증거 한계

- 데스크톱 1440px 화면만 캡처했다. 모바일 reflow, 200% 확대, 키보드 전체 순회, 스크린리더 발화는 별도 검증이 필요하다.
- 관리자 내부 화면은 인증 세션이 없어 코드 검사만 수행했다.
- 저장·발행·도메인 연결 같은 운영 변경 액션은 실행하지 않았다.

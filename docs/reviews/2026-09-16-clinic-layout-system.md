# 병원 공개 사이트 — 레이아웃·타이포그래피 정비

상태: **LOCAL_DESIGN_IMPLEMENTED_PRODUCTION_BUILD_VERIFIED_NOT_DEPLOYED**.
브랜치: `design/clinic-layout-system-20260916`. 기준선: `c20d26b1a6666c1aa3826b7dc832ceac96a11d29`.
운영 배포·원격 push는 하지 않았다. 정본 수치와 파일 해시는 같은 이름의 `-validation.json`에 있다.

## 확인한 문제

실제 공개 페이지의 computed style을 측정했다. 장편한외과 390px에서 헤더/히어로/진료 영역/
본문의 좌측 시작점은 각각 16/22/36/20px였다. 1440px에서는 헤더와 본문이 120px인데
히어로만 48px였다. 여러 시점의 CSS shorthand·미디어 쿼리·중첩 padding이 토큰을 덮었다.
기존 첫 번째 CSS 문자열만 찾는 테스트는 이러한 최종 브라우저 값을 검증하지 못했다.

## 변경한 공통 규칙

| 역할 | PC | 태블릿 | 모바일 |
|---|---|---|---|
| 콘텐츠 최대 너비 | 1,200px | 가용 너비 | 가용 너비 |
| 최소 좌우 여백 | 48px | 32px (≤1,024px) | 20px (≤720px) |
| 일반 섹션 상하 간격 | 80px | 64px | 48px |
| 짧은 섹션 상하 간격 | 48px | 40px | 32px |
| 섹션 제목→내용 | 32px | 32px | 24px |
| 주요 H1 / 섹션 H2 | 36–48px / 32px | 화면 폭에 따른 display / 32px | 34px / 26px |
| 서브페이지 H1 | 40px | 40px | 30px |
| 주요 카드·의료진 H3 | 22px | 22px | 22px |
| 본문 / 보조 정보 | 16px / 14px | 동일 | 동일 |
| 글 상세 본문 | 18px, 행간 1.85 | 18px | 17px, 행간 1.85 |

`globals.css`의 `Clinic layout system`이 geometry/type을 소유한다. 대응하는 기존 선언
630개를 제거했으며 새 블록만 추가해서 우선순위를 더 꼬는 방식을 사용하지 않았다.
색상은 기존 승인된 병원 palette를 유지한다. 그림자·그라데이션·새 이미지·새 라이브러리는
운영 코드에 추가하지 않았다. 메타 텍스트는 기존 중립색 토큰 중 읽을 수 있는 단계로 맞췄다.

히어로는 제목과 실사진을 동일한 콘텐츠 rail 안에 배치한다. 모바일 이미지는 16:9로
재구성하고, 이미지 없는 병원은 가짜 이미지/빈 우측 칸 없이 타이포그래피로 구성한다.
진료 항목은 2열 설명형 목록/모바일 1열로 바꿨다. 짧은 소개를 2줄까지 읽을 수 있으며
전체 내용은 기존 상세 페이지로 이어진다. 의료진은 모바일 사진·이름 옆의 좁은 공간에
약력을 끼우지 않고 소개·자격을 전체 폭으로 읽게 했다.

헤더·사실 요약·섹션·콘텐츠 목록·글 상세·푸터가 같은 너비 규칙을 따른다. 하위 페이지의
inline 14px 본문과 개별 간격 예외는 공통 역할 클래스로 바꿨다. treatments/contact ID의
중복을 없앴으며, 데이터가 없는 선택 섹션은 페이지 내 바로가기에서도 숨긴다.

## 검증

잠금 파일대로 설치한 Next.js 16.3.3의 standalone production build에서 확인했다.
실제 공개 데이터의 read-only snapshot을 사용했고 fixture API는 localhost에서 GET만 받는다.
미디어는 기존 승인된 공개 HTTPS URL을 사용했다. 실제 고객 데이터나 원고를 수정하지 않았다.

- Site 단위·계약 테스트: **338 passed / 0 failed / 0 skipped**.
- Site lint·typecheck·production build: 모두 통과.
- 공개 스냅샷 9개 병원 × 7개 폭의 홈 + 대표 3개 병원의 하위 5종 페이지 × 3개 폭:
  **108개 화면 조합 통과**.
- 로컬 검증용 데이터 스트레스 6종 × 7개 폭: **42개 화면 조합 통과**.
- 최종 production 렌더 검사: 총 **150개 통과**. 섹션 좌우 rail, 가로 overflow,
  H1/H2 위계, 본문 최소 크기, 프로젝트의 주요 모바일 44px 터치 영역, 고유 ID,
  존재하는 anchor, JSON-LD 존재, 브라우저 runtime error를 검사했다.
- 장편한외과의 최종 좌측 시작점: 390/768/1280/1440px에서 각각 **20/32/48/120px**.
- 사용자 문구 guard와 `git diff --check` 통과. Backend/Admin/인프라/lock 파일 diff 없음. 비병원 화면 CSS 계약 1,719개도
  수정 전후 일치하므로 플랫폼 랜딩·무료 진단의 스타일은 바꾸지 않았다.

실제 브라우저 크기 계산을 검증하는 `scripts/verify_clinic_layout.mjs`를 추가했다.
기존 CSS 검사도 first-match 대신 전체 selector/declaration을 읽도록 고쳐 이후 복귀를 잡는다.
새 `clinic-layout-contract.test.ts`는 선택 섹션 링크와 중복 anchor/inline 예외를 보호한다.
이는 WCAG 적합성 인증, 실제 브라우저 확대 조작, 모든 키보드 경로의 E2E 완료를 뜻하지 않는다.

```bash
cd site
npm ci
npm test
npm run lint
npm run typecheck
npm run build
cd ..
# 별도 설치한 Playwright + 읽기 전용 fixture API를 사용하는 실제 렌더 검사:
node scripts/verify_clinic_layout.mjs --base-url http://127.0.0.1:PORT \
  --fixtures-dir /path/to/public-data --output-dir /path/to/evidence
```

위 렌더 명령은 저장소 루트에서 실행한다. 별도 Playwright 설치는 `PLAYWRIGHT_MODULE`,
Chrome 실행 파일은 `CHROME_BIN` 환경변수로 전달할 수 있다. `--home-only`는 스트레스 fixture용이다.
Mac mini의 production 검수 포트는 `59681`, 링크 이동까지 확인할 수 있는
격리 개발 미리보기 포트는 `57573`다. 둘 다 원본과 분리한 공개 데이터 복사본이다. 실제 custom domain은
fixture 응답에서만 로컬 경로로 정규화했고 운영 host-routing 코드는 수정하지 않았다.

화면 이미지/측정값/로그: `/private/tmp/reputation-clinic-design-20260916-kkxgact4`. 파일이 큰 full-page 캡처는 lazy 이미지나 긴 지면의
합성 방식에 영향을 받을 수 있어 헤더 영역과 의료진·진료 영역 crop도 함께 검토했다.
합성 검증 데이터는 명시적으로 ‘레이아웃 검증용’으로 구분해 로컬에만 두며 커밋/배포하지 않는다.
원래 `aside-test.js`는 SHA-256으로 불변을 확인했고 커밋 대상에서 제외한다.

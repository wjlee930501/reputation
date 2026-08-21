# 병원 공개 사이트 디자인 시스템 교정 후 검증

- 기준선: `artifacts/visual-system-audit-2026-08-21/system-report.md`
- 대상: ACTIVE 공개 병원 6곳
- 변경 화면 캡처: 병원별 홈 desktop 1440×900 / mobile 390×844, 총 12장
- 구조 검증: 병원별 홈·콘텐츠 목록·첫 실제 콘텐츠 상세·의료진·진료 영역·방문 안내 × 2 viewport, 총 72 checks

## 적용한 시스템 교정

1. 병원 대표색을 semantic theme으로 연결하고, 버튼 전경과 focus ring 대비를 자동 선택했다.
2. slug 전용 hero 문구·사진 예외를 제거하고, 검증된 시설 자산 → 밝은 typographic panel 순서로 통일했다.
3. hero/header 진료과 표시는 최대 2개로 제한했다.
4. 콘텐츠 0–2개 병원은 반복 질문 모듈을 생략하고, 사진 갤러리는 기본 6장·최대 8장으로 제한했다.
5. 모바일 4개 행동 바를 shared header로 이동해 모든 route에서 유지했다.
6. 의료진의 `대표원장` 중복 표기를 제거하고 slug 기반 인물 대체 이미지를 금지했다.
7. 모바일 article table은 필요할 때만 자연스럽게 넓어지고, 좌우 이동 안내를 보이도록 했다.
8. 온보딩 원본 이미지는 Next Image responsive optimizer를 거치며 84 quality를 명시적으로 허용했다.

## 자동 검증 결과

72/72 checks 통과:

- HTTP 200
- `.clinic-shell` 존재
- visible H1 정확히 1개
- document horizontal overflow 0
- desktop mobile action bar hidden
- mobile action bar `display:grid`
- 완료된 이미지 중 `naturalWidth=0` 0개

단위/정적 검증:

- site tests: 213 passed
- TypeScript: passed
- ESLint: passed
- production build: passed with 6 hospitals and 55 treatment static paths generated

## 증거 한계

Aside REPL은 기존 라이브 기준선 72장을 캡처하는 데 사용했다. 이번 로컬 변경 검증에서는 Aside의 격리 브라우저가 host loopback에 접근하지 못해, 동일 Chromium 계열의 로컬 Playwright로 변경 화면과 72개 구조 조건을 검증했다. 배포 전 라이브 주소에 대한 Aside 재캡처는 별도 release gate로 남는다.

의료진 사진의 provenance는 현재 API payload에 별도 필드가 없어 화면 코드만으로 진위를 확정할 수 없다. 이번 교정은 hero에서 시설 사진만 우선하고 slug 기반 인물 artwork를 제거했지만, doctor identity 슬롯의 원본 승인 상태는 온보딩 데이터 모델 후속 작업이 필요하다.

# 장편한외과 홈페이지 디자인 QA

- source visual: `/Users/woojinlee/.codex/generated_images/019f638b-9f9c-7b10-90f6-a9458a9455f8/exec-23586333-85eb-4354-bfb2-a30f5aaa435b.png`
- desktop implementation: `/Users/woojinlee/projects/Reputation/tmp/jangclinic-qa/implementation-desktop.png`
- mobile implementation: `/Users/woojinlee/projects/Reputation/tmp/jangclinic-qa/implementation-mobile.png`
- combined comparison input: `/Users/woojinlee/projects/Reputation/tmp/jangclinic-qa/compare-desktop.png`
- tested route: `http://localhost:3000/jangpyeonhanoegwayiweon`

## Pass history

### Pass 1

- P1 · layout: 대표 진료가 2열 디렉터리와 별도 제목으로 렌더되어 소스의 4열 인덱스보다 첫 화면이 길어졌다.
  - fix: 홈페이지에는 대표 4개만 남기고 4열 번호형 인덱스로 변경했다. 전체 진료는 기존 진료 영역 페이지에서 탐색한다.
- P2 · layout: 의료진 섹션의 중복 도입부가 프로필 사진을 첫 화면 아래로 밀었다.
  - fix: 로컬 원장 사진이 있는 장편한외과에서는 도입부를 접근성 텍스트로 유지하되 시각적으로 숨기고 섹션 상단 여백을 줄였다.
- P2 · performance: 갤러리의 공개 API 이미지를 원본 크기로 우회 전달하고 있었다.
  - fix: Next Image 최적화 경로를 사용해 반응형 `srcset`과 AVIF/WebP 전달을 적용했다.

### Pass 2

- typography: 소스의 굵은 에디토리얼 헤드라인, 행간, 파란 강조 위계가 유지됨.
- layout: 헤더, 양분형 히어로, 정보 레일, 4열 진료 인덱스, 의료진 시작 순서가 소스와 일치함.
- color: 아이보리 배경, 잉크 네이비, 코발트 포인트, 얇은 회색 구분선이 일관됨.
- imagery: 제공된 원장 사진 2장을 히어로와 프로필에 각각 사용했고 인물 크롭 및 선명도를 확인함.
- icons: 기존 아이콘 라이브러리의 동일한 스트로크 계열을 사용하고 버튼·정보 레일에서 정렬을 확인함.
- responsiveness: 1536×1024 및 390×844에서 겹침, 잘림, 가로 넘침 없음. 모바일 하단 고정 액션의 탭 영역을 확인함.
- accessibility: H1/H2 구조, 대체 텍스트, 전화·길찾기 링크, 모바일 내비게이션 라벨, 시각적으로 숨긴 설명 텍스트를 확인함.
- interactions: 오시는 길과 대표 진료 상세 링크가 올바른 경로로 이동함.
- console: error/warning 없음.

## Verification

- `npm run typecheck`: passed
- `npm run lint`: passed
- `npm test`: 78 passed
- production `npm run build`: passed
- `git diff --check`: passed

final result: passed

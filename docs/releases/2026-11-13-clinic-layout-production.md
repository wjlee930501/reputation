# 2026-11-13 병원 사이트 레이아웃 A/B/C 운영 배포

상태: **준비 — 아직 배포하지 않았다.** 배포하면 이 문서의 '실행 기록'을 채운다.

## 범위
- PR #306(`claude/clinic-layout-abc`). 설계·결정은 [계획 문서](../plans/2026-11-13-clinic-site-layouts-abc.md).
- 운영은 이미 main `6c21141d`(PR #305)로 돌고 있다(`/api/v1/health/live` release, 2026-11-13 17:44 KST 확인). 그래서 이번 배포에 실리는 변경은 이 PR뿐이다.
- 대상: **5개 서비스 전부**(`deploy.sh all`) + 마이그레이션 `0095_add_hospital_site_layout`.
  - DB: `hospitals.site_layout VARCHAR(1) NULL` + CHECK(`A`/`B`/`C`). 추가형이고 기존 행은 NULL이다. 병원 행이 20개 남짓이라 CHECK 검증 잠금은 순간이다.
  - Backend: 프로필 PATCH·계약 등록·공개 API의 `site_layout`, `GET /admin/hospitals/site-layout-distribution`. Worker·Beat는 같은 이미지라 함께 바뀌지만 동작 변화는 없다.
  - Site: 레이아웃 A/C 홈, B CSS, 하위 페이지 셸, 명조 서체(`/fonts/clinic-serif/`).
  - Admin: 계약 등록 필수 선택, 병원 정보에서 변경, BFF 반복 질의값 보존.

## 배포해도 바뀌지 않는 것
- 운영 공개 병원의 화면. 모든 병원이 NULL이라 레이아웃 도입 전 구성 그대로 그린다. 바뀌는 것은 마케터가 레이아웃을 고른 병원뿐이다.
- 단, 모든 병원 페이지가 명조 서체 `@font-face`를 함께 받는다. `unicode-range` 조각이라 쓰지 않는 병원은 글꼴 파일을 받지 않는다.
- 섹션 제목·안내문 문자열은 공통 상수로 옮겼을 뿐 글자는 같다(`git show 6c21141d:<파일>`과 대조함).

## 배포 전 체크리스트
1. PR #306 CI 통과, main에 병합.
2. 병합된 main으로 배포용 worktree를 만든다(작업 폴더의 미추적 파일을 섞지 않는다).
   ```bash
   git fetch origin && git worktree add .omo/deploy-layout origin/main
   ```
3. `gcloud auth login`(사람). 2026-11-13 17:44에 토큰이 만료돼 있었다.
4. 운영 상태를 읽기 전용으로 남긴다.
   - 리비전 5개와 트래픽 고정 여부. `deploy.sh`도 고정이면 멈춘다.
   - 오늘 배포 잠금(`gs://reputation-reports-mso-platform-481505/ops/deploy-lock/<KST 날짜>.json`). 같은 날 다른 릴리스가 있으면 `--override-reason`이 필요하다.
   - DB head가 `0093_add_hospital_search_verification`인지(`ops_sql_report` 또는 migrate Job).
5. **시간대:** Worker·Beat를 바꾸므로 KST 22:15~02:00과 04·07·08·12·18·22시 앞뒤 45분을 피한다. 오늘 가능한 창은 **18:45~21:30**이다(빌드·마이그레이션 시간을 감안). 매월 1일이 아니므로 그 제한은 없다.

## 실행
```bash
cd .omo/deploy-layout
make db-budget-guard
make copy-guard   # 시스템 Python 3.9는 한글 ast 버그로 멈춘다 — backend/.venv/bin/python scripts/check_user_facing_terms.py
bash scripts/deploy.sh all
mkdir -p ../rollback-layout && cp .deploy-rollback ../rollback-layout/all
```
`deploy.sh`가 스스로 하는 일: 운영 env 정본 사용, 배치 시간대 재확인, readiness Job, 공개 표면 확인(release·병원 slug·canonical_host·Site 리비전), 실패 시 트래픽 자동 롤백.

## 배포 뒤 확인
- [ ] `/api/v1/health/live`의 release가 병합 SHA다. readiness Job이 통과했다.
- [ ] DB head가 `0095_add_hospital_site_layout`이다.
- [ ] 공개 병원 14곳의 헬스(hospital ID·canonical host·현재 Site 리비전)를 확인했다. 한 곳의 홈 HTML에 `clinic-shell--layout-`이 없다(NULL 그대로).
- [ ] 공개 API에 `site_layout: null`이 실린다.
- [ ] `/fonts/clinic-serif/ClinicSerif-Bold.subset.0.woff2`가 200이고 CSP 위반이 없다.
- [ ] **Admin(로그인)**:
  - [ ] 계약 등록 화면에 카드 3장이 보이고, 고르기 전에는 등록 버튼이 막힌다(실제 등록은 하지 않는다).
  - [ ] 병원 정보 → 브랜드 섹션에 '사이트 레이아웃'과 같은 지역 분포 줄이 보인다.
  - [ ] 저장은 대표 승인 뒤 한 병원으로만 한다. 그 병원 공개 페이지가 몇 분 안에 바뀌는지 본다.
- [ ] 새 리비전의 ERROR 로그가 30분 동안 0건이다.
- [ ] `run_beat_entry daily-fleet-heartbeat` 또는 다음 일일 요약이 "점검 이상 없음"이다.

## 롤백
- 트래픽을 되돌리면 된다(`bash scripts/deploy.sh rollback`, 좌표 `.omo/rollback-layout/all`). 모든 병원이 레이아웃 도입 전 화면으로 돌아온다.
- 0095 컬럼은 남겨도 옛 코드에 무해하다(옛 ORM이 그 컬럼을 읽지 않는다). DB는 되돌리지 않는다.
- 롤백 전에 마케터가 레이아웃을 고른 병원이 있으면, 그 값은 DB에 남았다가 다시 배포하면 그대로 적용된다.

## 실행 기록
(배포 뒤 채운다 — 시각, 리비전, readiness, 확인 결과, 롤백 좌표)

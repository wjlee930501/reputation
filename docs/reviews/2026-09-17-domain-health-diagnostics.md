# 행복드림의원 도메인 알림 점검

상태: 공개 주소 정상 확인 / 진단 코드 보완 로컬 검증 완료 / 미배포.
대상: `ai.happydreamclinic.co.kr`, 행복드림의원.
참고 코드: `OPS-AADC44B537D0`.
Slack 알림 확인: 2026-09-17 03:46 및 05:46 KST.

## 공개 주소 확인

2026-09-17 08:19~08:28 KST에 공개 DNS와 HTTPS를 확인했다.
Google 8.8.8.8, Cloudflare 1.1.1.1, Gabia 권한 서버 3곳의 CNAME이 일치했다.
연결: `ai.happydreamclinic.co.kr → cname.reputation.motionlabs.kr → 34.117.192.90`.
인증서 발급자: Google Trust Services WR3. 대상 도메인 SAN 일치.
유효기간: 2026-09-15 05:30:29 UTC ~ 2026-12-14 06:24:21 UTC.
홈·tenant health·robots.txt·sitemap.xml·llms.txt는 HTTP 200과 TLS 검증 성공.
병원 ID와 slug가 올바른 행복드림의원으로 확인됐다.
Site 리비전: `reputation-site-img-142435-5432c5e`.
DNS·인증서·IAM·병원 레코드·운영 리비전은 변경하지 않았다.

## 코드 보완

저장된 안전한 오류 설명이 Slack projection에 빠져 기본 문구만 보이던 결함을 수정했다.
모든 실패를 DNS 안내로 연결하지 않고 timeout·요청 제한·서버 오류·병원 식별 불일치를 구분한다.
일시 응답 실패는 같은 호스트에서 최대 한 번 재확인한다. 긴 Retry-After는 무시하지 않는다.
TLS 검증, redirect 거부, 병원 ID·slug·host·release 검사와 3회 정상 확인 후 복구 규칙은 유지한다.
선별 회귀 검사: 220 passed / 0 failed / 0 skipped. PostgreSQL 실제 복구·중복 알림 검사 포함.
Ruff, 사용자 문구 guard, git diff 검사 통과. 운영 호출 없이 격리 DB/Redis로 검증했다.

## 미확인 사항

당시 HTTP 코드·timeout·인증서 오류 여부와 incident 현재 상태는 아직 확정하지 못했다.
Mac mini와 Air 모두 Gcloud 재인증이 필요해 서버 로그 및 관리자 상태 조회가 막혔다.
현재 정상이라는 사실이 새벽에도 정상이었다는 증거는 아니다. 알림을 오탐으로 단정하지 않는다.
운영 배포는 하지 않았으며, 재인증 후 실제 로그를 확인하고 반영 여부를 결정해야 한다.

기준선: 운영 main `5432c5e`. 브랜치: `fix/domain-health-diagnostics-20260917`.
앞선 계약 이행·월간 보고서 미배포 작업은 포함하지 않는 별도 worktree다.
검증 자료: `/private/tmp/reputation-domain-audit-20260917-DeXSBL`.

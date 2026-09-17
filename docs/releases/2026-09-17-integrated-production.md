# 자율 계약 이행 · 월간 보고서 · 도메인 감시 운영 배포

상태: **DEPLOYED_VERIFIED_WITH_CONFIGURATION_FOLLOWUPS**.
실제 애플리케이션 소스는 PR #120의 main 병합 커밋 `2381566883cfcfcf87d317a2c9671b96817499a8`이다.
2026-09-17 09:00:54 KST에 Backend rollout을 완료했고, 09:09 KST에 현재 리비전·트래픽·설정·오류를 재확인했다.
문서 후속 커밋은 운영 이미지의 소스 SHA가 아니며 문서만 변경한 이유로 재배포하지 않는다.
정본 검증 수치는 [validation JSON](2026-09-17-integrated-production-validation.json)을 본다.

## 포함한 변경과 검토 범위

- 계약 이행 `694b978` → 통합 `be13419`: 누락 슬롯·회수·동시 목표 선택·월별 계약 집계·재실행 일정.
- 월간 보고서 `27f863f` → 통합 `56bb1fd`: 확정 반복·동결 질문·부분 측정·전체 질문표·PDF 검증 및 복구.
- 도메인 감시 `f81b068` → 통합 `7585d61`: 실패 원인 표시, 원인별 안내, 최대 1회 일시 오류 재확인.
- 운영 로그 검토 후 `9726959`: 병원과 공개 host, 예외 클래스 체인을 기록한다. 예외 본문·헤더·인증 값은 기록하지 않는다.

기준선 이후 관련 변경을 별도 worktree에서 통합하고 원격에 push한 후 PR #120으로 병합했다.
남아 있는 9월 15일 대체 웹 레이아웃 실험 브랜치는 이미 배포한 공통 디자인·이미지 최적화보다 오래된 대안이며 병합 대상에서 제외했다.
통합 시점의 다른 열린 PR은 없었다. 기존 `aside-test.js`는 내용 보존 및 커밋 제외했다.

## 행복드림의원 알림의 실제 근거

03:45 및 05:45 KST의 기존 Worker 로그는 `tls_or_network_error`를 기록했다.
해당 병원 incident 감사 이력은 04:30 및 06:30 KST에 연속 정상 확인으로 자동 복구됐음을 보여준다.
DNS·TLS·병원 marker는 정상이며 DNS, Certificate Manager, Cloud Armor를 변경하지 않았다.
과거 로그는 전송 예외를 하나의 코드로 합쳤으므로 당시 DNS·TLS·연결 세부 원인을 확정하지 않는다.
현재 정상화와 과거 모든 방문자의 무영향은 다른 주장이다. 이번 검토는 후자를 입증하지 않는다.
운영센터의 사람 계정 전용 경계를 우회하거나 과거 incident를 수동 ACK로 숨기지 않았다.

## 배포 및 검증

API·Worker·Beat에 공통 suffix `ops-235758-2381566`을 적용했으며 모두 Ready, 트래픽 100%다.
Site는 `reputation-site-img-142435-5432c5e`, Admin은 `reputation-admin-ref-151613-18531d3`을 유지했다.
환경·secret 참조·네트워크·서비스 계정·리소스·스케일링은 보존하고 이미지와 release 식별자만 갱신했다.

마이그레이션 확인 → Worker → RedBeat 재조정 → Beat → 현재 release readiness → API 순서로 실행했다.
DB head는 `0079_topic_swap_fallback`, RedBeat 일정 버전은 `2026-09-17.1`이다. 스키마 변경이나 backfill은 없다.
발행 회수 08~23시 매 정각과 추가 생성 회수 12·18·22시가 영속 스케줄에도 반영됐다.

| 검증 | 결과 |
|---|---|
| 병합 main 원격 CI | 9/9 success, run 35164057607 |
| Backend 전체 | 4,326 passed, 실패·skip 0개, coverage 85.05% |
| 최종 이미지 | Cloud Build 성공, 변경된 운영 파일 25개 소스 hash 일치 |
| 이미지 내 보고서 | 질문 31개를 전체 보존한 3쪽 PDF 생성·검증 |
| 운영 readiness | DB·Redis·필수 설정·현재 release 7개 큐 canary 통과 |
| 공개 표면 | 병원 9곳·공개 글 151개 보존, 대표 페이지·이미지 검증 통과 |
| 서비스 설정 | Backend 설정 보존, Site/Admin 리비전·트래픽 불변 |
| 오류 조회 | 08:57:58~09:09:46 KST: 개발 Slack 미설정 오류 1건, 기타 ERROR 이상 0건 |

## 별도로 남아 있는 운영 설정과 범위

개발 전용 Slack webhook이 설정되지 않아 개발 알림 1건이 HOLD다. 운영 채널로 임의 전환하거나
알림을 성공 처리하지 않았다. 기존 API에서도 발생하던 NHN SMS secret 초기화 경고도 남아 있으며
SMS 실제 발송은 이번 검증 범위가 아니다. 두 설정 이슈를 DNS 장애나 이번 코드의 회귀로 단정하지 않는다.
09:08 외부 감시에는 당일 5편 발행과 예정 잔여 5건이 기록됐다. 큐·실행기 정상과 계약 전량 이행은 다르다.
유료 AI 재생성·고객 보고서 재전달·시험 Slack/SMS 발송을 수동 실행하지 않았다.
검증용 로컬 PostgreSQL·Redis는 종료했다.

## 롤백

직전 Backend 리비전은 각 서비스의 `ref-151613-18531d3`이다. 증거 디렉터리의
`rollback-revisions.json`에 이미지 digest와 트래픽 좌표를 보존했다.
Backend를 되돌리는 경우 Worker·Beat·API를 함께 판단하고, 해당 소스와 일치하는 RedBeat
영속 일정도 재조정해야 한다. 단순 API 트래픽 복귀만으로 스케줄 변경이 취소되지 않는다.
DB downgrade와 병원 DNS 변경은 이번 롤백 절차가 아니다.

상세 원본 로그·이미지 검증·서비스 전후 상태는 배포 머신의
`/private/tmp/reputation-integrated-release-20260917-b6dlisqf`에 보관했다.
추적 문서에는 비밀 값·전체 환경 설정·고객 원문을 복사하지 않았고 필요한 안전한 검증 결과만 남겼다.

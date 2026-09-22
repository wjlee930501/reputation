# 2026-09-22 — 통합 안정성 수정 운영 배포

## 배포 기준

PR #138의 main 병합 코드 `a35b7f2a0c8d7ee568abbd9a7abdda75a231576f`를 운영 5개
서비스에 배포했다. 검증한 PR HEAD `778ae20`과 main의 애플리케이션 내용은 동일하다.
이 문서를 추가한 커밋은 배포 기록이며 런타임 이미지의 소스 SHA가 아니다.

- PR CI 9개 + 실제 큐 리허설 1개 통과. main CI `35659455705`도 9/9 통과.
- Backend 4,646개, Admin 627개, Site 364개는 통합 코드의 기존 검증 결과다.
- Cloud Build: `48a14573-6711-4202-b3b8-6c0996752591` (asia-northeast3), SUCCESS.
- rollout suffix: `integrity-a35b7f2`. 2026-09-22 10:05 KST까지 사후 확인.
- DB head `0079_topic_swap_fallback`, 신규 schema 변경 없음.

## 이미지와 실제 런타임

| 서비스 | 이미지 digest (Artifact Registry reputation 저장소) |
|---|---|
| API / Worker / Beat | `sha256:12ca43b6bbac01d67a9905455a7957944c6ab7017a22aa2125ce51696af98415` |
| Site | `sha256:d7a690831ce022147299af0933e55451e96e98fd9889cbacd672813a9fcd413a` |
| Admin | `sha256:8f137ed78e95ad284d876d2b1ccae2be2b6f9502fc1ec6d2695fa888a2849003` |

서비스명은 각각 `reputation-api`, `reputation-worker`, `reputation-beat`,
`reputation-site`, `reputation-admin`이다. 서비스마다 `<서비스명>-integrity-a35b7f2`
리비전이 Ready이며 트래픽 100%와 실제 image digest 일치를 확인했다. Worker와 Beat의
정상 실행은 트래픽 설정만으로 판단하지 않고 아래 큐 readiness로 별도 확인했다.
환경·secret 참조·서비스 계정·자원·네트워크 설정은 변경 전 구성과 비교해 보존했다.

## 운영 사후 검증

새 이미지로 마이그레이션 확인, Worker, RedBeat 재조정, Beat, readiness, API,
Site/Admin 순으로 갱신했다. 대기 중이던 revision의 전환은 최종 describe와 트래픽으로
재확인했다. 재개 시 이미 배포된 서비스는 중복 업데이트하지 않았다.

- 최신 readiness 실행 `reputation-production-readiness-pdmtv`: 성공.
  2026-09-22 10:03:26 KST 기준 DB·Redis·schema·등록 task·라우팅·스케줄·필수 설정
  16개 검사 모두 true, 현재 release의 7개 큐 canary 확인.
- 배포 전 실제 파일 검사: 월간 PDF 10개와 리드 PDF 10개의 byte/hash 일치.
  전체 파일 전수 검사나 원장에게 실제 전달한 검증은 아니다.
- 공개 병원 9곳의 홈페이지·llms.txt·대표 글·대표 이미지 HTTP 확인 실패 0.
  공개 API 목록 169건 → 재개 확인 176건, 배포 전 169건 누락 0.
  176개 글의 본문·이미지를 전수 방문한 것은 아니다.
- 자기 도메인 7곳은 기존 hospital_id·canonical host와 새 Site 리비전을 확인.
  다른 병원의 `/{slug}/llms.txt`를 자기 도메인에서 요청하면 404.
- 운영 랜딩과 미인증 Admin 로그인: 1440px·390px 네 화면 HTTP 200,
  기대 문구 확인, 가로 넘침·브라우저 오류 없음. 인증 후 전체 업무 E2E는 아니다.
- 새 리비전 오류 조회: 2026-09-22 06:50~10:05 KST, ERROR 이상 및 구조화
  ERROR/CRITICAL·Traceback 검색 결과 0. 09:30 이후 HTTP 5xx 조회 결과도 0.
  관측 범위 밖의 오류나 모든 향후 실행이 없다는 의미는 아니다.

수동 고객 메일·문자·Slack 시험 발송, 유료 생성 시험, 기존 보고서 일괄 재생성은
실행하지 않았다. 운영에 등록된 정상 자동 작업은 계속 동작한다. 새 보고서 템플릿은
배포 후 생성하는 문서에 적용되며 기존 artifact는 일괄 덮어쓰지 않는다.

## 롤백 기준

| 서비스 | 배포 전 정상 리비전 |
|---|---|
| reputation-api | `reputation-api-00194-qpc` |
| reputation-worker | `reputation-worker-00183-pf9` |
| reputation-beat | `reputation-beat-00179-wmh` |
| reputation-site | `reputation-site-img-142435-5432c5e` |
| reputation-admin | `reputation-admin-00089-6lg` |

필요 시 서비스별 직전 구성을 확인해 되돌리며, Worker/Beat는 큐 소비·서명 release·
영속 RedBeat 스케줄을 함께 대조한다. HTTP 트래픽만 되돌린 뒤 복구 완료라 하지 않는다.
이번 배포에 신규 DB migration은 없지만 자동 DB downgrade는 실행하지 않는다.

## 남은 검증 범위와 증거

실제 GCS 파일 읽기는 위 표본으로 확인했다. 상용 AI의 성공 생성 전체 경로, 로그인 후
원장 보고서 다운로드·전달의 브라우저 E2E, 이후 야간·월말·월초 전체 주기는 이번에
새로 실행하지 않았다. 기존 CI/SQL/격리 큐 검증과 운영 관측을 서로 혼동하지 않는다.

로컬 증거 위치: `/private/tmp/reputation-production-20260922-qzhqfn1q/evidence/`.
`production-services.json`, `preflight-result.json`, `public-preservation-resumed.json`,
`readiness-resumed-safe.json`, `error-inventory-resumed.json`,
`browser-production.json`, `deployment-verification.json`에 단계별 결과가 남아 있다.
원본 서비스 설정 등 private 스냅샷은 외부 공유·Git 추적 대상이 아니다.
원본 `aside-test.js`의 SHA-256은 `162a5c2c47c553bab3fe929e5b243db4b396fad5708519c6739f5ecdfeb826dc`로 유지했다.

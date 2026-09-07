# 현재 배포와 헬스체크

문서 버전: **1.0** · 갱신일: **2026-09-07 (Asia/Seoul)**
구현 기준: **`345a6420998bcba21169519cf5ad77600cbfa94b`**

이 문서는 현재 코드의 배포 진입점과 실제 운영 구성을 설명한다. `docs/plans`의 Vercel/Supabase 구성은 과거 대체 배포안이다. 마지막 실행 결과는 [릴리스 기록](../releases/2026-09-07-345a642.md)에 분리했다.

## 운영 구성

| 항목 | 확인된 구성 |
|---|---|
| GCP project / region | `mso-platform-481505` / `asia-northeast3` |
| 플랫폼 / Admin | `reputation.motionlabs.kr` / `admin.reputation.motionlabs.kr` |
| HTTPS Load Balancer IP | `34.117.192.90` |
| Cloud Run 서비스 | `reputation-api`, `reputation-worker`, `reputation-beat`, `reputation-site`, `reputation-admin` |
| Backend 이미지 | API/Worker/Beat 공통 이미지, `SERVICE`로 실행 역할 선택 |
| Frontend 이미지 | Site/Admin 각각 Next standalone 이미지 |
| DB / broker | Cloud SQL PostgreSQL 16 / Memorystore Redis |
| 저장소 / 인증서 | GCS, Load Balancer·Certificate Manager 관련 Terraform 구성 |
| 배포 검사 Job | `reputation-migrate`, `reputation-redbeat-reconcile`, `reputation-production-readiness` |

구성 정본은 [Terraform](../../terraform/cloudrun.tf), [DB](../../terraform/cloudsql.tf), [배포 스크립트](../../scripts/deploy.sh)다. 인프라 선언과 실서비스의 차이가 있을 수 있으므로 다음 배포에서는 다시 조회한다. Secret Manager 값·환경변수 전체를 출력하지 말고 필요한 비밀이 아닌 설정과 secret 참조의 보존 여부만 확인한다.

## 배포 진입점과 실제 순서

저장소 루트의 `.env.production`과 접근 가능한 Secret Manager 설정을 준비한다. 비밀 값을 추적 파일에 넣지 않는다. `scripts/deploy.sh`는 Git 소스 버전을 고정하고 필수 환경·secret·DB 연결·공개 도메인 등을 검사한다.

```bash
make db-budget-guard
make copy-guard
bash scripts/deploy.sh all
```

전체 배포의 실제 순서:

1. 어떤 변경보다 먼저 필수 설정·도메인·secret·저장소·런타임 조건을 검사한다.
2. 현재 서비스 5개의 리비전을 `.deploy-rollback`에 보관한다.
3. Backend/Site/Admin 이미지를 빌드하고 업로드한다.
4. 새 Backend 이미지로 마이그레이션을 실행한다. 기존 코드와 호환되는 추가형 스키마 변경을 전제로 한다.
5. Worker를 배포하고 Redis에 남은 RedBeat 스케줄을 재조정한다.
6. Beat를 배포한 뒤 production readiness Job으로 DB·Redis·현재 릴리스 큐 소비와 필수 설정을 확인한다.
7. API, Site, Admin을 배포한다.
8. 실제 트래픽·리비전과 외부 공개 표면을 별도로 검사한다. 앞선 readiness만으로 이후 프론트엔드까지 검증되었다고 보지 않는다.

`backend`는 Backend 3종 배포다. **`api`도 Worker와 Beat를 먼저 함께 갱신하는 경로**이므로 API만 바뀐다고 가정하지 않는다. `worker`, `beat`, `site`, `admin`, `migrate` 개별 대상도 제공한다. 정확한 범위는 스크립트의 마지막 case문을 확인한다.

2026-09-07 배포는 로컬 업로드 지연 때문에 Cloud Build와 digest 기반 rollout을 사용했고 기존 서비스 환경·secret 참조 hash 보존을 별도로 검증했다. 이 실행을 `scripts/deploy.sh`의 기본 Cloud Build 기능으로 오해하지 않는다. 반복 가능한 기본 진입점은 위 스크립트이며 대체 배포도 같은 마이그레이션·Worker/Beat·readiness 순서를 지켜야 한다.

## 검증할 증거

| 층 | 완료 판단 |
|---|---|
| 소스 | PR/main CI, 실제 배포 소스 Git SHA와 이미지 digest 대응 |
| 런타임 | 5개 서비스 Ready, 의도한 revision에 트래픽, 환경·secret 참조 보존 |
| DB | Alembic 현재 revision과 이미지의 expected head 일치 |
| 작업 | 현재 릴리스의 6개 큐 canary, 필수 task routing, RedBeat schedule version |
| API / Admin | health와 로그인 표면 정상, 인증·오류 경계 유지 |
| 병원 공개 표면 | 홈페이지와 대표 글, 이미지 SSR, 정확한 병원·canonical·공개 주소 |
| 크롤러 표면 | host별 sitemap, robots, llms.txt, 구조화 데이터와 공개 URL 범위 |
| 로그 | 새 revision의 실제 오류와 작업 실패 여부; 조회 시점·범위 기록 |

병원 헬스는 `https://{병원 공개 호스트}/.well-known/reputation-health`다. HTTP 200뿐 아니라 `hospital_id`, `slug`, `canonical_host`, `release`(Cloud Run에서는 `K_REVISION`)가 해당 병원·배포와 맞아야 한다. 기본 주소와 자기 도메인을 모두 사용하는 운영 집합을 대상으로 확인한다.

`python -m app.utils.production_readiness`와 대기형 `python -m app.utils.wait_production_readiness`는 배포 이미지·운영 연결에서 실행하는 검사다. 공개 페이지 200만으로 DB 연결·스케줄 실행·실제 외부 AI 생성 성공까지 증명하지 않는다. 모델·이메일·Slack의 실제 호출은 별도의 명시된 검증 범위로 기록한다.

## 롤백과 문서 변경

```bash
bash scripts/deploy.sh rollback
```

롤백은 `.deploy-rollback`에 저장된 revision으로 **트래픽을 복귀**한다. 이미 적용된 DB 마이그레이션은 되돌리지 않는다. 스키마가 구버전과 호환되는지 먼저 판단하며 무조건 `alembic downgrade`하지 않는다. 롤백 뒤에도 현재 큐·API·공개 표면을 다시 확인한다.

문서만 바뀐 릴리스는 기존 런타임이 그대로임을 기록한다. 문서 Git SHA를 기존 이미지의 소스 SHA로 바꿔 적거나 불필요한 전체 재배포를 수행하지 않는다.

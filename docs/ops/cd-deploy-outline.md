# 운영 배포 CD 개요 (GitHub Actions + Workload Identity Federation)

상태: **개요만**(2026-10-11 자율 운영 MasterPlan P2-B Task 2-10). 워크플로·Terraform은 아직 없다.
작업 PC 배포(`bash scripts/deploy.sh …`)가 현재 정본이다. 결정 D13에 따라 CD도 **사람 승인 클릭 하나**를 남긴다.

## 필요한 GCP 자원 (Terraform `terraform/github_deploy.tf` 권장)

- Workload Identity Pool `github` + OIDC Provider(issuer `https://token.actions.githubusercontent.com`).
  attribute mapping: `google.subject=assertion.sub`, `attribute.repository`, `attribute.ref`, `attribute.workflow_ref`.
  attribute_condition: `assertion.repository=="wjlee930501/reputation" && assertion.ref.startsWith("refs/tags/release-")`.
- 배포 전용 SA `reputation-deployer@<project>` — **키 파일 없음**. 권한:
  - `roles/run.admin`(서비스 배포·`update-traffic`·Job create/update/execute). 줄이려면 `run.developer` + 아래 actAs.
  - `roles/iam.serviceAccountUser` on `reputation-sa`, `reputation-site-sa`, `reputation-admin-sa`.
  - `roles/artifactregistry.writer` on 저장소 `reputation`.
  - `roles/secretmanager.viewer`(버전 describe) + `roles/secretmanager.secretAccessor`는 `SLACK_WEBHOOK_URL`, `SLACK_WEBHOOK_URL_DEV`에만
    (`verify_slack_webhooks`가 값을 읽어 탐침한다).
  - `roles/storage.objectAdmin` on `reputation-reports-<project>`, 조건 `resource.name.startsWith(".../objects/ops/")`
    (배포 잠금·예외 기록·롤백 좌표). images 버킷은 `roles/storage.legacyBucketReader`(버킷 describe).
  - `roles/cloudsql.viewer`(`sql users list`), `roles/logging.viewer`(VPC preflight 로그 읽기).
  - `roles/iam.workloadIdentityUser`: `principalSet://iam.googleapis.com/<pool>/attribute.repository/wjlee930501/reputation`.
- GitHub: Repository variables `GCP_PROJECT_ID`, `GCP_REGION`, `WIF_PROVIDER`, `DEPLOY_SA`, `PUBLIC_DOMAIN`, `ADMIN_DOMAIN`.
  **시크릿 없음**(JSON 키 금지). Environment `production` + required reviewer(대표 또는 지정자) — 승인 클릭 하나만 사람 몫.

## 게이트 단계 (모두 `scripts/deploy.sh` 재사용)

1. 트리거: `push: tags: ['release-*']`(main에서 만든 태그만 — `git merge-base --is-ancestor $GITHUB_SHA origin/main`)
   + `workflow_dispatch`(input `target`: api|all|site|admin).
2. CI 녹색 확인: 같은 SHA의 `CI` 워크플로 결론이 success인지 `gh api repos/:owner/:repo/commits/$SHA/check-runs`로 본다.
3. `concurrency: { group: deploy-production, cancel-in-progress: false }` — GCS 하루 잠금과 이중 방어.
4. 추가형 마이그레이션 검사(새 `scripts/check_additive_migrations.py`): 운영 head부터 새 head까지 `upgrade()`에
   `drop_table`·`drop_column`·`rename_table`·`alter_column(type_=…)`·기본값 없는 NOT NULL·`DROP` SQL이 있으면 실패.
   예외는 파일 머리 주석 `# deploy: non-additive-approved <사유>`로만(그런 릴리스는 `AUTO_ROLLBACK=0`).
5. 인증: `google-github-actions/auth@v2` → `setup-gcloud@v2` → `gcloud auth configure-docker ${REGION}-docker.pkg.dev`.
6. 배포: `bash scripts/deploy.sh ${{ inputs.target || 'api' }}` — env 정본(운영 env, 로컬 파일 없음)·시간대·하루 잠금·
   readiness 자동 롤백·공개 표면 확인 자동 롤백이 그대로 적용된다. env 변경은 리뷰된 파일(예: `deploy/env-changes.env`)을
   `.env.production`으로 복사하고 `--accept-env-diff`를 붙이는 별도 input으로만.
7. 사후: `.deploy-rollback`과 `.omo/evidence/deploy-preflight/*`를 artifact로 올리고 롤백 좌표를
   `gs://reputation-reports-<project>/ops/deploy-rollback/<SHA>.txt`에도 복사. 별도 `rollback.yml`(workflow_dispatch)이 그 파일을
   `DEPLOY_ROLLBACK_STATE_FILE`로 받아 `bash scripts/deploy.sh rollback`.

## `deploy.sh`가 작업 PC를 가정하는 지점 (개선 목록)

- Docker 데몬(`docker build --platform linux/amd64`·`docker push`): ubuntu 러너는 그대로 동작한다(`configure-docker` 필요).
  대안은 `gcloud builds submit`(러너에 Docker 불필요, `cloudbuild.builds.editor` 추가).
- 로컬 파일: backend는 env 정본 전환으로 `.env.production`이 필요 없다. site/admin은 `read_env_file_value`로
  `GOOGLE_SITE_VERIFICATION`·`GA_MEASUREMENT_ID`·`GCP_STORAGE_BUCKET`을 읽는다 — Actions 변수로 넘기거나 Site 서비스 env에서 읽게 한다.
  `.deploy-rollback`, 증거 디렉터리 `.omo/evidence/deploy-preflight`도 러너에서 사라지므로 위 7번으로 보존한다.
- `git status --porcelain` 깨끗함 검사는 checkout 직후라 통과한다. `REPUTATION_RELEASE_REVISION=$GITHUB_SHA`로 넘기는 편이 명확하다.
- `gcloud config get-value project`는 `GCP_PROJECT_ID`를 넘기면 쓰지 않는다. `uv`는 `DATABASE_URL`이 있을 때만(러너에는 없음 → VPC Job 경로).
- 오류 안내의 `gcloud auth login`(`require_asset_bucket` 근처)은 WIF에서 의미가 없으므로 문구를 조건부로 바꾼다.

## 계약 영향 (CD 완료 뒤)

CLAUDE.md "배포는 [현재 배포 안내]를 따른다"에 "운영 배포는 `release-*` 태그 → GitHub Actions(WIF) → `deploy.sh`.
작업 PC 배포는 비상 경로"를 더한다. 그 전까지는 이 문서가 설계 개요일 뿐이다.

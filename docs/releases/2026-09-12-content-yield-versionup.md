# 2026-09-12 콘텐츠 수율 버전업 (v2.7) 기록

문서 버전: 0.1 (작성 중) · 브랜치 `claude/system-performance-review-x6vtn4` · 기준 `ca48ee8`

계획과 근거는 [버전업 계획](../plans/2026-09-12-content-yield-versionup-plan.md)을 본다. 이 문서는 실제 변경, 검증 증거, 배포 시 주의를 기록한다.

## 범위

(구현 완료 후 채운다)

## 검증

(구현 완료 후 채운다)

## 배포 시 주의

- 운영 배포본 `a774851` 이후 main의 31개 커밋이 미배포다. 이 브랜치의 변경은 그 위에 쌓인다. 마이그레이션 head는 배포 시 다시 확인한다.
- `CONTENT_IMAGE_REQUIRED_FOR_PUBLISH` 기본값은 false다. 종전처럼 이미지를 발행 선행조건으로 되돌리려면 true로 배포한다.
- 비용 가드에 `essence` 카테고리가 추가된다. Redis 키가 새로 생기며 기존 `content` 한도는 그대로다.
- RedBeat 스케줄 버전이 올라간다. 배포 후 영속 스케줄 재조정을 확인한다.

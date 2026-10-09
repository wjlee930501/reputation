# Mixed-version integrity rehearsal

This directory is the isolated Task 16 release rehearsal. It uses a disposable Docker
Compose project, an internal-only network, synthetic hospital/admin/report fixtures, and
test-only provider callbacks. It must never load the repository `.env`, cloud credentials,
or the shared QA PostgreSQL/Redis containers.

Fast scaffold validation:

```bash
bash scripts/integrity_rehearsal/run.sh --scaffold-check
```

CI/bootstrap validation uses only tracked inputs and performs no Docker build:

```bash
RUNTIME_SOURCE_SHA="$(git rev-parse HEAD)" \
  COMPATIBLE_READER_MANIFEST=scripts/integrity_rehearsal/fixtures/compatible-reader.json \
  bash scripts/integrity_rehearsal/run.sh --bootstrap-check
```

Final execution is fail-closed on the tracked schema-v2 compatible reader manifest. It
records both the original pre-Task-15 checkpoint and the narrowly verified read-only
hotfix that is the actual rollback image:

```json
{
  "schemaVersion": 2,
  "verifiedTasks": "1-14",
  "createdBeforeTask15": false,
  "originalCheckpointCreatedBeforeTask15": true,
  "readerContract": "purpose-first-compatible-public-read-v1",
  "sourceSha": "<hotfix full SHA>",
  "originalCheckpointSha": "<original full SHA>",
  "hotfix": {
    "parentSha": "<original full SHA>",
    "createdAfterTask15Started": true,
    "paths": ["<exact allowlisted reader paths>"],
    "evidence": ["<non-empty verification artifacts>"]
  }
}
```

The harness builds the compatible reader from that exact Git object and records its image
ID/digest. The pinned `41f61d6f...` baseline is used only for expanded-schema and original
input characterization. A validated local baseline registry is an optional cache. When it
is absent, as in a clean CI checkout, the runner builds the baseline from the pinned Git
object and records the resulting image identity. Rollback traffic is served by
`api-compatible`; no compatible worker, Beat, or Admin role exists.

Final command:

```bash
COMPATIBLE_READER_MANIFEST=scripts/integrity_rehearsal/fixtures/compatible-reader.json \
  RUNTIME_SOURCE_SHA=<verified-full-40-character-runtime-sha> \
  timeout --signal=TERM --kill-after=30s 17m \
  bash scripts/integrity_rehearsal/run.sh
```

Each run writes artifacts under `.omo/evidence/task-16/runs/` and registers its Docker
project/resources in `.omo/evidence/task-16/resources.json`. The EXIT trap captures logs,
removes only that project and images built by the run, and records a cleanup receipt.
The terminal artifact gate requires all 27 named browser captures across the new-flow,
PASS-promotion, and compatible-reader rollback sessions, including the scrolled report
rows, internal report evidence, and the pre/post legacy-budget action states.

# Re:putation 1.0 Stabilization Scope

> **For Hermes:** Keep this as the weekly execution boundary. Do not expand into P2/product-new-surface work unless Woojin explicitly re-scopes.

**Goal:** Ship a solid Re:putation 1.0 internal/beta-ready system by the end of this week.

**Target Window:** 2026-05-06 (Wed) ~ 2026-05-08 (Fri) KST

**Architecture:** Re:putation 1.0 is an AI exposure consulting/content operations system for clinics. The 1.0 finish line is not more features; it is stable operating contracts, owner-ready admin UX, safe public exposure policy, report readiness, and repeatable verification.

**Tech Stack:** FastAPI backend, SQLAlchemy/PostgreSQL, Next.js admin/site, Python smoke scripts, pytest, npm build.

---

## 1.0 Non-Negotiables

- No homepage-builder/product-agency drift.
- No generic blog-agency positioning.
- No AI-ranking guarantee or manipulation language.
- Public content exposes only approved/aligned material.
- Content/report workflows must remain grounded in approved operating standards and reviewed sources.
- Admin UI must be operator/owner-friendly, not raw enum/internal-language heavy.
- Push/deploy requires explicit approval; local commits are allowed.

## 1.0 Scope — Must Finish This Week

### Slice 1: Remaining Admin Copy + Runtime-Washing Audit

**Objective:** Remove or contain remaining user-facing wording risks in Admin Profile/Schedule and any leftover fallback labels.

**Files to inspect first:**
- `admin/app/hospitals/[id]/profile/page.tsx`
- `admin/app/hospitals/[id]/schedule/page.tsx`
- `admin/app/hospitals/[id]/dashboard/page.tsx`
- `admin/app/hospitals/[id]/content/page.tsx`
- `admin/app/hospitals/[id]/reports/page.tsx`
- `admin/types/index.ts`

**Acceptance:**
- No risky terms found by `scripts/check_user_facing_terms.py`.
- Remaining `washOperatorText` use is documented/intentional, or replaced by backend display fields.
- One small commit.

**Verification:**
```bash
python scripts/check_user_facing_terms.py
git diff --check
cd admin && npm run build
```

### Slice 2: Full Regression Baseline

**Objective:** Establish a clean 1.0 regression baseline after recent display-boundary work.

**Commands:**
```bash
python scripts/check_user_facing_terms.py
git diff --check
cd backend && uv run pytest -q
cd ../admin && npm run build
cd ../site && npm run build
cd .. && python scripts/demo_seed_runtime_smoke.py
```

**Acceptance:**
- All commands pass.
- `backend/uv.lock` noise reverted if touched.
- Repo clean after verification.

### Slice 3: Browser QA — Core Admin Journey

**Objective:** Verify the operator journey visually and catch regressions that tests/build miss.

**Screens:**
- Login
- Hospitals list
- Dashboard
- Essence
- Content
- Query targets
- Exposure actions
- Reports
- Profile/Schedule if materially changed

**Acceptance:**
- No console JS errors.
- No obvious internal/raw wording in primary cards/tables/drawers.
- No broken navigation or empty-state confusion in demo seed data.

### Slice 4: Report/Owner-Ready Output Final Check

**Objective:** Make sure the 1.0 value proposition is visible to clinic owner/operator.

**Checkpoints:**
- Report summary explains what changed/what to do next.
- Missing/incomplete report states are understandable.
- Essence/source/content alignment signals are understandable.
- PDF status/review status labels are owner/operator-friendly.

**Acceptance:**
- Any fix remains copy/UI-level or tiny DTO label-level.
- No new analytics/product feature scope.

### Slice 5: 1.0 Release Readiness Note

**Objective:** Produce a concise internal readiness note for Woojin.

**Create:**
- `docs/plans/2026-05-08-reputation-1.0-readiness-note.md` or update this plan with final status.

**Contents:**
- What 1.0 includes
- What is deliberately excluded
- Verification results
- Known risks
- Go/no-go recommendation

## 1.0 Scope — Should Do If Time Remains

### Auth/Session Hygiene

- Confirm logout/session expiry/admin password separation status.
- Fix only obvious small gaps.
- Do not build full RBAC.

### Additional Fixture QA

- One non-demo hospital with sparse data.
- One incomplete report/content state.
- One SoV failure/partial measurement state.

## Explicitly Out of Scope for 1.0

- Source stale diff engine.
- Alignment scoring model beyond existing contract checks.
- Full RBAC/audit explorer.
- Customer approval portal.
- Fully automated publishing.
- Crawlers or external data acquisition expansion.
- Homepage/site-builder workflows.
- New growth/marketing dashboards not needed for operating 1.0.

## Weekly Execution Order

1. Wed AM/early: Slice 1 — remaining copy/runtime washing audit.
2. Wed: Slice 2 — full regression baseline.
3. Thu: Slice 3 — browser QA and small fixes.
4. Thu/Fri: Slice 4 — report/owner-ready final check.
5. Fri: Slice 5 — readiness note and go/no-go.

## Commit/Verification Rules

- One narrow slice per commit.
- Before every commit:
```bash
python scripts/check_user_facing_terms.py
git diff --check
```
- For backend-affecting changes: run relevant pytest file(s).
- For admin-affecting changes: run `cd admin && npm run build`.
- For final readiness: run full regression baseline.
- Revert `backend/uv.lock` if only touched by `uv run` noise.

## Definition of Done for Re:putation 1.0

- Full regression baseline passes.
- Core admin browser journey has no obvious wording/navigation/console issues.
- Demo seed runtime smoke passes.
- Repo is clean with local commits organized by slice.
- 1.0 readiness note exists.
- Push/deploy remains unexecuted until Woojin explicitly approves.

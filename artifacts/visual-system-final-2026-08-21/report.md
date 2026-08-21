# Multi-clinic visual system final QA — 2026-08-21

## Scope

- Clinics: 6 active onboarded hospitals
- Routes per clinic: home, contents, article, doctor, treatments, visit
- Viewports: 390×844, 768×1024, 1440×1000
- Runtime: local production build connected to the production public read API

## Automated surface result

108 / 108 route-and-viewport checks passed.

Each check requires:

- HTTP 200
- exactly one H1
- no horizontal overflow
- no broken images
- no Next.js development overlay
- visible four-action bar at the mobile viewport

Machine-readable evidence: `metrics.json`.

## Manual review sample

Reviewed representative home, article, doctor, contents, and visit captures across all three
viewport classes. Confirmed:

- hospital theme colors stay distinct while action text remains contrast-safe;
- unverified character/editorial images do not occupy named-doctor identity slots;
- content cover crops use role-specific aspect ratios instead of one fixed image box;
- mobile tables and long content remain within the viewport;
- sparse and rich clinics use different section density without tenant-specific layout forks;
- all public routes share the same mobile contact actions.

## Evidence boundaries

The public read API does not yet contain the new visual-identity fields until the migration and
Admin-entered values are deployed. The screenshots therefore validate safe fallbacks and current
approved assets. Custom hero copy, explicit access/media modes, and new art direction are covered by
typed unit/API tests and the production build; their live tenant values require operator entry after
deployment.

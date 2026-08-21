# Final visual-system manual QA — 2026-08-21

## Verdict

PASS for the public multi-clinic surface and the implementation contracts reviewed below.
No reproducible public-route blocker remained in the fresh 14:15 production-build artifacts.

## surfaceEvidence

| scenario id | criterion reference | surface | exact invocation | verdict | artifactRefs |
| --- | --- | --- | --- | --- | --- |
| public-matrix-108 | all public routes remain usable at target breakpoints | six hospital slugs × home/contents/article/doctor/treatments/visit × mobile/tablet/desktop | `node` summary of `artifacts/visual-system-final-2026-08-21/metrics.json`; 108 PNGs inspected by route/viewport samples | PASS | `metrics-final`, `screens-final`, `report-final` |
| hero-media-provenance | real facility imagery and fallback media keep truthful labels | public home hero and doctor surfaces | `view_image` on Nowon/Jang/Happiness/SeoulW/Gang home desktops and Nowon doctor desktop | PASS | `hero-samples`, `doctor-sample` |
| responsive-content-media | content images use role-specific fitting without horizontal overflow | public contents/article surfaces | `view_image` on Yeonse contents tablet, Gang article mobile, Jang article mobile; metrics overflow and broken-image assertions | PASS | `content-samples`, `metrics-final` |
| mobile-contact-actions | shared action bar is present on all routes | public mobile routes | `metrics.json` `mobileActionVisible` across all 108 checks | PASS | `metrics-final` |
| admin-visual-contract | profile/onboarding controls compile and are wired to the API fields | Admin profile + onboarding source controls | `npm run typecheck`, `npm run lint`, `npm test` in `admin`; source inspection of `profile/page.tsx` and `onboarding/page.tsx` for color/copy/media/access/art-direction/provenance controls | PASS | `admin-checks`, `admin-profile-source`, `admin-onboarding-source` |
| backend-visual-contract | migration, serializer, validators, provenance, and prompt direction are executable | Backend Admin/Public APIs and image worker | focused `pytest` and `ruff` commands listed in `admin-checks`; migration `alembic heads` | PASS | `backend-checks`, `migration` |

## adversarialCases

| scenario id | criterion reference | adversarial class | expected behavior | verdict | artifactRefs |
| --- | --- | --- | --- | --- | --- |
| invalid-theme-contrast | tenant theme must remain readable | light brand color / contrast regression | action text is derived to AA-safe color while the original brand token remains available | PASS | `theme-tests` |
| editorial-doctor-image | provenance must not invent a named doctor | editorial character or missing identity metadata | doctor identity falls back to monogram; editorial art cannot occupy a named-doctor slot | PASS | `site-tests`, `doctor-sample` |
| editorial-facility-image | generated art must not masquerade as documentary facility evidence | editorial graphic selected for media | art is labeled/treated as brand graphic or content-only; gallery filters it out | PASS | `site-tests`, `backend-checks` |
| forbidden-hero-copy | medical-advertising guard applies to onboarding copy | superlative/forbidden expression in hero copy | API validation rejects the update | PASS | `backend-checks` |
| missing-photo-classification | new photo uploads must declare their role | photo upload without real/editorial classification | API returns 422 and does not accept an unclassified photo | PASS | `backend-checks`, `admin-onboarding-source` |
| unsafe-image-prompt | generated image must not contain claims or identifiable people | sensitive treatment title / real-clinic implication | prompt requires anonymous non-graphic editorial art, no text/logo/face/documentary claim | PASS | `backend-checks`, `image-prompt-source` |
| sparse-content-density | sparse tenant must not receive a padded template | 0–2 published contents | answer/care-flow modules are suppressed and featured list is capped | PASS | `site-tests`, `clinic-design-source` |
| mobile-long-article | long content must remain inside viewport | narrow 390px article/table | no horizontal overflow; article image/table remains readable | PASS | `content-samples`, `metrics-final` |

## artifactRefs

| id | kind | description | path |
| --- | --- | --- | --- |
| metrics-final | metrics | Fresh 108-route × viewport machine checks: 108/108, no failures or console errors | `artifacts/visual-system-final-2026-08-21/metrics.json` |
| screens-final | screenshots | 108 fresh production screenshots covering six hospitals, six routes, three viewports | `artifacts/visual-system-final-2026-08-21/*.png` |
| report-final | report | Scope, checks, manual sample, and live-data boundary | `artifacts/visual-system-final-2026-08-21/report.md` |
| theme-tests | tests | Theme contrast, media provenance, gallery filtering, density/access-mode behavior | `site/lib/clinic-theme.test.ts`, `site/lib/clinic-design.test.ts` |
| site-tests | test output | 218 site tests passed; typecheck and lint passed | `site/lib/*.test.ts` |
| admin-checks | test output | 269 Admin tests passed; typecheck and lint passed | `admin/lib/*.test.ts` |
| admin-profile-source | source | Admin profile controls for palette, access mode, hero copy/media, art direction | `admin/app/hospitals/[id]/profile/page.tsx` |
| admin-onboarding-source | source | Upload classification and editable provenance/usage controls | `admin/app/hospitals/[id]/onboarding/page.tsx` |
| backend-checks | test output | 60 focused backend tests and Ruff checks passed | `backend/tests/test_hospital_visual_identity.py`, `backend/tests/test_image_direction.py`, `backend/tests/test_essence_upload_public.py`, `backend/tests/test_public_site.py` |
| migration | migration | Single Alembic head includes visual identity fields | `backend/alembic/versions/0051_add_hospital_visual_identity.py` |
| image-prompt-source | source | Hospital philosophy, specialties, palette, and art direction enter safe prompt contract | `backend/app/services/image_direction.py`, `backend/app/services/image_engine.py` |
| doctor-sample | screenshot | Named doctor surface uses monogram when no verified identity asset is present | `artifacts/visual-system-final-2026-08-21/noweontab365yiweon__doctor__desktop.png` |
| hero-samples | screenshots | Representative themed/verified hero media across tenants | `artifacts/visual-system-final-2026-08-21/noweontab365yiweon__home__desktop.png`, `artifacts/visual-system-final-2026-08-21/jangpyeonhanoegwayiweon__home__desktop.png`, `artifacts/visual-system-final-2026-08-21/haengbogdeurimyiweon__home__desktop.png` |
| content-samples | screenshots | Role-specific content crops and narrow article/table behavior | `artifacts/visual-system-final-2026-08-21/yeonsesogsiweonnaegwayiweon__contents__tablet.png`, `artifacts/visual-system-final-2026-08-21/gangsimjangnaegwayiweon__article__mobile.png` |
| clinic-design-source | source | Shared composition/density contract without slug-specific forks | `site/lib/clinic-design.ts` |

## Boundary

The authenticated Admin profile/onboarding save flow was not exercised against a live operator account in this QA run; the local login surface returned HTTP 200, while the authenticated mutation path is covered by the Admin/backend contracts above. New visual identity values still require migration/deployment and operator entry before they can appear in the live public API.

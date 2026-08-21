# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-21
- Primary product surfaces: Admin operating console, public landing, public hospital second-homepage, medical content list/detail, treatment/doctor/visit pages.
- Evidence reviewed: `artifacts/visual-audit-report-2026-07-22.md`, `artifacts/visual-audit-*.png`, `admin/app/AdminShell.tsx`, `admin/app/hospitals/[id]/layout.tsx`, Admin route pages, `admin/app/globals.css`, `site/app/[slug]/page.tsx`, `site/app/[slug]/_components/*`, `site/app/[slug]/contents/[contentId]/page.tsx`, `site/app/globals.css`, `docs/prd/REPUTATION-UIUX-BRAND-WEBBLOG-PRD.md`, `design-qa.md`.

## Brand
- Personality: clinical, expert, calm, direct, locally trustworthy.
- Trust signals: physician portrait and credentials, diagnosis-first care principle, treatment areas, care flow, official phone/address/hours, published medical content count, references and update dates on articles.
- Avoid: generic SaaS card sprawl, internal SEO/AI phrasing on public surfaces, decorative gradients, vague “정보 허브” positioning as the main headline, and Admin chrome that crowds out the operator's current task.

## Product goals
- Goals: make any onboarded hospital look like a credible second homepage and medical blog; help patients understand care scope before calling; let an AE identify and execute the next operational action without scanning a long page; support sales demos with realistic depth.
- Non-goals: replace the hospital’s primary homepage, guarantee medical outcomes, present generated images as real patient results.
- Success signals: public first viewport communicates specialty, physician, treatments, and contact path; article pages show source/update/medical caveat without duplicated headings; mobile has immediate phone and visit actions; Admin mobile shows real task content within the first 220px and never clips controls or data horizontally.

## Personas and jobs
- Primary personas: hospital directors evaluating the product; prospective patients checking symptoms, doctor, hours, and location; AEs operating hospital onboarding, content, measurement, and reporting.
- User jobs: assess credibility, find the right treatment area, call or navigate, read a medical article with enough depth to trust it; identify blockers, update hospital data, and complete the next operational action quickly.
- Key contexts of use: mobile search traffic, sales demo on desktop, patient sharing of article URLs, AE desktop operations, and urgent mobile follow-up by an AE.

## Information architecture
- Primary navigation: Public — medical content, doctor, treatment areas, visit, official homepage. Admin — hospital list, onboarding, leads, current hospital task selector, operations settings.
- Core routes/screens: Public hospital home, content list/detail, doctor, treatments, visit. Admin hospital list/new, dashboard, onboarding, profile, content, schedule, reports, Wiki, essence, query targets, exposure actions.
- Content hierarchy: Public — access-led hero -> quick facts/actions -> treatment scope -> physician -> accountable content -> care flow -> curated gallery -> visit/contact. Sparse content removes redundant discovery modules; rich content introduces lead/support hierarchy. Admin — current status/next action -> blockers -> working data -> history/secondary detail.

## Design Principles
- Lead with care reality: specialty, doctor, diagnosis process, and contact path must be visible before decorative content.
- Make content accountable: article pages must show authoring basis, update date, references, and medical caveat near the title.
- Prefer editorial density over card sprawl: use sections, tables, flows, and profile panels before repeated generic cards.
- Put the task before the chrome: Admin navigation and hospital metadata must collapse on mobile so the current task appears in the first quarter of the viewport.
- Progressive disclosure for operational depth: lists stay compact; editing detail opens one item at a time in an accordion, detail pane, or disclosure region.
- Responsive means re-composition, not horizontal scrolling: tables become cards/key-value rows and no task-critical element relies on `min-width` below 640px.

## Clinic composition contract
- One shared system, three explicit axes — never hospital-slug CSS or copy branches:
  - `contentDensity`: `sparse` (0–2 published items), `standard` (3–8), `rich` (9+). Sparse shows one accountable lead and omits duplicate question/content modules; standard shows a lead plus compact support; rich adds filters and deeper library navigation.
  - `mediaMode`: `verified-real`, `brand-graphic`, `typographic`. Doctor identity slots accept only verified real-person media; generated people never stand in for a named clinician. Facility imagery and abstract editorial motifs remain separate roles.
  - `accessMode`: `urgent`, `appointment`, `specialist`. It changes first-viewport priority only: urgent leads with today-hours/phone/directions, appointment with booking/hours, specialist with physician credentials. It does not fork the component tree.
- Hospital-specific director requests are data, never tenant branches: `hero_specialties` may override only the first-viewport specialty labels, `hero_headline`/`hero_description` own approved public copy, `hero_image_url` selects a provenance-approved managed asset, and `content_focus_topics` constrains future editorial generation and publication independently from public hero labels. A content item must select one exact allowed topic and ground that topic in its title or body; metadata alone never satisfies the publication gate.
- Data stress fixtures are part of the contract: content 0/1/3/6/19+, photos 0/1/4/8/22+, treatments 5/6/8/12+, hospital names from short through `서울W내과의원 위례점`, 1–8 regions, and partial logo/color/photo data.
- Every public route keeps phone, hours, treatment and directions access on mobile through the shared clinic shell; contact availability is not a home-hero side effect.
- Collection previews are bounded: home gallery 6 images by default and never more than 8; larger libraries disclose a count and link rather than rendering every asset in the reading flow.

## Visual Language
- Direction: restrained local-clinic editorial. Warm paper, precise hairlines, strong Korean type and verified photography carry identity. The memorable moment is the first viewport's truthful pairing of care scope with immediate access information—not decoration.
- Semantic color: every `.clinic-shell` receives `--clinic-brand`, `--clinic-brand-action`, `--clinic-brand-hover`, `--clinic-brand-soft`, `--clinic-on-brand`, `--clinic-ink`, `--clinic-paper`, `--clinic-line`, and `--clinic-focus`. Admin-approved primary color seeds a contrast-safe ramp; accent is limited to quiet supporting details. Components never consume raw customer colors or hard-code a second brand blue. Default fallback is clinical blue only when no approved color exists. No purple.
- Color distribution: neutral/paper 80%, deep actionable brand 15%, fresh/supporting accent at most 5%. White text is allowed only when the computed background meets 4.5:1; otherwise the system chooses dark ink.
- Typography (Pretendard only; weight contrast 400/600/800): editorial hierarchy is built from scale + weight, not new fonts. Hero display `.clinic-hero-title` 44px desktop / 30px mobile with the specialty declared in accent blue (`.clinic-hero-title-sub`; guards against "협진 협진" when a specialty already contains 협진). Section titles `.clinic-section-title` 30px / 24px. Article title 40px / 27px. Article body 17px / line-height 1.8, measure ~680px. Body/notes ≥16px, mobile included.
- Visual anchors (v6) — every section owns one dominant anchor: hero = verified facility/physician photograph or a typographic access panel; facts = today's hours + phone + address rail; treatments = ordered directory; doctor = verified portrait or typographic monogram with credentials; featured = one accountable lead plus density-aware support; gallery = curated 6–8 approved facility photos; visit = three direct actions plus transport/preparation details. No generated person is a trust anchor.
- Rhythm — editorial density over card sprawl: each section changes layout so nothing is a repeated card grid, and white ↔ `#f6f9fc`/cool-paper bands alternate. Structure is expressed with hairline dividers + whitespace; boxes are used only where they earn hierarchy (calendar, cards, band).
- Section headers are lightweight: title (and an optional one-line note) only. Mechanical eyebrow+title+description triplets are removed — the title carries the message.
- Brand illustration system (`_components/brand.tsx`, hand-authored inline SVG, no icon package): a 2-tone deep-blue + blue-grey abstract language — concentric arcs, a pulse curve, and a dot grid for the hero (`HeroLineArt`); three thin-line principle icons; and seven abstract content-type cover motifs (FAQ speech bubbles / DISEASE concentric rings / TREATMENT steps / COLUMN pen nib / HEALTH waves / LOCAL pin-on-grid / NOTICE bell). All strokes use `currentColor` + `--brand-ink-2`, `aria-hidden`, clean `viewBox`, no emoji, no gradients, no organ/body depiction.
- Shape/radius/elevation: 4–16px radii according to hierarchy, no shadows anywhere on the clinic surface (regression-guarded), no gradients, no glassmorphism.
- Motion: interactive hover/focus/press only, 150ms — underline/arrow/colour feedback. Non-interactive gallery media never zooms or animates.
- Imagery: images are progressive enhancement over an always-present SSR underlay, never required and never a bare grey box. `verified-real-person`, `verified-facility`, `generated-editorial`, and `abstract-brand` are distinct usage roles. Doctor identity accepts only `verified-real-person`; facility and content media never inherit person-oriented alt text. Content covers use a single explicit aspect-ratio slot and a type motif when no approved image exists. Meaningful images receive Korean alt text; decorative motifs use `alt=""` deliberately.
- Admin rhythm: 32px desktop / 16px mobile page padding; 28–32px / 20–24px section gap; 20–24px / 16px card padding; 44px minimum controls. Admin body 14/22, table 13/20, helper at least 12/18. Ten- or eleven-pixel text is reserved for versions and non-critical metadata.

## Components
- Reused: `ClinicHeader`, `ClinicGallery`.
- Brand SVG (v4): `_components/brand.tsx` exports `HeroLineArt`, `IconExam` / `IconExplain` / `IconAftercare`, and `ContentMotif` (7 type motifs). `ContentCover` (client) composes a type motif + watermark echo + optional real image.
- `ClinicHero` (v6): data-driven access-led split. At most two display specialties appear in the H1; full specialty/region lists remain in supporting facts. Verified media fills the right plane; no-media state becomes an intentional typographic access panel rather than a fake portrait or empty navy slab. No slug branches.
- `ClinicHero` director-request state: when `hero_specialties` is non-empty it replaces the kicker/H1 specialty labels only, with up to three approved labels; the default profile display remains capped at two. The underlying medical profile remains unchanged. The Admin presents these labels beside hero copy and managed-image selection so an AE can fulfill future requests without code changes.
- `HospitalFacts` (v4): weekly진료시간 calendar (`.clinic-week`, KST-today highlighted, 휴진 notice row) + three key-fact cards (전화 / 주소+길찾기 / 진료영역·지역), official channel chips + HIRA line.
- `AnswerClusters` (v4): `.clinic-answers-layout` = guidance rail (eyebrow, note, "처음 오시나요?" hint, all-content link) + main column with a blue-left-rule top-3 question index (`.clinic-qa-list`) and a compact question-only list (`.clinic-qa-compact`), then a treatment strip.
- `CarePrinciples` (v4): the 진료철학 section — blue-grey band, a fact-based lede (uses public_about only when clean; a front-end `sanitizePublicAbout` guard drops internal-pipeline language like "자료에서 확인된 핵심 메시지" so a not-yet-demoted polluted value never leaks), and three principle cards each with a thin-line icon.
- `TreatmentGrid` (v4): 4 lead cards (index + name + 2-line desc + more link) via `.clinic-tx-cards`, remaining as a 2-column compact index (`.clinic-tx-index`) — data-independent, works at 3 or 12 items.
- `CareFlow` (v4): 4-step connected timeline with per-step aux markers (`.clinic-flow-node-aux`); horizontal desktop, vertical mobile.
- `DoctorIntro` (v6): verified real portrait over a permanent monogram, one canonical role label, structured credentials and a bounded specialty/region summary. A role equal to the eyebrow is rendered once.
- `FeaturedContent` / `/contents` featured / article header: `ContentCover` banner (real image or type motif, never an empty box).
- `ContactCard` (v4): action-first 방문 안내 — three action buttons (전화하기 / 길찾기 / 진료시간 보기), a location frame, three visit checklists (주차 / 대중교통 / 초진 준비물), then official channel cards.
- `ClinicQuickActions`: mobile-only route-invariant 전화/진료시간/진료안내/길찾기 bar owned by shared header/chrome, with safe-area clearance and 44px targets.
- `ClinicFooter` (v6): top CTA row ("진료 문의가 필요하신가요?" + phone button), then a 2-column info block (병원명·대표자·공식 홈페이지 / 연락처) and fine-print disclaimer + copyright.
- `ContentCard`, `/treatments`, article page reused with the shared v3/v4 chrome (`.clinic-section-head`, H2 accent-rule anchors, footnote-card references).
- `ClinicAvatar`: monogram is a permanent SSR underlay; the real image is rendered client-side after mount and removed on blank/error (never a grey box or broken-image glyph).
- Category colour: 7 content types get low-saturation chips (`.clinic-tag--faq/disease/treatment/column/health/local/notice`), distinguished by lightness/depth within the unified clinical blue system (plus the existing green/yellow/red/grey status accents) — purple is never used, per the Visual Language "no purple" rule.
- Variants and states: missing/unverified doctor photo → monogram + credentials; missing facility media → typographic access panel; unsupported/slow/absent URLs render no image; sparse content feeds collapse to one lead and remove duplicated discovery; gallery previews are capped; mobile tables stack when semantically simple or show a labelled horizontal-scroll affordance.
- Token/component ownership: `buildClinicThemeStyle` owns the full semantic clinic token set and every public route applies it at `.clinic-shell`. Component CSS consumes semantic tokens only; no slug-specific theme or raw customer color.
- Admin responsive ownership: `AdminShell` owns the compact mobile app bar; hospital `[id]/layout.tsx` owns the compact hospital summary and route selector; reusable `.admin-responsive-table`, `.admin-kv-list`, `.admin-page-grid`, and disclosure patterns live in `admin/app/globals.css`.
- Article ownership: the public content renderer sanitizes a leading Markdown H1 that duplicates the page title and starts generated body sections at H2.

## Accessibility
- Target standard: WCAG AA-oriented contrast and keyboard semantics.
- Keyboard/focus behavior: links and buttons remain native anchors.
- Contrast/readability: no low-contrast text on image without overlay; normal text targets at least 4.5:1, including landing eyebrow/disclaimer text.
- Screen-reader semantics: use sections, headings, lists, and nav labels; one visible/semantic H1 per public article; selected route/filter states expose `aria-current` or the relevant native disclosure semantics.
- Media semantics: named physician/facility images are meaningful and require descriptive alt text. Abstract motifs are decorative. Generated people cannot enter physician identity states.
- Reduced motion: no required motion; reveal/typing motion must honor `prefers-reduced-motion`.

## Responsive Behavior
- Supported breakpoints/devices: 1440px desktop, 768px tablet, 390px mobile, plus 200% zoom stress at an effective 640px layout.
- Layout adaptations: public hero two-column becomes single-column; treatment/flow cards collapse; mobile bottom action bar appears. Admin fixed sidebar becomes a 56px app bar with a disclosure menu; hospital tabs become a current-route selector; tables and DNS/report rows become stacked cards.
- Touch/hover differences: mobile CTAs must be 44px+ high and not depend on hover.

## Interaction States
- Loading: preserve layout with existing skeletons; mobile headers must not expand while data loads.
- Empty: missing photos/content should collapse gracefully; Admin empty states explain why the state is empty, the next action, and the expected result.
- Error: unsupported asset URLs return `null` and avoid `next/image` crashes; Admin errors stay inline and keep a retry action visible.
- Success: contact links use native phone/external anchors; Admin save/publish success leaves a compact status confirmation near the action.
- Disabled: disabled actions retain readable labels and explain their prerequisite nearby.
- Offline/slow network: images should not be required for text comprehension.

## Content Voice
- Tone: patient-facing, physician-accountable, non-promotional, no internal product language.
- Terminology: use “진료 원칙”, “진료 흐름”, “진료 영역”, “대표원장”, “정형외과 전문의”.
- Microcopy rules: avoid “AI”, “검색 시스템”, “브랜드 구조” in patient-facing pages; avoid claims of best, guaranteed, painless, or unique outcomes.

## Implementation Constraints
- Framework/styling system: Next.js App Router, React, Tailwind/global CSS, `@motionz-kr/ui` tokens where already integrated, `next/image` (gallery) + guarded `<img>` (avatar/cover). Prefer existing repo-native components over introducing a new UI layer.
- Design-token constraints: clinic components use the `--clinic-*` semantic contract. Legacy `--color-revisit-*` aliases may bridge untouched components temporarily but cannot receive new raw values.
- Performance constraints: no new heavy visual dependencies; brand illustrations are hand-authored inline SVG (no icon package).
- CSP (`next.config.mjs`): production stays strict. Dev-only additions (`process.env.NODE_ENV !== 'production'`) allow `http://localhost:8000` in `img-src` (preview backend assets) and `'unsafe-eval'` in `script-src` (Next dev HMR uses eval; without it client hydration fails and the doctor photo / covers never load locally). Neither relaxation ships to production.
- Compatibility constraints: site test runner uses Node native tests with TypeScript stripping.
- Test/screenshot expectations: run Site tests/lint/build/typecheck plus the six hospital data-stress fixtures. Aside Browser captures every public route for all six live hospitals at 390/768/1440 after the final edit; independent visual QA checks theme differentiation, CJK wraps, H1=1, page/table overflow, media provenance state, CTA availability and sparse/rich composition.

## Open Questions
- [x] Customer-specific approved primary colors seed a contrast-safe semantic ramp on all clinic routes. Raw customer colors never directly style text/buttons. Decision: 2026-08-21 multi-hospital audit.
- [ ] Should mobile bottom actions include Kakao when present? Owner: product. Impact: conversion path.
- [ ] After compacting query targets, should desktop use an inline accordion or persistent right detail pane? Owner: product/operations. Impact: bulk comparison versus editing speed. Current implementation defaults to accessible accordion.

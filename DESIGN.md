# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-05-27
- Primary product surfaces: public hospital second-homepage, medical content list, medical content detail, treatment/doctor/visit pages.
- Evidence reviewed: `site/app/[slug]/page.tsx`, `site/app/[slug]/_components/*`, `site/app/[slug]/contents/[contentId]/page.tsx`, `site/app/globals.css`, Playwright screenshots under `/private/tmp/motionlabs-*`, reference screenshots `/private/tmp/ref-goys.png`, `/private/tmp/ref-newstandard.png`, `/private/tmp/ref-newstandard-mobile.png`.

## Brand
- Personality: clinical, expert, calm, direct, locally trustworthy.
- Trust signals: physician portrait and credentials, diagnosis-first care principle, treatment areas, care flow, official phone/address/hours, published medical content count, references and update dates on articles.
- Avoid: SaaS dashboard feeling, generic card grids, internal SEO/AI phrasing, decorative gradients, vague “정보 허브” positioning as the main headline.

## Product goals
- Goals: make any onboarded hospital look like a credible second homepage and medical blog; help patients understand care scope before calling; support sales demos with realistic depth.
- Non-goals: replace the hospital’s primary homepage, guarantee medical outcomes, present generated images as real patient results.
- Success signals: first viewport communicates specialty, physician, treatments, and contact path; article pages show source/update/medical caveat; mobile has immediate phone and visit actions.

## Personas and jobs
- Primary personas: hospital directors evaluating the product; prospective patients checking symptoms, doctor, hours, and location; internal operators preparing demos.
- User jobs: assess credibility, find the right treatment area, call or navigate, read a medical article with enough depth to trust it.
- Key contexts of use: mobile search traffic, sales demo on desktop, patient sharing of article URLs.

## Information architecture
- Primary navigation: medical content, doctor, treatment areas, visit, official homepage.
- Core routes/screens: hospital home, content list, content detail, doctor, treatments, visit.
- Content hierarchy: center-style hero -> care principle -> treatment scope -> care flow -> physician -> featured articles -> gallery -> visit/contact.

## Design Principles
- Lead with care reality: specialty, doctor, diagnosis process, and contact path must be visible before decorative content.
- Make content accountable: article pages must show authoring basis, update date, references, and medical caveat near the title.
- Prefer editorial density over card sprawl: use sections, tables, flows, and profile panels before repeated generic cards.

## Visual Language
- Color: white, cool grey, clinical blue, and a restrained deep navy for primary medical CTAs.
- Typography: large confident Korean headings, readable body at 16px+ on mobile and 17px+ in articles.
- Spacing/layout rhythm: first viewport should be compact enough to hint at the next section; desktop max width around 1120px.
- Shape/radius/elevation: small to medium radius, minimal shadows, borders used for clinical structure.
- Motion: subtle hover/focus only.
- Imagery/iconography: real doctor/clinic images first; generated medical illustrations are acceptable only as explanatory article visuals with caveat.

## Components
- Existing components to reuse: `ClinicHeader`, `ClinicHero`, `CarePrinciples`, `TreatmentGrid`, `DoctorIntro`, `FeaturedContent`, `ClinicGallery`, `ContactCard`.
- New/changed components: `CareFlow`, `MobileActionBar`, expanded treatment cards, center-style hero panel.
- Variants and states: missing doctor photo must degrade to monogram; unsupported image URLs must render no image rather than crash.
- Token/component ownership: keep global CSS tokens; avoid introducing a new design-system dependency.

## Accessibility
- Target standard: WCAG AA-oriented contrast and keyboard semantics.
- Keyboard/focus behavior: links and buttons remain native anchors.
- Contrast/readability: no low-contrast text on image without overlay.
- Screen-reader semantics: use sections, headings, lists, and nav labels.
- Reduced motion: no required motion.

## Responsive Behavior
- Supported breakpoints/devices: desktop, tablet, 390px mobile.
- Layout adaptations: hero two-column becomes single-column; treatment/flow cards collapse; mobile bottom action bar appears.
- Touch/hover differences: mobile CTAs must be 44px+ high and not depend on hover.

## Interaction States
- Loading: existing Next rendering.
- Empty: missing photos/content should collapse gracefully.
- Error: unsupported asset URLs return `null` and avoid `next/image` crashes.
- Success: contact links use native phone/external anchors.
- Disabled: not applicable.
- Offline/slow network: images should not be required for text comprehension.

## Content Voice
- Tone: patient-facing, physician-accountable, non-promotional, no internal product language.
- Terminology: use “진료 원칙”, “진료 흐름”, “진료 영역”, “대표원장”, “정형외과 전문의”.
- Microcopy rules: avoid “AI”, “검색 시스템”, “브랜드 구조” in patient-facing pages; avoid claims of best, guaranteed, painless, or unique outcomes.

## Implementation Constraints
- Framework/styling system: Next.js app router, React 18, global CSS, `next/image`.
- Design-token constraints: reuse `--color-revisit-*` tokens and existing component classes.
- Performance constraints: no new heavy visual dependencies.
- Compatibility constraints: site test runner uses Node native tests with TypeScript stripping.
- Test/screenshot expectations: run site tests, lint, build, typecheck, backend content tests, and Playwright screenshots for desktop/mobile.

## Open Questions
- [ ] Should customer-specific brand colors override the default clinical blue palette? Owner: product. Impact: brand differentiation.
- [ ] Should mobile bottom actions include Kakao when present? Owner: product. Impact: conversion path.

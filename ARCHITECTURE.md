# Reputation content pipeline architecture

This document is the maintainer contract for the scheduled content path: content
generation, review, publication, public-cache revalidation, and the Celery Beat and
deployment windows that keep those stages coordinated.

> **Maintainer rule:** no behavior change to this pipeline, its schedules, retry or
> terminal-state policy, alert ownership, publication gates, revalidation, or deploy
> windows may be merged without updating this document in the same change.

The executable sources remain authoritative. The main entry points are
`backend/app/workers/tasks.py`, `backend/app/workers/generation_retry_policy.py`,
`backend/app/workers/generation_incident_control.py`,
`backend/app/services/content_publication.py`,
`backend/app/services/site_revalidate.py`, and
`backend/app/core/celery_app.py`.

## End-to-end content path

```text
monthly slot allocation
        |
        v
23:00 generation dispatcher ---- recovery dispatchers (01/04/07/12/18/22)
        |                                  |
        +---------- claim/lease -----------+
                           |
                           v
              one generate_claimed_content_item
                           |
          approved Essence + brief + cost guard
                           |
             OpenRouter writer -> independent review
                           |
              deterministic gates/correction
                           |
           OpenRouter image -> image policy review
                           |
                    DRAFT + attempt facts
                           |
             07:45 prepublish recovery/report
                           |
          08:00..23:30 publication safety gate
                           |
             PUBLISHED + immutable revision
                           |
        revalidate public routes + enqueue IndexNow
```

### 1. Generation and durable control

The nightly and recovery batch tasks are dispatchers. They select eligible slots,
claim a bounded lease, create an `OperationRun`, and fan out one
`generate_claimed_content_item` task per slot onto the `content` queue. The per-item
task rechecks the lease before provider work. Celery delivery is not the retry
policy: scheduled sweeps and the durable `generation_attempt` fragment in
`ContentItem.essence_check_summary` own retry timing and terminal decisions.

For an empty slot, generation requires the currently approved hospital Essence,
prepares an automatic brief, passes the content cost guard, calls the OpenRouter
writer, and runs independent AI review plus deterministic content gates. The text is
written with a conditional update tied to status, revision, and generation claim so
an operator edit/cancel wins races. Text commits before image generation; an image
failure must not roll back a usable body.

Representative images and image-policy review also use OpenRouter. Image bytes are
uploaded only after policy verification. A verified image from the same hospital may
be reused to keep a publishable body from missing its release, with later replacement
passes at 01:20, 04:20, and 07:20.

Generation outcomes are recorded in both item `OperationRun`s and the durable
`generation_attempt`. A recovery-owned failure is `RETRYING` with a real deadline.
An exhausted or deterministic failure is `OPERATOR_REQUIRED` (or an existing input
change terminal class) with no promised retry. Publication gates must preserve a
stored specific cause; `CONTENT_NOT_GENERATED` is only a symptom when no stronger
generation cause exists.

### 2. Review and publication readiness

`review_generated_content` is advisory input to the shared publication assessment,
not a manual approval queue. The shared policy in
`services/content_publication.py` checks generated text, required structured fields,
approved Essence alignment, independent-review state, references, and image
readiness. Manual recovery and scheduled publishing use this common safety policy.

Reference URLs are verified outside long row locks. Their results are applied only if
the content revision, topic, and reference snapshot are unchanged. Temporary source
site failures can defer publication; deterministic missing/invalid evidence follows
the reference policy rather than being treated as a provider outage.

The 07:45 task does not publish. It projects unresolved prepublish blockers and the
stored root cause into Operations/Slack under the notification ownership rules.

### 3. Automatic publication

`morning_content_auto_publish` is the only normal automatic publisher. It scans the
current seven-day catch-up window, locks one content row and its hospital, verifies
that the hospital is active/live and not on auto-publish hold, refreshes references,
and runs the shared publication assessment. A live generation claim is never
overwritten.

On success it sets `PUBLISHED`, records publication identity and audit facts, invokes
`reconcile_content_revisions`, requires exactly one active approved revision, stores a
public-surface intent, and commits. If the approved revision is not created, the
transaction rolls back and a dedicated developer incident is recorded. A database
`PUBLISHED` flag alone is not proof that the public surface updated.

### 4. Revalidation and indexing

After the publish commit, `trigger_content_site_revalidate_safe` invalidates the
content route, hospital route, and affected treatment routes. Revalidation failure is
post-commit and must not turn a successful publication into an API retry that
publishes again. Durable public-surface/revalidation control owns retry and incidents.
IndexNow intent is enqueued in the publication transaction and drained independently;
an indexing-hint failure is not a publication failure.

## Celery Beat schedule (Asia/Seoul)

Celery is configured with `timezone="Asia/Seoul"`; RedBeat persists schedule state
and holds a Redis distributed lock. `REDBEAT_SCHEDULE_VERSION` must be raised whenever
a Beat entry is added, removed, or retimed, and deployment reconciles the declared
schedule into Redis.

### Content lifecycle schedules

| Beat entry | KST schedule | Responsibility |
| --- | --- | --- |
| `nightly-content-generation` | daily 23:00 | Dispatch generation for tomorrow through the configured D+3 lookahead. |
| `overnight-content-generation-recovery` | daily 01:00, 04:00, 07:00 | Reclaim eligible failed/missed slots within retry budgets. |
| `daytime-content-generation-recovery` | daily 12:00, 18:00, 22:00 | Same recovery policy after daytime dependency recovery. |
| `refresh-reused-content-images` | daily 01:20, 04:20, 07:20 | Replace borrowed verified images without risking the published fallback. |
| `post-publish-ai-review` | daily 03:10 | Review bounded samples of already published content; findings do not silently unpublish. |
| `prepublish-content-generation-recovery` | daily 07:45 | Project unresolved blockers and the stored generation cause before first publish. |
| `morning-content-auto-publish` | hourly 08:00-23:00 | Run safety gates and publish due/catch-up content. |
| `content-auto-publish-after-sweeps` | daily 12:30, 18:30, 22:30, 23:30 | Revisit rows repaired or locked during same-minute sweeps; 23:30 is the last same-day chance. |
| `current-month-slot-reconciliation` | daily 21:30 | Repair missing current-month allocations before backlog planning. |
| `stranded-content-recovery` | daily 22:30 | Move old ungenerated backlog into bounded future capacity without rewriting operator edits. |
| `weekly-generation-rejection-rollup` | Monday 09:15 | Summarize deterministic generation/publication blockers by hospital/cause. |
| `monthly-slot-generation` | every 6 hours on days 25-31 | Create next-month slots (idempotent across repeated ticks). |

### Supporting schedules

| Beat entry | KST schedule | Responsibility |
| --- | --- | --- |
| `reconcile-essence-snapshots` | every 15 minutes | Recover missed Essence synthesis dispatches. |
| `weekly-sov-monitoring` | Monday 02:00 | Weekly non-monthly-cohort visibility measurement. |
| `monthly-sov-measurement` | every 6 hours on days 24-31 | Complete the fixed month-end measurement cohort. |
| `monthly-reports` | 00:15 on days 1-8 | Build/finalize the prior-month reports with bounded catch-up. |
| `monthly-report-gap-summary` | 09:00 on days 1-8 | Report remaining automatic/manual report gaps. |
| `purge-expired-leads` | daily 04:00 | Delete leads after their retention period. |
| `weekly-naver-source-sync` | Tuesday 03:00 | Import new official-channel posts for review. |
| `drain-lead-diagnoses` | every minute | Recover intake dispatch and advance lead diagnosis work. |
| `daily-fleet-heartbeat` | hourly at :00 | Enqueue the fleet operations summary when its reporting window is due. |
| notification, IndexNow, provider-usage, monthly-artifact and autonomous-workflow drains | every minute | Recover committed intents independently of the originating task. |
| milestone projection, live custom-domain health | every 15 minutes | Project durable milestones and check public custom domains. |
| queue canaries (`default`, `control`, `content`, `sov`, `reports`, `leadgen`, `certificates`) | every 5 minutes | Prove Beat-to-queue-to-worker delivery for each queue. |

The external pipeline watchdog is intentionally outside this Beat dependency: Cloud
Scheduler calls the API every five minutes and at 08:30 so a dead Beat/worker can be
reported without asking that same Beat to report itself.

## Deployment batch window

`scripts/deploy.sh` blocks Worker/Beat-affecting targets (`backend`, `api`, `worker`,
`beat`, and `all`) during KST 22:15-02:00 (both endpoints included) and within 45 minutes before or after 04:00,
07:00, 08:00, 12:00, 18:00, and 22:00. It checks before the first production mutation
and again immediately before Worker/Beat replacement because a build can cross into a
batch window. `site`, `admin`, `migrate`, and `rollback` are not blocked by this rule.

An exceptional override requires a non-empty `--override-reason`; it is recorded and
does not make interruption safe. The deploy script also reconciles RedBeat state,
guards production environment drift, and checks pinned traffic/readiness. See
`docs/ops/deployment-runbook.md`. Deployment and merge are separate operations; code
changes do not imply permission to deploy.

## Known failure modes and ownership

| Failure | Durable cause / behavior | Owner and recovery |
| --- | --- | --- |
| OpenRouter monthly API-key limit (403 `Key limit exceeded`) | `OPENROUTER_KEY_LIMIT_EXCEEDED`, `OPERATOR_REQUIRED`; Redis circuit prevents further automatic fleet calls for that key/month. The first detector owns immediate Slack; later slots retain the exact cause without alert fan-out. | Operator raises the key limit or rotates the key, then runs one explicit retry as a probe. A rotated key uses a new circuit key, but existing terminal slots still require that explicit probe; a new UTC month both uses a new circuit key and makes old-period attempts eligible. Never classify a generic 403 as this condition. |
| OpenRouter 429/5xx/connection/timeout | `PROVIDER_UNAVAILABLE` or `PROVIDER_TIMEOUT`, bounded environment recovery. | Scheduled sweeps retry within daily and multi-day budgets; only exhausted work becomes operator action. |
| Provider 400/401/403/404 other than the exact key-limit signature | Existing configuration/generic failure classification. | Inspect credentials, model, and request. Do not assume monthly spend exhaustion. |
| Cost guard limit | `COST_BLOCKED`; provider call is not made. | Cost guard owns its alert and reset/override policy. |
| Missing approved Essence | `MISSING_APPROVED_ESSENCE`; synthesis is dispatched and the slot remains system-owned. | Essence reconciliation and a later generation sweep. |
| Writer/review sample rejection | Bounded sample and correction budgets; eventually terminal or topic-swap/renewal policy. | Automatic while a concrete deadline exists, otherwise operator/input change. |
| Image generation/policy failure | Text remains committed; bounded image retries and verified-image reuse. | Image recovery schedules, then operator if exhausted. |
| Reference site outage | Publication may defer while evidence is temporarily unverifiable. | Later publisher pass; final-day summary names the site outage. |
| Worker loss, stale lease, or duplicate delivery | Claim token, operation ownership, conditional write, late ack, and worker-lost redelivery prevent duplicate writes. | Next valid delivery/sweep; stale work cannot overwrite operator changes. |
| Beat/Redis outage | RedBeat lock/tick liveness and queue canaries expose dispatch gaps; worker Redis sockets are bounded. | Cloud Run restart plus external watchdog alert. |
| Approved revision creation failure | Publication transaction rolls back; `AUTO_PUBLISH_REVISION_NOT_CREATED` incident. | Developer fixes revision reconciliation; hourly publisher revisits it. |
| Site revalidation failure after publish | Publication stays committed; revalidation retry/incident records public-surface lag. | Revalidation control; do not republish as a cache workaround. |
| Slack delivery failure | Notification outbox retries independently and records delivery incidents. | Notification control path; content state is unchanged. |

## Change checklist

When changing this architecture, update the relevant code and this file together,
then run focused unit tests for classification, retry/terminal eligibility,
notification cadence/copy, Celery routing/schedules, publication gates, and
revalidation. A schedule change also requires a RedBeat version bump. A new terminal
cause needs durable attempt semantics, Operations copy, Slack ownership, gate
preservation, and a documented way to become eligible again.

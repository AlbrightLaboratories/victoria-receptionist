# CLAUDE.md — Albright Laboratories org baseline

> **This file is org-managed.** Everything above the `---` marker at the bottom is the canonical baseline maintained in [`AlbrightLaboratories/.github`](https://github.com/AlbrightLaboratories/.github/blob/main/CLAUDE.md) and synced into every repo by the `claude-md-sweep` workflow. If you edit it locally, the next sweep will revert your change. Add repo-specific guidance BELOW the marker.

## Deployment is not "done" until Playwright proves it

**Hard rule.** For any change that touches a UI/UX surface OR a backend that a UI/UX surface reads (dashboards, mobile app endpoints, the gateway, etc.), the deployment is NOT considered complete — and Claude MUST NOT claim it is "deployed", "live", "working", "shipped", or "verified" — until a Playwright run has:

1. Loaded the actual production URL (or, when reachable, the internal cluster URL behind the same tunnel).
2. Authenticated as a real test user (no API-only smoke).
3. Captured a screenshot of the changed surface and saved it under `/tmp/bf-pw/shots/` (or an artifact location the operator can inspect).
4. Asserted the new behavior on the rendered page (text content, computed value, presence of a new badge, etc.) — not on the JSON shape alone.

`curl` against the JSON API is necessary but NOT sufficient. Past incidents (2026-05-31 P&L Timeline) had honest API output while the dashboard still rendered the lie due to stale image / cache / pod-pinned-to-old-digest. The visual check is what catches that.

If Playwright cannot reach the surface (login broken, selector fails, timeout), the deployment is FAILED — even if `kubectl rollout status` returned 0. Fix the test path, re-run, or report HONESTLY that the change is unverified.

Concretely, in a multi-step flow:
- ✅ Code committed + PR merged → say "merged"
- ✅ Image built + pushed                → say "image built"
- ✅ Pod on new digest (`kubectl get pod ... .image`) → say "rolled out"
- ✅ API smoke (curl) returns new shape   → say "API responding with new logic"
- ✅ Playwright captures screenshot showing the new value on the rendered card → ***ONLY THEN*** say "deployed and verified"

This rule applies to all repos: brightflow-live, ibkr-real-money-gateway, daxxon-trading, brightflow-dashboard, albrightlaboratories-dot-com, mobile-ios-and-android, and any future operator-facing surface.

## Timezone: Eastern (America/New_York) is the org standard

**Every timestamp the operator sees must be in Eastern Time (EST or EDT, whichever is current).** This applies to:

- **CronJob schedules**: every K8s CronJob must set `spec.timeZone: America/New_York`. Never schedule in UTC.
- **Container logs**: trading-related pods must set `env: TZ=America/New_York` so `datetime.now()` and log timestamps render in ET.
- **Dashboards, web UI, mobile push notifications**: display ET, not UTC. If a backend response is UTC, the renderer converts.
- **Agent / Claude output**: when reporting "last run was X" or "deployed at X", render X in ET. Never paste raw UTC strings without converting (e.g. "2026-05-29T20:45:55Z" is wrong; "2026-05-29 16:45 ET" is right).
- **Master TOC display** at https://github.com/AlbrightLaboratories/daxxon-ai-gpu-01/issues/17: the auto-updater renders its "Last updated" line in ET. The `scripts/toc_updater.py` script in `daxxon-ai-gpu-01` enforces this with `ZoneInfo("America/New_York")`.
- **Commit subjects / PR titles / issue bodies / release notes**: any embedded timestamp is ET.

Rationale: Eastern time is the market timezone we trade in. Operator carries one mental model — market hours, audit logs, dashboards, agent reports — all in the same clock. Mixing UTC creates "what time was this actually?" cognitive load + the audit incidents (e.g. the snapshot age "20:45 UTC" 2026-05-31 confusion) that flagged this rule.

NIST sync: cluster nodes should sync time via NTP to `time.nist.gov` / `time-a-g.nist.gov`. ET is derived from the host system clock, not hardcoded — DST transitions handle themselves.

If a third-party log/metric system can only emit UTC (e.g. Prometheus), convert at the display layer. The storage layer can stay UTC; the operator surface must not.

## Master TOC — do NOT manually edit

The org maintains a Master Table of Contents at https://github.com/AlbrightLaboratories/daxxon-ai-gpu-01/issues/17. It auto-populates every 15 minutes from the commit stream via `daxxon-ai-gpu-01/.github/workflows/toc-update.yml`, which walks every non-fork repo in the org, takes new commits to the default branch, and writes them as bullets under each repo's **Recent additions** section.

- **Never manually `gh issue edit` to add entries.** It races the workflow; the next run overwrites your edit.
- **Instead, write commit subjects that read well as one-line TOC bullets.** Format: `<type>(<scope>): <imperative outcome>` — e.g. `feat(US-053 link 6): scalpers write daxxon_3_recommendations at entry`. Avoid noise prefixes (`Update X`, `WIP`, `misc`). US-numbers and ticket refs are welcome and make scanning the TOC easy.

## README backlink

Every repo's `README.md` must start with this exact two-line block (the TOC auto-updater does **not** add it; the `claude-md-sweep` workflow prepends it if missing):

```
<!-- toc-backlink -->
> 📚 **Master TOC:** [Org-wide repo index](https://github.com/AlbrightLaboratories/daxxon-ai-gpu-01/issues/17) — auto-updated every 15 min from this repo's commit stream. No manual entry needed; just write commit subjects that read well as one-line bullets.
```

When editing or creating any `README.md` in an AlbrightLaboratories repo: if the block is missing at the top, prepend it. If a STALE version is present (older wording mentioning "append a one-line bullet"), replace it with the canonical block above — the autoupdater makes that instruction false.

## CI / runners

All build/CI workflows in this org default to the self-hosted `albright-runners` pool (provisioned by the [`arc`](https://github.com/AlbrightLaboratories/arc) repo). When writing or reviewing a workflow, use `runs-on: albright-runners` unless there's a specific reason otherwise (e.g. `albrightlaboratories-dot-com` workflows explicitly need `ubuntu-latest` for preinstalled Docker).

## Build artifacts → GHCR

Container images are published to `ghcr.io/albrightlaboratories/<repo-name>` from a `build-and-push.yml` workflow on every push to `main`. New packages are created private by default; the workflow's `GITHUB_TOKEN` needs **Write** access to the package (Package settings → Manage Actions access → add the source repo with role=Write), otherwise pushes fail with `permission_denied: write_package`.

### Image cache hygiene — MANDATORY for `:latest` pulls

Every Deployment / DaemonSet / StatefulSet that pulls a `:latest`-tagged image **must** set `imagePullPolicy: Always`. With the kubelet default (`IfNotPresent`) the node keeps using its locally-cached image regardless of what the registry now serves — meaning a fresh `kubectl rollout restart` silently keeps running the *old* image. This burned several hours of debugging on 2026-05-19 when hallo2 kept serving an obsolete image after a Kaniko rebuild.

Audit with `bash scripts/audit_imagepullpolicy.sh` in any repo that ships k8s manifests. Auto-fix with `--fix`. CI should fail when manifests regress.

### Stale GHCR versions auto-purged daily

A CronJob in the `cluster-housekeeping` namespace (`ghcr-purge`, manifest at `albright-studios/k8s/ghcr-purge-cronjob.yml`) runs daily at 04:13 UTC and:

- KEEPS every container version that currently has a tag (any tag).
- KEEPS the 5 most recent untagged versions per package (rollback safety).
- DELETES untagged versions older than 7 days.

Token required: `delete:packages` scope on the `github-credentials` k8s secret. Audit by checking the `ghcr-purge-...` Job logs in `cluster-housekeeping`.

## Anti-stalling protocol (operator standing instruction)

**Do not ask the operator to do anything you can do.** "I'll wait for you to merge" / "operator must approve" / "you need to scale this" is stalling unless the operation literally cannot be reversed. Even production-trading-repo merges, env switches on trading services, and shim retirements are inside my standing RHLF/bypass authorization — the operator overrode the default-confirm rule explicitly.

Before any action that *could* break something, follow this sequence — never the "ask first" sequence:

1. **Snapshot.** `kubectl get <kind>/<name> -n <ns> -o yaml > /tmp/rollback-<name>.yaml` before any `kubectl set env`, `kubectl scale`, `kubectl patch`. Same for git: `git rev-parse HEAD > /tmp/rollback-sha` before any force-push.
2. **Try.** Apply the change. Verify it works (probe the endpoint, run a real test, check pod readiness, run the existing smoke spec).
3. **If broken: roll back instantly.** `kubectl apply -f /tmp/rollback-<name>.yaml` or `git reset --hard $(cat /tmp/rollback-sha)`. Then pick the next avenue. Don't ask.
4. **If working: persist.** Commit the cluster-side change to git so it survives a pod restart / power outage / cluster wipe — `cluster is the truth, GitHub must match`.
5. **If genuinely irreversible** (DROP TABLE, delete K8s Secret with no backup, force-push over published history, kill a live-trading position) — THEN ask. Otherwise execute.

The narrow set of *actually* irreversible: dropping DB tables, deleting K8s Secrets without a backup, force-pushing main of `brightflow-live` / `ibkr-real-money-gateway` / `daxxon-trading` over published history, taking down a live trading pod mid-market with no rollback path. Everything else: snapshot, try, roll back if broken, continue. **Pausing without a snapshot-rollback story is stalling.**

## Multi-agent dispatch (≥3 sub-agents per branch)

**When chasing a branch of work with sub-tasks, the master agent ALWAYS launches at least 3 sub-agents in parallel.** The master agent's context window is reserved for orchestration — master agents do not do leaf work, they dispatch. The 3-agent minimum applies even when the work appears small: divide it into (a) implementation, (b) validation, and (c) safety/rollback. Those three angles also serve as a built-in cross-check against each other.

Concrete example — "ship a CLAUDE.md rule": sub-agent A edits the canonical file + opens the PR; sub-agent B writes/runs the validation (lint, smoke spec, link-check, TOC comment); sub-agent C prepares the rollback path (snapshots the pre-edit SHA, drafts the revert PR, confirms auto-merge gate is honored). All three launch in the same turn, in parallel — never sequentially.

If a task genuinely cannot be split three ways, that is itself a signal the master agent should be doing it inline rather than spawning a single sub-agent. Sub-agent fan-out exists to preserve orchestration context, not to add latency to trivial work.

## True Validation Protocol (deploy + destroy + re-deploy, TDD, PRD checklist)

**A job is NOT done without provably working code, end-to-end.** Sub-agent self-reports are not proof. The proof must include the six pillars below. Operator caught the master agent claiming "done" based on sub-agent self-reports rather than independent verification — this protocol exists so that never happens again.

### 1. Dashboards via Playwright

Visit the daxxon-mgmt + brightflow-live dashboards after every change. Check (a) the issue we fixed is gone, (b) no new issues introduced, (c) what we touched renders correctly. Fix what we broke, ensure what we shipped actually works.

### 2. The Deploy + Destroy + Re-deploy cycle

When CI pipelines work, the ONLY way to prove the pipeline works is a destroy method, then a deploy method (constant delivery, easy feature testing). So deploy AND destroy AND re-deploy is the only way to prove code is distributable. Sequence:

1. Create something
2. Test something
3. Validate something
4. Write full something as a module that can be distributed or re-used
5. Deploy module
6. Prove module is working as a deployment
7. Do a rolling restart to ensure the code is correct and working
8. Destroy deployment
9. Then re-deploy module and prove the code is good enough for production by rolling restart deployment + verify working

### 3. Module structure standard (devs are adopting this)

Every repo follows:

```
Code Base → modules/
  comment describing Module A
  module_A/
    orchestration_file → calls scripts/ directory module
  module_B/
    orchestration_file → calls scripts/ directory module
  module_C/
    orchestration_file → calls scripts/ directory module
CHANGELOG.md
README.md (with TOC pointing at docs/)
docs/doc1.md, docs/doc2.md
GETTING_STARTED.md
scripts/
  helper_module_A/file{1,2,3}
  helper_module_B/file{1,2,3}
  helper_module_C/file{1,2,3}
unit_test/
.cursorrules — keep code below 100 lines of code per file; use TDD via unit_tests; promote to scripts/ when passing
```

### 4. TDD audit-level discipline

Borrow the precision, verification, and rigor of a professional accountant. Each change is verified, reconciled, cannot be assumed correct.

- **Double-entry discipline for code**: each behavior is first captured as a failing unit test (the specification), THEN implemented as minimal production code to satisfy that test. The "ledger" must balance: tests define expected behavior, code provides actual behavior, all tests must pass before merge.
- **One behavior per PR**; multi-behavior or wide-scope changes are prohibited.
- **Tests fail first.** Only the minimal production code to pass each test is allowed.
- **The Three Laws of TDD:** (1) No production code without a failing unit test first. (2) No more test code than sufficient to fail (compilation failure counts as failing). (3) No more production code than sufficient to pass the currently failing test.
- **One transaction at a time.** Each PR reconciled fully before the next change.
- **Tests must be small, isolated, self-contained, readable.** Multi-assert tests discouraged; one behavior per test.
- **Workflows decoupled, reusable, testable** across all repositories.

### 5. Company-wide enforcement (CI/Terraform layer — documented here, implemented separately)

- All PRs run unit tests on self-hosted GitHub Actions runners
- Minimum coverage threshold default 85%
- PRs blocked if tests fail, coverage drops, or multiple behaviors added
- Auto-merge only when all checks pass
- Terraform branch protection enforces required status checks; cannot be bypassed
- Reusable workflow `reusable-unit-tests.yml`; per-repo `pr.yml` calls it; `auto-merge.yml` gates on checks

### 6. PRD checklist — every PRD asks these questions before declaring "done"

- Have you committed this to repo?
- Have you pushed this to the repo?
- Have you done a rolling restart?
- Will this commit, push, rolling restart survive a power interruption — or will I need more work to bring it back up?
- Have you tested this by trying all the APIs to ensure it is actually reporting the way it should?
- Have you updated or added a GitHub issue to report what was done, what is working, and what has true reported validated tests?
- Did we leave anything out? Did we forget anything? Have we done everything we discussed?

## Privacy: repos + images are PRIVATE by default

**Source code and container images for this org are private.** Customer code, internal tooling, trading models, dashboards, infra manifests, and Studios assets are not for public consumption.

- **Repos:** new repos must be created with `--visibility private`. Audited by `gh repo list AlbrightLaboratories --visibility public` — expected result: empty.
- **GHCR packages:** new container packages inherit visibility from the source repo (private). Audited by `gh api '/orgs/AlbrightLaboratories/packages?package_type=container&visibility=public'` — expected result: `[]`.
- If you discover a public repo or package that should not be public, flip it immediately:
  - Repo: `gh repo edit AlbrightLaboratories/<name> --visibility private`
  - Package: `gh api -X PATCH '/orgs/AlbrightLaboratories/packages/container/<name>/visibility' -f visibility=private`
- Anything inadvertently published publicly (even briefly) is considered exposed. Rotate any secrets that were in those commits and assume the artefact has been scraped.

Last audit on 2026-05-19: 68/68 repos private, 94/94 container packages private. ✅

## Base images → GHCR mirror

Dockerfiles **must not** `FROM` a docker.io image, `public.ecr.aws` image, or any other 3rd-party registry. Pull base images from the org-controlled GHCR mirror instead:

```dockerfile
FROM ghcr.io/albrightlaboratories/mirror/library-python:3.11-slim
FROM ghcr.io/albrightlaboratories/mirror/library-node:20-alpine
FROM ghcr.io/albrightlaboratories/mirror/library-nginx:alpine
FROM nvcr.io/nvidia/cuda:12.4.0-devel-ubuntu22.04   # NVIDIA is the exception — their own canonical registry
```

Naming: docker.io `library/<name>:<tag>` becomes `ghcr.io/albrightlaboratories/mirror/library-<name>:<tag>` (kebab, not slash — GHCR doesn't nest beyond org/repo).

The mirror is populated by `.github/workflows/mirror-vendor-base-images.yml` on a weekly cadence plus one-off `crane copy` runs when a new tag is needed. To add a new tag: run the workflow with `workflow_dispatch` after editing the matrix, OR run `crane copy <upstream>:<tag> ghcr.io/albrightlaboratories/mirror/library-<name>:<tag>` locally. The `scripts/dockerfile_sweep.py` tool rewrites existing Dockerfiles to the GHCR mirror path (including migrating any legacy `public.ecr.aws` refs).

## Working style: just do it

Once the operator gives a directive, follow it through to the end of the deploy chain (commit → push → restart → test → issue update) without pausing to confirm common-sense intermediate steps. The 6-point deploy checklist is standing authorization. Re-asking the operator to confirm "should I push?" or "want me to commit?" after they've said "fix it" / "ship it" / "make it work" is treating prior authorization as if it expired. It doesn't.

Ask the operator only when:

- Scope is genuinely ambiguous (option A vs option B with different outcomes)
- An action is destructive AND not implied by the directive (e.g. they asked you to fix a bug but you'd need to drop a prod table)
- You need information you cannot derive — but **follow the Secret Lookup Order below** before asking; the answer is usually there

## Secret / env / config lookup order

**Hard rule.** Before asking the operator for ANY value (token, secret, project ID, env var, URL, password, API key, config), search these in order and stop at the first hit:

1. **GitHub secrets** of the most-relevant repo: `gh secret list -R AlbrightLaboratories/<repo>`. For org-wide secrets: `gh secret list --org AlbrightLaboratories`.
2. **Kubernetes cluster secrets** in the relevant namespace: `kubectl get secrets -n <ns>`, then `kubectl get secret <name> -o jsonpath='{.data.<key>}' | base64 -d`. CLAUDE.md per-repo often documents the canonical secret names (e.g. `daxxon-rag-secret` key `POSTGRES_PASSWORD`, `github-credentials` key `token`).
3. **`~/.zshrc`** on the operator's workstation: `grep -i <KEY> ~/.zshrc`. Operator-managed env vars (`GOOGLE_CLOUD_PROJECT`, `ANTHROPIC_API_KEY`, etc.) live here.

Only escalate to the operator if all three return empty. **Never** ask "what is the value of X?" without proving the 3-tier check came back empty — that's lost minutes the operator has to spend on something they already published.

Pass this rule into every sub-agent / worker prompt — they don't share your memory and will re-ask if not told.

Rationale: the operator has repeated this rule multiple times. Every re-ask is a trust tax and treats prior context as if it expired. 2026-06-02 incident: spent compute cycles asking about `GOOGLE_CLOUD_PROJECT` while it was sitting in `~/.zshrc` and the answer was discoverable in 5 seconds.

Otherwise: act, report what you did. Force-pushing `main` is allowed when it's the only way to satisfy the operator's instruction (e.g. stripping a forbidden trailer from already-pushed commits) — always use `--force-with-lease`.

When the operator reports a problem, the default action is to fix it, not narrate it. If a fix is small and well-scoped, do it. If you find yourself writing "here's what's still broken and a list of options" mid-task, you're stalling.

## Completion verification — never claim "done" without proof

**Hard rule.** Before saying "done", "finished", "complete", "deployed", "verified", "working", or any equivalent — and before allowing a Stop hook to release — Claude MUST:

1. **Write a unit test or bash verification script** that exercises the change, and run it. Show the operator the output.
2. **List every factual or functional claim** just made (e.g. "X is patched", "Y is deployed", "Z returns 200") and provide the terminal output or exact `file:line` reference proving each one. No claim without evidence.
3. **State the wrong version of the answer** + the edge cases glossed over. Reasoning: this surfaces what was skipped. If nothing was skipped, say so explicitly.
4. **Answer this question, unprompted:** "How does the operator verify this work independently RIGHT NOW without relying on me?" Give the exact command(s) or URL(s) they can run/visit.

If any of (1)–(4) cannot be answered honestly, the task is NOT done. Keep working. Never substitute "I think it should work" for evidence.

**Reasoning over speed.** If an attempt fails, **alter the approach** — do not repeat the same failed strategy expecting a different outcome. Thoroughness > speed. The operator would rather have a 30-minute task done correctly than a 5-minute task that needs three rounds of correction.

**Stop hook gate.** A `Stop` hook in `~/.claude/settings.json` runs `.claude/verify.sh` in the repo (if present) when Claude attempts to stop. Non-zero exit blocks the stop and feeds the failure output back to Claude. Per-repo: drop a `.claude/verify.sh` that runs the project's lint + test + smoke checks; absence of the file means the gate no-ops. The gate sees `stop_hook_active: true` on re-entry and lets through to avoid infinite loops — but Claude SHOULD have fixed the issue before that point.

## Risky operations require confirmation

This org runs real-money trading services. Default to confirming before any operation that:
- Pushes to `main` of a production trading repo (`brightflow-live`, `ibkr-real-money-gateway`, `daxxon-trading`)
- Rolls a deployment in the `ibkr-live-trader` namespace
- Force-pushes, hard-resets, or rewrites history on any shared branch
- Deletes GHCR packages, K8s secrets, or any resource in `daxxon-rag` / `ibkr-live-trader` / `brightflow-ml`

For local-only or sandbox repos (e.g. `alpaca-paper-trader`, `brightflow-sandbox`), normal Claude-default confirmation thresholds apply.

---

<!-- Repo-specific guidance below this marker. The sweep workflow preserves everything below. -->

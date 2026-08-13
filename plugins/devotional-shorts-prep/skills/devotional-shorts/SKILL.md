---
name: devotional-shorts
description: Create and coordinate Korean devotional Shorts preparation from a full Bible passage, including exegesis, devotional points, scored hooks, spoken scripts, review reports, revisions, approval checks, and approval-gated image generation and delivery. Use when the user supplies a meditation passage, asks for 묵상 포인트 or 묵상 쇼츠 원고, requests a revision, approves a reported draft, asks to generate or recover approved scene images, or uses 승인 확인, 보고 재전송, or 작업 상태 commands.
---

# Devotional Shorts

Turn a full meditation passage into a source-grounded Korean Shorts draft. Keep content rules in the bundled references instead of improvising a new format.

## Route the request

Read [`references/system-boundaries.md`](references/system-boundaries.md) before writing files, contacting Telegram, checking approval, or generating images.

- For a full passage or a request for devotional points, follow **Draft a new script**.
- For feedback on an existing draft, follow **Revise an existing draft**.
- For `승인 확인`, `보고 재전송`, or `작업 상태`, read [`references/telegram-contract.md`](references/telegram-contract.md) and follow its exact command routing.
- For an `approved` result or a request to generate, recover, or deliver approved images, read [`references/image-contract.md`](references/image-contract.md) and follow its exact gate and command loop.
- Use `scripts/content_workflow.py` for content checking, scene segmentation, report rendering, and draft or revision creation. Use `scripts/job_store.py` for later state writes. Use only bundled deterministic scripts for other file or Telegram operations. If the installed version lacks a required script, report that the operation is unavailable; never pretend it succeeded.
- Use `scripts/image_workflow.py` as the only approval gate and persistence path around built-in ImageGen calls.
- Resolve `scripts/` from the plugin root, two directories above this `SKILL.md`; do not assume the user's project contains those scripts. Pass the user's project separately as `--project-root`.

## Draft a new script

1. Require the full passage text. Accept the reference, title, date, theological emphasis, and visual direction as optional context.
2. Read [`references/method.md`](references/method.md) and [`references/quality-evaluation.md`](references/quality-evaluation.md) completely before drafting. Apply the declared method version exactly.
3. If approved examples exist, read [`references/examples.md`](references/examples.md) and use them only as style evidence. Never turn a single example into a new rule.
4. Read [`references/generation-contract.md`](references/generation-contract.md) and use its exact input and generation JSON fields.
5. Separate what the passage states from interpretation, inference, and present-day application.
6. Produce the five devotional-point fields in their required order.
7. Generate ten distinct opening questions and ten distinct thumbnail phrases. Score every candidate with the reference rubrics, select one, and keep the next three as alternatives.
8. Write the spoken script in the fixed ten-part order, including the exact CTA.
9. Use the bundled `segment` command to obtain immutable scene boundaries, then add a Korean visual description and English prompt to every boundary.
10. Apply the five-axis quality evaluation. Repair only failed axes once. If any axis remains below 4, the average remains below 4.2, or an immediate-failure condition remains, stop without reporting or saving.
11. Run the bundled `check` command. Repair only its failed checks once; if the second check fails, stop and give a Korean recovery instruction.
12. Use the bundled `create-draft` command to render [`templates/report.md`](templates/report.md) and atomically store the validated `DRAFT` outside the plugin under the current project `jobs/` directory.
13. If Telegram settings are available, run `preflight` and `send-report` from the Telegram contract. If configuration or delivery fails, preserve the local `DRAFT` and give its job ID, report path, and Korean recovery instruction.
14. Return the saved job ID, revision, method version, five quality scores and average, review warnings, report path, self-check result, and actual Telegram status. Never infer a successful report from a local draft.

## Revise an existing draft

1. Identify the requested change and the sections that must remain unchanged.
2. Read [`references/generation-contract.md`](references/generation-contract.md). Preserve non-target sections verbatim and name them with `--preserve-section`; use `--full-regeneration` when core meaning changes.
3. Recompute dependent sections only when the changed core meaning affects them.
4. Re-run the five-axis quality evaluation and `check`, repair a failed item once, then use `create-revision` with a unique idempotency key.
5. Report which sections changed and which were preserved.
6. Treat the revision as requiring a new approval; never reuse approval from earlier content.

## Guardrails

- Do not invent a passage reference, historical detail, speaker motive, or quotation source.
- Do not generate images merely because prompts or visual directions are discussed.
- Do not send content to Telegram before its local report has been saved successfully.
- Do not present an ambiguous, conditional, revision, or hold response as approval.
- Do not call ImageGen unless the current job revision, approval decision, report message ID, and content hashes pass the approval gate.
- Do not change the method from raw conversations or new examples during a production run.
- If a user instruction conflicts with factual accuracy or the approval gate, keep the guardrail and explain the conflict.

## Run approval commands

- For `승인 확인`, use `approval_workflow.py check-approval`; never classify an unvalidated raw Telegram message directly.
- For `보고 재전송`, use `approval_workflow.py resend-report`; never reuse an earlier summary message ID.
- For `작업 상태`, use `approval_workflow.py status`; this command must not require Telegram configuration or network access.
- If the job ID is omitted, allow automatic selection only when exactly one approval-pending job exists.
- On `revision_requested`, preserve the feedback and follow **Revise an existing draft**. On `hold` or `unclear`, stop. On `approved`, use the image contract; never skip its independent approval gate.

## Generate approved images

1. Read [`references/image-contract.md`](references/image-contract.md) completely.
2. Run its `start` command before any ImageGen call.
3. Use `next-scene`, call the installed `imagegen` skill's built-in tool once with the returned prompt, and record success or failure before requesting another scene.
4. Stop after a second failure and preserve every successful file. Use `resume-failed` only with the same approved prompt.
5. Run `deliver` after generation stops. Report the final job state, generated·failed·delivered counts, and local image folder.

## Output order

Return the method version, input warning, five devotional points, selected and alternative hooks, full script, estimated duration, verified quotation sources, five quality scores and average, and self-check result in that order. Keep candidate scoring details concise unless the user asks to inspect all candidates.

When producing a persisted review report, include every section from the report template even when its value is `없음`.

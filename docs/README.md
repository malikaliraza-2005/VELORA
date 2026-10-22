# VELORA documentation

VELORA is a Pakistan-first, WhatsApp-based ordering SaaS for restaurants and cloud kitchens. Hotels come later. This folder holds two kinds of documents, and the difference matters.

## Folder structure

```text
docs/
├── research/       Research inputs (NotebookLM-generated). NOT approved requirements.
├── architecture/   Recorded architecture decisions, with status for each.
├── planning/       MVP scope, implementation plan (master roadmap), status tracker, OpenWA validation plan, local testing guide.
│   └── phases/     One detailed task file per phase, P0–P13.
└── README.md       This file.
```

| Folder | What it contains | Authority |
|---|---|---|
| `research/` | 17 research documents (01–15, 17, 18) produced with NotebookLM before the architecture review. | **Input only.** Many claims were found to be outdated, unverified or contradictory during the review. Labels such as "Status: Approved" and "Verified Facts" inside these files are part of the original research output and **do not** mean the owner approved them. |
| `architecture/` | [`architecture-decisions.md`](architecture/architecture-decisions.md): every major decision with its rationale, alternatives, trade-offs and status. | **Authoritative** for anything marked APPROVED. Items marked ADOPTED (delegated) are authoritative but can be reversed by the owner. |
| `planning/` | [`mvp-scope.md`](planning/mvp-scope.md), [`implementation-plan.md`](planning/implementation-plan.md) (master roadmap), [`implementation-status.md`](planning/implementation-status.md), [`openwa-validation-plan.md`](planning/openwa-validation-plan.md), [`local-testing-guide.md`](planning/local-testing-guide.md) (try it by hand) | Authoritative plan, derived from the decisions. |
| `planning/phases/` | `p00-…md` to `p13-…md`: detailed tasks for each phase, each with prerequisites, deliverables, security, tests, acceptance criteria and rollback | Authoritative task detail. **Status is tracked only in `implementation-status.md`.** |

## Precedence

If documents disagree, the higher item wins:

1. Decisions the owner approved explicitly (status **APPROVED** in `architecture/architecture-decisions.md`).
2. Decisions the owner delegated to the engineering lead (status **ADOPTED (delegated)**).
3. Planning documents in `planning/`.
4. Research documents in `research/`.

A recommendation in `research/` is never a requirement just because it appears there.

## Status legend (used in all engineering documents)

| Status | Meaning |
|---|---|
| **APPROVED** | The owner explicitly approved it. |
| **ADOPTED (delegated)** | The owner asked the engineering lead to decide. Reversible. Flagged for owner review. |
| **VALIDATION-DEPENDENT** | Cannot be finalised until the OpenWA validation trial results are in. |
| **PENDING EXTERNAL** | Needs a lawyer, accountant, merchant interviews or vendor confirmation. |
| **NOT APPROVED / OPTIONAL** | Proposed, but not accepted. Not in scope. |
| **REJECTED** | Considered and rejected, with reasons recorded. |
| **DEFERRED / ROADMAP** | Out of MVP scope. Kept for later phases. |

## Notes on the research files

- **No original PRD in the repository.** The research docs cite `PRODUCT_REQUIREMENTS.md` [PRD] and OpenWA documentation as their sources, but neither exists here, so those citations cannot be checked. Decision AD-13 assumes no original PRD exists.
- **No doc 16.** `16-implementation-roadmap.md` never existed and was not created. [`planning/implementation-plan.md`](planning/implementation-plan.md) covers that role.
- **File renamed.** `01-executive-summary-and-feasibility copy.md` was renamed to `research/01-executive-summary-and-feasibility.md` on 2026-10-20. This matches the title line inside the file and the plain-text references in docs 12 and 13. The content is unchanged.
- **File kept with `-v2`.** `12-pakistan-payments-and-localization-v2.md` keeps its suffix because its title line and doc 15 both use that name.
- **Moved without edits.** All research files moved from `docs/` to `docs/research/` on 2026-10-20. SHA-256 checksums were compared before and after the move and all 17 match. No research content was edited.
- **No hyperlinks between research docs.** They only mention each other by plain-text filename, so the move broke no links.

## Product constraints (owner)

- VELORA has its **own database**. Stock Flow is a separate project and is **not** used.
- OpenWA is the intended MVP WhatsApp integration. It is **unvalidated** until the gates in [`planning/openwa-validation-plan.md`](planning/openwa-validation-plan.md) pass, and **is not claimed to be compliant or production-ready**. The official Meta WhatsApp Cloud API is documented as the alternative.
- A payment is never marked verified from AI output or a screenshot alone.

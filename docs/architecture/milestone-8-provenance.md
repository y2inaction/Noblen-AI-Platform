# Milestone 8: Permission propagation and provenance (implementation contract)

**Status:** In progress. It implements ADR-0037, which **stays Proposed until every
gate in §9 passes**, and builds on ADR-0035 (implemented) and ADR-0036 (accepted).
RLS (ADR-0034) is out of scope. It is reviewed separately after M8 and is not
started automatically.

The question M8 answers: **can permission context survive the whole AI execution
chain?** The chain is Human → Agent → Workflow → Tool → Knowledge/Memory/Integration
→ Result → Persistence → Publication → Viewer. Provenance is attribution. Authorization
remains a decision made at read time, against the viewer's *current* permissions.

## 1. Decisions (approved)

1. **Widening visibility.** Run content is visible to the run's person, or to a viewer
   who can read *every* recorded source (`participant OR can_read_sources`). Admins and
   superusers get no bypass for private sources. The existing `knowledge:read_all`
   remains the only explicit exception, and only for knowledge sources.
2. **Fail closed.** Only the run's person sees the content when:
   - `sources_truncated = true`;
   - any source is missing, deleted or unresolvable;
   - `sources IS NULL` (a run from before M8).
3. **Restricted-publication approval is deferred.** M8 records provenance and
   publication attribution. `require_approval_to_publish_restricted` is not introduced.
4. **ADR-0036 is Accepted** (this milestone's first commit).
5. **Branch:** `claude/happy-davinci-bufc14`, restarted from `main` at `3ec2f43`
   (fast-forward, no force-push). One draft PR into `main`.

## 2. Scope

**In:**
- recording the sources of agent runs, workflow step runs and workflow runs;
- read-time authorization through the recorded sources;
- attribution of publications;
- tests, frontend handling of withheld reasons, docs.

**Out:**
- RLS;
- the publication-approval gate;
- policy engines, content classifiers, encryption changes;
- new product features and unrelated refactoring.

## 3. Security invariants (each has a test)

- **I1. No disclosure beyond sources.** Seeing a run's content never tells a viewer
  anything they cannot already read.
- **I2. References only.** Provenance stores types and ids, never content text.
- **I3. Fail closed.** Missing, truncated, deleted or unknown provenance hides the
  content from everyone but the run's person.
- **I4. Server-side only.** Provenance is written only by server capture points. No
  API field and no model output can write it.
- **I5. No admin bypass.** Admins and superusers get no bypass for private sources,
  except `knowledge:read_all` for knowledge sources.
- **I6. Tenant isolation.** A reference from another organization can never be
  recorded or satisfy a check.
- **I7. ADR-0035 unchanged.** Its metadata rule and approver rule hold, and all 14 of
  its tests pass unchanged.
- **I8. Current-time authorization.** The check uses the viewer's permissions at read
  time. Revoking access to a source hides content the viewer saw before; granting
  access to every source reveals content hidden before.
- **I9. Bounded cost.** Resolution is bulk per reference type. The number of queries
  does not depend on the number of references.

## 4. Data model (one reversible migration)

| Table | New columns |
|---|---|
| `agent_runs` | `sources` JSON NULL, `sources_truncated` bool, `acting_role` |
| `workflow_step_runs` | `sources` JSON NULL, `sources_truncated` bool |
| `workflow_runs` | `sources` JSON NULL (accumulated over its steps), `sources_truncated` bool, `acting_role` |

- A reference is `{type, id}`. The types are `knowledge_document`,
  `knowledge_table`, `memory` (with its scope), `integration`, `workflow_step`,
  `agent_run`, `conversation` (M8.7, see §6) and `external_input`.
- At most 500 references per source-bearing row. On overflow, `sources_truncated =
  true`.
- **Truncation propagates.** A step that inherits from a truncated upstream step, or
  a workflow run with any truncated step, is itself truncated.
- `NULL` means unknown. That is pre-M8 data, treated as fail-closed.

## 5. Capture points

- A small collector on `ToolContext`, `provenance.add(ref)`, written only by server
  code.
- `search_knowledge` and the knowledge-table tools record only the documents and
  tables actually returned, never the candidates that were filtered out.
- Memory records the memories injected by `PERSISTENT` context and those returned by
  `recall_memories`.
- `IntegrationGateway.use` and `call_mcp` record the connection used.
- Workflow templating records which `steps.<id>` paths `render()` read. The step
  inherits those steps' sources and truncation, transitively and conservatively.
- An agent step's sources are its agent run's sources.
- A workflow run's sources are the union of its steps' sources.
- Webhook, event and manual run input is recorded as `external_input`.
- An agent run started through the agent API records the conversation it reads (the
  person's message and, per the memory mode, earlier turns) as `conversation`. The
  conversation the workflow engine creates for one step is not recorded: it holds
  only the rendered step input, whose provenance the step already inherits.

## 6. Read rule

- **Rule.** Run content is visible to the run's person, or to a viewer who can read
  every recorded source *now*: `participant OR can_read_sources`. The viewer's
  current permissions decide (I8). Nothing captured at execution time grants
  access.
- `rbac/visibility.resolve_source_access` / `can_read_sources` resolve references in
  bulk, inside the run's organization, reusing the existing checks:

  | Reference | Check |
  |---|---|
  | `knowledge_document` | `readable_documents_query` (`knowledge:read_all` is the only bypass) |
  | `knowledge_table` | its parent document |
  | `memory` | `_visible_to_member` on the stored row (the reference's scope label is not trusted), retention applied |
  | `integration` | `integration:use` |
  | `conversation` | `conversations.readable_by`, the conversation-participant rule |
  | `external_input` | organization member |
  | `workflow_step` / `agent_run` | their own recorded sources, followed at most `MAX_PROVENANCE_DEPTH` (4) levels |

- **Fail closed.** For anyone but the run's person, each of these withholds the
  content as `unknown_provenance`:
  - `sources IS NULL` (before M8);
  - `sources = []`, because an empty list proves nothing and never widens
    visibility;
  - `sources_truncated = true`, which propagates (§4);
  - a malformed reference, or one that cannot be resolved: missing, deleted, or in
    another organization;
  - a run reference that forms a cycle or lies deeper than the depth limit.
- **Restricted.** When every reference resolves but the viewer cannot read one or
  more of them, the content is withheld as `restricted_sources`. The response never
  says which source denied access.
- **Reasons.** `content_withheld_reason` is `null` when the content is shown.
  Otherwise it is exactly one of `restricted_sources` or `unknown_provenance`.
  There is no other externally visible reason.
- **Cost (I9).** One query per reference type present, plus one per run type for
  each level of run nesting (at most `MAX_PROVENANCE_DEPTH`). The count never
  depends on the number of references. List endpoints resolve all their runs in
  one batch.
- The ADR-0035 presenters stay the single place where the rule is applied: agent
  and workflow runs (list, detail and decision responses), their step outputs and
  errors, escalation reasons in the operations overview, and the execution result
  returned to an approver. Approvers still see the approval requests they decide.

## 7. Publication attribution

- Tasks and AGENT/ORGANIZATION memories created by a run store `source_run_id`.
- The audit events `agent.tool_executed` and `workflow.tool_executed` carry
  `source_counts` and `restricted: bool`, never content.

## 8. Test matrix (written first; every new test fails before its fix)

- **Recording.**
  - The collector captures exactly the documents, memories and connections
    returned, not the filtered candidates.
  - Workflow steps inherit transitively, and workflow runs accumulate.
  - 501 references → truncated, and truncation propagates to children and to the
    workflow run.
- **Canary matrix.** VIEWER, MEMBER, MANAGER and ADMIN × source kind:

  | Source | Expected |
  |---|---|
  | organization knowledge | visible |
  | restricted document without a grant | withheld |
  | restricted document, grant added later | visible |
  | deleted source | withheld |
  | private memory | withheld |
  | truncated provenance | withheld |
  | pre-M8 run | withheld |

- **Current-time authorization (I8).**
  - *Revocation:* a run uses restricted document X, the viewer can read X, and the
    content is visible. The viewer's access to X is revoked. The same viewer is now
    withheld.
  - *Grant:* the run completes while the viewer lacks access, so the content is
    withheld. The viewer is granted every source, and the content becomes visible.
- **Invariants.**
  - The canary never appears anywhere in `sources` (I2).
  - No API input accepts `sources` or `sources_truncated` (I4).
  - An admin without a grant is withheld (I5).
  - A reference from another organization is rejected (I6).
  - The ADR-0035 suite passes unchanged (I7).
- **Every provenance field is asserted:** organization, initiator, acting role, agent
  and version, workflow, version and step, tool, sources, timestamps.
- **Performance (I9).** The query count for one non-participant content read is
  identical for 1 and for 500 references.

## 9. Gates before asking to merge

- ruff, format and mypy clean.
- Full SQLite suite, and the full PostgreSQL + pgvector suite.
- Migration upgrade → downgrade → upgrade on PostgreSQL.
- Frontend lint, typecheck and build.
- The 13-step browser walkthrough with no console errors.
- A live cross-role check.
- CI green on the final head.
- Only then is ADR-0037 finalized as Implemented.

## 10. Commit sequence and stop points

| Step | Content | Stop point |
|---|---|---|
| M8.1 | `docs(security): accept ADR-0036 visibility model`, plus this contract | **stop and report** |
| M8.2 | security tests (failing) | **stop and report the failing evidence** |
| M8.3 | schema and migration | |
| M8.4 | provenance collector | |
| M8.5 | capture points | |
| M8.6 | transitive provenance and workflow-run accumulation | |
| M8.7 | current-time authorization (read rule, presenters) | |
| M8.8 | publication attribution | |
| M8.9 | frontend | |
| M8.10 | final docs, ADR-0037 finalized, validation | **stop and report; no merge without explicit approval** |

- If any existing test has to change, stop and explain why before changing it.
- The merge is a normal non-squash merge commit, "Merge PR #N: Milestone 8 —
  Permission Propagation & Provenance", and only after approval.

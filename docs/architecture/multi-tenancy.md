# Multi-Tenancy

**Status:** ✅ Implemented (Phase 1) and applied to every later table, including
M1's `agent_runs` and `agent_run_steps`.

```
Noblen
 ├── Organization A ── members · agents · versions · conversations · approvals
 │                     · runs · knowledge · usage · audit
 └── Organization B ── …
```

## Enforcement (defence in depth)

1. **Request:** the active organization comes from the token or
   `X-Organization-Id`, and an ACTIVE membership is verified in the database on
   every request.
2. **Service:** queries go through `tenant_scoped(...)`, which is generic over the
   statement since M1. A row in another tenant behaves like a missing row (404).
3. **Runtime:** tools receive the organization from the run's context and never
   from model arguments. Knowledge retrieval is org-scoped in SQL.
4. **Tests (permanent quality gate):** `tests/test_tenant_isolation.py`, the
   Phase 3 and 4 isolation tests, and M1's
   `test_runs_and_operations_are_tenant_isolated` (runs, run detail, operations
   metrics and approval modification across organizations).
5. **Planned:** PostgreSQL row-level security on the highest-risk tables.

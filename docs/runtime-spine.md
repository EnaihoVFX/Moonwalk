# Runtime Spine

This is the active Moonwalk runtime architecture.

## Active path

1. `backend/servers/*`
   - receive user input / UI actions
   - normalize approval actions into text follow-ups
2. `backend/agent/core_v2.py`
   - main V2 runtime loop
   - world state assembly
   - routing
   - tool selection
   - milestone planning
   - plan gating
   - milestone execution
   - verification and memory updates
3. `backend/agent/task_planner.py`
   - produces `MilestonePlan`
   - milestone planning, compound-task checks, and sync fallback only
4. `backend/agent/planner.py`
   - runtime milestone dataclasses plus `ExecutionStep` execution primitive
5. `backend/agent/legacy_planner.py`
   - legacy `ExecutionPlan` / `PlanTemplates` compatibility surface for offline tests
6. `backend/agent/legacy_task_planner.py`
   - legacy step-plan compatibility mixin for offline validation only
7. `backend/agent/milestone_executor.py`
   - executes milestone micro-loops
8. `backend/agent/verifier.py`
   - evidence gate for tool results
9. `backend/tools/selector.py`
   - request-scoped tool surface
10. `backend/browser/*` + extension
   - browser perception and browser-specific reasoning helpers

## Active planning unit

`MilestonePlan` is the only active planning unit in the V2 runtime.

The live V2 path no longer branches into step-plan execution.

## Legacy code

The repository still contains isolated step-plan compatibility code for tests.
That code is not part of the active V2 runtime spine and should be treated as
compatibility baggage, not as current architecture.

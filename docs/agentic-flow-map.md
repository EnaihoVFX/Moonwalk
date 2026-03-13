# Moonwalk V2 Agentic Flow Map

This document maps the current V2 runtime as it exists today.

It is not a target architecture doc. It is a "what actually happens now" doc so we can:

1. see where the current agent loop is strong,
2. see where templates still short-circuit reasoning,
3. identify which branches already look OpenClaw-like,
4. plan the remaining migration from template-led planning to loop-led execution.

## Core runtime components

### Ingress and transport

- `backend/servers/local_server.py`
  Local Electron/WebSocket entrypoint. Receives `text_input`, `audio_chunk`, and `user_action`.
- `backend/servers/mac_client.py`
  Mac-side relay that maps UI actions like `approve_plan` / `cancel_plan` into agent-follow-up text.

### Main agent runtime

- `backend/agent/core_v2.py`
  Main runtime loop for V2. Owns:
  - request intake
  - world-state assembly
  - routing
  - tool selection
  - planning
  - approval gating
  - step execution
  - milestone execution
  - verification
  - working memory and research logging
  - final response dispatch

### Intent and task modeling

- `backend/agent/world_state.py`
  Defines:
  - `UserIntent`
  - `TaskGraph`
  - `WorldState`
  - `IntentParser`

### Planning

- `backend/agent/task_planner.py`
  Builds `MilestonePlan` objects for all requests, plus compound-task checks
  and sync fallback handling.

- `backend/agent/template_registry.py`
  Loads JSON packs and surfaces them as advisory skill overlays only.

- `backend/agent/planner.py`
  Dataclasses for milestone planning plus the `ExecutionStep` primitive used by
  the milestone executor.

- `backend/agent/legacy_planner.py`
  Legacy `ExecutionPlan` / `PlanTemplates` helpers kept only for compatibility
  tests and offline validation.

- `backend/agent/legacy_task_planner.py`
  Legacy step-plan template/preflight compatibility mixin kept only for tests
  and offline validation.

### Execution

- `backend/agent/milestone_executor.py`
  LLM micro-loop for all milestone plans.

### Tooling, browser, and verification

- `backend/tools/selector.py`
  Narrows the tool surface for the current request.
- `backend/tools/browser_tools.py`
  Raw browser-ref tools.
- `backend/tools/browser_aci.py`
  Higher-level browser ACI tools used by the milestone loop.
- `backend/agent/verifier.py`
  Tool result verification and retry/failure guidance.

### Memory

- `backend/agent/memory.py`
  Working memory for actions, entities, and research snippets.

## High-level state machine

The current runtime is best understood as this sequence:

1. transport receives user input
2. `MoonwalkAgentV2.run()` starts
3. conversation/user-profile context is updated
4. `WorldState` is built
5. pending-plan follow-up is checked
6. model routing chooses FAST or POWERFUL
7. tool selector narrows the tool set
8. planner produces a milestone plan
9. high-risk plans may pause behind a plan gate
10. execution runs through the milestone micro-loop
11. each tool call is verified, logged, and optionally repaired
12. final response is sent to UI and memory is updated

## Detailed runtime stages

## Stage 0: Transport ingress

### Local runtime

In `backend/servers/local_server.py`, incoming WebSocket messages are mapped as follows:

- `text_input` -> `assistant.run_agent_text(...)`
- `user_action` -> action mapped to text (`proceed`, `cancel`) -> `assistant.run_agent_text(...)`
- `audio_chunk` -> speech pipeline -> text -> `assistant.run_agent_text(...)`

This means UI button approval is intentionally normalized into the same follow-up language path as a typed/spoken `"proceed"`.

### Cloud runtime

`backend/servers/cloud_server.py` does the same normalization for `user_action`.

## Stage 1: Request intake and session bookkeeping

At the top of `MoonwalkAgentV2.run()` in `backend/agent/core_v2.py`:

- the request is logged
- the foreground task is registered for self-spawn protection
- the user turn is appended to conversation memory
- user profile facts are extracted from the raw utterance
- the UI receives a `thinking` state

This happens before any planning.

## Stage 2: Sense -> build `WorldState`

`_build_world_state()` in `backend/agent/core_v2.py` builds a structured runtime view from:

- current app/window/browser context
- extracted entities from the user text
- parsed intent
- clipboard / selected text / screenshot presence

Then, during planning, the planner augments that with a `TaskGraph`.

## Stage 3: Parse intent + extract task graph

The current system uses two parallel representations:

### `UserIntent`

Fast, shallow summary:

- primary action
- target type
- target value
- parameters
- ambiguity

### `TaskGraph`

Richer compound-task model:

- `primary_action`
- `primary_goal`
- `entities`
- `selectors`
- `constraints`
- `desired_outcomes`
- `unresolved_slots`
- `complexity_score`

This is the key change that stops compound requests from collapsing into a single target too early.

Example:

`edit the latest video in Downloads in CapCut`

becomes roughly:

- action: `modify`
- entities: `app=CapCut`, `folder=Downloads`, `content=video`
- selector: `latest`
- unresolved slot: `specific_edit_instructions`

## Stage 4: Pending-plan follow-up handling

Before normal planning continues, `run()` checks whether there is a frozen pending plan.

Possible follow-up classifications:

- `approve`
- `cancel`
- `modify`

Behavior:

- `cancel` clears the pending plan and responds immediately
- `approve` executes the frozen plan, unless stale-check logic forces one refresh
- `modify` clears the pending plan and replans with the previous plan embedded as context

Staleness is based on:

- age / TTL
- active app change
- browser domain change

with exceptions for approval spoken from the Moonwalk Electron panel while the original browser task is still valid.

## Stage 5: Model routing

`backend/providers/router.py` selects:

- `FAST` for deterministic trivial requests
- `POWERFUL` for almost everything else

Today the rule is intentionally conservative:

- browser work -> POWERFUL
- follow-up pronoun / approval style requests -> POWERFUL
- unusual or garbled speech -> POWERFUL

This stage chooses the provider before planning or execution.

## Stage 6: Tool selection

`backend/tools/selector.py` narrows the candidate tool set using:

- user request text
- current app / URL
- conversation snippet
- intent
- entity types

Important current behavior:

- mixed app + local-file tasks retain `FILE_SYSTEM` tools
- research/browser tasks retain:
  - `web_search`
  - `browser_read_page`
  - `read_page_content`
  - `extract_structured_data`
  - `web_scrape`

This stage matters because the planner can only build plans from the tools it sees.

## Stage 7: Planning

Planning now has two layers:

### 7A. Task-graph-aware planning

`TaskPlanner.create_plan()`:

1. parses `intent`
2. extracts `task_graph`
3. applies hard safety rules
4. decides whether template shortcuts should be bypassed
5. loads matching skill overlays from the template registry
6. optionally still returns a direct template/template-pack plan
7. otherwise falls back to LLM planning

### 7B. Skill overlays

JSON packs are no longer only "emit final plan" shortcuts.

For compound tasks they can now act as advisory overlays:

- strategy hints
- tool hints
- domain guidance

These are injected into the planner prompt and later into the execution loops as `skill_context`.

### 7C. Preflight validation

Before a plan is accepted, `_preflight_validate_plan()` checks:

- tool names / args
- basic tool contract validity
- browser baseline correctness
- research plan structure
- task-graph coverage

This is where shallow plans like "just open the app" can now be rejected for compound tasks.

## Stage 8: Milestone plan generation

`should_use_milestones()` now always returns `True`.

This means every request, from `open spotify` to `research UK housing`, is
converted into a `MilestonePlan`. Simple requests become single-milestone
plans; compound requests produce multi-milestone plans.

## Stage 9: Plan approval gate

For milestone plans, `_should_gate_plan()` decides whether to pause and ask for approval.

Current gating logic:

- no gate for read-only plans
- no gate for low-risk trivial plans
- gate high-risk plans involving side effects
- gate plans with 3+ real steps and side-effect tools

If gated:

1. a pending frozen plan object is created
2. `await_reply` is used with `modal="plan"`
3. the UI gets numbered steps + `plan_id`
4. no real side-effect steps run yet

This is the current "plan-gated execution" layer.

## Stage 10A: Milestone-plan execution

If the plan is a `MilestonePlan`, `_execute_milestone_plan()` runs it milestone by milestone.

Each milestone gets:

- milestone goal
- success signal
- deliverables from prior milestones
- skill overlays
- recent structured observations
- current environment
- hidden raw browser tools, exposed higher-level ACI tools

The milestone executor then loops:

1. perceive
2. prompt LLM
3. choose next tool
4. execute tool
5. verify and log result
6. detect stall / low-yield repetition
7. continue until evidence-backed completion or failure

This is the most OpenClaw-like part of the current system.

## Stage 11: Per-tool execution path

Both execution branches converge on `_execute_step()`.

That method currently handles:

### 11A. Browser freshness

Before browser reads:

- compare current browser URL to stored snapshot URL
- refresh refs if needed

### 11B. Tool execution

Call the tool through the shared tool registry.

### 11C. Post-navigation settling

After:

- `web_search`
- `open_url`
- browser click tools
- `find_and_act` click mode

the runtime waits, refreshes browser refs, and updates tracked URL state.

### 11D. Browser drift recovery

For browser reading/extraction tools:

- `browser_read_page`
- `browser_read_text`
- `read_page_content`
- `extract_structured_data`
- `get_page_summary`

the runtime checks whether the result came from the wrong tab/domain and can:

- switch back to expected tab/domain
- refresh refs
- retry the same read/extract tool

### 11E. Research logging

Research-style tool results are parsed into:

- source URL
- title
- content/snippets
- structured extracted rows

Then:

- detailed snippets are printed into the live `ResearchStream`
- high-value content is stored into working memory as research snippets

### 11F. Verification

Every tool result goes through `ToolVerifier`.

Verifier output includes:

- success/failure
- confidence
- retry suggestion
- suggested fix

Low-confidence results are treated as failures.

### 11G. Retry / fallback / failure

If verification fails:

- retry same step if allowed
- optionally try fallback tool
- otherwise mark step failed

Step-path recovery also has special browser recovery logic for some browser tools.

## Stage 12: Memory, learning, and finalization

During and after execution, the runtime updates:

- conversation memory
- working memory action log
- working memory research snippets
- user profile facts
- foreground-agent state

At the end:

- a final response is resolved
- `send_response` is called
- websocket payload is emitted
- a research summary may be printed
- successful completed plans can be recorded back into the planner example bank

## Scenario traces

## Scenario A: trivial direct command

Example:

`open spotify`

Flow:

1. ingress receives text
2. `run()` logs request and builds `WorldState`
3. no pending plan follow-up
4. router likely chooses `FAST`
5. tool selector keeps simple app-control tools
6. planner sees simple non-compound task graph
7. planner emits a single-milestone plan
9. no approval gate
10. `_execute_milestone_plan()` runs a 1-milestone micro-loop
11. `open_app` executes inside that loop
12. verifier confirms or rejects
13. final response is sent

Key characteristic:

- the LLM loop still exists, but the task stays lightweight because there is
  only one milestone and minimal tool work

## Scenario B: compound native-app + local-file task

Example:

`edit the latest video in Downloads in CapCut`

Flow:

1. request enters `run()`
2. `TaskGraph` preserves:
   - app
   - folder
   - content
   - selector
   - unresolved edit slot
3. tool selector keeps both app and file-system tools
4. planner sees a compound graph
5. template shortcuts are bypassed
6. skill overlays may still be attached
7. milestone planning is selected
8. milestone executor runs milestone loop
9. loop should gather file candidate, open app, import/operate, or pause for missing edit instruction

Key characteristic:

- this is where the architecture is moving from template-led to loop-led

## Scenario C: research -> document

Example:

`research UK housing and create a Google document`

Flow:

1. compound/research task graph is created
2. planner loads research skill overlays
3. milestone planning is selected
4. milestone 1 gathers sources and extracted notes
5. browser ACI tools / research tools run
6. research snippets go into working memory
7. milestone 2 uses synthesized research body for `gdocs_create` / `gdocs_append`
8. milestone completion requires evidence, not just "done"
9. final response returns the doc link

Key characteristic:

- this is currently the strongest path in the agent

## Scenario D: high-risk plan with approval gate

Example:

`research housing options and write a Google doc`

Flow:

1. plan is generated
2. `_should_gate_plan()` decides it has meaningful side effects
3. a frozen pending plan is stored
4. `await_reply` shows a plan modal
5. no side-effect steps run yet
6. user clicks Proceed or says proceed
7. `user_action` is mapped to text
8. `run()` sees pending follow-up = approve
9. stale-check runs
10. frozen plan executes, or is refreshed once if stale

Key characteristic:

- planning and execution are separated

## Scenario E: modify / cancel follow-up

Example:

User sees a plan and says:

- `cancel`
- `instead only research Surrey flats`

Flow:

- `cancel` -> clear pending plan -> respond `Plan cancelled.`
- modification -> clear pending plan -> embed prior plan in replanning request -> generate a new plan

Key characteristic:

- approval does not force replanning
- modification does

## Scenario F: explicit clarification via `await_reply`

Example:

`help me edit my video`

if the runtime cannot infer what edits are required.

Flow:

1. planner detects unresolved slot such as `specific_edit_instructions`
2. preflight requires `await_reply` in the plan
3. execution reaches `await_reply`
4. plan pauses with `awaiting_reply=True`
5. follow-up user answer is interpreted as instruction, not literal text
6. execution resumes using the locked provider

Key characteristic:

- this is a blocking sub-loop inside the main task

## Scenario G: browser research on search results

Example:

`research flats in Egham`

Ideal current flow:

1. `web_search`
2. `extract_structured_data(item_type="results")` or `read_page_content`
3. structured observations include result titles, snippets, and `href`s
4. milestone LLM chooses:
   - `open_url` with extracted `href`, or
   - `find_and_act` on a specific result
5. content page opens
6. `read_page_content` / `web_scrape` / `browser_read_page` extract source content
7. research snippets are stored

Key characteristic:

- this is the path we are currently strengthening
- recent fixes were specifically aimed at making search-result extraction actionable

## Scenario H: browser drift / wrong-tab recovery

Example:

The agent searched Google, but extraction reads a YouTube tab.

Flow:

1. extraction tool returns a result from the wrong domain
2. `_recover_browser_context()` compares observed URL vs expected URL/domain
3. runtime tries `browser_switch_tab`
4. runtime refreshes refs
5. same extraction/read tool is retried

Key characteristic:

- this recovery now applies to high-level ACI read/extract tools, not only raw browser reads

## Scenario I: failure / partial completion

If a milestone cannot be verified:

- repeated zero-yield actions are treated as stall
- unsupported completion claims are rejected
- milestone can fail while later research milestones may continue

Key characteristic:

- milestone execution is the authoritative evidence-aware runtime path

## Current architecture summary

Today Moonwalk V2 is milestone-first:

- all requests plan as milestones
- task graphs determine whether the task is simple or compound
- skill overlays influence the milestone prompt but do not bypass planning
- milestone execution is the authoritative agent loop
- live V2 runtime no longer branches into step-plan execution

## Current architecture tension points

These are the main "not fully OpenClaw yet" areas:

1. legacy `ExecutionPlan` compatibility types still exist in the codebase
   but are now isolated in `backend/agent/legacy_planner.py` and
   `backend/agent/legacy_task_planner.py`

2. some legacy template/step-plan helpers still exist in compatibility modules
   even though they no longer drive the main runtime

3. browser research is split between:
   - raw browser tools
   - ACI tools
   - web fetch/scrape tools

4. the loop is strong for research and compound tasks, but not yet the universal default

## Migration implication

If we want an OpenClaw-style architecture, the direction is:

1. keep `TaskGraph` as the canonical task representation
2. make skill overlays purely advisory
3. push more tasks into a unified control loop
4. make evidence gating consistent across all execution modes
5. reduce the amount of "template matched -> execution decided" behavior

This document should be treated as the baseline map for that migration.

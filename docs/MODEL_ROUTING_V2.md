# Dynamic model routing v2 — OpenCode + Codex

## Goal

Select the best *available* model separately for every routed agent, and select the reasoning effort separately from the model. The host runtime decides which models exist; OpenRouter enriches those models with external benchmark, pricing, latency, and uptime priors.

The system intentionally does **not** ask OpenRouter which models OpenCode or Codex can execute. Availability and quality are separate trust domains.

## Runtime flow

```text
REQUEST
  -> request normalization
  -> task router / risk
  -> continuous task capability profile
  -> routed agents
  -> host model discovery
       OpenCode: .harness/opencode/catalog-snapshot.json
       Codex:    $CODEX_HOME/models_cache.json
                 fallback: codex debug models --bundled
  -> OpenRouter enrichment
  -> per-agent target + hard floors
  -> deterministic ranking
  -> reasoning-effort selection
  -> independence selection for audit/review/verify
  -> provider activation
       OpenCode: active-task.json consumed by existing plugin
       Codex: active-task.json + regenerated .codex/agents/*.toml
```

## Capability vector

Every task receives a 0–5 target:

- `reasoning`
- `coding`
- `tool_use`
- `reliability`

The base vector is:

```json
{"reasoning": 2.0, "coding": 1.5, "tool_use": 2.0, "reliability": 2.5}
```

Regex signals and structured `risk_factors` add deterministic deltas. R2/R3 apply minimum target floors. A task may add explicit minimums with:

```json
{
  "model_requirements": {
    "reasoning": 4.5,
    "reliability": 4.5
  }
}
```

Explicit values are floors, not downgrades.

## Role transformation

The task vector is transformed for each agent. Examples:

- `planner`: +reasoning, +reliability, -coding
- `implementer`: +coding, +tool_use
- `reviewer`: +reasoning, +reliability
- `security-reviewer`: strong +reasoning/+reliability
- `docs-researcher`: +tool_use, -coding

The exact deltas are in `harness/models.json`.

## Hard eligibility

A model must first satisfy the existing model-class floors and risk floors. Examples:

- coding class: reasoning >= 3, coding >= 4, tool_use >= 4, reliability >= 3
- reasoning class: reasoning >= 4, reliability >= 3
- R3 additionally requires reasoning >= 4, reliability >= 4, tool_use >= 3

Unknown capability is zero. Therefore missing evidence never makes a model look stronger.

## Exact quality scoring formula

For each capability dimension `i`:

```text
coverage_i = min(model_i / target_i, 1)

surplus_i =
  0                                      if model_i <= target_i or target_i == 5
  min((model_i-target_i)/(5-target_i),1) otherwise

component_i = 0.85 * coverage_i + 0.15 * surplus_i
```

The model-class base quality weight is made task-sensitive:

```text
dynamic_weight_i = class_weight_i * (0.5 + target_i / 5)
normalized_weight_i = dynamic_weight_i / sum(dynamic_weight)
quality = sum(normalized_weight_i * component_i)
```

Cost and latency are stored on the same 0–5 scale where **5 is better** (cheaper/faster).

Local evidence uses a neutral prior until enough samples exist:

```text
confidence = min(samples / 20, 1)
local_component = (local_score/5)*confidence + 0.5*(1-confidence)
```

Final score:

```text
score = 100 * (
    Wq * quality
  + Wc * cost/5
  + Wl * latency/5
  + We * local_component
)
```

Risk changes the objective weights:

| Risk | Quality | Cost | Latency | Local evidence |
|---|---:|---:|---:|---:|
| R0 | 0.55 | 0.20 | 0.20 | 0.05 |
| R1 | 0.65 | 0.15 | 0.15 | 0.05 |
| R2 | 0.78 | 0.08 | 0.08 | 0.06 |
| R3 | 0.90 | 0.02 | 0.02 | 0.06 |

This prevents cost/latency from dominating critical work while still making small tasks economical.

## OpenRouter score derivation

The sync uses runtime-discovered candidates only.

### Reasoning

`Artificial Analysis intelligence_index` is converted to a 1–5 percentile score over the benchmark population:

```text
percentile = (count(lower) + 0.5*count(equal)) / N
score = 1 + 4*percentile
```

### Coding

Same percentile conversion using `coding_index`.

### Tool use

```text
tool_use = 0.70 * agentic_percentile_score
         + 0.30 * (5 if model exposes tools else 1)
```

If there is no Agentic Index, tool support alone receives a conservative score of 3; no tool support remains 0.

### Reliability

OpenRouter endpoint uptime is mapped as:

```text
reliability = clamp(1 + 4*((uptime_percent - 95)/5), 1, 5)
```

No uptime evidence -> 0.

### Cost

Average prompt/completion token price is ranked among the current host candidates. Cheapest receives 5 and most expensive receives 1. A single candidate receives neutral 3.

### Latency

Median endpoint p50 latency is ranked inversely among current host candidates. Fastest receives 5 and slowest receives 1. Missing latency -> 0.

The raw values and provenance are retained in each generated inventory. The derived 0–5 score is never the only stored evidence.

## Reasoning effort

Model choice and reasoning effort are separate decisions.

Pressure:

```text
pressure =
    0.45 * reasoning_target
  + 0.25 * reliability_target
  + 0.15 * tool_use_target
  + 0.15 * coding_target
  + role_bias
  + risk_bias
```

Thresholds:

```text
pressure < 2.50 -> low
pressure < 3.50 -> medium
pressure < 4.35 -> high
pressure < 4.80 -> xhigh
otherwise       -> max
```

Risk caps:

- R0 <= medium
- R1 <= high
- R2 <= xhigh
- R3 <= max

If the chosen model does not support the desired effort, the router uses the nearest lower supported effort, then the nearest higher effort.

### OpenCode

For models known to support variants, the selected runtime ID becomes:

```text
provider/model#high
```

The existing OpenCode harness plugin already parses `#variant` and applies it to the agent model.

### Codex

Codex activation injects into the generated custom-agent TOML:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
```

Clearing activation removes these lines and restores inheritance.

## Independent review / verification

A reviewer should not merely repeat the implementer's model when a comparable alternative exists.

Rules are applied in this order:

1. Different exact model if alternative score >= 90% of the unrestricted best.
2. Different model family if alternative score >= 92%.
3. Different vendor if alternative score >= 94%.

Roles:

- `test-auditor` avoids `implementer`
- `reviewer` avoids `implementer`
- `security-reviewer` avoids `implementer`
- `verifier` avoids `implementer` and `reviewer`

If no comparable independent alternative exists, the best model is still used and the selection records `same_model_fallback` rather than claiming strong independence.

## Local learning

Record an outcome after a task or evaluation:

```bash
python3 scripts/model_feedback.py \
  --provider codex \
  --model gpt-5.6-sol \
  --passed \
  --quality 4.7
```

The history lives only under `.harness/model-history/` and is already excluded by `.gitignore`.

The local score is:

```text
smoothed_success = (passed + 0.5) / (samples + 1)
local_score = 5 * (0.60*smoothed_success + 0.40*(average_quality/5))
```

Sample confidence is applied by the main router, so a model cannot become a permanent champion from one lucky task.

## OpenCode usage

Open OpenCode once so the existing plugin exports its catalog, then:

```bash
export OPENROUTER_API_KEY=...
python3 scripts/openrouter_sync.py --provider opencode
python3 scripts/providers/opencode_activate_task.py tasks/MY-TASK.json
```

Activation never contacts OpenRouter. It reads `.harness/model-inventories/opencode.json`. Regenerate that file only when you explicitly run `openrouter_sync.py`.

Clear:

```bash
python3 scripts/providers/opencode_activate_task.py --clear
```

## Codex usage

Start Codex once so its account-visible model cache exists, then:

```bash
export OPENROUTER_API_KEY=...
python3 scripts/providers/codex_inventory.py
python3 scripts/providers/codex_activate_task.py tasks/MY-TASK.json
```

The activation regenerates `.codex/agents/*.toml` with per-agent model/effort bindings.

Clear and restore normal inheritance:

```bash
python3 scripts/providers/codex_activate_task.py --clear
```

If needed, restrict routing to an explicit account-tested allowlist:

```bash
export HARNESS_CODEX_MODELS="gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna"
```

## Failure behavior

- No runtime inventory: R0–R2 inherit the active session model; R3 can block according to policy.
- No eligible model: R0–R2 inherit; R3 blocks.
- Inventory age is not enforced in manual-refresh mode. The last explicitly generated scored inventory remains authoritative until you regenerate it.
- Missing scored inventory: R0–R2 inherit the active model; R3 follows the configured no-inventory policy. Run `openrouter_sync.py` explicitly to create/update scores.
- OpenRouter availability matters only during the explicit sync command, never during task activation.
- Provider-specific effort unknown: select the model but leave effort unset/inherited.

# Harness Benchmark — guía de instalación y uso

## Qué instala

- `scripts/harness_benchmark.py`
- formato `suite.json` + `case.json`
- soporte de hidden tests externos
- A/B `baseline` vs `full`
- ablations `no_tdd`, `no_impact`, `eager_agents`, `no_reviewer`, `no_code_quality`
- repetición N veces
- métricas agregadas
- `report.json` + `report.html`
- conexión automática con `scripts/eval_recorder.py`

Las ablations se aplican **solo en workspaces temporales**. No cambian el comportamiento normal del harness.

## Instalación

Instalalo **después de todos los overlays anteriores** (model-routing, discovery, code-quality, Adaptive TDD y Change Impact/Progressive Budget). Este benchmark debe ser la última capa.

```bash
python3 _package/apply.py /ruta/a/tu/harness
```

Después:

```bash
cd /ruta/a/tu/harness
python3 scripts/check_harness.py
python3 -m unittest discover -s tests -p "test_*.py"
```

## Probar el formato

```bash
python3 scripts/harness_benchmark.py validate benchmarks/examples/suite.json
```

## A/B inicial

```bash
python3 scripts/harness_benchmark.py run \
  benchmarks/examples/suite.json \
  --provider opencode \
  --variants baseline,full \
  --repetitions 3
```

OpenCode usa actualmente su modo no interactivo `opencode run`; el runner selecciona `harness-orchestrator` para `full` y un agente mínimo generado en el workspace para `baseline`.

Codex usa `codex exec --json -C <workspace> --sandbox workspace-write --ephemeral`.

## Caso real

Estructura pública:

```text
benchmarks/cases/webhook-idempotency/
├── case.json
└── repo/
```

`repo/` debe ser un snapshot anterior al fix.

Ejemplo:

```json
{
  "schema_version": 1,
  "id": "BENCH-WEBHOOK-IDEMPOTENCY",
  "tags": ["bug", "persistence", "idempotency"],
  "task": {
    "description": "Make duplicate webhook delivery idempotent.",
    "acceptance_criteria": [
      "First delivery is processed",
      "Duplicate delivery does not create a second transaction"
    ],
    "files": ["src/webhooks.py"],
    "risk": "R2"
  },
  "workspace": {
    "source": "repo",
    "harness_overlay": true,
    "setup_commands": []
  },
  "verification": {
    "public_checks": [
      {
        "name": "public tests",
        "kind": "regression",
        "argv": ["python3", "-m", "pytest", "-q"]
      }
    ],
    "private_oracle": "oracle.json"
  }
}
```

Si el snapshot ya contiene el harness instalado, usá:

```json
"harness_overlay": false
```

## Hidden tests reales

Guardalos fuera del repo:

```text
~/harness-benchmark-private/
└── BENCH-WEBHOOK-IDEMPOTENCY/
    ├── oracle.json
    └── test_hidden_idempotency.py
```

`oracle.json`:

```json
{
  "schema_version": 1,
  "files": [
    {
      "source": "test_hidden_idempotency.py",
      "destination": "tests/test_hidden_idempotency.py"
    }
  ],
  "checks": [
    {
      "name": "private acceptance",
      "kind": "acceptance",
      "argv": ["python3", "-m", "pytest", "-q", "tests/test_hidden_idempotency.py"]
    }
  ],
  "impacted_files": [
    "src/webhooks.py",
    "src/transactions.py"
  ]
}
```

Los hidden files se copian al workspace **después** de que el agente termina. Cuando usás `--private-root` en POSIX, el runner además quita temporalmente permisos al directorio privado del caso durante la ejecución del agente y los restaura antes de verificar. Esto es defensa en profundidad; mantené igualmente los holdouts fuera del proyecto.

## Suite

```json
{
  "schema_version": 1,
  "name": "my-private-suite",
  "cases": [
    "cases/webhook-idempotency/case.json",
    "cases/cpa-zero/case.json"
  ]
}
```

## A/B serio

```bash
python3 scripts/harness_benchmark.py run \
  benchmarks/suite.json \
  --provider opencode \
  --variants baseline,full \
  --repetitions 5 \
  --private-root ~/harness-benchmark-private
```

Opcionalmente fijá el modelo primario:

```bash
--model provider/model
```

Esto mide el sistema completo. Si el harness usa subagent model routing, `full` puede seguir eligiendo modelos distintos para roles especializados.

## Ablations

```bash
python3 scripts/harness_benchmark.py run \
  benchmarks/suite.json \
  --provider opencode \
  --variants full,no_tdd,no_impact,eager_agents,no_reviewer,no_code_quality \
  --repetitions 5 \
  --private-root ~/harness-benchmark-private
```

Interpretación:

- `no_tdd`: TDD deshabilitado en la copia temporal.
- `no_impact`: impact gate y bonus de vecinos del grafo deshabilitados.
- `eager_agents`: quita el beneficio del Progressive Agent Budget activando soporte routed desde el inicio.
- `no_reviewer`: remueve reviewer del route temporal.
- `no_code_quality`: remueve la skill de implementer/reviewer en el workspace temporal.

## Métricas

Cuando hay datos disponibles:

- `task_success_rate`
- `first_pass_success_rate`
- `hidden_test_pass_rate`
- `bug_introduction_rate`
- `mutation_score`
- `impact_recall`
- `tdd_valid_red_rate`
- `human_intervention_rate`
- `tokens`
- `cost_usd`
- `latency_seconds`
- `agents_used_mean`

Una task pasa si executor + checks públicos + checks privados pasan.

Un check `kind: regression` que falla cuenta como bug introducido.

`impact_recall` compara `impact.json` contra `oracle.impacted_files`.

`tdd_valid_red_rate` inspecciona `tdd-evidence.jsonl`.

## Mutation score

Cualquier check puede extraer una métrica:

```json
{
  "name": "mutation",
  "kind": "mutation",
  "argv": ["python3", "scripts/run_mutation.py"],
  "metric": {
    "name": "mutation_score",
    "regex": "mutation_score=([0-9.]+)",
    "scale": 0.01
  }
}
```

Si imprime `mutation_score=82`, se registra `0.82`.

## Reportes

Cada run genera:

```text
.harness/benchmarks/runs/<run-id>/
├── report.json
├── report.html
└── runs/
```

Regenerar HTML:

```bash
python3 scripts/harness_benchmark.py report \
  .harness/benchmarks/runs/<run-id>/report.json
```

## Conectar con eval_recorder.py

```bash
python3 scripts/harness_benchmark.py run \
  benchmarks/suite.json \
  --provider opencode \
  --variants baseline,full \
  --repetitions 5 \
  --private-root ~/harness-benchmark-private \
  --record-eval \
  --record-variant full
```

El runner genera un JSON numérico y llama automáticamente a:

```bash
python3 scripts/eval_recorder.py --label benchmark-<suite>-full --metrics <metrics.json>
```

Después:

```bash
python3 scripts/evolution_engine.py
```

## Recomendación de tamaño

Primera suite seria:

- 5 bugs históricos
- 3 features
- 2 refactors
- 2 external API
- 2 persistence/concurrency
- 1 auth/security

≈ 15 casos.

Primero `3` repeticiones. Cuando esté estable, 30–50 casos × 3–5 repeticiones.

## Regla de interpretación

No preguntes solo “¿full gana?”.

Ejemplo:

```text
FULL       success 84%   bugs 4%   tokens 125k
NO_TDD     success 78%   bugs 9%   tokens 106k
```

TDD probablemente justifica su coste.

```text
FULL          success 84%   tokens 125k
EAGER_AGENTS  success 84%   tokens 165k
```

Progressive Agent Budget está aportando eficiencia aunque no cambie la calidad.

## Nota sobre tokens/coste

El runner parsea JSON/JSONL del proveedor de forma conservadora y marca la procedencia como `provider-json-best-effort`. Para decisiones financieras finas, contrastá con el billing real del proveedor.

## Comando recomendado

```bash
python3 scripts/harness_benchmark.py run \
  benchmarks/suite.json \
  --provider opencode \
  --variants baseline,full,no_tdd,no_impact,eager_agents,no_reviewer,no_code_quality \
  --repetitions 5 \
  --private-root ~/harness-benchmark-private \
  --record-eval \
  --record-variant full
```

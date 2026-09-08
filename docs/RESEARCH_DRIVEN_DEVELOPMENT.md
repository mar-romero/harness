# Research-Driven Development overlay

This overlay adds an adaptive research layer **before executable harness tasks**. It does not replace the existing product-discovery, risk, TDD, review, verification, evidence, or publication controls.

## Modes

| Mode | Use |
|---|---|
| `none` | Bounded, well-understood task. |
| `light` | Ordinary feature discovery; use the existing discovery dossier. |
| `research` | External evidence or a technical/domain unknown materially affects the decision. |
| `full` | New project/epic, ambiguous domain, conflicting stakeholders, or expensive-to-reverse architecture. |

`harness/research-policy.json` is the switch and routing policy. `python3 scripts/research_discovery.py assess <discovery.json>` is the deterministic assessment surface.

## Full flow

`Research → Discover → Model → Decide → Architect → Scenarios → approved product discovery → tasks → normal harness`

Research-RDD artifacts are planning evidence, not implementation authority.

## Artifacts

- `planning/research/<id>.json`: sources, findings, contrary evidence, assumptions and open questions.
- `planning/domain/<id>.json`: vocabulary, relationships, invariants and ambiguity.
- `planning/decisions/<id>.json`: alternatives, rationale, evidence, consequences and reversibility.
- `planning/scenarios/<id>.json`: observable behavior scenarios that bridge into acceptance tests/BDD/TDD.

Start scaffolding with:

```bash
python3 scripts/research_discovery.py init planning/discovery/<id>.json
```

After agents/humans complete the artifacts:

```bash
python3 scripts/research_discovery.py status planning/discovery/<id>.json
```

Only after Research-RDD is ready and the existing discovery bundle is explicitly approved should `product_planning.py materialize` create executable tasks.

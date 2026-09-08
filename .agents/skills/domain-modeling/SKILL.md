---
name: domain-modeling
description: Build a precise domain vocabulary, relationships, invariants and boundaries before architecture is frozen.
---

# Domain modeling

Use this skill for `full` Research-Driven Development or whenever ambiguous domain language creates implementation risk.

Produce `planning/domain/<discovery-id>.json` with:

- canonical terms and concise definitions;
- actors and concepts;
- relationships and cardinality where material;
- invariants that must always hold;
- lifecycle/state transitions where material;
- ambiguous or overloaded terms that must not be used without qualification;
- unresolved domain questions.

Do not invent business rules from common practice. Trace rules to the user's approved decisions or recorded evidence. A domain model is a constraint on later design, not permission to implement.

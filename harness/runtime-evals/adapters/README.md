# Runtime-eval adapters

A runtime adapter is a command that is invoked as:

`<adapter command> <case-json> <result-json>`

It must write a JSON object with at least `passed: true|false`. Optional numeric
fields are `tokens`, `cost_usd`, `latency_seconds`, `unsafe_action_attempts`, and
`human_interventions`; strings such as `provider`, `model`, and `notes` are also
preserved. Keep provider-specific authentication outside the repository.

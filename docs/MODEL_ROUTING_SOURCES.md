# External sources used by this design

Checked 2026-09-04.

- OpenRouter model catalog API: https://openrouter.ai/docs/api/api-reference/models/get-models
- OpenRouter benchmark API: https://openrouter.ai/docs/api/api-reference/benchmarks/get-benchmarks
- OpenRouter model endpoints API: https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints
- OpenAI GPT-5.6 Sol model documentation: https://developers.openai.com/api/docs/models/gpt-5.6-sol
- OpenAI GPT-5.6 Luna model documentation: https://developers.openai.com/api/docs/models/gpt-5.6-luna
- Codex source/tests documenting CODEX_HOME/models_cache.json: https://github.com/openai/codex/blob/main/codex-rs/app-server/tests/common/models_cache.rs
- Codex custom subagents support `model` and `model_reasoning_effort`: https://learn.chatgpt.com/es-419/docs/agent-configuration/subagents

Design rule: runtime availability remains authoritative even when OpenRouter has richer model metadata.

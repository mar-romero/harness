# Subscription CLI Bridge

## Objetivo

Este módulo permite que el harness trate los **CLIs oficiales autenticados con la suscripción/cuenta del usuario** como runtimes intercambiables, sin convertir cookies o credenciales privadas en una API propia.

Runtimes soportados:

- OpenAI Codex CLI
- Anthropic Claude Code
- GitHub Copilot CLI
- Cursor CLI (`cursor-agent`, con fallback a `agent`)
- xAI Grok Build
- Google Gemini CLI

La arquitectura es:

```text
Task + risk + role
        |
        v
  model_router.py
        |
        v
provider/model selection
        |
        v
subscription_bridge.py
        |
   +----+-----+--------+--------+------+------+
   |          |        |        |      |      |
 Codex      Claude   Copilot  Cursor  Grok  Gemini
   |          |        |        |      |      |
official browser/OAuth/subscription login owned by each CLI
```

## Principios de seguridad

1. **No se leen** cookies del navegador, keychains, archivos OAuth ni credential stores de los proveedores.
2. Por defecto se eliminan del proceso hijo las variables de API directa del proveedor (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CURSOR_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`, etc.). Esto fuerza al CLI a depender de su login oficial ya configurado o a fallar.
3. Roles `read-only` usan el modo de permisos más restrictivo disponible y se compara una huella del working tree Git antes/después. Si el CLI modifica el repo, el run termina con `exit_code=73`.
4. El único rol writer canónico sigue siendo `implementer`. Si existe `.worktrees/<task>`, el bridge lo usa automáticamente. El writer se niega a ejecutarse sobre el checkout principal salvo override explícito.
5. `--allow-shell` es opt-in para los runners en los que el shell amplía significativamente la superficie de escritura.
6. `--allow-api-credentials` existe solo como escape hatch explícito; no lo uses si tu objetivo es consumir la cuota incluida de las suscripciones.

## Instalación del parche

Desde la carpeta descomprimida del parche:

```bash
python install.py /ruta/al/harness
```

Para ejecutar además el diagnóstico de los CLIs:

```bash
python install.py /ruta/al/harness --doctor
```

El instalador:

- verifica que el destino sea un harness compatible;
- comprueba SHA-256 antes de reemplazar archivos existentes;
- guarda backup de los archivos modificados;
- copia los archivos del bridge;
- regenera/verifica adapters canónicos;
- ejecuta las evaluaciones determinísticas;
- ejecuta la suite de unit tests.

## Instalar los CLIs oficiales

El bridge **no instala silenciosamente herramientas de terceros**. Instalá solamente los runtimes cuyas suscripciones quieras usar.

Ejemplos de instalación actuales (confirmá siempre la documentación oficial si cambian):

```bash
# OpenAI Codex
npm install -g @openai/codex

# Anthropic Claude Code
npm install -g @anthropic-ai/claude-code

# GitHub Copilot CLI
npm install -g @github/copilot

# Google Gemini CLI
npm install -g @google/gemini-cli

# Cursor (macOS/Linux)
curl https://cursor.com/install -fsS | bash

# Grok Build (macOS/Linux/WSL)
curl -fsSL https://x.ai/cli/install.sh | bash
```

En Windows, usá los instaladores oficiales correspondientes cuando el comando anterior no aplique.

## Login

Podés autenticar cada CLI directamente o usar el bridge:

```bash
python scripts/subscription_bridge.py login codex
python scripts/subscription_bridge.py login claude
python scripts/subscription_bridge.py login copilot
python scripts/subscription_bridge.py login cursor
python scripts/subscription_bridge.py login grok
python scripts/subscription_bridge.py login gemini
```

También:

```bash
python scripts/subscription_bridge.py login all
```

Notas:

- Codex debe quedar autenticado con **ChatGPT**, no con API key, si querés usar la cuota del plan de ChatGPT.
- Claude Code debe autenticarse con la cuenta **Claude.ai Pro/Max**. El bridge remueve `ANTHROPIC_API_KEY` al lanzar Claude para evitar que una variable de entorno cambie el billing a API PAYG.
- Copilot usa su flujo OAuth oficial (`copilot login`).
- Cursor usa browser login (`cursor-agent login`).
- Grok usa `grok login` / browser auth.
- Gemini se inicia con `gemini`; usá `/auth` y "Sign in with Google" si es necesario.

## Diagnóstico

```bash
python scripts/subscription_bridge.py doctor
```

Ejemplo conceptual:

```json
{
  "providers": [
    {"provider":"codex","installed":true,"auth_state":"subscription"},
    {"provider":"claude","installed":true,"auth_state":"subscription"},
    {"provider":"cursor","installed":true,"auth_state":"subscription"}
  ]
}
```

`doctor` **no muestra ni lee secretos**. Para algunos CLIs no existe un status no-interactivo estable; esos aparecen como `unknown` y el primer run es la verificación definitiva.

## Inventario de modelos

Generar el pool común:

```bash
python scripts/subscription_bridge.py inventory
```

O limitar proveedores:

```bash
python scripts/subscription_bridge.py inventory --providers codex,claude,copilot
```

El resultado se guarda en:

```text
.harness/model-inventories/subscriptions.json
```

Los IDs quedan namespaced:

```text
codex/gpt-...
claude/sonnet
copilot/gpt-5.4
cursor/auto
grok/grok-...
gemini/auto
```

El score `cost` de este inventario **no representa precio API**. Representa eficiencia relativa de cuota de suscripción para que el router pueda preferir modelos rápidos/baratos-de-cuota cuando la tarea no necesita un modelo premium.

### Disponibilidad real

- Codex: el bridge intenta descubrir el catálogo local que ya conoce Codex.
- Grok: usa `grok models` cuando está disponible.
- Claude: usa aliases estables `sonnet`, `opus`, `haiku`; el plan puede limitar alguno.
- Copilot: incluye el catálogo documentado para Copilot CLI; la disponibilidad exacta depende del plan.
- Cursor/Gemini: `auto` es el default seguro porque sus catálogos cambian frecuentemente.

Siempre podés forzar un modelo en un run aunque no esté en el inventario estático:

```bash
python scripts/subscription_bridge.py run TASK-1 reviewer --provider copilot --model gpt-5.4
```

## Activar una tarea con routing cross-provider

```bash
python scripts/subscription_bridge.py activate tasks/TASK-1.json
```

Para crear además el worktree aislado del writer:

```bash
python scripts/subscription_bridge.py activate tasks/TASK-1.json --create-worktree
```

El flujo reutiliza las mismas piezas del harness:

```text
request normalization
  -> task_router
  -> context compiler
  -> impact plan
  -> progressive agent budget
  -> subscription inventory
  -> model_router
  -> per-role provider/model selection
```

Los resultados se guardan en:

```text
.harness/runs/TASK-1/model-selections.json
```

Por ejemplo, el router podría terminar con:

```text
explorer     -> cursor/auto
planner      -> claude/opus
implementer  -> codex/gpt-...
reviewer     -> copilot/gpt-5.4
verifier     -> grok/grok-...
```

La independencia de reviewer/verifier del `model_router` existente sigue funcionando porque cada modelo conserva su `vendor` real.

## Ejecutar un rol

Usar lo seleccionado automáticamente:

```bash
python scripts/subscription_bridge.py run TASK-1 explorer
python scripts/subscription_bridge.py run TASK-1 planner
python scripts/subscription_bridge.py run TASK-1 implementer
python scripts/subscription_bridge.py run TASK-1 reviewer
```

Cada run queda registrado en:

```text
.harness/runs/TASK-1/subscription-runs/<timestamp>-<role>.json
```

Incluye:

- proveedor y modelo;
- reasoning effort solicitado;
- salida final normalizada;
- usage cuando el CLI lo expone;
- variables de API directa que fueron removidas (solo nombres, nunca valores);
- fingerprint de integridad para roles read-only;
- selección original del router.

## Forzar proveedor/modelo

Esto permite alternar manualmente para comparar:

```bash
python scripts/subscription_bridge.py run TASK-1 reviewer \
  --provider claude --model sonnet

python scripts/subscription_bridge.py run TASK-1 reviewer \
  --provider copilot --model gpt-5.4 --effort high

python scripts/subscription_bridge.py run TASK-1 explorer \
  --provider cursor --model auto
```

## Shell

Por defecto el writer de Claude/Copilot no recibe shell irrestricto. Para una tarea en worktree donde necesitás que el agente ejecute tests:

```bash
python scripts/subscription_bridge.py run TASK-1 implementer --allow-shell
```

Esto aumenta la superficie de riesgo. Las reglas del harness siguen en el prompt, pero un CLI local con shell ejecuta con los permisos de tu usuario salvo el sandbox propio que ofrezca el proveedor.

## Cómo se conecta con CodeGraph/context efficiency

Si instalaste antes el parche de Context Efficiency/CodeGraph, ambos son complementarios:

```text
CodeGraph/repo map/symbol snippets
          |
          v
 compact context pack
          |
          v
 subscription bridge
          |
          v
 selected provider/model
```

El bridge manda al CLI el `repo_map`, CodeGraph output y snippets ya compactados cuando existen, en vez de obligar a cada proveedor a releer el repositorio desde cero.

## Qué NO hace

- No evade rate limits ni cuotas de las suscripciones.
- No garantiza que un modelo esté incluido en tu plan: cada proveedor decide disponibilidad y límites.
- No usa una suscripción web como si fuera una API HTTP genérica.
- No extrae cookies, OAuth tokens ni secrets de credential stores.
- No impide que un proveedor cambie flags/model aliases en futuras versiones del CLI; `doctor` y los tests locales ayudan a detectar drift.
- No combina las cuotas en un “saldo único”; el router simplemente decide qué CLI oficial usar en cada rol.

## Flujo recomendado

```bash
# 1. Ver estado
python scripts/subscription_bridge.py doctor

# 2. Login donde haga falta
python scripts/subscription_bridge.py login codex
python scripts/subscription_bridge.py login claude
python scripts/subscription_bridge.py login copilot
python scripts/subscription_bridge.py login cursor
python scripts/subscription_bridge.py login grok
python scripts/subscription_bridge.py login gemini

# 3. Ver pool normalizado
python scripts/subscription_bridge.py inventory

# 4. Activar tarea y crear worktree writer
python scripts/subscription_bridge.py activate tasks/TASK-1.json --create-worktree

# 5. Ejecutar roles con selección automática
python scripts/subscription_bridge.py run TASK-1 explorer
python scripts/subscription_bridge.py run TASK-1 planner
python scripts/subscription_bridge.py run TASK-1 implementer
python scripts/subscription_bridge.py run TASK-1 reviewer
```

## Archivos agregados

```text
harness/subscription-providers.json
harness/model-providers/subscriptions.json
harness/schema/subscription-inventory.schema.json
harness/schema/subscription-run.schema.json
scripts/subscription_runtime.py
scripts/subscription_bridge.py
tests/test_subscription_bridge.py
docs/SUBSCRIPTION_BRIDGE.md
```

El parche modifica además únicamente los README de `scripts/` y `harness/model-providers/` para documentar la nueva frontera.

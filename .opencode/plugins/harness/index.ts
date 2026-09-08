import { Plugin } from "@opencode-ai/plugin"
import { promises as fs } from "node:fs"
import path from "node:path"
import { spawn } from "node:child_process"

const REFRESH_MS = 5 * 60 * 1000

function nowIso() {
  return new Date().toISOString()
}

function unwrapList(value) {
  if (Array.isArray(value)) return value
  if (value && Array.isArray(value.data)) return value.data
  return []
}

function parseModelRef(value) {
  if (!value || typeof value !== "string") return undefined
  const slash = value.indexOf("/")
  if (slash <= 0) return undefined
  const providerID = value.slice(0, slash)
  const remainder = value.slice(slash + 1)
  const hash = remainder.indexOf("#")
  if (hash < 0) return { providerID, id: remainder }
  return { providerID, id: remainder.slice(0, hash), variant: remainder.slice(hash + 1) }
}

function inside(root, candidate) {
  const r = path.resolve(root)
  const c = path.resolve(candidate)
  return c === r || c.startsWith(r + path.sep)
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"))
  } catch {
    return fallback
  }
}

async function writeJsonAtomic(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true })
  const tmp = file + ".tmp"
  await fs.writeFile(tmp, JSON.stringify(value, null, 2) + "\n", "utf8")
  await fs.rename(tmp, file)
}

async function appendJsonLine(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true })
  await fs.appendFile(file, JSON.stringify(value) + "\n", "utf8")
}

function runPython(root, args) {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", args, {
      cwd: root,
      env: { ...process.env, HARNESS_MODEL_INVENTORY_OPENCODE: path.join(root, ".harness/opencode/model-inventory.json") },
      stdio: ["ignore", "pipe", "pipe"],
    })
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (chunk) => { stdout += chunk.toString() })
    child.stderr.on("data", (chunk) => { stderr += chunk.toString() })
    child.on("error", reject)
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }))
  })
}

function rawPrice(model) {
  const tiers = Array.isArray(model?.cost) ? model.cost : []
  const values = tiers.map((tier) => {
    const input = Number(tier?.input)
    const output = Number(tier?.output)
    if (!Number.isFinite(input) || !Number.isFinite(output)) return undefined
    return input + output
  }).filter((x) => Number.isFinite(x))
  return values.length ? Math.min(...values) : undefined
}

export default Plugin.define({
  id: "portable-harness.opencode",
  async setup(ctx) {
    const root = path.resolve(ctx.location?.project?.canonical || ctx.location?.project?.directory || ctx.location?.directory || process.cwd())
    const runtimeDir = path.join(root, ".harness/opencode")
    const inventoryFile = path.join(runtimeDir, "model-inventory.json")
    const catalogFile = path.join(runtimeDir, "catalog-snapshot.json")
    const activeFile = path.join(runtimeDir, "active-task.json")
    const sessionFile = path.join(runtimeDir, "session.json")
    const auditFile = path.join(runtimeDir, "permission-audit.jsonl")
    const overridesFile = path.join(root, "harness/opencode-model-overrides.json")

    let lastInventoryRefresh = 0
    let activeMtime = -1
    let activeRisk = "R1"
    let modelMap = new Map()

    async function refreshInventory(force = false) {
      const ts = Date.now()
      if (!force && ts - lastInventoryRefresh < REFRESH_MS) return
      const result = await ctx.catalog.model.list()
      const models = unwrapList(result).filter((m) => m && m.enabled !== false)
      const overrides = await readJson(overridesFile, { profiles: {} })
      const profiles = overrides?.profiles || {}

      const prices = models.map(rawPrice).filter((x) => Number.isFinite(x))
      const low = prices.length ? Math.min(...prices) : undefined
      const high = prices.length ? Math.max(...prices) : undefined
      const costScore = (price) => {
        if (!Number.isFinite(price)) return 0
        if (low === high) return 3
        return Math.round((5 - 4 * ((price - low) / (high - low))) * 1000) / 1000
      }

      const normalized = []
      for (const model of models) {
        const baseID = `${model.providerID}/${model.id}`
        const profile = profiles[baseID] || {}
        const variant = typeof profile.variant === "string" && profile.variant ? `#${profile.variant}` : ""
        const id = baseID + variant
        const supportsTools = model?.capabilities?.tools === true
        const supportsReasoning = Boolean(model?.compatibility?.reasoningField)
        normalized.push({
          id,
          enabled: profile.enabled !== false && model.enabled !== false,
          native: true,
          capabilities: {
            reasoning: Number(profile.reasoning ?? (supportsReasoning ? 2 : 0)),
            coding: Number(profile.coding ?? 0),
            tool_use: Number(profile.tool_use ?? (supportsTools ? 4 : 0)),
            reliability: Number(profile.reliability ?? (model.status === "active" ? 2 : 1)),
          },
          cost: Number(profile.cost ?? costScore(rawPrice(model))),
          latency: Number(profile.latency ?? 0),
          context_window: Number(model?.limit?.context ?? 0),
          notes: profile.notes || "OpenCode catalog metadata; quality scores above conservative discovery defaults require reviewed overrides.",
        })
      }

      await writeJsonAtomic(catalogFile, { generated_at: nowIso(), models })
      await writeJsonAtomic(inventoryFile, {
        schema_version: 1,
        provider: "opencode",
        generated_at: nowIso(),
        source: "OpenCode V2 runtime catalog via local harness plugin + reviewed harness/opencode-model-overrides.json",
        models: normalized,
      })
      lastInventoryRefresh = ts
    }

    async function refreshActiveModels() {
      let stat
      try {
        stat = await fs.stat(activeFile)
      } catch {
        if (modelMap.size) {
          modelMap = new Map()
          activeRisk = "R1"
          activeMtime = -1
          await ctx.agent.reload()
        }
        return
      }
      if (stat.mtimeMs === activeMtime) return
      const active = await readJson(activeFile, {})
      activeRisk = typeof active?.risk === "string" ? active.risk : "R1"
      const next = new Map()
      for (const selection of active?.selections || []) {
        if (selection?.status !== "selected" || selection?.action !== "use") continue
        const ref = parseModelRef(selection.model_id)
        if (ref && selection.agent) next.set(selection.agent, ref)
      }
      modelMap = next
      activeMtime = stat.mtimeMs
      await ctx.agent.reload()
    }

    await refreshInventory(true)
    await refreshActiveModels()

    await ctx.agent.transform((editor) => {
      for (const [agentID, model] of modelMap.entries()) {
        if (!editor.get(agentID)) continue
        editor.update(agentID, (agent) => { agent.model = model })
      }
    })

    await ctx.session.hook("context", async (event) => {
      await refreshInventory(false)
      await refreshActiveModels()
      const active = await readJson(activeFile, {})
      if (active?.task_id) {
        event.system.push({
          text: `Harness runtime: task=${active.task_id}; risk=${active.risk}; route=${active.route_path}; context=${active.context_path}; evidence=.harness/runs/${active.task_id}/evidence.jsonl. Treat these durable artifacts as authoritative and respect the role boundaries of agent ${event.agent}.`,
        })
      }
    })

    await ctx.shell.hook("create.before", (event) => {
      if (!inside(root, event.cwd)) throw new Error("Harness: shell cwd outside project is not allowed")
      event.timeout = Math.min(Number(event.timeout || 300000), 300000)
      event.env.HARNESS_MODEL_INVENTORY_OPENCODE = inventoryFile
    })

    await ctx.permission.hook("evaluate", async (event) => {
      // RECEIPT_RDD_SESSION_V1: capture provider session identity before the shell/edit runs.
      if (event.sessionID) {
        await writeJsonAtomic(sessionFile, { schema_version: 1, session_id: String(event.sessionID), observed_at: nowIso() })
      }
      await refreshActiveModels()
      let decision
      if (event.action === "shell") {
        for (const command of event.resources || []) {
          const result = await runPython(root, ["scripts/gate.py", "command", String(command), "--risk", activeRisk])
          if (result.code !== 0) {
            try { decision = JSON.parse(result.stdout) } catch { decision = { allow: false, human_gate: false, reason: result.stderr || "shell gate failed closed" } }
            break
          }
        }
      } else if (event.action === "edit") {
        for (const resource of event.resources || []) {
          const result = await runPython(root, ["scripts/gate.py", "path", String(resource)])
          if (result.code !== 0) {
            try { decision = JSON.parse(result.stdout) } catch { decision = { allow: false, human_gate: false, reason: result.stderr || "path gate failed closed" } }
            break
          }
        }
      }

      if (decision && decision.allow === false) {
        event.effect = decision.human_gate ? "ask" : "deny"
        event.message = `Harness gate: ${decision.reason || "blocked"}`
      }
      await appendJsonLine(auditFile, {
        at: nowIso(),
        session_id: event.sessionID,
        agent: event.agent || null,
        risk: activeRisk,
        action: event.action,
        resources: event.resources,
        effect: event.effect,
        message: event.message || null,
      })
    })

    const timer = setInterval(() => {
      void refreshInventory(false).catch((error) => console.error("harness inventory refresh failed", error))
      void refreshActiveModels().catch((error) => console.error("harness active-model refresh failed", error))
    }, 60_000)

    return () => clearInterval(timer)
  },
})

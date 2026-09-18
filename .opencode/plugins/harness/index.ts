import { Plugin } from "@opencode-ai/plugin"
import { promises as fs } from "node:fs"
import path from "node:path"
import { createHash } from "node:crypto"
import { spawn, spawnSync } from "node:child_process"
import { assertNoReparse, insideReal } from "./path_guards.mjs"

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

function worktreeOverlay(root, provider) {
  // Match harnesslib.worktree_identity(): Git metadata, rather than cwd or a
  // host canonical path, defines the isolated provider namespace.
  const checkout = path.resolve(root)
  const top = spawnSync("git", ["rev-parse", "--show-toplevel"], { cwd: checkout, encoding: "utf8" })
  const common = spawnSync("git", ["rev-parse", "--git-common-dir"], { cwd: checkout, encoding: "utf8" })
  if (top.status !== 0 || common.status !== 0) throw new Error("Harness: worktree Git metadata unavailable")
  const worktreeRoot = path.resolve(String(top.stdout || "").trim())
  const commonRaw = String(common.stdout || "").trim()
  const commonDir = path.resolve(checkout, commonRaw)
  if (!commonRaw || worktreeRoot !== checkout) throw new Error("Harness: worktree root does not match Git checkout")
  const normal = (value) => process.platform === "win32" ? value.toLowerCase() : value
  const id = createHash("sha256").update(`${normal(worktreeRoot)}\0${normal(commonDir)}`, "utf8").digest("hex")
  return path.join(worktreeRoot, ".harness", "overlays", id, provider)
}

async function validateActive(active, provider, ownerId, root) {
  if (!active || typeof active !== "object") throw new Error("Harness: active binding is invalid")
  if (active.schema_version !== 3 || active.provider !== provider) throw new Error("Harness: active binding schema/provider mismatch")
  if (!active.overlay || active.overlay.schema_version !== 1 || active.overlay.worktree_id !== ownerId) {
    throw new Error("Harness: active binding belongs to another worktree")
  }
  if (typeof active.task_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$/.test(active.task_id)) {
    throw new Error("Harness: active binding task id is invalid")
  }
  const expected = {
    route_path: `.harness/runs/${active.task_id}/route.json`,
    context_path: `.harness/runs/${active.task_id}/context.json`,
    progress_path: `.harness/runs/${active.task_id}/progress.json`,
    task_snapshot_path: `.harness/runs/${active.task_id}/task.json`,
    impact_path: `.harness/runs/${active.task_id}/impact.json`,
    agent_budget_path: `.harness/runs/${active.task_id}/agent-budget.json`,
  }
  for (const key of Object.keys(expected)) {
    const value = active[key]
    if (typeof value !== "string" || !value || path.isAbsolute(value) || value.split(/[\\/]+/).includes("..")) {
      throw new Error(`Harness: active binding ${key} is invalid`)
    }
    if (value !== expected[key]) throw new Error(`Harness: active binding ${key} does not match task id`)
  }
  const expectedModel = path.join(".harness", "overlays", ownerId, provider, "model-selections.json")
  if (path.normalize(active.model_selections_path) !== path.normalize(expectedModel)) throw new Error("Harness: active model selections path is not local")
  const selectionFile = path.resolve(root, active.model_selections_path)
  await assertNoReparse(root, selectionFile)
  const selectionRaw = await fs.readFile(selectionFile, "utf8")
  const selections = JSON.parse(selectionRaw)
  if (selections.schema_version !== 2 || selections.provider !== provider || selections.task_id !== active.task_id) {
    throw new Error("Harness: model selections task/provider/schema mismatch")
  }
  if (active.model_selections_sha256 && createHash("sha256").update(selectionRaw, "utf8").digest("hex") !== active.model_selections_sha256) {
    throw new Error("Harness: active model selections integrity mismatch")
  }
  const inventoryFile = path.resolve(root, selections.inventory_path || "")
  const expectedInventory = path.resolve(root, ".harness", "overlays", ownerId, provider, "enriched-inventory.json")
  await assertNoReparse(root, inventoryFile)
  if (inventoryFile !== expectedInventory || selections.inventory_sha256 !== createHash("sha256").update(await fs.readFile(inventoryFile)).digest("hex")) {
    throw new Error("Harness: model selection inventory binding is invalid")
  }
  const inventory = JSON.parse(await fs.readFile(inventoryFile, "utf8"))
  const modelIds = new Set((inventory.models || []).map((model) => String(model.id)))
  for (const selection of selections.selections || []) {
    if (selection.action === "use" && selection.status === "selected" && !modelIds.has(String(selection.model_id || selection.base_model_id))) {
      throw new Error("Harness: selected model is absent from the provider-local inventory")
    }
  }
  if (JSON.stringify(active.selections || []) !== JSON.stringify(selections.selections || [])) {
    throw new Error("Harness: active selections differ from the provider-local selection file")
  }
  if (active.task_path !== `tasks/${active.task_id}.json`) throw new Error("Harness: active task path does not match task id")
  await assertNoReparse(root, path.join(root, active.task_path))
  return active
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"))
  } catch {
    return fallback
  }
}

async function assertNoLegacyState(root, provider) {
  const common = spawnSync("git", ["rev-parse", "--git-common-dir"], { cwd: root, encoding: "utf8" })
  const commonRoot = common.status === 0 ? path.dirname(path.resolve(root, String(common.stdout || "").trim())) : root
  for (const base of [...new Set([path.resolve(root), commonRoot])]) {
    const legacy = path.join(base, ".harness", provider)
    for (const name of ["active-task.json", "session.json", "permission-audit.jsonl", "catalog-snapshot.json", "model-inventory.json", "enriched-inventory.json", "model-selections.json"]) {
      try {
        await fs.stat(path.join(legacy, name))
        throw new Error(`Harness: legacy unscoped ${provider} state detected (${name})`)
      } catch (error) {
        if (error?.code !== "ENOENT") throw error
      }
    }
  }
}

async function writeJsonAtomic(file, value, root) {
  if (root) await assertNoReparse(root, file)
  await fs.mkdir(path.dirname(file), { recursive: true })
  const tmp = file + ".tmp"
  if (root) await assertNoReparse(root, tmp)
  await fs.writeFile(tmp, JSON.stringify(value, null, 2) + "\n", "utf8")
  if (root) await assertNoReparse(root, tmp)
  if (root) await assertNoReparse(root, file)
  await fs.rename(tmp, file)
}

async function appendJsonLine(file, value, root) {
  if (root) await assertNoReparse(root, file)
  await fs.mkdir(path.dirname(file), { recursive: true })
  if (root) await assertNoReparse(root, file)
  await fs.appendFile(file, JSON.stringify(value) + "\n", "utf8")
}

function runPython(root, args, inventoryPath) {
  return new Promise((resolve, reject) => {
    const child = spawn("python", args, {
      cwd: root,
      env: { ...process.env, HARNESS_MODEL_INVENTORY_OPENCODE: inventoryPath },
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
    // `directory` identifies the checkout hosting this OpenCode session;
    // `canonical` can point at another linked worktree and must not win.
    const root = path.resolve(ctx.location?.project?.directory || ctx.location?.directory || ctx.location?.project?.canonical || process.cwd())
    if (await fs.realpath(root) !== root) throw new Error("Harness: project root must not be a symlink or junction")
    const runtimeDir = worktreeOverlay(root, "opencode")
    const ownerId = path.basename(path.dirname(runtimeDir))
    const inventoryFile = path.join(runtimeDir, "model-inventory.json")
    const enrichedInventoryFile = path.join(runtimeDir, "enriched-inventory.json")
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
      await assertNoLegacyState(root, "opencode")
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

      await writeJsonAtomic(catalogFile, { generated_at: nowIso(), models }, root)
      const inventoryPayload = {
        schema_version: 3,
        provider: "opencode",
        generated_at: nowIso(),
        source: "OpenCode V2 runtime catalog via local harness plugin + reviewed harness/opencode-model-overrides.json",
        models: normalized,
      }
      await writeJsonAtomic(inventoryFile, inventoryPayload, root)
      await writeJsonAtomic(enrichedInventoryFile, inventoryPayload, root)
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
      const active = await validateActive(await readJson(activeFile, {}), "opencode", ownerId, root)
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

    await assertNoLegacyState(root, "opencode")
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
      let active = null
      try {
        await fs.stat(activeFile)
        active = await validateActive(await readJson(activeFile, {}), "opencode", ownerId, root)
      } catch (error) {
        if (error?.code !== "ENOENT") throw error
      }
      if (active?.task_id) {
        event.system.push({
          text: `Harness runtime: task=${active.task_id}; risk=${active.risk}; route=${active.route_path}; context=${active.context_path}; evidence=.harness/runs/${active.task_id}/evidence.jsonl. Treat these durable artifacts as authoritative and respect the role boundaries of agent ${event.agent}.`,
        })
      }
    })

    await ctx.shell.hook("create.before", async (event) => {
      await assertNoReparse(root, event.cwd)
      if (!(await insideReal(root, event.cwd))) throw new Error("Harness: shell cwd outside project is not allowed")
      event.timeout = Math.min(Number(event.timeout || 300000), 300000)
      event.env.HARNESS_MODEL_INVENTORY_OPENCODE = enrichedInventoryFile
    })

    await ctx.permission.hook("evaluate", async (event) => {
      await assertNoLegacyState(root, "opencode")
      // RECEIPT_RDD_SESSION_V1: capture provider session identity before the shell/edit runs.
      if (event.sessionID) {
        await writeJsonAtomic(sessionFile, { schema_version: 1, session_id: String(event.sessionID), observed_at: nowIso() }, root)
      }
      await refreshActiveModels()
      let decision = null
      if (event.action === "shell") {
        decision = { allow: false, human_gate: false, reason: "no valid harness gate decision" }
        for (const command of event.resources || []) {
          const result = await runPython(root, ["scripts/gate.py", "command", String(command), "--risk", activeRisk], enrichedInventoryFile)
          if (result.code !== 0) {
            try { decision = JSON.parse(result.stdout) } catch { decision = { allow: false, human_gate: false, reason: result.stderr || "shell gate failed closed" } }
            break
          }
          try {
            const parsed = JSON.parse(result.stdout)
            if (typeof parsed.allow !== "boolean") throw new Error("invalid gate response")
            decision = parsed
          } catch { decision = { allow: false, human_gate: false, reason: "malformed shell gate response" }; break }
        }
      } else if (event.action === "edit") {
        decision = { allow: false, human_gate: false, reason: "no valid harness gate decision" }
        for (const resource of event.resources || []) {
          const result = await runPython(root, ["scripts/gate.py", "path", String(resource)], enrichedInventoryFile)
          if (result.code !== 0) {
            try { decision = JSON.parse(result.stdout) } catch { decision = { allow: false, human_gate: false, reason: result.stderr || "path gate failed closed" } }
            break
          }
          try {
            const parsed = JSON.parse(result.stdout)
            if (typeof parsed.allow !== "boolean") throw new Error("invalid gate response")
            decision = parsed
          } catch { decision = { allow: false, human_gate: false, reason: "malformed path gate response" }; break }
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
      }, root)
    })

    const timer = setInterval(() => {
      void refreshInventory(false).catch((error) => console.error("harness inventory refresh failed", error))
      void refreshActiveModels().catch((error) => console.error("harness active-model refresh failed", error))
    }, 60_000)

    return () => clearInterval(timer)
  },
})

#!/usr/bin/env node
// Windows-safe stdio transport for Harness ACI. Tool execution remains Python.
const { spawnSync } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");

const ROOT = path.resolve(__dirname, "..");
const SERVER_INFO = { name: "portable-harness-aci", version: "1.0.0" };

function python() {
  const user = process.env.USERPROFILE;
  const candidates = [
    process.env.HARNESS_ACI_PYTHON,
    user && path.join(user, ".local", "bin", "python.exe"),
    user && path.join(user, ".local", "bin", "python3.exe"),
    "python",
  ].filter(Boolean);
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8", windowsHide: true });
    if (!probe.error && probe.status === 0) return candidate;
  }
  throw new Error("no supported Python interpreter found for Harness ACI");
}

function worker(request) {
  const child = spawnSync(python(), ["-u", path.join(ROOT, "scripts", "aci_mcp_worker.py")], {
    cwd: ROOT, input: JSON.stringify(request), encoding: "utf8", windowsHide: true, maxBuffer: 1024 * 1024,
  });
  if (child.error || child.status !== 0) throw new Error((child.stderr || child.error?.message || "ACI worker failed").trim());
  return JSON.parse(child.stdout);
}

function response(id, result) { return { jsonrpc: "2.0", id, result }; }
function error(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }
function send(value) { process.stdout.write(JSON.stringify(value) + "\n"); }

function handle(message) {
  const method = message.method;
  const id = message.id;
  const params = message.params || {};
  if (method === "notifications/initialized") return;
  if (method === "initialize") {
    const protocolVersion = params.protocolVersion;
    if (typeof protocolVersion !== "string" || !protocolVersion) return send(error(id, -32602, "initialize requires protocolVersion"));
    return send(response(id, { protocolVersion, capabilities: { tools: { listChanged: false } }, serverInfo: SERVER_INFO,
      instructions: "Use Harness ACI tools before raw shell for matching repository inspection/check operations." }));
  }
  if (method === "ping") return send(response(id, {}));
  if (method === "tools/list") return send(response(id, worker({ operation: "tools/list" })));
  if (method === "tools/call") {
    const result = worker({ operation: "tools/call", name: params.name, arguments: params.arguments || {} });
    return send(result.error ? error(id, -32602, result.error) : response(id, result));
  }
  if (Object.prototype.hasOwnProperty.call(message, "id")) send(error(id, -32601, `Method not found: ${method}`));
}

readline.createInterface({ input: process.stdin, crlfDelay: Infinity }).on("line", (line) => {
  try { handle(JSON.parse(line)); }
  catch (err) { send(error(null, -32603, `Harness ACI bridge error: ${err.message}`)); }
});

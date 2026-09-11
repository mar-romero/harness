#!/usr/bin/env node
// Windows-safe stdio transport for Harness ACI. Tool execution remains Python.
const { spawn, spawnSync } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");

const ROOT = path.resolve(__dirname, "..");
const SERVER_INFO = { name: "portable-harness-aci", version: "1.0.0" };

function python() {
  const user = process.env.USERPROFILE || process.env.HOME;
  const bundled = user && (process.platform === "win32"
    ? path.join(user, ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe")
    : path.join(user, ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "bin", "python"));
  const candidates = [
    process.env.HARNESS_ACI_PYTHON,
    bundled,
    process.env.PYTHON,
    "python",
  ].filter(Boolean);
  for (const candidate of candidates) {
    const probe = spawnSync(candidate, ["--version"], { encoding: "utf8", windowsHide: true });
    if (!probe.error && probe.status === 0) return candidate;
  }
  throw new Error("no supported Python interpreter found for Harness ACI");
}

let workerProcess = null;
let workerBuffer = "";
let workerStderr = "";
const workerQueue = [];

function rejectWorkerQueue(error) {
  while (workerQueue.length) workerQueue.shift().reject(error);
}

function startWorker() {
  if (workerProcess) return;
  const child = spawn(python(), ["-u", path.join(ROOT, "scripts", "aci_mcp_worker.py")], {
    cwd: ROOT,
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  workerProcess = child;
  workerBuffer = "";
  workerStderr = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    workerBuffer += chunk;
    let newline;
    while ((newline = workerBuffer.indexOf("\n")) >= 0) {
      const line = workerBuffer.slice(0, newline).trim();
      workerBuffer = workerBuffer.slice(newline + 1);
      if (!line) continue;
      const request = workerQueue.shift();
      if (!request) continue;
      try {
        request.resolve(JSON.parse(line));
      } catch (error) {
        request.reject(new Error(`invalid ACI worker response: ${error.message}`));
      }
    }
  });
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { workerStderr += chunk; });
  const fail = (error) => {
    if (workerProcess !== child) return;
    workerProcess = null;
    rejectWorkerQueue(error);
  };
  child.once("error", fail);
  child.once("exit", (code, signal) => {
    const detail = workerStderr.trim();
    const suffix = detail ? `: ${detail}` : "";
    fail(new Error(`ACI worker exited (${code ?? "signal"} ${signal || ""})${suffix}`.trim()));
  });
}

function worker(request) {
  startWorker();
  return new Promise((resolve, reject) => {
    workerQueue.push({ resolve, reject });
    try {
      workerProcess.stdin.write(JSON.stringify(request) + "\n");
    } catch (error) {
      workerQueue.pop();
      reject(error);
    }
  });
}

function stopWorker() {
  if (!workerProcess) return;
  workerProcess.kill();
  workerProcess = null;
  rejectWorkerQueue(new Error("ACI worker stopped"));
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
  if (method === "tools/list") {
    return worker({ operation: "tools/list" })
      .then((result) => send(response(id, result)))
      .catch((err) => send(error(id, -32603, `Harness ACI bridge error: ${err.message}`)));
  }
  if (method === "tools/call") {
    return worker({ operation: "tools/call", name: params.name, arguments: params.arguments || {} })
      .then((result) => send(result.error ? error(id, -32602, result.error) : response(id, result)))
      .catch((err) => send(error(id, -32603, `Harness ACI bridge error: ${err.message}`)));
  }
  if (Object.prototype.hasOwnProperty.call(message, "id")) send(error(id, -32601, `Method not found: ${method}`));
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  try { Promise.resolve(handle(JSON.parse(line))).catch((err) => send(error(null, -32603, `Harness ACI bridge error: ${err.message}`))); }
  catch (err) { send(error(null, -32603, `Harness ACI bridge error: ${err.message}`)); }
});
input.on("close", stopWorker);

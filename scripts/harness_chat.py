#!/usr/bin/env python3
"""Neutral terminal chat for the multi-provider harness.

Run this application instead of opening a provider-specific coding agent. On the
first launch it can invoke the official provider login flows. Afterwards plain
terminal messages become harness tasks and the deterministic control plane picks
providers/models/agents and drives the workflow automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from harnesslib import run_dir, write_json_atomic  # noqa: E402
from subscription_bridge import ACTIVE_PATH, doctor, login  # noqa: E402
from autonomous_orchestrator import RunnerIO, run_to_completion  # noqa: E402

CHAT_DIR = ROOT / ".harness" / "chat"
SETUP_PATH = CHAT_DIR / "setup.json"
SESSION_PATH = ROOT / ".harness" / "subscriptions" / "session.json"
TASK_DIR = CHAT_DIR / "tasks"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_session() -> str:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    sid = None
    if SESSION_PATH.is_file():
        try:
            sid = json.loads(SESSION_PATH.read_text(encoding="utf-8")).get("session_id")
        except Exception:
            sid = None
    if not isinstance(sid, str) or not sid:
        sid = "neutral-" + uuid.uuid4().hex
        write_json_atomic(SESSION_PATH, {"schema_version": 1, "session_id": sid, "created_at": now_iso(), "surface": "neutral-terminal-chat"})
    os.environ["HARNESS_SESSION_ID"] = sid
    return sid


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [S/n] " if default else " [s/N] "
    try:
        raw = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in {"s", "si", "sí", "y", "yes"}


def _provider_rows() -> list[dict[str, Any]]:
    return list(doctor().get("providers") or [])


def print_provider_status() -> None:
    rows = _provider_rows()
    print("\nProveedores oficiales detectados:")
    for row in rows:
        state = row.get("auth_state")
        installed = "sí" if row.get("installed") else "no"
        print(f"  - {row.get('provider'):8s} instalado={installed:2s} auth={state}")
    print()


def auth_wizard(*, force: bool = False) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    attempted: set[str] = set()
    if SETUP_PATH.is_file() and not force:
        try:
            attempted = set(json.loads(SETUP_PATH.read_text(encoding="utf-8")).get("auth_attempted") or [])
        except Exception:
            attempted = set()
    rows = _provider_rows()
    installed = [row for row in rows if row.get("installed")]
    if not installed:
        print("No encontré CLIs oficiales instalados. Instalá al menos uno (Codex, Claude Code, Copilot, Cursor, Grok o Gemini) y volvé a abrir el harness.")
        return
    pending = []
    for row in installed:
        provider = str(row.get("provider"))
        state = str(row.get("auth_state") or "unknown")
        if state == "subscription" or state.startswith("authenticated") or state == "subscription-or-local-auth":
            continue
        if provider in attempted and not force:
            continue
        pending.append(provider)
    if not pending:
        return
    print("El harness puede abrir los flujos de autenticación oficiales. No lee cookies ni tokens privados.")
    if not ask_yes_no("¿Autenticar ahora los proveedores instalados que todavía no verificamos?", True):
        return
    for provider in pending:
        print(f"\n=== Login oficial: {provider} ===")
        code = login(provider)
        attempted.add(provider)
        if code != 0:
            print(f"{provider}: el login terminó con código {code}; podés reintentarlo luego con :auth.")
    write_json_atomic(SETUP_PATH, {"schema_version": 1, "auth_attempted": sorted(attempted), "updated_at": now_iso()})
    print_provider_status()


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:5]
    slug = "-".join(words) or "task"
    return slug[:24]


def create_task(text: str) -> Path:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    task_id = f"CHAT-{stamp}-{uuid.uuid4().hex[:4]}-{_slug(text)}"[:64].rstrip("-")
    task = {
        "id": task_id,
        "description": text.strip(),
        "files": [],
        "acceptance_criteria": [],
        "tags": ["neutral-chat"],
        "created_by": "harness-chat",
        "created_at": now_iso(),
    }
    path = TASK_DIR / f"{task_id}.json"
    write_json_atomic(path, task)
    return path


def active_task() -> tuple[str, Path] | None:
    if not ACTIVE_PATH.is_file():
        return None
    try:
        data = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    task_id = data.get("task_id")
    raw = data.get("task_path")
    if not isinstance(task_id, str) or not isinstance(raw, str):
        return None
    progress = run_dir(task_id) / "progress.json"
    if not progress.is_file():
        return None
    try:
        state = json.loads(progress.read_text(encoding="utf-8"))
    except Exception:
        return None
    if state.get("state") == "DONE":
        return None
    path = (ROOT / raw).resolve()
    if not path.is_file():
        return None
    return task_id, path


def show_active_status() -> None:
    active = active_task()
    if not active:
        print("No hay una tarea activa sin terminar.")
        return
    task_id, _ = active
    progress = json.loads((run_dir(task_id) / "progress.json").read_text(encoding="utf-8"))
    models = {}
    mp = run_dir(task_id) / "model-selections.json"
    if mp.is_file():
        try:
            models = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            models = {}
    print(json.dumps({
        "task_id": task_id,
        "state": progress.get("state"),
        "current_step": progress.get("current_step"),
        "risk": progress.get("risk"),
        "models": [
            {"agent": x.get("agent"), "model": x.get("base_model_id"), "effort": x.get("reasoning_effort")}
            for x in models.get("selections", []) if x.get("action") == "use"
        ],
    }, indent=2, ensure_ascii=False))


def run_task_path(path: Path) -> dict[str, Any]:
    io = RunnerIO(emit=print, ask_yes_no=ask_yes_no)
    print("\nEl control plane toma la tarea. Los proveedores quedan como workers intercambiables.\n")
    result = run_to_completion(path, io=io)
    if result.get("status") == "DONE":
        print(f"\n✓ {result.get('task_id')} terminada y cerrada por los gates del harness.\n")
    else:
        print(f"\n■ {result.get('task_id')} quedó {result.get('status')}: {result.get('reason') or 'ver progreso'}\n")
    return result


def maybe_resume() -> None:
    active = active_task()
    if not active:
        return
    task_id, path = active
    progress = json.loads((run_dir(task_id) / "progress.json").read_text(encoding="utf-8"))
    print(f"Encontré una tarea pendiente: {task_id} ({progress.get('state')} en {progress.get('current_step')}).")
    if ask_yes_no("¿Reanudarla ahora?", True):
        # Resume only WAITING/BLOCKED/STALLED states. RUNNING/READY can be driven directly.
        if progress.get("state") in {"WAITING", "BLOCKED", "STALLED"}:
            from orchestrator import resume
            resume(task_id, note="resumed from neutral terminal chat")
        run_task_path(path)


def help_text() -> str:
    return (
        "Escribí una tarea en lenguaje natural y presioná Enter.\n"
        "El harness crea la tarea, localiza archivos, selecciona agentes/modelos/proveedores y conduce los gates.\n\n"
        "Comandos opcionales del chat:\n"
        "  :auth       abrir/reintentar logins oficiales\n"
        "  :providers  ver CLIs/auth detectados\n"
        "  :status     ver la tarea activa\n"
        "  :help       mostrar esta ayuda\n"
        "  :quit       salir (el progreso queda persistido)\n"
    )


def chat_loop() -> int:
    ensure_session()
    print("\nHarness Neutral Chat")
    print("====================")
    print("Una terminal; el control plane elige Codex/Claude/Copilot/Cursor/Grok/Gemini por tarea y rol.\n")
    auth_wizard(force=False)
    maybe_resume()
    print(help_text())
    while True:
        try:
            raw = input("harness> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo. El estado de las tareas queda guardado.")
            return 0
        if not raw:
            continue
        if raw in {":quit", ":q", "exit", "quit"}:
            return 0
        if raw == ":help":
            print(help_text()); continue
        if raw == ":auth":
            auth_wizard(force=True); continue
        if raw == ":providers":
            print_provider_status(); continue
        if raw == ":status":
            show_active_status(); continue
        if raw.startswith(":"):
            print("Comando desconocido. Usá :help."); continue
        task_path = create_task(raw)
        try:
            run_task_path(task_path)
        except Exception as exc:
            print(f"\nError del control plane: {exc}")
            print("La tarea y el progreso quedaron persistidos; al reiniciar el chat se puede reanudar.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Neutral terminal chat for the multi-provider harness")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("chat")
    sub.add_parser("auth")
    sub.add_parser("providers")
    sub.add_parser("status")
    runp = sub.add_parser("run")
    runp.add_argument("text", nargs="+")
    args = ap.parse_args()
    ensure_session()
    if args.command in {None, "chat"}:
        return chat_loop()
    if args.command == "auth":
        auth_wizard(force=True); return 0
    if args.command == "providers":
        print_provider_status(); return 0
    if args.command == "status":
        show_active_status(); return 0
    if args.command == "run":
        result = run_task_path(create_task(" ".join(args.text)))
        return 0 if result.get("status") == "DONE" else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

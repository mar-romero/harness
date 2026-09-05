#!/usr/bin/env python3
"""Build provider runtime model inventories enriched with OpenRouter metadata.

Availability is discovered from the host (OpenCode catalog or Codex model cache).
OpenRouter is used only for external benchmark, pricing, latency, and uptime priors.
No API key is ever persisted.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harnesslib import ROOT, write_json_atomic

OPENROUTER = "https://openrouter.ai/api/v1"
PROVIDER_DIR = ROOT / "harness" / "model-providers"
HISTORY_DIR = ROOT / ".harness" / "model-history"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def load_provider_config(provider: str) -> dict[str, Any]:
    path = PROVIDER_DIR / f"{provider}.json"
    if not path.exists():
        raise ValueError(
            f"missing provider model config: {path.relative_to(ROOT)}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(
    url: str,
    api_key: str | None,
    timeout: int = 30,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "portable-agent-harness/model-routing-v2",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _list_payload(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        if isinstance(value.get("data"), list):
            return value["data"]

        if isinstance(value.get("models"), list):
            return value["models"]

    return []


def _runtime_efforts(model: dict[str, Any]) -> list[str]:
    keys = (
        "supported_efforts",
        "supported_reasoning_efforts",
        "supportedReasoningEfforts",
        "reasoning_efforts",
        "reasoningEfforts",
    )

    for key in keys:
        value = model.get(key)

        if isinstance(value, list):
            out = []

            for item in value:
                if isinstance(item, str):
                    out.append(item)

                elif isinstance(item, dict):
                    name = (
                        item.get("effort")
                        or item.get("name")
                        or item.get("value")
                    )
                    if name:
                        out.append(str(name))

            if out:
                return list(dict.fromkeys(out))

    return []


def _configured_efforts(
    model_id: str,
    cfg: dict[str, Any],
    runtime: list[str] | None = None,
) -> list[str]:
    if runtime:
        return runtime

    effort = cfg.get("effort", {})

    for rule in effort.get("model_rules", []):
        if re.search(str(rule.get("pattern", "^$")), model_id):
            return [str(x) for x in rule.get("supported", [])]

    return [
        str(x)
        for x in effort.get("unknown_model_supported", [])
    ]


def _family(openrouter_id: str | None) -> str:
    if not openrouter_id:
        return ""

    vendor, _, model = openrouter_id.partition("/")

    m = re.match(r"^(gpt-\d+(?:\.\d+)?)", model)
    if m:
        return f"{vendor}/{m.group(1)}"

    for suffix in (
        "-pro",
        "-mini",
        "-nano",
        "-sol",
        "-terra",
        "-luna",
        "-flash",
        "-fast",
    ):
        if model.endswith(suffix):
            return f"{vendor}/{model[:-len(suffix)]}"

    return openrouter_id

def _normalize_model_name(value: str) -> str:
    value = value.strip().lower()

    for suffix in (
        "-fin-free",
        "-contributor-free",
        "-free",
        ":free",
    ):
        if value.endswith(suffix):
            value = value[:-len(suffix)]

    return value


def _resolve_openrouter_id(
    native_id: str,
    aliases: dict[str, Any],
    model_idx: dict[str, dict[str, Any]],
) -> tuple[str | None, str]:
    # Explicit override always wins.
    if native_id in aliases:
        override = aliases[native_id]

        if override is None:
            return None, "explicit-unmatched"

        return str(override), "explicit-alias"

    native_model = native_id.split("/", 1)[-1]
    normalized_native = _normalize_model_name(native_model)

    matches = []

    for openrouter_id in model_idx:
        openrouter_model = openrouter_id.split("/", 1)[-1]
        normalized_openrouter = _normalize_model_name(openrouter_model)

        if normalized_openrouter == normalized_native:
            matches.append(openrouter_id)

    matches = list(dict.fromkeys(matches))

    if len(matches) == 1:
        return matches[0], "auto-exact-normalized"

    if len(matches) > 1:
        return None, "ambiguous"

    return None, "unmatched"

def discover_opencode(
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog = ROOT / cfg["runtime_catalog"]
    data = _load_json(catalog, {})

    catalog_generated_at = (
        data.get("generated_at")
        if isinstance(data, dict)
        else None
    )

    models = _list_payload(data)

    # Manual-sync fallback.
    #
    # If the OpenCode runtime catalog snapshot does not exist or is empty,
    # query the locally installed OpenCode executable. This remains a local
    # discovery operation; OpenRouter network access still only happens when
    # openrouter_sync.py itself is explicitly invoked.
    if not models:
        discovery = cfg.get("discovery", {})

        command = [
            str(x)
            for x in discovery.get(
                "command",
                ["opencode", "models"],
            )
        ]

        run = None

        if command:
            executable = shutil.which(command[0])

            if executable:
                resolved_command = [
                    executable,
                    *command[1:],
                ]

                try:
                    run = subprocess.run(
                        resolved_command,
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        shell=False,
                        check=False,
                    )
                except (
                    OSError,
                    subprocess.TimeoutExpired,
                ):
                    run = None

        if run is not None and run.returncode == 0:
            models = []

            for line in run.stdout.splitlines():
                full = line.strip()

                if not full or "/" not in full:
                    continue

                provider_id, native_model_id = full.split("/", 1)

                models.append(
                    {
                        "providerID": provider_id,
                        "id": native_model_id,
                        "enabled": True,
                    }
                )

    discovery = cfg.get("discovery", {})

    profile_name = str(
        discovery.get("access_profile", "free")
    )

    profiles = discovery.get("profiles", {})
    profile = profiles.get(profile_name, {})

    allow_prefixes = tuple(
        str(x)
        for x in profile.get("allow_prefixes", [])
    )

    deny_prefixes = tuple(
        str(x)
        for x in profile.get("deny_prefixes", [])
    )

    aliases = cfg.get("openrouter_aliases", {})

    out: list[dict[str, Any]] = []

    for model in models:
        if (
            not isinstance(model, dict)
            or model.get("enabled") is False
        ):
            continue

        provider_id = (
            model.get("providerID")
            or model.get("provider_id")
            or model.get("provider")
        )

        native_model_id = (
            model.get("id")
            or model.get("model")
        )

        if not provider_id or not native_model_id:
            continue

        native_id = f"{provider_id}/{native_model_id}"

        if (
            allow_prefixes
            and not native_id.startswith(allow_prefixes)
        ):
            continue

        if (
            deny_prefixes
            and native_id.startswith(deny_prefixes)
        ):
            continue

        params = model.get("params") or {}
        capabilities = model.get("capabilities") or {}

        out.append(
            {
                "id": native_id,
                "native_id": native_id,
                "provider_id": provider_id,
                "model_id": native_model_id,
                "openrouter_id": None,
                "openrouter_match": "pending",
                "native": True,
                "enabled": True,
                "vendor": provider_id,
                "supports_tools": bool(
                    capabilities.get("tools") is True
                    or "tools" in params
                ),
                "supports_reasoning": bool(
                    (
                        model.get("compatibility")
                        or {}
                    ).get("reasoningField")
                    or "reasoning" in params
                ),
                "supported_efforts": _configured_efforts(
                    native_id,
                    cfg,
                    _runtime_efforts(model),
                ),
                "availability_source": (
                    "opencode-runtime-command"
                ),
                "availability_generated_at": (
                    catalog_generated_at
                ),
            }
        )

    return out

def _codex_home(cfg: dict[str, Any]) -> Path:
    env_name = str(
        cfg.get("discovery", {}).get(
            "codex_home_env",
            "CODEX_HOME",
        )
    )

    value = os.getenv(env_name)

    return (
        Path(value).expanduser()
        if value
        else Path.home() / ".codex"
    )


def _codex_cache_candidates(
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    cache_name = str(
        cfg.get("discovery", {}).get(
            "cache_file",
            "models_cache.json",
        )
    )

    cache = _load_json(
        _codex_home(cfg) / cache_name,
        {},
    )

    cache_fetched_at = (
        cache.get("fetched_at")
        if isinstance(cache, dict)
        else None
    )

    out = []

    for model in _list_payload(cache):
        if not isinstance(model, dict):
            continue

        mid = (
            model.get("slug")
            or model.get("model")
            or model.get("id")
        )

        if (
            not mid
            or model.get("hidden") is True
            or model.get("visibility") == "hidden"
        ):
            continue

        native = str(mid)

        openrouter_id = (
            native
            if "/" in native
            else "openai/" + native
        )

        out.append(
            {
                "id": native,
                "openrouter_id": openrouter_id,
                "openrouter_match": "native-derived",
                "native": True,
                "enabled": True,
                "vendor": "openai",
                "family": _family(openrouter_id),
                "context_window": int(
                    model.get("context_window")
                    or model.get("contextWindow")
                    or 0
                ),
                "supports_tools": True,
                "supports_reasoning": True,
                "supported_efforts": _configured_efforts(
                    native,
                    cfg,
                    _runtime_efforts(model),
                ),
                "availability_source": (
                    "codex-models-cache"
                ),
                "availability_generated_at": (
                    cache_fetched_at
                ),
            }
        )

    return out


def _codex_bundled_candidates(
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    command = [
        str(x)
        for x in cfg.get("discovery", {}).get(
            "bundled_fallback_command",
            [],
        )
    ]

    if not command or not shutil.which(command[0]):
        return []

    try:
        run = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if run.returncode != 0:
        return []

    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError:
        return []

    out = []

    for model in _list_payload(payload):
        if not isinstance(model, dict):
            continue

        if (
            model.get("show_in_picker") is False
            or model.get("visibility") == "hidden"
        ):
            continue

        mid = (
            model.get("slug")
            or model.get("model")
            or model.get("id")
        )

        if not mid:
            continue

        native = str(mid)

        openrouter_id = (
            native
            if "/" in native
            else "openai/" + native
        )

        out.append(
            {
                "id": native,
                "openrouter_id": openrouter_id,
                "openrouter_match": "native-derived",
                "native": True,
                "enabled": True,
                "vendor": "openai",
                "family": _family(openrouter_id),
                "context_window": int(
                    model.get("context_window")
                    or model.get("contextWindow")
                    or 0
                ),
                "supports_tools": True,
                "supports_reasoning": True,
                "supported_efforts": _configured_efforts(
                    native,
                    cfg,
                    _runtime_efforts(model),
                ),
                "availability_source": (
                    "codex-bundled-fallback"
                ),
                "availability_generated_at": _now(),
            }
        )

    return out


def discover_codex(
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = _codex_cache_candidates(cfg)

    if not candidates:
        candidates = _codex_bundled_candidates(cfg)

    allow_env = str(
        cfg.get("discovery", {}).get(
            "allowlist_env",
            "HARNESS_CODEX_MODELS",
        )
    )

    allow_raw = os.getenv(allow_env, "").strip()

    if allow_raw:
        allow = {
            x.strip()
            for x in allow_raw.split(",")
            if x.strip()
        }

        candidates = [
            x
            for x in candidates
            if x["id"] in allow
        ]

    return candidates


def discover_provider(
    provider: str,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    if provider == "opencode":
        return discover_opencode(cfg)

    if provider == "codex":
        return discover_codex(cfg)

    raise ValueError(
        f"unsupported provider for v2 sync: {provider}"
    )


def _float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _percentiles(
    values_by_id: dict[str, float],
) -> dict[str, float]:
    values = sorted(values_by_id.values())

    if not values:
        return {}

    out = {}

    for mid, value in values_by_id.items():
        less = sum(1 for x in values if x < value)
        equal = sum(1 for x in values if x == value)

        percentile = (
            less + 0.5 * equal
        ) / len(values)

        out[mid] = round(
            1.0 + 4.0 * percentile,
            3,
        )

    return out


def _inverse_rank_scores(
    values_by_id: dict[str, float],
) -> dict[str, float]:
    if not values_by_id:
        return {}

    if len(values_by_id) == 1:
        return {
            next(iter(values_by_id)): 3.0
        }

    ordered = sorted(
        values_by_id.items(),
        key=lambda x: (x[1], x[0]),
    )

    n = len(ordered) - 1

    return {
        mid: round(
            5.0 - 4.0 * (idx / n),
            3,
        )
        for idx, (mid, _) in enumerate(ordered)
    }


def _price(
    model: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> float | None:
    pricing = (
        model.get("pricing")
        or (benchmark or {}).get("pricing")
        or {}
    )

    prompt = _float(pricing.get("prompt"))
    completion = _float(pricing.get("completion"))

    vals = [
        x
        for x in (prompt, completion)
        if x is not None and x >= 0
    ]

    return (
        sum(vals) / len(vals)
        if vals
        else None
    )


def _endpoint_metrics(
    payload: dict[str, Any],
) -> tuple[float | None, float | None]:
    data = (
        payload.get("data")
        if isinstance(payload, dict)
        else None
    )

    endpoints = (
        data.get("endpoints", [])
        if isinstance(data, dict)
        else []
    )

    latencies = []
    uptimes = []

    for ep in endpoints if isinstance(
        endpoints,
        list,
    ) else []:
        if not isinstance(ep, dict):
            continue

        latency = (
            ep.get("latency_last_30m")
            or ep.get("latency")
            or {}
        )

        p50 = (
            _float(latency.get("p50"))
            if isinstance(latency, dict)
            else _float(latency)
        )

        if p50 is not None and p50 >= 0:
            latencies.append(p50)

        uptime = _float(
            ep.get("uptime_last_1d")
        )

        if uptime is None:
            uptime = _float(
                ep.get("uptime_last_30m")
            )

        if uptime is not None:
            uptimes.append(uptime)

    return (
        statistics.median(latencies)
        if latencies
        else None,
        statistics.mean(uptimes)
        if uptimes
        else None,
    )


def _reliability_score(
    uptime: float | None,
) -> float:
    if uptime is None:
        return 0.0

    return round(
        max(
            1.0,
            min(
                5.0,
                1.0
                + 4.0
                * ((uptime - 95.0) / 5.0),
            ),
        ),
        3,
    )


def _local_evidence(
    provider: str,
) -> dict[str, Any]:
    payload = _load_json(
        HISTORY_DIR / f"{provider}.json",
        {},
    )

    return (
        payload.get("models", {})
        if isinstance(payload, dict)
        else {}
    )


def _model_index(
    models_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    out = {}

    for model in _list_payload(models_payload):
        if (
            isinstance(model, dict)
            and model.get("id")
        ):
            out[str(model["id"])] = model

            if model.get("canonical_slug"):
                out[
                    str(model["canonical_slug"])
                ] = model

    return out


def _benchmark_index(
    bench_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    out = {}

    for row in _list_payload(bench_payload):
        if not isinstance(row, dict):
            continue

        mid = (
            row.get("model_permaslug")
            or row.get("model_id")
            or row.get("id")
        )

        if mid:
            out[str(mid)] = row

    return out


def _fetch_sources(
    api_key: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    models = _http_json(
        f"{OPENROUTER}/models",
        api_key,
    )

    if not api_key:
        return (
            models,
            {
                "data": [],
                "meta": {
                    "warning": (
                        "OPENROUTER_API_KEY missing; "
                        "benchmark endpoint not queried"
                    )
                },
            },
        )

    benchmarks = _http_json(
        (
            f"{OPENROUTER}/benchmarks"
            "?source=artificial-analysis"
        ),
        api_key,
    )

    return models, benchmarks

def _local_fallback_scores(local_evidence: dict[str, Any]) -> dict[str, float | int]:
    benchmarks = local_evidence.get("benchmarks", {})
    capabilities = local_evidence.get("capabilities", {})
    limits = local_evidence.get("limits", {})

    # Conservative translation of external/local evidence into the harness 1..5 scale.
    # We only use this when OpenRouter data is unavailable.
    orpt = benchmarks.get("orpt_bench", {})
    swe = benchmarks.get("swe_atlas_codebase_qna", {})

    composite = _float(orpt.get("composite"))
    success_rate = _float(orpt.get("success_rate"))
    resolve_rate = _float(swe.get("resolve_rate"))

    quality_values = [
        x for x in (composite, success_rate, resolve_rate)
        if x is not None and 0.0 <= x <= 1.0
    ]

    if quality_values:
        quality = sum(quality_values) / len(quality_values)
        base_score = round(1.0 + 4.0 * quality, 3)
    else:
        base_score = 0.0

    reasoning = base_score if capabilities.get("reasoning") is True else 0.0
    coding = base_score

    tool_use = 3.0 if capabilities.get("tool_call") is True else 0.0

    context_window = int(limits.get("context") or 0)

    return {
        "reasoning": reasoning,
        "coding": coding,
        "tool_use": tool_use,
        "context_window": context_window,
    }

def refresh_provider_inventory(
    provider: str,
    *,
    api_key: str | None = None,
    models_payload: dict[str, Any] | None = None,
    benchmarks_payload: dict[str, Any] | None = None,
    endpoint_payloads: dict[
        str,
        dict[str, Any],
    ] | None = None,
    fetch_endpoints: bool | None = None,
) -> tuple[dict[str, Any], Path]:
    cfg = load_provider_config(provider)
    candidates = discover_provider(provider, cfg)

    api_key = (
        api_key
        if api_key is not None
        else os.getenv("OPENROUTER_API_KEY")
    )

    if (
        models_payload is None
        or benchmarks_payload is None
    ):
        try:
            (
                fetched_models,
                fetched_benchmarks,
            ) = _fetch_sources(api_key)

            models_payload = (
                models_payload
                or fetched_models
            )

            benchmarks_payload = (
                benchmarks_payload
                or fetched_benchmarks
            )

        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            models_payload = (
                models_payload
                or {"data": []}
            )

            benchmarks_payload = (
                benchmarks_payload
                or {"data": []}
            )

            fetch_error = str(exc)

        else:
            fetch_error = None

    else:
        fetch_error = None

    model_idx = _model_index(
        models_payload or {}
    )

    bench_idx = _benchmark_index(
        benchmarks_payload or {}
    )
    if provider == "opencode":
        aliases = cfg.get("openrouter_aliases", {})

        for candidate in candidates:
            oid, match_type = _resolve_openrouter_id(
                candidate["id"],
                aliases,
                model_idx,
            )

            candidate["openrouter_id"] = oid
            candidate["openrouter_match"] = match_type
    
    intelligence = {
        mid: x
        for mid, row in bench_idx.items()
        if (
            x := _float(
                row.get("intelligence_index")
            )
        )
        is not None
    }

    coding = {
        mid: x
        for mid, row in bench_idx.items()
        if (
            x := _float(
                row.get("coding_index")
            )
        )
        is not None
    }

    agentic = {
        mid: x
        for mid, row in bench_idx.items()
        if (
            x := _float(
                row.get("agentic_index")
            )
        )
        is not None
    }

    reasoning_scores = _percentiles(
        intelligence
    )

    coding_scores = _percentiles(coding)
    agentic_scores = _percentiles(agentic)

    prices: dict[str, float] = {}

    for candidate in candidates:
        oid = candidate.get("openrouter_id")

        if not oid:
            continue

        price = _price(
            model_idx.get(oid, {}),
            bench_idx.get(oid),
        )

        if price is not None:
            prices[candidate["id"]] = price

    cost_scores = _inverse_rank_scores(
        prices
    )

    do_endpoints = (
        cfg.get("openrouter", {}).get(
            "fetch_endpoint_health",
            True,
        )
        if fetch_endpoints is None
        else fetch_endpoints
    )

    endpoint_payloads = endpoint_payloads or {}

    latency_values: dict[str, float] = {}
    uptime_values: dict[str, float] = {}

    for candidate in candidates:
        oid = candidate.get("openrouter_id")

        if not oid:
            continue

        payload = endpoint_payloads.get(oid)

        if (
            payload is None
            and do_endpoints
            and api_key
            and "/" in oid
        ):
            author, slug = oid.split("/", 1)

            try:
                payload = _http_json(
                    (
                        f"{OPENROUTER}/models/"
                        f"{urllib.parse.quote(author)}/"
                        f"{urllib.parse.quote(slug, safe=':')}"
                        "/endpoints"
                    ),
                    api_key,
                )

            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                json.JSONDecodeError,
            ):
                payload = None

        if payload:
            latency, uptime = (
                _endpoint_metrics(payload)
            )

            if latency is not None:
                latency_values[
                    candidate["id"]
                ] = latency

            if uptime is not None:
                uptime_values[
                    candidate["id"]
                ] = uptime

    latency_scores = _inverse_rank_scores(
        latency_values
    )

    local = _local_evidence(provider)

    normalized = []

    for candidate in candidates:
        native_id = candidate["id"]
        oid = candidate.get("openrouter_id")
        local_evidence = (
            local.get(native_id)
            or (local.get(oid) if oid else {})
            or {}
        )

        local_fallback = _local_fallback_scores(local_evidence)
        model = (
            model_idx.get(oid, {})
            if oid
            else {}
        )

        bench = (
            bench_idx.get(oid, {})
            if oid
            else {}
        )

        tool_supported = bool(
            candidate.get("supports_tools")
            or "tools"
            in (
                model.get(
                    "supported_parameters"
                )
                or []
            )
        )

        reasoning = (
            reasoning_scores.get(oid, 0.0)
            if oid
            else 0.0
        )

        if reasoning <= 0:
            reasoning = float(local_fallback["reasoning"])

        coding_score = (
            coding_scores.get(oid, 0.0)
            if oid
            else 0.0
        )

        if coding_score <= 0:
            coding_score = float(local_fallback["coding"])

        agentic_score = (
            agentic_scores.get(oid)
            if oid
            else None
        )

        if agentic_score is None:
            if tool_supported:
                tool_use = 3.0
            else:
                tool_use = float(local_fallback["tool_use"])
        else:
            tool_use = round(
                0.70 * agentic_score
                + 0.30
                * (
                    5.0
                    if tool_supported
                    else 1.0
                ),
                3,
            )

        uptime = uptime_values.get(
            native_id
        )

        reliability = (
            _reliability_score(uptime)
        )

        cost = cost_scores.get(
            native_id,
            0.0,
        )

        latency = latency_scores.get(
            native_id,
            0.0,
        )

        known = [
            reasoning > 0,
            coding_score > 0,
            tool_use > 0,
            reliability > 0,
            cost > 0,
            latency > 0,
        ]

        confidence = round(
            sum(
                1
                for x in known
                if x
            )
            / len(known),
            3,
        )

        normalized.append(
            {
                "id": native_id,
                "openrouter_id": oid,
                "openrouter_match": (
                    candidate.get(
                        "openrouter_match",
                        (
                            "matched"
                            if oid
                            else "unmatched"
                        ),
                    )
                ),
                "enabled": bool(
                    candidate.get(
                        "enabled",
                        True,
                    )
                ),
                "native": bool(
                    candidate.get(
                        "native",
                        True,
                    )
                ),
                "vendor": (
                    candidate.get("vendor")
                ),
                "family": (
                    candidate.get("family")
                    or _family(oid)
                ),
                "supported_efforts": (
                    candidate.get(
                        "supported_efforts",
                        [],
                    )
                ),
                "capabilities": {
                    "reasoning": reasoning,
                    "coding": coding_score,
                    "tool_use": tool_use,
                    "reliability": reliability,
                },
                "cost": cost,
                "latency": latency,
                "context_window": int(
                    candidate.get("context_window")
                    or model.get("context_length")
                    or local_fallback["context_window"]
                    or 0
                ),
                "raw_metrics": {
                    "intelligence_index": (
                        _float(
                            bench.get(
                                "intelligence_index"
                            )
                        )
                    ),
                    "coding_index": (
                        _float(
                            bench.get(
                                "coding_index"
                            )
                        )
                    ),
                    "agentic_index": (
                        _float(
                            bench.get(
                                "agentic_index"
                            )
                        )
                    ),
                    "average_token_price": (
                        prices.get(native_id)
                    ),
                    "latency_p50": (
                        latency_values.get(
                            native_id
                        )
                    ),
                    "uptime_1d": uptime,
                    "tool_parameter_supported": (
                        tool_supported
                    ),
                },
                "provenance": {
                    "availability": (
                        candidate.get(
                            "availability_source"
                        )
                    ),
                    "availability_generated_at": (
                        candidate.get(
                            "availability_generated_at"
                        )
                    ),
                    "quality": (
                        (
                            "OpenRouter /api/v1/"
                            "benchmarks "
                            "artificial-analysis"
                        )
                        if bench
                        else None
                    ),
                    "pricing": (
                        "OpenRouter /api/v1/models"
                        if model
                        else None
                    ),
                    "endpoint_health": (
                        "OpenRouter model endpoints"
                        if (
                            native_id
                            in latency_values
                            or native_id
                            in uptime_values
                        )
                        else None
                    ),
                    "confidence": confidence,
                },
                "local_evidence": local_evidence,
                "local_fallback_used": bool(
                    local_evidence
                    and (
                        not oid
                        or not model
                        or not bench
                    )
                ),
            }
        )

    dest = ROOT / str(
        cfg["enriched_inventory"]
    )

    availability_times = [
        c.get(
            "availability_generated_at"
        )
        for c in candidates
        if c.get(
            "availability_generated_at"
        )
    ]

    availability_generated_at = (
        min(availability_times)
        if availability_times
        else _now()
    )

    payload = {
        "schema_version": 2,
        "provider": provider,
        "generated_at": _now(),
        "availability_generated_at": (
            availability_generated_at
        ),
        "source": (
            "runtime availability + OpenRouter external priors "
            "+ optional local harness evidence"
        ),
        "openrouter_benchmark_as_of": (
            (benchmarks_payload or {})
            .get("meta", {})
            .get("as_of")
            if isinstance(
                benchmarks_payload,
                dict,
            )
            else None
        ),
        "openrouter_fetch_error": (
            fetch_error
        ),
        "models": normalized,
    }

    write_json_atomic(dest, payload)

    return payload, dest


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Discover host models and enrich "
            "them using OpenRouter."
        )
    )

    ap.add_argument(
        "--provider",
        choices=[
            "opencode",
            "codex",
            "all",
        ],
        default="all",
    )

    ap.add_argument(
        "--no-endpoints",
        action="store_true",
        help=(
            "Skip per-model latency/uptime "
            "endpoint requests"
        ),
    )

    args = ap.parse_args()

    providers = (
        ["opencode", "codex"]
        if args.provider == "all"
        else [args.provider]
    )

    rc = 0

    for provider in providers:
        try:
            payload, dest = (
                refresh_provider_inventory(
                    provider,
                    fetch_endpoints=(
                        not args.no_endpoints
                    ),
                )
            )

        except Exception as exc:
            print(
                json.dumps(
                    {
                        "provider": provider,
                        "ok": False,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )

            rc = 1
            continue

        models = payload.get("models", [])

        matched = [
            m["id"]
            for m in models
            if m.get("openrouter_id")
        ]

        auto_matched = [
            m["id"]
            for m in models
            if str(m.get("openrouter_match", "")).startswith("auto-")
        ]

        alias_matched = [
            m["id"]
            for m in models
            if m.get("openrouter_match") == "explicit-alias"
        ]

        ambiguous = [
            m["id"]
            for m in models
            if m.get("openrouter_match") == "ambiguous"
        ]

        unmatched = [
            m["id"]
            for m in models
            if not m.get("openrouter_id")
            and m.get("openrouter_match") != "ambiguous"
        ]

        print(
            json.dumps(
                {
                    "provider": provider,
                    "ok": True,
                    "models": len(models),
                    "matched": len(matched),
                    "unmatched": len(unmatched),
                    "unmatched_models": unmatched,
                    "auto_matched": len(auto_matched),
                    "alias_matched": len(alias_matched),
                    "ambiguous": len(ambiguous),
                    "ambiguous_models": ambiguous,
                    "output": str(
                        dest.relative_to(ROOT)
                    ),
                    "openrouter_fetch_error": (
                        payload.get(
                            "openrouter_fetch_error"
                        )
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
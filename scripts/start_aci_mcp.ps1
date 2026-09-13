$ErrorActionPreference = "Stop"

$bridge = Join-Path $PSScriptRoot "aci_mcp_node.js"

function Test-NodeCandidate($candidate) {
    if ([string]::IsNullOrWhiteSpace($candidate) -or
        -not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        return $false
    }

    try {
        & $candidate --version *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Test-TransientNodePath($candidate) {
    return $candidate -match '(?i)(^|[\\/])fnm_multishells([\\/]|$)'
}

function Write-LauncherError($message) {
    $writer = [System.IO.StreamWriter]::new([Console]::OpenStandardError())
    try {
        $writer.WriteLine($message)
        $writer.Flush()
    }
    finally {
        $writer.Dispose()
    }
}

function Get-CodexRuntimeNodeCandidates {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        return
    }

    $runtimeRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\runtimes\cua_node"
    if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
        return
    }

    Get-ChildItem -LiteralPath $runtimeRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object { Join-Path $_.FullName "bin\node.exe" }
}

function Get-InstalledNodeCandidates {
    foreach ($root in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not [string]::IsNullOrWhiteSpace($root)) {
            Join-Path $root "nodejs\node.exe"
        }
    }

    Get-Command node.exe -CommandType Application -All -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Source }
}

$node = $null
foreach ($candidate in (@(
    Get-InstalledNodeCandidates
    Get-CodexRuntimeNodeCandidates
) | Select-Object -Unique)) {
    if (-not (Test-TransientNodePath $candidate) -and (Test-NodeCandidate $candidate)) {
        $node = $candidate
        break
    }
}

if (-not $node) {
    Write-LauncherError "harness-aci: no usable stable Node.js runtime found"
    exit 127
}

& $node $bridge
exit $LASTEXITCODE

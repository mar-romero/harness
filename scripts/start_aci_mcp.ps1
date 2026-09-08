$ErrorActionPreference = "Stop"

$log = Join-Path $PSScriptRoot "harness-aci-startup.log"
$bridge = Join-Path $PSScriptRoot "aci_mcp_node.js"

function Log($message) {
    Add-Content -Path $log -Value "$(Get-Date -Format o) $message"
}

try {
    Log "=== START ==="
    Log "PID=$PID"
    Log "PWD=$PWD"
    Log "PSScriptRoot=$PSScriptRoot"
    Log "bridge=$bridge"
    Log "APPDATA=$env:APPDATA"
    Log "LOCALAPPDATA=$env:LOCALAPPDATA"
    Log "PATH=$env:PATH"

    # 1. Node disponible en PATH
    $node = Get-Command node.exe -ErrorAction SilentlyContinue

    if ($node) {
        Log "Using PATH node: $($node.Source)"
        & $node.Source $bridge 2>> $log
        $code = $LASTEXITCODE
        Log "Node exited code=$code"
        exit $code
    }

    # 2. Node instalado por fnm
    $fnmRoot = Join-Path $env:APPDATA "fnm\node-versions"
    Log "Checking fnm root: $fnmRoot"

    if (Test-Path $fnmRoot) {
        $fnmNode = Get-ChildItem $fnmRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $candidate = Join-Path $_.FullName "installation\node.exe"
                if (Test-Path $candidate) {
                    $candidate
                }
            } |
            Select-Object -First 1

        if ($fnmNode) {
            Log "Using fnm node: $fnmNode"
            & $fnmNode $bridge 2>> $log
            $code = $LASTEXITCODE
            Log "Node exited code=$code"
            exit $code
        }
    }

    # 3. Node incluido con Codex Desktop
    $codexRuntimeRoot = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\runtimes\cua_node"
    Log "Checking Codex runtime: $codexRuntimeRoot"

    if (Test-Path $codexRuntimeRoot) {
        $codexNode = Get-ChildItem $codexRuntimeRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object {
                $candidate = Join-Path $_.FullName "bin\node.exe"
                if (Test-Path $candidate) {
                    $candidate
                }
            } |
            Select-Object -First 1

        if ($codexNode) {
            Log "Using Codex node: $codexNode"
            & $codexNode $bridge 2>> $log
            $code = $LASTEXITCODE
            Log "Node exited code=$code"
            exit $code
        }
    }

    Log "ERROR: No Node runtime found"
    exit 127
}
catch {
    Log "EXCEPTION: $($_.Exception.ToString())"
    exit 126
}
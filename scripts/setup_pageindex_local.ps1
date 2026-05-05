param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path -LiteralPath $InstallRoot -ErrorAction SilentlyContinue
if (-not $root) {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    $root = Resolve-Path -LiteralPath $InstallRoot
}

$manifestPath = Join-Path $root "installation.json"
$manifest = @{
    repo_dir = ""
    python_path = ""
    script_path = ""
    version = ""
    status = "manual_setup_required"
}

$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Output "Created local PageIndex setup manifest at $manifestPath"


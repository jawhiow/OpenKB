[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Command = "status",

    [Alias("Host")]
    [string]$HostName = "127.0.0.1",

    [int]$Port = 8765,

    [switch]$Browser,

    [string]$Python = "",

    [string]$StateDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if ([string]::IsNullOrWhiteSpace($StateDir)) {
    $StateDir = Join-Path $RepoRoot ".openkb-client"
}

$PidFile = Join-Path $StateDir "client-$Port.pid"
$OutLog = Join-Path $StateDir "client-$Port.out.log"
$ErrLog = Join-Path $StateDir "client-$Port.err.log"
$Url = "http://${HostName}:$Port"

function Write-Failure {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Write-Line {
    param([string]$Message)
    [Console]::Out.WriteLine($Message)
}

function Ensure-StateDir {
    if (-not (Test-Path -LiteralPath $StateDir)) {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    }
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($Python)) {
        return $Python
    }

    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    return "python"
}

function Get-ManagedProcess {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return $null
    }

    $rawPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $pidValue = 0
    if (-not [int]::TryParse([string]$rawPid, [ref]$pidValue)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }

    try {
        return Get-Process -Id $pidValue -ErrorAction Stop
    }
    catch {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Get-PortOwner {
    try {
        return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
    }
    catch {
        return $null
    }
}

function Show-Status {
    $managed = Get-ManagedProcess
    if ($null -ne $managed) {
        Write-Line "OpenKB client is running."
        Write-Line "PID: $($managed.Id)"
        Write-Line "URL: $Url"
        Write-Line "Logs: $OutLog"
        Write-Line "Errors: $ErrLog"
        return 0
    }

    $owner = Get-PortOwner
    if ($null -ne $owner) {
        Write-Line "OpenKB client is not running from this script."
        Write-Line "Port $Port is already used by PID $($owner.OwningProcess)."
        return 4
    }

    Write-Line "OpenKB client is not running."
    return 3
}

function Start-ManagedClient {
    Ensure-StateDir

    $managed = Get-ManagedProcess
    if ($null -ne $managed) {
        Write-Line "OpenKB client is already running."
        Write-Line "PID: $($managed.Id)"
        Write-Line "URL: $Url"
        return 0
    }

    $owner = Get-PortOwner
    if ($null -ne $owner) {
        Write-Failure "Port $Port is already used by PID $($owner.OwningProcess). Stop that process or choose another -Port."
        return 2
    }

    $pythonExe = Resolve-Python
    $clientArgs = @(
        "-m",
        "openkb",
        "client",
        "--host",
        $HostName,
        "--port",
        [string]$Port
    )

    if (-not $Browser.IsPresent) {
        $clientArgs += "--no-browser"
    }

    $process = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList $clientArgs `
        -WorkingDirectory $RepoRoot `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog

    Set-Content -LiteralPath $PidFile -Value ([string]$process.Id) -Encoding ASCII
    Start-Sleep -Milliseconds 700

    try {
        $fresh = Get-Process -Id $process.Id -ErrorAction Stop
        Write-Line "OpenKB client started."
        Write-Line "PID: $($fresh.Id)"
        Write-Line "URL: $Url"
        Write-Line "Logs: $OutLog"
        Write-Line "Errors: $ErrLog"
        return 0
    }
    catch {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Failure "OpenKB client exited immediately. Check logs: $ErrLog"
        return 1
    }
}

function Stop-ManagedClient {
    $managed = Get-ManagedProcess
    if ($null -eq $managed) {
        Write-Line "OpenKB client is not running."
        return 0
    }

    Stop-Process -Id $managed.Id -Force -ErrorAction Stop
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Line "OpenKB client stopped."
    Write-Line "PID: $($managed.Id)"
    return 0
}

switch ($Command) {
    "start" {
        exit (Start-ManagedClient)
    }
    "stop" {
        exit (Stop-ManagedClient)
    }
    "restart" {
        $stopCode = Stop-ManagedClient
        if ($stopCode -ne 0) {
            exit $stopCode
        }
        exit (Start-ManagedClient)
    }
    "status" {
        exit (Show-Status)
    }
}

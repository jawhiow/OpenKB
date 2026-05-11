[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status")]
    [string]$Command = "status",

    [Alias("Host")]
    [string]$HostName = "0.0.0.0",

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

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return [string]$process.CommandLine
    }
    catch {
        return ""
    }
}

function Test-OpenKbClientProcess {
    param([int]$ProcessId)
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return $false
    }
    return (
        (
            $commandLine -like "* openkb client *" -or
            $commandLine -like "* openkb client" -or
            $commandLine -like "*-m openkb client*" -or
            $commandLine -like "*openkb.client.server:create_app*"
        ) -and
        $commandLine -like "*--port*" -and
        $commandLine -like "*$Port*"
    )
}

function Stop-ProcessTree {
    param([int]$RootProcessId)

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -RootProcessId ([int]$child.ProcessId)
    }

    try {
        Stop-Process -Id $RootProcessId -Force -ErrorAction Stop
    }
    catch {
        # Process may have exited while we were stopping its children.
    }
}

function Stop-OpenKbPortOwner {
    $owner = Get-PortOwner
    if ($null -eq $owner) {
        return $false
    }

    $ownerPid = [int]$owner.OwningProcess
    if (-not (Test-OpenKbClientProcess -ProcessId $ownerPid)) {
        Write-Failure "Port $Port is used by PID $ownerPid, but it does not look like an OpenKB client process."
        return $false
    }

    Write-Line "Stopping unmanaged OpenKB client on port $Port."
    Write-Line "PID: $ownerPid"
    try {
        $ownerProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction Stop
        $parentPid = [int]$ownerProcess.ParentProcessId
        if ($parentPid -gt 0 -and (Test-OpenKbClientProcess -ProcessId $parentPid)) {
            $ownerPid = $parentPid
        }
    }
    catch {
        # Fall back to stopping the listening process itself.
    }
    Stop-ProcessTree -RootProcessId $ownerPid

    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
        if ($null -eq (Get-PortOwner)) {
            return $true
        }
    }

    return ($null -eq (Get-PortOwner))
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
        if (Stop-OpenKbPortOwner) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
            Write-Line "OpenKB client stopped."
            return 0
        }

        if ($null -ne (Get-PortOwner)) {
            return 2
        }

        Write-Line "OpenKB client is not running."
        return 0
    }

    Stop-Process -Id $managed.Id -Force -ErrorAction Stop
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
        if ($null -eq (Get-PortOwner)) {
            break
        }
        $owner = Get-PortOwner
        if ($null -ne $owner -and (Test-OpenKbClientProcess -ProcessId ([int]$owner.OwningProcess))) {
            Stop-OpenKbPortOwner | Out-Null
            break
        }
    }
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

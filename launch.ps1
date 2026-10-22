<#
.SYNOPSIS
    One-command launcher for the whole simulation stack: Unreal/ProjectAirSim, the env
    container, the ArduPilot SITL(s) and the flight code.

.DESCRIPTION
    Runs on Windows (not inside WSL), because it has to start Unreal. Everything container
    side is driven through `wsl -e bash -lc`.

    The order is forced by a circular wait between the sim and the SITL: the SITL pulls
    scene data on startup so a scene must already exist, but a drone that spawns before its
    SITL is up softlocks. So:

      1. Start Unreal standalone (-game) and wait for the ProjectAirSim server on 8989.
      2. Bring up the env container.
      3. suas.py --stage empty          (gives the SITL a scene to pull)
      4. Start the sim container in its own window and wait for the SITL(s) to bind.
      5. suas.py --stage spawn          (spawns the drones, then runs the flight code)

.PARAMETER NumDrones
    Drones to spawn, and SITL instances to start. Passed through as NUM_DRONES.

.PARAMETER UnrealProject
    Path to Simulation.uproject. Found automatically by searching up from this script, or
    set the UNREAL_PROJECT environment variable.

.PARAMETER AirsimHost
    Where the ProjectAirSim server is reachable from inside WSL. 127.0.0.1 is correct with
    mirrored networking; use the "vEthernet (WSL)" address otherwise.

.PARAMETER SkipUnreal
    Leave Unreal alone and only require that 8989 already answers.

.EXAMPLE
    .\launch.ps1
    .\launch.ps1 -NumDrones 4
#>

[CmdletBinding()]
param(
    [int]$NumDrones = 1,
    [string]$UnrealProject = $env:UNREAL_PROJECT,
    [string]$AirsimHost = "127.0.0.1",
    [switch]$SkipUnreal,
    [int]$UnrealTimeoutSec = 300,
    [int]$SitlTimeoutSec = 420
)

$ErrorActionPreference = "Stop"
$AirsimPort = 8989  # ProjectAirSim topic manager; the client's connect() target

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Invoke-Wsl([string]$command, [switch]$AllowFailure) {
    # -l so the login shell picks up pipx's PATH, where podman-compose lives
    $output = & wsl.exe -e bash -lc $command 2>&1
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "WSL command failed (exit $LASTEXITCODE):`n  $command`n$($output -join "`n")"
    }
    return $output
}

function Test-Port([string]$targetHost, [int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($targetHost, $port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-For([string]$what, [int]$timeoutSec, [scriptblock]$test) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    Write-Host "Waiting for $what" -NoNewline
    while ((Get-Date) -lt $deadline) {
        if (& $test) {
            Write-Host " ok" -ForegroundColor Green
            return
        }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 2
    }
    Write-Host ""
    throw "Timed out after ${timeoutSec}s waiting for $what"
}

function Find-UnrealProject {
    if ($UnrealProject) {
        if (-not (Test-Path $UnrealProject)) { throw "No .uproject at '$UnrealProject'" }
        return (Resolve-Path $UnrealProject).Path
    }
    # Walk up from this script: the Unreal project is a sibling repo, not part of this one
    $dir = $PSScriptRoot
    for ($i = 0; $i -lt 5; $i++) {
        $candidate = Join-Path $dir "SimulationRepository\Simulation.uproject"
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
        $parent = Split-Path $dir -Parent
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    throw "Could not find SimulationRepository\Simulation.uproject above '$PSScriptRoot'. Pass -UnrealProject."
}

function Resolve-UnrealEditor([string]$uproject) {
    $association = (Get-Content $uproject -Raw | ConvertFrom-Json).EngineAssociation
    if (-not $association) { throw "'$uproject' has no EngineAssociation" }

    $root = $null
    # Launcher installs register under HKLM by version ("5.2")
    $installed = "HKLM:\SOFTWARE\EpicGames\Unreal Engine\$association"
    if (Test-Path $installed) { $root = (Get-ItemProperty $installed).InstalledDirectory }
    # Source builds register under HKCU by GUID instead
    if (-not $root) {
        $builds = "HKCU:\SOFTWARE\Epic Games\Unreal Engine\Builds"
        if (Test-Path $builds) {
            $value = (Get-ItemProperty $builds).$association
            if ($value) { $root = $value }
        }
    }
    if (-not $root) { throw "Engine '$association' is not registered in HKLM or HKCU" }

    $editor = Join-Path $root "Engine\Binaries\Win64\UnrealEditor.exe"
    if (-not (Test-Path $editor)) { throw "No UnrealEditor.exe at '$editor'" }
    return $editor
}

# Counts SITL panes that have reached the point of binding their sensor input socket. That
# happens before ArduPilot boots (boot needs sensor data, which needs the drones), so it is
# the latest readiness signal available pre-spawn.
$SitlReadyProbe = @'
sim=$(podman ps --format '{{.Names}}' | grep -E '_sim(_|$)' | head -1)
if [ -z "$sim" ]; then echo 0; exit 0; fi
n=0
for pane in $(podman exec "$sim" tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null); do
    if podman exec "$sim" tmux capture-pane -J -pt "$pane" -S - 2>/dev/null \
        | grep -q 'Bind SITL sensor input\|No sensor message'; then
        n=$((n + 1))
    fi
done
echo "$n"
'@

# --- 1. Unreal ---------------------------------------------------------------------------

if (Test-Port "127.0.0.1" $AirsimPort) {
    Write-Step "ProjectAirSim already serving on $AirsimPort, leaving Unreal alone"
} elseif ($SkipUnreal) {
    throw "Nothing is listening on $AirsimPort and -SkipUnreal was passed. Start Unreal first."
} else {
    $uproject = Find-UnrealProject
    $editor = Resolve-UnrealEditor $uproject
    Write-Step "Starting Unreal standalone: $uproject"
    Write-Host "  $editor"
    Start-Process -FilePath $editor -ArgumentList @(
        "`"$uproject`"", "-game", "-windowed", "-ResX=1280", "-ResY=720"
    ) | Out-Null
    Wait-For "the ProjectAirSim server on $AirsimPort" $UnrealTimeoutSec {
        Test-Port "127.0.0.1" $AirsimPort
    }
}

# --- 2. env container --------------------------------------------------------------------

$wslDir = (Invoke-Wsl "wslpath -a '$PSScriptRoot'").Trim()
Write-Step "Restarting containers (compose project at $wslDir)"
Invoke-Wsl "cd '$wslDir' && podman-compose down" -AllowFailure | Out-Null
Invoke-Wsl "cd '$wslDir' && NUM_DRONES=$NumDrones podman-compose up -d env" | Out-Null
Wait-For "the env container" 60 {
    $state = Invoke-Wsl "podman inspect -f '{{.State.Running}}' multirotor_env" -AllowFailure
    return ($state -join "") -match "true"
}

# --- 3. empty scene ----------------------------------------------------------------------

Write-Step "Loading the empty scene (so the SITL has scene data to pull)"
Invoke-Wsl "podman exec -i multirotor_env python -u simulation/interfaces/suas.py --stage empty"

# --- 4. SITL ----------------------------------------------------------------------------

Write-Step "Starting $NumDrones SITL instance(s) in a new window"
$simCommand = "cd '$wslDir' && NUM_DRONES=$NumDrones AIRSIM_HOST=$AirsimHost podman-compose run --rm sim"
$windowsTerminal = Get-Command wt.exe -ErrorAction SilentlyContinue
if ($windowsTerminal) {
    Start-Process wt.exe -ArgumentList @(
        "-w", "0", "new-tab", "--title", "SITL", "wsl.exe", "-e", "bash", "-lc", "`"$simCommand`""
    ) | Out-Null
} else {
    Start-Process wsl.exe -ArgumentList @("-e", "bash", "-lc", "`"$simCommand`"") | Out-Null
}

Wait-For "$NumDrones SITL instance(s) to bind (first run also builds ArduPilot)" $SitlTimeoutSec {
    $count = (Invoke-Wsl $SitlReadyProbe -AllowFailure | Select-Object -Last 1)
    $parsed = 0
    if ([int]::TryParse(($count -as [string]).Trim(), [ref]$parsed)) { return $parsed -ge $NumDrones }
    return $false
}

# --- 5. spawn and fly --------------------------------------------------------------------

Write-Step "Spawning drone(s) and starting the flight code"
Invoke-Wsl "podman exec -i multirotor_env python -u simulation/interfaces/suas.py --stage spawn" -AllowFailure

Write-Host ""
Write-Host "Flight code exited. Still running: Unreal, the env container and the SITL window." -ForegroundColor Yellow
Write-Host "  Watch the SITL:  ./run_container.sh attach sim"
Write-Host "  Stop containers: ./run_container.sh shutdown"
Write-Host "  Fly again:       .\launch.ps1 -NumDrones $NumDrones"

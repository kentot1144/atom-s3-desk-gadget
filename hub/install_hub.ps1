# AtomS3 desk hub installer (run on the PC that should host the hub)
# Right-click -> Run with PowerShell, or run install_hub.bat

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host ""
Write-Host "=== AtomS3 desk hub setup ===" -ForegroundColor Cyan
Write-Host "Folder: $here"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python was not found in PATH. Install Python 3 first." -ForegroundColor Red
    exit 1
}
$pyw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $pyw) {
    Write-Host "pythonw.exe was not found next to python.exe." -ForegroundColor Red
    exit 1
}

Write-Host "Python: $($py.Source)"

& python -c "import psutil" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing psutil..."
    & python -m pip install --user psutil
}

# --- firewall rules (UAC prompt) ---
function Ensure-Rule($name, $proto, $port) {
    if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
        Write-Host "Firewall rule present: $name"
        return
    }
    Write-Host "Adding firewall rule for $proto $port (approve the UAC prompt)..."
    Start-Process netsh -Verb RunAs -ArgumentList (
        'advfirewall firewall add rule name="' + $name +
        '" dir=in action=allow protocol=' + $proto + ' localport=' + $port
    ) | Out-Null
    Start-Sleep -Seconds 4
}

Ensure-Rule "AtomS3 Hub 8765" TCP 8765
Ensure-Rule "AtomS3 Hub discovery 8766" UDP 8766

# --- stop every old hub instance (python.exe or pythonw.exe) ---
$old = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python|pythonw)\.exe$' -and
        $_.CommandLine -and $_.CommandLine -like "*hub.py*"
    }
foreach ($p in $old) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped old hub PID $($p.ProcessId) ($($p.Name))"
}
if ($old) { Start-Sleep -Seconds 2 }

# --- autostart shortcut (hidden) ---
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "AtomS3 Hub.lnk"
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pyw.Source
$lnk.Arguments = '"' + (Join-Path $here "hub.py") + '"'
$lnk.WorkingDirectory = $here
$lnk.WindowStyle = 7
$lnk.Description = "AtomS3 desk hub"
$lnk.Save()
Write-Host "Autostart: $lnkPath"

# --- silent watchdog (starts the hub again if it ever stops answering) ---
$checkVbs = Join-Path $here "check_hub.vbs"
$taskCmd = 'wscript.exe "' + $checkVbs + '"'
& schtasks /create /tn "AtomS3 Hub Watchdog" /tr $taskCmd /sc minute /mo 2 /f | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Watchdog: AtomS3 Hub Watchdog (checks every 2 minutes, no window)"
} else {
    Write-Host "Could not register the watchdog task (optional)." -ForegroundColor Yellow
}

# --- start now ---
Start-Process $pyw.Source -ArgumentList ('"' + (Join-Path $here "hub.py") + '"') -WorkingDirectory $here -WindowStyle Hidden
Start-Sleep -Seconds 2

try {
    $r = Invoke-WebRequest "http://localhost:8765/ping" -UseBasicParsing -TimeoutSec 5
    Write-Host "Hub is running: $($r.Content)" -ForegroundColor Green
} catch {
    Write-Host "Hub did not answer yet. Check the console output: python hub.py" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Stats page: http://localhost:8765/stats" -ForegroundColor Cyan
Write-Host "AtomS3 config -> hub_url: http://$((Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -like '192.168.*' -and $_.PrefixOrigin -ne 'WellKnown'} | Select-Object -First 1).IPAddress):8765"
Write-Host ""

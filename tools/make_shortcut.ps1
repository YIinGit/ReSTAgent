param(
  [string]$Dest = "$env:USERPROFILE\Desktop",
  [string]$Root = "",
  [string]$Pythonw = ""
)

$ErrorActionPreference = "Stop"

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Root "ReSTAgent.pyw"))) {
  Write-Host "[ERROR] ReSTAgent.pyw not found under $Root"
  exit 1
}

function Test-Python([string[]]$Cmd) {
  try {
    $exe = $Cmd[0]
    $pre = @()
    if ($Cmd.Count -gt 1) { $pre = $Cmd[1..($Cmd.Count - 1)] }
    $out = & $exe @pre -c "import sys;print(sys.executable if sys.version_info>=(3,10) else '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return $out.Trim() }
  } catch { }
  return $null
}

$python = $null
if ($Pythonw) {
  $python = $Pythonw -replace 'pythonw\.exe$', 'python.exe'
} else {
  foreach ($cand in @(
      @("py", "-3.12"), @("py", "-3.13"), @("py", "-3.14"), @("py", "-3.11"),
      @("py", "-3.10"), @("python"), @("python3"), @("py", "-3"), @("py")
    )) {
    $python = Test-Python $cand
    if ($python) { break }
  }
}

if (-not $python) {
  Write-Host "[ERROR] No usable Python 3.10+ found."
  Write-Host "Run 'py -0p' to inspect registered versions, or reinstall Python."
  exit 1
}

$pyw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $null }

if (-not (Test-Path $Dest)) { New-Item -ItemType Directory -Path $Dest -Force | Out-Null }

$ws = New-Object -ComObject WScript.Shell
$lnkPath = Join-Path $Dest "ReSTAgent.lnk"
$lnk = $ws.CreateShortcut($lnkPath)

if ($pyw) {
  $lnk.TargetPath = $pyw
  $lnk.Arguments = "-m app.desktop"
  $lnk.WindowStyle = 7
} else {
  $lnk.TargetPath = "$env:SystemRoot\System32\cmd.exe"
  $lnk.Arguments = '/c ""' + (Join-Path $Root "ReSTAgent.bat") + '""'
  $lnk.WindowStyle = 7
}
$lnk.WorkingDirectory = $Root
$lnk.IconLocation = (Join-Path $Root "web\icon.ico")
$lnk.Description = "ReSTAgent"
$lnk.Save()

Write-Host "Shortcut created: $lnkPath"
Write-Host "  python : $python"
if ($pyw) {
  Write-Host "  target : $pyw -m app.desktop"
} else {
  Write-Host "  target : ReSTAgent.bat (pythonw.exe not found)"
}

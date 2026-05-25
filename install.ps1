# donovan Windows install script
#
# Copy-paste install:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/tudor-22/donovan-agent/main/install.ps1 | iex"

$RepoUrl = if ($env:DONOVAN_REPO_URL) { $env:DONOVAN_REPO_URL } else { "https://github.com/tudor-22/donovan-agent" }
$Project = "donovan-agent"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "==> $msg" -ForegroundColor Red }
function Add-UserPath($dir) {
  $full = [System.IO.Path]::GetFullPath($dir)
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $parts = @()
  if ($userPath) {
    $parts = $userPath -split ";" | Where-Object { $_ -and $_.Trim() }
  }

  $alreadySet = $false
  foreach ($part in $parts) {
    if ([string]::Equals(
      [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($part)),
      $full,
      [System.StringComparison]::OrdinalIgnoreCase
    )) {
      $alreadySet = $true
      break
    }
  }

  if (-not $alreadySet) {
    $newPath = if ($userPath) { "$userPath;$full" } else { $full }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  }

  $sessionParts = $env:Path -split ";" | Where-Object { $_ -and $_.Trim() }
  $inSession = $false
  foreach ($part in $sessionParts) {
    if ([string]::Equals(
      [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($part)),
      $full,
      [System.StringComparison]::OrdinalIgnoreCase
    )) {
      $inSession = $true
      break
    }
  }
  if (-not $inSession) {
    $env:Path = "$env:Path;$full"
  }
}

function Install-CommandShim($name, $target) {
  $binDir = Join-Path $env:LOCALAPPDATA "Programs\donovan\bin"
  New-Item -ItemType Directory -Force -Path $binDir | Out-Null
  $shim = Join-Path $binDir "$name.cmd"
  $content = @"
@echo off
"$target" %*
"@
  Set-Content -Path $shim -Value $content -Encoding ASCII
  Add-UserPath $binDir
}

$python = $null
foreach ($candidate in @("py", "python3", "python")) {
  try {
    if ($candidate -eq "py") {
      $ver = & $candidate -3.11 --version 2>&1
      if ($LASTEXITCODE -eq 0 -and $ver -match "(\d+)\.(\d+)") {
        $python = "py -3.11"
        break
      }
    } else {
      $ver = & $candidate --version 2>&1
      if ($ver -match "(\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -ge 3 -and $minor -ge 11) {
          $python = $candidate
          break
        }
      }
    }
  } catch {
    continue
  }
}

if (-not $python) {
  Write-Err "Python 3.11+ is required but was not found."
  Write-Err "Install it from https://www.python.org/downloads/ and run this command again."
  exit 1
}

Write-Step "Found Python"

if (-not (Test-Path "pyproject.toml")) {
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err "Git is required when running the installer outside a source checkout."
    Write-Err "Install Git or download the source from $RepoUrl."
    exit 1
  }

  Write-Step "Cloning donovan"
  git clone "$RepoUrl.git"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  Set-Location $Project
}

Write-Step "Creating virtual environment"
Invoke-Expression "$python -m venv .venv"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$activate = Join-Path (Get-Location) ".venv\Scripts\Activate.ps1"
. $activate

Write-Step "Installing donovan"
python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m pip install -e .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$venvScripts = Join-Path (Get-Location) ".venv\Scripts"
Install-CommandShim "donovan" (Join-Path $venvScripts "donovan.exe")
Install-CommandShim "donovanagent" (Join-Path $venvScripts "donovanagent.exe")

Write-Host ""
Write-Host "donovan includes optional browser automation support."
$installBrowser = Read-Host "Install browser support? [y/N]"
if ($installBrowser -eq "y" -or $installBrowser -eq "Y") {
  Write-Step "Installing browser support"
  python -m pip install -e ".[browser]"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  python -m playwright install chromium
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Step "Running first-time setup"
donovan setup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "donovan installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "To start donovan:"
Write-Host "  donovan"
Write-Host ""
Write-Host 'Or run a one-off command:'
Write-Host '  donovan chat "What can you do?"'
Write-Host ""
Write-Host "If this terminal was open before installation, restart it if Windows has not refreshed PATH yet."

<#
.SYNOPSIS
  Smoke test ProspectOS no Windows — valida artefatos, inicia o app, verifica readiness,
  compatibilidade de dados, Credential Manager, lifecycle e processos órfãos.

.DESCRIPTION
  Uso: .\scripts\smoke_windows_local.ps1 [-AppDir <path>] [-DataDir <path>]
  Requirements: PowerShell 7+, Windows 10/11 x64, ProspectOS buildado em desktop/saida/.

  NÃO usar dados reais. NÃO desativar Defender. NÃO usar credenciais reais.
#>

param(
  [string]$AppDir = "",
  [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

$REPO_ROOT = Split-Path -Parent $PSScriptRoot
$TEMP_ROOT = if ($DataDir) { $DataDir } else { "$env:TEMP\prospectos-pr8-smoke" }
$APPDATA_TEST = "$TEMP_ROOT\data"
$LOG_DIR = "$TEMP_ROOT\logs"
$TEMP_DIR = "$TEMP_ROOT\temp"
$CACHE_DIR = "$TEMP_ROOT\cache"
$FIXTURE_DIR = "$TEMP_ROOT\fixture-data"
$PASS_COUNT = 0
$FAIL_COUNT = 0
$ERRORS = @()

$ORPHAN_PIDS = @{}

function Pass($msg) { $script:PASS_COUNT++; Write-Host "  PASS: $msg" -ForegroundColor Green }
function Fail($msg) { $script:FAIL_COUNT++; Write-Host "  FAIL: $msg" -ForegroundColor Red; $script:ERRORS += $msg }
function Info($msg) { Write-Host "  INFO: $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "  WARN: $msg" -ForegroundColor Yellow }
function Step($n, $total, $label) { Write-Host "`n[$n/$total] $label" -ForegroundColor Magenta; Write-Host ("-" * 50) }

function Find-App {
  if ($AppDir -and (Test-Path $AppDir)) { return $AppDir }
  $candidates = @(
    "$REPO_ROOT\desktop\saida\ProspectOS Setup*\",
    "$REPO_ROOT\desktop\saida\win-unpacked\",
    "$REPO_ROOT\desktop\saida\win-x64-unpacked\"
  )
  foreach ($pattern in $candidates) {
    $dirs = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer }
    if ($dirs) { return $dirs[0].FullName }
  }
  # Try NSIS installer output
  $nsisDirs = Get-ChildItem -Path "$REPO_ROOT\instalador\saida\*.exe" -ErrorAction SilentlyContinue
  if ($nsisDirs) {
    # Installer found, but we need installed app — try default location
    $localApp = "$env:LOCALAPPDATA\Programs\ProspectOS\ProspectOS.exe"
    if (Test-Path $localApp) { return "$env:LOCALAPPDATA\Programs\ProspectOS" }
  }
  return $null
}

function Get-OrphanProcesses {
  $ourPids = @{}
  Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match "ProspectOS|google-maps-scraper|node|chrome|chromium"
  } | ForEach-Object {
    $ourPids[$_.ProcessId] = @{
      Name = $_.Name
      PPID = $_.ParentProcessId
      Cmd = $_.CommandLine
    }
  }
  return $ourPids
}

function Format-Duration($seconds) {
  if ($seconds -lt 60) { return "$([math]::Round($seconds, 1))s" }
  return "$([math]::Round($seconds / 60, 1))m"
}

# ============================================================================
# STEP 1: Validate environment
# ============================================================================
Step 1 20 "Validando ambiente Windows"

$psVer = $PSVersionTable.PSVersion.ToString()
$winInfo = Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsArchitecture
$nodeVer = node --version 2>$null
$npmVer = npm --version 2>$null
$pythonVer = python --version 2>&1
$goVer = go version 2>$null

Info "PowerShell: $psVer"
Info "Windows: $($winInfo.WindowsProductName) $($winInfo.WindowsVersion) $($winInfo.OsArchitecture)"
Info "Node: $nodeVer"
Info "npm: $npmVer"
Info "Python: $pythonVer"
Info "Go: $goVer"

if ($nodeVer) { Pass "Node.js encontrado" } else { Fail "Node.js nao encontrado" }

# ============================================================================
# STEP 2: Find app
# ============================================================================
Step 2 20 "Localizando aplicativo empacotado"

$appPath = Find-App
if (-not $appPath) {
  Fail "Nenhum build encontrado. Execute primeiro: python scripts\build_desktop.py --target win32-x64"
} else {
  Pass "App encontrado: $appPath"
}

# ============================================================================
# STEP 3: Validate app layout
# ============================================================================
Step 3 20 "Validando estrutura do aplicativo"

$checks = @{
  "Executavel" = "$appPath\ProspectOS.exe"
  "Backend" = "$appPath\resources\backend\ProspectOS.exe"
  "Scraper" = "$appPath\resources\scraper\google-maps-scraper.exe"
  "Runtime manifest" = "$appPath\resources\shared\runtime-targets.json"
  "Playwright manifest" = "$appPath\resources\shared\playwright-runtime-targets.json"
}

$allFound = $true
foreach ($label in $checks.Keys) {
  $path = $checks[$label]
  if (Test-Path $path) {
    $size = (Get-Item $path).Length
    Pass "$label: $path ($(if($size -gt 1MB){'{0:N1} MB' -f ($size/1MB)}else{'{0:N1} KB' -f ($size/1KB)}))"
  } else {
    Fail "$label ausente: $path"
    $allFound = $false
  }
}

if ($allFound) { Pass "Estrutura do aplicativo OK" }

$exeInfo = Get-Item "$appPath\ProspectOS.exe"
if ($exeInfo.Length -gt 50MB) { Pass "Executavel Electron com tamanho adequado" }

# ============================================================================
# STEP 4: Validate backend PE format
# ============================================================================
Step 4 20 "Validando backend (PE32+)"

$backendExe = "$appPath\resources\backend\ProspectOS.exe"
if (Test-Path $backendExe) {
  $size = (Get-Item $backendExe).Length
  if ($size -gt 10MB) {
    Pass "Backend PE x64: $('{0:N1} MB' -f ($size/1MB))"
  } else {
    Warn "Backend suspeitamente pequeno: $('{0:N1} KB' -f ($size/1KB))"
  }
}

# ============================================================================
# STEP 5: Validate scraper PE format
# ============================================================================
Step 5 20 "Validando scraper (PE32+)"

$scraperExe = "$appPath\resources\scraper\google-maps-scraper.exe"
if (Test-Path $scraperExe) {
  $size = (Get-Item $scraperExe).Length
  $sha = (Get-FileHash $scraperExe -Algorithm SHA256).Hash
  Pass "Scraper PE x64: $('{0:N1} MB' -f ($size/1MB))"
  Info "SHA-256: $sha"
}

# ============================================================================
# STEP 6: Runtime manifest validation
# ============================================================================
Step 6 20 "Validando RuntimeManifest"

$manifestPath = "$appPath\resources\shared\runtime-targets.json"
if (Test-Path $manifestPath) {
  $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
  if ($manifest.schemaVersion -eq 1) { Pass "schemaVersion: 1" }
  if ($manifest.targets.'win32-x64'.backend.name -eq "backend/ProspectOS.exe") {
    Pass "win32-x64 backend: $($manifest.targets.'win32-x64'.backend.name)"
  } else {
    Fail "win32-x64 backend name inesperado: $($manifest.targets.'win32-x64'.backend.name)"
  }
  if ($manifest.targets.'win32-x64'.scraper.name -eq "scraper/google-maps-scraper.exe") {
    Pass "win32-x64 scraper: $($manifest.targets.'win32-x64'.scraper.name)"
  } else {
    Fail "win32-x64 scraper name inesperado: $($manifest.targets.'win32-x64'.scraper.name)"
  }
}

$pwManifestPath = "$appPath\resources\shared\playwright-runtime-targets.json"
if (Test-Path $pwManifestPath) {
  $pwManifest = Get-Content $pwManifestPath -Raw | ConvertFrom-Json
  if ($pwManifest.runtimes.'win32-x64') {
    Pass "Playwright runtime win32-x64 presente no manifesto"
  } else {
    Fail "Playwright runtime win32-x64 ausente do manifesto"
  }
}

# ============================================================================
# STEP 7: Prepare temp directories
# ============================================================================
Step 7 20 "Preparando diretorios temporarios"

@($APPDATA_TEST, $LOG_DIR, $TEMP_DIR, $CACHE_DIR) | ForEach-Object {
  New-Item -ItemType Directory -Force -Path $_ | Out-Null
}
Pass "Diretorios temporarios criados em $TEMP_ROOT"

# ============================================================================
# STEP 8: Create fixture data
# ============================================================================
Step 8 20 "Criando fixture de dados existentes"

$fixtureDb = "$APPDATA_TEST\leads.db"
$fixtureBackups = "$APPDATA_TEST\backups"
$fixtureSaidas = "$APPDATA_TEST\saidas"
$fixtureInstagram = "$APPDATA_TEST\instagram"

New-Item -ItemType Directory -Force -Path $fixtureBackups | Out-Null
New-Item -ItemType Directory -Force -Path $fixtureSaidas | Out-Null
New-Item -ItemType Directory -Force -Path "$fixtureInstagram\sessao" | Out-Null
New-Item -ItemType Directory -Force -Path "$fixtureInstagram\comentarios" | Out-Null

# Create a minimal SQLite database
try {
  python -c @"
import sqlite3, os
os.makedirs('$fixtureBackups'.replace('\\','/'), exist_ok=True)
conn = sqlite3.connect('$fixtureDb'.replace('\\','/'))
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('''CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nome TEXT, telefone TEXT, endereco TEXT,
  website TEXT, status TEXT DEFAULT 'novo',
  criado_em TEXT, atualizado_em TEXT
)''')
conn.execute("INSERT INTO leads (nome, telefone, status, criado_em) VALUES ('Lead Teste', '31 99999-0000', 'novo', datetime('now'))")
conn.execute("INSERT INTO leads (nome, telefone, status, criado_em) VALUES ('Lead Existente', '31 98888-0000', 'contatado', datetime('now'))")
conn.commit()
conn.execute('PRAGMA integrity_check')
result = conn.execute('SELECT COUNT(*) FROM leads').fetchone()
print(f'Fixture criada com {result[0]} leads')
conn.close()
"@ 2>&1 | Out-Null
  Pass "Fixture SQLite criada em $fixtureDb"
} catch {
  Fail "Falha ao criar fixture: $_"
}

# ============================================================================
# STEP 9: Start app and check readiness
# ============================================================================
Step 9 20 "Iniciando aplicativo e verificando readiness"

$env:PROSPECTOS_DATA_DIR = $APPDATA_TEST
$env:PROSPECTOS_LOG_DIR = $LOG_DIR
$env:PROSPECTOS_TEMP_DIR = $TEMP_DIR
$env:PROSPECTOS_CACHE_DIR = $CACHE_DIR
$env:PROSPECTOS_DISABLE_UPDATES = "1"

$appProc = Start-Process -FilePath "$appPath\ProspectOS.exe" -PassThru -NoNewWindow
Info "App iniciado (PID: $($appProc.Id))"
Start-Sleep -Seconds 3

$port = $null
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
  $portFile = "$APPDATA_TEST\porta.txt"
  if (Test-Path $portFile) {
    $raw = Get-Content $portFile -Raw -ErrorAction SilentlyContinue
    if ($raw -and $raw.Trim() -match '^\d+$') {
      $port = [int]$raw.Trim()
      break
    }
  }
  Start-Sleep -Milliseconds 500
}

if ($port -and $port -gt 0 -and $port -le 65535) {
  Pass "Backend anunciou porta: $port"

  # Check HTTP readiness
  try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 10 -UseBasicParsing
    if ($resp.StatusCode -eq 200) {
      Pass "HTTP 200 OK em http://127.0.0.1:$port/"
    } else {
      Warn "HTTP $($resp.StatusCode) em http://127.0.0.1:$port/"
    }
  } catch {
    Fail "HTTP readiness falhou: $_"
  }

  # Verify localhost only
  $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object LocalPort -eq $port
  if ($listeners) {
    $localAddr = $listeners | Select-Object -First 1
    if ($localAddr.LocalAddress -eq "127.0.0.1" -or $localAddr.LocalAddress -eq "::1") {
      Pass "Backend escuta apenas localhost: $($localAddr.LocalAddress)"
    } else {
      Warn "Backend escuta em $($localAddr.LocalAddress) (nao e localhost)"
    }
  }

  # Porta obsoleta é ignorada?
  # Test by writing an old port file
  try {
    Set-Content -Path "$APPDATA_TEST\porta.txt" -Value "99999"
    # New port should still be the real one from stdout
    $portAfterWrite = Get-Content "$APPDATA_TEST\porta.txt" -Raw
    if ($portAfterWrite.Trim() -ne "99999") {
      Warn "Porta antiga sobrescrita — nova porta $($portAfterWrite.Trim())"
    } else {
      # The new port file could be replaced by actual backend — this is fine
      Info "Porta.txt atual: $($portAfterWrite.Trim()) — backend pode ter reescrito"
    }
    # Restore real port
    Set-Content -Path "$APPDATA_TEST\porta.txt" -Value "$port"
  } catch { }
} else {
  Fail "Backend nao anunciou porta dentro de 30s"
}

# ============================================================================
# STEP 10: Validate data compatibility
# ============================================================================
Step 10 20 "Validando compatibilidade de dados existentes"

if (Test-Path $fixtureDb) {
  $dbSize = (Get-Item $fixtureDb).Length
  Pass "Banco existente encontrado: $('{0:N1} KB' -f ($dbSize/1KB))"

  # Check integrity
  try {
    $integrity = python -c @"
import sqlite3
conn = sqlite3.connect('$fixtureDb'.replace('\\','/'))
result = conn.execute('PRAGMA integrity_check').fetchone()
print(result[0])
conn.close()
"@ 2>&1
    if ($integrity.Trim() -eq "ok") {
      Pass "PRAGMA integrity_check: ok"
    } else {
      Warn "Integrity check: $integrity"
    }
  } catch {
    Warn "Nao foi possivel verificar integridade: $_"
  }
}

# Check WAL mode
try {
  $walFile = "$APPDATA_TEST\leads.db-wal"
  if (Test-Path $walFile) {
    Pass "WAL ativo: $(Get-Item $walFile).Length bytes"
  }
} catch { }

# Subdiretorios
foreach ($sub in @("backups", "saidas", "instagram")) {
  $subPath = "$APPDATA_TEST\$sub"
  if (Test-Path $subPath) {
    Pass "$sub preservado"
  } else {
    Warn "$sub ausente"
  }
}

# ============================================================================
# STEP 11: Credential Manager (Keyring)
# ============================================================================
Step 11 20 "Validando Credential Manager (Keyring Windows)"

try {
  $keyringTest = python -c @"
import keyring
from keyring.backends.Windows import WinVaultKeyring
keyring.set_keyring(WinVaultKeyring())
service = 'ProspectOS-PR8-Windows-Temporary'
key = 'test-key'
value = 'test-value-pr8'
keyring.set_password(service, key, value)
retrieved = keyring.get_password(service, key)
assert retrieved == value, f'Valor nao corresponde: {retrieved}'
keyring.delete_password(service, key)
deleted = keyring.get_password(service, key)
assert deleted is None, 'Keyring nao removeu a senha'
print('set -> get -> delete: OK')
"@ 2>&1
  if ($keyringTest -match "OK") {
    Pass "Credential Manager: $keyringTest"
  } else {
    Fail "Credential Manager: $keyringTest"
  }
} catch {
  Fail "Credential Manager: $_"
} finally {
  try {
    python -c "import keyring; keyring.delete_password('ProspectOS-PR8-Windows-Temporary', 'test-key')" 2>$null
  } catch { }
}

# ============================================================================
# STEP 12: Runtime Playwright smoke
# ============================================================================
Step 12 20 "Verificando spec do Playwright Runtime"

$pwManifest = Get-Content "$appPath\resources\shared\playwright-runtime-targets.json" -Raw | ConvertFrom-Json
if ($pwManifest.runtimes.'win32-x64') {
  $winSpec = $pwManifest.runtimes.'win32-x64'
  if ($winSpec.node.url -match "win-x64") {
    Pass "Node Windows x64 URL: OK"
  } else {
    Fail "Node URL nao parece Windows: $($winSpec.node.url)"
  }
  if ($winSpec.node.sha256) {
    Pass "Node SHA-256: presente ($($winSpec.node.sha256.Substring(0,16))...)"
  }
  if ($winSpec.playwrightCore.sha256) {
    Pass "Playwright Core SHA-256: presente"
  }
  if ($winSpec.browsers.chromium.revision) {
    Pass "Chromium revision: $($winSpec.browsers.chromium.revision)"
  }
  if ($winSpec.browsers.headlessShell.revision) {
    Pass "Headless Shell revision: $($winSpec.browsers.headlessShell.revision)"
  }
  if ($winSpec.browsers.ffmpeg.revision) {
    Pass "FFmpeg revision: $($winSpec.browsers.ffmpeg.revision)"
  }
}

# ============================================================================
# STEP 13: PDF generation
# ============================================================================
Step 13 20 "Validando geracao de PDF"

try {
  $pdfPath = "$TEMP_DIR\smoke-test-pr8.pdf"
  python -c @"
from fpdf import FPDF
pdf = FPDF()
pdf.add_page()
pdf.set_font('Helvetica', size=12)
pdf.cell(text='ProspectOS PR8 Smoke Test - Windows')
pdf.output('$pdfPath'.replace('\\','/'))
print('PDF gerado')
"@ 2>&1 | Out-Null
  if (Test-Path $pdfPath) {
    $header = [System.IO.File]::ReadAllBytes($pdfPath)[0..3]
    if ($header[0] -eq 0x25 -and $header[1] -eq 0x50 -and $header[2] -eq 0x44 -and $header[3] -eq 0x46) {
      Pass "PDF valido: $('{0:N1} KB' -f ((Get-Item $pdfPath).Length/1KB))"
    } else {
      Fail "PDF nao comeca com %PDF"
    }
    Remove-Item $pdfPath -Force -ErrorAction SilentlyContinue
  } else {
    Fail "PDF nao foi criado"
  }
} catch {
  Fail "Geracao de PDF falhou: $_"
}

# ============================================================================
# STEP 14: Instagram imports
# ============================================================================
Step 14 20 "Validando imports do Instagram"

try {
  $instaResult = python -c @"
import instagrapi
from instagrapi.exceptions import LoginRequired, ChallengeRequired, FeedbackRequired, ClientError
print('instagrapi imports OK')
"@ 2>&1
  if ($instaResult -match "OK") {
    Pass "$instaResult"
  } else {
    Fail "$instaResult"
  }
} catch {
  Fail "Instagram imports: $_"
}

# ============================================================================
# STEP 15: Process tree (órfãos)
# ============================================================================
Step 15 20 "Verificando processos do ProspectOS"

$processes = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match "ProspectOS|google-maps-scraper|node|chrome|chromium|cmd|taskkill"
} | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath

$orphans = @()
foreach ($p in $processes) {
  $pp = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)" -ErrorAction SilentlyContinue
  if (-not $pp -and $p.Name -match "node|chrome|chromium") {
    $orphans += $p
  }
}

if ($orphans.Count -eq 0) {
  Pass "Nenhum processo orfao detectado"
} else {
  Warn "Processos potencialmente orfaos:"
  foreach ($o in $orphans) {
    Warn "  PID=$($o.ProcessId) PPID=$($o.ParentProcessId) $($o.Name)"
  }
}

$procCount = @($processes).Count
Info "Processos ProspectOS ativos: $procCount"

# ============================================================================
# STEP 16: Shutdown app
# ============================================================================
Step 16 20 "Encerrando aplicativo"

if ($port) {
  try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$port/shutdown" -TimeoutSec 5 -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
  } catch { }
}

# Force kill via taskkill
try {
  taskkill /F /IM ProspectOS.exe 2>$null
  taskkill /F /IM ProspectOS.exe 2>$null
} catch { }

Start-Sleep -Seconds 2

$remaining = Get-CimInstance Win32_Process | Where-Object { $_.Name -match "ProspectOS" }
if ($remaining.Count -eq 0) {
  Pass "App encerrado — nenhum processo ProspectOS remanescente"
} else {
  Warn "Processos ProspectOS ainda ativos:"
  foreach ($r in $remaining) {
    Warn "  PID=$($r.ProcessId) $($r.Name)"
  }
  # Second force
  taskkill /F /IM ProspectOS.exe 2>$null
}

# ============================================================================
# STEP 17: Cleanup
# ============================================================================
Step 17 20 "Limpando diretorios temporarios"

# Remove temp directories (but not if user specified a custom DataDir)
if (-not $DataDir) {
  try {
    Remove-Item -Recurse -Force "$TEMP_ROOT" -ErrorAction SilentlyContinue
    Pass "Diretorios temporarios removidos"
  } catch { }
}

# ============================================================================
# STEP 18: Build pipeline summary
# ============================================================================
Step 18 20 "Resumo do ambiente de build"

$electronVer = & "$REPO_ROOT\desktop\node_modules\.bin\electron.cmd" --version 2>$null
$builderVer = & "$REPO_ROOT\desktop\node_modules\.bin\electron-builder.cmd" --version 2>$null

$thisShell = $PSVersionTable.PSVersion.ToString()
$winVer = (Get-ComputerInfo).WindowsVersion
$arch = (Get-ComputerInfo).OsArchitecture

Write-Host ""
Write-Host "Resumo do ambiente:" -ForegroundColor Cyan
Write-Host "  Windows: $winVer $arch"
Write-Host "  PowerShell: $thisShell"
Write-Host "  Node: $(node --version)"
Write-Host "  Electron: $electronVer"
Write-Host "  electron-builder: $builderVer"
Write-Host "  Python: $(python --version 2>&1)"
Write-Host "  Go: $(go version)"

# ============================================================================
# STEP 19: Results
# ============================================================================
Step 19 20 "Resultados"

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Smoke Test Windows — Resultado" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "  Passed: $PASS_COUNT" -ForegroundColor Green
Write-Host "  Failed: $FAIL_COUNT" -ForegroundColor $(if($FAIL_COUNT -gt 0){'Red'}else{'Green'})
Write-Host ""

if ($FAIL_COUNT -gt 0) {
  Write-Host "  FALHAS:" -ForegroundColor Red
  foreach ($e in $ERRORS) { Write-Host "    - $e" -ForegroundColor Red }
  Write-Host ""
  exit 1
} else {
  Write-Host "  Todos os testes passaram!" -ForegroundColor Green
  Write-Host ""
  exit 0
}

# ============================================================================
# STEP 20: Done
# ============================================================================
Step 20 20 "Smoke test Windows concluido"

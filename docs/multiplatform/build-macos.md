# Build macOS — ProspectOS.app

**Última atualização:** 2026-07-21 (PR 11)
**Plataforma:** macOS Apple Silicon (darwin-arm64)
**Validado em:** MacBook Apple M4 Pro, macOS 26.4, Python 3.14.5, Node 22.22.2

---

## Índice

- [1. Requisitos](#1-requisitos)
- [2. Comando único de build](#2-comando-único-de-build)
- [3. Dependências passo a passo](#3-dependências-passo-a-passo)
- [4. Pipeline de build](#4-pipeline-de-build)
- [5. Smoke automatizado](#5-smoke-automatizado)
- [6. Estrutura do `.app`](#6-estrutura-do-app)
- [7. Arquiteturas](#7-arquiteturas)
- [8. Paths](#8-paths)
- [9. Health](#9-health)
- [10. Diagnóstico](#10-diagnóstico)
- [11. Keychain](#11-keychain)
- [12. Playwright Runtime](#12-playwright-runtime)
- [13. Scraper](#13-scraper)
- [14. Lifecycle macOS](#14-lifecycle-macos)
- [15. Limitações](#15-limitações)
- [16. Troubleshooting](#16-troubleshooting)

---

## 1. Requisitos

### Hardware

- Mac com Apple Silicon (M1/M2/M3/M4)
- 8 GB RAM mínimo (16 GB recomendado)
- 5 GB de espaço livre em disco

### Software

| Ferramenta | Versão mínima | Verificar |
|---|---|---|
| macOS | 14.0 (Sonoma) | `sw_vers` |
| Xcode CLI | Última | `xcrun --show-sdk-path` |
| Python | 3.13+ | `python3 --version` |
| Node.js | 20+ | `node --version` |
| npm | 10+ | `npm --version` |
| Go | 1.22+ | `go version` |

### Arquitetura

```bash
arch
# → arm64
```

Build nativo requer arm64. Rosetta não é suportado.

---

## 2. Comando único de build

```bash
python scripts/build_desktop.py
```

Este comando executa o pipeline completo de 8 etapas:

1. Valida ambiente (arm64, PyInstaller, Go, Node)
2. Frontend React (`npm ci && npm run build`)
3. Backend PyInstaller (`--onedir`, arm64)
4. Scraper Go (clona tag fixa v1.16.3, compila arm64)
5. Staging de recursos (manifests, licenças)
6. Valida staging
7. Electron (electron-builder dir arm64)
8. Valida `.app` (estrutura, arquiteturas, permissões)

Flags:

```bash
--clean              # limpa outputs antes de buildar
--skip-frontend      # usa frontend/dist existente
--skip-scraper       # usa scraper existente no staging
--target darwin-arm64 # força target (auto-detectado em arm64)
```

### Saída

```
desktop/saida/mac-arm64/ProspectOS.app
```

---

## 3. Dependências passo a passo

### Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt
pip install 'pyinstaller>=6.21,<7'
pip check
```

PyInstaller é dependência de desenvolvimento (não está em `requirements.txt`).

### Frontend

```bash
cd frontend && npm ci
```

### Desktop (Electron)

```bash
cd desktop && npm ci
```

---

## 4. Pipeline de build

Cada etapa pode ser executada individualmente:

```bash
# 1. Frontend
cd frontend && npm ci && npm run build

# 2. Backend PyInstaller
python scripts/build_backend.py --clean

# 3. Scraper Go
python -c "
import subprocess, json
sources = json.loads(open('scripts/native-artifact-sources.json').read())
scraper = sources['scraper']
subprocess.run(['git', 'clone', f\"https://github.com/{scraper['upstream']}.git\", '.tmp-scraper', '--depth', '1', '--branch', scraper['tag']], check=True)
subprocess.run(['go', 'build', '-trimpath', '-o', '/tmp/google-maps-scraper', '.'], cwd='.tmp-scraper', env={**__import__('os').environ, 'GOOS': 'darwin', 'GOARCH': 'arm64', 'CGO_ENABLED': '0'}, check=True)
"

# 4. Staging
python -c "
import shutil, subprocess, sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
STAGING = REPO_ROOT / 'desktop' / '.runtime-resources' / 'darwin-arm64'
STAGING.mkdir(parents=True, exist_ok=True)
shutil.copytree(REPO_ROOT / 'backend' / 'dist' / 'ProspectOS', STAGING / 'backend', dirs_exist_ok=True)
shutil.copy('/tmp/google-maps-scraper', STAGING / 'scraper' / 'google-maps-scraper')
Path(STAGING / 'scraper' / 'google-maps-scraper').chmod(0o755)
shutil.copy2(REPO_ROOT / 'shared' / 'runtime-targets.json', STAGING / 'shared' / 'runtime-targets.json')
shutil.copy2(REPO_ROOT / 'shared' / 'playwright-runtime-targets.json', STAGING / 'shared' / 'playwright-runtime-targets.json')
"

# 5. Electron
cd desktop && CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder --config electron-builder.yml --mac dir --arm64 --publish never
```

### Duração esperada

| Etapa | Duração |
|---|---|
| Frontend | ~30s |
| Backend PyInstaller | ~40s |
| Scraper Go | ~40s (download + compile) |
| Electron | ~30s |
| **Total** | **~2 min** |

---

## 5. Smoke automatizado

### Backend standalone (sem Electron)

```bash
scripts/smoke_macos_local.py --backend-only
```

Ou manualmente:

```bash
SMOKE=/tmp/prospectos-smoke
rm -rf "$SMOKE"
mkdir -p "$SMOKE/data" "$SMOKE/logs" "$SMOKE/cache" "$SMOKE/temp"

PROSPECTOS_DATA_DIR="$SMOKE/data" \
PROSPECTOS_LOG_DIR="$SMOKE/logs" \
PROSPECTOS_CACHE_DIR="$SMOKE/cache" \
PROSPECTOS_TEMP_DIR="$SMOKE/temp" \
PROSPECTOS_RUNTIME_TARGET=darwin-arm64 \
PROSPECTOS_NO_BROWSER=1 \
backend/dist/ProspectOS/ProspectOS &
PID=$!

# Aguarda startup
for i in $(seq 1 10); do
  PORT_FILE="$SMOKE/data/porta.txt"
  if [ -f "$PORT_FILE" ]; then
    PORT=$(cat "$PORT_FILE")
    break
  fi
  sleep 1
done

# Health check
curl -s "http://127.0.0.1:$PORT/api/health" | python3 -m json.tool

# Encerra
kill $PID 2>/dev/null; wait $PID 2>/dev/null
```

### .app completo

```bash
scripts/smoke_macos_local.py
```

O smoke valida:

- Estrutura do `.app`
- Arquiteturas (Electron, backend, scraper)
- Permissões
- Manifests
- Startup e health
- Banco SQLite e WAL
- Logs estruturados
- Keychain
- Diagnóstico ZIP
- Isolamento (PATH reduzido)
- Processos órfãos

---

## 6. Estrutura do `.app`

```
ProspectOS.app/
└── Contents/
    ├── Info.plist
    ├── MacOS/
    │   └── ProspectOS              # Electron arm64
    ├── Frameworks/
    │   ├── Electron Framework.framework/
    │   ├── ProspectOS Helper.app/
    │   ├── ProspectOS Helper (GPU).app/
    │   ├── ProspectOS Helper (Renderer).app/
    │   ├── ProspectOS Helper (Plugin).app/
    │   ├── Mantle.framework/
    │   └── ReactiveObjC.framework/
    └── Resources/
        ├── app.asar                 # Electron app code
        ├── icon.icns                # Ícone do aplicativo
        ├── backend/
        │   ├── ProspectOS           # Backend PyInstaller arm64
        │   └── _internal/           # Dependências Python empacotadas
        ├── scraper/
        │   ├── google-maps-scraper  # Scraper Go arm64
        │   └── LICENSE              # MIT (scraper)
        └── shared/
            ├── runtime-targets.json
            └── playwright-runtime-targets.json
```

### Tamanhos de referência

| Componente | Tamanho |
|---|---|
| Electron | ~100 MB |
| Backend _internal | ~114 MB |
| Backend executável | ~18 MB |
| Scraper | ~59 MB |
| app.asar | ~2 MB |
| **Total** | **~408 MB** |

---

## 7. Arquiteturas

| Componente | Arquitetura | Verificação |
|---|---|---|
| Electron | `arm64` | `lipo -info Contents/MacOS/ProspectOS` |
| Backend | `arm64` | `lipo -info Contents/Resources/backend/ProspectOS` |
| Scraper | `arm64` | `lipo -info Contents/Resources/scraper/google-maps-scraper` |
| Node (runtime) | `arm64` | Gerenciado pelo PlaywrightRuntimeManager |
| Chromium | `arm64` | Gerenciado pelo PlaywrightRuntimeManager |

```bash
APP=desktop/saida/mac-arm64/ProspectOS.app
for f in \
  "$APP/Contents/MacOS/ProspectOS" \
  "$APP/Contents/Resources/backend/ProspectOS" \
  "$APP/Contents/Resources/scraper/google-maps-scraper"; do
  echo "$f: $(file "$f" | grep -o 'arm64\|x86_64')"
done
```

---

## 8. Paths

| Finalidade | Padrão macOS | Env var |
|---|---|---|
| Dados | `~/Library/Application Support/ProspectOS` | `PROSPECTOS_DATA_DIR` |
| Logs | `~/Library/Logs/ProspectOS` | `PROSPECTOS_LOG_DIR` |
| Cache | `~/Library/Caches/ProspectOS` | `PROSPECTOS_CACHE_DIR` |
| Temp | `$TMPDIR/ProspectOS` | `PROSPECTOS_TEMP_DIR` |
| Resources | `ProspectOS.app/Contents/Resources` | `PROSPECTOS_RESOURCE_DIR` |

### Precedência

1. Env var `PROSPECTOS_*` (passada ao backend pelo Electron)
2. Electron `app.getPath()` (modo empacotado)
3. Fallback nativo do macOS

### Inside the .app

```bash
APP=desktop/saida/mac-arm64/ProspectOS.app
test -d "$APP/Contents/Resources/backend/_internal"  # Dependências Python
test -d "$APP/Contents/Resources/scraper"             # Scraper Go
test -f "$APP/Contents/Resources/shared/runtime-targets.json"  # Manifesto
```

---

## 9. Health

Endpoint: `GET /api/health`

### Exemplo de resposta

```json
{
  "service": "prospectos-backend",
  "status": "ok",
  "version": "2.0.0",
  "runtimeTarget": "darwin-arm64",
  "timestamp": "2026-07-21T23:49:15Z",
  "checks": {
    "database": {
      "status": "ok",
      "journalMode": "wal",
      "reachable": true
    },
    "filesystem": {
      "status": "ok"
    },
    "jobs": {
      "status": "ok",
      "running": 0
    },
    "keychain": {
      "status": "ok",
      "backend": "Keyring"
    },
    "manifest": {
      "status": "ok"
    },
    "playwrightRuntime": {
      "status": "ok"
    },
    "scraper": {
      "status": "ok",
      "state": "idle",
      "available": true
    }
  }
}
```

### Status possíveis

| Status | Significado | HTTP |
|---|---|---|
| `ok` | Todos os checks saudáveis | 200 |
| `degraded` | Subsistema não crítico falhou (ex: runtime não instalado) | 200 |
| `unhealthy` | Subsistema crítico falhou | 503 |
| `starting` | Backend ainda inicializando | 503 |

### Health sem side effects

O health check **não**:

- Instala Playwright Runtime
- Baixa arquivos
- Inicia scraper
- Altera banco
- Toca no Keychain
- Gera PDF
- Cria jobs
- Usa rede externa

---

## 10. Diagnóstico

### Exportar ZIP de diagnóstico

```bash
curl -s "http://127.0.0.1:$PORT/api/diagnostics/export" -o /tmp/diagnostics.zip
```

### Conteúdo do ZIP

```
diagnostics.json          # Relatório estruturado completo (sanitizado)
health.json               # Snapshot do health
manifest-summary.json     # Resumo do runtime manifest
logs/                     # Últimos logs (truncados)
README.txt                # Instruções de uso
```

### Sanitização

O diagnóstico automaticamente remove:

- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`
- Qualquer valor contendo `password`, `authorization`, `cookie`, `session`, `JWT`
- Credenciais de proxy

Também exclui do ZIP:

- Banco de dados (`*.db`, `*.db-wal`, `*.db-shm`)
- CSV
- PDF
- Sessões
- Executáveis
- Runtime Playwright
- Cache completo

### Limites

| Item | Limite |
|---|---|
| Tamanho máximo por arquivo de log | 5 MB |
| Tamanho máximo total do ZIP | 20 MB |
| Truncamento explícito no JSON | logs restritos aos últimos 100 eventos |

---

## 11. Keychain

O backend usa `keyring` para acessar o Keychain do macOS.

### Verificação

```bash
# No bundle
PROSPECTOS_DATA_DIR=/tmp/keyring-test/data \
PROSPECTOS_LOG_DIR=/tmp/keyring-test/logs \
PROSPECTOS_CACHE_DIR=/tmp/keyring-test/cache \
PROSPECTOS_TEMP_DIR=/tmp/keyring-test/temp \
PROSPECTOS_RUNTIME_TARGET=darwin-arm64 \
PROSPECTOS_NO_BROWSER=1 \
backend/dist/ProspectOS/ProspectOS &
PID=$!
# Aguarda e checa health — keychain status deve ser "ok"
kill $PID 2>/dev/null
```

### Service name

```python
import keyring
keyring.set_service("ProspectOS-PR11-Local-Smoke")
keyring.set_password("ProspectOS-PR11-Local-Smoke", "test-key", "test-value")
assert keyring.get_password("ProspectOS-PR11-Local-Smoke", "test-key") == "test-value"
keyring.delete_password("ProspectOS-PR11-Local-Smoke", "test-key")
```

### Backend selecionado

macOS usa `keyring.backends.macOS` (acesso ao Keychain nativo).

---

## 12. Playwright Runtime

### Fluxo de instalação

```
1. NOT_INSTALLED → downloads Node.js + playwright-core
2. Valida checksums SHA-256
3. Extrai e staged
4. Instala navegadores (Chromium)
5. Valida binários
6. READY
```

### Cache

```
~/Library/Caches/ProspectOS/playwright/installations/darwin-arm64/
├── node/                          # Node.js gerenciado
├── pw-1.60.0-chromium-1223/       # Playwright core versionada
│   ├── node                       # Node do runtime
│   ├── node.exe                   # (Windows)
│   └── driver/
└── browsers/
    ├── chromium-1223/             # Chromium arm64 (~350 MB)
    ├── headless_shell-1223/       # Headless Shell arm64
    └── ffmpeg/
```

### Estados

| Estado | Significado |
|---|---|
| `NOT_INSTALLED` | Runtime não instalado |
| `DOWNLOADING` | Baixando componentes |
| `EXTRACTING` | Extraindo arquivos |
| `INSTALLING` | Instalando navegadores |
| `VALIDATING` | Verificando integridade |
| `READY` | Pronto para uso |
| `ERROR` | Falha na instalação |
| `UNSUPPORTED` | Target não suportado |

### Verificação

```python
from playwright_runtime.manager import PlaywrightRuntimeManager
mgr = PlaywrightRuntimeManager(cache_dir="/tmp/cache", target="darwin-arm64")
state, _ = mgr.ensure_ready()
print(state)  # RuntimeState.READY
```

---

## 13. Scraper

O scraper Google Maps é compilado a partir de fonte fixa:

| Campo | Valor |
|---|---|
| Upstream | `gosom/google-maps-scraper` |
| Tag | `v1.16.3` |
| Commit | `25751bf24ea292792b88100aab5e9b6c794448e3` |
| Arquitetura | `arm64` |
| SHA-256 (build PR 11) | `44eba2b1acfac4602696ccafe86aeebae3ea99e2dea9d377712c8529d8619b19` |

### Execução

O scraper é executado pelo `ScraperProcessRunner` que:

1. Lê progresso do **stderr** (JSON Lines)
2. Lê resultados do **stdout** (CSV)
3. Aplica timeout configurável
4. Suporta cancelamento via SIGTERM → SIGKILL

---

## 14. Lifecycle macOS

### Comportamento implementado

| Ação | Comportamento | Código |
|---|---|---|
| Fechar janela | App permanece no Dock | `main.js:202` |
| Clicar no Dock | Recria janela | `main.js:208` |
| Segunda instância | Foca a existente | `main.js:27,32` |
| `Command+Q` | Encerra backend + scraps | `main.js:223` |
| Crash do backend | MessageBox + encerra | `main.js:155` |

### Encerramento

```
before-quit
→ event.preventDefault()
→ backendSupervisor.stop() (SIGTERM → 10s grace → SIGKILL)
→ app.quit()
```

### Verificação de órfãos

```bash
ps -eo pid,ppid,command | grep -E "ProspectOS|google-maps-scraper|playwright|Chromium" | grep -v grep
# Esperado: 0 processos
```

---

## 15. Limitações

### Conhecidas

| Limitação | Impacto | Plano |
|---|---|---|
| Porta 5000 conflita com AirPlay Receiver | Backend usa fallback para porta aleatória | Configurar porta fixa via env |
| `.app` smoke requer GUI | Lifecycle e Playwright não testáveis em CI headless | Smoke manual no checklist |
| Playwright Runtime não validado no `.app` | Primeiro download ~938 MB (Node + Chromium) | Smoke manual |
| Busca real Google Maps não validada | Requer Playwright instalado | Smoke manual |
| Apenas arm64 | Mac Intel não suportado | Fora de escopo |
| Sem Developer ID / notarização | `.app` só roda no Mac do desenvolvedor | Fora de escopo |
| Sem auto-update macOS | Atualizações manuais via Git | Fora de escopo |

### Fora de escopo (não implementar)

- Developer ID e notarização
- DMG público
- GitHub Release macOS
- Auto-update macOS
- Mac App Store
- Mac Intel
- Linux
- Lightpanda
- Migração para web/Next.js

---

## 16. Troubleshooting

### PyInstaller falha com `arm64` não detectado

```bash
# Verificar arquitetura
arch  # deve retornar arm64

# Se não, abrir terminal nativo (não Rosetta)
env /usr/bin/arch -arm64 /bin/zsh
```

### `pip install` falha com `ResolutionImpossible`

```bash
# Verificar versão do requests
pip install requests==2.34.2
pip check
```

### Porta 5000 ocupada

```bash
# Verificar o que está usando
lsof -i :5000
# AirPlay Receiver (ControlCe) — comum no macOS

# O backend detecta automaticamente e escolhe outra porta
# Procure por LISTENING_ON=<porta> no stdout ou
# cat ~/Library/Application\ Support/ProspectOS/porta.txt
```

### `.app` não abre

```bash
# Tentar abrir pelo terminal para ver erros
open /path/to/ProspectOS.app

# Verificar logs
cat ~/Library/Logs/ProspectOS/prospeccao.log

# Verificar se o backend está executando
ps -eo pid,command | grep ProspectOS
```

### Dependências Homebrew no bundle

```bash
# Verificar dependências dinâmicas
otool -L backend/dist/ProspectOS/ProspectOS | grep -E "/opt/homebrew|/usr/local"
# Se encontrar, o PyInstaller não isolou corretamente
```

### Processos órfãos

```bash
# Listar processos ProspectOS
ps -eo pid,ppid,command | grep -E "ProspectOS|google-maps-scraper" | grep -v grep

# Matar todos
pkill -9 -f ProspectOS
```

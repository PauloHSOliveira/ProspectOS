# Auditoria Técnica: ProspectOS → Multiplataforma (macOS Apple Silicon)

> **Data:** 2026-07-20
> **Ambiente:** MacBook Apple M4 (arm64) — macOS 26.4
> **Branch:** `main` @ `bebc2f1be6b1b8617ecee802cd3132f9647449b2`
> **Propósito:** Identificar todas as melhorias necessárias para que o ProspectOS rode nativamente em macOS Apple Silicon, Windows x64 e Linux x64, preservando dados de usuários Windows existentes.

---

# 1. Executive verdict

| Pergunta | Resposta |
| -------- | -------- |
| Preservar Electron? | **Sim** — sem impedimento técnico |
| Preservar React/Flask/SQLite? | **Sim** — zero dependência de Windows |
| Funcionar no M4 sem reescrita? | **Não** — mas com refatoração cirúrgica sim |
| Principal bloqueador | **Scraper + Node: caminhos `.exe` hardcoded e fallback `C:\`** |
| 2º principal bloqueador | **`keyring.backends.Windows` + ausência de backend macOS Keychain** |
| Esforço estimado | **2–3 sprints (5–7 dias úteis)** para M4 funcional |
| Confiança | **Alta (~85%)** — o código Python/React é limpo e portável |

---

# 2. Ambiente verificado

| Item | Valor |
| ---- | ----- |
| **OS** | macOS 26.4 (Darwin 25.4.0) |
| **Arquitetura** | arm64 (Apple M4) |
| **Branch** | `main` |
| **HEAD** | `bebc2f1be6b1b8617ecee802cd3132f9647449b2` |
| **Working tree** | limpa |
| **Node** | v22.22.2 |
| **npm** | 10.9.7 |
| **Electron** | 38.2.0 |
| **electron-builder** | 26.0.12 |
| **Python** | 3.14.5 |
| **PyInstaller** | **não instalado** |
| **Go** | **não instalado** |
| **Xcode CLI** | presente |
| **Rosetta** | não instalado |

---

# 3. Arquitetura real

```
Electron (main.js)
└── subprocess: backend Python (PyInstaller bundle)
    ├── Flask + Waitress (WSGI, 127.0.0.1:porta)
    ├── SQLite (leads.db em DIR_DADOS)
    ├── jobs (threads daemon)
    │   ├── scraper (subprocess: google-maps-scraper)
    │   │   └── Playwright (Chromium via Node)
    │   └── Instagram (instagrapi direto, sem subprocess)
    ├── React (build estático servido pelo Flask)
    ├── PDF (fpdf2, síncrono nas rotas)
    └── keyring (credenciais do sistema)
```

## Quem inicia o quê

| Processo | Iniciado por | Args | Encerramento |
|----------|-------------|------|-------------|
| Backend Python | `main.js:65` `spawn(exe, [], {env, windowsHide})` | nenhum | `backend.kill()` no `window-all-closed` / `before-quit` |
| Scraper | `jobs.py:233` `subprocess.Popen(comando, ...)` | `-input -results -lang pt -depth 5 -exit-on-inactivity 3m` (+ `-geo -radius -zoom` no modo mapa) | `processo.kill()` no timeout; `processo.wait()` normal |
| Playwright/Chromium | Iniciado internamente pelo scraper Go | — | gerenciado pelo scraper |

---

# 4. Bloqueadores do Mac M4

| # | Prio | Bloqueador | Evidência | Impacto | Correção | Esforço |
| - | ---- | ---------- | --------- | ------- | -------- | ------- |
| 1 | **P0** | `.exe` hardcoded + fallback `C:\Program Files\nodejs\node.exe` | `jobs.py:315,436,442`; `main.js:39,42`; `prospectos.spec:31,36` | App não acha scraper nem Node | `PlatformPaths` + `RuntimeManifest` | 4h |
| 2 | **P0** | `process.env.APPDATA` | `main.js:46`, `paths.py:33` | `DIR_DADOS` vira path errado no macOS | `app.getPath('userData')` + `PROSPECTOS_DATA_DIR` | 2h |
| 3 | **P0** | `windowsHide: true` (opção inválida no macOS) | `main.js:68` | Ignorado silenciosamente, mas não idiomático | Remover; ajustar para POSIX | 30min |
| 4 | **P0** | `keyring.backends.Windows` no spec + sem backend macOS | `prospectos.spec:45`; `db.py:41-57` | Chaves de API não persistem no Keychain | Adicionar `keyring.backends.macOS` | 2h |
| 5 | **P0** | Build Electron só tem target `win` | `electron-builder.yml:28-35`; `package.json:12-13`; `afterPack.js` usa `rcedit` | Build quebra no macOS | Adicionar `mac` target; `.icns`; condicionar `afterPack` | 4h |
| 6 | **P0** | PyInstaller spec com nomes `.exe` + hiddenimport Windows-only | `prospectos.spec:31,36,45` | Build PyInstaller no macOS quebra | Parametrizar por plataforma | 2h |
| 7 | **P1** | Frontend docs citam `google-maps-scraper.exe` | `FaqDoc.tsx:16`, `GoogleMapsDoc.tsx:27`, `InstalacaoDoc.tsx:21,43` | Usuário vê texto incorreto | Atualizar para `RuntimeManifest` | 1h |
| 8 | **P1** | `Ctrl` vs `CommandOrControl` não gerenciado | Nenhum atalho Electron configurado | Baixo — frontend React usa teclado comum | Verificar no futuro | — |
| 9 | **P2** | `iniciar.bat` dev workflow (Windows-only) | `app.py:7,97,157`; `iniciar.bat` | Dev no macOS sem equivalente | Criar `iniciar.sh` ou script npm | 1h |
| 10 | **P2** | `buscar.ps1` dev scraper runner (Windows-only) | `buscar.ps1` | Dev no macOS sem equivalente | Migrar para script Python ou npm | 1h |
| 11 | **P2** | Instalador Inno Setup só Windows | `prospectos.iss` | Distribuição macOS sem DMG | electron-builder `mac` target + DMG | incluso no #5 |
| 12 | **P2** | Ícone `.ico` sem `.icns` | `main.js:128`; `electron-builder.yml:12` | Ícone não aparece no Dock | Adicionar `build/icon.icns` | 30min |
| 13 | **P3** | Testes só mockam `APPDATA` (Windows) | `test_paths.py:63` | Cobertura macOS insuficiente | Adicionar teste macOS paths | 1h |

---

# 5. Resultado da auditoria do scraper

**Projeto upstream:** [`gosom/google-maps-scraper`](https://github.com/gosom/google-maps-scraper)

| Item | Resposta |
| ---- | -------- |
| Linguagem | **Go** (1.26+) |
| Automação | **Playwright** (Chromium) — baixa browser em runtime |
| Saída | CSV ou JSON (NDJSON de progresso + CSV final) |
| Binários pré-compilados | Windows amd64, Linux amd64/arm64, **macOS amd64 + arm64** |
| Binary name | `google_maps_scraper-<version>-<os>-<arch>` (sem `.exe` no macOS/Linux) |
| Uso no ProspectOS | binário Windows baixado manualmente, renomeado para `google-maps-scraper.exe` |
| Código-fonte no repo | **Não** — só o binário referenciado |
| Customizações | **Não** — usado vanilla via CLI |
| Licença | MIT |
| Contrato CSV | `place_id`, `title`, `category`, `address`, `phone`, `website`, `review_rating`, `review_count`, `input_id` |
| Progresso | JSON no stdout: `{"message": "X places found"}`, `{"message": "job finished"}` |
| Códigos de saída | 0 = sucesso, != 0 = falha |
| Timeout interno | `-exit-on-inactivity 3m` |

## Cenário A — Nativo e diretamente portável ✅

O scraper já suporta `darwin-arm64` oficialmente. O ProspectOS só precisa:

1. Baixar o binário correto para `darwin-arm64`
2. Renomeá-lo sem `.exe`
3. Ajustar o `RuntimeManifest` para apontar ao binário correto por plataforma
4. Garantir que o Playwright baixe o Chromium para arm64 (automático na primeira execução)

---

# 6. Compatibilidade das dependências

## Electron/Node

| Dependência | Versão | arm64 | Risco | Ação |
| ----------- | ------ | ----- | ----- | ---- |
| Electron | 38.2.0 | ✅ nativo | Nenhum | Já suporta arm64 |
| electron-builder | 26.0.12 | ✅ | Nenhum | Adicionar `mac` target |
| electron-updater | 6.6.2 | ✅ | P2 | Auto-update via GitHub Releases (cross-platform) |
| rcedit | ^5.0.2 | ❌ Win-only | **P0** | Condicionar `afterPack.js` |

## Python

| Dependência | Versão | arm64 | PyInstaller | Risco | Ação |
| ----------- | ------ | ----- | ----------- | ----- | ---- |
| Flask | 3.1.3 | ✅ wheel | ✅ | Nenhum | Manter |
| waitress | 3.0.2 | ✅ wheel | ✅ | Nenhum | Manter |
| google-genai | 2.10.0 | ✅ wheel | ⚠️ provável | Baixo | Testar |
| openai | 2.44.0 | ✅ wheel | ✅ | Nenhum | Manter |
| python-dotenv | 1.2.2 | ✅ wheel | ✅ | Nenhum | Manter |
| keyring | 25.6.0 | ✅ wheel | ⚠️ hiddenimport | Médio | Adicionar `keyring.backends.macOS` |
| fpdf2 | 2.8.7 | ✅ wheel | ✅ | Nenhum | Manter |
| ddgs | 9.14.4 | ✅ pure | ✅ | Nenhum | Manter |
| requests | 2.32.3 | ✅ wheel | ✅ | Nenhum | Manter |
| pytest | 9.1.1 | ✅ wheel | ❌ excluído | Nenhum | Já excluído |
| instagrapi | 2.18.3 | ✅ wheel | ⚠️ hiddenimport | Baixo | Já listado |
| PyInstaller | — | ✅ nativo | — | Nenhum | Instalar |

> **keyring no macOS:** O backend padrão do `keyring` no macOS usa o Keychain nativo via `keyring.backends.macOS.Keyring`. Detectado automaticamente em runtime. Só precisa do hiddenimport correto.

> **PyInstaller no macOS arm64:** PyInstaller 6.x+ suporta nativamente `darwin-arm64`. Gera bundle `.app` ou `--onedir` que funciona em M1/M2/M3/M4.

## Go (scraper)

- Go 1.26+ suporta `darwin/arm64` nativamente
- Binário pré-compilado disponível no upstream
- **Sem Go instalado necessário** — baixa-se o binário pré-compilado

## Browser automation

- Playwright baixa Chromium apropriado para a arquitetura na primeira execução
- Node.js necessário para o Playwright interno do scraper
- `PLAYWRIGHT_NODEJS_PATH` já é configurável

---

# 7. Suposições Windows encontradas

| Arquivo | Ocorrência | Classificação | Mudança necessária |
| ------- | ---------- | ------------- | ------------------ |
| `desktop/main.js:39` | `path.join(process.resourcesPath, "backend", "ProspectOS.exe")` | **P0** | `RuntimeManifest.get_backend_name()` |
| `desktop/main.js:42` | `path.join(__dirname, "..", "backend", "dist", "ProspectOS", "ProspectOS.exe")` | **P0** | Idem |
| `desktop/main.js:46` | `process.env.APPDATA` | **P0** | `app.getPath('userData')` |
| `desktop/main.js:68` | `windowsHide: true` | P1 | Remover (POSIX ignora) |
| `desktop/main.js:165` | `%APPDATA%\\ProspectOS\\logs\\prospeccao.log` | P1 | Mensagem dinâmica por plataforma |
| `desktop/electron-builder.yml` | Só target `win` + `.ico` | **P0** | Adicionar `mac` target + `.icns` |
| `desktop/package.json:12-13` | `--win` flag nos scripts | **P0** | `npm run dist` sem flag; builder decide |
| `desktop/afterPack.js` | `rcedit` (Windows-only) | **P0** | Condicional `if (process.platform === 'win32')` |
| `backend/paths.py:33` | `os.environ.get("APPDATA", str(Path.home()))` | **P0** | Receber via env var `PROSPECTOS_DATA_DIR` |
| `backend/paths.py` (docstring) | `%APPDATA%\\ProspectOS` | P2 | Atualizar docstring |
| `backend/jobs.py:315` | `caminho_recurso("google-maps-scraper.exe")` | **P0** | `RuntimeManifest.scraper_name()` |
| `backend/jobs.py:436` | `caminho_recurso("node", "node.exe")` | **P0** | `RuntimeManifest.node_name()` |
| `backend/jobs.py:442` | `r"C:\Program Files\nodejs\node.exe"` | **P0** | Remover fallback Windows; usar `which node` ou Node portátil |
| `backend/prospectos.spec:31` | `google-maps-scraper.exe` | **P0** | Nome do binário por plataforma |
| `backend/prospectos.spec:36` | `node.exe` | **P0** | Nome do binário por plataforma |
| `backend/prospectos.spec:45` | `keyring.backends.Windows` | **P0** | Adicionar `keyring.backends.macOS` |
| `backend/db.py:35` | comentário "Windows Credential Manager, via keyring/DPAPI" | P3 | Atualizar comentário |
| `backend/app.py:7,97,157` | menções a `iniciar.bat` | P2 | Mensagens dinâmicas |
| `backend/LEIA-ME.md` | `py`, `Ctrl+C`, `PowerShell` (Windows) | P2 | Adicionar instruções macOS |
| `backend/instagram/raspar_comentarios.py:58,63` | `py instagram\\login.py` (backslash) | P2 | Usar `Path()` ou forward-slash |
| `backend/instagram/enriquecer_perfis.py:160,165` | `py instagram\\login.py` (backslash) | P2 | Idem |
| `iniciar.bat` | Script batch completo | **P0** | Criar `iniciar.sh` |
| `backend/buscar.ps1` | PowerShell script | P2 | Migrar para Python ou npm script |
| `instalador/prospectos.iss` | Inno Setup (Windows-only) | P2 | Substituir por electron-builder DMG |
| Frontend docs | Múltiplas referências a `.exe` e Windows | P1 | Texto dinâmico por plataforma |

---

# 8. Plano mínimo para M4

O menor plano que entrega: **build → abrir → backend funcionar → frontend carregar → scraper funcionar → banco persistir → PDF funcionar → app encerrar corretamente**.

```
1. PlatformPaths
   └── Electron passa PROSPECTOS_DATA_DIR ao backend
   └── backend usa env var em vez de APPDATA

2. RuntimeManifest
   └── nomes de binário por plataforma (sem .exe)

3. Atualizar electron-builder.yml
   └── mac target + icns

4. Baixar artefatos darwin-arm64
   └── scraper (gosom/google-maps-scraper)
   └── Node portátil (nodejs.org)

5. Atualizar PyInstaller spec
   └── hiddenimports + datas por plataforma

6. Condicionar afterPack.js
   └── só roda em Windows (process.platform === 'win32')

7. Build PyInstaller no macOS arm64
8. Build Electron macOS arm64
9. Smoke test: app abre, backend sobe, frontend carrega
```

---

# 9. Arquitetura multiplataforma recomendada

## PlatformPaths

```
Electron (main.js)
  ├── app.getPath('userData') → ~/Library/Application Support/ProspectOS
  ├── app.getPath('logs')     → ~/Library/Logs/ProspectOS
  ├── app.getPath('cache')    → ~/Library/Caches/ProspectOS
  └── process.resourcesPath   → dentro do .app

  ↓ passa como env vars ao backend

PROSPECTOS_DATA_DIR
PROSPECTOS_LOG_DIR
PROSPECTOS_CACHE_DIR
PROSPECTOS_TEMP_DIR
PROSPECTOS_RESOURCE_DIR
```

O backend **não** lê `APPDATA`. Recebe tudo do Electron ou usa defaults POSIX.

**Paths nativos:**

| Finalidade | macOS | Windows | Linux |
| ---------- | ----- | ------- | ----- |
| Dados | `~/Library/Application Support/ProspectOS` | `%APPDATA%\ProspectOS` | `$XDG_DATA_HOME/ProspectOS` |
| Logs | `~/Library/Logs/ProspectOS` | `%APPDATA%\ProspectOS\logs` | `$XDG_DATA_HOME/ProspectOS/logs` |
| Cache | `~/Library/Caches/ProspectOS` | `%LOCALAPPDATA%\ProspectOS\cache` | `$XDG_CACHE_HOME/ProspectOS` |
| Temp | `$TMPDIR/ProspectOS` | `%TMP%\ProspectOS` | `/tmp/ProspectOS` |

## RuntimeManifest

```python
MANIFEST = {
    "darwin-arm64": {
        "backend": "ProspectOS",
        "scraper": "google-maps-scraper",
        "node": "node",
    },
    "darwin-x64": {
        "backend": "ProspectOS",
        "scraper": "google-maps-scraper",
        "node": "node",
    },
    "win32-x64": {
        "backend": "ProspectOS.exe",
        "scraper": "google-maps-scraper.exe",
        "node": "node.exe",
    },
    "linux-x64": {
        "backend": "ProspectOS",
        "scraper": "google-maps-scraper",
        "node": "node",
    },
}
```

Nunca concatenar `.exe` diretamente.

## ProcessSupervisor

Interface capaz de:

- Iniciar processos
- Registrar PID
- Aguardar readiness
- Enviar encerramento gracioso
- Encerrar árvore de processos
- Aplicar timeout
- Capturar stdout/stderr
- Gerar diagnóstico

| Comportamento | POSIX (macOS/Linux) | Windows |
| ------------- | ------------------- | ------- |
| Process group | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` |
| Sinal suave | `SIGTERM` | `CTRL_BREAK_EVENT` |
| Sinal duro | `SIGKILL` | `taskkill /F /T` |

## SecretStore

| Plataforma | Backend keyring |
| ---------- | --------------- |
| macOS | `keyring.backends.macOS.Keyring` (Keychain) |
| Windows | `keyring.backends.Windows.WinVaultKeyring` (Credential Manager) |
| Linux | `keyring.backends.SecretService.Keyring` (Secret Service / KWallet) |

Sem fallback silencioso para plaintext.

---

# 10. Roadmap priorizado

## Fase A — Auditoria e baseline (1 dia)

- [ ] Registrar ambiente ✅
- [ ] Criar `scripts/` com helpers de dev multiplataforma
- [ ] Instalar PyInstaller no macOS
- [ ] Validar `pip install -r requirements.txt` no macOS arm64

## Fase B — Núcleo multiplataforma (2 dias)

- [ ] `PlatformPaths` — Electron passa `PROSPECTOS_DATA_DIR` ao backend
- [ ] `RuntimeManifest` — nomes de binário por plataforma
- [ ] `ProcessSupervisor` — abstração de subprocesso
- [ ] Atualizar `paths.py` para usar env var
- [ ] Atualizar `electron-builder.yml` — `mac` targets + `icns`
- [ ] Condicionar `afterPack.js` para Windows-only
- [ ] `SecretStore` — adicionar `keyring.backends.macOS` ao hiddenimports

## Fase C — Mac M4 funcional (2 dias)

- [ ] Baixar scraper `darwin-arm64` + Node portátil arm64
- [ ] Atualizar `prospectos.spec` com paths corretos
- [ ] Build PyInstaller no macOS arm64
- [ ] Build Electron macOS arm64
- [ ] Smoke test: app abre, backend sobe, frontend carrega
- [ ] Testar scraper (texto + mapa), PDF, IA, Instagram
- [ ] Testar encerramento sem processos órfãos

## Fase D — Mac M4 distribuível (2 dias)

- [ ] Apple Developer Program ($99/ano)
- [ ] Developer ID + certificado de aplicação
- [ ] Hardened Runtime + entitlements
- [ ] Notarização + stapling
- [ ] DMG + electron-builder `mac` config completo
- [ ] CI/CD para builds macOS

## Fase E — Regressão Windows (1 dia)

- [ ] Confirmar que `PlatformPaths` não quebrou `APPDATA`
- [ ] Confirmar que `RuntimeManifest` mapeia `.exe` corretamente
- [ ] Executar suite de testes no Windows
- [ ] Build PyInstaller + Electron Windows
- [ ] Validar migração de dados (leads.db existente)

## Fase F — Linux + Mac Intel (2 dias)

- [ ] Adicionar target `linux-x64` ao `RuntimeManifest`
- [ ] Configurar `linux` no electron-builder
- [ ] Testar build Linux
- [ ] Testar Mac Intel (x64) com Rosetta ou build nativo

## Fase G — Robustez e performance (contínuo)

- [ ] Testes E2E empacotados
- [ ] Lazy loading frontend
- [ ] SQLite WAL + backup com API nativa
- [ ] Observabilidade: logs estruturados
- [ ] Timeouts e retries consistentes

---

# 11. Divisão em pull requests

| PR | Objetivo | Escopo | Dependências | Critério de aceite |
| -- | -------- | ------ | ------------ | ------------------ |
| **1** | `PlatformPaths` | `desktop/main.js`, `backend/paths.py`, `backend/app.py` | Nenhuma | Testes passam; `PROSPECTOS_DATA_DIR` funcional |
| **2** | `RuntimeManifest` | `backend/runtime.py` (novo), `backend/jobs.py`, `backend/prospectos.spec` | PR #1 | Scraper + Node achados no macOS sem `.exe` |
| **3** | `ProcessSupervisor` | `backend/supervisor.py` (novo), `backend/jobs.py` | PR #2 | Subprocessos iniciam/encerram corretamente no macOS |
| **4** | Build Electron macOS | `electron-builder.yml`, `package.json`, `afterPack.js`, icns | PR #1 | `npm run dist` gera `.app` funcional |
| **5** | Build PyInstaller macOS | `prospectos.spec`, `requirements.txt` | PR #2, PR #3 | PyInstaller gera bundle funcional no arm64 |
| **6** | SecretStore | `backend/db.py`, `prospectos.spec` | PR #1 | keyring salva/lê no macOS Keychain |
| **7** | Documentação + scripts | `CONTRIBUTING.md`, README, scripts dev | PR #1–6 | Dev workflow funcional no macOS |
| **8** | CI/CD multiplataforma | `.github/workflows/` (novo) | PR #1–7 | Build + smoke test nos 3 SOs |

---

# 12. Critérios de aceite do M4

- [ ] `npm run dist` gera `ProspectOS-3.0.0-arm64.dmg` (ou `.app` zipado)
- [ ] `.app` abre sem aviso de segurança (assinatura local ou `xattr -dr`)
- [ ] Backend Python sobe (PID visível no Activity Monitor)
- [ ] Frontend React carrega em `http://127.0.0.1:<porta>`
- [ ] Scraper executa busca por texto e retorna CSV
- [ ] Scraper executa busca por mapa (modo pino + raio)
- [ ] Leads persistem em `~/Library/Application Support/ProspectOS/leads.db`
- [ ] Backups criados em `~/Library/Application Support/ProspectOS/backups/`
- [ ] PDF gerado sem erros
- [ ] Instagram: login + raspagem + enriquecimento funcionam
- [ ] Chaves de API salvas no Keychain do macOS
- [ ] App encerra sem processos órfãos (backend morre junto)
- [ ] `Command+Q` fecha o app
- [ ] Todas as janelas fechadas **não** encerram o app (comportamento macOS padrão)
- [ ] Suite de testes: `py -m pytest` passa
- [ ] Scraper sem o binário correto mostra erro amigável (não "falta .exe")

---

# 13. Riscos e decisões abertas

## Técnico

| Risco | Impacto | Mitigação |
| ----- | ------- | --------- |
| PyInstaller arm64: dependências C não encontradas | Alto | Validar com `pip install` + build real; PyInstaller 6+ cobre bem |
| Playwright + Chromium arm64: download ~300MB na 1ª execução | Médio | Documentar; considerar incluir no bundle |
| keyring no macOS: sandbox pede permissão | Baixo | App assinado pede uma vez e lembra |
| Node portátil arm64: Playwright precisa de `PLAYWRIGHT_NODEJS_PATH` | Baixo | Já configurável via env var |

## Produto

| Risco | Impacto | Mitigação |
| ----- | ------- | --------- |
| Case-sensitivity do FS | Baixo | APFS padrão é case-insensitive (igual NTFS) |
| Path separators | Baixo | `pathlib.Path` resolve tudo |

## Distribuição

| Risco | Impacto | Mitigação |
| ----- | ------- | --------- |
| Apple Developer Program: $99/ano obrigatório | Médio | Estágio 1 (build local) não precisa; Estágio 2 sim |
| Hardened Runtime: entitlements específicas | Médio | Mapear necessidades (keyring, subprocess, files) |
| Assinatura de binários internos (PyInstaller, scraper, node) | Médio | Incluir no hardenedRuntime exceptions ou assinar todos |

## Itens que exigem hardware M4

- [ ] Build real do PyInstaller arm64
- [ ] Build real do Electron arm64
- [ ] Execução do scraper arm64
- [ ] Teste de performance e memória
- [ ] Teste de notarização

---

# 14. Comandos executados

```bash
uname -a                        # Darwin arm64 (M4)
sw_vers                         # macOS 26.4
arch                            # arm64
git branch                      # main
git rev-parse HEAD              # bebc2f1be6b1b8617ecee802cd3132f9647449b2
git status --short              # (limpa)
node --version                  # v22.22.2
npm --version                   # 10.9.7
npx electron --version          # v43.1.1
npx electron-builder --version  # 26.15.3
python3 --version               # 3.14.5
pyinstaller --version           # Não instalado
go version                      # Não instalado
xcode-select -p                 # /Library/Developer/CommandLineTools (instalado)
pkgutil --pkg-info=com.apple.pkg.Rosetta  # Não instalado
find .github -name "*.yml"     # Nenhum workflow CI encontrado
```

---

# 15. Próxima ação recomendada

## Criar o PR #1 — `PlatformPaths`

**Arquivos:**
- `desktop/main.js` — substituir `process.env.APPDATA` por `app.getPath('userData')`; passar `PROSPECTOS_DATA_DIR` ao backend
- `backend/paths.py` — ler `PROSPECTOS_DATA_DIR` do ambiente; fallback para `Path.home() / "Library/Application Support/ProspectOS"` no macOS
- `backend/app.py` — receber diretórios do ambiente (data, log, cache, temp, resource)
- `backend/tests/test_paths.py` — adicionar teste para modo macOS (sem `APPDATA`)

**Critério de aceite:**
```bash
PROSPECTOS_DATA_DIR=/tmp/prospectos-test python3 -m pytest backend/tests/test_paths.py -v
# → test_dados_vao_para_prospectos_data_dir PASSES
```

**Esforço estimado:** ~2h

Este PR é o foundation de tudo. Sem ele, nenhum path funciona no macOS.

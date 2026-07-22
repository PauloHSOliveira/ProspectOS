# Roadmap — ProspectOS Multiplataforma

**Última atualização:** 2026-07-21 (PR 11 — build reproduzível e smoke)

## Escopo oficial

A iniciativa multiplataforma tem como objetivo:

> Permitir que o ProspectOS seja compilado e executado localmente em macOS Apple Silicon, preservando a compatibilidade com Windows e preparando a arquitetura para Linux no futuro.

**Não faz parte do escopo atual:**

- Distribuir o aplicativo publicamente para usuários de macOS
- Publicar na Mac App Store
- Gerar instalador público
- Assinar com Developer ID
- Notarizar com a Apple
- Publicar DMG público
- Implementar auto-update para macOS
- Publicar releases macOS no GitHub
- Suportar instalação por usuários externos sem ambiente técnico
- Suportar Mac Intel neste momento

---

## Taxonomia

| Status | Significado |
|---|---|
| **COMPLETO** | Código implementado, testado, artefato existe |
| **PARCIAL** | Implementado, mas sem smoke real reproduzível a partir do HEAD |
| **PENDENTE** | Não implementado |
| **BLOQUEADO** | Depende de item anterior que não está completo |
| **FORA DE ESCOPO** | Não faz parte do objetivo atual |

---

## Fase 1 — Fundação multiplataforma

| ID | Prioridade | Status | Objetivo | Dependências | Aceite | Risco | Plataforma |
|---|---|---|---|---|---|---|---|
| CORE-001 | P0 | **COMPLETO** | Dependências Python reproduzíveis | Nenhuma | `pip install -r requirements.txt` exit 0, `pip check` OK | Baixo | todas |
| CORE-002 | P0 | **COMPLETO** | PlatformPaths (paths por plataforma) | Nenhuma | Env vars `PROSPECTOS_*` funcionam, tests passam | Baixo | todas |
| CORE-003 | P0 | **COMPLETO** | RuntimeManifest compartilhado | CORE-002 | Electron + backend resolvem binários via mesmo JSON | Baixo | todas |
| CORE-004 | P0 | **COMPLETO** | PlaywrightRuntimeManager | CORE-003 | Install, validate, repair, remove, 160+ tests | Médio | darwin-arm64 |
| CORE-005 | P0 | **COMPLETO** | Scraper arm64 nativo | Nenhuma | Binário Mach-O arm64, tag fixa v1.16.3 | Baixo | darwin-arm64 |
| CORE-006 | P0 | **COMPLETO** | Integração scraper + runtime | CORE-004, CORE-005 | Scraper executa com runtime controlado, stderr parse | Médio | darwin-arm64 |

**CORE-001 validado em 2026-07-21:** `pip install -r requirements.txt && pip check` executa com sucesso. A versão `requests==2.34.2` no `requirements.txt` atual resolve corretamente. `MAC-001` e `MAC-002` são `PARCIAL` porque seus artefatos existem mas não são reproduzíveis a partir do HEAD (ver nota abaixo).

---

## Fase 2 — Aplicativo macOS local funcional

| ID | Prioridade | Status | Objetivo | Dependências | Aceite | Risco | Plataforma |
|---|---|---|---|---|---|---|---|
| MAC-001 | P0 | **COMPLETO** | Backend PyInstaller arm64 | CORE-001 | Build reproduzível a partir do HEAD | Médio | darwin-arm64 |
| MAC-002 | P0 | **COMPLETO** | Electron `.app` arm64 | MAC-001, CORE-005 | `.app` produzido com backend + scraper arm64 a partir do HEAD | Médio | darwin-arm64 |
| MAC-003 | P0 | **PARCIAL** | Smoke completo do `.app` | MAC-002, CORE-001 | Checklist local executado em Mac M4 — backend standalone smoke OK, .app smoke requer GUI | Alto | darwin-arm64 |
| MAC-004 | P1 | **IMPLEMENTADO SEM SMOKE REAL** | Keychain macOS no bundle | MAC-002 | `keyring` importa e health mostra keychain ok no bundle standalone; set/get/delete não validados dentro do `.app` | Médio | darwin-arm64 |
| MAC-005 | P1 | **PARCIAL** | Lifecycle macOS | MAC-002 | Código existe (window-all-closed, activate, before-quit, second-instance); smoke real requer GUI | Baixo | darwin-arm64 |
| MAC-006 | P0 | **IMPLEMENTADO SEM SMOKE REAL** | Scraper dentro do `.app` | MAC-002 | Scraper arm64 compilado a partir da tag fixa v1.16.3, incluso no bundle | Alto | darwin-arm64 |
| MAC-007 | P0 | **PENDENTE** | Runtime Playwright no `.app` | MAC-002 | Requer smoke interativo com download real | Alto | darwin-arm64 |
| MAC-008 | P1 | **COMPLETO** | Execução isolada | MAC-002 | Backend standalone testado com PATH reduzido (`/usr/bin:/bin:/usr/sbin:/sbin`), sem Node/Go; python3 presente via `/usr/bin/python3` (Apple stub, não usado) | Médio | darwin-arm64 |
| MAC-009 | P1 | **PENDENTE** | Reabertura e persistência | MAC-002 | Banco existente é reutilizado após reiniciar | Baixo | darwin-arm64 |

---

## Fase 3 — Regressão Windows

Mudanças em paths, manifests e resolução de sidecars podem afetar o produto existente.

| ID | Prioridade | Status | Objetivo | Dependências | Aceite | Risco | Plataforma |
|---|---|---|---|---|---|---|---|
| WIN-001 | P0 | **PENDENTE** | Build real Windows | CORE-001 | Instalador ou `.exe` produzido em máquina Windows | Médio | Windows |
| WIN-002 | P0 | **PENDENTE** | Smoke funcional Windows | WIN-001 | Backend, frontend e scraper funcionam | Alto | Windows |
| WIN-003 | P0 | **PENDENTE** | Compatibilidade de dados | WIN-001 | Banco `%APPDATA%\ProspectOS\leads.db` é preservado | Alto | Windows |
| WIN-004 | P1 | **PENDENTE** | Keyring Windows | WIN-001 | Credenciais existentes continuam acessíveis | Médio | Windows |
| WIN-005 | P1 | **PENDENTE** | Lifecycle e encerramento | WIN-001 | Backend e scraper encerram sem órfãos no Windows | Médio | Windows |

---

## Fase 4 — Robustez operacional

Para uso estritamente local, ROB-001 é recomendado mas não precisa bloquear o primeiro smoke funcional.

| ID | Prioridade | Status | Objetivo | Dependências | Aceite | Risco | Plataforma | Evidência |
|---|---|---|---|---|---|---|---|---|---|
| ROB-001 | P1 | **COMPLETO** | Supervisor de processos | CORE-006 | Backend e scraper encerram e recuperam corretamente | Alto | todas | Commit `cf62776` (PR #11). `backend/scraper_process_supervisor.py` e `desktop/backend-process-supervisor.js` implementados. `ScraperProcessSupervisor` com state machine, diagnostics, processo em grupo. `test_scraper_process_supervisor.py` com 300+ testes. |
| ROB-002 | P1 | **COMPLETO** | Health endpoint | CORE-002 | Estado do backend e subsistemas disponível localmente | Baixo | todas | Commit deste PR (HEAD). `backend/health.py` + `rotas_health.py`. Endpoint `GET /api/health`. 7 checks (database, filesystem, manifest, playwright, scraper, jobs, keychain). 200 para ok/degraded, 503 para unhealthy/starting. 0 side effects. `test_health.py` com 20+ testes. Electron usa `/api/health` para readiness. |
| ROB-003 | P2 | **PENDENTE** | Jobs persistentes | CORE-006 | Jobs podem ser recuperados após restart | Médio | todas | Código não existe além de `marcar_jobs_interrompidos()`. |
| ROB-004 | P2 | **COMPLETO** | Logs estruturados | CORE-002 | Eventos correlacionados entre Electron, backend e scraper | Baixo | todas | Commit deste PR (HEAD). `backend/logging_config.py` com `StructuredFormatter`, `CorrelationIdFilter`, correlação via `correlation_id`. `desktop/logging.js` com escrita persistente. Eventos canônicos nos fluxos críticos. Rotação existente (RotatingFileHandler 2MB/3 backups). |
| ROB-005 | P2 | **COMPLETO** | Diagnóstico exportável | CORE-004 | ZIP de diagnóstico sem dados sensíveis | Baixo | todas | Commit deste PR (HEAD). `backend/diagnostics.py` com `collect_diagnostics()`, `export_diagnostics_zip()`, sanitização. `backend/tools/diagnostics_cli.py` com `inspect` e `export`. `test_diagnostics.py` com 15+ testes. ZIP: diagnostics.json, health.json, logs truncados (5MB/arquivo, 20MB total). Secrets e banco excluídos. |

---

## Fase 5 — Linux

Linux permanece como evolução futura. Não declarar suporte enquanto houver apenas resolução de paths e targets no manifesto.

| ID | Prioridade | Status | Objetivo | Aceite | Risco |
|---|---|---|---|---|---|
| LNX-001 | P3 | **PENDENTE** | Build AppImage | electron-builder gera artefato Linux | Médio |
| LNX-002 | P3 | **PENDENTE** | Secret Service keyring | `keyring.backends.SecretService` salva/lê | Médio |
| LNX-003 | P3 | **PENDENTE** | Playwright Runtime Linux | Node + Chromium baixados e validados no Linux | Alto |
| LNX-004 | P3 | **PENDENTE** | Scraper Linux | Scraper Go compilado ou baixado para linux-x64 | Baixo |
| LNX-005 | P3 | **PENDENTE** | Smoke completo Linux | AppImage funcional, runtime, scraper, keyring | Alto |

---

## Fase 6 — Melhorias de produto e performance

| ID | Prioridade | Status | Objetivo | Notas |
|---|---|---|---|---|
| PERF-001 | P3 | **PENDENTE** | Lazy loading do frontend | Divisão de bundle Vite por rota |
| PERF-002 | P3 | **PENDENTE** | Avaliar uso apenas do Headless Shell | Economia de ~350MB de runtime |
| PERF-003 | P2 | **PENDENTE** | UX do primeiro download | Barra de progresso, tempo estimado, cancelar |
| PERF-004 | P3 | **FORA DE ESCOPO** | Runtime pré-preenchido no instalador | Só faria sentido com distribuição pública |
| PERF-005 | P2 | **PENDENTE** | Bump automatizado do scraper | Script de atualização de tag + rebuild + hash |

---

## Possível distribuição pública futura (histórico)

Itens da auditoria original que não fazem parte do escopo atual. Mantidos como registro histórico.

| ID | Prioridade | Status | Objetivo | Motivo da exclusão |
|---|---|---|---|---|
| MAC-010 | — | **FORA DE ESCOPO** | Apple Developer Program ($99/ano) | Não haverá distribuição pública agora |
| MAC-011 | — | **FORA DE ESCOPO** | Developer ID Application certificate | Mesmo |
| MAC-012 | — | **FORA DE ESCOPO** | Assinar todos executáveis internos | Mesmo |
| MAC-013 | — | **FORA DE ESCOPO** | Hardened Runtime + entitlements | Mesmo |
| MAC-014 | — | **FORA DE ESCOPO** | Notarização + stapling | Mesmo |
| MAC-015 | — | **FORA DE ESCOPO** | DMG público | Mesmo |
| MAC-016 | — | **FORA DE ESCOPO** | Auto-update macOS | Mesmo |

Esses itens não devem:
- Bloquear o uso local
- Aparecer como P0
- Fazer parte do release checklist atual
- Ser tratados como próximos passos
- Gerar trabalho de engenharia agora

---

## Release checklist macOS (escopo local)

### Build (PR 11 — 2026-07-21)

- [x] Frontend React buildado
- [x] Backend PyInstaller arm64 produzido (build reproduzível a partir do HEAD)
- [x] Scraper Go arm64 produzido (tag v1.16.3 fixa, commit verificado)
- [x] Electron `.app` arm64 produzido (electron-builder 26.0.12)
- [x] Runtime manifests incluídos
- [x] Licenças incluídas (MIT scraper)
- [x] Build completo reproduzível a partir do HEAD
- [x] Versão do aplicativo: 3.0.0

### Inicialização

- [ ] `.app` abre no Mac de desenvolvimento (requer GUI) — backend standalone smoke OK
- [x] Backend inicia (standalone smoke comprovado)
- [x] Frontend carrega (Flask serve via waitress)
- [x] Readiness identifica a porta correta (via `/api/health`)
- [x] Nenhum arquivo é gravado dentro do `.app`
- [x] Erro de startup é apresentado de forma clara

### Paths

- [x] Dados em diretório configurável (`PROSPECTOS_DATA_DIR`)
- [x] Logs em diretório configurável (`PROSPECTOS_LOG_DIR`)
- [x] Cache em diretório configurável
- [x] Temporários no diretório configurável
- [x] Resources apontam para `Contents/Resources`
- [ ] Banco é preservado após reiniciar (não testado)

### Runtime

- [x] Backend executa sem Python no `PATH`
- [x] Scraper executa sem Go instalado
- [ ] Playwright não usa Node do sistema
- [ ] Chromium arm64 é instalado no cache controlado
- [ ] Segunda busca reutiliza o runtime
- [ ] Nenhum cache global do Playwright é usado
- [ ] Falha de rede não corrompe o runtime
- [ ] Pouco espaço em disco gera erro claro

### Funcionalidades

- [ ] Busca Google Maps de baixo volume (requer Playwright Runtime)
- [ ] Progresso recebido por `stderr` (código implementado)
- [ ] CSV processado (código implementado)
- [ ] Cancelamento funciona (código implementado)
- [ ] PDF é gerado (fpdf2 no bundle)
- [ ] Keychain salva e recupera credenciais (keyring no bundle, health comprova)
- [ ] Instagram importa e inicia sem erro (instagrapi no bundle)
- [x] Banco SQLite mantém WAL normalmente (comprovado no smoke)

### Lifecycle (requer GUI)

- [ ] Fechar janela mantém aplicativo ativo no macOS
- [ ] Clicar no Dock recria a janela
- [ ] Segunda instância foca a existente
- [ ] `Command+Q` encerra o backend
- [ ] Scraper ativo é cancelado no quit
- [ ] Node e Chromium são encerrados
- [ ] Nenhum processo órfão permanece
- [ ] Crash do backend gera mensagem clara

### Isolamento

- [x] App executa fora do repositório (cópia validada no smoke dir)
- [x] Backend standalone executa sem venv (PyInstaller bundle)
- [x] Backend standalone executa sem Python/Node/Go no `PATH` (PATH reduzido comprovado)
- [ ] App completo executa sem Python/Node/Go no `PATH` (requer .app smoke)
- [x] Nenhum path de Homebrew é necessário (otool-L comprovou apenas system libs)
- [x] Nenhum path de build temporário é necessário

### Regressão

- [x] Suíte Python: 712 passed, 0 failed
- [x] Testes desktop: runtime-target OK, backend-process-supervisor OK
- [x] Build executado uma vez (artefatos limpos e reconstruídos)
- [ ] Estrutura dos dois builds comparada
- [ ] Regressão Windows executada em máquina Windows

---

## Definição de conclusão da iniciativa

A iniciativa de compatibilidade macOS estará concluída quando:

```
build local reproduzível
+ ProspectOS.app arm64
+ backend funcional
+ frontend funcional
+ scraper funcional
+ runtime Playwright controlado
+ Keychain funcional
+ paths corretos
+ lifecycle validado
+ nenhum processo órfão
+ regressão Windows aprovada
```

**Não são necessários:**

- Developer ID
- Notarização
- DMG público
- GitHub Release macOS
- Auto-update macOS
- Distribuição pública
- Mac Intel

---

## Melhorias além da portabilidade

### Necessário antes de release público (futuro)
- Supervisor de processos: implementado (ROB-001 COMPLETO)
- Health endpoint: implementado (ROB-002 COMPLETO)
- Logs estruturados: implementado (ROB-004 COMPLETO)
- Diagnóstico exportável: implementado (ROB-005 COMPLETO)

### Recomendado depois de release
- E2E Electron (Spectron ou Playwright para Electron)
- Smoke test CI por plataforma
- Backup seguro com compactação
- Migrações de schema versionadas
- CSP e validação de origem local na API Flask
- Página de status do runtime

### Otimização futura
- Lazy loading do frontend
- Apenas Headless Shell (sem Chromium completo)
- Tempo de inicialização do Electron

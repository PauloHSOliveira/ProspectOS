# Gate 1B — Build e validação do Google Maps Scraper no Mac M4

## 1. Gate Verdict

**PASS WITH CAVEATS**

| Questão | Resultado |
|---|---|
| Source compilou no M4 | **SIM** (nativo, sem patch) |
| Artefato Mach-O arm64 | **SIM** (Mach-O 64-bit arm64, non-fat) |
| CLI compatível | **SIM** (todos os 9 flags esperados existem) |
| Executou busca | **SIM** (20 padarias encontradas em BH) |
| Node externo necessário | **NÃO** (usa Node embutido no driver Playwright) |
| Chromium instalado | **SIM** (auto-download v149 arm64 na primeira execução) |
| CSV compatível | **SIM** (9 campos esperados presentes + extras) |
| stdout compatível | **PARCIAL** (progresso JSON vai pra stderr, não stdout) |
| Exit codes utilizáveis | **SIM** (0=ok, 1=erro, 2=flag) |
| Encerramento da árvore | **SIM** (SIGTERM encerra scraper + driver + Chromium em ~5s) |
| Wrapper atual compatível | **PARCIAL** (lê stdout, scraper escreve em stderr) |
| Working tree preservada | **SIM** (0 alterações) |

## 2. Versão Validada

| Item | Valor |
|---|---|
| Upstream | gosom/google-maps-scraper |
| Tag | v1.16.3 |
| Commit | 25751bf24ea292792b88100aab5e9b6c794448e3 |
| Motivo da escolha | Tag mais recente; README do ProspectOS indica "latest release" |
| Go (build) | 1.26.5 darwin/arm64 (Homebrew) |
| Playwright Go | mxschmitt/playwright-go v0.6100.0 (direta); playwright-community/playwright-go v0.6000.0 (indireta via scrapemate) |

**Nota crítica:** O go.mod tem tanto `mxschmitt/playwright-go v0.6100.0` (driver 1.61.1, instalador) quanto `playwright-community/playwright-go v0.6000.0` (driver 1.60.0, runtime via scrapemate). O runtime usa o driver 1.60.0 que não está mais disponível nos CDNs da Microsoft. O commit `25751bf` ("Fixes 404 playwright") corrigiu apenas o caminho de instalação, não o runtime.

## 3. Build

| Item | Valor |
|---|---|
| Comando | `go build -trimpath -o /tmp/prospectos-gate1b-build/google-maps-scraper .` |
| Resultado | **SUCESSO** (sem warnings) |
| Duração | ~26s (com cache frio) |
| Arquitetura | arm64 |
| Tamanho | 80 MB |
| SHA-256 | `525581065e5478091cc208a7a3c30a992147f4fdd7b2b290208c5265648ced31` |
| Bibliotecas dinâmicas | libresolv, CoreFoundation, Security, libSystem.B (apenas system libraries) |
| CGO | CGO_ENABLED=1 (default), linka system libraries padrão do macOS |

## 4. CLI

| Argumento esperado pelo ProspectOS | Existe no scraper | Tipo | Compatível |
|---|---|---|---|
| `-input <path>` | `-input` | string | ✅ |
| `-results <path>` | `-results` | string | ✅ (default "stdout") |
| `-lang pt` | `-lang` | string | ✅ (default "en") |
| `-depth 5` | `-depth` | int | ✅ (default 10) |
| `-exit-on-inactivity 3m` | `-exit-on-inactivity` | duration | ✅ |
| `-geo lat,lng` | `-geo` | string | ✅ |
| `-radius <m>` | `-radius` | float64 | ✅ |
| `-zoom <n>` | `-zoom` | int | ✅ (range 0-21, ProspectOS usa 8-17) |
| `-proxies <csv>` | `-proxies` | string | ✅ |

**CLI compatible: yes**

## 5. Runtime

| Item | Primeira execução | Segunda execução (reuso) |
|---|---|---|
| Duração total | ~29s (inclui download browsers) | ~18s (cache) |
| Exit code | 0 | 0 |
| Resultados | 20 linhas CSV | 18 linhas CSV |
| stdout | 0 bytes | 0 bytes |
| stderr | 12 KB (34 linhas, 24 JSON) | 12 KB (30 linhas, 21 JSON) |
| Downloads | Driver 1.61.1 + Chromium 1228 | Nenhum (cache) |

## 6. Node e Playwright

**External Node required: no**

Evidência:
- O scraper foi executado com `env -i PATH="/usr/bin:/bin:/usr/sbin:/sbin"` (sem Node.js no PATH, sem `PLAYWRIGHT_NODEJS_PATH`)
- O driver 1.60.0 possui Node embutido (`node` arm64, 115 MB) em `~/Library/Caches/ms-playwright-go/1.60.0/node`
- O Node embutido é usado para executar `package/cli.js run-driver` (driver do Playwright)
- O sistema Node.js do usuário (v22.22.2 via nvm) não é necessário

Exceção: se o diretório do driver não existir (instalação limpa), o Playwright precisa baixá-lo. O download do driver 1.60.0 está quebrado (404 no CDN) — é necessário obter o `playwright-core` do npm registry.

## 7. Chromium

| Componente | Local | Tamanho | Arquitetura |
|---|---|---|---|
| Scraper (binário) | `/tmp/prospectos-gate1b-build/google-maps-scraper` | 80 MB | arm64 |
| Driver Playwright (Node) | `~/Library/Caches/ms-playwright-go/1.60.0/node` | 115 MB | arm64 |
| Playwright-core JS | `~/Library/Caches/ms-playwright-go/1.60.0/package/` | 13 MB | JS (platform-neutral) |
| Chromium | `~/Library/Caches/ms-playwright/chromium-1228/` | 344 MB | arm64 |
| Chromium Headless Shell | `~/Library/Caches/ms-playwright/chromium_headless_shell-1228/` | 192 MB | arm64 |

**Mecanismo de instalação:** Playwright auto-download: primeiro o driver (Node + package JS do npm), depois o Chromium + Headless Shell do CDN da Microsoft.

**Cache:** `~/Library/Caches/ms-playwright/` e `~/Library/Caches/ms-playwright-go/`. Reutilizável entre execuções.

**Browser reusable after first install:** SIM (segunda execução sem re-downloads, apenas verificação de integridade).

**Estratégia recomendada para ProspectOS:**
- O binário arm64 do scraper (80 MB) pode ser distribuído com o app
- Playwright driver e Chromium precisam ser baixados na primeira execução (ou pré-instalados num script de setup)
- Total de cache de runtime: ~664 MB (Node driver 115 MB + Chromium 344 MB + Headless Shell 192 MB + package 13 MB)

## 8. CSV

**CSV compatible: yes**

| Campo esperado | Campo real (header) | Compatível |
|---|---|---|
| `place_id` | `place_id` | ✅ |
| `title` | `title` | ✅ |
| `category` | `category` | ✅ |
| `address` | `address` | ✅ |
| `phone` | `phone` | ✅ |
| `website` | `website` | ✅ |
| `review_rating` | `review_rating` | ✅ |
| `review_count` | `review_count` | ✅ |
| `input_id` | `input_id` | ✅ |

Encoding: UTF-8, delimitador: vírgula, quoting: padrão CSV. Campos extras adicionais (27 campos além dos esperados) são ignorados pelo `csv.DictReader` do ProspectOS.

## 9. Stdout

**stdout compatible: partial**

O scraper moderno (v1.16.3) emite todo progresso JSON para **stderr**, não stdout. O stdout fica vazio quando `-results <file>` é usado.

Formato do progresso no stderr:
```json
{"level":"info","component":"scrapemate","jobid":"...","time":"...","message":"20 places found"}
```

O parser atual do ProspectOS usa `json.loads(linha).get("message")` que funcionaria com este formato. O problema é que o parser lê de `process.stdout`, mas as mensagens estão em `process.stderr`.

**Mudança necessária:** Alterar o wrapper para ler progresso de stderr, ou redirecionar stderr→stdout.

Mensagens relevantes:

| Mensagem real | Reconhecida pelo ProspectOS | Efeito |
|---|---|---|
| `"message":"N places found"` | ✅ (via `endswith("places found")`) | Incrementa contador |
| `"message":"job finished"` | ✅ (via `== "job finished"`) | Incrementa processadas |
| `"message":"scrapemate exited"` | ❌ (ignorada) | Nenhum |

## 10. Process Tree

```
google-maps-scraper
└── node <driverDir>/package/cli.js run-driver
    └── Chromium <cache>/chrome-mac-arm64/Google Chrome for Testing.app
```

O driver Node.js é filho direto do scraper. O Chromium é gerenciado pelo driver. A árvore usa um único PGID (process group).

## 11. Encerramento

| Item | Resultado |
|---|---|
| SIGTERM no processo principal | ✅ Scraper captura SIGTERM, cancela contexto, aguarda ~5s |
| Driver Node encerrou | ✅ Filho Node encerrou junto com o pai |
| Chromium encerrou | ✅ Gerenciado pelo driver — driver termina, Chromium termina |
| Processos órfãos | ❌ Nenhum |
| SIGKILL necessário | ❌ Não (SIGTERM foi suficiente, com wait de 5s) |
| Exit code após SIGTERM | 1 (context.Canceled) |

## 12. Integração com o ProspectOS

| Aspecto | Situação | Mudança necessária |
|---|---|---|
| Nome do binário | `google-maps-scraper` (sem `.exe`) | `RuntimeManifest` precisa retornar nome correto por plataforma |
| Wrapper (Popen) | Lê stdout, mas scraper escreve em stderr | Trocar `processo.stdout` → `processo.stderr` ou redirecionar `2>&1` |
| Argumentos CLI | 100% compatíveis | Nenhuma |
| CSV | 100% compatível | Nenhuma |
| stdout (progresso) | JSON em stderr vs stdout | Migrar leitura para stderr |
| Timeout | `processo.kill()` já funciona | Nenhuma |
| PATH para Node | Desnecessário (Node embutido) | Nenhuma |
| CWD gravável | Necessário (Playwright cache) | Já usa `DIR_DADOS` |

## 13. Bloqueadores Confirmados

| # | Prioridade | Bloqueador | Evidência | Próxima ação |
|---|---|---|---|---|
| 1 | **MÉDIO** | Driver `playwright-community/playwright-go v0.6000.0` (v1.60.0) não disponível no CDN da Microsoft (404) | `curl` aos 3 mirrors retorna 404. Código do run.go (stderr): `error: got non 200 status code: 404` | O driver pode ser obtido do npm registry (`playwright-core-1.60.0.tgz`) + Node binary da versão 1.61.1. Estratégia de build em CI precisa incluir este passo |
| 2 | **BAIXO** | Progresso JSON em stderr, não stdout | Verificação direta: stdout vazio, stderr contém 24 mensagens JSON | Adaptar `rodar_scraper_com_progresso` ou wrapper para ler stderr |

## 14. Suposições Descartadas

- ❌ **"Não é possível compilar arm64"** → FALSO. Compilou nativamente em 26s sem patches.
- ❌ **"Node externo é obrigatório"** → FALSO. O Playwright-go baixa Node próprio (115 MB arm64) no diretório do driver.
- ❌ **"CSV mudou de formato"** → FALSO. Todos os 9 campos esperados estão presentes.
- ❌ **"stdout mudou de formato"** → VERDADEIRO, mas apenas no pipe (stderr vs stdout). O formato JSON e os campos `message` são compatíveis.
- ❌ **"Precisa de Rosetta"** → FALSO. Binário e Chromium são arm64 nativos.
- ❌ **"Tag v1.16.3 resolveu o 404 do Playwright"** → PARCIALMENTE FALSO. A correção só afeta o caminho `installplaywright`, não o runtime via `scrapemate`.

## 15. Estratégia de Distribuição Recomendada

**Opção A — Build próprio em CI (RECOMENDADA)**

```
checkout upstream tag v1.16.3
→ go mod verify
→ go build -trimpath -o google-maps-scraper
→ browser setup: playwright-core do npm + node binary
→ smoke test: --version + CSV
→ checksum + notarização macOS
→ incluir no Electron como recurso
```

**Razões:**
- **Reprodutibilidade:** build sem patches, depende apenas de Go 1.26.5 + módulos
- **Segurança:** fixado a commit `25751bf`, sem binários não verificados
- **Licenciamento:** MIT — permitido redistribuir sem restrições
- **Atualização:** bump de tag + rebuild
- **Assinatura:** binário Go não requer assinatura de runtime; Chromium e Node são do próprio Playwright

**Requisitos adicionais no CI:**
- Instalar Go 1.26.5
- `go build -trimpath`
- Obter `playwright-core-1.60.0` do npm registry (não do CDN da Microsoft)
- Obter Node binary (pode usar o mesmo da versão 1.61.1)
- Asset final: ~80 MB (binário) + ~310 MB (Playwright driver + Node + Chromium, baixados na primeira execução)

## 16. Decisão sobre o Próximo PR

**Scraper arm64 deixou de ser bloqueador? yes**

**Ação única recomendada:** Iniciar PR de adaptação do wrapper no ProspectOS:
1. Atualizar `RuntimeManifest` para nome do binário por plataforma
2. Adaptar `rodar_scraper_com_progresso` para ler progresso do stderr ou redirecionar `2>&1`
3. Atualizar docs de instalação para macOS
4. (Opcional) Adicionar script de CI build do scraper

## 17. Comandos Executados

| Comando | Resultado |
|---|---|
| `brew install go` | ✅ Go 1.26.5 instalado |
| `git clone https://github.com/gosom/google-maps-scraper.git` | ✅ v1.16.3 |
| `go mod download && go mod verify` | ✅ Todas as dependências verificadas |
| `go build -trimpath -o ...` | ✅ ~26s, 80 MB, arm64 |
| `file / lipo / otool -L / shasum` | ✅ Mach-O arm64, sem dependências externas |
| `PLAYWRIGHT_INSTALL_ONLY=1 ...` | ✅ Chromium arm64 baixado |
| `curl -sL https://registry.npmjs.org/playwright-core/-/playwright-core-1.60.0.tgz` | ✅ Driver npm obtido (workaround 1.60.0) |
| `./scraper -input queries.txt -results ...` | ✅ 20 padarias encontradas, CSV válido |
| `env -i PATH=/usr/bin:/bin ... ./scraper` | ✅ Funciona sem Node externo |
| `kill -TERM; sleep 5` | ✅ Encerramento limpo |

## 18. Temporários

| Caminho | Finalidade | Tamanho | Limpeza |
|---|---|---|---|
| `/tmp/prospectos-gate1b-scraper-src/` | Clone upstream v1.16.3 | 38 MB | Manual |
| `/tmp/prospectos-gate1b-build/` | Build arm64 + scripts | 65 MB | Manual |
| `/tmp/prospectos-gate1b-run/` | Inputs, CSVs, logs de teste | 1.1 MB | Manual |
| `/tmp/prospectos-gate1b-home/` | HOME temporário (vazio) | 0 B | Manual |
| `/tmp/playwright-core-1.60.0.tgz` | Driver npm (workaround) | 2.7 MB | Manual |
| `~/Library/Caches/ms-playwright-go/1.60.0/` | Driver Playwright 1.60.0 (Node + package) | 128 MB | Reutilizável |
| `~/Library/Caches/ms-playwright/chromium-1228/` | Chromium v149 arm64 | 344 MB | Reutilizável |
| `~/Library/Caches/ms-playwright/chromium_headless_shell-1228/` | Headless Shell v149 arm64 | 192 MB | Reutilizável |
| Go (Homebrew) | /opt/homebrew/Cellar/go/1.26.5 | 229 MB | `brew uninstall go` |

## 19. Conclusão

**Podemos produzir e distribuir um scraper nativo darwin-arm64 para o ProspectOS? yes**

**Próxima ação única recomendada:**
Iniciar PR de adaptação do wrapper no ProspectOS: corrigir pipe de progresso (stderr), atualizar nome do binário, e preparar `RuntimeManifest` para darwin-arm64.

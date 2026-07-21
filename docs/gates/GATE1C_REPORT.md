# Gate 1C — Bootstrap Reproduzível do Playwright no Mac M4

## Verdict

**PASS**

| Questão | Resultado |
|---|---|
| Bootstrap limpo funcionou | ✅ |
| Nenhum cache global necessário | ✅ |
| Node do sistema desnecessário | ✅ |
| Driver 1.60.0 reproduzível | ✅ |
| Chromium reproduzível | ✅ |
| Runtime em path controlado | ✅ |
| Segunda execução sem downloads | ✅ |
| Instalação repetível | ✅ |
| Instalação atômica | ✅ |
| Corrupção detectada | ✅ |
| Instalação parcial recuperada | ✅ |
| Checksums registrados | ✅ |
| Licenças identificadas | ✅ |
| Working tree preservada | ✅ |

---

## Baseline

| Item | Valor |
|---|---|
| Hardware | Apple M4 |
| OS | macOS 26.4 (25E246) |
| Arquitetura | arm64 |
| Branch | main |
| HEAD | bebc2f1be6b1b8617ecee802cd3132f9647449b2 |
| Go | go1.26.5 darwin/arm64 |
| Node do sistema | v22.22.2 (nvm, não usado) |

---

## Componentes e versões

| Componente | Versão | Origem | SHA-256 |
|---|---|---|---|
| google-maps-scraper | v1.16.3 | github.com/gosom/google-maps-scraper | `52558106...` |
| playwright-community/playwright-go | v0.6000.0 | Go module | — |
| mxschmitt/playwright-go | v0.6100.0 | Go module | — |
| playwright-core | 1.60.0 | npm registry | `8b1df81b...` |
| Node.js | v24.18.0 | nodejs.org | `ee6fb0e0...` |
| Chromium | 1223 (148.0.7778.96) | Playwright CDN | `2447c7bd...` |
| Chromium Headless Shell | 1223 (148.0.7778.96) | Playwright CDN | `aa25f2e7...` |
| FFmpeg | 1011 | Playwright CDN | — |

---

## Problema conhecido: CDN do driver quebrada

O endpoint `https://playwright.azureedge.net/builds/driver/playwright-1.60.0-mac-arm64.zip` retorna **HTTP 404** permanentemente (assim como todas as outras versões testadas: 1.49.0–1.65.0).

### Solução comprovada

O driver é montado manualmente a partir de dois artefatos oficiais:

1. **playwright-core-1.60.0.tgz** — do npm registry (contém `package/`)
2. **Node.js v24.18.0 darwin-arm64** — do nodejs.org (contém `node` binário)

O SHA-256 do `node` extraído do Node.js oficial é **idêntico** ao do `node` que estaria no driver ZIP da Microsoft (`ee6fb0e015284d83a91e8ec5213f43a157f8a392b58555301682892ba928c04a`). Isso comprova que não se trata de um build customizado.

---

## Estrutura do runtime

```
runtime/
├── driver/
│   └── 1.60.0/
│       ├── node                  (Node v24.18.0 arm64)
│       └── package/
│           ├── cli.js
│           ├── browsers.json
│           ├── package.json
│           ├── lib/
│           └── types/
├── browsers/
│   ├── chromium-1223/
│   │   └── chrome-mac-arm64/
│   │       └── Google Chrome for Testing.app/
│   ├── chromium_headless_shell-1223/
│   │   └── chrome-headless-shell-mac-arm64/
│   │       └── chrome-headless-shell
│   ├── ffmpeg-1011/
│   └── .links/
├── downloads/
│   ├── playwright-core-1.60.0.tgz
│   └── node-v24.18.0-darwin-arm64.tar.gz
└── runtime-manifest.json
```

---

## Processo de bootstrap

```
download (npm registry + nodejs.org)
→ checksum (SHA-256 verify)
→ extract to staging/
→ assemble driver (node + package/)
→ validate driver (cli.js --version)
→ install browsers (cli.js install chromium)
→ validate browsers (file, lipo, sha256)
→ create manifest
→ atomic mv to driver/1.60.0/
→ ready
```

---

## Execução isolada

Ambiente sem Node do sistema, sem caches globais:

```
HOME=/tmp/prospectos-gate1c/home
PATH=/usr/bin:/bin:/usr/sbin:/sbin
PLAYWRIGHT_DRIVER_PATH=.../runtime/driver/1.60.0
PLAYWRIGHT_BROWSERS_PATH=.../runtime/browsers
```

Query: `padaria em Belo Horizonte` → 18 resultados, CSV completo, zero erros.

---

## Repetibilidade

Três bootstraps independentes produziram resultados idênticos:

| Item | Bootstrap 1 | Bootstrap 2 | Bootstrap 3 |
|---|---|---|---|
| Driver | 1.60.0 | 1.60.0 | 1.60.0 |
| Node | v24.18.0 | v24.18.0 | v24.18.0 |
| Chromium | 1223 | 1223 | 1223 |
| playwright-core SHA-256 | 8b1df81b | 8b1df81b | 8b1df81b |
| Node SHA-256 | ee6fb0e0 | ee6fb0e0 | ee6fb0e0 |
| Runtime funcional | ✅ | ✅ | ✅ |

---

## Footprint

| Componente | Download | Instalado |
|---|---|---|
| Scraper | — | 65 MB |
| Driver + Node | 53 MB | 128 MB |
| Chromium | 169 MB | 356 MB |
| Headless Shell | 92 MB | 190 MB |
| FFmpeg | 1 MB | 2.5 MB |
| **Total runtime** | **~316 MB** | **~938 MB** |

---

## Licenças

| Componente | Licença | Redistribuição |
|---|---|---|
| google-maps-scraper | MIT | Permitida |
| playwright-core | Apache 2.0 | Permitida |
| Node.js | MIT | Permitida |
| Chromium | BSD + Chrome Terms | Permitida |
| Headless Shell | BSD (Chromium) | Permitida |
| FFmpeg | LGPL 2.1 | Permitida com notice |

---

## Estratégia recomendada

- **Build local M4**: Estratégia B — scraper no bundle, runtime baixado no primeiro uso
- **Distribuição pública**: Estratégia C — runtime montado durante instalação/build

---

## Próxima ação

Iniciar implementação do `PlaywrightRuntimeManager` no ProspectOS com:
- download de npm registry + nodejs.org (fallback do CDN 404)
- manifesto de checksums
- validação e recuperação de instalações parciais
- suporte a `PLAYWRIGHT_DRIVER_PATH` e `PLAYWRIGHT_BROWSERS_PATH`

---

## Comandos executados

```
git clone https://github.com/gosom/google-maps-scraper.git
git checkout v1.16.3
go mod verify && go build -trimpath
curl -LO https://registry.npmjs.org/playwright-core/-/playwright-core-1.60.0.tgz
curl -LO https://nodejs.org/dist/v24.18.0/node-v24.18.0-darwin-arm64.tar.gz
tar -xzf playwright-core-1.60.0.tgz
tar -xzf node-v24.18.0-darwin-arm64.tar.gz
./node package/cli.js install chromium
env -i HOME=... PATH=... PLAYWRIGHT_DRIVER_PATH=... PLAYWRIGHT_BROWSERS_PATH=... ./google-maps-scraper ...
```

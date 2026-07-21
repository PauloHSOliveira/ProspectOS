# Gate 1 — Prova Técnica do Runtime no Mac M4

Data: 2026-07-20  
Veredito: **BLOCKED**

O runtime Python, PyInstaller e Keychain foi comprovado em Apple Silicon. O Gate permanece bloqueado porque não existe artefato oficial `darwin-arm64` para o scraper Google Maps e porque o `requirements.txt` possui conflito de dependências.

## Resumo

| Questão | Resultado |
| --- | --- |
| Dependências Python instaladas | Não — conflito `instagrapi` × `requests` |
| Testes Python | Parcial — 140 testes direcionados passaram |
| PyInstaller arm64 | Sim |
| Backend source mode | Sim, com monkeypatch temporário |
| Keychain via keyring | Sim |
| Scraper arm64 | Não disponível oficialmente |
| Node externo necessário | Inconclusivo |
| Chromium disponível | Inconclusivo para o scraper |
| CSV compatível | Inconclusivo |
| stdout compatível | Inconclusivo |
| Encerramento da árvore | Inconclusivo |
| Working tree limpa | Não — arquivo pré-existente não rastreado |

## Ambiente real

| Item | Valor |
| --- | --- |
| Hardware | Apple M4 |
| OS | macOS 26.4, build 25E246 |
| Arquitetura | arm64 |
| Branch | `main` |
| HEAD | `bebc2f1be6b1b8617ecee802cd3132f9647449b2` |
| Working tree inicial | `?? AUDITORIA_MULTIPLATAFORMA.md` |
| Node | `v22.22.2` |
| Electron declarado | `38.2.0` |
| Electron resolvido | Não; `desktop/node_modules` ausente |
| electron-builder declarado | `26.0.12` |
| electron-builder resolvido | Não; `desktop/node_modules` ausente |
| Python | `3.14.5`, `/opt/homebrew/bin/python3` |
| PyInstaller no venv | `6.21.0` |
| Go | Ausente |

O runtime Orca estava indisponível (`stale_bootstrap`), portanto não foi possível criar dispatches rastreados para agentes OpenCode.

## Dependências Python

O `pip install -r backend/requirements.txt` falhou durante a resolução:

```text
instagrapi 2.18.3 requires requests>=2.34.2,<3
requirements.txt pins requests==2.32.3
```

As demais dependências foram instaladas temporariamente no venv para isolar o runtime Apple Silicon. PyInstaller 6.21.0 e as wheels nativas/universal2 relevantes instalaram sob Python 3.14.5.

`pip check`, após a instalação temporária parcial, confirmou também ausência de `PySocks` para `instagrapi`; isso decorre de não ser possível instalar a árvore declarada de dependências de forma consistente.

## Testes Python

| Suíte | Passed | Failed | Skipped | Duração | Observação |
| --- | ---: | ---: | ---: | ---: | --- |
| Completa, raiz do repo | — | coleta falhou | — | 4.78s | cwd incorreto para imports |
| Completa, `backend/` | — | coleta falhou | — | 0.91s | `instagrapi` incompleto pelo conflito |
| Paths | 15 | 0 | 0 | 0.26s | passou |
| Jobs | 40 | 0 | 0 | 1.28s | passou |
| Database | 11 | 0 | 0 | 0.40s | passou |
| Processamento | 65 | 0 | 0 | 0.15s | passou |
| PDF/Diagnóstico | 9 | 0 | 0 | 0.51s | passou |

A falha de coleta integral é de dependência, não de macOS. Os testes de socket em `test_paths.py` também passaram quando executados fora do sandbox restritivo.

## PyInstaller

- Versão: `6.21.0`.
- Smoke test: passou.
- Artefato executado:

```text
Mach-O 64-bit executable arm64
Non-fat file ... is architecture: arm64
ProspectOS PyInstaller arm64 smoke test
```

O spec real falha antes da análise por exigir `backend/google-maps-scraper.exe`, que não existe no checkout. Trata-se de limitação do spec/projeto, não de incompatibilidade do PyInstaller com M4.

## Keychain

- Backend: `keyring.backends.macOS.Keyring`
- Prioridade: `5`
- Fluxo `set → get → comparação → delete → get None`: passou.
- O secret temporário não foi impresso e foi removido.

O Keychain via `keyring` funciona neste macOS. Assinatura/notarização ainda requer teste em um bundle distribuível.

## Scraper oficial

Upstream: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper).

- Release atual: `v1.16.3`, publicado em 2026-07-13.
- Asset atual: somente `google_maps_scraper-1.16.3-windows-amd64.exe` (60.99 MB).
- Histórico consultado: 73 releases.
- Releases antigos oferecem `darwin-amd64`, mas **nenhum** oferece `darwin-arm64`.

Não foi executado binário do scraper: usar Darwin amd64 em um M4 violaria a regra de não usar emulação. Assim:

```text
External Node required: inconclusive
CSV compatible: inconclusive
stdout compatible: inconclusive
termination works: inconclusive
```

O parser atual espera JSON por linha com mensagens como `X places found` e `job finished`; isso não foi validado contra stdout real arm64. O parser CSV espera, entre outros, `place_id`, `title`, `category`, `address`, `phone`, `website`, `review_rating`, `review_count` e `input_id`; também não houve CSV real arm64 para comparação.

## Backend em modo fonte

O backend subiu usando dados monkeypatched em `/tmp/prospectos-gate1-data`:

- porta: `57117`;
- resposta em `/`: HTTP 404 esperado, pois `frontend/dist` não existe;
- arquivos temporários: `leads.db`, `logs/prospeccao.log`, `porta.txt`;
- recebeu SIGTERM e encerrou com código `143`.

`PROSPECTOS_DATA_DIR` não é suportada pelo código atual; em modo fonte, `paths.py` usa diretamente `backend/`. O monkeypatch foi necessário para não tocar no banco real.

## Bloqueadores confirmados

| # | Prioridade | Bloqueador | Evidência | Próxima ação |
| ---: | --- | --- | --- | --- |
| 1 | P0 | Não existe release oficial `darwin-arm64` do scraper | 73 releases sem asset arm64 | Definir estratégia oficial de distribuição/build do scraper |
| 2 | P0 | Spec exige `.exe` Windows e `node/node.exe` | `backend/prospectos.spec` | Adaptar após decidir o runtime do scraper |
| 3 | P1 | `requirements.txt` impossível de resolver | `instagrapi` requer requests >= 2.34.2; pin é 2.32.3 | Corrigir pins em PR separado |
| 4 | P1 | Frontend distribuível ausente | `frontend/dist` inexistente | Definir/buildar artefato frontend |
| 5 | P2 | Dados em fonte ignoram `PROSPECTOS_DATA_DIR` | Smoke exigiu monkeypatch | Tratar em PlatformPaths |
| 6 | P2 | Encerramento da árvore não comprovado | Sem scraper arm64 e sem process group | Testar após escolher runtime |

## Decisões necessárias

### Técnicas

Definir se o scraper será compilado e distribuído oficialmente para arm64, se será aguardado suporte upstream, ou se será substituído.

### Produto

Decidir se a busca Google Maps é obrigatória no primeiro build macOS.

### Distribuição

Definir a estratégia de Chromium somente após existir um runtime arm64 testável: bundle, download gerenciado ou navegador instalado.

## Recomendação do PR 1

Não iniciar `PlatformPaths` como primeiro PR de portabilidade M4. Primeiro resolver a decisão P0 sobre o scraper arm64; sem ela, o PR só deslocaria caminhos de uma dependência que não consegue executar no M4.

## Temporários e limpeza

| Caminho | Finalidade | Tamanho aproximado |
| --- | --- | ---: |
| `/tmp/prospectos-gate1-venv` | venv Python | 214 MB |
| `/tmp/prospectos-gate1-logs` | logs de evidência | 168 KB |
| `/tmp/prospectos-gate1-pyinstaller-smoke-dist` | executável arm64 | 8 MB |
| `/tmp/prospectos-gate1-pyinstaller-smoke-build` | workpath PyInstaller | 11 MB |
| `/tmp/prospectos-gate1-scraper` | metadados oficiais | 540 KB |
| `/tmp/prospectos-gate1-data` | banco e logs temporários | 108 KB |

```bash
rm -rf \
  /tmp/prospectos-gate1-venv \
  /tmp/prospectos-gate1-logs \
  /tmp/prospectos-gate1-pyinstaller-smoke-dist \
  /tmp/prospectos-gate1-pyinstaller-smoke-build \
  /tmp/prospectos-gate1-scraper \
  /tmp/prospectos-gate1-data \
  /tmp/prospectos-gate1-home \
  /tmp/prospectos-gate1-dist \
  /tmp/prospectos-gate1-build
```

## Conclusão

```text
Podemos iniciar o PR 1? no
```

Próxima ação única recomendada: definir e obter uma estratégia oficial para um scraper Google Maps nativo arm64 antes de qualquer PR de portabilidade.

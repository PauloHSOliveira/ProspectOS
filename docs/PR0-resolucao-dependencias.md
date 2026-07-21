# PR 0 — Corrigir e tornar reproduzíveis as dependências Python do ProspectOS

## Relatório técnico

### 1. Verdict

**PASS**

| Questão                         | Resultado                                    |
| ------------------------------- | -------------------------------------------- |
| Instalação limpa                | ✅ `pip install -r requirements.txt` exit 0  |
| `pip check`                     | ✅ No broken requirements found              |
| Python 3.14                     | ✅ Todas as dependências suportam            |
| Import `requests`               | ✅ 2.34.2                                    |
| Import `instagrapi`             | ✅ 2.18.3                                    |
| PySocks                         | ✅ 1.7.1 (transitivo, resolvido)            |
| Suíte completa coletada         | ✅ 293 testes                                |
| Suíte completa                  | ✅ 293 passed, 0 failed, 0 skipped          |
| Testes Instagram                | ✅ 35 passed (mocks, sem login real)        |
| PyInstaller dependency analysis | ✅ Onefile build ok; spec Windows falha (esperado) |
| Diff restrito                   | ✅ 1 arquivo, 1 linha                       |
| Commit criado                   | ✅ `a4d3bb7`                                 |

---

### 2. Root cause

**Conflito original:**

- `instagrapi==2.18.3` declara `Requires-Dist: requests<3,>=2.34.2`
- `requirements.txt` pinava `requests==2.32.3` (abaixo do mínimo 2.34.2)
- O resolver do pip recusava instalar: `ResolutionImpossible`

`google-genai==2.10.0` também exige `requests>=2.28.1,<3.0.0` (compatível).

**PySocks:** `instagrapi` declara `PySocks>=1.7.1,<2` como dependência obrigatória. Após corrigir `requests`, o pip instala `PySocks` automaticamente. Nenhum código do ProspectOS importa `socks` diretamente.

---

### 3. Solução aplicada

**1 linha alterada em `backend/requirements.txt`:**

```diff
-requests==2.32.3
+requests==2.34.2
```

`requests==2.34.2` é a versão mais recente da série 2.x, atende `instagrapi>=2.34.2` e mantém o estilo de pin exato do projeto.

Nenhuma outra dependência foi alterada. PySocks não foi pinado explicitamente por ser resolvido automaticamente.

---

### 4. Validação

| Etapa                      | Comando                                      | Resultado       |
| -------------------------- | -------------------------------------------- | --------------- |
| Instalação limpa           | `pip install -r requirements.txt`            | exit 0          |
| Integridade                | `pip check`                                  | Sem quebras     |
| Imports runtime            | `python -c "import requests, instagrapi..."` | Todos ok        |
| Testes                     | `python -m pytest -v`                        | 293 passed      |
| PyInstaller (onefile)      | `python -m PyInstaller --onefile app.py`     | Build completo  |
| PyInstaller (spec Windows) | `python -m PyInstaller prospectos.spec`      | Falha esperada (arquivos Windows ausentes no macOS) |

---

### 5. Arquivos modificados

```
backend/requirements.txt  | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

---

### 6. Commit

```
SHA:      a4d3bb783b388228fc0fc77569854d29a7cf8141
Mensagem: fix(backend): resolve Python dependency conflicts
Branch:   main
HEAD:     bebc2f1 → a4d3bb7
```

---

### 7. Comandos para reproduzir

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend/requirements.txt
python -m pip check
cd backend && python -m pytest -v
```

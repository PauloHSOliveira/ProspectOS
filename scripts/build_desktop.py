"""Build ProspectOS.app for darwin-arm64 — orchestrator.

Usage:
    python scripts/build_desktop.py [--clean] [--skip-frontend] [--skip-scraper]

Requires: Python 3, PyInstaller, Node/npm, Go, Xcode CLI.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
DESKTOP_DIR = REPO_ROOT / "desktop"
SHARED_DIR = REPO_ROOT / "shared"
SCRIPTS_DIR = REPO_ROOT / "scripts"

STAGING_DIR = DESKTOP_DIR / ".runtime-resources"

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
PYTHON = os.environ.get("PROSPECTOS_PYTHON", sys.executable)

DEFAULT_TARGET = "darwin-arm64" if IS_MACOS else "win32-x64" if IS_WINDOWS else None
TARGET = os.environ.get("PROSPECTOS_BUILD_TARGET", DEFAULT_TARGET)
if not TARGET:
    _bail("Nao foi possivel determinar target. Use --target ou env PROSPECTOS_BUILD_TARGET.")
STAGING_TARGET = STAGING_DIR / TARGET

SCRAPER_OUTPUT_NAME = {
    "darwin-arm64": "google-maps-scraper",
    "darwin-x64": "google-maps-scraper",
    "win32-x64": "google-maps-scraper.exe",
    "linux-x64": "google-maps-scraper",
}.get(TARGET, "google-maps-scraper")


def _bail(msg: str):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _info(msg: str):
    print(f"  • {msg}")


def _ok(msg: str):
    print(f"  ✓ {msg}")


def _step(n: int, total: int, label: str):
    print(f"\n[{n}/{total}] {label}")
    print("-" * 50)


def _du(path: Path) -> str:
    if not path.exists():
        return "N/A"
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    for unit in ("B", "KB", "MB", "GB"):
        if total < 1024:
            return f"{total:.1f}{unit}"
        total /= 1024
    return f"{total:.1f}TB"


def _run(cmd, cwd=None, **kwargs):
    _info(f"$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd or REPO_ROOT), **kwargs)
    if result.returncode != 0:
        _bail(f"Comando falhou (exit={result.returncode})")
    return result


TOTAL_STEPS = 8


def validate_environment():
    _step(1, TOTAL_STEPS, "Validando ambiente")

    if IS_MACOS:
        arch = os.uname().machine
        if arch != "arm64":
            _bail(f"Requer arm64, detectado: {arch}")
        _ok(f"Sistema: {sys.platform}-{arch}")
        result = subprocess.run(
            ["xcrun", "--show-sdk-path"], capture_output=True, text=True
        )
        if result.returncode != 0:
            _info("  aviso: Xcode CLI pode nao estar instalado")
        else:
            _info(f"Xcode SDK: {result.stdout.strip()}")
    elif IS_WINDOWS:
        arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
        if arch not in ("amd64", "x86_64", ""):
            _info(f"  aviso: arquitetura detectada: {arch}")
        _ok(f"Sistema: {sys.platform}-{arch or 'amd64'}")
    else:
        _bail(f"Plataforma nao suportada: {sys.platform}. Use macOS ou Windows.")

    try:
        import PyInstaller  # noqa: F401
        _info(f"PyInstaller: {__import__('PyInstaller').__version__}")
    except ImportError:
        _bail("PyInstaller nao instalado. pip install 'pyinstaller>=6.21,<7'")

    result = subprocess.run(["go", "version"], capture_output=True, text=True)
    if result.returncode != 0:
        _bail("Go nao encontrado. Instale com: brew install go (macOS) ou choco install go (Windows)")
    _info(f"Go: {result.stdout.strip()}")

    result = subprocess.run(["node", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        _bail("Node.js nao encontrado.")
    _info(f"Node: {result.stdout.strip()}")

    _ok("Ambiente OK")


def build_frontend():
    _step(2, TOTAL_STEPS, "Build do frontend")

    frontend_dist = FRONTEND_DIR / "dist"
    if frontend_dist.exists():
        _info(f"Removendo frontend/dist existente ({_du(frontend_dist)})")
        shutil.rmtree(frontend_dist)

    lockfile = FRONTEND_DIR / "package-lock.json"
    npm_cmd = "npm ci" if lockfile.exists() else "npm install"
    _info(f"Executando: {npm_cmd}")
    _run(npm_cmd.split(), cwd=FRONTEND_DIR)

    _info("Executando: npm run build")
    _run(["npm", "run", "build"], cwd=FRONTEND_DIR)

    if not frontend_dist.exists():
        _bail("npm run build concluido mas frontend/dist nao foi criado")
    _ok(f"Frontend pronto ({_du(frontend_dist)})")


def build_backend():
    _step(3, TOTAL_STEPS, "Build do backend PyInstaller")

    backend_dist = BACKEND_DIR / "dist"
    if backend_dist.exists():
        _info(f"Removendo backend/dist existente ({_du(backend_dist)})")
        shutil.rmtree(backend_dist)

    script = SCRIPTS_DIR / "build_backend.py"
    if not script.exists():
        _bail(f"Script de build do backend nao encontrado: {script}")

    _run([PYTHON, str(script), "--skip-frontend"], cwd=REPO_ROOT)

    bundle_dir = backend_dist / "ProspectOS"
    executable = bundle_dir / ("ProspectOS.exe" if IS_WINDOWS else "ProspectOS")
    if not executable.exists():
        _bail(f"Executavel do backend nao encontrado: {executable}")

    if IS_MACOS:
        result = subprocess.run(["file", str(executable)], capture_output=True, text=True)
        _info(f"file: {result.stdout.strip()}")

        result = subprocess.run(
            ["lipo", "-info", str(executable)], capture_output=True, text=True
        )
        _info(f"lipo: {result.stdout.strip()}")

        if "arm64" not in result.stdout:
            _bail(f"Backend nao e arm64:\n{result.stdout}")

        result = subprocess.run(
            ["otool", "-L", str(executable)], capture_output=True, text=True
        )
        for line in result.stdout.split("\n"):
            if "/opt/homebrew" in line or "/usr/local" in line:
                _info(f"  aviso: dependencia externa: {line.strip()}")

    _ok(f"Backend pronto: {_du(bundle_dir)}")


def build_scraper():
    _step(4, TOTAL_STEPS, "Build do scraper Go arm64")

    artifacts_file = SCRIPTS_DIR / "native-artifact-sources.json"
    if not artifacts_file.exists():
        _bail(f"Arquivo de fontes nao encontrado: {artifacts_file}")

    sources = json.loads(artifacts_file.read_text())
    scraper_cfg = sources.get("scraper", {})
    upstream = scraper_cfg.get("upstream", "gosom/google-maps-scraper")
    tag = scraper_cfg.get("tag", "v1.16.3")
    commit = scraper_cfg.get("commit", "")

    scraper_out = STAGING_TARGET / "scraper" / SCRAPER_OUTPUT_NAME
    if scraper_out.exists():
        _info(f"Removendo scraper existente")
        scraper_out.unlink()

    STAGING_TARGET.mkdir(parents=True, exist_ok=True)

    temp_dir = REPO_ROOT / ".tmp-scraper-build"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    _info(f"Clonando {upstream} tag={tag}")
    _run(["git", "clone",
          f"https://github.com/{upstream}.git",
          str(temp_dir),
          "--depth", "1",
          "--branch", tag])

    if commit:
        _info(f"Verificando commit {commit}")
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_dir,
            capture_output=True, text=True,
        )
        actual_commit = result.stdout.strip()
        if actual_commit != commit:
            _bail(f"Commit mismatch: esperado {commit}, obtido {actual_commit}")

    _info("Verificando modulos Go")
    _run(["go", "mod", "verify"], cwd=temp_dir)

    _info("Compilando scraper")
    scraper_dir = STAGING_TARGET / "scraper"
    scraper_dir.mkdir(parents=True, exist_ok=True)

    env = {**os.environ}
    if TARGET == "win32-x64":
        env["GOOS"] = "windows"
        env["GOARCH"] = "amd64"
        env["CGO_ENABLED"] = "0"
    elif TARGET == "darwin-arm64":
        env["GOOS"] = "darwin"
        env["GOARCH"] = "arm64"
        env["CGO_ENABLED"] = "0"

    _run([
        "go", "build",
        "-trimpath",
        "-o", str(scraper_out),
        ".",
    ], cwd=temp_dir, env=env)

    if not scraper_out.exists():
        _bail(f"Scraper nao foi compilado: {scraper_out}")

    if IS_MACOS:
        result = subprocess.run(["file", str(scraper_out)], capture_output=True, text=True)
        _info(f"file: {result.stdout.strip()}")

        result = subprocess.run(
            ["lipo", "-info", str(scraper_out)], capture_output=True, text=True
        )
        _info(f"lipo: {result.stdout.strip()}")

        if "arm64" not in result.stdout and TARGET == "darwin-arm64":
            _bail(f"Scraper nao e arm64:\n{result.stdout}")

    import hashlib
    sha = hashlib.sha256(scraper_out.read_bytes()).hexdigest()
    _info(f"SHA-256: {sha}")

    license_src = temp_dir / "LICENSE"
    if license_src.exists():
        shutil.copy2(license_src, scraper_dir / "LICENSE")
        _info("Licenca copiada")

    shutil.rmtree(temp_dir)

    scraper_out.chmod(0o755)
    _ok(f"Scraper pronto: {_du(scraper_dir)}")


def stage_resources():
    _step(5, TOTAL_STEPS, "Montando staging de recursos")

    STAGING_TARGET.mkdir(parents=True, exist_ok=True)

    shared_dest = STAGING_TARGET / "shared"
    shared_dest.mkdir(parents=True, exist_ok=True)
    for f in ["runtime-targets.json", "playwright-runtime-targets.json"]:
        src = SHARED_DIR / f
        if src.exists():
            shutil.copy2(src, shared_dest / f)
            _info(f"  shared/{f} copiado")

    scraper_src = STAGING_TARGET / "scraper" / SCRAPER_OUTPUT_NAME
    if scraper_src.exists():
        _info(f"  scraper ja presente ({_du(scraper_src.parent)})")
    else:
        _info("  aviso: scraper ausente, sera necessario builda-lo antes")

    _info(f"Staging em: {STAGING_TARGET}")

    backend_dist = BACKEND_DIR / "dist"
    if backend_dist.exists():
        _ok(f"Backend dist presente ({_du(backend_dist)})")
    else:
        _info("  aviso: backend dist ausente")

    _ok("Staging montado")


def validate_staging():
    _step(6, TOTAL_STEPS, "Validando staging")

    required = [
        ("Manifesto runtime", SHARED_DIR / "runtime-targets.json"),
        ("Manifesto Playwright", SHARED_DIR / "playwright-runtime-targets.json"),
        ("Script build_backend", SCRIPTS_DIR / "build_backend.py"),
    ]

    if STAGING_TARGET.exists():
        required.extend([
            ("Scraper", STAGING_TARGET / "scraper" / SCRAPER_OUTPUT_NAME),
            ("Manifesto shared", STAGING_TARGET / "shared" / "runtime-targets.json"),
            ("Manifesto Playwright shared", STAGING_TARGET / "shared" / "playwright-runtime-targets.json"),
        ])

    for label, p in required:
        if not p.exists():
            _info(f"  aviso: {label} ausente: {p}")
        else:
            _info(f"  {label}: {p}")

    backend_dist = BACKEND_DIR / "dist" / "ProspectOS"
    if backend_dist.exists():
        backend_exe = backend_dist / ("ProspectOS.exe" if IS_WINDOWS else "ProspectOS")
        if backend_exe.exists():
            if not IS_WINDOWS:
                if not os.access(str(backend_exe), os.X_OK):
                    _bail(f"Backend sem permissao de execucao: {backend_exe}")
                backend_exe.chmod(0o755)
            _ok(f"Backend executavel: {backend_exe}")
        _ok(f"Backend bundle: {backend_dist} ({_du(backend_dist)})")

    if STAGING_TARGET.exists():
        scraper = STAGING_TARGET / "scraper" / SCRAPER_OUTPUT_NAME
        if scraper.exists():
            if not IS_WINDOWS:
                if not os.access(str(scraper), os.X_OK):
                    _bail(f"Scraper sem permissao de execucao: {scraper}")
                scraper.chmod(0o755)
            _ok(f"Scraper executavel: {scraper}")

    _ok("Staging validado")


def build_electron():
    _step(7, TOTAL_STEPS, "Build Electron (electron-builder)")

    output_dir = DESKTOP_DIR / "saida"
    if output_dir.exists():
        _info(f"Removendo output existente ({_du(output_dir)})")
        shutil.rmtree(output_dir)

    if not (DESKTOP_DIR / "node_modules").exists():
        _info("node_modules ausente, executando npm ci")
        _run(["npm", "ci"], cwd=DESKTOP_DIR)

    _info("Configurando variaveis para build local (sem assinatura)")
    env = {**os.environ, "CSC_IDENTITY_AUTO_DISCOVERY": "false"}
    if "ELECTRON_BUILDER_ALLOW_UNRESOLVED_DEPENDENCIES" not in env:
        env["ELECTRON_BUILDER_ALLOW_UNRESOLVED_DEPENDENCIES"] = "1"

    if TARGET.startswith("darwin"):
        _run(
            ["npx", "electron-builder", "--config", "electron-builder.yml",
             "--mac", "dir", "--arm64", "--publish", "never"],
            cwd=DESKTOP_DIR,
            env=env,
        )
    elif TARGET == "win32-x64":
        _run(
            ["npx", "electron-builder", "--config", "electron-builder.yml",
             "--win", "--x64", "--publish", "never"],
            cwd=DESKTOP_DIR,
            env=env,
        )
    else:
        _bail(f"Target nao suportado pelo Electron: {TARGET}")

    _ok("Electron build concluido")


def validate_app():
    _step(TOTAL_STEPS, TOTAL_STEPS, "Validando aplicativo")

    output_dir = DESKTOP_DIR / "saida"

    if TARGET.startswith("darwin"):
        app_dirs = list(output_dir.rglob("*.app"))
        if not app_dirs:
            _bail(f"Nenhum .app encontrado em {output_dir}")
        app_path = app_dirs[0]
        _info(f"Aplicativo: {app_path}")

        _run(["plutil", "-p", str(app_path / "Contents" / "Info.plist")])

        electron_bin = app_path / "Contents" / "MacOS" / "ProspectOS"
        _run(["file", str(electron_bin)])
        _run(["lipo", "-info", str(electron_bin)])

        checks = [
            ("Electron", electron_bin),
            ("Backend", app_path / "Contents" / "Resources" / "backend" / "ProspectOS"),
            ("Scraper", app_path / "Contents" / "Resources" / "scraper" / SCRAPER_OUTPUT_NAME),
        ]

        for label, p in checks:
            if not p.exists():
                _bail(f"{label} nao encontrado em: {p}")
                continue
            result = subprocess.run(["file", str(p)], capture_output=True, text=True)
            _info(f"  {label}: {result.stdout.strip()}")
            result = subprocess.run(
                ["lipo", "-info", str(p)], capture_output=True, text=True
            )
            _info(f"    lipo: {result.stdout.strip()}")
            if not os.access(str(p), os.X_OK):
                _bail(f"{label} sem permissao de execucao")
            if "arm64" not in result.stdout:
                _bail(f"{label} nao e arm64")

        shared_manifest = app_path / "Contents" / "Resources" / "shared" / "runtime-targets.json"
        if not shared_manifest.exists():
            _bail(f"Manifesto compartilhado ausente: {shared_manifest}")

        result = subprocess.run(
            ["codesign", "-dv", "--verbose=4", str(app_path)],
            capture_output=True, text=True
        )
        _info(f"Assinatura:\n{result.stdout}\n{result.stderr}")

        total = sum(f.stat().st_size for f in app_path.rglob("*") if f.is_file())
        _info(f"Tamanho total: {_du(app_path)}")

        _ok(f"\n{'=' * 60}")
        _ok(f"  ProspectOS.app pronto!")
        _ok(f"  Path: {app_path}")
        _ok(f"  Tamanho: {_du(app_path)}")
        _ok(f"{'=' * 60}")

    elif TARGET == "win32-x64":
        win_dirs = [d for d in output_dir.iterdir() if d.is_dir()]
        if not win_dirs:
            _bail(f"Nenhum output do Windows encontrado em {output_dir}")
        app_path = win_dirs[0]
        _info(f"Aplicativo: {app_path}")

        exe_path = app_path / "ProspectOS.exe"
        if not exe_path.exists():
            _bail(f"Executavel nao encontrado: {exe_path}")
        _info(f"Executavel: {exe_path} ({_du(exe_path)})")

        for label, rel in [
            ("Backend", "resources/backend/ProspectOS.exe"),
            ("Scraper", f"resources/scraper/{SCRAPER_OUTPUT_NAME}"),
            ("Manifesto runtime", "resources/shared/runtime-targets.json"),
            ("Manifesto Playwright", "resources/shared/playwright-runtime-targets.json"),
        ]:
            p = app_path / rel
            if not p.exists():
                _bail(f"{label} nao encontrado em: {p}")
            _info(f"  {label}: {p}")

        total = sum(f.stat().st_size for f in app_path.rglob("*") if f.is_file())
        _info(f"Tamanho total: {_du(app_path)}")

        _ok(f"\n{'=' * 60}")
        _ok(f"  ProspectOS.exe pronto!")
        _ok(f"  Path: {app_path}")
        _ok(f"  Tamanho: {_du(app_path)}")
        _ok(f"{'=' * 60}")
    else:
        _bail(f"Validacao nao implementada para target: {TARGET}")


def main():
    parser = argparse.ArgumentParser(
        description="Build ProspectOS desktop app"
    )
    parser.add_argument("--target", default=None, help="build target (auto-detect if omitted)")
    parser.add_argument("--clean", action="store_true", help="clean build dirs first")
    parser.add_argument("--skip-frontend", action="store_true", help="skip frontend build")
    parser.add_argument("--skip-scraper", action="store_true", help="skip scraper build")
    args = parser.parse_args()

    global TARGET, STAGING_TARGET, SCRAPER_OUTPUT_NAME
    if args.target:
        TARGET = args.target
        STAGING_TARGET = STAGING_DIR / TARGET
        SCRAPER_OUTPUT_NAME = {
            "darwin-arm64": "google-maps-scraper",
            "darwin-x64": "google-maps-scraper",
            "win32-x64": "google-maps-scraper.exe",
            "linux-x64": "google-maps-scraper",
        }.get(TARGET, "google-maps-scraper")

    print("=" * 60)
    print(f"  ProspectOS — Desktop Build ({TARGET})")
    print("=" * 60)

    start = time.time()

    if args.clean:
        _info("Limpando diretorios de build...")
        for d in [BACKEND_DIR / "dist", BACKEND_DIR / "build",
                  FRONTEND_DIR / "dist", DESKTOP_DIR / "saida",
                  STAGING_DIR]:
            if d.exists():
                shutil.rmtree(d)
                _info(f"  removido: {d}")

    validate_environment()
    build_frontend()
    build_backend()

    if not args.skip_scraper:
        build_scraper()

    stage_resources()
    validate_staging()
    build_electron()
    validate_app()

    elapsed = time.time() - start
    print()
    print("-" * 60)
    print(f"  Build concluido em {elapsed:.0f}s")
    print("-" * 60)


if __name__ == "__main__":
    main()

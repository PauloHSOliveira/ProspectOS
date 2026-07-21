const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const fs = require("fs");

const {
  resolveRuntimeTarget,
  validateExecutable,
} = require("./runtime-target.js");
const {
  BackendProcessSupervisor,
  State,
  TerminationReason,
} = require("./backend-process-supervisor.js");
const logging = require("./logging.js");

let janela = null;
let backendEncerradoDeProposito = false;
let limpando = null;
let PROSPECTOS_DATA_DIR = null;
let PROSPECTOS_LOG_DIR = null;
let PROSPECTOS_RESOURCE_DIR = null;

const backendSupervisor = new BackendProcessSupervisor();

logging.logEvent("app.starting", `Platform: ${process.platform}, packaged: ${app.isPackaged}`);

const primeiraInstancia = app.requestSingleInstanceLock();
if (!primeiraInstancia) {
  logging.logEvent("app.duplicate", "Second instance detected, quitting");
  app.quit();
} else {
  app.on("second-instance", () => {
    logging.logEvent("app.second-instance", "Focusing existing window");
    if (janela) {
      if (janela.isMinimized()) janela.restore();
      janela.focus();
    }
  });
}

function manifestPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "shared", "runtime-targets.json")
    : path.join(__dirname, "..", "shared", "runtime-targets.json");
}

function resolverBackend() {
  const runtime = resolveRuntimeTarget({
    packaged: app.isPackaged,
    manifestPath: manifestPath(),
    resourcesPath: process.resourcesPath || path.join(__dirname, ".."),
    devRoot: path.join(__dirname, ".."),
  });
  return runtime.backendPath;
}

function resolverPaths() {
  PROSPECTOS_DATA_DIR = app.getPath("userData");
  PROSPECTOS_LOG_DIR = app.getPath("logs");

  const nomeBase = path.basename(PROSPECTOS_DATA_DIR);
  if (nomeBase !== "ProspectOS") {
    PROSPECTOS_DATA_DIR = path.join(path.dirname(PROSPECTOS_DATA_DIR), "ProspectOS");
    PROSPECTOS_LOG_DIR = path.join(
      process.platform === "darwin"
        ? path.join(app.getPath("home"), "Library", "Logs", "ProspectOS")
        : path.dirname(PROSPECTOS_DATA_DIR),
      "ProspectOS",
      "logs"
    );
  }

  const PROSPECTOS_TEMP_DIR = path.join(app.getPath("temp"), "ProspectOS");
  PROSPECTOS_RESOURCE_DIR = process.resourcesPath || path.join(__dirname, "..");

  try {
    fs.mkdirSync(PROSPECTOS_DATA_DIR, { recursive: true });
    fs.mkdirSync(PROSPECTOS_LOG_DIR, { recursive: true });
    fs.mkdirSync(PROSPECTOS_TEMP_DIR, { recursive: true });
  } catch (erro) {
    throw new Error(
      `Não foi possível criar os diretórios do ProspectOS:\n${erro.message}`
    );
  }

  return { PROSPECTOS_DATA_DIR, PROSPECTOS_LOG_DIR, PROSPECTOS_TEMP_DIR, PROSPECTOS_RESOURCE_DIR };
}

function ambienteBackend(pathsResolvidos) {
  const runtimeTarget = require("./runtime-target.js").getCurrentTarget();

  return {
    ...process.env,
    PROSPECTOS_NO_BROWSER: "1",
    PROSPECTOS_DATA_DIR: pathsResolvidos.PROSPECTOS_DATA_DIR,
    PROSPECTOS_LOG_DIR: pathsResolvidos.PROSPECTOS_LOG_DIR,
    PROSPECTOS_TEMP_DIR: pathsResolvidos.PROSPECTOS_TEMP_DIR,
    PROSPECTOS_CACHE_DIR: path.join(app.getPath("cache"), "ProspectOS"),
    PROSPECTOS_RESOURCE_DIR: pathsResolvidos.PROSPECTOS_RESOURCE_DIR,
    PROSPECTOS_RUNTIME_MANIFEST: manifestPath(),
    PROSPECTOS_PLAYWRIGHT_RUNTIME_MANIFEST: path.join(
      path.dirname(manifestPath()),
      "playwright-runtime-targets.json"
    ),
    PROSPECTOS_RUNTIME_TARGET: runtimeTarget,
  };
}

function criarJanela(porta) {
  janela = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1000,
    minHeight: 640,
    icon: path.join(__dirname, "prospectos.ico"),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  janela.loadURL(`http://127.0.0.1:${porta}`);
  janela.once("ready-to-show", () => { janela.show(); });
  janela.on("closed", () => { janela = null; });
}

function configurarAutoUpdate() {
  if (!app.isPackaged) return;
  if (process.env.PROSPECTOS_DISABLE_UPDATES === "1") return;
  try {
    const { autoUpdater } = require("electron-updater");
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.checkForUpdatesAndNotify().catch(() => {});
  } catch { }
}

async function iniciar() {
  try {
    const pathsResolvidos = resolverPaths();
    logging.setLogDir(PROSPECTOS_LOG_DIR);

    logging.logEvent("backend.starting", "Resolving backend executable");

    const exe = resolverBackend();

    validateExecutable(exe, "Backend do ProspectOS");

    logging.logEvent("backend.starting", "Backend resolved", { executable: exe });

    const env = ambienteBackend(pathsResolvidos);

    backendSupervisor.onCrash = (crashInfo) => {
      logging.logEvent("backend.crashed", "Backend process crashed", crashInfo);
      dialog.showErrorBox(
        "ProspectOS",
        "O motor do ProspectOS parou de responder. O aplicativo será fechado."
      );
      app.quit();
    };

    const backendUrl = await backendSupervisor.start({
      executable: exe,
      env,
      cwd: path.dirname(exe),
      dataDir: PROSPECTOS_DATA_DIR,
      resourceDir: PROSPECTOS_RESOURCE_DIR,
      logDir: PROSPECTOS_LOG_DIR,
    });

    const port = new URL(backendUrl).port;
    logging.logEvent("backend.ready", "Backend ready via health endpoint", { port });
    criarJanela(port);
    configurarAutoUpdate();
    logging.logEvent("app.ready", "Application window opened", { port });
  } catch (erro) {
    const logPath = PROSPECTOS_LOG_DIR
      ? path.join(PROSPECTOS_LOG_DIR, "prospeccao.log")
      : "logs/prospeccao.log";
    logging.logError("app.startup-failed", erro.message, { logPath });
    dialog.showErrorBox(
      "ProspectOS não conseguiu iniciar",
      `${erro.message}\n\nVeja os logs em: ${logPath}`
    );
    app.quit();
  }
}

async function limparBackend() {
  if (limpando) return limpando;
  limpando = (async () => {
    backendEncerradoDeProposito = true;
    logging.logEvent("backend.stopping", "Shutting down backend");
    await backendSupervisor.stop({ reason: TerminationReason.APP_QUIT });
    logging.logEvent("backend.stopped", "Backend stopped");
  })();
  return limpando;
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    if (backendSupervisor.isRunning()) {
      const diag = backendSupervisor.getDiagnostics();
      const backendUrl = diag.backendUrl;
      if (backendUrl) {
        const parsed = new URL(backendUrl);
        criarJanela(parsed.port);
      }
    } else {
      iniciar();
    }
  }
});

app.on("before-quit", async (event) => {
  if (!backendEncerradoDeProposito) {
    event.preventDefault();
    await limparBackend();
    app.quit();
  }
});

app.on("will-quit", () => {
  if (!backendSupervisor.pid) return;
  backendEncerradoDeProposito = true;
  logging.logEvent("backend.stopping", "Force-stopping backend during quit");
  try {
    backendSupervisor.forceStop({ reason: TerminationReason.APP_QUIT });
  } catch { }
  logging.close();
});

app.whenReady().then(iniciar);

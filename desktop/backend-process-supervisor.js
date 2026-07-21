const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");
const crypto = require("crypto");

const BACKEND_STARTUP_TIMEOUT_MS = 30_000;
const BACKEND_SHUTDOWN_GRACE_MS = 10_000;
const FORCED_TERMINATION_TIMEOUT_MS = 5_000;
const HTTP_READINESS_TIMEOUT_MS = 15_000;
const HTTP_READINESS_POLL_MS = 500;
const PORT_FILE_POLL_MS = 300;
const MAX_LOG_TAIL_LINES = 200;
const MAX_CAPTURED_LINE_LENGTH = 2000;
const IS_WINDOWS = process.platform === "win32";

const State = Object.freeze({
  IDLE: "IDLE",
  STARTING: "STARTING",
  WAITING_FOR_PORT: "WAITING_FOR_PORT",
  WAITING_FOR_HTTP: "WAITING_FOR_HTTP",
  READY: "READY",
  STOPPING: "STOPPING",
  STOPPED: "STOPPED",
  CRASHED: "CRASHED",
  FAILED: "FAILED",
});

const TerminationReason = Object.freeze({
  NORMAL: "NORMAL",
  USER_CANCELLED: "USER_CANCELLED",
  APP_QUIT: "APP_QUIT",
  STARTUP_TIMEOUT: "STARTUP_TIMEOUT",
  EXECUTION_TIMEOUT: "EXECUTION_TIMEOUT",
  BACKEND_CRASH: "BACKEND_CRASH",
  SCRAPER_ERROR: "SCRAPER_ERROR",
  RUNTIME_ERROR: "RUNTIME_ERROR",
  PARENT_EXIT: "PARENT_EXIT",
  FORCED: "FORCED",
  UNKNOWN: "UNKNOWN",
});

const VALID_TRANSITIONS = {
  [State.IDLE]: [State.STARTING],
  [State.STARTING]: [State.WAITING_FOR_PORT, State.STOPPING, State.FAILED],
  [State.WAITING_FOR_PORT]: [State.WAITING_FOR_HTTP, State.STOPPING, State.FAILED, State.CRASHED],
  [State.WAITING_FOR_HTTP]: [State.READY, State.STOPPING, State.FAILED, State.CRASHED],
  [State.READY]: [State.STOPPING, State.CRASHED],
  [State.STOPPING]: [State.STOPPED, State.FAILED],
  [State.STOPPED]: [State.IDLE],
  [State.CRASHED]: [State.IDLE],
  [State.FAILED]: [State.IDLE],
};

function isValidTransition(from, to) {
  const allowed = VALID_TRANSITIONS[from];
  if (!allowed) return false;
  return allowed.includes(to);
}

class BackendProcessSupervisor {
  constructor(options = {}) {
    this._state = State.IDLE;
    this._process = null;
    this._stopPromise = null;
    this._startToken = null;
    this._resolveReadiness = null;
    this._rejectReadiness = null;
    this._readinessPromise = null;
    this._portFileWatcherInterval = null;
    this._httpReadinessInterval = null;
    this._logTail = [];
    this._stdoutLines = 0;
    this._stderrLines = 0;
    this._backendUrl = null;

    this._pid = null;
    this._pgid = null;
    this._startedAt = null;
    this._readyAt = null;
    this._stopRequestedAt = null;
    this._exitedAt = null;
    this._returnCode = null;
    this._signal = null;
    this._terminationReason = null;
    this._gracefulTerminationAttempted = false;
    this._forcedTerminationAttempted = false;
    this._lastOutput = null;

    this._startupTimeoutMs = options.startupTimeoutMs || BACKEND_STARTUP_TIMEOUT_MS;
    this._shutdownGraceMs = options.shutdownGraceMs || BACKEND_SHUTDOWN_GRACE_MS;
    this._forcedTimeoutMs = options.forcedTimeoutMs || FORCED_TERMINATION_TIMEOUT_MS;

    this._onCrash = options.onCrash || null;
    this._onStateChange = options.onStateChange || null;

    this._log("supervisor created");
  }

  // --- Public API ---

  async start({ executable, args, env, cwd, dataDir, resourceDir, logDir } = {}) {
    if (this._state !== State.IDLE) {
      throw new Error(`BackendProcessSupervisor: cannot start from state ${this._state}`);
    }

    this._transitionTo(State.STARTING);
    this._startToken = crypto.randomUUID();
    this._startedAt = new Date().toISOString();
    this._terminationReason = null;
    this._returnCode = null;
    this._signal = null;
    this._exitedAt = null;
    this._readyAt = null;
    this._logTail = [];
    this._stdoutLines = 0;
    this._stderrLines = 0;
    this._gracefulTerminationAttempted = false;
    this._forcedTerminationAttempted = false;
    this._backendUrl = null;
    this._pid = null;
    this._pgid = null;
    this._lastOutput = null;

    this._validateExecutable(executable);

    const portFilePath = path.join(dataDir, "porta.txt");
    this._removeOldPortFile(portFilePath);

    const fullEnv = {
      ...env,
      PROSPECTOS_STARTUP_TOKEN: this._startToken,
      PROSPECTOS_DATA_DIR: dataDir,
      PROSPECTOS_LOG_DIR: logDir,
      PROSPECTOS_TEMP_DIR: path.join(dataDir, "..", "temp", "ProspectOS"),
      PROSPECTOS_RESOURCE_DIR: resourceDir,
    };

    this._log("process starting", { executable });

    const spawnOpts = {
      cwd: cwd || path.dirname(executable),
      env: fullEnv,
      stdio: ["ignore", "pipe", "pipe"],
    };

    if (IS_WINDOWS) {
      spawnOpts.windowsHide = true;
    }

    this._process = spawn(executable, args || [], spawnOpts);

    this._pid = this._process.pid;

    if (!IS_WINDOWS && this._process.pid) {
      try {
        this._pgid = process.platform === "darwin"
          ? this._process.pid
          : this._process.pid;
      } catch {
        this._pgid = null;
      }
    }

    this._log("process started", { pid: this._pid, pgid: this._pgid });

    this._transitionTo(State.WAITING_FOR_PORT);

    this._process.stdout.on("data", (data) => {
      this._onOutput("stdout", data);
    });

    this._process.stderr.on("data", (data) => {
      this._onOutput("stderr", data);
    });

    this._process.on("exit", (code, signal) => {
      this._onExit(code, signal);
    });

    this._process.on("error", (err) => {
      this._onProcessError(err);
    });

    const startupDeadline = Date.now() + this._startupTimeoutMs;

    this._readinessPromise = new Promise((resolve, reject) => {
      this._resolveReadiness = resolve;
      this._rejectReadiness = reject;
      this._startPortFileWatcher(portFilePath, dataDir, startupDeadline);
    });

    return this._readinessPromise;
  }

  async waitUntilReady() {
    if (this._state === State.READY) {
      return this._backendUrl;
    }
    if (this._readinessPromise) {
      return this._readinessPromise;
    }
    throw new Error("BackendProcessSupervisor: waitUntilReady called before start");
  }

  async stop({ reason = TerminationReason.NORMAL, force = false } = {}) {
    if (this._stopPromise) {
      return this._stopPromise;
    }

    this._stopRequestedAt = new Date().toISOString();

    this._stopPromise = (async () => {
      if (this._state === State.IDLE || this._state === State.STOPPED || this._state === State.FAILED) {
        return;
      }

      const wasReady = this._state === State.READY;
      this._transitionTo(State.STOPPING);
      this._terminationReason = reason;
      this._log("stop requested", { reason: this._terminationReason });

      this._cancelTimers();

      if (!this._process || this._process.killed) {
        this._finalizeStop();
        return;
      }

      if (force) {
        await this._performForcedStop();
        this._finalizeStop();
        return;
      }

      this._gracefulTerminationAttempted = true;

      if (IS_WINDOWS) {
        await this._windowsStop();
      } else {
        await this._posixStop();
      }

      this._finalizeStop();
    })();

    return this._stopPromise;
  }

  async forceStop({ reason = TerminationReason.FORCED } = {}) {
    return this.stop({ reason, force: true });
  }

  getState() {
    return this._state;
  }

  isRunning() {
    return this._state === State.READY
      || this._state === State.WAITING_FOR_PORT
      || this._state === State.WAITING_FOR_HTTP
      || this._state === State.STARTING;
  }

  get pid() {
    return this._pid;
  }

  get backendUrl() {
    return this._backendUrl;
  }

  getDiagnostics() {
    return {
      state: this._state,
      pid: this._pid,
      pgid: this._pgid,
      startedAt: this._startedAt,
      readyAt: this._readyAt,
      stopRequestedAt: this._stopRequestedAt,
      exitedAt: this._exitedAt,
      backendUrl: this._backendUrl,
      returnCode: this._returnCode,
      signal: this._signal,
      terminationReason: this._terminationReason,
      gracefulTerminationAttempted: this._gracefulTerminationAttempted,
      forcedTerminationAttempted: this._forcedTerminationAttempted,
      stdoutLineCount: this._stdoutLines,
      stderrLineCount: this._stderrLines,
      lastOutput: this._lastOutput,
    };
  }

  // --- Internal ---

  _log(message, extra = {}) {
    const pid = this._pid ? `[pid=${this._pid}]` : "";
    const state = `[${this._state}]`;
    const parts = [`[backend-supervisor]${state}${pid}`, message];
    if (Object.keys(extra).length) {
      parts.push(JSON.stringify(extra));
    }
    console.log(parts.join(" "));
  }

  _transitionTo(newState) {
    const oldState = this._state;
    if (!isValidTransition(oldState, newState)) {
      this._log("invalid state transition", { from: oldState, to: newState });
      return;
    }
    this._state = newState;
    this._log("state transition", { from: oldState, to: newState });
    if (this._onStateChange) {
      this._onStateChange({ from: oldState, to: newState, supervisor: this });
    }
  }

  _validateExecutable(executable) {
    if (!executable) {
      throw new Error("BackendProcessSupervisor: no executable provided");
    }
    if (!fs.existsSync(executable)) {
      throw new Error(
        `Backend não encontrado:\n${executable}\n\nVerifique se o backend foi compilado.\n`
      );
    }
    const stat = fs.statSync(executable);
    if (stat.isDirectory()) {
      throw new Error(`Backend é um diretório, não um executável: ${executable}`);
    }
  }

  _removeOldPortFile(portFilePath) {
    try {
      if (fs.existsSync(portFilePath)) {
        const oldContent = fs.readFileSync(portFilePath, "utf-8").trim();
        fs.unlinkSync(portFilePath);
        this._log("removed old port file", { oldContent: oldContent.substring(0, 100) });
      }
    } catch (err) {
      this._log("could not remove old port file", { error: err.message });
    }
  }

  _onOutput(stream, data) {
    const text = String(data);
    const lines = text.split("\n").filter(Boolean);

    if (stream === "stdout") {
      this._stdoutLines += lines.length;
    } else {
      this._stderrLines += lines.length;
    }

    for (const line of lines) {
      const truncated = line.length > MAX_CAPTURED_LINE_LENGTH
        ? line.substring(0, MAX_CAPTURED_LINE_LENGTH) + "...[truncated]"
        : line;

      this._logTail.push(truncated);
      if (this._logTail.length > MAX_LOG_TAIL_LINES) {
        this._logTail.shift();
      }
      this._lastOutput = truncated;

      const listenMatch = line.match(/LISTENING_ON=(\d+)/);
      if (listenMatch) {
        const port = Number(listenMatch[1]);
        this._onPortFound(port);
      }
    }
  }

  _onPortFound(port) {
    if (this._state !== State.WAITING_FOR_PORT && this._state !== State.WAITING_FOR_HTTP) {
      return;
    }

    if (port <= 0 || port > 65535) {
      this._log("invalid port from LISTENING_ON", { port });
      return;
    }

    this._log("port discovered via stdout", { port });

    if (this._state === State.WAITING_FOR_PORT) {
      this._transitionTo(State.WAITING_FOR_HTTP);
    }

    this._cancelPortWatcher();
    this._startHttpReadiness(port);
  }

  async _startPortFileWatcher(portFilePath, dataDir, deadline) {
    this._portFileWatcherInterval = setInterval(async () => {
      if (Date.now() > deadline) {
        this._cancelPortWatcher();
        const tail = this._logTail.slice(-50).join("\n");
        const err = new Error(
          `O backend não subiu em tempo hábil.\n\nÚltimos logs:\n${tail}`
        );
        this._terminationReason = TerminationReason.STARTUP_TIMEOUT;
        this._transitionTo(State.FAILED);
        await this.stop({ reason: TerminationReason.STARTUP_TIMEOUT });
        if (this._rejectReadiness) {
          this._rejectReadiness(err);
          this._resolveReadiness = null;
          this._rejectReadiness = null;
        }
        return;
      }

      if (this._state !== State.WAITING_FOR_PORT) {
        return;
      }

      try {
        if (!fs.existsSync(portFilePath)) {
          return;
        }

        const raw = fs.readFileSync(portFilePath, "utf-8").trim();
        let portData;

        try {
          portData = JSON.parse(raw);
        } catch {
          const port = Number(raw);
          if (port > 0 && port <= 65535) {
            this._onPortFileFound(port, portFilePath, dataDir);
          }
          return;
        }

        if (portData && typeof portData === "object") {
          if (portData.startupToken && portData.startupToken !== this._startToken) {
            this._log("port file has wrong startup token, ignoring", { fileToken: portData.startupToken });
            return;
          }
          const port = portData.port;
          if (port > 0 && port <= 65535) {
            this._onPortFileFound(port, portFilePath, dataDir);
          }
        }
      } catch {
      }
    }, PORT_FILE_POLL_MS);
  }

  _onPortFileFound(port, portFilePath, dataDir) {
    if (this._state !== State.WAITING_FOR_PORT) {
      return;
    }

    this._log("port discovered via port file", { port, path: portFilePath });

    try {
      fs.unlinkSync(portFilePath);
      this._log("removed port file after reading");
    } catch {
    }

    this._transitionTo(State.WAITING_FOR_HTTP);
    this._cancelPortWatcher();
    this._startHttpReadiness(port);
  }

  _cancelPortWatcher() {
    if (this._portFileWatcherInterval) {
      clearInterval(this._portFileWatcherInterval);
      this._portFileWatcherInterval = null;
    }
  }

  _startHttpReadiness(port) {
    const url = `http://127.0.0.1:${port}`;
    this._backendUrl = url;
    const deadline = Date.now() + HTTP_READINESS_TIMEOUT_MS;

    this._httpReadinessInterval = setInterval(async () => {
      if (Date.now() > deadline) {
        this._cancelHttpReadiness();
        const tail = this._logTail.slice(-50).join("\n");
        const err = new Error(
          `Backend não respondeu após readiness timeout na porta ${port}.\n\nÚltimos logs:\n${tail}`
        );
        this._terminationReason = TerminationReason.STARTUP_TIMEOUT;
        this._transitionTo(State.FAILED);
        await this.stop({ reason: TerminationReason.STARTUP_TIMEOUT });
        if (this._rejectReadiness) {
          this._rejectReadiness(err);
          this._resolveReadiness = null;
          this._rejectReadiness = null;
        }
        return;
      }

      try {
        const resp = await this._httpGet(`${url}/`);
        if (resp.status >= 200 && resp.status < 400) {
          this._cancelHttpReadiness();
          this._readyAt = new Date().toISOString();
          this._transitionTo(State.READY);
          this._log("readiness confirmed", { url });
          if (this._resolveReadiness) {
            this._resolveReadiness(url);
            this._resolveReadiness = null;
            this._rejectReadiness = null;
          }
        }
      } catch {
      }
    }, HTTP_READINESS_POLL_MS);
  }

  _cancelHttpReadiness() {
    if (this._httpReadinessInterval) {
      clearInterval(this._httpReadinessInterval);
      this._httpReadinessInterval = null;
    }
  }

  _httpGet(url) {
    return new Promise((resolve, reject) => {
      const req = http.get(url, (res) => {
        let data = "";
        res.on("data", (chunk) => { data += chunk; });
        res.on("end", () => resolve({ status: res.statusCode, body: data }));
      });
      req.on("error", reject);
      req.setTimeout(5000, () => { req.destroy(); reject(new Error("timeout")); });
      req.end();
    });
  }

  _onExit(code, signal) {
    this._exitedAt = new Date().toISOString();
    this._returnCode = code;
    this._signal = signal;
    this._process = null;

    this._log("process exited", { code, signal });

    const wasStarting = this._state === State.STARTING
      || this._state === State.WAITING_FOR_PORT
      || this._state === State.WAITING_FOR_HTTP;
    const wasReady = this._state === State.READY;
    const wasStopping = this._state === State.STOPPING;

    if (wasStarting) {
      this._cancelTimers();
      this._transitionTo(State.CRASHED);
      const tail = this._logTail.slice(-50).join("\n");
      const err = new Error(
        `O backend encerrou antes de subir (código ${code}, sinal ${signal}).\n\n` +
        `Últimos logs:\n${tail}`
      );
      if (this._rejectReadiness) {
        this._rejectReadiness(err);
        this._resolveReadiness = null;
        this._rejectReadiness = null;
      }
    } else if (wasReady) {
      this._transitionTo(State.CRASHED);
      this._terminationReason = this._terminationReason || TerminationReason.BACKEND_CRASH;
      this._log("backend crashed while ready");
      if (this._onCrash) {
        this._onCrash({
          code,
          signal,
          diagnostics: this.getDiagnostics(),
          supervisor: this,
        });
      }
    } else if (wasStopping) {
    }
  }

  _onProcessError(err) {
    this._log("process error", { error: err.message });
    if (this._rejectReadiness) {
      this._rejectReadiness(err);
      this._resolveReadiness = null;
      this._rejectReadiness = null;
    }
  }

  async _posixStop() {
    const proc = this._process;
    if (!proc || proc.killed) return;

    this._log("sending SIGTERM to process group");

    try {
      if (this._pgid) {
        process.kill(-this._pgid, "SIGTERM");
      } else {
        proc.kill("SIGTERM");
      }
    } catch (err) {
      this._log("SIGTERM failed, trying process.kill", { error: err.message });
      try { proc.kill("SIGTERM"); } catch {}
    }

    try {
      await this._waitForExit(proc, this._shutdownGraceMs);
    } catch {
      this._forcedTerminationAttempted = true;
      this._log("grace period expired, sending SIGKILL");
      try {
        if (this._pgid) {
          process.kill(-this._pgid, "SIGKILL");
        } else {
          proc.kill("SIGKILL");
        }
      } catch (err) {
        this._log("SIGKILL failed, trying process.kill", { error: err.message });
        try { proc.kill("SIGKILL"); } catch {}
      }
      try {
        await this._waitForExit(proc, this._forcedTimeoutMs);
      } catch {
        this._log("process did not exit after SIGKILL");
      }
    }
  }

  async _windowsStop() {
    const proc = this._process;
    if (!proc || proc.killed) return;

    this._log("sending graceful termination on Windows");

    try {
      proc.kill("SIGTERM");
    } catch {}

    try {
      await this._waitForExit(proc, this._shutdownGraceMs);
      return;
    } catch {
    }

    this._forcedTerminationAttempted = true;
    this._log("grace period expired, using taskkill fallback");

    try {
      const { execSync } = require("child_process");
      execSync(`taskkill /T /F /PID ${this._pid}`, { timeout: this._forcedTimeoutMs });
    } catch (err) {
      this._log("taskkill failed", { error: err.message });
      try { proc.kill(); } catch {}
    }
  }

  async _performForcedStop() {
    this._forcedTerminationAttempted = true;
    const proc = this._process;
    if (!proc || proc.killed) return;

    if (IS_WINDOWS) {
      try {
        const { execSync } = require("child_process");
        execSync(`taskkill /T /F /PID ${this._pid}`, { timeout: this._forcedTimeoutMs });
      } catch {
        try { proc.kill(); } catch {}
      }
    } else {
      try {
        if (this._pgid) {
          process.kill(-this._pgid, "SIGKILL");
        } else {
          proc.kill("SIGKILL");
        }
      } catch {
        try { proc.kill("SIGKILL"); } catch {}
      }
    }

    try {
      await this._waitForExit(proc, this._forcedTimeoutMs);
    } catch {
    }
  }

  _waitForExit(proc, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (proc.killed || proc.exitCode !== null) {
        resolve();
        return;
      }

      const timer = setTimeout(() => {
        reject(new Error("process did not exit in time"));
      }, timeoutMs);

      proc.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });

      proc.once("error", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  _cancelTimers() {
    this._cancelPortWatcher();
    this._cancelHttpReadiness();
  }

  _finalizeStop() {
    this._cancelTimers();

    if (this._process && !this._process.killed) {
      try {
        this._process.stdout.removeAllListeners();
        this._process.stderr.removeAllListeners();
        this._process.removeAllListeners();
      } catch {
      }
    }

    this._process = null;

    if (this._state !== State.FAILED && this._state !== State.CRASHED) {
      this._transitionTo(State.STOPPED);
    }

    this._log("cleanup complete");
  }

  reset() {
    if (this._state === State.STOPPED || this._state === State.FAILED || this._state === State.CRASHED) {
      this._state = State.IDLE;
      this._stopPromise = null;
      this._readinessPromise = null;
      this._resolveReadiness = null;
      this._rejectReadiness = null;
      this._log("reset");
    }
  }
}

module.exports = {
  BackendProcessSupervisor,
  State,
  TerminationReason,
};

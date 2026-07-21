const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { mkdtempSync, writeFileSync, mkdirSync, chmodSync, rmSync, existsSync } = require("node:fs");
const { join } = require("node:path");
const { tmpdir } = require("node:os");
const http = require("node:http");

const {
  BackendProcessSupervisor,
  State,
  TerminationReason,
} = require("../backend-process-supervisor.js");

function createFakeBackendScript(scriptDir) {
  const scriptPath = join(scriptDir, "fake-backend.cjs");
  writeFileSync(scriptPath, `
const http = require("http");
const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ status: "ok", service: "prospectos-backend" }));
});
process.on("SIGTERM", () => { server.close(() => process.exit(0)); });
process.on("SIGINT", () => { server.close(() => process.exit(0)); });
server.listen(0, "127.0.0.1", () => {
  const port = server.address().port;
  console.log("LISTENING_ON=" + port);
});
`, "utf-8");
  chmodSync(scriptPath, 0o755);
  return scriptPath;
}

function createCrashingScript(scriptDir) {
  const scriptPath = join(scriptDir, "crash.cjs");
  writeFileSync(scriptPath, `process.exit(1);\n`, "utf-8");
  chmodSync(scriptPath, 0o755);
  return scriptPath;
}

function createIgnoringScript(scriptDir) {
  const scriptPath = join(scriptDir, "ignore.cjs");
  writeFileSync(scriptPath, `
const http = require("http");
const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ status: "ok" }));
});
process.on("SIGTERM", () => {});
process.on("SIGINT", () => {});
server.listen(0, "127.0.0.1", () => {
  const port = server.address().port;
  console.log("LISTENING_ON=" + port);
});
`, "utf-8");
  chmodSync(scriptPath, 0o755);
  return scriptPath;
}

function createSlowScript(scriptDir) {
  const scriptPath = join(scriptDir, "slow.cjs");
  writeFileSync(scriptPath, `
const http = require("http");
const fs = require("fs");
const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ status: "ok" }));
});
setTimeout(() => {
  server.listen(0, "127.0.0.1", () => {
    const port = server.address().port;
    console.log("LISTENING_ON=" + port);
  });
}, 3000);
setTimeout(() => {}, 30000);
`, "utf-8");
  chmodSync(scriptPath, 0o755);
  return scriptPath;
}

describe("BackendProcessSupervisor", () => {
  let tmpDir;
  let fakeBackendPath;
  let crashingPath;
  let ignoringPath;
  let slowPath;

  before(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "bps-test-"));
    fakeBackendPath = createFakeBackendScript(tmpDir);
    crashingPath = createCrashingScript(tmpDir);
    ignoringPath = createIgnoringScript(tmpDir);
    slowPath = createSlowScript(tmpDir);
  });

  after(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  describe("State transitions", () => {
    it("starts in IDLE state", () => {
      const sup = new BackendProcessSupervisor();
      assert.equal(sup.getState(), State.IDLE);
    });

    it("rejects start from non-IDLE", async () => {
      const sup = new BackendProcessSupervisor();
      Object.assign(sup, { _state: State.READY });
      await assert.rejects(
        () => sup.start({ executable: "/nonexistent" }),
        /cannot start from state/
      );
    });
  });

  describe("Startup validation", () => {
    it("rejects missing executable", async () => {
      const sup = new BackendProcessSupervisor();
      await assert.rejects(
        () => sup.start({ executable: "/nonexistent/bin" }),
        /n[aã]o encontrado/
      );
    });

    it("rejects directory as executable", async () => {
      const sup = new BackendProcessSupervisor();
      await assert.rejects(
        () => sup.start({ executable: tmpDir }),
        /diret[oó]rio/
      );
    });
  });

  describe("Normal lifecycle", () => {
    it("starts and becomes READY", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 15000 });

      const url = await sup.start({
        executable: process.execPath,
        args: [fakeBackendPath],
        env: {},
        cwd: tmpDir,
        dataDir: tmpDir,
        resourceDir: tmpDir,
        logDir: tmpDir,
      });

      assert.ok(url.startsWith("http://127.0.0.1:"));
      assert.equal(sup.getState(), State.READY);
      assert.ok(sup.isRunning());
      assert.ok(sup.backendUrl.startsWith("http://"));

      await sup.stop({ reason: TerminationReason.NORMAL });
      assert.equal(sup.getState(), State.STOPPED);
    });

    it("stop is idempotent", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 15000 });

      await sup.start({
        executable: process.execPath,
        args: [fakeBackendPath],
        env: {},
        cwd: tmpDir,
        dataDir: tmpDir,
        resourceDir: tmpDir,
        logDir: tmpDir,
      });

      const r1 = await sup.stop({ reason: TerminationReason.NORMAL });
      const r2 = await sup.stop({ reason: TerminationReason.NORMAL });
      assert.strictEqual(r1, r2);
      assert.equal(sup.getState(), State.STOPPED);
    });
  });

  describe("Crash detection", () => {
    it("detects crash before readiness", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 5000 });

      await assert.rejects(
        () => sup.start({
          executable: process.execPath,
          args: [crashingPath],
          env: {},
          cwd: tmpDir,
          dataDir: tmpDir,
          resourceDir: tmpDir,
          logDir: tmpDir,
        }),
        /encerrou antes/
      );

      assert.ok(
        sup.getState() === State.CRASHED || sup.getState() === State.FAILED
      );
    });
  });

  describe("Termination reasons", () => {
    it("records NORMAL on clean stop", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 15000 });

      await sup.start({
        executable: process.execPath,
        args: [fakeBackendPath],
        env: {},
        cwd: tmpDir,
        dataDir: tmpDir,
        resourceDir: tmpDir,
        logDir: tmpDir,
      });

      await sup.stop({ reason: TerminationReason.NORMAL });
      const diag = sup.getDiagnostics();
      assert.equal(diag.terminationReason, TerminationReason.NORMAL);
    });

    it("records APP_QUIT on app quit", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 15000 });

      await sup.start({
        executable: process.execPath,
        args: [fakeBackendPath],
        env: {},
        cwd: tmpDir,
        dataDir: tmpDir,
        resourceDir: tmpDir,
        logDir: tmpDir,
      });

      await sup.stop({ reason: TerminationReason.APP_QUIT });
      const diag = sup.getDiagnostics();
      assert.equal(diag.terminationReason, TerminationReason.APP_QUIT);
    });
  });

  describe("Diagnostics", () => {
    it("returns structured diagnostics", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 15000 });

      await sup.start({
        executable: process.execPath,
        args: [fakeBackendPath],
        env: {},
        cwd: tmpDir,
        dataDir: tmpDir,
        resourceDir: tmpDir,
        logDir: tmpDir,
      });

      const diag = sup.getDiagnostics();
      assert.equal(diag.state, "READY");
      assert.ok(diag.pid > 0);
      assert.ok(diag.startedAt);
      assert.ok(diag.readyAt);
      assert.ok(diag.backendUrl);
      assert.equal(diag.gracefulTerminationAttempted, false);
      assert.equal(diag.forcedTerminationAttempted, false);

      await sup.stop({ reason: TerminationReason.NORMAL });
    });
  });

  describe("Force stop", () => {
    it("forceStops a running process", async () => {
      const sup = new BackendProcessSupervisor({
        startupTimeoutMs: 15000,
        shutdownGraceMs: 1000,
      });

      await sup.start({
        executable: process.execPath,
        args: [ignoringPath],
        env: {},
        cwd: tmpDir,
        dataDir: tmpDir,
        resourceDir: tmpDir,
        logDir: tmpDir,
      });

      const diagBefore = sup.getDiagnostics();
      assert.ok(diagBefore.state === "READY" || diagBefore.state === "WAITING_FOR_HTTP");

      await sup.forceStop({ reason: TerminationReason.FORCED });
      assert.ok(
        sup.getState() === State.STOPPED || sup.getState() === State.FAILED
      );
    });
  });

  describe("Startup timeout", () => {
    it("fails on timeout", async () => {
      const sup = new BackendProcessSupervisor({ startupTimeoutMs: 2000 });

      await assert.rejects(
        () => sup.start({
          executable: process.execPath,
          args: [slowPath],
          env: {},
          cwd: tmpDir,
          dataDir: tmpDir,
          resourceDir: tmpDir,
          logDir: tmpDir,
        }),
        /n[aã]o subiu/
      );
    });
  });
});

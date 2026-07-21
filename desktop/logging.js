/** Minimal structured logging for Electron process.

Logs to:
  1. Console (stdout) for real-time visibility
  2. File in PROSPECTOS_LOG_DIR for persistence

Canonical events:
  app.starting, app.ready
  backend.starting, backend.ready, backend.unhealthy, backend.crashed
  backend.stopping, backend.stopped
  scraper.starting, scraper.running, scraper.cancelled
  scraper.failed, scraper.completed
  playwright.inspect, playwright.installing, playwright.ready
  health.requested
  diagnostics.exported
*/

const fs = require("fs");
const path = require("path");

const MAX_LOG_SIZE = 2 * 1024 * 1024;
const MAX_BACKUPS = 3;
const MAX_LINE_LENGTH = 2000;

let _logDir = null;
let _logStream = null;

function setLogDir(dir) {
  _logDir = dir;
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch {}
  _rotateLog();
  _ensureStream();
}

function _logPath() {
  if (!_logDir) return null;
  return path.join(_logDir, "electron-tail.log");
}

function _ensureStream() {
  if (_logStream) return;
  const lp = _logPath();
  if (!lp) return;
  try {
    _logStream = fs.createWriteStream(lp, { flags: "a", encoding: "utf-8" });
    _logStream.on("error", () => { _logStream = null; });
  } catch {}
}

function _rotateLog() {
  const lp = _logPath();
  if (!lp) return;
  try {
    if (fs.existsSync(lp) && fs.statSync(lp).size > MAX_LOG_SIZE) {
      for (let i = MAX_BACKUPS - 1; i >= 0; i--) {
        const old = i === 0 ? lp : lp + "." + i;
        const newer = lp + "." + (i + 1);
        if (fs.existsSync(old)) {
          fs.renameSync(old, newer);
        }
      }
    }
  } catch {}
}

function _formatMessage(level, event, message, extra) {
  const ts = new Date().toISOString();
  const parts = [ts, level, `event=${event}`];
  if (message) parts.push(message);
  if (extra && Object.keys(extra).length) {
    parts.push(JSON.stringify(extra));
  }
  return parts.join(" | ").substring(0, MAX_LINE_LENGTH);
}

function _write(level, event, message, extra) {
  const line = _formatMessage(level, event, message, extra);
  console.log(line);
  if (_logStream) {
    try {
      _logStream.write(line + "\n");
    } catch {}
  }
}

function logEvent(event, message, extra) {
  _write("INFO", event, message, extra);
}

function logWarn(event, message, extra) {
  _write("WARN", event, message, extra);
}

function logError(event, message, extra) {
  _write("ERROR", event, message, extra);
}

function close() {
  if (_logStream) {
    try {
      _logStream.end();
    } catch {}
    _logStream = null;
  }
}

module.exports = {
  setLogDir,
  logEvent,
  logWarn,
  logError,
  close,
};

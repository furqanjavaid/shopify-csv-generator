const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const PROJECT_ROOT = path.join(__dirname, "..");
const PYTHON_EXE =
  "C:\\Users\\zalim\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";
const MAIN_PY = path.join(PROJECT_ROOT, "main.py");
const IMAGE_CONVERTER_PY = path.join(PROJECT_ROOT, "image_converter.py");
const CSV_MERGER_CLI = path.join(
  PROJECT_ROOT,
  "sentivo_extractor",
  "cli",
  "csv_merger_cli.py"
);
const INVOICE_PDF_CLI = path.join(
  PROJECT_ROOT,
  "sentivo_extractor",
  "cli",
  "invoice_pdf_cli.py"
);
const IMAGE_RESIZER_CLI = path.join(
  PROJECT_ROOT,
  "sentivo_extractor",
  "cli",
  "image_resizer_cli.py"
);
const IMAGE_EXTS = new Set([".jpg", ".jpeg", ".png", ".webp", ".gif"]);
const DATA_DIR = path.join(PROJECT_ROOT, "data");
const CLIENTS_JSON = path.join(DATA_DIR, "clients.json");
const INVOICES_JSON = path.join(DATA_DIR, "invoices.json");
const INVOICE_SETTINGS_JSON = path.join(DATA_DIR, "invoice-settings.json");
const DEFAULT_INVOICE_SETTINGS = {
  bankName: "",
  accountName: "",
  sortCode: "",
  accountNumber: "",
  iban: "",
  payoneerLink: "",
  paypalLink: "",
};
const DOWNLOADS_DIR = path.join(
  process.env.USERPROFILE || process.env.HOME || "",
  "Downloads"
);

let mainWindow = null;

/** @type {Record<string, { proc: import('child_process').ChildProcess | null, stopped: boolean, meta: Record<string, any> }>} */
const jobs = {
  scraper: { proc: null, stopped: false, meta: {} },
  converter: { proc: null, stopped: false, meta: {} },
  auditor: { proc: null, stopped: false, meta: {} },
  upload: { proc: null, stopped: false, meta: {} },
  merger: { proc: null, stopped: false, meta: {} },
  resizer: { proc: null, stopped: false, meta: {} },
};

function resolveAppIcon() {
  const png = path.join(PROJECT_ROOT, "assets", "sentivo icon.png");
  const ico = path.join(PROJECT_ROOT, "assets", "icon.ico");
  if (fs.existsSync(png)) return png;
  if (fs.existsSync(ico)) return ico;
  return undefined;
}

function sendToRenderer(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function ensurePython() {
  if (!fs.existsSync(PYTHON_EXE)) {
    throw new Error(`Python not found: ${PYTHON_EXE}`);
  }
}

function writeSeedCsv(outputFolder, urls) {
  fs.mkdirSync(outputFolder, { recursive: true });
  const csvPath = path.join(outputFolder, "hashim_websites.csv");
  const lines = ["url,type,department"];
  for (const raw of urls) {
    const url = String(raw || "").trim();
    if (!url) continue;
    const escaped = `"${url.replace(/"/g, '""')}"`;
    lines.push(`${escaped},auto,`);
  }
  fs.writeFileSync(csvPath, lines.join("\n") + "\n", "utf8");
  return csvPath;
}

function killJob(jobName) {
  const job = jobs[jobName];
  if (!job || !job.proc || job.proc.killed) {
    if (job) job.proc = null;
    return false;
  }
  const proc = job.proc;
  job.proc = null;
  job.stopped = true;
  try {
    if (process.platform === "win32" && proc.pid) {
      spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
    } else {
      proc.kill("SIGTERM");
    }
  } catch (_err) {
    try {
      proc.kill("SIGKILL");
    } catch (_e) {
      /* ignore */
    }
  }
  return true;
}

function killAllJobs() {
  for (const name of Object.keys(jobs)) {
    killJob(name);
  }
}

function spawnPythonJob({
  jobName,
  args,
  logChannel,
  doneChannel,
  envExtra = {},
  meta = {},
}) {
  ensurePython();
  const job = jobs[jobName];
  if (!job) throw new Error(`Unknown job: ${jobName}`);
  if (job.proc) throw new Error(`A ${jobName} job is already running.`);

  const env = { ...process.env, ...envExtra };
  env.PYTHONPATH = PROJECT_ROOT + path.delimiter + (env.PYTHONPATH || "");
  env.PYTHONUTF8 = "1";

  job.stopped = false;
  job.meta = { ...meta };

  sendToRenderer(logChannel, `[INFO] CMD: ${PYTHON_EXE} ${args.join(" ")}`);

  const proc = spawn(PYTHON_EXE, args, {
    cwd: PROJECT_ROOT,
    env,
    windowsHide: true,
  });
  job.proc = proc;

  const emitLines = (stream) => {
    const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
    rl.on("line", (line) => {
      sendToRenderer(logChannel, line);
      const reportMatch = String(line).match(/Report:\s*(.+)$/i);
      if (reportMatch) job.meta.reportPath = reportMatch[1].trim();
      const outMatch = String(line).match(/Output Path:\s*(.+)$/i);
      if (outMatch) job.meta.outputPath = outMatch[1].trim();
    });
  };
  if (proc.stdout) emitLines(proc.stdout);
  if (proc.stderr) emitLines(proc.stderr);

  proc.on("error", (err) => {
    sendToRenderer(logChannel, `[ERROR] Failed to start Python: ${err.message}`);
    job.proc = null;
    sendToRenderer(doneChannel, {
      code: -1,
      stopped: false,
      error: err.message,
      ...job.meta,
    });
  });

  proc.on("close", (code) => {
    const wasStopped = job.stopped;
    job.proc = null;
    job.stopped = false;
    sendToRenderer(doneChannel, {
      code: code == null ? -1 : code,
      stopped: wasStopped,
      ...job.meta,
    });
  });

  return { ok: true, pid: proc.pid, ...job.meta };
}

function spawnPythonJobWithStdin({
  jobName,
  args,
  stdinPayload,
  logChannel,
  doneChannel,
  meta = {},
}) {
  ensurePython();
  const job = jobs[jobName];
  if (!job) throw new Error(`Unknown job: ${jobName}`);
  if (job.proc) throw new Error(`A ${jobName} job is already running.`);

  const env = { ...process.env };
  env.PYTHONPATH = PROJECT_ROOT + path.delimiter + (env.PYTHONPATH || "");
  env.PYTHONUTF8 = "1";

  job.stopped = false;
  job.meta = { ...meta };

  sendToRenderer(logChannel, `[INFO] CMD: ${PYTHON_EXE} ${args.join(" ")}`);

  const proc = spawn(PYTHON_EXE, args, {
    cwd: PROJECT_ROOT,
    env,
    windowsHide: true,
  });
  job.proc = proc;

  const emitLines = (stream) => {
    const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
    rl.on("line", (line) => {
      const text = String(line);
      if (text.startsWith("<<<RESULT>>>")) {
        try {
          const result = JSON.parse(text.slice("<<<RESULT>>>".length));
          job.meta.result = result;
          if (result && result.output_path) {
            job.meta.outputPath = result.output_path;
          }
        } catch (_err) {
          /* ignore parse errors */
        }
        return;
      }
      sendToRenderer(logChannel, text);
      const outMatch = text.match(/Output Path:\s*(.+)$/i);
      if (outMatch) job.meta.outputPath = outMatch[1].trim();
    });
  };
  if (proc.stdout) emitLines(proc.stdout);
  if (proc.stderr) emitLines(proc.stderr);

  proc.on("error", (err) => {
    sendToRenderer(logChannel, `[ERROR] Failed to start Python: ${err.message}`);
    job.proc = null;
    sendToRenderer(doneChannel, {
      code: -1,
      stopped: false,
      error: err.message,
      ...job.meta,
    });
  });

  proc.on("close", (code) => {
    const wasStopped = job.stopped;
    job.proc = null;
    job.stopped = false;
    sendToRenderer(doneChannel, {
      code: code == null ? -1 : code,
      stopped: wasStopped,
      ...job.meta,
    });
  });

  try {
    proc.stdin.write(JSON.stringify(stdinPayload || {}));
    proc.stdin.end();
  } catch (err) {
    sendToRenderer(
      logChannel,
      `[ERROR] Failed to send payload: ${err && err.message ? err.message : err}`
    );
  }

  return { ok: true, pid: proc.pid, ...job.meta };
}

function runPythonJson(scriptPath, payload) {
  ensurePython();
  if (!fs.existsSync(scriptPath)) {
    return Promise.reject(new Error(`Script not found: ${scriptPath}`));
  }
  return new Promise((resolve, reject) => {
    const env = { ...process.env };
    env.PYTHONPATH = PROJECT_ROOT + path.delimiter + (env.PYTHONPATH || "");
    env.PYTHONUTF8 = "1";
    const proc = spawn(PYTHON_EXE, [scriptPath], {
      cwd: PROJECT_ROOT,
      env,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    proc.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    proc.on("error", (err) => reject(err));
    proc.on("close", (code) => {
      const lines = stdout
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean);
      let parsed = null;
      for (let i = lines.length - 1; i >= 0; i -= 1) {
        const line = lines[i];
        const jsonText = line.startsWith("<<<RESULT>>>")
          ? line.slice("<<<RESULT>>>".length)
          : line.startsWith("{")
            ? line
            : "";
        if (!jsonText) continue;
        try {
          parsed = JSON.parse(jsonText);
          break;
        } catch (_err) {
          /* keep looking */
        }
      }
      if (parsed) {
        resolve(parsed);
        return;
      }
      reject(
        new Error(
          stderr.trim() ||
            stdout.trim() ||
            `Python exited with code ${code == null ? -1 : code}`
        )
      );
    });
    try {
      proc.stdin.write(JSON.stringify(payload || {}));
      proc.stdin.end();
    } catch (err) {
      reject(err);
    }
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    icon: resolveAppIcon(),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function registerIpc() {
  ipcMain.handle("browse-folder", async () => {
    const result = await dialog.showOpenDialog(mainWindow || undefined, {
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
  });

  ipcMain.handle("browse-file", async (_event, options = {}) => {
    const filters = Array.isArray(options.filters) && options.filters.length
      ? options.filters
      : [
          { name: "Spreadsheets", extensions: ["csv", "xlsx", "xls"] },
          { name: "All Files", extensions: ["*"] },
        ];
    const result = await dialog.showOpenDialog(mainWindow || undefined, {
      properties: ["openFile"],
      filters,
    });
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
  });

  ipcMain.handle("save-file", async (_event, options = {}) => {
    const result = await dialog.showSaveDialog(mainWindow || undefined, {
      title: options.title || "Save file",
      defaultPath: options.defaultPath || undefined,
      filters: options.filters || [{ name: "All Files", extensions: ["*"] }],
    });
    if (result.canceled || !result.filePath) return null;
    return result.filePath;
  });

  ipcMain.handle("open-path", async (_event, targetPath) => {
    const p = String(targetPath || "").trim();
    if (!p || !fs.existsSync(p)) {
      return { ok: false, error: "Path not found" };
    }
    const err = await shell.openPath(p);
    return { ok: !err, error: err || "" };
  });

  ipcMain.handle("copy-file", async (_event, { source, destination } = {}) => {
    const src = String(source || "").trim();
    const dest = String(destination || "").trim();
    if (!src || !fs.existsSync(src)) throw new Error("Source file not found.");
    if (!dest) throw new Error("Destination path required.");
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
    return { ok: true, path: dest };
  });

  ipcMain.handle("stop-scraper", async () => ({ ok: killJob("scraper") }));
  ipcMain.handle("stop-converter", async () => ({ ok: killJob("converter") }));
  ipcMain.handle("stop-auditor", async () => ({ ok: killJob("auditor") }));
  ipcMain.handle("stop-upload", async () => ({ ok: killJob("upload") }));
  ipcMain.handle("image-resizer:stop", async () => ({ ok: killJob("resizer") }));

  function collectImagesFromFolder(folderPath) {
    const root = String(folderPath || "").trim();
    if (!root || !fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
      return [];
    }
    const out = [];
    for (const name of fs.readdirSync(root)) {
      const full = path.join(root, name);
      try {
        if (!fs.statSync(full).isFile()) continue;
      } catch (_err) {
        continue;
      }
      if (IMAGE_EXTS.has(path.extname(full).toLowerCase())) {
        out.push(full);
      }
    }
    out.sort((a, b) => a.localeCompare(b));
    return out;
  }

  ipcMain.handle("image-resizer:select-folder", async () => {
    const result = await dialog.showOpenDialog(mainWindow || undefined, {
      properties: ["openDirectory"],
    });
    if (result.canceled || !result.filePaths.length) {
      return { folder: null, files: [] };
    }
    const folder = result.filePaths[0];
    const files = collectImagesFromFolder(folder);
    return { folder, files };
  });

  ipcMain.handle("image-resizer:select-files", async () => {
    const result = await dialog.showOpenDialog(mainWindow || undefined, {
      properties: ["openFile", "multiSelections"],
      filters: [
        {
          name: "Images",
          extensions: ["jpg", "jpeg", "png", "webp", "gif"],
        },
      ],
    });
    if (result.canceled || !result.filePaths.length) {
      return { files: [] };
    }
    const files = result.filePaths.filter((p) =>
      IMAGE_EXTS.has(path.extname(p).toLowerCase())
    );
    return { files };
  });

  ipcMain.handle("image-resizer:open-folder", async (_event, folderPath) => {
    const p = String(folderPath || "").trim();
    if (!p) return { ok: false, error: "Folder path required" };
    fs.mkdirSync(p, { recursive: true });
    if (!fs.existsSync(p)) return { ok: false, error: "Folder not found" };
    const err = await shell.openPath(p);
    return { ok: !err, error: err || "" };
  });

  ipcMain.handle("image-resizer:start", async (_event, config = {}) => {
    if (!fs.existsSync(IMAGE_RESIZER_CLI)) {
      throw new Error(`image_resizer_cli.py not found: ${IMAGE_RESIZER_CLI}`);
    }
    const files = Array.isArray(config.files) ? config.files.filter(Boolean) : [];
    if (!files.length) throw new Error("Select at least one image.");
    const outputFolder = String(config.output_folder || config.outputFolder || "").trim();
    if (!outputFolder) throw new Error("Output folder is required.");

    ensurePython();
    const job = jobs.resizer;
    if (job.proc) throw new Error("A resizer job is already running.");

    const env = { ...process.env };
    env.PYTHONPATH = PROJECT_ROOT + path.delimiter + (env.PYTHONPATH || "");
    env.PYTHONUTF8 = "1";

    job.stopped = false;
    job.meta = { outputPath: outputFolder, total: files.length };

    sendToRenderer(
      "image-resizer:log-line",
      `[INFO] CMD: ${PYTHON_EXE} ${IMAGE_RESIZER_CLI}`
    );

    const proc = spawn(PYTHON_EXE, [IMAGE_RESIZER_CLI], {
      cwd: PROJECT_ROOT,
      env,
      windowsHide: true,
    });
    job.proc = proc;

    const handleLine = (line) => {
      const text = String(line || "").trim();
      if (!text) return;
      try {
        const evt = JSON.parse(text);
        if (evt && typeof evt === "object" && evt.type) {
          if (evt.type === "progress") {
            sendToRenderer("image-resizer:progress", evt);
            if (evt.size && !evt.failed) {
              sendToRenderer(
                "image-resizer:log-line",
                `[INFO] ✓ ${evt.file} → ${evt.size} (${evt.kb || 0}KB)`
              );
            }
            return;
          }
          if (evt.type === "error") {
            sendToRenderer("image-resizer:progress", evt);
            sendToRenderer(
              "image-resizer:log-line",
              `[ERROR] ${evt.file ? evt.file + ": " : ""}${evt.error || "Unknown error"}`
            );
            return;
          }
          if (evt.type === "done") {
            job.meta.outputPath = evt.output_folder || outputFolder;
            job.meta.total = evt.total;
            job.meta.ok = evt.ok;
            sendToRenderer("image-resizer:progress", evt);
            sendToRenderer(
              "image-resizer:log-line",
              `[INFO] Done. ${evt.ok || 0}/${evt.total || 0} resized → ${evt.output_folder || outputFolder}`
            );
            return;
          }
        }
      } catch (_err) {
        /* not JSON — forward as log */
      }
      sendToRenderer("image-resizer:log-line", text);
    };

    const emitLines = (stream) => {
      const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
      rl.on("line", handleLine);
    };
    if (proc.stdout) emitLines(proc.stdout);
    if (proc.stderr) emitLines(proc.stderr);

    proc.on("error", (err) => {
      sendToRenderer(
        "image-resizer:log-line",
        `[ERROR] Failed to start Python: ${err.message}`
      );
      job.proc = null;
      sendToRenderer("image-resizer:done", {
        code: -1,
        stopped: false,
        error: err.message,
        ...job.meta,
      });
    });

    proc.on("close", (code) => {
      const wasStopped = job.stopped;
      job.proc = null;
      job.stopped = false;
      sendToRenderer("image-resizer:done", {
        code: code == null ? -1 : code,
        stopped: wasStopped,
        ...job.meta,
      });
    });

    const payload = {
      files,
      mode: String(config.mode || "dimensions"),
      width: Number(config.width) || 0,
      height: Number(config.height) || 0,
      percentage: Number(config.percentage) || 100,
      max_side: Number(config.max_side || config.maxSide) || 0,
      maintain_aspect:
        config.maintain_aspect !== undefined
          ? Boolean(config.maintain_aspect)
          : Boolean(config.maintainAspect !== false),
      output_format: String(config.output_format || config.outputFormat || "same"),
      quality: Number(config.quality) || 85,
      output_folder: outputFolder,
    };

    try {
      proc.stdin.write(JSON.stringify(payload));
      proc.stdin.end();
    } catch (err) {
      sendToRenderer(
        "image-resizer:log-line",
        `[ERROR] Failed to send payload: ${err && err.message ? err.message : err}`
      );
    }

    return { ok: true, pid: proc.pid, outputPath: outputFolder, total: files.length };
  });

  ipcMain.handle("run-scraper", async (_event, config = {}) => {
    const urls = Array.isArray(config.urls) ? config.urls : [];
    const outputFolder = String(config.outputFolder || "").trim();
    if (!urls.length) throw new Error("No seed URLs provided.");
    if (!outputFolder) throw new Error("Output folder is required.");
    if (!fs.existsSync(MAIN_PY)) throw new Error(`main.py not found: ${MAIN_PY}`);

    const csvPath = writeSeedCsv(outputFolder, urls);
    const mode = String(config.mode || "");
    const isLite = /lite/i.test(mode);
    const maxRaw = String(config.maxProducts ?? "").trim();
    const maxProducts = maxRaw === "" ? "0" : maxRaw;
    const freshStart = Boolean(config.freshStart);
    const customVendor = String(config.customVendor || "").trim();
    const proxyUrl = String(config.proxyUrl || "").trim();

    const args = [
      MAIN_PY,
      "extract",
      "--input",
      csvPath,
      "--output",
      outputFolder,
      "--overwrite",
      freshStart ? "true" : "false",
      "--max-products-per-domain",
      maxProducts,
    ];
    if (isLite) {
      args.push("--pilot", "true", "--pilot-size-per-domain", "20");
    }
    if (customVendor) {
      args.push("--vendor", customVendor);
    }

    const envExtra = {};
    if (proxyUrl) envExtra.SENTIVO_HTTP_PROXY = proxyUrl;

    sendToRenderer("log-line", `[INFO] Seed CSV: ${csvPath}`);
    return spawnPythonJob({
      jobName: "scraper",
      args,
      logChannel: "log-line",
      doneChannel: "scraper-done",
      envExtra,
      meta: { csvPath, outputPath: outputFolder },
    });
  });

  ipcMain.handle("run-converter", async (_event, config = {}) => {
    const inputFolder = String(config.inputFolder || "").trim();
    const outputFolder = String(config.outputFolder || "").trim();
    const format = String(config.format || "WEBP").trim();
    const quality = String(config.quality ?? "85").trim();
    if (!inputFolder) throw new Error("Input folder is required.");
    if (!outputFolder) throw new Error("Output folder is required.");
    if (!fs.existsSync(IMAGE_CONVERTER_PY)) {
      throw new Error(`image_converter.py not found: ${IMAGE_CONVERTER_PY}`);
    }
    return spawnPythonJob({
      jobName: "converter",
      args: [
        IMAGE_CONVERTER_PY,
        "--input",
        inputFolder,
        "--output",
        outputFolder,
        "--format",
        format,
        "--quality",
        quality,
      ],
      logChannel: "converter-log-line",
      doneChannel: "converter-done",
      meta: { outputPath: outputFolder },
    });
  });

  ipcMain.handle("run-auditor", async (_event, config = {}) => {
    const url = String(config.url || "").trim();
    const mode = String(config.mode || "cro").trim() || "cro";
    if (!url) throw new Error("Store URL is required.");
    if (!fs.existsSync(MAIN_PY)) throw new Error(`main.py not found: ${MAIN_PY}`);
    return spawnPythonJob({
      jobName: "auditor",
      args: [MAIN_PY, "audit", "--url", url, "--mode", mode],
      logChannel: "auditor-log-line",
      doneChannel: "auditor-done",
      meta: {},
    });
  });

  ipcMain.handle("run-upload", async (_event, config = {}) => {
    const inputPath = String(config.inputPath || "").trim();
    if (!inputPath) throw new Error("Input file is required.");
    if (!fs.existsSync(inputPath)) throw new Error(`File not found: ${inputPath}`);
    if (!fs.existsSync(MAIN_PY)) throw new Error(`main.py not found: ${MAIN_PY}`);

    const mapping = config.mapping;
    const mappingJson =
      typeof mapping === "string"
        ? mapping
        : mapping
          ? JSON.stringify(mapping)
          : "";
    const outputPath = String(config.outputPath || "").trim();

    const args = [MAIN_PY, "upload", "--input", inputPath];
    if (mappingJson) {
      args.push("--mapping", mappingJson);
    }
    if (outputPath) {
      args.push("--output", outputPath);
    }

    return spawnPythonJob({
      jobName: "upload",
      args,
      logChannel: "upload-log-line",
      doneChannel: "upload-done",
      meta: { inputPath },
    });
  });

  ipcMain.handle("csv-merger:analyse", async (_event, payload = {}) => {
    if (!fs.existsSync(CSV_MERGER_CLI)) {
      throw new Error(`csv_merger_cli.py not found: ${CSV_MERGER_CLI}`);
    }
    const sources = Array.isArray(payload.sources) ? payload.sources : [];
    if (sources.length < 2) {
      throw new Error("Add at least 2 source files before analysing.");
    }
    return runPythonJson(CSV_MERGER_CLI, {
      action: "analyse",
      sources,
      settings: payload.settings || {},
    });
  });

  ipcMain.handle("csv-merger:merge", async (_event, payload = {}) => {
    if (!fs.existsSync(CSV_MERGER_CLI)) {
      throw new Error(`csv_merger_cli.py not found: ${CSV_MERGER_CLI}`);
    }
    const sources = Array.isArray(payload.sources) ? payload.sources : [];
    if (sources.length < 2) {
      throw new Error("Add at least 2 source files before merging.");
    }
    const settings = payload.settings || {};
    const outputFolder = String(settings.output_folder || "").trim();
    if (!outputFolder) throw new Error("Output folder is required.");

    return spawnPythonJobWithStdin({
      jobName: "merger",
      args: [CSV_MERGER_CLI],
      stdinPayload: {
        action: "merge",
        sources,
        settings,
        column_mappings: payload.column_mappings || {},
      },
      logChannel: "merger-log-line",
      doneChannel: "merger-done",
      meta: { outputPath: outputFolder },
    });
  });

  ipcMain.handle("csv-merger:open-folder", async (_event, folderPath) => {
    const p = String(folderPath || "").trim();
    if (!p || !fs.existsSync(p)) {
      return { ok: false, error: "Folder not found" };
    }
    const err = await shell.openPath(p);
    return { ok: !err, error: err || "" };
  });

  function ensureDataFile(filePath, fallback) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    if (!fs.existsSync(filePath)) {
      fs.writeFileSync(filePath, JSON.stringify(fallback, null, 2), "utf8");
    }
  }

  function readJsonArray(filePath) {
    ensureDataFile(filePath, []);
    try {
      const raw = fs.readFileSync(filePath, "utf8");
      const data = JSON.parse(raw || "[]");
      return Array.isArray(data) ? data : [];
    } catch (_err) {
      return [];
    }
  }

  function writeJsonArray(filePath, data) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const arr = Array.isArray(data) ? data : [];
    fs.writeFileSync(filePath, JSON.stringify(arr, null, 2), "utf8");
    return { ok: true, path: filePath, count: arr.length };
  }

  ipcMain.handle("invoices:read-clients", async () => readJsonArray(CLIENTS_JSON));
  ipcMain.handle("invoices:write-clients", async (_event, clients) =>
    writeJsonArray(CLIENTS_JSON, clients)
  );
  ipcMain.handle("invoices:read-invoices", async () => readJsonArray(INVOICES_JSON));
  ipcMain.handle("invoices:write-invoices", async (_event, invoices) =>
    writeJsonArray(INVOICES_JSON, invoices)
  );

  function readInvoiceSettings() {
    ensureDataFile(INVOICE_SETTINGS_JSON, DEFAULT_INVOICE_SETTINGS);
    try {
      const raw = fs.readFileSync(INVOICE_SETTINGS_JSON, "utf8");
      const data = JSON.parse(raw || "{}");
      return { ...DEFAULT_INVOICE_SETTINGS, ...(data && typeof data === "object" ? data : {}) };
    } catch (_err) {
      return { ...DEFAULT_INVOICE_SETTINGS };
    }
  }

  function writeInvoiceSettings(settings) {
    fs.mkdirSync(path.dirname(INVOICE_SETTINGS_JSON), { recursive: true });
    const next = {
      ...DEFAULT_INVOICE_SETTINGS,
      ...(settings && typeof settings === "object" ? settings : {}),
    };
    fs.writeFileSync(
      INVOICE_SETTINGS_JSON,
      JSON.stringify(next, null, 2),
      "utf8"
    );
    return { ok: true, path: INVOICE_SETTINGS_JSON, settings: next };
  }

  ipcMain.handle("invoices:read-settings", async () => readInvoiceSettings());
  ipcMain.handle("invoices:write-settings", async (_event, settings) =>
    writeInvoiceSettings(settings)
  );

  ipcMain.handle("invoices:export-pdf", async (_event, invoice = {}) => {
    if (!fs.existsSync(INVOICE_PDF_CLI)) {
      throw new Error(`invoice_pdf_cli.py not found: ${INVOICE_PDF_CLI}`);
    }
    const number = String(invoice.number || "INV-000").trim() || "INV-000";
    const safeName = number.replace(/[<>:"/\\|?*]/g, "_");
    fs.mkdirSync(DOWNLOADS_DIR, { recursive: true });
    const outputPath = path.join(DOWNLOADS_DIR, `${safeName}.pdf`);
    const result = await runPythonJson(INVOICE_PDF_CLI, {
      output_path: outputPath,
      invoice,
    });
    if (!result || result.ok === false) {
      const err =
        (result && result.errors && result.errors[0]) || "PDF export failed.";
      throw new Error(err);
    }
    const pdfPath = String(result.path || outputPath);
    if (fs.existsSync(pdfPath)) {
      await shell.openPath(pdfPath);
    }
    return { ok: true, path: pdfPath };
  });
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  registerIpc();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  killAllJobs();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

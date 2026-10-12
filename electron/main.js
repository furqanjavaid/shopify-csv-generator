const { app, BrowserWindow, Menu, ipcMain, dialog } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const PROJECT_ROOT = path.join(__dirname, "..");
const PYTHON_EXE =
  "C:\\Users\\zalim\\AppData\\Local\\Programs\\Python\\Python312\\python.exe";
const MAIN_PY = path.join(PROJECT_ROOT, "main.py");

let mainWindow = null;
let scraperProc = null;
let scraperStoppedByUser = false;

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

function killScraperProcess() {
  if (!scraperProc || scraperProc.killed) {
    scraperProc = null;
    return false;
  }
  const proc = scraperProc;
  scraperProc = null;
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

  ipcMain.handle("browse-file", async () => {
    const result = await dialog.showOpenDialog(mainWindow || undefined, {
      properties: ["openFile"],
      filters: [
        { name: "Spreadsheets", extensions: ["csv", "xlsx", "xls"] },
        { name: "All Files", extensions: ["*"] },
      ],
    });
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
  });

  ipcMain.handle("stop-scraper", async () => {
    scraperStoppedByUser = true;
    const killed = killScraperProcess();
    return { ok: killed };
  });

  ipcMain.handle("run-scraper", async (_event, config = {}) => {
    if (scraperProc) {
      throw new Error("A scraper job is already running.");
    }
    if (!fs.existsSync(PYTHON_EXE)) {
      throw new Error(`Python not found: ${PYTHON_EXE}`);
    }
    if (!fs.existsSync(MAIN_PY)) {
      throw new Error(`main.py not found: ${MAIN_PY}`);
    }

    const urls = Array.isArray(config.urls) ? config.urls : [];
    const outputFolder = String(config.outputFolder || "").trim();
    if (!urls.length) throw new Error("No seed URLs provided.");
    if (!outputFolder) throw new Error("Output folder is required.");

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

    const env = { ...process.env };
    env.PYTHONPATH = PROJECT_ROOT + path.delimiter + (env.PYTHONPATH || "");
    env.PYTHONUTF8 = "1";
    if (proxyUrl) {
      env.SENTIVO_HTTP_PROXY = proxyUrl;
    } else {
      delete env.SENTIVO_HTTP_PROXY;
    }

    scraperStoppedByUser = false;
    sendToRenderer("log-line", `[INFO] Seed CSV: ${csvPath}`);
    sendToRenderer("log-line", `[INFO] CMD: ${PYTHON_EXE} ${args.join(" ")}`);

    const proc = spawn(PYTHON_EXE, args, {
      cwd: PROJECT_ROOT,
      env,
      windowsHide: true,
    });
    scraperProc = proc;

    const emitLines = (stream) => {
      const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
      rl.on("line", (line) => {
        sendToRenderer("log-line", line);
      });
    };
    if (proc.stdout) emitLines(proc.stdout);
    if (proc.stderr) emitLines(proc.stderr);

    proc.on("error", (err) => {
      sendToRenderer("log-line", `[ERROR] Failed to start Python: ${err.message}`);
      scraperProc = null;
      sendToRenderer("scraper-done", {
        code: -1,
        stopped: false,
        error: err.message,
      });
    });

    proc.on("close", (code) => {
      const wasStopped = scraperStoppedByUser;
      scraperProc = null;
      scraperStoppedByUser = false;
      sendToRenderer("scraper-done", {
        code: code == null ? -1 : code,
        stopped: wasStopped,
      });
    });

    return { ok: true, csvPath, pid: proc.pid };
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
  killScraperProcess();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

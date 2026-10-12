const { app, BrowserWindow, Menu } = require("electron");
const fs = require("fs");
const path = require("path");

function resolveAppIcon() {
  const png = path.join(__dirname, "..", "assets", "sentivo icon.png");
  const ico = path.join(__dirname, "..", "assets", "icon.ico");
  if (fs.existsSync(png)) return png;
  if (fs.existsSync(ico)) return ico;
  return undefined;
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    icon: resolveAppIcon(),
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

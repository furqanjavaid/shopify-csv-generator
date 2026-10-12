const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("sentivo", {
  runScraper: (config) => ipcRenderer.invoke("run-scraper", config),
  stopScraper: () => ipcRenderer.invoke("stop-scraper"),
  browseFolder: () => ipcRenderer.invoke("browse-folder"),
  browseFile: () => ipcRenderer.invoke("browse-file"),
  onLogLine: (callback) => {
    const handler = (_event, line) => callback(line);
    ipcRenderer.on("log-line", handler);
    return () => ipcRenderer.removeListener("log-line", handler);
  },
  onScraperDone: (callback) => {
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on("scraper-done", handler);
    return () => ipcRenderer.removeListener("scraper-done", handler);
  },
});

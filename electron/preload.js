const { contextBridge, ipcRenderer } = require("electron");

function subscribe(channel, callback) {
  const handler = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld("sentivo", {
  runScraper: (config) => ipcRenderer.invoke("run-scraper", config),
  stopScraper: () => ipcRenderer.invoke("stop-scraper"),
  runConverter: (config) => ipcRenderer.invoke("run-converter", config),
  stopConverter: () => ipcRenderer.invoke("stop-converter"),
  runAuditor: (config) => ipcRenderer.invoke("run-auditor", config),
  stopAuditor: () => ipcRenderer.invoke("stop-auditor"),
  runUpload: (config) => ipcRenderer.invoke("run-upload", config),
  stopUpload: () => ipcRenderer.invoke("stop-upload"),
  browseFolder: () => ipcRenderer.invoke("browse-folder"),
  browseFile: () => ipcRenderer.invoke("browse-file"),
  saveFile: (options) => ipcRenderer.invoke("save-file", options),
  openPath: (targetPath) => ipcRenderer.invoke("open-path", targetPath),
  copyFile: (source, destination) =>
    ipcRenderer.invoke("copy-file", { source, destination }),
  onLogLine: (callback) => subscribe("log-line", callback),
  onScraperDone: (callback) => subscribe("scraper-done", callback),
  onConverterLogLine: (callback) => subscribe("converter-log-line", callback),
  onConverterDone: (callback) => subscribe("converter-done", callback),
  onAuditorLogLine: (callback) => subscribe("auditor-log-line", callback),
  onAuditorDone: (callback) => subscribe("auditor-done", callback),
  onUploadLogLine: (callback) => subscribe("upload-log-line", callback),
  onUploadDone: (callback) => subscribe("upload-done", callback),
});

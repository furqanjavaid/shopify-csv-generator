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
  browseFile: (options) => ipcRenderer.invoke("browse-file", options || {}),
  saveFile: (options) => ipcRenderer.invoke("save-file", options),
  openPath: (targetPath) => ipcRenderer.invoke("open-path", targetPath),
  copyFile: (source, destination) =>
    ipcRenderer.invoke("copy-file", { source, destination }),
  analyseCsvMerger: (payload) =>
    ipcRenderer.invoke("csv-merger:analyse", payload),
  runCsvMerger: (payload) => ipcRenderer.invoke("csv-merger:merge", payload),
  openCsvMergerFolder: (folderPath) =>
    ipcRenderer.invoke("csv-merger:open-folder", folderPath),
  readClients: () => ipcRenderer.invoke("invoices:read-clients"),
  writeClients: (clients) => ipcRenderer.invoke("invoices:write-clients", clients),
  readInvoices: () => ipcRenderer.invoke("invoices:read-invoices"),
  writeInvoices: (invoices) =>
    ipcRenderer.invoke("invoices:write-invoices", invoices),
  readInvoiceSettings: () => ipcRenderer.invoke("invoices:read-settings"),
  writeInvoiceSettings: (settings) =>
    ipcRenderer.invoke("invoices:write-settings", settings),
  exportInvoicePdf: (invoice) =>
    ipcRenderer.invoke("invoices:export-pdf", invoice),
  selectResizerFolder: () => ipcRenderer.invoke("image-resizer:select-folder"),
  selectResizerFiles: () => ipcRenderer.invoke("image-resizer:select-files"),
  startImageResizer: (config) => ipcRenderer.invoke("image-resizer:start", config),
  stopImageResizer: () => ipcRenderer.invoke("image-resizer:stop"),
  openResizerFolder: (folderPath) =>
    ipcRenderer.invoke("image-resizer:open-folder", folderPath),
  onLogLine: (callback) => subscribe("log-line", callback),
  onScraperDone: (callback) => subscribe("scraper-done", callback),
  onConverterLogLine: (callback) => subscribe("converter-log-line", callback),
  onConverterDone: (callback) => subscribe("converter-done", callback),
  onAuditorLogLine: (callback) => subscribe("auditor-log-line", callback),
  onAuditorDone: (callback) => subscribe("auditor-done", callback),
  onUploadLogLine: (callback) => subscribe("upload-log-line", callback),
  onUploadDone: (callback) => subscribe("upload-done", callback),
  onMergerLogLine: (callback) => subscribe("merger-log-line", callback),
  onMergerDone: (callback) => subscribe("merger-done", callback),
  onResizerLogLine: (callback) => subscribe("image-resizer:log-line", callback),
  onResizerProgress: (callback) => subscribe("image-resizer:progress", callback),
  onResizerDone: (callback) => subscribe("image-resizer:done", callback),
});

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("overlayAPI", {
  hideWindow: () => ipcRenderer.invoke("overlay:hide"),
  enableMouse: () => ipcRenderer.send("enable-mouse"),
  disableMouse: () => ipcRenderer.send("disable-mouse"),
  onStartListening: (handler) => {
    ipcRenderer.on("start-listening", handler);
    return () => ipcRenderer.removeListener("start-listening", handler);
  },
  onOverlayHidden: (handler) => {
    ipcRenderer.on("overlay-hidden", handler);
    return () => ipcRenderer.removeListener("overlay-hidden", handler);
  },
  logError: (msg) => ipcRenderer.send("log-error", msg),
  logInfo: (msg) => ipcRenderer.send("log-info", msg),
});

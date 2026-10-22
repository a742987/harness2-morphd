const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("morphd", {
  getStatus: () => ipcRenderer.invoke("morphd:status"),
  showWindow: () => ipcRenderer.invoke("morphd:show"),
  openSettings: () => ipcRenderer.invoke("morphd:open-settings"),
  pause: (until: string) => ipcRenderer.invoke("morphd:pause", String(until)),
  setIncognito: (active: boolean) =>
    ipcRenderer.invoke("morphd:incognito", Boolean(active)),
  applyCandidate: (id: string) =>
    ipcRenderer.invoke("morphd:apply-candidate", String(id)),
  revertCandidate: (id: string) =>
    ipcRenderer.invoke("morphd:revert-candidate", String(id)),
  quitShell: () => ipcRenderer.invoke("morphd:quit-shell"),
});

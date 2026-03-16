const { contextBridge, ipcRenderer } = require('electron');

// Пробрасываем только нужный API в страницу
contextBridge.exposeInMainWorld('__tabletoneDesktop', {
  notify: (title, body) => ipcRenderer.send('show-notification', { title, body })
});

// Удаляем следы Electron из navigator
delete window.process;
delete window.require;

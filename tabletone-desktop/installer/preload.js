const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('installer', {
    close:          ()      => ipcRenderer.send('close-app'),
    minimize:       ()      => ipcRenderer.send('minimize-app'),
    chooseDir:      ()      => ipcRenderer.invoke('choose-dir'),
    getDefaultDir:  ()      => ipcRenderer.invoke('get-default-dir'),
    install:        (dir)   => ipcRenderer.invoke('install', dir),
    openDir:        (dir)   => ipcRenderer.send('open-install-dir', dir),
    launch:         (dir)   => ipcRenderer.send('launch-app', dir),
    onProgress:     (cb)    => ipcRenderer.on('install-progress', (_, d) => cb(d)),
    dragWindow:     (x, y)  => ipcRenderer.send('drag-window', { x, y }),
});

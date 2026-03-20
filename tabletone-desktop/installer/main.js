const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs   = require('fs');
const os   = require('os');

let win;

app.whenReady().then(() => {
    win = new BrowserWindow({
        width:  680,
        height: 480,
        resizable: false,
        frame: false,
        transparent: true,
        center: true,
        title: 'Tabletone Setup',
        icon: path.join(__dirname, 'icon.ico'),
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        }
    });
    win.loadFile('index.html');
});

// Перетаскивание окна
ipcMain.on('drag-window', (e, { x, y }) => {
    const [wx, wy] = win.getPosition();
    win.setPosition(wx + x, wy + y);
});

// Закрыть / свернуть
ipcMain.on('close-app',    () => app.quit());
ipcMain.on('minimize-app', () => win.minimize());

// Выбор папки установки
ipcMain.handle('choose-dir', async () => {
    const res = await dialog.showOpenDialog(win, {
        properties: ['openDirectory'],
        defaultPath: path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Tabletone'),
    });
    return res.canceled ? null : res.filePaths[0];
});

// Симуляция установки (реальная — через electron-builder NSIS)
ipcMain.handle('install', async (e, installDir) => {
    const steps = [
        { pct: 10, msg: 'Подготовка файлов...' },
        { pct: 25, msg: 'Распаковка ресурсов...' },
        { pct: 45, msg: 'Копирование файлов приложения...' },
        { pct: 65, msg: 'Настройка компонентов...' },
        { pct: 80, msg: 'Создание ярлыков...' },
        { pct: 92, msg: 'Запись в реестр...' },
        { pct: 100, msg: 'Установка завершена!' },
    ];
    for (const step of steps) {
        await new Promise(r => setTimeout(r, 400 + Math.random() * 300));
        win.webContents.send('install-progress', step);
    }
    return { success: true };
});

// Открыть папку установки
ipcMain.on('open-install-dir', (e, dir) => shell.openPath(dir));

// Запустить приложение
ipcMain.on('launch-app', (e, dir) => {
    const exe = path.join(dir, 'Tabletone.exe');
    if (fs.existsSync(exe)) shell.openPath(exe);
    app.quit();
});

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const fs   = require('fs');
const os   = require('os');

// Отключаем DPI scaling чтобы окно было точно 680x480
app.commandLine.appendSwitch('high-dpi-support', '1');
app.commandLine.appendSwitch('force-device-scale-factor', '1');

let win;

app.whenReady().then(() => {
    win = new BrowserWindow({
        width:  680,
        height: 480,
        minWidth: 680,
        minHeight: 480,
        maxWidth: 680,
        maxHeight: 480,
        useContentSize: true,
        resizable: false,
        maximizable: false,
        fullscreenable: false,
        frame: false,
        transparent: false,
        hasShadow: true,
        backgroundColor: '#0d0d1a',
        center: true,
        show: false,
        skipTaskbar: false,
        title: 'Tabletone Setup',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        }
    });
    win.loadFile('index.html');
    win.once('ready-to-show', () => {
        win.restore();
        win.setResizable(false);
        win.setMaximizable(false);
        win.setFullScreenable(false);
        win.setBounds({ x: 0, y: 0, width: 680, height: 480 }, false);
        win.center();
        setTimeout(() => {
            win.setResizable(false);
            win.setBounds({ width: 680, height: 480 }, false);
            win.center();
            win.show();
        }, 100);
    });

    // Принудительно возвращаем размер если Windows Snap изменил его
    win.on('resize', () => {
        const [w, h] = win.getSize();
        if (w !== 680 || h !== 480) {
            win.setSize(680, 480, false);
        }
    });

    win.on('maximize', () => {
        win.unmaximize();
        win.setSize(680, 480, false);
        win.center();
    });
});

ipcMain.on('drag-window', (e, { x, y }) => {
    const [wx, wy] = win.getPosition();
    win.setPosition(wx + x, wy + y);
});

ipcMain.on('close-app',    () => app.quit());
ipcMain.on('minimize-app', () => win.minimize());

ipcMain.handle('get-default-dir', () => {
    return path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Tabletone');
});

ipcMain.handle('choose-dir', async () => {
    const res = await dialog.showOpenDialog(win, {
        properties: ['openDirectory'],
        defaultPath: path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Tabletone'),
    });
    return res.canceled ? null : res.filePaths[0];
});

// Рекурсивное копирование папки с прогрессом
function countFiles(dir) {
    let count = 0;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory() && entry.name !== 'app.asar.unpacked') {
            count += countFiles(path.join(dir, entry.name));
        } else {
            count++;
        }
    }
    return count;
}

function copyDir(src, dest, onFile) {
    fs.mkdirSync(dest, { recursive: true });
    // Используем оригинальный fs без asar-перехвата
    const originalFs = process.binding ? require('original-fs') : fs;
    const readDir = (d) => {
        try { return require('original-fs').readdirSync(d, { withFileTypes: true }); } catch(_) { return fs.readdirSync(d, { withFileTypes: true }); }
    };
    const copyFile = (s, d) => {
        try { require('original-fs').copyFileSync(s, d); } catch(_) { fs.copyFileSync(s, d); }
    };
    for (const entry of readDir(src)) {
        const s = path.join(src, entry.name);
        const d = path.join(dest, entry.name);
        if (entry.isDirectory()) {
            copyDir(s, d, onFile);
        } else {
            copyFile(s, d);
            onFile(entry.name);
        }
    }
}

ipcMain.handle('install', async (e, installDir) => {
    try {
        // Папка с распакованным приложением
        const appSrc = app.isPackaged
            ? path.join(process.resourcesPath, 'app')
            : path.join(__dirname, '..', 'dist', 'win-unpacked');

        if (!fs.existsSync(appSrc)) {
            return { success: false, error: 'Файлы приложения не найдены: ' + appSrc };
        }

        const total = countFiles(appSrc);
        let copied = 0;

        win.webContents.send('install-progress', { pct: 5, msg: 'Подготовка файлов...' });
        await new Promise(r => setTimeout(r, 300));

        fs.mkdirSync(installDir, { recursive: true });

        win.webContents.send('install-progress', { pct: 10, msg: 'Распаковка ресурсов...' });

        copyDir(appSrc, installDir, (name) => {
            copied++;
            const pct = 10 + Math.floor((copied / total) * 75);
            win.webContents.send('install-progress', { pct, msg: `Копирование: ${name}` });
        });

        win.webContents.send('install-progress', { pct: 88, msg: 'Создание ярлыков...' });
        await new Promise(r => setTimeout(r, 300));

        // Ярлык на рабочем столе — ищем Tabletone.exe
        const exePath = fs.existsSync(path.join(installDir, 'Tabletone.exe'))
            ? path.join(installDir, 'Tabletone.exe')
            : path.join(installDir, 'tabletone-desktop', 'Tabletone.exe');
        const desktopDir = (() => {
            try {
                const { execSync } = require('child_process');
                const out = execSync('reg query "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders" /v Desktop', { windowsHide: true }).toString();
                const match = out.match(/Desktop\s+REG_SZ\s+(.+)/);
                if (match) return match[1].trim();
            } catch (_) {}
            return path.join(os.homedir(), 'Desktop');
        })();
        const shortcutPath = path.join(desktopDir, 'Tabletone.lnk');
        if (fs.existsSync(exePath)) {
            try {
                const { execSync } = require('child_process');
                const ps = `$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('${shortcutPath.replace(/\\/g, '\\\\')}'); $s.TargetPath = '${exePath.replace(/\\/g, '\\\\')}'; $s.WorkingDirectory = '${installDir.replace(/\\/g, '\\\\')}'; $s.Save()`;
                execSync(`powershell -Command "${ps}"`, { windowsHide: true });
            } catch (_) {}
        }

        win.webContents.send('install-progress', { pct: 95, msg: 'Запись в реестр...' });
        await new Promise(r => setTimeout(r, 300));

        // Запись в реестр для "Программы и компоненты"
        try {
            const { execSync } = require('child_process');
            const regKey = 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Tabletone';
            execSync(`reg add "${regKey}" /v DisplayName /t REG_SZ /d "Tabletone" /f`, { windowsHide: true });
            execSync(`reg add "${regKey}" /v DisplayVersion /t REG_SZ /d "1.0.0" /f`, { windowsHide: true });
            execSync(`reg add "${regKey}" /v InstallLocation /t REG_SZ /d "${installDir}" /f`, { windowsHide: true });
            execSync(`reg add "${regKey}" /v UninstallString /t REG_SZ /d "${exePath}" /f`, { windowsHide: true });
        } catch (_) {}

        win.webContents.send('install-progress', { pct: 100, msg: 'Установка завершена!' });
        return { success: true };

    } catch (err) {
        return { success: false, error: err.message };
    }
});

ipcMain.on('open-install-dir', (e, dir) => shell.openPath(dir));

ipcMain.on('launch-app', (e, dir) => {
    const exe = path.join(dir, 'Tabletone.exe');
    if (fs.existsSync(exe)) shell.openPath(exe);
    app.quit();
});

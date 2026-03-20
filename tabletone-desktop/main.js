const { app, BrowserWindow, Notification, ipcMain, screen, Tray, Menu, nativeImage } = require('electron');
const path = require('path');

const APP_URL = 'https://hi-ybs0.onrender.com';
const ICON_PATH = path.join(__dirname, '..', 'static', 'images', 'logo.png');

let mainWindow;
let tray;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'Tabletone',
    icon: ICON_PATH,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // РЎРєСЂС‹РІР°РµРј С‡С‚Рѕ СЌС‚Рѕ Electron/WebView
      webSecurity: true,
    },
    autoHideMenuBar: true,
    frame: true,
  });

  // РњР°СЃРєРёСЂСѓРµРј User-Agent РїРѕРґ РѕР±С‹С‡РЅС‹Р№ Chrome
  mainWindow.webContents.setUserAgent(
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' +
    'AppleWebKit/537.36 (KHTML, like Gecko) ' +
    'Chrome/124.0.0.0 Safari/537.36 TabletoneApp/1.0'
  );

  mainWindow.loadURL(APP_URL);

  // РЈР±РёСЂР°РµРј Р·Р°РіРѕР»РѕРІРѕРє Electron РёР· HTTP Р·Р°РіРѕР»РѕРІРєРѕРІ
  mainWindow.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    delete headers['X-Electron-Version'];
    callback({ requestHeaders: headers });
  });

  mainWindow.on('close', (e) => {
    e.preventDefault();
    mainWindow.hide();
  });
}

function createTray() {
  const icon = nativeImage.createFromPath(ICON_PATH);
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip('Tabletone');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'РћС‚РєСЂС‹С‚СЊ', click: () => mainWindow.show() },
    { label: 'Р’С‹С…РѕРґ', click: () => { app.exit(0); } }
  ]));
  tray.on('click', () => {
    mainWindow.isVisible() ? mainWindow.focus() : mainWindow.show();
  });
}

// РџРѕРєР°Р·С‹РІР°РµРј СѓРІРµРґРѕРјР»РµРЅРёРµ РІ РїСЂР°РІРѕРј РЅРёР¶РЅРµРј СѓРіР»Сѓ
ipcMain.on('show-notification', (event, { title, body }) => {
  if (!Notification.isSupported()) return;

  const notif = new Notification({
    title,
    body,
    icon: ICON_PATH,
    silent: false,
  });

  // РџРѕР·РёС†РёРѕРЅРёСЂСѓРµРј РѕРєРЅРѕ СѓРІРµРґРѕРјР»РµРЅРёСЏ РІ РїСЂР°РІС‹Р№ РЅРёР¶РЅРёР№ СѓРіРѕР»
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  notif.on('show', () => {
    // Windows СЂР°Р·РјРµС‰Р°РµС‚ СѓРІРµРґРѕРјР»РµРЅРёСЏ СЃР°Рј РІ РїСЂР°РІРѕРј РЅРёР¶РЅРµРј СѓРіР»Сѓ С‡РµСЂРµР· Action Center
    // Р”РѕРїРѕР»РЅРёС‚РµР»СЊРЅРѕ РїРѕРєР°Р·С‹РІР°РµРј СЃРІРѕС‘ РѕРєРЅРѕ-С‚РѕСЃС‚
    showToast(title, body);
  });

  notif.show();
  notif.on('click', () => {
    mainWindow.show();
    mainWindow.focus();
  });
});

let toastWindow = null;

function showToast(title, body) {
  if (toastWindow && !toastWindow.isDestroyed()) {
    toastWindow.close();
  }

  const { width, height } = screen.getPrimaryDisplay().workAreaSize;

  toastWindow = new BrowserWindow({
    width: 320,
    height: 80,
    x: width - 330,
    y: height - 90,
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    focusable: false,
    transparent: true,
    webPreferences: {
      preload: path.join(__dirname, 'toast-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    }
  });

  toastWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', sans-serif;
    background: #1a1a2e;
    border-radius: 12px;
    border: 1px solid rgba(102,126,234,0.4);
    padding: 12px 16px;
    color: white;
    overflow: hidden;
    cursor: pointer;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    animation: slideIn 0.3s ease;
  }
  @keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }
  .title { font-size: 13px; font-weight: 600; color: #a78bfa; margin-bottom: 4px; }
  .body { font-size: 12px; color: #e2e8f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
</head>
<body onclick="window.close()">
  <div class="title">${title.replace(/</g,'&lt;')}</div>
  <div class="body">${body.replace(/</g,'&lt;')}</div>
</body>
</html>
  `)}`);

  // РђРІС‚РѕР·Р°РєСЂС‹С‚РёРµ С‡РµСЂРµР· 4 СЃРµРєСѓРЅРґС‹
  setTimeout(() => {
    if (toastWindow && !toastWindow.isDestroyed()) {
      toastWindow.close();
    }
  }, 4000);
}

app.whenReady().then(() => {
  createWindow();
  createTray();
});

app.on('window-all-closed', (e) => {
  // РќРµ Р·Р°РєСЂС‹РІР°РµРј РїСЂРёР»РѕР¶РµРЅРёРµ вЂ” РѕРЅРѕ Р¶РёРІС‘С‚ РІ С‚СЂРµРµ
});

app.on('activate', () => {
  mainWindow.show();
});

const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const {
  app,
  BrowserWindow,
  globalShortcut,
  ipcMain,
  screen,
  session,
  systemPreferences
} = require("electron");

const HOTKEYS = (process.env.LIQUID_HOTKEY || "CommandOrControl+Shift+Space,Alt+Space")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);

const WINDOW_LEVEL = "screen-saver";

let mainWindow;
let dashboardWindow = null;
let lastWakeAt = 0;
let pythonProcess = null;

function startPythonBackend() {
  const venvPythonPath = path.join(__dirname, "venv", "bin", "python3");
  const scriptPath = path.join(__dirname, "backend", "backend_server.py");

  if (!fs.existsSync(venvPythonPath)) {
    console.error(`[Backend] Python executable not found at: ${venvPythonPath}`);
    console.error("[Backend] Please ensure you have run: python3 -m venv venv");
    return;
  }

  console.log("[Backend] Starting Python server...");

  // Start the python process
  pythonProcess = spawn(venvPythonPath, [scriptPath], {
    cwd: __dirname,
    stdio: ['ignore', 'pipe', 'pipe']
  });

  // Pipe python stdout/stderr to our electron console
  pythonProcess.stdout.on('data', (data) => {
    process.stdout.write(`[Python] ${data.toString()}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    process.stderr.write(`[Python ERRROR] ${data.toString()}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[Backend] Python server exited with code ${code}`);
    pythonProcess = null;
  });
}

function stopPythonBackend() {
  if (pythonProcess) {
    console.log("[Backend] Stopping Python server...");
    pythonProcess.kill('SIGTERM');
    pythonProcess = null;
  }
}

function emitStartListening() {
  if (!mainWindow || mainWindow.isDestroyed()) return;

  if (mainWindow.webContents.isLoading()) {
    mainWindow.webContents.once("did-finish-load", () => {
      if (!mainWindow || mainWindow.isDestroyed()) return;
      mainWindow.webContents.send("start-listening");
    });
    return;
  }

  mainWindow.webContents.send("start-listening");
}

function createWindow() {
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;

  mainWindow = new BrowserWindow({
    width: width,
    height: height,
    x: display.workArea.x,
    y: display.workArea.y,
    show: true,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.setAlwaysOnTop(true, WINDOW_LEVEL);
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  mainWindow.setFullScreenable(false);
  setMousePassthrough(true);

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

function centerNearTop() {
  if (!mainWindow) return;
  const display = screen.getPrimaryDisplay();
  const x = Math.round(display.workArea.x + (display.workArea.width - WINDOW_WIDTH) / 2);
  const y = Math.max(display.workArea.y + 10, 8);
  mainWindow.setPosition(x, y, false);
}

function wakeOverlay() {
  if (!mainWindow) return;
  lastWakeAt = Date.now();
  mainWindow.show();
  emitStartListening();
}

function hideOverlay() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  setMousePassthrough(true);
  mainWindow.webContents.send("overlay-hidden");
}

function createDashboardWindow(agentId = null) {
  if (dashboardWindow && !dashboardWindow.isDestroyed()) {
    dashboardWindow.focus();
    return;
  }

  dashboardWindow = new BrowserWindow({
    width: 900,
    height: 600,
    minWidth: 640,
    minHeight: 400,
    show: false,
    frame: true,
    titleBarStyle: "hiddenInset",
    transparent: false,
    resizable: true,
    backgroundColor: "#f5f5f7",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (agentId) {
    dashboardWindow.loadFile(path.join(__dirname, "renderer", "dashboard.html"), {
      search: `agent=${encodeURIComponent(agentId)}`
    });
  } else {
    dashboardWindow.loadFile(path.join(__dirname, "renderer", "dashboard.html"));
  }

  dashboardWindow.once("ready-to-show", () => {
    dashboardWindow.show();
  });

  dashboardWindow.on("closed", () => {
    dashboardWindow = null;
  });
}

function registerHotkey() {
  globalShortcut.unregisterAll();
  let registeredCount = 0;

  for (const accelerator of HOTKEYS) {
    const ok = globalShortcut.register(accelerator, () => {
      wakeOverlay();
    });
    if (ok) {
      registeredCount += 1;
    } else {
      console.error(`Failed to register global shortcut: ${accelerator}`);
    }
  }

  // Register dashboard shortcut
  const dOk = globalShortcut.register("CommandOrControl+Shift+D", () => {
    createDashboardWindow();
  });
  if (dOk) {
    registeredCount += 1;
  } else {
    console.error("Failed to register dashboard shortcut: CommandOrControl+Shift+D");
  }

  if (registeredCount === 0) {
    console.error("No usable global shortcuts were registered.");
  }
}

function setMousePassthrough(ignore) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (ignore) {
    mainWindow.setIgnoreMouseEvents(true, { forward: true });
    return;
  }
  mainWindow.setIgnoreMouseEvents(false);
}

async function configureMicrophonePermissions() {
  session.defaultSession.setPermissionCheckHandler((_, permission) => {
    if (permission === "media" || permission === "microphone") {
      return true;
    }
    return false;
  });

  session.defaultSession.setPermissionRequestHandler((_, permission, callback) => {
    if (permission === "media" || permission === "microphone") {
      callback(true);
      return;
    }
    callback(false);
  });

  if (process.platform === "darwin") {
    const status = systemPreferences.getMediaAccessStatus("microphone");
    if (status !== "granted") {
      try {
        await systemPreferences.askForMediaAccess("microphone");
      } catch (err) {
        console.error("Microphone permission prompt failed:", err);
      }
    }
  }
}

app.whenReady().then(async () => {
  await configureMicrophonePermissions();

  // Start backend before creating the window
  startPythonBackend();

  createWindow();
  registerHotkey();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      registerHotkey();
    }
  });
});

ipcMain.handle("overlay:hide", () => {
  hideOverlay();
});

ipcMain.on("enable-mouse", () => {
  setMousePassthrough(false);
});

ipcMain.on("disable-mouse", () => {
  setMousePassthrough(true);
});

ipcMain.on("log-error", (event, msg) => {
  console.error(`[Renderer WS Error] ${msg}`);
});

ipcMain.on("log-info", (event, msg) => {
  console.log(`[Renderer Info] ${msg}`);
});

ipcMain.on("open-dashboard", (event, agentId) => {
  createDashboardWindow(agentId);
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  stopPythonBackend();
});

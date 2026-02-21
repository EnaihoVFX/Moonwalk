# Liquid Assistant (Electron Overlay)

## 1. Project scaffolding

```bash
mkdir liquid-assistant
cd liquid-assistant
npm init -y
npm install electron
```

Then set:

- `"main": "main.js"`
- `"scripts": { "start": "electron ." }`

## 2. Main process (`main.js`)

- Transparent frameless window (`transparent: true`, `frame: false`)
- Always on top and hidden from dock/task switch lists (`alwaysOnTop: true`, `skipTaskbar: true`)
- Window stays visible (no blur-to-hide)
- Ghost mouse mode enabled by default:
  - `mainWindow.setIgnoreMouseEvents(true, { forward: true })`
- Global hotkeys (defaults): `Cmd/Ctrl+Shift+Space` and `Option+Space`
  - sends `start-listening` to renderer
  - override with env var: `LIQUID_HOTKEY="Command+Shift+Space"` or multiple: `LIQUID_HOTKEY="Command+Shift+Space,Alt+Space"`

## 3. Visual layer (`renderer/index.html`, `renderer/styles.css`)

- Gooey SVG filter at the top of `<body>` with `id="gooey"`
- Single `#pill` container, centered horizontally and positioned higher on screen
- State-driven classes on `#ui-wrapper`:
  - `.state-idle`
  - `.state-listening`
  - `.state-thinking`
  - `.state-responding`
- `#response-text` hidden by default and fades in only during responding

## 4. Bridge layer (`renderer/renderer.js`)

- WebSocket endpoint:
  - `new WebSocket("ws://localhost:8000/ws")`
- `ws.onmessage` handles:
  - state updates (`payload.state`)
  - response text injection (`payload.text`)
- Audio capture:
  - `navigator.mediaDevices.getUserMedia({ audio: true })`
  - `MediaRecorder` chunks every `250ms`
  - raw blob chunks are streamed directly with `ws.send(event.data)`

## 5. Interaction polishing via IPC

- `mouseenter` on `#pill` -> `ipcRenderer.send("enable-mouse")`
- `mouseleave` on `#pill` -> `ipcRenderer.send("disable-mouse")`
- `main.js` handlers:
  - `enable-mouse` -> `mainWindow.setIgnoreMouseEvents(false)`
  - `disable-mouse` -> `mainWindow.setIgnoreMouseEvents(true, { forward: true })`

## macOS microphone permissions

- Runtime prompt in `main.js`:
  - `systemPreferences.askForMediaAccess("microphone")`
- Packaged app must include `Info.plist` key:

```xml
<key>NSMicrophoneUsageDescription</key>
<string>Liquid Assistant needs microphone access for voice commands.</string>
```

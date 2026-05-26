from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


COMPANION_VERSION = "0.1.15"
SUPPORTED_BROWSERS = {
    "chromium": "Chromium-family browsers: Chrome, Edge, Brave, Vivaldi, Opera, Arc, Chromium",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "vivaldi": "Vivaldi",
    "opera": "Opera",
    "arc": "Arc",
    "firefox": "Firefox",
}

CHROMIUM_MANIFEST = {
    "manifest_version": 3,
    "name": "Donovan Browser Companion",
    "version": COMPANION_VERSION,
    "description": "Lets Donovan Agent read and interact with the active browser tab with user permission.",
    "permissions": ["activeTab", "scripting", "tabs"],
    "host_permissions": ["<all_urls>", "http://127.0.0.1:8765/*", "http://localhost:8765/*"],
    "background": {"service_worker": "background.js"},
    "action": {"default_title": "Donovan Companion"},
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["content.js"],
            "run_at": "document_idle",
        }
    ],
}

FIREFOX_MANIFEST = {
    "manifest_version": 2,
    "name": "Donovan Browser Companion",
    "version": COMPANION_VERSION,
    "description": "Lets Donovan Agent read and interact with the active browser tab with user permission.",
    "permissions": [
        "activeTab",
        "tabs",
        "<all_urls>",
        "http://127.0.0.1:8765/*",
        "http://localhost:8765/*",
    ],
    "background": {"scripts": ["background.js"]},
    "browser_action": {"default_title": "Donovan Companion"},
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["content.js"],
            "run_at": "document_idle",
        }
    ],
    "browser_specific_settings": {
        "gecko": {
            "id": "donovan-browser-companion@tudor-iustin",
            "strict_min_version": "109.0",
        }
    },
}

BACKGROUND_JS = r"""
const SERVER = "http://127.0.0.1:8765";
const api = globalThis.browser || globalThis.chrome;

function call(fn, ...args) {
  return new Promise((resolve, reject) => {
    try {
      const maybe = fn(...args, result => {
        const err = api.runtime && api.runtime.lastError;
        if (err) reject(new Error(err.message));
        else resolve(result);
      });
      if (maybe && typeof maybe.then === "function") maybe.then(resolve, reject);
    } catch (err) {
      reject(err);
    }
  });
}

async function post(path, body) {
  try {
    await fetch(`${SERVER}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {})
    });
  } catch (err) {}
}

async function getCommand() {
  try {
    const response = await fetch(`${SERVER}/extension/poll`);
    if (!response.ok) return null;
    const data = await response.json();
    return data.command || null;
  } catch (err) {
    return null;
  }
}

async function queryTabs(query) {
  return await call(api.tabs.query.bind(api.tabs), query);
}

async function activeTab() {
  const tabs = await queryTabs({active: true, currentWindow: true});
  return tabs[0] || null;
}

async function listTabs() {
  const tabs = await queryTabs({});
  return tabs.map((tab, index) => ({
    index,
    id: tab.id,
    title: tab.title || "",
    url: tab.url || "",
    active: !!tab.active,
    windowId: tab.windowId
  }));
}

async function injectContentScript(tabId) {
  if (api.scripting && api.scripting.executeScript) {
    await call(api.scripting.executeScript.bind(api.scripting), {target: {tabId}, files: ["content.js"]});
    return;
  }
  await call(api.tabs.executeScript.bind(api.tabs), tabId, {file: "content.js"});
}

async function sendToTab(tabId, command) {
  try {
    return await call(api.tabs.sendMessage.bind(api.tabs), tabId, command);
  } catch (err) {
    await injectContentScript(tabId);
    return await call(api.tabs.sendMessage.bind(api.tabs), tabId, command);
  }
}

async function focusTab(tab) {
  if (!tab) return;
  await call(api.tabs.update.bind(api.tabs), tab.id, {active: true});
  if (api.windows && api.windows.update) {
    await call(api.windows.update.bind(api.windows), tab.windowId, {focused: true, state: "normal"});
  }
}

async function minimizeTab(tab) {
  if (!tab || !api.windows || !api.windows.update) return;
  await call(api.windows.update.bind(api.windows), tab.windowId, {state: "minimized"});
}

async function runCommand(command) {
  const tab = await activeTab();
  if (!tab && command.type !== "list_tabs") {
    return {success: false, error: "No active tab"};
  }
  if (command.type === "list_tabs") {
    return {success: true, tabs: await listTabs()};
  }
  if (command.type === "use_tab") {
    const tabs = await queryTabs({});
    const needle = String(command.tab || "").toLowerCase();
    let target = tabs.find((t, i) => String(i) === needle || String(t.id) === needle);
    if (!target) {
      target = tabs.find(t => (t.title || "").toLowerCase().includes(needle) || (t.url || "").toLowerCase().includes(needle));
    }
    if (!target) return {success: false, error: `No tab matched ${command.tab}`};
    await focusTab(target);
    return {success: true, title: target.title || "", url: target.url || ""};
  }
  if (command.type === "focus_browser") {
    await focusTab(tab);
    return {success: true, title: tab.title || "", url: tab.url || ""};
  }
  if (command.type === "minimize_browser") {
    await minimizeTab(tab);
    return {success: true, title: tab.title || "", url: tab.url || ""};
  }
  if (command.type === "active_tab") {
    await focusTab(tab);
    return {success: true, title: tab.title || "", url: tab.url || "", id: tab.id};
  }
  if (command.type === "screenshot") {
    await focusTab(tab);
    const dataUrl = await call(api.tabs.captureVisibleTab.bind(api.tabs), tab.windowId, {format: "png"});
    return {success: true, title: tab.title || "", url: tab.url || "", dataUrl};
  }
  await focusTab(tab);
  const result = await sendToTab(tab.id, command);
  return Object.assign({title: tab.title || "", url: tab.url || ""}, result || {});
}

async function tick() {
  const command = await getCommand();
  if (!command) return;
  let result;
  try {
    result = await runCommand(command);
  } catch (err) {
    result = {success: false, error: String(err && err.message || err)};
  }
  await post("/extension/result", {id: command.id, result});
}

setInterval(tick, 400);
if (api.runtime && api.runtime.onInstalled) api.runtime.onInstalled.addListener(() => tick());
const action = api.action || api.browserAction;
if (action && action.onClicked) action.onClicked.addListener(() => tick());
"""

CONTENT_JS = r"""
const api = globalThis.browser || globalThis.chrome;

function nodePath(el) {
  if (!el) return "";
  if (el.id) return `#${CSS.escape(el.id)}`;
  const parts = [];
  while (el && el.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
    let part = el.nodeName.toLowerCase();
    if (el.className && typeof el.className === "string") {
      const cls = el.className.trim().split(/\s+/).slice(0, 2).map(c => `.${CSS.escape(c)}`).join("");
      part += cls;
    }
    parts.unshift(part);
    el = el.parentElement;
  }
  return parts.join(" > ");
}

function snapshot() {
  const interactive = Array.from(document.querySelectorAll("a,button,input,textarea,select,[role='button'],[contenteditable='true']"))
    .slice(0, 120)
    .map((el, index) => ({
      index,
      selector: nodePath(el),
      text: (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim().slice(0, 160),
      tag: el.tagName.toLowerCase()
    }));
  return {
    success: true,
    title: document.title,
    url: location.href,
    text: (document.body ? document.body.innerText : "").slice(0, 20000),
    selection: String(window.getSelection ? window.getSelection() : ""),
    interactive
  };
}

api.runtime.onMessage.addListener((command, sender, sendResponse) => {
  (async () => {
    if (command.type === "snapshot") return snapshot();
    if (command.type === "click") {
      const el = document.querySelector(command.selector);
      if (!el) return {success: false, error: `Selector not found: ${command.selector}`};
      el.scrollIntoView({block: "center", inline: "center"});
      el.click();
      return {success: true};
    }
    if (command.type === "type") {
      const el = document.querySelector(command.selector);
      if (!el) return {success: false, error: `Selector not found: ${command.selector}`};
      el.focus();
      if ("value" in el) {
        el.value = command.text || "";
        el.dispatchEvent(new Event("input", {bubbles: true}));
        el.dispatchEvent(new Event("change", {bubbles: true}));
      } else {
        el.textContent = command.text || "";
        el.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: command.text || ""}));
      }
      return {success: true};
    }
    if (command.type === "press") {
      document.activeElement && document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: command.key || "Enter", bubbles: true}));
      return {success: true};
    }
    if (command.type === "evaluate") {
      const value = Function(`"use strict"; return (${command.script});`)();
      return {success: true, value: String(value)};
    }
    return {success: false, error: `Unknown command: ${command.type}`};
  })().then(sendResponse).catch(err => sendResponse({success: false, error: String(err && err.message || err)}));
  return true;
});
"""


@dataclass
class CompanionCommand:
    id: str
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    event: threading.Event = field(default_factory=threading.Event)


class BrowserCompanionService:
    def __init__(self, data_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.data_dir = Path(data_dir)
        self.extension_root = self.data_dir / "browser_companion_extensions"
        self.extension_dir = self.extension_root / "chromium"
        self.firefox_extension_dir = self.extension_root / "firefox"
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._queue: "queue.Queue[CompanionCommand]" = queue.Queue()
        self._pending: dict[str, CompanionCommand] = {}
        self._lock = threading.Lock()
        self._last_seen = 0.0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def install_extension_files(self) -> Path:
        self._write_extension(self.extension_dir, CHROMIUM_MANIFEST)
        self._write_extension(self.firefox_extension_dir, FIREFOX_MANIFEST)
        return self.extension_root

    def _write_extension(self, path: Path, manifest: dict[str, Any]) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (path / "background.js").write_text(BACKGROUND_JS, encoding="utf-8")
        (path / "content.js").write_text(CONTENT_JS, encoding="utf-8")

    def setup_instructions(self, browser: str | None = None, open_page: bool = True) -> str:
        self.install_extension_files()
        browser_key = self._normalize_browser(browser)
        opened = self._open_extension_page(browser_key) if open_page else False
        return self._format_setup_instructions(browser_key, opened=opened)

    def _normalize_browser(self, browser: str | None) -> str:
        key = (browser or "chromium").strip().lower()
        aliases = {
            "default": "chromium",
            "chrome": "chrome",
            "google": "chrome",
            "google-chrome": "chrome",
            "msedge": "edge",
            "microsoft-edge": "edge",
            "ff": "firefox",
            "mozilla": "firefox",
            "mozilla-firefox": "firefox",
        }
        key = aliases.get(key, key)
        return key if key in SUPPORTED_BROWSERS else "chromium"

    def _extension_page_url(self, browser: str) -> str:
        if browser == "firefox":
            return "about:debugging#/runtime/this-firefox"
        if browser == "edge":
            return "edge://extensions/"
        if browser == "brave":
            return "brave://extensions/"
        if browser == "vivaldi":
            return "vivaldi://extensions/"
        if browser == "opera":
            return "opera://extensions/"
        if browser == "arc":
            return "arc://extensions/"
        return "chrome://extensions/"

    def _open_extension_page(self, browser: str) -> bool:
        url = self._extension_page_url(browser)
        command = self._browser_open_command(browser, url)
        if not command:
            return False
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    def _browser_open_command(self, browser: str, url: str) -> list[str] | None:
        if sys.platform == "win32":
            return self._windows_browser_command(browser, url)
        if sys.platform == "darwin":
            return self._macos_browser_command(browser, url)
        return self._linux_browser_command(browser, url)

    def _windows_browser_command(self, browser: str, url: str) -> list[str] | None:
        names = {
            "chrome": ["chrome.exe", "chrome"],
            "chromium": ["chrome.exe", "chrome", "chromium.exe", "chromium"],
            "edge": ["msedge.exe", "msedge"],
            "brave": ["brave.exe", "brave"],
            "vivaldi": ["vivaldi.exe", "vivaldi"],
            "opera": ["opera.exe", "opera"],
            "firefox": ["firefox.exe", "firefox"],
        }.get(browser, ["chrome.exe", "chrome", "msedge.exe", "msedge"])
        for name in names:
            path = shutil.which(name)
            if path:
                return [path, url]
        common_paths = {
            "edge": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
            "chrome": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ],
            "firefox": [
                r"C:\Program Files\Mozilla Firefox\firefox.exe",
                r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            ],
            "brave": [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ],
        }
        for path_text in common_paths.get(browser, []):
            path = Path(path_text)
            if path.exists():
                return [str(path), url]
        return None

    def _macos_browser_command(self, browser: str, url: str) -> list[str] | None:
        apps = {
            "chrome": "Google Chrome",
            "chromium": "Chromium",
            "edge": "Microsoft Edge",
            "brave": "Brave Browser",
            "vivaldi": "Vivaldi",
            "opera": "Opera",
            "arc": "Arc",
            "firefox": "Firefox",
        }
        app = apps.get(browser)
        if not app:
            return None
        return ["open", "-a", app, url]

    def _linux_browser_command(self, browser: str, url: str) -> list[str] | None:
        names = {
            "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
            "chromium": ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"],
            "edge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
            "brave": ["brave-browser", "brave"],
            "vivaldi": ["vivaldi", "vivaldi-stable"],
            "opera": ["opera"],
            "firefox": ["firefox"],
        }.get(browser, ["chromium", "chromium-browser", "google-chrome", "firefox"])
        for name in names:
            path = shutil.which(name)
            if path:
                return [path, url]
        return None

    def _format_setup_instructions(self, browser: str, opened: bool) -> str:
        chromium_path = self.extension_dir
        firefox_path = self.firefox_extension_dir
        page_url = self._extension_page_url(browser)
        opened_text = (
            "Donovan opened the matching extension page automatically."
            if opened
            else "Donovan could not open the extension page automatically. Open it manually in your browser."
        )
        platform_note = (
            "On macOS, Linux, or Windows, use the extension folder that matches your browser."
        )
        safari_note = (
            "Safari uses a different signed Safari Web Extension package, so Donovan cannot load "
            "this unpacked extension directly there yet. Use Chrome, Edge, Brave, Vivaldi, Opera, "
            "Arc, Chromium, or Firefox for companion-mode tab control."
        )
        return (
            f"Extension folders:\n"
            f"- Chromium-family: {chromium_path}\n"
            f"- Firefox: {firefox_path}\n\n"
            f"Selected browser: {SUPPORTED_BROWSERS.get(browser, SUPPORTED_BROWSERS['chromium'])}\n"
            f"{opened_text}\n"
            f"Extension page: {page_url}\n\n"
            "Chromium-family setup:\n"
            "1. Open your browser's Extensions page.\n"
            "2. Turn on Developer mode.\n"
            "3. Click Load unpacked.\n"
            "4. Select the Chromium-family extension folder above.\n\n"
            "Firefox setup:\n"
            "1. Open about:debugging#/runtime/this-firefox.\n"
            "2. Click Load Temporary Add-on.\n"
            "3. Select manifest.json inside the Firefox extension folder above.\n\n"
            f"{platform_note}\n"
            f"{safari_note}\n\n"
            "Then run /browser companion start and use /browser companion active, "
            "/browser companion snapshot, or ask Donovan about the active tab."
        )

    def start(self) -> None:
        if self._server is not None:
            return
        self.install_extension_files()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._send_json(200, {})

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/extension/poll":
                    service._last_seen = __import__("time").time()
                    try:
                        command = service._queue.get_nowait()
                    except queue.Empty:
                        self._send_json(200, {"command": None})
                        return
                    with service._lock:
                        service._pending[command.id] = command
                    self._send_json(200, {"command": command.payload})
                elif self.path == "/status":
                    self._send_json(200, service.status())
                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if self.path == "/extension/result":
                    command_id = str(data.get("id", ""))
                    with service._lock:
                        command = service._pending.pop(command_id, None)
                    if command is not None:
                        command.result = data.get("result") or {}
                        command.event.set()
                    self._send_json(200, {"ok": True})
                else:
                    self._send_json(404, {"error": "not found"})

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def status(self) -> dict[str, Any]:
        import time

        connected = self._last_seen > 0 and (time.time() - self._last_seen) < 5
        return {
            "running": self._server is not None,
            "url": self.url,
            "platform": sys.platform,
            "chromium_extension_dir": str(self.extension_dir),
            "firefox_extension_dir": str(self.firefox_extension_dir),
            "extension_connected": connected,
            "supported_browsers": list(SUPPORTED_BROWSERS),
        }

    def command(self, command_type: str, **kwargs: Any) -> dict[str, Any]:
        self.start()
        command_id = str(uuid.uuid4())
        payload = {"id": command_id, "type": command_type, **kwargs}
        command = CompanionCommand(id=command_id, payload=payload)
        self._queue.put(command)
        if not command.event.wait(timeout=10):
            return {
                "success": False,
                "error": (
                    "Browser companion did not respond. Make sure the matching extension is "
                    "installed and enabled in your current browser."
                ),
            }
        return command.result or {"success": False, "error": "No result from browser companion."}

    def focus(self) -> dict[str, Any]:
        """Bring the active companion browser window forward while Donovan works."""
        return self.command("focus_browser")

    def minimize(self) -> dict[str, Any]:
        """Minimize the active companion browser window after Donovan finishes browser work."""
        return self.command("minimize_browser")

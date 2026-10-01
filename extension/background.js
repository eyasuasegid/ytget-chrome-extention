/**
 * ytget Chrome Extension - Background Service Worker (Manifest V3)
 * Handles communication with the local companion server and native messaging host.
 * Bypasses webpage Mixed Content, PNA, and CSP restrictions.
 */

const DEFAULT_SETTINGS = {
  downloadDir: "",
  defaultQuality: "best",
  autoStartServer: true,
  showInPageBtn: true,
  notifyComplete: true,
  serverPort: 8765,
  displayMode: "popup",
  skipExisting: true,
  embedThumbnail: true,
  maxConcurrent: 10,
};

function getSettings() {
  return new Promise((resolve) => {
    if (chrome?.storage?.local) {
      chrome.storage.local.get("ytget_settings", (res) => {
        resolve({ ...DEFAULT_SETTINGS, ...(res?.ytget_settings || {}) });
      });
    } else {
      resolve(DEFAULT_SETTINGS);
    }
  });
}

async function isServerRunning(port = 8765) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1200);
    const resp = await fetch(`http://127.0.0.1:${port}/status`, {
      method: "GET",
      signal: controller.signal,
    });
    clearTimeout(timer);
    return resp.ok;
  } catch {
    return false;
  }
}

function startServerNative() {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage("com.ytget.server", { action: "start" }, (resp) => {
        if (chrome.runtime.lastError) {
          resolve({ success: false, error: chrome.runtime.lastError.message });
        } else {
          resolve({ success: true, response: resp });
        }
      });
    } catch (e) {
      resolve({ success: false, error: e.message });
    }
  });
}

async function ensureServerRunning(port, autoStart) {
  if (await isServerRunning(port)) {
    return { ok: true };
  }
  if (!autoStart) {
    return {
      ok: false,
      error: "Companion server is offline. Please start it from the extension popup.",
    };
  }

  // Attempt to launch via Native Messaging host
  const launchRes = await startServerNative();
  if (!launchRes.success) {
    return {
      ok: false,
      error: `Could not auto-start companion server: ${launchRes.error}`,
    };
  }

  // Poll until online (up to 3 seconds)
  for (let i = 0; i < 6; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await isServerRunning(port)) {
      return { ok: true };
    }
  }

  return { ok: false, error: `Server launched but port ${port} did not respond in time.` };
}

let lastKnownMedia = null;

function safeBroadcastMessage(msg) {
  try {
    chrome.runtime.sendMessage(msg, () => {
      // Accessing chrome.runtime.lastError explicitly clears unchecked error status
      void chrome.runtime.lastError;
    });
  } catch {}
}

// -------------------------------------------------------------
// Message Listener
// -------------------------------------------------------------
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "START_DOWNLOAD") {
    (async () => {
      try {
        const settings = await getSettings();
        const port = settings.serverPort || 8765;

        // Ensure companion server is up
        const ready = await ensureServerRunning(port, settings.autoStartServer);
        if (!ready.ok) {
          sendResponse({ success: false, error: ready.error });
          return;
        }

        const payload = {
          url: message.url,
          quality: message.quality || settings.defaultQuality || "best",
          skip_existing: settings.skipExisting !== false,
          embed_thumbnail: settings.embedThumbnail !== false,
          output_dir: message.output_dir || settings.downloadDir || undefined,
        };

        const resp = await fetch(`http://127.0.0.1:${port}/download`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

        if (resp.ok) {
          const data = await resp.json();
          // Broadcast to all extension views (popup, side panel) safely
          safeBroadcastMessage({
            type: "DOWNLOAD_STARTED",
            job_id: data.job_id,
            url: message.url,
            quality: payload.quality,
            data: data,
          });

          sendResponse({ success: true, data });
        } else {
          const errData = await resp.json().catch(() => ({}));
          sendResponse({
            success: false,
            error: errData.error || `Server error (HTTP ${resp.status})`,
          });
        }
      } catch (err) {
        sendResponse({
          success: false,
          error: err.message || "Failed to contact companion server",
        });
      }
    })();
    return true; // Keep channel open for async response
  }

  if (message.type === "PROBE_URL") {
    (async () => {
      try {
        const settings = await getSettings();
        const port = settings.serverPort || 8765;
        const targetUrl = message.url;
        if (!targetUrl) {
          sendResponse({ success: false, error: "Missing url" });
          return;
        }

        const resp = await fetch(`http://127.0.0.1:${port}/probe?url=${encodeURIComponent(targetUrl)}`);
        if (resp.ok) {
          const data = await resp.json();
          sendResponse({ success: true, data });
        } else {
          const errData = await resp.json().catch(() => ({}));
          sendResponse({ success: false, error: errData.error || `HTTP ${resp.status}` });
        }
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  if (message.type === "OPEN_SIDE_PANEL") {
    (async () => {
      let windowId = sender.tab?.windowId;
      if (!windowId && chrome?.tabs) {
        try {
          const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
          windowId = tab?.windowId;
        } catch {}
      }

      if (chrome?.sidePanel?.open && windowId) {
        chrome.sidePanel.open({ windowId })
          .then(() => sendResponse({ success: true }))
          .catch((e) => sendResponse({ success: false, error: e.message }));
      } else {
        sendResponse({ success: false, error: "Side panel not supported or no active window" });
      }
    })();
    return true;
  }

  if (message.type === "SWITCH_TO_POPUP_AND_OPEN" || message.type === "OPEN_POPUP") {
    (async () => {
      try {
        if (chrome?.sidePanel?.setPanelBehavior) {
          try {
            await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false });
          } catch {}
        }
        if (chrome?.action?.setPopup) {
          try {
            await chrome.action.setPopup({ popup: "popup.html" });
          } catch {}
        }

        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        const windowId = tab?.windowId;

        // Ensure Chrome browser window is focused
        if (windowId && chrome?.windows?.update) {
          try {
            await chrome.windows.update(windowId, { focused: true });
          } catch {}
        }

        // Allow side panel close animation and focus to settle
        await new Promise((r) => setTimeout(r, 220));

        if (chrome?.action?.openPopup) {
          try {
            if (windowId) {
              await chrome.action.openPopup({ windowId });
            } else {
              await chrome.action.openPopup();
            }
            sendResponse({ success: true });
          } catch (popErr) {
            sendResponse({ success: false, error: popErr?.message || "openPopup failed" });
          }
        } else {
          sendResponse({ success: false, error: "action.openPopup not supported" });
        }
      } catch (e) {
        sendResponse({ success: false, error: e.message });
      }
    })();
    return true;
  }

  if (message.type === "CHECK_SERVER") {
    (async () => {
      const settings = await getSettings();
      const online = await isServerRunning(settings.serverPort || 8765);
      sendResponse({ online, port: settings.serverPort || 8765 });
    })();
    return true;
  }

  if (message.type === "YOUTUBE_MEDIA_CHANGED") {
    lastKnownMedia = message.data || null;
    sendResponse({ success: true });
    return false;
  }

  if (message.type === "GET_LAST_MEDIA") {
    sendResponse({ success: true, data: lastKnownMedia });
    return false;
  }

  // Fallback acknowledgement for any other message type to prevent "Receiving end does not exist"
  sendResponse({ received: true });
  return false;
});

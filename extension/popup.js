/**
 * ytget Chrome Extension - Popup & Settings Controller
 * Handles downloads, live progress, native server control, settings, and Chrome Side Panel integration.
 */

const DEFAULT_SETTINGS = {
  downloadDir: "",
  displayMode: "popup", // "popup" | "sidepanel"
  defaultQuality: "best",
  skipExisting: true,
  embedThumbnail: true,
  maxConcurrent: 10,
  autoStartServer: true,
  showInPageBtn: true,
  notifyComplete: true,
  serverPort: 8765,
};

let currentSettings = { ...DEFAULT_SETTINGS };
let SERVER_BASE = `http://127.0.0.1:${currentSettings.serverPort}`;
let activeJobsCount = 0;
let pollInterval = null;
let isServerOnline = false;
const previouslyCompletedJobIds = new Set();
const isInsideSidePanel = location.pathname.endsWith("sidepanel.html") || location.search.includes("sidepanel");

function safeSendMessage(msg, callback) {
  try {
    if (!chrome?.runtime?.sendMessage) return;
    chrome.runtime.sendMessage(msg, (response) => {
      const err = chrome.runtime.lastError;
      if (typeof callback === "function") {
        callback(err ? null : response, err);
      }
    });
  } catch {}
}

// DOM Elements - Views & Navigation
const mainView = document.getElementById("mainView");
const settingsView = document.getElementById("settingsView");
const settingsToggleBtn = document.getElementById("settingsToggleBtn");
const backFromSettingsBtn = document.getElementById("backFromSettingsBtn");

// DOM Elements - Header & Server Controls
const serverStatus = document.getElementById("serverStatus");
const serverToggleBtn = document.getElementById("serverToggleBtn");
const serverToggleIcon = document.getElementById("serverToggleIcon");
const serverToggleText = document.getElementById("serverToggleText");
const serverAlert = document.getElementById("serverAlert");
const quickStartBtn = document.getElementById("quickStartBtn");
const footerPortLabel = document.getElementById("footerPortLabel");
const refreshBtn = document.getElementById("refreshBtn");
const toastMessage = document.getElementById("toastMessage");

// DOM Elements - Downloader
const urlInput = document.getElementById("urlInput");
const pasteBtn = document.getElementById("pasteBtn");
const mediaTypeBadge = document.getElementById("mediaTypeBadge");
const mediaPreview = document.getElementById("mediaPreview");
const mediaTitle = document.getElementById("mediaTitle");
const playlistSwitchGroup = document.getElementById("playlistSwitchGroup");
const btnTargetPlaylist = document.getElementById("btnTargetPlaylist");
const btnTargetVideo = document.getElementById("btnTargetVideo");
const qualitySelect = document.getElementById("qualitySelect");
const skipExistingCheck = document.getElementById("skipExistingCheck");
const embedThumbnailCheck = document.getElementById("embedThumbnailCheck");
const downloadBtn = document.getElementById("downloadBtn");
const activeDownloadDirLabel = document.getElementById("activeDownloadDirLabel");

// DOM Elements - Progress (Multi-download container)
const progressCardsContainer = document.getElementById("progressCardsContainer");
const historyList = document.getElementById("historyList");

// DOM Elements - Settings Form
const modePopupBtn = document.getElementById("modePopupBtn");
const modeSidebarBtn = document.getElementById("modeSidebarBtn");
const openSidePanelNowBtn = document.getElementById("openSidePanelNowBtn");
const settingDownloadDir = document.getElementById("settingDownloadDir");
const settingDefaultQuality = document.getElementById("settingDefaultQuality");
const settingMaxConcurrent = document.getElementById("settingMaxConcurrent");
const settingAutoStart = document.getElementById("settingAutoStart");
const settingShowInPageBtn = document.getElementById("settingShowInPageBtn");
const settingNotifyComplete = document.getElementById("settingNotifyComplete");
const settingServerPort = document.getElementById("settingServerPort");
const saveSettingsBtn = document.getElementById("saveSettingsBtn");
const resetSettingsBtn = document.getElementById("resetSettingsBtn");

const NATIVE_HOST = "com.ytget.server";

document.addEventListener("DOMContentLoaded", async () => {
  if (isInsideSidePanel) {
    document.body.classList.add("sidepanel-view");
  }
  await loadStoredSettings();
  setupEventListeners();

  const isUp = await checkServerStatus();
  if (!isUp && currentSettings.autoStartServer) {
    await startServerNative();
  }

  await detectActiveTab();
  watchActiveTabChanges();
  await loadRecentTasks();

  // Instant real-time task sync every 1 second while popup/sidepanel is open
  setInterval(loadRecentTasks, 1000);
});

// Real-time synchronization with background worker, content script, and other extension views
chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "YOUTUBE_MEDIA_CHANGED" && message.data) {
    handleMediaDetected(message.data);
  } else if (message.type === "DOWNLOAD_STARTED" || message.type === "JOB_STATUS_CHANGED") {
    startProgressPolling();
    loadRecentTasks();
  } else if (message.type === "SETTINGS_CHANGED") {
    loadStoredSettings();
  }
});

// -------------------------------------------------------------
// Event Listeners Setup
// -------------------------------------------------------------
function setupEventListeners() {
  // Navigation
  settingsToggleBtn.addEventListener("click", () => switchView(true));
  backFromSettingsBtn.addEventListener("click", () => switchView(false));

  // Server controls
  serverToggleBtn.addEventListener("click", toggleServer);
  if (quickStartBtn) {
    quickStartBtn.addEventListener("click", startServerNative);
  }
  refreshBtn.addEventListener("click", () => {
    checkServerStatus();
    loadRecentTasks();
  });

  // URL input
  pasteBtn.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        urlInput.value = text.trim();
        analyzeUrl(urlInput.value);
      }
    } catch {
      urlInput.focus();
    }
  });

  urlInput.addEventListener("input", () => {
    const val = urlInput.value.trim();
    const parsed = parseYouTubeUrl(val);
    handleMediaDetected(parsed);
  });

  if (btnTargetPlaylist) {
    btnTargetPlaylist.addEventListener("click", selectPlaylistMode);
  }
  if (btnTargetVideo) {
    btnTargetVideo.addEventListener("click", selectSingleMode);
  }

  // Quick settings on main view
  qualitySelect.addEventListener("change", () => {
    currentSettings.defaultQuality = qualitySelect.value;
    saveSettingsToStorage();
  });
  skipExistingCheck.addEventListener("change", () => {
    currentSettings.skipExisting = skipExistingCheck.checked;
    saveSettingsToStorage();
  });
  embedThumbnailCheck.addEventListener("change", () => {
    currentSettings.embedThumbnail = embedThumbnailCheck.checked;
    saveSettingsToStorage();
  });

  // Download Action
  downloadBtn.addEventListener("click", handleDownloadClick);

  // Multi-job Progress Cards Event Delegation (Pause/Resume & Cancel)
  if (progressCardsContainer) {
    progressCardsContainer.addEventListener("click", async (e) => {
      const btn = e.target.closest(".progress-action-btn");
      if (!btn) return;
      const jobId = btn.getAttribute("data-job-id");
      const action = btn.getAttribute("data-action");
      if (!jobId || !action) return;

      if (action === "cancel") {
        await handleCancelJob(jobId);
      } else if (action === "toggle-pause") {
        const isPaused = btn.classList.contains("btn-resume");
        await handleTogglePauseJob(jobId, isPaused);
      }
    });
  }

  // Settings Panel - Real-time auto-saving
  settingDefaultQuality.addEventListener("change", autoSaveFromSettingsForm);
  if (settingMaxConcurrent) {
    settingMaxConcurrent.addEventListener("change", autoSaveFromSettingsForm);
    settingMaxConcurrent.addEventListener("input", autoSaveFromSettingsForm);
  }
  settingAutoStart.addEventListener("change", autoSaveFromSettingsForm);
  settingShowInPageBtn.addEventListener("change", autoSaveFromSettingsForm);
  settingNotifyComplete.addEventListener("change", autoSaveFromSettingsForm);
  settingServerPort.addEventListener("change", autoSaveFromSettingsForm);
  settingDownloadDir.addEventListener("input", autoSaveFromSettingsForm);

  // Settings Panel - Display Mode (Popup vs Side Panel)
  modePopupBtn.addEventListener("click", () => setDisplayMode("popup"));
  modeSidebarBtn.addEventListener("click", () => setDisplayMode("sidepanel"));
  openSidePanelNowBtn.addEventListener("click", openSidePanelNow);

  // Settings Panel - Presets
  document.querySelectorAll(".preset-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const dir = chip.getAttribute("data-dir");
      settingDownloadDir.value = dir === "default" ? "" : dir;
      autoSaveFromSettingsForm();
    });
  });

  // Settings Panel - Save & Reset
  saveSettingsBtn.addEventListener("click", handleSaveSettings);
  resetSettingsBtn.addEventListener("click", handleResetSettings);
}

// -------------------------------------------------------------
// View Switching
// -------------------------------------------------------------
function switchView(showSettings) {
  if (showSettings) {
    mainView.classList.add("hidden");
    settingsView.classList.remove("hidden");
    populateSettingsForm();
  } else {
    settingsView.classList.add("hidden");
    mainView.classList.remove("hidden");
  }
}

// -------------------------------------------------------------
// Storage & Settings Management
// -------------------------------------------------------------
function loadStoredSettings() {
  return new Promise((resolve) => {
    if (!chrome?.storage?.local) {
      applySettingsToUI();
      return resolve(currentSettings);
    }

    chrome.storage.local.get("ytget_settings", (result) => {
      if (result && result.ytget_settings) {
        currentSettings = { ...DEFAULT_SETTINGS, ...result.ytget_settings };
      }
      SERVER_BASE = `http://127.0.0.1:${currentSettings.serverPort || 8765}`;
      applySettingsToUI();
      resolve(currentSettings);
    });
  });
}

function saveSettingsToStorage() {
  if (chrome?.storage?.local) {
    chrome.storage.local.set({ ytget_settings: currentSettings });
  }
}

function autoSaveFromSettingsForm() {
  currentSettings.downloadDir = settingDownloadDir.value.trim();
  currentSettings.defaultQuality = settingDefaultQuality.value;
  currentSettings.autoStartServer = settingAutoStart.checked;
  currentSettings.showInPageBtn = settingShowInPageBtn.checked;
  currentSettings.notifyComplete = settingNotifyComplete.checked;

  if (settingMaxConcurrent) {
    const mc = parseInt(settingMaxConcurrent.value, 10);
    if (!isNaN(mc) && mc >= 1 && mc <= 50) {
      currentSettings.maxConcurrent = mc;
      syncMaxConcurrentToServer(mc);
    }
  }

  const port = parseInt(settingServerPort.value, 10);
  if (port >= 1024 && port <= 65535) {
    currentSettings.serverPort = port;
    SERVER_BASE = `http://127.0.0.1:${port}`;
  }

  saveSettingsToStorage();
  applySettingsToUI();
}

async function syncMaxConcurrentToServer(count) {
  if (!isServerOnline) return;
  try {
    await fetch(`${SERVER_BASE}/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_concurrent: count }),
    });
  } catch (e) {
    console.debug("Could not sync max_concurrent to server:", e);
  }
}

function applySettingsToUI() {
  qualitySelect.value = currentSettings.defaultQuality || "best";
  skipExistingCheck.checked = !!currentSettings.skipExisting;
  embedThumbnailCheck.checked = !!currentSettings.embedThumbnail;

  const portStr = currentSettings.serverPort || 8765;
  footerPortLabel.innerHTML = `ytget v1.0 • Port: <strong>${portStr}</strong>`;

  const destStr = currentSettings.downloadDir || "downloads/";
  activeDownloadDirLabel.textContent = destStr.length > 22 ? destStr.slice(0, 19) + "..." : destStr;
  activeDownloadDirLabel.title = currentSettings.downloadDir || "Default project folder";

  if (openSidePanelNowBtn) {
    if (isInsideSidePanel) {
      openSidePanelNowBtn.textContent = "🗗 Switch to Popup Window Now";
    } else {
      openSidePanelNowBtn.textContent = "◨ Open in Chrome Side Panel Now";
    }
  }
}

function populateSettingsForm() {
  settingDownloadDir.value = currentSettings.downloadDir || "";
  settingDefaultQuality.value = currentSettings.defaultQuality || "best";
  if (settingMaxConcurrent) {
    settingMaxConcurrent.value = currentSettings.maxConcurrent || 10;
  }
  settingAutoStart.checked = !!currentSettings.autoStartServer;
  settingShowInPageBtn.checked = currentSettings.showInPageBtn !== false;
  settingNotifyComplete.checked = currentSettings.notifyComplete !== false;
  settingServerPort.value = currentSettings.serverPort || 8765;

  updateDisplayModeButtons(currentSettings.displayMode);
}

function handleSaveSettings() {
  autoSaveFromSettingsForm();
  showToast("✅ Settings saved successfully!", 2500);
  setTimeout(() => switchView(false), 500);
}

function handleResetSettings() {
  currentSettings = { ...DEFAULT_SETTINGS };
  saveSettingsToStorage();
  populateSettingsForm();
  applySettingsToUI();
  setDisplayMode("popup");
  showToast("Defaults restored", 2500);
}

// -------------------------------------------------------------
// Display Mode (Popup vs Chrome Side Panel)
// -------------------------------------------------------------
async function setDisplayMode(mode) {
  currentSettings.displayMode = mode;
  saveSettingsToStorage();
  updateDisplayModeButtons(mode);

  if (chrome?.sidePanel?.setPanelBehavior) {
    try {
      const openOnAction = (mode === "sidepanel");
      await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: openOnAction });
    } catch (e) {
      console.warn("Could not set panel behavior:", e);
    }
  }

  if (mode === "sidepanel") {
    // If user is ALREADY inside the Side Panel, DO NOT close!
    if (isInsideSidePanel) {
      showToast("Already inside Side Panel", 2000);
      return;
    }

    // We are currently in the floating popup window:
    // Open the side panel immediately, then close this popup window
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab?.windowId && chrome?.sidePanel?.open) {
        await chrome.sidePanel.open({ windowId: tab.windowId });
        showToast("Switched to Side Panel!", 2000);
        setTimeout(() => window.close(), 150);
      }
    } catch (err) {
      showToast(`Side Panel: ${err.message}`, 3000);
    }
  } else {
    // User selected "popup"
    if (!isInsideSidePanel) {
      showToast("Already in Popup Window mode", 2000);
      return;
    }

    // We are currently in the Side Panel:
    // Delegate window focus and popup opening to background worker, then close side panel
    safeSendMessage({ type: "SWITCH_TO_POPUP_AND_OPEN" });
    window.close();
  }
}

function updateDisplayModeButtons(mode) {
  if (mode === "sidepanel") {
    modeSidebarBtn.classList.add("active");
    modePopupBtn.classList.remove("active");
  } else {
    modePopupBtn.classList.add("active");
    modeSidebarBtn.classList.remove("active");
  }
}

async function openSidePanelNow() {
  if (isInsideSidePanel) {
    // If in side panel, this button switches back to popup
    setDisplayMode("popup");
    return;
  }

  if (chrome?.sidePanel?.open) {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab?.windowId) {
        await chrome.sidePanel.open({ windowId: tab.windowId });
        window.close(); // Close current popup window
      }
    } catch (err) {
      showToast(`Side Panel open error: ${err.message}`, 4000);
    }
  } else {
    showToast("Side Panel API not available in this browser", 4000);
  }
}

// -------------------------------------------------------------
// Server Connectivity & Native Messaging Controls
// -------------------------------------------------------------
async function checkServerStatus() {
  setServerUI("checking", "Checking...");
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);

    const resp = await fetch(`${SERVER_BASE}/status`, {
      method: "GET",
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (resp.ok) {
      const data = await resp.json();
      isServerOnline = true;
      syncMaxConcurrentToServer(currentSettings.maxConcurrent || 10);
      setServerUI("online", `Online (${currentSettings.serverPort})`);
      setToggleBtnState("Stop", false);
      serverAlert.classList.add("hidden");
      downloadBtn.disabled = false;
      return true;
    }
    throw new Error("Invalid response");
  } catch (err) {
    isServerOnline = false;
    setServerUI("offline", "Offline");
    setToggleBtnState("Start", false);
    serverAlert.classList.remove("hidden");
    downloadBtn.disabled = true;
    return false;
  }
}

async function toggleServer() {
  if (isServerOnline) {
    await stopServerNative();
  } else {
    await startServerNative();
  }
}

function startServerNative() {
  setToggleBtnState("Starting...", true);
  showToast("Launching ytget server...", 2000);

  return new Promise((resolve) => {
    if (!chrome?.runtime?.sendNativeMessage) {
      showToast("Native messaging not available", 4000);
      setToggleBtnState("Start", false);
      return resolve(false);
    }

    chrome.runtime.sendNativeMessage(NATIVE_HOST, { action: "start" }, async (response) => {
      if (chrome.runtime.lastError) {
        const errDetail = chrome.runtime.lastError.message || "Host disconnected";
        console.warn("Native host error:", errDetail);
        showToast(`Native host error: ${errDetail}`, 6000);
        setToggleBtnState("Start", false);
        return resolve(false);
      }

      if (response && (response.status === "started" || response.status === "already_running")) {
        showToast("🚀 Server started successfully!", 3000);
        await checkServerStatus();
        await loadRecentTasks();
        return resolve(true);
      } else {
        showToast(`Server launch error: ${response?.message || "Unknown error"}`, 5000);
        setToggleBtnState("Start", false);
        return resolve(false);
      }
    });
  });
}

function stopServerNative() {
  setToggleBtnState("Stopping...", true);
  showToast("Stopping ytget server...", 2000);

  return new Promise((resolve) => {
    if (!chrome?.runtime?.sendNativeMessage) {
      showToast("Native messaging not available", 3000);
      return resolve(false);
    }

    chrome.runtime.sendNativeMessage(NATIVE_HOST, { action: "stop" }, async (response) => {
      if (chrome.runtime.lastError) {
        showToast(`Native error: ${chrome.runtime.lastError.message}`, 5000);
        return resolve(false);
      }
      showToast("⏹ Server stopped", 3000);
      await checkServerStatus();
      resolve(true);
    });
  });
}

function setToggleBtnState(text, disabled) {
  if (!serverToggleBtn) return;
  serverToggleText.textContent = text;
  serverToggleBtn.disabled = disabled;
  if (text.includes("Stop") || text.includes("Stopping")) {
    serverToggleIcon.textContent = "⏹";
    serverToggleBtn.classList.add("is-running");
    serverToggleBtn.title = "Click to stop ytget server";
  } else {
    serverToggleIcon.textContent = "▶";
    serverToggleBtn.classList.remove("is-running");
    serverToggleBtn.title = "Click to start ytget server";
  }
}

function setServerUI(state, text) {
  serverStatus.className = `status-badge ${state}`;
  const dot = serverStatus.querySelector(".status-dot") || document.createElement("span");
  dot.className = "status-dot";
  const label = serverStatus.querySelector(".status-text") || document.createElement("span");
  label.className = "status-text";
  label.textContent = text;
  serverStatus.innerHTML = "";
  serverStatus.appendChild(dot);
  serverStatus.appendChild(label);
}

// -------------------------------------------------------------
// Active Tab Detection & Adaptive YouTube Tracking
// -------------------------------------------------------------
let currentMediaData = {
  url: "",
  title: "",
  videoUrl: null,
  playlistUrl: null,
  isPlaylist: false,
  mode: "playlist",
};

let lastActiveTabUrl = "";

function parseYouTubeUrl(url, rawTitle = "") {
  try {
    const urlObj = new URL(url);
    const pathname = urlObj.pathname;
    const searchParams = urlObj.searchParams;

    const isWatch = pathname.includes("/watch");
    const isShorts = pathname.includes("/shorts/");
    const isPlaylistPage = pathname.includes("/playlist");
    const listParam = searchParams.get("list");
    const isPlaylist = isPlaylistPage || (!!listParam && listParam !== "WL" && listParam !== "LL");

    let videoUrl = null;
    let playlistUrl = null;

    if (isWatch && searchParams.get("v")) {
      videoUrl = `https://www.youtube.com/watch?v=${searchParams.get("v")}`;
    } else if (isShorts) {
      videoUrl = url;
    }

    if (listParam && listParam !== "WL" && listParam !== "LL") {
      playlistUrl = `https://www.youtube.com/playlist?list=${listParam}`;
    }

    const cleanTitle = rawTitle ? rawTitle.replace(/\(\d+\)\s*/, "").replace(/\s*-\s*YouTube$/, "").trim() : "";

    return {
      url: url,
      title: cleanTitle,
      isPlaylist: isPlaylist,
      isWatch: isWatch,
      isShorts: isShorts,
      videoUrl: videoUrl,
      playlistUrl: playlistUrl,
      playlistId: listParam,
    };
  } catch {
    return { url, title: rawTitle };
  }
}

function handleMediaDetected(data) {
  if (!data || !data.url) return;
  if (!data.url.includes("youtube.com") && !data.url.includes("youtu.be")) return;

  // Don't overwrite if user is actively typing in the input box
  if (document.activeElement === urlInput && urlInput.value && data.url === lastActiveTabUrl) {
    return;
  }

  const urlChanged = data.url !== lastActiveTabUrl;
  lastActiveTabUrl = data.url;

  currentMediaData = {
    ...currentMediaData,
    ...data,
  };

  const hasPlaylist = !!(data.playlistUrl || (data.url.includes("list=") && !data.url.includes("list=WL") && !data.url.includes("list=LL")));
  const hasVideo = !!(data.videoUrl || data.url.includes("/watch") || data.url.includes("/shorts/"));

  if (playlistSwitchGroup) {
    if (hasPlaylist && hasVideo && data.playlistUrl && data.videoUrl) {
      playlistSwitchGroup.classList.remove("hidden");
      if (urlChanged) {
        currentMediaData.mode = "playlist";
      }
      if (currentMediaData.mode === "single") {
        selectSingleMode();
      } else {
        selectPlaylistMode();
      }
    } else {
      playlistSwitchGroup.classList.add("hidden");
      urlInput.value = data.url;
      analyzeUrl(data.url);
    }
  } else {
    urlInput.value = data.url;
    analyzeUrl(data.url);
  }

  if (data.title) {
    const cleanTitle = data.title.replace(/\(\d+\)\s*/, "").replace(/\s*-\s*YouTube$/, "").trim();
    mediaTitle.textContent = cleanTitle;
    mediaPreview.classList.remove("hidden");
  }
}

function selectPlaylistMode() {
  currentMediaData.mode = "playlist";
  if (btnTargetPlaylist) btnTargetPlaylist.classList.add("active");
  if (btnTargetVideo) btnTargetVideo.classList.remove("active");
  if (currentMediaData.playlistUrl) {
    urlInput.value = currentMediaData.playlistUrl;
  } else if (currentMediaData.url) {
    urlInput.value = currentMediaData.url;
  }
  mediaTypeBadge.className = "badge badge-playlist";
  mediaTypeBadge.textContent = "📑 Playlist";
  probeTargetUrl(urlInput.value);
}

function selectSingleMode() {
  currentMediaData.mode = "single";
  if (btnTargetVideo) btnTargetVideo.classList.add("active");
  if (btnTargetPlaylist) btnTargetPlaylist.classList.remove("active");
  if (currentMediaData.videoUrl) {
    urlInput.value = currentMediaData.videoUrl;
  }
  mediaTypeBadge.className = "badge badge-video";
  mediaTypeBadge.textContent = "🎬 Single Video";
  probeTargetUrl(urlInput.value);
}

async function detectActiveTab() {
  if (!chrome?.tabs?.query) return;

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) return;

    if (tab.url.includes("youtube.com") || tab.url.includes("youtu.be")) {
      const parsed = parseYouTubeUrl(tab.url, tab.title);
      handleMediaDetected(parsed);

      if (tab.id) {
        chrome.tabs.sendMessage(tab.id, { type: "GET_CURRENT_MEDIA" }, (resp) => {
          if (chrome.runtime.lastError) return;
          if (resp && resp.url) {
            handleMediaDetected(resp);
          }
        });
      }
    }
  } catch (e) {
    console.debug("Could not inspect current tab:", e);
  }
}

function watchActiveTabChanges() {
  if (chrome?.tabs?.onUpdated) {
    chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
      if (tab && tab.active && (changeInfo.url || changeInfo.title || changeInfo.status === "complete")) {
        if (tab.url && (tab.url.includes("youtube.com") || tab.url.includes("youtu.be"))) {
          const parsed = parseYouTubeUrl(tab.url, tab.title);
          handleMediaDetected(parsed);
        }
      }
    });
  }

  if (chrome?.tabs?.onActivated) {
    chrome.tabs.onActivated.addListener(() => {
      detectActiveTab();
    });
  }

  // Active polling fallback for dynamic YouTube SPA changes
  setInterval(detectActiveTab, 1000);
}

let lastProbedUrl = "";
let probeAbortController = null;
let probeDebounceTimer = null;

function probeTargetUrl(url, debounceMs = 0) {
  if (probeDebounceTimer) {
    clearTimeout(probeDebounceTimer);
    probeDebounceTimer = null;
  }
  if (debounceMs > 0) {
    probeDebounceTimer = setTimeout(() => _executeProbe(url), debounceMs);
  } else {
    _executeProbe(url);
  }
}

async function _executeProbe(url) {
  if (!url || (!url.includes("youtube.com") && !url.includes("youtu.be"))) return;
  const clean = url.trim();
  if (clean === lastProbedUrl) return;
  lastProbedUrl = clean;

  if (probeAbortController) {
    try {
      probeAbortController.abort();
    } catch {}
  }
  probeAbortController = new AbortController();

  const prevVal = qualitySelect.value;
  let loadingOpt = qualitySelect.querySelector("option[data-loading='true']");
  if (!loadingOpt) {
    loadingOpt = document.createElement("option");
    loadingOpt.setAttribute("data-loading", "true");
    loadingOpt.textContent = "⏳ Detecting available qualities & sizes...";
    loadingOpt.disabled = true;
    qualitySelect.appendChild(loadingOpt);
  }

  try {
    const resp = await fetch(`${SERVER_BASE}/probe?url=${encodeURIComponent(clean)}`, {
      signal: probeAbortController.signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.status === "ok" && data.qualities && data.qualities.length > 0) {
      qualitySelect.innerHTML = "";
      let foundPrev = false;

      data.qualities.forEach((q) => {
        const opt = document.createElement("option");
        opt.value = q.id;
        const sizePart = q.size_str ? ` — ~${q.size_str}` : "";
        opt.textContent = `${q.label}${sizePart}`;
        qualitySelect.appendChild(opt);

        if (q.id === prevVal) {
          foundPrev = true;
        }
      });

      if (foundPrev) {
        qualitySelect.value = prevVal;
      } else if (currentSettings.defaultQuality && qualitySelect.querySelector(`option[value="${currentSettings.defaultQuality}"]`)) {
        qualitySelect.value = currentSettings.defaultQuality;
      } else {
        qualitySelect.value = "best";
      }

      if (data.title && mediaTitle) {
        const cleanTitle = data.title.replace(/\(\d+\)\s*/, "").replace(/\s*-\s*YouTube$/, "").trim();
        mediaTitle.textContent = cleanTitle;
        mediaPreview.classList.remove("hidden");
      }
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    const lOpt = qualitySelect.querySelector("option[data-loading='true']");
    if (lOpt) lOpt.remove();
  }
}

function analyzeUrl(url) {
  if (!url) {
    mediaTypeBadge.className = "badge badge-neutral";
    mediaTypeBadge.textContent = "Auto-Detect";
    mediaPreview.classList.add("hidden");
    return;
  }

  const isPlaylist = url.includes("list=") && !url.includes("list=WL") && !url.includes("list=LL");
  const isShorts = url.includes("/shorts/");

  if (isPlaylist) {
    mediaTypeBadge.className = "badge badge-playlist";
    mediaTypeBadge.textContent = "📑 Playlist";
  } else if (isShorts) {
    mediaTypeBadge.className = "badge badge-video";
    mediaTypeBadge.textContent = "📱 Shorts";
  } else {
    mediaTypeBadge.className = "badge badge-video";
    mediaTypeBadge.textContent = "🎬 Single Video";
  }

  probeTargetUrl(url, 250);
}

// -------------------------------------------------------------
// Download Handler
// -------------------------------------------------------------
async function handleDownloadClick() {
  const url = urlInput.value.trim();
  if (!url) {
    showToast("Please enter or paste a YouTube URL", 3000);
    urlInput.focus();
    return;
  }

  const isServerUp = await checkServerStatus();
  if (!isServerUp) {
    showToast("Please click Start Server to launch the companion server", 4000);
    return;
  }

  downloadBtn.disabled = true;
  downloadBtn.innerHTML = `<span>Queueing download...</span>`;

  try {
    const payload = {
      url: url,
      quality: qualitySelect.value,
      skip_existing: skipExistingCheck.checked,
      embed_thumbnail: embedThumbnailCheck.checked,
      output_dir: currentSettings.downloadDir || undefined,
    };

    const resp = await fetch(`${SERVER_BASE}/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();
    if (resp.ok && data.job_id) {
      showToast("Download added to queue! 🚀", 3000);
      startProgressPolling();
      await loadRecentTasks();
    } else {
      showToast(`Error: ${data.error || "Failed to start download"}`, 4000);
    }
  } catch (err) {
    showToast("Network error communicating with ytget server", 4000);
  } finally {
    resetDownloadBtn();
  }
}

function resetDownloadBtn() {
  downloadBtn.disabled = false;
  downloadBtn.innerHTML = `
    <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <span>Download with ytget</span>
  `;
}

// -------------------------------------------------------------
// Live Progress Polling (Multi-Job Support)
// -------------------------------------------------------------
function startProgressPolling() {
  if (pollInterval) clearInterval(pollInterval);
  loadRecentTasks();
  pollInterval = setInterval(loadRecentTasks, 750);
}

function stopProgressPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

function createJobCard(job) {
  const card = document.createElement("section");
  card.className = "card progress-card";
  card.setAttribute("data-job-id", job.job_id);

  const title = job.current_item_title || job.title || job.url || "Downloading...";
  const isPaused = job.status === "paused";
  const pct = Math.round(job.overall_progress || 0);

  let itemsText = "--";
  if (job.is_playlist && job.total_items > 1) {
    itemsText = `Item ${job.current_item_index || 1}/${job.total_items}`;
  } else if (job.item_downloaded_str) {
    itemsText = job.item_downloaded_str;
  }

  card.innerHTML = `
    <div class="card-header">
      <div class="progress-title-wrap">
        <span class="status-pulse ${isPaused ? "paused" : ""}"></span>
        <span class="active-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
      </div>
      <div class="progress-actions">
        <button class="progress-action-btn ${isPaused ? "btn-resume" : "btn-pause"}" type="button" data-action="toggle-pause" data-job-id="${job.job_id}" title="${isPaused ? "Resume download" : "Pause download"}">
          <span class="pause-icon">${isPaused ? "▶" : "⏸"}</span>
          <span class="pause-text">${isPaused ? "Resume" : "Pause"}</span>
        </button>
        <button class="progress-action-btn btn-cancel" type="button" data-action="cancel" data-job-id="${job.job_id}" title="Cancel this download">
          <span>✕</span>
          <span>Cancel</span>
        </button>
      </div>
    </div>

    <div class="progress-bar-container">
      <div class="progress-bar" style="width: ${pct}%; ${isPaused ? "filter: grayscale(70%);" : ""}"></div>
    </div>

    <div class="progress-stats">
      <span class="stat-pct">${pct}%</span>
      <span class="stat-phase" title="${escapeHtml(job.phase || "")}">${escapeHtml(job.phase || "Processing...")}</span>
    </div>

    <div class="progress-details">
      <span class="progress-speed">${escapeHtml(job.speed || "--/s")}</span>
      <span class="progress-eta">${escapeHtml(job.eta || "ETA --")}</span>
      <span class="progress-items">${escapeHtml(itemsText)}</span>
    </div>
  `;

  return card;
}

function updateJobCard(card, job) {
  const isPaused = job.status === "paused";
  const title = job.current_item_title || job.title || job.url || "Downloading...";
  const pct = Math.round(job.overall_progress || 0);

  const titleEl = card.querySelector(".active-title");
  if (titleEl && titleEl.textContent !== title) {
    titleEl.textContent = title;
    titleEl.title = title;
  }

  const pulse = card.querySelector(".status-pulse");
  if (pulse) {
    if (isPaused) pulse.classList.add("paused");
    else pulse.classList.remove("paused");
  }

  const pauseBtn = card.querySelector('[data-action="toggle-pause"]');
  if (pauseBtn) {
    const pauseIcon = pauseBtn.querySelector(".pause-icon");
    const pauseText = pauseBtn.querySelector(".pause-text");
    if (isPaused) {
      pauseBtn.className = "progress-action-btn btn-resume";
      pauseBtn.title = "Resume download";
      if (pauseIcon) pauseIcon.textContent = "▶";
      if (pauseText) pauseText.textContent = "Resume";
    } else {
      pauseBtn.className = "progress-action-btn btn-pause";
      pauseBtn.title = "Pause download";
      if (pauseIcon) pauseIcon.textContent = "⏸";
      if (pauseText) pauseText.textContent = "Pause";
    }
  }

  const bar = card.querySelector(".progress-bar");
  if (bar) {
    bar.style.width = `${pct}%`;
    bar.style.filter = isPaused ? "grayscale(70%)" : "none";
  }

  const pctEl = card.querySelector(".stat-pct");
  if (pctEl) pctEl.textContent = `${pct}%`;

  const phaseEl = card.querySelector(".stat-phase");
  if (phaseEl) {
    phaseEl.textContent = job.phase || "Processing...";
    phaseEl.title = job.phase || "";
  }

  const speedEl = card.querySelector(".progress-speed");
  if (speedEl) speedEl.textContent = job.speed || "--/s";

  const etaEl = card.querySelector(".progress-eta");
  if (etaEl) etaEl.textContent = job.eta || "ETA --";

  let itemsText = "--";
  if (job.is_playlist && job.total_items > 1) {
    itemsText = `Item ${job.current_item_index || 1}/${job.total_items}`;
  } else if (job.item_downloaded_str) {
    itemsText = job.item_downloaded_str;
  }
  const itemsEl = card.querySelector(".progress-items");
  if (itemsEl) itemsEl.textContent = itemsText;
}

function renderActiveJobs(activeJobs) {
  if (!progressCardsContainer) return;

  const currentJobIds = new Set(activeJobs.map((j) => j.job_id));

  // Remove cards that are no longer active
  const existingCards = progressCardsContainer.querySelectorAll(".progress-card");
  existingCards.forEach((card) => {
    const id = card.getAttribute("data-job-id");
    if (!currentJobIds.has(id)) {
      card.remove();
    }
  });

  // Create or update cards for each active job
  activeJobs.forEach((job) => {
    const existingCard = progressCardsContainer.querySelector(`[data-job-id="${job.job_id}"]`);
    if (existingCard) {
      updateJobCard(existingCard, job);
    } else {
      const newCard = createJobCard(job);
      progressCardsContainer.appendChild(newCard);
    }
  });
}

async function handleTogglePauseJob(jobId, isCurrentlyPaused) {
  if (!jobId) return;
  const endpoint = isCurrentlyPaused ? "resume" : "pause";

  // Optimistic UI update
  const card = progressCardsContainer?.querySelector(`[data-job-id="${jobId}"]`);
  if (card) {
    const btn = card.querySelector('[data-action="toggle-pause"]');
    const pulse = card.querySelector(".status-pulse");
    const bar = card.querySelector(".progress-bar");
    if (isCurrentlyPaused) {
      if (btn) {
        btn.className = "progress-action-btn btn-pause";
        btn.innerHTML = `<span class="pause-icon">⏸</span><span class="pause-text">Pause</span>`;
        btn.title = "Pause download";
      }
      if (pulse) pulse.classList.remove("paused");
      if (bar) bar.style.filter = "none";
    } else {
      if (btn) {
        btn.className = "progress-action-btn btn-resume";
        btn.innerHTML = `<span class="pause-icon">▶</span><span class="pause-text">Resume</span>`;
        btn.title = "Resume download";
      }
      if (pulse) pulse.classList.add("paused");
      if (bar) bar.style.filter = "grayscale(70%)";
    }
  }

  try {
    const resp = await fetch(`${SERVER_BASE}/${endpoint}/${jobId}`, { method: "POST" });
    if (resp.ok) {
      showToast(isCurrentlyPaused ? "▶ Download resumed" : "⏸ Download paused", 2000);
    } else {
      showToast(`Could not ${endpoint} download`, 3000);
    }
  } catch (err) {
    console.warn("Pause/resume error:", err);
  } finally {
    loadRecentTasks();
  }
}

async function handleCancelJob(jobId) {
  if (!jobId) return;

  // Instant optimistic dismissal (0ms UI latency)
  const card = progressCardsContainer?.querySelector(`[data-job-id="${jobId}"]`);
  if (card) {
    card.remove();
  }
  showToast("Download cancelled", 2000);

  try {
    const resp = await fetch(`${SERVER_BASE}/cancel/${jobId}`, { method: "POST" });
    if (!resp.ok) {
      console.warn("Cancel returned status:", resp.status);
    }
  } catch (err) {
    console.warn("Cancel failed:", err);
  } finally {
    loadRecentTasks();
  }
}

// -------------------------------------------------------------
// Recent Tasks & History
// -------------------------------------------------------------
async function loadRecentTasks() {
  try {
    const resp = await fetch(`${SERVER_BASE}/tasks?limit=25`);
    if (!resp.ok) return;

    const data = await resp.json();
    const jobs = data.jobs || [];

    const activeJobs = jobs.filter((j) =>
      ["downloading", "fetching", "queued", "paused"].includes(j.status)
    );

    activeJobsCount = activeJobs.length;
    renderActiveJobs(activeJobs);

    if (activeJobsCount > 0 && !pollInterval) {
      startProgressPolling();
    } else if (activeJobsCount === 0 && pollInterval) {
      stopProgressPolling();
    }

    // Check newly completed jobs for notifications
    jobs.forEach((j) => {
      if (j.status === "completed" && !previouslyCompletedJobIds.has(j.job_id)) {
        previouslyCompletedJobIds.add(j.job_id);
        if (currentSettings.notifyComplete) {
          showToast(`✅ Download complete: ${j.title || "Video"}`, 4000);
        }
      }
    });

    renderHistory(jobs);
  } catch {
    // Server offline
  }
}

function renderHistory(jobs) {
  if (!jobs || jobs.length === 0) {
    historyList.innerHTML = `<div class="empty-state">No recent downloads yet</div>`;
    return;
  }

  historyList.innerHTML = jobs
    .map((j) => {
      const title = j.title || j.url;
      const statusClass = (j.status || "").toLowerCase();
      const statusLabel = (j.status || "").toUpperCase();
      return `
        <div class="history-item">
          <span class="history-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
          <span class="history-badge ${statusClass}">${statusLabel}</span>
        </div>
      `;
    })
    .join("");
}

// -------------------------------------------------------------
// Utilities
// -------------------------------------------------------------
function showToast(text, duration = 3000) {
  toastMessage.textContent = text;
  toastMessage.classList.remove("hidden");
  setTimeout(() => {
    toastMessage.classList.add("hidden");
  }, duration);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

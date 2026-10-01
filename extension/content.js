/**
 * ytget Chrome Extension - YouTube Content Script
 * Injects a native-looking "⬇ ytget" download button directly on YouTube video and playlist pages.
 * Handles dropdown menus attached to body to prevent clipping by YouTube's overflow:hidden containers.
 * Communicates with the background service worker to prevent Mixed Content / CORS / PNA issues.
 */

(function () {
  "use strict";

  let currentDropdown = null;
  let currentTriggerBtn = null;
  let cleanupListeners = null;

  // -------------------------------------------------------------
  // Settings Helper
  // -------------------------------------------------------------
  function getSettings(callback) {
    if (chrome?.storage?.local) {
      chrome.storage.local.get("ytget_settings", (result) => {
        callback(result?.ytget_settings || {});
      });
    } else {
      callback({});
    }
  }

  // -------------------------------------------------------------
  // YouTube SPA Navigation & Injection Watcher
  // -------------------------------------------------------------
  function checkAndInject() {
    getSettings((settings) => {
      if (settings.showInPageBtn === false) {
        removeInjectedButtons();
        return;
      }

      const pathname = location.pathname;
      const isWatch = pathname.includes("/watch") || pathname.includes("/shorts/");
      const isPlaylist = pathname.includes("/playlist");

      if (isWatch) {
        injectWatchButton(settings);
      } else if (isPlaylist) {
        injectPlaylistButton(settings);
      } else {
        removeInjectedButtons();
      }
    });
  }

  function removeInjectedButtons() {
    const w = document.getElementById("ytget-watch-btn-wrap");
    if (w) w.remove();
    const p = document.getElementById("ytget-playlist-btn-wrap");
    if (p) p.remove();
    closeDropdown();
  }

  // -------------------------------------------------------------
  // Real-time Media Change Notification for Extension
  // -------------------------------------------------------------
  function extractPageMediaInfo() {
    const href = location.href;
    try {
      const urlObj = new URL(href);
      const pathname = urlObj.pathname;
      const searchParams = urlObj.searchParams;

      const isWatch = pathname.includes("/watch");
      const isShorts = pathname.includes("/shorts/");
      const isPlaylistPage = pathname.includes("/playlist");
      const listParam = searchParams.get("list");
      const isPlaylist = isPlaylistPage || (!!listParam && listParam !== "WL" && listParam !== "LL");

      let title = document.title ? document.title.replace(/\(\d+\)\s*/, "").replace(/\s*-\s*YouTube$/, "").trim() : "";
      if (isWatch) {
        const domTitle = document.querySelector("h1.ytd-watch-metadata yt-formatted-string, ytd-watch-metadata h1")?.textContent?.trim();
        if (domTitle) title = domTitle;
      } else if (isPlaylistPage) {
        const plTitle = document.querySelector("yt-dynamic-sizing-formatted-string.ytd-playlist-header-renderer, #header-container h1, ytd-playlist-header-renderer h1")?.textContent?.trim();
        if (plTitle) title = plTitle;
      }

      let videoUrl = null;
      let playlistUrl = null;

      if (isWatch && searchParams.get("v")) {
        videoUrl = `https://www.youtube.com/watch?v=${searchParams.get("v")}`;
      } else if (isShorts) {
        videoUrl = href;
      }

      if (listParam && listParam !== "WL" && listParam !== "LL") {
        playlistUrl = `https://www.youtube.com/playlist?list=${listParam}`;
      }

      return {
        url: href,
        title: title,
        isPlaylist: isPlaylist,
        isWatch: isWatch,
        isShorts: isShorts,
        videoUrl: videoUrl,
        playlistUrl: playlistUrl,
        playlistId: listParam,
      };
    } catch {
      return { url: href, title: document.title };
    }
  }

  function safeSendMessage(msg, callback) {
    try {
      if (!chrome?.runtime?.sendMessage) return;
      chrome.runtime.sendMessage(msg, (response) => {
        // Accessing chrome.runtime.lastError explicitly clears unchecked error status
        const err = chrome.runtime.lastError;
        if (typeof callback === "function") {
          callback(err ? null : response, err);
        }
      });
    } catch {}
  }

  function notifyMediaChanged() {
    try {
      const data = extractPageMediaInfo();
      safeSendMessage({
        type: "YOUTUBE_MEDIA_CHANGED",
        data: data,
      });
    } catch {}
  }

  window.addEventListener("yt-navigate-finish", () => {
    checkAndInject();
    notifyMediaChanged();
    setTimeout(notifyMediaChanged, 400);
  });
  window.addEventListener("spfdone", () => {
    checkAndInject();
    notifyMediaChanged();
  });
  window.addEventListener("popstate", () => {
    checkAndInject();
    notifyMediaChanged();
  });
  window.addEventListener("load", () => {
    checkAndInject();
    notifyMediaChanged();
  });

  // Notify extension when a video starts playing or resumes
  document.addEventListener(
    "play",
    (e) => {
      if (e.target && e.target.tagName === "VIDEO") {
        notifyMediaChanged();
      }
    },
    true
  );

  // Respond to query from popup or sidepanel
  if (chrome?.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
      if (req.type === "GET_CURRENT_MEDIA") {
        sendResponse(extractPageMediaInfo());
        return false;
      }
    });
  }

  // Instant response to extension settings changes without page refresh
  if (chrome?.storage?.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.ytget_settings) {
        removeInjectedButtons();
        checkAndInject();
      }
    });
  }

  // Periodic safeguard for YouTube dynamic re-renders
  setInterval(() => {
    checkAndInject();
  }, 1500);

  // -------------------------------------------------------------
  // Watch Page Injection (Video Action Bar)
  // -------------------------------------------------------------
  function injectWatchButton(settings) {
    if (document.getElementById("ytget-watch-btn-wrap")) return;

    const targetSelectors = [
      "#top-row #actions #top-level-buttons-computed",
      "#actions-inner #top-level-buttons-computed",
      "#top-level-buttons-computed",
      "#actions #flexible-item-buttons",
      "ytd-watch-metadata #actions",
    ];

    let container = null;
    for (const sel of targetSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        container = el;
        break;
      }
    }

    if (!container) return;

    const wrapper = document.createElement("div");
    wrapper.id = "ytget-watch-btn-wrap";
    wrapper.className = "ytget-btn-container";

    const button = document.createElement("button");
    button.id = "ytget-watch-btn";
    button.className = "ytget-action-btn";
    button.type = "button";
    button.title = "Download video with local ytget";
    button.innerHTML = `
      <svg class="ytget-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2">
        <circle cx="12" cy="12" r="9" fill="#dc2626" stroke="#dc2626"/>
        <path d="M12 7v7m0 0l-3-3m3 3l3-3m-6 5h6" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="ytget-btn-text">ytget</span>
      <span class="ytget-arrow">▾</span>
    `;

    button.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleDropdown(button, location.href, false, settings);
    });

    wrapper.appendChild(button);
    container.insertBefore(wrapper, container.firstChild);
    prefetchProbe(location.href);
  }

  // -------------------------------------------------------------
  // Playlist Page Injection
  // -------------------------------------------------------------
  function injectPlaylistButton(settings) {
    if (document.getElementById("ytget-playlist-btn-wrap")) return;

    const selectors = [
      "ytd-playlist-header-renderer #buttons",
      "ytd-playlist-header-renderer .action-actions",
      "#page-manager ytd-browse[page-subtype='playlist'] #buttons",
    ];

    let container = null;
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        container = el;
        break;
      }
    }

    if (!container) return;

    const wrapper = document.createElement("div");
    wrapper.id = "ytget-playlist-btn-wrap";
    wrapper.className = "ytget-btn-container";

    const button = document.createElement("button");
    button.id = "ytget-playlist-btn";
    button.className = "ytget-action-btn ytget-playlist-btn";
    button.type = "button";
    button.title = "Download playlist with local ytget";
    button.innerHTML = `
      <svg class="ytget-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" stroke="#dc2626" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="ytget-btn-text">Download Playlist</span>
      <span class="ytget-arrow">▾</span>
    `;

    button.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleDropdown(button, location.href, true, settings);
    });

    wrapper.appendChild(button);
    container.appendChild(wrapper);
  }

  // -------------------------------------------------------------
  // Video Format Probing & Cache
  // -------------------------------------------------------------
  const probeCache = new Map();

  function prefetchProbe(url) {
    if (!url || probeCache.has(url)) return;
    safeSendMessage({ type: "PROBE_URL", url }, (res) => {
      if (res && res.success && res.data) {
        probeCache.set(url, res.data);
      }
    });
  }

  // -------------------------------------------------------------
  // Body-Attached Dropdown Menu
  // -------------------------------------------------------------
  function buildDropdownHtml(qualities, isProbing, titlePrefix) {
    let itemsHtml = `<div class="ytget-dropdown-header">${titlePrefix}</div>`;

    if (isProbing) {
      itemsHtml += `
        <div class="ytget-probing-indicator">
          <span class="ytget-probing-spinner"></span>
          <span>Detecting available qualities & sizes...</span>
        </div>
      `;
    }

    for (const opt of qualities) {
      const sizeTag = opt.size_str ? `<span class="ytget-size-tag">~${opt.size_str}</span>` : "";
      itemsHtml += `
        <button class="ytget-dropdown-item" type="button" data-quality="${opt.id}">
          <div class="ytget-item-row">
            <span class="ytget-item-label">${opt.label}</span>
            ${sizeTag}
          </div>
          ${opt.desc ? `<div class="ytget-item-desc">${opt.desc}</div>` : ""}
        </button>
      `;
    }

    itemsHtml += `
      <div class="ytget-dropdown-divider"></div>
      <button class="ytget-dropdown-item ytget-dropdown-secondary" type="button" data-action="open-sidepanel">
        <div class="ytget-item-label">◨ Open ytget Side Panel</div>
        <div class="ytget-item-desc">Monitor queue & configure settings</div>
      </button>
    `;

    return itemsHtml;
  }

  function attachDropdownListeners(dropdown, buttonEl, url) {
    dropdown.querySelectorAll(".ytget-dropdown-item").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();

        const action = btn.getAttribute("data-action");
        if (action === "open-sidepanel") {
          closeDropdown();
          safeSendMessage({ type: "OPEN_SIDE_PANEL" });
          return;
        }

        const quality = btn.getAttribute("data-quality");
        closeDropdown();
        triggerDownload(url, quality, buttonEl);
      });
    });
  }

  function toggleDropdown(buttonEl, url, isPlaylist, settings) {
    // If clicking same open button, toggle off
    if (currentDropdown && currentTriggerBtn === buttonEl) {
      closeDropdown();
      return;
    }

    closeDropdown();

    const rect = buttonEl.getBoundingClientRect();
    const dropdown = document.createElement("div");
    dropdown.className = "ytget-dropdown-menu";
    dropdown.id = "ytget-active-dropdown";

    // Position fixed directly below button, clamped inside window
    const menuWidth = 280;
    const leftPos = Math.max(12, Math.min(rect.left, window.innerWidth - menuWidth - 12));
    dropdown.style.left = `${leftPos}px`;
    dropdown.style.width = `${menuWidth}px`;

    // Check if dropdown fits below button; if not, place above
    const estimatedHeight = 280;
    if (rect.bottom + estimatedHeight > window.innerHeight && rect.top > estimatedHeight) {
      dropdown.style.top = `${rect.top - estimatedHeight - 8}px`;
    } else {
      dropdown.style.top = `${rect.bottom + 8}px`;
    }

    const titlePrefix = isPlaylist ? "Download Playlist:" : "Download Video:";
    const cachedData = probeCache.get(url);

    if (cachedData && cachedData.qualities && cachedData.qualities.length > 0) {
      dropdown.innerHTML = buildDropdownHtml(cachedData.qualities, false, titlePrefix);
      attachDropdownListeners(dropdown, buttonEl, url);
    } else {
      // Fallback while probing
      const defQuality = settings.defaultQuality || "best";
      const initialOptions = [
        { id: defQuality, label: `⚡ Quick Download (${defQuality.toUpperCase()})`, desc: "Using your saved default quality", size_str: "" },
        { id: "best", label: "✨ Best Available", desc: "Highest resolution available", size_str: "" },
        { id: "1080", label: "🎬 1080p Full HD", desc: "Standard 1080p MP4", size_str: "" },
        { id: "720", label: "📺 720p HD", desc: "Fast download & smaller file", size_str: "" },
        { id: "audio", label: "🎵 Audio Only (MP3)", desc: "High-quality MP3 with cover art", size_str: "" },
      ];
      dropdown.innerHTML = buildDropdownHtml(initialOptions, true, titlePrefix);
      attachDropdownListeners(dropdown, buttonEl, url);

      // Async fetch probe
      safeSendMessage({ type: "PROBE_URL", url }, (res) => {
        if (res && res.success && res.data && res.data.qualities && res.data.qualities.length > 0) {
          probeCache.set(url, res.data);
          if (currentDropdown === dropdown) {
            dropdown.innerHTML = buildDropdownHtml(res.data.qualities, false, titlePrefix);
            attachDropdownListeners(dropdown, buttonEl, url);
          }
        } else if (currentDropdown === dropdown) {
          const indicator = dropdown.querySelector(".ytget-probing-indicator");
          if (indicator) indicator.remove();
        }
      });
    }

    document.body.appendChild(dropdown);
    currentDropdown = dropdown;
    currentTriggerBtn = buttonEl;

    const arrow = buttonEl.querySelector(".ytget-arrow");
    if (arrow) arrow.classList.add("ytget-arrow-up");

    // Outside click & dismiss listener with small delay so the triggering click doesn't close it
    setTimeout(() => {
      function onOutsideClick(e) {
        if (currentDropdown && !currentDropdown.contains(e.target) && !buttonEl.contains(e.target)) {
          closeDropdown();
        }
      }

      function onKeyDown(e) {
        if (e.key === "Escape") closeDropdown();
      }

      function onScrollOrResize() {
        closeDropdown();
      }

      document.addEventListener("click", onOutsideClick);
      document.addEventListener("keydown", onKeyDown);
      window.addEventListener("scroll", onScrollOrResize, { passive: true });
      window.addEventListener("resize", onScrollOrResize, { passive: true });

      cleanupListeners = () => {
        document.removeEventListener("click", onOutsideClick);
        document.removeEventListener("keydown", onKeyDown);
        window.removeEventListener("scroll", onScrollOrResize);
        window.removeEventListener("resize", onScrollOrResize);
      };
    }, 40);
  }

  function closeDropdown() {
    if (currentDropdown) {
      currentDropdown.remove();
      currentDropdown = null;
    }
    if (currentTriggerBtn) {
      const arrow = currentTriggerBtn.querySelector(".ytget-arrow");
      if (arrow) arrow.classList.remove("ytget-arrow-up");
      currentTriggerBtn = null;
    }
    if (cleanupListeners) {
      cleanupListeners();
      cleanupListeners = null;
    }
  }

  // -------------------------------------------------------------
  // Send Download Request via Background Service Worker
  // -------------------------------------------------------------
  function triggerDownload(url, quality, buttonEl) {
    const textSpan = buttonEl?.querySelector(".ytget-btn-text");
    const originalText = textSpan ? textSpan.textContent : "ytget";

    if (buttonEl) {
      buttonEl.classList.add("ytget-btn-busy");
      if (textSpan) textSpan.textContent = "Connecting...";
    }

    showToast("Connecting to local ytget server...", "info", 2500);

    chrome.runtime.sendMessage(
      {
        type: "START_DOWNLOAD",
        url: url,
        quality: quality,
      },
      (response) => {
        if (buttonEl) buttonEl.classList.remove("ytget-btn-busy");

        if (chrome.runtime.lastError) {
          if (textSpan) textSpan.textContent = originalText;
          showToast(
            `❌ Extension communication error: ${chrome.runtime.lastError.message}`,
            "error",
            5000
          );
          return;
        }

        if (response && response.success) {
          const qualityLabel = quality === "audio" ? "MP3 Audio" : `${quality.toUpperCase()}`;
          showToast(`🚀 ytget: Download started (${qualityLabel})!`, "success", 5000);
          if (textSpan) {
            textSpan.textContent = "✓ Queued";
            setTimeout(() => {
              textSpan.textContent = originalText;
            }, 3000);
          }
        } else {
          if (textSpan) textSpan.textContent = originalText;
          const errMsg = response?.error || "Companion server rejected request";
          showToast(`❌ ${errMsg}`, "error", 6000);
        }
      }
    );
  }

  // -------------------------------------------------------------
  // Floating Toast Notification
  // -------------------------------------------------------------
  function showToast(text, type = "info", duration = 4000) {
    let toast = document.getElementById("ytget-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "ytget-toast";
      document.body.appendChild(toast);
    }

    toast.className = `ytget-toast ytget-toast-${type} ytget-toast-visible`;
    toast.textContent = text;

    if (toast._timer) clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      toast.classList.remove("ytget-toast-visible");
    }, duration);
  }
})();

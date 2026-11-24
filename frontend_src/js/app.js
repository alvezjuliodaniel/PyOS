/**
 * MicroOS - Bootstrap del Escritorio SPA
 * Conecta MicroOSClient (WebSocket JSON-RPC) con WindowManager y la barra de tareas.
 */
(function (global) {
  "use strict";

  const client = new global.MicroOSClient("/ws");
  const wm = new global.WindowManager({
    desktopEl: document.getElementById("desktop"),
    layerEl: document.getElementById("windows-layer"),
    taskbarEl: document.getElementById("taskbar-windows"),
    dockEl: document.getElementById("dock-apps"),
    iconsEl: document.getElementById("desktop-icons"),
  });

  const trayBoard = document.getElementById("tray-board");
  const trayRam = document.getElementById("tray-ram");
  const trayWs = document.getElementById("tray-ws");
  const trayWsText = document.getElementById("tray-ws-text");
  const trayClock = document.getElementById("tray-clock");
  const toastBox = document.getElementById("toast-container");

  function toast(msg, type = "info", durationMs = 3200) {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = msg;
    toastBox.appendChild(el);
    setTimeout(() => el.remove(), durationMs);
  }

  function updateClock() {
    const d = new Date();
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    trayClock.textContent = `${hh}:${mm}`;
  }
  updateClock();
  setInterval(updateClock, 10000);

  function escHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Namespace global compartido con las apps de Fase 5
  const MicroOS = {
    client,
    wm,
    toast,
    escHtml,
    boardInfo: null,
    lastStats: null,
  };
  global.MicroOS = MicroOS;

  // Cargador dinamico de aplicaciones instaladas en /apps/<id>/
  const dynamicAppIds = new Set();

  function loadAppScript(appId, uiUrl) {
    return new Promise((resolve) => {
      const prev = document.querySelector(`script[data-dyn-app="${appId}"]`);
      if (prev) prev.remove();
      const s = document.createElement("script");
      s.dataset.dynApp = appId;
      s.src = `${uiUrl}?t=${Date.now()}`;
      s.onload = () => resolve(true);
      s.onerror = () => resolve(false);
      document.body.appendChild(s);
    });
  }

  async function syncDynamicApps(forceReloadId = null) {
    try {
      const list = await client.call("apps", "list");
      const currentIds = new Set(list.map((a) => a.id));

      // Desregistrar apps desinstaladas
      for (const id of Array.from(dynamicAppIds)) {
        if (!currentIds.has(id)) {
          wm.unregisterApp(id);
          const tag = document.querySelector(`script[data-dyn-app="${id}"]`);
          if (tag) tag.remove();
          dynamicAppIds.delete(id);
        }
      }

      // Cargar nuevas o recargadas
      for (const app of list) {
        if (!app.has_ui || !app.ui_url) continue;
        if (!dynamicAppIds.has(app.id) || forceReloadId === app.id) {
          if (forceReloadId === app.id) wm.unregisterApp(app.id);
          await loadAppScript(app.id, app.ui_url);
          dynamicAppIds.add(app.id);
        }
      }
    } catch (err) {
      console.warn("syncDynamicApps:", err);
    }
  }

  MicroOS.syncDynamicApps = syncDynamicApps;

  // Eventos de conexion WebSocket
  client.on("ws_open", async () => {
    trayWs.classList.remove("ws-off");
    trayWs.classList.add("ws-on");
    trayWsText.textContent = "Conectado";
    try {
      const info = await client.call("sys", "info");
      MicroOS.boardInfo = info;
      trayBoard.textContent = `${info.name}${info.psram ? " +PSRAM" : ""}`;
      trayBoard.title = `${info.machine} | MicroPython ${info.mpy_version}`;
      const startBoard = document.getElementById("start-board-lbl");
      if (startBoard) startBoard.textContent = `${info.name}${info.psram ? " +PSRAM" : ""}`;
      document.title = `MicroOS — ${info.name}`;
    } catch (err) {
      console.warn("No se pudo obtener sys.info:", err);
    }
    syncDynamicApps();
  });

  client.on("apps_changed", (ev) => {
    const reloadId = ev && (ev.action === "install" || ev.action === "reload") ? ev.id : null;
    syncDynamicApps(reloadId);
  });

  client.on("ws_close", () => {
    trayWs.classList.remove("ws-on");
    trayWs.classList.add("ws-off");
    trayWsText.textContent = "Reconectando...";
  });

  // Eventos push del Kernel
  client.on("sys_stats", (stats) => {
    MicroOS.lastStats = stats;
    if (stats && stats.mem) {
      const freeKb = Math.round(stats.mem.free / 1024);
      trayRam.textContent = `RAM ${freeKb} KB`;
      trayRam.classList.toggle("ram-warn", Boolean(stats.mem.warn && !stats.mem.critical));
      trayRam.classList.toggle("ram-crit", Boolean(stats.mem.critical));
    }
  });

  client.on("mem_alert", (ev) => {
    if (!ev) return;
    const kb = ev.mem ? Math.round(ev.mem.free / 1024) : "?";
    toast(
      ev.level === "critical"
        ? `RAM critica en placa: ${kb} KB libres`
        : `RAM baja en placa: ${kb} KB libres`,
      ev.level === "critical" ? "err" : "warn",
      4500
    );
  });

  // Ventana nativa "Acerca de MicroOS" (boton MicroOS en barra de tareas)
  wm.registerApp({
    id: "about",
    title: "Acerca de MicroOS",
    icon: "⚡",
    width: 420,
    height: 300,
    hidden: true,
    mount(container) {
      const render = (info, stats) => {
        container.innerHTML = `
          <div class="card" style="display:flex;flex-direction:column;gap:8px;">
            <div style="font-size:15px;font-weight:700;">MicroOS ${info ? info.os_version : ""}</div>
            <div class="muted">Minisistema operativo web en MicroPython</div>
            <div class="mono" style="font-size:12px;line-height:1.6;">
              Placa: ${info ? info.name : "---"} (${info ? info.arch : "--"})<br>
              Nucleos: ${info ? info.cores : "-"} | PSRAM: ${info ? (info.psram ? "SI" : "NO") : "-"}<br>
              MicroPython: ${info ? info.mpy_version : "---"}<br>
              Hostname: ${info ? info.hostname + ".local" : "---"}<br>
              RAM libre: ${stats && stats.mem ? Math.round(stats.mem.free / 1024) + " KB" : "---"}<br>
              Uptime: ${stats ? stats.uptime_s + " s" : "---"}
            </div>
            <div style="display:flex;gap:8px;margin-top:4px;">
              <button class="btn primary" id="about-gc">Ejecutar GC</button>
              <button class="btn" id="about-refresh">Actualizar</button>
            </div>
          </div>
        `;
        const gcBtn = container.querySelector("#about-gc");
        const refBtn = container.querySelector("#about-refresh");
        if (gcBtn) {
          gcBtn.onclick = async () => {
            try {
              const m = await client.call("sys", "gc");
              toast(`GC OK: ${Math.round(m.free / 1024)} KB libres`, "ok");
              const st = await client.call("sys", "stats");
              render(MicroOS.boardInfo, st);
            } catch (e) {
              toast(e.message, "err");
            }
          };
        }
        if (refBtn) {
          refBtn.onclick = async () => {
            try {
              const [inf, st] = await Promise.all([
                client.call("sys", "info"),
                client.call("sys", "stats"),
              ]);
              MicroOS.boardInfo = inf;
              MicroOS.lastStats = st;
              render(inf, st);
            } catch (e) {
              toast(e.message, "err");
            }
          };
        }
      };

      render(MicroOS.boardInfo, MicroOS.lastStats);
      if (client.connected) {
        client.call("sys", "stats").then((st) => render(MicroOS.boardInfo, st)).catch(() => {});
      }
    },
  });

  // ------------------------------------------------------------------
  // Tema / Personalizacion persistente (Panel de Control)
  // ------------------------------------------------------------------
  const WALLPAPERS = {
    midnight: "radial-gradient(circle at 20% 20%, #1e293b 0%, #0f172a 55%, #020617 100%)",
    win7: "radial-gradient(circle at 30% 25%, #0284c7 0%, #0c4a6e 48%, #082f49 100%)",
    emerald: "radial-gradient(circle at 25% 20%, #065f46 0%, #064e3b 50%, #022c22 100%)",
    aurora: "radial-gradient(circle at 20% 25%, #4c1d95 0%, #1e1b4b 52%, #09090b 100%)",
  };
  const ACCENTS = {
    sky: "#38bdf8",
    emerald: "#22c55e",
    violet: "#a78bfa",
    amber: "#f59e0b",
  };

  function applyAppearance(wpKey, accKey) {
    const r = document.documentElement.style;
    if (wpKey && WALLPAPERS[wpKey]) r.setProperty("--bg-desktop", WALLPAPERS[wpKey]);
    if (accKey && ACCENTS[accKey]) {
      r.setProperty("--accent", ACCENTS[accKey]);
      r.setProperty("--border-focus", ACCENTS[accKey]);
    }
  }
  MicroOS.applyAppearance = applyAppearance;
  MicroOS.WALLPAPERS = WALLPAPERS;
  MicroOS.ACCENTS = ACCENTS;
  try {
    applyAppearance(localStorage.getItem("microos_wp"), localStorage.getItem("microos_acc"));
  } catch {}

  // ------------------------------------------------------------------
  // Menu Inicio clasico estilo Windows 7 (dinamico)
  // ------------------------------------------------------------------
  const startMenu = document.getElementById("start-menu");
  const startAppsEl = document.getElementById("start-apps");
  const startSearchEl = document.getElementById("start-search");
  const btnStart = document.getElementById("btn-start");

  function closeStartMenu() {
    startMenu.classList.add("hidden");
  }

  function renderStartApps(filter = "") {
    const q = filter.trim().toLowerCase();
    startAppsEl.innerHTML = "";
    const list = Array.from(wm.apps.values()).filter((a) => !a.hidden);
    const filtered = q ? list.filter((a) => a.title.toLowerCase().includes(q) || a.id.includes(q)) : list;

    if (!filtered.length) {
      startAppsEl.innerHTML = '<div class="muted" style="padding:10px;font-size:12px;">Sin resultados.</div>';
      return;
    }

    for (const app of filtered) {
      const b = document.createElement("button");
      b.className = "w7-start-app";
      b.innerHTML = `<span class="w7-start-app-ico">${escHtml(app.icon)}</span><span>${escHtml(app.title)}</span>`;
      b.onclick = () => {
        closeStartMenu();
        wm.open(app.id);
      };
      startAppsEl.appendChild(b);
    }
  }

  wm.onAppsChange(() => renderStartApps(startSearchEl.value));

  btnStart.addEventListener("click", (e) => {
    e.stopPropagation();
    const opening = startMenu.classList.toggle("hidden");
    if (!opening) {
      startSearchEl.value = "";
      renderStartApps("");
      startSearchEl.focus();
    }
  });

  startMenu.addEventListener("click", (e) => e.stopPropagation());
  startSearchEl.addEventListener("input", () => renderStartApps(startSearchEl.value));
  startSearchEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const first = startAppsEl.querySelector(".w7-start-app");
      if (first) first.click();
    } else if (e.key === "Escape") {
      closeStartMenu();
    }
  });

  startMenu.querySelectorAll("[data-open]").forEach((btn) => {
    btn.addEventListener("click", () => {
      closeStartMenu();
      wm.open(btn.dataset.open);
    });
  });

  document.getElementById("start-gc").addEventListener("click", async () => {
    closeStartMenu();
    try {
      const m = await client.call("sys", "gc");
      toast(`GC OK: ${Math.round(m.free / 1024)} KB libres`, "ok");
    } catch (e) { toast(e.message, "err"); }
  });

  document.getElementById("start-reboot").addEventListener("click", async () => {
    closeStartMenu();
    if (!confirm("¿Reiniciar la placa ESP32 ahora?")) return;
    try {
      await client.call("sys", "reboot");
      toast("Reiniciando ESP32...", "warn", 4000);
    } catch (e) { toast(e.message, "err"); }
  });

  window.addEventListener("click", closeStartMenu);
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeStartMenu(); });

  // Registrar aplicaciones nativas si fueron cargadas (Fase 5)
  if (Array.isArray(global.MicroOSApps)) {
    for (const appDef of global.MicroOSApps) {
      wm.registerApp(appDef);
    }
  }
  renderStartApps("");

  document.getElementById("start-logout").addEventListener("click", async () => {
    closeStartMenu();
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {}
    client.close();
    showAuthOverlay(false);
  });

  // ------------------------------------------------------------------
  // Flujo de Autenticacion / Primer Arranque (SEC-01)
  // ------------------------------------------------------------------
  const authOverlay = document.getElementById("auth-overlay");
  const authForm = document.getElementById("auth-form");
  const authTitle = document.getElementById("auth-title");
  const authSub = document.getElementById("auth-sub");
  const authUser = document.getElementById("auth-user");
  const authPass = document.getElementById("auth-pass");
  const authPass2 = document.getElementById("auth-pass2");
  const authErr = document.getElementById("auth-err");
  const authBtn = document.getElementById("auth-btn");
  let isSetupMode = false;

  function showAuthOverlay(setupMode) {
    isSetupMode = Boolean(setupMode);
    authErr.textContent = "";
    authPass.value = "";
    authPass2.value = "";
    if (isSetupMode) {
      authTitle.textContent = "Configurar MicroOS";
      authSub.textContent = "Crea la contrasena de administrador inicial (min. 4 chars)";
      authPass2.classList.remove("hidden");
      authBtn.textContent = "Crear y Entrar";
    } else {
      authTitle.textContent = "Iniciar sesion en MicroOS";
      authSub.textContent = "Introduce tus credenciales de administrador";
      authPass2.classList.add("hidden");
      authBtn.textContent = "Entrar";
    }
    authOverlay.classList.remove("hidden");
    authPass.focus();
  }

  authForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    authErr.textContent = "";
    const username = authUser.value.trim() || "admin";
    const password = authPass.value;
    if (isSetupMode) {
      if (password.length < 4) { authErr.textContent = "Minimo 4 caracteres"; return; }
      if (password !== authPass2.value) { authErr.textContent = "Las contrasenas no coinciden"; return; }
    }
    authBtn.disabled = true;
    try {
      const url = isSetupMode ? "/api/auth/setup" : "/api/auth/login";
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const data = await r.json();
      if (!r.ok || !data.ok) {
        authErr.textContent = data.error || "Error de autenticacion";
        return;
      }
      authOverlay.classList.add("hidden");
      client.connect();
    } catch (err) {
      authErr.textContent = "No se pudo conectar con la placa";
    } finally {
      authBtn.disabled = false;
    }
  });

  async function initAuthAndConnect() {
    try {
      const r = await fetch("/api/auth/status");
      const st = await r.json();
      if (st.authenticated) {
        authOverlay.classList.add("hidden");
        client.connect();
      } else {
        showAuthOverlay(st.needs_setup);
      }
    } catch {
      client.connect();
    }
  }

  initAuthAndConnect();
})(window);

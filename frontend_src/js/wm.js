/**
 * PyOS - Gestor de Ventanas (Window Manager) en Vanilla JS
 * Soporta arrastrar, redimensionar, minimizar, maximizar, cerrar y z-index dinamico.
 */
(function (global) {
  "use strict";

  class WindowManager {
    constructor({ desktopEl, layerEl, taskbarEl, dockEl, iconsEl }) {
      this.desktopEl = desktopEl;
      this.layerEl = layerEl;
      this.taskbarEl = taskbarEl;
      this.dockEl = dockEl;
      this.iconsEl = iconsEl;

      this.apps = new Map();
      this.windows = new Map(); // id -> state
      this._zTop = 10;
      this._cascade = 0;
      this._focusedId = null;
      this._appsListeners = [];
    }

    onAppsChange(fn) {
      this._appsListeners.push(fn);
    }

    _notifyAppsChange() {
      for (const fn of this._appsListeners) {
        try { fn(this.apps); } catch (e) {}
      }
    }

    _loadGeom(appId) {
      try {
        const raw = localStorage.getItem(`pyos_win_${appId}`);
        return raw ? JSON.parse(raw) : null;
      } catch {
        return null;
      }
    }

    _saveGeom(state) {
      if (!state || !state.appId) return;
      try {
        const prev = this._loadGeom(state.appId) || {};
        const geom = state.maximized
          ? { ...prev, maximized: true }
          : {
              left: state.el.offsetLeft,
              top: state.el.offsetTop,
              width: state.el.offsetWidth,
              height: state.el.offsetHeight,
              maximized: false,
            };
        localStorage.setItem(`pyos_win_${state.appId}`, JSON.stringify(geom));
      } catch {}
    }

    resetWindowPositions() {
      try {
        for (const k of Object.keys(localStorage)) {
          if (k.startsWith("pyos_win_")) localStorage.removeItem(k);
        }
      } catch {}
    }

    registerApp(appDef) {
      this.apps.set(appDef.id, appDef);
      this._renderLaunchers();
      this._notifyAppsChange();
    }

    unregisterApp(appId) {
      for (const [winId, st] of Array.from(this.windows.entries())) {
        if (st.appId === appId) this.close(winId);
      }
      if (this.apps.delete(appId)) {
        this._renderLaunchers();
        this._notifyAppsChange();
      }
    }

    _renderLaunchers() {
      this.dockEl.innerHTML = "";
      this.iconsEl.innerHTML = "";

      for (const app of this.apps.values()) {
        if (app.hidden) continue;

        // Boton en el dock inferior
        const dockBtn = document.createElement("button");
        dockBtn.className = "dock-btn";
        dockBtn.title = app.title;
        dockBtn.textContent = app.icon;
        dockBtn.addEventListener("click", () => this.open(app.id));
        this.dockEl.appendChild(dockBtn);

        // Acceso directo en el escritorio
        const deskBtn = document.createElement("button");
        deskBtn.className = "desk-icon";
        const gSpan = document.createElement("span");
        gSpan.className = "desk-icon-glyph";
        gSpan.textContent = app.icon;
        const lSpan = document.createElement("span");
        lSpan.className = "desk-icon-label";
        lSpan.textContent = app.title;
        deskBtn.appendChild(gSpan);
        deskBtn.appendChild(lSpan);
        deskBtn.addEventListener("dblclick", () => this.open(app.id));
        deskBtn.addEventListener("click", () => {
          // En tactil / clic rapido opcional: doble clic o clic directo en moviles
          if (window.matchMedia("(pointer: coarse)").matches) this.open(app.id);
        });
        this.iconsEl.appendChild(deskBtn);
      }
    }

    open(appId) {
      const app = this.apps.get(appId);
      if (!app) return null;

      // Si es instancia unica (por defecto) y ya esta abierta, enfocarla/restaurarla
      if (app.singleton !== false && this.windows.has(appId)) {
        const existing = this.windows.get(appId);
        if (existing.minimized) {
          existing.minimized = false;
          existing.el.classList.remove("minimized");
        }
        this.focus(appId);
        return existing;
      }

      const winId = app.singleton === false ? `${appId}-${Date.now()}` : appId;
      const saved = this._loadGeom(appId);
      const maxW = Math.max(300, this.desktopEl.clientWidth - 20);
      const maxH = Math.max(200, this.desktopEl.clientHeight - 20);
      const w = Math.max(300, Math.min((saved && saved.width) || app.width || 560, maxW));
      const h = Math.max(200, Math.min((saved && saved.height) || app.height || 380, maxH));
      const offset = (this._cascade++ % 6) * 26;
      const defLeft = Math.max(12, Math.min(120 + offset, this.desktopEl.clientWidth - w - 12));
      const defTop = Math.max(12, Math.min(36 + offset, this.desktopEl.clientHeight - h - 12));
      const left = saved && typeof saved.left === "number"
        ? Math.max(0, Math.min(saved.left, this.desktopEl.clientWidth - 80))
        : defLeft;
      const top = saved && typeof saved.top === "number"
        ? Math.max(0, Math.min(saved.top, this.desktopEl.clientHeight - 40))
        : defTop;

      const winEl = document.createElement("section");
      winEl.className = "win";
      winEl.dataset.winId = winId;
      winEl.style.width = `${w}px`;
      winEl.style.height = `${h}px`;
      winEl.style.left = `${left}px`;
      winEl.style.top = `${top}px`;
      if (saved && saved.maximized) winEl.classList.add("maximized");

      winEl.innerHTML = `
        <header class="win-header">
          <div class="win-title">
            <span>${app.icon}</span>
            <span class="win-title-text"></span>
          </div>
          <div class="win-controls">
            <button class="win-btn min" title="Minimizar">&#8211;</button>
            <button class="win-btn max" title="Maximizar">&#9723;</button>
            <button class="win-btn close" title="Cerrar">&times;</button>
          </div>
        </header>
        <div class="win-body"></div>
        <div class="win-resize" title="Redimensionar"></div>
      `;

      winEl.querySelector(".win-title-text").textContent = app.title;
      const headerEl = winEl.querySelector(".win-header");
      const bodyEl = winEl.querySelector(".win-body");
      const resizeEl = winEl.querySelector(".win-resize");

      // Pill en barra de tareas
      const pillEl = document.createElement("button");
      pillEl.className = "tb-win-pill";
      pillEl.textContent = `${app.icon} ${app.title}`;
      pillEl.addEventListener("click", () => {
        const st = this.windows.get(winId);
        if (!st) return;
        if (st.minimized) {
          st.minimized = false;
          st.el.classList.remove("minimized");
          this.focus(winId);
        } else if (this._focusedId === winId) {
          this.minimize(winId);
        } else {
          this.focus(winId);
        }
      });

      const state = {
        id: winId,
        appId,
        el: winEl,
        bodyEl,
        pillEl,
        minimized: false,
        maximized: Boolean(saved && saved.maximized),
        cleanup: null,
      };

      // Controles de ventana
      winEl.addEventListener("pointerdown", () => this.focus(winId));
      winEl.querySelector(".min").addEventListener("click", (e) => {
        e.stopPropagation();
        this.minimize(winId);
      });
      winEl.querySelector(".max").addEventListener("click", (e) => {
        e.stopPropagation();
        this.toggleMaximize(winId);
      });
      winEl.querySelector(".close").addEventListener("click", (e) => {
        e.stopPropagation();
        this.close(winId);
      });
      headerEl.addEventListener("dblclick", () => this.toggleMaximize(winId));

      this._attachDrag(headerEl, state);
      this._attachResize(resizeEl, state);

      this.windows.set(winId, state);
      this.layerEl.appendChild(winEl);
      this.taskbarEl.appendChild(pillEl);
      this.focus(winId);

      // Montar contenido de la aplicacion
      if (typeof app.mount === "function") {
        try {
          const cleanup = app.mount(bodyEl, {
            winId,
            wm: this,
            setTitle: (t) => {
              winEl.querySelector(".win-title-text").textContent = t;
              pillEl.textContent = `${app.icon} ${t}`;
            },
          });
          if (typeof cleanup === "function") state.cleanup = cleanup;
        } catch (err) {
          bodyEl.innerHTML = `<div class="card mono">Error al montar app: ${err.message}</div>`;
        }
      }

      return state;
    }

    focus(winId) {
      const target = this.windows.get(winId);
      if (!target) return;
      this._zTop += 1;
      target.el.style.zIndex = String(this._zTop);
      this._focusedId = winId;

      for (const [id, st] of this.windows) {
        const isActive = id === winId && !st.minimized;
        st.el.classList.toggle("focused", isActive);
        st.pillEl.classList.toggle("active", isActive);
      }
    }

    minimize(winId) {
      const st = this.windows.get(winId);
      if (!st) return;
      st.minimized = true;
      st.el.classList.add("minimized");
      st.el.classList.remove("focused");
      st.pillEl.classList.remove("active");
      if (this._focusedId === winId) this._focusedId = null;
    }

    toggleMaximize(winId) {
      const st = this.windows.get(winId);
      if (!st) return;
      st.maximized = !st.maximized;
      st.el.classList.toggle("maximized", st.maximized);
      this._saveGeom(st);
      this.focus(winId);
    }

    close(winId) {
      const st = this.windows.get(winId);
      if (!st) return;
      this._saveGeom(st);
      if (typeof st.cleanup === "function") {
        try {
          st.cleanup();
        } catch (err) {
          console.error("[WM] Cleanup error:", err);
        }
      }
      st.el.remove();
      st.pillEl.remove();
      this.windows.delete(winId);
      if (this._focusedId === winId) this._focusedId = null;
    }

    _attachDrag(headerEl, state) {
      let dragging = false;
      let startX = 0, startY = 0, origLeft = 0, origTop = 0;

      headerEl.addEventListener("pointerdown", (e) => {
        if (e.target.closest(".win-controls") || state.maximized) return;
        dragging = true;
        startX = e.clientX;
        startY = e.clientY;
        origLeft = state.el.offsetLeft;
        origTop = state.el.offsetTop;
        headerEl.setPointerCapture(e.pointerId);
      });

      headerEl.addEventListener("pointermove", (e) => {
        if (!dragging) return;
        const maxX = Math.max(0, this.desktopEl.clientWidth - 80);
        const maxY = Math.max(0, this.desktopEl.clientHeight - 36);
        const nx = Math.max(-state.el.offsetWidth + 80, Math.min(origLeft + (e.clientX - startX), maxX));
        const ny = Math.max(0, Math.min(origTop + (e.clientY - startY), maxY));
        state.el.style.left = `${nx}px`;
        state.el.style.top = `${ny}px`;
      });

      const stop = () => {
        if (dragging) {
          dragging = false;
          this._saveGeom(state);
        }
      };
      headerEl.addEventListener("pointerup", stop);
      headerEl.addEventListener("pointercancel", stop);
    }

    _attachResize(resizeEl, state) {
      let resizing = false;
      let startX = 0, startY = 0, origW = 0, origH = 0;

      resizeEl.addEventListener("pointerdown", (e) => {
        if (state.maximized) return;
        e.stopPropagation();
        resizing = true;
        startX = e.clientX;
        startY = e.clientY;
        origW = state.el.offsetWidth;
        origH = state.el.offsetHeight;
        resizeEl.setPointerCapture(e.pointerId);
      });

      resizeEl.addEventListener("pointermove", (e) => {
        if (!resizing) return;
        const maxW = this.desktopEl.clientWidth - state.el.offsetLeft;
        const maxH = this.desktopEl.clientHeight - state.el.offsetTop;
        const nw = Math.max(300, Math.min(origW + (e.clientX - startX), maxW));
        const nh = Math.max(200, Math.min(origH + (e.clientY - startY), maxH));
        state.el.style.width = `${nw}px`;
        state.el.style.height = `${nh}px`;
      });

      const stop = () => {
        if (resizing) {
          resizing = false;
          this._saveGeom(state);
        }
      };
      resizeEl.addEventListener("pointerup", stop);
      resizeEl.addEventListener("pointercancel", stop);
    }
  }

  global.WindowManager = WindowManager;
})(window);

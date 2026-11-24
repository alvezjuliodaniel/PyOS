/**
 * MicroOS - Cliente WebSocket con reconexion automatica y cola de promesas JSON-RPC
 */
(function (global) {
  "use strict";

  class MicroOSClient {
    constructor(wsPath = "/ws") {
      this.wsPath = wsPath;
      this.ws = null;
      this.connected = false;
      this._nextId = 1;
      this._pending = new Map(); // id -> { resolve, reject, timer }
      this._listeners = new Map(); // event -> Set<fn>
      this._retryMs = 1000;
      this._maxRetryMs = 5000;
      this._reconnectTimer = null;
      this._manualClose = false;
    }

    get url() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${location.host}${this.wsPath}`;
    }

    on(event, fn) {
      if (!this._listeners.has(event)) {
        this._listeners.set(event, new Set());
      }
      this._listeners.get(event).add(fn);
      return () => this.off(event, fn);
    }

    off(event, fn) {
      const set = this._listeners.get(event);
      if (set) set.delete(fn);
    }

    _emit(event, data) {
      const set = this._listeners.get(event);
      if (!set) return;
      for (const fn of set) {
        try {
          fn(data);
        } catch (err) {
          console.error("[MicroOSClient] Listener error:", err);
        }
      }
    }

    connect() {
      this._manualClose = false;
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
      if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
        return;
      }

      try {
        this.ws = new WebSocket(this.url);
      } catch (err) {
        this._scheduleReconnect();
        return;
      }

      this.ws.onopen = () => {
        this.connected = true;
        this._retryMs = 1000;
        this._emit("ws_open", { url: this.url });
      };

      this.ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }

        // Respuesta JSON-RPC
        if (Object.prototype.hasOwnProperty.call(msg, "id") && msg.id !== null) {
          const entry = this._pending.get(msg.id);
          if (entry) {
            clearTimeout(entry.timer);
            this._pending.delete(msg.id);
            if (msg.ok) {
              entry.resolve(msg.data);
            } else {
              entry.reject(new Error(msg.error || "Error RPC"));
            }
          }
          return;
        }

        // Evento push del Kernel
        if (msg.event) {
          this._emit(msg.event, msg.data);
        }
      };

      this.ws.onerror = () => {
        // onclose manejara la reconexion
      };

      this.ws.onclose = () => {
        const wasConnected = this.connected;
        this.connected = false;
        this._rejectAllPending("Conexion WebSocket cerrada");
        if (wasConnected) {
          this._emit("ws_close", {});
        }
        if (!this._manualClose) {
          this._scheduleReconnect();
        }
      };
    }

    close() {
      this._manualClose = true;
      if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
      if (this.ws) this.ws.close();
    }

    _scheduleReconnect() {
      if (this._reconnectTimer) return;
      const delay = this._retryMs;
      this._retryMs = Math.min(this._retryMs * 2, this._maxRetryMs);
      this._reconnectTimer = setTimeout(() => {
        this._reconnectTimer = null;
        this.connect();
      }, delay);
    }

    _rejectAllPending(reason) {
      for (const [, entry] of this._pending) {
        clearTimeout(entry.timer);
        entry.reject(new Error(reason));
      }
      this._pending.clear();
    }

    call(app, cmd, args = {}, timeoutMs = 8000) {
      return new Promise((resolve, reject) => {
        if (!this.connected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
          reject(new Error("WebSocket desconectado"));
          return;
        }
        const id = this._nextId++;
        const timer = setTimeout(() => {
          this._pending.delete(id);
          reject(new Error(`Timeout RPC (${app}.${cmd})`));
        }, timeoutMs);

        this._pending.set(id, { resolve, reject, timer });
        try {
          this.ws.send(JSON.stringify({ id, app, cmd, args }));
        } catch (err) {
          clearTimeout(timer);
          this._pending.delete(id);
          reject(err);
        }
      });
    }
  }

  global.MicroOSClient = MicroOSClient;
})(window);

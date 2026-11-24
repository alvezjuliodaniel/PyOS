/**
 * PyOS App: Ajustes Wi-Fi (Estado, Escaneo 2.4 GHz y Conexion STA)
 */
(function (global) {
  "use strict";

  global.PyOSApps = global.PyOSApps || [];

  global.PyOSApps.push({
    id: "wifi",
    title: "Ajustes Wi-Fi",
    icon: "📶",
    width: 480,
    height: 430,
    mount(container) {
      const { client, toast, escHtml } = global.PyOS;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:10px;height:100%;">
          <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <strong>Estado de Red</strong>
              <button class="btn" id="w-ref">Actualizar</button>
            </div>
            <div id="w-status" class="mono muted" style="font-size:12px;line-height:1.6;">Cargando...</div>
          </div>

          <div class="card" style="display:flex;flex-direction:column;gap:6px;">
            <strong>Conectar a Wi-Fi (2.4 GHz)</strong>
            <div style="display:flex;gap:6px;">
              <input id="w-ssid" class="input" placeholder="Nombre de red (SSID)" style="flex:1;" />
              <input id="w-pass" class="input" type="password" placeholder="Contrasena" style="flex:1;" />
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <span class="muted" style="font-size:11px;">Se guardara en /config/wifi.json</span>
              <button class="btn ok" id="w-conn">Guardar y Conectar</button>
            </div>
          </div>

          <div class="card" style="flex:1;display:flex;flex-direction:column;gap:6px;min-height:0;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong>Redes Cercanas</strong>
              <button class="btn primary" id="w-scan">Escanear</button>
            </div>
            <div id="w-list" style="overflow:auto;flex:1;display:flex;flex-direction:column;gap:4px;">
              <div class="muted" style="font-size:12px;">Pulsa "Escanear" para buscar redes 2.4 GHz.</div>
            </div>
          </div>
        </div>
      `;

      const elStatus = container.querySelector("#w-status");
      const elSsid = container.querySelector("#w-ssid");
      const elPass = container.querySelector("#w-pass");
      const elList = container.querySelector("#w-list");
      const btnScan = container.querySelector("#w-scan");

      async function loadStatus() {
        try {
          const st = await client.call("net", "status");
          elStatus.innerHTML = `
            Modo: <strong style="color:var(--accent);">${escHtml(st.mode.toUpperCase())}</strong> · SSID: <strong>${escHtml(st.ssid || "---")}</strong><br>
            IP: ${escHtml(st.ip)} · Host: ${escHtml(st.hostname)}.local<br>
            RSSI: ${st.rssi !== null && st.rssi !== undefined ? escHtml(st.rssi) + " dBm" : "N/A"} · MAC: ${escHtml(st.mac || "--")}
          `;
        } catch (e) {
          elStatus.textContent = `Error: ${e.message}`;
        }
      }

      async function scanNets() {
        btnScan.disabled = true;
        btnScan.textContent = "Escaneando...";
        elList.innerHTML = '<div class="muted">Buscando redes...</div>';
        try {
          const nets = await client.call("net", "scan", {}, 12000);
          elList.innerHTML = "";
          if (!nets.length) {
            elList.innerHTML = '<div class="muted">No se encontraron redes 2.4 GHz.</div>';
            return;
          }
          for (const n of nets) {
            const row = document.createElement("button");
            row.className = "btn";
            row.style.cssText = "display:flex;justify-content:space-between;align-items:center;text-align:left;padding:6px 8px;";
            const bars = n.rssi > -60 ? "▂▄▆█" : n.rssi > -75 ? "▂▄▆_" : "▂▄__";
            row.innerHTML = `
              <span>${n.auth ? "🔒" : "🔓"} <strong>${escHtml(n.ssid)}</strong></span>
              <span class="mono muted" style="font-size:11px;">${bars} ${escHtml(n.rssi)} dBm</span>
            `;
            row.onclick = () => {
              elSsid.value = n.ssid;
              elPass.focus();
            };
            elList.appendChild(row);
          }
        } catch (e) {
          elList.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
          toast(e.message, "err");
        } finally {
          btnScan.disabled = false;
          btnScan.textContent = "Escanear";
        }
      }

      container.querySelector("#w-ref").onclick = loadStatus;
      btnScan.onclick = scanNets;

      container.querySelector("#w-conn").onclick = async () => {
        const ssid = elSsid.value.trim();
        const password = elPass.value;
        if (!ssid) return toast("Introduce un SSID", "warn");
        try {
          await client.call("net", "connect", { ssid, password });
          toast(`Conectando a "${ssid}"... Accede luego a http://pyos.local`, "ok", 5000);
        } catch (e) {
          toast(e.message, "err");
        }
      };

      const unsub = client.on("net_status", () => loadStatus());
      loadStatus();
      return () => unsub();
    },
  });
})(window);

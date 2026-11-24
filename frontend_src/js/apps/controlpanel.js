/**
 * PyOS App: Panel de Control (estilo Windows 7 por categorias)
 * Sistema, Red/Hostname, Hardware/OLED, Programas y Apariencia/Ventanas.
 */
(function (global) {
  "use strict";

  global.PyOSApps = global.PyOSApps || [];

  global.PyOSApps.push({
    id: "controlpanel",
    title: "Panel de Control",
    icon: "⚙️",
    width: 680,
    height: 480,
    mount(container) {
      const { client, wm, toast, applyAppearance } = global.PyOS;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:10px;height:100%;overflow:auto;">
          <div style="font-size:15px;font-weight:700;">Ajustar la configuracion del equipo</div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:10px;">
            <!-- Sistema y Seguridad -->
            <div class="card" style="display:flex;flex-direction:column;gap:8px;">
              <div style="font-weight:700;color:var(--accent);">🛡️ Sistema y Seguridad</div>
              <div id="cp-sys-info" class="mono muted" style="font-size:11.5px;line-height:1.5;">Cargando...</div>
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <button class="btn" id="cp-mon">Monitor del Sistema</button>
                <button class="btn primary" id="cp-gc">Liberar RAM (GC)</button>
                <button class="btn danger" id="cp-reboot">Reiniciar ESP32</button>
              </div>
            </div>

            <!-- Redes e Internet -->
            <div class="card" style="display:flex;flex-direction:column;gap:8px;">
              <div style="font-weight:700;color:var(--accent);">🌐 Redes e Internet</div>
              <div style="display:flex;gap:6px;align-items:center;">
                <label class="muted" style="font-size:12px;">Hostname:</label>
                <input id="cp-host" class="input mono" style="flex:1;" value="pyos" />
                <button class="btn ok" id="cp-host-save">Guardar</button>
              </div>
              <div style="display:flex;gap:6px;">
                <button class="btn primary" id="cp-wifi">Abrir Ajustes Wi-Fi</button>
              </div>
            </div>

            <!-- Hardware y OLED -->
            <div class="card" style="display:flex;flex-direction:column;gap:8px;">
              <div style="font-weight:700;color:var(--accent);">🔌 Hardware y Pantalla OLED</div>
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12px;">
                <label><input type="checkbox" id="cp-oled-en" checked /> OLED Activa</label>
                <label>SDA:<input id="cp-oled-sda" class="input mono" style="width:46px;" /></label>
                <label>SCL:<input id="cp-oled-scl" class="input mono" style="width:46px;" /></label>
                <select id="cp-oled-h" class="input mono">
                  <option value="64">128x64</option>
                  <option value="32">128x32</option>
                </select>
              </div>
              <div style="display:flex;gap:6px;">
                <button class="btn ok" id="cp-oled-save">Guardar OLED</button>
                <button class="btn" id="cp-gpio">Panel de Hardware (GPIO)</button>
              </div>
            </div>

            <!-- Apariencia y Personalizacion -->
            <div class="card" style="display:flex;flex-direction:column;gap:8px;">
              <div style="font-weight:700;color:var(--accent);">🎨 Apariencia y Ventanas</div>
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12px;">
                <span class="muted">Fondo:</span>
                <button class="btn" data-wp="midnight">Noche</button>
                <button class="btn" data-wp="win7">Azul Win7</button>
                <button class="btn" data-wp="emerald">Esmeralda</button>
                <button class="btn" data-wp="aurora">Aurora</button>
              </div>
              <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:12px;">
                <span class="muted">Acento:</span>
                <button class="btn" data-acc="sky" style="border-color:#38bdf8;">Cielo</button>
                <button class="btn" data-acc="emerald" style="border-color:#22c55e;">Verde</button>
                <button class="btn" data-acc="violet" style="border-color:#a78bfa;">Violeta</button>
                <button class="btn" data-acc="amber" style="border-color:#f59e0b;">Ambar</button>
              </div>
              <div>
                <button class="btn" id="cp-reset-pos">Restablecer posiciones de ventanas</button>
              </div>
            </div>

            <!-- Programas -->
            <div class="card" style="display:flex;flex-direction:column;gap:8px;">
              <div style="font-weight:700;color:var(--accent);">📦 Programas y Caracteristicas</div>
              <div class="muted" style="font-size:12px;">Instala o desinstala aplicaciones dinamicas (.mapp.json) en caliente sin tocar el firmware.</div>
              <div>
                <button class="btn primary" id="cp-apps">Abrir Gestor de Apps</button>
              </div>
            </div>
          </div>
        </div>
      `;

      const elSys = container.querySelector("#cp-sys-info");
      const elHost = container.querySelector("#cp-host");
      const elOledEn = container.querySelector("#cp-oled-en");
      const elOledSda = container.querySelector("#cp-oled-sda");
      const elOledScl = container.querySelector("#cp-oled-scl");
      const elOledH = container.querySelector("#cp-oled-h");

      let sysCfg = {};
      let oledCfg = {};
      let chipId = "esp32c3";

      async function loadConfigs() {
        try {
          const info = await client.call("sys", "info");
          chipId = info.chip_id || "esp32c3";
          elSys.innerHTML = `${info.name} (${info.arch}) · ${info.cores} nucleo(s)<br>MicroPython ${info.mpy_version} · PSRAM: ${info.psram ? "SI" : "NO"}`;
          elHost.value = info.hostname || "pyos";
        } catch {}

        try {
          const s = await client.call("fs", "read", { path: "/config/system.json" });
          sysCfg = JSON.parse(s.content);
          if (sysCfg.hostname) elHost.value = sysCfg.hostname;
        } catch {}

        try {
          const o = await client.call("fs", "read", { path: "/config/oled.json" });
          oledCfg = JSON.parse(o.content);
          elOledEn.checked = oledCfg.enabled !== false;
          elOledH.value = String(oledCfg.height || 64);
          const p = (oledCfg.pins && oledCfg.pins[chipId]) || { sda: 6, scl: 7 };
          elOledSda.value = String(p.sda);
          elOledScl.value = String(p.scl);
        } catch {}
      }

      container.querySelector("#cp-mon").onclick = () => wm.open("monitor");
      container.querySelector("#cp-wifi").onclick = () => wm.open("wifi");
      container.querySelector("#cp-gpio").onclick = () => wm.open("gpio");
      container.querySelector("#cp-apps").onclick = () => wm.open("appstore");

      container.querySelector("#cp-gc").onclick = async () => {
        try {
          const m = await client.call("sys", "gc");
          toast(`GC OK: ${Math.round(m.free / 1024)} KB libres`, "ok");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#cp-reboot").onclick = async () => {
        if (!confirm("¿Reiniciar la placa ESP32?")) return;
        try {
          await client.call("sys", "reboot");
          toast("Reiniciando ESP32...", "warn", 4000);
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#cp-host-save").onclick = async () => {
        const hn = elHost.value.trim().toLowerCase();
        if (!hn) return toast("Hostname vacio", "warn");
        sysCfg.hostname = hn;
        try {
          await client.call("fs", "write", { path: "/config/system.json", content: JSON.stringify(sysCfg, null, 2) });
          toast(`Hostname '${hn}.local' guardado (reinicia para aplicar)`, "ok");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#cp-oled-save").onclick = async () => {
        oledCfg.enabled = elOledEn.checked;
        oledCfg.height = Number(elOledH.value) || 64;
        oledCfg.width = 128;
        oledCfg.pins = oledCfg.pins || {};
        oledCfg.pins[chipId] = {
          sda: Number(elOledSda.value) || 6,
          scl: Number(elOledScl.value) || 7,
        };
        try {
          await client.call("fs", "write", { path: "/config/oled.json", content: JSON.stringify(oledCfg, null, 2) });
          toast("Configuracion OLED guardada (reinicia para aplicar)", "ok");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelectorAll("[data-wp]").forEach((b) => {
        b.onclick = () => {
          const k = b.dataset.wp;
          try { localStorage.setItem("pyos_wp", k); } catch {}
          applyAppearance(k, null);
          toast("Fondo actualizado", "ok", 1800);
        };
      });

      container.querySelectorAll("[data-acc]").forEach((b) => {
        b.onclick = () => {
          const k = b.dataset.acc;
          try { localStorage.setItem("pyos_acc", k); } catch {}
          applyAppearance(null, k);
          toast("Color de acento actualizado", "ok", 1800);
        };
      });

      container.querySelector("#cp-reset-pos").onclick = () => {
        wm.resetWindowPositions();
        toast("Posiciones de ventanas restablecidas", "ok");
      };

      loadConfigs();
    },
  });
})(window);

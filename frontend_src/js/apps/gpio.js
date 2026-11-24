/**
 * PyOS App 4: Panel de Hardware (GPIO / ADC / PWM)
 * Se adapta automaticamente al mapa de pines seguros de la placa (C3 / S / S3).
 */
(function (global) {
  "use strict";

  global.PyOSApps = global.PyOSApps || [];

  global.PyOSApps.push({
    id: "gpio",
    title: "Panel de Hardware",
    icon: "🔌",
    width: 640,
    height: 450,
    mount(container) {
      const { client, toast } = global.PyOS;
      let boardData = null;
      let adcTimer = null;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:10px;height:100%;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
            <div>
              <strong id="g-board">Cargando placa...</strong>
              <span id="g-meta" class="muted" style="margin-left:8px;font-size:12px;"></span>
            </div>
            <div style="display:flex;gap:6px;">
              <button class="btn" id="g-auto-adc">Auto ADC: OFF</button>
              <button class="btn primary" id="g-refresh">Refrescar</button>
            </div>
          </div>

          <div style="overflow:auto;flex:1;display:flex;flex-direction:column;gap:10px;">
            <div class="card">
              <div style="font-weight:600;margin-bottom:8px;">Pines Digitales Seguros (Entrada / Salida / PWM)</div>
              <div id="g-io-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:8px;"></div>
            </div>

            <div class="card" id="g-in-wrap" style="display:none;">
              <div style="font-weight:600;margin-bottom:8px;">Pines Solo Entrada (ESP32 Clasico)</div>
              <div id="g-in-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;"></div>
            </div>

            <div class="card">
              <div style="font-weight:600;margin-bottom:8px;">Canales ADC1 Seguros (Wi-Fi Safe)</div>
              <div id="g-adc-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:8px;"></div>
            </div>
          </div>
        </div>
      `;

      const elBoard = container.querySelector("#g-board");
      const elMeta = container.querySelector("#g-meta");
      const ioGrid = container.querySelector("#g-io-grid");
      const inWrap = container.querySelector("#g-in-wrap");
      const inGrid = container.querySelector("#g-in-grid");
      const adcGrid = container.querySelector("#g-adc-grid");
      const btnAutoAdc = container.querySelector("#g-auto-adc");

      function renderBoard() {
        if (!boardData) return;
        const { pins, states } = boardData;
        elBoard.textContent = boardData.board;
        elMeta.textContent = `I/O: ${pins.safe_io.length} | ADC1: ${pins.safe_adc.length} | Max PWM: ${boardData.pwm_max_channels}`;

        // Pines I/O seguros
        ioGrid.innerHTML = "";
        for (const pin of pins.safe_io) {
          const st = states[pin] || states[String(pin)] || null;
          const isHigh = st && st.mode === "out" && st.val === 1;
          const isPwm = st && st.mode === "pwm";

          const card = document.createElement("div");
          card.className = "card";
          card.style.padding = "8px";
          card.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <span class="mono" style="font-weight:700;">GPIO ${pin}</span>
              <span class="mono" style="font-size:11px;color:${isHigh ? "var(--ok)" : isPwm ? "var(--accent)" : "var(--text-muted)"};" data-st="${pin}">
                ${isPwm ? `PWM ${st.duty_pct}%` : st ? `${st.mode.toUpperCase()}=${st.val}` : "LIBRE"}
              </span>
            </div>
            <div style="display:flex;gap:4px;margin-bottom:6px;">
              <button class="btn ${isHigh ? "ok" : ""}" style="flex:1;padding:3px;" data-act="toggle">Toggle</button>
              <button class="btn" style="padding:3px 6px;" data-act="read">Leer</button>
              <button class="btn" style="padding:3px 6px;" data-act="rel" title="Liberar">✕</button>
            </div>
            <div style="display:flex;align-items:center;gap:6px;">
              <span class="muted" style="font-size:10px;">PWM</span>
              <input type="range" min="0" max="100" value="${isPwm ? Math.round(st.duty_pct) : 0}" style="flex:1;" data-act="pwm" />
            </div>
          `;

          card.querySelector('[data-act="toggle"]').onclick = async () => {
            try {
              await client.call("gpio", "toggle", { pin });
              loadInfo();
            } catch (e) { toast(e.message, "err"); }
          };
          card.querySelector('[data-act="read"]').onclick = async () => {
            try {
              const r = await client.call("gpio", "get", { pin });
              card.querySelector(`[data-st="${pin}"]`).textContent = `IN=${r.val}`;
            } catch (e) { toast(e.message, "err"); }
          };
          card.querySelector('[data-act="rel"]').onclick = async () => {
            try {
              await client.call("gpio", "release", { pin });
              loadInfo();
            } catch (e) { toast(e.message, "err"); }
          };
          card.querySelector('[data-act="pwm"]').onchange = async (e) => {
            const duty = Number(e.target.value);
            try {
              if (duty === 0) {
                await client.call("gpio", "pwm_stop", { pin });
              } else {
                await client.call("gpio", "pwm", { pin, freq: 1000, duty });
              }
              loadInfo();
            } catch (err) { toast(err.message, "err"); }
          };

          ioGrid.appendChild(card);
        }

        // Pines solo entrada (ESP32 clasico)
        if (pins.safe_in_only && pins.safe_in_only.length) {
          inWrap.style.display = "block";
          inGrid.innerHTML = "";
          for (const pin of pins.safe_in_only) {
            const card = document.createElement("div");
            card.className = "card";
            card.style.cssText = "padding:8px;display:flex;justify-content:space-between;align-items:center;";
            card.innerHTML = `<span class="mono">GPIO ${pin}</span><span class="mono muted" data-in="${pin}">--</span><button class="btn" style="padding:2px 6px;">Leer</button>`;
            card.querySelector("button").onclick = async () => {
              try {
                const r = await client.call("gpio", "get", { pin });
                card.querySelector(`[data-in="${pin}"]`).textContent = String(r.val);
              } catch (e) { toast(e.message, "err"); }
            };
            inGrid.appendChild(card);
          }
        } else {
          inWrap.style.display = "none";
        }

        // Canales ADC1
        adcGrid.innerHTML = "";
        for (const pin of pins.safe_adc) {
          const card = document.createElement("div");
          card.className = "card";
          card.style.padding = "8px";
          card.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
              <span class="mono" style="font-weight:700;">ADC GPIO ${pin}</span>
              <button class="btn" style="padding:2px 6px;font-size:11px;">Leer</button>
            </div>
            <div class="mono" style="font-size:12px;" data-adc-val="${pin}">-- mV (--%)</div>
            <div style="height:6px;background:rgba(2,6,23,0.7);border-radius:4px;overflow:hidden;margin-top:5px;">
              <div data-adc-bar="${pin}" style="height:100%;width:0%;background:var(--accent);transition:width 0.2s;"></div>
            </div>
          `;
          card.querySelector("button").onclick = () => readAdcPin(pin);
          adcGrid.appendChild(card);
        }
      }

      async function readAdcPin(pin) {
        try {
          const r = await client.call("gpio", "adc", { pin });
          const valEl = container.querySelector(`[data-adc-val="${pin}"]`);
          const barEl = container.querySelector(`[data-adc-bar="${pin}"]`);
          if (valEl) valEl.textContent = `${r.mv} mV (${r.pct}%)`;
          if (barEl) barEl.style.width = `${Math.min(100, Math.max(0, r.pct))}%`;
        } catch (e) {
          toast(e.message, "err");
        }
      }

      async function readAllAdc() {
        if (!boardData) return;
        for (const pin of boardData.pins.safe_adc) {
          await readAdcPin(pin);
        }
      }

      async function loadInfo() {
        try {
          boardData = await client.call("gpio", "info");
          renderBoard();
        } catch (e) {
          toast(e.message, "err");
        }
      }

      container.querySelector("#g-refresh").onclick = loadInfo;
      btnAutoAdc.onclick = () => {
        if (adcTimer) {
          clearInterval(adcTimer);
          adcTimer = null;
          btnAutoAdc.textContent = "Auto ADC: OFF";
          btnAutoAdc.classList.remove("primary");
        } else {
          readAllAdc();
          adcTimer = setInterval(readAllAdc, 1500);
          btnAutoAdc.textContent = "Auto ADC: ON";
          btnAutoAdc.classList.add("primary");
        }
      };

      loadInfo();

      return () => {
        if (adcTimer) clearInterval(adcTimer);
      };
    },
  });
})(window);

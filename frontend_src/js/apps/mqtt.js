/**
 * PyOS App: PyOS MQTT Center (MQTT 3.1.1 sobre TLS Verificado + AOS IoT)
 * Paneles: Conexion/TLS, Explorador de Topicos, Publicador y Monitor de Conexion.
 */
(function (global) {
  "use strict";

  global.PyOSApps = global.PyOSApps || [];

  global.PyOSApps.push({
    id: "mqtt",
    title: "PyOS MQTT Center",
    icon: "📡",
    width: 720,
    height: 510,
    mount(container) {
      const { client, toast, escHtml } = global.PyOS;
      let paused = false;
      let messages = [];
      let filterText = "";

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px;height:100%;">
          <!-- Cabecera de estado TLS -->
          <div class="card" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;padding:8px 10px;">
            <div style="display:flex;align-items:center;gap:8px;">
              <span id="mq-led" class="status-led"></span>
              <strong id="mq-state">Desconectado</strong>
              <span id="mq-tls-badge" class="tray-pill">🔒 TLS Obligatorio</span>
              <span id="mq-uptime" class="mono muted" style="font-size:11px;"></span>
            </div>
            <div style="display:flex;gap:6px;">
              <button class="btn ok" id="mq-btn-conn">Conectar</button>
              <button class="btn danger" id="mq-btn-disc">Desconectar</button>
              <button class="btn" id="mq-btn-ref">↻</button>
            </div>
          </div>

          <!-- Pestanas -->
          <div style="display:flex;gap:4px;flex-wrap:wrap;">
            <button class="btn primary" data-tab="conn">🔐 Conexion y TLS</button>
            <button class="btn" data-tab="sub">📥 Explorador de Topicos</button>
            <button class="btn" data-tab="pub">📤 Publicador</button>
            <button class="btn" data-tab="mon">📈 Monitor</button>
          </div>

          <!-- TAB 1: Conexion y TLS -->
          <div data-pane="conn" style="display:flex;flex-direction:column;gap:8px;flex:1;overflow:auto;">
            <div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted">Broker Host</span>
                <input id="mq-host" class="input mono" placeholder="mqtt.aos-iot.example.com" />
              </label>
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted">Puerto TLS (defecto 8883)</span>
                <input id="mq-port" class="input mono" type="number" value="8883" />
              </label>
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted">Client ID (unico del dispositivo)</span>
                <input id="mq-cid" class="input mono" />
              </label>
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted">Topico base AOS IoT</span>
                <input id="mq-base" class="input mono" placeholder="aosiot/projects/u3" />
              </label>
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted">Keep Alive (s)</span>
                <input id="mq-ka" class="input mono" type="number" value="60" />
              </label>
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted">Usuario</span>
                <input id="mq-user" class="input mono" />
              </label>
              <label style="display:flex;flex-direction:column;gap:3px;">
                <span class="muted" id="mq-pass-lbl">Contrasena (no se devuelve al navegador)</span>
                <input id="mq-pass" class="input" type="password" placeholder="Dejar vacio para conservar" autocomplete="new-password" />
              </label>
            </div>

            <div class="card" style="display:flex;flex-direction:column;gap:6px;">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-weight:600;">Certificado CA Personalizado (Opcional) <span id="mq-ca-st" class="muted" style="font-weight:400;"></span></span>
                <label class="btn" style="margin:0;font-size:11px;">
                  Cargar .pem/.crt
                  <input type="file" id="mq-ca-file" accept=".pem,.crt,.cer" style="display:none;" />
                </label>
              </div>
              <textarea id="mq-ca" class="input mono" rows="3" style="font-size:10.5px;resize:vertical;" placeholder="Opcional: deja vacio para usar el bundle CA integrado (Let's Encrypt / DigiCert)."></textarea>
              <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
                <div style="display:flex;gap:12px;font-size:12px;">
                  <label><input type="checkbox" id="mq-auto" /> Inicio automatico</label>
                  <label><input type="checkbox" id="mq-aos" checked /> Nodo AOS IoT</label>
                </div>
                <button class="btn primary" id="mq-save">Guardar Configuracion</button>
              </div>
            </div>
            <div id="mq-err" class="mono" style="color:var(--err);font-size:11.5px;"></div>
          </div>

          <!-- TAB 2: Explorador de Topicos -->
          <div data-pane="sub" style="display:none;flex-direction:column;gap:8px;flex:1;min-height:0;">
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <input id="mq-sub-topic" class="input mono" placeholder="Topico (ej. aos/devices/# o sensores/+/temp)" style="flex:1;min-width:180px;" />
              <select id="mq-sub-qos" class="input mono"><option value="0">QoS 0</option><option value="1">QoS 1</option></select>
              <button class="btn primary" id="mq-sub-add">Suscribir</button>
            </div>
            <div id="mq-subs-pills" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
            <div style="display:flex;gap:6px;align-items:center;">
              <input id="mq-filter" class="input" placeholder="🔍 Filtrar mensajes..." style="flex:1;" />
              <button class="btn" id="mq-pause">Pausar</button>
              <button class="btn" id="mq-clear">Limpiar</button>
            </div>
            <div id="mq-feed" class="card mono" style="flex:1;overflow:auto;background:rgba(2,6,23,0.85);font-size:11.5px;display:flex;flex-direction:column;gap:4px;"></div>
          </div>

          <!-- TAB 3: Publicador -->
          <div data-pane="pub" style="display:none;flex-direction:column;gap:8px;flex:1;">
            <div class="card" style="display:flex;flex-direction:column;gap:8px;flex:1;">
              <div style="display:flex;gap:6px;flex-wrap:wrap;">
                <input id="mq-pub-topic" class="input mono" placeholder="Topico destino (sin wildcards # ni +)" style="flex:1;" />
                <select id="mq-pub-qos" class="input mono"><option value="0">QoS 0</option><option value="1">QoS 1</option></select>
                <label style="display:flex;align-items:center;gap:4px;font-size:12px;"><input type="checkbox" id="mq-pub-ret" /> Retain</label>
              </div>
              <textarea id="mq-pub-body" class="input mono" style="flex:1;resize:none;font-size:12px;" placeholder='{"temp": 24.5, "ok": true}'></textarea>
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span class="muted" style="font-size:11px;">Limite: 4096 B · Soporta texto plano o JSON</span>
                <div style="display:flex;gap:6px;">
                  <button class="btn" id="mq-pub-fmt">Validar JSON</button>
                  <button class="btn ok" id="mq-pub-send">📤 Publicar</button>
                </div>
              </div>
            </div>
          </div>

          <!-- TAB 4: Monitor de Conexion -->
          <div data-pane="mon" style="display:none;flex-direction:column;gap:8px;flex:1;">
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;">
              <div class="card"><div class="muted">Enviados / Recibidos</div><div id="mq-st-io" class="mono" style="font-size:16px;font-weight:700;">0 / 0</div></div>
              <div class="card"><div class="muted">Reconexiones / Errores TLS</div><div id="mq-st-err" class="mono" style="font-size:16px;font-weight:700;">0 / 0</div></div>
              <div class="card"><div class="muted">Latencia RTT (PING)</div><div id="mq-st-lat" class="mono" style="font-size:16px;font-weight:700;">-- ms</div></div>
            </div>
            <div class="card mono" id="mq-st-mem" style="font-size:12px;line-height:1.7;"></div>
            <div class="card mono muted" id="mq-st-aos" style="font-size:11.5px;line-height:1.6;"></div>
          </div>
        </div>
      `;

      // Cambio de pestanas
      container.querySelectorAll("[data-tab]").forEach((btn) => {
        btn.onclick = () => {
          const t = btn.dataset.tab;
          container.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("primary", b === btn));
          container.querySelectorAll("[data-pane]").forEach((p) => {
            p.style.display = p.dataset.pane === t ? "flex" : "none";
          });
        };
      });

      const elLed = container.querySelector("#mq-led");
      const elState = container.querySelector("#mq-state");
      const elTls = container.querySelector("#mq-tls-badge");
      const elUp = container.querySelector("#mq-uptime");
      const elErr = container.querySelector("#mq-err");
      const elHost = container.querySelector("#mq-host");
      const elPort = container.querySelector("#mq-port");
      const elCid = container.querySelector("#mq-cid");
      const elBase = container.querySelector("#mq-base");
      const elKa = container.querySelector("#mq-ka");
      const elUser = container.querySelector("#mq-user");
      const elPass = container.querySelector("#mq-pass");
      const elPassLbl = container.querySelector("#mq-pass-lbl");
      const elCa = container.querySelector("#mq-ca");
      const elCaSt = container.querySelector("#mq-ca-st");
      const elAuto = container.querySelector("#mq-auto");
      const elAos = container.querySelector("#mq-aos");
      const elSubs = container.querySelector("#mq-subs-pills");
      const elFeed = container.querySelector("#mq-feed");

      function renderFeed() {
        const q = filterText.toLowerCase();
        const list = q
          ? messages.filter((m) => m.topic.toLowerCase().includes(q) || m.payload.toLowerCase().includes(q))
          : messages;
        elFeed.innerHTML = "";
        if (!list.length) {
          elFeed.innerHTML = '<div class="muted">Sin mensajes recibidos.</div>';
          return;
        }
        for (const m of list.slice().reverse()) {
          const row = document.createElement("div");
          row.style.cssText = "padding:5px 7px;border-radius:5px;background:rgba(30,41,59,0.5);border:1px solid var(--border);";
          row.innerHTML = `
            <div style="display:flex;justify-content:space-between;font-size:10.5px;">
              <strong style="color:var(--accent);">${escHtml(m.topic)}</strong>
              <span class="muted">QoS ${m.qos} · t+${m.t}s</span>
            </div>
            <div style="margin-top:3px;word-break:break-all;">${escHtml(m.payload)}</div>
          `;
          elFeed.appendChild(row);
        }
      }

      function applyStatus(st, fillInputs = false) {
        if (!st) return;
        const ok = st.connected;
        elLed.style.background = ok ? "var(--ok)" : st.state === "connecting" ? "var(--warn)" : "var(--err)";
        elState.textContent = ok ? "Conectado" : st.state === "connecting" ? "Conectando TLS..." : "Desconectado";
        elTls.textContent = ok ? "🔒 TLS Verificado (CERT_REQUIRED)" : "🔒 TLS Obligatorio";
        elTls.classList.toggle("ws-on", ok);
        elUp.textContent = ok ? `Conectado ${st.connected_s}s` : "";
        elErr.textContent = st.last_error ? `Ultimo error: ${st.last_error}` : "";

        elPassLbl.textContent = st.has_password ? "Contrasena (guardada ✓)" : "Contrasena (sin configurar)";
        elCaSt.textContent = st.ca_source === "custom"
          ? "(CA personalizada ✓)"
          : st.ca_source === "builtin"
            ? "(Bundle CA integrado activo ✓)"
            : "(Sin CA)";

        if (fillInputs) {
          elHost.value = st.host || "";
          elPort.value = String(st.port || 8883);
          elCid.value = st.client_id || "";
          elBase.value = st.aos_base_topic || "aos/devices";
          elKa.value = String(st.keepalive || 60);
          elUser.value = st.username || "";
          elAuto.checked = Boolean(st.autostart);
          elAos.checked = Boolean(st.aos_enabled);
        }

        // Pills de suscripciones
        elSubs.innerHTML = "";
        for (const t of st.subscriptions || []) {
          const p = document.createElement("span");
          p.className = "tray-pill";
          p.innerHTML = `<span class="mono">${escHtml(t)}</span> <button class="btn danger" style="padding:0 4px;font-size:10px;border:none;">×</button>`;
          p.querySelector("button").onclick = async () => {
            try {
              await client.call("mqtt", "unsubscribe", { topic: t });
              loadStatus(false);
            } catch (e) { toast(e.message, "err"); }
          };
          elSubs.appendChild(p);
        }

        // Monitor
        const s = st.stats || {};
        container.querySelector("#mq-st-io").textContent = `${s.sent || 0} / ${s.received || 0}`;
        container.querySelector("#mq-st-err").textContent = `${s.reconnects || 0} / ${s.tls_errors || 0}`;
        container.querySelector("#mq-st-lat").textContent = st.latency_ms != null ? `${st.latency_ms} ms` : "-- ms";
        container.querySelector("#mq-st-mem").innerHTML =
          `Perfil hardware: <strong>${escHtml(st.profile)}</strong><br>` +
          `RAM libre antes TLS: ${s.ram_before_tls ? Math.round(s.ram_before_tls / 1024) + " KB" : "--"}<br>` +
          `RAM libre tras TLS : ${s.ram_after_tls ? Math.round(s.ram_after_tls / 1024) + " KB" : "--"} ` +
          `(delta TLS: ${s.tls_ram_delta ? Math.round(s.tls_ram_delta / 1024) + " KB" : "--"})`;

        const cid = escHtml(st.client_id || "pyos");
        const base = escHtml(st.aos_base_topic || "aos/devices");
        container.querySelector("#mq-st-aos").innerHTML =
          `<strong>Esquema AOS IoT (${st.aos_enabled ? "Activo" : "Inactivo"}):</strong><br>` +
          `Telemetria: ${base}/${cid}/telemetry<br>` +
          `Estado LWT: ${base}/${cid}/status<br>` +
          `Comandos  : ${base}/${cid}/commands → responses`;
      }

      async function loadStatus(fillInputs = false) {
        try {
          const st = await client.call("mqtt", "status");
          applyStatus(st, fillInputs);
        } catch (e) { toast(e.message, "err"); }
      }

      container.querySelector("#mq-ca-file").onchange = async (e) => {
        const f = e.target.files[0];
        if (!f) return;
        elCa.value = await f.text();
        e.target.value = "";
      };

      container.querySelector("#mq-save").onclick = async () => {
        const payload = {
          host: elHost.value.trim(),
          port: Number(elPort.value) || 8883,
          client_id: elCid.value.trim(),
          aos_base_topic: elBase.value.trim() || "aos/devices",
          username: elUser.value.trim(),
          keepalive: Number(elKa.value) || 60,
          autostart: elAuto.checked,
          aos_enabled: elAos.checked,
        };
        if (elPass.value) payload.password = elPass.value;
        if (elCa.value.trim()) payload.ca_pem = elCa.value.trim();
        try {
          const st = await client.call("mqtt", "configure", payload);
          elPass.value = "";
          elCa.value = "";
          applyStatus(st, true);
          toast("Configuracion MQTT TLS guardada", "ok");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#mq-btn-conn").onclick = async () => {
        try {
          const st = await client.call("mqtt", "connect");
          applyStatus(st, false);
          toast("Iniciando conexion MQTT TLS...", "info");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#mq-btn-disc").onclick = async () => {
        try {
          const st = await client.call("mqtt", "disconnect");
          applyStatus(st, false);
          toast("MQTT desconectado", "info");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#mq-btn-ref").onclick = () => loadStatus(false);

      container.querySelector("#mq-sub-add").onclick = async () => {
        const topic = container.querySelector("#mq-sub-topic").value.trim();
        const qos = Number(container.querySelector("#mq-sub-qos").value);
        if (!topic) return toast("Introduce un topico", "warn");
        try {
          await client.call("mqtt", "subscribe", { topic, qos });
          container.querySelector("#mq-sub-topic").value = "";
          loadStatus(false);
          toast(`Suscrito a ${topic}`, "ok");
        } catch (e) { toast(e.message, "err"); }
      };

      container.querySelector("#mq-filter").oninput = (e) => { filterText = e.target.value; renderFeed(); };
      container.querySelector("#mq-pause").onclick = (e) => {
        paused = !paused;
        e.target.textContent = paused ? "Reanudar" : "Pausar";
        e.target.classList.toggle("primary", paused);
      };
      container.querySelector("#mq-clear").onclick = () => { messages = []; renderFeed(); };

      container.querySelector("#mq-pub-fmt").onclick = () => {
        const area = container.querySelector("#mq-pub-body");
        try {
          area.value = JSON.stringify(JSON.parse(area.value), null, 2);
          toast("JSON valido", "ok");
        } catch (e) { toast(`JSON invalido: ${e.message}`, "err"); }
      };

      container.querySelector("#mq-pub-send").onclick = async () => {
        const topic = container.querySelector("#mq-pub-topic").value.trim();
        const payload = container.querySelector("#mq-pub-body").value;
        const qos = Number(container.querySelector("#mq-pub-qos").value);
        const retain = container.querySelector("#mq-pub-ret").checked;
        if (!topic) return toast("Introduce un topico destino", "warn");
        try {
          await client.call("mqtt", "publish", { topic, payload, qos, retain }, 10000);
          toast(`Publicado en ${topic} (QoS ${qos})`, "ok");
          loadStatus(false);
        } catch (e) { toast(e.message, "err"); }
      };

      const u1 = client.on("mqtt_status", (st) => applyStatus(st, false));
      const u2 = client.on("mqtt_msg", (m) => {
        if (paused) return;
        messages.push(m);
        if (messages.length > 60) messages.shift();
        renderFeed();
      });
      const u3 = client.on("mqtt_error", (ev) => {
        if (ev && ev.error) toast(`MQTT: ${ev.error}`, "err", 4500);
      });

      loadStatus(true);
      client.call("mqtt", "messages", { limit: 30 }).then((ms) => {
        if (Array.isArray(ms)) { messages = ms; renderFeed(); }
      }).catch(() => {});

      return () => { u1(); u2(); u3(); };
    },
  });
})(window);

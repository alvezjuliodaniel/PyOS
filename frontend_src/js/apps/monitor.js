/**
 * MicroOS App 1: Monitor del Sistema
 * Grafico dual en <canvas> (RAM libre + Carga CPU %), metricas por nucleo,
 * uso de Flash, RSSI Wi-Fi y gestor de tareas uasyncio activas.
 */
(function (global) {
  "use strict";

  global.MicroOSApps = global.MicroOSApps || [];

  function fmtUptime(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const h = String(Math.floor(s / 3600)).padStart(2, "0");
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const r = String(s % 60).padStart(2, "0");
    return `${h}:${m}:${r}`;
  }

  global.MicroOSApps.push({
    id: "monitor",
    title: "Monitor del Sistema",
    icon: "📊",
    width: 650,
    height: 510,
    mount(container) {
      const { client, toast, escHtml } = global.MicroOS;
      const ramHistory = [];
      const cpuHistory = [];
      const MAX_PTS = 40;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:9px;height:100%;">
          <!-- Resumen superior -->
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;">
            <div class="card"><div class="muted">RAM Libre</div><div id="m-ram" class="mono" style="font-size:14px;font-weight:700;">--</div></div>
            <div class="card"><div class="muted">Flash Usado</div><div id="m-flash" class="mono" style="font-size:14px;font-weight:700;">--</div></div>
            <div class="card"><div class="muted">CPU / Temp</div><div id="m-cpu" class="mono" style="font-size:14px;font-weight:700;">--</div></div>
            <div class="card"><div class="muted">Wi-Fi / Uptime</div><div id="m-net" class="mono" style="font-size:12px;font-weight:700;">--</div></div>
          </div>

          <!-- Procesador y Nucleos -->
          <div class="card" style="padding:8px 10px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:6px;">
              <span style="font-weight:600;">Procesador y Nucleos</span>
              <span id="m-cpu-meta" class="mono muted" style="font-size:11px;">--</span>
            </div>
            <div id="m-cores" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;"></div>
          </div>

          <!-- Grafico dual RAM + CPU -->
          <div class="card" style="padding:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
              <span style="font-weight:600;">
                Historial · <span style="color:#38bdf8;">━ RAM Libre</span> · <span style="color:#22c55e;">━ Carga CPU %</span>
              </span>
              <div style="display:flex;gap:6px;">
                <button class="btn primary" id="m-gc">GC Ahora</button>
                <button class="btn" id="m-ref">Refrescar</button>
              </div>
            </div>
            <canvas id="m-canvas" width="590" height="105" style="width:100%;height:105px;background:rgba(2,6,23,0.6);border-radius:6px;display:block;"></canvas>
          </div>

          <!-- Tareas uasyncio -->
          <div class="card" style="flex:1;overflow:auto;">
            <div style="font-weight:600;margin-bottom:6px;">Tareas uasyncio activas</div>
            <table style="width:100%;border-collapse:collapse;font-size:12px;">
              <thead>
                <tr class="muted" style="text-align:left;border-bottom:1px solid var(--border);">
                  <th style="padding:4px;">Tarea</th>
                  <th style="padding:4px;">Estado</th>
                  <th style="padding:4px;">Critica</th>
                  <th style="padding:4px;">Reinicios</th>
                  <th style="padding:4px;">Accion</th>
                </tr>
              </thead>
              <tbody id="m-tasks"></tbody>
            </table>
          </div>
        </div>
      `;

      const elRam = container.querySelector("#m-ram");
      const elFlash = container.querySelector("#m-flash");
      const elCpu = container.querySelector("#m-cpu");
      const elNet = container.querySelector("#m-net");
      const elCpuMeta = container.querySelector("#m-cpu-meta");
      const elCores = container.querySelector("#m-cores");
      const elTasks = container.querySelector("#m-tasks");
      const canvas = container.querySelector("#m-canvas");
      const ctx = canvas.getContext("2d");

      function drawChart(totalKb) {
        const w = canvas.width;
        const h = canvas.height;
        ctx.clearRect(0, 0, w, h);

        ctx.strokeStyle = "rgba(148,163,184,0.14)";
        ctx.lineWidth = 1;
        for (let i = 1; i <= 3; i++) {
          const y = Math.round((h * i) / 4);
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(w, y);
          ctx.stroke();
        }

        // Curva RAM Libre (azul)
        if (ramHistory.length >= 2) {
          const maxRam = Math.max(totalKb || 180, ...ramHistory, 64);
          ctx.beginPath();
          ramHistory.forEach((kb, idx) => {
            const x = (idx / (MAX_PTS - 1)) * w;
            const y = h - (kb / maxRam) * (h - 14) - 6;
            if (idx === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          });
          ctx.strokeStyle = "#38bdf8";
          ctx.lineWidth = 2;
          ctx.stroke();

          const lastX = ((ramHistory.length - 1) / (MAX_PTS - 1)) * w;
          ctx.lineTo(lastX, h);
          ctx.lineTo(0, h);
          ctx.closePath();
          ctx.fillStyle = "rgba(56,189,248,0.12)";
          ctx.fill();
        }

        // Curva Carga CPU % (verde)
        if (cpuHistory.length >= 2) {
          ctx.beginPath();
          cpuHistory.forEach((pct, idx) => {
            const x = (idx / (MAX_PTS - 1)) * w;
            const y = h - (Math.min(100, Math.max(0, pct)) / 100) * (h - 14) - 6;
            if (idx === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
          });
          ctx.strokeStyle = "#22c55e";
          ctx.lineWidth = 2;
          ctx.stroke();
        }
      }

      function renderCores(st) {
        const cpu = st.cpu || {
          arch: "--",
          cores: 1,
          freq_mhz: st.cpu_mhz || 0,
          max_mhz: st.cpu_mhz || 160,
          load_pct: 0,
          tasks_running: 0,
        };
        const freq = cpu.freq_mhz || st.cpu_mhz || 0;
        const maxFreq = cpu.max_mhz || freq || 160;
        const load = Number(cpu.load_pct || 0);
        const coresCount = cpu.cores || 1;

        elCpuMeta.textContent = `${cpu.arch} · ${coresCount} ${coresCount > 1 ? "nucleos" : "nucleo"} · ${freq}/${maxFreq} MHz · Uptime ${fmtUptime(st.uptime_s)}`;

        const coreList = [];
        if (coresCount >= 2) {
          // ESP32 / ESP32-S3: Core 0 = PRO_CPU (Wi-Fi/LWIP/FreeRTOS), Core 1 = APP_CPU (MicroPython/uasyncio)
          const wifiActive = st.net ? st.net.connected : true;
          const c0Load = wifiActive ? Math.min(100, Math.round(12 + load * 0.25)) : 4;
          coreList.push({
            name: "Core 0 (PRO_CPU)",
            role: "Wi-Fi / Red / FreeRTOS",
            pct: c0Load,
            color: "#38bdf8",
            detail: `${freq} MHz · ${wifiActive ? "Radio activa" : "Idle"}`,
          });
          coreList.push({
            name: "Core 1 (APP_CPU)",
            role: "MicroPython / Kernel uasyncio",
            pct: Math.round(load),
            color: load > 75 ? "#ef4444" : load > 45 ? "#f59e0b" : "#22c55e",
            detail: `${freq} MHz · ${cpu.tasks_running || 0} tareas`,
          });
        } else {
          // ESP32-C3: Single-core RISC-V
          coreList.push({
            name: "Core 0 (RISC-V)",
            role: "MicroPython + Wi-Fi + Kernel",
            pct: Math.round(load),
            color: load > 75 ? "#ef4444" : load > 45 ? "#f59e0b" : "#22c55e",
            detail: `${freq} MHz · ${cpu.tasks_running || 0} tareas`,
          });
        }

        elCores.innerHTML = "";
        for (const c of coreList) {
          const box = document.createElement("div");
          box.style.cssText = "background:rgba(2,6,23,0.55);border:1px solid var(--border);border-radius:6px;padding:7px 9px;";
          box.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;">
              <strong class="mono">${c.name}</strong>
              <span class="mono" style="color:${c.color};font-weight:700;">${c.pct}%</span>
            </div>
            <div class="muted" style="font-size:10.5px;margin:2px 0 5px;">${c.role} · ${c.detail}</div>
            <div style="height:6px;background:rgba(148,163,184,0.16);border-radius:4px;overflow:hidden;">
              <div style="height:100%;width:${Math.max(2, Math.min(100, c.pct))}%;background:${c.color};transition:width 0.3s;"></div>
            </div>
          `;
          elCores.appendChild(box);
        }
      }

      function applyStats(st) {
        if (!st) return;
        let totKb = 180;
        if (st.mem) {
          const freeKb = Math.round(st.mem.free / 1024);
          totKb = Math.round(st.mem.total / 1024);
          elRam.textContent = `${freeKb} / ${totKb} KB`;
          elRam.style.color = st.mem.critical ? "var(--err)" : st.mem.warn ? "var(--warn)" : "var(--ok)";
          ramHistory.push(freeKb);
          if (ramHistory.length > MAX_PTS) ramHistory.shift();
        }
        const cpuLoad = st.cpu && st.cpu.load_pct !== undefined ? Number(st.cpu.load_pct) : 0;
        cpuHistory.push(cpuLoad);
        if (cpuHistory.length > MAX_PTS) cpuHistory.shift();

        drawChart(totKb);
        renderCores(st);

        if (st.flash) {
          elFlash.textContent = `${st.flash.used_pct}% (${Math.round(st.flash.free / 1024)}K lib)`;
        }
        const tempStr = st.temp_c !== null && st.temp_c !== undefined ? `${st.temp_c}°C` : "N/A";
        elCpu.textContent = `${st.cpu_mhz || 0}MHz · ${tempStr}`;
        if (st.net) {
          const rssi = st.net.rssi !== null && st.net.rssi !== undefined ? `${st.net.rssi} dBm` : st.net.mode.toUpperCase();
          elNet.textContent = `${st.net.ip} (${rssi})`;
        } else {
          elNet.textContent = `Up ${fmtUptime(st.uptime_s)}`;
        }
      }

      async function loadTasks() {
        try {
          const tasks = await client.call("sys", "tasks");
          elTasks.innerHTML = "";
          for (const t of tasks) {
            const tr = document.createElement("tr");
            tr.style.borderBottom = "1px solid rgba(148,163,184,0.1)";
            const col = t.state === "running" ? "var(--ok)" : t.state === "failed" ? "var(--err)" : "var(--warn)";
            tr.innerHTML = `
              <td class="mono" style="padding:4px;">${escHtml(t.name)}</td>
              <td style="padding:4px;color:${col};">${escHtml(t.state)}</td>
              <td style="padding:4px;">${t.critical ? "SI" : "NO"}</td>
              <td class="mono" style="padding:4px;">${t.restarts}</td>
              <td style="padding:4px;"><button class="btn" style="padding:2px 6px;font-size:11px;">Reiniciar</button></td>
            `;
            tr.querySelector("button").onclick = async () => {
              try {
                await client.call("sys", "task_restart", { name: t.name });
                toast(`Tarea '${t.name}' reiniciada`, "ok");
                setTimeout(loadTasks, 300);
              } catch (e) {
                toast(e.message, "err");
              }
            };
            elTasks.appendChild(tr);
          }
        } catch (e) {
          // Ignorar si desconectado
        }
      }

      async function refreshAll() {
        try {
          const st = await client.call("sys", "stats");
          applyStats(st);
          await loadTasks();
        } catch (e) {
          toast(e.message, "err");
        }
      }

      container.querySelector("#m-gc").onclick = async () => {
        try {
          const m = await client.call("sys", "gc");
          toast(`GC completado: ${Math.round(m.free / 1024)} KB libres`, "ok");
          refreshAll();
        } catch (e) {
          toast(e.message, "err");
        }
      };
      container.querySelector("#m-ref").onclick = refreshAll;

      const unsub = client.on("sys_stats", (st) => applyStats(st));
      const taskTimer = setInterval(loadTasks, 5000);
      refreshAll();

      return () => {
        unsub();
        clearInterval(taskTimer);
      };
    },
  });
})(window);

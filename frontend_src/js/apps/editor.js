/**
 * MicroOS App: Editor de Texto / Notas (integrado con LittleFS y REPL Python)
 */
(function (global) {
  "use strict";

  global.MicroOSApps = global.MicroOSApps || [];

  global.MicroOSApps.push({
    id: "editor",
    title: "Editor de Texto",
    icon: "📝",
    width: 620,
    height: 440,
    mount(container, ctx) {
      const { client, toast } = global.MicroOS;
      let currentPath = "/apps/nota.txt";
      let dirty = false;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px;height:100%;">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
            <input id="ed-path" class="input mono" value="${currentPath}" style="flex:1;min-width:150px;" placeholder="/apps/archivo.txt" />
            <button class="btn" id="ed-open">Abrir</button>
            <button class="btn" id="ed-new">Nuevo</button>
            <button class="btn ok" id="ed-save">Guardar</button>
            <button class="btn primary" id="ed-run" title="Ejecutar script Python en la placa">▶ Run</button>
          </div>
          <textarea id="ed-area" class="input mono" style="flex:1;resize:none;line-height:1.5;font-size:12.5px;tab-size:4;" spellcheck="false" placeholder="Escribe tus notas o codigo MicroPython aqui..."></textarea>
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;" class="muted mono">
            <span id="ed-status">Listo</span>
            <span id="ed-stats">0 lineas · 0 palabras · 0 chars</span>
          </div>
        </div>
      `;

      const elPath = container.querySelector("#ed-path");
      const elArea = container.querySelector("#ed-area");
      const elStatus = container.querySelector("#ed-status");
      const elStats = container.querySelector("#ed-stats");

      function updateTitle() {
        const name = currentPath.split("/").pop() || "sin_titulo";
        ctx.setTitle(`Editor — ${name}${dirty ? " *" : ""}`);
      }

      function updateStats() {
        const v = elArea.value;
        const lines = v ? v.split("\n").length : 0;
        const words = v.trim() ? v.trim().split(/\s+/).length : 0;
        elStats.textContent = `${lines} lineas · ${words} palabras · ${v.length} chars`;
      }

      async function openFile(path) {
        const target = (path || "").trim();
        if (!target) return;
        try {
          const res = await client.call("fs", "read", { path: target, max_bytes: 8192 });
          currentPath = res.path;
          elPath.value = currentPath;
          elArea.value = res.content;
          dirty = false;
          elStatus.textContent = `Abierto: ${currentPath}`;
          updateStats();
          updateTitle();
        } catch (e) {
          toast(e.message, "err");
          elStatus.textContent = `Error: ${e.message}`;
        }
      }

      async function saveFile() {
        const target = elPath.value.trim();
        if (!target) return toast("Indica una ruta valida", "warn");
        try {
          const res = await client.call("fs", "write", { path: target, content: elArea.value });
          currentPath = res.path;
          elPath.value = currentPath;
          dirty = false;
          elStatus.textContent = `Guardado (${res.written} B)`;
          updateTitle();
          toast(`Guardado ${currentPath}`, "ok");
        } catch (e) {
          toast(e.message, "err");
        }
      }

      elArea.addEventListener("input", () => {
        if (!dirty) {
          dirty = true;
          updateTitle();
        }
        updateStats();
      });

      // Soporte de tecla Tab para indentar y Ctrl+S para guardar
      elArea.addEventListener("keydown", (e) => {
        if (e.key === "Tab") {
          e.preventDefault();
          const s = elArea.selectionStart;
          const end = elArea.selectionEnd;
          elArea.value = elArea.value.slice(0, s) + "    " + elArea.value.slice(end);
          elArea.selectionStart = elArea.selectionEnd = s + 4;
          dirty = true;
          updateStats();
          updateTitle();
        } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          saveFile();
        }
      });

      container.querySelector("#ed-open").onclick = () => openFile(elPath.value);
      container.querySelector("#ed-save").onclick = saveFile;
      container.querySelector("#ed-new").onclick = () => {
        elArea.value = "";
        currentPath = "/apps/nuevo.txt";
        elPath.value = currentPath;
        dirty = false;
        elStatus.textContent = "Documento nuevo";
        updateStats();
        updateTitle();
      };

      container.querySelector("#ed-run").onclick = async () => {
        const code = elArea.value.trim();
        if (!code) return;
        try {
          const res = await client.call("term", "exec", { code });
          const out = [res.output, res.result].filter(Boolean).join("\n");
          elStatus.textContent = out ? `Salida: ${out.slice(0, 80)}` : "Ejecutado sin salida";
          toast(out ? out.slice(0, 120) : "Codigo ejecutado OK", "ok");
        } catch (e) {
          elStatus.textContent = `Error: ${e.message}`;
          toast(e.message, "err");
        }
      };

      updateStats();
      updateTitle();
    },
  });
})(window);

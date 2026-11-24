/**
 * MicroOS App: Explorador de Archivos estilo Windows 7 (LittleFS)
 * Incluye: historial Atras/Adelante, Breadcrumbs, busqueda instantanea,
 * barra de comandos (Organizar, Copiar/Cortar/Pegar, Renombrar, Nueva carpeta),
 * panel izquierdo de Favoritos, vistas Detalles/Iconos/Lista, ordenamiento,
 * panel de vista previa editable, menu contextual, Drag&Drop y atajos Win7.
 */
(function (global) {
  "use strict";

  global.MicroOSApps = global.MicroOSApps || [];

  function fmtSize(bytes) {
    if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(2)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

  function fileMeta(item) {
    if (item.type === "dir") return { ico: "📁", kind: "Carpeta de archivos" };
    const n = item.name.toLowerCase();
    if (n.endsWith(".py") || n.endsWith(".mpy")) return { ico: "🐍", kind: "Script Python" };
    if (n.endsWith(".json")) return { ico: "⚙️", kind: "Archivo JSON" };
    if (n.endsWith(".gz")) return { ico: "📦", kind: "Archivo Gzip" };
    if (n.endsWith(".html") || n.endsWith(".css") || n.endsWith(".js")) return { ico: "🌐", kind: "Archivo Web" };
    if (n.endsWith(".txt") || n.endsWith(".md") || n.endsWith(".log")) return { ico: "📄", kind: "Documento de texto" };
    return { ico: "📃", kind: "Archivo" };
  }

  function joinPath(dir, name) {
    return dir === "/" ? `/${name}` : `${dir.replace(/\/+$/, "")}/${name}`;
  }

  global.MicroOSApps.push({
    id: "files",
    title: "Explorador de Archivos",
    icon: "📁",
    width: 820,
    height: 520,
    mount(container, ctx) {
      const { client, toast, escHtml } = global.MicroOS;

      let currentDir = "/";
      let entries = [];
      let history = [];
      let histPos = -1;
      let selected = new Set(); // full paths
      let clipboard = null; // { mode: 'copy'|'cut', items: [{path, name, type}] }
      let viewMode = "details"; // 'details' | 'icons' | 'list'
      let sortBy = "name"; // 'name' | 'type' | 'size'
      let sortAsc = true;
      let searchQuery = "";
      let showPreview = true;
      let previewPath = null;
      let activeCtxMenu = null;

      container.innerHTML = `
        <div class="w7-exp">
          <!-- Barra de navegacion (Atras, Adelante, Subir, Breadcrumbs, Busqueda) -->
          <div class="w7-nav">
            <button class="w7-nav-btn" id="w7-back" title="Atras (Alt+Izq)" disabled>◀</button>
            <button class="w7-nav-btn" id="w7-fwd" title="Adelante (Alt+Der)" disabled>▶</button>
            <button class="w7-nav-btn" id="w7-up" title="Subir un nivel (Backspace)">⬆</button>
            <div class="w7-crumb-bar" id="w7-crumbs" title="Clic para editar ruta"></div>
            <input class="input mono" id="w7-path-in" style="display:none;flex:1;height:26px;" />
            <button class="w7-nav-btn" id="w7-ref" title="Actualizar (F5)">↻</button>
            <input class="input" id="w7-search" placeholder="🔍 Buscar..." style="width:155px;height:26px;" />
          </div>

          <!-- Barra de comandos estilo Windows 7 -->
          <div class="w7-cmd">
            <div class="w7-cmd-group">
              <button class="w7-cmd-btn" id="w7-org">Organizar ▾</button>
              <button class="w7-cmd-btn" id="w7-mkdir">📁 Nueva carpeta</button>
              <button class="w7-cmd-btn" id="w7-newfile">📄 Nuevo archivo</button>
              <label class="w7-cmd-btn" style="margin:0;">⬆ Subir<input type="file" id="w7-upfile" multiple style="display:none;" /></label>
              <button class="w7-cmd-btn" id="w7-dl" disabled>⬇ Descargar</button>
              <button class="w7-cmd-btn" id="w7-paste" disabled>📋 Pegar</button>
            </div>
            <div class="w7-cmd-group">
              <button class="w7-cmd-btn active" data-view="details" title="Vista Detalles">☰ Detalles</button>
              <button class="w7-cmd-btn" data-view="icons" title="Vista Iconos grandes">⊞ Iconos</button>
              <button class="w7-cmd-btn" data-view="list" title="Vista Lista compacta">≡ Lista</button>
              <button class="w7-cmd-btn active" id="w7-prev-btn" title="Mostrar/ocultar Panel de vista previa">◫ Vista previa</button>
            </div>
          </div>

          <!-- Cuerpo principal: Panel izquierdo + Lista/Iconos + Vista previa -->
          <div class="w7-main">
            <aside class="w7-side">
              <div class="w7-side-hdr">Favoritos</div>
              <button class="w7-side-item" data-nav="/">🖥️ Equipo (/)</button>
              <button class="w7-side-item" data-nav="/apps">📱 Aplicaciones</button>
              <button class="w7-side-item" data-nav="/config">⚙️ Configuracion</button>
              <div class="w7-side-hdr">Sistema</div>
              <button class="w7-side-item" data-nav="/www">🌐 Web (/www)</button>
              <button class="w7-side-item" data-nav="/sys">🔧 Nucleo (/sys)</button>
            </aside>

            <main class="w7-content" id="w7-content"></main>

            <aside class="w7-preview" id="w7-preview">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:4px;">
                <span id="w7-prev-title" class="mono muted" style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Vista previa</span>
                <button class="btn ok" id="w7-prev-save" style="padding:2px 7px;font-size:11px;" disabled>Guardar</button>
              </div>
              <textarea id="w7-prev-text" class="input mono" style="flex:1;resize:none;font-size:11.5px;line-height:1.4;" spellcheck="false" placeholder="Selecciona un archivo para previsualizar o editar..." disabled></textarea>
            </aside>
          </div>

          <!-- Panel de detalles inferior estilo Windows 7 -->
          <footer class="w7-details">
            <div id="w7-det-left" style="display:flex;align-items:center;gap:10px;overflow:hidden;"></div>
            <div id="w7-det-right" class="mono muted" style="font-size:11px;white-space:nowrap;"></div>
          </footer>
        </div>
      `;

      const elBack = container.querySelector("#w7-back");
      const elFwd = container.querySelector("#w7-fwd");
      const elUp = container.querySelector("#w7-up");
      const elCrumbs = container.querySelector("#w7-crumbs");
      const elPathIn = container.querySelector("#w7-path-in");
      const elSearch = container.querySelector("#w7-search");
      const elContent = container.querySelector("#w7-content");
      const elPreview = container.querySelector("#w7-preview");
      const elPrevTitle = container.querySelector("#w7-prev-title");
      const elPrevText = container.querySelector("#w7-prev-text");
      const elPrevSave = container.querySelector("#w7-prev-save");
      const elDetLeft = container.querySelector("#w7-det-left");
      const elDetRight = container.querySelector("#w7-det-right");
      const btnDl = container.querySelector("#w7-dl");
      const btnPaste = container.querySelector("#w7-paste");
      const btnPrev = container.querySelector("#w7-prev-btn");

      function closeCtxMenu() {
        if (activeCtxMenu) {
          activeCtxMenu.remove();
          activeCtxMenu = null;
        }
      }

      function getSelectedEntries() {
        return entries.filter((e) => selected.has(joinPath(currentDir, e.name)));
      }

      function getFilteredSorted() {
        let list = entries;
        if (searchQuery) {
          const q = searchQuery.toLowerCase();
          list = list.filter((e) => e.name.toLowerCase().includes(q));
        }
        return [...list].sort((a, b) => {
          // Carpetas siempre primero como en Windows 7
          if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
          let cmp = 0;
          if (sortBy === "size") cmp = (a.size || 0) - (b.size || 0);
          else if (sortBy === "type") cmp = fileMeta(a).kind.localeCompare(fileMeta(b).kind);
          else cmp = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
          return sortAsc ? cmp : -cmp;
        });
      }

      function renderBreadcrumbs() {
        elCrumbs.innerHTML = "";
        const rootBtn = document.createElement("button");
        rootBtn.className = "w7-crumb";
        rootBtn.textContent = "💻 Equipo";
        rootBtn.onclick = (e) => { e.stopPropagation(); navigate("/"); };
        elCrumbs.appendChild(rootBtn);

        if (currentDir !== "/") {
          const parts = currentDir.split("/").filter(Boolean);
          let acc = "";
          for (const p of parts) {
            acc += "/" + p;
            const sep = document.createElement("span");
            sep.className = "w7-crumb-sep";
            sep.textContent = "▸";
            elCrumbs.appendChild(sep);

            const b = document.createElement("button");
            b.className = "w7-crumb";
            b.textContent = p;
            const target = acc;
            b.onclick = (e) => { e.stopPropagation(); navigate(target); };
            elCrumbs.appendChild(b);
          }
        }
      }

      function updateNavAndSidebar() {
        elBack.disabled = histPos <= 0;
        elFwd.disabled = histPos >= history.length - 1;
        elUp.disabled = currentDir === "/";
        ctx.setTitle(`Explorador — ${currentDir}`);
        container.querySelectorAll(".w7-side-item").forEach((b) => {
          b.classList.toggle("active", b.dataset.nav === currentDir);
        });
        renderBreadcrumbs();
      }

      function updateDetailsPane() {
        const selItems = getSelectedEntries();
        btnDl.disabled = selItems.length !== 1 || selItems[0].type !== "file";
        btnPaste.disabled = !clipboard || !clipboard.items.length;

        if (selItems.length === 0) {
          elDetLeft.innerHTML = `<span>📁</span><span><strong>${entries.length}</strong> elementos en <span class="mono">${escHtml(currentDir)}</span></span>`;
          const tot = entries.reduce((s, e) => s + (e.size || 0), 0);
          elDetRight.textContent = clipboard
            ? `Portapapeles: ${clipboard.items.length} (${clipboard.mode === "cut" ? "Cortar" : "Copiar"}) · ${fmtSize(tot)}`
            : `Total: ${fmtSize(tot)}`;
        } else if (selItems.length === 1) {
          const it = selItems[0];
          const meta = fileMeta(it);
          elDetLeft.innerHTML = `<span style="font-size:20px;">${meta.ico}</span><div><strong>${escHtml(it.name)}</strong> <span class="muted">(${meta.kind})</span></div>`;
          elDetRight.textContent = it.type === "file" ? `Tamano: ${fmtSize(it.size)}` : "Carpeta";
        } else {
          const tot = selItems.reduce((s, e) => s + (e.size || 0), 0);
          elDetLeft.innerHTML = `<span>📑</span><span><strong>${selItems.length}</strong> elementos seleccionados</span>`;
          elDetRight.textContent = `Tamano: ${fmtSize(tot)}`;
        }
      }

      async function loadPreview(item) {
        if (!showPreview) return;
        if (!item || item.type !== "file") {
          previewPath = null;
          elPrevTitle.textContent = item ? `${item.name} (Carpeta)` : "Vista previa";
          elPrevText.value = "";
          elPrevText.disabled = true;
          elPrevSave.disabled = true;
          return;
        }
        const full = joinPath(currentDir, item.name);
        previewPath = full;
        elPrevTitle.textContent = item.name;
        if (item.name.toLowerCase().endsWith(".gz") || item.name.toLowerCase().endsWith(".mpy")) {
          elPrevText.value = `[Archivo binario (${fmtSize(item.size)}). Usa Descargar para obtenerlo.]`;
          elPrevText.disabled = true;
          elPrevSave.disabled = true;
          return;
        }
        try {
          const res = await client.call("fs", "read", { path: full, max_bytes: 8192 });
          elPrevText.value = res.content;
          elPrevText.disabled = false;
          elPrevSave.disabled = false;
        } catch (err) {
          elPrevText.value = `[Sin vista previa: ${err.message}]`;
          elPrevText.disabled = true;
          elPrevSave.disabled = true;
        }
      }

      function renderItems() {
        const list = getFilteredSorted();
        elContent.innerHTML = "";
        const cutPaths = new Set(clipboard && clipboard.mode === "cut" ? clipboard.items.map((i) => i.path) : []);

        if (!list.length) {
          elContent.innerHTML = `<div class="muted" style="padding:24px;text-align:center;">${searchQuery ? "Ningun archivo coincide con la busqueda." : "Esta carpeta esta vacia."}</div>`;
          updateDetailsPane();
          return;
        }

        const bindInteractions = (el, item, full) => {
          el.dataset.full = full;
          el.draggable = true;

          // Drag interno para mover 1 o varios archivos seleccionados a carpetas
          el.addEventListener("dragstart", (e) => {
            if (!selected.has(full)) {
              selected.clear();
              selected.add(full);
              renderItems();
            }
            const paths = Array.from(selected);
            e.dataTransfer.setData("application/x-microos-paths", JSON.stringify(paths));
            e.dataTransfer.effectAllowed = "move";
          });

          // Si el elemento es carpeta, aceptar soltar archivos sobre ella
          if (item.type === "dir") {
            el.addEventListener("dragover", (e) => {
              if (e.dataTransfer.types.includes("application/x-microos-paths")) {
                e.preventDefault();
                e.stopPropagation();
                el.classList.add("drop-target");
              }
            });
            el.addEventListener("dragleave", () => el.classList.remove("drop-target"));
            el.addEventListener("drop", async (e) => {
              const raw = e.dataTransfer.getData("application/x-microos-paths");
              if (!raw) return;
              e.preventDefault();
              e.stopPropagation();
              el.classList.remove("drop-target");
              await movePathsToDir(JSON.parse(raw), full);
            });
          }

          el.addEventListener("click", (e) => {
            e.stopPropagation();
            closeCtxMenu();
            if (e.ctrlKey || e.metaKey) {
              if (selected.has(full)) selected.delete(full);
              else selected.add(full);
            } else {
              selected.clear();
              selected.add(full);
            }
            renderItems();
            if (selected.size === 1) loadPreview(item);
          });
          el.addEventListener("dblclick", (e) => {
            e.stopPropagation();
            if (item.type === "dir") navigate(full);
            else {
              showPreview = true;
              elPreview.style.display = "flex";
              btnPrev.classList.add("active");
              loadPreview(item);
            }
          });
          el.addEventListener("contextmenu", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!selected.has(full)) {
              selected.clear();
              selected.add(full);
              renderItems();
            }
            openContextMenu(e.clientX, e.clientY, item);
          });
        };

        if (viewMode === "details") {
          const arrow = (col) => (sortBy === col ? (sortAsc ? " ▲" : " ▼") : "");
          const table = document.createElement("table");
          table.className = "w7-table";
          table.innerHTML = `
            <thead><tr>
              <th data-sort="name">Nombre${arrow("name")}</th>
              <th data-sort="type" style="width:150px;">Tipo${arrow("type")}</th>
              <th data-sort="size" style="width:90px;text-align:right;">Tamano${arrow("size")}</th>
            </tr></thead>
            <tbody></tbody>
          `;
          table.querySelectorAll("th[data-sort]").forEach((th) => {
            th.onclick = () => {
              const col = th.dataset.sort;
              if (sortBy === col) sortAsc = !sortAsc;
              else { sortBy = col; sortAsc = true; }
              renderItems();
            };
          });
          const tbody = table.querySelector("tbody");
          for (const item of list) {
            const full = joinPath(currentDir, item.name);
            const meta = fileMeta(item);
            const tr = document.createElement("tr");
            tr.className = `w7-row${selected.has(full) ? " sel" : ""}${cutPaths.has(full) ? " cut" : ""}`;
            tr.innerHTML = `
              <td class="mono">${meta.ico} ${escHtml(item.name)}</td>
              <td class="muted">${meta.kind}</td>
              <td class="mono muted" style="text-align:right;">${item.type === "file" ? fmtSize(item.size) : ""}</td>
            `;
            bindInteractions(tr, item, full);
            tbody.appendChild(tr);
          }
          elContent.appendChild(table);
        } else {
          const grid = document.createElement("div");
          grid.className = "w7-grid";
          if (viewMode === "list") grid.style.gridTemplateColumns = "repeat(auto-fill,minmax(160px,1fr))";
          for (const item of list) {
            const full = joinPath(currentDir, item.name);
            const meta = fileMeta(item);
            const tile = document.createElement("div");
            tile.className = `w7-tile${selected.has(full) ? " sel" : ""}${cutPaths.has(full) ? " cut" : ""}`;
            if (viewMode === "list") {
              tile.style.cssText = "flex-direction:row;justify-content:flex-start;padding:4px 6px;gap:6px;text-align:left;";
              tile.innerHTML = `<span>${meta.ico}</span><span class="mono w7-tile-name">${escHtml(item.name)}</span>`;
            } else {
              tile.innerHTML = `<span class="w7-tile-ico">${meta.ico}</span><span class="mono w7-tile-name">${escHtml(item.name)}</span>`;
            }
            bindInteractions(tile, item, full);
            grid.appendChild(tile);
          }
          elContent.appendChild(grid);
        }
        updateDetailsPane();
      }

      async function navigate(path, pushHistory = true) {
        try {
          const res = await client.call("fs", "ls", { path });
          currentDir = res.path;
          entries = res.entries;
          selected.clear();
          if (pushHistory && history[histPos] !== currentDir) {
            history = history.slice(0, histPos + 1);
            history.push(currentDir);
            histPos = history.length - 1;
          }
          updateNavAndSidebar();
          renderItems();
          loadPreview(null);
        } catch (err) {
          toast(err.message, "err");
        }
      }

      // Acciones de archivos estilo Windows 7
      function setClipboard(mode) {
        const sel = getSelectedEntries();
        if (!sel.length) return;
        clipboard = {
          mode,
          items: sel.map((i) => ({ path: joinPath(currentDir, i.name), name: i.name, type: i.type })),
        };
        toast(`${sel.length} elemento(s) ${mode === "cut" ? "cortado(s)" : "copiado(s)"}`, "info", 2000);
        renderItems();
      }

      async function doPaste() {
        if (!clipboard || !clipboard.items.length) return;
        try {
          for (const it of clipboard.items) {
            let targetName = it.name;
            let dst = joinPath(currentDir, targetName);
            if (dst === it.path && clipboard.mode === "copy") {
              dst = joinPath(currentDir, `copia_${targetName}`);
            }
            if (clipboard.mode === "cut") {
              await client.call("fs", "mv", { src: it.path, dst });
            } else {
              await client.call("fs", "cp", { src: it.path, dst });
            }
          }
          if (clipboard.mode === "cut") clipboard = null;
          toast("Pegado completado", "ok");
          navigate(currentDir, false);
        } catch (err) {
          toast(err.message, "err");
        }
      }

      async function doRename() {
        const sel = getSelectedEntries();
        if (sel.length !== 1) return;
        const it = sel[0];
        const newName = prompt("Nuevo nombre:", it.name);
        if (!newName || newName.trim() === it.name) return;
        try {
          await client.call("fs", "mv", {
            src: joinPath(currentDir, it.name),
            dst: joinPath(currentDir, newName.trim()),
          });
          toast("Renombrado OK", "ok");
          navigate(currentDir, false);
        } catch (err) {
          toast(err.message, "err");
        }
      }

      async function doDelete() {
        const sel = getSelectedEntries();
        if (!sel.length) return;
        const msg = sel.length === 1 ? `¿Eliminar "${sel[0].name}"?` : `¿Eliminar ${sel.length} elementos seleccionados?`;
        if (!confirm(msg)) return;
        try {
          for (const it of sel) {
            await client.call("fs", "rm", { path: joinPath(currentDir, it.name), recursive: it.type === "dir" });
          }
          toast("Eliminado correctamente", "ok");
          navigate(currentDir, false);
        } catch (err) {
          toast(err.message, "err");
        }
      }

      async function doMkdir() {
        const name = prompt("Nombre de la nueva carpeta:", "Nueva carpeta");
        if (!name) return;
        try {
          await client.call("fs", "mkdir", { path: joinPath(currentDir, name.trim()) });
          navigate(currentDir, false);
        } catch (err) { toast(err.message, "err"); }
      }

      async function doNewFile() {
        const name = prompt("Nombre del nuevo archivo:", "nuevo.txt");
        if (!name) return;
        const full = joinPath(currentDir, name.trim());
        try {
          await client.call("fs", "write", { path: full, content: "" });
          await navigate(currentDir, false);
          selected.add(full);
          const created = entries.find((e) => e.name === name.trim());
          renderItems();
          if (created) loadPreview(created);
        } catch (err) { toast(err.message, "err"); }
      }

      function doDownload() {
        const sel = getSelectedEntries();
        if (sel.length !== 1 || sel[0].type !== "file") return;
        const full = joinPath(currentDir, sel[0].name);
        window.open(`/api/fs/download?path=${encodeURIComponent(full)}`, "_blank");
      }

      function doProperties() {
        const sel = getSelectedEntries();
        if (sel.length === 1) {
          const it = sel[0];
          const meta = fileMeta(it);
          alert(`Propiedades\n\nNombre: ${it.name}\nTipo: ${meta.kind}\nRuta: ${joinPath(currentDir, it.name)}\nTamano: ${fmtSize(it.size || 0)}`);
        } else {
          const tot = entries.reduce((s, e) => s + (e.size || 0), 0);
          alert(`Propiedades de carpeta\n\nRuta: ${currentDir}\nElementos: ${entries.length}\nTamano archivos: ${fmtSize(tot)}`);
        }
      }

      async function movePathsToDir(paths, destDir) {
        let moved = 0;
        try {
          for (const src of paths) {
            if (src === destDir) continue;
            const name = src.split("/").pop();
            const dst = joinPath(destDir, name);
            if (src === dst) continue;
            await client.call("fs", "mv", { src, dst });
            moved++;
          }
          if (moved) {
            toast(`${moved} elemento(s) movido(s) a ${destDir}`, "ok");
            navigate(currentDir, false);
          }
        } catch (err) {
          toast(err.message, "err");
        }
      }

      async function uploadFiles(fileList) {
        for (const file of fileList) {
          const target = joinPath(currentDir, file.name);
          try {
            const resp = await fetch(`/api/fs/upload?path=${encodeURIComponent(target)}`, { method: "POST", body: file });
            const data = await resp.json();
            if (!data.ok) throw new Error(data.error || "Error subiendo");
            toast(`Subido: ${file.name} (${fmtSize(data.written)})`, "ok");
          } catch (err) {
            toast(`${file.name}: ${err.message}`, "err");
          }
        }
        navigate(currentDir, false);
      }

      function openContextMenu(x, y, item) {
        closeCtxMenu();
        const menu = document.createElement("div");
        menu.className = "w7-ctx";
        const hasSel = selected.size > 0;
        const single = selected.size === 1;
        const isFile = single && item && item.type === "file";

        const items = [
          item ? { lbl: item.type === "dir" ? "Abrir" : "Vista previa / Editar", fn: () => item.type === "dir" ? navigate(joinPath(currentDir, item.name)) : loadPreview(item) } : null,
          isFile ? { lbl: "Descargar", fn: doDownload } : null,
          { sep: true },
          { lbl: "Cortar", key: "Ctrl+X", dis: !hasSel, fn: () => setClipboard("cut") },
          { lbl: "Copiar", key: "Ctrl+C", dis: !hasSel, fn: () => setClipboard("copy") },
          { lbl: "Pegar", key: "Ctrl+V", dis: !clipboard, fn: doPaste },
          { sep: true },
          { lbl: "Nueva carpeta", fn: doMkdir },
          { lbl: "Nuevo archivo", fn: doNewFile },
          { sep: true },
          { lbl: "Renombrar", key: "F2", dis: !single, fn: doRename },
          { lbl: "Eliminar", key: "Supr", dis: !hasSel, fn: doDelete },
          { lbl: "Propiedades", fn: doProperties },
        ].filter(Boolean);

        for (const it of items) {
          if (it.sep) { menu.appendChild(document.createElement("hr")); continue; }
          const b = document.createElement("button");
          b.disabled = Boolean(it.dis);
          b.innerHTML = `<span>${it.lbl}</span><span class="muted" style="font-size:10px;">${it.key || ""}</span>`;
          b.onclick = () => { closeCtxMenu(); it.fn(); };
          menu.appendChild(b);
        }

        document.body.appendChild(menu);
        const mx = Math.min(x, window.innerWidth - menu.offsetWidth - 8);
        const my = Math.min(y, window.innerHeight - menu.offsetHeight - 8);
        menu.style.left = `${Math.max(4, mx)}px`;
        menu.style.top = `${Math.max(4, my)}px`;
        activeCtxMenu = menu;
      }

      // Eventos de UI
      elBack.onclick = () => { if (histPos > 0) { histPos--; navigate(history[histPos], false); } };
      elFwd.onclick = () => { if (histPos < history.length - 1) { histPos++; navigate(history[histPos], false); } };
      elUp.onclick = () => { if (currentDir !== "/") navigate(currentDir.replace(/\/[^/]+$/, "") || "/"); };
      container.querySelector("#w7-ref").onclick = () => navigate(currentDir, false);

      // Clic en barra de breadcrumbs -> modo edicion manual de ruta
      elCrumbs.onclick = () => {
        elCrumbs.style.display = "none";
        elPathIn.style.display = "block";
        elPathIn.value = currentDir;
        elPathIn.focus();
        elPathIn.select();
      };
      const finishPathEdit = (go) => {
        elPathIn.style.display = "none";
        elCrumbs.style.display = "flex";
        if (go) navigate(elPathIn.value.trim() || "/");
      };
      elPathIn.onkeydown = (e) => {
        if (e.key === "Enter") finishPathEdit(true);
        else if (e.key === "Escape") finishPathEdit(false);
      };
      elPathIn.onblur = () => finishPathEdit(false);

      elSearch.oninput = () => { searchQuery = elSearch.value.trim(); renderItems(); };

      container.querySelector("#w7-org").onclick = (e) => {
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        openContextMenu(r.left, r.bottom + 2, getSelectedEntries()[0] || null);
      };
      container.querySelector("#w7-mkdir").onclick = doMkdir;
      container.querySelector("#w7-newfile").onclick = doNewFile;
      btnDl.onclick = doDownload;
      btnPaste.onclick = doPaste;
      container.querySelector("#w7-upfile").onchange = (e) => {
        if (e.target.files.length) uploadFiles(e.target.files);
        e.target.value = "";
      };

      container.querySelectorAll("[data-view]").forEach((b) => {
        b.onclick = () => {
          viewMode = b.dataset.view;
          container.querySelectorAll("[data-view]").forEach((x) => x.classList.toggle("active", x === b));
          renderItems();
        };
      });

      btnPrev.onclick = () => {
        showPreview = !showPreview;
        elPreview.style.display = showPreview ? "flex" : "none";
        btnPrev.classList.toggle("active", showPreview);
        if (showPreview && selected.size === 1) loadPreview(getSelectedEntries()[0]);
      };

      elPrevSave.onclick = async () => {
        if (!previewPath) return;
        try {
          const r = await client.call("fs", "write", { path: previewPath, content: elPrevText.value });
          toast(`Guardado (${fmtSize(r.written)})`, "ok");
          navigate(currentDir, false);
        } catch (err) { toast(err.message, "err"); }
      };

      container.querySelectorAll(".w7-side-item").forEach((b) => {
        b.onclick = () => navigate(b.dataset.nav);
        // Permitir soltar archivos arrastrados en los accesos del panel izquierdo
        b.ondragover = (e) => {
          if (e.dataTransfer.types.includes("application/x-microos-paths")) {
            e.preventDefault();
            b.classList.add("drop-target");
          }
        };
        b.ondragleave = () => b.classList.remove("drop-target");
        b.ondrop = async (e) => {
          const raw = e.dataTransfer.getData("application/x-microos-paths");
          if (!raw) return;
          e.preventDefault();
          b.classList.remove("drop-target");
          await movePathsToDir(JSON.parse(raw), b.dataset.nav);
        };
      });

      // Seleccion multiple por recuadro arrastrando el raton (Marquee / Rubber-band)
      let marqueeActive = false;
      let marqueeMoved = false;
      let mqStartX = 0, mqStartY = 0;
      let mqEl = null;

      elContent.addEventListener("mousedown", (e) => {
        if (e.button !== 0 || e.target.closest("[data-full]")) return;
        closeCtxMenu();
        marqueeActive = true;
        marqueeMoved = false;
        const rect = elContent.getBoundingClientRect();
        mqStartX = e.clientX - rect.left + elContent.scrollLeft;
        mqStartY = e.clientY - rect.top + elContent.scrollTop;
        if (!e.ctrlKey && !e.metaKey) selected.clear();
      });

      const onDocMouseMove = (e) => {
        if (!marqueeActive) return;
        const rect = elContent.getBoundingClientRect();
        const curX = e.clientX - rect.left + elContent.scrollLeft;
        const curY = e.clientY - rect.top + elContent.scrollTop;
        const x = Math.min(mqStartX, curX);
        const y = Math.min(mqStartY, curY);
        const w = Math.abs(curX - mqStartX);
        const h = Math.abs(curY - mqStartY);
        if (w < 4 && h < 4) return;
        marqueeMoved = true;

        if (!mqEl) {
          mqEl = document.createElement("div");
          mqEl.className = "w7-marquee";
          elContent.appendChild(mqEl);
        }
        mqEl.style.left = `${x}px`;
        mqEl.style.top = `${y}px`;
        mqEl.style.width = `${w}px`;
        mqEl.style.height = `${h}px`;

        const box = mqEl.getBoundingClientRect();
        elContent.querySelectorAll("[data-full]").forEach((node) => {
          const r = node.getBoundingClientRect();
          const hit = !(r.right < box.left || r.left > box.right || r.bottom < box.top || r.top > box.bottom);
          if (hit) selected.add(node.dataset.full);
          else if (!e.ctrlKey && !e.metaKey) selected.delete(node.dataset.full);
          node.classList.toggle("sel", selected.has(node.dataset.full));
        });
        updateDetailsPane();
      };

      const onDocMouseUp = () => {
        if (!marqueeActive) return;
        marqueeActive = false;
        if (mqEl) { mqEl.remove(); mqEl = null; }
        if (marqueeMoved) {
          const sel = getSelectedEntries();
          if (sel.length === 1) loadPreview(sel[0]);
          updateDetailsPane();
        }
      };

      window.addEventListener("mousemove", onDocMouseMove);
      window.addEventListener("mouseup", onDocMouseUp);

      elContent.onclick = () => {
        if (marqueeMoved) { marqueeMoved = false; return; }
        closeCtxMenu();
        selected.clear();
        renderItems();
      };
      elContent.oncontextmenu = (e) => {
        e.preventDefault();
        openContextMenu(e.clientX, e.clientY, null);
      };

      // Drag & Drop desde el PC para subir archivos
      elContent.ondragover = (e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          elContent.classList.add("dragover");
        }
      };
      elContent.ondragleave = () => elContent.classList.remove("dragover");
      elContent.ondrop = (e) => {
        if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
        e.preventDefault();
        elContent.classList.remove("dragover");
        uploadFiles(e.dataTransfer.files);
      };

      // Atajos de teclado Windows 7 cuando la ventana tiene foco
      const onGlobalClick = () => closeCtxMenu();
      const onKeyDown = (e) => {
        const win = container.closest(".win");
        if (!win || !win.classList.contains("focused")) return;
        if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;

        if (e.key === "F2") { e.preventDefault(); doRename(); }
        else if (e.key === "Delete") { e.preventDefault(); doDelete(); }
        else if (e.key === "F5") { e.preventDefault(); navigate(currentDir, false); }
        else if (e.key === "Backspace") { e.preventDefault(); elUp.click(); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") { e.preventDefault(); setClipboard("copy"); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "x") { e.preventDefault(); setClipboard("cut"); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") { e.preventDefault(); doPaste(); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
          e.preventDefault();
          selected = new Set(entries.map((it) => joinPath(currentDir, it.name)));
          renderItems();
        }
      };

      window.addEventListener("click", onGlobalClick);
      window.addEventListener("keydown", onKeyDown);

      navigate("/");

      return () => {
        closeCtxMenu();
        window.removeEventListener("click", onGlobalClick);
        window.removeEventListener("keydown", onKeyDown);
        window.removeEventListener("mousemove", onDocMouseMove);
        window.removeEventListener("mouseup", onDocMouseUp);
      };
    },
  });
})(window);

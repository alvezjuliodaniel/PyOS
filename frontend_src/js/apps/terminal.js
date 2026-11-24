/**
 * MicroOS App: Terminal Nativa estilo Linux / Windows (pantalla continua inline)
 * Prompt inline en la ultima linea, sin barras ni botones artificiales.
 * Soporta comandos Linux + alias Windows (dir, cls, type, del), Tab-autocomplete,
 * Ctrl+L (limpiar), Ctrl+C (cancelar / detener dmesg -f), Ctrl+D / exit() para Python REPL.
 */
(function (global) {
  "use strict";

  global.MicroOSApps = global.MicroOSApps || [];

  const BUILTIN_CMDS = [
    "help", "history", "ls", "dir", "cd", "pwd", "cat", "type", "head", "wc", "nano",
    "mkdir", "touch", "rm", "del", "cp", "copy", "mv", "move", "echo",
    "uname", "free", "df", "ps", "uptime", "dmesg", "ifconfig", "ip",
    "iwlist", "gpio", "gc", "reboot", "python", "py", "whoami", "hostname",
    "date", "clear", "cls", "exit",
  ];

  function parseRedirection(rawLine) {
    // Detecta '>>' o '>' fuera de comillas (ej. 'ls -l > out.txt', '> vacio.txt', 'echo hi >> log.txt')
    let quote = null;
    for (let i = 0; i < rawLine.length; i++) {
      const ch = rawLine[i];
      if (quote) {
        if (ch === quote) quote = null;
      } else if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === ">") {
        const append = rawLine[i + 1] === ">";
        const cmdPart = rawLine.slice(0, i).trim();
        const filePart = rawLine.slice(i + (append ? 2 : 1)).trim().replace(/^["']|["']$/g, "");
        return { cmdPart, filePart, append };
      }
    }
    return { cmdPart: rawLine.trim(), filePart: null, append: false };
  }

  function resolvePath(cwd, target) {
    if (!target || target === ".") return cwd;
    const raw = target.startsWith("/") ? target : `${cwd.replace(/\/+$/, "")}/${target}`;
    const parts = [];
    for (const seg of raw.split("/")) {
      if (!seg || seg === ".") continue;
      if (seg === "..") parts.pop();
      else parts.push(seg);
    }
    return "/" + parts.join("/");
  }

  function splitArgs(line) {
    const out = [];
    let cur = "";
    let quote = null;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (quote) {
        if (ch === quote) quote = null;
        else cur += ch;
      } else if (ch === '"' || ch === "'") {
        quote = ch;
      } else if (ch === " " || ch === "\t") {
        if (cur) { out.push(cur); cur = ""; }
      } else {
        cur += ch;
      }
    }
    if (cur) out.push(cur);
    return out;
  }

  global.MicroOSApps.push({
    id: "terminal",
    title: "Terminal",
    icon: "💻",
    width: 680,
    height: 430,
    mount(container, ctx) {
      const { client, wm } = global.MicroOS;
      const HIST_KEY = "microos_term_history";
      const MAX_HIST = 100;
      let history = [];
      try {
        const saved = JSON.parse(localStorage.getItem(HIST_KEY) || "[]");
        if (Array.isArray(saved)) history = saved.slice(-MAX_HIST);
      } catch {}
      let histIdx = history.length;
      let draftLine = "";
      let cwd = "/";
      let mode = "sh"; // 'sh' | 'py'

      function saveHistory() {
        try { localStorage.setItem(HIST_KEY, JSON.stringify(history.slice(-MAX_HIST))); } catch {}
      }

      function pushHistory(cmd) {
        if (!cmd) return;
        if (history[history.length - 1] !== cmd) {
          history.push(cmd);
          if (history.length > MAX_HIST) history.shift();
          saveHistory();
        }
        histIdx = history.length;
        draftLine = "";
      }
      let followLogs = false;
      let busy = false;
      let captureBuf = null; // Buffer temporal cuando se usa '>' o '>>'

      // Pantalla negra continua ocupando el 100% del cuerpo de la ventana
      container.style.padding = "0";
      container.style.overflow = "hidden";
      container.style.position = "relative";
      container.innerHTML = `
        <div id="term-screen" class="mono" style="width:100%;height:100%;overflow:auto;background:#0c0c0c;color:#e5e7eb;padding:10px 12px;font-size:12.5px;line-height:1.45;cursor:text;">
          <div id="term-hist" style="white-space:pre-wrap;word-break:break-word;"></div>
          <div id="term-line" style="display:flex;align-items:center;gap:0;">
            <span id="term-prompt" style="color:#22c55e;font-weight:700;white-space:pre;flex-shrink:0;"></span>
            <input id="term-in" class="mono" autocomplete="off" spellcheck="false" style="flex:1;min-width:40px;background:transparent;border:none;outline:none;color:#f8fafc;font-size:12.5px;line-height:1.45;padding:0;margin:0;caret-color:#38bdf8;" />
          </div>
        </div>
        <div id="term-nano" class="mono" style="display:none;position:absolute;inset:0;background:#0c0c0c;color:#e5e7eb;flex-direction:column;font-size:12.5px;">
          <div id="nano-hdr" style="background:#e5e7eb;color:#0c0c0c;padding:2px 8px;display:flex;justify-content:space-between;font-weight:700;flex-shrink:0;">
            <span>MicroOS nano 1.0</span>
            <span id="nano-file"></span>
            <span id="nano-mod"></span>
          </div>
          <textarea id="nano-ed" class="mono" spellcheck="false" style="flex:1;width:100%;background:#0c0c0c;color:#f8fafc;border:none;outline:none;resize:none;padding:8px 10px;font-size:12.5px;line-height:1.45;tab-size:4;"></textarea>
          <div id="nano-msg" style="text-align:center;min-height:18px;font-size:11.5px;color:#fbbf24;padding:1px 6px;flex-shrink:0;"></div>
          <div style="display:flex;flex-wrap:wrap;gap:10px;padding:4px 8px;border-top:1px solid #262626;font-size:11.5px;flex-shrink:0;">
            <span id="nano-btn-save" style="cursor:pointer;"><b style="background:#e5e7eb;color:#0c0c0c;padding:0 3px;">^O</b> Guardar</span>
            <span id="nano-btn-exit" style="cursor:pointer;"><b style="background:#e5e7eb;color:#0c0c0c;padding:0 3px;">^X</b> Salir</span>
            <span id="nano-btn-cut" style="cursor:pointer;"><b style="background:#e5e7eb;color:#0c0c0c;padding:0 3px;">^K</b> Cortar linea</span>
            <span id="nano-btn-find" style="cursor:pointer;"><b style="background:#e5e7eb;color:#0c0c0c;padding:0 3px;">^W</b> Buscar</span>
          </div>
        </div>
      `;

      const screenEl = container.querySelector("#term-screen");
      const histEl = container.querySelector("#term-hist");
      const lineEl = container.querySelector("#term-line");
      const promptEl = container.querySelector("#term-prompt");
      const inEl = container.querySelector("#term-in");
      const nanoEl = container.querySelector("#term-nano");
      const nanoFileEl = container.querySelector("#nano-file");
      const nanoModEl = container.querySelector("#nano-mod");
      const nanoEd = container.querySelector("#nano-ed");
      const nanoMsg = container.querySelector("#nano-msg");

      let nanoPath = null;
      let nanoDirty = false;

      async function openNano(filePath) {
        nanoPath = filePath;
        nanoDirty = false;
        nanoFileEl.textContent = filePath;
        nanoModEl.textContent = "";
        nanoMsg.textContent = "";
        try {
          const res = await client.call("fs", "read", { path: filePath, max_bytes: 8192 });
          nanoEd.value = res.content;
          const lines = res.content ? res.content.split("\n").length : 0;
          nanoMsg.textContent = `[ Leidas ${lines} lineas ]`;
        } catch {
          nanoEd.value = "";
          nanoMsg.textContent = "[ Archivo nuevo ]";
        }
        nanoEl.style.display = "flex";
        ctx.setTitle(`nano — ${filePath}`);
        nanoEd.focus();
      }

      async function saveNano() {
        if (!nanoPath) return;
        try {
          const res = await client.call("fs", "write", { path: nanoPath, content: nanoEd.value });
          nanoDirty = false;
          nanoModEl.textContent = "";
          const lines = nanoEd.value ? nanoEd.value.split("\n").length : 0;
          nanoMsg.textContent = `[ Escritas ${lines} lineas (${res.written} B) ]`;
        } catch (err) {
          nanoMsg.textContent = `[ Error: ${err.message} ]`;
        }
      }

      function closeNano() {
        if (nanoDirty && !confirm(`¿Descartar cambios sin guardar en ${nanoPath}?`)) return;
        nanoEl.style.display = "none";
        nanoPath = null;
        nanoDirty = false;
        updatePrompt();
        inEl.focus();
      }

      function cutNanoLine() {
        const v = nanoEd.value;
        const pos = nanoEd.selectionStart;
        const start = v.lastIndexOf("\n", pos - 1) + 1;
        let end = v.indexOf("\n", pos);
        if (end === -1) end = v.length; else end += 1;
        nanoEd.value = v.slice(0, start) + v.slice(end);
        nanoEd.selectionStart = nanoEd.selectionEnd = start;
        nanoDirty = true;
        nanoModEl.textContent = "Modificado";
      }

      function findInNano() {
        const q = prompt("Buscar texto:");
        if (!q) return;
        const idx = nanoEd.value.indexOf(q, nanoEd.selectionEnd);
        const found = idx !== -1 ? idx : nanoEd.value.indexOf(q);
        if (found !== -1) {
          nanoEd.focus();
          nanoEd.setSelectionRange(found, found + q.length);
          nanoMsg.textContent = `[ Encontrado en posicion ${found} ]`;
        } else {
          nanoMsg.textContent = `[ No encontrado: "${q}" ]`;
        }
      }

      nanoEd.addEventListener("input", () => {
        if (!nanoDirty) { nanoDirty = true; nanoModEl.textContent = "Modificado"; }
      });

      nanoEd.addEventListener("keydown", (e) => {
        if (e.key === "Tab") {
          e.preventDefault();
          const s = nanoEd.selectionStart, end = nanoEd.selectionEnd;
          nanoEd.value = nanoEd.value.slice(0, s) + "    " + nanoEd.value.slice(end);
          nanoEd.selectionStart = nanoEd.selectionEnd = s + 4;
          nanoDirty = true; nanoModEl.textContent = "Modificado";
        } else if (e.ctrlKey && (e.key.toLowerCase() === "o" || e.key.toLowerCase() === "s")) {
          e.preventDefault(); saveNano();
        } else if ((e.ctrlKey && e.key.toLowerCase() === "x") || e.key === "Escape") {
          e.preventDefault(); closeNano();
        } else if (e.ctrlKey && e.key.toLowerCase() === "k") {
          e.preventDefault(); cutNanoLine();
        } else if (e.ctrlKey && e.key.toLowerCase() === "w") {
          e.preventDefault(); findInNano();
        }
      });

      container.querySelector("#nano-btn-save").onclick = saveNano;
      container.querySelector("#nano-btn-exit").onclick = closeNano;
      container.querySelector("#nano-btn-cut").onclick = cutNanoLine;
      container.querySelector("#nano-btn-find").onclick = findInNano;

      function getHost() {
        return (global.MicroOS.boardInfo && global.MicroOS.boardInfo.hostname) || "microos";
      }

      function updatePrompt() {
        if (mode === "py") {
          promptEl.textContent = ">>> ";
          promptEl.style.color = "#38bdf8";
          ctx.setTitle("Terminal — Python REPL");
        } else {
          promptEl.textContent = `root@${getHost()}:${cwd}$ `;
          promptEl.style.color = "#22c55e";
          ctx.setTitle(`Terminal — ${cwd}`);
        }
      }

      function scrollBottom() {
        screenEl.scrollTop = screenEl.scrollHeight;
      }

      function print(text, color = "") {
        if (captureBuf !== null) {
          captureBuf.push(String(text));
          return;
        }
        const div = document.createElement("div");
        if (color) div.style.color = color;
        div.textContent = text;
        histEl.appendChild(div);
        while (histEl.childNodes.length > 400) histEl.removeChild(histEl.firstChild);
        scrollBottom();
      }

      function printPromptEcho(cmdText) {
        const div = document.createElement("div");
        const p = document.createElement("span");
        p.style.color = mode === "py" ? "#38bdf8" : "#22c55e";
        p.style.fontWeight = "700";
        p.textContent = promptEl.textContent;
        const c = document.createElement("span");
        c.textContent = cmdText;
        div.appendChild(p);
        div.appendChild(c);
        histEl.appendChild(div);
        scrollBottom();
      }

      function printLog(entry) {
        if (!entry) return;
        const col = entry.lvl === "ERR" ? "#f87171" : entry.lvl === "WARN" ? "#fbbf24" : "#94a3b8";
        print(`[${entry.t}s][${entry.lvl}][${entry.tag}] ${entry.msg}`, col);
      }

      async function execPython(code) {
        if (code === "exit()" || code === "quit()") {
          mode = "sh";
          updatePrompt();
          return;
        }
        const res = await client.call("term", "exec", { code });
        if (res.output) print(res.output);
        if (res.result !== null && res.result !== undefined) print(res.result, "#86efac");
      }

      async function execLinux(rawLine) {
        const { cmdPart, filePart, append } = parseRedirection(rawLine);

        // '> archivo.txt' sin comando previo: crea archivo vacio (o trunca)
        if (filePart && !cmdPart) {
          const dest = resolvePath(cwd, filePart);
          if (!append) {
            await client.call("fs", "write", { path: dest, content: "" });
          } else {
            let prev = "";
            try { prev = (await client.call("fs", "read", { path: dest, max_bytes: 8192 })).content; } catch {}
            await client.call("fs", "write", { path: dest, content: prev });
          }
          return;
        }

        if (filePart) {
          captureBuf = [];
          try {
            await execLinuxCmd(cmdPart);
            const outText = captureBuf.join("\n") + (captureBuf.length ? "\n" : "");
            const dest = resolvePath(cwd, filePart);
            let finalText = outText;
            if (append) {
              try {
                const prev = (await client.call("fs", "read", { path: dest, max_bytes: 8192 })).content;
                finalText = prev + outText;
              } catch {}
            }
            await client.call("fs", "write", { path: dest, content: finalText });
          } finally {
            captureBuf = null;
          }
          return;
        }

        await execLinuxCmd(cmdPart);
      }

      async function execLinuxCmd(rawLine) {
        const tokens = splitArgs(rawLine);
        if (!tokens.length) return;
        let cmd = tokens[0];
        const args = tokens.slice(1);

        // Alias Windows -> Linux
        const aliases = { dir: "ls", cls: "clear", type: "cat", del: "rm", copy: "cp", move: "mv" };
        if (aliases[cmd]) cmd = aliases[cmd];

        if (cmd === "nano") {
          if (!args[0]) throw new Error("Uso: nano <archivo>");
          await openNano(resolvePath(cwd, args[0]));
          return;
        }
        if (cmd === "exit") { wm.close(ctx.winId); return; }
        if (cmd === "history") {
          if (args[0] === "-c") {
            history = [];
            histIdx = 0;
            saveHistory();
            print("Historial limpiado.", "#94a3b8");
            return;
          }
          history.forEach((h, idx) => print(`${String(idx + 1).padStart(4)}  ${h}`));
          return;
        }
        if (cmd === "clear") { histEl.innerHTML = ""; return; }
        if (cmd === "pwd") { print(cwd); return; }
        if (cmd === "whoami") { print("root"); return; }
        if (cmd === "date") { print(new Date().toString()); return; }
        if (cmd === "echo") { print(args.join(" ")); return; }
        if (cmd === "help") {
          print(
            "Archivos : ls [-l] (dir), cd <dir>, pwd, cat <f> (type), head <f>, wc <f>, nano <f>,\n" +
            "           mkdir <d>, touch <f>, rm [-r] <p> (del), cp/mv <a> <b>, cmd > f, cmd >> f, > f\n" +
            "Sistema  : uname [-a], free, df, ps, uptime, dmesg [-f], gc, reboot, clear (cls), exit\n" +
            "Red/HW   : ifconfig | ip, iwlist, gpio <get|set|toggle|adc> <pin> [val]\n" +
            "Python   : python [expr]  (escribe 'exit()' o Ctrl+D para volver al shell)\n" +
            "Atajos   : Tab (autocompletar), Ctrl+L (limpiar), Ctrl+C (cancelar), ↑/↓ (historial)",
            "#94a3b8"
          );
          return;
        }
        if (cmd === "hostname") {
          const i = await client.call("sys", "info");
          print(i.hostname || "microos");
          return;
        }
        if (cmd === "python" || cmd === "python3" || cmd === "py") {
          if (!args.length) {
            mode = "py";
            print("MicroPython REPL (exit() o Ctrl+D para volver al shell)", "#94a3b8");
            updatePrompt();
            return;
          }
          if (args[0] === "-c") {
            const inlineCode = args.slice(1).join(" ");
            await execPython(inlineCode);
            return;
          }
          if (args[0].endsWith(".py")) {
            const target = resolvePath(cwd, args[0]);
            const res = await client.call("term", "run_file", { path: target, argv: args.slice(1) }, 15000);
            if (res.output) print(res.output);
            return;
          }
          const code = rawLine.slice(tokens[0].length).trim();
          await execPython(code);
          return;
        }
        if (cmd === "cd") {
          const target = resolvePath(cwd, args[0] || "/");
          await client.call("fs", "ls", { path: target });
          cwd = target;
          updatePrompt();
          return;
        }
        if (cmd === "ls") {
          const longFmt = args.includes("-l") || args.includes("-la") || args.includes("-al");
          const pathArg = args.find((a) => !a.startsWith("-")) || ".";
          const target = resolvePath(cwd, pathArg);
          const res = await client.call("fs", "ls", { path: target });
          if (longFmt) {
            for (const e of res.entries) {
              const perm = e.type === "dir" ? "drwxr-xr-x" : "-rw-r--r--";
              const sz = String(e.size || 0).padStart(7, " ");
              print(`${perm}  ${sz}  ${e.name}${e.type === "dir" ? "/" : ""}`, e.type === "dir" ? "#38bdf8" : "#e5e7eb");
            }
          } else {
            print(res.entries.map((e) => (e.type === "dir" ? e.name + "/" : e.name)).join("   ") || "");
          }
          return;
        }
        if (cmd === "cat" || cmd === "head" || cmd === "wc") {
          if (!args[0]) throw new Error(`Uso: ${cmd} <archivo>`);
          const target = resolvePath(cwd, args[0]);
          const res = await client.call("fs", "read", { path: target, max_bytes: 8192 });
          if (cmd === "cat") print(res.content);
          else if (cmd === "head") print(res.content.split("\n").slice(0, 15).join("\n"));
          else {
            const lines = res.content ? res.content.split("\n").length : 0;
            const words = res.content.trim() ? res.content.trim().split(/\s+/).length : 0;
            print(`  ${lines}   ${words}   ${res.content.length} ${target}`);
          }
          return;
        }
        if (cmd === "mkdir") {
          if (!args[0]) throw new Error("Uso: mkdir <carpeta>");
          await client.call("fs", "mkdir", { path: resolvePath(cwd, args[0]) });
          return;
        }
        if (cmd === "touch") {
          if (!args[0]) throw new Error("Uso: touch <archivo>");
          await client.call("fs", "write", { path: resolvePath(cwd, args[0]), content: "" });
          return;
        }
        if (cmd === "rm") {
          const rec = args.includes("-r") || args.includes("-rf");
          const p = args.find((a) => !a.startsWith("-"));
          if (!p) throw new Error("Uso: rm [-r] <ruta>");
          await client.call("fs", "rm", { path: resolvePath(cwd, p), recursive: rec });
          return;
        }
        if (cmd === "cp" || cmd === "mv") {
          if (args.length < 2) throw new Error(`Uso: ${cmd} <origen> <destino>`);
          await client.call("fs", cmd, { src: resolvePath(cwd, args[0]), dst: resolvePath(cwd, args[1]) });
          return;
        }
        if (cmd === "uname") {
          const i = await client.call("sys", "info");
          print(args.includes("-a")
            ? `MicroOS ${i.os_version} ${i.hostname} ${i.name} ${i.arch} ${i.cores}c MicroPython-${i.mpy_version}`
            : `MicroOS`);
          return;
        }
        if (cmd === "free") {
          const s = await client.call("sys", "stats");
          const m = s.mem;
          print("              total        used        free");
          print(`Mem:   ${String(m.total).padStart(10)}  ${String(m.alloc).padStart(10)}  ${String(m.free).padStart(10)}  (${m.used_pct}%)`);
          return;
        }
        if (cmd === "df") {
          const s = await client.call("sys", "stats");
          const f = s.flash;
          print("Filesystem     Size      Used     Avail   Use%  Mounted on");
          print(`littlefs   ${String(Math.round(f.total / 1024) + "K").padStart(8)}  ${String(Math.round(f.used / 1024) + "K").padStart(8)}  ${String(Math.round(f.free / 1024) + "K").padStart(8)}   ${String(f.used_pct + "%").padStart(5)}  /`);
          return;
        }
        if (cmd === "ps") {
          const tasks = await client.call("sys", "tasks");
          print("TASK               STATE       CRIT   RESTARTS");
          for (const t of tasks) print(`${t.name.padEnd(18)} ${t.state.padEnd(11)} ${(t.critical ? "yes" : "no").padEnd(6)} ${t.restarts}`);
          return;
        }
        if (cmd === "uptime") {
          const s = await client.call("sys", "stats");
          print(`up ${s.uptime_s}s | ${s.cpu_mhz} MHz | load ${s.cpu ? s.cpu.load_pct : 0}% | temp ${s.temp_c ?? "N/A"} C`);
          return;
        }
        if (cmd === "dmesg") {
          const logs = await client.call("sys", "logs", { limit: 32 });
          for (const l of logs) printLog(l);
          if (args.includes("-f")) {
            followLogs = true;
            print("-- Siguiendo logs en vivo (pulsa Ctrl+C para detener) --", "#fbbf24");
          }
          return;
        }
        if (cmd === "ifconfig" || cmd === "ip") {
          const n = await client.call("net", "status");
          print(`wlan0 (${n.mode.toUpperCase()}): ${n.connected ? "UP" : "DOWN"}  ssid="${n.ssid}"\n  inet ${n.ip}  netmask ${n.netmask}  gw ${n.gateway}\n  ether ${n.mac || "--"}  host ${n.hostname}.local  rssi ${n.rssi ?? "N/A"} dBm`);
          return;
        }
        if (cmd === "iwlist") {
          print("Escaneando redes 2.4 GHz...", "#94a3b8");
          const nets = await client.call("net", "scan", {}, 12000);
          for (const n of nets) print(`  ${String(n.rssi).padStart(4)} dBm  ${n.auth ? "WPA" : "OPEN"}  ${n.ssid}`);
          return;
        }
        if (cmd === "gpio") {
          const sub = args[0];
          const pin = Number(args[1]);
          if (!sub || Number.isNaN(pin)) throw new Error("Uso: gpio <get|set|toggle|adc> <pin> [val]");
          const r = await client.call("gpio", sub, sub === "set" ? { pin, val: Number(args[2] || 0) } : { pin });
          print(JSON.stringify(r));
          return;
        }
        if (cmd === "gc") {
          const m = await client.call("sys", "gc");
          print(`GC OK: ${m.free} B libres (${Math.round(m.free / 1024)} KB)`, "#86efac");
          return;
        }
        if (cmd === "reboot") {
          await client.call("sys", "reboot");
          print("Reiniciando ESP32...", "#fbbf24");
          return;
        }
        throw new Error(`sh: ${cmd}: comando no encontrado`);
      }

      async function handleTab() {
        if (mode !== "sh") return;
        const val = inEl.value;
        const parts = val.split(" ");
        if (parts.length === 1) {
          const matches = BUILTIN_CMDS.filter((c) => c.startsWith(parts[0]));
          if (matches.length === 1) inEl.value = matches[0] + " ";
          else if (matches.length > 1) {
            printPromptEcho(val);
            print(matches.join("  "), "#94a3b8");
          }
          return;
        }
        const last = parts[parts.length - 1];
        try {
          const res = await client.call("fs", "ls", { path: cwd });
          const matches = res.entries.filter((e) => e.name.startsWith(last));
          if (matches.length === 1) {
            parts[parts.length - 1] = matches[0].name + (matches[0].type === "dir" ? "/" : "");
            inEl.value = parts.join(" ");
          } else if (matches.length > 1) {
            printPromptEcho(val);
            print(matches.map((m) => m.name + (m.type === "dir" ? "/" : "")).join("  "), "#94a3b8");
          }
        } catch {}
      }

      // Clic en cualquier parte de la pantalla negra enfoca el cursor inline
      screenEl.addEventListener("click", () => {
        if (!window.getSelection().toString()) inEl.focus();
      });

      inEl.addEventListener("keydown", async (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (busy) return;
          const line = inEl.value.trim();
          printPromptEcho(inEl.value);
          inEl.value = "";
          if (!line) return;
          pushHistory(line);

          busy = true;
          lineEl.style.visibility = "hidden";
          try {
            if (mode === "py") await execPython(line);
            else await execLinux(line);
          } catch (err) {
            print(err.message, "#f87171");
          } finally {
            busy = false;
            lineEl.style.visibility = "visible";
            scrollBottom();
            if (nanoEl.style.display !== "flex") inEl.focus();
          }
        } else if (e.key === "ArrowUp") {
          if (history.length && histIdx > 0) {
            if (histIdx === history.length) draftLine = inEl.value;
            histIdx--;
            inEl.value = history[histIdx];
            requestAnimationFrame(() => inEl.setSelectionRange(inEl.value.length, inEl.value.length));
          }
          e.preventDefault();
        } else if (e.key === "ArrowDown") {
          if (histIdx < history.length - 1) {
            histIdx++;
            inEl.value = history[histIdx];
          } else if (histIdx === history.length - 1) {
            histIdx = history.length;
            inEl.value = draftLine;
          }
          requestAnimationFrame(() => inEl.setSelectionRange(inEl.value.length, inEl.value.length));
          e.preventDefault();
        } else if (e.key === "Tab") {
          e.preventDefault();
          handleTab();
        } else if (e.ctrlKey && e.key.toLowerCase() === "l") {
          e.preventDefault();
          histEl.innerHTML = "";
        } else if (e.ctrlKey && e.key.toLowerCase() === "c") {
          e.preventDefault();
          printPromptEcho(inEl.value + "^C");
          inEl.value = "";
          if (followLogs) {
            followLogs = false;
            print("-- Logs en vivo detenidos --", "#94a3b8");
          }
        } else if (e.ctrlKey && e.key.toLowerCase() === "d") {
          e.preventDefault();
          if (mode === "py") {
            mode = "sh";
            print("");
            updatePrompt();
          }
        }
      });

      const unsubLog = client.on("log", (entry) => { if (followLogs) printLog(entry); });
      print("MicroOS Terminal (Linux/Windows). Escribe 'help' o 'python'.", "#94a3b8");
      updatePrompt();
      inEl.focus();

      return () => unsubLog();
    },
  });
})(window);

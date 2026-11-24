/**
 * MicroOS App: Gestor de Aplicaciones Dinamicas (Instalar / Desinstalar en caliente)
 * Sin modificar el firmware MicroPython ni reiniciar la placa.
 */
(function (global) {
  "use strict";

  global.MicroOSApps = global.MicroOSApps || [];

  // Plantillas listas para instalar con 1 clic como demostracion
  const TEMPLATES = {
    timer: {
      manifest: {
        id: "cronometro",
        name: "Cronometro",
        version: "1.0.0",
        icon: "⏱️",
        description: "Cronometro ligero en tiempo real",
      },
      backend: "",
      ui: `(function(){
  MicroOS.wm.registerApp({
    id:"cronometro", title:"Cronometro", icon:"⏱️", width:300, height:210,
    mount(c){
      let ms=0, t=null;
      c.innerHTML='<div class="card" style="text-align:center;"><div id="tm-v" class="mono" style="font-size:32px;font-weight:700;margin:10px 0;">00:00.0</div><div style="display:flex;gap:8px;justify-content:center;"><button class="btn ok" id="tm-s">Iniciar</button><button class="btn" id="tm-p">Pausar</button><button class="btn danger" id="tm-r">Cero</button></div></div>';
      const v=c.querySelector("#tm-v");
      const draw=()=>{const m=String(Math.floor(ms/60000)).padStart(2,"0"),s=String(Math.floor((ms%60000)/1000)).padStart(2,"0"),d=Math.floor((ms%1000)/100);v.textContent=m+":"+s+"."+d;};
      c.querySelector("#tm-s").onclick=()=>{if(!t)t=setInterval(()=>{ms+=100;draw();},100);};
      c.querySelector("#tm-p").onclick=()=>{clearInterval(t);t=null;};
      c.querySelector("#tm-r").onclick=()=>{clearInterval(t);t=null;ms=0;draw();};
      return ()=>clearInterval(t);
    }
  });
})();`,
    },
    blink: {
      manifest: {
        id: "blink",
        name: "Blink LED",
        version: "1.0.0",
        icon: "💡",
        description: "Controla un LED en cualquier GPIO seguro (UI + Backend Python)",
      },
      backend: `def rpc(cmd, args, ctx):
    hal = ctx["hal"]
    if cmd == "pulse":
        pin = int(args.get("pin", hal.get_board_info()["pins"]["safe_io"][0]))
        val = hal.gpio_toggle(pin)
        return {"pin": pin, "val": val}
    if cmd == "pins":
        return hal.get_board_info()["pins"]["safe_io"]
    raise ValueError("cmd desconocido")
`,
      ui: `(function(){
  MicroOS.wm.registerApp({
    id:"blink", title:"Blink LED", icon:"💡", width:320, height:220,
    mount(c){
      const {client, toast} = MicroOS;
      c.innerHTML='<div class="card" style="display:flex;flex-direction:column;gap:10px;"><label>GPIO seguro: <select id="bl-p" class="input mono"></select></label><button class="btn primary" id="bl-t" style="padding:10px;font-size:14px;">⚡ Alternar LED</button><div id="bl-s" class="mono muted">Listo</div></div>';
      const sel=c.querySelector("#bl-p"), st=c.querySelector("#bl-s");
      client.call("blink","pins").then(pins=>{sel.innerHTML=pins.map(p=>"<option value="+p+">GPIO "+p+"</option>").join("");}).catch(()=>{});
      c.querySelector("#bl-t").onclick=async()=>{
        try{const r=await client.call("blink","pulse",{pin:Number(sel.value)});st.textContent="GPIO "+r.pin+" -> "+(r.val?"ENCENDIDO (1)":"APAGADO (0)");}
        catch(e){toast(e.message,"err");}
      };
    }
  });
})();`,
    },
  };

  global.MicroOSApps.push({
    id: "appstore",
    title: "Gestor de Apps",
    icon: "📦",
    width: 680,
    height: 480,
    mount(container) {
      const { client, wm, toast, escHtml } = global.MicroOS;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:10px;height:100%;">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;">
            <strong>Aplicaciones Instaladas en /apps/</strong>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <label class="btn primary" style="margin:0;">
                ⬆ Instalar .mapp.json
                <input type="file" id="as-file" accept=".json" style="display:none;" />
              </label>
              <button class="btn" id="as-tpl-timer">+ Demo Cronometro</button>
              <button class="btn" id="as-tpl-blink">+ Demo Blink (Py+JS)</button>
              <button class="btn" id="as-ref">↻</button>
            </div>
          </div>

          <div id="as-list" class="card" style="min-height:110px;max-height:170px;overflow:auto;display:flex;flex-direction:column;gap:6px;"></div>

          <div class="card" style="flex:1;display:flex;flex-direction:column;gap:6px;min-height:0;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <strong>Instalar / Crear App Personalizada en Vivo</strong>
              <button class="btn ok" id="as-install">Instalar en Caliente</button>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;">
              <input id="as-id" class="input mono" placeholder="id (ej. miapp)" style="width:120px;" />
              <input id="as-name" class="input" placeholder="Nombre visible" style="flex:1;min-width:120px;" />
              <input id="as-icon" class="input" value="🧩" style="width:48px;text-align:center;" />
              <input id="as-desc" class="input" placeholder="Descripcion corta" style="flex:1;min-width:140px;" />
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;flex:1;min-height:0;">
              <textarea id="as-ui" class="input mono" style="resize:none;font-size:11px;" spellcheck="false" placeholder="ui.js (opcional):&#10;MicroOS.wm.registerApp({ id:'miapp', title:'Mi App', icon:'🧩', mount(c){ c.innerHTML='Hola!'; } });"></textarea>
              <textarea id="as-be" class="input mono" style="resize:none;font-size:11px;" spellcheck="false" placeholder="backend.py (opcional):&#10;def rpc(cmd, args, ctx):&#10;    return {'ok': True}"></textarea>
            </div>
          </div>
        </div>
      `;

      const elList = container.querySelector("#as-list");

      async function refresh() {
        try {
          const apps = await client.call("apps", "list");
          elList.innerHTML = "";
          if (!apps.length) {
            elList.innerHTML = '<div class="muted">No hay apps dinamicas instaladas. Prueba "+ Demo Cronometro" o "+ Demo Blink".</div>';
            return;
          }
          for (const a of apps) {
            const row = document.createElement("div");
            row.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px;border-radius:6px;background:rgba(15,23,42,0.55);border:1px solid var(--border);";
            row.innerHTML = `
              <div style="display:flex;align-items:center;gap:8px;overflow:hidden;">
                <span style="font-size:20px;">${escHtml(a.icon)}</span>
                <div style="overflow:hidden;">
                  <div><strong>${escHtml(a.name)}</strong> <span class="mono muted" style="font-size:11px;">(${escHtml(a.id)} v${escHtml(a.version)})</span></div>
                  <div class="muted" style="font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    ${escHtml(a.description || "Sin descripcion")} · ${a.has_ui ? "UI" : ""}${a.has_ui && a.has_backend ? "+" : ""}${a.has_backend ? "Python" : ""}
                  </div>
                </div>
              </div>
              <div style="display:flex;gap:4px;flex-shrink:0;">
                ${a.has_ui ? '<button class="btn primary" data-act="open">Abrir</button>' : ""}
                <button class="btn" data-act="reload" title="Recargar">↻</button>
                <button class="btn danger" data-act="rm">Desinstalar</button>
              </div>
            `;
            const openBtn = row.querySelector('[data-act="open"]');
            if (openBtn) openBtn.onclick = () => wm.open(a.id);
            row.querySelector('[data-act="reload"]').onclick = async () => {
              try {
                await client.call("apps", "reload", { id: a.id });
                toast(`App '${a.name}' recargada`, "ok");
              } catch (e) { toast(e.message, "err"); }
            };
            row.querySelector('[data-act="rm"]').onclick = async () => {
              if (!confirm(`¿Desinstalar "${a.name}" (${a.id})?`)) return;
              try {
                await client.call("apps", "uninstall", { id: a.id });
                if (global.MicroOS.syncDynamicApps) await global.MicroOS.syncDynamicApps();
                toast(`App '${a.name}' desinstalada`, "ok");
                refresh();
              } catch (e) { toast(e.message, "err"); }
            };
            elList.appendChild(row);
          }
        } catch (e) {
          elList.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
        }
      }

      async function installPkg(pkg) {
        try {
          await client.call("apps", "install", pkg, 12000);
          if (global.MicroOS.syncDynamicApps) {
            await global.MicroOS.syncDynamicApps(pkg.manifest.id);
          }
          toast(`App '${pkg.manifest.name || pkg.manifest.id}' instalada en caliente`, "ok");
          refresh();
        } catch (e) {
          toast(e.message, "err");
        }
      }

      container.querySelector("#as-tpl-timer").onclick = () => installPkg(TEMPLATES.timer);
      container.querySelector("#as-tpl-blink").onclick = () => installPkg(TEMPLATES.blink);
      container.querySelector("#as-ref").onclick = refresh;

      container.querySelector("#as-file").onchange = async (e) => {
        const f = e.target.files[0];
        if (!f) return;
        try {
          const pkg = JSON.parse(await f.text());
          if (!pkg.manifest || !pkg.manifest.id) throw new Error("Falta manifest.id en el paquete .mapp.json");
          await installPkg(pkg);
        } catch (err) {
          toast(err.message, "err");
        } finally {
          e.target.value = "";
        }
      };

      container.querySelector("#as-install").onclick = () => {
        const id = container.querySelector("#as-id").value.trim().toLowerCase();
        const name = container.querySelector("#as-name").value.trim() || id;
        const icon = container.querySelector("#as-icon").value.trim() || "🧩";
        const description = container.querySelector("#as-desc").value.trim();
        const ui = container.querySelector("#as-ui").value;
        const backend = container.querySelector("#as-be").value;
        if (!id) return toast("Indica un ID para la app (ej. miapp)", "warn");
        installPkg({
          manifest: { id, name, icon, description, version: "1.0.0" },
          ui,
          backend,
        });
      };

      const unsub = client.on("apps_changed", () => refresh());
      refresh();
      return () => unsub();
    },
  });
})(window);

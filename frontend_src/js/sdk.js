/**
 * PyOS SDK v0.2.0 (Vanilla JS, preparado para compatibilidad AOS)
 * Expone PyOS.defineApp(...) con permisos declarados, APIs tipadas
 * (system, fs, gpio, net, mqtt, ui, events) y limpieza automatica de recursos.
 */
(function (global) {
  "use strict";

  function createScopedSDK(permsSet, addCleanup) {
    const { client, toast, escHtml } = global.PyOS;

    function check(cap) {
      if (permsSet.size > 0 && !permsSet.has(cap) && !permsSet.has("*")) {
        throw new Error(`Permiso '${cap}' no declarado en la aplicacion`);
      }
    }

    return {
      system: {
        info: () => { check("sys.read"); return client.call("sys", "info"); },
        stats: () => { check("sys.read"); return client.call("sys", "stats"); },
        tasks: () => { check("sys.read"); return client.call("sys", "tasks"); },
        logs: (limit = 32) => { check("sys.read"); return client.call("sys", "logs", { limit }); },
        gc: () => { check("sys.admin"); return client.call("sys", "gc"); },
      },
      fs: {
        list: (path = "/") => { check("fs.read"); return client.call("fs", "ls", { path }); },
        read: (path, max_bytes = 8192) => { check("fs.read"); return client.call("fs", "read", { path, max_bytes }); },
        write: (path, content) => { check("fs.write"); return client.call("fs", "write", { path, content }); },
        mkdir: (path) => { check("fs.write"); return client.call("fs", "mkdir", { path }); },
        remove: (path, recursive = false) => { check("fs.write"); return client.call("fs", "rm", { path, recursive }); },
        copy: (src, dst) => { check("fs.write"); return client.call("fs", "cp", { src, dst }); },
        move: (src, dst) => { check("fs.write"); return client.call("fs", "mv", { src, dst }); },
      },
      gpio: {
        info: () => { check("gpio.read"); return client.call("gpio", "info"); },
        read: (pin) => { check("gpio.read"); return client.call("gpio", "get", { pin }); },
        adc: (pin) => { check("gpio.read"); return client.call("gpio", "adc", { pin }); },
        write: (pin, val) => { check("gpio.write"); return client.call("gpio", "set", { pin, val }); },
        toggle: (pin) => { check("gpio.write"); return client.call("gpio", "toggle", { pin }); },
        pwm: (pin, freq = 1000, duty = 50) => { check("gpio.write"); return client.call("gpio", "pwm", { pin, freq, duty }); },
        pwmStop: (pin) => { check("gpio.write"); return client.call("gpio", "pwm_stop", { pin }); },
      },
      net: {
        status: () => { check("sys.read"); return client.call("net", "status"); },
        scan: () => { check("sys.admin"); return client.call("net", "scan", {}, 12000); },
      },
      mqtt: {
        status: () => { check("mqtt.pubsub"); return client.call("mqtt", "status"); },
        publish: (topic, payload, opts = {}) => {
          check("mqtt.pubsub");
          return client.call("mqtt", "publish", {
            topic,
            payload,
            qos: opts.qos || 0,
            retain: Boolean(opts.retain),
          }, 10000);
        },
        subscribe: (topic, qos = 0) => {
          check("mqtt.pubsub");
          return client.call("mqtt", "subscribe", { topic, qos });
        },
      },
      events: {
        on(eventName, fn) {
          const unsub = client.on(eventName, fn);
          addCleanup(unsub);
          return unsub;
        },
      },
      ui: {
        toast,
        escHtml,
      },
    };
  }

  function defineApp(spec) {
    if (!spec || !spec.id) throw new Error("defineApp requiere 'id'");
    const perms = new Set(Array.isArray(spec.permissions) ? spec.permissions : ["*"]);

    global.PyOS.wm.registerApp({
      id: spec.id,
      title: spec.title || spec.id,
      icon: spec.icon || "🧩",
      width: spec.width || 460,
      height: spec.height || 340,
      singleton: spec.singleton !== false,
      mount(container, baseCtx) {
        const cleanups = [];
        const addCleanup = (fn) => { if (typeof fn === "function") cleanups.push(fn); };
        const runCleanups = () => {
          while (cleanups.length) {
            const fn = cleanups.pop();
            try { fn(); } catch {}
          }
        };

        const sdk = createScopedSDK(perms, addCleanup);
        const appCtx = Object.assign({}, baseCtx, {
          sdk,
          onCleanup: addCleanup,
          setInterval(fn, ms) {
            const id = global.setInterval(fn, ms);
            addCleanup(() => global.clearInterval(id));
            return id;
          },
        });

        try {
          const ret = spec.mount(container, appCtx);
          if (typeof ret === "function") addCleanup(ret);
        } catch (err) {
          runCleanups();
          throw err;
        }
        return runCleanups;
      },
    });
  }

  // Enlazar al objeto PyOS cuando este disponible
  function attach() {
    if (global.PyOS) {
      global.PyOS.defineApp = defineApp;
    }
  }
  attach();
  global._attachPyOSSDK = attach;
})(window);

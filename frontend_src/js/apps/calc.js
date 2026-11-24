/**
 * MicroOS App: Calculadora (Estandar + Vista HEX/BIN para embebidos)
 */
(function (global) {
  "use strict";

  global.MicroOSApps = global.MicroOSApps || [];

  function safeEvalExpr(expr) {
    // Evaluador aritmetico puro por descenso recursivo (cero eval/Function - FE-02)
    const s = expr.replace(/\s+/g, "").replace(/×/g, "*").replace(/÷/g, "/").replace(/−/g, "-");
    if (!s || !/^[0-9+\-*/().%]+$/.test(s)) throw new Error("Expresion invalida");
    let pos = 0;

    function parseExpr() {
      let val = parseTerm();
      while (pos < s.length && (s[pos] === "+" || s[pos] === "-")) {
        const op = s[pos++];
        const r = parseTerm();
        val = op === "+" ? val + r : val - r;
      }
      return val;
    }

    function parseTerm() {
      let val = parseFactor();
      while (pos < s.length && (s[pos] === "*" || s[pos] === "/" || s[pos] === "%")) {
        const op = s[pos++];
        const r = parseFactor();
        if (op === "*") val *= r;
        else if (op === "/") val /= r;
        else val %= r;
      }
      return val;
    }

    function parseFactor() {
      if (s[pos] === "+") { pos++; return parseFactor(); }
      if (s[pos] === "-") { pos++; return -parseFactor(); }
      if (s[pos] === "(") {
        pos++;
        const v = parseExpr();
        if (s[pos] !== ")") throw new Error("Parentesis sin cerrar");
        pos++;
        return v;
      }
      const start = pos;
      while (pos < s.length && ((s[pos] >= "0" && s[pos] <= "9") || s[pos] === ".")) pos++;
      if (start === pos) throw new Error("Numero esperado");
      return Number(s.slice(start, pos));
    }

    const val = parseExpr();
    if (pos !== s.length || !Number.isFinite(val)) throw new Error("Error matematico");
    return Math.round(val * 1e10) / 1e10;
  }

  global.MicroOSApps.push({
    id: "calc",
    title: "Calculadora",
    icon: "🧮",
    width: 310,
    height: 410,
    mount(container) {
      let expr = "";
      let lastVal = 0;

      container.innerHTML = `
        <div style="display:flex;flex-direction:column;gap:8px;height:100%;">
          <div class="card" style="padding:10px;text-align:right;background:rgba(2,6,23,0.75);">
            <div id="c-expr" class="mono muted" style="font-size:12px;min-height:16px;overflow:hidden;text-overflow:ellipsis;">&nbsp;</div>
            <div id="c-disp" class="mono" style="font-size:24px;font-weight:700;overflow:hidden;text-overflow:ellipsis;">0</div>
            <div id="c-base" class="mono muted" style="font-size:10px;margin-top:4px;">HEX: 0x0 · BIN: 0b0</div>
          </div>

          <div id="c-keys" style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;flex:1;"></div>
        </div>
      `;

      const elExpr = container.querySelector("#c-expr");
      const elDisp = container.querySelector("#c-disp");
      const elBase = container.querySelector("#c-base");
      const elKeys = container.querySelector("#c-keys");

      const keys = [
        ["C", "danger"], ["(", ""], [")", ""], ["⌫", ""],
        ["x²", ""], ["√", ""], ["%", ""], ["÷", "primary"],
        ["7", ""], ["8", ""], ["9", ""], ["×", "primary"],
        ["4", ""], ["5", ""], ["6", ""], ["−", "primary"],
        ["1", ""], ["2", ""], ["3", ""], ["+", "primary"],
        ["±", ""], ["0", ""], [".", ""], ["=", "ok"],
      ];

      function updateBase(n) {
        if (Number.isInteger(n) && Math.abs(n) <= 0xFFFFFFFF) {
          const u = n >>> 0;
          elBase.textContent = `HEX: 0x${u.toString(16).toUpperCase()} · BIN: 0b${u.toString(2)}`;
        } else {
          elBase.textContent = "HEX/BIN disponible para enteros";
        }
      }

      function render(displayText) {
        elExpr.textContent = expr || "\u00A0";
        elDisp.textContent = displayText !== undefined ? String(displayText) : (expr || "0");
      }

      function press(k) {
        if (k === "C") {
          expr = "";
          lastVal = 0;
          render("0");
          updateBase(0);
          return;
        }
        if (k === "⌫") {
          expr = expr.slice(0, -1);
          render();
          return;
        }
        if (k === "±") {
          if (!expr) return;
          expr = expr.startsWith("-") ? expr.slice(1) : "-" + expr;
          render();
          return;
        }
        if (k === "x²" || k === "√") {
          try {
            const v = expr ? safeEvalExpr(expr) : lastVal;
            const r = k === "x²" ? v * v : Math.sqrt(v);
            if (!Number.isFinite(r)) throw new Error("Error");
            lastVal = Math.round(r * 1e10) / 1e10;
            expr = String(lastVal);
            render(lastVal);
            updateBase(lastVal);
          } catch {
            render("Error");
            expr = "";
          }
          return;
        }
        if (k === "=") {
          if (!expr) return;
          try {
            const res = safeEvalExpr(expr);
            elExpr.textContent = `${expr} =`;
            lastVal = res;
            expr = String(res);
            elDisp.textContent = expr;
            updateBase(res);
          } catch {
            render("Error");
            expr = "";
          }
          return;
        }
        expr += k;
        render();
      }

      for (const [label, cls] of keys) {
        const b = document.createElement("button");
        b.className = `btn ${cls}`;
        b.textContent = label;
        b.style.cssText = "font-size:15px;font-weight:600;";
        b.onclick = () => press(label);
        elKeys.appendChild(b);
      }

      const onKey = (e) => {
        const win = container.closest(".win");
        if (!win || !win.classList.contains("focused")) return;
        if (/^[0-9.()+\-%]$/.test(e.key)) { press(e.key); e.preventDefault(); }
        else if (e.key === "*") { press("×"); e.preventDefault(); }
        else if (e.key === "/") { press("÷"); e.preventDefault(); }
        else if (e.key === "Enter" || e.key === "=") { press("="); e.preventDefault(); }
        else if (e.key === "Backspace") { press("⌫"); e.preventDefault(); }
        else if (e.key === "Escape") { press("C"); e.preventDefault(); }
      };

      window.addEventListener("keydown", onKey);
      return () => window.removeEventListener("keydown", onKey);
    },
  });
})(window);

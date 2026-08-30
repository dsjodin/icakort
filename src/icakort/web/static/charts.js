/* Handritade SVG-diagram.
 *
 * Medvetet utan diagrambibliotek: specen (2 px linjer, 4 px rundade
 * datauändar, 2 px ytmellanrum mellan staplar, krysshår + tooltip) är
 * enklare att träffa exakt än att övertala ett bibliotek, och dashboarden
 * blir beroendefri och fungerar offline.
 */
(function (global) {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var BAR_MAX = 24;      // staplar fyller aldrig hela bandet
  var GAP = 2;           // ytmellanrum mellan angränsande märken
  var RADIUS = 4;        // rundad datauände

  function el(tag, attrs) {
    var node = document.createElementNS(NS, tag);
    for (var key in attrs || {}) {
      if (attrs[key] !== null && attrs[key] !== undefined) {
        node.setAttribute(key, attrs[key]);
      }
    }
    return node;
  }

  function token(name) {
    return getComputedStyle(document.body).getPropertyValue(name).trim();
  }

  function formatKr(ore, decimals) {
    var value = (ore || 0) / 100;
    var opts = {
      minimumFractionDigits: decimals === undefined ? 0 : decimals,
      maximumFractionDigits: decimals === undefined ? 0 : decimals
    };
    return value.toLocaleString("sv-SE", opts) + " kr";
  }

  /* Runda axelsteg till jämna tal. */
  function niceTicks(max, count) {
    if (!(max > 0)) return [0, 1];
    var raw = max / (count || 4);
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var norm = raw / mag;
    var step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    var ticks = [];
    for (var v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
    if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }

  /* Stapel med rundad datauände och rak baslinje. */
  function barPath(x, y, w, h, side) {
    var r = Math.min(RADIUS, w / 2, Math.max(h, 0));
    if (h <= 0.5 || w <= 0.5) return "";
    if (side === "top") {
      return "M" + x + "," + (y + h) +
        "V" + (y + r) + "a" + r + "," + r + " 0 0 1 " + r + ",-" + r +
        "h" + (w - 2 * r) + "a" + r + "," + r + " 0 0 1 " + r + "," + r +
        "V" + (y + h) + "Z";
    }
    if (side === "left") {
      // Liggande stapel åt vänster: rundad vänsterände, rak mot baslinjen.
      r = Math.min(RADIUS, w, h / 2);
      return "M" + (x + w) + "," + y +
        "H" + (x + r) + "a" + r + "," + r + " 0 0 0 -" + r + "," + r +
        "v" + (h - 2 * r) + "a" + r + "," + r + " 0 0 0 " + r + "," + r +
        "H" + (x + w) + "Z";
    }
    if (side === "right") {
      r = Math.min(RADIUS, w, h / 2);
      return "M" + x + "," + y +
        "h" + (w - r) + "a" + r + "," + r + " 0 0 1 " + r + "," + r +
        "v" + (h - 2 * r) + "a" + r + "," + r + " 0 0 1 -" + r + "," + r +
        "H" + x + "Z";
    }
    return "M" + x + "," + y + "h" + w + "v" + h + "h" + (-w) + "Z";
  }

  /* En tooltip per diagram, positionerad över SVG:n. */
  function Tooltip(container) {
    var node = document.createElement("div");
    node.className = "tooltip";
    node.hidden = true;
    container.appendChild(node);
    return {
      node: node,
      hide: function () { node.hidden = true; },
      show: function (x, y, head, rows) {
        node.replaceChildren();
        var h = document.createElement("div");
        h.className = "tooltip-head";
        h.textContent = head;                     // etiketter är data: aldrig innerHTML
        node.appendChild(h);
        rows.forEach(function (row) {
          var line = document.createElement("div");
          line.className = "tooltip-row";
          if (row.color) {
            var key = document.createElement("span");
            key.className = "tooltip-key";
            key.style.background = row.color;
            line.appendChild(key);
          }
          var value = document.createElement("span");
          value.className = "tooltip-value";
          value.textContent = row.value;
          line.appendChild(value);
          var name = document.createElement("span");
          name.className = "tooltip-name";
          name.textContent = row.name;
          line.appendChild(name);
          node.appendChild(line);
        });
        node.hidden = false;
        var width = node.offsetWidth;
        var left = Math.max(0, Math.min(container.clientWidth - width, x - width / 2));
        node.style.left = left + "px";
        node.style.top = Math.max(0, y - node.offsetHeight - 12) + "px";
      }
    };
  }

  function prepare(container) {
    container.replaceChildren();
    return {
      width: Math.max(320, container.clientWidth || 640),
      tooltip: Tooltip(container)
    };
  }

  function emptyState(container, message) {
    container.replaceChildren();
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = message;
    container.appendChild(p);
  }

  /* ---------- Kolumndiagram, en serie ---------- */
  function columns(container, data, options) {
    options = options || {};
    if (!data.length) return emptyState(container, options.empty || "Ingen data.");
    var ctx = prepare(container);
    var W = ctx.width;
    var H = options.height || 260;
    var pad = { top: 20, right: 8, bottom: 34, left: 60 };
    var plotW = W - pad.left - pad.right;
    var plotH = H - pad.top - pad.bottom;

    var max = Math.max.apply(null, data.map(function (d) { return d.value; }));
    var ticks = niceTicks(max, 4);
    var top = ticks[ticks.length - 1];
    var y = function (v) { return pad.top + plotH - (v / top) * plotH; };

    var svg = el("svg", { width: W, height: H, role: "img" });
    var band = plotW / data.length;
    var barW = Math.min(BAR_MAX, Math.max(4, band - GAP * 2));
    var peak = data.indexOf(data.reduce(function (a, b) { return b.value > a.value ? b : a; }));

    ticks.forEach(function (t) {
      svg.appendChild(el("line", {
        x1: pad.left, x2: W - pad.right, y1: y(t), y2: y(t),
        stroke: token(t === 0 ? "--baseline" : "--gridline"), "stroke-width": 1
      }));
      var label = el("text", {
        x: pad.left - 8, y: y(t) + 4, "text-anchor": "end",
        fill: token("--text-muted"), "font-size": 11
      });
      label.textContent = formatKr(t);
      svg.appendChild(label);
    });

    data.forEach(function (d, i) {
      var cx = pad.left + band * i + band / 2;
      var x = cx - barW / 2;
      var h = Math.max(0, (d.value / top) * plotH);
      svg.appendChild(el("path", {
        d: barPath(x, y(d.value), barW, h, "top"), fill: token("--seq")
      }));

      // Tom etikett = medvetet utglesad axel. Tooltipen har ändå full text.
      if (d.label) {
        var tick = el("text", {
          x: cx, y: H - 14, "text-anchor": "middle",
          fill: token("--text-muted"), "font-size": 11
        });
        tick.textContent = d.label;
        svg.appendChild(tick);
      }

      // Bara toppen får direktetikett - en siffra på varje stapel läses inte.
      if (i === peak) {
        var value = el("text", {
          x: cx, y: y(d.value) - 8, "text-anchor": "middle",
          fill: token("--text-primary"), "font-size": 11, "font-weight": 600
        });
        value.textContent = formatKr(d.value);
        svg.appendChild(value);
      }

      // Träffytan är hela bandet, inte bara de målade pixlarna.
      var hit = el("rect", {
        x: pad.left + band * i, y: pad.top, width: band, height: plotH,
        fill: "transparent", tabindex: 0
      });
      function show() {
        ctx.tooltip.show(cx, y(d.value), d.title || d.label, [
          { value: formatKr(d.value, 2), name: options.valueName || "", color: token("--seq") }
        ].concat(d.extra || []));
      }
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", ctx.tooltip.hide);
      hit.addEventListener("blur", ctx.tooltip.hide);
      svg.appendChild(hit);
    });

    container.appendChild(svg);
  }

  /* ---------- Liggande staplar, en serie ---------- */
  function barsH(container, data, options) {
    options = options || {};
    if (!data.length) return emptyState(container, options.empty || "Ingen data.");
    var ctx = prepare(container);
    var W = ctx.width;
    var rowH = 28;
    var H = data.length * rowH + 8;
    var hasSuffix = data.some(function (d) { return d.suffix; });
    var labelW = Math.min(200, Math.max(90, Math.round(W * 0.24)));
    var valueW = hasSuffix ? 170 : 96;
    var plotW = Math.max(40, W - labelW - valueW);

    // Negativa värden (rabattrader) får en egen riktning från nollinjen i
    // stället för att ritas som positiv längd - annars ljuger stapeln.
    var max = Math.max(0, Math.max.apply(null, data.map(function (d) { return d.value; })));
    var min = Math.min(0, Math.min.apply(null, data.map(function (d) { return d.value; })));
    var span = (max - min) || 1;
    var zeroX = labelW + (-min / span) * plotW;

    var svg = el("svg", { width: W, height: H, role: "img" });
    if (min < 0) {
      svg.appendChild(el("line", {
        x1: zeroX, x2: zeroX, y1: 0, y2: H - 6,
        stroke: token("--baseline"), "stroke-width": 1
      }));
    }

    data.forEach(function (d, i) {
      var y = i * rowH + 4;
      var barH = Math.min(BAR_MAX, rowH - GAP * 2 - 4);
      var barY = y + (rowH - 4 - barH) / 2;
      var negative = d.value < 0;
      var w = (Math.abs(d.value) / span) * plotW;
      var color = d.color || (negative ? token("--negative") : token("--seq"));

      var label = el("text", {
        x: labelW - 10, y: barY + barH / 2 + 4, "text-anchor": "end",
        fill: token("--text-secondary"), "font-size": 12
      });
      label.textContent = d.label;
      svg.appendChild(label);

      svg.appendChild(el("path", {
        d: negative
          ? barPath(zeroX - w, barY, w, barH, "left")
          : barPath(zeroX, barY, w, barH, "right"),
        fill: color
      }));

      // Värdet vid spetsen - utom för negativa staplar, som växer in mot
      // kategorinamnet: där hamnar det på den fria sidan om nollinjen.
      var tipX = negative ? zeroX + 8 : zeroX + w + 8;
      var value = el("text", {
        x: tipX, y: barY + barH / 2 + 4, "text-anchor": "start",
        fill: token("--text-primary"), "font-size": 12
      });
      value.setAttribute("font-variant-numeric", "tabular-nums");
      value.textContent = formatKr(d.value);
      svg.appendChild(value);

      if (d.suffix) {
        var suffix = el("text", {
          x: tipX, y: barY + barH / 2 + 4, dx: 8 + measure(formatKr(d.value)),
          fill: token("--text-muted"), "font-size": 11
        });
        suffix.textContent = d.suffix;
        svg.appendChild(suffix);
      }

      var hit = el("rect", {
        x: 0, y: y, width: W, height: rowH - 2, fill: "transparent", tabindex: 0
      });
      function show() {
        ctx.tooltip.show(negative ? zeroX - w : zeroX + w, y + 4, d.label,
          [{ value: formatKr(d.value, 2), name: d.note || d.suffix || "", color: color }]);
      }
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", ctx.tooltip.hide);
      hit.addEventListener("blur", ctx.tooltip.hide);
      svg.appendChild(hit);
    });

    container.appendChild(svg);
  }

  /* Grov textbredd i px vid 12 px systemsans - räcker för att placera ett
     efterföljande suffix utan att mäta i DOM:en per bildruta. */
  function measure(text) {
    return String(text).length * 6.8;
  }

  /* ---------- Staplade kolumner, flera serier ---------- */
  function stackedColumns(container, labels, series, options) {
    options = options || {};
    if (!labels.length || !series.length) {
      return emptyState(container, options.empty || "Ingen data.");
    }
    var ctx = prepare(container);
    var W = ctx.width;
    var H = options.height || 300;
    var pad = { top: 16, right: 8, bottom: 34, left: 60 };
    var plotW = W - pad.left - pad.right;
    var plotH = H - pad.top - pad.bottom;

    var totals = labels.map(function (_, i) {
      return series.reduce(function (sum, s) { return sum + Math.max(0, s.values[i] || 0); }, 0);
    });
    var max = Math.max.apply(null, totals) || 1;
    var ticks = niceTicks(max, 4);
    var top = ticks[ticks.length - 1];
    var y = function (v) { return pad.top + plotH - (v / top) * plotH; };

    var svg = el("svg", { width: W, height: H, role: "img" });
    ticks.forEach(function (t) {
      svg.appendChild(el("line", {
        x1: pad.left, x2: W - pad.right, y1: y(t), y2: y(t),
        stroke: token(t === 0 ? "--baseline" : "--gridline"), "stroke-width": 1
      }));
      var label = el("text", {
        x: pad.left - 8, y: y(t) + 4, "text-anchor": "end",
        fill: token("--text-muted"), "font-size": 11
      });
      label.textContent = formatKr(t);
      svg.appendChild(label);
    });

    var band = plotW / labels.length;
    var barW = Math.min(BAR_MAX + 8, Math.max(6, band - GAP * 3));

    labels.forEach(function (label, i) {
      var cx = pad.left + band * i + band / 2;
      var x = cx - barW / 2;
      var cursor = 0;
      series.forEach(function (s, si) {
        var value = Math.max(0, s.values[i] || 0);
        if (value <= 0) return;
        var h = (value / top) * plotH;
        var yTop = y(cursor + value);
        // 2 px av ytan mellan segmenten gör grannarna distinkta utan kantlinje.
        var drawn = Math.max(0, h - (cursor > 0 ? GAP : 0));
        var isTop = si === series.length - 1 ||
          series.slice(si + 1).every(function (o) { return !(o.values[i] > 0); });
        svg.appendChild(el("path", {
          d: barPath(x, yTop, barW, drawn, isTop ? "top" : "square"),
          fill: s.color
        }));
        cursor += value;
      });

      if (label) {
        var tick = el("text", {
          x: cx, y: H - 14, "text-anchor": "middle",
          fill: token("--text-muted"), "font-size": 11
        });
        tick.textContent = label;
        svg.appendChild(tick);
      }

      var hit = el("rect", {
        x: pad.left + band * i, y: pad.top, width: band, height: plotH,
        fill: "transparent", tabindex: 0
      });
      function show() {
        var rows = series
          .map(function (s) { return { s: s, v: s.values[i] || 0 }; })
          .filter(function (r) { return r.v > 0; })
          .sort(function (a, b) { return b.v - a.v; })
          .map(function (r) {
            return { value: formatKr(r.v, 2), name: r.s.name, color: r.s.color };
          });
        rows.push({ value: formatKr(totals[i], 2), name: "Totalt", color: null });
        var head = (options.titles && options.titles[i]) || label;
        ctx.tooltip.show(cx, y(totals[i]), head, rows);   // en tooltip, alla serier
      }
      hit.addEventListener("pointermove", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", ctx.tooltip.hide);
      hit.addEventListener("blur", ctx.tooltip.hide);
      svg.appendChild(hit);
    });

    container.appendChild(svg);
    container.appendChild(legend(series, "rect"));
  }

  function legend(series, shape) {
    var box = document.createElement("div");
    box.className = "legend";
    series.forEach(function (s) {
      var item = document.createElement("span");
      item.className = "legend-item";
      var swatch = document.createElement("span");
      swatch.className = shape === "line" ? "legend-line" : "legend-swatch";
      swatch.style.background = s.color;
      item.appendChild(swatch);
      var name = document.createElement("span");
      name.textContent = s.name;
      item.appendChild(name);
      box.appendChild(item);
    });
    return box;
  }

  /* ---------- Linjediagram med krysshår ---------- */
  function line(container, points, options) {
    options = options || {};
    if (points.length < 2) {
      return emptyState(container, options.empty || "För få mätpunkter för en kurva.");
    }
    var ctx = prepare(container);
    var W = ctx.width;
    var H = options.height || 240;
    var pad = { top: 24, right: 80, bottom: 34, left: 60 };
    var plotW = W - pad.left - pad.right;
    var plotH = H - pad.top - pad.bottom;

    var values = points.map(function (p) { return p.value; });
    var max = Math.max.apply(null, values);
    var ticks = niceTicks(max, 4);
    var top = ticks[ticks.length - 1];
    var x = function (i) { return pad.left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW); };
    var y = function (v) { return pad.top + plotH - (v / top) * plotH; };
    var color = token("--series-1");
    var surface = token("--surface-1");

    var svg = el("svg", { width: W, height: H, role: "img" });
    ticks.forEach(function (t) {
      svg.appendChild(el("line", {
        x1: pad.left, x2: W - pad.right, y1: y(t), y2: y(t),
        stroke: token(t === 0 ? "--baseline" : "--gridline"), "stroke-width": 1
      }));
      var label = el("text", {
        x: pad.left - 8, y: y(t) + 4, "text-anchor": "end",
        fill: token("--text-muted"), "font-size": 11
      });
      label.textContent = formatKr(t);
      svg.appendChild(label);
    });

    var crosshair = el("line", {
      y1: pad.top, y2: pad.top + plotH, stroke: token("--baseline"),
      "stroke-width": 1, visibility: "hidden"
    });
    svg.appendChild(crosshair);

    svg.appendChild(el("path", {
      d: points.map(function (p, i) { return (i ? "L" : "M") + x(i) + "," + y(p.value); }).join(""),
      fill: "none", stroke: color, "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round"
    }));

    points.forEach(function (p, i) {
      svg.appendChild(el("circle", {
        cx: x(i), cy: y(p.value), r: 4, fill: color, stroke: surface, "stroke-width": 2
      }));
    });

    var last = points[points.length - 1];
    var endLabel = el("text", {
      x: x(points.length - 1) + 10, y: y(last.value) + 4,
      fill: token("--text-primary"), "font-size": 12, "font-weight": 600
    });
    endLabel.textContent = formatKr(last.value, 2);
    svg.appendChild(endLabel);

    [0, points.length - 1].forEach(function (i) {
      var tick = el("text", {
        x: x(i), y: H - 14,
        "text-anchor": i === 0 ? "start" : "end",
        fill: token("--text-muted"), "font-size": 11
      });
      tick.textContent = points[i].label;
      svg.appendChild(tick);
    });

    // Krysshåret hittar närmaste X - läsaren siktar på ett datum, inte på en 2 px-linje.
    var surfaceHit = el("rect", {
      x: pad.left, y: pad.top, width: plotW, height: plotH, fill: "transparent", tabindex: 0
    });
    function nearest(event) {
      var box = svg.getBoundingClientRect();
      var px = event.clientX - box.left;
      var best = 0, bestDist = Infinity;
      points.forEach(function (_, i) {
        var d = Math.abs(x(i) - px);
        if (d < bestDist) { bestDist = d; best = i; }
      });
      return best;
    }
    function show(event) {
      var i = event.clientX === undefined ? points.length - 1 : nearest(event);
      var p = points[i];
      crosshair.setAttribute("x1", x(i));
      crosshair.setAttribute("x2", x(i));
      crosshair.setAttribute("visibility", "visible");
      ctx.tooltip.show(x(i), y(p.value), p.label, [
        { value: formatKr(p.value, 2), name: p.note || (options.valueName || ""), color: color }
      ]);
    }
    function hide() {
      crosshair.setAttribute("visibility", "hidden");
      ctx.tooltip.hide();
    }
    surfaceHit.addEventListener("pointermove", show);
    surfaceHit.addEventListener("focus", show);
    surfaceHit.addEventListener("pointerleave", hide);
    surfaceHit.addEventListener("blur", hide);
    svg.appendChild(surfaceHit);

    container.appendChild(svg);
  }

  global.Charts = {
    columns: columns,
    barsH: barsH,
    stackedColumns: stackedColumns,
    line: line,
    formatKr: formatKr,
    token: token
  };
})(window);

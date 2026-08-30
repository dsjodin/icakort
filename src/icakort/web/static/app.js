/* Dashboardens datalager: hämtar JSON, håller filterstate och ritar om. */
(function () {
  "use strict";

  var MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun",
                "jul", "aug", "sep", "okt", "nov", "dec"];
  var STACK_SLOTS = 6;   // fem kategorier + "Övrigt" - taket för en läsbar stapel

  var state = { from: "", to: "", store: "", category: "", order: "spend", item: null };
  var latest = {};       // senast hämtade svar, för omritning vid resize

  function $(id) { return document.getElementById(id); }

  function query(extra) {
    var params = new URLSearchParams();
    if (state.from) params.set("from", state.from);
    if (state.to) params.set("to", state.to);
    if (state.store) params.set("store", state.store);
    if (state.category) params.set("category", state.category);
    for (var key in extra || {}) params.set(key, extra[key]);
    return params.toString();
  }

  function getJSON(path, extra) {
    return fetch(path + "?" + query(extra)).then(function (response) {
      if (!response.ok) throw new Error(path + " gav " + response.status);
      return response.json();
    });
  }

  function monthLabel(month, index) {
    var parts = String(month).split("-");
    var name = MONTHS[parseInt(parts[1], 10) - 1] || month;
    return (index === 0 || parts[1] === "01") ? name + " " + parts[0].slice(2) : name;
  }

  function percent(fraction, decimals) {
    return (fraction * 100).toLocaleString("sv-SE", {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals
    }) + " %";
  }

  function text(tag, value, className) {
    var node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  }

  function table(container, columns, rows, onRow) {
    container.replaceChildren();
    if (!rows.length) {
      container.appendChild(text("p", "Ingen data att visa.", "empty"));
      return;
    }
    var scroll = document.createElement("div");
    scroll.className = "table-scroll";
    var el = document.createElement("table");
    var head = document.createElement("tr");
    columns.forEach(function (col) {
      head.appendChild(text("th", col.title, col.num ? "num" : ""));
    });
    el.appendChild(document.createElement("thead")).appendChild(head);
    var body = document.createElement("tbody");
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      columns.forEach(function (col) {
        if (col.cell) {
          var td = document.createElement("td");
          td.appendChild(col.cell(row));
          tr.appendChild(td);
        } else {
          tr.appendChild(text("td", col.value(row), col.num ? "num" : ""));
        }
      });
      if (onRow) onRow(tr, row);
      body.appendChild(tr);
    });
    el.appendChild(body);
    scroll.appendChild(el);
    container.appendChild(scroll);
  }

  /* ---------- Rendering ---------- */

  function renderOverview(data) {
    var s = data.summary;
    $("kpi-total").textContent = Charts.formatKr(s.total_ore, 2);
    $("kpi-receipts").textContent = s.receipts.toLocaleString("sv-SE");
    $("kpi-avg").textContent = Charts.formatKr(s.avg_receipt_ore, 2);
    $("kpi-coverage").textContent = data.coverage.covered_share === null
      ? "–"
      : percent(data.coverage.covered_share, 0);
    $("period").textContent = s.receipts
      ? s.first_date + " – " + s.last_date + " · " + s.items + " varurader"
      : "Inga kvitton matchar filtret.";

    Charts.columns($("chart-monthly"), data.monthly.map(function (row, i) {
      return {
        label: monthLabel(row.month, i),
        value: row.total_ore,
        extra: [{ value: String(row.receipts), name: "kvitton", color: null }]
      };
    }), { valueName: "totalt" });

    var stores = data.by_store;
    $("card-stores").hidden = stores.length < 2;
    if (stores.length >= 2) {
      Charts.barsH($("chart-stores"), stores.map(function (row) {
        return { label: row.store, value: row.total_ore, suffix: row.receipts + " kvitton" };
      }));
    }
  }

  function renderCategories(data) {
    var total = data.by_category.reduce(function (sum, row) { return sum + row.total_ore; }, 0);
    Charts.barsH($("chart-categories"), data.by_category.map(function (row) {
      var share = total ? percent(row.total_ore / total, 1) : "";
      return { label: row.category, value: row.total_ore, suffix: share, note: share + " av totalen" };
    }));

    // Fem största kategorier + Övrigt: fler serier gör stapeln oläsbar.
    var top = data.by_category.slice(0, STACK_SLOTS - 1).map(function (row) { return row.category; });
    var months = [];
    var cells = {};
    data.by_month.forEach(function (row) {
      if (months.indexOf(row.month) === -1) months.push(row.month);
      var name = top.indexOf(row.category) === -1 ? "Övrigt" : row.category;
      cells[row.month + "|" + name] = (cells[row.month + "|" + name] || 0) + row.total_ore;
    });
    months.sort();

    var names = top.concat(["Övrigt"]);
    var series = names.map(function (name, i) {
      return {
        name: name,
        color: Charts.token("--series-" + (i + 1)),
        values: months.map(function (month) { return cells[month + "|" + name] || 0; })
      };
    }).filter(function (s) {
      return s.values.some(function (v) { return v > 0; });
    });

    Charts.stackedColumns(
      $("chart-category-months"),
      months.map(monthLabel),
      series
    );

    // Tabellvyn är reliefen för de ljusa serierna i ljust läge.
    table($("table-category-months"),
      [{ title: "Månad", value: function (r) { return r.month; } }]
        .concat(series.map(function (s) {
          return {
            title: s.name, num: true,
            value: function (r) { return Charts.formatKr(cells[r.month + "|" + s.name] || 0, 2); }
          };
        })),
      months.map(function (month) { return { month: month }; }));
  }

  function renderItems(data) {
    table($("table-items"), [
      { title: "Vara", value: function (r) { return r.name; } },
      { title: "Kategori", value: function (r) { return r.category; } },
      { title: "Gånger", num: true, value: function (r) { return String(r.times); } },
      { title: "Totalt", num: true, value: function (r) { return Charts.formatKr(r.total_ore, 2); } }
    ], data.items, function (tr, row) {
      tr.className = "clickable" + (state.item === row.name_key ? " is-active" : "");
      tr.tabIndex = 0;
      tr.addEventListener("click", function () { selectItem(row); });
      tr.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectItem(row);
        }
      });
    });
  }

  function selectItem(row) {
    state.item = row.name_key;
    $("price-title").textContent = "Prisutveckling – " + row.name;
    $("card-price").hidden = false;
    fetch("/api/price?name_key=" + encodeURIComponent(row.name_key))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        latest.price = data;
        renderPrice(data);
        $("card-price").scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    renderItems(latest.items);
  }

  function renderPrice(data) {
    Charts.line($("chart-price"), data.points.map(function (point) {
      return {
        label: point.date,
        value: point.unit_price_ore,
        note: "styckpris" + (point.unit ? " per " + point.unit : "")
      };
    }), { valueName: "styckpris", empty: "Bara ett köptillfälle med styckpris – ingen kurva än." });
  }

  function renderQuality(data) {
    var box = $("quality");
    box.replaceChildren();
    var share = data.coverage.covered_share;
    if (share === null) {
      // Inga varurader alls: säg det rakt ut i stället för att rita en tom mätare.
      box.appendChild(text("p",
        "Inga varurader har tolkats ur kvittona. Rådatan finns kvar — kör " +
        "`icakort reparse` efter en rättad tolkning.", "note"));
      return;
    }

    box.appendChild(text("p", "Andel av varukronorna som fått en kategori:", "note"));
    var meter = document.createElement("div");
    meter.className = "meter";
    var fill = document.createElement("div");
    fill.className = "meter-fill";
    fill.style.width = (share * 100).toFixed(1) + "%";
    meter.appendChild(fill);
    box.appendChild(meter);
    box.appendChild(text("p",
      percent(share, 1) + " kategoriserat · " +
      Charts.formatKr(data.coverage.unknown_ore, 2) + " okategoriserat på " +
      data.coverage.unknown_items + " rader", "note"));

    // Regelfilen redigeras på värden (i containern: volymen). Knappen kör om
    // kategoriseringen så ändringen slår igenom utan omstart.
    var rerun = document.createElement("button");
    rerun.type = "button";
    rerun.textContent = "Kategorisera om";
    rerun.className = "rerun";
    rerun.addEventListener("click", function () {
      rerun.disabled = true;
      fetch("/api/categorize", { method: "POST" }).then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) throw new Error(data.detail || "kunde inte kategorisera om");
          refresh();
        });
      }).catch(function (error) {
        rerun.disabled = false;
        window.alert(error.message);
      });
    });
    box.appendChild(rerun);

    box.appendChild(text("h3", "Störst okategoriserat"));
    var unknown = document.createElement("div");
    box.appendChild(unknown);
    table(unknown, [
      { title: "Vara", value: function (r) { return r.example_name; } },
      { title: "Gånger", num: true, value: function (r) { return String(r.times); } },
      { title: "Totalt", num: true, value: function (r) { return Charts.formatKr(r.total_ore, 2); } },
      { title: "Sätt kategori", cell: categoryPicker }
    ], data.unknown);

    box.appendChild(text("h3", "Kvitton där raderna inte summerar till totalen"));
    var mismatched = document.createElement("div");
    box.appendChild(mismatched);
    if (!data.mismatched.length) {
      mismatched.appendChild(text("p", "Alla kvitton stämmer.", "note"));
    } else {
      table(mismatched, [
        { title: "Datum", value: function (r) { return r.purchase_date; } },
        { title: "Butik", value: function (r) { return r.store_name; } },
        { title: "Kvitto", num: true, value: function (r) { return Charts.formatKr(r.total_ore, 2); } },
        { title: "Rader", num: true, value: function (r) { return Charts.formatKr(r.item_sum_ore, 2); } },
        { title: "Diff", num: true, value: function (r) { return Charts.formatKr(r.diff_ore, 2); } }
      ], data.mismatched);
    }
  }

  /* Kategoriväljaren gör `categorize --unknown`-arbetsflödet till en dropdown. */
  function categoryPicker(row) {
    var select = document.createElement("select");
    select.className = "category-picker";
    select.setAttribute("aria-label", "Kategori för " + row.example_name);
    select.appendChild(new Option("Välj …", ""));
    (latest.allCategories || []).forEach(function (name) {
      select.appendChild(new Option(name, name));
    });
    select.addEventListener("change", function () {
      if (!select.value) return;
      select.disabled = true;
      fetch("/api/overrides", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name_key: row.name_key, category: select.value })
      }).then(function (response) {
        if (!response.ok) throw new Error("kunde inte spara kategorin");
        refresh();
      }).catch(function (error) {
        select.disabled = false;
        window.alert(error.message);
      });
    });
    return select;
  }

  /* ---------- Inloggning och synk ---------- */

  var pollTimer = null;

  function renderSession(data) {
    var state = $("session-state");
    state.classList.toggle("is-authenticated", data.authenticated);
    state.textContent = data.authenticated ? "Inloggad hos Kivra" : "Inte inloggad";
    $("btn-sync").hidden = !data.authenticated;
  }

  function loadSession() {
    return fetch("/api/session").then(function (r) { return r.json(); }).then(renderSession);
  }

  function startJob(path) {
    setButtonsBusy(true);
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all_stores: false, max_receipts: 0 })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.detail || "kunde inte starta jobbet");
        renderJob(data);
        pollJob();
      });
    }).catch(function (error) {
      setButtonsBusy(false);
      window.alert(error.message);
    });
  }

  function setButtonsBusy(busy) {
    $("btn-login").disabled = busy;
    $("btn-sync").disabled = busy;
    $("btn-reparse").disabled = busy;
  }

  function renderJob(job) {
    var panel = $("job-panel");
    if (!job.kind) { panel.hidden = true; return; }
    panel.hidden = false;
    $("job-title").textContent = { login: "Loggar in och synkar",
                                    sync: "Synkar",
                                    reparse: "Tolkar om sparad rådata" }[job.kind] || "Jobb";

    var log = $("job-log");
    log.textContent = job.log.join("\n") || "Startar …";
    log.classList.toggle("is-error", job.state === "error");
    log.scrollTop = log.scrollHeight;

    // Kivras QR roterar varje sekund; en gammal bildruta avvisas av BankID,
    // så bilden byts vid varje poll.
    var qr = $("job-qr");
    qr.hidden = !job.has_qr;
    if (job.has_qr) $("job-qr-img").src = "/api/job/qr.svg?t=" + Date.now();

    var done = job.state !== "running";
    $("job-dismiss").hidden = !done;
    setButtonsBusy(!done);

    if (done && job.result) {
      var what = job.kind === "reparse"
        ? job.result.reparsed + " kvitton omtolkade"
        : job.result.fetched + " nya kvitton";
      var warning = job.result.unparsed
        ? " · VARNING: " + job.result.unparsed + " utan varurader"
        : (job.result.uncategorized
            ? " · " + job.result.uncategorized + " rader okategoriserade" : "");
      $("job-title").textContent = "Klart: " + what + warning;
    }
    if (job.state === "error") $("job-title").textContent = "Jobbet misslyckades";
  }

  function pollJob() {
    clearTimeout(pollTimer);
    fetch("/api/job").then(function (r) { return r.json(); }).then(function (job) {
      renderJob(job);
      if (job.state === "running") {
        pollTimer = setTimeout(pollJob, 1000);
      } else if (job.kind) {
        loadSession();
        refresh();
      }
    });
  }

  /* ---------- Laddning ---------- */

  function refresh() {
    Promise.all([
      getJSON("/api/overview"),
      getJSON("/api/categories"),
      getJSON("/api/items", { order: state.order, limit: 40 }),
      fetch("/api/quality").then(function (r) { return r.json(); })
    ]).then(function (results) {
      latest.overview = results[0];
      latest.categories = results[1];
      latest.items = results[2];
      latest.quality = results[3];
      renderOverview(latest.overview);
      renderCategories(latest.categories);
      renderItems(latest.items);
      renderQuality(latest.quality);
    }).catch(function (error) {
      $("period").textContent = "Kunde inte hämta data: " + error.message;
    });
  }

  function redraw() {
    if (latest.overview) renderOverview(latest.overview);
    if (latest.categories) renderCategories(latest.categories);
    if (latest.price && !$("card-price").hidden) renderPrice(latest.price);
  }

  function setPreset(button) {
    document.querySelectorAll(".presets button").forEach(function (b) {
      b.classList.toggle("is-active", b === button);
    });
    var preset = button.dataset.preset;
    var today = new Date();
    var iso = function (d) { return d.toISOString().slice(0, 10); };
    if (preset === "all") {
      state.from = latest.bounds.date_from || "";
      state.to = latest.bounds.date_to || "";
    } else if (preset === "ytd") {
      state.from = today.getFullYear() + "-01-01";
      state.to = iso(today);
    } else {
      var days = parseInt(preset, 10);
      var start = new Date(today.getTime() - days * 86400000);
      state.from = iso(start);
      state.to = iso(today);
    }
    $("f-from").value = state.from;
    $("f-to").value = state.to;
    refresh();
  }

  function init() {
    fetch("/api/filters").then(function (r) { return r.json(); }).then(function (data) {
      latest.bounds = data;
      state.from = data.date_from || "";
      state.to = data.date_to || "";
      $("f-from").value = state.from;
      $("f-to").value = state.to;
      data.stores.forEach(function (name) {
        $("f-store").appendChild(new Option(name, name));
      });
      data.categories.forEach(function (name) {
        $("f-category").appendChild(new Option(name, name));
      });
      latest.allCategories = data.all_categories || data.categories;
      refresh();
    });

    $("f-from").addEventListener("change", function () { state.from = this.value; refresh(); });
    $("f-to").addEventListener("change", function () { state.to = this.value; refresh(); });
    $("f-store").addEventListener("change", function () { state.store = this.value; refresh(); });
    $("f-category").addEventListener("change", function () { state.category = this.value; refresh(); });

    document.querySelectorAll(".presets button").forEach(function (button) {
      button.addEventListener("click", function () { setPreset(button); });
    });
    document.querySelectorAll(".toolbar button").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll(".toolbar button").forEach(function (b) {
          b.classList.toggle("is-active", b === button);
        });
        state.order = button.dataset.order;
        getJSON("/api/items", { order: state.order, limit: 40 }).then(function (data) {
          latest.items = data;
          renderItems(data);
        });
      });
    });

    loadSession();
    pollJob();
    $("btn-login").addEventListener("click", function () { startJob("/api/job/login"); });
    $("btn-sync").addEventListener("click", function () { startJob("/api/job/sync"); });
    $("btn-reparse").addEventListener("click", function () { startJob("/api/job/reparse"); });
    $("job-dismiss").addEventListener("click", function () { $("job-panel").hidden = true; });

    var timer;
    window.addEventListener("resize", function () {
      clearTimeout(timer);
      timer = setTimeout(redraw, 150);
    });
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redraw);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();

/* Dashboardens datalager: hämtar JSON, håller filterstate och ritar om. */
(function () {
  "use strict";

  var MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun",
                "jul", "aug", "sep", "okt", "nov", "dec"];
  var CLEAR_OVERRIDE = "\u0000clear";   // eget värde, kan inte krocka med ett kategorinamn
  var STACK_SLOTS = 6;   // fem kategorier + "Övrigt" - taket för en läsbar stapel

  var state = { from: "", to: "", store: "", category: "", group: "",
                order: "spend", item: null };
  var latest = {};       // senast hämtade svar, för omritning vid resize

  function $(id) { return document.getElementById(id); }

  function query(extra) {
    var params = new URLSearchParams();
    if (state.from) params.set("from", state.from);
    if (state.to) params.set("to", state.to);
    if (state.store) params.set("store", state.store);
    if (state.category) params.set("category", state.category);
    if (state.group) params.set("group", state.group);
    for (var key in extra || {}) params.set(key, extra[key]);
    return params.toString();
  }

  function getJSON(path, extra) {
    return fetch(path + "?" + query(extra)).then(function (response) {
      if (!response.ok) throw new Error(path + " gav " + response.status);
      return response.json();
    });
  }

  function monthLabel(month, index, total) {
    var parts = String(month).split("-");
    var name = MONTHS[parseInt(parts[1], 10) - 1] || month;
    var isJanuary = parts[1] === "01";
    // Över ett par år ryms inte en etikett per månad. Då märks bara
    // årsskiftena ut -- annars klumpar de ihop sig till oläslig gröt.
    if (total > 24) return isJanuary ? parts[0] : "";
    return (index === 0 || isJanuary) ? name + " " + parts[0].slice(2) : name;
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
        label: monthLabel(row.month, i, data.monthly.length),
        title: row.month,
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
    // Diagrammen visar grupper, tabellen visar löven. Fyrtio kategorier
    // ryms inte i en läsbar stapel, men de behövs i detaljen.
    var rows = state.group ? data.by_category : data.by_group;
    var months = state.group ? data.by_month : data.group_by_month;
    var total = rows.reduce(function (sum, row) { return sum + row.total_ore; }, 0);

    $("category-level").textContent = state.group
      ? "Kategorier i " + state.group
      : "Huvudgrupper";
    $("category-back").hidden = !state.group;

    Charts.barsH($("chart-categories"), rows.map(function (row) {
      var share = total ? percent(row.total_ore / total, 1) : "";
      return {
        label: row.category,
        value: row.total_ore,
        suffix: share,
        note: share + " av totalen"
      };
    }), {
      // Klick på en grupp borrar ner till dess kategorier.
      onSelect: state.group ? null : function (row) {
        state.group = row.label;
        refresh();
      }
    });

    var top = rows.slice(0, STACK_SLOTS - 1).map(function (row) { return row.category; });
    var labels = [];
    var cells = {};
    months.forEach(function (row) {
      if (labels.indexOf(row.month) === -1) labels.push(row.month);
      var name = top.indexOf(row.category) === -1 ? "Övrigt" : row.category;
      cells[row.month + "|" + name] = (cells[row.month + "|" + name] || 0) + row.total_ore;
    });
    labels.sort();

    var names = top.concat(["Övrigt"]);
    var series = names.map(function (name, i) {
      return {
        name: name,
        color: Charts.token("--series-" + (i + 1)),
        values: labels.map(function (month) { return cells[month + "|" + name] || 0; })
      };
    }).filter(function (s) {
      return s.values.some(function (v) { return v > 0; });
    });

    Charts.stackedColumns(
      $("chart-category-months"),
      labels.map(function (m, i) { return monthLabel(m, i, labels.length); }),
      series,
      { titles: labels }
    );

    table($("table-category-months"),
      [{ title: "Månad", value: function (r) { return r.month; } }]
        .concat(series.map(function (s) {
          return {
            title: s.name, num: true,
            value: function (r) { return Charts.formatKr(cells[r.month + "|" + s.name] || 0, 2); }
          };
        })),
      labels.map(function (month) { return { month: month }; }));
  }

  function renderItems(data) {
    table($("table-items"), [
      { title: "Vara", value: function (r) { return r.name; } },
      // Rättning där felet råkar synas. Granskningsvyn är för genomgång.
      { title: "Kategori", cell: function (r) { return categoryPicker(r, r.category); } },
      { title: "Gånger", num: true, value: function (r) { return String(r.times); } },
      { title: "Totalt", num: true, value: function (r) { return Charts.formatKr(r.total_ore, 2); } }
    ], data.items, function (tr, row) {
      tr.className = "clickable" + (state.item === row.name_key ? " is-active" : "");
      tr.tabIndex = 0;
      tr.addEventListener("click", function (event) {
        // Dropdownen ligger i raden; att öppna den ska inte byta prisdiagram.
        if (event.target.tagName === "SELECT") return;
        selectItem(row);
      });
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

    var suggest = document.createElement("button");
    suggest.type = "button";
    suggest.className = "rerun";
    suggest.textContent = "Föreslå kategorier med Claude";
    suggest.addEventListener("click", function () { startJob("/api/job/classify"); });
    box.appendChild(suggest);

    box.appendChild(text("h3", "Okategoriserat"));

    var panel = document.createElement("div");
    panel.className = "bulk";
    box.appendChild(panel);
    renderBulk(panel, "");

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

  /* Kategoriväljaren gör `categorize --unknown`-arbetsflödet till en dropdown.
     Med `current` satt fungerar den även som rättning av en befintlig kategori. */
  function categoryPicker(row, current) {
    var choices = latest.allCategories || [];
    var settable = choices.indexOf(current) !== -1;
    var select = document.createElement("select");
    select.className = "category-picker";
    select.setAttribute("aria-label", "Kategori för " + (row.example_name || row.name));

    // Nuvarande värde behöver inte gå att välja -- "Okategoriserat" finns
    // medvetet inte bland valen. Utan en platshållare för det visar
    // webbläsaren första alternativet i stället, så raden ser ut att ha en
    // kategori den inte har. Det är svårt att upptäcka och lätt att tro på.
    if (!settable) {
      var placeholder = new Option(current || "Välj …", "");
      placeholder.disabled = true;
      select.appendChild(placeholder);
    }
    choices.forEach(function (name) {
      select.appendChild(new Option(name, name));
    });
    if (settable) {
      // Vägen tillbaka: utan den blir en felaktig rättning permanent.
      select.appendChild(new Option("— låt reglerna bestämma —", CLEAR_OVERRIDE));
    }

    // Sätt värdet efter att alternativen finns. new Option(..., selected)
    // förutsätter att värdet är ett av dem.
    select.value = settable ? current : "";

    select.addEventListener("change", function () {
      if (!select.value) return;
      select.disabled = true;
      if (select.value === CLEAR_OVERRIDE) {
        fetch("/api/overrides/" + encodeURIComponent(row.name_key), { method: "DELETE" })
          .then(function () { refresh(); });
        return;
      }
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
    document.querySelectorAll("button.rerun").forEach(function (b) { b.disabled = busy; });
  }

  function renderJob(job) {
    var panel = $("job-panel");
    if (!job.kind) { panel.hidden = true; return; }
    panel.hidden = false;
    $("job-title").textContent = { login: "Loggar in och synkar",
                                    sync: "Synkar",
                                    reparse: "Tolkar om sparad rådata",
                                    classify: "Frågar Claude om kategorier" }[job.kind] || "Jobb";

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
        : job.kind === "classify"
          ? job.result.assigned + " varor kategoriserade (~$" + job.result.cost_usd + ")"
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

  /* ---------- Bulkkategorisering ----------
   * Ett par hundra okända varunamn går inte att beta av med en dropdown per
   * rad. Här kan många markeras och kategoriseras i ett svep, och grupperna
   * bygger på första ordet i varunamnet -- svenska kvittonamn leder med
   * produktordet, så "KYCKLING*" blir en användbar hög direkt.
   */

  function renderBulk(panel, search) {
    fetch("/api/uncategorized?limit=100&search=" + encodeURIComponent(search))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        panel.replaceChildren();

        if (data.groups.length) {
          panel.appendChild(text("p", "Förslag ur dina egna varunamn:", "note"));
          var groups = document.createElement("div");
          groups.className = "bulk-groups";
          data.groups.forEach(function (group) {
            var chip = document.createElement("button");
            chip.type = "button";
            chip.className = "bulk-chip";
            chip.textContent = group.examples[0] +
              (group.name_keys.length > 1 ? "  +" + (group.name_keys.length - 1) + " fler" : "") +
              "  ·  " + Charts.formatKr(group.total_ore);
            chip.title = group.examples.join("\n");
            chip.addEventListener("click", function () {
              applyBulk(panel, group.name_keys, search, group.prefix);
            });
            groups.appendChild(chip);
          });
          panel.appendChild(groups);
        }

        var tools = document.createElement("div");
        tools.className = "bulk-tools";
        var field = document.createElement("input");
        field.type = "search";
        field.placeholder = "Sök vara…";
        field.value = search;
        field.addEventListener("change", function () { renderBulk(panel, field.value); });
        tools.appendChild(field);

        var all = document.createElement("button");
        all.type = "button";
        all.textContent = "Markera alla synliga";
        tools.appendChild(all);

        var picker = document.createElement("select");
        picker.appendChild(new Option("Välj kategori …", ""));
        (latest.allCategories || []).forEach(function (name) {
          picker.appendChild(new Option(name, name));
        });
        tools.appendChild(picker);

        var apply = document.createElement("button");
        apply.type = "button";
        apply.className = "primary";
        apply.textContent = "Sätt kategori";
        tools.appendChild(apply);
        panel.appendChild(tools);

        panel.appendChild(text("p",
          data.total + " okategoriserade varunamn" +
          (data.items.length < data.total ? " (visar " + data.items.length + ")" : ""),
          "note"));

        var boxes = [];
        var list = document.createElement("div");
        list.className = "table-scroll";
        var el = document.createElement("table");
        var head = document.createElement("tr");
        ["", "Vara", "Gånger", "Totalt"].forEach(function (title, i) {
          head.appendChild(text("th", title, i > 1 ? "num" : ""));
        });
        el.appendChild(document.createElement("thead")).appendChild(head);
        var body = document.createElement("tbody");
        data.items.forEach(function (row) {
          var tr = document.createElement("tr");
          var cell = document.createElement("td");
          var check = document.createElement("input");
          check.type = "checkbox";
          check.value = row.name_key;
          check.setAttribute("aria-label", "Markera " + row.example_name);
          boxes.push(check);
          cell.appendChild(check);
          tr.appendChild(cell);
          tr.appendChild(text("td", row.example_name));
          tr.appendChild(text("td", String(row.times), "num"));
          tr.appendChild(text("td", Charts.formatKr(row.total_ore, 2), "num"));
          body.appendChild(tr);
        });
        el.appendChild(body);
        list.appendChild(el);
        panel.appendChild(list);

        all.addEventListener("click", function () {
          boxes.forEach(function (box) { box.checked = true; });
        });
        apply.addEventListener("click", function () {
          var chosen = boxes.filter(function (b) { return b.checked; })
                            .map(function (b) { return b.value; });
          if (!picker.value || !chosen.length) return;
          applyBulk(panel, chosen, search, picker.value);
        });
      });
  }

  function applyBulk(panel, nameKeys, search, category) {
    if (!category || !nameKeys.length) return;
    var chosen = category;
    if (!(latest.allCategories || []).includes(category)) {
      chosen = window.prompt("Kategori för " + nameKeys.length + " varor",
                             (latest.allCategories || [])[0] || "");
      if (!chosen) return;
    }
    fetch("/api/overrides/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name_keys: nameKeys, category: chosen })
    }).then(function (response) {
      if (!response.ok) throw new Error("kunde inte spara");
      refresh();
    }).catch(function (error) { window.alert(error.message); });
  }

  /* ---------- Upplåsning ----------
   * Nyckeln byts mot en cookie via POST, så hemligheten aldrig hamnar i
   * webbläsarhistoriken eller i adressfältets autocomplete. Triggern är en
   * tangentsekvens -- ingenting klickbart och ingenting synligt.
   */

  function watchForUnlockSequence() {
    var target = "oppna";
    var typed = "";
    document.addEventListener("keydown", function (event) {
      if (event.target && /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
      if (event.key.length !== 1) return;
      typed = (typed + event.key.toLowerCase()).slice(-target.length);
      if (typed !== target) return;
      typed = "";
      var key = window.prompt("Nyckel");
      if (!key) return;
      fetch("/api/unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key })
      }).then(function (response) {
        if (response.ok) window.location.href = "/o";
      });
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
    watchForUnlockSequence();
    $("btn-login").addEventListener("click", function () { startJob("/api/job/login"); });
    $("btn-sync").addEventListener("click", function () { startJob("/api/job/sync"); });
    $("btn-reparse").addEventListener("click", function () { startJob("/api/job/reparse"); });
    $("category-back").addEventListener("click", function () {
      state.group = "";
      refresh();
    });
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

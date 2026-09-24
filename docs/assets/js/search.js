---
---
/* Client-side search over search.json. The index is 25 pages, so it is fetched
   once on first interaction and scored in memory — no dependency, no service. */
(function () {
  "use strict";

  var root = document.querySelector("[data-search]");
  if (!root) return;

  var input = root.querySelector(".search__input");
  var panel = root.querySelector(".search__panel");
  var index = null;
  var loading = null;
  var active = -1;

  function load() {
    if (loading) return loading;
    loading = fetch("{{ '/search.json' | relative_url }}")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) { index = data; return data; })
      .catch(function () { index = []; return []; });
    return loading;
  }

  function score(entry, terms) {
    var title = entry.title.toLowerCase();
    var text = entry.text.toLowerCase();
    var total = 0;
    for (var i = 0; i < terms.length; i++) {
      var t = terms[i];
      if (!t) continue;
      var inTitle = title.indexOf(t);
      var inText = text.indexOf(t);
      if (inTitle === -1 && inText === -1) return 0;
      if (inTitle === 0) total += 120;
      else if (inTitle > -1) total += 60;
      if (inText > -1) total += 10;
    }
    return total;
  }

  /* Returns the snippet as alternating plain/marked segments so the caller can
     build it with text nodes. Matching happens on the raw text, and nothing is
     ever parsed as HTML. */
  function segments(entry, term) {
    var text = entry.text;
    var at = text.toLowerCase().indexOf(term);
    var cut = at === -1 ? text.slice(0, 150) : text.slice(Math.max(0, at - 60), Math.max(0, at - 60) + 170);
    var lead = at > 60 ? "…" : "";
    var out = [];
    var rest = cut;
    if (!term) return [{ text: lead + rest, mark: false }];
    var lower = rest.toLowerCase();
    var cursor = 0;
    var found = lower.indexOf(term, cursor);
    while (found !== -1) {
      if (found > cursor) out.push({ text: rest.slice(cursor, found), mark: false });
      out.push({ text: rest.slice(found, found + term.length), mark: true });
      cursor = found + term.length;
      found = lower.indexOf(term, cursor);
    }
    if (cursor < rest.length) out.push({ text: rest.slice(cursor), mark: false });
    if (lead) out.unshift({ text: lead, mark: false });
    return out;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function render(results, terms) {
    active = -1;
    panel.replaceChildren();
    if (!results.length) {
      panel.appendChild(el("p", "search__empty", "No pages match that."));
    } else {
      results.forEach(function (r) {
        var a = el("a", "result");
        a.setAttribute("role", "option");
        a.setAttribute("aria-selected", "false");
        a.setAttribute("href", r.url);
        a.appendChild(el("span", "result__title", r.title));
        var snip = el("span", "result__snippet");
        segments(r, terms[0]).forEach(function (part) {
          snip.appendChild(part.mark ? el("mark", null, part.text) : document.createTextNode(part.text));
        });
        a.appendChild(snip);
        panel.appendChild(a);
      });
    }
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function close() {
    panel.hidden = true;
    panel.replaceChildren();
    active = -1;
    input.setAttribute("aria-expanded", "false");
  }

  function run() {
    var q = input.value.trim().toLowerCase();
    if (q.length < 2) { close(); return; }
    load().then(function () {
      var terms = q.split(/\s+/);
      var hits = [];
      for (var i = 0; i < index.length; i++) {
        var s = score(index[i], terms);
        if (s > 0) hits.push({ s: s, title: index[i].title, url: index[i].url, text: index[i].text });
      }
      hits.sort(function (a, b) { return b.s - a.s; });
      render(hits.slice(0, 8), terms);
    });
  }

  function move(step) {
    var items = panel.querySelectorAll(".result");
    if (!items.length) return;
    if (active > -1) {
      items[active].classList.remove("is-active");
      items[active].setAttribute("aria-selected", "false");
    }
    active = (active + step + items.length) % items.length;
    items[active].classList.add("is-active");
    items[active].setAttribute("aria-selected", "true");
    items[active].scrollIntoView({ block: "nearest" });
  }

  input.addEventListener("input", run);
  input.addEventListener("focus", load);

  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Enter") {
      var items = panel.querySelectorAll(".result");
      if (active > -1 && items[active]) { e.preventDefault(); window.location = items[active].href; }
    } else if (e.key === "Escape") { close(); input.blur(); }
  });

  document.addEventListener("click", function (e) {
    if (!root.contains(e.target)) close();
  });

  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "/" && tag !== "input" && tag !== "textarea") {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });

  /* Mobile navigation drawer. */
  var toggle = document.querySelector("[data-menu]");
  var sidebar = document.getElementById("sidebar");
  var scrim = document.querySelector("[data-scrim]");

  function setMenu(open) {
    if (!sidebar || !toggle) return;
    sidebar.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (scrim) scrim.hidden = !open;
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      setMenu(!sidebar.classList.contains("is-open"));
    });
  }
  if (scrim) scrim.addEventListener("click", function () { setMenu(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setMenu(false);
  });
})();

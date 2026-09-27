/*!
 * Broadsheet embeddable gallery widget. Vendored, offline, no build step.
 * Include with:
 *   <div id="broadsheet-gallery"></div>
 *   <script src="http://<your-broadsheet-host>/embed/<event-slug>/gallery.js"
 *           data-target="#broadsheet-gallery"></script>
 */
(function () {
  var thisScript = document.currentScript;
  if (!thisScript) return;

  var m = thisScript.src.match(/\/embed\/([^/]+)\/gallery\.js/);
  if (!m) return;
  var slug = m[1];
  var origin = thisScript.src.split("/embed/")[0];
  var target = document.querySelector(thisScript.getAttribute("data-target") || "#broadsheet-gallery");
  if (!target) return;

  var css = "#broadsheet-embed{font:14px/1.4 system-ui,sans-serif;color:#1A1A18;"
    + "display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}"
    + "#broadsheet-embed a{color:inherit;text-decoration:none}"
    + "#broadsheet-embed .bs-card{border:1px solid #ccc;padding:12px;background:#F3EEE3}"
    + "#broadsheet-embed .bs-kicker{font:700 11px/1 monospace;letter-spacing:.06em;"
    + "text-transform:uppercase;color:#C8401A;margin-bottom:4px}"
    + "#broadsheet-embed h4{margin:0 0 6px;font-size:16px}"
    + "#broadsheet-embed p{margin:0;color:#555}";
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  target.textContent = "Loading projects…";

  fetch(origin + "/api/events/" + encodeURIComponent(slug) + "/projects")
    .then(function (r) {
      if (!r.ok) throw new Error("request failed: " + r.status);
      return r.json();
    })
    .then(function (projects) {
      var wrap = document.createElement("div");
      wrap.id = "broadsheet-embed";
      if (!projects.length) {
        wrap.textContent = "No projects submitted yet.";
      }
      projects.forEach(function (p) {
        var card = document.createElement("div");
        card.className = "bs-card";
        var kicker = document.createElement("div");
        kicker.className = "bs-kicker";
        kicker.textContent = p.track || "";
        var h = document.createElement("h4");
        var a = document.createElement("a");
        a.href = origin + "/projects/" + encodeURIComponent(p.id);
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = p.title;
        h.appendChild(a);
        var summary = document.createElement("p");
        summary.textContent = p.summary || "";
        card.appendChild(kicker);
        card.appendChild(h);
        card.appendChild(summary);
        wrap.appendChild(card);
      });
      target.textContent = "";
      target.appendChild(wrap);
    })
    .catch(function () {
      target.textContent = "Could not load the Broadsheet gallery.";
    });
})();

/* =========================================================================
   ui.js — reusable UI primitives for PDF Manager.

   Provides a global `UI` object:
     - toast(msg, type)                 notifications
     - openModal(title, bodyEl, footEl) / closeModal()
     - theme init + toggle (persisted in localStorage)
     - dropzone(el, onFiles)            drag-drop + click-to-browse
     - renderFileList(el, files, onRemove)
     - renderThumbnails(container, thumbs, mode, selection)
         modes: "select" | "rearrange" | "crop" | "redact" | "view"
       mutates / reads the shared `selection` object so callers can collect
       the user's page choices, reorder, crop box or redaction boxes.
   Vanilla JS, no dependencies.
   ========================================================================= */
(function (global) {
  "use strict";

  /* ------------------------------ Toasts -------------------------------- */
  function toastWrap() {
    var w = document.getElementById("toastWrap");
    if (!w) {
      w = document.createElement("div");
      w.id = "toastWrap";
      w.className = "toast-wrap";
      document.body.appendChild(w);
    }
    return w;
  }

  function toast(msg, type, timeout) {
    type = type || "info";
    var icons = { ok: "✔", err: "✖", warn: "⚠", info: "ℹ" };
    var el = document.createElement("div");
    el.className = "toast " + type;
    el.innerHTML =
      '<span class="t-icon">' + (icons[type] || icons.info) + "</span>" +
      '<span class="t-msg"></span>' +
      '<button class="t-close" aria-label="Dismiss">×</button>';
    el.querySelector(".t-msg").textContent = msg;
    var close = function () { if (el.parentNode) el.parentNode.removeChild(el); };
    el.querySelector(".t-close").onclick = close;
    toastWrap().appendChild(el);
    setTimeout(close, timeout || (type === "err" ? 6000 : 3800));
  }

  /* ------------------------------- Modal -------------------------------- */
  var modalOverlay = null;

  function ensureModal() {
    if (modalOverlay) return modalOverlay;
    modalOverlay = document.createElement("div");
    modalOverlay.className = "modal-overlay";
    modalOverlay.innerHTML =
      '<div class="modal" role="dialog" aria-modal="true">' +
      '  <div class="modal-head"><h3></h3><button class="close" aria-label="Close">×</button></div>' +
      '  <div class="modal-body"></div>' +
      '  <div class="modal-foot"></div>' +
      "</div>";
    document.body.appendChild(modalOverlay);
    modalOverlay.querySelector(".close").onclick = closeModal;
    modalOverlay.addEventListener("click", function (e) {
      if (e.target === modalOverlay) closeModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeModal();
    });
    return modalOverlay;
  }

  /**
   * Open a modal. bodyEl is a DOM node; footButtons is an array of
   * {label, class, onClick, keepOpen}. Returns nothing; use closeModal().
   */
  function openModal(title, bodyEl, footButtons) {
    var m = ensureModal();
    m.querySelector(".modal-head h3").textContent = title;
    var body = m.querySelector(".modal-body");
    body.innerHTML = "";
    if (typeof bodyEl === "string") body.innerHTML = bodyEl;
    else if (bodyEl) body.appendChild(bodyEl);

    var foot = m.querySelector(".modal-foot");
    foot.innerHTML = "";
    (footButtons || []).forEach(function (b) {
      var btn = document.createElement("button");
      btn.className = "btn " + (b.class || "");
      btn.textContent = b.label;
      btn.onclick = function () {
        var keep = b.onClick ? b.onClick() : false;
        if (!keep && !b.keepOpen) closeModal();
      };
      foot.appendChild(btn);
    });
    m.classList.add("open");
  }

  function closeModal() { if (modalOverlay) modalOverlay.classList.remove("open"); }

  /* ------------------------------- Theme -------------------------------- */
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    var btn = document.getElementById("themeToggle");
    if (btn) btn.textContent = t === "dark" ? "☀" : "☽"; // sun / moon
  }
  function initTheme() {
    var saved = localStorage.getItem("pdfmgr-theme");
    if (!saved) saved = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    applyTheme(saved);
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    localStorage.setItem("pdfmgr-theme", cur);
    applyTheme(cur);
  }

  /* ----------------------------- Progress ------------------------------- */
  /** Create a progress widget; returns {el, set(pct,label), remove()}. */
  function progress(label) {
    var wrap = document.createElement("div");
    wrap.innerHTML =
      '<div class="progress-label"><span class="pl-text"></span><span class="pl-pct"></span></div>' +
      '<div class="progress"><span></span></div>';
    wrap.querySelector(".pl-text").textContent = label || "";
    var bar = wrap.querySelector(".progress > span");
    var pct = wrap.querySelector(".pl-pct");
    return {
      el: wrap,
      set: function (p, text) {
        bar.style.width = Math.max(0, Math.min(100, p)) + "%";
        pct.textContent = p != null ? p + "%" : "";
        if (text != null) wrap.querySelector(".pl-text").textContent = text;
      },
      remove: function () { if (wrap.parentNode) wrap.parentNode.removeChild(wrap); },
    };
  }

  /* ----------------------------- Dropzone ------------------------------- */
  /**
   * Wire a dropzone element + a hidden file input.
   * @param {HTMLElement} zone
   * @param {HTMLInputElement} input
   * @param {(files:File[])=>void} onFiles  called with newly added files
   */
  function dropzone(zone, input, onFiles) {
    zone.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () {
      if (input.files.length) onFiles(Array.prototype.slice.call(input.files));
      input.value = ""; // allow re-selecting the same file
    });
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("drag"); });
    });
    zone.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) onFiles(Array.prototype.slice.call(files));
    });
  }

  /* ---------------------------- File list ------------------------------- */
  function renderFileList(el, files, onRemove) {
    el.innerHTML = "";
    if (!files.length) return;
    files.forEach(function (f, idx) {
      var li = document.createElement("li");
      li.className = "file-item";
      li.innerHTML =
        '<span class="fi-icon">📄</span>' +
        '<span class="grow"><div class="fi-name"></div><div class="fi-size"></div></span>' +
        '<button class="fi-remove" title="Remove">×</button>';
      li.querySelector(".fi-name").textContent = f.name;
      li.querySelector(".fi-size").textContent = API.humanSize(f.size);
      li.querySelector(".fi-remove").onclick = function () { onRemove(idx); };
      el.appendChild(li);
    });
  }

  /* ----------------- Reorderable image thumbnails ----------------------- */
  /**
   * Render the chosen image Files as a draggable thumbnail strip so the user
   * can set their page order (page 1 = first). Reads pixels straight from the
   * File via object URLs — no server round-trip. Mutates nothing itself; the
   * caller supplies callbacks that reorder / remove from its own files array.
   *
   * @param {HTMLElement} container
   * @param {File[]} files
   * @param {{onReorder:(from:number,to:number)=>void, onRemove:(i:number)=>void}} cbs
   */
  function renderImageReorder(container, files, cbs) {
    container.innerHTML = "";
    if (!files.length) return;

    var grid = document.createElement("div");
    grid.className = "thumb-grid";
    container.appendChild(grid);

    files.forEach(function (f, i) {
      var div = document.createElement("div");
      div.className = "thumb";
      div.draggable = true;
      div.dataset.idx = i;
      div.innerHTML =
        '<span class="order-badge">' + (i + 1) + "</span>" +
        '<button class="thumb-remove" title="Remove" aria-label="Remove">×</button>' +
        '<img alt="" loading="lazy">' +
        '<span class="pg-num"></span>';
      var img = div.querySelector("img");
      var url = URL.createObjectURL(f);
      img.src = url;
      img.onload = function () { URL.revokeObjectURL(url); };
      img.onerror = function () { URL.revokeObjectURL(url); };
      var label = f.name.length > 18 ? f.name.slice(0, 16) + "…" : f.name;
      div.querySelector(".pg-num").textContent = label;
      div.querySelector(".thumb-remove").addEventListener("click", function (e) {
        e.stopPropagation();
        cbs.onRemove(i);
      });
      grid.appendChild(div);
    });

    var dragIdx = null;
    grid.querySelectorAll(".thumb").forEach(function (el) {
      el.addEventListener("dragstart", function () { dragIdx = +el.dataset.idx; el.classList.add("dragging"); });
      el.addEventListener("dragend", function () { el.classList.remove("dragging"); });
      el.addEventListener("dragover", function (e) { e.preventDefault(); });
      el.addEventListener("drop", function (e) {
        e.preventDefault();
        var target = +el.dataset.idx;
        if (dragIdx == null || dragIdx === target) return;
        cbs.onReorder(dragIdx, target);
      });
    });
  }

  /* -------------------------- Thumbnail grid ---------------------------- */
  /**
   * Render a thumbnail grid and wire interaction per `mode`.
   *
   * @param {HTMLElement} container
   * @param {Array} thumbs   [{page,url,width,height}]
   * @param {string} mode    "select" | "rearrange" | "crop" | "redact" | "view"
   * @param {object} selection shared state, mutated in place:
   *        - select   -> selection.pages : Set<number>
   *        - rearrange-> selection.order : number[] (1-based, current order)
   *        - crop     -> selection.cropBox : {x0,y0,x1,y1} | null  (+ selection.pages for "which pages")
   *        - redact   -> selection.redactBoxes : [{page,x0,y0,x1,y1}]
   */
  function renderThumbnails(container, thumbs, mode, selection) {
    container.innerHTML = "";
    var grid = document.createElement("div");
    grid.className = "thumb-grid";
    container.appendChild(grid);

    if (mode === "rearrange") {
      if (!selection.order || selection.order.length !== thumbs.length) {
        selection.order = thumbs.map(function (t) { return t.page; });
      }
      renderRearrange(grid, thumbs, selection);
      return;
    }

    thumbs.forEach(function (t) {
      var div = document.createElement("div");
      div.className = "thumb";
      div.dataset.page = t.page;
      div.innerHTML = '<img alt="Page ' + t.page + '" loading="lazy">' +
        '<span class="pg-num">' + t.page + "</span>";
      div.querySelector("img").src = t.url;
      grid.appendChild(div);

      if (mode === "select") {
        if (selection.pages.has(t.page)) div.classList.add("selected");
        div.addEventListener("click", function () {
          if (selection.pages.has(t.page)) { selection.pages.delete(t.page); div.classList.remove("selected"); }
          else { selection.pages.add(t.page); div.classList.add("selected"); }
        });
      } else if (mode === "crop") {
        attachDrawLayer(div, t, selection, false);
      } else if (mode === "redact") {
        attachDrawLayer(div, t, selection, true);
      }
    });
  }

  /* ----- rearrange: drag-drop reorder, keeps selection.order in sync ----- */
  function renderRearrange(grid, thumbs, selection) {
    var byPage = {};
    thumbs.forEach(function (t) { byPage[t.page] = t; });

    function paint() {
      grid.innerHTML = "";
      selection.order.forEach(function (pg, i) {
        var t = byPage[pg];
        var div = document.createElement("div");
        div.className = "thumb";
        div.draggable = true;
        div.dataset.page = pg;
        div.innerHTML =
          '<span class="order-badge">' + (i + 1) + "</span>" +
          '<img alt="Page ' + pg + '" loading="lazy">' +
          '<span class="pg-num">p' + pg + "</span>";
        div.querySelector("img").src = t.url;
        grid.appendChild(div);
      });
      wireDnd();
    }

    var dragPage = null;
    function wireDnd() {
      grid.querySelectorAll(".thumb").forEach(function (el) {
        el.addEventListener("dragstart", function () { dragPage = +el.dataset.page; el.classList.add("dragging"); });
        el.addEventListener("dragend", function () { el.classList.remove("dragging"); });
        el.addEventListener("dragover", function (e) { e.preventDefault(); });
        el.addEventListener("drop", function (e) {
          e.preventDefault();
          var target = +el.dataset.page;
          if (dragPage == null || dragPage === target) return;
          var from = selection.order.indexOf(dragPage);
          var to = selection.order.indexOf(target);
          selection.order.splice(from, 1);
          selection.order.splice(to, 0, dragPage);
          paint();
        });
      });
    }
    paint();
  }

  /* ----- crop/redact: draw normalized boxes over a thumbnail ------------- */
  function attachDrawLayer(thumbDiv, thumb, selection, isRedact) {
    var layer = document.createElement("div");
    layer.className = "draw-layer";
    thumbDiv.appendChild(layer);

    // Redraw any existing boxes for this page (redact only; crop is single global box).
    function repaint() {
      layer.querySelectorAll(".draw-box").forEach(function (b) { b.remove(); });
      if (isRedact) {
        (selection.redactBoxes || []).filter(function (b) { return b.page === thumb.page; })
          .forEach(function (b) { drawBox(b, true); });
      } else if (selection.cropBox && selection.cropPages && selection.cropPages.indexOf(thumb.page) >= 0) {
        drawBox(selection.cropBox, false);
      } else if (selection.cropBox && !selection.cropPages) {
        // crop applies to all pages -> show on every thumb
        drawBox(selection.cropBox, false);
      }
    }
    function drawBox(b, redact) {
      var el = document.createElement("div");
      el.className = "draw-box" + (redact ? " redact" : "");
      el.style.left = (b.x0 * 100) + "%";
      el.style.top = (b.y0 * 100) + "%";
      el.style.width = ((b.x1 - b.x0) * 100) + "%";
      el.style.height = ((b.y1 - b.y0) * 100) + "%";
      layer.appendChild(el);
    }

    var start = null, ghost = null;
    layer.addEventListener("mousedown", function (e) {
      e.preventDefault();
      var r = layer.getBoundingClientRect();
      start = { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
      ghost = document.createElement("div");
      ghost.className = "draw-box" + (isRedact ? " redact" : "");
      layer.appendChild(ghost);
    });
    layer.addEventListener("mousemove", function (e) {
      if (!start) return;
      var r = layer.getBoundingClientRect();
      var cx = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      var cy = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      var x0 = Math.min(start.x, cx), y0 = Math.min(start.y, cy);
      ghost.style.left = (x0 * 100) + "%";
      ghost.style.top = (y0 * 100) + "%";
      ghost.style.width = (Math.abs(cx - start.x) * 100) + "%";
      ghost.style.height = (Math.abs(cy - start.y) * 100) + "%";
    });
    var finish = function (e) {
      if (!start) return;
      var r = layer.getBoundingClientRect();
      var cx = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      var cy = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      var box = {
        x0: +Math.min(start.x, cx).toFixed(4), y0: +Math.min(start.y, cy).toFixed(4),
        x1: +Math.max(start.x, cx).toFixed(4), y1: +Math.max(start.y, cy).toFixed(4),
      };
      start = null;
      if (ghost) { ghost.remove(); ghost = null; }
      if ((box.x1 - box.x0) < 0.01 || (box.y1 - box.y0) < 0.01) { repaint(); return; }
      if (isRedact) {
        selection.redactBoxes = selection.redactBoxes || [];
        selection.redactBoxes.push({ page: thumb.page, x0: box.x0, y0: box.y0, x1: box.x1, y1: box.y1 });
      } else {
        selection.cropBox = box; // single crop box (applied to chosen / all pages)
      }
      repaint();
      if (selection._onDraw) selection._onDraw();
    };
    layer.addEventListener("mouseup", finish);
    layer.addEventListener("mouseleave", function (e) { if (start) finish(e); });

    // double-click clears boxes on this page (redact) or the crop box
    layer.addEventListener("dblclick", function () {
      if (isRedact) {
        selection.redactBoxes = (selection.redactBoxes || []).filter(function (b) { return b.page !== thumb.page; });
      } else { selection.cropBox = null; }
      repaint();
      if (selection._onDraw) selection._onDraw();
    });

    repaint();
  }

  global.UI = {
    toast: toast,
    openModal: openModal,
    closeModal: closeModal,
    initTheme: initTheme,
    toggleTheme: toggleTheme,
    progress: progress,
    dropzone: dropzone,
    renderFileList: renderFileList,
    renderImageReorder: renderImageReorder,
    renderThumbnails: renderThumbnails,
  };
})(window);

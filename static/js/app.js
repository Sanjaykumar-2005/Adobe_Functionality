/* =========================================================================
   app.js — PDF Manager application controller.

   - Defines the 19 tools (definitions + per-tool option schema + request build).
   - Renders the category sidebar.
   - Renders each tool's workspace: upload zone, file list, options panel,
     page-thumbnail preview (select / rearrange / crop / redact), Run action.
   - Wires every tool to its EXACT backend endpoint and shows download links.
   - Implements the Batch panel.

   Depends on: api.js (window.API), ui.js (window.UI).  Vanilla JS only.
   ========================================================================= */
(function () {
  "use strict";

  /* ===================== File-type accept helpers ====================== */
  var ACCEPT_PDF = ".pdf";
  var ACCEPT_WORD = ".doc,.docx";
  var ACCEPT_IMG = ".jpg,.jpeg,.png,.bmp,.tif,.tiff,.webp";

  var FONTS = [
    { value: "helv", label: "Helvetica" },
    { value: "tiro", label: "Times" },
    { value: "cour", label: "Courier" },
  ];
  var POSITIONS = [
    { value: "center", label: "Center" },
    { value: "top-left", label: "Top left" },
    { value: "top-right", label: "Top right" },
    { value: "bottom-left", label: "Bottom left" },
    { value: "bottom-right", label: "Bottom right" },
    { value: "tile", label: "Tile" },
  ];
  var NUM_POSITIONS = [
    { value: "bottom-center", label: "Bottom center" },
    { value: "bottom-left", label: "Bottom left" },
    { value: "bottom-right", label: "Bottom right" },
    { value: "top-center", label: "Top center" },
    { value: "top-left", label: "Top left" },
    { value: "top-right", label: "Top right" },
  ];
  var PAGE_SIZES = ["A4", "A3", "A5", "Letter", "Legal", "Fit"].map(function (s) {
    return { value: s, label: s };
  });

  /* ============================ Tool registry =========================== */
  // Each tool: id, name, category, icon, desc, multi, accept, fileField,
  //   pageMode (null|select|rearrange|crop|redact), pageHint,
  //   options[], custom (fn rendering extra panel),
  //   validate(ws,opts)->err|null, build(fd,opts,ws)
  var TOOLS = [
    /* ----------------------------- CONVERT ---------------------------- */
    {
      id: "word-to-pdf", name: "Word → PDF", category: "Convert", icon: "📝",
      desc: "Convert Word documents (.doc/.docx) into PDF.",
      multi: true, accept: ACCEPT_WORD, fileField: "files",
      endpoint: "/api/convert/word-to-pdf",
      options: [], build: function () {},
    },
    {
      id: "image-to-pdf", name: "Image → PDF", category: "Convert", icon: "🖼️",
      desc: "Combine images into a PDF. Drag the thumbnails to set each image's page order.",
      multi: true, accept: ACCEPT_IMG, fileField: "files", reorderable: true,
      endpoint: "/api/convert/image-to-pdf",
      options: [
        { name: "page_size", label: "Page size", type: "select", choices: PAGE_SIZES, default: "A4" },
        { name: "merge", label: "Merge all images into one PDF", type: "checkbox", default: true },
      ],
      build: function (fd, o) {
        fd.append("page_size", o.page_size);
        fd.append("merge", o.merge ? "true" : "false");
      },
    },
    {
      id: "pdf-to-word", name: "PDF → Word", category: "Convert", icon: "📃",
      desc: "Convert PDF files into editable Word (.docx).",
      multi: true, accept: ACCEPT_PDF, fileField: "files",
      endpoint: "/api/convert/pdf-to-word",
      options: [], build: function () {},
    },

    /* ---------------------------- ORGANIZE ---------------------------- */
    {
      id: "merge", name: "Merge", category: "Organize", icon: "🔗",
      desc: "Combine several PDFs into one. Files merge in the order listed.",
      multi: true, accept: ACCEPT_PDF, fileField: "files",
      endpoint: "/api/organize/merge",
      options: [],
      validate: function (ws) { return ws.files.length < 2 ? "Add at least two PDFs to merge." : null; },
      build: function (fd, o, ws) {
        // order = indices in current upload order
        fd.append("order", JSON.stringify(ws.files.map(function (_, i) { return i; })));
      },
    },
    {
      id: "split", name: "Split", category: "Organize", icon: "✂️",
      desc: "Split a PDF by single pages, ranges, every N pages, or a custom selection.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/split", pageMode: "select",
      pageHint: "For the “Custom pages” mode, click thumbnails to choose pages (or type them below).",
      options: [
        {
          name: "mode", label: "Split mode", type: "select", default: "pages",
          choices: [
            { value: "pages", label: "One PDF per page" },
            { value: "ranges", label: "By ranges (e.g. 1-3,5,8-10)" },
            { value: "every_n", label: "Every N pages" },
            { value: "custom", label: "Custom pages → single PDF" },
          ],
        },
        { name: "ranges", label: "Ranges", type: "text", placeholder: "1-3,5,8-10", default: "", visibleIf: function (v) { return v.mode === "ranges"; } },
        { name: "n", label: "Pages per chunk (N)", type: "number", min: 1, default: 2, visibleIf: function (v) { return v.mode === "every_n"; } },
        { name: "pages", label: "Pages", type: "text", placeholder: "1,3,5", default: "", hint: "Used for Custom mode (or click thumbnails).", visibleIf: function (v) { return v.mode === "custom"; } },
      ],
      validate: function (ws, o) {
        if (o.mode === "ranges" && !o.ranges.trim()) return "Enter at least one range.";
        if (o.mode === "every_n" && (!o.n || o.n < 1)) return "N must be at least 1.";
        if (o.mode === "custom" && !pagesString(ws, o)) return "Select or type the pages to extract.";
        return null;
      },
      build: function (fd, o, ws) {
        fd.append("mode", o.mode);
        if (o.mode === "ranges") fd.append("ranges", o.ranges.trim());
        if (o.mode === "every_n") fd.append("n", String(o.n));
        if (o.mode === "custom") fd.append("pages", pagesString(ws, o));
      },
    },
    {
      id: "rotate", name: "Rotate", category: "Organize", icon: "🔄",
      desc: "Rotate pages. Select specific pages, or leave none selected to rotate all.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/rotate", pageMode: "select",
      pageHint: "Click thumbnails to rotate only those pages. Select none to rotate every page.",
      options: [
        {
          name: "rotation", label: "Rotation", type: "select", default: "90",
          choices: [{ value: "90", label: "90° clockwise" }, { value: "180", label: "180°" }, { value: "270", label: "270° (90° ccw)" }],
        },
        { name: "pages", label: "Pages (optional)", type: "text", placeholder: "1,3,5 — overrides selection", default: "" },
      ],
      build: function (fd, o, ws) {
        fd.append("rotation", o.rotation);
        var pg = pagesString(ws, o);
        if (pg) fd.append("pages", pg);
      },
    },
    {
      id: "rearrange", name: "Rearrange", category: "Organize", icon: "↕️",
      desc: "Drag page thumbnails to reorder the document.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/rearrange", pageMode: "rearrange", autoPreview: true,
      pageHint: "Drag thumbnails to set the new page order, then Run.",
      options: [],
      validate: function (ws) {
        if (!ws.thumbs.length) return "Load the page thumbnails first.";
        if (!ws.selection.order || ws.selection.order.length !== ws.thumbs.length) return "Reorder the pages first.";
        return null;
      },
      build: function (fd, o, ws) { fd.append("order", JSON.stringify(ws.selection.order)); },
    },
    {
      id: "extract", name: "Extract", category: "Organize", icon: "📤",
      desc: "Pull selected pages into a new PDF (original untouched).",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/extract", pageMode: "select", autoPreview: true,
      pageHint: "Click the pages you want to extract (or type them below).",
      options: [{ name: "pages", label: "Pages (optional)", type: "text", placeholder: "1,3,5", default: "" }],
      validate: function (ws, o) { return pagesString(ws, o) ? null : "Select at least one page."; },
      build: function (fd, o, ws) { fd.append("pages", pagesString(ws, o)); },
    },
    {
      id: "delete", name: "Delete Pages", category: "Organize", icon: "🗑️",
      desc: "Remove selected pages and download the rest.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/delete", pageMode: "select", autoPreview: true,
      pageHint: "Click the pages you want to delete (or type them below).",
      options: [{ name: "pages", label: "Pages (optional)", type: "text", placeholder: "2,4", default: "" }],
      validate: function (ws, o) { return pagesString(ws, o) ? null : "Select at least one page to delete."; },
      build: function (fd, o, ws) { fd.append("pages", pagesString(ws, o)); },
    },
    {
      id: "crop", name: "Crop", category: "Organize", icon: "⛶",
      desc: "Draw a crop rectangle on a page; it applies to all pages (or those you type).",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/crop", pageMode: "crop", autoPreview: true,
      pageHint: "Drag on a page preview to draw the crop box. Double-click to clear it.",
      options: [{ name: "pages", label: "Apply to pages (optional)", type: "text", placeholder: "blank = all pages", default: "" }],
      validate: function (ws) { return ws.selection.cropBox ? null : "Draw a crop rectangle on a page first."; },
      build: function (fd, o, ws) {
        fd.append("box", JSON.stringify(ws.selection.cropBox));
        if (o.pages.trim()) fd.append("pages", o.pages.trim());
      },
    },
    {
      id: "compress", name: "Compress", category: "Organize", icon: "🗜️",
      desc: "Reduce PDF file size by downsampling images.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/organize/compress",
      options: [{
        name: "level", label: "Compression level", type: "select", default: "medium",
        choices: [{ value: "low", label: "Low (best quality)" }, { value: "medium", label: "Medium" }, { value: "high", label: "High (smallest)" }],
      }],
      build: function (fd, o) { fd.append("level", o.level); },
    },

    /* ------------------------------ EDIT ------------------------------ */
    {
      id: "edit", name: "Edit Text", category: "Edit", icon: "✏️",
      desc: "Add text, insert images, or erase regions. Coordinates are 0..1 from the top-left.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/edit/text", custom: "edits",
      options: [],
      validate: function (ws) { return (ws.entries && ws.entries.length) ? null : "Add at least one edit."; },
      build: function (fd, o, ws) { buildEntries(fd, ws); },
    },
    {
      id: "fill-sign", name: "Fill & Sign", category: "Edit", icon: "🖊️",
      desc: "Add text fields, dates, checkboxes and signatures (typed or image).",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/edit/fill-sign", custom: "fields",
      options: [],
      validate: function (ws) { return (ws.entries && ws.entries.length) ? null : "Add at least one field."; },
      build: function (fd, o, ws) { buildEntries(fd, ws); },
    },
    {
      id: "redact", name: "Redact", category: "Edit", icon: "⬛",
      desc: "Permanently black-out regions of pages.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/edit/redact", pageMode: "redact", autoPreview: true,
      pageHint: "Drag on a page to add a redaction box (add several). Double-click a page to clear its boxes.",
      options: [],
      validate: function (ws) { return (ws.selection.redactBoxes && ws.selection.redactBoxes.length) ? null : "Draw at least one redaction box."; },
      build: function (fd, o, ws) { fd.append("boxes", JSON.stringify(ws.selection.redactBoxes)); },
    },
    {
      id: "watermark", name: "Watermark", category: "Edit", icon: "💧",
      desc: "Stamp text or an image watermark on every page.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/edit/watermark", custom: "wmimage",
      options: [
        { name: "wm_type", label: "Type", type: "select", default: "text", choices: [{ value: "text", label: "Text" }, { value: "image", label: "Image" }] },
        { name: "text", label: "Text", type: "text", default: "CONFIDENTIAL", visibleIf: function (v) { return v.wm_type === "text"; } },
        { name: "position", label: "Position", type: "select", choices: POSITIONS, default: "center" },
        { name: "opacity", label: "Opacity", type: "range", min: 0.05, max: 1, step: 0.05, default: 0.3 },
        { name: "rotation", label: "Rotation (°)", type: "number", default: 45 },
        { name: "font", label: "Font", type: "select", choices: FONTS, default: "helv", visibleIf: function (v) { return v.wm_type === "text"; } },
        { name: "font_size", label: "Font size", type: "number", default: 48, visibleIf: function (v) { return v.wm_type === "text"; } },
        { name: "color", label: "Color", type: "color", default: "#888888", visibleIf: function (v) { return v.wm_type === "text"; } },
      ],
      validate: function (ws, o) {
        if (o.wm_type === "image" && !ws.extraImage) return "Choose a watermark image.";
        if (o.wm_type === "text" && !o.text.trim()) return "Enter watermark text.";
        return null;
      },
      build: function (fd, o, ws) {
        fd.append("wm_type", o.wm_type);
        fd.append("position", o.position);
        fd.append("opacity", String(o.opacity));
        fd.append("rotation", String(o.rotation));
        if (o.wm_type === "text") {
          fd.append("text", o.text);
          fd.append("font", o.font);
          fd.append("font_size", String(o.font_size));
          fd.append("color", o.color);
        } else if (ws.extraImage) {
          fd.append("image", ws.extraImage);
        }
      },
    },
    {
      id: "page-numbers", name: "Page Numbers", category: "Edit", icon: "#️⃣",
      desc: "Add page numbers with optional prefix/suffix.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/edit/page-numbers",
      options: [
        { name: "position", label: "Position", type: "select", choices: NUM_POSITIONS, default: "bottom-center" },
        { name: "start", label: "Start at", type: "number", default: 1, min: 0 },
        { name: "prefix", label: "Prefix", type: "text", placeholder: "Page ", default: "" },
        { name: "suffix", label: "Suffix", type: "text", placeholder: " / 10", default: "" },
        { name: "font", label: "Font", type: "select", choices: FONTS, default: "helv" },
        { name: "font_size", label: "Font size", type: "number", default: 12 },
        { name: "color", label: "Color", type: "color", default: "#000000" },
      ],
      build: function (fd, o) {
        ["position", "start", "prefix", "suffix", "font", "font_size", "color"].forEach(function (k) {
          fd.append(k, String(o[k]));
        });
      },
    },

    /* ---------------------------- SECURITY ---------------------------- */
    {
      id: "protect", name: "Password Protect", category: "Security", icon: "🔒",
      desc: "Encrypt a PDF and set permissions. At least one password is required.",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      endpoint: "/api/security/protect",
      options: [
        { name: "user_pw", label: "Open password (user)", type: "password", default: "", hint: "Required to open the file." },
        { name: "owner_pw", label: "Owner password (optional)", type: "password", default: "", hint: "Controls permissions; defaults to the open password." },
        { name: "print", label: "Allow printing", type: "checkbox", default: true },
        { name: "modify", label: "Allow editing", type: "checkbox", default: true },
        { name: "copy", label: "Allow copying text", type: "checkbox", default: true },
        { name: "annotate", label: "Allow annotations", type: "checkbox", default: true },
      ],
      validate: function (ws, o) { return (o.user_pw || o.owner_pw) ? null : "Enter at least one password."; },
      build: function (fd, o) {
        fd.append("user_pw", o.user_pw);
        fd.append("owner_pw", o.owner_pw);
        ["print", "modify", "copy", "annotate"].forEach(function (k) { fd.append(k, o[k] ? "true" : "false"); });
      },
    },

    /* ------------------------------- OCR ------------------------------ */
    {
      id: "ocr", name: "OCR", category: "OCR", icon: "🔎",
      desc: "Extract text from a PDF (Azure OCR), then optionally summarize it (LLM).",
      multi: false, accept: ACCEPT_PDF, fileField: "file",
      custom: "ocr", noRun: true,   // actions live in the custom panel (Extract / Summarize)
    },
  ];

  var CATEGORIES = ["Convert", "Organize", "Edit", "Security", "OCR", "Batch"];

  /* ============================ Shared utils =========================== */
  // Pages string from current selection or a manual "pages" option field.
  function pagesString(ws, o) {
    if (o && o.pages && String(o.pages).trim()) return String(o.pages).trim();
    if (ws.selection.pages.size) {
      return Array.from(ws.selection.pages).sort(function (a, b) { return a - b; }).join(",");
    }
    return "";
  }

  function freshSelection() {
    return { pages: new Set(), order: [], cropBox: null, redactBoxes: [] };
  }

  /* ------------------------- Field rendering -------------------------- */
  function fieldEl(def, value) {
    var v = value != null ? value : def.default;
    var wrap = document.createElement("div");
    wrap.className = def.type === "checkbox" ? "field" : "field";
    wrap.setAttribute("data-field", def.name);

    if (def.type === "checkbox") {
      var lbl = document.createElement("label");
      lbl.className = "check";
      var cb = document.createElement("input");
      cb.type = "checkbox"; cb.setAttribute("data-name", def.name); cb.checked = !!v;
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(" " + def.label));
      wrap.appendChild(lbl);
      if (def.hint) wrap.appendChild(hintEl(def.hint));
      return wrap;
    }

    var label = document.createElement("label");
    label.textContent = def.label;
    wrap.appendChild(label);

    var input;
    if (def.type === "select") {
      input = document.createElement("select");
      (def.choices || []).forEach(function (c) {
        var op = document.createElement("option");
        op.value = c.value; op.textContent = c.label;
        if (String(c.value) === String(v)) op.selected = true;
        input.appendChild(op);
      });
    } else if (def.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = 3; input.value = v || "";
    } else if (def.type === "range") {
      input = document.createElement("input");
      input.type = "range";
      input.min = def.min; input.max = def.max; input.step = def.step; input.value = v;
      var rv = document.createElement("span");
      rv.className = "range-val"; rv.textContent = v;
      input.addEventListener("input", function () { rv.textContent = input.value; });
      var rowR = document.createElement("div");
      rowR.className = "row";
      input.setAttribute("data-name", def.name);
      rowR.appendChild(input); rowR.appendChild(rv);
      wrap.appendChild(rowR);
      if (def.hint) wrap.appendChild(hintEl(def.hint));
      return wrap;
    } else {
      input = document.createElement("input");
      input.type = def.type; // text | number | password | color
      input.value = v != null ? v : "";
      if (def.min != null) input.min = def.min;
      if (def.max != null) input.max = def.max;
      if (def.step != null) input.step = def.step;
      if (def.placeholder) input.placeholder = def.placeholder;
    }
    input.setAttribute("data-name", def.name);
    wrap.appendChild(input);
    if (def.hint) wrap.appendChild(hintEl(def.hint));
    return wrap;
  }
  function hintEl(text) { var d = document.createElement("div"); d.className = "hint"; d.textContent = text; return d; }

  function readField(scope, def) {
    var input = scope.querySelector('[data-name="' + def.name + '"]');
    if (!input) return def.default;
    if (def.type === "checkbox") return input.checked;
    if (def.type === "number" || def.type === "range") return input.value === "" ? null : Number(input.value);
    return input.value;
  }
  function readAll(scope, defs) {
    var o = {};
    defs.forEach(function (d) { o[d.name] = readField(scope, d); });
    return o;
  }
  function applyVisibility(scope, defs) {
    var vals = readAll(scope, defs);
    defs.forEach(function (d) {
      if (!d.visibleIf) return;
      var w = scope.querySelector('[data-field="' + d.name + '"]');
      if (w) w.style.display = d.visibleIf(vals) ? "" : "none";
    });
  }

  /* ============================ Workspace ============================== */
  var host; // #workspace element

  function selectTool(tool) {
    // highlight sidebar
    document.querySelectorAll(".tool-link").forEach(function (el) {
      el.classList.toggle("active", el.dataset.tool === tool.id);
    });
    closeSidebarMobile();
    if (tool.id === "batch") return renderBatch();
    renderTool(tool);
  }

  function renderTool(tool) {
    var ws = {
      tool: tool, files: [], thumbs: [], selection: freshSelection(),
      entries: [], extraImage: null,
    };

    host.innerHTML = "";

    // Header
    var head = document.createElement("div");
    head.className = "ws-head";
    head.innerHTML = "<h1></h1><p></p>";
    head.querySelector("h1").textContent = tool.icon + "  " + tool.name;
    head.querySelector("p").textContent = tool.desc;
    host.appendChild(head);

    // Upload card
    var upCard = document.createElement("div");
    upCard.className = "card";
    upCard.innerHTML =
      "<h3>1. Upload</h3>" +
      '<div class="dropzone"><div class="dz-icon">⬆️</div>' +
      '<div class="dz-main">Drag & drop ' + (tool.multi ? "files" : "a file") + " here</div>" +
      '<div class="dz-hint">or click to browse · accepts ' + tool.accept + "</div></div>" +
      '<input type="file" class="hidden" ' + (tool.multi ? "multiple" : "") + ' accept="' + tool.accept + '">' +
      '<ul class="file-list"></ul>' +
      (tool.reorderable ? '<div class="reorder-hint hint" style="margin-top:6px"></div><div class="img-reorder-host"></div>' : "");
    host.appendChild(upCard);
    var zone = upCard.querySelector(".dropzone");
    var input = upCard.querySelector('input[type="file"]');
    var listEl = upCard.querySelector(".file-list");
    var reorderHost = upCard.querySelector(".img-reorder-host");

    function refreshFiles() {
      if (tool.reorderable) {
        // Draggable thumbnail strip; page order = ws.files order (sent as-is).
        listEl.style.display = "none";
        var rh = upCard.querySelector(".reorder-hint");
        if (rh) rh.textContent = ws.files.length
          ? "Drag thumbnails to reorder — the first image becomes page 1."
          : "";
        UI.renderImageReorder(reorderHost, ws.files, {
          onReorder: function (from, to) {
            var moved = ws.files.splice(from, 1)[0];
            ws.files.splice(to, 0, moved);
            refreshFiles();
          },
          onRemove: function (i) {
            ws.files.splice(i, 1);
            refreshFiles(); updateRunState();
          },
        });
        updateRunState();
        return;
      }
      UI.renderFileList(listEl, ws.files, function (i) {
        ws.files.splice(i, 1);
        ws.thumbs = []; ws.selection = freshSelection();
        refreshFiles(); refreshPreview(); updateRunState();
      });
      updateRunState();
    }
    UI.dropzone(zone, input, function (files) {
      if (tool.multi) ws.files = ws.files.concat(files);
      else ws.files = [files[0]];
      ws.thumbs = []; ws.selection = freshSelection();
      refreshFiles(); refreshPreview();
      if (tool.autoPreview && ws.files.length) loadPages();
    });

    // Options card
    var optCard = null, optForm = null;
    if (tool.options && tool.options.length) {
      optCard = document.createElement("div");
      optCard.className = "card";
      optCard.innerHTML = "<h3>2. Options</h3>";
      optForm = document.createElement("div");
      tool.options.forEach(function (d) { optForm.appendChild(fieldEl(d)); });
      optForm.addEventListener("input", function () { applyVisibility(optForm, tool.options); updateRunState(); });
      optForm.addEventListener("change", function () { applyVisibility(optForm, tool.options); });
      applyVisibility(optForm, tool.options);
      optCard.appendChild(optForm);
      host.appendChild(optCard);
    }

    // Custom panels (edit entries / fill-sign / watermark image / ocr detect)
    if (tool.custom) host.appendChild(renderCustomPanel(tool, ws, function () { updateRunState(); }));

    // Preview / page-selection card
    var previewCard = null, previewBody = null;
    if (tool.pageMode) {
      previewCard = document.createElement("div");
      previewCard.className = "card";
      previewCard.innerHTML =
        "<h3>" + (tool.options.length ? "3" : "2") + ". Pages</h3>" +
        '<div class="hint-bar"></div>' +
        '<button class="btn btn-sm load-pages">🖼️ Load page thumbnails</button>' +
        '<div class="preview-body" style="margin-top:12px"></div>';
      previewCard.querySelector(".hint-bar").textContent = tool.pageHint || "";
      previewBody = previewCard.querySelector(".preview-body");
      previewCard.querySelector(".load-pages").onclick = loadPages;
      host.appendChild(previewCard);
    }

    function refreshPreview() { if (previewBody) previewBody.innerHTML = ""; }

    function loadPages() {
      if (!ws.files.length) { UI.toast("Upload a PDF first.", "warn"); return; }
      var btn = previewCard.querySelector(".load-pages");
      var prog = UI.progress("Rendering pages…");
      previewBody.innerHTML = ""; previewBody.appendChild(prog.el); prog.set(40);
      API.preview(ws.files[0], { dpi: 90 }).then(function (res) {
        prog.remove();
        ws.thumbs = res.thumbnails || [];
        if (!ws.thumbs.length) { previewBody.innerHTML = '<div class="empty">No previewable pages (is this a PDF?).</div>'; return; }
        ws.selection = freshSelection();
        ws.selection._onDraw = function () { updateRunState(); };
        UI.renderThumbnails(previewBody, ws.thumbs, tool.pageMode, ws.selection);
        updateRunState();
      }).catch(function (e) { prog.remove(); UI.toast(e.message, "err"); previewBody.innerHTML = ""; });
      void btn;
    }

    // Run + results — suppressed for tools whose actions live in a custom panel.
    var runBtn = null, runStatus = null, resultArea = null;
    if (!tool.noRun) {
      var runCard = document.createElement("div");
      runCard.className = "card";
      runCard.innerHTML =
        '<div class="row"><button class="btn btn-primary run-btn">▶ Run ' + tool.name + "</button>" +
        '<span class="run-status grow"></span></div>' +
        '<div class="result-area"></div>';
      host.appendChild(runCard);
      runBtn = runCard.querySelector(".run-btn");
      runStatus = runCard.querySelector(".run-status");
      resultArea = runCard.querySelector(".result-area");
    }

    function updateRunState() {
      if (!runBtn) return;
      var ready = ws.files.length > 0;
      runBtn.disabled = !ready;
    }

    if (runBtn) runBtn.onclick = function () {
      var opts = optForm ? readAll(optForm, tool.options) : {};
      if (!ws.files.length) { UI.toast("Add a file first.", "warn"); return; }
      var err = tool.validate ? tool.validate(ws, opts) : null;
      if (err) { UI.toast(err, "warn"); return; }

      var fd = new FormData();
      if (tool.fileField === "files") ws.files.forEach(function (f) { fd.append("files", f); });
      else fd.append("file", ws.files[0]);
      try { tool.build(fd, opts, ws); } catch (e) { UI.toast(e.message, "err"); return; }

      runBtn.disabled = true;
      resultArea.innerHTML = "";
      var prog = UI.progress("Uploading…");
      runStatus.innerHTML = "";
      runStatus.appendChild(prog.el);

      API.postForm(tool.endpoint, fd, function (pct) {
        prog.set(pct, pct < 100 ? "Uploading…" : "Processing…");
      }).then(function (res) {
        prog.set(100, "Done");
        setTimeout(function () { prog.remove(); }, 400);
        runStatus.innerHTML = '<span style="color:var(--ok);font-weight:600">✔ Completed</span>';
        UI.toast(tool.name + " completed.", "ok");
        renderResults(resultArea, res);
        runBtn.disabled = false;
      }).catch(function (e) {
        prog.remove();
        runStatus.innerHTML = '<span style="color:var(--err);font-weight:600">✖ Failed</span>';
        UI.toast(e.message, "err");
        runBtn.disabled = false;
      });
    };

    refreshFiles();
    updateRunState();
  }

  /* ---------------------- Results rendering --------------------------- */
  function renderResults(area, res) {
    area.innerHTML = "";
    var files = res.files || [];
    var zip = res.zip || null;

    if (res.warning) {
      var warn = document.createElement("div");
      warn.className = "hint-bar warn-bar";
      warn.style.cssText = "margin:14px 0 4px;border-left:3px solid var(--warn,#e0a800);padding:8px 10px";
      warn.innerHTML = "⚠ " + String(res.warning);
      area.appendChild(warn);
    }

    var header = document.createElement("div");
    header.className = "row";
    header.style.margin = "14px 0 4px";
    header.innerHTML = "<h3 style='margin:0'>Result files</h3>";
    if (files.length > 1 || zip) {
      var allBtn = document.createElement("button");
      allBtn.className = "btn btn-sm";
      allBtn.style.marginLeft = "auto";
      allBtn.textContent = zip ? "⬇ Download ZIP" : "⬇ Download all";
      allBtn.onclick = function () { if (zip) API.download(zip); else API.downloadAll(files); };
      header.appendChild(allBtn);
    }
    area.appendChild(header);

    if (!files.length && !zip) {
      area.appendChild(makeEmpty("Operation succeeded but returned no files."));
      return;
    }

    var list = document.createElement("div");
    list.className = "result-list";
    files.forEach(function (d) { list.appendChild(resultRow(d)); });
    if (zip) list.appendChild(resultRow(zip, true));
    area.appendChild(list);
  }
  function resultRow(d, isZip) {
    var row = document.createElement("div");
    row.className = "result-item";
    row.innerHTML =
      '<span class="ri-icon">' + (isZip ? "🗂️" : "📄") + "</span>" +
      '<span class="grow"><div class="ri-name"></div><div class="ri-size"></div></span>' +
      '<button class="btn btn-sm btn-primary ri-dl">⬇ Download</button>';
    row.querySelector(".ri-name").textContent = d.name;
    row.querySelector(".ri-size").textContent = API.humanSize(d.size);
    row.querySelector(".ri-dl").onclick = function () { API.download(d); };
    return row;
  }
  function makeEmpty(t) { var d = document.createElement("div"); d.className = "empty"; d.textContent = t; return d; }

  /* ------------------- Custom panels (per tool) ----------------------- */
  function renderCustomPanel(tool, ws, onChange) {
    var card = document.createElement("div");
    card.className = "card";

    if (tool.custom === "ocr") {
      card.innerHTML =
        "<h3>Extract &amp; Summarize</h3>" +
        "<p class='hint'>Upload a PDF, extract its text via the selected OCR engine, then optionally summarize it with the LLM.</p>" +
        '<div class="row" style="gap:8px;align-items:center;margin-bottom:10px">' +
        '<label class="hint" style="margin:0">OCR engine</label>' +
        '<select class="ocr-provider"><option>Loading…</option></select></div>' +
        '<div class="row" style="gap:8px">' +
        '<button class="btn btn-sm extract-btn">📝 Extract text</button>' +
        '<button class="btn btn-sm summarize-btn">🧠 Summarize</button></div>' +
        '<div class="ai-out" style="margin-top:12px"></div>';

      // ---- OCR engine picker (Chandra / PaddleOCR / ...) ----
      var providerSel = card.querySelector(".ocr-provider");
      API.getJSON("/api/ocr/providers").then(function (r) {
        providerSel.innerHTML = "";
        (r.providers || []).forEach(function (p) {
          var opt = document.createElement("option");
          opt.value = p.id;
          opt.textContent = p.label + (p.configured ? "" : " (needs setup)");
          opt.disabled = !p.configured;
          if (p.id === r.default && p.configured) opt.selected = true;
          providerSel.appendChild(opt);
        });
        if (!providerSel.value && providerSel.options.length) {
          // default not configured → select the first configured one, if any
          for (var i = 0; i < providerSel.options.length; i++) {
            if (!providerSel.options[i].disabled) { providerSel.selectedIndex = i; break; }
          }
        }
      }).catch(function () {
        providerSel.innerHTML = "<option value=''>(unavailable)</option>";
      });

      // ---- Extract text (OCR) + Summarize (LLM) ----
      var aiOut = card.querySelector(".ai-out");
      var lastText = "";      // cached extracted text — reused by Summarize (no re-OCR)
      var lastSummary = "";

      function esc(s) {
        return String(s).replace(/[&<>"]/g, function (c) {
          return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
      }
      function renderAi() {
        var html = "";
        if (lastText) {
          html += "<h4 style='margin:0 0 6px'>Extracted text</h4>" +
            "<textarea class='ai-text' readonly rows='10' style='width:100%;resize:vertical'>" +
            esc(lastText) + "</textarea>";
        }
        if (lastSummary) {
          html += "<h4 style='margin:12px 0 6px'>Summary</h4>" +
            "<div class='hint-bar' style='white-space:pre-wrap'>" + esc(lastSummary) + "</div>";
        }
        aiOut.innerHTML = html;
      }

      card.querySelector(".extract-btn").onclick = function () {
        if (!ws.files.length) { UI.toast("Upload a PDF first.", "warn"); return; }
        aiOut.innerHTML = '<span class="spinner"></span> Extracting text…';
        var fd = new FormData(); fd.append("file", ws.files[0]);
        if (providerSel.value) fd.append("engine", providerSel.value);
        API.postForm("/api/ocr/extract", fd).then(function (r) {
          lastText = r.extracted_text || ""; lastSummary = "";
          if (!lastText) { aiOut.innerHTML = "<div class='hint-bar'>No text returned.</div>"; return; }
          renderAi();
        }).catch(function (e) { aiOut.innerHTML = ""; UI.toast(e.message, "err"); });
      };

      card.querySelector(".summarize-btn").onclick = function () {
        var fd = new FormData();
        if (lastText) { fd.append("text", lastText); }          // reuse extracted text
        else if (ws.files.length) {                              // OCR (selected engine) then summarize
          fd.append("file", ws.files[0]);
          if (providerSel.value) fd.append("engine", providerSel.value);
        }
        else { UI.toast("Upload a PDF or extract text first.", "warn"); return; }
        aiOut.innerHTML = '<span class="spinner"></span> Summarizing…';
        API.postForm("/api/ocr/summarize", fd).then(function (r) {
          lastText = r.extracted_text || lastText;
          lastSummary = r.summary || "";
          renderAi();
        }).catch(function (e) { renderAi(); UI.toast(e.message, "err"); });
      };
      return card;
    }

    if (tool.custom === "wmimage") {
      card.innerHTML = "<h3>Watermark image (for Image type)</h3>" +
        '<input type="file" accept="' + ACCEPT_IMG + '" class="wm-img"><div class="wm-name hint" style="margin-top:6px"></div>';
      card.querySelector(".wm-img").addEventListener("change", function (e) {
        ws.extraImage = e.target.files[0] || null;
        card.querySelector(".wm-name").textContent = ws.extraImage ? "Selected: " + ws.extraImage.name : "";
        onChange();
      });
      return card;
    }

    // edits / fields (repeatable entry lists)
    var isEdit = tool.custom === "edits";
    card.innerHTML = "<h3>" + (isEdit ? "Edits" : "Fields") + "</h3>" +
      '<div class="row entry-buttons"></div>' +
      '<div class="entry-list" style="margin-top:12px"></div>';
    var btnRow = card.querySelector(".entry-buttons");
    var listEl = card.querySelector(".entry-list");

    var specs = isEdit ? EDIT_SPECS : FILL_SPECS;
    Object.keys(specs).forEach(function (key) {
      var b = document.createElement("button");
      b.className = "btn btn-sm";
      b.textContent = "＋ " + specs[key].label;
      b.onclick = function () { openEntryModal(specs[key], key, function (entry) { ws.entries.push(entry); paintEntries(); onChange(); }); };
      btnRow.appendChild(b);
    });

    function paintEntries() {
      listEl.innerHTML = "";
      if (!ws.entries.length) { listEl.appendChild(makeEmpty("No items yet — add one above.")); return; }
      ws.entries.forEach(function (en, i) {
        var row = document.createElement("div");
        row.className = "result-item";
        row.innerHTML = '<span class="grow"><div class="ri-name"></div><div class="ri-size"></div></span>' +
          '<button class="btn btn-sm fi-remove-btn">Remove</button>';
        row.querySelector(".ri-name").textContent = entrySummary(en);
        row.querySelector(".ri-size").textContent = "page " + en.page;
        row.querySelector(".fi-remove-btn").onclick = function () { ws.entries.splice(i, 1); paintEntries(); onChange(); };
        listEl.appendChild(row);
      });
    }
    paintEntries();
    return card;
  }

  function entrySummary(en) {
    if (en.type === "checkbox") return "☑ Checkbox (" + (en.checked ? "checked" : "unchecked") + ")";
    if (en.type === "delete_region") return "Erase region";
    if (en.type === "add_image" || en.type === "signature_image") return "Image: " + (en._file ? en._file.name : "?");
    return (en.type.replace("_", " ")) + ": “" + (en.text || "") + "”";
  }

  /* Entry field specs for the Edit Text tool */
  var EDIT_SPECS = {
    add_text: {
      label: "Text", type: "add_text",
      fields: [
        numF("page", "Page", 1), numF("x", "X (0..1)", 0.1, 0, 1, 0.01), numF("y", "Y (0..1)", 0.1, 0, 1, 0.01),
        { name: "text", label: "Text", type: "text", default: "" },
        numF("font_size", "Font size", 14), { name: "color", label: "Color", type: "color", default: "#000000" },
        { name: "bold", label: "Bold", type: "checkbox", default: false }, { name: "italic", label: "Italic", type: "checkbox", default: false },
      ],
    },
    delete_region: {
      label: "Erase region", type: "delete_region",
      fields: [numF("page", "Page", 1), numF("x0", "X0", 0.1, 0, 1, 0.01), numF("y0", "Y0", 0.1, 0, 1, 0.01), numF("x1", "X1", 0.5, 0, 1, 0.01), numF("y1", "Y1", 0.2, 0, 1, 0.01)],
    },
    add_image: {
      label: "Image", type: "add_image", image: true,
      fields: [numF("page", "Page", 1), numF("x0", "X0", 0.1, 0, 1, 0.01), numF("y0", "Y0", 0.1, 0, 1, 0.01), numF("x1", "X1", 0.5, 0, 1, 0.01), numF("y1", "Y1", 0.4, 0, 1, 0.01)],
    },
  };
  /* Entry field specs for the Fill & Sign tool */
  var FILL_SPECS = {
    text: { label: "Text", type: "text", fields: [numF("page", "Page", 1), numF("x", "X (0..1)", 0.1, 0, 1, 0.01), numF("y", "Y (0..1)", 0.1, 0, 1, 0.01), { name: "text", label: "Text", type: "text", default: "" }, numF("font_size", "Font size", 12)] },
    date: { label: "Date", type: "date", fields: [numF("page", "Page", 1), numF("x", "X (0..1)", 0.1, 0, 1, 0.01), numF("y", "Y (0..1)", 0.1, 0, 1, 0.01), { name: "text", label: "Date text", type: "text", default: "" }] },
    checkbox: { label: "Checkbox", type: "checkbox", fields: [numF("page", "Page", 1), numF("x", "X (0..1)", 0.1, 0, 1, 0.01), numF("y", "Y (0..1)", 0.1, 0, 1, 0.01), { name: "checked", label: "Checked", type: "checkbox", default: true }] },
    signature_text: { label: "Signature (text)", type: "signature_text", fields: [numF("page", "Page", 1), numF("x", "X (0..1)", 0.1, 0, 1, 0.01), numF("y", "Y (0..1)", 0.1, 0, 1, 0.01), { name: "text", label: "Signature", type: "text", default: "" }, numF("font_size", "Font size", 22)] },
    signature_image: { label: "Signature (image)", type: "signature_image", image: true, fields: [numF("page", "Page", 1), numF("x0", "X0", 0.1, 0, 1, 0.01), numF("y0", "Y0", 0.7, 0, 1, 0.01), numF("x1", "X1", 0.4, 0, 1, 0.01), numF("y1", "Y1", 0.85, 0, 1, 0.01)] },
  };
  function numF(name, label, def, min, max, step) {
    var f = { name: name, label: label, type: "number", default: def };
    if (min != null) f.min = min; if (max != null) f.max = max; if (step != null) f.step = step;
    return f;
  }

  function openEntryModal(spec, typeKey, onAdd) {
    var form = document.createElement("div");
    spec.fields.forEach(function (d) { form.appendChild(fieldEl(d)); });
    var fileInput = null;
    if (spec.image) {
      var fwrap = document.createElement("div");
      fwrap.className = "field";
      fwrap.innerHTML = "<label>Image file</label>";
      fileInput = document.createElement("input");
      fileInput.type = "file"; fileInput.accept = ACCEPT_IMG;
      fwrap.appendChild(fileInput);
      form.appendChild(fwrap);
    }
    UI.openModal("Add " + spec.label, form, [
      { label: "Cancel", class: "btn-ghost" },
      {
        label: "Add", class: "btn-primary", onClick: function () {
          var en = readAll(form, spec.fields);
          en.type = spec.type;
          if (spec.image) {
            if (!fileInput.files[0]) { UI.toast("Choose an image.", "warn"); return true; }
            en._file = fileInput.files[0];
          }
          onAdd(en);
        },
      },
    ]);
  }

  // Append edits/fields JSON + ordered image files for the active tool.
  function buildEntries(fd, ws) {
    var key = ws.tool.id === "edit" ? "edits" : "fields";
    var imgIdx = 0;
    var clean = ws.entries.map(function (en) {
      var copy = {};
      Object.keys(en).forEach(function (k) { if (k !== "_file") copy[k] = en[k]; });
      if (en._file) { fd.append("image_" + imgIdx, en._file); imgIdx++; }
      return copy;
    });
    fd.append(key, JSON.stringify(clean));
  }

  /* ============================== Batch =============================== */
  var BATCH_OPS = [
    { value: "word_to_pdf", label: "Word → PDF" },
    { value: "image_to_pdf", label: "Image → PDF" },
    { value: "pdf_to_word", label: "PDF → Word" },
    { value: "merge", label: "Merge (all into one)" },
    { value: "split", label: "Split" },
    { value: "compress", label: "Compress" },
    { value: "ocr", label: "OCR" },
    { value: "rotate", label: "Rotate" },
    { value: "watermark", label: "Watermark (text)" },
    { value: "page_numbers", label: "Page numbers" },
    { value: "protect", label: "Password protect" },
  ];
  // option fields per batch op (subset of the per-tool options)
  var BATCH_OPT_FIELDS = {
    image_to_pdf: [{ name: "page_size", label: "Page size", type: "select", choices: PAGE_SIZES, default: "A4" }, { name: "merge", label: "Merge per file", type: "checkbox", default: true }],
    split: [{ name: "mode", label: "Mode", type: "select", default: "pages", choices: [{ value: "pages", label: "Per page" }, { value: "every_n", label: "Every N" }, { value: "ranges", label: "Ranges" }] }, { name: "n", label: "N", type: "number", default: 2 }, { name: "ranges", label: "Ranges", type: "text", default: "" }],
    compress: [{ name: "level", label: "Level", type: "select", default: "medium", choices: [{ value: "low", label: "Low" }, { value: "medium", label: "Medium" }, { value: "high", label: "High" }] }],
    ocr: [{ name: "lang", label: "Language", type: "text", default: "eng" }, { name: "force", label: "Force OCR", type: "checkbox", default: false }],
    rotate: [{ name: "rotation", label: "Rotation", type: "select", default: "90", choices: [{ value: "90", label: "90°" }, { value: "180", label: "180°" }, { value: "270", label: "270°" }] }],
    watermark: [{ name: "text", label: "Text", type: "text", default: "CONFIDENTIAL" }, { name: "position", label: "Position", type: "select", choices: POSITIONS, default: "center" }, { name: "opacity", label: "Opacity", type: "range", min: 0.05, max: 1, step: 0.05, default: 0.3 }, { name: "color", label: "Color", type: "color", default: "#888888" }],
    page_numbers: [{ name: "position", label: "Position", type: "select", choices: NUM_POSITIONS, default: "bottom-center" }, { name: "start", label: "Start", type: "number", default: 1 }, { name: "prefix", label: "Prefix", type: "text", default: "" }],
    protect: [{ name: "user_pw", label: "Open password", type: "password", default: "" }, { name: "owner_pw", label: "Owner password", type: "password", default: "" }, { name: "print", label: "Allow print", type: "checkbox", default: true }, { name: "modify", label: "Allow edit", type: "checkbox", default: true }, { name: "copy", label: "Allow copy", type: "checkbox", default: true }, { name: "annotate", label: "Allow annotate", type: "checkbox", default: true }],
  };

  function batchOptionsObject(op, vals) {
    if (op === "image_to_pdf") return { page_size: vals.page_size, merge: !!vals.merge };
    if (op === "split") { var o = { mode: vals.mode }; if (vals.mode === "every_n") o.n = vals.n; if (vals.mode === "ranges") o.ranges = vals.ranges; return o; }
    if (op === "compress") return { level: vals.level };
    if (op === "ocr") return { lang: vals.lang || "eng", force: !!vals.force };
    if (op === "rotate") return { rotation: Number(vals.rotation) };
    if (op === "watermark") return { wm_type: "text", text: vals.text, position: vals.position, opacity: vals.opacity, color: vals.color };
    if (op === "page_numbers") return { position: vals.position, start: vals.start, prefix: vals.prefix };
    if (op === "protect") return { user_pw: vals.user_pw, owner_pw: vals.owner_pw, permissions: { print: !!vals.print, modify: !!vals.modify, copy: !!vals.copy, annotate: !!vals.annotate } };
    return {};
  }

  function renderBatch() {
    host.innerHTML = "";
    var ws = { files: [] };

    var head = document.createElement("div");
    head.className = "ws-head";
    head.innerHTML = "<h1>🧩  Batch Processing</h1><p>Apply one operation to many files at once, then download everything as a ZIP.</p>";
    host.appendChild(head);

    // Upload (mixed types allowed; backend filters per op)
    var upCard = document.createElement("div");
    upCard.className = "card";
    upCard.innerHTML = "<h3>1. Upload files</h3>" +
      '<div class="dropzone"><div class="dz-icon">⬆️</div><div class="dz-main">Drag & drop files</div>' +
      '<div class="dz-hint">or click to browse</div></div>' +
      '<input type="file" class="hidden" multiple><ul class="file-list"></ul>';
    host.appendChild(upCard);
    var listEl = upCard.querySelector(".file-list");
    function refreshFiles() {
      UI.renderFileList(listEl, ws.files, function (i) { ws.files.splice(i, 1); refreshFiles(); });
    }
    UI.dropzone(upCard.querySelector(".dropzone"), upCard.querySelector('input[type="file"]'), function (files) {
      ws.files = ws.files.concat(files); refreshFiles();
    });

    // Operation + options
    var opCard = document.createElement("div");
    opCard.className = "card";
    opCard.innerHTML = "<h3>2. Operation</h3>";
    var opForm = document.createElement("div");
    opForm.appendChild(fieldEl({ name: "operation", label: "Operation", type: "select", choices: BATCH_OPS, default: "compress" }));
    var opOptsHost = document.createElement("div");
    opForm.appendChild(opOptsHost);
    opCard.appendChild(opForm);
    host.appendChild(opCard);

    function renderOpOptions() {
      var op = readField(opForm, { name: "operation", type: "select" });
      opOptsHost.innerHTML = "";
      (BATCH_OPT_FIELDS[op] || []).forEach(function (d) { opOptsHost.appendChild(fieldEl(d)); });
    }
    opForm.querySelector('[data-name="operation"]').addEventListener("change", renderOpOptions);
    renderOpOptions();

    // Run + results
    var runCard = document.createElement("div");
    runCard.className = "card";
    runCard.innerHTML = '<div class="row"><button class="btn btn-primary run-batch">▶ Run batch</button><span class="grow batch-prog"></span></div>' +
      '<div class="batch-results"></div>';
    host.appendChild(runCard);
    var progHost = runCard.querySelector(".batch-prog");
    var out = runCard.querySelector(".batch-results");

    runCard.querySelector(".run-batch").onclick = function () {
      if (!ws.files.length) { UI.toast("Add at least one file.", "warn"); return; }
      var op = readField(opForm, { name: "operation", type: "select" });
      var fieldDefs = BATCH_OPT_FIELDS[op] || [];
      var vals = readAll(opOptsHost, fieldDefs);
      if (op === "protect" && !vals.user_pw && !vals.owner_pw) { UI.toast("Enter a password for protect.", "warn"); return; }

      var fd = new FormData();
      ws.files.forEach(function (f) { fd.append("files", f); });
      fd.append("operation", op);
      fd.append("options", JSON.stringify(batchOptionsObject(op, vals)));

      var btn = runCard.querySelector(".run-batch");
      btn.disabled = true; out.innerHTML = "";
      var prog = UI.progress("Uploading…");
      progHost.innerHTML = ""; progHost.appendChild(prog.el);

      API.postForm("/api/batch/", fd, function (pct) { prog.set(pct, pct < 100 ? "Uploading…" : "Processing files…"); })
        .then(function (res) {
          prog.set(100, "Done"); setTimeout(function () { prog.remove(); }, 400);
          renderBatchResults(out, res);
          UI.toast("Batch finished: " + res.success_count + " ok, " + res.failure_count + " failed.", res.failure_count ? "warn" : "ok");
          btn.disabled = false;
        })
        .catch(function (e) { prog.remove(); UI.toast(e.message, "err"); btn.disabled = false; });
    };

    refreshFiles();
  }

  function renderBatchResults(out, res) {
    out.innerHTML = "";
    var results = res.results || [];

    var counts = document.createElement("div");
    counts.className = "batch-counts";
    counts.innerHTML = '<span class="c-ok">✔ ' + (res.success_count || 0) + " succeeded</span>" +
      '<span class="c-err">✖ ' + (res.failure_count || 0) + " failed</span>";
    out.appendChild(counts);

    if (res.zip) {
      var z = document.createElement("button");
      z.className = "btn btn-primary btn-sm"; z.style.marginTop = "12px";
      z.textContent = "🗂️ Download all (ZIP)";
      z.onclick = function () { API.download(res.zip); };
      out.appendChild(z);
    } else if (res.files && res.files.length) {
      var a = document.createElement("button");
      a.className = "btn btn-sm"; a.style.marginTop = "12px";
      a.textContent = "⬇ Download all";
      a.onclick = function () { API.downloadAll(res.files); };
      out.appendChild(a);
    }

    var table = document.createElement("table");
    table.className = "batch-table";
    table.innerHTML = "<thead><tr><th>File</th><th>Status</th><th>Details</th><th></th></tr></thead><tbody></tbody>";
    var tb = table.querySelector("tbody");
    results.forEach(function (r) {
      var tr = document.createElement("tr");
      var outputs = r.outputs || [];
      tr.innerHTML =
        "<td></td>" +
        '<td><span class="status-pill ' + (r.status === "success" ? "success" : "failed") + '">' + r.status + "</span></td>" +
        "<td></td><td></td>";
      tr.children[0].textContent = r.file;
      tr.children[2].textContent = r.status === "success" ? (outputs.length + " output" + (outputs.length === 1 ? "" : "s")) : (r.reason || "failed");
      if (outputs.length) {
        var dl = document.createElement("button");
        dl.className = "btn btn-sm";
        dl.textContent = "⬇";
        dl.title = "Download outputs";
        dl.onclick = function () { API.downloadAll(outputs); };
        tr.children[3].appendChild(dl);
      }
      tb.appendChild(tr);
    });
    out.appendChild(table);
  }

  /* ============================== Sidebar ============================= */
  function renderSidebar() {
    var nav = document.getElementById("sidebarNav");
    nav.innerHTML = "";
    CATEGORIES.forEach(function (cat) {
      var title = document.createElement("div");
      title.className = "cat-title";
      title.textContent = cat;
      nav.appendChild(title);

      var tools = cat === "Batch"
        ? [{ id: "batch", name: "Batch Processing", icon: "🧩" }]
        : TOOLS.filter(function (t) { return t.category === cat; });

      tools.forEach(function (t) {
        var btn = document.createElement("button");
        btn.className = "tool-link";
        btn.dataset.tool = t.id;
        btn.innerHTML = '<span class="ti">' + t.icon + '</span><span>' + t.name + "</span>";
        btn.onclick = function () {
          var def = t.id === "batch" ? { id: "batch" } : TOOLS.find(function (x) { return x.id === t.id; });
          selectTool(def);
        };
        nav.appendChild(btn);
      });
    });
  }

  /* --------------------------- Mobile sidebar ------------------------- */
  function openSidebarMobile() {
    document.querySelector(".sidebar").classList.add("open");
    document.querySelector(".backdrop").classList.add("show");
  }
  function closeSidebarMobile() {
    document.querySelector(".sidebar").classList.remove("open");
    document.querySelector(".backdrop").classList.remove("show");
  }

  /* =============================== Init =============================== */
  function init() {
    host = document.getElementById("workspace");
    UI.initTheme();
    renderSidebar();

    document.getElementById("themeToggle").onclick = UI.toggleTheme;
    var mt = document.getElementById("menuToggle");
    if (mt) mt.onclick = openSidebarMobile;
    document.querySelector(".backdrop").onclick = closeSidebarMobile;

    // open the first tool by default
    selectTool(TOOLS[0]);
  }

  document.addEventListener("DOMContentLoaded", init);
})();

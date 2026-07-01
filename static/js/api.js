/* =========================================================================
   api.js — thin fetch wrapper around the PDF Manager backend.

   The backend always answers with the JSON envelope:
     success: { "success": true,  "job_id": "...", "files": [{name,url,size}], ...extra }
     error:   { "success": false, "error": "message" }   (HTTP 4xx/5xx)

   Exposes a global `API` object (no modules / build step).
   ========================================================================= */
(function (global) {
  "use strict";

  /**
   * POST a FormData payload and resolve to the parsed JSON envelope.
   * Rejects (throws) with an Error whose message is the backend `error`
   * string (or an HTTP status fallback) when success is false.
   *
   * @param {string} url
   * @param {FormData} formData
   * @param {(pct:number)=>void} [onUpload]  optional upload-progress callback (0..100)
   * @returns {Promise<object>} parsed envelope
   */
  function postForm(url, formData, onUpload) {
    return new Promise(function (resolve, reject) {
      // Use XHR so we can report real upload progress (fetch can't yet).
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);

      if (onUpload && xhr.upload) {
        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable) onUpload(Math.round((e.loaded / e.total) * 100));
        };
      }

      xhr.onload = function () {
        var data;
        try {
          data = JSON.parse(xhr.responseText);
        } catch (err) {
          reject(new Error("Server returned an invalid response (HTTP " + xhr.status + ")."));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.success) {
          resolve(data);
        } else {
          reject(new Error((data && data.error) || ("Request failed (HTTP " + xhr.status + ")")));
        }
      };

      xhr.onerror = function () { reject(new Error("Network error — is the server running?")); };
      xhr.ontimeout = function () { reject(new Error("Request timed out.")); };
      xhr.send(formData);
    });
  }

  /** GET a JSON endpoint and resolve to the parsed envelope (throws on failure). */
  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().then(function (data) {
        if (r.ok && data && data.success) return data;
        throw new Error((data && data.error) || ("Request failed (HTTP " + r.status + ")"));
      });
    });
  }

  /** Build an absolute URL for a file descriptor's relative `url`. */
  function fileURL(descriptor) {
    if (!descriptor || !descriptor.url) return "#";
    return descriptor.url; // backend returns "/download/<job>/<name>"
  }

  /**
   * Trigger a browser download for a single file descriptor {name,url,size}.
   * Uses a hidden <a download> so the /download endpoint's attachment header
   * does the rest.
   */
  function download(descriptor) {
    if (!descriptor || !descriptor.url) return;
    var a = document.createElement("a");
    a.href = descriptor.url;
    a.download = descriptor.name || "";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  /** Download every descriptor in a list (small stagger to avoid popup blocking). */
  function downloadAll(descriptors) {
    (descriptors || []).forEach(function (d, i) {
      setTimeout(function () { download(d); }, i * 250);
    });
  }

  /* --------- Convenience endpoints used by the page-selection UI --------- */

  /**
   * Ask the backend to render thumbnails for a PDF (or basic info for images).
   * @returns {Promise<{job_id,pages,thumbnails:[{page,url,width,height}]}>}
   */
  function preview(file, opts) {
    opts = opts || {};
    var fd = new FormData();
    fd.append("file", file);
    if (opts.dpi) fd.append("dpi", opts.dpi);
    if (opts.pages) fd.append("pages", opts.pages);
    return postForm("/api/preview", fd);
  }

  /** Quick page count for a single PDF. */
  function info(file) {
    var fd = new FormData();
    fd.append("file", file);
    return postForm("/api/info", fd);
  }

  /** Human-readable byte size. */
  function humanSize(bytes) {
    if (bytes == null) return "";
    var u = ["B", "KB", "MB", "GB"], i = 0, n = bytes;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + " " + u[i];
  }

  global.API = {
    postForm: postForm,
    getJSON: getJSON,
    fileURL: fileURL,
    download: download,
    downloadAll: downloadAll,
    preview: preview,
    info: info,
    humanSize: humanSize,
  };
})(window);

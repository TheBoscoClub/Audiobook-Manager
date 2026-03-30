/**
 * Shared API client — centralizes fetch calls, error handling, and toast notifications.
 *
 * Usage:
 *   const data = await api.get('/api/stats');
 *   const result = await api.post('/api/utilities/rescan', {});
 *   const user = await api.put('/auth/account/username', { username: 'new' });
 *   await api.delete('/api/admin/suggestions/5');
 *   await api.patch('/api/user/preferences', { sort_order: 'title_asc' });
 *
 * All methods:
 *   - Include credentials (same-origin cookies)
 *   - Parse JSON responses automatically
 *   - Throw on non-2xx with a descriptive error message
 *   - Optionally show toast notifications on error (pass { toast: false } to suppress)
 *
 * Options (second or third arg depending on method):
 *   - toast:    boolean (default true) — show toast on error
 *   - raw:      boolean (default false) — return the raw Response instead of parsed JSON
 *   - signal:   AbortSignal — pass through to fetch
 *   - headers:  object — merged with defaults
 */
const api = {
  /**
   * Perform a GET request.
   * @param {string} url
   * @param {object} [opts] - { toast, raw, signal, headers }
   * @returns {Promise<object>}
   */
  async get(url, opts) {
    return this._request(url, { method: "GET" }, opts);
  },

  /**
   * Perform a POST request with optional JSON body.
   * @param {string} url
   * @param {object} [data] - request body (will be JSON-stringified)
   * @param {object} [opts] - { toast, raw, signal, headers }
   * @returns {Promise<object>}
   */
  async post(url, data, opts) {
    const fetchOpts = { method: "POST" };
    if (data !== undefined && data !== null) {
      fetchOpts.headers = { "Content-Type": "application/json" };
      fetchOpts.body = JSON.stringify(data);
    }
    return this._request(url, fetchOpts, opts);
  },

  /**
   * Perform a PUT request with optional JSON body.
   * @param {string} url
   * @param {object} [data] - request body (will be JSON-stringified)
   * @param {object} [opts] - { toast, raw, signal, headers }
   * @returns {Promise<object>}
   */
  async put(url, data, opts) {
    const fetchOpts = { method: "PUT" };
    if (data !== undefined && data !== null) {
      fetchOpts.headers = { "Content-Type": "application/json" };
      fetchOpts.body = JSON.stringify(data);
    }
    return this._request(url, fetchOpts, opts);
  },

  /**
   * Perform a PATCH request with optional JSON body.
   * @param {string} url
   * @param {object} [data] - request body (will be JSON-stringified)
   * @param {object} [opts] - { toast, raw, signal, headers }
   * @returns {Promise<object>}
   */
  async patch(url, data, opts) {
    const fetchOpts = { method: "PATCH" };
    if (data !== undefined && data !== null) {
      fetchOpts.headers = { "Content-Type": "application/json" };
      fetchOpts.body = JSON.stringify(data);
    }
    return this._request(url, fetchOpts, opts);
  },

  /**
   * Perform a DELETE request.
   * @param {string} url
   * @param {object} [opts] - { toast, raw, signal, headers }
   * @returns {Promise<object>}
   */
  async delete(url, opts) {
    return this._request(url, { method: "DELETE" }, opts);
  },

  /**
   * Internal: execute a fetch request with standard error handling.
   * @param {string} url
   * @param {object} fetchOpts - fetch init options (method, headers, body)
   * @param {object} [opts]    - api-level options (toast, raw, signal, headers)
   * @returns {Promise<object|Response>}
   */
  async _request(url, fetchOpts, opts) {
    opts = opts || {};
    const useToast = opts.toast !== false;

    // Always include credentials for session cookies
    fetchOpts.credentials = "same-origin";

    // Merge extra headers
    if (opts.headers) {
      fetchOpts.headers = Object.assign({}, fetchOpts.headers || {}, opts.headers);
    }

    // Pass through AbortSignal
    if (opts.signal) {
      fetchOpts.signal = opts.signal;
    }

    // keepalive: ensures request completes even if page unloads (iframe navigation)
    if (opts.keepalive) {
      fetchOpts.keepalive = true;
    }

    const response = await fetch(url, fetchOpts);

    // Return raw Response if requested (for blob downloads, etc.)
    if (opts.raw) {
      if (!response.ok) {
        const msg = await this._extractError(response);
        if (useToast) this._toast(msg, "error");
        throw new Error(msg);
      }
      return response;
    }

    if (!response.ok) {
      const msg = await this._extractError(response);
      if (useToast) this._toast(msg, "error");
      throw new Error(msg);
    }

    // Some endpoints return 204 No Content
    const text = await response.text();
    if (!text) return {};
    return JSON.parse(text);
  },

  /**
   * Extract a human-readable error message from a failed response.
   */
  async _extractError(response) {
    let errorMessage = "HTTP " + response.status + ": " + response.statusText;
    try {
      const data = await response.json();
      if (data.error) {
        errorMessage = data.error;
      } else if (data.message) {
        errorMessage = data.message;
      }
    } catch (_e) {
      // Response wasn't JSON — use default
    }
    return errorMessage;
  },

  /**
   * Show a toast notification. Finds showToast on window or on the library
   * instance, falling back to console.error.
   */
  _toast(message, type) {
    if (typeof showToast === "function") {
      showToast(message, type);
    } else if (typeof window.library !== "undefined" && typeof window.library.showToast === "function") {
      window.library.showToast(message, type);
    } else {
      console.error("[api] " + type + ": " + message);
    }
  },
};

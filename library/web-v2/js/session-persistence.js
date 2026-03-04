/**
 * Multi-layer session persistence.
 * Stores session tokens in localStorage and IndexedDB so the session
 * can be recovered even if the browser clears cookies.
 */
const SessionPersistence = {
  _DB_NAME: "library_auth",
  _STORE_NAME: "session",
  _KEY: "session_token",
  _LS_KEY: "library_session_token",

  async store(token) {
    try {
      localStorage.setItem(this._LS_KEY, token);
    } catch (e) {
      /* localStorage unavailable */
    }
    try {
      await this._idbPut(token);
    } catch (e) {
      /* IndexedDB unavailable */
    }
  },

  async recover() {
    // Try localStorage first (fastest)
    try {
      const lsToken = localStorage.getItem(this._LS_KEY);
      if (lsToken) return lsToken;
    } catch (e) {
      /* localStorage unavailable */
    }
    // Fall back to IndexedDB
    try {
      return await this._idbGet();
    } catch (e) {
      return null;
    }
  },

  async clear() {
    try {
      localStorage.removeItem(this._LS_KEY);
    } catch (e) {}
    try {
      await this._idbDelete();
    } catch (e) {}
  },

  _openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(this._DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(this._STORE_NAME)) {
          db.createObjectStore(this._STORE_NAME);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  },

  async _idbPut(token) {
    const db = await this._openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(this._STORE_NAME, "readwrite");
      tx.objectStore(this._STORE_NAME).put(token, this._KEY);
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    });
  },

  async _idbGet() {
    const db = await this._openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(this._STORE_NAME, "readonly");
      const req = tx.objectStore(this._STORE_NAME).get(this._KEY);
      req.onsuccess = () => {
        db.close();
        resolve(req.result || null);
      };
      req.onerror = () => {
        db.close();
        reject(req.error);
      };
    });
  },

  async _idbDelete() {
    const db = await this._openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(this._STORE_NAME, "readwrite");
      tx.objectStore(this._STORE_NAME).delete(this._KEY);
      tx.oncomplete = () => {
        db.close();
        resolve();
      };
      tx.onerror = () => {
        db.close();
        reject(tx.error);
      };
    });
  },
};

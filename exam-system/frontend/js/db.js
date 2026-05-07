/**
 * IndexedDB wrapper for offline caching of answers.
 * Answers submitted while offline are queued and synced on reconnect.
 */
const ExamDB = (() => {
  const DB_NAME = "exam_offline_cache";
  const DB_VERSION = 1;
  let db = null;

  async function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const d = e.target.result;
        if (!d.objectStoreNames.contains("pending_answers")) {
          d.createObjectStore("pending_answers", { keyPath: "id", autoIncrement: true });
        }
        if (!d.objectStoreNames.contains("session_state")) {
          d.createObjectStore("session_state", { keyPath: "key" });
        }
      };
      req.onsuccess = (e) => { db = e.target.result; resolve(db); };
      req.onerror = (e) => reject(e);
    });
  }

  async function getDB() {
    if (!db) await open();
    return db;
  }

  async function queueAnswer(answer) {
    const d = await getDB();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("pending_answers", "readwrite");
      tx.objectStore("pending_answers").add(answer);
      tx.oncomplete = resolve;
      tx.onerror = reject;
    });
  }

  async function getPendingAnswers() {
    const d = await getDB();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("pending_answers", "readonly");
      const req = tx.objectStore("pending_answers").getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = reject;
    });
  }

  async function clearPendingAnswers() {
    const d = await getDB();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("pending_answers", "readwrite");
      tx.objectStore("pending_answers").clear();
      tx.oncomplete = resolve;
      tx.onerror = reject;
    });
  }

  async function saveState(key, value) {
    const d = await getDB();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("session_state", "readwrite");
      tx.objectStore("session_state").put({ key, value });
      tx.oncomplete = resolve;
      tx.onerror = reject;
    });
  }

  async function getState(key) {
    const d = await getDB();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("session_state", "readonly");
      const req = tx.objectStore("session_state").get(key);
      req.onsuccess = () => resolve(req.result ? req.result.value : null);
      req.onerror = reject;
    });
  }

  async function clearState() {
    const d = await getDB();
    return new Promise((resolve, reject) => {
      const tx = d.transaction("session_state", "readwrite");
      tx.objectStore("session_state").clear();
      tx.oncomplete = resolve;
      tx.onerror = reject;
    });
  }

  return { open, queueAnswer, getPendingAnswers, clearPendingAnswers, saveState, getState, clearState };
})();

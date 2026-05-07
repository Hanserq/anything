/**
 * Anti-cheat monitor.
 * Monitors tab switching, focus loss, fullscreen exit.
 * Reports violations to the server via provided callback.
 */
class AntiCheat {
  constructor({ onViolation, maxStrikes = 3, onLocked }) {
    this.onViolation = onViolation;
    this.maxStrikes = maxStrikes;
    this.onLocked = onLocked;
    this.strikes = 0;
    this.active = false;
    this._handlers = {};
  }

  start() {
    this.active = true;
    this._bindEvents();
    this._requestFullscreen();
    console.log("[AntiCheat] Monitoring started");
  }

  stop() {
    this.active = false;
    this._unbindEvents();
    console.log("[AntiCheat] Monitoring stopped");
  }

  _bindEvents() {
    this._handlers.visibility = () => {
      if (!this.active) return;
      if (document.hidden) this._report("tab_switch", "Tab/window switched");
    };
    this._handlers.blur = () => {
      if (!this.active) return;
      this._report("focus_loss", "Window lost focus");
    };
    this._handlers.fullscreenChange = () => {
      if (!this.active) return;
      if (!document.fullscreenElement) {
        this._report("fullscreen_exit", "Fullscreen exited");
      }
    };
    this._handlers.paste = (e) => {
      if (!this.active) return;
      e.preventDefault();
      this._report("paste_attempt", "Paste blocked");
    };
    this._handlers.contextmenu = (e) => { e.preventDefault(); };
    this._handlers.keydown = (e) => {
      // Block common shortcuts
      if ((e.ctrlKey || e.metaKey) && ["c","v","u","a","p"].includes(e.key.toLowerCase())) {
        e.preventDefault();
      }
      // F12, DevTools detection attempts
      if (e.key === "F12") e.preventDefault();
    };

    document.addEventListener("visibilitychange", this._handlers.visibility);
    window.addEventListener("blur", this._handlers.blur);
    document.addEventListener("fullscreenchange", this._handlers.fullscreenChange);
    document.addEventListener("paste", this._handlers.paste);
    document.addEventListener("contextmenu", this._handlers.contextmenu);
    document.addEventListener("keydown", this._handlers.keydown);
  }

  _unbindEvents() {
    document.removeEventListener("visibilitychange", this._handlers.visibility);
    window.removeEventListener("blur", this._handlers.blur);
    document.removeEventListener("fullscreenchange", this._handlers.fullscreenChange);
    document.removeEventListener("paste", this._handlers.paste);
    document.removeEventListener("contextmenu", this._handlers.contextmenu);
    document.removeEventListener("keydown", this._handlers.keydown);
  }

  _requestFullscreen() {
    const el = document.documentElement;
    if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
  }

  _report(type, description) {
    this.strikes++;
    console.warn(`[AntiCheat] Violation: ${type} | Strike ${this.strikes}/${this.maxStrikes}`);
    if (this.onViolation) {
      this.onViolation({ violation_type: type, description, strike: this.strikes });
    }
    if (this.strikes >= this.maxStrikes && this.onLocked) {
      this.onLocked(this.strikes);
    }
  }

  setStrikes(n) {
    this.strikes = n;
  }
}

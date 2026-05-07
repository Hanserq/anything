/**
 * WebSocket Client with auto-reconnect and offline cache sync.
 * Reconnects exponentially up to MAX_DELAY ms.
 */
class ExamWSClient {
  constructor({ url, onMessage, onOpen, onClose, onError }) {
    this.url = url;
    this.onMessage = onMessage;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.onError = onError;
    this.ws = null;
    this.reconnectAttempt = 0;
    this.maxDelay = 16000;
    this.intentionalClose = false;
    this._pingInterval = null;
  }

  connect() {
    this.intentionalClose = false;
    this._doConnect();
  }

  _doConnect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;
    console.log(`[WS] Connecting to ${this.url} (attempt ${this.reconnectAttempt + 1})`);
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log("[WS] Connected");
      this.reconnectAttempt = 0;
      this._startPing();
      if (this.onOpen) this.onOpen();
    };

    this.ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (this.onMessage) this.onMessage(msg);
      } catch (err) {
        console.error("[WS] Message parse error", err);
      }
    };

    this.ws.onclose = (e) => {
      this._stopPing();
      if (this.onClose) this.onClose(e);
      if (!this.intentionalClose) this._scheduleReconnect();
    };

    this.ws.onerror = (e) => {
      console.error("[WS] Error", e);
      if (this.onError) this.onError(e);
    };
  }

  _scheduleReconnect() {
    const delay = Math.min(500 * Math.pow(2, this.reconnectAttempt), this.maxDelay);
    this.reconnectAttempt++;
    console.log(`[WS] Reconnecting in ${delay}ms...`);
    setTimeout(() => this._doConnect(), delay);
  }

  _startPing() {
    this._pingInterval = setInterval(() => {
      this.send({ type: "heartbeat" });
    }, 20000);
  }

  _stopPing() {
    if (this._pingInterval) {
      clearInterval(this._pingInterval);
      this._pingInterval = null;
    }
  }

  send(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  close() {
    this.intentionalClose = true;
    this._stopPing();
    if (this.ws) this.ws.close();
  }

  get isConnected() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }
}

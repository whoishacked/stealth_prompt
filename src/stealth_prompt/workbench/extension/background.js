/**
 * Service worker: the only component that talks to the Python broker.
 *
 * The content script runs inside a page the target application controls, so it
 * is never given the broker token. It exchanges messages with this worker over
 * `chrome.runtime`, and this worker holds the WebSocket.
 *
 * `config.js` is generated per session by the Python launcher and carries the
 * broker port, the one-time token, and the target origin.
 */

/* global WB_CONFIG */
importScripts('config.js');

let socket = null;
let connected = false;
/** @type {Set<chrome.runtime.Port>} */
const ports = new Set();
/**
 * The one tab this run drives. Broker commands go here and nowhere else.
 *
 * Broadcasting to every matching tab is how one operation gets executed twice:
 * open the target in two tabs and both content scripts would fill, submit, and
 * report. The first content script to complete the handshake wins the binding;
 * every other tab is told it is a bystander and stays inert.
 */
let boundPortId = null;
let boundPageId = null;
/** @type {Array<string>} */
const outbox = [];

function nextPageId() {
  return 'page-' + Math.random().toString(36).slice(2, 10);
}

/** Send to every dock, for connection state only. */
function broadcast(message) {
  for (const port of ports) {
    try {
      port.postMessage(message);
    } catch (err) {
      ports.delete(port);
    }
  }
}

/** Send only to the bound tab. */
function toBoundTab(message) {
  for (const port of ports) {
    if (port.__wbPortId === boundPortId) {
      try {
        port.postMessage(message);
      } catch (err) {
        ports.delete(port);
      }
      return true;
    }
  }
  return false;
}

function connect() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const url = `${WB_CONFIG.brokerUrl}?token=${encodeURIComponent(WB_CONFIG.token)}`;
  socket = new WebSocket(url);

  socket.addEventListener('open', () => {
    connected = true;
    broadcast({ channel: 'broker-state', connected: true });
    send({ type: 'hello', payload: { extensionVersion: '0.1.0' } });
    while (outbox.length) {
      socket.send(outbox.shift());
    }
  });

  socket.addEventListener('message', (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch (err) {
      return;
    }
    // Commands that act on the page go to the bound tab alone; status-shaped
    // frames are safe to mirror to any open dock.
    const actionable = frame && frame.type === 'perform_operation';
    if (actionable) {
      toBoundTab({ channel: 'broker-frame', frame });
    } else {
      broadcast({ channel: 'broker-frame', frame });
    }
  });

  socket.addEventListener('close', () => {
    connected = false;
    socket = null;
    broadcast({ channel: 'broker-state', connected: false });
  });

  socket.addEventListener('error', () => {
    broadcast({ channel: 'broker-state', connected: false, error: true });
  });
}

function send(message) {
  const raw = JSON.stringify(message);
  if (raw.length > WB_CONFIG.maxMessageBytes) {
    broadcast({
      channel: 'broker-frame',
      frame: {
        type: 'error',
        payload: { code: 'too_large', message: 'message exceeds the size limit' },
      },
    });
    return;
  }
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(raw);
  } else {
    outbox.push(raw);
    connect();
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'stealth-prompt-dock') {
    port.disconnect();
    return;
  }
  port.__wbPortId = nextPageId();
  ports.add(port);

  const isFirst = boundPortId === null;
  if (isFirst) {
    boundPortId = port.__wbPortId;
    boundPageId = port.__wbPortId;
  }

  port.onDisconnect.addListener(() => {
    ports.delete(port);
    if (port.__wbPortId === boundPortId) {
      // The active tab went away (closed or navigated). Do not silently
      // promote another tab: the run is bound to a page, and the broker is
      // told so it can stop rather than act on a different page.
      boundPortId = null;
      boundPageId = null;
      send({ type: 'run_control', payload: { action: 'stop', reason: 'tab_closed' } });
    }
  });

  port.onMessage.addListener((message) => {
    if (!message || typeof message !== 'object') {
      return;
    }
    if (message.channel === 'to-broker' && message.frame) {
      // Only the bound tab may originate frames that affect the run.
      if (port.__wbPortId !== boundPortId) {
        port.postMessage({ channel: 'bystander' });
        return;
      }
      const frame = message.frame;
      frame.payload = Object.assign({}, frame.payload, { page_id: boundPageId });
      send(frame);
      return;
    }
    if (message.channel === 'config-request') {
      port.postMessage({
        channel: 'config',
        targetOrigin: WB_CONFIG.targetOrigin,
        connected,
        bound: port.__wbPortId === boundPortId,
        pageId: boundPageId,
      });
    }
  });

  port.postMessage({
    channel: 'broker-state',
    connected,
    bound: port.__wbPortId === boundPortId,
  });
  connect();
});

connect();

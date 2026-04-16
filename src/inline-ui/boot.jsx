import React from "react";
import { createRoot } from "react-dom/client";
import { InlineCardsApp } from "./InlineCardsApp";
import "./inline-ui.css";

const roots = new Map();

function ensureRoot(sessionId, mountNode) {
  const existing = roots.get(sessionId);
  if (existing && existing.mountNode === mountNode && mountNode.isConnected) {
    return existing.root;
  }

  if (existing) {
    existing.root.unmount();
    roots.delete(sessionId);
  }

  const root = createRoot(mountNode);
  roots.set(sessionId, {
    mountNode,
    root,
  });

  return root;
}

function render(payload) {
  if (!payload?.mountNode || !payload.sessionId) {
    return;
  }

  const root = ensureRoot(payload.sessionId, payload.mountNode);
  root.render(<InlineCardsApp {...payload} />);
}

function dispose(sessionId) {
  const existing = roots.get(sessionId);
  if (!existing) return;
  existing.root.unmount();
  roots.delete(sessionId);
}

function disposeAll() {
  for (const sessionId of roots.keys()) {
    dispose(sessionId);
  }
}

window.Verity = window.Verity || {};
window.Verity.reactUiBridge = {
  render,
  dispose,
  disposeAll,
};

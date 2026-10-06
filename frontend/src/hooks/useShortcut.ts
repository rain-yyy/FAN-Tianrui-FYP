"use client";

import { useEffect } from "react";

/** Keys typed into a field, or pressed while a modal is open, are not shortcuts. */
function ignoreTarget(target: EventTarget | null): boolean {
  if (document.querySelector("dialog:modal")) return true;
  return (
    target instanceof Element &&
    target.closest(
      "input, textarea, select, [contenteditable=''], [contenteditable='true']",
    ) !== null
  );
}

/**
 * Single-key page shortcut (`m`, `/`, `?`). Letters match either case;
 * Ctrl, Alt and Cmd combinations are left to the browser. Pass a stable
 * handler (useCallback) to avoid resubscribing on every render.
 */
export function useShortcut(key: string, handler: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const letter = /^[a-z]$/i.test(key);
    const onKeyDown = (event: KeyboardEvent) => {
      const matches = letter
        ? event.key.toLowerCase() === key.toLowerCase()
        : event.key === key;
      if (!matches) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.repeat || event.defaultPrevented) return;
      if (ignoreTarget(event.target)) return;
      event.preventDefault();
      handler();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [key, handler, enabled]);
}

"use client";

import { X } from "lucide-react";
import { useModalDialog } from "@/hooks/useModalDialog";
import { type DictKey, t } from "@/lib/i18n";

const SHORTCUTS: ReadonlyArray<[string, DictKey]> = [
  ["/", "shortcutAsk"],
  ["Esc", "shortcutClose"],
  ["?", "shortcutHelp"],
];

/** Keyboard shortcuts, opened with `?` from the top bar. */
export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  const dialogProps = useModalDialog(onClose);
  return (
    <dialog
      {...dialogProps}
      aria-labelledby="shortcut-help-title"
      className="m-auto w-[min(26rem,calc(100vw-2rem))] border border-n-4 bg-sheet p-0 text-ink backdrop:bg-ink/40"
    >
      <div className="flex h-12 items-center justify-between border-n-3 border-b pr-2 pl-5">
        <h2 id="shortcut-help-title" className="font-medium text-sm">
          {t("shortcutTitle")}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("shortcutCloseHelp")}
          className="inline-flex h-8 w-8 items-center justify-center text-n-6 hover:bg-n-1 hover:text-ink"
        >
          <X aria-hidden className="h-4 w-4" />
        </button>
      </div>
      <dl className="grid grid-cols-[auto_1fr] items-baseline gap-x-5 gap-y-3 px-5 py-5 text-sm">
        {SHORTCUTS.map(([key, label]) => (
          <div key={key} className="contents">
            <dt>
              <kbd className="inline-block min-w-7 border border-n-4 px-1.5 py-0.5 text-center font-mono text-ink text-xs">
                {key}
              </kbd>
            </dt>
            <dd className="text-n-8">{t(label)}</dd>
          </div>
        ))}
      </dl>
    </dialog>
  );
}

"use client";

import {
  type MouseEvent,
  type SyntheticEvent,
  useLayoutEffect,
  useRef,
} from "react";

/**
 * Opens a native `<dialog>` as a modal for as long as the component is
 * mounted: focus trap, Esc and an inert page come with `showModal()`.
 *
 * It opens in a layout effect, so children can measure it in their own
 * effects. React removes the element before the cleanup runs, so the
 * browser's own focus restore never fires; focus goes back to the opener by
 * hand. Spread the returned props onto the `<dialog>`.
 */
export function useModalDialog(onClose: () => void) {
  const ref = useRef<HTMLDialogElement>(null);

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  return {
    ref,
    onCancel: (event: SyntheticEvent<HTMLDialogElement>) => {
      event.preventDefault();
      onClose();
    },
    // A click on the dialog element itself is a click on the backdrop.
    onClick: (event: MouseEvent<HTMLDialogElement>) => {
      if (event.target === ref.current) onClose();
    },
  };
}

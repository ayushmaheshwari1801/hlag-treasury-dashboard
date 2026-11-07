import { useEffect, useRef, type ReactNode } from "react";

let openDrawers = 0, originalOverflow = "";
const focusSelector = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), textarea:not([disabled]), select:not([disabled]), summary, [tabindex]';
function focusableControls(dialog: HTMLElement) {
  return Array.from(dialog.querySelectorAll<HTMLElement>(focusSelector)).filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0);
}
function topModalDialog(exclude?: HTMLElement) {
  const visible = Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]')).filter(element => element !== exclude && element.getClientRects().length > 0);
  const layer = (element: HTMLElement) => {
    for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
      const z = Number.parseInt(getComputedStyle(parent).zIndex, 10);
      if (Number.isFinite(z)) return z;
    }
    return 0;
  };
  return visible.reduce<HTMLElement | undefined>((top, element) => !top || layer(element) >= layer(top) ? element : top, undefined);
}
function attachDrawerAccessibility(dialog: HTMLElement, onClose: () => void) {
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  if (!openDrawers++) originalOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  const focusFirst = () => (focusableControls(dialog)[0] ?? dialog).focus({ preventScroll: true });
  if (topModalDialog() === dialog) focusFirst();
  const keydown = (event: KeyboardEvent) => {
    if (topModalDialog() !== dialog) return;
    if (event.key === "Escape") { event.preventDefault(); onClose(); }
    if (event.key === "Tab") {
      const controls = focusableControls(dialog), first = controls[0], last = controls[controls.length - 1];
      if (!first) { event.preventDefault(); dialog.focus(); }
      else if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }
  };
  const focusin = (event: FocusEvent) => { if (topModalDialog() === dialog && !dialog.contains(event.target as Node)) focusFirst(); };
  document.addEventListener("keydown", keydown);
  document.addEventListener("focusin", focusin);
  return () => {
    document.removeEventListener("keydown", keydown); document.removeEventListener("focusin", focusin);
    if (!--openDrawers) document.body.style.overflow = originalOverflow;
    const remaining = topModalDialog(dialog);
    if (previousFocus?.isConnected && (!remaining || remaining.contains(previousFocus))) previousFocus.focus({ preventScroll: true });
  };
}

export function Drawer({ title, onClose, children, wide, modal }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; modal?: boolean }) {
  const dialog = useRef<HTMLElement>(null), closeAction = useRef(onClose);
  closeAction.current = onClose;
  useEffect(() => {
    if (dialog.current) return attachDrawerAccessibility(dialog.current, () => closeAction.current());
  }, []);
  return (
    <div className="overlay" onClick={onClose}>
      <aside ref={dialog} tabIndex={-1} className={`${modal ? "modal" : "drawer"} ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}>
        <header><h2>{title}</h2><button className="x" onClick={onClose} aria-label="Close">{"✕"}</button></header>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function Prov({ p }: { p?: string | null }) {
  if (!p) return null;
  const cls = p === "OBSERVED" ? "ok" : p === "UNAVAILABLE" ? "bad" : "warn";
  return <span className={`chip ${cls}`}>{p}</span>;
}
export function StatusChip({ s }: { s: string }) {
  return <span className={`chip st-${s.replace(/\s/g, "").toLowerCase()}`}>{s}</span>;
}

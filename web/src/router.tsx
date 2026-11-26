/**
 * A small client-side router: the address bar is the state. The server answers
 * every app path with index.html, so links, reloads and bookmarks all work.
 */

import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
window.addEventListener('popstate', notify);

type LeaveGuard = () => Promise<unknown> | void;
let leaveGuard: LeaveGuard | null = null;

/**
 * Lets a screen finish something (like saving) before the app moves to another screen.
 * Returns a function that removes the guard. One at a time: the newest replaces any other.
 */
export function onLeave(guard: LeaveGuard): () => void {
  leaveGuard = guard;
  return () => {
    if (leaveGuard === guard) leaveGuard = null;
  };
}

/** Go to `to` within the app, once any `onLeave` guard has finished. */
export function navigate(to: string, { replace = false } = {}): void {
  if (to === location.pathname + location.search) return;
  const go = () => {
    history[replace ? 'replaceState' : 'pushState'](null, '', to);
    notify();
  };
  const guard = leaveGuard;
  if (!guard) return go();
  leaveGuard = null;
  Promise.resolve()
    .then(guard)
    .catch(() => undefined) // a failed save still lets you leave; the editor showed the error
    .finally(go);
}

export function usePath(): string {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const update = () => setPath(location.pathname);
    listeners.add(update);
    return () => {
      listeners.delete(update);
    };
  }, []);
  return path;
}

/** `match('/campaigns/:id', '/campaigns/cmp_1')` → `{ id: 'cmp_1' }`; null when it doesn't match. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const want = pattern.split('/').filter(Boolean);
  const got = path.split('/').filter(Boolean);
  if (want.length !== got.length) return null;
  const params: Record<string, string> = {};
  for (const [i, part] of want.entries()) {
    if (part.startsWith(':')) params[part.slice(1)] = decodeURIComponent(got[i]!);
    else if (part !== got[i]) return null;
  }
  return params;
}

interface LinkProps {
  href: string;
  children: ComponentChildren;
  class?: string;
  'aria-current'?: 'page';
  'aria-label'?: string;
}

/** An ordinary link that navigates in-app on a plain left click. */
export function Link({ href, children, ...rest }: LinkProps) {
  const onClick = (event: MouseEvent) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(href);
  };
  return (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  );
}

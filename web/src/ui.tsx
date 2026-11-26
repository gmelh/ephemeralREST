/** Small building blocks shared by every screen. */

import type { ComponentChildren } from 'preact';
import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { api, ApiError } from './api';

// ---------------------------------------------------------------------------
// Data loading

export type Loaded<T> =
  | { state: 'loading' }
  | { state: 'error'; error: string }
  | { state: 'ready'; data: T };

/**
 * Run `load` on mount (and when `reload` is called); track loading and errors.
 * A reload keeps showing what's there until the fresh data arrives, so forms on the
 * page keep their messages and nothing jumps; new `deps` start from "loading".
 */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []): [Loaded<T>, () => void] {
  const [result, setResult] = useState<Loaded<T>>({ state: 'loading' });
  const [version, setVersion] = useState(0);
  const loadedFor = useRef<unknown[] | null>(null);
  useEffect(() => {
    let current = true;
    const previous = loadedFor.current;
    const sameDeps = previous !== null && previous.length === deps.length && deps.every((d, i) => Object.is(d, previous[i]));
    loadedFor.current = deps;
    if (!sameDeps) setResult({ state: 'loading' });
    load()
      .then((data) => current && setResult({ state: 'ready', data }))
      .catch((error: unknown) => current && setResult({ state: 'error', error: describe(error) }));
    return () => {
      current = false;
    };
  }, [...deps, version]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return [result, reload];
}

/** A list endpoint read a page at a time, with "Show more". `path` has no limit or cursor. */
export function usePaged<T>(path: string, pageSize = 50): {
  items: T[]; state: 'loading' | 'ready' | 'error'; error: string | null;
  hasMore: boolean; loadingMore: boolean; more: () => void; reload: () => void;
} {
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [version, setVersion] = useState(0);
  const url = (after: string | null) =>
    `${path}${path.includes('?') ? '&' : '?'}limit=${pageSize}${after ? `&cursor=${encodeURIComponent(after)}` : ''}`;

  useEffect(() => {
    let current = true;
    setState('loading');
    api<{ data: T[]; next_cursor: string | null }>(url(null))
      .then((page) => {
        if (!current) return;
        setItems(page.data);
        setCursor(page.next_cursor);
        setState('ready');
      })
      .catch((e: unknown) => {
        if (!current) return;
        setError(describe(e));
        setState('error');
      });
    return () => {
      current = false;
    };
  }, [path, version]);

  const more = () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    api<{ data: T[]; next_cursor: string | null }>(url(cursor))
      .then((page) => {
        setItems((prev) => [...prev, ...page.data]);
        setCursor(page.next_cursor);
      })
      .catch((e: unknown) => setError(describe(e)))
      .finally(() => setLoadingMore(false));
  };
  return { items, state, error, hasMore: Boolean(cursor), loadingMore, more, reload: () => setVersion((v) => v + 1) };
}

/** A problem found before asking the server, worded for the person. */
export class UserError extends Error {}

export function describe(error: unknown): string {
  if (error instanceof ApiError) return error.summary;
  if (error instanceof UserError) return error.message;
  if (error instanceof TypeError) return "Couldn't reach the server. Check your connection and try again.";
  return 'Something went wrong. Please try again.';
}

export function Loading({ result, children }: { result: Loaded<unknown>; children: () => ComponentChildren }) {
  if (result.state === 'loading') return <p class="muted">Loading…</p>;
  if (result.state === 'error') return <Notice tone="error">{result.error}</Notice>;
  return <>{children()}</>;
}

// ---------------------------------------------------------------------------
// Layout

export function PageHeader({ title, intro, actions }: { title: string; intro?: string; actions?: ComponentChildren }) {
  return (
    <header class="page-header">
      <div>
        <h1>{title}</h1>
        {intro && <p class="muted">{intro}</p>}
      </div>
      {actions && <div class="page-header__actions">{actions}</div>}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ComponentChildren }) {
  return (
    <div class="empty">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

export function Notice({ tone = 'info', children }: { tone?: 'info' | 'error' | 'success'; children: ComponentChildren }) {
  return (
    <div class={`notice notice--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Forms

interface FieldProps {
  label: string;
  value: string;
  onValue: (value: string) => void;
  hint?: string;
  id?: string;
  type?: 'text' | 'email' | 'password' | 'url' | 'number';
  required?: boolean;
  autoComplete?: string;
  placeholder?: string;
}

export function Field({ label, hint, value, onValue, id, type = 'text', ...input }: FieldProps) {
  const fieldId = id ?? `field-${label.toLowerCase().replace(/\W+/g, '-')}`;
  return (
    <label class="field" for={fieldId}>
      <span>{label}</span>
      {/* Preact's typings split <input> props by `type`, so one shared element needs the cast. */}
      <input
        id={fieldId}
        type={type as 'text'}
        value={value}
        onInput={(e) => onValue(e.currentTarget.value)}
        {...input}
      />
      {hint && <small class="muted">{hint}</small>}
    </label>
  );
}

interface ButtonProps {
  children: ComponentChildren;
  variant?: 'primary' | 'plain' | 'danger';
  busy?: boolean;
  type?: 'button' | 'submit';
  disabled?: boolean;
  onClick?: () => void;
}

export function Button({ variant = 'plain', busy, children, disabled, type = 'button', onClick }: ButtonProps) {
  return (
    <button type={type} class={`btn btn--${variant}`} disabled={disabled || busy} aria-busy={busy} onClick={onClick}>
      {busy ? 'Working…' : children}
    </button>
  );
}

export function TextArea({ label, hint, value, onValue, id, rows = 3, required, placeholder }: {
  label: string; value: string; onValue: (value: string) => void;
  hint?: string; id?: string; rows?: number; required?: boolean; placeholder?: string;
}) {
  const fieldId = id ?? `field-${label.toLowerCase().replace(/\W+/g, '-')}`;
  return (
    <label class="field" for={fieldId}>
      <span>{label}</span>
      <textarea id={fieldId} rows={rows} value={value} required={required} placeholder={placeholder}
                onInput={(e) => onValue(e.currentTarget.value)} />
      {hint && <small class="muted">{hint}</small>}
    </label>
  );
}

export function Select({ label, hint, value, onValue, id, options }: {
  label: string; value: string; onValue: (value: string) => void;
  options: Array<[value: string, label: string]>; hint?: string; id?: string;
}) {
  const fieldId = id ?? `field-${label.toLowerCase().replace(/\W+/g, '-')}`;
  return (
    <label class="field" for={fieldId}>
      <span>{label}</span>
      <select id={fieldId} value={value} onChange={(e) => onValue(e.currentTarget.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      {hint && <small class="muted">{hint}</small>}
    </label>
  );
}

export function Checkbox({ label, checked, onChecked, hint, disabled }: {
  label: ComponentChildren; checked: boolean; onChecked: (checked: boolean) => void; hint?: string; disabled?: boolean;
}) {
  return (
    <label class="check">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChecked(e.currentTarget.checked)} />
      <span>
        {label}
        {hint && <small class="muted">{hint}</small>}
      </span>
    </label>
  );
}

/** A value that only updates once `delay` ms pass without changes (for search boxes). */
export function useDebounced<T>(value: T, delay = 300): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** Form submission with a busy flag and an error message. */
export function useSubmit(action: () => Promise<void>): {
  busy: boolean;
  error: string | null;
  onSubmit: (event: Event) => void;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onSubmit = (event: Event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    action()
      .catch((e: unknown) => setError(describe(e)))
      .finally(() => setBusy(false));
  };
  return { busy, error, onSubmit };
}

// ---------------------------------------------------------------------------
// Formatting

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

/** Database timestamps ("2026-11-17 04:58:00") are UTC without saying so. */
export function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const utc = /^\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(iso) ? `${iso.replace(' ', 'T')}Z` : iso;
  const date = new Date(utc);
  return Number.isNaN(date.getTime()) ? iso : dateFormat.format(date);
}

export function fullName(person: { first_name: string; last_name: string }): string {
  return [person.first_name, person.last_name].filter(Boolean).join(' ');
}

export function count(n: number, singular: string, plural = `${singular}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : plural}`;
}

/** Text with a Copy button, for values people paste somewhere else. */
export function CopyText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span class="copy">
      <code>{text}</code>
      <button type="button" class="copy__button" onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
      }}>{copied ? 'Copied' : 'Copy'}</button>
    </span>
  );
}

/**
 * Remembered choices: how you left a screen is how you find it next time.
 *
 * Stored as cookies named "eph_pref_<key>" (one per choice, a year at a time, this site
 * only). Each value is checked when read, so a stale or edited value falls back to the
 * default instead of breaking a screen. If cookies are blocked, choices last for the visit.
 *
 * (The same helper as MailStudio's web app.)
 */

import { useEffect, useState } from 'preact/hooks';

const COOKIE_PREFIX = 'eph_pref_';
const MAX_AGE = 60 * 60 * 24 * 365;
const CHANGED = 'ephemeral:pref-changed';

/** Other tabs hear about changes here (cookies have no change event of their own). */
const channel: BroadcastChannel | null = typeof BroadcastChannel === 'function' ? new BroadcastChannel('ephemeral-prefs') : null;

/** Choices made while cookies are blocked, so they still last for the visit. */
const memory = new Map<string, string>();

function readCookie(key: string): string | null {
  const name = COOKIE_PREFIX + key + '=';
  for (const part of document.cookie.split(';')) {
    const item = part.trim();
    if (item.startsWith(name)) {
      try {
        return decodeURIComponent(item.slice(name.length));
      } catch {
        return null;
      }
    }
  }
  return null;
}

function writeCookie(key: string, raw: string): void {
  const secure = location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${COOKIE_PREFIX}${key}=${encodeURIComponent(raw)}; Path=/; Max-Age=${MAX_AGE}; SameSite=Lax${secure}`;
}

function readRaw(key: string): string | null {
  return readCookie(key) ?? memory.get(key) ?? null;
}

export function getPref<T>(key: string, fallback: T, valid: (value: unknown) => value is T): T {
  const raw = readRaw(key);
  if (raw === null) return fallback;
  try {
    const value: unknown = JSON.parse(raw);
    return valid(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function hasPref(key: string): boolean {
  return readRaw(key) !== null;
}

export function setPref(key: string, value: unknown): void {
  const raw = JSON.stringify(value);
  memory.set(key, raw);
  writeCookie(key, raw);
  window.dispatchEvent(new CustomEvent(CHANGED, { detail: key }));
  channel?.postMessage(key);
}

/**
 * Like useState, but remembered. Other screens using the same key, in this tab or
 * another one, update when it changes.
 */
export function usePref<T>(key: string, fallback: T, valid: (value: unknown) => value is T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => getPref(key, fallback, valid));
  useEffect(() => {
    const reload = () => setValue(getPref(key, fallback, valid));
    const sameTab = (e: Event) => (e as CustomEvent<string>).detail === key && reload();
    const otherTab = (e: MessageEvent) => e.data === key && reload();
    window.addEventListener(CHANGED, sameTab);
    channel?.addEventListener('message', otherTab);
    return () => {
      window.removeEventListener(CHANGED, sameTab);
      channel?.removeEventListener('message', otherTab);
    };
  }, [key]);
  return [value, (next: T) => {
    setValue(next);
    setPref(key, next);
  }];
}

/** Validators for the common shapes. */
export const oneOf = <T extends string>(...options: T[]) =>
  (value: unknown): value is T => typeof value === 'string' && (options as string[]).includes(value);
export const isBoolean = (value: unknown): value is boolean => typeof value === 'boolean';
export const isString = (value: unknown): value is string => typeof value === 'string';

/** The centred card that every signed-out page sits in. */

import type { ComponentChildren } from 'preact';
import { Brand } from '../nav';

export function AuthCard({ title, children, onSubmit }: {
  title: string; children: ComponentChildren; onSubmit?: (event: Event) => void;
}) {
  const body = (
    <>
      <Brand />
      <h1>{title}</h1>
      {children}
    </>
  );
  return (
    <main class="auth">
      {onSubmit
        ? <form class="card auth__card" onSubmit={onSubmit}>{body}</form>
        : <div class="card auth__card">{body}</div>}
    </main>
  );
}

/** A query string value from the address bar. */
export function query(name: string): string {
  return new URLSearchParams(location.search).get(name) ?? '';
}

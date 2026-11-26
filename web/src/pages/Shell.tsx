/** The signed-in layout: the app bar across the top, the current screen below. */

import type { JSX } from 'preact';
import { AppBar } from '../nav';
import { match, usePath } from '../router';
import { useSession } from '../session';
import { administers } from '../config';
import { Activity } from './Activity';
import { SiteDetail, Sites } from './Sites';
import { Account } from './Account';
import { ApiTester } from './ApiTester';
import { Email } from './Email';
import { EmailTemplateEditor, EmailTemplates } from './EmailTemplates';
import { FederationTest } from './FederationTest';
import { Services } from './Services';
import { Settings } from './Settings';
import { Dashboard } from './Dashboard';
import { KeyDetail } from './KeyDetail';
import { Keys } from './Keys';
import { RateLimits } from './RateLimits';
import { NotFound } from './NotFound';
import { Output } from './Output';

type Params = Record<string, string>;
type Route = [pattern: string, render: (p: Params) => JSX.Element, adminOnly?: boolean];

/** Exact patterns, most specific first. */
const ROUTES: Route[] = [
  ['/account', () => <Account />],
  ['/output', () => <Output />],
  ['/', () => <Dashboard />, true],
  ['/keys', () => <Keys />, true],
  ['/keys/:id', (p) => <KeyDetail key={p.id} id={Number(p.id)} />, true],
  ['/keys/:id/output', (p) => <Output key={p.id} keyId={Number(p.id)} />, true],
  ['/rate-limits', () => <RateLimits />, true],
  ['/services', () => <Services />, true],
  ['/services/test', () => <FederationTest />, true],
  ['/email', () => <Email />, true],
  ['/email/templates', () => <EmailTemplates />, true],
  ['/email/templates/:name', (p) => <EmailTemplateEditor key={p.name} name={p.name!} />, true],
  ['/settings', () => <Settings />, true],
  ['/sites', () => <Sites />, true],
  ['/sites/:id', (p) => <SiteDetail key={p.id} id={Number(p.id)} />, true],
  ['/activity', () => <Activity />, true],
  ['/api-tester', () => <ApiTester />],
];

export function Shell() {
  const path = usePath();
  const { session } = useSession();

  // A user's home is their account; an administrator's is the dashboard (at the admin
  // address, when there is one: elsewhere administrators see their own account).
  const admin = administers(session.user);
  let page: JSX.Element | null = path === '/' && !admin ? <Account /> : null;
  for (const [pattern, render, adminOnly] of page ? [] : ROUTES) {
    const params = match(pattern, path);
    if (params && (!adminOnly || admin)) {
      page = render(params);
      break;
    }
  }

  return (
    <div class="shell">
      <AppBar />
      <main class="content">{page ?? <NotFound />}</main>
    </div>
  );
}

/**
 * The app bar across the top of every signed-in screen: the logo, the main sections,
 * and your account. Everything is one click away from anywhere.
 */

import { administers, config } from './config';
import { Link, usePath } from './router';
import { useSession } from './session';
import { useTheme } from './theme';

const ADMIN_NAV = [
  { href: '/', label: 'Dashboard' },
  { href: '/keys', label: 'Keys' },
  { href: '/sites', label: 'Sites' },
  { href: '/rate-limits', label: 'Rate limits' },
  { href: '/services', label: 'Services' },
  { href: '/email', label: 'Email' },
  { href: '/settings', label: 'Settings' },
  { href: '/activity', label: 'Activity' },
  { href: '/api-tester', label: 'API tester' },
];

const USER_NAV = [
  { href: '/', label: 'My account' },
  { href: '/output', label: 'Output' },
  { href: '/api-tester', label: 'API tester' },
];

/** The logo: the install's name, with "REST" picked out in the default name. */
export function Brand({ link = false }: { link?: boolean }) {
  const name = config.site_name || 'ephemeralREST';
  const mark = name.endsWith('REST')
    ? <>◎ {name.slice(0, -4)}<span class="brand__accent">REST</span></>
    : <>◎ <span class="brand__accent">{name}</span></>;
  return link
    ? <Link href="/" class="brand brand--link">{mark}</Link>
    : <div class="brand">{mark}</div>;
}

export function AppBar() {
  const path = usePath();
  const { session, signOut } = useSession();
  const [theme, setTheme] = useTheme();
  const nav = administers(session.user) ? ADMIN_NAV : USER_NAV;
  const current = (href: string) => (href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`));

  return (
    <header class="app-bar">
      <Brand link />
      <nav aria-label="Main" class="app-bar__nav">
        {nav.map((item) => (
          <Link key={item.href} href={item.href} class={`app-bar__link${current(item.href) ? ' is-active' : ''}`}
                aria-current={current(item.href) ? 'page' : undefined}>
            {item.label}
          </Link>
        ))}
      </nav>
      <div class="app-bar__user">
        <Link href="/account" class={`app-bar__link${path === '/account' ? ' is-active' : ''}`}
              aria-current={path === '/account' ? 'page' : undefined}>
          {session.user.name || session.user.identifier}
        </Link>
        <button type="button" class="app-bar__link app-bar__button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
          {theme === 'dark' ? '☀' : '☾'}
        </button>
        <button type="button" class="app-bar__link app-bar__button" onClick={() => void signOut()}>
          Log out
        </button>
      </div>
    </header>
  );
}

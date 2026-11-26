/**
 * Talking to the ephemeralREST API from the browser.
 *
 * The session cookie travels automatically. Every change also carries the
 * session's CSRF token, which the server checks so other websites can't act
 * on the user's behalf. Errors arrive as {error, message?, details?} and are
 * thrown as ApiError.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | undefined;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, body: Record<string, unknown> | null) {
    super(String(body?.error ?? `Request failed (${status})`));
    this.status = status;
    this.detail = typeof body?.message === 'string' ? body.message : undefined;
    this.details = body?.details && typeof body.details === 'object' ? (body.details as Record<string, unknown>) : undefined;
  }

  /** One line suitable for showing to a person. */
  get summary(): string {
    if (this.status === 429) return 'Too many requests — please wait a moment and try again.';
    if (this.details) {
      const parts = Object.entries(this.details).map(([field, problem]) => {
        const text = Array.isArray(problem) ? problem.join(' ') : String(problem);
        return `${field.replace(/_/g, ' ')}: ${text}`;
      });
      if (parts.length) return parts.join(' ');
    }
    if (this.detail && this.detail !== this.message) return `${this.message}. ${this.detail}`;
    return this.message;
  }
}

let csrfToken: string | null = null;
let onSignedOut: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** Called when the server says the session has ended (expired, or logged out elsewhere). */
export function onSessionEnded(handler: () => void): void {
  onSignedOut = handler;
}

export type Method = 'GET' | 'POST' | 'PUT' | 'DELETE';

export async function api<T>(path: string, method: Method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;

  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;

  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const problem = (data && typeof data === 'object' ? data : null) as Record<string, unknown> | null;
    if (response.status === 401 && !path.startsWith('/auth/') && csrfToken) onSignedOut?.();
    throw new ApiError(response.status, problem);
  }
  return data as T;
}

/**
 * A raw call for the API tester: any method and path, with an optional API key
 * instead of the session. Never throws for HTTP errors.
 */
export async function rawRequest(
  method: string, path: string, body: string, apiKey: string,
): Promise<{ status: number; ms: number; text: string; headers: Array<[string, string]> }> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body.trim()) headers['Content-Type'] = 'application/json';
  if (apiKey) headers['X-API-Key'] = apiKey;
  else if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const started = performance.now();
  const response = await fetch(`/api/v1${path.startsWith('/') ? path : `/${path}`}`, {
    method,
    headers,
    credentials: apiKey ? 'omit' : 'same-origin',
    body: method === 'GET' || method === 'HEAD' || !body.trim() ? undefined : body,
  });
  const text = await response.text();
  return { status: response.status, ms: Math.round(performance.now() - started), text, headers: [...response.headers.entries()] };
}

// ---------------------------------------------------------------------------
// Shapes returned by the API (the parts the app uses)

export interface RateLimits {
  per_minute: number | null;
  per_hour: number | null;
  per_day: number | null;
}

export interface User {
  id: number;
  name: string;
  identifier: string;
  admin: boolean;
  active: boolean;
  rate_limits: RateLimits;
}

export interface Session {
  user: User;
  csrf_token: string;
}

export interface PublicConfig {
  site_name: string;
  version: string;
  trusted_device_days: number;
  allow_admin_promotion: boolean;
  allow_registration: boolean;
  public_url: string;
  /** With an admin address, administration answers only there. */
  admin_url?: string;
  /** Whether administration answers at this address. */
  admin_here?: boolean;
}

/** An app that uses ephemeralREST through a domain key, such as a hosted AstrologyStudio site. */
export interface Site {
  id: number;
  name: string;
  domain: string;
  key_prefix: string;
  active: boolean;
  created_at: string | null;
  /** Addresses its sign-up and password emails may link to. */
  origins: string[];
  email_delivery: { allowed: boolean; mode: 'self' | 'ephemeral' };
  /** Accounts made through this site. */
  accounts: number;
  rate_per_minute: number | null;
  rate_per_hour: number | null;
  rate_per_day: number | null;
}

export interface SiteAccount {
  id: number;
  name: string;
  identifier: string;
  active: number | boolean;
  created_at: string | null;
  has_password: boolean;
}

export interface AdminAction {
  id: number;
  at: string;
  admin_id: number | null;
  admin_email: string;
  ip: string;
  action: string;
  target_type: string;
  target_id: number | null;
  detail: string | null;
}

export interface SiteDetail extends Site {
  account_list: SiteAccount[];
  activity: AdminAction[];
}

export interface SiteDefaults {
  email_delivery_allowed: boolean;
  rate_per_minute: number | null;
  rate_per_hour: number | null;
  rate_per_day: number | null;
}

export interface AuthStatus {
  setup_required: boolean;
  session: Session | null;
  config: PublicConfig;
}

/** What /auth/login answers: signed in, or the next step. */
export type LoginResult =
  | { session: Session }
  | { '2fa_required': true; email: string }
  | { must_change_password: true; email: string };

export interface KeyRecord {
  id: number;
  key_type: string;
  name: string;
  identifier: string;
  key_prefix: string;
  admin: boolean | number;
  active: boolean | number;
  rate_per_minute: number | null;
  rate_per_hour: number | null;
  rate_per_day: number | null;
  output_config: Record<string, unknown> | null;
  must_change_password?: boolean | number;
  created_at: string;
  updated_at: string;
}

export interface FederatedService {
  id: number;
  slug: string;
  display_name: string;
  description: string | null;
  base_url: string | null;
  active: boolean | number;
  created_at?: string;
  updated_at?: string;
}

export interface OutputSettings {
  key_id: number;
  identifier: string;
  stored: Record<string, unknown>;
  effective: Record<string, unknown>;
  defaults: Record<string, unknown>;
}

export interface ClassLimits {
  rate_per_minute: number;
  rate_per_hour: number;
  rate_per_day: number;
}

export interface SmtpConfig {
  host?: string;
  port?: string;
  user?: string;
  from_addr?: string;
  use_tls?: string;
  use_ssl?: string;
  admin_email?: string;
  base_url?: string;
  portal_url?: string;
  password_set: boolean;
}

export interface EmailTemplate {
  name: string;
  subject: string;
  header_text: string;
  body_text: string;
  footer_text: string;
  bg_color: string;
  panel_color: string;
  text_color: string;
  content_width: number;
  header_align: string;
  customised?: boolean;
}

export interface Health {
  status: string;
  version: string;
  timestamp: string;
  api_usage?: Record<string, unknown>;
  celestial_bodies?: string[];
  supported_house_systems?: string[];
}

export interface CacheStats {
  [key: string]: number | string | null;
}

export interface ServerSettings {
  public_url: string;
  site_name: string;
  version: string;
  database: string;
  geocoding: 'google' | 'offline';
  ephemeris_files: string[];
  cities_imported: number | null;
  session_timeout: number;
  session_max_hours: number;
  trusted_device_days: number;
  allow_admin_promotion: boolean;
  allow_registration: boolean;
  rate_limiting: boolean;
  cors_origins: string[];
  store_enabled: boolean;
}

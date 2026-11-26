/** The install's public settings (site name, version, …), loaded once at start-up. */

import type { PublicConfig } from './api';

export let config: PublicConfig = {
  site_name: 'ephemeralREST',
  version: '',
  trusted_device_days: 28,
  allow_admin_promotion: true,
  allow_registration: true,
  public_url: '',
  admin_url: '',
  admin_here: true,
};

export function setConfig(next: PublicConfig): void {
  config = next;
  document.title = next.site_name || 'ephemeralREST';
}

/** Whether this person administers here: an administrator, at an address where administration answers. */
export function administers(user: { admin: boolean }): boolean {
  return user.admin && config.admin_here !== false;
}

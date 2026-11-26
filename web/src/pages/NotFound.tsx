import { Link } from '../router';
import { EmptyState } from '../ui';

export function NotFound() {
  return (
    <EmptyState title="Page not found">
      <p>That page doesn't exist. <Link href="/">Go to the start</Link></p>
    </EmptyState>
  );
}

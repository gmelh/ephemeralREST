################################################################################
#                                                                              #
#  Ephemeral.REST — Swiss Ephemeris REST API                                   #
#  Copyright (C) 2026  Ephemeral.REST contributors                             #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify       #
#  it under the terms of the GNU Affero General Public License as published   #
#  by the Free Software Foundation, either version 3 of the License, or       #
#  (at your option) any later version.                                         #
#                                                                              #
#  This program is distributed in the hope that it will be useful,            #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the              #
#  GNU Affero General Public License for more details.                         #
#                                                                              #
#  You should have received a copy of the GNU Affero General Public License   #
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.    #
#                                                                              #
#  ADDITIONAL NOTICE — Swiss Ephemeris dependency:                             #
#  This software uses the Swiss Ephemeris library developed by                #
#  Astrodienst AG, Zurich, Switzerland. The Swiss Ephemeris is licensed       #
#  under the GNU Affero General Public License (AGPL) v3. Use of this        #
#  software therefore requires compliance with the AGPL v3, which includes    #
#  the obligation to make source code available to users who interact with    #
#  this software over a network.                                              #
#  See https://www.astro.com/swisseph/ for full details.                      #
#                                                                              #
################################################################################
################################################################################
# account_store.py                                                             #
################################################################################
"""
Account Store — a general-purpose, per-account JSON document store.

Any authenticated API key can keep its own private JSON documents here,
organised as   namespace / key  →  document.  The server never interprets
a document's content: it handles ownership, revisions, a change feed for
syncing between devices, deletion markers and size limits, and nothing
else. What the documents mean is entirely up to the client application.

    namespace   chosen by the application, reverse-DNS style recommended
                so unrelated apps can't collide: "mindforce.market-charts",
                "example.notes". Lowercase a-z 0-9 . _ - , 1–64 chars,
                starting with a letter or digit.
    key         chosen by the application within its namespace, e.g.
                "ASX/BHP". 1–255 chars of  A-Z a-z 0-9 . _ ~ : @ ! $ & ' ( )
                * + , ; = ^ -  and "/" as a separator (no empty, "." or
                ".." segments, no leading/trailing "/").
    content     any JSON value. Stored compactly; never parsed beyond
                checking that it is valid JSON within STORE_MAX_DOC_BYTES.
    meta        optional small JSON object (≤ STORE_MAX_META_BYTES)
                returned in listings, so a client can show a list (names,
                tags…) without downloading every document.

Revisions and safe updates
    Every write increments the document's `revision`. A client can make a
    write conditional on the revision it last saw:
        If-Match: <revision>   — write only if unchanged since then,
                                 otherwise 412 + the current document
        If-None-Match: *       — create only; 412 if it already exists
    The same preconditions can be given as ?if_match= / ?if_none_match=*
    query parameters, for clients behind CORS configurations that don't
    allow those headers. With no precondition a write is unconditional.

Change feed
    Each account has a single counter, `seq`, that increases by one on
    every write or delete in any namespace. Each document records the seq
    of its last change. A client stores the highest seq it has seen and
    later asks for everything after it:
        GET /account/store/<ns>?since=<seq>
    A counter is used rather than timestamps because device clocks drift
    and two writes can land in the same second.

Deletion
    DELETE leaves a small deletion marker (content removed, `deleted: true`)
    so other devices learn about the delete through the change feed. Markers
    older than STORE_TOMBSTONE_DAYS are purged by `cleanup.py --store`.
    Writing to a deleted key recreates it, continuing its revision number.

Endpoints (all require X-API-Key)
    GET    /account/store                    namespaces, counts, usage, limits
    GET    /account/store/<ns>               change feed / listing (no content
                                             unless ?include=content)
    GET    /account/store/<ns>/<key>         one document (ETag = revision)
    PUT    /account/store/<ns>/<key>         create or replace
                                             body: {"content": <json>,
                                                    "meta": {...}}  (meta optional:
                                             omitted = keep existing,
                                             null = clear)
    DELETE /account/store/<ns>/<key>         delete (leaves a marker)

Configuration (.env) — see config.py
    STORE_ENABLED, STORE_REQUIRE_GRANT, STORE_MAX_DOC_BYTES,
    STORE_MAX_META_BYTES, STORE_MAX_ACCOUNT_BYTES, STORE_MAX_DOCS,
    STORE_TOMBSTONE_DAYS, STORE_RATE_LIMIT_EXEMPT

Wired up from app.py via init_account_store(). The two tables
(account_documents, account_store_seq) are created with the rest of the
schema in database.py, so tools that open the database without the app
(key_manager.py, cleanup.py) always find them.
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, g, jsonify, request

logger = logging.getLogger(__name__)

store_api = Blueprint('store', __name__)

# Grant name checked in api_key_services when STORE_REQUIRE_GRANT=true.
STORE_SERVICE = 'store'

_NAMESPACE_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._~:@!$&'()*+,;=^-]+$")

# Injected by init_account_store()
_store = None    # type: Optional[AccountStore]
_config = None


# ==============================================================================
# Validation helpers
# ==============================================================================

def valid_namespace(ns: str) -> bool:
    return bool(ns) and bool(_NAMESPACE_RE.match(ns))


def valid_key(key: str) -> bool:
    if not key or len(key) > 255:
        return False
    for seg in key.split('/'):
        if seg in ('', '.', '..') or not _KEY_SEGMENT_RE.match(seg):
            return False
    return True


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def _utcnow() -> datetime:
    """Naive UTC — stored as-is in both backends (DATETIME(6) in MySQL,
    ISO text in SQLite), so no session time-zone conversion applies."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value) -> Optional[str]:
    """Normalise a stored timestamp (SQLite ISO text or MySQL datetime
    string) to ISO 8601 UTC with a trailing Z."""
    if value is None:
        return None
    s = str(value).replace(' ', 'T')
    return s if s.endswith('Z') else s + 'Z'


def _parse_revision(value: Optional[str]) -> Optional[int]:
    """Accepts 5, "5", W/"5" — returns None if not a revision number."""
    if value is None:
        return None
    v = str(value).strip()
    if v.startswith('W/'):
        v = v[2:]
    v = v.strip('"').strip()
    return int(v) if v.isdigit() else None


# ==============================================================================
# Database layer
# ==============================================================================

class PreconditionFailed(Exception):
    def __init__(self, current: Optional[Dict[str, Any]]):
        super().__init__('precondition failed')
        self.current = current


class QuotaExceeded(Exception):
    pass


class AccountStore:
    """All SQL for the store. Uses DatabaseManager's connection handling,
    so the same SQLite-flavoured statements run on MySQL via its
    translation layer (? placeholders, INSERT OR IGNORE)."""

    def __init__(self, db_manager, config):
        self.db = db_manager
        self.cfg = config

    # ── Internals ─────────────────────────────────────────────────────────

    def _ensure_seq_row(self, key_id: int):
        """Create the account's counter row in its own short transaction.
        Kept out of the write transaction because concurrent INSERTs of the
        same new key take gap/insert-intention locks in InnoDB and can
        deadlock; once the row exists, the write only needs a plain row
        lock on it (see _begin_write)."""
        def _insert():
            with self.db.get_connection() as conn:
                conn.execute('INSERT OR IGNORE INTO account_store_seq (key_id, seq) VALUES (?, 0)',
                             (key_id,))
        self._with_retry(_insert)

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Lock conflicts worth retrying: InnoDB deadlock (1213) or lock
        wait timeout (1205), SQLite 'database is locked'."""
        errno = getattr(exc, 'errno', None)
        msg = str(exc).lower()
        return errno in (1205, 1213) or 'deadlock' in msg or 'lock wait timeout' in msg \
            or 'database is locked' in msg

    def _with_retry(self, fn):
        for attempt in range(4):
            try:
                return fn()
            except (PreconditionFailed, QuotaExceeded):
                raise
            except Exception as e:
                if attempt < 3 and self._is_transient(e):
                    logger.warning(f'Account store: retrying after lock conflict ({e})')
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise

    def _begin_write(self, conn, key_id: int) -> int:
        """Start a write: take the account's counter and return the next
        seq. Doing this FIRST serialises all writes for an account — the
        counter row stays locked (InnoDB) / the database stays write-locked
        (SQLite BEGIN IMMEDIATE) until commit — so the revision check that
        follows can't race another writer. The row itself is created
        beforehand by _ensure_seq_row()."""
        if self.db.db_type == 'sqlite':
            conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE account_store_seq SET seq = seq + 1 WHERE key_id = ?', (key_id,))
        row = conn.execute('SELECT seq FROM account_store_seq WHERE key_id = ?',
                           (key_id,)).fetchone()
        return int(row['seq'])

    @staticmethod
    def _row_to_doc(row, include_content: bool = True) -> Dict[str, Any]:
        doc = {
            'namespace':  row['namespace'],
            'key':        row['doc_key'],
            'revision':   int(row['revision']),
            'seq':        int(row['seq']),
            'size':       int(row['size']),
            'deleted':    row['deleted_at'] is not None,
            'meta':       json.loads(row['meta']) if row['meta'] else None,
            'created_at': _iso(row['created_at']),
            'updated_at': _iso(row['updated_at']),
        }
        if doc['deleted']:
            doc['deleted_at'] = _iso(row['deleted_at'])
        if include_content:
            doc['content'] = (json.loads(row['content'])
                              if row['content'] is not None else None)
        return doc

    def _fetch(self, conn, key_id: int, namespace: str, key: str):
        return conn.execute(
            'SELECT * FROM account_documents '
            'WHERE key_id = ? AND namespace = ? AND doc_key = ?',
            (key_id, namespace, key)).fetchone()

    def _check_precondition(self, row, if_match: Optional[int], if_none_match: bool):
        live = row is not None and row['deleted_at'] is None
        if if_none_match and live:
            raise PreconditionFailed(self._row_to_doc(row))
        if if_match is not None:
            if not live or int(row['revision']) != if_match:
                raise PreconditionFailed(self._row_to_doc(row) if row is not None else None)

    # ── Reads ─────────────────────────────────────────────────────────────

    def get(self, key_id: int, namespace: str, key: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = self._fetch(conn, key_id, namespace, key)
            return self._row_to_doc(row) if row is not None else None

    def changes(self, key_id: int, namespace: str, since: int, limit: int,
                include_content: bool) -> Dict[str, Any]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM account_documents '
                'WHERE key_id = ? AND namespace = ? AND seq > ? '
                'ORDER BY seq ASC LIMIT ?',
                (key_id, namespace, since, limit + 1)).fetchall()
            seq_row = conn.execute('SELECT seq FROM account_store_seq WHERE key_id = ?',
                                   (key_id,)).fetchone()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [self._row_to_doc(r, include_content) for r in rows]
        return {
            'namespace':  namespace,
            'items':      items,
            'has_more':   has_more,
            # Resume point for the next call: the last seq in this page, or
            # the caller's own `since` if nothing changed.
            'next_since': items[-1]['seq'] if items else since,
            'account_seq': int(seq_row['seq']) if seq_row else 0,
        }

    def summary(self, key_id: int) -> Dict[str, Any]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                'SELECT namespace, '
                '       SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) AS documents, '
                '       SUM(CASE WHEN deleted_at IS NULL THEN size ELSE 0 END) AS bytes, '
                '       MAX(seq) AS last_seq '
                'FROM account_documents WHERE key_id = ? '
                'GROUP BY namespace ORDER BY namespace',
                (key_id,)).fetchall()
            seq_row = conn.execute('SELECT seq FROM account_store_seq WHERE key_id = ?',
                                   (key_id,)).fetchone()
        namespaces = [{
            'namespace': r['namespace'],
            'documents': int(r['documents'] or 0),
            'bytes':     int(r['bytes'] or 0),
            'last_seq':  int(r['last_seq'] or 0),
        } for r in rows]
        return {
            'namespaces':  namespaces,
            'account_seq': int(seq_row['seq']) if seq_row else 0,
            'usage': {
                'documents': sum(n['documents'] for n in namespaces),
                'bytes':     sum(n['bytes'] for n in namespaces),
            },
            'limits': {
                'max_doc_bytes':     self.cfg.STORE_MAX_DOC_BYTES,
                'max_meta_bytes':    self.cfg.STORE_MAX_META_BYTES,
                'max_account_bytes': self.cfg.STORE_MAX_ACCOUNT_BYTES,
                'max_documents':     self.cfg.STORE_MAX_DOCS,
            },
        }

    # ── Writes ────────────────────────────────────────────────────────────

    def put(self, key_id: int, namespace: str, key: str, content_json: str,
            meta_json: Optional[str], meta_given: bool,
            if_match: Optional[int], if_none_match: bool) -> Tuple[Dict[str, Any], bool]:
        """Create or replace. Returns (document without content, created)."""
        self._ensure_seq_row(key_id)
        return self._with_retry(lambda: self._put_once(
            key_id, namespace, key, content_json, meta_json, meta_given, if_match, if_none_match))

    def _put_once(self, key_id, namespace, key, content_json, meta_json, meta_given,
                  if_match, if_none_match):
        size = len(content_json.encode('utf-8'))
        refused = None
        with self.db.get_connection() as conn:
            seq = self._begin_write(conn, key_id)
            row = self._fetch(conn, key_id, namespace, key)
            try:
                self._check_precondition(row, if_match, if_none_match)
            except PreconditionFailed as e:
                refused = e
            if refused is None:
                result, refused = self._put_locked(conn, key_id, namespace, key, row, seq,
                                                   size, content_json, meta_json, meta_given)
            if refused is not None:
                # Expected outcomes, not database errors — roll back here
                # (returning the seq) and raise after the context closes,
                # so get_connection() doesn't log them as failures.
                conn.rollback()
        if refused is not None:
            raise refused
        return result

    def _put_locked(self, conn, key_id, namespace, key, row, seq, size,
                    content_json, meta_json, meta_given):
        """Body of put() once the account is locked and preconditions pass.
        Returns (result, None) or (None, QuotaExceeded)."""
        live = row is not None and row['deleted_at'] is None
        usage = conn.execute(
            'SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes '
            'FROM account_documents WHERE key_id = ? AND deleted_at IS NULL',
            (key_id,)).fetchone()
        used_bytes = int(usage['bytes']) - (int(row['size']) if live else 0)
        used_docs = int(usage['n']) - (1 if live else 0)
        if used_bytes + size > self.cfg.STORE_MAX_ACCOUNT_BYTES:
            return None, QuotaExceeded(
                f'Account storage limit reached ({self.cfg.STORE_MAX_ACCOUNT_BYTES} bytes)')
        if used_docs + 1 > self.cfg.STORE_MAX_DOCS:
            return None, QuotaExceeded(
                f'Account document limit reached ({self.cfg.STORE_MAX_DOCS} documents)')

        now = self.db._fmt_dt(_utcnow())
        if row is None:
            conn.execute(
                'INSERT INTO account_documents '
                '(key_id, namespace, doc_key, content, meta, revision, seq, size, '
                ' created_at, updated_at, deleted_at) '
                'VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)',
                (key_id, namespace, key, content_json, meta_json, seq, size, now, now))
        else:
            # Recreating a deleted key starts a fresh document (new
            # created_at, meta not inherited) but keeps counting
            # revisions, so a stale If-Match from before the delete
            # can never match the new document.
            new_meta = meta_json if (meta_given or not live) else row['meta']
            created = row['created_at'] if live else now
            conn.execute(
                'UPDATE account_documents SET content = ?, meta = ?, '
                '  revision = revision + 1, seq = ?, size = ?, '
                '  created_at = ?, updated_at = ?, deleted_at = NULL '
                'WHERE key_id = ? AND namespace = ? AND doc_key = ?',
                (content_json, new_meta, seq, size, created, now,
                 key_id, namespace, key))
        new_row = self._fetch(conn, key_id, namespace, key)
        return (self._row_to_doc(new_row, include_content=False), not live), None

    def delete(self, key_id: int, namespace: str, key: str,
               if_match: Optional[int]) -> Optional[Dict[str, Any]]:
        """Replace a live document with a deletion marker. Returns the
        marker, or None if there was no live document."""
        self._ensure_seq_row(key_id)
        return self._with_retry(lambda: self._delete_once(key_id, namespace, key, if_match))

    def _delete_once(self, key_id, namespace, key, if_match):
        with self.db.get_connection() as conn:
            seq = self._begin_write(conn, key_id)
            row = self._fetch(conn, key_id, namespace, key)
            if row is None or row['deleted_at'] is not None:
                conn.rollback()          # nothing to delete — give the seq back
                return None
            refused = None
            try:
                self._check_precondition(row, if_match, False)
            except PreconditionFailed as e:
                refused = e
                conn.rollback()
            if refused is None:
                marker = self._delete_locked(conn, key_id, namespace, key, seq)
        if refused is not None:
            raise refused
        return marker

    def _delete_locked(self, conn, key_id, namespace, key, seq):
        now = self.db._fmt_dt(_utcnow())
        conn.execute(
            'UPDATE account_documents SET content = NULL, meta = NULL, size = 0, '
            '  revision = revision + 1, seq = ?, updated_at = ?, deleted_at = ? '
            'WHERE key_id = ? AND namespace = ? AND doc_key = ?',
            (seq, now, now, key_id, namespace, key))
        return self._row_to_doc(self._fetch(conn, key_id, namespace, key),
                                include_content=False)

    # ── Maintenance ───────────────────────────────────────────────────────

    def count_expired_tombstones(self, days: int) -> int:
        cutoff = self.db._fmt_dt(_utcnow() - timedelta(days=days))
        with self.db.get_connection() as conn:
            row = conn.execute(
                'SELECT COUNT(*) AS n FROM account_documents '
                'WHERE deleted_at IS NOT NULL AND deleted_at < ?', (cutoff,)).fetchone()
            return int(row['n'])

    def purge_tombstones(self, days: int) -> int:
        """Remove deletion markers older than `days`. A device that hasn't
        synced for longer than this won't learn about those deletes, so
        keep it comfortably longer than any expected offline period."""
        cutoff = self.db._fmt_dt(_utcnow() - timedelta(days=days))
        with self.db.get_connection() as conn:
            cur = conn.execute(
                'DELETE FROM account_documents '
                'WHERE deleted_at IS NOT NULL AND deleted_at < ?', (cutoff,))
            return cur.rowcount or 0

    def delete_account(self, conn, key_id: int):
        """Remove everything an account stored. Called from
        DatabaseManager.delete_api_key inside its own transaction."""
        conn.execute('DELETE FROM account_documents WHERE key_id = ?', (key_id,))
        conn.execute('DELETE FROM account_store_seq WHERE key_id = ?', (key_id,))


# ==============================================================================
# HTTP layer
# ==============================================================================

def _error(message: str, status: int, **extra):
    body = {'error': message, 'status': status}
    body.update(extra)
    return jsonify(body), status


def _caller() -> Tuple[Optional[int], Optional[tuple]]:
    """(key_id, None) for a real key allowed to use the store, else
    (None, error_response)."""
    try:
        key_id = int(g.user['id'])
    except (TypeError, ValueError, KeyError, AttributeError):
        return None, _error('The account store is not available for guest/debug sessions', 403)
    if _config.STORE_REQUIRE_GRANT and not g.user.get('admin'):
        if not _store.db.key_has_service(key_id, STORE_SERVICE):
            return None, _error(
                f"This key has not been granted the '{STORE_SERVICE}' service", 403)
    return key_id, None


def _preconditions() -> Tuple[Optional[int], bool, Optional[tuple]]:
    """Read If-Match / If-None-Match from headers or query string.
    Returns (if_match_revision, if_none_match_star, error)."""
    raw_match = request.headers.get('If-Match') or request.args.get('if_match')
    raw_none = request.headers.get('If-None-Match') or request.args.get('if_none_match')
    if_match = None
    if raw_match is not None:
        if_match = _parse_revision(raw_match)
        if if_match is None:
            return None, False, _error('If-Match must be a document revision number', 400)
    if_none_match = False
    if raw_none is not None:
        if raw_none.strip() != '*':
            return None, False, _error('If-None-Match only supports "*" (create only)', 400)
        if_none_match = True
    if if_match is not None and if_none_match:
        return None, False, _error('Use either If-Match or If-None-Match, not both', 400)
    return if_match, if_none_match, None


def _with_etag(response_tuple, revision: int):
    resp, status = response_tuple
    resp.headers['ETag'] = f'"{revision}"'
    return resp, status


def _precondition_failed(exc: PreconditionFailed):
    return _error('Document has changed (precondition failed)', 412, current=exc.current)


def _check_ns(namespace: str):
    if not valid_namespace(namespace):
        return _error('Invalid namespace — use 1-64 chars of a-z 0-9 . _ - '
                      'starting with a letter or digit', 400)
    return None


def _check_key(key: str):
    if not valid_key(key):
        return _error('Invalid key — 1-255 chars, "/"-separated segments of '
                      "A-Z a-z 0-9 . _ ~ : @ ! $ & ' ( ) * + , ; = ^ -", 400)
    return None


@store_api.route('/account/store', methods=['GET'])
def store_summary():
    """Namespaces this account has used, with counts, usage and limits."""
    key_id, err = _caller()
    if err:
        return err
    return jsonify(_store.summary(key_id))


@store_api.route('/account/store/<namespace>', methods=['GET'])
def store_list(namespace):
    """Change feed for one namespace, oldest change first.
    ?since=<seq>  only changes after this account seq (default 0 = all)
    ?limit=<n>    page size, 1-500 (default 100)
    ?include=content   include each document's content (full export / pull)
    Deleted documents appear as markers ("deleted": true)."""
    key_id, err = _caller()
    if err:
        return err
    bad = _check_ns(namespace)
    if bad:
        return bad
    try:
        since = int(request.args.get('since', 0))
        limit = int(request.args.get('limit', 100))
    except ValueError:
        return _error('since and limit must be integers', 400)
    if since < 0:
        return _error('since must be >= 0', 400)
    limit = max(1, min(limit, 500))
    include_content = request.args.get('include', '') == 'content'
    return jsonify(_store.changes(key_id, namespace, since, limit, include_content))


@store_api.route('/account/store/<namespace>/<path:key>', methods=['GET'])
def store_get(namespace, key):
    key_id, err = _caller()
    if err:
        return err
    bad = _check_ns(namespace) or _check_key(key)
    if bad:
        return bad
    doc = _store.get(key_id, namespace, key)
    if doc is None or doc['deleted']:
        return _error('Document not found', 404)
    return _with_etag((jsonify(doc), 200), doc['revision'])


@store_api.route('/account/store/<namespace>/<path:key>', methods=['PUT'])
def store_put(namespace, key):
    """Body: {"content": <any JSON>, "meta": {...} | null}  (meta optional)."""
    key_id, err = _caller()
    if err:
        return err
    bad = _check_ns(namespace) or _check_key(key)
    if bad:
        return bad
    if_match, if_none_match, err = _preconditions()
    if err:
        return err

    max_body = _config.STORE_MAX_DOC_BYTES + _config.STORE_MAX_META_BYTES + 4096
    if request.content_length is not None and request.content_length > max_body:
        return _error(f'Request too large (document limit {_config.STORE_MAX_DOC_BYTES} bytes)', 413)

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or 'content' not in body:
        return _error('Body must be a JSON object with a "content" field', 400)

    content_json = _compact_json(body['content'])
    if len(content_json.encode('utf-8')) > _config.STORE_MAX_DOC_BYTES:
        return _error(f'Document too large (limit {_config.STORE_MAX_DOC_BYTES} bytes)', 413)

    meta_given = 'meta' in body
    meta_json = None
    if meta_given and body['meta'] is not None:
        if not isinstance(body['meta'], dict):
            return _error('"meta" must be a JSON object or null', 400)
        meta_json = _compact_json(body['meta'])
        if len(meta_json.encode('utf-8')) > _config.STORE_MAX_META_BYTES:
            return _error(f'"meta" too large (limit {_config.STORE_MAX_META_BYTES} bytes)', 413)

    try:
        doc, created = _store.put(key_id, namespace, key, content_json,
                                  meta_json, meta_given, if_match, if_none_match)
    except PreconditionFailed as e:
        return _precondition_failed(e)
    except QuotaExceeded as e:
        return _error(str(e), 507)
    return _with_etag((jsonify(doc), 201 if created else 200), doc['revision'])


@store_api.route('/account/store/<namespace>/<path:key>', methods=['DELETE'])
def store_delete(namespace, key):
    key_id, err = _caller()
    if err:
        return err
    bad = _check_ns(namespace) or _check_key(key)
    if bad:
        return bad
    if_match, if_none_match, err = _preconditions()
    if err:
        return err
    if if_none_match:
        return _error('If-None-Match is not meaningful on DELETE', 400)
    try:
        marker = _store.delete(key_id, namespace, key, if_match)
    except PreconditionFailed as e:
        return _precondition_failed(e)
    if marker is None:
        return _error('Document not found', 404)
    return jsonify(marker)


STORE_ENDPOINTS = ['store.store_summary', 'store.store_list',
                   'store.store_get', 'store.store_put', 'store.store_delete']


def init_account_store(app, db_manager, auth_manager, config) -> Optional[AccountStore]:
    """Create the schema, register the routes and wrap them with API-key
    authentication. Returns the AccountStore (or None when disabled)."""
    global _store, _config
    if not config.STORE_ENABLED:
        logger.info('Account store disabled (STORE_ENABLED=false)')
        return None
    _config = config
    _store = AccountStore(db_manager, config)   # schema: database.py
    app.register_blueprint(store_api)
    for endpoint in STORE_ENDPOINTS:
        app.view_functions[endpoint] = auth_manager.require_api_key(app.view_functions[endpoint])
    logger.info('Account store enabled at /account/store')
    return _store
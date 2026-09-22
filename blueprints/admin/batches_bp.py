# ============================================================
# blueprints/admin/batches_bp.py
# Super-admin only. Content batches: group questions/PDFs for
# later bulk editing. Deleting a batch or removing items never
# deletes the underlying content.
#
# Bulk edits come in two flavors:
#   • /bulk-edit         — one change at a time (kept for compat)
#   • /bulk-edit-batch   — many staged changes, committed together
# ============================================================

from flask import (
    Blueprint, render_template, request, session, jsonify,
    redirect, url_for, flash, abort,
)
import json
import logging
from datetime import datetime, timezone

from db import (
    create_batch, get_batch, list_batches, recent_batches,
    update_batch, delete_batch,
    add_batch_items, remove_batch_items, get_batch_item_ids,
    get_batches_for_item, batch_items_count,
    record_batch_edit, get_batch_edit, mark_batch_edit_undone,
    clean_expired_batch_edits,
    get_question_by_id, update_question,
    get_pdf_by_id, get_all_pdfs, get_questions_paginated,
    execute_with_retry,
)
from subjects_config import get_all_subjects, get_subject
from question_utils import VALID_GRADES, grade_label
from utils import validate_csrf
from services.admin.guards import super_admin_required
from services.admin.audit import write_audit

logger = logging.getLogger(__name__)

admin_batches_bp = Blueprint(
    'admin_batches', __name__, url_prefix='/admin/batches'
)

UNDO_SECONDS = 60


# ─── Action → column maps (used by the batch editor) ─────
_QUESTION_ACTION_COLUMN = {
    'set_grade':      'grade',
    'set_subject':    'subject_code',
    'set_chapter':    'chapter',
    'set_difficulty': 'difficulty',
    'set_status':     'status',
    'set_pdf_code':   'pdf_code',
    'set_pdf_page':   'pdf_page',
}

_PDF_ACTION_COLUMN = {
    'set_subject':    'subject',
    'set_curriculum': 'curriculum',
    'set_class':      'class',
    'set_chapter':    'chapter',
    'toggle_premium': 'is_premium',
}

_TAG_ACTIONS = ('add_tag', 'remove_tag', 'clear_tags')


# ─── Helpers ──────────────────────────────────────────────

def _csrf_ok():
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(token) and token == session.get('csrf_token')


def _int_list(raw):
    out = []
    for x in (raw or []):
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _build_question_filter(batch_ids, search='', grade='', subject='',
                           status='', chapter=''):
    if not batch_ids:
        return "1=0", []
    placeholders = ','.join('?' for _ in batch_ids)
    where = [f"q.id IN ({placeholders})"]
    params = list(batch_ids)
    if search:
        where.append("(q.question_text LIKE ? OR q.chapter LIKE ? OR q.tags LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if grade:
        where.append("q.grade = ?")
        params.append(grade)
    if subject:
        where.append("q.subject_code = ?")
        params.append(subject)
    if status:
        where.append("q.status = ?")
        params.append(status)
    if chapter:
        where.append("q.chapter LIKE ?")
        params.append(f"%{chapter}%")
    return " AND ".join(where), params


def _build_pdf_filter(batch_ids, search='', subject='', curriculum='',
                      class_filter=''):
    if not batch_ids:
        return "1=0", []
    placeholders = ','.join('?' for _ in batch_ids)
    where = [f"id IN ({placeholders})"]
    params = list(batch_ids)
    if search:
        where.append("(title LIKE ? OR code LIKE ? OR subject LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if curriculum:
        where.append("curriculum = ?")
        params.append(curriculum)
    if class_filter:
        where.append("class = ?")
        params.append(class_filter)
    return " AND ".join(where), params


# ─── Batch list ───────────────────────────────────────────

@admin_batches_bp.route('/', methods=['GET'], endpoint='index')
@super_admin_required
def index():
    clean_expired_batch_edits()

    search = (request.args.get('search') or '').strip()
    kind = (request.args.get('kind') or '').strip()
    try:
        page = max(1, int(request.args.get('page') or 1))
    except ValueError:
        page = 1
    per_page = 20

    batches, total = list_batches(search=search, kind=kind,
                                  page=page, per_page=per_page)
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1

    return render_template(
        'dashboard/admin/batches/index.html',
        batches=batches,
        total=total,
        page=page,
        total_pages=total_pages,
        search=search,
        kind_filter=kind,
    )


# ─── New batch ────────────────────────────────────────────

@admin_batches_bp.route('/new', methods=['GET'], endpoint='new')
@super_admin_required
def new():
    return render_template('dashboard/admin/batches/new.html')


@admin_batches_bp.route('/new', methods=['POST'], endpoint='create')
@super_admin_required
def create():
    if not validate_csrf():
        abort(403)

    name = (request.form.get('name') or '').strip()
    kind = (request.form.get('kind') or 'mixed').strip().lower()
    notes = (request.form.get('notes') or '').strip()

    if not name:
        flash('Batch name is required.', 'error')
        return redirect(url_for('admin_batches.new'))
    if kind not in ('questions', 'pdfs', 'mixed'):
        kind = 'mixed'

    batch_id = create_batch(name=name, kind=kind,
                            admin_id=session.get('user_id'), notes=notes)
    if not batch_id:
        flash('Could not create batch.', 'error')
        return redirect(url_for('admin_batches.new'))

    write_audit(
        action='batch.create', target_type='batch', target_id=batch_id,
        before=None, after={'name': name, 'kind': kind},
        severity='info',
    )
    flash('Batch created. Add items below.', 'success')
    return redirect(url_for('admin_batches.detail', batch_id=batch_id))


# ─── Detail / editor ──────────────────────────────────────

@admin_batches_bp.route('/<int:batch_id>', methods=['GET'], endpoint='detail')
@super_admin_required
def detail(batch_id):
    batch = get_batch(batch_id)
    if not batch:
        abort(404)
    counts = batch_items_count(batch_id)

    return render_template(
        'dashboard/admin/batches/detail.html',
        batch=batch,
        counts=counts,
        subjects=get_all_subjects(),
        grades=[{'code': g, 'label': grade_label(g)} for g in VALID_GRADES],
    )


@admin_batches_bp.route('/<int:batch_id>/items', methods=['GET'],
                        endpoint='items')
@super_admin_required
def items(batch_id):
    batch = get_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    item_type = (request.args.get('type') or '').strip()
    search = (request.args.get('search') or '').strip()
    grade = (request.args.get('grade') or '').strip().upper()
    subject = (request.args.get('subject') or '').strip()
    status = (request.args.get('status') or '').strip()
    curriculum = (request.args.get('curriculum') or '').strip().upper()
    class_filter = (request.args.get('class') or '').strip().upper()

    if item_type == 'question':
        ids = get_batch_item_ids(batch_id, 'question')
        where, params = _build_question_filter(
            ids, search=search, grade=grade, subject=subject,
            status=status,
        )
        cursor = execute_with_retry(f"""
            SELECT id, subject_code, question_text, grade, difficulty,
                   chapter, status, pdf_code, pdf_page, created_at
            FROM questions q
            WHERE {where}
            ORDER BY q.id DESC
        """, params)
        rows = []
        for r in cursor.fetchall():
            d = dict(r)
            subj = get_subject(d.get('subject_code'))
            d['subject_name'] = subj['name'] if subj else d.get('subject_code')
            d['subject_icon'] = subj.get('icon', '📚') if subj else '📚'
            rows.append(d)
        return jsonify({'items': rows, 'total': len(rows)})

    if item_type == 'pdf':
        ids = get_batch_item_ids(batch_id, 'pdf')
        where, params = _build_pdf_filter(
            ids, search=search, subject=subject,
            curriculum=curriculum, class_filter=class_filter,
        )
        cursor = execute_with_retry(f"""
            SELECT id, code, title, subject, curriculum, class,
                   chapter, is_premium, uploaded_at
            FROM pdfs
            WHERE {where}
            ORDER BY id DESC
        """, params)
        rows = [dict(r) for r in cursor.fetchall()]
        return jsonify({'items': rows, 'total': len(rows)})

    return jsonify({'items': [], 'total': 0})


# ─── Single item fetch + save (drawer) ────────────────────

@admin_batches_bp.route('/<int:batch_id>/item/<item_type>/<int:item_id>',
                        methods=['GET'], endpoint='item_get')
@super_admin_required
def item_get(batch_id, item_type, item_id):
    if item_type == 'question':
        q = get_question_by_id(item_id)
        if not q:
            return jsonify({'error': 'Not found'}), 404
        return jsonify({'item': q})
    if item_type == 'pdf':
        p = get_pdf_by_id(item_id)
        if not p:
            return jsonify({'error': 'Not found'}), 404
        return jsonify({'item': p})
    return jsonify({'error': 'Invalid item_type'}), 400


@admin_batches_bp.route('/<int:batch_id>/item/<item_type>/<int:item_id>',
                        methods=['POST'], endpoint='item_save')
@super_admin_required
def item_save(batch_id, item_type, item_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    data = request.get_json(silent=True) or {}

    if item_type == 'question':
        current = get_question_by_id(item_id)
        if not current:
            return jsonify({'error': 'Question not found'}), 404
        payload = {
            'subject_code': (data.get('subject_code') or current['subject_code']).strip(),
            'question_text': (data.get('question_text') or current['question_text']).strip(),
            'options': data.get('options') or current.get('options') or {},
            'correct_answer': (data.get('correct_answer') or current['correct_answer']).strip().upper(),
            'difficulty': int(data.get('difficulty') or current.get('difficulty') or 1),
            'chapter': (data.get('chapter') or current.get('chapter') or '').strip(),
            'tags': (data.get('tags') or current.get('tags') or '').strip(),
            'explanation': (data.get('explanation') or current.get('explanation') or '').strip(),
            'pdf_code': (data.get('pdf_code') or current.get('pdf_code') or None),
            'pdf_page': data.get('pdf_page') or current.get('pdf_page'),
            'status': (data.get('status') or current.get('status') or 'active').strip(),
            'grade': (data.get('grade') or current.get('grade') or 'F4').strip().upper(),
            'updated_by': session.get('user_id'),
        }
        if not update_question(item_id, payload):
            return jsonify({'error': 'Save failed'}), 500
        write_audit(
            action='batch.item_update', target_type='question',
            target_id=item_id,
            before={'grade': current.get('grade')},
            after={'grade': payload['grade']},
            severity='info',
        )
        return jsonify({'success': True})

    if item_type == 'pdf':
        current = get_pdf_by_id(item_id)
        if not current:
            return jsonify({'error': 'PDF not found'}), 404
        title = (data.get('title') or current['title']).strip()
        subject = (data.get('subject') or current.get('subject') or '').strip()
        if not title or not subject:
            return jsonify({'error': 'Title and Subject are required'}), 400
        try:
            execute_with_retry("""
                UPDATE pdfs SET
                    title = ?, description = ?, curriculum = ?, class = ?,
                    subject = ?, chapter = ?, tags = ?, is_premium = ?
                WHERE id = ?
            """, (
                title,
                (data.get('description') or current.get('description') or '').strip(),
                (data.get('curriculum') or current.get('curriculum') or 'PL').strip(),
                (data.get('class') or current.get('class') or '').strip(),
                subject,
                (data.get('chapter') or current.get('chapter') or '').strip(),
                (data.get('tags') or current.get('tags') or '').strip(),
                1 if data.get('is_premium') else int(current.get('is_premium') or 0),
                item_id,
            ), commit=True)
            write_audit(
                action='batch.item_update', target_type='pdf',
                target_id=item_id, before=None,
                after={'title': title, 'subject': subject},
                severity='info',
            )
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"batch item_save pdf failed: {e}")
            return jsonify({'error': 'Save failed'}), 500

    return jsonify({'error': 'Invalid item_type'}), 400


# ─── Single-change bulk edit (kept for compat) ────────────

@admin_batches_bp.route('/<int:batch_id>/bulk-edit', methods=['POST'],
                        endpoint='bulk_edit')
@super_admin_required
def bulk_edit(batch_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    batch = get_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    payload = request.get_json(silent=True) or {}
    item_type = (payload.get('item_type') or '').strip()
    action = (payload.get('action') or '').strip()
    value = payload.get('value')
    item_ids = _int_list(payload.get('item_ids'))

    if item_type not in ('question', 'pdf'):
        return jsonify({'error': 'Invalid item_type'}), 400
    if not item_ids:
        return jsonify({'error': 'No items selected'}), 400

    owned = set(get_batch_item_ids(batch_id, item_type))
    valid_ids = [x for x in item_ids if x in owned]
    if not valid_ids:
        return jsonify({'error': 'No valid items'}), 400

    try:
        before_snapshot, _ = _apply_batch_changes(
            item_type,
            [{'action': action, 'value': value, 'item_ids': valid_ids}],
            owned,
        )

        edit_id = record_batch_edit(
            batch_id=batch_id,
            admin_id=session.get('user_id'),
            action=action,
            item_type=item_type,
            item_ids=valid_ids,
            before_data=before_snapshot,
            undo_seconds=UNDO_SECONDS,
        )

        write_audit(
            action=f'batch.bulk_{action}',
            target_type=item_type,
            before={'count': len(valid_ids)},
            after={'count': len(valid_ids), 'batch_id': batch_id},
            severity='info',
        )

        return jsonify({
            'success': True,
            'affected': len(valid_ids),
            'undo_id': edit_id,
            'undo_seconds': UNDO_SECONDS,
        })
    except Exception as e:
        logger.error(f"bulk_edit failed: {e}", exc_info=True)
        return jsonify({'error': 'Bulk edit failed'}), 500


# ─── Multi-change bulk edit (staged, one save) ────────────

@admin_batches_bp.route('/<int:batch_id>/bulk-edit-batch', methods=['POST'],
                        endpoint='bulk_edit_batch')
@super_admin_required
def bulk_edit_batch(batch_id):
    """
    Stage many changes and commit them in one shot.

    Body:
      {
        "item_type": "question" | "pdf",
        "changes": [
          {"action": "set_grade", "value": "F3", "item_ids": [1,2,3]},
          {"action": "set_subject", "value": "geography", "item_ids": [1,2,3]},
          {"action": "add_tag", "value": "algebra", "item_ids": [1,2,3]}
        ]
      }
    """
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    batch = get_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404

    payload = request.get_json(silent=True) or {}
    item_type = (payload.get('item_type') or '').strip()
    raw_changes = payload.get('changes') or []

    if item_type not in ('question', 'pdf'):
        return jsonify({'error': 'Invalid item_type'}), 400
    if not isinstance(raw_changes, list) or not raw_changes:
        return jsonify({'error': 'No changes provided'}), 400

    owned = set(get_batch_item_ids(batch_id, item_type))
    if not owned:
        return jsonify({'error': 'Batch is empty'}), 400

    # Filter and validate
    clean_changes = []
    total_ids = set()
    for ch in raw_changes:
        if not isinstance(ch, dict):
            continue
        action = (ch.get('action') or '').strip()
        value = ch.get('value')
        ids = [x for x in _int_list(ch.get('item_ids') or []) if x in owned]
        if not action or not ids:
            continue
        clean_changes.append({'action': action, 'value': value, 'item_ids': ids})
        total_ids.update(ids)

    if not clean_changes:
        return jsonify({'error': 'No valid changes'}), 400

    try:
        before_snapshot, applied = _apply_batch_changes(
            item_type, clean_changes, owned,
        )

        edit_id = record_batch_edit(
            batch_id=batch_id,
            admin_id=session.get('user_id'),
            action='batch_multi',
            item_type=item_type,
            item_ids=sorted(total_ids),
            before_data=before_snapshot,
            undo_seconds=UNDO_SECONDS,
        )

        write_audit(
            action='batch.bulk_batch_edit',
            target_type=item_type,
            before={'count': len(total_ids)},
            after={
                'count': len(total_ids),
                'changes': [c['action'] for c in clean_changes],
                'batch_id': batch_id,
            },
            severity='info',
        )

        return jsonify({
            'success': True,
            'affected': len(total_ids),
            'changes_applied': applied,
            'undo_id': edit_id,
            'undo_seconds': UNDO_SECONDS,
        })
    except Exception as e:
        logger.error(f"bulk_edit_batch failed: {e}", exc_info=True)
        return jsonify({'error': 'Bulk edit failed'}), 500


# ─── Core: apply a list of changes atomically-ish ─────────

def _apply_batch_changes(item_type, changes, owned_set):
    """
    changes: list of {action, value, item_ids}
    Returns (before_snapshot, applied_count).

    Snapshotting: for every column touched by any change, we capture
    the pre-change value across every affected id — one query per
    change. Snapshot values are only captured once per (item, column).
    """
    table = 'questions' if item_type == 'question' else 'pdfs'
    col_map = _QUESTION_ACTION_COLUMN if item_type == 'question' else _PDF_ACTION_COLUMN

    before_snapshot = {'item_type': item_type, 'items': {}}
    applied = 0

    for ch in changes:
        action = ch.get('action') or ''
        value = ch.get('value')
        ids = ch.get('item_ids') or []
        if not ids:
            continue

        placeholders = ','.join('?' for _ in ids)

        # ── Tag operations ───────────────────────────────
        if action in _TAG_ACTIONS:
            ok = _apply_tag_op(table, action, value, ids, before_snapshot)
            if ok:
                applied += 1
            continue

        # ── Column updates ───────────────────────────────
        column = col_map.get(action)
        if not column:
            continue

        cast_value = _cast_column_value(action, value)
        if cast_value is _INVALID:
            continue

        # Snapshot the pre-change value for this column on these ids
        try:
            cursor = execute_with_retry(
                f"SELECT id, {column} AS v FROM {table} "
                f"WHERE id IN ({placeholders})",
                ids,
            )
            for row in cursor.fetchall():
                iid_str = str(row['id'])
                bucket = before_snapshot['items'].setdefault(iid_str, {})
                if column not in bucket:
                    bucket[column] = row['v']
        except Exception as e:
            logger.warning(f"snapshot {table}.{column} failed: {e}")

        # Apply the update
        try:
            extra_set = ", updated_at = ?" if table == 'questions' else ""
            params = [cast_value]
            if table == 'questions':
                params.append(datetime.now(timezone.utc).isoformat())
            params.extend(ids)
            execute_with_retry(
                f"UPDATE {table} SET {column} = ?{extra_set} "
                f"WHERE id IN ({placeholders})",
                params, commit=True,
            )
            applied += 1
        except Exception as e:
            logger.error(f"apply {table}.{column} failed: {e}")

    return before_snapshot, applied


class _InvalidType:
    pass

_INVALID = _InvalidType()


def _cast_column_value(action, value):
    """Return the properly typed value for a given action, or _INVALID."""
    if action == 'set_difficulty':
        try:
            return max(1, min(5, int(value)))
        except (TypeError, ValueError):
            return _INVALID
    if action == 'set_pdf_page':
        if value in (None, '', 'null'):
            return None
        try:
            n = int(value)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return _INVALID
    if action == 'set_grade':
        v = (value or '').strip().upper()
        return v if v in VALID_GRADES else _INVALID
    if action == 'set_status':
        v = (value or '').strip()
        return v if v in ('active', 'archived', 'draft') else _INVALID
    if action == 'set_curriculum':
        v = (value or '').strip().upper()
        return v if v in ('PL', 'SO', 'SL') else _INVALID
    if action == 'set_class':
        v = (value or '').strip().upper()
        return v if v in ('F4', 'F3', 'G8', 'G7') else _INVALID
    if action == 'toggle_premium':
        return 1 if value else 0
    # set_subject, set_chapter, set_pdf_code — string
    if value is None:
        return _INVALID
    return str(value).strip()


def _apply_tag_op(table, action, value, ids, before_snapshot):
    """Add / remove / clear tags. Returns True on success."""
    placeholders = ','.join('?' for _ in ids)

    try:
        cursor = execute_with_retry(
            f"SELECT id, tags FROM {table} WHERE id IN ({placeholders})",
            ids,
        )
        rows = cursor.fetchall()
    except Exception as e:
        logger.error(f"tag read failed: {e}")
        return False

    # Snapshot existing tags
    for row in rows:
        iid_str = str(row['id'])
        bucket = before_snapshot['items'].setdefault(iid_str, {})
        if 'tags' not in bucket:
            bucket['tags'] = row['tags']

    if action == 'clear_tags':
        try:
            execute_with_retry(
                f"UPDATE {table} SET tags = '' "
                f"WHERE id IN ({placeholders})",
                ids, commit=True,
            )
            return True
        except Exception as e:
            logger.error(f"clear_tags failed: {e}")
            return False

    tag = (value or '').strip().lower()
    if not tag:
        return False

    for row in rows:
        existing = [t.strip() for t in (row['tags'] or '').split(',') if t.strip()]
        lowered = [t.lower() for t in existing]

        if action == 'add_tag':
            if tag not in lowered:
                existing.append(tag)
        else:  # remove_tag
            existing = [t for t in existing if t.lower() != tag]

        new_tags = ','.join(existing)
        try:
            execute_with_retry(
                f"UPDATE {table} SET tags = ? WHERE id = ?",
                (new_tags, row['id']), commit=True,
            )
        except Exception as e:
            logger.warning(f"tag update failed for {row['id']}: {e}")

    return True


# ─── Undo ─────────────────────────────────────────────────

@admin_batches_bp.route('/<int:batch_id>/undo/<int:edit_id>',
                        methods=['POST'], endpoint='undo')
@super_admin_required
def undo(batch_id, edit_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403

    edit = get_batch_edit(edit_id)
    if not edit or edit['batch_id'] != batch_id:
        return jsonify({'error': 'Edit not found'}), 404
    if edit.get('undone'):
        return jsonify({'error': 'Already undone'}), 400

    try:
        undo_until = datetime.fromisoformat(edit['undo_until'])
        if datetime.now(timezone.utc) > undo_until:
            return jsonify({'error': 'Undo window expired'}), 400
    except Exception:
        return jsonify({'error': 'Invalid undo state'}), 400

    item_type = edit['item_type']
    before = json.loads(edit['before_data']) or {}
    items_map = before.get('items') or {}
    if not items_map:
        return jsonify({'error': 'Nothing to undo'}), 400

    table = 'questions' if item_type == 'question' else 'pdfs'

    try:
        for item_id_str, fields in items_map.items():
            if not fields:
                continue
            try:
                item_id = int(item_id_str)
            except ValueError:
                continue
            set_clauses = []
            params = []
            for col, val in fields.items():
                set_clauses.append(f"{col} = ?")
                params.append(val)
            if not set_clauses:
                continue
            params.append(item_id)
            execute_with_retry(
                f"UPDATE {table} SET {', '.join(set_clauses)} WHERE id = ?",
                params, commit=True,
            )

        mark_batch_edit_undone(edit_id)
        write_audit(
            action='batch.undo', target_type=item_type,
            before=None, after={'edit_id': edit_id, 'count': len(items_map)},
            severity='warning',
        )
        return jsonify({'success': True, 'restored': len(items_map)})
    except Exception as e:
        logger.error(f"undo failed: {e}", exc_info=True)
        return jsonify({'error': 'Undo failed'}), 500


# ─── Rename / pin / delete ────────────────────────────────

@admin_batches_bp.route('/<int:batch_id>/rename', methods=['POST'],
                        endpoint='rename')
@super_admin_required
def rename(batch_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403
    name = (request.get_json(silent=True) or {}).get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name required'}), 400
    if not update_batch(batch_id, {'name': name[:200]}):
        return jsonify({'error': 'Rename failed'}), 500
    write_audit(
        action='batch.rename', target_type='batch', target_id=batch_id,
        before=None, after={'name': name}, severity='info',
    )
    return jsonify({'success': True, 'name': name})


@admin_batches_bp.route('/<int:batch_id>/pin', methods=['POST'],
                        endpoint='pin')
@super_admin_required
def pin(batch_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403
    batch = get_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Not found'}), 404
    new_val = 0 if batch.get('pinned') else 1
    if not update_batch(batch_id, {'pinned': new_val}):
        return jsonify({'error': 'Pin failed'}), 500
    return jsonify({'success': True, 'pinned': bool(new_val)})


@admin_batches_bp.route('/<int:batch_id>/delete', methods=['POST'],
                        endpoint='delete')
@super_admin_required
def delete(batch_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403
    batch = get_batch(batch_id)
    if not batch:
        return jsonify({'error': 'Not found'}), 404

    if not delete_batch(batch_id):
        return jsonify({'error': 'Delete failed'}), 500

    write_audit(
        action='batch.delete', target_type='batch', target_id=batch_id,
        before={'name': batch['name'], 'item_count': batch['item_count']},
        after=None, severity='warning',
    )
    return jsonify({'success': True})


# ─── Remove / add items ───────────────────────────────────

@admin_batches_bp.route('/<int:batch_id>/remove-items', methods=['POST'],
                        endpoint='remove_items')
@super_admin_required
def remove_items(batch_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403
    payload = request.get_json(silent=True) or {}
    item_type = (payload.get('item_type') or '').strip()
    item_ids = _int_list(payload.get('item_ids'))
    if item_type not in ('question', 'pdf') or not item_ids:
        return jsonify({'error': 'Nothing to remove'}), 400

    removed = remove_batch_items(batch_id, item_type, item_ids)
    write_audit(
        action='batch.remove_items', target_type=item_type,
        before={'count': len(item_ids)}, after={'removed': removed},
        severity='info',
    )
    return jsonify({'success': True, 'removed': removed})


@admin_batches_bp.route('/<int:batch_id>/add-items', methods=['POST'],
                        endpoint='add_items')
@super_admin_required
def add_items(batch_id):
    if not _csrf_ok():
        return jsonify({'error': 'Invalid session.'}), 403
    payload = request.get_json(silent=True) or {}
    item_type = (payload.get('item_type') or '').strip()
    item_ids = _int_list(payload.get('item_ids'))
    if item_type not in ('question', 'pdf') or not item_ids:
        return jsonify({'error': 'Nothing to add'}), 400

    added = add_batch_items(batch_id, item_type, item_ids)
    return jsonify({'success': True, 'added': added})


# ─── Recent batches (topbar dropdown) ─────────────────────

@admin_batches_bp.route('/recent', methods=['GET'], endpoint='recent')
@super_admin_required
def recent():
    return jsonify({'batches': recent_batches(limit=5)})
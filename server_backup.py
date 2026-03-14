"""
ProspectorAI — Servidor Flask
DiazUX Studio
"""

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import smtplib
import ssl
import json
import sqlite3
import os
import uuid
import hashlib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from datetime import datetime
import threading

app = Flask(__name__, static_folder='.')
CORS(app)

DB_PATH = 'prospector.db'
BASE_URL = 'http://localhost:5000'

# ── BASE DE DATOS ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS prospects (
            id          TEXT PRIMARY KEY,
            brand       TEXT,
            contact     TEXT,
            email       TEXT,
            url         TEXT,
            problem     TEXT,
            source      TEXT,
            status      TEXT DEFAULT 'Sin contactar',
            score       INTEGER DEFAULT 0,
            date        TEXT,
            notes       TEXT,
            deal_value  REAL DEFAULT 0,
            deal_stage  TEXT DEFAULT 'Lead',
            last_contact TEXT
        );
        CREATE TABLE IF NOT EXISTS emails_sent (
            id          TEXT PRIMARY KEY,
            prospect_id TEXT,
            subject     TEXT,
            body        TEXT,
            sent_at     TEXT,
            opened      INTEGER DEFAULT 0,
            opened_at   TEXT,
            clicked     INTEGER DEFAULT 0,
            sequence_num INTEGER DEFAULT 1,
            track_id    TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS activities (
            id          TEXT PRIMARY KEY,
            prospect_id TEXT,
            type        TEXT,
            description TEXT,
            date        TEXT
        );
    ''')
    conn.commit()
    conn.close()

init_db()

# ── HELPERS ──────────────────────────────────────────────────────
def log_activity(prospect_id, type_, description):
    conn = get_db()
    conn.execute('INSERT INTO activities VALUES (?,?,?,?,?)',
        (str(uuid.uuid4()), prospect_id, type_, description,
         datetime.now().strftime('%Y-%m-%d %H:%M')))
    conn.commit()
    conn.close()

def calculate_score(prospect):
    score = 0
    if prospect['email']:           score += 20
    if prospect['url']:             score += 10
    if prospect['contact']:         score += 10
    if prospect['problem']:         score += 15
    status_scores = {
        'Sin contactar': 0, 'Email 1 enviado': 15, 'Email 2 enviado': 20,
        'Email 3 enviado': 25, 'Respondió': 50, 'Reunión agendada': 75, 'Cliente': 100
    }
    score += status_scores.get(prospect['status'], 0)
    return min(score, 100)

# ── CONFIG ───────────────────────────────────────────────────────
@app.route('/api/config', methods=['GET', 'POST'])
def config():
    conn = get_db()
    if request.method == 'POST':
        data = request.json
        for k, v in data.items():
            conn.execute('INSERT OR REPLACE INTO config VALUES (?,?)', (k, str(v)))
        conn.commit()
        conn.close()
        return jsonify({'ok': True})
    rows = conn.execute('SELECT key, value FROM config').fetchall()
    conn.close()
    return jsonify({r['key']: r['value'] for r in rows})

# ── PROSPECTS CRUD ───────────────────────────────────────────────
@app.route('/api/prospects', methods=['GET'])
def get_prospects():
    status = request.args.get('status')
    conn = get_db()
    if status and status != 'all':
        rows = conn.execute('SELECT * FROM prospects WHERE status=? ORDER BY date DESC', (status,)).fetchall()
    else:
        rows = conn.execute('SELECT * FROM prospects ORDER BY date DESC').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/prospects', methods=['POST'])
def add_prospect():
    data = request.json
    pid = str(uuid.uuid4())
    conn = get_db()
    conn.execute('''INSERT INTO prospects
        (id,brand,contact,email,url,problem,source,status,score,date,notes,deal_value,deal_stage)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (pid, data.get('brand',''), data.get('contact',''), data.get('email',''),
         data.get('url',''), data.get('problem',''), data.get('source',''),
         'Sin contactar', 0, datetime.now().strftime('%d/%m/%Y'),
         data.get('notes',''), data.get('deal_value',0), 'Lead'))
    conn.commit()
    # Recalculate score
    row = dict(conn.execute('SELECT * FROM prospects WHERE id=?', (pid,)).fetchone())
    score = calculate_score(row)
    conn.execute('UPDATE prospects SET score=? WHERE id=?', (score, pid))
    conn.commit()
    conn.close()
    log_activity(pid, 'created', 'Prospecto creado')
    return jsonify({'ok': True, 'id': pid})

@app.route('/api/prospects/<pid>', methods=['PUT'])
def update_prospect(pid):
    data = request.json
    conn = get_db()
    fields = ['brand','contact','email','url','problem','source','status','notes','deal_value','deal_stage']
    updates = {k: data[k] for k in fields if k in data}
    if updates:
        set_clause = ', '.join(f'{k}=?' for k in updates)
        values = list(updates.values()) + [pid]
        conn.execute(f'UPDATE prospects SET {set_clause} WHERE id=?', values)
        if 'status' in updates:
            conn.execute('UPDATE prospects SET last_contact=? WHERE id=?',
                (datetime.now().strftime('%d/%m/%Y %H:%M'), pid))
            log_activity(pid, 'status', f'Estado → {updates["status"]}')
        row = dict(conn.execute('SELECT * FROM prospects WHERE id=?', (pid,)).fetchone())
        score = calculate_score(row)
        conn.execute('UPDATE prospects SET score=? WHERE id=?', (score, pid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/prospects/<pid>', methods=['DELETE'])
def delete_prospect(pid):
    conn = get_db()
    conn.execute('DELETE FROM prospects WHERE id=?', (pid,))
    conn.execute('DELETE FROM emails_sent WHERE prospect_id=?', (pid,))
    conn.execute('DELETE FROM activities WHERE prospect_id=?', (pid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/prospects/bulk', methods=['POST'])
def bulk_add():
    prospects = request.json.get('prospects', [])
    added = 0
    for p in prospects:
        pid = str(uuid.uuid4())
        conn = get_db()
        conn.execute('''INSERT INTO prospects
            (id,brand,contact,email,url,problem,source,status,score,date,notes,deal_value,deal_stage)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (pid, p.get('brand',''), p.get('contact',''), p.get('email',''),
             p.get('url',''), p.get('problem',''), p.get('source','Scraper'),
             'Sin contactar', 0, datetime.now().strftime('%d/%m/%Y'), '', 0, 'Lead'))
        conn.commit()
        conn.close()
        added += 1
    return jsonify({'ok': True, 'added': added})

# ── EMAIL SEND + TRACKING ────────────────────────────────────────
@app.route('/api/email/send', methods=['POST'])
def send_email():
    data = request.json
    conn = get_db()
    cfg = {r['key']: r['value'] for r in conn.execute('SELECT key,value FROM config').fetchall()}
    conn.close()

    smtp_host = cfg.get('smtp_host', '')
    smtp_port = int(cfg.get('smtp_port', 587))
    smtp_user = cfg.get('smtp_user', '')
    smtp_pass = cfg.get('smtp_pass', '')
    from_name = cfg.get('name', 'DiazUX Studio')

    if not all([smtp_host, smtp_user, smtp_pass]):
        return jsonify({'ok': False, 'error': 'Configurá SMTP en Ajustes primero'})

    to_email = data.get('to')
    subject  = data.get('subject', '')
    body     = data.get('body', '')
    prospect_id = data.get('prospect_id', '')
    seq_num  = data.get('sequence_num', 1)

    # Tracking pixel
    track_id = str(uuid.uuid4())
    pixel = f'<img src="{BASE_URL}/track/open/{track_id}" width="1" height="1" style="display:none"/>'
    html_body = body.replace('\n', '<br>') + pixel

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f'{from_name} <{smtp_user}>'
        msg['To']      = to_email
        msg.attach(MIMEText(body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())

        # Log
        conn = get_db()
        conn.execute('INSERT INTO emails_sent VALUES (?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()), prospect_id, subject, body,
             datetime.now().strftime('%Y-%m-%d %H:%M'), 0, None, 0, seq_num, track_id))
        status_map = {1: 'Email 1 enviado', 2: 'Email 2 enviado', 3: 'Email 3 enviado'}
        if prospect_id and seq_num in status_map:
            conn.execute('UPDATE prospects SET status=?, last_contact=? WHERE id=?',
                (status_map[seq_num], datetime.now().strftime('%d/%m/%Y %H:%M'), prospect_id))
            row = dict(conn.execute('SELECT * FROM prospects WHERE id=?', (prospect_id,)).fetchone())
            conn.execute('UPDATE prospects SET score=? WHERE id=?', (calculate_score(row), prospect_id))
        conn.commit()
        conn.close()
        log_activity(prospect_id, 'email', f'Email #{seq_num} enviado a {to_email}')
        return jsonify({'ok': True, 'track_id': track_id})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ── TRACKING PIXEL ───────────────────────────────────────────────
@app.route('/track/open/<track_id>')
def track_open(track_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM emails_sent WHERE track_id=?', (track_id,)).fetchone()
    if row and not row['opened']:
        conn.execute('UPDATE emails_sent SET opened=1, opened_at=? WHERE track_id=?',
            (datetime.now().strftime('%Y-%m-%d %H:%M'), track_id))
        conn.commit()
        if row['prospect_id']:
            log_activity(row['prospect_id'], 'open', f'Email #{row["sequence_num"]} abierto')
    conn.close()
    # Return 1x1 transparent GIF
    gif = b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    from flask import Response
    return Response(gif, mimetype='image/gif')

# ── EMAIL HISTORY ────────────────────────────────────────────────
@app.route('/api/emails/<prospect_id>')
def get_emails(prospect_id):
    conn = get_db()
    rows = conn.execute('SELECT * FROM emails_sent WHERE prospect_id=? ORDER BY sent_at DESC', (prospect_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── STATS / DASHBOARD ────────────────────────────────────────────
@app.route('/api/stats')
def get_stats():
    conn = get_db()
    total    = conn.execute('SELECT COUNT(*) FROM prospects').fetchone()[0]
    new_p    = conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Sin contactar'").fetchone()[0]
    in_seq   = conn.execute("SELECT COUNT(*) FROM prospects WHERE status IN ('Email 1 enviado','Email 2 enviado','Email 3 enviado')").fetchone()[0]
    replied  = conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Respondió'").fetchone()[0]
    meetings = conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Reunión agendada'").fetchone()[0]
    clients  = conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Cliente'").fetchone()[0]
    emails_t = conn.execute('SELECT COUNT(*) FROM emails_sent').fetchone()[0]
    opens    = conn.execute('SELECT COUNT(*) FROM emails_sent WHERE opened=1').fetchone()[0]
    pipeline = conn.execute('SELECT COALESCE(SUM(deal_value),0) FROM prospects').fetchone()[0]
    # By stage
    stages   = conn.execute("SELECT status, COUNT(*) as cnt FROM prospects GROUP BY status").fetchall()
    # Top prospects by score
    top      = conn.execute('SELECT id,brand,email,score,status FROM prospects ORDER BY score DESC LIMIT 5').fetchall()
    # Recent activity
    recent   = conn.execute('SELECT * FROM activities ORDER BY date DESC LIMIT 10').fetchall()
    conn.close()
    rate = round(replied / total * 100, 1) if total else 0
    open_rate = round(opens / emails_t * 100, 1) if emails_t else 0
    return jsonify({
        'total': total, 'new': new_p, 'in_seq': in_seq,
        'replied': replied, 'meetings': meetings, 'clients': clients,
        'rate': rate, 'emails_sent': emails_t, 'opens': opens,
        'open_rate': open_rate, 'pipeline_value': pipeline,
        'stages': [dict(r) for r in stages],
        'top_prospects': [dict(r) for r in top],
        'recent_activity': [dict(r) for r in recent]
    })

# ── ACTIVITIES ───────────────────────────────────────────────────
@app.route('/api/activities/<prospect_id>')
def get_activities(prospect_id):
    conn = get_db()
    rows = conn.execute('SELECT * FROM activities WHERE prospect_id=? ORDER BY date DESC', (prospect_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ── SMTP TEST ────────────────────────────────────────────────────
@app.route('/api/smtp/test', methods=['POST'])
def test_smtp():
    data = request.json
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(data['host'], int(data['port'])) as s:
            s.starttls(context=context)
            s.login(data['user'], data['password'])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

# ── EXPORT CSV ───────────────────────────────────────────────────
@app.route('/api/export/csv')
def export_csv():
    conn = get_db()
    rows = conn.execute('SELECT * FROM prospects ORDER BY date DESC').fetchall()
    conn.close()
    lines = ['Marca,Contacto,Email,URL,Estado,Score,Fecha,Problema,Fuente,Deal Value,Deal Stage']
    for r in rows:
        vals = [str(r[f] or '') for f in ['brand','contact','email','url','status','score','date','problem','source','deal_value','deal_stage']]
        lines.append(','.join(f'"{v.replace(chr(34), chr(39))}"' for v in vals))
    from flask import Response
    return Response('\n'.join(lines), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=prospectos_diazux.csv'})

# ── SERVE FRONTEND ───────────────────────────────────────────────
@app.route('/')
def index():
    return send_file('index.html')

if __name__ == '__main__':
    print('\n' + '='*50)
    print('  ProspectorAI — DiazUX Studio')
    print('  Servidor corriendo en http://localhost:5000')
    print('='*50 + '\n')
    app.run(debug=False, port=5000, host='0.0.0.0')

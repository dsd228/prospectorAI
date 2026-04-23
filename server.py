"""
ProspectorAI — Servidor Flask COMPLETO
DiazUX Studio
"""
from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from flask_cors import CORS
import smtplib, ssl, json, sqlite3, os, uuid, threading, time
import socket
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import urllib.request, urllib.parse, urllib.error
from prospector_engine import ProspectorEngine
from web_auditor import audit_website

app = Flask(__name__, static_folder='.')
CORS(app)
DB_PATH = 'prospector.db'
BASE_URL = os.environ.get('BASE_URL', 'https://prospectorai.onrender.com')
AUTOPILOT_SCHEDULES = [
    {"time": "09:00", "action": "run_prospecting", "query": "negocios sin web argentina"},
    {"time": "11:00", "action": "send_pending_emails"},
    {"time": "14:00", "action": "publish_scheduled_posts"},
    {"time": "16:00", "action": "send_pending_dms"},
    {"time": "18:00", "action": "generate_weekly_posts"},
]
DAILY_LIMITS = {
    "emails": int(os.environ.get("EMAIL_DAILY_LIMIT", "30")),
    "dms_instagram": int(os.environ.get("DM_DAILY_LIMIT", "20")),
    "dms_linkedin": 15,
    "posts_instagram": 3,
    "posts_linkedin": 2,
}
DELAYS = {
    "between_emails": int(os.environ.get("DELAY_BETWEEN_EMAILS", "300")),
    "between_dms_ig": int(os.environ.get("DELAY_BETWEEN_DMS_IG", "600")),
    "between_dms_li": int(os.environ.get("DELAY_BETWEEN_DMS_LI", "900")),
    "between_scraping": int(os.environ.get("DELAY_BETWEEN_SCRAPING", "2")),
    "after_post": int(os.environ.get("DELAY_AFTER_POST", "1800")),
}
LINKEDIN_OAUTH_STATE = {}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS prospects (
            id TEXT PRIMARY KEY, brand TEXT, contact TEXT, email TEXT,
            url TEXT, problem TEXT, source TEXT, status TEXT DEFAULT 'Sin contactar',
            score INTEGER DEFAULT 0, date TEXT, notes TEXT,
            deal_value REAL DEFAULT 0, deal_stage TEXT DEFAULT 'Lead', last_contact TEXT);
        CREATE TABLE IF NOT EXISTS emails_sent (
            id TEXT PRIMARY KEY, prospect_id TEXT, subject TEXT, body TEXT,
            sent_at TEXT, opened INTEGER DEFAULT 0, opened_at TEXT,
            clicked INTEGER DEFAULT 0, sequence_num INTEGER DEFAULT 1, track_id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS activities (
            id TEXT PRIMARY KEY, prospect_id TEXT, type TEXT, description TEXT, date TEXT);
        CREATE TABLE IF NOT EXISTS social_posts (
            id TEXT PRIMARY KEY, platform TEXT, content TEXT, image_path TEXT,
            status TEXT DEFAULT 'draft', scheduled_at TEXT, published_at TEXT,
            post_url TEXT, error TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS social_tokens (
            platform TEXT PRIMARY KEY, access_token TEXT, refresh_token TEXT,
            expires_at TEXT, user_id TEXT, extra TEXT);
        CREATE TABLE IF NOT EXISTS email_sequences (
            id TEXT PRIMARY KEY,
            prospect_id TEXT,
            sequence_num INTEGER,
            subject TEXT,
            body TEXT,
            scheduled_at TEXT,
            sent INTEGER DEFAULT 0,
            sent_at TEXT,
            generated_by_model TEXT
        );
        CREATE TABLE IF NOT EXISTS scraping_jobs (
            id TEXT PRIMARY KEY,
            query TEXT,
            source TEXT,
            industry TEXT,
            location TEXT,
            results_count INTEGER,
            qualified_count INTEGER,
            started_at TEXT,
            completed_at TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS audit_reports (
            id TEXT PRIMARY KEY,
            prospect_id TEXT,
            url TEXT,
            performance_score INTEGER,
            mobile_friendly INTEGER,
            has_ssl INTEGER,
            load_time_ms INTEGER,
            seo_score INTEGER,
            tech_stack TEXT,
            problems_detected TEXT,
            opportunity_summary TEXT,
            recommended_service TEXT,
            created_at TEXT,
            model_used TEXT
        );
        CREATE TABLE IF NOT EXISTS opt_out (
            id TEXT PRIMARY KEY,
            prospect_id TEXT,
            reason TEXT,
            created_at TEXT
        );
    ''')
    conn.commit(); conn.close()

init_db()

def migrate_db():
    conn = get_db()
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(prospects)").fetchall()}
    alters = [
        ("instagram_handle", "TEXT"),
        ("linkedin_url", "TEXT"),
        ("phone", "TEXT"),
        ("industry", "TEXT"),
        ("location", "TEXT"),
        ("audit_data", "TEXT"),
        ("audit_date", "TEXT"),
        ("performance_score", "INTEGER DEFAULT 0"),
        ("recommended_service", "TEXT"),
        ("dm_instagram_sent", "INTEGER DEFAULT 0"),
        ("dm_linkedin_sent", "INTEGER DEFAULT 0"),
    ]
    for name, typ in alters:
        if name not in cols:
            conn.execute(f"ALTER TABLE prospects ADD COLUMN {name} {typ}")
    conn.commit(); conn.close()

migrate_db()

# ═══════════════════════════════════════════════════════════════════
# HELPER: Llamada a Claude API (sin SDK, solo urllib)
# ═══════════════════════════════════════════════════════════════════
def _claude(prompt, system="Sos un experto en marketing digital argentino. Respondés siempre en español rioplatense, de forma directa y profesional.", max_tokens=1000, temperature=0.7):
    """Llama a Claude claude-sonnet-4-20250514 via HTTP. Devuelve (texto, error)."""
    cfg = get_cfg()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or cfg.get("anthropic_api_key", "")
    if not api_key:
        return None, "Configurá ANTHROPIC_API_KEY en Ajustes o en variables de entorno"

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            res = json.loads(r.read())
        text = res["content"][0]["text"]
        return text, None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        print(f"Claude API error: {err}")
        try:
            msg = json.loads(err).get("error", {}).get("message", err)
        except:
            msg = err
        return None, msg
    except Exception as e:
        return None, str(e)


# ═══════════════════════════════════════════════════════════════════
# HELPER: Detección de modelos Ollama instalados
# ═══════════════════════════════════════════════════════════════════
MODEL_TASKS = {
    "audit_analysis": ["deepseek-r1", "llama3.1", "mistral", "llama3"],
    "email_writing": ["llama3.1", "mistral", "llama3", "phi3"],
    "post_instagram": ["llama3.1", "mistral", "llama3", "gemma"],
    "post_linkedin": ["deepseek-r1", "llama3.1", "mistral"],
    "dm_writing": ["llama3.1", "mistral", "llama3"],
    "lead_scoring": ["deepseek-r1", "llama3.1", "phi3"],
    "fast_generation": ["phi3", "gemma:2b", "mistral"],
}
PREFERRED_ORDER = [
    "deepseek-r1:latest",
    "llama3.1:latest",
    "llama3.1:8b",
    "llama3:latest",
    "mistral:latest",
    "mixtral:latest",
    "phi3:latest",
    "gemma:latest",
    "deepseek-coder:latest",
]

def _get_ollama_models():
    """Devuelve lista de modelos instalados en Ollama local."""
    cfg = get_cfg()
    ollama_url = os.environ.get("OLLAMA_URL", "") or cfg.get("ollama_url", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read())
        models = [m["name"] for m in res.get("models", [])]
        return models
    except:
        return []


def _best_ollama_model(task="general"):
    """Selecciona el mejor modelo Ollama según la tarea."""
    models = _get_ollama_models()
    if not models:
        return "llama3:latest"
    preferred = MODEL_TASKS.get(task, PREFERRED_ORDER)
    if task in ("creative", "general"):
        preferred = ["llama3.1", "mistral", "llama3", "deepseek-r1"]
    for model in preferred:
        for installed in models:
            if model.split(":")[0] in installed.lower():
                return installed

    return models[0] if models else "llama3:latest"

def _ollama_candidates(task="general"):
    """Lista de modelos candidatos (mejor primero) para una tarea."""
    models = _get_ollama_models()
    if not models:
        return ["deepseek-r1:latest"]
    primary = _best_ollama_model(task)
    ordered = [primary] + [m for m in models if m != primary]
    return ordered

def _extract_json_object(text):
    clean = (text or "").strip().replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(clean[start:end])
    raise ValueError("No se encontró JSON válido en la respuesta")

def _ollama_generate(prompt, task="general", expect_json=False, timeout=75):
    """
    Ejecuta Ollama con fallback entre modelos instalados.
    Devuelve (respuesta_texto|dict, modelo_usado, error)
    """
    cfg = get_cfg()
    ollama_url = os.environ.get("OLLAMA_URL", "") or cfg.get("ollama_url", "http://localhost:11434")
    errors = []
    for model in _ollama_candidates(task):
        try:
            body = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_ctx": 8192
                }
            }).encode()
            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.loads(r.read())
            text = res.get("response", "").strip()
            if not text:
                raise ValueError("Respuesta vacía de Ollama")
            if expect_json:
                return _extract_json_object(text), model, None
            return text, model, None
        except Exception as e:
            errors.append(f"{model}: {e}")
    return None, None, " | ".join(errors) if errors else "No hay modelos Ollama disponibles"

def ollama_generate(prompt: str, task: str = "general", system: str = None):
    """
    Genera texto con el mejor modelo disponible para la tarea.
    Fallback: Ollama -> Gemini.
    Retorna (texto, error, model).
    """
    system_default = (
        "Sos el asistente de DiazUX Studio (diazuxstudio.com.ar), una agencia argentina "
        "de diseño web y desarrollo. Escribís en español rioplatense, directo y profesional."
    )
    full_prompt = f"{system or system_default}\n\n{prompt}"
    text, model, err = _ollama_generate(full_prompt, task=task, expect_json=False)
    if text:
        return text, None, f"ollama/{model}"

    cfg = get_cfg()
    gemini_key = os.environ.get('GEMINI_API_KEY', '') or cfg.get('gemini_api_key', '')
    if gemini_key:
        body = json.dumps({
            'contents': [{'parts': [{'text': full_prompt}]}],
            'generationConfig': {'maxOutputTokens': 1200, 'temperature': 0.7}
        }).encode()
        try:
            req = urllib.request.Request(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}',
                data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=35) as r:
                res = json.loads(r.read())
            return res['candidates'][0]['content']['parts'][0]['text'], None, "gemini/gemini-1.5-flash"
        except Exception as e:
            return None, f"Ollama y Gemini fallaron: {err} | {e}", None
    return None, f"Ollama falló: {err}", None

def seed_tokens_from_env():
    """Al arrancar, carga tokens desde variables de entorno si no están en la DB."""
    seeds = [
        ('instagram', 'INSTAGRAM_TOKEN', 'INSTAGRAM_USER_ID', 'INSTAGRAM_USERNAME'),
        ('linkedin',  'LINKEDIN_TOKEN',  'LINKEDIN_USER_ID',  'LINKEDIN_USERNAME'),
    ]
    conn = get_db()
    for platform, tok_env, uid_env, uname_env in seeds:
        token = os.environ.get(tok_env, '')
        if not token:
            continue
        existing = conn.execute(
            'SELECT access_token FROM social_tokens WHERE platform=?', (platform,)
        ).fetchone()
        if existing:
            continue
        uid   = os.environ.get(uid_env, '')
        uname = os.environ.get(uname_env, '')
        conn.execute(
            'INSERT OR REPLACE INTO social_tokens VALUES (?,?,?,?,?,?)',
            (platform, token, '', '', uid, json.dumps({'username': uname}))
        )
        print(f'[startup] Token {platform} cargado desde env ({uname or uid})')
    conn.commit(); conn.close()

seed_tokens_from_env()

def log_activity(pid, type_, desc):
    conn = get_db()
    conn.execute('INSERT INTO activities VALUES (?,?,?,?,?)',
        (str(uuid.uuid4()), pid, type_, desc, datetime.now().strftime('%Y-%m-%d %H:%M')))
    conn.commit(); conn.close()

def score(p):
    s = 0
    if p['email']:   s += 20
    if p['url']:     s += 10
    if p['contact']: s += 10
    if p['problem']: s += 15
    s += {'Sin contactar':0,'Email 1 enviado':15,'Email 2 enviado':20,'Email 3 enviado':25,
          'Respondió':50,'Reunión agendada':75,'Cliente':100}.get(p['status'],0)
    return min(s, 100)

def get_cfg():
    conn = get_db()
    r = {row['key']: row['value'] for row in conn.execute('SELECT key,value FROM config').fetchall()}
    conn.close()
    env_map = {
        'meta_app_id':'META_APP_ID','meta_app_secret':'META_APP_SECRET',
        'linkedin_client_id':'LINKEDIN_CLIENT_ID','linkedin_client_secret':'LINKEDIN_CLIENT_SECRET',
        'ollama_url':'OLLAMA_URL','model':'OLLAMA_MODEL',
        'smtp_host':'SMTP_HOST','smtp_port':'SMTP_PORT','smtp_user':'SMTP_USER','smtp_pass':'SMTP_PASS',
    }
    for key,env in env_map.items():
        val=os.environ.get(env)
        if val: r[key]=val
    return r

# ── CONFIG ───────────────────────────────────────────────────────
@app.route('/api/config', methods=['GET','POST'])
def config():
    conn = get_db()
    if request.method == 'POST':
        for k,v in request.json.items():
            conn.execute('INSERT OR REPLACE INTO config VALUES (?,?)',(k,str(v)))
        conn.commit(); conn.close(); return jsonify({'ok':True})
    rows = conn.execute('SELECT key,value FROM config').fetchall()
    conn.close(); return jsonify({r['key']:r['value'] for r in rows})

# ── PROSPECTS ────────────────────────────────────────────────────
@app.route('/api/prospects', methods=['GET'])
def get_prospects():
    s = request.args.get('status')
    conn = get_db()
    rows = conn.execute('SELECT * FROM prospects WHERE status=? ORDER BY date DESC',(s,)).fetchall() \
           if s and s!='all' else conn.execute('SELECT * FROM prospects ORDER BY date DESC').fetchall()
    conn.close(); return jsonify([dict(r) for r in rows])

@app.route('/api/prospects', methods=['POST'])
def add_prospect():
    d = request.json; pid = str(uuid.uuid4())
    conn = get_db()
    conn.execute('INSERT INTO prospects (id,brand,contact,email,url,problem,source,status,score,date,notes,deal_value,deal_stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (pid,d.get('brand',''),d.get('contact',''),d.get('email',''),d.get('url',''),
         d.get('problem',''),d.get('source',''),'Sin contactar',0,datetime.now().strftime('%d/%m/%Y'),d.get('notes',''),d.get('deal_value',0),d.get('deal_stage','Lead')))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM prospects WHERE id=?',(pid,)).fetchone())
    conn.execute('UPDATE prospects SET score=? WHERE id=?',(score(row),pid))
    conn.commit(); conn.close()
    log_activity(pid,'created','Prospecto creado')
    return jsonify({'ok':True,'id':pid})

@app.route('/api/prospects/bulk', methods=['POST'])
def bulk_add():
    added=0
    for p in request.json.get('prospects',[]):
        pid=str(uuid.uuid4()); conn=get_db()
        conn.execute('INSERT INTO prospects (id,brand,contact,email,url,problem,source,status,score,date,notes,deal_value,deal_stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (pid,p.get('brand',''),p.get('contact',''),p.get('email',''),p.get('url',''),p.get('problem',''),p.get('source','Scraper'),'Sin contactar',0,datetime.now().strftime('%d/%m/%Y'),'',0,'Lead'))
        conn.commit(); conn.close(); added+=1
    return jsonify({'ok':True,'added':added})

@app.route('/api/prospects/<pid>', methods=['PUT'])
def update_prospect(pid):
    d=request.json; conn=get_db()
    fields=['brand','contact','email','url','problem','source','status','notes','deal_value','deal_stage']
    updates={k:d[k] for k in fields if k in d}
    if updates:
        conn.execute(f'UPDATE prospects SET {", ".join(k+"=?" for k in updates)} WHERE id=?',list(updates.values())+[pid])
        if 'status' in updates:
            conn.execute('UPDATE prospects SET last_contact=? WHERE id=?',(datetime.now().strftime('%d/%m/%Y %H:%M'),pid))
            log_activity(pid,'status',f'Estado → {updates["status"]}')
        row=dict(conn.execute('SELECT * FROM prospects WHERE id=?',(pid,)).fetchone())
        conn.execute('UPDATE prospects SET score=? WHERE id=?',(score(row),pid))
    conn.commit(); conn.close(); return jsonify({'ok':True})

@app.route('/api/prospects/<pid>', methods=['DELETE'])
def delete_prospect(pid):
    conn=get_db()
    for t in ['prospects','emails_sent','activities']:
        conn.execute(f'DELETE FROM {t} WHERE {"id" if t=="prospects" else "prospect_id"}=?',(pid,))
    conn.commit(); conn.close(); return jsonify({'ok':True})

@app.route('/api/prospects/<pid>/optout', methods=['POST'])
def optout_prospect(pid):
    d = request.json or {}
    reason = d.get('reason', 'No desea más contacto')
    conn = get_db()
    conn.execute('INSERT INTO opt_out VALUES (?,?,?,?)',
                 (str(uuid.uuid4()), pid, reason, datetime.now().strftime('%Y-%m-%d %H:%M')))
    conn.commit(); conn.close()
    log_activity(pid, 'autopilot', f'Opt-out registrado: {reason}')
    return jsonify({'ok': True})

# ── EMAIL ────────────────────────────────────────────────────────
@app.route('/api/email/send', methods=['POST'])
def send_email():
    d=request.json; cfg=get_cfg()
    if not all([cfg.get('smtp_host'),cfg.get('smtp_user'),cfg.get('smtp_pass')]):
        return jsonify({'ok':False,'error':'Configurá SMTP en Ajustes'})
    track_id=str(uuid.uuid4())
    pixel=f'<img src="{BASE_URL}/track/open/{track_id}" width="1" height="1" style="display:none"/>'
    html_body=d.get('body','').replace('\n','<br>')+pixel
    try:
        msg=MIMEMultipart('alternative')
        msg['Subject']=d.get('subject',''); msg['From']=f'{cfg.get("name","DiazUX")} <{cfg["smtp_user"]}>'; msg['To']=d.get('to','')
        msg.attach(MIMEText(d.get('body',''),'plain')); msg.attach(MIMEText(html_body,'html'))
        ctx=ssl.create_default_context()
        with smtplib.SMTP(cfg['smtp_host'],int(cfg.get('smtp_port',587))) as s:
            s.starttls(context=ctx); s.login(cfg['smtp_user'],cfg['smtp_pass']); s.sendmail(cfg['smtp_user'],d.get('to',''),msg.as_string())
        conn=get_db()
        conn.execute('INSERT INTO emails_sent VALUES (?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()),d.get('prospect_id',''),d.get('subject',''),d.get('body',''),datetime.now().strftime('%Y-%m-%d %H:%M'),0,None,0,d.get('sequence_num',1),track_id))
        pid=d.get('prospect_id',''); seq=d.get('sequence_num',1)
        sm={1:'Email 1 enviado',2:'Email 2 enviado',3:'Email 3 enviado'}
        if pid and seq in sm:
            conn.execute('UPDATE prospects SET status=?,last_contact=? WHERE id=?',(sm[seq],datetime.now().strftime('%d/%m/%Y %H:%M'),pid))
            row=dict(conn.execute('SELECT * FROM prospects WHERE id=?',(pid,)).fetchone())
            conn.execute('UPDATE prospects SET score=? WHERE id=?',(score(row),pid))
        conn.commit(); conn.close()
        log_activity(pid,'email',f'Email #{seq} enviado a {d.get("to","")}')
        return jsonify({'ok':True,'track_id':track_id})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)})

def _send_email_internal(to_email, subject, body, prospect_id='', sequence_num=1):
    conn = get_db()
    sent_today = conn.execute(
        "SELECT COUNT(*) FROM emails_sent WHERE substr(sent_at,1,10)=?",
        (datetime.now().strftime('%Y-%m-%d'),)
    ).fetchone()[0]
    conn.close()
    if sent_today >= DAILY_LIMITS["emails"]:
        return False, 'Límite diario de emails alcanzado'
    cfg = get_cfg()
    if not all([cfg.get('smtp_host'), cfg.get('smtp_user'), cfg.get('smtp_pass')]):
        return False, 'Configurá SMTP en Ajustes'
    track_id = str(uuid.uuid4())
    pixel = f'<img src="{BASE_URL}/track/open/{track_id}" width="1" height="1" style="display:none"/>'
    html_body = (body or '').replace('\n', '<br>') + pixel
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f'{cfg.get("name","DiazUX")} <{cfg["smtp_user"]}>'
        msg['To'] = to_email
        msg.attach(MIMEText(body or '', 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(cfg['smtp_host'], int(cfg.get('smtp_port', 587))) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(cfg['smtp_user'], cfg['smtp_pass'])
            s.sendmail(cfg['smtp_user'], to_email, msg.as_string())
        conn = get_db()
        conn.execute('INSERT INTO emails_sent VALUES (?,?,?,?,?,?,?,?,?,?)',
            (str(uuid.uuid4()), prospect_id, subject, body, datetime.now().strftime('%Y-%m-%d %H:%M'),
             0, None, 0, sequence_num, track_id))
        conn.commit(); conn.close()
        return True, track_id
    except Exception as e:
        return False, str(e)

def schedule_email(prospect_id, email_num, delay_days, subject, body, model_used=''):
    when = (datetime.now() + timedelta(days=delay_days)).strftime('%Y-%m-%d %H:%M')
    conn = get_db()
    conn.execute('INSERT INTO email_sequences VALUES (?,?,?,?,?,?,?,?,?)',
        (str(uuid.uuid4()), prospect_id, email_num, subject, body, when, 0, None, model_used))
    conn.commit(); conn.close()

@app.route('/track/open/<track_id>')
def track_open(track_id):
    conn=get_db()
    row=conn.execute('SELECT * FROM emails_sent WHERE track_id=?',(track_id,)).fetchone()
    if row and not row['opened']:
        conn.execute('UPDATE emails_sent SET opened=1,opened_at=? WHERE track_id=?',(datetime.now().strftime('%Y-%m-%d %H:%M'),track_id))
        conn.commit()
        if row['prospect_id']: log_activity(row['prospect_id'],'open',f'Email #{row["sequence_num"]} abierto')
    conn.close()
    gif=b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
    return Response(gif, mimetype='image/gif')

@app.route('/api/emails/<pid>')
def get_emails(pid):
    conn=get_db()
    rows=conn.execute('SELECT * FROM emails_sent WHERE prospect_id=? ORDER BY sent_at DESC',(pid,)).fetchall()
    conn.close(); return jsonify([dict(r) for r in rows])

# ── STATS ────────────────────────────────────────────────────────
@app.route('/api/stats')
def get_stats():
    conn=get_db()
    total=conn.execute('SELECT COUNT(*) FROM prospects').fetchone()[0]
    new=conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Sin contactar'").fetchone()[0]
    in_seq=conn.execute("SELECT COUNT(*) FROM prospects WHERE status IN ('Email 1 enviado','Email 2 enviado','Email 3 enviado')").fetchone()[0]
    replied=conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Respondió'").fetchone()[0]
    meetings=conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Reunión agendada'").fetchone()[0]
    clients=conn.execute("SELECT COUNT(*) FROM prospects WHERE status='Cliente'").fetchone()[0]
    et=conn.execute('SELECT COUNT(*) FROM emails_sent').fetchone()[0]
    op=conn.execute('SELECT COUNT(*) FROM emails_sent WHERE opened=1').fetchone()[0]
    pl=conn.execute('SELECT COALESCE(SUM(deal_value),0) FROM prospects').fetchone()[0]
    stages=conn.execute('SELECT status,COUNT(*) as cnt FROM prospects GROUP BY status').fetchall()
    top=conn.execute('SELECT id,brand,email,score,status FROM prospects ORDER BY score DESC LIMIT 5').fetchall()
    recent=conn.execute('SELECT * FROM activities ORDER BY date DESC LIMIT 10').fetchall()
    pp=conn.execute('SELECT COUNT(*) FROM social_posts').fetchone()[0]
    pub=conn.execute("SELECT COUNT(*) FROM social_posts WHERE status='published'").fetchone()[0]
    sch=conn.execute("SELECT COUNT(*) FROM social_posts WHERE status='scheduled'").fetchone()[0]
    conn.close()
    return jsonify({'total':total,'new':new,'in_seq':in_seq,'replied':replied,'meetings':meetings,'clients':clients,
        'rate':round(replied/total*100,1) if total else 0,'emails_sent':et,'opens':op,
        'open_rate':round(op/et*100,1) if et else 0,'pipeline_value':pl,
        'stages':[dict(r) for r in stages],'top_prospects':[dict(r) for r in top],
        'recent_activity':[dict(r) for r in recent],'posts_total':pp,'posts_published':pub,'posts_scheduled':sch})

@app.route('/api/ai/generate', methods=['POST'])
def ai_generate():
    """Genera texto con Claude (fallback a Gemini, luego a Ollama local)."""
    d = request.json
    prompt = d.get('prompt', '')
    if not prompt:
        return jsonify({'ok': False, 'error': 'Prompt vacío'})

    # 1) Intentar Claude API
    cfg = get_cfg()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "") or cfg.get("anthropic_api_key", "")
    if anthropic_key:
        text, err = _claude(prompt)
        if text:
            return jsonify({'ok': True, 'response': text, 'model': 'claude'})
        print(f"  Claude falló, intentando Gemini: {err}")

    # 2) Fallback Gemini
    gemini_key = os.environ.get('GEMINI_API_KEY', '') or cfg.get('gemini_api_key', '')
    if gemini_key:
        body = json.dumps({
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'maxOutputTokens': 1024, 'temperature': 0.7}
        }).encode()
        try:
            req = urllib.request.Request(
                f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}',
                data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
            text = res['candidates'][0]['content']['parts'][0]['text']
            return jsonify({'ok': True, 'response': text, 'model': 'gemini'})
        except Exception as e:
            print(f"  Gemini falló, usando Ollama: {e}")

    # 3) Fallback Ollama local (con retries entre modelos)
    text, model, err = _ollama_generate(prompt, task=d.get('task', 'general'), expect_json=False)
    if text:
        return jsonify({'ok': True, 'response': text, 'model': f'ollama/{model}'})
    return jsonify({'ok': False, 'error': f'Sin APIs disponibles: {err}'})

def validate_before_send(prospect: dict):
    email = (prospect.get('email') or '').strip()
    if not email:
        return False, 'Prospecto sin email'
    if '@' not in email or '.' not in email.split('@')[-1]:
        return False, 'Email inválido'
    domain = email.split('@')[-1].strip().lower()
    try:
        socket.getaddrinfo(domain, 25)
    except Exception:
        return False, 'Dominio de email sin resolución DNS/MX'
    conn = get_db()
    opt = conn.execute("SELECT id FROM opt_out WHERE prospect_id=? LIMIT 1", (prospect.get('id', ''),)).fetchone()
    if opt:
        conn.close()
        return False, 'Prospecto en lista opt-out'
    recent = conn.execute(
        "SELECT sent_at FROM emails_sent WHERE prospect_id=? ORDER BY sent_at DESC LIMIT 1",
        (prospect.get('id', ''),)
    ).fetchone()
    conn.close()
    if recent:
        try:
            dt = datetime.strptime(recent['sent_at'], '%Y-%m-%d %H:%M')
            if datetime.now() - dt < timedelta(hours=72):
                return False, 'Contactado hace menos de 72hs'
        except Exception:
            pass
    if not _get_ollama_models():
        cfg = get_cfg()
        if not (os.environ.get('GEMINI_API_KEY', '') or cfg.get('gemini_api_key', '')):
            return False, 'Sin modelos Ollama ni fallback Gemini disponible'
    return True, 'ok'

def _generate_email_pair(prompt, task='email_writing'):
    text, err, model = ollama_generate(prompt, task=task)
    if err:
        return None, None, model or 'none', err
    subject = 'Oportunidad para mejorar tu presencia digital'
    body = text or ''
    if 'ASUNTO:' in body.upper():
        lines = body.splitlines()
        for ln in lines:
            if ln.upper().startswith('ASUNTO:'):
                subject = ln.split(':', 1)[1].strip() or subject
                break
        body = '\n'.join([ln for ln in lines if not ln.upper().startswith('ASUNTO:')]).replace('CUERPO:', '').strip()
    return subject, body, model or 'unknown', None

def trigger_post_audit_sequence(prospect: dict, audit_data: dict):
    ok, reason = validate_before_send(prospect)
    if not ok:
        return {"ok": False, "error": reason}

    brand = prospect.get('brand', '')
    url = prospect.get('url', '')
    contact = prospect.get('contact', 'equipo')
    problems = ', '.join(audit_data.get('problems_detected', [])[:4])
    perf = audit_data.get('performance_score', 0)
    mobile_status = 'sí' if audit_data.get('mobile_friendly') else 'no'
    tech = ', '.join(audit_data.get('tech_stack', []))

    p1 = f'Auditaste {brand} ({url}). Problemas: {problems}. Score {perf}/100. Mobile-friendly: {mobile_status}. Stack: {tech}. Escribí email corto y persuasivo para {contact}. Formato ASUNTO y CUERPO.'
    p2 = f'Segundo email de seguimiento para {brand}. Oportunidad: {audit_data.get("opportunity_summary","")}. Servicio recomendado: {audit_data.get("recommended_service","redesign")}. Incluí valor y CTA.'
    p3 = f'Tercer y último email para {brand}. Cierre honesto con urgencia real y CTA a WhatsApp/Calendly.'

    s1, b1, m1, e1 = _generate_email_pair(p1, task='email_writing')
    if e1:
        return {"ok": False, "error": e1}
    sent, err = _send_email_internal(prospect.get('email', ''), s1, b1, prospect.get('id', ''), 1)
    if not sent:
        return {"ok": False, "error": err}

    s2, b2, m2, _ = _generate_email_pair(p2, task='email_writing')
    s3, b3, m3, _ = _generate_email_pair(p3, task='email_writing')
    schedule_email(prospect.get('id', ''), 2, 3, s2 or 'Seguimiento', b2 or '', m2)
    schedule_email(prospect.get('id', ''), 3, 7, s3 or 'Cierre', b3 or '', m3)
    log_activity(prospect.get('id', ''), 'autopilot', json.dumps({
        "action": "email_sequence_generated",
        "model_used": [m1, m2, m3],
        "send_status": "ok"
    }, ensure_ascii=False))
    return {"ok": True}

def _send_instagram_dm_internal(recipient_username, message_text):
    conn = get_db()
    tok = conn.execute("SELECT * FROM social_tokens WHERE platform='instagram'").fetchone()
    conn.close()
    if not tok:
        return False, 'Instagram no conectado'
    token = tok['access_token']; ig_id = tok['user_id']
    search, err = _ig_api(
        f'{ig_id}?fields=business_discovery.fields(id,username)'
        f'&username_to_lookup={recipient_username.lstrip("@")}', token
    )
    if err:
        return False, f'No se pudo buscar @{recipient_username}: {err}'
    recipient_ig_id = (search.get('business_discovery') or {}).get('id', '')
    if not recipient_ig_id:
        return False, f'Usuario @{recipient_username} no encontrado/profesional'
    body = {'recipient': {'id': recipient_ig_id}, 'message': {'text': message_text}}
    result, err = _ig_api(f'{ig_id}/messages', token, method='POST', body=body)
    if err:
        return False, err
    return True, result.get('message_id', result.get('id', ''))

def trigger_instagram_dm(prospect: dict, audit_data: dict):
    handle = (prospect.get('instagram_handle') or '').strip()
    if not handle:
        return {"ok": False, "error": "sin instagram_handle"}
    prompt = (
        f"Escribí un DM corto para Instagram para {handle}. "
        f"Marca: {prospect.get('brand','')}. Industria: {prospect.get('industry','')}. "
        f"Problema principal: {(audit_data.get('problems_detected') or [''])[0]}. "
        f"Oportunidad: {audit_data.get('opportunity_summary','')}. "
        "Máx 4 oraciones, tono humano, español rioplatense, cerrar con pregunta."
    )
    text, err, model = ollama_generate(prompt, task='dm_writing')
    if err:
        return {"ok": False, "error": err}
    ok, result = _send_instagram_dm_internal(handle, text[:900])
    if ok:
        pid = prospect.get('id', '')
        conn = get_db()
        conn.execute('UPDATE prospects SET dm_instagram_sent=1 WHERE id=?', (pid,))
        conn.commit(); conn.close()
        log_activity(pid, 'autopilot', json.dumps({
            "action": "dm_instagram_sent",
            "model_used": model,
            "send_status": "ok"
        }, ensure_ascii=False))
        return {"ok": True}
    return {"ok": False, "error": result}

def trigger_linkedin_dm(prospect: dict, audit_data: dict):
    """
    Best-effort: LinkedIn mensajes directos requieren permisos/URNs específicos.
    Si existe recipient_urn en config o en linkedin_url como urn, intenta envío.
    """
    linkedin_url = (prospect.get('linkedin_url') or '').strip()
    if not linkedin_url:
        return {"ok": False, "error": "sin linkedin_url"}
    cfg = get_cfg()
    recipient_urn = cfg.get('linkedin_recipient_urn', '')
    if linkedin_url.startswith('urn:li:'):
        recipient_urn = linkedin_url
    if not recipient_urn:
        return {"ok": False, "error": "LinkedIn recipient URN no configurado"}
    prompt = (
        f"Escribí un mensaje de LinkedIn para {prospect.get('brand','')} "
        f"con problema principal {(audit_data.get('problems_detected') or [''])[0]}. "
        "Máximo 5 oraciones, tono profesional humano, CTA suave."
    )
    text, err, model = ollama_generate(prompt, task='dm_writing')
    if err:
        return {"ok": False, "error": err}
    conn = get_db()
    tok = conn.execute("SELECT * FROM social_tokens WHERE platform='linkedin'").fetchone()
    conn.close()
    if not tok:
        return {"ok": False, "error": "LinkedIn no conectado"}
    token = tok['access_token']
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    payload = {
        "recipients": [recipient_urn],
        "subject": "Sugerencia rápida de mejora digital",
        "body": text[:1200]
    }
    try:
        req = urllib.request.Request(
            'https://api.linkedin.com/v2/messages',
            data=json.dumps(payload).encode(),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20):
            pass
        pid = prospect.get('id', '')
        conn = get_db()
        conn.execute('UPDATE prospects SET dm_linkedin_sent=1 WHERE id=?', (pid,))
        conn.commit(); conn.close()
        log_activity(pid, 'autopilot', json.dumps({
            "action": "dm_linkedin_sent",
            "model_used": model,
            "send_status": "ok"
        }, ensure_ascii=False))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/personalize — email + DM personalizados por prospecto
# ═══════════════════════════════════════════════════════════════════
@app.route('/api/ai/personalize', methods=['POST'])
def ai_personalize():
    """
    Genera email de outreach + DM de Instagram personalizados para un prospecto.
    Body: { prospect_id, sender_name, sender_company, sender_web, case_study, tone? }
    """
    d = request.json
    pid = d.get("prospect_id", "")

    if not pid:
        return jsonify({"ok": False, "error": "prospect_id requerido"})

    conn = get_db()
    p = conn.execute("SELECT * FROM prospects WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not p:
        return jsonify({"ok": False, "error": "Prospecto no encontrado"})

    p = dict(p)
    cfg_data = get_cfg()
    sender  = d.get("sender_name",    "") or cfg_data.get("name", "David Díaz")
    company = d.get("sender_company", "") or cfg_data.get("company", "DiazUX Studio")
    web     = d.get("sender_web",     "") or cfg_data.get("web", "diazux.tech")
    case    = d.get("case_study",     "") or cfg_data.get("case_study", "")
    tone    = d.get("tone", "profesional pero cercano")

    prospect_ctx = f"""
Marca/empresa: {p.get("brand", "")}
URL del sitio: {p.get("url", "sin sitio web")}
Problema detectado: {p.get("problem", "")}
Nombre del contacto: {p.get("contact", "el/la dueño/a")}
Seguidores Instagram: {p.get("notes", "")[:100]}
Fuente: {p.get("source", "")}
""".strip()

    prompt = f"""
Sos {sender} de {company} ({web}), experto en diseño web, UX y conversión para marcas argentinas.

DATOS DEL PROSPECTO:
{prospect_ctx}

{"CASO DE ÉXITO PARA MENCIONAR: " + case if case else ""}

TONO: {tone}

Generá EXACTAMENTE este JSON (sin explicaciones, sin markdown, sin texto extra):
{{
  "email_subject": "Asunto del email (corto, directo, sin clichés como 'Espero que estés bien')",
  "email_body": "Cuerpo del email en texto plano. Máx 180 palabras. Párrafos separados con \\n\\n. Sin saludos genéricos. Empezá con un gancho basado en el problema específico. Mencioná el caso de éxito brevemente. CTA claro y específico al final.",
  "dm_ig": "Mensaje directo para Instagram. Máx 60 palabras. Informal, como hablaría un humano. Sin emojis de empresa. CTA con link o pedido de respuesta.",
  "dm_subject": "Primera línea del DM que llame la atención (máx 12 palabras)",
  "hook_audit": "Frase para el gancho del email basada en el problema detectado (máx 15 palabras)"
}}
"""

    text, err = _claude(prompt, max_tokens=700)
    if err:
        # Fallback a Gemini/Ollama vía _claude ya maneja fallback interno
        return jsonify({"ok": False, "error": err})

    # Parsear JSON de Claude
    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        parsed = json.loads(clean[start:end])
        return jsonify({"ok": True, **parsed})
    except Exception as e:
        return jsonify({"ok": True, "email_body": text, "raw": True})


# ═══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/content — captions para Stories, Reels y posts
# ═══════════════════════════════════════════════════════════════════
@app.route('/api/ai/content', methods=['POST'])
def ai_content():
    """
    Genera contenido optimizado para Stories, Reels y posts de Instagram/LinkedIn.
    Body: {
      type: "story" | "reel" | "post" | "carousel",
      topic: "descripción del contenido o de la marca",
      niche: "moda" | "belleza" | "gastronomía" | "tech" | ...,
      objective: "vender" | "crecer" | "educar" | "fidelizar",
      tone: "premium" | "cercano" | "disruptivo" | "educativo",
      platform: "instagram" | "linkedin" | "ambas",
      include_hashtags: true/false
    }
    """
    d = request.json
    content_type = d.get("type", "post")
    topic        = d.get("topic", "")
    niche        = d.get("niche", "marca")
    objective    = d.get("objective", "crecer")
    tone_        = d.get("tone", "cercano")
    platform     = d.get("platform", "instagram")
    inc_tags     = d.get("include_hashtags", True)

    if not topic:
        return jsonify({"ok": False, "error": "Necesito saber de qué trata el contenido"})

    type_guide = {
        "story": "Historia de Instagram (texto corto, máx 3 slides, muy visual, CTA en el último slide)",
        "reel":  "Reel de Instagram (gancho en los primeros 2 seg, guión de 30-60 seg, transiciones, CTA al final)",
        "post":  "Post estático/carrusel para feed (caption persuasivo, estructura AIDA, CTA)",
        "carousel": "Carrusel de 5-8 slides (slide 1: gancho, slides 2-6: contenido de valor, slide final: CTA)",
    }.get(content_type, "Publicación en redes sociales")

    prompt = f"""
Sos un experto en contenido digital para marcas argentinas con tono {tone_}.

TIPO DE CONTENIDO: {type_guide}
TEMA/CONTEXTO: {topic}
NICHO: {niche}
OBJETIVO: {objective}
PLATAFORMA: {platform}

Generá EXACTAMENTE este JSON (sin markdown, sin texto extra afuera del JSON):
{{
  "hook": "Gancho principal — primera oración/texto que aparece (máx 10 palabras, que genere curiosidad o impacto)",
  "caption": "Caption completo listo para copiar. Párrafos separados con \\n\\n. Emojis moderados y estratégicos. Incluí el CTA al final.",
  "slides": ["Texto slide 1", "Texto slide 2", "..."],
  "cta": "Call to action específico y directo",
  "best_time": "Mejor horario para publicar este tipo de contenido (hora Argentina)",
  "hashtags": {{"instagram": ["tag1", "tag2", "...20 tags mix de nicho y masivos"], "linkedin": ["tag1", "tag2", "...5 tags profesionales"]}},
  "tips": ["Tip visual 1", "Tip visual 2", "Tip de producción"],
  "reel_script": "Guión completo del reel con timestamps si aplica (solo si type es reel)"
}}

IMPORTANTE:
- Para stories: slides debe tener 3 elementos
- Para carruseles: slides debe tener 7-8 elementos
- Para posts y reels: slides puede ser array vacío []
- Hashtags solo si include_hashtags = {inc_tags}
"""

    text, err = _claude(prompt, max_tokens=1200)
    if err:
        return jsonify({"ok": False, "error": err})

    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        parsed = json.loads(clean[start:end])
        parsed["type"]     = content_type
        parsed["platform"] = platform
        return jsonify({"ok": True, **parsed})
    except Exception as e:
        return jsonify({"ok": True, "caption": text, "raw": True})


# ═══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/dm-campaign — genera secuencia de DMs para prospectos
# ═══════════════════════════════════════════════════════════════════
@app.route('/api/ai/dm-campaign', methods=['POST'])
def ai_dm_campaign():
    """
    Genera 3 mensajes de DM para una campaña de prospección.
    Body: { brand, problem, niche, sender_name, sender_company }
    """
    d = request.json
    prompt = f"""
Sos {d.get("sender_name","David")} de {d.get("sender_company","DiazUX Studio")}, experto en diseño web y conversión.

PROSPECTO:
- Marca: {d.get("brand","")}
- Problema: {d.get("problem","")}
- Nicho: {d.get("niche","")}

Generá una secuencia de 3 DMs de Instagram para esta marca. Cada uno en días distintos.
Respondé SOLO JSON sin markdown:
{{
  "dm1": {{
    "day": 0,
    "message": "Primer DM. Máx 50 palabras. Presentación + gancho basado en el problema. Sin sonar vendedor.",
    "subject_line": "Primera oración gancho"
  }},
  "dm2": {{
    "day": 3,
    "message": "Seguimiento. Máx 60 palabras. Aportá valor (insight o dato relevante al nicho). CTA suave.",
    "subject_line": "Primera oración"
  }},
  "dm3": {{
    "day": 7,
    "message": "Último intento. Máx 45 palabras. Directo, sin rodeos. Cierre o apertura de conversación.",
    "subject_line": "Primera oración"
  }}
}}
"""
    text, err = _claude(prompt, max_tokens=600)
    if err:
        return jsonify({"ok": False, "error": err})
    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        start = clean.find("{"); end = clean.rfind("}") + 1
        parsed = json.loads(clean[start:end])
        return jsonify({"ok": True, **parsed})
    except:
        return jsonify({"ok": True, "raw": text})


# ═══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/ollama-models — devuelve modelos instalados
# ═══════════════════════════════════════════════════════════════════
@app.route('/api/ai/ollama-models', methods=['GET'])
def get_ollama_models():
    """Devuelve lista de modelos Ollama instalados y el mejor recomendado."""
    models = _get_ollama_models()
    best = _best_ollama_model("creative")
    return jsonify({"ok": True, "models": models, "recommended": best})

@app.route('/api/activities/<pid>')
def get_activities(pid):
    conn=get_db()
    rows=conn.execute('SELECT * FROM activities WHERE prospect_id=? ORDER BY date DESC',(pid,)).fetchall()
    conn.close(); return jsonify([dict(r) for r in rows])

@app.route('/api/smtp/test', methods=['POST'])
def test_smtp():
    d=request.json
    try:
        with smtplib.SMTP(d['host'],int(d['port'])) as s:
            s.starttls(context=ssl.create_default_context()); s.login(d['user'],d['password'])
        return jsonify({'ok':True})
    except Exception as e: return jsonify({'ok':False,'error':str(e)})

@app.route('/api/export/csv')
def export_csv():
    conn=get_db()
    rows=conn.execute('SELECT * FROM prospects ORDER BY date DESC').fetchall(); conn.close()
    lines=['Marca,Contacto,Email,URL,Estado,Score,Fecha,Problema,Fuente,Deal Value,Deal Stage']
    for r in rows:
        vals=[str(r[f] or '') for f in ['brand','contact','email','url','status','score','date','problem','source','deal_value','deal_stage']]
        lines.append(','.join(f'"{v.replace(chr(34),chr(39))}"' for v in vals))
    return Response('\n'.join(lines),mimetype='text/csv',headers={'Content-Disposition':'attachment;filename=prospectos_diazux.csv'})

def _upsert_prospect_from_engine(p):
    conn = get_db()
    row = conn.execute('SELECT id FROM prospects WHERE brand=? LIMIT 1', (p.get('brand', ''),)).fetchone()
    if row:
        pid = row['id']
        conn.execute('UPDATE prospects SET url=?, source=?, industry=?, location=?, instagram_handle=?, linkedin_url=?, phone=? WHERE id=?',
                     (p.get('url',''), p.get('source',''), p.get('industry',''), p.get('location',''),
                      p.get('instagram_handle',''), p.get('linkedin_url',''), p.get('phone',''), pid))
    else:
        pid = str(uuid.uuid4())
        conn.execute('INSERT INTO prospects (id,brand,contact,email,url,problem,source,status,score,date,notes,deal_value,deal_stage,phone,instagram_handle,linkedin_url,industry,location) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (pid,p.get('brand',''),p.get('contact',''),p.get('email',''),p.get('url',''),
             '',p.get('source','Autopilot'),'Sin contactar',0,datetime.now().strftime('%d/%m/%Y'),'',
             0,'Lead',p.get('phone',''),p.get('instagram_handle',''),p.get('linkedin_url',''),
             p.get('industry',''),p.get('location','')))
    conn.commit(); conn.close()
    return pid

@app.route('/api/autopilot/run', methods=['POST'])
def autopilot_run():
    d = request.json or {}
    query = d.get('search_query', 'negocios sin web argentina')
    industry = d.get('industry', '')
    location = d.get('location', '')
    max_prospects = int(d.get('max_prospects', 20))
    auto_send_emails = bool(d.get('auto_send_emails', True))
    auto_send_dms = bool(d.get('auto_send_dms', True))
    auto_publish_posts = bool(d.get('auto_publish_posts', False))
    post_platforms = d.get('post_platforms', ['instagram', 'linkedin'])
    errors, audited, qualified, emails_sent, dms_sent, posts_published = [], 0, 0, 0, 0, 0

    job_id = str(uuid.uuid4())
    started = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = get_db()
    conn.execute('INSERT INTO scraping_jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                 (job_id, query, 'multi', industry, location, 0, 0, started, None, 'running'))
    conn.commit(); conn.close()

    engine = ProspectorEngine()
    prospects = engine.find_prospects(query=query, industry=industry, location=location, max_prospects=max_prospects)

    for p in prospects:
        pid = _upsert_prospect_from_engine(p)
        audit = audit_website(p.get('url', ''))
        score_val = engine.score_prospect(p, audit)
        if score_val >= 40:
            qualified += 1
        audited += 1
        try:
            conn = get_db()
            conn.execute('UPDATE prospects SET score=?, problem=?, audit_data=?, audit_date=?, performance_score=?, recommended_service=? WHERE id=?',
                         (score_val, '; '.join(audit.get('problems_detected', [])), json.dumps(audit, ensure_ascii=False),
                          datetime.now().strftime('%Y-%m-%d %H:%M'), int(audit.get('performance_score', 0)),
                          audit.get('recommended_service', ''), pid))
            conn.execute('INSERT INTO audit_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (str(uuid.uuid4()), pid, p.get('url',''), int(audit.get('performance_score', 0)),
                          int(bool(audit.get('mobile_friendly'))), int(bool(audit.get('has_ssl'))),
                          int(audit.get('load_time_ms', 0)), int(audit.get('seo_score', 0)),
                          json.dumps(audit.get('tech_stack', []), ensure_ascii=False),
                          json.dumps(audit.get('problems_detected', []), ensure_ascii=False),
                          audit.get('opportunity_summary', ''), audit.get('recommended_service', ''),
                          datetime.now().strftime('%Y-%m-%d %H:%M'), _best_ollama_model('audit_analysis')))
            conn.commit(); conn.close()
        except Exception as e:
            errors.append(f'audit_store:{pid}:{e}')

        if auto_send_emails and p.get('email'):
            conn = get_db()
            row = conn.execute('SELECT * FROM prospects WHERE id=?', (pid,)).fetchone()
            conn.close()
            prospect = dict(row) if row else {}
            res = trigger_post_audit_sequence(prospect, audit)
            if res.get('ok'):
                emails_sent += 1
            else:
                errors.append(f"email:{pid}:{res.get('error')}")
        if auto_send_dms:
            conn = get_db()
            row = conn.execute('SELECT * FROM prospects WHERE id=?', (pid,)).fetchone()
            conn.close()
            prospect = dict(row) if row else {}
            if prospect.get('instagram_handle'):
                dm_ig = trigger_instagram_dm(prospect, audit)
                if dm_ig.get('ok'):
                    dms_sent += 1
                else:
                    errors.append(f"dm_ig:{pid}:{dm_ig.get('error')}")
            if prospect.get('linkedin_url'):
                dm_li = trigger_linkedin_dm(prospect, audit)
                if dm_li.get('ok'):
                    dms_sent += 1
                else:
                    errors.append(f"dm_li:{pid}:{dm_li.get('error')}")
        time.sleep(min(1, max(0, DELAYS["between_scraping"] / 5)))

    if auto_publish_posts and prospects:
        topic = f"Tips de mejora web para {industry or 'negocios'} en {location or 'Argentina'}"
        payload = {'topic': topic, 'platforms': post_platforms, 'autopublish': True}
        with app.test_request_context('/api/social/autopromote', method='POST', json=payload):
            response = social_autopromote()
            try:
                data = response.get_json()
                for _, st in (data.get('results') or {}).items():
                    if st.get('ok'):
                        posts_published += 1
            except Exception:
                errors.append('publish:auto parse error')

    conn = get_db()
    conn.execute('UPDATE scraping_jobs SET results_count=?, qualified_count=?, completed_at=?, status=? WHERE id=?',
                 (len(prospects), qualified, datetime.now().strftime('%Y-%m-%d %H:%M'), 'done', job_id))
    conn.commit(); conn.close()
    return jsonify({
        'ok': True,
        'prospects_found': len(prospects),
        'qualified': qualified,
        'audited': audited,
        'emails_sent': emails_sent,
        'dms_sent': dms_sent,
        'posts_published': posts_published,
        'errors': errors
    })

@app.route('/api/autopilot/jobs', methods=['GET'])
def autopilot_jobs():
    limit = int(request.args.get('limit', 20))
    conn = get_db()
    rows = conn.execute('SELECT * FROM scraping_jobs ORDER BY started_at DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return jsonify({'ok': True, 'jobs': [dict(r) for r in rows]})

@app.route('/api/prospects/<pid>/audit-report', methods=['GET'])
def prospect_audit_report(pid):
    conn = get_db()
    p = conn.execute('SELECT brand,url,audit_data,performance_score,recommended_service,problem FROM prospects WHERE id=?', (pid,)).fetchone()
    conn.close()
    if not p:
        return jsonify({'ok': False, 'error': 'Prospecto no encontrado'}), 404
    data = {}
    if p['audit_data']:
        try:
            data = json.loads(p['audit_data'])
        except Exception:
            data = {}
    return jsonify({
        'ok': True,
        'prospect': {
            'id': pid,
            'brand': p['brand'],
            'url': p['url'],
            'performance_score': p['performance_score'],
            'recommended_service': p['recommended_service'],
            'problem': p['problem']
        },
        'audit': data
    })

# ══════════════════════════════════════════════════════════════════
# REDES SOCIALES
# ══════════════════════════════════════════════════════════════════

@app.route('/api/social/tokens', methods=['GET'])
def get_tokens():
    conn=get_db()
    rows=conn.execute('SELECT platform,user_id,expires_at,extra FROM social_tokens').fetchall(); conn.close()
    return jsonify({r['platform']:{'connected':True,'user_id':r['user_id'],'expires_at':r['expires_at'],'extra':r['extra']} for r in rows})

@app.route('/api/social/tokens', methods=['POST'])
def save_token():
    d=request.json; conn=get_db()
    conn.execute('INSERT OR REPLACE INTO social_tokens VALUES (?,?,?,?,?,?)',
        (d['platform'],d['access_token'],d.get('refresh_token',''),d.get('expires_at',''),d.get('user_id',''),json.dumps(d.get('extra',{}))))
    conn.commit(); conn.close(); return jsonify({'ok':True})

@app.route('/api/social/tokens/<platform>', methods=['DELETE'])
def delete_token(platform):
    conn=get_db(); conn.execute('DELETE FROM social_tokens WHERE platform=?',(platform,)); conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/social/posts', methods=['GET'])
def get_posts():
    status=request.args.get('status'); platform=request.args.get('platform')
    conn=get_db(); q='SELECT * FROM social_posts WHERE 1=1'; params=[]
    if status:   q+=' AND status=?';   params.append(status)
    if platform: q+=' AND platform=?'; params.append(platform)
    rows=conn.execute(q+' ORDER BY created_at DESC',params).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/social/posts', methods=['POST'])
def create_post():
    d=request.json; pid=str(uuid.uuid4()); conn=get_db()
    conn.execute('INSERT INTO social_posts VALUES (?,?,?,?,?,?,?,?,?,?)',
        (pid,d['platform'],d['content'],d.get('image_path',''),d.get('status','draft'),
         d.get('scheduled_at',''),None,None,None,datetime.now().strftime('%Y-%m-%d %H:%M')))
    conn.commit(); conn.close(); return jsonify({'ok':True,'id':pid})

@app.route('/api/social/posts/<pid>', methods=['PUT'])
def update_post(pid):
    d=request.json; conn=get_db()
    fields=['content','image_path','status','scheduled_at']
    updates={k:d[k] for k in fields if k in d}
    if updates:
        conn.execute(f'UPDATE social_posts SET {", ".join(k+"=?" for k in updates)} WHERE id=?',list(updates.values())+[pid])
        conn.commit()
    conn.close(); return jsonify({'ok':True})

@app.route('/api/social/posts/<pid>', methods=['DELETE'])
def delete_post(pid):
    conn=get_db()
    row=conn.execute('SELECT image_path FROM social_posts WHERE id=?',(pid,)).fetchone()
    if row and row['image_path'] and os.path.exists(row['image_path']): os.remove(row['image_path'])
    conn.execute('DELETE FROM social_posts WHERE id=?',(pid,)); conn.commit(); conn.close()
    return jsonify({'ok':True})

@app.route('/api/social/upload', methods=['POST'])
def upload_image():
    if 'image' not in request.files: return jsonify({'ok':False,'error':'No image'})
    file=request.files['image']
    if not file.filename: return jsonify({'ok':False,'error':'Empty filename'})
    os.makedirs('uploads',exist_ok=True)
    ext=os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg','.jpeg','.png','.gif','.mp4','.mov']: return jsonify({'ok':False,'error':'Tipo no permitido'})
    filename=str(uuid.uuid4())+ext; path=os.path.join('uploads',filename)
    file.save(path)
    return jsonify({'ok':True,'path':path,'url':f'{BASE_URL}/uploads/{filename}'})

@app.route('/uploads/<filename>')
def serve_upload(filename): return send_from_directory('uploads',filename)

def _publish_linkedin(post_id, content, image_path=None):
    conn=get_db(); tok=conn.execute("SELECT * FROM social_tokens WHERE platform='linkedin'").fetchone(); conn.close()
    if not tok: return False,'Token LinkedIn no configurado'
    token=tok['access_token']; person_id=tok['user_id']
    hdrs={'Authorization':f'Bearer {token}','Content-Type':'application/json','X-Restli-Protocol-Version':'2.0.0'}
    try:
        asset_urn=None
        if image_path and os.path.exists(image_path):
            reg=json.dumps({"registerUploadRequest":{"recipes":["urn:li:digitalmediaRecipe:feedshare-image"],"owner":f"urn:li:person:{person_id}","serviceRelationships":[{"relationshipType":"OWNER","identifier":"urn:li:userGeneratedContent"}]}}).encode()
            req=urllib.request.Request('https://api.linkedin.com/v2/assets?action=registerUpload',data=reg,headers=hdrs,method='POST')
            with urllib.request.urlopen(req) as r: rd=json.loads(r.read())
            upload_url=rd['value']['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
            asset_urn=rd['value']['asset']
            with open(image_path,'rb') as f: img=f.read()
            ir=urllib.request.Request(upload_url,data=img,method='PUT'); ir.add_header('Authorization',f'Bearer {token}')
            with urllib.request.urlopen(ir): pass
        body={"author":f"urn:li:person:{person_id}","lifecycleState":"PUBLISHED","specificContent":{"com.linkedin.ugc.ShareContent":{"shareCommentary":{"text":content},"shareMediaCategory":"IMAGE" if asset_urn else "NONE",**({"media":[{"status":"READY","description":{"text":""},"media":asset_urn,"title":{"text":""}}]} if asset_urn else {})}},"visibility":{"com.linkedin.ugc.MemberNetworkVisibility":"PUBLIC"}}
        req2=urllib.request.Request('https://api.linkedin.com/v2/ugcPosts',data=json.dumps(body).encode(),headers=hdrs,method='POST')
        with urllib.request.urlopen(req2) as r: res=json.loads(r.read())
        url=f"https://www.linkedin.com/feed/update/{res.get('id','')}"
        conn=get_db(); conn.execute("UPDATE social_posts SET status='published',published_at=?,post_url=? WHERE id=?",(datetime.now().strftime('%Y-%m-%d %H:%M'),url,post_id)); conn.commit(); conn.close()
        return True,url
    except Exception as e:
        conn=get_db(); conn.execute("UPDATE social_posts SET status='error',error=? WHERE id=?",(str(e),post_id)); conn.commit(); conn.close()
        return False,str(e)

def _publish_instagram(post_id, content, image_path=None):
    conn=get_db(); tok=conn.execute("SELECT * FROM social_tokens WHERE platform='instagram'").fetchone(); conn.close()
    if not tok: return False,'Token Instagram no configurado'
    if not image_path: return False,'Instagram requiere imagen'
    token=tok['access_token']; ig_id=tok['user_id']
    try:
        img_url=f"{BASE_URL}/uploads/{os.path.basename(image_path)}"
        p1=urllib.parse.urlencode({'image_url':img_url,'caption':content,'access_token':token})
        req=urllib.request.Request(f'https://graph.facebook.com/v18.0/{ig_id}/media',data=p1.encode(),method='POST')
        with urllib.request.urlopen(req) as r: c=json.loads(r.read())
        cid=c.get('id'); assert cid, f'No container: {c}'
        p2=urllib.parse.urlencode({'creation_id':cid,'access_token':token})
        req2=urllib.request.Request(f'https://graph.facebook.com/v18.0/{ig_id}/media_publish',data=p2.encode(),method='POST')
        with urllib.request.urlopen(req2) as r: res=json.loads(r.read())
        url=f"https://www.instagram.com/p/{res.get('id','')}"
        conn=get_db(); conn.execute("UPDATE social_posts SET status='published',published_at=?,post_url=? WHERE id=?",(datetime.now().strftime('%Y-%m-%d %H:%M'),url,post_id)); conn.commit(); conn.close()
        return True,url
    except Exception as e:
        conn=get_db(); conn.execute("UPDATE social_posts SET status='error',error=? WHERE id=?",(str(e),post_id)); conn.commit(); conn.close()
        return False,str(e)

def _publish_tiktok(post_id, content, image_path=None):
    conn=get_db(); tok=conn.execute("SELECT * FROM social_tokens WHERE platform='tiktok'").fetchone(); conn.close()
    if not tok: return False,'Token TikTok no configurado'
    token=tok['access_token']
    try:
        hdrs={'Authorization':f'Bearer {token}','Content-Type':'application/json; charset=UTF-8'}
        body=json.dumps({"post_info":{"title":content[:150],"privacy_level":"PUBLIC_TO_EVERYONE","disable_duet":False,"disable_comment":False,"disable_stitch":False},"source_info":{"source":"PULL_FROM_URL","video_url":f"{BASE_URL}/uploads/{os.path.basename(image_path)}" if image_path else ""}}).encode()
        req=urllib.request.Request('https://open.tiktokapis.com/v2/post/publish/video/init/',data=body,headers=hdrs,method='POST')
        with urllib.request.urlopen(req) as r: res=json.loads(r.read())
        if res.get('error',{}).get('code','')!='ok': return False,res.get('error',{}).get('message','Error')
        conn=get_db(); conn.execute("UPDATE social_posts SET status='published',published_at=? WHERE id=?",(datetime.now().strftime('%Y-%m-%d %H:%M'),post_id)); conn.commit(); conn.close()
        return True,'https://www.tiktok.com'
    except Exception as e:
        conn=get_db(); conn.execute("UPDATE social_posts SET status='error',error=? WHERE id=?",(str(e),post_id)); conn.commit(); conn.close()
        return False,str(e)

PUBLISHERS={'linkedin':_publish_linkedin,'instagram':_publish_instagram,'tiktok':_publish_tiktok}

@app.route('/api/social/publish/<post_id>', methods=['POST'])
def publish_post(post_id):
    conn=get_db(); post=conn.execute('SELECT * FROM social_posts WHERE id=?',(post_id,)).fetchone(); conn.close()
    if not post: return jsonify({'ok':False,'error':'Post no encontrado'})
    if post['platform'] not in PUBLISHERS: return jsonify({'ok':False,'error':'Plataforma no soportada'})
    ok,result=PUBLISHERS[post['platform']](post_id,post['content'],post['image_path'])
    return jsonify({'ok':ok,'url':result if ok else None,'error':None if ok else result})

@app.route('/api/social/publish-all', methods=['POST'])
def publish_all():
    d=request.json; content=d.get('content',''); image_path=d.get('image_path','')
    platforms=d.get('platforms',['linkedin','instagram','tiktok']); results={}
    for platform in platforms:
        pid=str(uuid.uuid4()); conn=get_db()
        conn.execute('INSERT INTO social_posts VALUES (?,?,?,?,?,?,?,?,?,?)',
            (pid,platform,content,image_path,'publishing','',None,None,None,datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit(); conn.close()
        if platform in PUBLISHERS:
            ok,result=PUBLISHERS[platform](pid,content,image_path)
            results[platform]={'ok':ok,'url':result if ok else None,'error':None if ok else result}
    return jsonify({'ok':True,'results':results})

@app.route('/api/social/autopromote', methods=['POST'])
def social_autopromote():
    """
    Genera contenido por plataforma con el mejor modelo disponible de Ollama
    y opcionalmente lo publica o agenda de forma automática.
    Body:
    {
      "topic": "...",
      "brand": "DiazUX Studio",
      "goal": "conseguir leads",
      "platforms": ["linkedin","instagram"],
      "image_path": "uploads/xxx.png",
      "autopublish": true/false,
      "scheduled_at": "YYYY-MM-DD HH:MM" (opcional)
    }
    """
    d = request.json or {}
    topic = d.get('topic', '').strip()
    if not topic:
        return jsonify({'ok': False, 'error': 'topic es requerido'})

    platforms = d.get('platforms', ['linkedin', 'instagram'])
    platforms = [p for p in platforms if p in ['linkedin', 'instagram', 'tiktok']]
    if not platforms:
        return jsonify({'ok': False, 'error': 'No hay plataformas válidas'})

    brand = d.get('brand', 'DiazUX Studio')
    goal = d.get('goal', 'atraer prospectos de calidad')
    audience = d.get('audience', 'dueños/as y líderes de negocio')
    image_path = d.get('image_path', '')
    autopublish = bool(d.get('autopublish', True))
    scheduled_at = (d.get('scheduled_at') or '').strip()

    prompt = f"""
Sos estratega senior de social media para una agencia argentina.

Marca: {brand}
Objetivo comercial: {goal}
Audiencia: {audience}
Tema de campaña: {topic}
Plataformas: {", ".join(platforms)}

Devolvé SOLO JSON (sin markdown) con esta estructura:
{{
  "posts": {{
    "linkedin": {{"content":"post profesional de 80-180 palabras con CTA"}},
    "instagram": {{"content":"caption de 40-120 palabras + CTA + hashtags relevantes"}},
    "tiktok": {{"content":"texto corto (máx 150 chars) + hook"}}
  }}
}}

Reglas:
- Español rioplatense natural.
- Evitá frases genéricas.
- Cada plataforma debe tener copy distinto y optimizado.
"""

    generated, used_model, err = _ollama_generate(prompt, task='creative', expect_json=True, timeout=90)
    if err:
        return jsonify({'ok': False, 'error': f'No se pudo generar contenido con Ollama: {err}'})

    posts = (generated or {}).get('posts', {})
    if not isinstance(posts, dict):
        return jsonify({'ok': False, 'error': 'Formato de respuesta inválido del modelo'})

    results = {}
    conn = get_db()
    for platform in platforms:
        content = ((posts.get(platform) or {}).get('content') or '').strip()
        if not content:
            results[platform] = {'ok': False, 'error': 'Sin contenido generado'}
            continue

        pid = str(uuid.uuid4())
        status = 'scheduled' if scheduled_at else ('publishing' if autopublish else 'draft')
        conn.execute(
            'INSERT INTO social_posts VALUES (?,?,?,?,?,?,?,?,?,?)',
            (pid, platform, content, image_path, status, scheduled_at, None, None, None, datetime.now().strftime('%Y-%m-%d %H:%M'))
        )
        conn.commit()

        if scheduled_at:
            results[platform] = {'ok': True, 'post_id': pid, 'status': 'scheduled', 'scheduled_at': scheduled_at}
            continue
        if not autopublish:
            results[platform] = {'ok': True, 'post_id': pid, 'status': 'draft'}
            continue

        if platform not in PUBLISHERS:
            conn.execute("UPDATE social_posts SET status='error',error=? WHERE id=?", ('Plataforma no soportada', pid))
            results[platform] = {'ok': False, 'post_id': pid, 'error': 'Plataforma no soportada'}
            continue

        ok, publish_result = PUBLISHERS[platform](pid, content, image_path)
        results[platform] = {
            'ok': ok,
            'post_id': pid,
            'status': 'published' if ok else 'error',
            'url': publish_result if ok else None,
            'error': None if ok else publish_result
        }

    conn.close()
    return jsonify({
        'ok': True,
        'model': f'ollama/{used_model}',
        'topic': topic,
        'autopublish': autopublish,
        'scheduled_at': scheduled_at or None,
        'results': results
    })

@app.route('/api/social/schedule', methods=['POST'])
def schedule_post():
    d=request.json; pid=str(uuid.uuid4()); conn=get_db()
    conn.execute('INSERT INTO social_posts VALUES (?,?,?,?,?,?,?,?,?,?)',
        (pid,d['platform'],d['content'],d.get('image_path',''),'scheduled',d['scheduled_at'],None,None,None,datetime.now().strftime('%Y-%m-%d %H:%M')))
    conn.commit(); conn.close(); return jsonify({'ok':True,'id':pid})

def check_scheduled():
    now=datetime.now().strftime('%Y-%m-%d %H:%M')
    conn=get_db()
    posts=conn.execute("SELECT * FROM social_posts WHERE status='scheduled' AND scheduled_at<=?",(now,)).fetchall(); conn.close()
    for p in posts:
        if p['platform'] in PUBLISHERS: PUBLISHERS[p['platform']](p['id'],p['content'],p['image_path'])

def process_pending_email_sequences():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn = get_db()
    rows = conn.execute("SELECT * FROM email_sequences WHERE sent=0 AND scheduled_at<=? ORDER BY scheduled_at ASC LIMIT 10", (now,)).fetchall()
    for r in rows:
        p = conn.execute("SELECT * FROM prospects WHERE id=?", (r['prospect_id'],)).fetchone()
        if not p or not p['email']:
            conn.execute("UPDATE email_sequences SET sent=1,sent_at=? WHERE id=?", (now, r['id']))
            continue
        ok, result = _send_email_internal(p['email'], r['subject'], r['body'], r['prospect_id'], int(r['sequence_num'] or 1))
        if ok:
            conn.execute("UPDATE email_sequences SET sent=1,sent_at=? WHERE id=?", (now, r['id']))
            # Delay suave para evitar ráfagas sin bloquear el scheduler demasiado tiempo.
            time.sleep(min(2, max(0, DELAYS["between_emails"] / 300)))
        else:
            log_activity(r['prospect_id'], 'autopilot', json.dumps({
                "action": "email_send_failed",
                "email_subject": r['subject'],
                "error": result
            }, ensure_ascii=False))
    conn.commit(); conn.close()

def maybe_run_autopilot_schedule():
    now = datetime.now().strftime('%H:%M')
    day = datetime.now().weekday()  # lunes=0
    for sch in AUTOPILOT_SCHEDULES:
        if sch['time'] != now:
            continue
        if sch['action'] == 'run_prospecting':
            with app.test_request_context('/api/autopilot/run', method='POST', json={
                "search_query": sch.get("query", "negocios sin web argentina"),
                "max_prospects": 10,
                "auto_send_emails": False,
                "auto_publish_posts": False
            }):
                autopilot_run()
        elif sch['action'] == 'send_pending_emails':
            process_pending_email_sequences()
        elif sch['action'] == 'publish_scheduled_posts':
            check_scheduled()
        elif sch['action'] == 'generate_weekly_posts' and day == 0:
            with app.test_request_context('/api/social/autopromote', method='POST', json={
                "topic": "tips semanales de conversion para pymes argentinas",
                "platforms": ["instagram", "linkedin"],
                "autopublish": False
            }):
                social_autopromote()

def scheduler_loop():
    while True:
        try:
            check_scheduled()
            process_pending_email_sequences()
            maybe_run_autopilot_schedule()
        except Exception:
            pass
        time.sleep(60)

# ── OAUTH ────────────────────────────────────────────────────────
@app.route('/api/social/auth/linkedin')
def linkedin_auth():
    cfg=get_cfg(); cid=cfg.get('linkedin_client_id','')
    if not cid: return jsonify({'ok':False,'error':'Configurá LinkedIn Client ID en Ajustes → Redes Sociales'})
    state = uuid.uuid4().hex
    LINKEDIN_OAUTH_STATE[state] = time.time()
    ru=f'{BASE_URL}/api/social/auth/linkedin/callback'
    scope = "openid profile w_member_social"
    url=f'https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id={cid}&redirect_uri={urllib.parse.quote(ru)}&scope={urllib.parse.quote(scope)}&state={state}'
    return jsonify({'ok':True,'auth_url':url})

@app.route('/api/social/auth/linkedin/callback')
def linkedin_callback():
    code=request.args.get('code'); cfg=get_cfg()
    state = request.args.get('state', '')
    if not state or state not in LINKEDIN_OAUTH_STATE:
        return '<html><body style="background:#080808;color:#f87171;font-family:sans-serif;padding:40px;">Error: OAuth state inválido o expirado.</body></html>'
    LINKEDIN_OAUTH_STATE.pop(state, None)
    if not code: return '<script>window.close()</script>'
    try:
        data=urllib.parse.urlencode({'grant_type':'authorization_code','code':code,'redirect_uri':f'{BASE_URL}/api/social/auth/linkedin/callback','client_id':cfg.get('linkedin_client_id',''),'client_secret':cfg.get('linkedin_client_secret','')}).encode()
        req=urllib.request.Request('https://www.linkedin.com/oauth/v2/accessToken',data=data,method='POST')
        with urllib.request.urlopen(req) as r: tok=json.loads(r.read())
        token=tok['access_token']
        req2=urllib.request.Request('https://api.linkedin.com/v2/userinfo'); req2.add_header('Authorization',f'Bearer {token}')
        with urllib.request.urlopen(req2) as r: user=json.loads(r.read())
        conn=get_db()
        conn.execute('INSERT OR REPLACE INTO social_tokens VALUES (?,?,?,?,?,?)',('linkedin',token,'',(datetime.now()+timedelta(seconds=tok.get('expires_in',5184000))).strftime('%Y-%m-%d'),user.get('sub',''),json.dumps({'name':user.get('name','')})))
        conn.commit(); conn.close()
        return '<html><body style="background:#080808;color:#F2AABF;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0"><h2>✓ LinkedIn conectado. Podés cerrar esta ventana.</h2></body></html>'
    except Exception as e:
        return f'<html><body style="background:#080808;color:#f87171;font-family:sans-serif;padding:40px;">Error LinkedIn callback: {e}<br><small>Verificá BASE_URL, Redirect URI exacto y scope w_member_social.</small></body></html>'

# ── INSTAGRAM LOGIN API (token directo, sin OAuth complicado) ────
def _ig_api(path, token, method='GET', body=None):
    """Helper: llama Graph API y devuelve (data_dict, error_str)"""
    sep = '&' if '?' in path else '?'
    url = f'https://graph.instagram.com/{path}{sep}access_token={token}'
    try:
        data = json.dumps(body).encode() if body else None
        hdrs = {'Content-Type': 'application/json'} if body else {}
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8')
        print(f'IG API error {path}: {err}')
        try: msg = json.loads(err).get('error', {}).get('message', err)
        except: msg = err
        return None, msg
    except Exception as e:
        return None, str(e)

@app.route('/api/social/auth/instagram')
def instagram_auth():
    """Inicia OAuth con Instagram Login API (v21+)"""
    cfg = get_cfg()
    app_id = cfg.get('meta_app_id', '')
    if not app_id:
        return jsonify({'ok': False, 'error': 'Configurá Meta App ID en Ajustes → Redes Sociales'})
    ru = f'{BASE_URL}/api/social/auth/instagram/callback'
    scopes = 'instagram_business_basic,instagram_business_manage_messages,instagram_business_content_publish'
    url = (f'https://www.instagram.com/oauth/authorize'
           f'?enable_fb_login=0&force_authentication=1'
           f'&client_id={app_id}'
           f'&redirect_uri={urllib.parse.quote(ru)}'
           f'&response_type=code'
           f'&scope={urllib.parse.quote(scopes)}')
    return jsonify({'ok': True, 'auth_url': url})

@app.route('/api/social/auth/instagram/callback')
def instagram_callback():
    """Callback OAuth Instagram Login API — obtiene token y perfil"""
    code = request.args.get('code')
    cfg  = get_cfg()
    if not code:
        return '<script>window.close()</script>'
    app_id     = cfg.get('meta_app_id', '')
    app_secret = cfg.get('meta_app_secret', '')
    redirect_uri = f'{BASE_URL}/api/social/auth/instagram/callback'
    print(f'IG callback — app_id={app_id} secret_len={len(app_secret)} code_len={len(code)}')
    # 1) Intercambiar code → token de corta duración
    data = urllib.parse.urlencode({
        'client_id': app_id, 'client_secret': app_secret,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri, 'code': code
    }).encode()
    try:
        req = urllib.request.Request(
            'https://api.instagram.com/oauth/access_token', data=data, method='POST')
        with urllib.request.urlopen(req) as r:
            short = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f'IG step1 error: {body}')
        return f'<html><body style="background:#080808;color:#f87171;font-family:sans-serif;padding:40px;"><h2>Error paso 1 (token corto):</h2><pre style="color:#fbbf24">{body}</pre></body></html>'
    short_token = short.get('access_token', '')
    ig_user_id  = str(short.get('user_id', ''))
    print(f'IG step1 OK — user_id={ig_user_id}')
    # 2) Intercambiar → token de larga duración (60 días)
    params = urllib.parse.urlencode({
        'grant_type': 'ig_exchange_token',
        'client_secret': app_secret,
        'access_token': short_token
    })
    try:
        req2 = urllib.request.Request(
            f'https://graph.instagram.com/access_token?{params}')
        with urllib.request.urlopen(req2) as r:
            long_tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f'IG step2 error: {body}')
        return f'<html><body style="background:#080808;color:#f87171;font-family:sans-serif;padding:40px;"><h2>Error paso 2 (token largo):</h2><pre style="color:#fbbf24">{body}</pre></body></html>'
    token      = long_tok.get('access_token', short_token)
    expires_in = long_tok.get('expires_in', 5184000)
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).strftime('%Y-%m-%d')
    # 3) Obtener perfil
    profile, err = _ig_api(f'{ig_user_id}?fields=id,name,username', token)
    if err:
        print(f'IG profile error: {err}')
        profile = {'id': ig_user_id, 'username': ig_user_id, 'name': ''}
    username = profile.get('username', ig_user_id)
    name     = profile.get('name', '')
    print(f'IG connected — @{username} id={ig_user_id}')
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO social_tokens VALUES (?,?,?,?,?,?)',
        ('instagram', token, '', expires_at, ig_user_id,
         json.dumps({'username': username, 'name': name})))
    conn.commit(); conn.close()
    return (f'<html><body style="background:#080808;color:#F2AABF;font-family:sans-serif;'
            f'display:flex;flex-direction:column;align-items:center;justify-content:center;'
            f'height:100vh;margin:0;gap:8px">'
            f'<h2>✓ Instagram conectado</h2>'
            f'<p style="color:#aaa;margin:0">@{username} · token válido hasta {expires_at}</p>'
            f'<p style="color:#666;font-size:12px">Podés cerrar esta ventana</p>'
            f'</body></html>')

@app.route('/api/social/auth/instagram/token', methods=['POST'])
def instagram_save_token():
    """Guardar token manualmente (desde el panel de Meta for Developers)"""
    d = request.json
    token = d.get('access_token', '').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'Token vacío'})
    # Verificar token obteniendo perfil
    profile, err = _ig_api('me?fields=id,name,username', token)
    if err:
        return jsonify({'ok': False, 'error': f'Token inválido: {err}'})
    ig_id    = profile.get('id', '')
    username = profile.get('username', ig_id)
    name     = profile.get('name', '')
    print(f'IG manual token — @{username} id={ig_id}')
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO social_tokens VALUES (?,?,?,?,?,?)',
        ('instagram', token, '', '', ig_id,
         json.dumps({'username': username, 'name': name})))
    conn.commit(); conn.close()
    return jsonify({'ok': True, 'username': username, 'ig_id': ig_id})

@app.route('/api/social/auth/instagram/verify', methods=['GET'])
def instagram_verify():
    """Verificar que el token guardado sigue funcionando"""
    conn = get_db()
    tok = conn.execute("SELECT * FROM social_tokens WHERE platform='instagram'").fetchone()
    conn.close()
    if not tok:
        return jsonify({'ok': False, 'error': 'No hay token guardado'})
    profile, err = _ig_api(f'{tok["user_id"]}?fields=id,name,username', tok['access_token'])
    if err:
        return jsonify({'ok': False, 'error': err})
    return jsonify({'ok': True, 'username': profile.get('username'), 'name': profile.get('name'), 'ig_id': profile.get('id')})

# ── INSTAGRAM DMs ────────────────────────────────────────────────
@app.route('/api/social/instagram/dm', methods=['POST'])
def instagram_send_dm():
    """Enviar DM a un usuario de Instagram (requiere instagram_business_manage_messages)"""
    d = request.json
    recipient_username = d.get('username', '').lstrip('@')
    recipient_ig_id    = d.get('ig_id', '')
    message_text       = d.get('message', '')
    if not message_text:
        return jsonify({'ok': False, 'error': 'Mensaje vacío'})
    conn = get_db()
    tok = conn.execute("SELECT * FROM social_tokens WHERE platform='instagram'").fetchone()
    conn.close()
    if not tok:
        return jsonify({'ok': False, 'error': 'Instagram no conectado. Configurá el token primero.'})
    token  = tok['access_token']
    ig_id  = tok['user_id']
    # Si tenemos username pero no ig_id del destinatario, buscarlo
    if not recipient_ig_id and recipient_username:
        search, err = _ig_api(
            f'{ig_id}?fields=business_discovery.fields(id,username)'
            f'&username_to_lookup={recipient_username}', token)
        if err:
            return jsonify({'ok': False, 'error': f'No se pudo encontrar @{recipient_username}: {err}'})
        recipient_ig_id = (search.get('business_discovery') or {}).get('id', '')
        if not recipient_ig_id:
            return jsonify({'ok': False, 'error': f'Usuario @{recipient_username} no encontrado o no es cuenta profesional'})
    if not recipient_ig_id:
        return jsonify({'ok': False, 'error': 'Necesitás especificar username o ig_id del destinatario'})
    # Enviar mensaje
    body = {'recipient': {'id': recipient_ig_id}, 'message': {'text': message_text}}
    result, err = _ig_api(f'{ig_id}/messages', token, method='POST', body=body)
    if err:
        return jsonify({'ok': False, 'error': err})
    msg_id = result.get('message_id', result.get('id', ''))
    print(f'IG DM sent to {recipient_ig_id} — msg_id={msg_id}')
    return jsonify({'ok': True, 'message_id': msg_id, 'recipient_ig_id': recipient_ig_id})

@app.route('/api/social/instagram/conversations', methods=['GET'])
def instagram_conversations():
    """Listar conversaciones/DMs activos"""
    conn = get_db()
    tok = conn.execute("SELECT * FROM social_tokens WHERE platform='instagram'").fetchone()
    conn.close()
    if not tok:
        return jsonify({'ok': False, 'error': 'Instagram no conectado'})
    token = tok['access_token']; ig_id = tok['user_id']
    data, err = _ig_api(
        f'{ig_id}/conversations?platform=instagram&fields=id,participants,updated_time', token)
    if err:
        return jsonify({'ok': False, 'error': err})
    return jsonify({'ok': True, 'conversations': data.get('data', [])})

@app.route('/')
def index(): return send_file('index.html')

if __name__=='__main__':
    threading.Thread(target=scheduler_loop,daemon=True).start()
    print('\n'+'='*50+'\n  ProspectorAI — DiazUX Studio\n  http://localhost:5000\n'+'='*50+'\n')
    app.run(debug=False,port=5000,host='0.0.0.0')

"""
ProspectorAI — Servidor Flask COMPLETO
DiazUX Studio
"""
from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from flask_cors import CORS
import smtplib, ssl, json, sqlite3, os, uuid, threading, time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
import urllib.request, urllib.parse, urllib.error

app = Flask(__name__, static_folder='.')
CORS(app)
DB_PATH = 'prospector.db'
BASE_URL = os.environ.get('BASE_URL', 'https://prospectorai.onrender.com')

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
    ''')
    conn.commit(); conn.close()

init_db()

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
    """Genera texto con Groq API (gratis, rápido)"""
    d = request.json
    prompt = d.get('prompt', '')
    if not prompt:
        return jsonify({'ok': False, 'error': 'Prompt vacío'})
    api_key = os.environ.get('GROQ_API_KEY', '')
    if not api_key:
        cfg = get_cfg()
        api_key = cfg.get('groq_api_key', '')
    if not api_key:
        return jsonify({'ok': False, 'error': 'Configurá GROQ_API_KEY en Render → Environment'})
    body = json.dumps({
        'model': 'llama-3.3-70b-versatile',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 1024,
        'temperature': 0.7
    }).encode()
    try:
        req = urllib.request.Request(
            'https://api.groq.com/openai/v1/chat/completions',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        text = res['choices'][0]['message']['content']
        return jsonify({'ok': True, 'response': text})
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8')
        print(f'Groq API error: {err}')
        return jsonify({'ok': False, 'error': err})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

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

def scheduler_loop():
    while True:
        try: check_scheduled()
        except: pass
        time.sleep(60)

# ── OAUTH ────────────────────────────────────────────────────────
@app.route('/api/social/auth/linkedin')
def linkedin_auth():
    cfg=get_cfg(); cid=cfg.get('linkedin_client_id','')
    if not cid: return jsonify({'ok':False,'error':'Configurá LinkedIn Client ID en Ajustes → Redes Sociales'})
    ru=f'{BASE_URL}/api/social/auth/linkedin/callback'
    url=f'https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id={cid}&redirect_uri={urllib.parse.quote(ru)}&scope={urllib.parse.quote("openid profile w_member_social")}'
    return jsonify({'ok':True,'auth_url':url})

@app.route('/api/social/auth/linkedin/callback')
def linkedin_callback():
    code=request.args.get('code'); cfg=get_cfg()
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
        return f'<html><body style="background:#080808;color:#f87171;font-family:sans-serif;padding:40px;">Error: {e}</body></html>'

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
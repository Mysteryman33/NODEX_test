import gevent.monkey
gevent.monkey.patch_all()

import os
import json
import secrets
import hashlib
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, Response, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import uuid

DATABASE_URL = os.getenv("DATABASE_URL")
DB_TYPE = "postgres" if DATABASE_URL else "sqlite"

if DB_TYPE == "postgres":
    import psycopg2
    from psycopg2.extras import RealDictCursor

GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)
socketio = SocketIO(app, cors_allowed_origins="*")

def get_db():
    if DB_TYPE == "postgres":
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
        conn = sqlite3.connect("local_fallback.db")
        conn.row_factory = sqlite3.Row
        return conn

def run_query(cursor, query, params=()):
    if DB_TYPE == "sqlite":
        cursor.execute(query.replace("%s", "?"), params)
    else:
        cursor.execute(query, params)

def insert_returning_id(cursor, query, params=()):
    if DB_TYPE == "sqlite":
        clean_query = query.replace(" RETURNING id", "").replace("%s", "?")
        cursor.execute(clean_query, params)
        return cursor.lastrowid
    else:
        cursor.execute(query, params)
        return cursor.fetchone()["id"]

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    if DB_TYPE == "postgres":
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS graphs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT 'Untitled Canvas',
                data TEXT NOT NULL,
                share_id TEXT UNIQUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                settings JSONB NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS graph_collaborators (
                graph_id INTEGER REFERENCES graphs(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (graph_id, user_id)
            );
        """)
    else:
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS graphs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT DEFAULT 'Untitled Canvas',
                data TEXT NOT NULL,
                share_id TEXT UNIQUE,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                settings TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS graph_collaborators (
                graph_id INTEGER,
                user_id INTEGER,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (graph_id, user_id),
                FOREIGN KEY (graph_id) REFERENCES graphs(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
    conn.commit()
    try:
        run_query(cursor, "ALTER TABLE graphs ADD COLUMN share_id TEXT UNIQUE;")
        conn.commit()
    except:
        conn.rollback() if DB_TYPE == "postgres" else None
    try:
        run_query(cursor, "ALTER TABLE graphs ADD COLUMN title TEXT DEFAULT 'Untitled Canvas';")
        conn.commit()
    except:
        conn.rollback() if DB_TYPE == "postgres" else None
    cursor.close()
    conn.close()

init_db()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
<title>SecondBrain — Sign In</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&display=swap');
*{box-sizing:border-box;margin:0;padding:0;}
:root{--bg:#F7F5F0;--surface:#FFFFFF;--border:#E5E0D8;--text:#3E3A35;--muted:#8A837A;--accent:#A69076;--accent-hover:#8C7761;--green:#7B8A72;--red:#C88A7A;}
body{background:var(--bg);color:var(--text);font-family:'Outfit',system-ui,sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;overflow:hidden;}
.bg-grid{position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(rgba(166,144,118,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(166,144,118,0.05) 1px,transparent 1px);background-size:40px 40px;}
.card{position:relative;z-index:1;width:380px;background:rgba(255,255,255,0.6);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.8);border-radius:24px;padding:40px;box-shadow:0 20px 40px rgba(0,0,0,0.03),inset 0 1px 0 rgba(255,255,255,1);}
.logo{font-size:12px;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);margin-bottom:12px;display:flex;align-items:center;gap:8px;font-weight:500;}
.logo-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);}
h1{font-size:26px;font-weight:600;margin-bottom:8px;color:var(--text);}
.subtitle{font-size:13px;color:var(--muted);margin-bottom:32px;font-weight:300;}
.tabs{display:flex;gap:4px;margin-bottom:28px;background:rgba(0,0,0,0.03);border-radius:12px;padding:4px;}
.tab{flex:1;padding:10px;font-family:inherit;font-size:13px;border:none;border-radius:10px;cursor:pointer;transition:all .2s;color:var(--muted);background:transparent;font-weight:500;}
.tab.active{background:var(--surface);color:var(--text);box-shadow:0 4px 12px rgba(0,0,0,0.05);}
.field{margin-bottom:16px;}
label{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:8px;font-weight:500;}
input{width:100%;padding:12px 14px;background:var(--surface);border:1px solid var(--border);border-radius:12px;color:var(--text);font-family:inherit;font-size:14px;outline:none;transition:border-color .2s,box-shadow .2s;}
input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(166,144,118,0.15);}
.remember{display:flex;align-items:center;gap:8px;margin-bottom:24px;cursor:pointer;}
.remember input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent);}
.remember span{font-size:13px;color:var(--muted);}
.btn{width:100%;padding:14px;background:var(--accent);border:none;border-radius:12px;color:#fff;font-family:inherit;font-size:14px;font-weight:500;cursor:pointer;transition:all .2s;letter-spacing:.02em;box-shadow:0 8px 16px rgba(166,144,118,0.2);margin-bottom:12px;}
.btn:hover{background:var(--accent-hover);transform:translateY(-1px);box-shadow:0 12px 20px rgba(166,144,118,0.25);}
.btn-guest{background:transparent;border:1px solid var(--border);color:var(--text);box-shadow:none;margin-bottom:0;}
.btn-guest:hover{background:rgba(0,0,0,0.02);border-color:var(--muted);box-shadow:none;transform:none;}
.msg{font-size:13px;margin-top:16px;padding:12px 14px;border-radius:10px;display:none;}
.msg.error{background:rgba(200,138,122,0.1);color:var(--red);}
.msg.success{background:rgba(123,138,114,0.1);color:var(--green);}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="card">
  <div class="logo"><div class="logo-dot"></div>SecondBrain</div>
  <h1>Welcome back</h1>
  <div class="subtitle">A peaceful space for your thoughts.</div>
  <div class="tabs">
    <button class="tab active" id="tab-signin" onclick="switchTab('signin')">Sign In</button>
    <button class="tab" id="tab-signup" onclick="switchTab('signup')">Sign Up</button>
  </div>
  <div class="field"><label>Email</label><input type="email" id="email" placeholder="you@example.com" autocomplete="email"/></div>
  <div class="field"><label>Password</label><input type="password" id="password" placeholder="••••••••" autocomplete="current-password"/></div>
  <label class="remember"><input type="checkbox" id="remember" checked/><span>Keep me signed in for 30 days</span></label>
  <button class="btn" onclick="submit()" id="main-btn">Sign In →</button>
  <button class="btn btn-guest" onclick="window.location.href='/auth/guest'">Continue as Guest (Not Saved)</button>
  <div class="msg" id="msg"></div>
</div>
<script>
let mode='signin';
function switchTab(t){mode=t;document.getElementById('tab-signin').classList.toggle('active',t==='signin');document.getElementById('tab-signup').classList.toggle('active',t==='signup');document.getElementById('main-btn').textContent=t==='signin'?'Sign In →':'Create Account →';document.getElementById('msg').style.display='none';}
async function submit(){const email=document.getElementById('email').value.trim();const password=document.getElementById('password').value;const remember=document.getElementById('remember').checked;const msg=document.getElementById('msg');msg.style.display='none';if(!email||!password){msg.className='msg error';msg.textContent='Please fill in all fields.';msg.style.display='block';return;}const endpoint=mode==='signin'?'/auth/login':'/auth/signup';const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password,remember})});const d=await r.json();if(d.ok){msg.className='msg success';msg.textContent='Success! Redirecting…';msg.style.display='block';const nextUrl=d.next_url||'/';setTimeout(()=>window.location.href=nextUrl,500);}else{msg.className='msg error';msg.textContent=d.error||'Something went wrong.';msg.style.display='block';}}
document.addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
</script>
</body>
</html>"""

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect("/")
    return Response(LOGIN_HTML, mimetype="text/html")

@app.route("/auth/guest")
def auth_guest():
    session["user_id"] = -1
    session["email"] = "Guest"
    return redirect("/b/guest")

@app.route("/auth/signup", methods=["POST"])
def signup():
    d = request.get_json()
    email = (d.get("email") or "").strip().lower()
    pw = d.get("password") or ""
    remember = d.get("remember", True)
    if not email or not pw: return jsonify({"error": "Email and password required."})
    if len(pw) < 6: return jsonify({"error": "Password must be at least 6 characters."})
    conn = get_db()
    cursor = conn.cursor()
    try:
        user_id = insert_returning_id(cursor, "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id", (email, hash_password(pw)))
        conn.commit()
        session.permanent = remember
        session["user_id"] = user_id
        session["email"] = email
        next_url = session.pop("next_url", "/")
        return jsonify({"ok": True, "next_url": next_url})
    except Exception:
        conn.rollback()
        return jsonify({"error": "Email already registered."})
    finally:
        cursor.close()
        conn.close()

@app.route("/auth/login", methods=["POST"])
def login():
    d = request.get_json()
    email = (d.get("email") or "").strip().lower()
    pw = d.get("password") or ""
    remember = d.get("remember", True)
    conn = get_db()
    cursor = conn.cursor()
    run_query(cursor, "SELECT * FROM users WHERE email=%s AND password_hash=%s", (email, hash_password(pw)))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    if not user:
        return jsonify({"error": "Invalid email or password."})
    session.permanent = remember
    session["user_id"] = user["id"]
    session["email"] = email
    next_url = session.pop("next_url", "/")
    return jsonify({"ok": True, "next_url": next_url})

@app.route("/auth/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/auth/me")
def me():
    if "user_id" not in session: return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "email": session.get("email")})

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
<title>SecondBrain</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600&display=swap');
:root{
  --ui-scale: 1.0;
  --bg: #F7F5F0;
  --canvas-bg: #F7F5F0;
  --surface: #FFFFFF;
  --surface2: #FBFaf8;
  --border: #E8E2D9;
  --border2: #DCD5CB;
  --text: #3E3A35;
  --muted: #8A837A;
  --muted2: #A39B8B;
  --glass-bg: rgba(255, 255, 255, 0.7);
  --grid-color: rgba(166, 144, 118, 0.15);
  --accent: #A69076;
  --accent2: #8C7761;
  --accent-soft: rgba(166, 144, 118, 0.12);
  --green: #8B9982;
  --blue: #8C9CA6;
  --yellow: #C2B38A;
  --orange: #C29A78;
  --purple: #9B8C9C;
  --red: #C88A7A;
  --node-q: #EBE5D9;
  --node-a: #E1D9CD;
  --node-note: #F2EFE9;
  --node-timer: #D8CAB8;
  --node-brainstorm: #D4C9BD;
  --node-browser: #D6E4ED;
  --ring-bg: #EAE6DF;
  --select-color: #A69076;
}
*{box-sizing:border-box;}
body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:'Outfit',system-ui,sans-serif;overflow:hidden;touch-action:none;font-weight:400;}
#app{position:fixed;inset:0;display:flex;flex-direction:column;z-index:1;}
*::-webkit-scrollbar{display:none;}
*{-ms-overflow-style:none;scrollbar-width:none;}

#top-bar-container{position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:200;pointer-events:none;}
#top-bar{display:flex;gap:8px;align-items:center;background:var(--glass-bg);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);padding:8px 12px;border-radius:40px;border:1px solid rgba(255,255,255,0.8);box-shadow:0 10px 40px rgba(0,0,0,0.04),inset 0 1px 0 rgba(255,255,255,1);pointer-events:auto;}

.board-title-input{background:transparent;border:none;color:var(--text);font-family:inherit;font-weight:500;font-size:calc(14px * var(--ui-scale));outline:none;padding:calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));border-radius:20px;transition:background 0.2s,box-shadow 0.2s;min-width:130px;max-width:250px;text-align:center;}
.board-title-input:hover,.board-title-input:focus{background:rgba(0,0,0,0.03);box-shadow:inset 0 0 0 1px rgba(0,0,0,0.05);}

.user-badge{font-size:calc(11px * var(--ui-scale));color:var(--muted);padding:calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));border:1px solid var(--border);border-radius:20px;background:var(--surface);letter-spacing:.02em;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;font-weight:500;}
.user-badge a{color:var(--accent);text-decoration:none;}
.user-badge a:hover{color:var(--accent2);}

#canvas-wrapper{flex:1;overflow:scroll;position:relative;background:var(--canvas-bg);cursor:default;}
#canvas{position:absolute;top:0;left:0;background-image:radial-gradient(var(--grid-color) 1px,transparent 1px);background-size:30px 30px;}
#link-layer{position:absolute;inset:0;pointer-events:auto;}

#lasso-box{position:absolute;border:1px dashed var(--accent);background:var(--accent-soft);pointer-events:none;z-index:5000;display:none;border-radius:8px;}

.markdown-body{font-family:inherit;font-size:inherit;line-height:1.5;}
.markdown-body p{margin:0 0 8px 0;}
.markdown-body p:last-child{margin:0;}
.markdown-body strong{font-weight:600;color:var(--text);}
.markdown-body em{font-style:italic;}
.markdown-body code{background:rgba(0,0,0,0.05);padding:2px 4px;border-radius:6px;font-size:0.9em;font-family:monospace;}

@keyframes pulse-think{0%{opacity:0.5;}50%{opacity:1;}100%{opacity:0.5;}}
.thinking-spinner{animation:pulse-think 1.5s infinite ease-in-out;font-style:italic;color:var(--accent);font-weight:500;}
.is-thinking .bubble{border-color:var(--accent);box-shadow:0 4px 16px var(--accent-soft);}

.node{position:absolute;cursor:grab;user-select:none;font-size:13px;line-height:1.4;z-index:10;}
.node-circle{width:8px;height:8px;border-radius:50%;margin-bottom:6px;flex-shrink:0;transition:transform .2s;box-shadow:0 2px 4px rgba(0,0,0,0.05);}
.node-text{position:relative;white-space:pre-wrap;color:var(--text);transition:opacity .2s ease;}
.node-time{position:absolute;bottom:-20px;left:0;font-size:10px;color:var(--muted);white-space:nowrap;pointer-events:none;}

.dim-0 .node-text{opacity:1;}.dim-1 .node-text{opacity:.85;}.dim-2 .node-text{opacity:.70;}.dim-3 .node-text{opacity:.60;}.dim-4 .node-text{opacity:.50;}
.node:hover .node-text{opacity:1!important;}
.node:hover .node-circle{transform:scale(1.3);}

.content-box{max-height:150px;overflow-y:auto;overflow-wrap:anywhere;padding-right:4px;max-width:300px;}
.content-box.expanded{max-height:none;}
.expand-btn{background:transparent;border:none;color:var(--accent);font-size:11px;cursor:pointer;padding:4px 0 0 0;display:none;font-family:inherit;width:100%;text-align:left;font-weight:500;}
.expand-btn:hover{text-decoration:underline;}

.node-question .node-circle,.node-text .node-circle{background:var(--node-q);}
.node-answer .node-circle{background:var(--node-a);}
.node-answer.completed .node-circle{background:var(--blue);}
.node-timer .node-circle{background:var(--node-timer);}
.node-timer.completed .node-circle{background:var(--blue);}
.node-note .node-circle{background:var(--node-note);}
.node-brainstorm .node-circle{background:var(--node-brainstorm);}
.node-browser .node-circle{background:var(--node-browser);}

.node.selected .node-circle{background:var(--select-color)!important;transform:scale(1.4);box-shadow:0 0 0 2px var(--surface),0 0 0 4px var(--select-color);}
.node.ctrl-highlight .node-circle{outline:2px solid var(--accent);outline-offset:3px;}
.node.find-focus .node-circle{box-shadow:0 0 0 4px var(--accent)!important;animation:find-pulse 1s ease-in-out 3;}

.pinned-highlight .node-circle{box-shadow:0 0 0 3px var(--surface),0 0 0 5px var(--yellow)!important;transform:scale(1.4);}
.group-hull.pinned-highlight{border-width:2px!important;border-color:var(--yellow)!important;border-style:dashed!important;background:rgba(194,179,138,0.05)!important;opacity:1!important;box-shadow:0 8px 32px rgba(194,179,138,0.15)!important;z-index:10;}

@keyframes find-pulse{0%,100%{transform:scale(1);}50%{transform:scale(1.4);}}

.group-badge{position:absolute;top:-8px;left:-4px;width:10px;height:10px;border-radius:50%;border:2px solid var(--surface);z-index:6;}

.bubble{min-width:150px;min-height:50px;max-width:400px;max-height:250px;padding:12px 14px;border-radius:16px;border:1px solid var(--border);background:var(--surface);position:relative;overflow-y:auto;overflow-wrap:anywhere;resize:both;box-shadow:0 8px 24px rgba(0,0,0,0.03);}
.bubble-header{display:flex;justify-content:flex-end;margin-bottom:6px;}
.copy-btn{background:var(--surface2);border:1px solid var(--border);color:var(--muted);font-size:11px;padding:4px 8px;border-radius:12px;cursor:pointer;font-family:inherit;transition:all .2s;display:flex;align-items:center;gap:4px;font-weight:500;}
.copy-btn:hover{border-color:var(--accent);color:var(--text);background:var(--surface);}
.copy-btn svg{width:12px;height:12px;}

.note-wrap{display:flex;flex-direction:column;gap:6px;position:relative;}
.note-title{background:transparent;border:none;border-bottom:1px solid var(--border);color:var(--text);font-family:inherit;font-size:14px;font-weight:600;padding:4px 6px;width:100%;outline:none;transition:border-color 0.2s;}
.note-title:focus{border-bottom-color:var(--accent);}
.note-body{min-width:150px;min-height:80px;max-width:none;max-height:250px;overflow-y:auto;overflow-wrap:anywhere;resize:both;background:var(--surface);border:1px solid var(--border);border-radius:16px;color:var(--text);font-family:inherit;font-size:13px;padding:12px 14px;outline:none;box-shadow:0 8px 24px rgba(0,0,0,0.03);transition:border-color 0.2s;}
.note-body:focus{border-color:var(--accent);}

.brainstorm-wrap{display:flex;flex-direction:column;gap:6px;width:260px;}
.brainstorm-input{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:10px 12px;font-size:13px;outline:none;border-radius:12px;font-family:inherit;resize:vertical;min-height:50px;max-height:200px;width:100%;white-space:pre-wrap;box-shadow:0 8px 24px rgba(0,0,0,0.03);transition:border-color 0.2s;}
.brainstorm-input:focus{border-color:var(--accent);}
.brainstorm-run{background:var(--accent);color:white;font-weight:500;font-family:inherit;border:none;padding:8px 12px;font-size:12px;cursor:pointer;border-radius:10px;transition:all 0.2s;}
.brainstorm-run:hover{background:var(--accent2);transform:translateY(-1px);box-shadow:0 4px 12px rgba(166,144,118,0.2);}

/* ── Browser Node ── */
.browser-wrap{display:flex;flex-direction:column;width:480px;height:320px;background:var(--surface);border:1px solid var(--border);border-radius:16px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,0.04);resize:both;min-width:280px;min-height:200px;}
.browser-toolbar{display:flex;align-items:center;gap:6px;padding:8px 10px;background:var(--surface2);border-bottom:1px solid var(--border);flex-shrink:0;user-select:none;}
.browser-dots{display:flex;gap:5px;flex-shrink:0;}
.browser-dot{width:10px;height:10px;border-radius:50%;cursor:pointer;transition:filter 0.15s;}
.browser-dot.red{background:#FF5F57;}.browser-dot.yellow{background:#FEBC2E;}.browser-dot.green{background:#28C840;}
.browser-dot:hover{filter:brightness(0.8);}
.browser-address-bar{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:inherit;font-size:12px;padding:5px 10px;outline:none;transition:border-color 0.2s,box-shadow 0.2s;}
.browser-address-bar:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(166,144,118,0.15);}
.browser-nav-btn{background:transparent;border:none;color:var(--muted);font-size:15px;cursor:pointer;padding:4px 5px;border-radius:6px;transition:all 0.15s;flex-shrink:0;display:flex;align-items:center;justify-content:center;line-height:1;width:26px;height:26px;}
.browser-nav-btn:hover{background:rgba(0,0,0,0.06);color:var(--text);}
.browser-nav-btn:disabled{opacity:0.3;cursor:default;}
.browser-iframe-wrap{flex:1;position:relative;overflow:hidden;background:#fff;}
.browser-iframe{width:100%;height:100%;border:none;display:block;}
.browser-loading-overlay{position:absolute;inset:0;background:var(--surface);display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--muted);pointer-events:none;flex-direction:column;gap:8px;}
.browser-spinner{width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin-browser 0.7s linear infinite;}
@keyframes spin-browser{to{transform:rotate(360deg);}}
.browser-new-tab{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:14px;padding:20px;background:var(--surface);}
.browser-new-tab-logo{font-size:22px;font-weight:600;color:var(--accent);letter-spacing:-0.5px;}
.browser-new-tab-hint{font-size:11px;color:var(--muted);text-align:center;max-width:280px;line-height:1.5;}
.browser-quick-links{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;max-width:360px;}
.browser-quick-link{background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:7px 12px;font-size:11px;color:var(--text);cursor:pointer;transition:all 0.15s;font-weight:500;font-family:inherit;}
.browser-quick-link:hover{border-color:var(--accent);color:var(--accent);}
.browser-blocked-msg{display:none;position:absolute;inset:0;background:var(--surface);flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:20px;text-align:center;}
.browser-blocked-msg.visible{display:flex;}
.browser-blocked-icon{font-size:32px;}
.browser-blocked-title{font-size:14px;font-weight:500;color:var(--text);}
.browser-blocked-desc{font-size:12px;color:var(--muted);line-height:1.5;max-width:280px;}
.browser-open-btn{background:var(--accent);color:white;border:none;border-radius:10px;padding:8px 16px;font-size:12px;font-family:inherit;font-weight:500;cursor:pointer;transition:all 0.2s;}
.browser-open-btn:hover{background:var(--accent2);}

.timer-ring{position:relative;width:80px;height:80px;display:flex;align-items:center;justify-content:center;}
.timer-ring svg{position:absolute;inset:0;transform:rotate(-90deg);}
.ring-bg{fill:none;stroke:var(--ring-bg);stroke-width:6;}
.ring-progress{fill:none;stroke-width:6;stroke-linecap:round;stroke:url(#timerGradient);transition:stroke .2s ease;}
.timer-text{position:relative;font-size:12px;font-weight:500;}

.group-hull{position:absolute;border-radius:24px;border:1px solid;pointer-events:all;z-index:2;cursor:move;transition:opacity .3s,border-color .3s,box-shadow .3s;}
.group-hull:not(.collapsed){opacity:.08;}.group-hull:not(.collapsed):hover{opacity:.15;}
.group-hull.collapsed{opacity:.25;}.group-hull.collapsed:hover{opacity:.4;}
.group-label{position:absolute;font-size:11px;opacity:.6;pointer-events:none;z-index:3;letter-spacing:.05em;text-transform:uppercase;font-weight:600;}
.group-label.collapsed-label{pointer-events:all;cursor:pointer;opacity:.9;}
.group-resize-handle{position:absolute;width:14px;height:14px;background:rgba(0,0,0,.1);border-radius:50%;cursor:se-resize;z-index:12;right:-6px;bottom:-6px;pointer-events:all;transition:background 0.2s;}
.group-resize-handle:hover{background:rgba(0,0,0,.3);}

#ctx-menu{position:fixed;background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:6px;z-index:300;display:none;min-width:180px;box-shadow:0 10px 40px rgba(0,0,0,0.06);}
#ctx-menu.visible{display:block;}
.ctx-item{padding:10px 14px;font-size:12px;cursor:pointer;border-radius:10px;color:var(--text);font-weight:500;}
.ctx-item:hover{background:var(--bg);}
.ctx-item.danger{color:var(--red);}

#suggestions-bar{position:fixed;bottom:100px;left:50%;transform:translateX(-50%);display:flex;gap:8px;z-index:999;background:transparent;border:none;padding:0;min-height:auto;}
.suggestion-btn{background:var(--glass-bg);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.8);border-radius:24px;padding:8px 16px;font-size:12px;font-weight:500;color:var(--text);cursor:pointer;transition:all .2s;max-width:320px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 4px 12px rgba(0,0,0,0.05);}
.suggestion-btn:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-2px);box-shadow:0 6px 16px rgba(0,0,0,0.08);}

#input-bar-container{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);z-index:1000;}
#input-bar{display:flex;gap:12px;padding:12px 16px;background:var(--glass-bg);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.8);border-radius:30px;width:640px;max-width:90vw;box-shadow:0 12px 40px rgba(0,0,0,0.06),inset 0 1px 0 rgba(255,255,255,1);align-items:flex-end;}
#prompt{flex:1;resize:none;background:transparent;border:none;color:var(--text);padding:8px 6px;font-family:inherit;font-size:14px;outline:none;max-height:120px;min-height:24px;overflow-y:auto;line-height:1.5;margin-bottom:2px;}
#prompt::placeholder{color:var(--muted);font-weight:300;}
#send-btn{width:40px;height:40px;border-radius:50%;padding:0;background:var(--accent);color:white;border:none;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:all 0.2s;font-size:18px;font-weight:bold;flex-shrink:0;margin-bottom:2px;box-shadow:0 4px 12px rgba(166,144,118,0.2);}
#send-btn:hover{transform:scale(1.05);background:var(--accent2);box-shadow:0 6px 16px rgba(166,144,118,0.3);}

.top-btn{background:transparent;border:none;color:var(--text);padding:calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));border-radius:20px;font-family:inherit;font-size:calc(13px * var(--ui-scale));font-weight:500;cursor:pointer;transition:all .2s ease;display:flex;align-items:center;gap:6px;white-space:nowrap;}
.top-btn:hover{background:rgba(0,0,0,0.04);color:var(--accent);}
.top-btn svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}

.btn-primary-glass{background:var(--accent);color:white;border-radius:20px;box-shadow:0 4px 12px rgba(166,144,118,0.2);}
.btn-primary-glass:hover{background:var(--accent2);color:white;transform:translateY(-1px);box-shadow:0 6px 16px rgba(166,144,118,0.3);}

#chat-window{position:fixed;width:320px;height:400px;min-width:250px;min-height:300px;background:var(--glass-bg);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);border:1px solid rgba(255,255,255,0.8);border-radius:24px;display:none;flex-direction:column;z-index:2000;box-shadow:0 16px 50px rgba(0,0,0,0.06);resize:both;overflow:hidden;}
#chat-window.visible{display:flex;}
.chat-header{padding:14px 18px;border-bottom:1px solid var(--border);font-weight:600;display:flex;justify-content:space-between;align-items:center;font-size:14px;cursor:move;user-select:none;}
#chat-close{background:transparent;border:none;color:var(--muted);cursor:pointer;font-size:18px;transition:color 0.2s;padding:0 4px;}
#chat-close:hover{color:var(--red);}
.chat-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;}
.chat-msg{padding:10px 14px;border-radius:16px;font-size:12px;word-wrap:break-word;max-width:85%;position:relative;}
.chat-msg.sent{align-self:flex-end;background:var(--accent);color:white;border-bottom-right-radius:4px;box-shadow:0 4px 12px rgba(166,144,118,0.15);}
.chat-msg.received{align-self:flex-start;background:var(--surface);border:1px solid var(--border);color:var(--text);border-bottom-left-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,0.03);}
.chat-msg .sender{font-weight:600;font-size:10px;opacity:0.9;margin-bottom:4px;}
.chat-msg.sent .sender{text-align:right;}
.chat-msg .timestamp{font-size:9px;opacity:0.7;margin-top:6px;text-align:right;display:block;}
.chat-msg .node-ref{display:inline-block;background:rgba(0,0,0,0.05);border:1px solid rgba(0,0,0,0.1);padding:4px 8px;border-radius:8px;margin-bottom:6px;cursor:pointer;color:inherit;text-decoration:none;font-size:10px;transition:all 0.2s;font-weight:500;}
.chat-msg.sent .node-ref{background:rgba(255,255,255,0.15);border-color:rgba(255,255,255,0.3);}
.chat-msg .node-ref:hover{background:rgba(0,0,0,0.1);}
.chat-msg.sent .node-ref:hover{background:rgba(255,255,255,0.25);}
.chat-msg .delete-msg{position:absolute;top:6px;right:6px;background:transparent;border:none;color:inherit;opacity:0;font-size:12px;cursor:pointer;transition:opacity 0.2s;}
.chat-msg:hover .delete-msg{opacity:0.6;}
.chat-msg .delete-msg:hover{opacity:1;}
.chat-input-wrap{display:flex;border-top:1px solid var(--border);padding:12px;background:rgba(255,255,255,0.5);gap:8px;}
#chat-input{flex:1;background:var(--surface);border:1px solid var(--border);border-radius:20px;color:var(--text);outline:none;font-family:inherit;font-size:13px;padding:8px 12px;transition:border-color 0.2s;}
#chat-input:focus{border-color:var(--accent);}
#chat-send{background:var(--accent);border:none;color:white;border-radius:50%;width:34px;height:34px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;box-shadow:0 4px 10px rgba(166,144,118,0.2);transition:all 0.2s;}
#chat-send:hover{background:var(--accent2);transform:scale(1.05);}

#zoom-controls{position:fixed;bottom:130px;right:20px;display:flex;flex-direction:column;gap:8px;z-index:200;}
.zoom-btn{background:var(--glass-bg);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.8);color:var(--text);width:40px;height:40px;border-radius:16px;font-family:inherit;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .2s;box-shadow:0 4px 12px rgba(0,0,0,0.05);}
.zoom-btn:hover{background:var(--surface);border-color:var(--accent);color:var(--accent);transform:translateY(-1px);box-shadow:0 6px 16px rgba(0,0,0,0.08);}
#zoom-label{background:var(--glass-bg);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid rgba(255,255,255,0.8);color:var(--muted);font-size:11px;border-radius:12px;text-align:center;padding:6px 0;font-family:inherit;font-weight:500;}

#slash-popup{position:absolute;bottom:calc(100% + 16px);left:16px;background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:8px;z-index:1000;display:none;min-width:280px;box-shadow:0 12px 40px rgba(0,0,0,0.08);}
#slash-popup.visible{display:block;}
.slash-item{display:flex;align-items:flex-start;gap:12px;padding:10px 12px;border-radius:10px;cursor:pointer;}
.slash-item:hover,.slash-item.active{background:var(--bg);}
.slash-item-cmd{color:var(--accent);font-size:13px;font-weight:600;white-space:nowrap;min-width:80px;}
.slash-item-desc{color:var(--muted);font-size:12px;line-height:1.4;}

#color-picker-popup{position:fixed;background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px;z-index:1500;display:none;box-shadow:0 12px 40px rgba(0,0,0,.08);flex-direction:column;gap:12px;min-width:240px;}
#color-picker-popup.visible{display:flex;}
.color-picker-title{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:600;}
.color-swatches{display:flex;gap:8px;flex-wrap:wrap;}
.color-swatch{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:transform .2s,border-color .2s;}
.color-swatch:hover{transform:scale(1.15);}
.color-swatch.active{border-color:var(--text);box-shadow:0 4px 12px rgba(0,0,0,0.1);}
.color-picker-input{background:var(--bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:inherit;font-size:13px;padding:8px 12px;width:100%;outline:none;transition:border-color 0.2s;}
.color-picker-input:focus{border-color:var(--accent);}
.color-picker-actions{display:flex;gap:8px;}
.color-picker-btn{flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:12px;padding:8px;border-radius:10px;cursor:pointer;transition:all .2s;font-weight:500;}
.color-picker-btn:hover{border-color:var(--accent);background:var(--surface);}

.modal-overlay{position:fixed;inset:0;background:rgba(247,245,240,0.8);z-index:3000;display:none;align-items:center;justify-content:center;backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);}
.modal-overlay.visible{display:flex;}
.modal-box{background:var(--surface);border:1px solid var(--border);border-radius:24px;padding:32px;min-width:360px;display:flex;flex-direction:column;gap:20px;box-shadow:0 20px 60px rgba(0,0,0,0.05);max-width:90vw;}
.modal-title{font-size:18px;font-weight:600;color:var(--text);display:flex;justify-content:space-between;}
.settings-row{display:flex;flex-direction:column;gap:8px;}
.settings-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;font-weight:600;}
.settings-select,.modal-input{background:var(--bg);border:1px solid var(--border);border-radius:12px;color:var(--text);font-family:inherit;font-size:14px;padding:12px 14px;outline:none;width:100%;transition:border-color 0.2s;}
.settings-select:focus,.modal-input:focus{border-color:var(--accent);}
.modal-close,.modal-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:calc(13px * var(--ui-scale));padding:calc(10px * var(--ui-scale)) calc(16px * var(--ui-scale));border-radius:12px;cursor:pointer;transition:all .2s;align-self:flex-end;font-weight:500;display:flex;align-items:center;gap:6px;}
.modal-close:hover{border-color:var(--accent);background:var(--surface);}
.modal-btn.primary{background:var(--accent);color:white;border:none;box-shadow:0 4px 12px rgba(166,144,118,0.2);}
.modal-btn.primary:hover{background:var(--accent2);transform:translateY(-1px);box-shadow:0 6px 16px rgba(166,144,118,0.25);}

.share-input-wrap{display:flex;gap:8px;align-items:center;}
.collab-list{max-height:150px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;margin-top:8px;}
.collab-item{font-size:13px;padding:10px 14px;background:var(--bg);border-radius:12px;border:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;}
.remove-collab-btn{background:transparent;border:none;color:var(--red);font-weight:bold;cursor:pointer;padding:6px;border-radius:8px;transition:background 0.2s;}
.remove-collab-btn:hover{background:rgba(200,138,122,0.1);}

.dash-list{max-height:240px;overflow-y:auto;display:flex;flex-direction:column;gap:10px;}
.dash-item{font-size:13px;padding:16px;background:var(--surface);border-radius:16px;border:1px solid var(--border);transition:all 0.2s;display:flex;justify-content:space-between;align-items:center;box-shadow:0 4px 12px rgba(0,0,0,0.02);}
.dash-item:hover{border-color:var(--accent);transform:translateY(-1px);box-shadow:0 8px 24px rgba(0,0,0,0.04);}
.dash-delete-btn{background:transparent;border:none;color:var(--muted);cursor:pointer;font-size:16px;margin-left:8px;padding:6px;border-radius:8px;transition:all 0.2s;}
.dash-delete-btn:hover{background:rgba(200,138,122,0.1);color:var(--red);}

#presence-bar{display:flex;gap:-8px;margin-left:10px;align-items:center;}
.presence-avatar{width:28px;height:28px;border-radius:50%;border:2px solid var(--surface);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:12px;color:white;font-weight:600;text-transform:uppercase;transition:transform 0.2s,z-index 0.2s;position:relative;box-shadow:0 2px 8px rgba(0,0,0,0.1);}
.presence-avatar:hover{transform:translateY(-3px) scale(1.1);z-index:100!important;}
.remote-cursor{position:absolute;pointer-events:none;z-index:9000;display:flex;flex-direction:column;align-items:flex-start;transition:transform 0.1s linear,left 0.1s linear,top 0.1s linear;}
.remote-cursor svg{width:16px;height:16px;transform:translate(-3px,-3px);}
.remote-cursor-label{background:currentColor;color:white;font-size:10px;padding:3px 8px;border-radius:8px;margin-left:10px;margin-top:-2px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,0.15);white-space:nowrap;}

#tutorial-overlay{position:fixed;inset:0;background:rgba(247,245,240,0.85);z-index:5000;display:none;align-items:center;justify-content:center;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);}
#tutorial-overlay.visible{display:flex;}
.tut-box{background:var(--surface);border:1px solid var(--border);border-radius:24px;padding:40px;width:440px;max-width:90vw;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,0.08);position:relative;}
.tut-title{font-size:22px;font-weight:600;color:var(--text);margin-bottom:16px;}
.tut-desc{font-size:14px;color:var(--muted);margin-bottom:32px;line-height:1.6;}
.tut-dots{display:flex;justify-content:center;gap:8px;margin-bottom:32px;}
.tut-dot{width:8px;height:8px;border-radius:50%;background:var(--border);transition:background 0.3s;}
.tut-dot.active{background:var(--accent);}
.tut-btn-row{display:flex;justify-content:space-between;align-items:center;}
.tut-skip{background:transparent;border:none;color:var(--muted);cursor:pointer;font-family:inherit;font-size:13px;font-weight:500;transition:color 0.2s;}
.tut-skip:hover{color:var(--text);}
.tut-next{background:var(--accent);border:none;color:white;padding:12px 24px;border-radius:12px;font-weight:500;font-size:14px;cursor:pointer;font-family:inherit;box-shadow:0 8px 16px rgba(166,144,118,0.2);transition:all 0.2s;}
.tut-next:hover{background:var(--accent2);transform:translateY(-1px);box-shadow:0 12px 20px rgba(166,144,118,0.25);}

@media (max-width:768px){
  #top-bar-container{width:95%;top:12px;}
  #top-bar{overflow-x:auto;flex-wrap:nowrap;justify-content:flex-start;}
  .board-title-input{min-width:100px;font-size:13px;}
  .top-btn{padding:6px 10px;font-size:12px;}
  #input-bar-container{width:95%;bottom:16px;}
  #input-bar{width:100%;padding:10px 14px;border-radius:24px;}
  #suggestions-bar{bottom:80px;flex-wrap:nowrap;overflow-x:auto;width:100vw;padding:0 10px;justify-content:flex-start;}
  .suggestion-btn{flex-shrink:0;}
  #chat-window{width:calc(100vw - 32px);right:16px;height:400px;bottom:90px;left:16px!important;top:auto!important;}
}
</style>
</head>
<body>
<div id="app">
  <div id="top-bar-container">
    <div id="top-bar">
      <button class="top-btn" id="dash-btn" title="Dashboard">
        <svg viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg> Dash
      </button>
      <div style="width:1px;height:16px;background:rgba(0,0,0,0.1);margin:0 4px;flex-shrink:0;"></div>
      <input type="text" id="board-title-input" class="board-title-input" value="Untitled Canvas" />
      <div id="sync-indicator" title="Sync Status" style="display:flex;align-items:center;margin-left:4px;flex-shrink:0;"></div>
      <div style="width:1px;height:16px;background:rgba(0,0,0,0.1);margin:0 4px;flex-shrink:0;"></div>
      <button class="top-btn" id="study-btn">
        <svg viewBox="0 0 24 24"><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path></svg> Study
      </button>
      <button class="top-btn" id="auto-btn">
        <svg viewBox="0 0 24 24"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path></svg> Auto Layout
      </button>
      <button class="top-btn" id="group-btn">
        <svg viewBox="0 0 24 24"><path d="M4 4h6v6H4z"></path><path d="M14 4h6v6h-6z"></path><path d="M14 14h6v6h-6z"></path><path d="M4 14h6v6H4z"></path></svg> Group
      </button>
      <button class="top-btn" id="note-btn">
        <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg> Note
      </button>
      <button class="top-btn" id="brainstorm-btn">
        <svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg> Brainstorm
      </button>
      <button class="top-btn" id="browser-btn">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg> Browser
      </button>
      <button class="top-btn" id="settings-btn" title="Settings">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg> Settings
      </button>
      <button class="top-btn" id="chat-btn" style="display:none;">
        <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg> Chat
      </button>
      <button class="top-btn btn-guest" id="exit-guest-btn" style="display:none;" onclick="window.location.href='/auth/logout'">
        <svg viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg> Exit Guest
      </button>
      <div id="presence-bar"></div>
      <button class="top-btn btn-primary-glass" id="share-btn">Share</button>
      <div class="user-badge" id="user-badge">…</div>
    </div>
  </div>

  <div id="canvas-wrapper">
    <div id="canvas">
      <div id="lasso-box"></div>
      <svg id="link-layer"></svg>
    </div>
  </div>

  <div id="chat-window">
    <div class="chat-header">Team Chat <button id="chat-close">✕</button></div>
    <div class="chat-messages" id="chat-messages"></div>
    <div class="chat-input-wrap">
      <input type="text" id="chat-input" placeholder="Type a message...">
      <button id="chat-send"><svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg></button>
    </div>
  </div>

  <div id="suggestions-bar"></div>
  <div id="input-bar-container">
    <div id="input-bar">
      <div id="slash-popup"></div>
      <textarea id="prompt" placeholder="Ask anything. Type / for commands..." rows="1"></textarea>
      <button id="send-btn" title="Send (Enter)">↑</button>
    </div>
  </div>
</div>

<div id="zoom-controls">
  <button class="zoom-btn" id="recenter-btn" title="Recenter view" style="font-size:14px;">⊙</button>
  <button class="zoom-btn" id="zoom-in-btn">+</button>
  <div id="zoom-label">100%</div>
  <button class="zoom-btn" id="zoom-out-btn">−</button>
</div>

<div id="color-picker-popup">
  <div class="color-picker-title">Group Color</div>
  <div class="color-swatches" id="color-swatches"></div>
  <input class="color-picker-input" id="group-name-input" placeholder="Group name (optional)"/>
  <div class="color-picker-actions">
    <button class="color-picker-btn" id="color-confirm-btn" style="background:var(--accent);color:white;border:none;">Create Group</button>
    <button class="color-picker-btn" id="color-cancel-btn">Cancel</button>
  </div>
</div>

<div id="ctx-menu"></div>

<div id="settings-modal" class="modal-overlay">
  <div class="modal-box">
    <div class="modal-title">Settings & Customization</div>
    <div class="settings-row">
      <div class="settings-label">Color Theme</div>
      <select class="settings-select" id="theme-select">
        <option value="zen_light" selected>Zen Ivory (Light)</option>
        <option value="zen_dark">Espresso (Dark)</option>
        <option value="minimal">Minimal White</option>
      </select>
    </div>
    <div class="settings-row">
      <div class="settings-label">UI Scale</div>
      <select class="settings-select" id="ui-scale-select">
        <option value="0.85">Small</option>
        <option value="1.0" selected>Normal</option>
        <option value="1.15">Large</option>
      </select>
    </div>
    <div class="settings-row">
      <div class="settings-label">Zoom Speed</div>
      <select class="settings-select" id="zoom-speed-select">
        <option value="0.03">Very Slow</option>
        <option value="0.05">Slow</option>
        <option value="0.08" selected>Normal</option>
        <option value="0.12">Fast</option>
        <option value="0.2">Very Fast</option>
      </select>
    </div>
    <button class="modal-close" id="settings-close-btn">Done</button>
  </div>
</div>

<div id="share-modal" class="modal-overlay">
  <div class="modal-box">
    <div class="modal-title">Share Canvas</div>
    <div class="settings-row">
      <div class="settings-label">Share via link</div>
      <div class="share-input-wrap">
        <input type="text" id="share-link-input" class="modal-input" readonly/>
        <button class="modal-btn primary" id="share-copy-btn"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button>
      </div>
    </div>
    <div style="height:1px;background:var(--border);margin:8px 0;"></div>
    <div class="settings-row">
      <div class="settings-label">Add Collaborators (By Email)</div>
      <div class="share-input-wrap">
        <input type="email" id="invite-email" class="modal-input" placeholder="collab@example.com"/>
        <button class="modal-btn primary" id="invite-btn">Invite</button>
      </div>
    </div>
    <div class="settings-row" id="collab-wrap" style="display:none;">
      <div class="settings-label">Active Collaborators</div>
      <div class="collab-list" id="collab-list"></div>
    </div>
    <button class="modal-close" onclick="document.getElementById('share-modal').classList.remove('visible')">Done</button>
  </div>
</div>

<div id="dash-modal" class="modal-overlay">
  <div class="modal-box" style="width:520px;max-width:90vw;">
    <div class="modal-title">
      <span>Canvas Dashboard</span>
      <a href="/auth/logout" style="font-size:13px;color:var(--red);text-decoration:none;display:flex;align-items:center;gap:4px;font-weight:500;"><svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg> Sign Out</a>
    </div>
    <div class="settings-row">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="settings-label">My Canvases</div>
        <button class="modal-btn primary" id="new-canvas-btn" style="padding:8px 14px;font-size:12px;">+ New Canvas</button>
      </div>
      <div class="dash-list" id="my-dash-list">Loading...</div>
    </div>
    <div style="height:1px;background:var(--border);margin:8px 0;"></div>
    <div class="settings-row">
      <div class="settings-label">Shared with You</div>
      <div class="dash-list" id="shared-dash-list">Loading...</div>
    </div>
    <button class="modal-close" onclick="document.getElementById('dash-modal').classList.remove('visible')">Close</button>
  </div>
</div>

<div id="tutorial-overlay">
  <div class="tut-box">
    <div class="tut-title" id="tut-title">Welcome to SecondBrain</div>
    <div class="tut-desc" id="tut-desc">Let's take a quick 1-minute tour to help you get started with your peaceful new knowledge graph.</div>
    <div class="tut-dots" id="tut-dots">
      <div class="tut-dot active"></div><div class="tut-dot"></div><div class="tut-dot"></div><div class="tut-dot"></div>
    </div>
    <div class="tut-btn-row">
      <button class="tut-skip" onclick="endTutorial()">Skip</button>
      <button class="tut-next" id="tut-next-btn" onclick="nextTutorialStep()">Next Step →</button>
    </div>
  </div>
</div>

<script>
const IS_SHARED = true;
const SHARE_ID = '$$SHARE_ID$$';
const IS_OWNER = $$IS_OWNER$$;
const BOARD_TITLE = '$$BOARD_TITLE$$';

if(SHARE_ID === 'guest'){document.getElementById("exit-guest-btn").style.display="flex";}

let socket = null;
if(SHARE_ID !== 'guest'){socket = io();}
const remoteCursors = {};
const userLastPositions = {};
let currentUserEmail = "";

let nodes=[], links=[], groups=[];
let chatMessages=[];
let chatReplyNode=null;
let nextNodeId=1, nextLinkId=1, nextGroupId=1;
let draggingNode=null, dragOffset={x:0,y:0};
let draggingGroup=null, groupDragOffset={x:0,y:0}, groupDragNodeOffsets=[];
let resizingGroup=null, resizeStartX=0, resizeStartY=0, resizeStartW=0, resizeStartH=0;
let resizingNode=null, resizingTarget=null;
let mergeHintEl=null, mergeTargetNode=null;
let groupAddHintEl=null, groupAddTarget=null;
let lastNodeId=null, lastQuestionNodeId=null;
let isPanning=false, panStartX=0, panStartY=0, panScrollX=0, panScrollY=0, panMoved=false;
let isCtrlHeld=false, isShiftHeld=false, ctrlHighlightedNodes=[];
let undoStack=[], redoStack=[];
const MAX_HISTORY=80;
let slashActive=false, slashSelectedIndex=0;
let ctxTargetGroupId=null;
let ctxTargetNodeId=null;
let pendingGroupColor="#A69076";
let editingGroupId=null;
let currentScale=1.0;
const MIN_SCALE=0.1, MAX_SCALE=4.0;
let SCALE_STEP=0.08;
const CANVAS_W=8000, CANVAS_H=8000;
const ORIGIN_X=3000, ORIGIN_Y=3000;
let hasActiveContext=false;
let explicitlyDeselected=false;
let isLassoing=false;
let lassoStartX=0, lassoStartY=0;

const GROUP_COLORS=["#C2A878","#B5996D","#A39B8B","#D4C3A3","#B89A9A","#9E9B85","#C88A7A","#D9CFC1","#E3D7CB","#EBE5D9"];
const SLASH_COMMANDS=[
  {cmd:"/find",   desc:"Scroll to the most relevant node",  argHint:"/find "},
  {cmd:"/pinned", desc:"Highlight all pinned items",         argHint:"/pinned"},
  {cmd:"/delete", desc:"Delete: all | last | prompts",       argHint:"/delete "},
  {cmd:"/undo",   desc:"Undo last action",                   argHint:"/undo"},
  {cmd:"/redo",   desc:"Redo last undone action",            argHint:"/redo"},
];

const THEMES={
  zen_light:{bg:"#F7F5F0",canvas:"#F7F5F0",surface:"#FFFFFF",surface2:"#FBFAF8",border:"#E8E2D9",border2:"#DCD5CB",text:"#3E3A35",muted:"#8A837A",muted2:"#A39B8B",glassBg:"rgba(255,255,255,0.7)",gridColor:"rgba(166,144,118,0.15)"},
  zen_dark:{bg:"#1E1C1A",canvas:"#1E1C1A",surface:"#2A2825",surface2:"#36322F",border:"#45403C",border2:"#57524C",text:"#E8E2D9",muted:"#A39B8B",muted2:"#8A837A",glassBg:"rgba(42,40,37,0.75)",gridColor:"rgba(255,255,255,0.05)"},
  minimal:{bg:"#FFFFFF",canvas:"#FAFAFA",surface:"#FFFFFF",surface2:"#F4F4F4",border:"#E0E0E0",border2:"#CCCCCC",text:"#222222",muted:"#777777",muted2:"#999999",glassBg:"rgba(255,255,255,0.8)",gridColor:"rgba(0,0,0,0.05)"}
};

const syncSvgSynced=`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
const syncSvgSyncing=`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg>`;
const syncSvgOffline=`<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--red)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22.61 16.95A5 5 0 0 0 18 10h-1.26a8 8 0 0 0-7.05-6M5 5a8 8 0 0 0 4 15h9a5 5 0 0 0 1.7-.3M1 1l22 22"/></svg>`;
const iconGo=`<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"></line><polyline points="12 5 19 12 12 19"></polyline></svg>`;
const iconTrash=`<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>`;
const iconDoor=`<svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>`;

const canvasWrapper=document.getElementById("canvas-wrapper");
const canvas=document.getElementById("canvas");
const linkLayer=document.getElementById("link-layer");
const promptEl=document.getElementById("prompt");
const suggestionsBar=document.getElementById("suggestions-bar");
const slashPopup=document.getElementById("slash-popup");
const colorPickerPopup=document.getElementById("color-picker-popup");
const ctxMenu=document.getElementById("ctx-menu");
const lassoBox=document.getElementById("lasso-box");

promptEl.addEventListener("input",function(){this.style.height="auto";this.style.height=(this.scrollHeight)+"px";});

let syncTimeout;
function showSyncing(){if(SHARE_ID==='guest')return;const ind=document.getElementById("sync-indicator");if(ind){ind.innerHTML=syncSvgSyncing;const svg=ind.querySelector("svg");if(svg)svg.style.animation="spin 1s linear infinite";}}
function showSynced(){if(SHARE_ID==='guest'){document.getElementById("sync-indicator").innerHTML=syncSvgOffline;return;}const ind=document.getElementById("sync-indicator");if(ind)ind.innerHTML=syncSvgSynced;}
const styleSheet=document.createElement("style");styleSheet.innerText="@keyframes spin{100%{transform:rotate(360deg);}}";document.head.appendChild(styleSheet);

const titleInput=document.getElementById("board-title-input");
titleInput.value=BOARD_TITLE;
titleInput.addEventListener("change",async()=>{
  if(SHARE_ID==='guest'){titleInput.value=BOARD_TITLE;return;}
  if(!IS_OWNER){alert("Only the owner can rename the canvas.");titleInput.value=BOARD_TITLE;return;}
  const newTitle=titleInput.value.trim()||"Untitled Canvas";
  try{await fetch("/api/board/title",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({share_id:SHARE_ID,title:newTitle})});if(socket)socket.emit("title_update",{room:SHARE_ID,title:newTitle});}catch(e){}
});

fetch("/auth/me").then(r=>r.json()).then(d=>{
  const badge=document.getElementById("user-badge");
  if(d.authenticated){currentUserEmail=d.email;badge.innerHTML=d.email;}
  else{badge.innerHTML='<a href="/login">Sign in</a>';}
});

function applyTheme(name){
  const t=THEMES[name]||THEMES.zen_light;const r=document.documentElement;
  r.style.setProperty('--bg',t.bg);r.style.setProperty('--canvas-bg',t.canvas);r.style.setProperty('--surface',t.surface);r.style.setProperty('--surface2',t.surface2);
  r.style.setProperty('--border',t.border);r.style.setProperty('--border2',t.border2);r.style.setProperty('--text',t.text);r.style.setProperty('--muted',t.muted);r.style.setProperty('--muted2',t.muted2);
  r.style.setProperty('--glass-bg',t.glassBg);r.style.setProperty('--grid-color',t.gridColor);
}
function applyUiScale(scale){document.documentElement.style.setProperty('--ui-scale',scale);}

document.getElementById("settings-btn").onclick=()=>document.getElementById("settings-modal").classList.add("visible");
document.getElementById("settings-close-btn").onclick=()=>{document.getElementById("settings-modal").classList.remove("visible");saveSettings();};
document.querySelectorAll(".modal-overlay").forEach(m=>m.addEventListener("click",e=>{if(e.target===m){m.classList.remove("visible");if(m.id==="settings-modal")saveSettings();}}));
document.getElementById("theme-select").addEventListener("change",e=>applyTheme(e.target.value));
document.getElementById("ui-scale-select").addEventListener("change",e=>applyUiScale(e.target.value));

async function saveSettings(){
  if(SHARE_ID==='guest')return;
  const settings={zoomSpeed:document.getElementById("zoom-speed-select").value,theme:document.getElementById("theme-select").value,uiScale:document.getElementById("ui-scale-select").value};
  SCALE_STEP=parseFloat(settings.zoomSpeed);
  try{await fetch("/save_settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(settings)});}catch(e){}
}
async function loadSettings(){
  if(SHARE_ID==='guest')return;
  try{const r=await fetch("/load_settings");if(r.ok){const data=await r.json();if(data.zoomSpeed){document.getElementById("zoom-speed-select").value=data.zoomSpeed;SCALE_STEP=parseFloat(data.zoomSpeed);}if(data.theme){document.getElementById("theme-select").value=data.theme;applyTheme(data.theme);}if(data.uiScale){document.getElementById("ui-scale-select").value=data.uiScale;applyUiScale(data.uiScale);}}}catch(e){}
}

let chatDrag=false;let chatStart={x:0,y:0};const chatWin=document.getElementById("chat-window");
document.querySelector(".chat-header").addEventListener("mousedown",e=>{if(e.target.id==='chat-close')return;chatDrag=true;chatStart={x:e.clientX-chatWin.offsetLeft,y:e.clientY-chatWin.offsetTop};});
document.addEventListener("mousemove",e=>{if(!chatDrag)return;chatWin.style.left=(e.clientX-chatStart.x)+"px";chatWin.style.top=(e.clientY-chatStart.y)+"px";chatWin.style.bottom="auto";chatWin.style.right="auto";});
document.addEventListener("mouseup",()=>{if(chatDrag){chatDrag=false;localStorage.setItem("chat_pos",JSON.stringify({left:chatWin.style.left,top:chatWin.style.top}));}});
document.getElementById("chat-btn").onclick=()=>{chatWin.classList.toggle("visible");setTimeout(renderChat,50);};
document.getElementById("chat-close").onclick=()=>chatWin.classList.remove("visible");

function renderChat(){
  const box=document.getElementById("chat-messages");box.innerHTML="";
  chatMessages.forEach(m=>{
    const div=document.createElement("div");div.className="chat-msg "+(m.sender===currentUserEmail?"sent":"received");
    let refHtml=m.nodeRef?`<div class="node-ref" onclick="smartRecenter(true,${m.nodeRefX},${m.nodeRefY});const el=getNodeEl(${m.nodeRef});if(el){el.classList.add('find-focus');setTimeout(()=>el.classList.remove('find-focus'),3000);}">↳ Replying to Node</div><br>`:"";
    let delHtml=(m.sender===currentUserEmail||IS_OWNER)?`<button class="delete-msg" onclick="deleteChatMessage(${m.id})">✕</button>`:"";
    let timeStr=new Date(m.time||Date.now()).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    div.innerHTML=`${delHtml}<div class="sender">${m.sender===currentUserEmail?"You":m.sender}</div>${refHtml}${m.text}<span class="timestamp">${timeStr}</span>`;
    box.appendChild(div);
  });
  box.scrollTop=box.scrollHeight;
}
function sendChat(){
  const inp=document.getElementById("chat-input");const text=inp.value.trim();if(!text)return;
  const msg={id:Date.now(),sender:currentUserEmail||"Guest",text:text,time:Date.now(),nodeRef:chatReplyNode?.id,nodeRefX:chatReplyNode?.x,nodeRefY:chatReplyNode?.y};
  chatMessages.push(msg);renderChat();if(socket)socket.emit("chat_message",{room:SHARE_ID,msg:msg});saveGraph();inp.value="";chatReplyNode=null;inp.placeholder="Type a message...";
}
document.getElementById("chat-send").onclick=sendChat;
document.getElementById("chat-input").onkeydown=e=>{if(e.key==="Enter")sendChat();};
window.deleteChatMessage=function(id){chatMessages=chatMessages.filter(m=>m.id!==id);renderChat();if(socket)socket.emit("delete_chat_message",{room:SHARE_ID,id:id});saveGraph();};

if(socket){
  socket.on("chat_message",data=>{chatMessages.push(data.msg);renderChat();if(!chatWin.classList.contains("visible")){const btn=document.getElementById("chat-btn");btn.style.color="var(--accent)";setTimeout(()=>{btn.style.color="var(--text)";},2000);}});
  socket.on("delete_chat_message",data=>{chatMessages=chatMessages.filter(m=>m.id!==data.id);renderChat();});
}

function initCanvas(){canvas.style.width=CANVAS_W+"px";canvas.style.height=CANVAS_H+"px";linkLayer.setAttribute("width",CANVAS_W);linkLayer.setAttribute("height",CANVAS_H);canvasWrapper.scrollLeft=(ORIGIN_X-canvasWrapper.clientWidth/2)*currentScale;canvasWrapper.scrollTop=(ORIGIN_Y-canvasWrapper.clientHeight/2)*currentScale;}
function applyZoom(newScale,pivotClientX,pivotClientY){
  newScale=Math.max(MIN_SCALE,Math.min(MAX_SCALE,newScale));if(Math.abs(newScale-currentScale)<0.001)return;
  const px=pivotClientX!=null?pivotClientX:canvasWrapper.clientWidth/2;const py=pivotClientY!=null?pivotClientY:canvasWrapper.clientHeight/2;
  const worldX=(canvasWrapper.scrollLeft+px)/currentScale;const worldY=(canvasWrapper.scrollTop+py)/currentScale;
  currentScale=newScale;canvas.style.transform=`scale(${currentScale})`;canvas.style.transformOrigin="0 0";canvas.style.width=CANVAS_W+"px";canvas.style.height=CANVAS_H+"px";
  updateScrollSpacer();canvasWrapper.scrollLeft=worldX*currentScale-px;canvasWrapper.scrollTop=worldY*currentScale-py;
  redrawLinks();document.getElementById("zoom-label").textContent=Math.round(currentScale*100)+"%";
}
let spacerEl=null;
function updateScrollSpacer(){if(!spacerEl){spacerEl=document.createElement("div");spacerEl.style.cssText="position:absolute;top:0;left:0;pointer-events:none;";canvasWrapper.appendChild(spacerEl);}spacerEl.style.width=(CANVAS_W*currentScale)+"px";spacerEl.style.height=(CANVAS_H*currentScale)+"px";}
function initZoom(){canvas.style.transform=`scale(${currentScale})`;canvas.style.transformOrigin="0 0";canvas.style.position="absolute";canvas.style.top="0";canvas.style.left="0";linkLayer.setAttribute("width",CANVAS_W);linkLayer.setAttribute("height",CANVAS_H);updateScrollSpacer();}

document.getElementById("zoom-in-btn").onclick=()=>applyZoom(currentScale+SCALE_STEP);
document.getElementById("zoom-out-btn").onclick=()=>applyZoom(currentScale-SCALE_STEP);
canvasWrapper.addEventListener("wheel",e=>{if(e.ctrlKey||e.metaKey){e.preventDefault();const delta=e.deltaY>0?-SCALE_STEP:SCALE_STEP;const rect=canvasWrapper.getBoundingClientRect();applyZoom(currentScale+delta,e.clientX-rect.left,e.clientY-rect.top);}},{passive:false});

function smartRecenter(animate=true,customX=null,customY=null,customZoom=null){
  if(customX!==null&&customY!==null){applyZoom(customZoom!==null?customZoom:currentScale,canvasWrapper.clientWidth/2,canvasWrapper.clientHeight/2);setTimeout(()=>{canvasWrapper.scrollTo({left:customX*currentScale-canvasWrapper.clientWidth/2,top:customY*currentScale-canvasWrapper.clientHeight/2,behavior:animate?"smooth":"instant"});},30);return;}
  const pts=[];
  nodes.forEach(n=>{if(n.groupId!==undefined){const g=groups.find(x=>x.id===n.groupId);if(g&&g.collapsed)return;}const el=getNodeEl(n.id);const w=el?el.offsetWidth:100,h=el?el.offsetHeight:40;pts.push({x:n.x,y:n.y,w,h});});
  groups.forEach(g=>{if(g.collapsed){const cx=g.collapsedX||ORIGIN_X,cy=g.collapsedY||ORIGIN_Y;const cw=g.collapsedW||160,ch=g.collapsedH||60;pts.push({x:cx,y:cy,w:cw,h:ch});}});
  if(!pts.length){canvasWrapper.scrollTo({left:(ORIGIN_X-canvasWrapper.clientWidth/2)*currentScale,top:(ORIGIN_Y-canvasWrapper.clientHeight/2)*currentScale,behavior:animate?"smooth":"instant"});return;}
  const xs1=pts.map(p=>p.x),xs2=pts.map(p=>p.x+(p.w||100));const ys1=pts.map(p=>p.y),ys2=pts.map(p=>p.y+(p.h||40));
  const minX=Math.min(...xs1),maxX=Math.max(...xs2);const minY=Math.min(...ys1),maxY=Math.max(...ys2);const midX=(minX+maxX)/2,midY=(minY+maxY)/2;
  const PAD=80;const contentW=maxX-minX+PAD*2,contentH=maxY-minY+PAD*2;const scaleX=canvasWrapper.clientWidth/contentW,scaleY=canvasWrapper.clientHeight/contentH;
  const fitScale=Math.min(Math.max(Math.min(scaleX,scaleY)*0.88,MIN_SCALE),MAX_SCALE);
  applyZoom(fitScale,canvasWrapper.clientWidth/2,canvasWrapper.clientHeight/2);setTimeout(()=>{canvasWrapper.scrollTo({left:midX*fitScale-canvasWrapper.clientWidth/2,top:midY*fitScale-canvasWrapper.clientHeight/2,behavior:animate?"smooth":"instant"});},30);
}
document.getElementById("recenter-btn").onclick=()=>smartRecenter();

function zoomToGroup(gid,animate=true){const g=groups.find(x=>x.id===gid);if(!g)return;const bounds=getGroupBounds(g);if(!bounds)return;const padding=100;const cx=(bounds.minX+bounds.maxX)/2;const cy=(bounds.minY+bounds.maxY)/2;const w=bounds.maxX-bounds.minX+padding*2;const h=bounds.maxY-bounds.minY+padding*2;const scaleX=canvasWrapper.clientWidth/w;const scaleY=canvasWrapper.clientHeight/h;const fitScale=Math.min(Math.max(Math.min(scaleX,scaleY)*0.9,MIN_SCALE),MAX_SCALE);applyZoom(fitScale,canvasWrapper.clientWidth/2,canvasWrapper.clientHeight/2);setTimeout(()=>{canvasWrapper.scrollTo({left:cx*fitScale-canvasWrapper.clientWidth/2,top:cy*fitScale-canvasWrapper.clientHeight/2,behavior:animate?"smooth":"instant"});},30);}
function clientToCanvas(cx,cy){const rect=canvasWrapper.getBoundingClientRect();return{x:(cx-rect.left+canvasWrapper.scrollLeft)/currentScale,y:(cy-rect.top+canvasWrapper.scrollTop)/currentScale};}

const getNodeEl=id=>canvas.querySelector('.node[data-id="'+id+'"]');
const getGroupEl=id=>canvas.querySelector('.group-hull[data-gid="'+id+'"]');
const getSelectedNodes=()=>nodes.filter(n=>n.selected);
function applyDimClass(el,dim){for(let i=0;i<=4;i++)el.classList.remove("dim-"+i);el.classList.add("dim-"+dim);}

function captureSnapshot(){return JSON.stringify({nodes:nodes.map(n=>({...n,meta:{...n.meta}})),links:links.map(l=>({...l})),groups:groups.map(g=>({...g,nodeIds:[...g.nodeIds]})),nextNodeId,nextLinkId,nextGroupId});}
function pushUndo(){undoStack.push(captureSnapshot());if(undoStack.length>MAX_HISTORY)undoStack.shift();redoStack=[];}
function restoreSnapshot(snap){const s=JSON.parse(snap);canvas.querySelectorAll(".node,.group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());nodes=s.nodes;links=s.links;groups=s.groups||[];nextNodeId=s.nextNodeId;nextLinkId=s.nextLinkId;nextGroupId=s.nextGroupId||1;nodes.forEach(n=>{if(!n.meta)n.meta={};createNodeElement(n);});redrawLinks();redrawGroups();saveGraph();}
function undo(){if(!undoStack.length)return;redoStack.push(captureSnapshot());restoreSnapshot(undoStack.pop());}
function redo(){if(!redoStack.length)return;undoStack.push(captureSnapshot());restoreSnapshot(redoStack.pop());}

const isMac=navigator.platform.toUpperCase().includes("MAC");
document.addEventListener("keydown",e=>{
  if(e.key==="Shift")isShiftHeld=true;
  const mod=isMac?e.metaKey:e.ctrlKey;
  if(e.ctrlKey||e.metaKey)isCtrlHeld=true;
  if(mod&&e.key==="z"&&!e.shiftKey){e.preventDefault();undo();return;}
  if(mod&&(e.key==="y"||(e.key==="z"&&e.shiftKey))){e.preventDefault();redo();return;}
  const active=document.activeElement;
  const inInput=active===promptEl||active.tagName==="INPUT"||active.tagName==="TEXTAREA";
  if(!inInput){
    if(e.key==="Delete"||e.key==="Backspace"){e.preventDefault();const sel=getSelectedNodes();if(sel.length>0){sel.forEach(n=>deleteNode(n.id));}return;}
    if((e.key==="l"||e.key==="L")&&!mod){e.preventDefault();linkSelectedNodes();return;}
    if((e.key==="s"||e.key==="S")&&!mod){e.preventDefault();splitSelectedLinks();return;}
    if((e.key==="g"||e.key==="G")&&!mod){e.preventDefault();triggerGroupUI();return;}
  }
});
document.addEventListener("keyup",e=>{if(e.key==="Shift")isShiftHeld=false;if(!e.ctrlKey&&!e.metaKey){isCtrlHeld=false;clearCtrlHighlights();}});

function getDirectNeighborAnswers(nodeId){const nids=new Set();links.forEach(l=>{if(l.sourceId===nodeId)nids.add(l.targetId);if(l.targetId===nodeId)nids.add(l.sourceId);});return nodes.filter(n=>nids.has(n.id)&&n.type==="answer");}
function getTreeNodes(nodeId){let q=[nodeId];let vis=new Set([nodeId]);while(q.length){let curr=q.shift();links.forEach(l=>{if(l.sourceId===curr&&!vis.has(l.targetId)){vis.add(l.targetId);q.push(l.targetId);}if(l.targetId===curr&&!vis.has(l.sourceId)){vis.add(l.sourceId);q.push(l.sourceId);}});}return nodes.filter(n=>vis.has(n.id));}
function clearCtrlHighlights(){canvas.querySelectorAll(".node.ctrl-highlight").forEach(el=>el.classList.remove("ctrl-highlight"));ctrlHighlightedNodes=[];}
function applyCtrlHighlight(node){clearCtrlHighlights();ctrlHighlightedNodes=getDirectNeighborAnswers(node.id);ctrlHighlightedNodes.forEach(n=>{const el=getNodeEl(n.id);if(el)el.classList.add("ctrl-highlight");});}
function applyTreeHighlight(node){clearCtrlHighlights();ctrlHighlightedNodes=getTreeNodes(node.id);ctrlHighlightedNodes.forEach(n=>{const el=getNodeEl(n.id);if(el)el.classList.add("ctrl-highlight");});}
function linkSelectedNodes(){const sel=getSelectedNodes();if(sel.length<2)return;pushUndo();for(let i=0;i<sel.length-1;i++)addLink(sel[i].id,sel[i+1].id);redrawLinks();saveGraph();}
function splitSelectedLinks(){const sel=getSelectedNodes();if(sel.length<2)return;pushUndo();const selIds=new Set(sel.map(n=>n.id));links=links.filter(l=>!(selIds.has(l.sourceId)&&selIds.has(l.targetId)));redrawLinks();saveGraph();}

function getGroupBounds(group){const memberNodes=nodes.filter(n=>group.nodeIds.includes(n.id));if(!memberNodes.length)return null;let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;memberNodes.forEach(n=>{const el=getNodeEl(n.id);const w=el?el.offsetWidth:80,h=el?el.offsetHeight:40;minX=Math.min(minX,n.x);minY=Math.min(minY,n.y);maxX=Math.max(maxX,n.x+w);maxY=Math.max(maxY,n.y+h);});return{minX,minY,maxX,maxY};}

function createGroup(nodeIds,color,name){pushUndo();const id=nextGroupId++;const group={id,name:name||("Group "+id),color,nodeIds:[...nodeIds],pinned:false,collapsed:false,collapsedW:160,collapsedH:60,collapsedX:null,collapsedY:null};groups.push(group);nodeIds.forEach(nid=>{const n=nodes.find(x=>x.id===nid);if(n)n.groupId=id;const el=getNodeEl(nid);if(el){let b=el.querySelector(".group-badge");if(!b){b=document.createElement("div");b.className="group-badge";el.appendChild(b);}b.style.background=color;}});redrawGroups();saveGraph();}
function deleteGroup(gid){pushUndo();const g=groups.find(x=>x.id===gid);if(!g)return;if(g.collapsed)expandGroup(gid,true);g.nodeIds.forEach(nid=>{const n=nodes.find(x=>x.id===nid);if(n)delete n.groupId;const el=getNodeEl(nid);if(el){const b=el.querySelector(".group-badge");if(b)b.remove();}});groups=groups.filter(x=>x.id!==gid);redrawGroups();saveGraph();}
function addNodeToGroup(nodeId,gid){const g=groups.find(x=>x.id===gid);if(!g||g.nodeIds.includes(nodeId))return;pushUndo();g.nodeIds.push(nodeId);const n=nodes.find(x=>x.id===nodeId);if(n)n.groupId=gid;const el=getNodeEl(nodeId);if(el){let b=el.querySelector(".group-badge");if(!b){b=document.createElement("div");b.className="group-badge";el.appendChild(b);}b.style.background=g.color;}redrawGroups();saveGraph();}
function collapseGroup(gid,emitEvent=true){const g=groups.find(x=>x.id===gid);if(!g||g.collapsed)return;if(emitEvent&&socket)socket.emit("group_action",{room:SHARE_ID,action:"collapse",gid});pushUndo();g.savedPositions={};g.nodeIds.forEach(nid=>{const n=nodes.find(x=>x.id===nid);if(n)g.savedPositions[nid]={x:n.x,y:n.y};});const bounds=getGroupBounds(g);if(bounds){const cx=(bounds.minX+bounds.maxX)/2-((g.collapsedW||160)/2);const cy=(bounds.minY+bounds.maxY)/2-((g.collapsedH||60)/2);g.collapsedX=cx;g.collapsedY=cy;}g.collapsed=true;g.nodeIds.forEach(nid=>{const el=getNodeEl(nid);if(el)el.style.display="none";});redrawLinks();redrawGroups();saveGraph();if(emitEvent)setTimeout(()=>smartRecenter(true),80);}
function expandGroup(gid,skipSave,emitEvent=true){const g=groups.find(x=>x.id===gid);if(!g||!g.collapsed)return;if(emitEvent&&socket)socket.emit("group_action",{room:SHARE_ID,action:"expand",gid});g.collapsed=false;if(g.savedPositions){g.nodeIds.forEach(nid=>{const n=nodes.find(x=>x.id===nid);if(n&&g.savedPositions[nid]){n.x=g.savedPositions[nid].x;n.y=g.savedPositions[nid].y;}const el=getNodeEl(nid);if(el){el.style.display="";if(n){el.style.left=n.x+"px";el.style.top=n.y+"px";}el.style.opacity="0";el.style.transform="scale(0.85)";el.style.transition="opacity .25s ease,transform .25s ease";requestAnimationFrame(()=>{el.style.opacity="";el.style.transform="";setTimeout(()=>el.style.transition="",300);});}});delete g.savedPositions;}else{g.nodeIds.forEach(nid=>{const el=getNodeEl(nid);if(el)el.style.display="";});}redrawLinks();redrawGroups();if(!skipSave)saveGraph();if(emitEvent)zoomToGroup(gid,true);}

function redrawGroups(){
  canvas.querySelectorAll(".group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());
  groups.forEach(group=>{
    if(group.collapsed){
      const hull=document.createElement("div");hull.className="group-hull collapsed"+(group.pinned?" pinned-highlight":"");hull.dataset.gid=group.id;
      const cx=group.collapsedX||ORIGIN_X,cy=group.collapsedY||ORIGIN_Y;const cw=group.collapsedW||160,ch=group.collapsedH||60;
      hull.style.cssText=`left:${cx}px;top:${cy}px;width:${cw}px;height:${ch}px;border-color:${group.color};background:${group.color};box-shadow:0 8px 32px ${group.color}22;`;
      hull.addEventListener("mousedown",e=>{if(e.button!==0)return;if(e.target.classList.contains("group-resize-handle"))return;e.stopPropagation();draggingGroup=group;const cc=clientToCanvas(e.clientX,e.clientY);groupDragOffset={x:cc.x-cx,y:cc.y-cy};groupDragNodeOffsets=[];});
      const rh=document.createElement("div");rh.className="group-resize-handle";hull.appendChild(rh);
      rh.addEventListener("mousedown",e=>{e.stopPropagation();e.preventDefault();resizingGroup=group;resizeStartX=e.clientX;resizeStartY=e.clientY;resizeStartW=group.collapsedW;resizeStartH=group.collapsedH;});
      hull.addEventListener("dblclick",e=>{e.stopPropagation();expandGroup(group.id);});
      hull.addEventListener("contextmenu",e=>{e.preventDefault();e.stopPropagation();ctxTargetGroupId=group.id;ctxTargetNodeId=null;buildCtxMenu("group",e.clientX,e.clientY);});
      canvas.appendChild(hull);
      const label=document.createElement("div");label.className="group-label collapsed-label";label.textContent=group.name+" ("+group.nodeIds.length+")";label.style.cssText=`left:${cx+12}px;top:${cy+ch/2-7}px;color:${group.color};font-size:12px;`;label.addEventListener("dblclick",()=>expandGroup(group.id));canvas.appendChild(label);return;
    }
    const memberNodes=nodes.filter(n=>group.nodeIds.includes(n.id));if(!memberNodes.length)return;
    const bounds=getGroupBounds(group);if(!bounds)return;const{minX,minY,maxX,maxY}=bounds;const pad=24;
    const hull=document.createElement("div");hull.className="group-hull"+(group.pinned?" pinned-highlight":"");hull.dataset.gid=group.id;
    hull.style.cssText=`left:${minX-pad}px;top:${minY-pad}px;width:${maxX-minX+pad*2}px;height:${maxY-minY+pad*2}px;border-color:${group.color};background:${group.color};`;
    hull.addEventListener("mousedown",e=>{if(e.button!==0)return;e.stopPropagation();draggingGroup=group;const cc=clientToCanvas(e.clientX,e.clientY);groupDragOffset={x:cc.x-(minX-pad),y:cc.y-(minY-pad)};groupDragNodeOffsets=group.nodeIds.map(nid=>{const n=nodes.find(x=>x.id===nid);return n?{id:nid,dx:n.x-(minX-pad),dy:n.y-(minY-pad)}:{id:nid,dx:0,dy:0};});});
    hull.addEventListener("click",e=>{if(e.shiftKey){e.stopPropagation();deleteGroup(group.id);}});
    hull.addEventListener("contextmenu",e=>{e.preventDefault();e.stopPropagation();ctxTargetGroupId=group.id;ctxTargetNodeId=null;buildCtxMenu("group",e.clientX,e.clientY);});
    canvas.insertBefore(hull,canvas.firstChild);
    const label=document.createElement("div");label.className="group-label";label.textContent=group.name;label.style.cssText=`left:${minX-pad+12}px;top:${minY-pad-22}px;color:${group.color};`;canvas.insertBefore(label,canvas.firstChild);
  });
}

function buildCtxMenu(type,x,y){
  ctxMenu.innerHTML="";
  if(type==="group"){
    const g=groups.find(g=>g.id===ctxTargetGroupId);if(!g)return;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-snap">Snap to group</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-pin-group">${g.pinned?'Unpin':'Pin'} group</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-rename">Rename group</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-recolor">Change color</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-collapse-toggle">${g.collapsed?'Expand group':'Collapse group'}</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item danger" id="ctx-delete-group">Delete group</div>`;
  }else if(type==="node"){
    const n=nodes.find(n=>n.id===ctxTargetNodeId);if(!n)return;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-snap">Snap to node</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-pin-node">${n.meta?.pinned?'Unpin':'Pin'} node</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item" id="ctx-reply-chat">Reply in Chat</div>`;
    ctxMenu.innerHTML+=`<div class="ctx-item danger" id="ctx-delete-node">Delete node</div>`;
  }
  ctxMenu.style.cssText=`left:${x}px;top:${y}px;`;ctxMenu.classList.add("visible");
  const snapBtn=document.getElementById("ctx-snap");
  if(snapBtn)snapBtn.onclick=()=>{if(ctxTargetGroupId!==null)zoomToGroup(ctxTargetGroupId);if(ctxTargetNodeId!==null){const n=nodes.find(x=>x.id===ctxTargetNodeId);if(n)smartRecenter(true,n.x,n.y,1.0);}ctxMenu.classList.remove("visible");};
  const pinGrpBtn=document.getElementById("ctx-pin-group");
  if(pinGrpBtn)pinGrpBtn.onclick=()=>{if(ctxTargetGroupId!==null){const g=groups.find(x=>x.id===ctxTargetGroupId);if(g){g.pinned=!g.pinned;redrawGroups();saveGraph();}}ctxMenu.classList.remove("visible");};
  const pinNodeBtn=document.getElementById("ctx-pin-node");
  if(pinNodeBtn)pinNodeBtn.onclick=()=>{if(ctxTargetNodeId!==null){const n=nodes.find(x=>x.id===ctxTargetNodeId);if(n){n.meta.pinned=!n.meta.pinned;redrawGroups();const el=getNodeEl(n.id);if(n.meta.pinned)el?.classList.add("pinned-highlight");else el?.classList.remove("pinned-highlight");saveGraph();}}ctxMenu.classList.remove("visible");};
  const replyChatBtn=document.getElementById("ctx-reply-chat");
  if(replyChatBtn)replyChatBtn.onclick=()=>{if(ctxTargetNodeId!==null){chatReplyNode=nodes.find(n=>n.id===ctxTargetNodeId);document.getElementById("chat-window").classList.add("visible");document.getElementById("chat-input").placeholder=`Replying to node...`;document.getElementById("chat-input").focus();}ctxMenu.classList.remove("visible");};
  const renBtn=document.getElementById("ctx-rename");
  if(renBtn)renBtn.onclick=()=>{if(ctxTargetGroupId===null)return;const g=groups.find(x=>x.id===ctxTargetGroupId);if(!g)return;const n=prompt("Group name:",g.name);if(n!==null){g.name=n.trim()||g.name;redrawGroups();saveGraph();}ctxMenu.classList.remove("visible");};
  const recBtn=document.getElementById("ctx-recolor");
  if(recBtn)recBtn.onclick=()=>{if(ctxTargetGroupId===null)return;editingGroupId=ctxTargetGroupId;const g=groups.find(x=>x.id===ctxTargetGroupId);if(g){pendingGroupColor=g.color;document.getElementById("group-name-input").value=g.name;}buildColorSwatches();colorPickerPopup.style.cssText=`top:${parseInt(ctxMenu.style.top)+30}px;left:${ctxMenu.style.left};`;colorPickerPopup.classList.add("visible");document.getElementById("color-confirm-btn").textContent="Update Group";ctxMenu.classList.remove("visible");};
  const colBtn=document.getElementById("ctx-collapse-toggle");
  if(colBtn)colBtn.onclick=()=>{if(ctxTargetGroupId===null)return;const g=groups.find(x=>x.id===ctxTargetGroupId);if(!g)return;if(g.collapsed)expandGroup(g.id);else collapseGroup(g.id);ctxMenu.classList.remove("visible");};
  const delGrpBtn=document.getElementById("ctx-delete-group");
  if(delGrpBtn)delGrpBtn.onclick=()=>{if(ctxTargetGroupId!==null)deleteGroup(ctxTargetGroupId);ctxMenu.classList.remove("visible");};
  const delNodeBtn=document.getElementById("ctx-delete-node");
  if(delNodeBtn)delNodeBtn.onclick=()=>{if(ctxTargetNodeId!==null){const target=nodes.find(n=>n.id===ctxTargetNodeId);if(target&&target.selected){getSelectedNodes().forEach(n=>deleteNode(n.id));}else{deleteNode(ctxTargetNodeId);}}ctxMenu.classList.remove("visible");};
}
document.addEventListener("click",e=>{if(!ctxMenu.contains(e.target))ctxMenu.classList.remove("visible");});
document.addEventListener("contextmenu",e=>{if(!e.target.closest(".group-hull")&&!e.target.closest(".node"))ctxMenu.classList.remove("visible");});

function buildColorSwatches(){const c=document.getElementById("color-swatches");c.innerHTML="";GROUP_COLORS.forEach(col=>{const sw=document.createElement("div");sw.className="color-swatch"+(col===pendingGroupColor?" active":"");sw.style.background=col;sw.onclick=()=>{pendingGroupColor=col;c.querySelectorAll(".color-swatch").forEach(el=>el.classList.remove("active"));sw.classList.add("active");};c.appendChild(sw);});}
document.getElementById("color-confirm-btn").onclick=()=>{const name=document.getElementById("group-name-input").value.trim();if(editingGroupId!==null){const g=groups.find(x=>x.id===editingGroupId);if(g){g.color=pendingGroupColor;if(name)g.name=name;g.nodeIds.forEach(nid=>{const el=getNodeEl(nid);if(el){const b=el.querySelector(".group-badge");if(b)b.style.background=pendingGroupColor;}});redrawGroups();saveGraph();}editingGroupId=null;document.getElementById("color-confirm-btn").textContent="Create Group";}else{const sel=getSelectedNodes();if(!sel.length){colorPickerPopup.classList.remove("visible");return;}createGroup(sel.map(n=>n.id),pendingGroupColor,name||undefined);}colorPickerPopup.classList.remove("visible");document.getElementById("group-name-input").value="";};
document.getElementById("color-cancel-btn").onclick=()=>{colorPickerPopup.classList.remove("visible");editingGroupId=null;document.getElementById("color-confirm-btn").textContent="Create Group";};

function triggerGroupUI(){const sel=getSelectedNodes();if(!sel.length){alert("Select at least one node to group.");return;}editingGroupId=null;document.getElementById("color-confirm-btn").textContent="Create Group";buildColorSwatches();let minX=Infinity,minY=Infinity;sel.forEach(n=>{minX=Math.min(minX,n.x);minY=Math.min(minY,n.y);});const rect=canvasWrapper.getBoundingClientRect();const screenX=(minX*currentScale)-canvasWrapper.scrollLeft+rect.left;const screenY=(minY*currentScale)-canvasWrapper.scrollTop+rect.top-120;colorPickerPopup.style.cssText=`top:${Math.max(20,screenY)}px;left:${Math.max(20,screenX)}px;`;colorPickerPopup.classList.add("visible");}
document.getElementById("group-btn").onclick=triggerGroupUI;

function createTimerContent(node){
  const wrap=document.createElement("div");wrap.className="timer-ring";const svgNS="http://www.w3.org/2000/svg";
  const svg=document.createElementNS(svgNS,"svg");svg.setAttribute("viewBox","0 0 40 40");const defs=document.createElementNS(svgNS,"defs");
  const grad=document.createElementNS(svgNS,"linearGradient");grad.setAttribute("id","timerGradient");grad.setAttribute("x1","0%");grad.setAttribute("y1","0%");grad.setAttribute("x2","100%");grad.setAttribute("y2","0%");
  [[0,"#C2A878"],[50,"#A69076"],[100,"#8C7761"]].forEach(([off,col])=>{const s=document.createElementNS(svgNS,"stop");s.setAttribute("offset",off+"%");s.setAttribute("stop-color",col);grad.appendChild(s);});
  defs.appendChild(grad);svg.appendChild(defs);
  const bg=document.createElementNS(svgNS,"circle");bg.setAttribute("class","ring-bg");bg.setAttribute("cx","20");bg.setAttribute("cy","20");bg.setAttribute("r","16");
  const prog=document.createElementNS(svgNS,"circle");prog.setAttribute("class","ring-progress");prog.setAttribute("cx","20");prog.setAttribute("cy","20");prog.setAttribute("r","16");
  const circ=2*Math.PI*16;prog.style.strokeDasharray=circ;prog.style.strokeDashoffset=circ;svg.appendChild(bg);svg.appendChild(prog);
  const txt=document.createElement("div");txt.className="timer-text";txt.textContent=formatTime(node.meta.seconds||0);
  wrap.appendChild(svg);wrap.appendChild(txt);node.meta._circumference=circ;node.meta._progressEl=prog;node.meta._textEl=txt;return wrap;
}
function formatTime(s){s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;if(h>0)return String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(sec).padStart(2,"0");return String(m).padStart(2,"0")+":"+String(sec).padStart(2,"0");}
function startTimer(node){
  const el=getNodeEl(node.id);if(!el)return;const total=node.meta.seconds;let remaining=total;const{_progressEl:prog,_textEl:txt,_circumference:circ}=node.meta;
  const tickMode=total<=300,start=performance.now();
  function upd(rem){const f=Math.max(0,Math.min(1,rem/total));if(prog){prog.style.strokeDashoffset=circ*(1-f);if(rem<=0)prog.style.stroke="#8C9CA6";}if(txt)txt.textContent=formatTime(rem);}
  function step(){if(!tickMode){remaining=Math.max(0,total-(performance.now()-start)/1000);upd(remaining);if(remaining<=0){node.completed=true;el.classList.add("completed");saveGraph();return;}requestAnimationFrame(step);}else{upd(remaining);if(remaining<=0){node.completed=true;el.classList.add("completed");saveGraph();return;}remaining--;setTimeout(step,1000);}}step();
}

// ── Browser Node Helper ──────────────────────────────────────────────────────
function normalizeBrowserUrl(raw) {
  raw = raw.trim();
  if (!raw) return null;
  // If it already has a protocol, use it
  if (/^https?:\/\//i.test(raw)) return raw;
  // If it looks like a URL (has a dot, no spaces, or starts with localhost)
  const looksLikeUrl = /^localhost|^[\w-]+\.[a-z]{2,}/i.test(raw) && !raw.includes(' ');
  if (looksLikeUrl) return 'https://' + raw;
  // Otherwise treat as a Google search
  return 'https://www.google.com/search?igu=1&q=' + encodeURIComponent(raw);
}

function createBrowserContent(node) {
  const wrap = document.createElement("div");
  wrap.className = "browser-wrap";
  if (node.meta.w) wrap.style.width = node.meta.w + "px";
  if (node.meta.h) wrap.style.height = node.meta.h + "px";

  // Toolbar
  const toolbar = document.createElement("div");
  toolbar.className = "browser-toolbar";

  const dots = document.createElement("div");
  dots.className = "browser-dots";
  const rdot = document.createElement("div"); rdot.className = "browser-dot red"; rdot.title = "Close node";
  rdot.onclick = e => { e.stopPropagation(); deleteNode(node.id); };
  const ydot = document.createElement("div"); ydot.className = "browser-dot yellow"; ydot.title = "Reload";
  const gdot = document.createElement("div"); gdot.className = "browser-dot green"; gdot.title = "Open in new tab";
  dots.appendChild(rdot); dots.appendChild(ydot); dots.appendChild(gdot);

  const backBtn = document.createElement("button"); backBtn.className = "browser-nav-btn"; backBtn.innerHTML = "‹"; backBtn.title = "Back";
  const fwdBtn = document.createElement("button"); fwdBtn.className = "browser-nav-btn"; fwdBtn.innerHTML = "›"; fwdBtn.title = "Forward";
  const addrBar = document.createElement("input"); addrBar.type = "text"; addrBar.className = "browser-address-bar"; addrBar.placeholder = "Search or enter URL...";
  addrBar.value = node.meta.url || "";
  addrBar.spellcheck = false;

  toolbar.appendChild(dots);
  toolbar.appendChild(backBtn);
  toolbar.appendChild(fwdBtn);
  toolbar.appendChild(addrBar);

  // Content area
  const iframeWrap = document.createElement("div");
  iframeWrap.className = "browser-iframe-wrap";

  const loadingOverlay = document.createElement("div");
  loadingOverlay.className = "browser-loading-overlay";
  loadingOverlay.innerHTML = '<div class="browser-spinner"></div><span>Loading...</span>';
  loadingOverlay.style.display = "none";

  const blockedMsg = document.createElement("div");
  blockedMsg.className = "browser-blocked-msg";
  blockedMsg.innerHTML = `
    <div class="browser-blocked-icon">🔒</div>
    <div class="browser-blocked-title">Can't display this page</div>
    <div class="browser-blocked-desc">This site blocks embedded frames. Click below to open it in a new tab.</div>
    <button class="browser-open-btn" id="bopen-${node.id}">Open in New Tab ↗</button>
  `;

  const iframe = document.createElement("iframe");
  iframe.className = "browser-iframe";
  iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-presentation");
  iframe.setAttribute("loading", "lazy");

  const newTab = document.createElement("div");
  newTab.className = "browser-new-tab";
  newTab.innerHTML = `
    <div class="browser-new-tab-logo">SecondBrain Browser</div>
    <div class="browser-new-tab-hint">Search the web or enter a URL above. Sites that block embedding will open in a new tab.</div>
    <div class="browser-quick-links">
      <button class="browser-quick-link" data-url="https://www.google.com/search?igu=1">Google</button>
      <button class="browser-quick-link" data-url="https://en.wikipedia.org/wiki/Main_Page">Wikipedia</button>
      <button class="browser-quick-link" data-url="https://www.youtube.com">YouTube</button>
      <button class="browser-quick-link" data-url="https://github.com">GitHub</button>
      <button class="browser-quick-link" data-url="https://news.ycombinator.com">HN</button>
      <button class="browser-quick-link" data-url="https://reddit.com">Reddit</button>
    </div>
  `;

  iframeWrap.appendChild(newTab);
  iframeWrap.appendChild(loadingOverlay);
  iframeWrap.appendChild(blockedMsg);
  iframeWrap.appendChild(iframe);
  iframe.style.display = "none";

  wrap.appendChild(toolbar);
  wrap.appendChild(iframeWrap);

  // Navigation logic
  let currentUrl = node.meta.url || "";
  let history = currentUrl ? [currentUrl] : [];
  let histIdx = history.length - 1;

  function navigate(url) {
    if (!url) return;
    currentUrl = url;
    node.meta.url = url;
    addrBar.value = url;

    newTab.style.display = "none";
    blockedMsg.classList.remove("visible");
    loadingOverlay.style.display = "flex";
    iframe.style.display = "none";
    iframe.src = "";

    // Push history
    history = history.slice(0, histIdx + 1);
    history.push(url);
    histIdx = history.length - 1;
    updateNavBtns();

    // Attempt to load
    setTimeout(() => {
      iframe.src = url;
    }, 30);

    saveGraph();
  }

  function updateNavBtns() {
    backBtn.disabled = histIdx <= 0;
    fwdBtn.disabled = histIdx >= history.length - 1;
  }

  iframe.addEventListener("load", () => {
    loadingOverlay.style.display = "none";
    try {
      // If we can access contentWindow, it loaded
      const w = iframe.contentWindow;
      if (w) {
        iframe.style.display = "block";
        blockedMsg.classList.remove("visible");
        // Try to get updated URL
        try { const loc = w.location.href; if (loc && loc !== "about:blank") { addrBar.value = loc; node.meta.url = loc; } } catch(e) {}
      }
    } catch(e) {
      iframe.style.display = "none";
      blockedMsg.classList.add("visible");
    }
  });

  iframe.addEventListener("error", () => {
    loadingOverlay.style.display = "none";
    iframe.style.display = "none";
    blockedMsg.classList.add("visible");
  });

  // Detect X-Frame-Options blocks via blank loads with no content
  iframe.addEventListener("load", () => {
    // Small delay to allow content to render, then check if body is empty
    setTimeout(() => {
      try {
        const doc = iframe.contentDocument || iframe.contentWindow?.document;
        if (doc && (doc.body?.innerHTML === "" || doc.title === "")) {
          iframe.style.display = "none";
          loadingOverlay.style.display = "none";
          blockedMsg.classList.add("visible");
        }
      } catch(e) {
        // Cross-origin: can't read, likely loaded ok OR blocked
        // If iframe is not showing, show blocked
        if (iframe.offsetWidth === 0) {
          blockedMsg.classList.add("visible");
        }
      }
    }, 800);
  });

  // Address bar interactions
  addrBar.addEventListener("mousedown", e => e.stopPropagation());
  addrBar.addEventListener("click", e => { e.stopPropagation(); addrBar.select(); });
  addrBar.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Enter") {
      const url = normalizeBrowserUrl(addrBar.value);
      if (url) navigate(url);
    }
  });

  backBtn.addEventListener("mousedown", e => e.stopPropagation());
  backBtn.addEventListener("click", e => {
    e.stopPropagation();
    if (histIdx > 0) { histIdx--; const url = history[histIdx]; navigate(url); }
  });

  fwdBtn.addEventListener("mousedown", e => e.stopPropagation());
  fwdBtn.addEventListener("click", e => {
    e.stopPropagation();
    if (histIdx < history.length - 1) { histIdx++; const url = history[histIdx]; navigate(url); }
  });

  ydot.addEventListener("click", e => { e.stopPropagation(); if (currentUrl) { iframe.src = ""; setTimeout(() => { iframe.src = currentUrl; }, 50); } });
  gdot.addEventListener("click", e => { e.stopPropagation(); if (currentUrl) window.open(currentUrl, "_blank"); });

  // Open-in-tab button inside blocked msg
  setTimeout(() => {
    const openBtn = document.getElementById(`bopen-${node.id}`);
    if (openBtn) openBtn.addEventListener("click", e => { e.stopPropagation(); if (currentUrl) window.open(currentUrl, "_blank"); });
  }, 100);

  // Quick links
  wrap.querySelectorAll(".browser-quick-link").forEach(btn => {
    btn.addEventListener("mousedown", e => e.stopPropagation());
    btn.addEventListener("click", e => { e.stopPropagation(); navigate(btn.dataset.url); });
  });

  // Resize observer to save dimensions
  const ro = new ResizeObserver(() => {
    node.meta.w = wrap.offsetWidth;
    node.meta.h = wrap.offsetHeight;
  });
  ro.observe(wrap);

  // Load initial URL if set
  if (currentUrl) {
    setTimeout(() => navigate(currentUrl), 150);
  }

  updateNavBtns();
  return wrap;
}

function updateNodeTextDOM(node) {
  const el = getNodeEl(node.id); if(!el) return;
  if (node.type === "answer") {
    const b = el.querySelector(".bubble div:last-child");
    if(b && document.activeElement !== b) {
      if(el.classList.contains("is-thinking")) b.innerHTML = '<div class="thinking-spinner">Thinking...</div>';
      else b.innerHTML = `<div class="markdown-body">${marked.parse(node.text||"")}</div>`;
    }
  } else if (node.type === "note") {
    const b = el.querySelector(".note-body"); if(b && document.activeElement !== b) b.value = node.text;
    const t = el.querySelector(".note-title"); if(t && document.activeElement !== t && node.meta.title) t.value = node.meta.title;
  } else if (node.type === "brainstorm") {
    const i = el.querySelector(".brainstorm-input"); if(i && document.activeElement !== i) i.value = node.meta.topic || "";
  } else if (node.type === "browser") {
    const a = el.querySelector(".browser-address-bar"); if(a && document.activeElement !== a) a.value = node.meta.url || "";
  } else if (node.type === "question" || node.type === "text") {
    const box = el.querySelector(".content-box");
    if(box) box.innerHTML = `<div class="markdown-body">${marked.parse(node.text||"")}</div>`;
    else { const t = el.querySelector(".node-text"); if(t) t.innerHTML = `<div class="markdown-body">${marked.parse(node.text||"")}</div>`; }
  } else {
    const t = el.querySelector(".node-text"); if(t) t.textContent = node.text;
  }
}

function handleNodeInputBroadcast(node){if(socket)socket.emit("node_text",{room:SHARE_ID,id:node.id,text:node.text,title:node.meta.title,topic:node.meta.topic});}

function renderBrainstormTree(nodeData,parentNodeId,x,y,level){
  if(!nodeData||!nodeData.topic)return;const n=addNode(nodeData.topic,"answer",x,y);addLink(parentNodeId,n.id);
  if(nodeData.children&&nodeData.children.length>0){const count=nodeData.children.length;const spacingY=Math.max(80,160/level);let startY=y-((count-1)*spacingY)/2;nodeData.children.forEach((child,i)=>{renderBrainstormTree(child,n.id,x+340,startY+i*spacingY,level+1);});}
}

function createNodeElement(node){
  const el=document.createElement("div");el.className="node node-"+node.type;if(node.completed)el.classList.add("completed");el.dataset.id=node.id;
  el.style.left=node.x+"px";el.style.top=node.y+"px";applyDimClass(el,node.dim||0);
  if(node.meta?.pinned)el.classList.add("pinned-highlight");
  if(node.groupId!==undefined){const g=groups.find(x=>x.id===node.groupId);if(g&&g.collapsed)el.style.display="none";}
  const circle=document.createElement("div");circle.className="node-circle";
  const textWrap=document.createElement("div");textWrap.className="node-text";

  if(node.type==="answer"){
    const bubble=document.createElement("div");bubble.className="bubble";const header=document.createElement("div");header.className="bubble-header";
    const copyBtn=document.createElement("button");copyBtn.className="copy-btn";copyBtn.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy`;
    copyBtn.onclick=e=>{e.stopPropagation();navigator.clipboard.writeText(node.text||"").catch(()=>{});copyBtn.style.color="var(--accent)";copyBtn.style.borderColor="var(--accent)";copyBtn.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied`;setTimeout(()=>{copyBtn.style.color="";copyBtn.style.borderColor="";copyBtn.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy`;},2000);};header.appendChild(copyBtn);
    const body=document.createElement("div");if(el.classList.contains("is-thinking"))body.innerHTML='<div class="thinking-spinner">Thinking...</div>';else body.innerHTML=`<div class="markdown-body">${marked.parse(node.text||"")}</div>`;
    bubble.appendChild(header);bubble.appendChild(body);bubble.addEventListener("scroll",()=>{if(bubble.scrollTop+bubble.clientHeight>=bubble.scrollHeight-2){node.completed=true;el.classList.add("completed");saveGraph();}});textWrap.appendChild(bubble);
  } else if(node.type==="timer"){
    textWrap.appendChild(createTimerContent(node));
  } else if(node.type==="note"){
    const wrap=document.createElement("div");wrap.className="note-wrap";
    const titleIn=document.createElement("input");titleIn.className="note-title";titleIn.placeholder="Title…";titleIn.value=node.meta.title||"";
    titleIn.addEventListener("input",e=>{e.stopPropagation();node.meta.title=titleIn.value;handleNodeInputBroadcast(node);});titleIn.addEventListener("change",()=>saveGraph());titleIn.addEventListener("mousedown",e=>{if(document.activeElement===titleIn)e.stopPropagation();});titleIn.addEventListener("click",e=>{e.stopPropagation();titleIn.focus();});
    const bodyIn=document.createElement("textarea");bodyIn.className="note-body";bodyIn.placeholder="Write anything…";bodyIn.value=node.text||"";
    bodyIn.addEventListener("input",e=>{e.stopPropagation();node.text=bodyIn.value;handleNodeInputBroadcast(node);});bodyIn.addEventListener("change",()=>saveGraph());bodyIn.addEventListener("mousedown",e=>{if(document.activeElement===bodyIn)e.stopPropagation();});bodyIn.addEventListener("click",e=>{e.stopPropagation();bodyIn.focus();});bodyIn.addEventListener("keydown",e=>e.stopPropagation());
    wrap.appendChild(titleIn);wrap.appendChild(bodyIn);textWrap.appendChild(wrap);
  } else if(node.type==="brainstorm"){
    const wrap=document.createElement("div");wrap.className="brainstorm-wrap";
    const input=document.createElement("textarea");input.className="brainstorm-input";input.placeholder="Topic...";input.value=node.meta.topic||"";
    input.addEventListener("input",e=>{e.stopPropagation();node.meta.topic=input.value;handleNodeInputBroadcast(node);});input.addEventListener("change",()=>saveGraph());input.addEventListener("mousedown",e=>{if(document.activeElement===input)e.stopPropagation();});input.addEventListener("click",e=>{e.stopPropagation();input.focus();});input.addEventListener("keydown",e=>e.stopPropagation());
    const runBtn=document.createElement("button");runBtn.className="brainstorm-run";runBtn.textContent="Run Idea Tree";
    runBtn.onclick=async(e)=>{
      e.stopPropagation();if(!input.value.trim())return;runBtn.textContent="Running...";
      const thinkNode=addNode("","answer",node.x+340,node.y);const tEl=getNodeEl(thinkNode.id);if(tEl)tEl.classList.add("is-thinking");updateNodeTextDOM(thinkNode);addLink(node.id,thinkNode.id);
      try{const r=await fetch("/brainstorm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic:input.value})});const d=await r.json();deleteNode(thinkNode.id);if(d.tree&&d.tree.children){const count=d.tree.children.length;const spacingY=160;let startY=node.y-((count-1)*spacingY)/2;d.tree.children.forEach((child,i)=>{renderBrainstormTree(child,node.id,node.x+340,startY+i*spacingY,1);});saveGraph();}}catch(err){deleteNode(thinkNode.id);}runBtn.textContent="Run Idea Tree";
    };
    wrap.appendChild(input);wrap.appendChild(runBtn);textWrap.appendChild(wrap);
  } else if(node.type==="browser"){
    // Browser node — contains its own toolbar + iframe, stop drag when interacting inside
    const browserContent = createBrowserContent(node);
    textWrap.appendChild(browserContent);
    // Prevent node drag when interacting inside the browser
    browserContent.addEventListener("mousedown", e => {
      if (e.target.classList.contains("browser-toolbar") || e.target === browserContent) return;
      if (!e.target.closest(".browser-toolbar")) e.stopPropagation();
    });
  } else {
    const box=document.createElement("div");box.className="content-box";box.innerHTML=`<div class="markdown-body">${marked.parse(node.text||"")}</div>`;
    const exp=document.createElement("button");exp.className="expand-btn";exp.textContent="Show more";
    textWrap.appendChild(box);textWrap.appendChild(exp);
    setTimeout(()=>{if(box.scrollHeight>box.clientHeight){exp.style.display="block";exp.onclick=(e)=>{e.stopPropagation();box.classList.toggle("expanded");exp.textContent=box.classList.contains("expanded")?"Show less":"Show more";redrawLinks();redrawGroups();};}},10);
  }

  const timeDiv=document.createElement("div");timeDiv.className="node-time";timeDiv.textContent=new Date(node.meta.timestamp||Date.now()).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  textWrap.appendChild(timeDiv);

  if(node.type==="answer"||node.type==="note"){
    const rh=document.createElement("div");rh.className="group-resize-handle";rh.style.background="rgba(0,0,0,0.1)";rh.style.zIndex=20;
    let targetEl=node.type==="answer"?textWrap.querySelector('.bubble'):textWrap.querySelector('.note-body');
    if(node.meta.w)targetEl.style.width=node.meta.w+"px";if(node.meta.h)targetEl.style.height=node.meta.h+"px";
    rh.addEventListener("mousedown",e=>{e.stopPropagation();e.preventDefault();resizingNode=node;resizingTarget=targetEl;resizeStartX=e.clientX;resizeStartY=e.clientY;resizeStartW=targetEl.offsetWidth;resizeStartH=targetEl.offsetHeight;});
    textWrap.appendChild(rh);
  }

  el.appendChild(circle);el.appendChild(textWrap);
  if(node.groupId!==undefined){const g=groups.find(x=>x.id===node.groupId);if(g){const badge=document.createElement("div");badge.className="group-badge";badge.style.background=g.color;el.appendChild(badge);}}

  el.addEventListener("mouseenter",()=>{if(isCtrlHeld&&isShiftHeld)applyTreeHighlight(node);else if(isCtrlHeld)applyCtrlHighlight(node);});
  el.addEventListener("mouseleave",()=>{if(isCtrlHeld)clearCtrlHighlights();});

  el.addEventListener("mousedown",e=>{
    if(e.shiftKey)return;
    if((node.type==="note"||node.type==="brainstorm")&&(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")){if(document.activeElement===e.target)return;}
    // For browser nodes, only drag from the dot/circle area or if clicking on the toolbar background
    if(node.type==="browser"){
      const isOnToolbarBg = e.target.classList.contains("browser-toolbar");
      const isOnCircle = e.target.classList.contains("node-circle");
      if(!isOnToolbarBg&&!isOnCircle)return;
    }
    if(e.target.classList.contains("group-resize-handle")||e.target.classList.contains("expand-btn")||e.target.closest('.copy-btn'))return;
    e.stopPropagation();draggingNode=node;const cc=clientToCanvas(e.clientX,e.clientY);dragOffset={x:cc.x-node.x,y:cc.y-node.y};
  });

  el.addEventListener("contextmenu",e=>{if(e.shiftKey)return;e.preventDefault();e.stopPropagation();ctxTargetNodeId=node.id;ctxTargetGroupId=null;buildCtxMenu("node",e.clientX,e.clientY);});

  el.addEventListener("click",e=>{
    if(e.target.closest('.copy-btn'))return;
    if(node.type==="browser"&&!e.target.classList.contains("node-circle")&&!e.target.classList.contains("node-text"))return;
    e.stopPropagation();
    if(e.shiftKey&&(e.ctrlKey||e.metaKey)){e.preventDefault();getTreeNodes(node.id).forEach(n=>{n.selected=true;let nel=getNodeEl(n.id);if(nel)nel.classList.add("selected");});hasActiveContext=true;updateSuggestionsDebounced();return;}
    if(e.shiftKey){if(node.selected){getSelectedNodes().forEach(n=>deleteNode(n.id));}else{deleteNode(node.id);}return;}
    if(e.ctrlKey||e.metaKey){e.preventDefault();[node,...ctrlHighlightedNodes].forEach(n=>{n.selected=true;const nel=getNodeEl(n.id);if(nel)nel.classList.add("selected");});hasActiveContext=true;updateSuggestionsDebounced();return;}
    pushUndo();node.selected=!node.selected;el.classList.toggle("selected",node.selected);hasActiveContext=nodes.some(n=>n.selected);updateSuggestionsDebounced();
  });

  let touchTimer=null;
  el.addEventListener("touchstart",e=>{
    if(e.touches.length>1)return;
    if(["TEXTAREA","INPUT","BUTTON"].includes(e.target.tagName)||e.target.closest('.copy-btn'))return;
    touchTimer=setTimeout(()=>{touchTimer=null;ctxTargetNodeId=node.id;ctxTargetGroupId=null;buildCtxMenu("node",e.touches[0].clientX,e.touches[0].clientY);if(navigator.vibrate)navigator.vibrate(50);},500);
    draggingNode=node;const cc=clientToCanvas(e.touches[0].clientX,e.touches[0].clientY);dragOffset={x:cc.x-node.x,y:cc.y-node.y};
  },{passive:true});
  el.addEventListener("touchmove",e=>{if(touchTimer){clearTimeout(touchTimer);touchTimer=null;}},{passive:true});
  el.addEventListener("touchend",e=>{if(touchTimer){clearTimeout(touchTimer);touchTimer=null;}},{passive:true});

  canvas.appendChild(el);return el;
}

function addNode(text,type,x=ORIGIN_X,y=ORIGIN_Y,meta={}){
  pushUndo();meta.timestamp=Date.now();
  const node={id:nextNodeId++,x,y,type,text,selected:false,dim:0,meta,completed:false};nodes.push(node);createNodeElement(node);
  const sel=getSelectedNodes();
  if(sel.length>0){sel.forEach(s=>addLink(s.id,node.id));}else if(!explicitlyDeselected&&lastNodeId!==null){addLink(lastNodeId,node.id);}
  explicitlyDeselected=false;lastNodeId=node.id;saveGraph();return node;
}
function addLink(sourceId,targetId){if(sourceId===targetId)return;if(links.some(l=>(l.sourceId===sourceId&&l.targetId===targetId)||(l.sourceId===targetId&&l.targetId===sourceId)))return;links.push({id:nextLinkId++,sourceId,targetId});redrawLinks();saveGraph();}
function deleteLink(id){links=links.filter(l=>l.id!==id);redrawLinks();saveGraph();}
function deleteNode(id){
  pushUndo();nodes=nodes.filter(n=>n.id!==id);links=links.filter(l=>l.sourceId!==id&&l.targetId!==id);
  groups.forEach(g=>{g.nodeIds=g.nodeIds.filter(nid=>nid!==id);});groups=groups.filter(g=>g.nodeIds.length>0);
  const el=getNodeEl(id);if(el)el.remove();redrawLinks();redrawGroups();saveGraph();
}

function redrawLinks(){
  linkLayer.innerHTML="";const strokeW=Math.max(1.5,1.2/currentScale);
  links.forEach(l=>{
    const a=nodes.find(n=>n.id===l.sourceId),b=nodes.find(n=>n.id===l.targetId);if(!a||!b)return;
    const aEl=getNodeEl(a.id),bEl=getNodeEl(b.id);
    if((aEl&&aEl.style.display==="none")||(bEl&&bEl.style.display==="none")||!aEl||!bEl)return;
    let ax=a.x+4,ay=a.y+4,bx=b.x+4,by=b.y+4;let dx=bx-ax,dy=by-ay;let dist=Math.hypot(dx,dy);let gap=16;
    if(dist>gap*2){ax+=(dx/dist)*gap;ay+=(dy/dist)*gap;bx-=(dx/dist)*gap;by-=(dy/dist)*gap;}
    const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.classList.add("edge");path.dataset.id=l.id;
    let dStr="";
    if(Math.abs(dx)>Math.abs(dy)){let midX=(ax+bx)/2;dStr=`M ${ax} ${ay} C ${midX} ${ay}, ${midX} ${by}, ${bx} ${by}`;}
    else{let midY=(ay+by)/2;dStr=`M ${ax} ${ay} C ${ax} ${midY}, ${bx} ${midY}, ${bx} ${by}`;}
    path.setAttribute("d",dStr);path.setAttribute("stroke","rgba(166,144,118,0.4)");path.setAttribute("stroke-width",strokeW);path.setAttribute("fill","none");path.style.transition="d 0.2s ease-out";linkLayer.appendChild(path);
  });
}
linkLayer.addEventListener("click",e=>{if(e.target.tagName==="path"&&e.target.classList.contains("edge")&&e.shiftKey)deleteLink(parseInt(e.target.dataset.id,10));});

function ensureMergeHint(){if(!mergeHintEl){mergeHintEl=document.createElement("div");mergeHintEl.className="merge-hint";mergeHintEl.textContent="Merge?";canvas.appendChild(mergeHintEl);}}
function showMergeHint(x,y){ensureMergeHint();mergeHintEl.style.cssText=`left:${x}px;top:${y}px;display:block;`;}
function hideMergeHint(){if(mergeHintEl)mergeHintEl.style.display="none";mergeTargetNode=null;}
function ensureGroupHint(){if(!groupAddHintEl){groupAddHintEl=document.createElement("div");groupAddHintEl.className="group-add-hint";canvas.appendChild(groupAddHintEl);}}
function showGroupAddHint(x,y,name){ensureGroupHint();groupAddHintEl.textContent="Add to "+name+"?";groupAddHintEl.style.cssText=`left:${x}px;top:${y}px;display:block;`;}
function hideGroupAddHint(){if(groupAddHintEl)groupAddHintEl.style.display="none";groupAddTarget=null;}

document.addEventListener("mousemove",e=>{
  if(resizingNode&&resizingTarget){const dx=(e.clientX-resizeStartX)/currentScale,dy=(e.clientY-resizeStartY)/currentScale;const newW=Math.max(120,resizeStartW+dx),newH=Math.max(50,resizeStartH+dy);resizingTarget.style.width=newW+"px";resizingTarget.style.height=newH+"px";resizingNode.meta.w=newW;resizingNode.meta.h=newH;redrawLinks();return;}
  if(resizingGroup){const dx=(e.clientX-resizeStartX)/currentScale,dy=(e.clientY-resizeStartY)/currentScale;resizingGroup.collapsedW=Math.max(80,resizeStartW+dx);resizingGroup.collapsedH=Math.max(30,resizeStartH+dy);redrawGroups();return;}
  if(draggingGroup){const cc=clientToCanvas(e.clientX,e.clientY);const ox=cc.x-groupDragOffset.x,oy=cc.y-groupDragOffset.y;if(draggingGroup.collapsed){draggingGroup.collapsedX=ox;draggingGroup.collapsedY=oy;}else{groupDragNodeOffsets.forEach(({id,dx,dy})=>{const n=nodes.find(x=>x.id===id);if(!n)return;n.x=ox+dx;n.y=oy+dy;const nel=getNodeEl(id);if(nel){nel.style.left=n.x+"px";nel.style.top=n.y+"px";}});redrawLinks();}redrawGroups();return;}
  if(!draggingNode)return;
  const cc=clientToCanvas(e.clientX,e.clientY);draggingNode.x=cc.x-dragOffset.x;draggingNode.y=cc.y-dragOffset.y;
  const el=getNodeEl(draggingNode.id);if(el){el.style.left=draggingNode.x+"px";el.style.top=draggingNode.y+"px";}
  if(socket)socket.emit("node_move",{room:SHARE_ID,id:draggingNode.id,x:draggingNode.x,y:draggingNode.y});
  redrawLinks();if(draggingNode.groupId!==undefined)redrawGroups();
  let closest=null,closestDist=Infinity;nodes.forEach(other=>{if(other.id===draggingNode.id)return;const dx=other.x-draggingNode.x,dy=other.y-draggingNode.y,d=Math.sqrt(dx*dx+dy*dy);if(d<closestDist){closestDist=d;closest=other;}});
  if(closest&&closestDist<60){mergeTargetNode=closest;showMergeHint(closest.x+20,closest.y-10);}else hideMergeHint();
  hideGroupAddHint();
  for(const g of groups){if(g.nodeIds.includes(draggingNode.id))continue;if(g.collapsed){const cx=g.collapsedX||ORIGIN_X,cy=g.collapsedY||ORIGIN_Y,cw=g.collapsedW||160,ch=g.collapsedH||60;if(draggingNode.x>cx&&draggingNode.x<cx+cw&&draggingNode.y>cy&&draggingNode.y<cy+ch){groupAddTarget=g;showGroupAddHint(draggingNode.x+20,draggingNode.y-10,g.name);break;}}else{const bounds=getGroupBounds(g);if(!bounds)continue;const pad=24;if(draggingNode.x>bounds.minX-pad&&draggingNode.x<bounds.maxX+pad&&draggingNode.y>bounds.minY-pad&&draggingNode.y<bounds.maxY+pad){groupAddTarget=g;showGroupAddHint(draggingNode.x+20,draggingNode.y-10,g.name);break;}}}
});

document.addEventListener("touchmove",e=>{if(draggingNode){e.preventDefault();const cc=clientToCanvas(e.touches[0].clientX,e.touches[0].clientY);draggingNode.x=cc.x-dragOffset.x;draggingNode.y=cc.y-dragOffset.y;const el=getNodeEl(draggingNode.id);if(el){el.style.left=draggingNode.x+"px";el.style.top=draggingNode.y+"px";}if(socket)socket.emit("node_move",{room:SHARE_ID,id:draggingNode.id,x:draggingNode.x,y:draggingNode.y});redrawLinks();if(draggingNode.groupId!==undefined)redrawGroups();}},{passive:false});

document.addEventListener("mouseup",async e=>{
  if(resizingNode){resizingNode=null;resizingTarget=null;saveGraph();return;}
  if(resizingGroup){resizingGroup=null;saveGraph();return;}
  if(draggingGroup){draggingGroup=null;groupDragNodeOffsets=[];saveGraph();return;}
  if(!draggingNode)return;
  const source=draggingNode;draggingNode=null;saveGraph();
  if(groupAddTarget){const gt=groupAddTarget;hideGroupAddHint();hideMergeHint();if(confirm('Add node to group "'+gt.name+'"?'))addNodeToGroup(source.id,gt.id);return;}
  if(mergeTargetNode&&mergeTargetNode.id!==source.id){const mt=mergeTargetNode;hideMergeHint();if(confirm("Merge these nodes with AI?")){try{const res=await fetch("/merge",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({a:mt.text||"",b:source.text||""})});const data=await res.json();mt.text=data.merged||((mt.text||"")+"\n"+(source.text||""));updateNodeTextDOM(mt);handleNodeInputBroadcast(mt);deleteNode(source.id);}catch(ex){mt.text=(mt.text||"")+"\n"+(source.text||"");updateNodeTextDOM(mt);handleNodeInputBroadcast(mt);deleteNode(source.id);}saveGraph();}return;}
  hideMergeHint();
});
document.addEventListener("touchend",e=>{if(draggingNode){draggingNode=null;saveGraph();}});

canvasWrapper.addEventListener("mousedown",e=>{
  if(e.target.closest(".node")||e.target.classList.contains("edge")||e.target.closest("#input-bar-container")||e.target.closest(".suggestion-btn")||e.target.closest(".group-hull")||e.target.closest("#top-bar-container")||e.target.closest("#zoom-controls")||e.target.closest("#chat-window"))return;
  if(e.shiftKey){isLassoing=true;const cc=clientToCanvas(e.clientX,e.clientY);lassoStartX=cc.x;lassoStartY=cc.y;lassoBox.style.left=(lassoStartX*currentScale)+"px";lassoBox.style.top=(lassoStartY*currentScale)+"px";lassoBox.style.width="0px";lassoBox.style.height="0px";lassoBox.style.display="block";return;}
  isPanning=true;panMoved=false;panStartX=e.clientX;panStartY=e.clientY;panScrollX=canvasWrapper.scrollLeft;panScrollY=canvasWrapper.scrollTop;canvasWrapper.style.cursor="grabbing";
});
canvasWrapper.addEventListener("mousemove",e=>{
  if(isLassoing){const cc=clientToCanvas(e.clientX,e.clientY);const x=Math.min(cc.x,lassoStartX),y=Math.min(cc.y,lassoStartY),w=Math.abs(cc.x-lassoStartX),h=Math.abs(cc.y-lassoStartY);lassoBox.style.left=x+"px";lassoBox.style.top=y+"px";lassoBox.style.width=w+"px";lassoBox.style.height=h+"px";return;}
  if(!isPanning)return;const dx=e.clientX-panStartX,dy=e.clientY-panStartY;if(Math.abs(dx)>2||Math.abs(dy)>2)panMoved=true;canvasWrapper.scrollLeft=panScrollX-dx;canvasWrapper.scrollTop=panScrollY-dy;
});
canvasWrapper.addEventListener("mouseup",e=>{
  if(isLassoing){isLassoing=false;lassoBox.style.display="none";const cc=clientToCanvas(e.clientX,e.clientY);const minX=Math.min(cc.x,lassoStartX),minY=Math.min(cc.y,lassoStartY),maxX=Math.max(cc.x,lassoStartX),maxY=Math.max(cc.y,lassoStartY);nodes.forEach(n=>{if(n.x>=minX&&n.x<=maxX&&n.y>=minY&&n.y<=maxY){n.selected=true;const el=getNodeEl(n.id);if(el)el.classList.add("selected");}});hasActiveContext=nodes.some(n=>n.selected);updateSuggestionsDebounced();return;}
  if(isPanning&&!panMoved){deselectAll();hasActiveContext=false;}isPanning=false;canvasWrapper.style.cursor="default";
});
canvasWrapper.addEventListener("mouseleave",()=>{isPanning=false;isLassoing=false;lassoBox.style.display="none";canvasWrapper.style.cursor="default";});

let initialPinchDist=null,initialPinchScale=null;
canvasWrapper.addEventListener("touchstart",e=>{if(e.touches.length===2){initialPinchDist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);initialPinchScale=currentScale;return;}if(e.target.closest(".node")||e.target.closest(".group-hull")||e.target.closest(".top-btn"))return;isPanning=true;panMoved=false;panStartX=e.touches[0].clientX;panStartY=e.touches[0].clientY;panScrollX=canvasWrapper.scrollLeft;panScrollY=canvasWrapper.scrollTop;},{passive:false});
canvasWrapper.addEventListener("touchmove",e=>{if(e.touches.length===2){e.preventDefault();const dist=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);if(initialPinchDist&&initialPinchScale&&initialPinchDist>0){const newScale=initialPinchScale*(dist/initialPinchDist);applyZoom(newScale,(e.touches[0].clientX+e.touches[1].clientX)/2,(e.touches[0].clientY+e.touches[1].clientY)/2);}return;}if(!isPanning)return;e.preventDefault();const dx=e.touches[0].clientX-panStartX;const dy=e.touches[0].clientY-panStartY;if(Math.abs(dx)>2||Math.abs(dy)>2)panMoved=true;canvasWrapper.scrollLeft=panScrollX-dx;canvasWrapper.scrollTop=panScrollY-dy;},{passive:false});
canvasWrapper.addEventListener("touchend",e=>{initialPinchDist=null;initialPinchScale=null;if(isPanning&&!panMoved){deselectAll();hasActiveContext=false;}isPanning=false;});

function deselectAll(){nodes.forEach(n=>n.selected=false);canvas.querySelectorAll(".node").forEach(el=>el.classList.remove("selected"));updateSuggestionsDebounced();explicitlyDeselected=true;}
document.addEventListener("click",e=>{if(!canvas.contains(e.target)&&!document.getElementById("top-bar-container").contains(e.target)&&!document.getElementById("input-bar-container").contains(e.target)&&!suggestionsBar.contains(e.target)&&!colorPickerPopup.contains(e.target)&&!document.getElementById("chat-window").contains(e.target)){deselectAll();hasActiveContext=false;}});

function getSmartSpawnPos(){const sel=getSelectedNodes();if(sel.length>0){const maxX=Math.max(...sel.map(n=>n.x));const avgY=sel.reduce((s,n)=>s+n.y,0)/sel.length;return{x:maxX+560,y:avgY};}if(nodes.length>0){const maxX=Math.max(...nodes.map(n=>n.x))+560;const midY=nodes.reduce((s,n)=>s+n.y,0)/nodes.length;return{x:maxX,y:midY};}return{x:ORIGIN_X,y:ORIGIN_Y};}

document.getElementById("auto-btn").onclick=()=>{
  pushUndo();const visited=new Set();const components=[];
  function bfs(startId){const comp=[];const q=[startId];visited.add(startId);while(q.length){const id=q.shift();comp.push(id);links.forEach(l=>{const nb=l.sourceId===id?l.targetId:l.targetId===id?l.sourceId:null;if(nb&&!visited.has(nb)&&nodes.find(n=>n.id===nb)){visited.add(nb);q.push(nb);}});}return comp;}
  nodes.forEach(n=>{if(!visited.has(n.id))components.push(bfs(n.id));});
  const isMobile=window.innerWidth<window.innerHeight;const X_SPACE=isMobile?220:340;const Y_SPACE=isMobile?180:120;const COMP_GAP=120;let gOffX=ORIGIN_X,gOffY=ORIGIN_Y;
  components.forEach(comp=>{if(!comp.length)return;if(comp.length===1){const n=nodes.find(x=>x.id===comp[0]);if(n){n.x=gOffX;n.y=gOffY;}if(isMobile){gOffX+=X_SPACE+COMP_GAP;}else{gOffY+=Y_SPACE+COMP_GAP;}return;}
    const roots=comp.filter(id=>{const n=nodes.find(x=>x.id===id);return n&&n.type==="question";});let root=roots.length?roots[0]:comp[0];
    const depth=new Map();const children=new Map();comp.forEach(id=>children.set(id,[]));const bfsQ=[root];const vis2=new Set([root]);depth.set(root,0);while(bfsQ.length){const cur=bfsQ.shift();links.forEach(l=>{let nb=null;if(l.sourceId===cur&&comp.includes(l.targetId))nb=l.targetId;else if(l.targetId===cur&&comp.includes(l.sourceId))nb=l.sourceId;if(nb&&!vis2.has(nb)){vis2.add(nb);depth.set(nb,(depth.get(cur)||0)+1);children.get(cur).push(nb);bfsQ.push(nb);}});}
    const sh=new Map();function calcSH(id){const kids=children.get(id)||[];if(!kids.length){sh.set(id,1);return 1;}const s=kids.reduce((a,k)=>a+calcSH(k),0);sh.set(id,s);return s;}calcSH(root);
    function assign(id,lateralOffset){const kids=children.get(id)||[];const n=nodes.find(x=>x.id===id);const d=depth.get(id)||0;const totalSpread=(sh.get(id)-1)*(isMobile?X_SPACE:Y_SPACE);const center=lateralOffset+totalSpread/2;if(n){if(isMobile){n.x=center;n.y=gOffY+d*Y_SPACE;}else{n.x=gOffX+d*X_SPACE;n.y=center;}}let ct=lateralOffset;kids.forEach(k=>{assign(k,ct);ct+=sh.get(k)*(isMobile?X_SPACE:Y_SPACE);});}
    if(isMobile){assign(root,gOffX);const maxY=Math.max(...comp.map(id=>{const n=nodes.find(x=>x.id===id);return n?n.y:gOffY;}));gOffY=maxY+Y_SPACE+COMP_GAP;}else{assign(root,gOffY);const maxY=Math.max(...comp.map(id=>{const n=nodes.find(x=>x.id===id);return n?n.y:gOffY;}));gOffY=maxY+Y_SPACE+COMP_GAP;}
  });
  nodes.forEach(n=>{const el=getNodeEl(n.id);if(el){el.style.left=n.x+"px";el.style.top=n.y+"px";}});redrawLinks();redrawGroups();saveGraph();setTimeout(()=>smartRecenter(true),60);
};

function buildSlashPopup(filter){slashPopup.innerHTML="";const filtered=SLASH_COMMANDS.filter(c=>c.cmd.startsWith(filter)||filter==="/");if(!filtered.length){hideSlashPopup();return;}filtered.forEach((c,i)=>{const item=document.createElement("div");item.className="slash-item"+(i===slashSelectedIndex?" active":"");const cs=document.createElement("span");cs.className="slash-item-cmd";cs.textContent=c.cmd;const ds=document.createElement("span");ds.className="slash-item-desc";ds.textContent=c.desc;item.appendChild(cs);item.appendChild(ds);item.onclick=()=>{promptEl.value=c.argHint;promptEl.setSelectionRange(c.argHint.length,c.argHint.length);hideSlashPopup();promptEl.focus();};slashPopup.appendChild(item);});slashPopup.classList.add("visible");slashActive=true;}
function hideSlashPopup(){slashPopup.classList.remove("visible");slashActive=false;slashSelectedIndex=0;}

promptEl.addEventListener("input",()=>{const val=promptEl.value;if(val.startsWith("/")){const p=val.split(" ");if(p.length===1)buildSlashPopup(p[0]);else hideSlashPopup();}else hideSlashPopup();updateSuggestionsDebounced();});
promptEl.addEventListener("keydown",e=>{
  if(slashActive){if(e.key==="ArrowDown"){e.preventDefault();slashSelectedIndex=Math.min(slashSelectedIndex+1,SLASH_COMMANDS.length-1);buildSlashPopup(promptEl.value);return;}if(e.key==="ArrowUp"){e.preventDefault();slashSelectedIndex=Math.max(slashSelectedIndex-1,0);buildSlashPopup(promptEl.value);return;}if(e.key==="Enter"){const item=slashPopup.querySelector(".slash-item.active");if(item){item.click();e.preventDefault();return;}}if(e.key==="Escape"){hideSlashPopup();return;}}
  if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendPrompt();promptEl.style.height="auto";}
});

async function runFindCommand(query){
  if(!query.trim())return;const descs=nodes.map(n=>({id:n.id,type:n.type,text:(n.text||"").slice(0,200),x:Math.round(n.x),y:Math.round(n.y)}));
  let foundId=null;
  try{const res=await fetch("/find",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,nodes:descs})});const data=await res.json();foundId=data.nodeId;}catch(e){}
  if(!foundId){const qLower=query.toLowerCase();const match=nodes.find(n=>(n.text||"").toLowerCase().includes(qLower)||(n.meta.title||"").toLowerCase().includes(qLower)||(n.meta.topic||"").toLowerCase().includes(qLower)||(n.meta.url||"").toLowerCase().includes(qLower));if(match)foundId=match.id;}
  if(foundId){const t=nodes.find(n=>n.id===foundId);if(t){canvas.querySelectorAll(".node.find-focus").forEach(el=>el.classList.remove("find-focus"));const el=getNodeEl(t.id);if(el)el.classList.add("find-focus");canvasWrapper.scrollTo({left:Math.max(0,t.x*currentScale-canvasWrapper.clientWidth/2+80),top:Math.max(0,t.y*currentScale-canvasWrapper.clientHeight/2+40),behavior:"smooth"});setTimeout(()=>{if(el)el.classList.remove("find-focus");},3000);}}else{alert("Could not find a matching node.");}
}
function runDeleteCommand(arg){const a=(arg||"").trim().toLowerCase();pushUndo();if(a==="all"){nodes=[];links=[];groups=[];canvas.querySelectorAll(".node,.group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());redrawLinks();saveGraph();return;}if(a==="last"||a===""){if(!nodes.length)return;const last=nodes.reduce((a,b)=>a.id>b.id?a:b);deleteNode(last.id);return;}if(a==="prompts"||a==="questions"){nodes.filter(n=>n.type==="question").map(n=>n.id).forEach(id=>deleteNode(id));return;}const match=nodes.find(n=>(n.text||"").toLowerCase().includes(a));if(match)deleteNode(match.id);}

document.getElementById("note-btn").onclick=()=>{const spawn=getSmartSpawnPos();addNode("","note",spawn.x,spawn.y,{title:"Untitled"});};
document.getElementById("brainstorm-btn").onclick=()=>{const spawn=getSmartSpawnPos();addNode("","brainstorm",spawn.x,spawn.y,{topic:""});};
document.getElementById("browser-btn").onclick=()=>{const spawn=getSmartSpawnPos();addNode("","browser",spawn.x,spawn.y,{url:"",w:480,h:320});};

function dimAllNodes(){nodes.forEach(n=>{n.dim=Math.min((n.dim||0)+1,4);const el=getNodeEl(n.id);if(el)applyDimClass(el,n.dim);});}
function buildContext(){const sel=getSelectedNodes();return sel.length===0?"":sel.map(n=>n.text).join("\n---\n");}
async function classifyInput(text){const res=await fetch("/classify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({input:text})});return await res.json();}

function renderSuggestions(list){suggestionsBar.innerHTML="";if(!list||!list.length)return;list.slice(0,3).forEach(s=>{const btn=document.createElement("button");btn.className="suggestion-btn";btn.textContent=s;btn.onclick=()=>{promptEl.value=s;promptEl.focus();updateSuggestionsDebounced();};suggestionsBar.appendChild(btn);});}
let suggestTimeout=null;function updateSuggestionsDebounced(){if(suggestTimeout)clearTimeout(suggestTimeout);suggestTimeout=setTimeout(updateSuggestions,400);}
async function updateSuggestions(){const raw=promptEl.value.trim();if(raw.startsWith("/"))return;const ctx=buildContext();if(!raw&&!ctx){suggestionsBar.innerHTML="";return;}try{const r=await fetch("/suggest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:raw||"(thinking)",context:ctx})});const d=await r.json();renderSuggestions(d.suggestions||[]);}catch(e){}}

function saveGraph(){
  if(SHARE_ID==='guest')return;showSyncing();
  const graphData={
    nodes:nodes.map(n=>({id:n.id,x:n.x,y:n.y,type:n.type,text:n.text,dim:n.dim||0,meta:{topic:n.meta.topic||"",seconds:n.meta.seconds||0,label:n.meta.label||"",title:n.meta.title||"",w:n.meta.w||null,h:n.meta.h||null,pinned:n.meta.pinned,timestamp:n.meta.timestamp,url:n.meta.url||""},completed:!!n.completed,groupId:n.groupId})),
    links:links.map(l=>({id:l.id,sourceId:l.sourceId,targetId:l.targetId})),
    groups:groups.map(g=>({id:g.id,name:g.name,color:g.color,pinned:g.pinned,nodeIds:[...g.nodeIds],collapsed:!!g.collapsed,collapsedW:g.collapsedW||160,collapsedH:g.collapsedH||60,collapsedX:g.collapsedX,collapsedY:g.collapsedY,savedPositions:g.savedPositions})),
    chat:chatMessages,nextNodeId,nextLinkId,nextGroupId
  };
  if(socket)socket.emit("graph_update",{room:SHARE_ID,graph:graphData});
  clearTimeout(syncTimeout);syncTimeout=setTimeout(showSynced,600);
}

async function loadGraph(){
  if(SHARE_ID==='guest')return;
  try{const res=await fetch(`/api/board/load/${SHARE_ID}`);if(!res.ok)return;const data=await res.json();if(!data||!data.nodes)return;
    nodes=data.nodes||[];links=data.links||[];groups=data.groups||[];chatMessages=data.chat||[];renderChat();
    groups.forEach(g=>{if(!g.collapsedW)g.collapsedW=160;if(!g.collapsedH)g.collapsedH=60;});
    nextNodeId=data.nextNodeId||(Math.max(0,...nodes.map(n=>n.id))+1);nextLinkId=data.nextLinkId||(Math.max(0,...links.map(l=>l.id))+1);nextGroupId=data.nextGroupId||(Math.max(0,...(groups.length?groups.map(g=>g.id):[0]))+1);
    canvas.querySelectorAll(".node,.group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());
    nodes.forEach(n=>{if(!n.meta)n.meta={};createNodeElement(n);});redrawLinks();redrawGroups();showSynced();
  }catch(e){console.warn("load failed",e);}
}

async function sendPrompt(){
  const raw=promptEl.value.trim();if(!raw)return;hideSlashPopup();
  if(raw.startsWith("/find ")){await runFindCommand(raw.slice(6).trim());promptEl.value="";return;}
  if(raw==="/undo"){undo();promptEl.value="";return;}if(raw==="/redo"){redo();promptEl.value="";return;}
  if(raw==="/pinned"){nodes.forEach(n=>{if(n.meta?.pinned){const el=getNodeEl(n.id);el?.classList.add("find-focus");setTimeout(()=>el?.classList.remove("find-focus"),4000);}});groups.forEach(g=>{if(g.pinned){const el=getGroupEl(g.id);el?.classList.add("pinned-highlight");setTimeout(()=>el?.classList.remove("pinned-highlight"),4000);}});promptEl.value="";return;}
  if(raw.startsWith("/delete")){runDeleteCommand(raw.slice(7));promptEl.value="";return;}
  dimAllNodes();const cls=await classifyInput(raw);const ctx=buildContext();const spawn=getSmartSpawnPos();
  if(cls.type==="timer"&&cls.seconds){const n=addNode("timer "+cls.seconds+"s","timer",spawn.x,spawn.y,{seconds:cls.seconds,label:"timer"});startTimer(n);promptEl.value="";saveGraph();updateSuggestionsDebounced();return;}
  const qn=addNode(raw,"question",spawn.x,spawn.y);lastQuestionNodeId=qn.id;
  const an=addNode("","answer",spawn.x+340,spawn.y);addLink(qn.id,an.id);
  const anel=getNodeEl(an.id);if(anel)anel.classList.add("is-thinking");an.selected=true;if(anel)anel.classList.add("selected");updateNodeTextDOM(an);
  smartRecenter(true,spawn.x+170,spawn.y);promptEl.value="";
  const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:raw,context:ctx})});const d=await r.json();
  an.text=d.reply||"";if(anel)anel.classList.remove("is-thinking");updateNodeTextDOM(an);
  hasActiveContext=true;redrawLinks();saveGraph();updateSuggestionsDebounced();
}

document.getElementById("send-btn").onclick=sendPrompt;
document.getElementById("study-btn").onclick=async()=>{const ctx=buildContext();if(!ctx){alert("Select some nodes first.");return;}const spawn=getSmartSpawnPos();const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:"Create a short focused study drill or quiz. Keep it concise.",context:ctx})});const d=await r.json();addNode(d.reply||"","answer",spawn.x,spawn.y);redrawLinks();saveGraph();};

initZoom();initCanvas();loadSettings();

const tutSteps=[{t:"Welcome to SecondBrain",d:"Let's take a quick 1-minute tour to help you get started with your peaceful new knowledge graph."},{t:"1. Exploring Ideas",d:"Type your questions, ideas, or prompts in the bottom bar and hit Enter. The AI will beautifully lay out answers. Type / to see useful commands."},{t:"2. Connecting Thoughts",d:"Click to select a node. Shift+Click or Shift+Drag to select multiple. Press 'L' to link selected nodes together. Press 'S' to split links."},{t:"3. Curating Focus",d:"Drag nodes to move them. Hold down to pan around. Select nodes and press 'G' to group them into elegant, color-coded spaces."}];
let tutCurrent=0;
function updateTutUI(){document.getElementById("tut-title").textContent=tutSteps[tutCurrent].t;document.getElementById("tut-desc").textContent=tutSteps[tutCurrent].d;document.querySelectorAll(".tut-dot").forEach((d,i)=>d.classList.toggle("active",i===tutCurrent));document.getElementById("tut-next-btn").textContent=tutCurrent===tutSteps.length-1?"Find Your Zen":"Next Step →";}
function nextTutorialStep(){if(tutCurrent<tutSteps.length-1){tutCurrent++;updateTutUI();}else endTutorial();}
function endTutorial(){document.getElementById("tutorial-overlay").classList.remove("visible");localStorage.setItem("tut_completed","true");}

loadGraph().then(()=>{if(nodes.length>0||groups.some(g=>g.collapsed)){setTimeout(()=>smartRecenter(false),100);}if(!localStorage.getItem("tut_completed")&&nodes.length===0){document.getElementById("tutorial-overlay").classList.add("visible");updateTutUI();}});

document.getElementById("dash-btn").onclick=async()=>{
  if(SHARE_ID==='guest'){alert("Please sign in or sign up to save and view canvases.");return;}
  document.getElementById("dash-modal").classList.add("visible");
  const myList=document.getElementById("my-dash-list");const sharedList=document.getElementById("shared-dash-list");myList.innerHTML="Loading...";sharedList.innerHTML="Loading...";
  try{const r=await fetch("/api/dashboard");const d=await r.json();myList.innerHTML="";sharedList.innerHTML="";
    if(d.my_graphs&&d.my_graphs.length>0){d.my_graphs.forEach(item=>{const row=document.createElement("div");row.className="dash-item";const dateStr=item.updated_at?item.updated_at.split('T')[0]:'Recently';row.innerHTML=`<div style="flex:1;"><strong>${item.title}</strong><br><span style="color:var(--muted);font-size:11px;">Updated: ${dateStr}</span></div><div style="display:flex;align-items:center;"><button class="modal-btn" onclick="window.location.href='/b/${item.share_id}'" style="padding:6px 12px;font-size:11px;margin-right:8px;">${iconGo} Go</button><button class="dash-delete-btn" onclick="deleteCanvas('${item.share_id}',event)" title="Delete Canvas">${iconTrash}</button></div>`;myList.appendChild(row);});}else{myList.innerHTML="<div style='color:var(--muted);font-size:12px;padding:12px;'>No canvases yet.</div>";}
    if(d.shared_with_me&&d.shared_with_me.length>0){d.shared_with_me.forEach(item=>{const row=document.createElement("div");row.className="dash-item";const dateStr=item.added_at?item.added_at.split('T')[0]:'Recently';row.innerHTML=`<div style="flex:1;"><strong>${item.title}</strong> (${item.owner_email})<br><span style="color:var(--muted);font-size:11px;">Added: ${dateStr}</span></div><div style="display:flex;align-items:center;"><button class="modal-btn" onclick="window.location.href='/b/${item.share_id}'" style="padding:6px 12px;font-size:11px;margin-right:8px;">${iconGo} Go</button><button class="dash-delete-btn" onclick="leaveCanvas('${item.share_id}','${currentUserEmail}',event)" title="Leave Canvas">${iconDoor}</button></div>`;sharedList.appendChild(row);});}else{sharedList.innerHTML="<div style='color:var(--muted);font-size:12px;padding:12px;'>No graphs shared with you yet.</div>";}
  }catch(e){myList.innerHTML="Error loading.";sharedList.innerHTML="Error loading.";}
};

window.deleteCanvas=async function(share_id,event){event.stopPropagation();if(!confirm("Are you sure you want to permanently delete this canvas?"))return;try{const res=await fetch(`/api/board/${share_id}`,{method:"DELETE"});const d=await res.json();if(d.ok)document.getElementById("dash-btn").click();else alert(d.error||"Failed to delete");}catch(e){alert("Error deleting");}};
window.leaveCanvas=async function(share_id,email,event){event.stopPropagation();if(!confirm("Are you sure you want to leave this canvas?"))return;try{const res=await fetch("/share/remove",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({share_id:share_id,email:email})});const d=await res.json();if(d.ok)document.getElementById("dash-btn").click();else alert(d.error||"Failed to leave");}catch(e){alert("Error leaving");}};
document.getElementById("new-canvas-btn").onclick=async()=>{try{const res=await fetch("/api/board/new",{method:"POST"});const d=await res.json();if(d.share_id)window.location.href="/b/"+d.share_id;}catch(e){}};

if(socket){
  socket.on("connect",()=>{socket.emit("join",{room:SHARE_ID});});
  socket.on("presence_update",users=>{const pb=document.getElementById("presence-bar");pb.innerHTML="";users.forEach((u,i)=>{const init=(u.email||"A").substring(0,1).toUpperCase();const av=document.createElement("div");av.className="presence-avatar";av.style.background=u.color||"var(--accent)";av.style.zIndex=users.length-i;av.title=u.email+(u.email===currentUserEmail?" (You)":" - Click to jump");av.textContent=init;if(u.email!==currentUserEmail)av.onclick=()=>jumpToUser(u.email);pb.appendChild(av);});if(users.length>1){document.getElementById("chat-btn").style.display="flex";}else{document.getElementById("chat-btn").style.display="none";document.getElementById("chat-window").classList.remove("visible");}});
  socket.on("cursor_update",c=>{let cursor=remoteCursors[c.id];userLastPositions[c.email]={x:c.x,y:c.y};if(!cursor){cursor=document.createElement("div");cursor.className="remote-cursor";cursor.innerHTML=`<svg viewBox="0 0 16 16" fill="${c.color}"><path d="M1 1l6 14 2-5 5-2L1 1z" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg><div class="remote-cursor-label" style="background:${c.color}">${c.email.split('@')[0]}</div>`;document.getElementById("link-layer").parentElement.appendChild(cursor);remoteCursors[c.id]=cursor;}cursor.style.left=c.x+"px";cursor.style.top=c.y+"px";});
  socket.on("cursor_remove",data=>{if(remoteCursors[data.id]){remoteCursors[data.id].remove();delete remoteCursors[data.id];}});
  socket.on("collab_removed",data=>{if(data.email===currentUserEmail){alert("Your access to this canvas has been revoked by the owner.");window.location.href="/";}else{fetchCollaborators();}});
  socket.on("node_move",data=>{const n=nodes.find(x=>x.id===data.id);if(n&&(!draggingNode||draggingNode.id!==n.id)){n.x=data.x;n.y=data.y;const el=getNodeEl(n.id);if(el){el.style.left=n.x+"px";el.style.top=n.y+"px";}redrawLinks();}});
  socket.on("node_text",data=>{const n=nodes.find(x=>x.id===data.id);if(n){n.text=data.text;if(data.title!==undefined)n.meta.title=data.title;if(data.topic!==undefined)n.meta.topic=data.topic;updateNodeTextDOM(n);}});
  socket.on("group_action",data=>{if(data.action==="collapse")collapseGroup(data.gid,false);if(data.action==="expand")expandGroup(data.gid,true,false);});
  socket.on("title_update",data=>{});
  socket.on("chat_message",data=>{chatMessages.push(data.msg);renderChat();});
  socket.on("delete_chat_message",data=>{chatMessages=chatMessages.filter(m=>m.id!==data.id);renderChat();});
  socket.on("graph_sync",graphData=>{
    const incomingNodes=new Map(graphData.nodes.map(n=>[n.id,n]));
    nodes=nodes.filter(n=>{if(!incomingNodes.has(n.id)){const el=getNodeEl(n.id);if(el)el.remove();return false;}return true;});
    graphData.nodes.forEach(inNode=>{const exist=nodes.find(n=>n.id===inNode.id);if(exist){const isDragging=draggingNode&&draggingNode.id===exist.id;const isEditing=document.activeElement&&document.activeElement.closest(`.node[data-id="${exist.id}"]`);let structuralChange=false;if(exist.type!==inNode.type||exist.groupId!==inNode.groupId)structuralChange=true;if(!isDragging){exist.x=inNode.x;exist.y=inNode.y;}if(!isEditing){exist.text=inNode.text;exist.meta=inNode.meta;exist.completed=inNode.completed;}exist.type=inNode.type;exist.groupId=inNode.groupId;const el=getNodeEl(exist.id);if(el){if(structuralChange){el.remove();createNodeElement(exist);}else{if(!isDragging){el.style.left=exist.x+"px";el.style.top=exist.y+"px";}if(!isEditing){updateNodeTextDOM(exist);if(exist.completed)el.classList.add("completed");else el.classList.remove("completed");}if(exist.meta?.pinned)el.classList.add("pinned-highlight");else el.classList.remove("pinned-highlight");}}}else{nodes.push(inNode);createNodeElement(inNode);}});
    links=graphData.links;redrawLinks();groups=graphData.groups;redrawGroups();if(graphData.chat){chatMessages=graphData.chat;renderChat();}nextNodeId=graphData.nextNodeId;nextLinkId=graphData.nextLinkId;nextGroupId=graphData.nextGroupId;
  });
  let lastCursorSync=0;canvasWrapper.addEventListener("mousemove",e=>{const now=Date.now();if(now-lastCursorSync>50){const cc=clientToCanvas(e.clientX,e.clientY);socket.emit("cursor_move",{room:SHARE_ID,x:cc.x,y:cc.y});lastCursorSync=now;}});
}

function jumpToUser(email){if(userLastPositions[email])smartRecenter(true,userLastPositions[email].x,userLastPositions[email].y);}

async function fetchCollaborators(){if(SHARE_ID==='guest')return;try{const res=await fetch(`/api/collaborators/${SHARE_ID}`);const data=await res.json();const list=document.getElementById("collab-list");list.innerHTML="";if(data.collaborators&&data.collaborators.length>0){data.collaborators.forEach(c=>{const el=document.createElement("div");el.className="collab-item";el.innerHTML=`<span>${c.email}</span><div style="display:flex;gap:8px;align-items:center;"><span style="color:var(--muted)">Can Edit</span>${IS_OWNER?`<button class="remove-collab-btn" onclick="removeCollab('${c.email}')" title="Remove">✕</button>`:''}</div>`;list.appendChild(el);});document.getElementById("collab-wrap").style.display="flex";}else{document.getElementById("collab-wrap").style.display="none";}}catch(e){}}
window.removeCollab=async function(email){if(!confirm(`Are you sure you want to remove ${email} from this canvas?`))return;try{const res=await fetch("/share/remove",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({share_id:SHARE_ID,email:email})});const d=await res.json();if(d.ok)fetchCollaborators();else alert(d.error||"Could not remove user.");}catch(e){}};

document.getElementById("share-btn").onclick=async()=>{if(SHARE_ID==='guest'){alert("Sign in or Sign up to share canvases!");return;}const input=document.getElementById("share-link-input");input.value=window.location.href;fetchCollaborators();document.getElementById("share-modal").classList.add("visible");};
document.getElementById("share-copy-btn").onclick=()=>{const input=document.getElementById("share-link-input");input.select();document.execCommand("copy");const btn=document.getElementById("share-copy-btn");btn.innerHTML=`<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied!`;setTimeout(()=>btn.innerHTML=`<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy`,2000);};
document.getElementById("invite-btn").onclick=async()=>{const emailInput=document.getElementById("invite-email");const email=emailInput.value.trim();if(!email)return;const btn=document.getElementById("invite-btn");btn.textContent="Adding...";try{const res=await fetch("/share/invite",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({share_id:SHARE_ID,email:email})});const data=await res.json();if(data.ok){emailInput.value="";fetchCollaborators();}else{alert(data.error||"Could not add user.");}}catch(e){}btn.textContent="Invite";};
</script>
</body>
</html>"""

@app.route("/")
def index():
    if "user_id" not in session:
        session["next_url"] = "/"
        return redirect("/login")
    if session["user_id"] == -1:
        return redirect("/b/guest")
    conn = get_db()
    cursor = conn.cursor()
    run_query(cursor, "SELECT share_id FROM graphs WHERE user_id=%s ORDER BY updated_at DESC LIMIT 1", (session["user_id"],))
    row = cursor.fetchone()
    if row:
        share_id = row["share_id"]
    else:
        share_id = secrets.token_urlsafe(12)
        run_query(cursor, "INSERT INTO graphs (user_id, data, share_id, title) VALUES (%s, %s, %s, %s)", (session["user_id"], "{}", share_id, "Personal Graph"))
        conn.commit()
    cursor.close()
    conn.close()
    return redirect(f"/b/{share_id}")

def call_groq(messages):
    if not GROQ_API_KEY:
        return "GROQ_API_KEY environment variable is not set."
    r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                      json={"model": GROQ_MODEL, "messages": messages}, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def classify_with_groq(user_input):
    sys = (f"Classify a message. Time: {(datetime.utcnow() - timedelta(hours=7)).strftime('%A %B %d %Y %H:%M PDT')}.\n"
           "Return ONLY JSON. Types: timer(+seconds), ai_command(+command), question, text.\n"
           'Examples: {"type":"timer","seconds":180} | {"type":"question"} | {"type":"text"}')
    try:
        raw = call_groq([{"role": "system", "content": sys}, {"role": "user", "content": user_input}])
        d = json.loads(raw)
        if isinstance(d, dict) and "type" in d: return d
    except: pass
    return {"type": "question"}

def chat_with_groq(prompt, context):
    sys = (f"Concise assistant. Time: {(datetime.utcnow() - timedelta(hours=7)).strftime('%A %B %d %Y %H:%M PDT')}.\n"
           "1-3 sentences unless asked for more. Don't mention nodes/graphs/context/internal structure.")
    msgs = [{"role": "system", "content": sys}]
    if context: msgs.append({"role": "user", "content": "Context:\n" + context})
    msgs.append({"role": "user", "content": prompt})
    try: return call_groq(msgs)
    except Exception as e: return f"AI Error: Configure your Groq API Key."

def suggest_with_groq(prompt, context):
    sys = ('Generate 3 follow-up suggestions. Return ONLY JSON: {"suggestions":["...","...","..."]}. No nodes/graphs.')
    msgs = [{"role": "system", "content": sys}]
    if context: msgs.append({"role": "user", "content": "Context:\n" + context})
    msgs.append({"role": "user", "content": prompt})
    try:
        raw = call_groq(msgs)
        d = json.loads(raw)
        if isinstance(d, dict) and "suggestions" in d: return d["suggestions"]
    except: pass
    return []

def merge_with_groq(a, b):
    try: return call_groq([{"role": "system", "content": "Merge two texts into one concise clean version. Don't mention merging."}, {"role": "user", "content": "Text A:\n" + a}, {"role": "user", "content": "Text B:\n" + b}])
    except: return f"{a}\n\n{b}"

def find_with_groq(query, node_descs):
    sys = 'Graph search: find the single most relevant node. Return ONLY valid JSON: {"nodeId": <id>} or {"nodeId": null}. No markdown tags.'
    try:
        raw = call_groq([{"role": "system", "content": sys}, {"role": "user", "content": f"Query: {query}\n\nNodes:\n{json.dumps(node_descs)}"}])
        clean = raw.strip().strip("```json").strip("```").strip()
        d = json.loads(clean)
        if isinstance(d, dict) and "nodeId" in d: return d
    except: pass
    return {"nodeId": None}

@app.route("/brainstorm", methods=["POST"])
def brainstorm():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    topic = d.get("topic", "")
    sys = ('You are a brainstorm assistant. Provide concise, high-quality ideas. '
           'Limit generation: Do not spam nodes. Max 2-3 nodes total unless instructed otherwise. Provide very concise, focused sub-topics. '
           'Format: {"topic": "Root idea", "children": [{"topic": "Sub idea 1", "children": [...]}]} '
           'Return ONLY a valid JSON object. No markdown formatting.')
    try:
        raw = call_groq([{"role": "system", "content": sys}, {"role": "user", "content": topic}])
        clean = raw.strip().strip("```json").strip("```").strip()
        tree = json.loads(clean)
        if isinstance(tree, dict) and "topic" in tree:
            return jsonify({"tree": tree})
    except: pass
    return jsonify({"tree": {"topic": topic, "children": [{"topic": f"{topic} concept 1", "children": []}, {"topic": f"{topic} concept 2", "children": []}]}})

@app.route("/save_settings", methods=["POST"])
def save_settings():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    if session["user_id"] == -1: return jsonify({"ok": True})
    conn = get_db()
    cursor = conn.cursor()
    try:
        uid = session["user_id"]
        settings_data = json.dumps(request.get_json(), ensure_ascii=False)
        run_query(cursor, "SELECT user_id FROM user_settings WHERE user_id=%s", (uid,))
        if cursor.fetchone():
            run_query(cursor, "UPDATE user_settings SET settings=%s WHERE user_id=%s", (settings_data, uid))
        else:
            run_query(cursor, "INSERT INTO user_settings (user_id, settings) VALUES (%s, %s)", (uid, settings_data))
        conn.commit()
    except: conn.rollback()
    finally: cursor.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/load_settings", methods=["GET"])
def load_settings():
    if "user_id" not in session: return jsonify({}), 401
    if session["user_id"] == -1: return jsonify({})
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "SELECT settings FROM user_settings WHERE user_id=%s", (session["user_id"],))
        row = cursor.fetchone()
        if not row: return jsonify({}), 404
        return jsonify(json.loads(row['settings']) if DB_TYPE == "sqlite" else row['settings'])
    finally: cursor.close(); conn.close()

@app.route("/classify", methods=["POST"])
def classify():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    return jsonify(classify_with_groq(request.get_json().get("input", "")))

@app.route("/chat", methods=["POST"])
def do_chat():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    return jsonify({"reply": chat_with_groq(d.get("prompt", ""), d.get("context", ""))})

@app.route("/suggest", methods=["POST"])
def suggest():
    if "user_id" not in session: return jsonify({"suggestions": []}), 200
    d = request.get_json()
    return jsonify({"suggestions": suggest_with_groq(d.get("prompt", ""), d.get("context", ""))})

@app.route("/merge", methods=["POST"])
def merge_nodes():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    return jsonify({"merged": merge_with_groq(d.get("a", ""), d.get("b", ""))})

@app.route("/find", methods=["POST"])
def find():
    if "user_id" not in session: return jsonify({"nodeId": None}), 200
    d = request.get_json()
    result = find_with_groq(d.get("query", ""), d.get("nodes", []))
    return jsonify({"nodeId": result.get("nodeId")})

@app.route("/b/<share_id>")
def board(share_id):
    if "user_id" not in session:
        session["next_url"] = f"/b/{share_id}"
        return redirect("/login")
    if share_id == "guest":
        html = INDEX_HTML.replace("$$SHARE_ID$$", "guest").replace("$$IS_OWNER$$", "true").replace("$$BOARD_TITLE$$", "Guest Canvas (Not Saved)")
        return Response(html, mimetype="text/html")
    conn = get_db()
    cursor = conn.cursor()
    run_query(cursor, "SELECT user_id, title FROM graphs WHERE share_id=%s", (share_id,))
    graph = cursor.fetchone()
    if not graph:
        cursor.close(); conn.close()
        error_html = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Canvas Not Found</title><style>body{background:#F7F5F0;color:#3E3A35;font-family:system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;}h2{margin-bottom:12px;}p{color:#8A837A;margin-bottom:32px;}a{color:#fff;text-decoration:none;background:#A69076;padding:12px 24px;border-radius:12px;}</style></head><body><h2>Canvas Not Found</h2><p>This canvas may have been deleted, or the URL is incorrect.</p><a href="/">← Go to Dashboard</a></body></html>"""
        return Response(error_html, mimetype="text/html"), 404
    is_owner = (graph["user_id"] == session["user_id"])
    if not is_owner:
        run_query(cursor, "SELECT 1 FROM graph_collaborators WHERE graph_id=(SELECT id FROM graphs WHERE share_id=%s) AND user_id=%s", (share_id, session["user_id"]))
        if not cursor.fetchone():
            cursor.close(); conn.close()
            error_html = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Access Denied</title><style>body{background:#F7F5F0;color:#3E3A35;font-family:system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;}h2{margin-bottom:12px;}p{color:#8A837A;margin-bottom:32px;}a{color:#fff;text-decoration:none;background:#A69076;padding:12px 24px;border-radius:12px;}</style></head><body><h2>Access Denied</h2><p>You do not have access to this canvas. Ask the owner to invite you.</p><a href="/">← Go to Dashboard</a></body></html>"""
            return Response(error_html, mimetype="text/html"), 403
    cursor.close(); conn.close()
    html = INDEX_HTML.replace("$$SHARE_ID$$", share_id).replace("$$IS_OWNER$$", "true" if is_owner else "false").replace("$$BOARD_TITLE$$", graph["title"].replace("'", "\\'"))
    return Response(html, mimetype="text/html")

@app.route("/api/board/new", methods=["POST"])
def create_board():
    if "user_id" not in session or session["user_id"] == -1: return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        share_id = secrets.token_urlsafe(12)
        run_query(cursor, "INSERT INTO graphs (user_id, data, share_id, title) VALUES (%s, %s, %s, %s)", (session["user_id"], "{}", share_id, "New Canvas"))
        conn.commit()
        return jsonify({"share_id": share_id})
    except Exception as e:
        conn.rollback(); return jsonify({"error": str(e)}), 500
    finally: cursor.close(); conn.close()

@app.route("/api/board/<share_id>", methods=["DELETE"])
def delete_board(share_id):
    if "user_id" not in session or session["user_id"] == -1: return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "SELECT id, user_id FROM graphs WHERE share_id=%s", (share_id,))
        graph = cursor.fetchone()
        if not graph: return jsonify({"error": "Not found"}), 404
        if graph["user_id"] != session["user_id"]: return jsonify({"error": "Only the owner can delete this canvas."}), 403
        run_query(cursor, "DELETE FROM graphs WHERE id=%s", (graph["id"],))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback(); return jsonify({"error": str(e)}), 500
    finally: cursor.close(); conn.close()

@app.route("/api/board/load/<share_id>", methods=["GET"])
def load_shared(share_id):
    if "user_id" not in session: return jsonify({}), 401
    if share_id == "guest": return jsonify({})
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "SELECT data FROM graphs WHERE share_id=%s", (share_id,))
        row = cursor.fetchone()
        if not row: return jsonify({}), 404
        return jsonify(json.loads(row["data"]))
    except: return jsonify({}), 500
    finally: cursor.close(); conn.close()

@app.route("/api/board/title", methods=["POST"])
def update_title():
    if "user_id" not in session or session["user_id"] == -1: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    share_id = d.get("share_id"); title = d.get("title", "Untitled Canvas")
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "UPDATE graphs SET title=%s WHERE share_id=%s AND user_id=%s", (title, share_id, session["user_id"]))
        conn.commit()
        return jsonify({"ok": True})
    finally: cursor.close(); conn.close()

@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    if "user_id" not in session or session["user_id"] == -1: return jsonify({}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        uid = session["user_id"]
        run_query(cursor, "SELECT share_id, title, updated_at FROM graphs WHERE user_id = %s ORDER BY updated_at DESC", (uid,))
        my_graphs = [dict(row) for row in cursor.fetchall()] if DB_TYPE == "sqlite" else cursor.fetchall()
        for row in my_graphs:
            if row.get("updated_at") and not isinstance(row["updated_at"], str): row["updated_at"] = row["updated_at"].isoformat()
        run_query(cursor, "SELECT g.share_id, g.title, gc.added_at, g.updated_at, u.email as owner_email FROM graph_collaborators gc JOIN graphs g ON gc.graph_id = g.id JOIN users u ON g.user_id = u.id WHERE gc.user_id = %s ORDER BY gc.added_at DESC", (uid,))
        shared_with_me = [dict(row) for row in cursor.fetchall()] if DB_TYPE == "sqlite" else cursor.fetchall()
        for row in shared_with_me:
            if row.get("added_at") and not isinstance(row["added_at"], str): row["added_at"] = row["added_at"].isoformat()
            if row.get("updated_at") and not isinstance(row["updated_at"], str): row["updated_at"] = row["updated_at"].isoformat()
        return jsonify({"my_graphs": my_graphs, "shared_with_me": shared_with_me})
    except Exception as e: return jsonify({"error": str(e)}), 500
    finally: cursor.close(); conn.close()

@app.route("/api/collaborators/<share_id>", methods=["GET"])
def get_collaborators(share_id):
    if "user_id" not in session: return jsonify({}), 401
    if share_id == "guest": return jsonify({"collaborators": []})
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "SELECT u.email FROM graph_collaborators gc JOIN graphs g ON gc.graph_id = g.id JOIN users u ON gc.user_id = u.id WHERE g.share_id = %s", (share_id,))
        collabs = [dict(row) for row in cursor.fetchall()] if DB_TYPE == "sqlite" else cursor.fetchall()
        return jsonify({"collaborators": collabs})
    finally: cursor.close(); conn.close()

@app.route("/share/invite", methods=["POST"])
def invite_collaborator():
    if "user_id" not in session or session["user_id"] == -1: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    email = d.get("email", "").strip().lower(); share_id = d.get("share_id")
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "SELECT id FROM graphs WHERE share_id=%s AND user_id=%s", (share_id, session["user_id"]))
        graph = cursor.fetchone()
        if not graph: return jsonify({"error": "Only the canvas owner can invite people."})
        run_query(cursor, "SELECT id FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        if not user: return jsonify({"error": "User not found. Ask them to sign up first!"})
        if user["id"] == session["user_id"]: return jsonify({"error": "You already own this canvas."})
        run_query(cursor, "INSERT INTO graph_collaborators (graph_id, user_id) VALUES (%s, %s) ON CONFLICT(graph_id, user_id) DO NOTHING" if DB_TYPE == "postgres" else "INSERT OR IGNORE INTO graph_collaborators (graph_id, user_id) VALUES (%s, %s)", (graph["id"], user["id"]))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback(); return jsonify({"error": "Database error."})
    finally: cursor.close(); conn.close()

@app.route("/share/remove", methods=["POST"])
def remove_collaborator():
    if "user_id" not in session or session["user_id"] == -1: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    email = d.get("email", "").strip().lower(); share_id = d.get("share_id")
    conn = get_db()
    cursor = conn.cursor()
    try:
        run_query(cursor, "SELECT id, user_id FROM graphs WHERE share_id=%s", (share_id,))
        graph = cursor.fetchone()
        if not graph: return jsonify({"error": "Canvas not found."})
        run_query(cursor, "SELECT id FROM users WHERE email=%s", (email,))
        target_user = cursor.fetchone()
        if not target_user: return jsonify({"error": "User not found."})
        is_owner = (graph["user_id"] == session["user_id"])
        is_self = (target_user["id"] == session["user_id"])
        if not (is_owner or is_self): return jsonify({"error": "Only the canvas owner can remove other collaborators."})
        run_query(cursor, "DELETE FROM graph_collaborators WHERE graph_id=%s AND user_id=%s", (graph["id"], target_user["id"]))
        conn.commit()
        socketio.emit("collab_removed", {"email": email}, to=share_id)
        return jsonify({"ok": True})
    finally: cursor.close(); conn.close()

connected_users = {}

@socketio.on("join")
def on_join(data):
    room = data.get("room")
    if not room or room == "guest": return
    email = session.get("email", "Anonymous")
    join_room(room)
    if room not in connected_users: connected_users[room] = {}
    colors = ["#C2A878","#B5996D","#A39B8B","#D4C3A3","#B89A9A","#9E9B85","#C88A7A","#D9CFC1"]
    color = colors[len(connected_users[room]) % len(colors)]
    connected_users[room][request.sid] = {"email": email, "color": color}
    emit("presence_update", list(connected_users[room].values()), to=room)

@socketio.on("disconnect")
def on_disconnect():
    for room, users in connected_users.items():
        if request.sid in users:
            del users[request.sid]
            emit("presence_update", list(users.values()), to=room)
            emit("cursor_remove", {"id": request.sid}, to=room)

@socketio.on("cursor_move")
def on_cursor_move(data):
    room = data.get("room")
    if room and room != "guest":
        emit("cursor_update", {"id": request.sid, "x": data.get("x"), "y": data.get("y"), "email": session.get("email", "Anonymous"), "color": connected_users.get(room, {}).get(request.sid, {}).get("color", "#A69076")}, to=room, include_self=False)

@socketio.on("node_move")
def on_node_move(data):
    room = data.get("room")
    if room and room != "guest":
        emit("node_move", {"id": data.get("id"), "x": data.get("x"), "y": data.get("y")}, to=room, include_self=False)

@socketio.on("node_text")
def on_node_text(data):
    room = data.get("room")
    if room and room != "guest":
        emit("node_text", data, to=room, include_self=False)

@socketio.on("group_action")
def on_group_action(data):
    room = data.get("room")
    if room and room != "guest":
        emit("group_action", data, to=room, include_self=False)

@socketio.on("title_update")
def on_title_update(data):
    room = data.get("room")
    if room and room != "guest":
        emit("title_update", data, to=room, include_self=False)

@socketio.on("chat_message")
def on_chat_message(data):
    room = data.get("room")
    if room and room != "guest":
        emit("chat_message", data, to=room, include_self=False)

@socketio.on("delete_chat_message")
def on_delete_chat_message(data):
    room = data.get("room")
    if room and room != "guest":
        emit("delete_chat_message", data, to=room, include_self=False)

@socketio.on("graph_update")
def on_graph_update(data):
    room = data.get("room")
    if room and room != "guest":
        conn = get_db()
        cursor = conn.cursor()
        try:
            run_query(cursor, "UPDATE graphs SET data=%s, updated_at=CURRENT_TIMESTAMP WHERE share_id=%s", (json.dumps(data.get("graph")), room))
            conn.commit()
        except: pass
        finally: cursor.close(); conn.close()
        emit("graph_sync", data.get("graph"), to=room, include_self=False)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "4000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)

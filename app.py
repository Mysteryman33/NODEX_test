import gevent.monkey
gevent.monkey.patch_all()

import os
import json
import secrets
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, Response, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
import uuid

GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Secure your API key by pulling it from Render's environment!
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(days=30)
socketio = SocketIO(app, cors_allowed_origins="*")

# ── DB (Updated for PostgreSQL) ───────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    cursor = conn.cursor()
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
    conn.commit()
    try:
        cursor.execute("ALTER TABLE graphs ADD COLUMN share_id TEXT UNIQUE;")
        conn.commit()
    except:
        conn.rollback()
    try:
        cursor.execute("ALTER TABLE graphs ADD COLUMN title TEXT DEFAULT 'Untitled Canvas';")
        conn.commit()
    except:
        conn.rollback()
    cursor.close()
    conn.close()

if DATABASE_URL:
    init_db()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ── Auth routes ───────────────────────────────────────────────────────────────

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>SecondBrain — Sign In</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0;}
:root{
  --bg:#050508;
  --surface:#0a0a0f;
  --border:#1a1a2e;
  --text:#f8f8f8;
  --muted:#6b7280;
  --accent:#8b5cf6;
  --accent-glow:rgba(139,92,246,0.4);
  --green:#10b981;
  --red:#ef4444;
}
body{
  background:var(--bg);
  color:var(--text);
  font-family:'Inter', system-ui, -apple-system, sans-serif;
  min-height:100vh;
  display:flex;
  align-items:center;
  justify-content:center;
  overflow:hidden;
  -webkit-font-smoothing: antialiased;
}
.bg-grid{
  position:fixed;inset:0;pointer-events:none;
  background-image:
    linear-gradient(rgba(139,92,246,0.08) 1px,transparent 1px),
    linear-gradient(90deg,rgba(139,92,246,0.08) 1px,transparent 1px);
  background-size:40px 40px;
}
.glow-orb{
  position:fixed;width:600px;height:600px;border-radius:50%;
  background:radial-gradient(circle,rgba(139,92,246,0.12) 0%,transparent 70%);
  pointer-events:none;
  top:50%;left:50%;transform:translate(-50%,-50%);
  filter: blur(40px);
}
.card{
  position:relative;z-index:1;
  width:380px;
  background:rgba(10, 10, 15, 0.7);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border:1px solid rgba(255,255,255,0.08);
  border-radius:24px;
  padding:40px;
  box-shadow:0 24px 60px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.1);
  animation: floatIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
@keyframes floatIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
.logo{
  font-size:12px;letter-spacing:.15em;text-transform:uppercase;font-weight: 600;
  color:var(--text);margin-bottom:12px;display:flex;align-items:center;gap:8px;
}
.logo-dot{
  width:8px;height:8px;border-radius:50%;background:var(--accent);
  box-shadow:0 0 12px var(--accent-glow);
  animation:pulse 2s ease-in-out infinite;
}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 12px var(--accent-glow);}50%{opacity:.6;box-shadow:0 0 20px var(--accent-glow);}}
h1{font-size:24px;font-weight:700;margin-bottom:6px;letter-spacing:-0.02em;}
.subtitle{font-size:13px;color:var(--muted);margin-bottom:32px;}
.tabs{display:flex;gap:4px;margin-bottom:28px;background:rgba(0,0,0,0.3);border-radius:12px;padding:4px;border:1px solid rgba(255,255,255,0.05);}
.tab{flex:1;padding:8px;font-family:inherit;font-size:13px;font-weight:500;border:none;border-radius:8px;cursor:pointer;transition:all .2s;color:var(--muted);background:transparent;}
.tab.active{background:rgba(255,255,255,0.1);color:var(--text);box-shadow:0 2px 8px rgba(0,0,0,0.2);}
.field{margin-bottom:16px;}
label{font-size:11px;font-weight:500;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:8px;}
input{
  width:100%;padding:12px 14px;
  background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.08);
  border-radius:10px;color:var(--text);font-family:inherit;font-size:14px;
  outline:none;transition:all .2s;
}
input:focus{border-color:var(--accent);background:rgba(0,0,0,0.4);box-shadow:0 0 0 3px rgba(139,92,246,0.15);}
.remember{display:flex;align-items:center;gap:8px;margin-bottom:24px;cursor:pointer;}
.remember input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent);border-radius:4px;}
.remember span{font-size:13px;color:var(--muted);}
.btn{
  width:100%;padding:12px;
  background:linear-gradient(135deg, var(--accent), #6d28d9);border:none;border-radius:10px;
  color:#fff;font-family:inherit;font-size:14px;font-weight:600;
  cursor:pointer;transition:all .2s;
  box-shadow:0 8px 24px rgba(139,92,246,0.25);
}
.btn:hover{transform:translateY(-1px);box-shadow:0 12px 32px rgba(139,92,246,0.4);}
.btn:active{transform:translateY(1px);}
.msg{font-size:12px;margin-top:16px;padding:10px 14px;border-radius:8px;display:none;font-weight:500;}
.msg.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:var(--red);}
.msg.success{background:rgba(16,185,129,0.1);border:1px solid rgba(16,185,129,0.2);color:var(--green);}
</style>
</head>
<body>
<div class="bg-grid"></div>
<div class="glow-orb"></div>
<div class="card">
  <div class="logo"><div class="logo-dot"></div>SecondBrain</div>
  <h1>Welcome back</h1>
  <div class="subtitle">Your knowledge graph awaits.</div>
  <div class="tabs">
    <button class="tab active" id="tab-signin" onclick="switchTab('signin')">Sign In</button>
    <button class="tab" id="tab-signup" onclick="switchTab('signup')">Sign Up</button>
  </div>
  <div class="field"><label>Email</label><input type="email" id="email" placeholder="you@example.com" autocomplete="email"/></div>
  <div class="field"><label>Password</label><input type="password" id="password" placeholder="••••••••" autocomplete="current-password"/></div>
  <label class="remember">
    <input type="checkbox" id="remember" checked/>
    <span>Keep me signed in for 30 days</span>
  </label>
  <button class="btn" onclick="submit()">Sign In →</button>
  <div class="msg" id="msg"></div>
</div>
<script>
let mode='signin';
function switchTab(t){
  mode=t;
  document.getElementById('tab-signin').classList.toggle('active',t==='signin');
  document.getElementById('tab-signup').classList.toggle('active',t==='signup');
  document.querySelector('.btn').textContent=t==='signin'?'Sign In →':'Create Account →';
  document.getElementById('msg').style.display='none';
}
async function submit(){
  const email=document.getElementById('email').value.trim();
  const password=document.getElementById('password').value;
  const remember=document.getElementById('remember').checked;
  const msg=document.getElementById('msg');
  msg.style.display='none';
  if(!email||!password){msg.className='msg error';msg.textContent='Please fill in all fields.';msg.style.display='block';return;}
  const endpoint=mode==='signin'?'/auth/login':'/auth/signup';
  const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password,remember})});
  const d=await r.json();
  if(d.ok){
    msg.className='msg success';msg.textContent='Success! Redirecting…';msg.style.display='block';
    const nextUrl = d.next_url || '/';
    setTimeout(()=>window.location.href=nextUrl,500);
  }
  else{msg.className='msg error';msg.textContent=d.error||'Something went wrong.';msg.style.display='block';}
}
document.addEventListener('keydown',e=>{if(e.key==='Enter')submit();});
</script>
</body>
</html>"""

@app.route("/login")
def login_page():
    if "user_id" in session:
        return redirect("/")
    return Response(LOGIN_HTML, mimetype="text/html")

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
        cursor.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id", (email, hash_password(pw)))
        user_id = cursor.fetchone()["id"]
        conn.commit()
        
        session.permanent = remember
        session["user_id"] = user_id
        session["email"] = email
        
        next_url = session.pop("next_url", "/")
        return jsonify({"ok": True, "next_url": next_url})
    except psycopg2.IntegrityError:
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
    cursor.execute("SELECT * FROM users WHERE email=%s AND password_hash=%s", (email, hash_password(pw)))
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

# ── Main app ──────────────────────────────────────────────────────────────────
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>SecondBrain</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
:root{
  --ui-scale: 1.0;
  
  /* Modern Glass Dark Theme */
  --bg:#050508;
  --canvas-bg:#050508;
  --surface:rgba(18, 18, 24, 0.65);
  --surface2:rgba(28, 28, 36, 0.8);
  --border:rgba(255,255,255,0.08);
  --border2:rgba(255,255,255,0.12);
  --text:#f8f8f8;
  --muted:#9ca3af;
  --muted2:#6b7280;

  /* Accent palette */
  --accent:#8b5cf6;
  --accent2:#7c3aed;
  --accent-glow:rgba(139,92,246,0.35);
  --accent-soft:rgba(139,92,246,0.15);

  --green:#10b981;
  --green-glow:rgba(16,185,129,0.35);
  --blue:#3b82f6;
  --blue-glow:rgba(59,130,246,0.35);
  --yellow:#f59e0b;
  --orange:#f97316;
  --purple:#a78bfa;
  --purple-glow:rgba(167,139,250,0.35);

  --node-q:#c4b5fd;
  --node-a:#6ee7b7;
  --node-note:#93c5fd;
  --node-timer:#fbbf24;
  --node-file:#818cf8;

  --ring-bg:#1e1e28;
  --select-color: #fcd34d;
}
*{box-sizing:border-box;}
body{margin:0;padding:0;background:var(--bg);color:var(--text);
  font-family:'Inter', system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
  overflow:hidden;}

#app{position:fixed;inset:0;display:flex;flex-direction:column;z-index:1;}

/* Hide scrollbars */
*::-webkit-scrollbar{display:none;}
*{-ms-overflow-style:none;scrollbar-width:none;}

#top-bar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 200;
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 24px;
  background: rgba(8, 8, 12, 0.65); backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.topbar-left, .topbar-center, .topbar-right { display: flex; gap: 8px; align-items: center; }
.topbar-center { position: absolute; left: 50%; transform: translateX(-50%); background: rgba(255,255,255,0.03); padding: 4px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.05); }

.board-title-input {
  background: transparent; border: none; color: var(--text);
  font-family: inherit; font-weight: 600; font-size: calc(15px * var(--ui-scale));
  outline: none; padding: calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));
  border-radius: 8px; transition: background 0.2s, box-shadow 0.2s;
  min-width: 150px; max-width: 300px; letter-spacing: -0.01em;
}
.board-title-input:hover, .board-title-input:focus {
  background: var(--surface2); box-shadow: inset 0 0 0 1px var(--border2);
}

.user-badge{
  font-size:calc(11px * var(--ui-scale));color:var(--muted2); font-weight: 500;
  padding:calc(6px * var(--ui-scale)) calc(12px * var(--ui-scale));
  border:1px solid var(--border);border-radius:8px;
  background:var(--surface);
}
.user-badge a{color:var(--accent);text-decoration:none; margin-left: 4px;}
.user-badge a:hover{color:#a78bfa;}

#canvas-wrapper{
  flex:1;overflow:scroll;position:relative;background:var(--canvas-bg);cursor:default;
}

/* Scalable Background Grid on the Canvas */
#canvas{
  position:absolute;top:0;left:0;
  background-image:
    linear-gradient(rgba(139,92,246,0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(139,92,246,0.05) 1px, transparent 1px);
  background-size: 40px 40px;
}
#link-layer{position:absolute;inset:0;pointer-events:auto;}

/* Lasso Selection Box */
#lasso-box {
  position: absolute;
  border: 1px solid rgba(139,92,246,0.8);
  background: rgba(139,92,246, 0.15);
  border-radius: 4px;
  pointer-events: none;
  z-index: 5000;
  display: none;
  backdrop-filter: blur(2px);
}

@keyframes popIn { 0% { opacity: 0; transform: scale(0.95) translateY(10px); } 100% { opacity: 1; transform: scale(1) translateY(0); } }

/* ── NODES ── */
.node{position:absolute;cursor:grab;user-select:none;font-size:13px;line-height:1.5;z-index:10; animation: popIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);}
.node-circle{
  width:12px;height:12px;border-radius:50%;margin-bottom:6px;
  flex-shrink:0;transition:box-shadow .2s,transform .2s; border: 2px solid var(--bg);
}
.node-text{position:relative; white-space:pre-wrap;color:var(--text);transition:opacity .2s ease;}

.dim-0 .node-text{opacity:1;}.dim-1 .node-text{opacity:.75;}
.dim-2 .node-text{opacity:.55;}.dim-3 .node-text{opacity:.35;}.dim-4 .node-text{opacity:.18;}
.node:hover .node-text{opacity:1!important;}
.node:hover .node-circle{transform:scale(1.25);}

/* Expandable content */
.content-box { max-height: 150px; overflow-y: auto; overflow-wrap: anywhere; padding-right: 4px; max-width: 320px; font-weight: 400;}
.content-box.expanded { max-height: none; }
.expand-btn {
  background: transparent; border: none; color: var(--accent); font-weight: 500;
  font-size: 11px; cursor: pointer; padding: 6px 0 0 0; display: none;
  font-family: inherit; width: 100%; text-align: left;
}
.expand-btn:hover { color: #a78bfa; }

/* Node type colors */
.node-question .node-circle, .node-text .node-circle {background:var(--node-q);box-shadow:0 0 12px var(--purple-glow),0 0 4px var(--purple-glow);}
.node-answer .node-circle{background:var(--node-a);box-shadow:0 0 12px var(--green-glow),0 0 4px var(--green-glow);}
.node-answer.completed .node-circle{background:var(--blue);box-shadow:0 0 12px var(--blue-glow),0 0 4px var(--blue-glow);}
.node-timer .node-circle{background:var(--node-timer);box-shadow:0 0 12px rgba(251,191,36,0.4),0 0 4px rgba(251,191,36,0.4);}
.node-timer.completed .node-circle{background:var(--blue);box-shadow:0 0 12px var(--blue-glow),0 0 4px var(--blue-glow);}
.node-note .node-circle{background:var(--node-note);box-shadow:0 0 12px rgba(147,197,253,0.4),0 0 4px rgba(147,197,253,0.4);}
.node-brainstorm .node-circle{background:var(--orange);box-shadow:0 0 12px rgba(249,115,22,0.4),0 0 4px rgba(249,115,22,0.4);}
.node-file .node-circle{background:var(--node-file);box-shadow:0 0 12px rgba(129,140,248,0.4),0 0 4px rgba(129,140,248,0.4);}

.node.selected .node-circle{background:var(--select-color)!important;box-shadow:0 0 0 2px var(--select-color),0 0 20px var(--accent-glow),0 0 8px var(--accent-glow)!important;transform:scale(1.3);}
.node.ctrl-highlight .node-circle{outline:2px solid var(--orange);outline-offset:3px;box-shadow:0 0 16px rgba(249,115,22,0.5)!important;}
.node.find-focus .node-circle{box-shadow:0 0 0 4px var(--accent),0 0 32px var(--accent-glow)!important;animation:find-pulse 1s ease-in-out 3;}
@keyframes find-pulse{0%,100%{transform:scale(1);}50%{transform:scale(1.5);}}

.group-badge{position:absolute;top:-10px;left:-6px;width:10px;height:10px;border-radius:50%;border:2px solid var(--bg);z-index:6;}

.bubble{
  min-width: 180px; min-height: 60px; max-width: 420px; max-height: 280px;
  padding:12px 14px; border-radius:16px;
  border:1px solid var(--border); background:rgba(20, 20, 28, 0.85); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px rgba(0,0,0,0.25);
  position: relative; overflow-y: auto; overflow-wrap: anywhere; resize: both;
}
.bubble-header{display:flex;justify-content:flex-end;margin-bottom:6px;}
.copy-btn{
  background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:var(--muted);
  font-size:10px; font-weight: 500; padding:4px 8px;border-radius:100px;cursor:pointer;
  font-family:inherit;transition:all .2s;
}
.copy-btn:hover{border-color:var(--accent);color:var(--text); background: rgba(139,92,246,0.1);}

.note-wrap{display:flex;flex-direction:column;gap:6px; position:relative;}
.note-title{
  background:transparent;border:none;border-bottom:1px solid var(--border);
  color:var(--text);font-family:inherit;font-size:14px;font-weight:600;
  padding:4px;width:100%;outline:none; transition: border-color 0.2s;
}
.note-title:focus{border-bottom-color:var(--accent);}
.note-body{
  min-width:180px;min-height:100px; max-width: none; max-height: 300px;
  overflow-y:auto; overflow-wrap: anywhere; resize:both;
  background:var(--surface2);border:1px solid var(--border);border-radius:12px;
  color:var(--text);font-family:inherit;font-size:13px;padding:12px;outline:none;
  box-shadow: 0 4px 16px rgba(0,0,0,0.15); transition: border-color 0.2s, box-shadow 0.2s;
}
.note-body:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft);}

.brainstorm-wrap { display: flex; flex-direction: column; gap: 6px; width: 280px;}
.brainstorm-input { 
  background: var(--surface2); border: 1px solid var(--border); 
  color: var(--text); padding: 10px 12px; font-size: 13px; outline: none; 
  border-radius: 10px; font-family: inherit; resize: vertical; 
  min-height: 60px; max-height: 220px; width: 100%; white-space: pre-wrap;
  box-shadow: inset 0 2px 4px rgba(0,0,0,0.1); transition: border-color 0.2s;
}
.brainstorm-input:focus { border-color: var(--orange); box-shadow:0 0 0 3px rgba(249,115,22,0.15);}
.brainstorm-run { background: var(--orange); color: white; font-weight: 600; font-family: inherit; border: none; padding: 10px; font-size: 12px; cursor: pointer; border-radius: 8px; transition: transform 0.2s, box-shadow 0.2s; box-shadow: 0 4px 12px rgba(249,115,22,0.3); }
.brainstorm-run:hover { transform: translateY(-1px); box-shadow: 0 6px 16px rgba(249,115,22,0.4); }
.brainstorm-run:active { transform: translateY(1px); }

.node-file-wrap {
  background: rgba(28,28,36,0.8); backdrop-filter: blur(12px); border: 1px solid var(--border2);
  border-radius: 16px; padding: 16px; width: 200px; cursor: pointer;
  transition: all 0.2s; box-shadow: 0 8px 24px rgba(0,0,0,0.2);
}
.node-file-wrap:hover { border-color: var(--node-file); transform: translateY(-2px); box-shadow: 0 12px 32px rgba(129,140,248,0.2); }
.node-file-wrap.drag-over { border-color: var(--node-file); background: rgba(129,140,248,0.15); border-style: dashed; }
.file-preview { max-width: 100%; max-height: 140px; border-radius: 8px; pointer-events: none; margin-top: 8px; object-fit: contain;}

.timer-ring{position:relative;width:90px;height:90px;display:flex;align-items:center;justify-content:center;}
.timer-ring svg{position:absolute;inset:0;transform:rotate(-90deg); filter: drop-shadow(0 4px 8px rgba(0,0,0,0.3));}
.ring-bg{fill:none;stroke:var(--ring-bg);stroke-width:6;}
.ring-progress{fill:none;stroke-width:6;stroke-linecap:round;stroke:url(#timerGradient);transition:stroke .2s ease;}
.timer-text{position:relative;font-size:13px;font-weight:600;letter-spacing:.02em;}

.group-hull{
  position:absolute;border-radius:24px;border:1px solid;
  pointer-events:all;z-index:2;cursor:move;
  transition:opacity .3s ease;
}
.group-hull:not(.collapsed){opacity:.08;}
.group-hull:not(.collapsed):hover{opacity:.15;}
.group-hull.collapsed{opacity:.3; border-radius: 16px; backdrop-filter: blur(8px);}
.group-hull.collapsed:hover{opacity:.45;}
.group-label{
  position:absolute;font-size:11px;font-weight:600;opacity:.6;pointer-events:none;
  z-index:3;letter-spacing:.08em;text-transform:uppercase;
}
.group-label.collapsed-label{pointer-events:all;cursor:pointer;opacity:.9;}
.group-resize-handle{
  position:absolute;width:14px;height:14px;
  background:rgba(255,255,255,.4);border-radius:4px;
  cursor:se-resize;z-index:12;right:-7px;bottom:-7px;pointer-events:all;
  transition: background 0.2s; box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
.group-resize-handle:hover{background:rgba(255,255,255,.9);}

#ctx-menu{
  position:fixed;background:rgba(18, 18, 24, 0.9); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  border:1px solid var(--border2);
  border-radius:12px;padding:6px;z-index:300;display:none;min-width:180px;
  box-shadow:0 12px 40px rgba(0,0,0,.5),0 0 0 1px rgba(255,255,255,0.05);
  animation: popIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
#ctx-menu.visible{display:block;}
.ctx-item{padding:10px 14px;font-size:12px;font-weight:500;cursor:pointer;border-radius:8px;color:var(--text); transition: background 0.1s;}
.ctx-item:hover{background:rgba(255,255,255,0.08);}
.ctx-item.danger{color:#f87171;}
.ctx-item.danger:hover{background:rgba(248,113,113,0.15);}

#suggestions-bar{
  position: fixed; bottom: 105px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 10px; z-index: 999;
  background: transparent; border: none; padding: 0; min-height: auto;
}
.suggestion-btn{
  background: rgba(18, 18, 24, 0.85); backdrop-filter: blur(16px);
  border: 1px solid var(--border); border-radius: 100px;
  padding: 8px 18px; font-size: 12px; font-weight: 500; color:var(--text); cursor:pointer;
  transition:all .2s; max-width: 340px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  box-shadow: 0 8px 24px rgba(0,0,0,0.3);
}
.suggestion-btn:hover{border-color:var(--accent);color:var(--text);box-shadow:0 8px 32px rgba(139,92,246,0.25); transform: translateY(-2px);}

#input-bar {
  position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%);
  display: flex; gap: 12px; padding: 12px 16px;
  background: rgba(18, 18, 24, 0.85); backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(255,255,255,0.1); border-radius: 24px;
  width: 680px; max-width: 90vw;
  box-shadow: 0 24px 48px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05);
  z-index: 1000; align-items: flex-end; transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
#input-bar:focus-within {
  box-shadow: 0 24px 64px rgba(0,0,0,0.6), 0 0 0 1px rgba(139,92,246,0.3);
  transform: translateX(-50%) translateY(-2px);
  border-color: rgba(139,92,246,0.4);
}
#prompt {
  flex: 1; resize: none; background: transparent; border: none;
  color: var(--text); padding: 8px 6px; font-family: inherit; font-size: 14px;
  outline: none; max-height: 140px; min-height: 24px; overflow-y: auto;
  line-height: 1.5; margin-bottom: 2px;
}
#prompt::placeholder { color: var(--muted); font-weight: 400; }
#send-btn {
  width: 40px; height: 40px; border-radius: 14px; padding: 0;
  background: linear-gradient(135deg, var(--accent), #6d28d9); color: white; border: none;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: transform 0.2s, box-shadow 0.2s;
  font-size: 18px; font-weight: bold; flex-shrink: 0;
  box-shadow: 0 4px 16px rgba(139,92,246,0.3);
}
#send-btn:hover { transform: scale(1.05); box-shadow: 0 8px 24px rgba(139,92,246,0.5); }
#send-btn:active { transform: scale(0.95); }

.top-btn{
  background:transparent;border:none;color:var(--muted);
  padding:calc(8px * var(--ui-scale)) calc(14px * var(--ui-scale));
  border-radius:10px;font-family:inherit;font-size:calc(12px * var(--ui-scale)); font-weight: 500;
  cursor:pointer; transition:all .2s;white-space:nowrap;
}
.top-btn:hover{background:rgba(255,255,255,0.08);color:var(--text);}
.top-btn:active{background:rgba(255,255,255,0.04);}

#zoom-controls{
  position:fixed;bottom:130px;right:24px;display:flex;flex-direction:column;gap:6px;z-index:200;
  background: rgba(18, 18, 24, 0.7); backdrop-filter: blur(16px); padding: 6px; border-radius: 12px;
  border: 1px solid var(--border); box-shadow: 0 8px 24px rgba(0,0,0,0.3);
}
.zoom-btn{
  background:transparent;border:none;color:var(--text);
  width:32px;height:32px;border-radius:8px;font-family:inherit;font-size:16px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;transition:all .2s;
}
.zoom-btn:hover{background:rgba(255,255,255,0.1);}
#zoom-label{
  color:var(--muted); font-size:10px; font-weight: 600; text-align:center; padding:4px 0; font-family:inherit;
}

#slash-popup{
  position:absolute; bottom:calc(100% + 16px); left:0;
  background:rgba(18, 18, 24, 0.95); backdrop-filter: blur(16px); border:1px solid var(--border2);
  border-radius:16px;padding:8px;z-index:1000;display:none;min-width:300px;
  box-shadow:0 16px 48px rgba(0,0,0,0.6),0 0 0 1px rgba(255,255,255,0.05);
  animation: popIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
#slash-popup.visible{display:block;}
.slash-item{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:10px;cursor:pointer; transition: background 0.1s;}
.slash-item:hover,.slash-item.active{background:rgba(255,255,255,0.08);}
.slash-item-cmd{color:var(--accent);font-size:13px;font-weight:600;white-space:nowrap;min-width:70px;}
.slash-item-desc{color:var(--muted);font-size:12px;line-height:1.4;}

#color-picker-popup{
  position:fixed;background:rgba(18, 18, 24, 0.95); backdrop-filter: blur(20px); border:1px solid var(--border2);
  border-radius:16px;padding:16px;z-index:1500;display:none;
  box-shadow:0 16px 48px rgba(0,0,0,.6);flex-direction:column;gap:12px;min-width:240px;
  animation: popIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
#color-picker-popup.visible{display:flex;}
.color-picker-title{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;}
.color-swatches{display:flex;gap:8px;flex-wrap:wrap;}
.color-swatch{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:all .2s;}
.color-swatch:hover{transform:scale(1.15); box-shadow: 0 4px 12px rgba(0,0,0,0.3);}
.color-swatch.active{border-color:white;box-shadow:0 0 12px currentColor; transform: scale(1.1);}
.color-picker-input{
  background:rgba(0,0,0,0.3);border:1px solid var(--border);border-radius:8px;
  color:var(--text);font-family:inherit;font-size:13px;padding:8px 12px;width:100%;outline:none; transition: border-color 0.2s;
}
.color-picker-input:focus{border-color:var(--accent);}
.color-picker-actions{display:flex;gap:8px; margin-top:4px;}
.color-picker-btn{
  flex:1;background:rgba(255,255,255,0.05);border:1px solid transparent;color:var(--text);
  font-family:inherit;font-size:12px;font-weight:500;padding:8px;border-radius:8px;cursor:pointer;transition:all .2s;
}
.color-picker-btn:hover{background:rgba(255,255,255,0.1);}
#color-confirm-btn { background: var(--accent); color: white; font-weight: 600;}
#color-confirm-btn:hover { background: var(--accent2); }

/* Modals */
.modal-overlay {
  position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:2000;
  display:none;align-items:center;justify-content:center;backdrop-filter:blur(12px);
  animation: fadeIn 0.2s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.modal-overlay.visible{display:flex;}
.modal-box {
  background:var(--surface2);border:1px solid var(--border2);border-radius:24px;
  padding:32px;min-width:360px;display:flex;flex-direction:column;gap:20px;
  box-shadow:0 32px 80px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.05); max-width: 90vw;
  animation: popIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.modal-title{font-size:18px;font-weight:700;color:var(--text);letter-spacing:-0.01em;}
.settings-row{display:flex;flex-direction:column;gap:8px;}
.settings-label{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;}
.settings-select, .modal-input{
  background:rgba(0,0,0,0.3);border:1px solid var(--border);border-radius:10px;
  color:var(--text);font-family:inherit;font-size:13px;padding:12px;outline:none; width: 100%;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.settings-select:focus, .modal-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(139,92,246,0.15); }
.modal-close, .modal-btn{
  background:rgba(255,255,255,0.05);border:none;color:var(--text);
  font-family:inherit;font-size:calc(13px * var(--ui-scale)); font-weight: 600;
  padding:calc(10px * var(--ui-scale)) calc(16px * var(--ui-scale));
  border-radius:10px;cursor:pointer;
  transition:all .2s; align-self: flex-end;
}
.modal-close:hover{background:rgba(255,255,255,0.1);}
.modal-btn.primary { background: linear-gradient(135deg, var(--accent), #6d28d9); color: white; box-shadow: 0 4px 12px rgba(139,92,246,0.3); }
.modal-btn.primary:hover { box-shadow: 0 8px 20px rgba(139,92,246,0.4); transform: translateY(-1px); }

.share-input-wrap { display: flex; gap: 8px; align-items: center; }
.collab-list { max-height: 180px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.collab-item { font-size: 13px; padding: 10px 14px; background: rgba(0,0,0,0.2); border-radius: 10px; border: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; }
.remove-collab-btn { background: transparent; border: none; color: #f87171; font-size:14px; font-weight: bold; cursor: pointer; padding: 4px 8px; border-radius: 6px; transition: background 0.2s;}
.remove-collab-btn:hover { background: rgba(248,113,113,0.15); }

.dash-list { max-height: 300px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.dash-item { font-size: 13px; padding: 14px 16px; background: rgba(0,0,0,0.2); border-radius: 12px; border: 1px solid var(--border); transition: all 0.2s; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
.dash-item:hover { border-color: var(--accent); background: rgba(139,92,246,0.05); transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }

.dash-delete-btn { background: transparent; border: none; color: var(--red); cursor: pointer; font-size: 16px; margin-left: 12px; padding: 6px; border-radius: 8px; transition: background 0.2s;}
.dash-delete-btn:hover { background: rgba(239,68,68,0.15); }

/* Share & Presence */
#share-btn {
  background: linear-gradient(135deg, var(--accent), #6d28d9); color: white; font-weight: 600; border: none;
  box-shadow: 0 4px 12px rgba(139,92,246,0.3); margin-left: auto; border-radius: 10px; padding: 8px 16px;
}
#share-btn:hover { box-shadow: 0 8px 20px rgba(139,92,246,0.4); transform: translateY(-1px); }
#presence-bar { display: flex; gap: -10px; margin-left: 12px; align-items: center;}
.presence-avatar {
  width: 30px; height: 30px; border-radius: 50%; border: 2px solid var(--surface);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  font-size: 12px; color: white; font-weight: 600; text-transform: uppercase;
  transition: transform 0.2s, z-index 0.2s; position: relative; box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
.presence-avatar:hover { transform: translateY(-4px) scale(1.1); z-index: 100 !important; }
.remote-cursor {
  position: absolute; pointer-events: none; z-index: 9000;
  display: flex; flex-direction: column; align-items: flex-start;
  transition: transform 0.1s linear, left 0.1s linear, top 0.1s linear;
}
.remote-cursor svg { width: 16px; height: 16px; transform: translate(-3px, -3px); filter: drop-shadow(0 2px 4px rgba(0,0,0,0.4));}
.remote-cursor-label {
  background: currentColor; color: white; font-size: 10px; padding: 3px 8px;
  border-radius: 6px; margin-left: 10px; margin-top: -2px; font-weight: 600;
  box-shadow: 0 4px 8px rgba(0,0,0,0.4); white-space: nowrap;
}
</style>
</head>
<body>
<div id="app">
  <div id="top-bar">
    <div class="topbar-left">
      <button class="top-btn" id="dash-btn" title="Dashboard">🏠 Dashboard</button>
      <div style="width: 1px; height: 20px; background: var(--border2); margin: 0 8px;"></div>
      <input type="text" id="board-title-input" class="board-title-input" value="Untitled Canvas" />
    </div>
    <div class="topbar-center">
      <button class="top-btn" id="study-btn">Study</button>
      <button class="top-btn" id="auto-btn">Auto Layout</button>
      <button class="top-btn" id="group-btn">Group</button>
      <button class="top-btn" id="note-btn">Note</button>
      <button class="top-btn" id="brainstorm-btn">Brainstorm</button>
      <button class="top-btn" id="drag-btn" title="Add File Node">📎 Drag File</button>
    </div>
    <div class="topbar-right">
      <button class="top-btn" id="settings-btn" title="Settings">⚙ Settings</button>
      <div id="presence-bar"></div>
      <button id="share-btn">Share Canvas</button>
      <div class="user-badge" id="user-badge">…</div>
    </div>
  </div>
  <div id="canvas-wrapper">
    <div id="canvas">
      <div id="lasso-box"></div>
      <svg id="link-layer"></svg>
    </div>
  </div>
  <div id="suggestions-bar"></div>
  <div id="input-bar">
    <div id="slash-popup"></div>
    <textarea id="prompt" placeholder="Ask anything... Type / for commands" rows="1"></textarea>
    <button id="send-btn" title="Send (Enter)">↑</button>
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
    <button class="color-picker-btn" id="color-confirm-btn">Create Group</button>
    <button class="color-picker-btn" id="color-cancel-btn">Cancel</button>
  </div>
</div>

<div id="ctx-menu">
  <div class="ctx-item" id="ctx-rename">Rename group</div>
  <div class="ctx-item" id="ctx-recolor">Change color</div>
  <div class="ctx-item" id="ctx-collapse-toggle">Collapse group</div>
  <div class="ctx-item danger" id="ctx-delete-group">Delete group</div>
</div>

<div id="settings-modal" class="modal-overlay">
  <div class="modal-box">
    <div class="modal-title">Settings & Customization</div>
    
    <div class="settings-row">
      <div class="settings-label">Color Theme</div>
      <select class="settings-select" id="theme-select">
        <option value="midnight" selected>Glass Dark</option>
        <option value="nord">Nord Dark</option>
        <option value="light">Light Mode</option>
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
        <button class="modal-btn primary" id="share-copy-btn">Copy</button>
      </div>
    </div>
    <div style="height: 1px; background: var(--border2); margin: 8px 0;"></div>
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
  <div class="modal-box" style="width: 540px; max-width: 90vw;">
    <div class="modal-title">Canvas Dashboard</div>
    
    <div class="settings-row">
      <div style="display:flex; justify-content:space-between; align-items:center;">
         <div class="settings-label">My Canvases</div>
         <button class="modal-btn primary" id="new-canvas-btn" style="padding:8px 14px; font-size: 12px;">+ New Canvas</button>
      </div>
      <div class="dash-list" id="my-dash-list">Loading...</div>
    </div>
    
    <div style="height: 1px; background: var(--border2); margin: 12px 0;"></div>
    
    <div class="settings-row">
      <div class="settings-label">Shared with You</div>
      <div class="dash-list" id="shared-dash-list">Loading...</div>
    </div>
    <button class="modal-close" onclick="document.getElementById('dash-modal').classList.remove('visible')">Close</button>
  </div>
</div>

<script>
// Injection points from Flask
const IS_SHARED = true;
const SHARE_ID = '$$SHARE_ID$$';
const IS_OWNER = $$IS_OWNER$$;
const BOARD_TITLE = '$$BOARD_TITLE$$';

let socket = io();
const remoteCursors = {};
const userLastPositions = {};
let currentUserEmail = "";

// ── State ─────────────────────────────────────────────────────────────────────
let nodes=[], links=[], groups=[];
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
let pendingGroupColor="#8b5cf6";
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

const GROUP_COLORS=["#ef4444","#f97316","#f59e0b","#84cc16","#10b981","#06b6d4","#3b82f6","#8b5cf6","#ec4899","#cbd5e1"];
const SLASH_COMMANDS=[
  {cmd:"/find",   desc:"Scroll to the most relevant node",  argHint:"/find "},
  {cmd:"/delete", desc:"Delete: all | last | prompts",       argHint:"/delete "},
  {cmd:"/undo",   desc:"Undo last action",                   argHint:"/undo"},
  {cmd:"/redo",   desc:"Redo last undone action",            argHint:"/redo"},
];

const THEMES = {
  midnight: { bg:"#050508", canvas:"#050508", surface:"rgba(18, 18, 24, 0.65)", surface2:"rgba(28, 28, 36, 0.8)", border:"rgba(255,255,255,0.08)", border2:"rgba(255,255,255,0.12)", text:"#f8f8f8", muted:"#9ca3af", muted2:"#6b7280" },
  nord: { bg:"#2E3440", canvas:"#2E3440", surface:"rgba(59, 66, 82, 0.8)", surface2:"rgba(67, 76, 94, 0.9)", border:"rgba(255,255,255,0.1)", border2:"rgba(255,255,255,0.15)", text:"#ECEFF4", muted:"#D8DEE9", muted2:"#E5E9F0" },
  light: { bg:"#f3f4f6", canvas:"#e5e7eb", surface:"rgba(255, 255, 255, 0.8)", surface2:"rgba(255, 255, 255, 0.95)", border:"rgba(0,0,0,0.1)", border2:"rgba(0,0,0,0.15)", text:"#1f2937", muted:"#4b5563", muted2:"#6b7280" }
};

// ── DOM ───────────────────────────────────────────────────────────────────────
const canvasWrapper=document.getElementById("canvas-wrapper");
const canvas=document.getElementById("canvas");
const linkLayer=document.getElementById("link-layer");
const promptEl=document.getElementById("prompt");
const suggestionsBar=document.getElementById("suggestions-bar");
const slashPopup=document.getElementById("slash-popup");
const colorPickerPopup=document.getElementById("color-picker-popup");
const ctxMenu=document.getElementById("ctx-menu");
const lassoBox=document.getElementById("lasso-box");

promptEl.addEventListener("input", function() {
  this.style.height = "auto";
  this.style.height = (this.scrollHeight) + "px";
});

// ── Board Title & Owner Controls ──────────────────────────────────────────────
const titleInput = document.getElementById("board-title-input");
titleInput.value = BOARD_TITLE;
titleInput.addEventListener("change", async () => {
  if (!IS_OWNER) {
      alert("Only the owner can rename the canvas.");
      titleInput.value = BOARD_TITLE;
      return;
  }
  const newTitle = titleInput.value.trim() || "Untitled Canvas";
  try {
      await fetch("/api/board/title", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ share_id: SHARE_ID, title: newTitle })
      });
      socket.emit("title_update", { room: SHARE_ID, title: newTitle });
  } catch(e) {}
});

// ── User badge ────────────────────────────────────────────────────────────────
fetch("/auth/me").then(r=>r.json()).then(d=>{
  const badge=document.getElementById("user-badge");
  if(d.authenticated){
    currentUserEmail = d.email;
    badge.innerHTML=d.email+' <a href="/auth/logout">Sign out</a>';
  } else {
    badge.innerHTML='<a href="/login">Sign in</a>';
  }
});

// ── Settings, Customization & Themes ──────────────────────────────────────────
function applyTheme(name) {
  const t = THEMES[name] || THEMES.midnight;
  const r = document.documentElement;
  r.style.setProperty('--bg', t.bg);
  r.style.setProperty('--canvas-bg', t.canvas);
  r.style.setProperty('--surface', t.surface);
  r.style.setProperty('--surface2', t.surface2);
  r.style.setProperty('--border', t.border);
  r.style.setProperty('--border2', t.border2);
  r.style.setProperty('--text', t.text);
  r.style.setProperty('--muted', t.muted);
  r.style.setProperty('--muted2', t.muted2);
}

function applyUiScale(scale) {
  document.documentElement.style.setProperty('--ui-scale', scale);
}

document.getElementById("settings-btn").onclick=()=>document.getElementById("settings-modal").classList.add("visible");
document.getElementById("settings-close-btn").onclick=()=>{ document.getElementById("settings-modal").classList.remove("visible"); saveSettings(); };
document.querySelectorAll(".modal-overlay").forEach(m => m.addEventListener("click", e => {
  if(e.target===m) { m.classList.remove("visible"); if(m.id==="settings-modal") saveSettings(); }
}));

document.getElementById("theme-select").addEventListener("change", e => applyTheme(e.target.value));
document.getElementById("ui-scale-select").addEventListener("change", e => applyUiScale(e.target.value));

async function saveSettings() {
  const settings = { 
    zoomSpeed: document.getElementById("zoom-speed-select").value,
    theme: document.getElementById("theme-select").value,
    uiScale: document.getElementById("ui-scale-select").value
  };
  SCALE_STEP = parseFloat(settings.zoomSpeed);
  try { await fetch("/save_settings", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(settings) }); } catch(e){}
}

async function loadSettings() {
  try {
    const r = await fetch("/load_settings");
    if(r.ok) { 
        const data = await r.json(); 
        if(data.zoomSpeed) { document.getElementById("zoom-speed-select").value = data.zoomSpeed; SCALE_STEP = parseFloat(data.zoomSpeed); } 
        if(data.theme) { document.getElementById("theme-select").value = data.theme; applyTheme(data.theme); }
        if(data.uiScale) { document.getElementById("ui-scale-select").value = data.uiScale; applyUiScale(data.uiScale); }
    }
  } catch(e){}
}

// ── Canvas init & Zoom ────────────────────────────────────────────────────────
function initCanvas(){
  canvas.style.width=CANVAS_W+"px";
  canvas.style.height=CANVAS_H+"px";
  linkLayer.setAttribute("width",CANVAS_W);
  linkLayer.setAttribute("height",CANVAS_H);
  canvasWrapper.scrollLeft=(ORIGIN_X-canvasWrapper.clientWidth/2)*currentScale;
  canvasWrapper.scrollTop=(ORIGIN_Y-canvasWrapper.clientHeight/2)*currentScale;
}

function applyZoom(newScale,pivotClientX,pivotClientY){
  newScale=Math.max(MIN_SCALE,Math.min(MAX_SCALE,newScale));
  if(Math.abs(newScale-currentScale)<0.001)return;
  const px=pivotClientX!=null?pivotClientX:canvasWrapper.clientWidth/2;
  const py=pivotClientY!=null?pivotClientY:canvasWrapper.clientHeight/2;
  const worldX=(canvasWrapper.scrollLeft+px)/currentScale;
  const worldY=(canvasWrapper.scrollTop+py)/currentScale;
  currentScale=newScale;
  canvas.style.transform=`scale(${currentScale})`;
  canvas.style.transformOrigin="0 0";
  canvas.style.width=CANVAS_W+"px";
  canvas.style.height=CANVAS_H+"px";
  updateScrollSpacer();
  canvasWrapper.scrollLeft=worldX*currentScale-px;
  canvasWrapper.scrollTop=worldY*currentScale-py;
  redrawLinks();
  document.getElementById("zoom-label").textContent=Math.round(currentScale*100)+"%";
}

let spacerEl=null;
function updateScrollSpacer(){
  if(!spacerEl){spacerEl=document.createElement("div");spacerEl.style.cssText="position:absolute;top:0;left:0;pointer-events:none;";canvasWrapper.appendChild(spacerEl);}
  spacerEl.style.width=(CANVAS_W*currentScale)+"px";
  spacerEl.style.height=(CANVAS_H*currentScale)+"px";
}
function initZoom(){
  canvas.style.transform=`scale(${currentScale})`;
  canvas.style.transformOrigin="0 0";
  canvas.style.position="absolute";
  canvas.style.top="0";
  canvas.style.left="0";
  linkLayer.setAttribute("width",CANVAS_W);
  linkLayer.setAttribute("height",CANVAS_H);
  updateScrollSpacer();
}

document.getElementById("zoom-in-btn").onclick=()=>applyZoom(currentScale+SCALE_STEP);
document.getElementById("zoom-out-btn").onclick=()=>applyZoom(currentScale-SCALE_STEP);
canvasWrapper.addEventListener("wheel",e=>{
  if(e.ctrlKey||e.metaKey){
    e.preventDefault();
    const delta=e.deltaY>0?-SCALE_STEP:SCALE_STEP;
    const rect=canvasWrapper.getBoundingClientRect();
    applyZoom(currentScale+delta,e.clientX-rect.left,e.clientY-rect.top);
  }
},{passive:false});

function smartRecenter(animate=true, customX=null, customY=null){
  if(customX !== null && customY !== null) {
      canvasWrapper.scrollTo({ left: customX*currentScale - canvasWrapper.clientWidth/2, top: customY*currentScale - canvasWrapper.clientHeight/2, behavior: animate?"smooth":"instant" });
      return;
  }
  const pts=[];
  nodes.forEach(n=>{
    if(n.groupId!==undefined){ const g=groups.find(x=>x.id===n.groupId); if(g&&g.collapsed)return; }
    const el=getNodeEl(n.id); const w=el?el.offsetWidth:100,h=el?el.offsetHeight:40;
    pts.push({x:n.x,y:n.y,w,h});
  });
  groups.forEach(g=>{
    if(g.collapsed){
      const cx=g.collapsedX||ORIGIN_X,cy=g.collapsedY||ORIGIN_Y; const cw=g.collapsedW||160,ch=g.collapsedH||60;
      pts.push({x:cx,y:cy,w:cw,h:ch});
    }
  });

  if(!pts.length){
    canvasWrapper.scrollTo({ left:(ORIGIN_X-canvasWrapper.clientWidth/2)*currentScale, top:(ORIGIN_Y-canvasWrapper.clientHeight/2)*currentScale, behavior:animate?"smooth":"instant" });
    return;
  }

  const xs1=pts.map(p=>p.x), xs2=pts.map(p=>p.x+(p.w||100));
  const ys1=pts.map(p=>p.y), ys2=pts.map(p=>p.y+(p.h||40));
  const minX=Math.min(...xs1), maxX=Math.max(...xs2);
  const minY=Math.min(...ys1), maxY=Math.max(...ys2);
  const midX=(minX+maxX)/2, midY=(minY+maxY)/2;

  const PAD=120;
  const contentW=maxX-minX+PAD*2, contentH=maxY-minY+PAD*2;
  const scaleX=canvasWrapper.clientWidth/contentW, scaleY=canvasWrapper.clientHeight/contentH;
  const fitScale=Math.min(Math.max(Math.min(scaleX,scaleY)*0.88,MIN_SCALE),MAX_SCALE);

  applyZoom(fitScale,canvasWrapper.clientWidth/2,canvasWrapper.clientHeight/2);
  setTimeout(()=>{ canvasWrapper.scrollTo({ left:midX*fitScale-canvasWrapper.clientWidth/2, top:midY*fitScale-canvasWrapper.clientHeight/2, behavior:animate?"smooth":"instant" }); },30);
}
document.getElementById("recenter-btn").onclick=()=>smartRecenter();

function zoomToGroup(gid, animate=true) {
  const g = groups.find(x=>x.id===gid); if(!g) return;
  const bounds = getGroupBounds(g); if(!bounds) return;
  const padding = 100;
  const cx = (bounds.minX + bounds.maxX) / 2;
  const cy = (bounds.minY + bounds.maxY) / 2;
  const w = bounds.maxX - bounds.minX + padding*2;
  const h = bounds.maxY - bounds.minY + padding*2;

  const scaleX = canvasWrapper.clientWidth / w;
  const scaleY = canvasWrapper.clientHeight / h;
  const fitScale = Math.min(Math.max(Math.min(scaleX, scaleY)*0.9, MIN_SCALE), MAX_SCALE);

  applyZoom(fitScale, canvasWrapper.clientWidth/2, canvasWrapper.clientHeight/2);
  setTimeout(()=>{ canvasWrapper.scrollTo({ left: cx*fitScale - canvasWrapper.clientWidth/2, top: cy*fitScale - canvasWrapper.clientHeight/2, behavior: animate ? "smooth" : "instant" }); }, 30);
}

function clientToCanvas(cx,cy){ const rect=canvasWrapper.getBoundingClientRect(); return{x:(cx-rect.left+canvasWrapper.scrollLeft)/currentScale,y:(cy-rect.top+canvasWrapper.scrollTop)/currentScale}; }

// ── Helpers ───────────────────────────────────────────────────────────────────
const getNodeEl=id=>canvas.querySelector('.node[data-id="'+id+'"]');
const getGroupEl=id=>canvas.querySelector('.group-hull[data-gid="'+id+'"]');
const getSelectedNodes=()=>nodes.filter(n=>n.selected);
function applyDimClass(el,dim){for(let i=0;i<=4;i++)el.classList.remove("dim-"+i);el.classList.add("dim-"+dim);}

// ── Undo / Redo ───────────────────────────────────────────────────────────────
function captureSnapshot(){ return JSON.stringify({ nodes:nodes.map(n=>({...n,meta:{...n.meta}})), links:links.map(l=>({...l})), groups:groups.map(g=>({...g,nodeIds:[...g.nodeIds]})), nextNodeId,nextLinkId,nextGroupId }); }
function pushUndo(){undoStack.push(captureSnapshot());if(undoStack.length>MAX_HISTORY)undoStack.shift();redoStack=[];}
function restoreSnapshot(snap){
  const s=JSON.parse(snap);
  canvas.querySelectorAll(".node,.group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());
  nodes=s.nodes;links=s.links;groups=s.groups||[];
  nextNodeId=s.nextNodeId;nextLinkId=s.nextLinkId;nextGroupId=s.nextGroupId||1;
  nodes.forEach(n=>createNodeElement(n));
  redrawLinks();redrawGroups();saveGraph();
}
function undo(){if(!undoStack.length)return;redoStack.push(captureSnapshot());restoreSnapshot(undoStack.pop());}
function redo(){if(!redoStack.length)return;undoStack.push(captureSnapshot());restoreSnapshot(redoStack.pop());}

// ── Keyboard ──────────────────────────────────────────────────────────────────
const isMac=navigator.platform.toUpperCase().includes("MAC");
document.addEventListener("keydown",e=>{
  if(e.key==="Shift") isShiftHeld=true;
  const mod=isMac?e.metaKey:e.ctrlKey;
  if(e.ctrlKey||e.metaKey)isCtrlHeld=true;
  if(mod&&e.key==="z"&&!e.shiftKey){e.preventDefault();undo();return;}
  if(mod&&(e.key==="y"||(e.key==="z"&&e.shiftKey))){e.preventDefault();redo();return;}
  const active=document.activeElement;
  const inInput=active===promptEl||active.tagName==="INPUT"||active.tagName==="TEXTAREA";
  if(!inInput){
    if((e.key==="l"||e.key==="L")&&!mod){e.preventDefault();linkSelectedNodes();return;}
    if((e.key==="s"||e.key==="S")&&!mod){e.preventDefault();splitSelectedLinks();return;}
    if((e.key==="g"||e.key==="G")&&!mod){e.preventDefault(); triggerGroupUI(); return;}
  }
});
document.addEventListener("keyup",e=>{
  if(e.key==="Shift") isShiftHeld=false;
  if(!e.ctrlKey&&!e.metaKey){isCtrlHeld=false;clearCtrlHighlights();}
});

function getDirectNeighborAnswers(nodeId){
  const nids=new Set(); links.forEach(l=>{if(l.sourceId===nodeId)nids.add(l.targetId);if(l.targetId===nodeId)nids.add(l.sourceId);});
  return nodes.filter(n=>nids.has(n.id)&&n.type==="answer");
}
function getTreeNodes(nodeId) {
  let q = [nodeId]; let vis = new Set([nodeId]);
  while(q.length) {
    let curr = q.shift();
    links.forEach(l => {
      if (l.sourceId === curr && !vis.has(l.targetId)) { vis.add(l.targetId); q.push(l.targetId); }
      if (l.targetId === curr && !vis.has(l.sourceId)) { vis.add(l.sourceId); q.push(l.sourceId); }
    });
  }
  return nodes.filter(n => vis.has(n.id));
}
function clearCtrlHighlights(){canvas.querySelectorAll(".node.ctrl-highlight").forEach(el=>el.classList.remove("ctrl-highlight"));ctrlHighlightedNodes=[];}
function applyCtrlHighlight(node){clearCtrlHighlights();ctrlHighlightedNodes=getDirectNeighborAnswers(node.id);ctrlHighlightedNodes.forEach(n=>{const el=getNodeEl(n.id);if(el)el.classList.add("ctrl-highlight");});}
function applyTreeHighlight(node) { clearCtrlHighlights(); ctrlHighlightedNodes = getTreeNodes(node.id); ctrlHighlightedNodes.forEach(n => { const el = getNodeEl(n.id); if (el) el.classList.add("ctrl-highlight"); }); }

function linkSelectedNodes(){const sel=getSelectedNodes();if(sel.length<2)return;pushUndo();for(let i=0;i<sel.length-1;i++)addLink(sel[i].id,sel[i+1].id);redrawLinks();saveGraph();}
function splitSelectedLinks(){const sel=getSelectedNodes();if(sel.length<2)return;pushUndo();const selIds=new Set(sel.map(n=>n.id));links=links.filter(l=>!(selIds.has(l.sourceId)&&selIds.has(l.targetId)));redrawLinks();saveGraph();}

// ── Groups ────────────────────────────────────────────────────────────────────
function getGroupBounds(group){
  const memberNodes=nodes.filter(n=>group.nodeIds.includes(n.id)); if(!memberNodes.length)return null;
  let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
  memberNodes.forEach(n=>{
    const el=getNodeEl(n.id);const w=el?el.offsetWidth:80,h=el?el.offsetHeight:40;
    minX=Math.min(minX,n.x);minY=Math.min(minY,n.y); maxX=Math.max(maxX,n.x+w);maxY=Math.max(maxY,n.y+h);
  });
  return{minX,minY,maxX,maxY};
}

function createGroup(nodeIds,color,name){
  pushUndo(); const id=nextGroupId++;
  const group={id,name:name||("Group "+id),color,nodeIds:[...nodeIds],collapsed:false,collapsedW:160,collapsedH:60,collapsedX:null,collapsedY:null};
  groups.push(group);
  nodeIds.forEach(nid=>{
    const n=nodes.find(x=>x.id===nid);if(n)n.groupId=id;
    const el=getNodeEl(nid); if(el){let b=el.querySelector(".group-badge");if(!b){b=document.createElement("div");b.className="group-badge";el.appendChild(b);}b.style.background=color;}
  });
  redrawGroups();saveGraph();
}

function deleteGroup(gid){
  pushUndo(); const g=groups.find(x=>x.id===gid);if(!g)return;
  if(g.collapsed)expandGroup(gid,true);
  g.nodeIds.forEach(nid=>{
    const n=nodes.find(x=>x.id===nid);if(n)delete n.groupId;
    const el=getNodeEl(nid);if(el){const b=el.querySelector(".group-badge");if(b)b.remove();}
  });
  groups=groups.filter(x=>x.id!==gid); redrawGroups();saveGraph();
}

function addNodeToGroup(nodeId,gid){
  const g=groups.find(x=>x.id===gid);if(!g||g.nodeIds.includes(nodeId))return;
  pushUndo(); g.nodeIds.push(nodeId);
  const n=nodes.find(x=>x.id===nodeId);if(n)n.groupId=gid;
  const el=getNodeEl(nodeId); if(el){let b=el.querySelector(".group-badge");if(!b){b=document.createElement("div");b.className="group-badge";el.appendChild(b);}b.style.background=g.color;}
  redrawGroups();saveGraph();
}

// Emitting Live Collapse Events Makes Them Seamless
function collapseGroup(gid, emitEvent = true){
  const g=groups.find(x=>x.id===gid);if(!g||g.collapsed)return;
  if (emitEvent && socket) socket.emit("group_action", { room: SHARE_ID, action: "collapse", gid });
  
  pushUndo(); g.savedPositions={};
  g.nodeIds.forEach(nid=>{const n=nodes.find(x=>x.id===nid);if(n)g.savedPositions[nid]={x:n.x,y:n.y};});
  const bounds=getGroupBounds(g);
  if(bounds){
    const cx=(bounds.minX+bounds.maxX)/2-((g.collapsedW||160)/2);
    const cy=(bounds.minY+bounds.maxY)/2-((g.collapsedH||60)/2);
    g.collapsedX=cx;g.collapsedY=cy;
  }
  g.collapsed=true; g.nodeIds.forEach(nid=>{const el=getNodeEl(nid);if(el)el.style.display="none";});
  redrawLinks();redrawGroups();saveGraph(); 
  if (emitEvent) setTimeout(()=>smartRecenter(true),80);
}

function expandGroup(gid, skipSave, emitEvent = true){
  const g=groups.find(x=>x.id===gid);if(!g||!g.collapsed)return;
  if (emitEvent && socket) socket.emit("group_action", { room: SHARE_ID, action: "expand", gid });
  
  g.collapsed=false;
  if(g.savedPositions){
    g.nodeIds.forEach(nid=>{
      const n=nodes.find(x=>x.id===nid);
      if(n&&g.savedPositions[nid]){n.x=g.savedPositions[nid].x;n.y=g.savedPositions[nid].y;}
      const el=getNodeEl(nid);
      if(el){ el.style.display=""; if(n){el.style.left=n.x+"px";el.style.top=n.y+"px";}
        el.style.opacity="0"; el.style.transform="scale(0.85)"; el.style.transition="opacity .25s ease,transform .25s ease";
        requestAnimationFrame(()=>{el.style.opacity="";el.style.transform="";setTimeout(()=>el.style.transition="",300);});
      }
    }); delete g.savedPositions;
  } else { g.nodeIds.forEach(nid=>{const el=getNodeEl(nid);if(el)el.style.display="";}); }
  redrawLinks();redrawGroups(); if(!skipSave)saveGraph(); 
  if(emitEvent) zoomToGroup(gid, true);
}

function redrawGroups(){
  canvas.querySelectorAll(".group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());
  groups.forEach(group=>{
    if(group.collapsed){
      const hull=document.createElement("div"); hull.className="group-hull collapsed";hull.dataset.gid=group.id;
      const cx=group.collapsedX||ORIGIN_X,cy=group.collapsedY||ORIGIN_Y; const cw=group.collapsedW||160,ch=group.collapsedH||60;
      hull.style.cssText=`left:${cx}px;top:${cy}px;width:${cw}px;height:${ch}px;border-color:${group.color};background:${group.color};box-shadow:0 8px 32px ${group.color}33;`;
      hull.addEventListener("mousedown",e=>{
        if(e.button!==0)return; if(e.target.classList.contains("group-resize-handle"))return; e.stopPropagation(); draggingGroup=group;
        const cc=clientToCanvas(e.clientX,e.clientY); groupDragOffset={x:cc.x-cx,y:cc.y-cy}; groupDragNodeOffsets=[];
      });
      const rh=document.createElement("div");rh.className="group-resize-handle"; hull.appendChild(rh);
      rh.addEventListener("mousedown",e=>{e.stopPropagation();e.preventDefault();resizingGroup=group;resizeStartX=e.clientX;resizeStartY=e.clientY;resizeStartW=group.collapsedW;resizeStartH=group.collapsedH;});
      hull.addEventListener("dblclick",e=>{e.stopPropagation();expandGroup(group.id);});
      hull.addEventListener("contextmenu",e=>{e.preventDefault();e.stopPropagation();ctxTargetGroupId=group.id;ctxMenu.style.cssText=`left:${e.clientX}px;top:${e.clientY}px;`;ctxMenu.classList.add("visible");document.getElementById("ctx-collapse-toggle").textContent="Expand group";});
      canvas.appendChild(hull);
      const label=document.createElement("div"); label.className="group-label collapsed-label"; label.textContent=group.name+" ("+group.nodeIds.length+")";
      label.style.cssText=`left:${cx+12}px;top:${cy+ch/2-7}px;color:${group.color};font-size:12px;`;
      label.addEventListener("dblclick",()=>expandGroup(group.id));
      canvas.appendChild(label); return;
    }
    const memberNodes=nodes.filter(n=>group.nodeIds.includes(n.id)); if(!memberNodes.length)return;
    const bounds=getGroupBounds(group);if(!bounds)return;
    const{minX,minY,maxX,maxY}=bounds; const pad=32;
    const hull=document.createElement("div"); hull.className="group-hull";hull.dataset.gid=group.id;
    hull.style.cssText=`left:${minX-pad}px;top:${minY-pad}px;width:${maxX-minX+pad*2}px;height:${maxY-minY+pad*2}px;border-color:${group.color};background:${group.color};`;
    hull.addEventListener("mousedown",e=>{
      if(e.button!==0)return;e.stopPropagation(); draggingGroup=group; const cc=clientToCanvas(e.clientX,e.clientY); groupDragOffset={x:cc.x-(minX-pad),y:cc.y-(minY-pad)};
      groupDragNodeOffsets=group.nodeIds.map(nid=>{const n=nodes.find(x=>x.id===nid);return n?{id:nid,dx:n.x-(minX-pad),dy:n.y-(minY-pad)}:{id:nid,dx:0,dy:0};});
    });
    hull.addEventListener("click",e=>{if(e.shiftKey){e.stopPropagation();deleteGroup(group.id);}});
    hull.addEventListener("contextmenu",e=>{e.preventDefault();e.stopPropagation();ctxTargetGroupId=group.id;ctxMenu.style.cssText=`left:${e.clientX}px;top:${e.clientY}px;`;ctxMenu.classList.add("visible");document.getElementById("ctx-collapse-toggle").textContent="Collapse group";});
    canvas.insertBefore(hull,canvas.firstChild);
    const label=document.createElement("div"); label.className="group-label"; label.textContent=group.name;
    label.style.cssText=`left:${minX-pad+8}px;top:${minY-pad-22}px;color:${group.color}; font-size:12px;`;
    canvas.insertBefore(label,canvas.firstChild);
  });
}

// Context Menu / Colors
document.getElementById("ctx-rename").onclick=()=>{
  if(ctxTargetGroupId===null)return; const g=groups.find(x=>x.id===ctxTargetGroupId);if(!g)return; const n=prompt("Group name:",g.name);
  if(n!==null){g.name=n.trim()||g.name;redrawGroups();saveGraph();} ctxMenu.classList.remove("visible");
};
document.getElementById("ctx-recolor").onclick=()=>{
  if(ctxTargetGroupId===null)return; editingGroupId=ctxTargetGroupId; const g=groups.find(x=>x.id===ctxTargetGroupId);
  if(g){pendingGroupColor=g.color;document.getElementById("group-name-input").value=g.name;} buildColorSwatches();
  colorPickerPopup.style.cssText=`top:${parseInt(ctxMenu.style.top)+30}px;left:${ctxMenu.style.left};`; colorPickerPopup.classList.add("visible"); document.getElementById("color-confirm-btn").textContent="Update Group"; ctxMenu.classList.remove("visible");
};
document.getElementById("ctx-collapse-toggle").onclick=()=>{
  if(ctxTargetGroupId===null)return; const g=groups.find(x=>x.id===ctxTargetGroupId);if(!g)return;
  if(g.collapsed)expandGroup(g.id);else collapseGroup(g.id); ctxMenu.classList.remove("visible");
};
document.getElementById("ctx-delete-group").onclick=()=>{if(ctxTargetGroupId!==null)deleteGroup(ctxTargetGroupId);ctxMenu.classList.remove("visible");};
document.addEventListener("click",e=>{if(!ctxMenu.contains(e.target))ctxMenu.classList.remove("visible");});
document.addEventListener("contextmenu",e=>{if(!e.target.closest(".group-hull"))ctxMenu.classList.remove("visible");});

function buildColorSwatches(){
  const c=document.getElementById("color-swatches");c.innerHTML="";
  GROUP_COLORS.forEach(col=>{
    const sw=document.createElement("div");sw.className="color-swatch"+(col===pendingGroupColor?" active":"");sw.style.background=col;
    sw.onclick=()=>{pendingGroupColor=col;c.querySelectorAll(".color-swatch").forEach(el=>el.classList.remove("active"));sw.classList.add("active");};
    c.appendChild(sw);
  });
}
document.getElementById("color-confirm-btn").onclick=()=>{
  const name=document.getElementById("group-name-input").value.trim();
  if(editingGroupId!==null){
    const g=groups.find(x=>x.id===editingGroupId);
    if(g){g.color=pendingGroupColor;if(name)g.name=name;g.nodeIds.forEach(nid=>{const el=getNodeEl(nid);if(el){const b=el.querySelector(".group-badge");if(b)b.style.background=pendingGroupColor;}});redrawGroups();saveGraph();}
    editingGroupId=null;document.getElementById("color-confirm-btn").textContent="Create Group";
  } else {
    const sel=getSelectedNodes();if(!sel.length){colorPickerPopup.classList.remove("visible");return;}
    createGroup(sel.map(n=>n.id),pendingGroupColor,name||undefined);
  }
  colorPickerPopup.classList.remove("visible");document.getElementById("group-name-input").value="";
};
document.getElementById("color-cancel-btn").onclick=()=>{colorPickerPopup.classList.remove("visible");editingGroupId=null;document.getElementById("color-confirm-btn").textContent="Create Group";};

function triggerGroupUI() {
  const sel=getSelectedNodes();if(!sel.length){alert("Select at least one node to group.");return;}
  editingGroupId=null;document.getElementById("color-confirm-btn").textContent="Create Group"; buildColorSwatches();
  let minX=Infinity, minY=Infinity; sel.forEach(n=>{minX=Math.min(minX,n.x); minY=Math.min(minY,n.y);});
  const rect=canvasWrapper.getBoundingClientRect(); const screenX = (minX*currentScale) - canvasWrapper.scrollLeft + rect.left; const screenY = (minY*currentScale) - canvasWrapper.scrollTop + rect.top - 120;
  colorPickerPopup.style.cssText=`top:${Math.max(20, screenY)}px;left:${Math.max(20, screenX)}px;`; colorPickerPopup.classList.add("visible");
}
document.getElementById("group-btn").onclick=triggerGroupUI;

// File Upload Handling
function handleFileUpload(file, node, wrap) {
  if(!file) return;
  if(file.size > 5 * 1024 * 1024) { alert("File is too large. Keep it under 5MB to keep syncing fast."); return; }
  
  node.meta.fileName = file.name;
  node.meta.fileType = file.type;
  
  const processResult = (dataUrl, textContent) => {
      node.meta.fileData = dataUrl;
      if(textContent) node.text = textContent;
      else node.text = `[Attached File: ${file.name}]`;
      saveGraph();
      const el = getNodeEl(node.id);
      if(el) { el.remove(); createNodeElement(node); redrawLinks(); redrawGroups(); }
  };

  const reader = new FileReader();
  reader.onload = (e) => {
      const dataUrl = e.target.result;
      const textExts = ['.txt','.md','.json','.csv','.py','.java','.js','.ts','.html','.css','.cpp','.c','.h','.xml','.yml','.yaml','.sh','.sql','.rtf','.ini'];
      const isText = file.type.startsWith("text/") || textExts.some(ext => file.name.toLowerCase().endsWith(ext));
      
      if(isText) {
          const txtReader = new FileReader();
          txtReader.onload = (e2) => processResult(dataUrl, e2.target.result);
          txtReader.readAsText(file);
      } else {
          processResult(dataUrl, null);
      }
  };
  reader.readAsDataURL(file);
}

// Timer
function createTimerContent(node){
  const wrap=document.createElement("div");wrap.className="timer-ring"; const svgNS="http://www.w3.org/2000/svg";
  const svg=document.createElementNS(svgNS,"svg");svg.setAttribute("viewBox","0 0 40 40"); const defs=document.createElementNS(svgNS,"defs");
  const grad=document.createElementNS(svgNS,"linearGradient");grad.setAttribute("id","timerGradient");grad.setAttribute("x1","0%");grad.setAttribute("y1","0%");grad.setAttribute("x2","100%");grad.setAttribute("y2","0%");
  [[0,"#10b981"],[50,"#8b5cf6"],[100,"#a78bfa"]].forEach(([off,col])=>{const s=document.createElementNS(svgNS,"stop");s.setAttribute("offset",off+"%");s.setAttribute("stop-color",col);grad.appendChild(s);});
  defs.appendChild(grad);svg.appendChild(defs);
  const bg=document.createElementNS(svgNS,"circle");bg.setAttribute("class","ring-bg");bg.setAttribute("cx","20");bg.setAttribute("cy","20");bg.setAttribute("r","16");
  const prog=document.createElementNS(svgNS,"circle");prog.setAttribute("class","ring-progress");prog.setAttribute("cx","20");prog.setAttribute("cy","20");prog.setAttribute("r","16");
  const circ=2*Math.PI*16;prog.style.strokeDasharray=circ;prog.style.strokeDashoffset=circ; svg.appendChild(bg);svg.appendChild(prog);
  const txt=document.createElement("div");txt.className="timer-text";txt.textContent=formatTime(node.meta.seconds||0);
  wrap.appendChild(svg);wrap.appendChild(txt);
  node.meta._circumference=circ;node.meta._progressEl=prog;node.meta._textEl=txt; return wrap;
}
function formatTime(s){s=Math.max(0,Math.floor(s));const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;if(h>0)return String(h).padStart(2,"0")+":"+String(m).padStart(2,"0")+":"+String(sec).padStart(2,"0");return String(m).padStart(2,"0")+":"+String(sec).padStart(2,"0");}
function startTimer(node){
  const el=getNodeEl(node.id);if(!el)return;
  const total=node.meta.seconds;let remaining=total; const{_progressEl:prog,_textEl:txt,_circumference:circ}=node.meta;
  const tickMode=total<=300,start=performance.now();
  function upd(rem){const f=Math.max(0,Math.min(1,rem/total));if(prog){prog.style.strokeDashoffset=circ*(1-f);if(rem<=0)prog.style.stroke="#3b82f6";}if(txt)txt.textContent=formatTime(rem);}
  function step(){
    if(!tickMode){remaining=Math.max(0,total-(performance.now()-start)/1000);upd(remaining);if(remaining<=0){node.completed=true;el.classList.add("completed");saveGraph();return;}requestAnimationFrame(step);}
    else{upd(remaining);if(remaining<=0){node.completed=true;el.classList.add("completed");saveGraph();return;}remaining--;setTimeout(step,1000);}
  } step();
}

// Node element
function updateNodeTextDOM(node) {
  const el = getNodeEl(node.id); if(!el) return;
  if (node.type === "answer") {
    const b = el.querySelector(".bubble div:last-child");
    if(b && document.activeElement !== b) b.textContent = node.text;
  } else if (node.type === "note") {
    const b = el.querySelector(".note-body");
    if(b && document.activeElement !== b) b.value = node.text;
    const t = el.querySelector(".note-title");
    if(t && document.activeElement !== t && node.meta.title) t.value = node.meta.title;
  } else if (node.type === "brainstorm") {
    const i = el.querySelector(".brainstorm-input");
    if(i && document.activeElement !== i) i.value = node.meta.topic || "";
  } else if (node.type === "question" || node.type === "text") {
    const box = el.querySelector(".content-box");
    if (box) box.textContent = node.text;
    else {
        const t = el.querySelector(".node-text");
        if(t) t.textContent = node.text;
    }
  } else if (node.type !== "file") {
    const t = el.querySelector(".node-text");
    if(t) t.textContent = node.text;
  }
}

function handleNodeInputBroadcast(node) {
  if (socket) {
      socket.emit("node_text", { room: SHARE_ID, id: node.id, text: node.text, title: node.meta.title, topic: node.meta.topic });
  }
}

function createNodeElement(node){
  const el=document.createElement("div"); el.className="node node-"+node.type; if(node.completed)el.classList.add("completed"); el.dataset.id=node.id;
  el.style.left=node.x+"px";el.style.top=node.y+"px"; applyDimClass(el,node.dim||0);
  if(node.groupId!==undefined){ const g=groups.find(x=>x.id===node.groupId); if(g&&g.collapsed)el.style.display="none"; }
  const circle=document.createElement("div");circle.className="node-circle";
  const textWrap=document.createElement("div");textWrap.className="node-text";

  if(node.type==="answer"){
    const bubble=document.createElement("div");bubble.className="bubble"; const header=document.createElement("div");header.className="bubble-header";
    const copyBtn=document.createElement("button");copyBtn.className="copy-btn";copyBtn.textContent="Copy";
    copyBtn.onclick=e=>{e.stopPropagation();navigator.clipboard.writeText(node.text||"").catch(()=>{});}; header.appendChild(copyBtn);
    const body=document.createElement("div");body.textContent=node.text;
    bubble.appendChild(header);bubble.appendChild(body);
    bubble.addEventListener("scroll",()=>{if(bubble.scrollTop+bubble.clientHeight>=bubble.scrollHeight-2){node.completed=true;el.classList.add("completed");saveGraph();}}); textWrap.appendChild(bubble);
  } else if(node.type==="timer"){ textWrap.appendChild(createTimerContent(node));
  } else if(node.type==="note"){
    const wrap=document.createElement("div");wrap.className="note-wrap";
    const titleIn=document.createElement("input");titleIn.className="note-title";titleIn.placeholder="Title…";titleIn.value=node.meta.title||"";
    titleIn.addEventListener("input",e=>{e.stopPropagation();node.meta.title=titleIn.value; handleNodeInputBroadcast(node); });
    titleIn.addEventListener("change", ()=>saveGraph());
    titleIn.addEventListener("mousedown",e=>{if(document.activeElement===titleIn)e.stopPropagation();}); titleIn.addEventListener("click",e=>{e.stopPropagation();titleIn.focus();});
    const bodyIn=document.createElement("textarea");bodyIn.className="note-body";bodyIn.placeholder="Write anything…";bodyIn.value=node.text||"";
    bodyIn.addEventListener("input",e=>{e.stopPropagation();node.text=bodyIn.value; handleNodeInputBroadcast(node); });
    bodyIn.addEventListener("change", ()=>saveGraph());
    bodyIn.addEventListener("mousedown",e=>{if(document.activeElement===bodyIn)e.stopPropagation();}); bodyIn.addEventListener("click",e=>{e.stopPropagation();bodyIn.focus();}); bodyIn.addEventListener("keydown",e=>e.stopPropagation());
    wrap.appendChild(titleIn);wrap.appendChild(bodyIn); textWrap.appendChild(wrap);
  } else if(node.type==="brainstorm"){
    const wrap=document.createElement("div");wrap.className="brainstorm-wrap";
    const input=document.createElement("textarea");input.className="brainstorm-input"; input.placeholder="Topic...";input.value=node.meta.topic||"";
    input.addEventListener("input",e=>{e.stopPropagation();node.meta.topic=input.value; handleNodeInputBroadcast(node); });
    input.addEventListener("change", ()=>saveGraph());
    input.addEventListener("mousedown",e=>{if(document.activeElement===input)e.stopPropagation();}); input.addEventListener("click",e=>{e.stopPropagation();input.focus();}); input.addEventListener("keydown",e=>e.stopPropagation());
    const runBtn=document.createElement("button");runBtn.className="brainstorm-run";runBtn.textContent="Run";
    runBtn.onclick=async (e)=>{
      e.stopPropagation(); if(!input.value.trim())return; runBtn.textContent="Running...";
      try {
        const r=await fetch("/brainstorm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic:input.value})});
        const d=await r.json(); const ideas=d.nodes||[]; const total = ideas.length; const startY = node.y - ((total-1) * 70) / 2;
        ideas.forEach((idea, i)=>{ const n=addNode(idea,"answer",node.x+320, startY + i*100); addLink(node.id,n.id); }); saveGraph();
        setTimeout(() => smartRecenter(true, node.x + 160, node.y), 100);
      } catch(err){}
      runBtn.textContent="Run";
    };
    wrap.appendChild(input);wrap.appendChild(runBtn); textWrap.appendChild(wrap);
  } else if (node.type === "file") {
    const wrap = document.createElement("div"); wrap.className = "node-file-wrap";
    if (node.meta.fileName) {
        wrap.innerHTML = `<div style="font-size:12px;font-weight:600;word-break:break-all;margin-bottom:6px;color:var(--accent);">📄 ${node.meta.fileName}</div>`;
        if (node.meta.fileData && node.meta.fileType && node.meta.fileType.startsWith("image/")) {
            wrap.innerHTML += `<img src="${node.meta.fileData}" class="file-preview" draggable="false"/>`;
        }
    } else {
        wrap.innerHTML = `
            <div style="font-size:12px;font-weight:500;text-align:center;pointer-events:none;color:var(--muted);">Drag & Drop any file<br><span style="font-size:10px; opacity:0.7;">or click to browse</span></div>
            <input type="file" style="display:none;"/>
        `;
        const fileInput = wrap.querySelector("input");
        wrap.onclick = (e) => { e.stopPropagation(); fileInput.click(); };
        fileInput.onchange = (e) => { e.stopPropagation(); handleFileUpload(e.target.files[0], node, wrap); };
        wrap.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); wrap.classList.add("drag-over"); };
        wrap.ondragleave = (e) => { e.preventDefault(); e.stopPropagation(); wrap.classList.remove("drag-over"); };
        wrap.ondrop = (e) => { e.preventDefault(); e.stopPropagation(); wrap.classList.remove("drag-over"); if(e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files[0], node, wrap); };
    }
    textWrap.appendChild(wrap);
  } else { 
    // Expandable content box for standard questions/text
    const box = document.createElement("div"); box.className = "content-box"; box.textContent = node.text;
    const exp = document.createElement("button"); exp.className = "expand-btn"; exp.textContent = "Show more";
    textWrap.appendChild(box); textWrap.appendChild(exp);
    
    setTimeout(() => {
        if (box.scrollHeight > box.clientHeight) {
            exp.style.display = "block";
            exp.onclick = (e) => {
                e.stopPropagation();
                box.classList.toggle("expanded");
                exp.textContent = box.classList.contains("expanded") ? "Show less" : "Show more";
                redrawLinks(); redrawGroups();
            };
        }
    }, 10);
  }

  if(node.type==="answer" || node.type==="note") {
    const rh = document.createElement("div"); rh.className = "group-resize-handle"; rh.style.background = "rgba(255,255,255,0.7)"; rh.style.zIndex = 20;
    let targetEl = node.type==="answer" ? textWrap.querySelector('.bubble') : textWrap.querySelector('.note-body');
    if(node.meta.w) targetEl.style.width = node.meta.w + "px"; if(node.meta.h) targetEl.style.height = node.meta.h + "px";
    rh.addEventListener("mousedown", e => { e.stopPropagation(); e.preventDefault(); resizingNode = node; resizingTarget = targetEl; resizeStartX = e.clientX; resizeStartY = e.clientY; resizeStartW = targetEl.offsetWidth; resizeStartH = targetEl.offsetHeight; });
    textWrap.appendChild(rh);
  }

  el.appendChild(circle);el.appendChild(textWrap);
  if(node.groupId!==undefined){ const g=groups.find(x=>x.id===node.groupId); if(g){const badge=document.createElement("div");badge.className="group-badge";badge.style.background=g.color;el.appendChild(badge);} }

  el.addEventListener("mouseenter",()=>{ if(isCtrlHeld && isShiftHeld) applyTreeHighlight(node); else if(isCtrlHeld) applyCtrlHighlight(node); });
  el.addEventListener("mouseleave",()=>{if(isCtrlHeld)clearCtrlHighlights();});

  el.addEventListener("mousedown",e=>{
    if(e.shiftKey)return;
    if((node.type==="note"||node.type==="brainstorm")&&(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT")){if(document.activeElement===e.target)return;}
    if(e.target.classList.contains("group-resize-handle") || e.target.classList.contains("expand-btn")) return;
    e.stopPropagation(); draggingNode=node; const cc=clientToCanvas(e.clientX,e.clientY); dragOffset={x:cc.x-node.x,y:cc.y-node.y};
  });

  el.addEventListener("click",e=>{
    e.stopPropagation();
    if(e.shiftKey && (e.ctrlKey || e.metaKey)){ e.preventDefault(); getTreeNodes(node.id).forEach(n => { n.selected = true; let nel = getNodeEl(n.id); if(nel) nel.classList.add("selected"); }); hasActiveContext=true; updateSuggestionsDebounced(); return; }
    if(e.shiftKey){deleteNode(node.id);return;}
    if(e.ctrlKey||e.metaKey){ e.preventDefault(); [node,...ctrlHighlightedNodes].forEach(n=>{n.selected=true;const nel=getNodeEl(n.id);if(nel)nel.classList.add("selected");}); hasActiveContext=true; updateSuggestionsDebounced();return; }
    pushUndo(); node.selected=!node.selected; el.classList.toggle("selected",node.selected); hasActiveContext=nodes.some(n=>n.selected); updateSuggestionsDebounced();
  });
  canvas.appendChild(el); return el;
}

function addNode(text,type,x=ORIGIN_X,y=ORIGIN_Y,meta={}){
  pushUndo(); const node={id:nextNodeId++,x,y,type,text,selected:false,dim:0,meta,completed:false}; nodes.push(node);createNodeElement(node);
  const sel=getSelectedNodes();
  if(sel.length>0) { sel.forEach(s=>addLink(s.id,node.id)); } else if(!explicitlyDeselected && lastNodeId!==null && type !== "file") { addLink(lastNodeId,node.id); }
  explicitlyDeselected=false; lastNodeId=node.id; saveGraph(); return node;
}

function addLink(sourceId,targetId){
  if(sourceId===targetId)return; if(links.some(l=>(l.sourceId===sourceId&&l.targetId===targetId)||(l.sourceId===targetId&&l.targetId===sourceId)))return;
  links.push({id:nextLinkId++,sourceId,targetId});redrawLinks();saveGraph();
}
function deleteLink(id){links=links.filter(l=>l.id!==id);redrawLinks();saveGraph();}
function deleteNode(id){
  pushUndo(); nodes=nodes.filter(n=>n.id!==id); links=links.filter(l=>l.sourceId!==id&&l.targetId!==id);
  groups.forEach(g=>{g.nodeIds=g.nodeIds.filter(nid=>nid!==id);}); groups=groups.filter(g=>g.nodeIds.length>0);
  const el=getNodeEl(id);if(el)el.remove(); redrawLinks();redrawGroups();saveGraph();
}

function redrawLinks(){
  linkLayer.innerHTML=""; const strokeW=Math.max(1.5,1.2/currentScale);
  links.forEach(l=>{
    const a=nodes.find(n=>n.id===l.sourceId),b=nodes.find(n=>n.id===l.targetId);if(!a||!b)return;
    const aEl=getNodeEl(a.id),bEl=getNodeEl(b.id);
    if((aEl&&aEl.style.display==="none")||(bEl&&bEl.style.display==="none")||!aEl||!bEl)return;
    const ax=a.x+6,ay=a.y+6,bx=b.x+6,by=b.y+6;
    const line=document.createElementNS("http://www.w3.org/2000/svg","line"); line.classList.add("edge");line.dataset.id=l.id;
    line.setAttribute("x1",ax);line.setAttribute("y1",ay); line.setAttribute("x2",bx);line.setAttribute("y2",by);
    line.setAttribute("stroke","rgba(139,92,246,0.4)"); line.setAttribute("stroke-width",strokeW); linkLayer.appendChild(line);
  });
}
linkLayer.addEventListener("click",e=>{if(e.target.tagName==="line"&&e.target.classList.contains("edge")&&e.shiftKey)deleteLink(parseInt(e.target.dataset.id,10));});

// Hints
function ensureMergeHint(){if(!mergeHintEl){mergeHintEl=document.createElement("div");mergeHintEl.className="merge-hint";mergeHintEl.textContent="Merge?";canvas.appendChild(mergeHintEl);}}
function showMergeHint(x,y){ensureMergeHint();mergeHintEl.style.cssText=`left:${x}px;top:${y}px;display:block;`;}
function hideMergeHint(){if(mergeHintEl)mergeHintEl.style.display="none";mergeTargetNode=null;}
function ensureGroupHint(){if(!groupAddHintEl){groupAddHintEl=document.createElement("div");groupAddHintEl.className="group-add-hint";canvas.appendChild(groupAddHintEl);}}
function showGroupAddHint(x,y,name){ensureGroupHint();groupAddHintEl.textContent="Add to "+name+"?";groupAddHintEl.style.cssText=`left:${x}px;top:${y}px;display:block;`;}
function hideGroupAddHint(){if(groupAddHintEl)groupAddHintEl.style.display="none";groupAddTarget=null;}

// Drag & Resize
document.addEventListener("mousemove",e=>{
  if(resizingNode && resizingTarget){
    const dx = (e.clientX - resizeStartX) / currentScale, dy = (e.clientY - resizeStartY) / currentScale;
    const newW = Math.max(120, resizeStartW + dx), newH = Math.max(50, resizeStartH + dy);
    resizingTarget.style.width = newW + "px"; resizingTarget.style.height = newH + "px"; resizingNode.meta.w = newW; resizingNode.meta.h = newH; redrawLinks(); return;
  }
  if(resizingGroup){
    const dx=(e.clientX-resizeStartX)/currentScale,dy=(e.clientY-resizeStartY)/currentScale;
    resizingGroup.collapsedW=Math.max(80,resizeStartW+dx); resizingGroup.collapsedH=Math.max(30,resizeStartH+dy); redrawGroups(); return;
  }
  if(draggingGroup){
    const cc=clientToCanvas(e.clientX,e.clientY); const ox=cc.x-groupDragOffset.x,oy=cc.y-groupDragOffset.y;
    if(draggingGroup.collapsed){draggingGroup.collapsedX=ox;draggingGroup.collapsedY=oy;}
    else{groupDragNodeOffsets.forEach(({id,dx,dy})=>{const n=nodes.find(x=>x.id===id);if(!n)return;n.x=ox+dx;n.y=oy+dy;const nel=getNodeEl(id);if(nel){nel.style.left=n.x+"px";nel.style.top=n.y+"px";}});redrawLinks();}
    redrawGroups(); return;
  }
  if(!draggingNode)return;
  const cc=clientToCanvas(e.clientX,e.clientY);
  draggingNode.x=cc.x-dragOffset.x;draggingNode.y=cc.y-dragOffset.y;
  const el=getNodeEl(draggingNode.id);
  if(el){el.style.left=draggingNode.x+"px";el.style.top=draggingNode.y+"px";}
  
  if (socket) {
    socket.emit("node_move", {room: SHARE_ID, id: draggingNode.id, x: draggingNode.x, y: draggingNode.y});
  }
  
  redrawLinks(); if(draggingNode.groupId!==undefined)redrawGroups();

  let closest=null,closestDist=Infinity;
  nodes.forEach(other=>{if(other.id===draggingNode.id)return;const dx=other.x-draggingNode.x,dy=other.y-draggingNode.y,d=Math.sqrt(dx*dx+dy*dy);if(d<closestDist){closestDist=d;closest=other;}});
  if(closest&&closestDist<60){mergeTargetNode=closest;showMergeHint(closest.x+20,closest.y-10);}else hideMergeHint();
  hideGroupAddHint();
  for(const g of groups){
    if(g.nodeIds.includes(draggingNode.id))continue;
    if(g.collapsed){
      const cx=g.collapsedX||ORIGIN_X,cy=g.collapsedY||ORIGIN_Y,cw=g.collapsedW||160,ch=g.collapsedH||60;
      if(draggingNode.x>cx&&draggingNode.x<cx+cw&&draggingNode.y>cy&&draggingNode.y<cy+ch){groupAddTarget=g;showGroupAddHint(draggingNode.x+20,draggingNode.y-10,g.name);break;}
    } else {
      const bounds=getGroupBounds(g);if(!bounds)continue; const pad=24;
      if(draggingNode.x>bounds.minX-pad&&draggingNode.x<bounds.maxX+pad&&draggingNode.y>bounds.minY-pad&&draggingNode.y<bounds.maxY+pad){groupAddTarget=g;showGroupAddHint(draggingNode.x+20,draggingNode.y-10,g.name);break;}
    }
  }
});

document.addEventListener("mouseup",async e=>{
  if(resizingNode) { resizingNode=null; resizingTarget=null; saveGraph(); return; }
  if(resizingGroup){resizingGroup=null;saveGraph();return;}
  if(draggingGroup){draggingGroup=null;groupDragNodeOffsets=[];saveGraph();return;}
  if(!draggingNode)return;
  const source=draggingNode;draggingNode=null;saveGraph();
  if(groupAddTarget){const gt=groupAddTarget;hideGroupAddHint();hideMergeHint();if(confirm('Add node to group "'+gt.name+'"?'))addNodeToGroup(source.id,gt.id);return;}
  if(mergeTargetNode&&mergeTargetNode.id!==source.id){
    const mt=mergeTargetNode;hideMergeHint();
    if(confirm("Merge these nodes with AI?")){
      try{
        const res=await fetch("/merge",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({a:mt.text||"",b:source.text||""})});
        const data=await res.json(); mt.text=data.merged||((mt.text||"")+"\n"+(source.text||""));
        updateNodeTextDOM(mt); handleNodeInputBroadcast(mt); deleteNode(source.id);
      }catch(ex){mt.text=(mt.text||"")+"\n"+(source.text||"");updateNodeTextDOM(mt);handleNodeInputBroadcast(mt);deleteNode(source.id);}
      saveGraph();
    }
    return;
  }
  hideMergeHint();
});

// Canvas Pan & Lasso
canvasWrapper.addEventListener("mousedown",e=>{
  if(e.target.closest(".node")||e.target.classList.contains("edge")||e.target.closest("#input-bar")||e.target.closest(".suggestion-btn")||e.target.closest(".group-hull")||e.target.closest("#top-bar")||e.target.closest("#zoom-controls"))return;
  if(e.shiftKey) {
    isLassoing=true; const cc=clientToCanvas(e.clientX, e.clientY); lassoStartX=cc.x; lassoStartY=cc.y;
    lassoBox.style.left=(lassoStartX*currentScale)+"px"; lassoBox.style.top=(lassoStartY*currentScale)+"px"; lassoBox.style.width="0px"; lassoBox.style.height="0px"; lassoBox.style.display="block"; return;
  }
  isPanning=true;panMoved=false;panStartX=e.clientX;panStartY=e.clientY;panScrollX=canvasWrapper.scrollLeft;panScrollY=canvasWrapper.scrollTop;canvasWrapper.style.cursor="grabbing";
});
canvasWrapper.addEventListener("mousemove",e=>{
  if(isLassoing){
    const cc=clientToCanvas(e.clientX, e.clientY);
    const x=Math.min(cc.x, lassoStartX), y=Math.min(cc.y, lassoStartY), w=Math.abs(cc.x - lassoStartX), h=Math.abs(cc.y - lassoStartY);
    lassoBox.style.left=x+"px"; lassoBox.style.top=y+"px"; lassoBox.style.width=w+"px"; lassoBox.style.height=h+"px"; return;
  }
  if(!isPanning)return;
  const dx=e.clientX-panStartX,dy=e.clientY-panStartY;if(Math.abs(dx)>2||Math.abs(dy)>2)panMoved=true;canvasWrapper.scrollLeft=panScrollX-dx;canvasWrapper.scrollTop=panScrollY-dy;
});
canvasWrapper.addEventListener("mouseup",e=>{
  if(isLassoing) {
    isLassoing=false; lassoBox.style.display="none"; const cc=clientToCanvas(e.clientX, e.clientY);
    const minX=Math.min(cc.x, lassoStartX), minY=Math.min(cc.y, lassoStartY), maxX=Math.max(cc.x, lassoStartX), maxY=Math.max(cc.y, lassoStartY);
    nodes.forEach(n => { if (n.x >= minX && n.x <= maxX && n.y >= minY && n.y <= maxY) { n.selected=true; const el=getNodeEl(n.id); if(el) el.classList.add("selected"); } });
    hasActiveContext=nodes.some(n=>n.selected); updateSuggestionsDebounced(); return;
  }
  if(isPanning&&!panMoved){deselectAll();hasActiveContext=false;}
  isPanning=false;canvasWrapper.style.cursor="default";
});
canvasWrapper.addEventListener("mouseleave",()=>{ isPanning=false;isLassoing=false; lassoBox.style.display="none"; canvasWrapper.style.cursor="default"; });

function deselectAll(){ nodes.forEach(n=>n.selected=false); canvas.querySelectorAll(".node").forEach(el=>el.classList.remove("selected")); updateSuggestionsDebounced(); explicitlyDeselected=true; }
document.addEventListener("click",e=>{ if(!canvas.contains(e.target)&&!document.getElementById("top-bar").contains(e.target)&&!document.getElementById("input-bar").contains(e.target)&&!suggestionsBar.contains(e.target)&&!colorPickerPopup.contains(e.target)){deselectAll();hasActiveContext=false;} });

function getSmartSpawnPos(){
  const sel=getSelectedNodes(); if(sel.length>0){const maxX=Math.max(...sel.map(n=>n.x));const avgY=sel.reduce((s,n)=>s+n.y,0)/sel.length;return{x:maxX+380,y:avgY};}
  if(nodes.length>0){const maxX=Math.max(...nodes.map(n=>n.x))+380;const midY=nodes.reduce((s,n)=>s+n.y,0)/nodes.length;return{x:maxX,y:midY};}
  return{x:ORIGIN_X,y:ORIGIN_Y};
}

document.getElementById("auto-btn").onclick=()=>{
  pushUndo(); const visited=new Set();const components=[];
  function bfs(startId){const comp=[];const q=[startId];visited.add(startId);while(q.length){const id=q.shift();comp.push(id);links.forEach(l=>{const nb=l.sourceId===id?l.targetId:l.targetId===id?l.sourceId:null;if(nb&&!visited.has(nb)&&nodes.find(n=>n.id===nb)){visited.add(nb);q.push(nb);}});}return comp;}
  nodes.forEach(n=>{if(!visited.has(n.id))components.push(bfs(n.id));});
  const COL_GAP=380,ROW_GAP=140,COMP_GAP_Y=100; let gOffX=ORIGIN_X,gOffY=ORIGIN_Y;
  components.forEach(comp=>{
    if(!comp.length)return;
    if(comp.length===1){const n=nodes.find(x=>x.id===comp[0]);if(n){n.x=gOffX;n.y=gOffY;}gOffY+=ROW_GAP+COMP_GAP_Y;return;}
    const roots=comp.filter(id=>{const n=nodes.find(x=>x.id===id);return n&&n.type==="question";}); let root=roots.length?roots[0]:comp[0];
    const depth=new Map();const children=new Map();comp.forEach(id=>children.set(id,[]));
    const bfsQ=[root];const vis2=new Set([root]);depth.set(root,0);
    while(bfsQ.length){const cur=bfsQ.shift();links.forEach(l=>{let nb=null;if(l.sourceId===cur&&comp.includes(l.targetId))nb=l.targetId;else if(l.targetId===cur&&comp.includes(l.sourceId))nb=l.sourceId;if(nb&&!vis2.has(nb)){vis2.add(nb);depth.set(nb,(depth.get(cur)||0)+1);children.get(cur).push(nb);bfsQ.push(nb);}});}
    const sh=new Map();function calcSH(id){const kids=children.get(id)||[];if(!kids.length){sh.set(id,1);return 1;}const s=kids.reduce((a,k)=>a+calcSH(k),0);sh.set(id,s);return s;}calcSH(root);
    function assign(id,top){const kids=children.get(id)||[];const n=nodes.find(x=>x.id===id);const d=depth.get(id)||0;const totalH=(sh.get(id)-1)*ROW_GAP;const cy=top+totalH/2;if(n){n.x=gOffX+d*COL_GAP;n.y=cy;}let ct=top;kids.forEach(k=>{assign(k,ct);ct+=sh.get(k)*ROW_GAP;});}
    assign(root,gOffY); const maxY=Math.max(...comp.map(id=>{const n=nodes.find(x=>x.id===id);return n?n.y:gOffY;})); gOffY=maxY+ROW_GAP+COMP_GAP_Y;
  });
  nodes.forEach(n=>{const el=getNodeEl(n.id);if(el){el.style.left=n.x+"px";el.style.top=n.y+"px";}}); redrawLinks();redrawGroups();saveGraph(); setTimeout(()=>smartRecenter(true),60);
};

// Slash commands
function buildSlashPopup(filter){
  slashPopup.innerHTML=""; const filtered=SLASH_COMMANDS.filter(c=>c.cmd.startsWith(filter)||filter==="/");
  if(!filtered.length){hideSlashPopup();return;}
  filtered.forEach((c,i)=>{
    const item=document.createElement("div");item.className="slash-item"+(i===slashSelectedIndex?" active":"");
    const cs=document.createElement("span");cs.className="slash-item-cmd";cs.textContent=c.cmd; const ds=document.createElement("span");ds.className="slash-item-desc";ds.textContent=c.desc;
    item.appendChild(cs);item.appendChild(ds);
    item.onclick=()=>{promptEl.value=c.argHint;promptEl.setSelectionRange(c.argHint.length,c.argHint.length);hideSlashPopup();promptEl.focus();}; slashPopup.appendChild(item);
  });
  slashPopup.classList.add("visible");slashActive=true;
}
function hideSlashPopup(){slashPopup.classList.remove("visible");slashActive=false;slashSelectedIndex=0;}

promptEl.addEventListener("input",()=>{ const val=promptEl.value; if(val.startsWith("/")){const p=val.split(" ");if(p.length===1)buildSlashPopup(p[0]);else hideSlashPopup();} else hideSlashPopup(); updateSuggestionsDebounced();});
promptEl.addEventListener("keydown",e=>{
  if(slashActive){
    if(e.key==="ArrowDown"){e.preventDefault();slashSelectedIndex=Math.min(slashSelectedIndex+1,SLASH_COMMANDS.length-1);buildSlashPopup(promptEl.value);return;}
    if(e.key==="ArrowUp"){e.preventDefault();slashSelectedIndex=Math.max(slashSelectedIndex-1,0);buildSlashPopup(promptEl.value);return;}
    if(e.key==="Enter"){const item=slashPopup.querySelector(".slash-item.active");if(item){item.click();e.preventDefault();return;}}
    if(e.key==="Escape"){hideSlashPopup();return;}
  }
  if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();sendPrompt(); promptEl.style.height = "auto"; }
});

async function runFindCommand(query){
  if(!query.trim())return; 
  const descs=nodes.map(n=>({id:n.id,type:n.type,text:(n.text||"").slice(0,200),x:Math.round(n.x),y:Math.round(n.y)}));
  
  let foundId = null;
  try {
      const res=await fetch("/find",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({query,nodes:descs})}); 
      const data=await res.json();
      foundId = data.nodeId;
  } catch(e) {}
  
  // Fallback: fast fuzzy match if AI failed
  if (!foundId) {
      const qLower = query.toLowerCase();
      const match = nodes.find(n => (n.text||"").toLowerCase().includes(qLower) || (n.meta.title||"").toLowerCase().includes(qLower) || (n.meta.topic||"").toLowerCase().includes(qLower) || (n.meta.fileName||"").toLowerCase().includes(qLower));
      if(match) foundId = match.id;
  }

  if(foundId){ 
      const t=nodes.find(n=>n.id===foundId); 
      if(t){ 
          canvas.querySelectorAll(".node.find-focus").forEach(el=>el.classList.remove("find-focus")); 
          const el=getNodeEl(t.id);if(el)el.classList.add("find-focus"); 
          canvasWrapper.scrollTo({left:Math.max(0,t.x*currentScale-canvasWrapper.clientWidth/2+80),top:Math.max(0,t.y*currentScale-canvasWrapper.clientHeight/2+40),behavior:"smooth"}); 
          setTimeout(()=>{if(el)el.classList.remove("find-focus");},3000); 
      } 
  } else {
      alert("Could not find a matching node.");
  }
}
function runDeleteCommand(arg){
  const a=(arg||"").trim().toLowerCase(); pushUndo();
  if(a==="all"){nodes=[];links=[];groups=[];canvas.querySelectorAll(".node,.group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());redrawLinks();saveGraph();return;}
  if(a==="last"||a===""){if(!nodes.length)return;const last=nodes.reduce((a,b)=>a.id>b.id?a:b);deleteNode(last.id);return;}
  if(a==="prompts"||a==="questions"){nodes.filter(n=>n.type==="question").map(n=>n.id).forEach(id=>deleteNode(id));return;}
  const match=nodes.find(n=>(n.text||"").toLowerCase().includes(a));if(match)deleteNode(match.id);
}

document.getElementById("note-btn").onclick=()=>{ const spawn=getSmartSpawnPos(); addNode("","note",spawn.x,spawn.y,{title:"Untitled"}); };
document.getElementById("brainstorm-btn").onclick=()=>{ const spawn=getSmartSpawnPos(); addNode("","brainstorm",spawn.x,spawn.y,{topic:""}); };
document.getElementById("drag-btn").onclick=()=>{ const spawn=getSmartSpawnPos(); addNode("","file",spawn.x,spawn.y); };

function dimAllNodes(){nodes.forEach(n=>{n.dim=Math.min((n.dim||0)+1,4);const el=getNodeEl(n.id);if(el)applyDimClass(el,n.dim);});}
function buildContext(){const sel=getSelectedNodes();return sel.length===0?"":sel.map(n=>n.text).join("\n---\n");}
async function classifyInput(text){ const res=await fetch("/classify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({input:text})}); return await res.json(); }

function renderSuggestions(list){suggestionsBar.innerHTML="";if(!list||!list.length)return;list.slice(0,3).forEach(s=>{const btn=document.createElement("button");btn.className="suggestion-btn";btn.textContent=s;btn.onclick=()=>{promptEl.value=s;promptEl.focus();updateSuggestionsDebounced();};suggestionsBar.appendChild(btn);});}
let suggestTimeout=null; function updateSuggestionsDebounced(){if(suggestTimeout)clearTimeout(suggestTimeout);suggestTimeout=setTimeout(updateSuggestions,400);}
async function updateSuggestions(){
  const raw=promptEl.value.trim();if(raw.startsWith("/"))return; const ctx=buildContext();if(!raw&&!ctx){suggestionsBar.innerHTML="";return;}
  try{const r=await fetch("/suggest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:raw||"(thinking)",context:ctx})});const d=await r.json();renderSuggestions(d.suggestions||[]);}catch(e){}
}

// ── Save / Load ───────────────────────────────────────────────────────────────
function saveGraph(){
  const graphData = {
    nodes:nodes.map(n=>({id:n.id,x:n.x,y:n.y,type:n.type,text:n.text,dim:n.dim||0,meta:{topic:n.meta.topic||"",seconds:n.meta.seconds||0,label:n.meta.label||"",title:n.meta.title||"",w:n.meta.w||null,h:n.meta.h||null,fileName:n.meta.fileName||null,fileType:n.meta.fileType||null,fileData:n.meta.fileData||null},completed:!!n.completed,groupId:n.groupId})),
    links:links.map(l=>({id:l.id,sourceId:l.sourceId,targetId:l.targetId})),
    groups:groups.map(g=>({id:g.id,name:g.name,color:g.color,nodeIds:[...g.nodeIds],collapsed:!!g.collapsed,collapsedW:g.collapsedW||160,collapsedH:g.collapsedH||60,collapsedX:g.collapsedX,collapsedY:g.collapsedY,savedPositions:g.savedPositions})),
    nextNodeId,nextLinkId,nextGroupId
  };
  if(socket) {
    socket.emit("graph_update", { room: SHARE_ID, graph: graphData });
  }
}
async function loadGraph(){
  try{
    const res=await fetch(`/api/board/load/${SHARE_ID}`);
    if(!res.ok)return;
    const data=await res.json();if(!data||!data.nodes)return;
    nodes=data.nodes||[];links=data.links||[];groups=data.groups||[];
    groups.forEach(g=>{if(!g.collapsedW)g.collapsedW=160;if(!g.collapsedH)g.collapsedH=60;});
    nextNodeId=data.nextNodeId||(Math.max(0,...nodes.map(n=>n.id))+1);
    nextLinkId=data.nextLinkId||(Math.max(0,...links.map(l=>l.id))+1);
    nextGroupId=data.nextGroupId||(Math.max(0,...(groups.length?groups.map(g=>g.id):[0]))+1);
    canvas.querySelectorAll(".node,.group-hull,.group-label,.group-collapse-btn").forEach(el=>el.remove());
    nodes.forEach(n=>{if(!n.meta)n.meta={};createNodeElement(n);}); redrawLinks();redrawGroups();
  }catch(e){console.warn("load failed",e);}
}

async function sendPrompt(){
  const raw=promptEl.value.trim();if(!raw)return; hideSlashPopup();
  if(raw.startsWith("/find ")){await runFindCommand(raw.slice(6).trim());promptEl.value="";return;}
  if(raw==="/undo"){undo();promptEl.value="";return;} if(raw==="/redo"){redo();promptEl.value="";return;}
  if(raw.startsWith("/delete")){runDeleteCommand(raw.slice(7));promptEl.value="";return;}
  dimAllNodes(); const cls=await classifyInput(raw); const ctx=buildContext(); const spawn=getSmartSpawnPos();
  if(cls.type==="timer"&&cls.seconds){ const n=addNode("timer "+cls.seconds+"s","timer",spawn.x,spawn.y,{seconds:cls.seconds,label:"timer"}); startTimer(n);promptEl.value="";saveGraph();updateSuggestionsDebounced(); setTimeout(() => smartRecenter(true, n.x + 100, n.y), 100); return; }
  if(cls.type==="ai_command"){ const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:raw,context:ctx})}); const d=await r.json(); const n=addNode(d.reply||"","answer",spawn.x,spawn.y); promptEl.value="";saveGraph();updateSuggestionsDebounced(); setTimeout(() => smartRecenter(true, n.x + 100, n.y), 100); return; }
  const qn=addNode(raw,"question",spawn.x,spawn.y); lastQuestionNodeId=qn.id;
  const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:raw,context:ctx})}); const d=await r.json();
  const an=addNode(d.reply||"","answer",spawn.x+360,spawn.y); addLink(qn.id,an.id); an.selected=true; const anel=getNodeEl(an.id);if(anel)anel.classList.add("selected");
  hasActiveContext=true; redrawLinks();promptEl.value="";saveGraph();updateSuggestionsDebounced();
  setTimeout(() => smartRecenter(true, an.x + 100, an.y + 50), 100);
}
document.getElementById("send-btn").onclick=sendPrompt;
document.getElementById("study-btn").onclick=async()=>{
  const ctx=buildContext();if(!ctx){alert("Select some nodes first.");return;} const spawn=getSmartSpawnPos();
  const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:"Create a short focused study drill or quiz. Keep it concise.",context:ctx})});
  const d=await r.json(); const n=addNode(d.reply||"","answer",spawn.x,spawn.y);redrawLinks();saveGraph();
  setTimeout(() => smartRecenter(true, n.x + 100, n.y), 100);
};

initZoom(); initCanvas(); loadSettings();
loadGraph().then(()=>{ if(nodes.length>0||groups.some(g=>g.collapsed)){ setTimeout(()=>smartRecenter(false),100); } });

// ── Dashboard / Multiple Canvases ─────────────────────────────────────────────
document.getElementById("dash-btn").onclick = async () => {
  document.getElementById("dash-modal").classList.add("visible");
  const myList = document.getElementById("my-dash-list");
  const sharedList = document.getElementById("shared-dash-list");
  myList.innerHTML = "Loading..."; sharedList.innerHTML = "Loading...";
  try {
    const r = await fetch("/api/dashboard");
    const d = await r.json();
    myList.innerHTML = ""; sharedList.innerHTML = "";
    
    if (d.my_graphs && d.my_graphs.length > 0) {
      d.my_graphs.forEach(item => {
        const row = document.createElement("div"); row.className = "dash-item";
        const dateStr = item.updated_at ? item.updated_at.split('T')[0] : 'Recently';
        row.innerHTML = `
          <div style="flex:1;"><strong>${item.title}</strong><br><span style="color:var(--muted2);font-size:10px;">Updated: ${dateStr}</span></div>
          <div style="display:flex;align-items:center;">
              <button class="modal-btn" onclick="window.location.href='/b/${item.share_id}'" style="padding:6px 10px;font-size:11px;margin-right:8px;">Go →</button>
              <button class="dash-delete-btn" onclick="deleteCanvas('${item.share_id}', event)" title="Delete Canvas">🗑</button>
          </div>
        `;
        myList.appendChild(row);
      });
    } else { myList.innerHTML = "<div style='color:var(--muted2); font-size:11px; padding:12px;'>No canvases yet.</div>"; }
    
    if (d.shared_with_me && d.shared_with_me.length > 0) {
      d.shared_with_me.forEach(item => {
        const row = document.createElement("div"); row.className = "dash-item";
        const dateStr = item.added_at ? item.added_at.split('T')[0] : 'Recently';
        row.innerHTML = `<div><strong>${item.title}</strong> (${item.owner_email})<br><span style="color:var(--muted2);font-size:10px;">Added: ${dateStr}</span></div><span style="color:var(--accent); font-weight:600;">Go →</span>`;
        row.onclick = () => window.location.href = "/b/" + item.share_id;
        sharedList.appendChild(row);
      });
    } else { sharedList.innerHTML = "<div style='color:var(--muted2); font-size:11px; padding:12px;'>No graphs shared with you yet.</div>"; }
  } catch(e) { 
    myList.innerHTML = "Error loading."; sharedList.innerHTML = "Error loading."; 
  }
};

window.deleteCanvas = async function(share_id, event) {
  event.stopPropagation();
  if(!confirm("Are you sure you want to permanently delete this canvas?")) return;
  try {
      const res = await fetch(`/api/board/${share_id}`, { method: "DELETE" });
      const d = await res.json();
      if(d.ok) document.getElementById("dash-btn").click();
      else alert(d.error || "Failed to delete");
  } catch(e) { alert("Error deleting"); }
};

document.getElementById("new-canvas-btn").onclick = async () => {
    try {
        const res = await fetch("/api/board/new", { method:"POST" });
        const d = await res.json();
        if(d.share_id) window.location.href = "/b/" + d.share_id;
    } catch(e) {}
};

// ── Collaboration (WebSockets) ────────────────────────────────────────────────
socket.on("connect", () => {
  socket.emit("join", { room: SHARE_ID });
});

function jumpToUser(email) {
  if (userLastPositions[email]) {
    smartRecenter(true, userLastPositions[email].x, userLastPositions[email].y);
  }
}

socket.on("presence_update", (users) => {
  const pb = document.getElementById("presence-bar");
  pb.innerHTML = "";
  users.forEach((u, i) => {
    const init = (u.email || "A").substring(0, 1).toUpperCase();
    const av = document.createElement("div");
    av.className = "presence-avatar";
    av.style.background = u.color || "#8b5cf6";
    av.style.zIndex = users.length - i;
    av.title = u.email + (u.email === currentUserEmail ? " (You)" : " - Click to jump");
    av.textContent = init;
    if (u.email !== currentUserEmail) av.onclick = () => jumpToUser(u.email);
    pb.appendChild(av);
  });
});

socket.on("cursor_update", (c) => {
  let cursor = remoteCursors[c.id];
  userLastPositions[c.email] = {x: c.x, y: c.y};
  if (!cursor) {
    cursor = document.createElement("div");
    cursor.className = "remote-cursor";
    cursor.innerHTML = `
      <svg viewBox="0 0 16 16" fill="${c.color}">
        <path d="M1 1l6 14 2-5 5-2L1 1z" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>
      <div class="remote-cursor-label" style="background: ${c.color}">${c.email.split('@')[0]}</div>
    `;
    document.getElementById("link-layer").parentElement.appendChild(cursor);
    remoteCursors[c.id] = cursor;
  }
  cursor.style.left = c.x + "px";
  cursor.style.top = c.y + "px";
});

socket.on("cursor_remove", (data) => {
  if (remoteCursors[data.id]) {
    remoteCursors[data.id].remove();
    delete remoteCursors[data.id];
  }
});

socket.on("collab_removed", (data) => {
  if (data.email === currentUserEmail) {
      alert("Your access to this canvas has been revoked by the owner.");
      window.location.href = "/";
  } else {
      fetchCollaborators();
  }
});

socket.on("node_move", data => {
  const n = nodes.find(x => x.id === data.id);
  if (n && (!draggingNode || draggingNode.id !== n.id)) {
      n.x = data.x; n.y = data.y;
      const el = getNodeEl(n.id);
      if (el) { el.style.left = n.x + "px"; el.style.top = n.y + "px"; }
      redrawLinks();
  }
});

socket.on("node_text", data => {
  const n = nodes.find(x => x.id === data.id);
  if (n) {
      n.text = data.text;
      if(data.title !== undefined) n.meta.title = data.title;
      if(data.topic !== undefined) n.meta.topic = data.topic;
      updateNodeTextDOM(n);
  }
});

socket.on("group_action", data => {
    if(data.action === "collapse") collapseGroup(data.gid, false);
    if(data.action === "expand") expandGroup(data.gid, true, false);
});

socket.on("graph_sync", (graphData) => {
  const incomingNodes = new Map(graphData.nodes.map(n => [n.id, n]));
  
  nodes = nodes.filter(n => {
    if (!incomingNodes.has(n.id)) {
      const el = getNodeEl(n.id); if(el) el.remove(); return false;
    }
    return true;
  });

  graphData.nodes.forEach(inNode => {
    const exist = nodes.find(n => n.id === inNode.id);
    if (exist) {
       const isDragging = draggingNode && draggingNode.id === exist.id;
       const isEditing = document.activeElement && document.activeElement.closest(`.node[data-id="${exist.id}"]`);
       
       let structuralChange = false;
       if (exist.type !== inNode.type || exist.groupId !== inNode.groupId || exist.meta.fileData !== inNode.meta.fileData) {
           structuralChange = true;
       }

       if (!isDragging) { exist.x = inNode.x; exist.y = inNode.y; }
       if (!isEditing) { exist.text = inNode.text; exist.meta = inNode.meta; exist.completed = inNode.completed; }
       exist.type = inNode.type; exist.groupId = inNode.groupId;
       
       const el = getNodeEl(exist.id);
       if (el) {
         if(structuralChange) { 
             el.remove(); 
             createNodeElement(exist); 
         } else {
             if(!isDragging) { el.style.left = exist.x + "px"; el.style.top = exist.y + "px"; }
             if(!isEditing) { updateNodeTextDOM(exist); if(exist.completed) el.classList.add("completed"); else el.classList.remove("completed"); }
         }
       }
    } else {
       nodes.push(inNode); createNodeElement(inNode);
    }
  });
  
  links = graphData.links; redrawLinks();
  groups = graphData.groups; redrawGroups();

  nextNodeId = graphData.nextNodeId; nextLinkId = graphData.nextLinkId; nextGroupId = graphData.nextGroupId;
});

let lastCursorSync = 0;
canvasWrapper.addEventListener("mousemove", e => {
  const now = Date.now();
  if (now - lastCursorSync > 50) {
    const cc = clientToCanvas(e.clientX, e.clientY);
    socket.emit("cursor_move", { room: SHARE_ID, x: cc.x, y: cc.y });
    lastCursorSync = now;
  }
});

// ── Share & Collaboration Management ──────────────────────────────────────────
async function fetchCollaborators() {
  try {
    const res = await fetch(`/api/collaborators/${SHARE_ID}`);
    const data = await res.json();
    const list = document.getElementById("collab-list");
    list.innerHTML = "";
    if (data.collaborators && data.collaborators.length > 0) {
      data.collaborators.forEach(c => {
        const el = document.createElement("div"); el.className = "collab-item";
        el.innerHTML = `<span>${c.email}</span>
        <div style="display:flex;gap:6px;align-items:center;">
            <span style="color:var(--muted2)">Can Edit</span>
            ${IS_OWNER ? `<button class="remove-collab-btn" onclick="removeCollab('${c.email}')" title="Remove">✕</button>` : ''}
        </div>`;
        list.appendChild(el);
      });
      document.getElementById("collab-wrap").style.display = "flex";
    } else { document.getElementById("collab-wrap").style.display = "none"; }
  } catch(e) {}
}

window.removeCollab = async function(email) {
    if (!confirm(`Are you sure you want to remove ${email} from this canvas?`)) return;
    try {
        const res = await fetch("/share/remove", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ share_id: SHARE_ID, email: email })
        });
        const d = await res.json();
        if (d.ok) fetchCollaborators(); // Socket emit takes care of kicking them
        else alert(d.error || "Could not remove user.");
    } catch(e) {}
};

document.getElementById("share-btn").onclick = async () => {
  const input = document.getElementById("share-link-input");
  input.value = window.location.href;
  fetchCollaborators();
  document.getElementById("share-modal").classList.add("visible");
};

document.getElementById("share-copy-btn").onclick = () => {
  const input = document.getElementById("share-link-input");
  input.select(); document.execCommand("copy");
  const btn = document.getElementById("share-copy-btn");
  btn.textContent = "Copied!"; setTimeout(()=>btn.textContent="Copy", 2000);
};

document.getElementById("invite-btn").onclick = async () => {
  const emailInput = document.getElementById("invite-email");
  const email = emailInput.value.trim();
  if (!email) return;
  const btn = document.getElementById("invite-btn");
  btn.textContent = "Adding...";
  try {
    const res = await fetch("/share/invite", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share_id: SHARE_ID, email: email })
    });
    const data = await res.json();
    if (data.ok) {
      emailInput.value = "";
      fetchCollaborators();
    } else {
      alert(data.error || "Could not add user.");
    }
  } catch(e) {}
  btn.textContent = "Invite";
};
</script>
</body>
</html>"""

@app.route("/")
def index():
    if "user_id" not in session: 
        session["next_url"] = "/"
        return redirect("/login")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT share_id FROM graphs WHERE user_id=%s ORDER BY updated_at DESC LIMIT 1", (session["user_id"],))
    row = cursor.fetchone()
    if row:
        share_id = row["share_id"]
    else:
        share_id = secrets.token_urlsafe(12)
        cursor.execute("INSERT INTO graphs (user_id, data, share_id, title) VALUES (%s, %s, %s, %s)", (session["user_id"], "{}", share_id, "Personal Graph"))
        conn.commit()
    cursor.close()
    conn.close()
    
    return redirect(f"/b/{share_id}")

# ── Groq API Calls ────────────────────────────────────────────────────────────
def call_groq(messages):
    r=requests.post(GROQ_URL,headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},
                    json={"model":GROQ_MODEL,"messages":messages},timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def classify_with_groq(user_input):
    sys=(f"Classify a message. Time: {datetime.now().strftime('%A %B %d %Y %H:%M PDT')}.\n"
         "Return ONLY JSON. Types: timer(+seconds), ai_command(+command), question, text.\n"
         'Examples: {"type":"timer","seconds":180} | {"type":"question"} | {"type":"text"}')
    raw=call_groq([{"role":"system","content":sys},{"role":"user","content":user_input}])
    try:
        d=json.loads(raw)
        if isinstance(d,dict) and "type" in d:return d
    except:pass
    return {"type":"question"}

def chat_with_groq(prompt,context):
    sys=(f"Concise assistant. Time: {datetime.now().strftime('%A %B %d %Y %H:%M PDT')}.\n"
         "1-3 sentences unless asked for more. Don't mention nodes/graphs/context/internal structure.")
    msgs=[{"role":"system","content":sys}]
    if context:msgs.append({"role":"user","content":"Context:\n"+context})
    msgs.append({"role":"user","content":prompt})
    return call_groq(msgs)

def suggest_with_groq(prompt,context):
    sys=('Generate 3 follow-up suggestions. Return ONLY JSON: {"suggestions":["...","...","..."]}. No nodes/graphs.')
    msgs=[{"role":"system","content":sys}]
    if context:msgs.append({"role":"user","content":"Context:\n"+context})
    msgs.append({"role":"user","content":prompt})
    raw=call_groq(msgs)
    try:
        d=json.loads(raw)
        if isinstance(d,dict) and "suggestions" in d:return d["suggestions"]
    except:pass
    return []

def merge_with_groq(a,b):
    return call_groq([{"role":"system","content":"Merge two texts into one concise clean version. Don't mention merging."},
                      {"role":"user","content":"Text A:\n"+a},{"role":"user","content":"Text B:\n"+b}])

def find_with_groq(query,node_descs):
    sys='Graph search: find the single most relevant node. Return ONLY valid JSON: {"nodeId": <id>} or {"nodeId": null}. No markdown tags.'
    raw=call_groq([{"role":"system","content":sys},
                   {"role":"user","content":f"Query: {query}\n\nNodes:\n{json.dumps(node_descs)}"}])
    try:
        clean=raw.strip().strip("```json").strip("```").strip()
        d=json.loads(clean)
        if isinstance(d,dict) and "nodeId" in d:return d
    except:pass
    return {"nodeId": None}

# ── App Routes ────────────────────────────────────────────────────────────────
@app.route("/brainstorm", methods=["POST"])
def brainstorm():
    if "user_id" not in session: return jsonify({"error":"unauthorized"}),401
    d = request.get_json()
    topic = d.get("topic", "")
    sys = ('You are a brainstorm assistant. Analyze the topic and branch out conceptually. '
           'Generate necessary, highly related subtopics. The number of subtopics should fit the complexity '
           'of the topic (between 2 and 8). Return ONLY a valid JSON array of strings. Do not include markdown formatting. '
           'Example output: ["Subtopic A", "Subtopic B", "Subtopic C"]')
    try:
        raw = call_groq([{"role": "system", "content": sys}, {"role": "user", "content": topic}])
        clean = raw.strip().strip("```json").strip("```").strip()
        nodes = json.loads(clean)
        if isinstance(nodes, list):
            return jsonify({"nodes": nodes})
    except Exception as e:
        pass
    return jsonify({"nodes": [f"{topic} idea 1", f"{topic} idea 2", f"{topic} idea 3"]})

@app.route("/save_settings", methods=["POST"])
def save_settings():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        uid = session["user_id"]
        settings_data = json.dumps(request.get_json(), ensure_ascii=False)
        cursor.execute("SELECT user_id FROM user_settings WHERE user_id=%s", (uid,))
        if cursor.fetchone():
            cursor.execute("UPDATE user_settings SET settings=%s WHERE user_id=%s", (settings_data, uid))
        else:
            cursor.execute("INSERT INTO user_settings (user_id, settings) VALUES (%s, %s)", (uid, settings_data))
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
    return jsonify({"ok": True})

@app.route("/load_settings", methods=["GET"])
def load_settings():
    if "user_id" not in session: return jsonify({}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT settings FROM user_settings WHERE user_id=%s", (session["user_id"],))
        row = cursor.fetchone()
        if not row: return jsonify({}), 404
        return jsonify(row['settings'])
    finally:
        cursor.close()
        conn.close()

@app.route("/classify",methods=["POST"])
def classify():
    if "user_id" not in session:return jsonify({"error":"unauthorized"}),401
    return jsonify(classify_with_groq(request.get_json().get("input","")))

@app.route("/chat",methods=["POST"])
def chat():
    if "user_id" not in session:return jsonify({"error":"unauthorized"}),401
    d=request.get_json()
    return jsonify({"reply":chat_with_groq(d.get("prompt",""),d.get("context",""))})

@app.route("/suggest",methods=["POST"])
def suggest():
    if "user_id" not in session:return jsonify({"suggestions":[]}),200
    d=request.get_json()
    return jsonify({"suggestions":suggest_with_groq(d.get("prompt",""),d.get("context",""))})

@app.route("/merge",methods=["POST"])
def merge():
    if "user_id" not in session:return jsonify({"error":"unauthorized"}),401
    d=request.get_json()
    return jsonify({"merged":merge_with_groq(d.get("a",""),d.get("b",""))})

@app.route("/find",methods=["POST"])
def find():
    if "user_id" not in session:return jsonify({"nodeId":None}),200
    d=request.get_json()
    result = find_with_groq(d.get("query",""),d.get("nodes",[]))
    return jsonify({"nodeId": result.get("nodeId")})

# ── Collaboration & Universal Board Routes ──────────────────────────────────────────────
@app.route("/b/<share_id>")
def board(share_id):
    if "user_id" not in session: 
        session["next_url"] = f"/b/{share_id}"
        return redirect("/login")
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, title FROM graphs WHERE share_id=%s", (share_id,))
    graph = cursor.fetchone()
    
    if not graph:
        cursor.close()
        conn.close()
        return "Canvas not found.", 404
        
    is_owner = (graph["user_id"] == session["user_id"])
    if not is_owner:
        cursor.execute("SELECT 1 FROM graph_collaborators WHERE graph_id=(SELECT id FROM graphs WHERE share_id=%s) AND user_id=%s", (share_id, session["user_id"]))
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return "You do not have access to this canvas. Ask the owner to invite you.", 403
            
    cursor.close()
    conn.close()
    
    html = INDEX_HTML.replace("$$SHARE_ID$$", share_id).replace("$$IS_OWNER$$", "true" if is_owner else "false").replace("$$BOARD_TITLE$$", graph["title"].replace("'", "\\'"))
    return Response(html, mimetype="text/html")

@app.route("/api/board/new", methods=["POST"])
def create_board():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        share_id = secrets.token_urlsafe(12)
        cursor.execute("INSERT INTO graphs (user_id, data, share_id, title) VALUES (%s, %s, %s, %s)", (session["user_id"], "{}", share_id, "New Canvas"))
        conn.commit()
        return jsonify({"share_id": share_id})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route("/api/board/<share_id>", methods=["DELETE"])
def delete_board(share_id):
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, user_id FROM graphs WHERE share_id=%s", (share_id,))
        graph = cursor.fetchone()
        if not graph: return jsonify({"error": "Not found"}), 404
        if graph["user_id"] != session["user_id"]: return jsonify({"error": "Only the owner can delete this canvas."}), 403
        
        cursor.execute("DELETE FROM graphs WHERE id=%s", (graph["id"],))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route("/api/board/load/<share_id>", methods=["GET"])
def load_shared(share_id):
    if "user_id" not in session: return jsonify({}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT data FROM graphs WHERE share_id=%s", (share_id,))
        row = cursor.fetchone()
        if not row: return jsonify({}), 404
        return jsonify(json.loads(row["data"]))
    except:
        return jsonify({}), 500
    finally:
        cursor.close()
        conn.close()

@app.route("/api/board/title", methods=["POST"])
def update_title():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    share_id = d.get("share_id")
    title = d.get("title", "Untitled Canvas")
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE graphs SET title=%s WHERE share_id=%s AND user_id=%s", (title, share_id, session["user_id"]))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        cursor.close()
        conn.close()

@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    if "user_id" not in session: return jsonify({}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        uid = session["user_id"]
        
        # My Graphs
        cursor.execute("""
            SELECT share_id, title, updated_at 
            FROM graphs 
            WHERE user_id = %s 
            ORDER BY updated_at DESC
        """, (uid,))
        my_graphs = cursor.fetchall()
        for row in my_graphs:
            if row.get("updated_at"): row["updated_at"] = row["updated_at"].isoformat()

        # Shared with me
        cursor.execute("""
            SELECT g.share_id, g.title, gc.added_at, g.updated_at, u.email as owner_email
            FROM graph_collaborators gc
            JOIN graphs g ON gc.graph_id = g.id
            JOIN users u ON g.user_id = u.id
            WHERE gc.user_id = %s
            ORDER BY gc.added_at DESC
        """, (uid,))
        shared_with_me = cursor.fetchall()
        for row in shared_with_me:
            if row.get("added_at"): row["added_at"] = row["added_at"].isoformat()
            if row.get("updated_at"): row["updated_at"] = row["updated_at"].isoformat()
                
        return jsonify({"my_graphs": my_graphs, "shared_with_me": shared_with_me})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route("/api/collaborators/<share_id>", methods=["GET"])
def get_collaborators(share_id):
    if "user_id" not in session: return jsonify({}), 401
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT u.email 
            FROM graph_collaborators gc
            JOIN graphs g ON gc.graph_id = g.id
            JOIN users u ON gc.user_id = u.id
            WHERE g.share_id = %s
        """, (share_id,))
        collabs = cursor.fetchall()
        return jsonify({"collaborators": collabs})
    finally:
        cursor.close()
        conn.close()

@app.route("/share/invite", methods=["POST"])
def invite_collaborator():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    email = d.get("email", "").strip().lower()
    share_id = d.get("share_id")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Check permissions
        cursor.execute("SELECT id FROM graphs WHERE share_id=%s AND user_id=%s", (share_id, session["user_id"]))
        graph = cursor.fetchone()
        if not graph: return jsonify({"error": "Only the canvas owner can invite people."})

        # Find user
        cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        if not user: return jsonify({"error": "User not found. Ask them to sign up first!"})
        if user["id"] == session["user_id"]: return jsonify({"error": "You already own this canvas."})
            
        cursor.execute("INSERT INTO graph_collaborators (graph_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (graph["id"], user["id"]))
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "Database error."})
    finally:
        cursor.close()
        conn.close()

@app.route("/share/remove", methods=["POST"])
def remove_collaborator():
    if "user_id" not in session: return jsonify({"error": "unauthorized"}), 401
    d = request.get_json()
    email = d.get("email", "").strip().lower()
    share_id = d.get("share_id")
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM graphs WHERE share_id=%s AND user_id=%s", (share_id, session["user_id"]))
        graph = cursor.fetchone()
        if not graph: return jsonify({"error": "Only the canvas owner can remove collaborators."})
        
        cursor.execute("SELECT id FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()
        if user:
            cursor.execute("DELETE FROM graph_collaborators WHERE graph_id=%s AND user_id=%s", (graph["id"], user["id"]))
            conn.commit()
            
            # Emit socket event instantly kicking them out of the frontend board
            socketio.emit("collab_removed", {"email": email}, to=share_id)
            
        return jsonify({"ok": True})
    finally:
        cursor.close()
        conn.close()

# Socket.IO Handlers
connected_users = {} # room -> {sid: {email, color}}

@socketio.on("join")
def on_join(data):
    room = data.get("room")
    email = session.get("email", "Anonymous")
    join_room(room)
    if room not in connected_users:
        connected_users[room] = {}
    
    colors = ["#ef4444","#f97316","#f59e0b","#84cc16","#10b981","#06b6d4","#3b82f6","#8b5cf6","#ec4899"]
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
    if room:
        emit("cursor_update", {
            "id": request.sid,
            "x": data.get("x"),
            "y": data.get("y"),
            "email": session.get("email", "Anonymous"),
            "color": connected_users.get(room, {}).get(request.sid, {}).get("color", "#fff")
        }, to=room, include_self=False)

@socketio.on("node_move")
def on_node_move(data):
    room = data.get("room")
    if room:
        emit("node_move", {"id": data.get("id"), "x": data.get("x"), "y": data.get("y")}, to=room, include_self=False)

@socketio.on("node_text")
def on_node_text(data):
    room = data.get("room")
    if room:
        emit("node_text", data, to=room, include_self=False)

@socketio.on("group_action")
def on_group_action(data):
    room = data.get("room")
    if room:
        emit("group_action", data, to=room, include_self=False)

@socketio.on("title_update")
def on_title_update(data):
    room = data.get("room")
    if room:
        emit("title_update", data, to=room, include_self=False)

@socketio.on("graph_update")
def on_graph_update(data):
    room = data.get("room")
    if room:
        conn = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE graphs SET data=%s, updated_at=CURRENT_TIMESTAMP WHERE share_id=%s", (json.dumps(data.get("graph")), room))
            conn.commit()
        except:
            pass
        finally:
            cursor.close()
            conn.close()
        emit("graph_sync", data.get("graph"), to=room, include_self=False)

if __name__=="__main__":
    port=int(os.getenv("PORT","4000"))
    socketio.run(app, host="0.0.0.0", port=port, debug=True)

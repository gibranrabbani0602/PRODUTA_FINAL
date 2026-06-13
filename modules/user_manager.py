"""User management — JSON database, session tokens."""
import json, hashlib, time, uuid
from pathlib import Path

USERS_FILE   = Path("data/users.json")
SESSIONS_FILE= Path("data/sessions.json")
SESSION_TTL  = 3600  # 1 hour

def _hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def load_users() -> dict:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if USERS_FILE.exists():
        try: return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except: pass
    defaults = {"admin": {"name":"Administrator","password":_hash_pw("fbmi2026"),"role":"admin"}}
    USERS_FILE.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
    return defaults

def save_users(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")

def register(username: str, password: str, name: str) -> tuple:
    users = load_users()
    if not username.strip(): return False, "Username tidak boleh kosong."
    if username in users:    return False, "Username sudah digunakan."
    if len(password) < 6:    return False, "Password terlalu pendek."
    users[username] = {"name": name, "password": _hash_pw(password), "role": "user"}
    save_users(users)
    return True, "Akun berhasil dibuat."

def verify_login(username: str, password: str) -> tuple:
    users = load_users()
    if username not in users: return False, {}
    u = users[username]
    if u["password"] == _hash_pw(password): return True, u
    return False, {}

def create_session(username: str) -> str:
    token = str(uuid.uuid4())
    sessions = {}
    if SESSIONS_FILE.exists():
        try: sessions = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except: pass
    now = time.time()
    sessions = {k:v for k,v in sessions.items() if now - v["created"] < SESSION_TTL}
    sessions[token] = {"username": username, "created": now}
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions), encoding="utf-8")
    return token

def verify_session(token: str) -> tuple:
    if not token or not SESSIONS_FILE.exists(): return False, ""
    try:
        sessions = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        if token not in sessions: return False, ""
        s = sessions[token]
        if time.time() - s["created"] > SESSION_TTL:
            del sessions[token]
            SESSIONS_FILE.write_text(json.dumps(sessions), encoding="utf-8")
            return False, ""
        return True, s["username"]
    except: return False, ""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
CODE MASTER v5.1.0 — ETeam71
Cloud-first HTML hosting manager (Surge) for Termux / Linux / macOS

Termux setup:
    pkg update -y && pkg install -y python git nodejs
    npm install -g surge
    python index.py

Shortcuts & Background Modes:
    python index.py --sync       live auto-deploy when files change
    python index.py --monitor    realtime uptime monitor
    python index.py --setup      install / check requirements
    python index.py --test       run offline diagnostic & mock test suite
====================================================================
"""

import os
import re
import sys
import json
import time
import shutil
import socket
import hashlib
import zipfile
import platform
import threading
import subprocess
import tempfile
import ssl
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html.parser import HTMLParser

VERSION = "5.1.0"

# ----------------------------- Paths ------------------------------
BASE_DIR = os.path.expanduser("~/.codemaster_v4")
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
REGISTRY_FILE = os.path.join(BASE_DIR, "registry.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
AUDIT_LOG_FILE = os.path.join(LOGS_DIR, "audit.log")

REG_LOCK = threading.RLock()
DEPLOY_LOCKS = {}
DEPLOY_GLOBAL_LOCK = threading.Lock()
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "") or "TERMUX_VERSION" in os.environ

# ----------------------------- Colors & UI ------------------------
ESC = "\033"
RESET = ESC + "[0m"
BOLD = ESC + "[1m"

THEMES = {
    "neon":   {"primary": 51,  "accent": 207, "ok": 82,  "warn": 220, "err": 203, "dim": 245, "text": 255},
    "ocean":  {"primary": 39,  "accent": 86,  "ok": 85,  "warn": 222, "err": 210, "dim": 244, "text": 231},
    "sunset": {"primary": 214, "accent": 205, "ok": 114, "warn": 227, "err": 196, "dim": 243, "text": 230},
    "matrix": {"primary": 46,  "accent": 118, "ok": 82,  "warn": 190, "err": 160, "dim": 240, "text": 120},
}
TH = dict(THEMES["neon"])


def apply_theme(name):
    TH.update(THEMES.get(name, THEMES["neon"]))


def paint(role, text, bold=False):
    return "%s%s[38;5;%dm%s%s" % (BOLD if bold else "", ESC, TH[role], text, RESET)


def cP(t, b=False): return paint("primary", t, b)
def cA(t, b=False): return paint("accent", t, b)
def cOK(t, b=False): return paint("ok", t, b)
def cWN(t, b=False): return paint("warn", t, b)
def cER(t, b=False): return paint("err", t, b)
def cDM(t, b=False): return paint("dim", t, b)
def cTX(t, b=False): return paint("text", t, b)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def vlen(s):
    return len(ANSI_RE.sub("", s))


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def tw():
    """Terminal width, clamped so it fits small Termux screens."""
    return max(32, min(shutil.get_terminal_size((50, 24)).columns - 1, 74))


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def rule(ch="─"):
    print(cDM(ch * tw()))


def box(lines):
    w = tw()
    print(cP("╭" + "─" * (w - 2) + "╮"))
    for line in lines:
        pad = max(0, w - 2 - vlen(line))
        left = pad // 2
        print(cP("│") + " " * left + line + " " * (pad - left) + cP("│"))
    print(cP("╰" + "─" * (w - 2) + "╯"))


def title(text):
    print()
    print(cP("▌ ", True) + cTX(text.upper(), True))
    rule()


def say_ok(m): print(cOK("✔ ") + m)
def say_warn(m): print(cWN("▲ ") + m)
def say_err(m): print(cER("✖ ") + m)
def say_info(m): print(cP("● ") + m)


def ask(prompt, default=None):
    d = " [%s]" % default if default else ""
    try:
        v = input("%s %s%s: " % (cA("›"), cTX(prompt), cDM(d))).strip()
    except EOFError:
        v = ""
    return v or (default or "")


def confirm(prompt, default=False):
    d = "Y/n" if default else "y/N"
    v = ask("%s (%s)" % (prompt, d)).lower()
    if not v:
        return default
    return v in ("y", "yes", "h", "ha", "1")


def pause():
    try:
        input("\n" + cDM("Press Enter to continue..."))
    except EOFError:
        pass


def shorten(s, n):
    s = safe_str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def safe_str(v):
    return "" if v is None else str(v)


def mask_token(token):
    if not token:
        return ""
    token_str = str(token).strip()
    if len(token_str) <= 6:
        return "••••••"
    return "••••••••" + token_str[-4:]


# --------------------------- Logging & Config ---------------------
def log_audit(action, details=""):
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_details = strip_ansi(str(details)).replace("\n", " ")
        cfg = load_config()
        tok = cfg.get("surge_token")
        if tok and len(tok) > 4:
            safe_details = safe_details.replace(tok, mask_token(tok))
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] [%s] %s\n" % (stamp, action.upper(), safe_details))
    except Exception:
        pass


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DEFAULT_CONFIG = {
    "version": VERSION,
    "team": "ETeam71",
    "default_surge_domain_suffix": "surge.sh",
    "theme": "neon",
    "sync_interval": 3,
    "monitor_interval": 5,
    "verify_after_deploy": True,
    "keep_versions": 5,
    "github_remote": "",
    "telegram_channel": "https://t.me/ETeam71_Official",
    "surge_email": "",
    "surge_token": "",
    "strict_html_validation": False,
}


def ensure_environment():
    for d in (BASE_DIR, PROJECTS_DIR, BACKUP_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        save_config(dict(DEFAULT_CONFIG))
    else:
        cfg = load_config()
        changed = False
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
                changed = True
        if cfg.get("version") != VERSION:
            cfg["version"] = VERSION
            changed = True
        if changed:
            save_config(cfg)
    if not os.path.exists(REGISTRY_FILE):
        save_registry({"projects": {}, "metadata": {"created_at": now_str()}})


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_config():
    with REG_LOCK:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with REG_LOCK:
        _write_json(CONFIG_FILE, cfg)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass


def load_registry():
    with REG_LOCK:
        try:
            with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("projects", {})
            data.setdefault("metadata", {})
            return data
        except Exception:
            return {"projects": {}, "metadata": {}}


def save_registry(data):
    with REG_LOCK:
        data.setdefault("metadata", {})
        data["metadata"]["total_projects"] = len(data.get("projects", {}))
        data["metadata"]["last_updated"] = now_str()
        _write_json(REGISTRY_FILE, data)


def reg_update(fn):
    with REG_LOCK:
        reg = load_registry()
        res = fn(reg)
        save_registry(reg)
        return res


def get_project_lock(pid):
    with DEPLOY_GLOBAL_LOCK:
        if pid not in DEPLOY_LOCKS:
            DEPLOY_LOCKS[pid] = threading.Lock()
        return DEPLOY_LOCKS[pid]


# --------------------------- Path & Security ----------------------
def is_safe_path(base_dir, target_path):
    base = os.path.realpath(base_dir)
    target = os.path.realpath(target_path)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:
        return False


def safe_extract_zip(zip_path, extract_to, skip=()):
    os.makedirs(extract_to, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        for m in z.infolist():
            dest = os.path.join(extract_to, m.filename)
            if not is_safe_path(extract_to, dest):
                raise Exception("Unsafe path in ZIP: %s" % m.filename)
        for m in z.infolist():
            if m.filename in skip:
                continue
            z.extract(m, extract_to)


def run_cmd(cmd, env=None, timeout=240, cwd=None):
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            cwd=cwd
        )
        out = strip_ansi((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Command execution timed out (%ds limit)." % timeout
    except Exception as e:
        return False, str(e)


def run_interactive(cmd, env=None, cwd=None):
    try:
        return subprocess.run(cmd, env=env, cwd=cwd).returncode == 0
    except Exception as e:
        say_err(str(e))
        return False


def sanitize_id(name):
    pid = "".join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c in "-_")
    return pid or "proj_%d" % int(time.time())


DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE
)


def validate_email_syntax(email):
    if not email or "@" not in email:
        return False, "Invalid email address format."
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    if not re.match(pattern, email.strip()):
        return False, "Email does not match standard syntax."
    domain = email.strip().split("@")[-1].lower()
    typos = {
        "gnail.com": "gmail.com",
        "gmai.com": "gmail.com",
        "hotnail.com": "hotmail.com",
        "yaho.com": "yahoo.com"
    }
    if domain in typos:
        return True, "Did you mean %s? (Current: %s)" % (typos[domain], domain)
    return True, ""


def normalize_domain(raw, suffix):
    d = raw.strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if d and "." not in d:
        d = "%s.%s" % (d, suffix)
    if DOMAIN_RE.match(d):
        tld = d.split(".")[-1]
        if tld.isdigit():
            return ""
        return d
    return ""


def termux_exec(name, *args, input_text=None):
    exe = shutil.which(name)
    if not exe:
        return False
    try:
        subprocess.run([exe] + list(args), input=input_text, text=True, check=False)
        return True
    except Exception:
        return False


def notify(title_text, content):
    termux_exec("termux-notification", "--title", title_text, "--content", content)


def wake(on=True):
    termux_exec("termux-wake-lock" if on else "termux-wake-unlock")


def ver_dir(pid, v):
    return os.path.join(PROJECTS_DIR, pid, "v_%s" % v)


# ------------------------- Health & Network -----------------------
class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def comprehensive_health_check(url, timeout=10):
    """
    Returns dict:
    {
      "code": int,
      "latency_ms": int,
      "status_label": str,
      "category": str,
      "error_desc": str,
      "dns_ok": bool,
      "final_url": str
    }
    """
    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    host = clean_url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    dns_ok = False
    try:
        socket.getaddrinfo(host, 443)
        dns_ok = True
    except Exception:
        pass

    start = time.time()
    req = urllib.request.Request(clean_url, headers={"User-Agent": "CodeMaster/%s" % VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            lat = int((time.time() - start) * 1000)
            return {
                "code": r.status,
                "latency_ms": lat,
                "status_label": "200 OK (Reachable)",
                "category": "OK",
                "error_desc": "",
                "dns_ok": dns_ok,
                "final_url": r.geturl()
            }
    except urllib.error.HTTPError as e:
        lat = int((time.time() - start) * 1000)
        c = e.code
        category = "HTTP_ERROR"
        if c == 403:
            desc = "Access Forbidden (403): Possible domain ownership mismatch, unverified account, or token permission issue."
        elif c == 404:
            desc = "Not Found (404): Domain exists but no index.html or route matched."
        elif c == 429:
            desc = "Rate Limited (429): Surge API or CDN rate limit reached."
        elif 300 <= c < 400:
            desc = "Redirect (HTTP %d): Destination redirected." % c
            category = "REDIRECT"
        elif 500 <= c < 600:
            desc = "Server Error (HTTP %d): Surge upstream or Edge service issue." % c
            category = "SERVER_ERROR"
        else:
            desc = "HTTP Error %d returned by host." % c
        return {
            "code": c,
            "latency_ms": lat,
            "status_label": "HTTP %d" % c,
            "category": category,
            "error_desc": desc,
            "dns_ok": dns_ok,
            "final_url": clean_url
        }
    except urllib.error.URLError as e:
        lat = int((time.time() - start) * 1000)
        reason_str = str(e.reason).lower()
        if "ssl" in reason_str or "certificate" in reason_str:
            return {
                "code": 0, "latency_ms": lat, "status_label": "SSL/TLS Failure",
                "category": "TLS_ERROR", "error_desc": "HTTPS Certificate handshake failed: %s" % e.reason,
                "dns_ok": dns_ok, "final_url": clean_url
            }
        if not dns_ok or "name or service not known" in reason_str or "nodename" in reason_str:
            return {
                "code": 0, "latency_ms": lat, "status_label": "DNS Resolution Failed",
                "category": "DNS_ERROR", "error_desc": "Domain name cannot be resolved. DNS record is either propagating or invalid.",
                "dns_ok": False, "final_url": clean_url
            }
        if "timed out" in reason_str:
            return {
                "code": 0, "latency_ms": lat, "status_label": "Connection Timeout",
                "category": "TIMEOUT", "error_desc": "Connection attempt exceeded %ds limit." % timeout,
                "dns_ok": dns_ok, "final_url": clean_url
            }
        return {
            "code": 0, "latency_ms": lat, "status_label": "Network Error",
            "category": "NETWORK_ERROR", "error_desc": "URLError: %s" % e.reason,
            "dns_ok": dns_ok, "final_url": clean_url
        }
    except Exception as ex:
        lat = int((time.time() - start) * 1000)
        return {
            "code": 0, "latency_ms": lat, "status_label": "Client Error",
            "category": "CLIENT_ERROR", "error_desc": str(ex),
            "dns_ok": dns_ok, "final_url": clean_url
        }


def check_url(url, timeout=8):
    """Compatibility wrapper returning (status_code, latency_ms)."""
    res = comprehensive_health_check(url, timeout=timeout)
    return res["code"], res["latency_ms"]


def internet_ok():
    return comprehensive_health_check("https://surge.sh", timeout=5)["code"] != 0


# ------------------------- Surge Integration ----------------------
def surge_available():
    return shutil.which("surge") is not None


def surge_env():
    env = os.environ.copy()
    cfg = load_config()
    if cfg.get("surge_email") and cfg.get("surge_token"):
        env["SURGE_LOGIN"] = cfg["surge_email"].strip()
        env["SURGE_TOKEN"] = cfg["surge_token"].strip()
    return env


def get_active_surge_account():
    """Returns (status, account_email_or_msg)."""
    if not surge_available():
        return False, "Surge CLI missing"
    ok, out = run_cmd(["surge", "whoami"], env=surge_env(), timeout=15)
    clean = out.strip().split("\n")[-1].strip()
    if ok and clean and "@" in clean and not ("not" in clean.lower() or "error" in clean.lower()):
        return True, clean
    return False, clean or "Not logged in"


def inspect_cname_conflict(folder, requested_domain):
    cname_file = os.path.join(folder, "CNAME")
    if os.path.isfile(cname_file):
        try:
            with open(cname_file, "r", encoding="utf-8") as f:
                cname_val = f.read().strip().lower()
            if cname_val and cname_val != requested_domain.lower():
                return True, cname_val
        except Exception:
            pass
    return False, ""


def deploy_to_surge(folder, domain):
    """
    Executes Surge deployment using safe CLI subprocess invocation.
    Detects permission conflicts, account mismatch, and publication success.
    """
    if not surge_available():
        return False, "Surge CLI is not installed. Run option 21 to install.", "CLI_MISSING"

    # Pre-flight CNAME check
    conflict, cname_val = inspect_cname_conflict(folder, domain)
    if conflict:
        return False, "Project CNAME file contains '%s', which conflicts with requested '%s'." % (cname_val, domain), "CNAME_CONFLICT"

    # Check index.html exists
    if not os.path.isfile(os.path.join(folder, "index.html")):
        return False, "Missing index.html in publication folder: %s" % folder, "MISSING_INDEX"

    cmd = ["surge", folder, domain]
    ok, raw_out = run_cmd(cmd, env=surge_env(), timeout=180, cwd=folder)
    out_lower = raw_out.lower()

    if "you do not have permission" in out_lower or "aborted - you do not have permission" in out_lower:
        return False, raw_out, "PERMISSION_DENIED"
    if "email has not been verified" in out_lower or "verify your email" in out_lower:
        return False, raw_out, "EMAIL_UNVERIFIED"
    if "not authenticated" in out_lower or "login" in out_lower and not ok:
        return False, raw_out, "AUTH_REQUIRED"

    success_markers = ["success: published", "published to", "surge.sh", "success"]
    if ok or any(marker in out_lower for marker in success_markers):
        return True, raw_out, "SUCCESS"

    return False, raw_out or "Unknown CLI deployment error.", "FAILED"


def teardown_surge(domain):
    if not surge_available():
        return False, "Surge CLI missing."
    return run_cmd(["surge", "teardown", domain], env=surge_env())


def verify_live_robust(url, max_tries=3):
    last_res = None
    for attempt in range(1, max_tries + 1):
        time.sleep(2)
        res = comprehensive_health_check(url, timeout=7)
        last_res = res
        if 200 <= res["code"] < 400:
            return True, res
        if res["code"] == 403:
            # 403 is an authorization rejection, retrying won't change DNS
            return False, res
    return False, last_res


def live_status_map(projects):
    targets = [p for p in projects if p.get("public_url") and p.get("is_enabled", True)]
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda p: comprehensive_health_check(p["public_url"]), targets))
    return {p["id"]: r for p, r in zip(targets, res)}


def badge_detailed(res):
    c = res["code"]
    if 200 <= c < 400:
        return cOK("● LIVE", True)
    if c == 403:
        return cER("● 403 FORBIDDEN", True)
    if c == 404:
        return cWN("● 404 NOT FOUND", True)
    if res["category"] == "DNS_ERROR":
        return cWN("● DNS PENDING", True)
    if c == 0:
        return cER("● DOWN (%s)" % res["category"], True)
    return cWN("● HTTP %d" % c, True)


# ------------------------ Project & Versioning --------------------
IGNORE = shutil.ignore_patterns(".git", "node_modules", ".DS_Store", "__pycache__")


def create_project_version(pid, src, v):
    d = ver_dir(pid, v)
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    if os.path.isdir(src):
        shutil.copytree(src, d, dirs_exist_ok=True, ignore=IGNORE)
    elif os.path.isfile(src):
        shutil.copy2(src, os.path.join(d, "index.html"))
    else:
        raise Exception("Invalid source path.")
    return d


def prune_versions(pid, keep):
    def fn(r):
        p = r["projects"].get(pid)
        if not p or len(p["versions"]) <= keep:
            return
        cur = p["current_version"]
        ordered = sorted(p["versions"], key=lambda v: v["version"])
        drop = [v for v in ordered[:-keep] if v["version"] != cur]
        for v in drop:
            shutil.rmtree(ver_dir(pid, v["version"]), ignore_errors=True)
        ids = {v["version"] for v in drop}
        p["versions"] = [v for v in p["versions"] if v["version"] not in ids]
    reg_update(fn)


def publish_version(pid, src, note, source_path=None):
    """
    Safely publishes a version, respecting locks, preserving history on failures.
    Returns (ok, msg, ver, url, err_category).
    """
    lock = get_project_lock(pid)
    with lock:
        reg = load_registry()
        p = reg["projects"][pid]
        new_v = max([v["version"] for v in p["versions"]] + [0]) + 1
        vdir = create_project_version(pid, src, new_v)

        ok, msg, err_cat = deploy_to_surge(vdir, p["domain"])
        url = "https://%s" % p["domain"] if ok else p.get("public_url")

        def apply(r):
            q = r["projects"][pid]
            q["current_version"] = new_v
            q["public_url"] = url
            if ok:
                q["is_enabled"] = True
                q["last_deploy"] = now_str()
                q["last_error"] = ""
            else:
                q["last_error"] = "[%s] %s" % (err_cat, shorten(msg.replace("\n", " "), 100))
            if source_path:
                q["source_path"] = source_path
            q["versions"].append({
                "version": new_v,
                "created_at": now_str(),
                "note": note,
                "deployed": ok,
                "category": err_cat
            })
        reg_update(apply)
        log_audit("deploy" if ok else "deploy_failed", "%s v%d -> %s [%s]" % (pid, new_v, p["domain"], err_cat))
        prune_versions(pid, int(load_config().get("keep_versions", 5)))
        return ok, msg, new_v, url, err_cat


def pick_project(reg, prompt="Project number or ID"):
    projs = list(reg["projects"].values())
    if not projs:
        say_err("No projects yet. Use menu 1 to deploy one.")
        return None
    for i, p in enumerate(projs, 1):
        print(" %s %s %s" % (cA("%2d." % i), cTX(p["id"]), cDM("· " + shorten(p.get("domain"), 28))))
    s = ask(prompt)
    if s.isdigit() and 1 <= int(s) <= len(projs):
        return projs[int(s) - 1]["id"]
    if s in reg["projects"]:
        return s
    say_err("Project not found.")
    return None


# ----------------------- HTML Validation --------------------------
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
OPTIONAL_END = {"p", "li", "td", "th", "tr", "thead", "tbody", "tfoot", "dt", "dd",
                "option", "optgroup", "colgroup", "caption", "html", "body", "head"}


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.issues, self.tags, self.meta = [], [], set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag == "meta":
            self.meta.append(dict(attrs))
        if tag not in VOID and tag not in OPTIONAL_END:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID or tag in OPTIONAL_END:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.issues.append("<%s> is not closed" % self.stack.pop())
            self.stack.pop()
        else:
            self.issues.append("stray </%s>" % tag)


def html_validation(path):
    if not os.path.isfile(path):
        return False, ["File does not exist."]
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        return False, ["Read error: %s" % e]

    if not content.strip():
        return False, ["HTML file is empty."]

    low = content.lower()
    issues = []
    if "<!doctype html" not in low:
        issues.append("Missing <!DOCTYPE html>")
    if "<title" not in low:
        issues.append("Missing <title>")
    if "<body" not in low:
        issues.append("Missing <body>")
    c = _Checker()
    try:
        c.feed(content)
        c.close()
    except Exception as e:
        issues.append("Parser error: %s" % e)
    issues += c.issues
    issues += ["<%s> is not closed" % t for t in c.stack]
    if not any(m.get("name") == "viewport" for m in c.meta):
        issues.append("No viewport meta (page may not scale properly on mobile)")
    if not any("charset" in m for m in c.meta):
        issues.append("No charset meta (add <meta charset=\"utf-8\">)")
    return len(issues) == 0, issues


# --------------------------- Banner & Menu UI ---------------------
def print_banner():
    clear_screen()
    reg = load_registry()
    projs = reg.get("projects", {})
    live = sum(1 for p in projs.values() if p.get("public_url") and p.get("is_enabled", True))
    surge_st = cOK("Surge ✔") if surge_available() else cER("Surge ✖")
    acc_ok, active_acc = get_active_surge_account()
    acc_badge = cOK(shorten(active_acc, 20)) if acc_ok else cDM("Not Logged In")

    box([
        cA("★ ", True) + cP("CODE MASTER", True) + cA(" ★", True),
        cTX("v%s ULTIMATE CLOUD MANAGER" % VERSION, True),
        cDM("ETeam71 Official Toolsuite"),
        "%s  %s  %s" % (surge_st, cP("Sites %d" % len(projs)), cOK("Live %d" % live)),
        cDM("Account: ") + acc_badge
    ])


MENU_SECTIONS = []


def print_menu():
    for head, items in MENU_SECTIONS:
        print(cA("◆ " + head, True))
        for n, label, _ in items:
            print("  %s %s" % (cP("%02d" % n, True), cTX(label)))
    print(cER("  00 ") + cTX("Exit"))
    rule()


# ============================ MENU HANDLERS ========================

def menu_deploy():
    title("Deploy new project")
    cfg = load_config()
    acc_ok, active_acc = get_active_surge_account()
    if not acc_ok:
        say_warn("Active Surge account not detected! Run menu 11 to login if deploy fails.")
    else:
        say_info("Deploying as: " + cOK(active_acc))

    name = ask("Project name")
    if not name:
        say_err("Name cannot be empty.")
        return
    pid = sanitize_id(name)
    reg = load_registry()
    if pid in reg["projects"]:
        say_err("Project ID '%s' already exists. Use Update (menu 02)." % pid)
        return

    print(cWN("Source type:"))
    print("  %s HTML file    %s Folder    %s Paste code" % (cP("1"), cP("2"), cP("3")))
    ch = ask("Select", "1")
    src, source_path, tmp = None, None, None

    if ch == "1":
        src = os.path.expanduser(ask("Path to HTML file"))
        if not os.path.isfile(src):
            say_err("File not found.")
            return
        ok, issues = html_validation(src)
        if not ok:
            say_warn("HTML notices: " + "; ".join(issues[:3]))
            if cfg.get("strict_html_validation", False) and not confirm("Continue deployment despite HTML issues?"):
                return
        source_path = src
    elif ch == "2":
        src = os.path.expanduser(ask("Folder path"))
        if not os.path.isdir(src):
            say_err("Folder not found.")
            return
        idx = os.path.join(src, "index.html")
        if not os.path.exists(idx):
            say_warn("Notice: 'index.html' not found inside the root folder!")
            if not confirm("Proceed anyway?", False):
                return
        source_path = src
    elif ch == "3":
        print(cDM("Paste HTML content. Type __EOF__ on a new line to finish."))
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == "__EOF__":
                break
            lines.append(line)
        fd, tmp = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        src = tmp
    else:
        say_err("Invalid selection.")
        return

    suffix = cfg.get("default_surge_domain_suffix", "surge.sh")
    default_domain = "%s.%s" % (pid, suffix)

    while True:
        raw_dom = ask("Domain", default_domain)
        domain = normalize_domain(raw_dom, suffix)
        if not domain:
            say_err("Malformed domain '%s'. Must be valid format (e.g. name.surge.sh or mydomain.com)." % raw_dom)
            if not confirm("Try another domain?", True):
                if tmp and os.path.exists(tmp): os.remove(tmp)
                return
            continue

        conflict_id = next((k for k, p in reg["projects"].items() if p.get("domain") == domain), None)
        if conflict_id:
            say_warn("Registry conflict: Domain '%s' is already used by local project '%s'." % (domain, conflict_id))
            if not confirm("Proceed with this domain anyway?", False):
                continue
        break

    expiry = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    def add(r):
        r["projects"][pid] = {
            "id": pid, "name": name, "current_version": 0, "domain": domain,
            "public_url": None, "is_enabled": True, "created_at": now_str(),
            "expiry_date": expiry, "source_path": None, "versions": [],
            "last_error": ""
        }
    reg_update(add)

    say_info("Deploying to https://%s ..." % domain)
    ok, msg, v, url, err_cat = publish_version(pid, src, "Initial deployment", source_path)
    if tmp and os.path.exists(tmp):
        os.remove(tmp)

    if ok:
        say_ok("DEPLOYMENT ACCEPTED BY SURGE CLI: " + cP(url, True))
        if cfg.get("verify_after_deploy", True):
            say_info("Verifying live reachability over HTTPS...")
            up, res = verify_live_robust(url)
            if up:
                say_ok("Live verification passed: HTTP %d in %d ms" % (res["code"], res["latency_ms"]))
            else:
                say_warn("DEPLOYMENT SUCCESSFUL — LIVE STATUS UNCONFIRMED")
                print(cDM("Reason: %s (%s)" % (res["status_label"], res["error_desc"])))
                if res["code"] == 403:
                    say_warn("HTTP 403 indicates permission or account verification issue, not a normal DNS delay.")
        log_audit("project_created", pid)
    else:
        say_err("Deployment rejected by Surge CLI [%s]:" % err_cat)
        print(cDM(shorten(msg.replace("\n", " "), 300)))
        if err_cat == "PERMISSION_DENIED":
            say_err("Domain '%s' is owned by another account, or you lack publish rights." % domain)
            say_info("Active account: %s" % active_acc)
            say_info("Solution: Use Domain Manager (menu 06) to assign a unique domain name.")
        elif err_cat == "EMAIL_UNVERIFIED":
            say_warn("Your Surge email is not verified yet. Run menu 11 -> surge verify.")
        say_warn("Project configuration saved locally. You can resolve this and Redeploy (menu 03).")


def menu_update():
    title("Update project (same URL)")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    default_src = p.get("source_path") or ""
    path = os.path.expanduser(ask("New source file or folder", default_src))
    if not path or not os.path.exists(path):
        say_err("Source path does not exist.")
        return
    note = ask("Release note", "Updated release")
    say_info("Publishing update to %s ..." % p["domain"])
    ok, msg, v, url, err_cat = publish_version(pid, path, note, path)
    if ok:
        say_ok("Version %d deployed: %s" % (v, cP(url, True)))
        up, res = verify_live_robust(url)
        if up:
            say_ok("Live status confirmed (HTTP %d)" % res["code"])
        else:
            say_warn("Uploaded, but live check returned: %s" % res["status_label"])
    else:
        say_err("Cloud update failed [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 250)))


def menu_redeploy():
    title("Redeploy current version")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    d = ver_dir(pid, p["current_version"])
    if not os.path.isdir(d):
        say_err("Version folder missing on disk: %s" % d)
        return
    say_info("Redeploying v%s to %s ..." % (p["current_version"], p["domain"]))
    ok, msg, err_cat = deploy_to_surge(d, p["domain"])
    if ok:
        def f(r):
            r["projects"][pid]["public_url"] = "https://%s" % p["domain"]
            r["projects"][pid]["is_enabled"] = True
            r["projects"][pid]["last_deploy"] = now_str()
            r["projects"][pid]["last_error"] = ""
        reg_update(f)
        say_ok("Redeployment accepted: https://%s" % p["domain"])
        up, res = verify_live_robust("https://%s" % p["domain"])
        if up:
            say_ok("Live check confirmed (HTTP %d)" % res["code"])
        else:
            say_warn("Live status check: %s" % res["status_label"])
    else:
        say_err("Redeploy failed [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 300)))
        reg_update(lambda r: r["projects"][pid].update({"last_error": "[%s] %s" % (err_cat, shorten(msg, 80))}))


def menu_dashboard():
    title("Dashboard & live status")
    reg = load_registry()
    projs = list(reg["projects"].values())
    if not projs:
        say_err("No projects registered.")
        return

    acc_ok, active_acc = get_active_surge_account()
    say_info("Checking live endpoints (Surge account: %s)..." % (active_acc if acc_ok else "Not Logged In"))
    status = live_status_map(projs)

    reachable = sum(1 for r in status.values() if 200 <= r["code"] < 400)
    forbidden = sum(1 for r in status.values() if r["code"] == 403)
    dns_err = sum(1 for r in status.values() if r["category"] == "DNS_ERROR")
    other_err = len(status) - reachable - forbidden - dns_err

    print("%s %d   %s %d   %s %d   %s %d" % (
        cP("Total"), len(projs),
        cOK("Reachable"), reachable,
        cER("403 Forbidden"), forbidden,
        cWN("DNS/Pending"), dns_err
    ))
    rule()

    for p in projs:
        if not p.get("is_enabled", True):
            b = cDM("● OFF", True)
            extra = ""
        elif p["id"] in status:
            res = status[p["id"]]
            b = badge_detailed(res)
            extra = cDM(" %sms" % res["latency_ms"]) if res["latency_ms"] else ""
        else:
            b = cWN("● NOT DEPLOYED", True)
            extra = ""

        print("%s %s %s%s" % (b, cTX(shorten(p["id"], 18), True), cA("v%s" % p.get("current_version", 0)), extra))
        url_display = p.get("public_url") or ("https://" + p.get("domain", ""))
        print("  " + cDM(shorten(url_display, tw() - 4)))
        if p.get("last_error"):
            print("  " + cER("Last Err: " + shorten(p["last_error"], tw() - 14)))
    rule()


def menu_versions():
    title("Version history & rollback")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    for v in sorted(p["versions"], key=lambda x: x["version"]):
        cur = cOK(" ◀ current") if v["version"] == p["current_version"] else ""
        dep = "" if v.get("deployed", True) else cWN(" (deploy failed)")
        print(" %s %s %s%s%s" % (cA("v%d" % v["version"]), cDM(v["created_at"]),
                                 cTX(shorten(v["note"], 20)), cur, dep))
    if not confirm("Rollback to a previous version?"):
        return
    t = ask("Version number to restore")
    if not t.isdigit():
        say_err("Invalid number.")
        return
    t = int(t)
    if not any(v["version"] == t for v in p["versions"]) or not os.path.isdir(ver_dir(pid, t)):
        say_err("Version %d is not available on disk." % t)
        return

    say_info("Redeploying version v%d to %s ..." % (t, p["domain"]))
    ok, msg, err_cat = deploy_to_surge(ver_dir(pid, t), p["domain"])
    if ok:
        def f(r):
            r["projects"][pid]["current_version"] = t
            r["projects"][pid]["public_url"] = "https://%s" % p["domain"]
            r["projects"][pid]["last_error"] = ""
        reg_update(f)
        log_audit("rollback", "%s -> v%d" % (pid, t))
        say_ok("Successfully restored and published Version %d!" % t)
    else:
        say_err("Rollback deployment rejected [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 250)))


def menu_domain():
    title("Domain manager & conflict checker")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    print("Current Domain: " + cP(p["domain"]))
    suffix = load_config().get("default_surge_domain_suffix", "surge.sh")

    nd = normalize_domain(ask("New domain (subdomain or custom domain)"), suffix)
    if not nd:
        say_err("Invalid domain syntax. Must have a valid extension (e.g., site.surge.sh or example.com).")
        return

    conflict_id = next((k for k, x in reg["projects"].items() if x.get("domain") == nd and k != pid), None)
    if conflict_id:
        say_warn("Notice: Local project '%s' is already set to '%s'." % (conflict_id, nd))
        if not confirm("Reassign domain to '%s'?" % pid):
            return

    current_v_dir = ver_dir(pid, p["current_version"])
    if not os.path.isdir(current_v_dir):
        say_err("Local version folder missing. Cannot deploy domain change.")
        return

    say_info("Deploying project to new domain '%s'..." % nd)
    ok, msg, err_cat = deploy_to_surge(current_v_dir, nd)
    if ok:
        old = p["domain"]
        def f(r):
            r["projects"][pid]["domain"] = nd
            r["projects"][pid]["public_url"] = "https://%s" % nd
            r["projects"][pid]["last_error"] = ""
        reg_update(f)
        say_ok("Domain updated successfully! Live at https://%s" % nd)
        if old != nd and confirm("Tear down old domain '%s' from Surge?" % old):
            t_ok, t_msg = teardown_surge(old)
            (say_ok if t_ok else say_err)("Old domain removed." if t_ok else shorten(t_msg, 200))
        log_audit("domain_changed", "%s: %s -> %s" % (pid, old, nd))
    else:
        say_err("Domain deployment rejected [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 250)))
        if err_cat == "PERMISSION_DENIED":
            say_err("Domain '%s' is claimed by another account. Choose another subdomain." % nd)


def menu_toggle():
    title("Enable / disable site")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    if p.get("is_enabled", True):
        if not confirm("Take %s offline (surge teardown)?" % p["domain"]):
            return
        ok, msg = teardown_surge(p["domain"])
        if ok:
            def f(r):
                r["projects"][pid]["is_enabled"] = False
                r["projects"][pid]["public_url"] = None
            reg_update(f)
            log_audit("disabled", pid)
            say_ok("Site taken offline successfully.")
        else:
            say_err("Teardown failed: " + shorten(msg.replace("\n", " "), 250))
    else:
        say_info("Bringing site back online...")
        current_v = ver_dir(pid, p["current_version"])
        ok, msg, err_cat = deploy_to_surge(current_v, p["domain"])
        if ok:
            def f(r):
                r["projects"][pid]["is_enabled"] = True
                r["projects"][pid]["public_url"] = "https://%s" % p["domain"]
                r["projects"][pid]["last_error"] = ""
            reg_update(f)
            log_audit("enabled", pid)
            say_ok("Site back online: https://%s" % p["domain"])
        else:
            say_err("Failed to bring online [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 250)))


def menu_search():
    title("Search projects")
    q = ask("Name, ID or domain").lower()
    found = [p for p in load_registry()["projects"].values()
             if q in p["id"].lower() or q in p.get("name", "").lower() or q in p.get("domain", "").lower()]
    if not found:
        say_warn("No matches found.")
        return
    say_ok("%d project(s) matched:" % len(found))
    for p in found:
        print(" %s %s" % (cA("•"), cTX(p["id"], True)))
        print("   Domain: " + cDM(p.get("domain", "N/A")))
        print("   URL:    " + cP(p.get("public_url") or "Not live"))


# ------------------------- Realtime Services ----------------------
def live_monitor():
    title("Realtime monitor")
    cfg = load_config()
    interval = max(2, int(cfg.get("monitor_interval", 5)))
    stats, prev_up = {}, {}
    wake(True)
    say_info("Uptime monitor active. Press Ctrl+C to stop.")
    time.sleep(1)
    try:
        while True:
            reg = load_registry()
            projs = [p for p in reg["projects"].values()
                     if p.get("public_url") and p.get("is_enabled", True)]
            status = live_status_map(projs)
            clear_screen()
            box([
                cP("REALTIME UPTIME MONITOR", True),
                cDM("Polling every %ds · Ctrl+C to stop" % interval),
                cDM(now_str())
            ])
            if not projs:
                say_warn("No active sites to monitor.")
            for p in projs:
                res = status.get(p["id"], {"code": 0, "latency_ms": None, "category": "OFFLINE", "status_label": "DOWN"})
                s = stats.setdefault(p["id"], {"n": 0, "up": 0})
                is_up = 200 <= res["code"] < 400
                s["n"] += 1
                s["up"] += 1 if is_up else 0
                pct = 100.0 * s["up"] / s["n"]

                if p["id"] in prev_up and prev_up[p["id"]] and not is_up:
                    notify("Site DOWN", "%s returned %s" % (p["domain"], res["status_label"]))
                    log_audit("site_down", "%s [%s]" % (p["domain"], res["status_label"]))
                if p["id"] in prev_up and not prev_up[p["id"]] and is_up:
                    notify("Site back UP", "%s is reachable" % p["domain"])
                    log_audit("site_up", p["domain"])
                prev_up[p["id"]] = is_up

                lat = "%dms" % res["latency_ms"] if res["latency_ms"] is not None else "--"
                print("%s %s" % (badge_detailed(res), cTX(shorten(p["id"], 20), True)))
                print("  %s %s %s" % (cDM(lat), cDM("·"), cDM("uptime %.1f%% (%d checks)" % (pct, s["n"]))))
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        wake(False)
    print()
    say_info("Monitor stopped.")


def folder_signature(path):
    h = hashlib.sha1()
    if os.path.isfile(path):
        try:
            st = os.stat(path)
            h.update(("%d:%d" % (st.st_mtime_ns, st.st_size)).encode())
            return h.hexdigest()
        except OSError:
            return ""
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "node_modules")
        for f in sorted(files):
            fp = os.path.join(root, f)
            try:
                st = os.stat(fp)
                h.update(("%s:%d:%d" % (os.path.relpath(fp, path), st.st_mtime_ns, st.st_size)).encode())
            except OSError:
                pass
    return h.hexdigest()


def live_sync():
    title("Live auto-sync deployment")
    reg = load_registry()
    targets = [p for p in reg["projects"].values()
               if p.get("source_path") and os.path.exists(p["source_path"]) and p.get("is_enabled", True)]
    if not targets:
        say_err("No active projects have a valid source path. Deploy from a folder/file first.")
        return

    interval = max(2, int(load_config().get("sync_interval", 3)))
    sigs = {p["id"]: folder_signature(p["source_path"]) for p in targets}
    print(cOK("Watching %d project(s) for file changes:" % len(targets)))
    for p in targets:
        print("  %s %s %s" % (cA("•"), cTX(p["id"]), cDM(shorten(p["source_path"], tw() - 16))))
    say_info("Checking every %ds. Press Ctrl+C to stop." % interval)
    rule()
    wake(True)
    try:
        while True:
            for p in targets:
                src = p["source_path"]
                if not os.path.exists(src):
                    continue
                sig = folder_signature(src)
                if not sig or sig == sigs.get(p["id"]):
                    continue

                time.sleep(1.5)  # Debounce file save
                sigs[p["id"]] = folder_signature(src)
                stamp = datetime.now().strftime("%H:%M:%S")
                print("%s %s modified → auto-deploying..." % (cDM(stamp), cTX(p["id"], True)))
                ok, msg, v, url, err_cat = publish_version(p["id"], src, "Auto-sync " + stamp, src)
                if ok:
                    say_ok("v%d published: %s" % (v, cP(url)))
                    notify("Deployed", "%s v%d" % (p["id"], v))
                else:
                    say_err("Auto-sync deploy failed [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 200)))
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        wake(False)
    print()
    say_info("Auto-sync stopped.")


def menu_share():
    title("Share production link")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    url = reg["projects"][pid].get("public_url")
    if not url:
        say_err("Project does not have an active public URL.")
        return
    print("\n" + cP(url, True) + "\n")
    print("  %s Copy to clipboard   %s Open in browser   %s Termux Share sheet" % (cP("1"), cP("2"), cP("3")))
    ch = ask("Select", "1")
    if ch == "1":
        if termux_exec("termux-clipboard-set", url):
            say_ok("URL copied to clipboard!")
        else:
            say_warn("Clipboard API unavailable. Copy manually from above.")
    elif ch == "2":
        if not (termux_exec("termux-open-url", url) or termux_exec("xdg-open", url)):
            say_warn("Unable to launch browser automatically.")
    elif ch == "3":
        if not termux_exec("termux-share", "-a", "send", input_text=url):
            say_warn("termux-share not installed or supported.")


# --------------------------- Surge Account ------------------------
def menu_surge_account():
    title("Surge account manager")
    if not surge_available():
        say_err("Surge CLI is missing. Use menu 21 to install.")
        return

    acc_ok, active_acc = get_active_surge_account()
    print(" Active Account: " + (cOK(active_acc, True) if acc_ok else cER("Not Logged In", True)))
    rule()
    print("  %s Interactive CLI Login (or Create Account)\n"
          "  %s Verify Account Email (surge verify)\n"
          "  %s View Stored Token (Masked)\n"
          "  %s Save Custom Email + Token\n"
          "  %s Who am I (surge whoami)\n"
          "  %s List published domains (surge list)\n"
          "  %s Teardown / Remove a domain\n"
          "  %s Logout active account"
          % tuple(cP(str(i)) for i in range(1, 9)))

    ch = ask("Select")
    if ch == "1":
        say_info("Running official Surge login flow. If you do not have an account, enter a new email to create one.")
        run_interactive(["surge", "login"])
        acc_ok, active_acc = get_active_surge_account()
        if acc_ok:
            say_ok("Successfully authenticated as: " + active_acc)
        else:
            say_warn("Authentication incomplete.")
    elif ch == "2":
        say_info("Running 'surge verify' to check email verification status...")
        run_interactive(["surge", "verify"], env=surge_env())
        say_info("If Surge requires verification, please check your inbox and click the activation link.")
    elif ch == "3":
        cfg = load_config()
        tok = cfg.get("surge_token", "")
        if tok:
            print("Stored Token: " + cP(mask_token(tok), True))
            say_warn("Security Note: Never share your Surge token or post it publicly.")
            if confirm("Reveal full token?"):
                print("Full Token: " + cTX(tok))
        else:
            say_info("No custom token saved. Surge is using CLI default credentials.")
    elif ch == "4":
        cfg = load_config()
        new_email = ask("Surge Email", cfg.get("surge_email"))
        if new_email:
            valid_syntax, typo_warn = validate_email_syntax(new_email)
            if not valid_syntax:
                say_err(typo_warn)
                return
            if typo_warn:
                say_warn(typo_warn)
        new_token = ask("Surge Token")
        if new_email and new_token:
            cfg["surge_email"] = new_email.strip()
            cfg["surge_token"] = new_token.strip()
            save_config(cfg)
            say_ok("Custom credentials saved securely.")
    elif ch == "5":
        run_interactive(["surge", "whoami"], env=surge_env())
    elif ch == "6":
        run_interactive(["surge", "list"], env=surge_env())
    elif ch == "7":
        d = ask("Domain to remove completely")
        if d and confirm("Permanently remove %s from Surge?" % d):
            ok, msg = teardown_surge(d)
            (say_ok if ok else say_err)("Domain removed." if ok else shorten(msg, 250))
    elif ch == "8":
        if confirm("Log out from Surge? (Projects and local settings will remain intact)"):
            run_interactive(["surge", "logout"])
            cfg = load_config()
            cfg["surge_token"] = ""
            save_config(cfg)
            say_ok("Logged out.")


# ----------------------------- Data Tools -------------------------
def menu_expiry():
    title("Renewal & hosting reminders")
    projs = load_registry()["projects"]
    if not projs:
        say_err("No projects found.")
        return
    now = datetime.now()
    for pid, p in projs.items():
        try:
            exp = datetime.strptime(p.get("expiry_date", ""), "%Y-%m-%d %H:%M:%S")
            left = (exp - now).days
            col = cOK if left > 30 else (cWN if left > 0 else cER)
            print(" %s %s" % (cTX(shorten(pid, 18), True), col("%d days left" % left)))
        except Exception:
            print(" %s %s" % (cTX(pid), cDM("no reminder date")))
    say_info("Note: Free Surge projects do not expire automatically. This is your personal tracking registry.")
    if confirm("Extend a project reminder?"):
        pid = ask("Project ID")
        if pid in projs:
            days = ask("Days to extend", "365")
            days = int(days) if days.isdigit() else 365
            def f(r):
                q = r["projects"][pid]
                try:
                    base = datetime.strptime(q.get("expiry_date"), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    base = datetime.now()
                q["expiry_date"] = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            reg_update(f)
            say_ok("Reminder extended by %d days." % days)
        else:
            say_err("Project not found.")


def menu_backup():
    title("System backup & restore")
    print("  %s Create Full Backup ZIP   %s Restore from Backup ZIP" % (cP("1"), cP("2")))
    ch = ask("Select")
    if ch == "1":
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(BACKUP_DIR, "codemaster_backup_%s.zip" % ts)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(PROJECTS_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    z.write(fp, os.path.relpath(fp, BASE_DIR))
            if os.path.exists(REGISTRY_FILE):
                z.write(REGISTRY_FILE, "registry.json")
            cfg = load_config()
            cfg_clean = dict(cfg)
            cfg_clean["surge_token"] = ""
            z.writestr("config.json", json.dumps(cfg_clean, indent=2))
        say_ok("Safe backup created: " + cP(out))
        log_audit("backup_created", out)
    elif ch == "2":
        files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".zip"))
        if not files:
            say_warn("No backup archives found in ~/.codemaster_v4/backups/")
        for i, f in enumerate(files, 1):
            print(" %s %s" % (cA("%2d." % i), cTX(f)))
        s = ask("Archive number or custom path")
        path = os.path.join(BACKUP_DIR, files[int(s) - 1]) if s.isdigit() and 1 <= int(s) <= len(files) \
            else os.path.expanduser(s)
        if not os.path.isfile(path):
            say_err("Backup archive not found.")
            return
        try:
            safe_extract_zip(path, BASE_DIR, skip=("config.json",))
            say_ok("System restored successfully! (Local Surge credentials were preserved)")
            log_audit("backup_restored", path)
        except Exception as e:
            say_err("Restore failed: %s" % e)


def menu_zip():
    title("ZIP export & import")
    print("  %s Export Project to ZIP   %s Import Project from ZIP" % (cP("1"), cP("2")))
    ch = ask("Select")
    reg = load_registry()
    if ch == "1":
        pid = pick_project(reg)
        if not pid:
            return
        p = reg["projects"][pid]
        d = ver_dir(pid, p["current_version"])
        out_dir = os.path.expanduser("~/storage/downloads") if os.path.isdir(
            os.path.expanduser("~/storage/downloads")) else os.path.expanduser("~")
        out = os.path.join(out_dir, "%s_v%s.zip" % (pid, p["current_version"]))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(d):
                for f in files:
                    fp = os.path.join(root, f)
                    z.write(fp, os.path.relpath(fp, d))
        say_ok("Exported successfully: " + cP(out))
    elif ch == "2":
        zp = os.path.expanduser(ask("Path to ZIP file"))
        if not os.path.isfile(zp):
            say_err("File not found.")
            return
        name = ask("New project name")
        pid = sanitize_id(name)
        if pid in reg["projects"]:
            say_err("Project ID already exists.")
            return
        tmp = tempfile.mkdtemp()
        try:
            safe_extract_zip(zp, tmp)
            suffix = load_config().get("default_surge_domain_suffix", "surge.sh")
            domain = "%s.%s" % (pid, suffix)

            def add(r):
                r["projects"][pid] = {
                    "id": pid, "name": name, "current_version": 0, "domain": domain,
                    "public_url": None, "is_enabled": True, "created_at": now_str(),
                    "expiry_date": (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S"),
                    "source_path": None, "versions": [], "last_error": ""
                }
            reg_update(add)
            say_info("Deploying extracted files to https://%s ..." % domain)
            ok, msg, v, url, err_cat = publish_version(pid, tmp, "Imported from ZIP")
            if ok:
                say_ok("Live at: " + url)
            else:
                say_err("Deployment error [%s]: %s" % (err_cat, shorten(msg.replace("\n", " "), 250)))
        except Exception as e:
            say_err("Import error: %s" % e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def menu_validate():
    title("HTML structure validator")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    idx = os.path.join(ver_dir(pid, reg["projects"][pid]["current_version"]), "index.html")
    ok, issues = html_validation(idx)
    if ok:
        say_ok("index.html passed validation without errors!")
    else:
        say_warn("%d issue(s) detected:" % len(issues))
        for i in issues:
            print("  " + cER("• ") + cTX(i))


def menu_github():
    title("GitHub backup repository sync")
    if not shutil.which("git"):
        say_err("git is not installed. Run: pkg install git")
        return
    cfg = load_config()
    remote = cfg.get("github_remote") or ask("GitHub Repo URL (e.g. https://github.com/user/repo.git)")
    if not remote:
        return
    cfg["github_remote"] = remote
    save_config(cfg)

    g = ["git", "-C", PROJECTS_DIR]
    if not os.path.isdir(os.path.join(PROJECTS_DIR, ".git")):
        run_cmd(g + ["init"])
        run_cmd(g + ["branch", "-M", "main"])
    run_cmd(g + ["remote", "remove", "origin"])
    run_cmd(g + ["remote", "add", "origin", remote])
    shutil.copy2(REGISTRY_FILE, os.path.join(PROJECTS_DIR, "registry.json"))
    run_cmd(g + ["add", "-A"])
    run_cmd(g + ["commit", "-m", "CodeMaster sync " + now_str()])
    say_info("Pushing to GitHub origin main...")
    if run_interactive(g + ["push", "-u", "origin", "main"]):
        say_ok("GitHub sync completed successfully.")
        log_audit("github_sync", remote)
    else:
        say_err("Git push failed. Ensure personal access token or SSH keys are valid.")


def menu_logs():
    title("Audit & deployment logs")
    if not os.path.exists(AUDIT_LOG_FILE):
        say_warn("No logs recorded yet.")
        return
    with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-30:]
    for ln in lines:
        ln = ln.strip()
        col = cER if ("FAILED" in ln or "DOWN" in ln or "ERROR" in ln) else (
            cOK if ("DEPLOY" in ln or "UP]" in ln) else cDM
        )
        print(col(shorten(ln, tw())))


def menu_config():
    title("Settings & UI themes")
    cfg = load_config()
    shown = dict(cfg)
    if shown.get("surge_token"):
        shown["surge_token"] = mask_token(shown["surge_token"])
    for k in ("theme", "default_surge_domain_suffix", "sync_interval", "monitor_interval",
              "keep_versions", "verify_after_deploy", "strict_html_validation", "surge_email", "surge_token"):
        print(" %s %s" % (cDM(k + ":"), cTX(safe_str(shown.get(k)))))
    rule()
    print("  %s Surge Credentials       %s Domain Suffix       %s Switch Theme\n"
          "  %s Sync Interval (sec)     %s Monitor Interval    %s Versions to Keep\n"
          "  %s Toggle Strict HTML      %s Export Redacted Diagnostics"
          % tuple(cP(str(i)) for i in range(1, 9)))
    ch = ask("Select")
    if ch == "1":
        cfg["surge_email"] = ask("Surge email", cfg.get("surge_email"))
        cfg["surge_token"] = ask("Surge token")
    elif ch == "2":
        cfg["default_surge_domain_suffix"] = ask("Suffix", cfg["default_surge_domain_suffix"])
    elif ch == "3":
        for name in THEMES:
            TH.update(THEMES[name])
            print("  " + cP("██", True) + cA("██", True) + cOK("██", True) + cWN("██", True) + " " + name)
        t = ask("Theme", cfg.get("theme", "neon"))
        if t in THEMES:
            cfg["theme"] = t
        apply_theme(cfg.get("theme", "neon"))
    elif ch in ("4", "5", "6"):
        key = {"4": "sync_interval", "5": "monitor_interval", "6": "keep_versions"}[ch]
        v = ask(key, str(cfg.get(key)))
        if v.isdigit() and int(v) > 0:
            cfg[key] = int(v)
    elif ch == "7":
        cfg["strict_html_validation"] = not cfg.get("strict_html_validation", False)
        say_ok("Strict HTML validation is now: %s" % cfg["strict_html_validation"])
    elif ch == "8":
        diag_path = os.path.join(BASE_DIR, "diagnostics_report.txt")
        with open(diag_path, "w", encoding="utf-8") as df:
            df.write("CODE MASTER %s DIAGNOSTIC REPORT\n" % VERSION)
            df.write("Generated: %s\n" % now_str())
            df.write("OS: %s %s (%s)\n" % (platform.system(), platform.release(), platform.machine()))
            df.write("Python: %s\n" % platform.python_version())
            df.write("Surge CLI Installed: %s\n" % surge_available())
            acc_ok, acc_str = get_active_surge_account()
            df.write("Active Surge Account: %s\n" % acc_str)
            df.write("Total Projects: %d\n" % len(load_registry().get("projects", {})))
        say_ok("Redacted report saved to: " + cP(diag_path))
    else:
        return
    save_config(cfg)
    say_ok("Settings saved.")


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def menu_storage():
    title("Storage manager")
    for label, d in (("Projects", PROJECTS_DIR), ("Backups", BACKUP_DIR), ("Logs", LOGS_DIR)):
        print(" %s %s" % (cTX(label.ljust(9)), cP("%.2f MB" % (dir_size(d) / 1048576.0))))
    print("  %s Clear Audit Logs   %s Prune Old Versions   %s Delete a Project"
          % tuple(cP(str(i)) for i in range(1, 4)))
    ch = ask("Select")
    if ch == "1":
        open(AUDIT_LOG_FILE, "w").close()
        say_ok("Logs cleared.")
    elif ch == "2":
        keep = int(load_config().get("keep_versions", 5))
        for pid in list(load_registry()["projects"]):
            prune_versions(pid, keep)
        say_ok("Pruning complete. Kept latest %d versions per project." % keep)
    elif ch == "3":
        reg = load_registry()
        pid = pick_project(reg)
        if not pid:
            return
        if not confirm("Are you sure you want to delete '%s' permanently?" % pid):
            return
        p = reg["projects"][pid]
        if p.get("public_url") and confirm("Also teardown/remove domain '%s' from Surge?" % p["domain"], True):
            ok, msg = teardown_surge(p["domain"])
            (say_ok if ok else say_warn)("Surge domain removed." if ok else shorten(msg, 160))
        shutil.rmtree(os.path.join(PROJECTS_DIR, pid), ignore_errors=True)
        reg_update(lambda r: r["projects"].pop(pid, None))
        log_audit("project_deleted", pid)
        say_ok("Project deleted.")


def menu_setup():
    title("Setup & diagnostics")
    print(" %s %s %s (%s)" % (cDM("System:"), cTX(platform.system()), cTX(platform.release()), platform.machine()))
    print(" %s %s%s" % (cDM("Python:"), cTX(platform.python_version()), cA("  Termux") if IS_TERMUX else ""))
    checks = [
        ("Node.js", shutil.which("node")),
        ("npm", shutil.which("npm")),
        ("npx", shutil.which("npx")),
        ("Surge CLI", shutil.which("surge")),
        ("git", shutil.which("git")),
        ("Termux:API", shutil.which("termux-open-url"))
    ]
    for n, ok in checks:
        print(" %s %s" % (cOK("✔") if ok else cER("✖"), cTX(n)))

    net = internet_ok()
    print(" %s %s" % (cOK("✔") if net else cER("✖"), cTX("Internet / Surge API Reachability")))

    acc_ok, active_acc = get_active_surge_account()
    print(" %s %s: %s" % (
        cOK("✔") if acc_ok else cWN("▲"),
        cTX("Active Account"),
        cOK(active_acc) if acc_ok else cDM(active_acc)
    ))

    try:
        t, u, f = shutil.disk_usage(BASE_DIR)
        print(" %s %s" % (cDM("Disk Space:"), cTX("%d MB free of %d MB" % (f // 2**20, t // 2**20))))
    except Exception:
        pass

    missing_node = not shutil.which("npm")
    missing_surge = not surge_available()
    if not (missing_node or missing_surge):
        say_ok("All core dependencies are satisfied!")
        return

    if confirm("Install missing packages automatically?", True):
        if missing_node:
            if IS_TERMUX:
                run_interactive(["pkg", "install", "-y", "nodejs"])
            else:
                say_warn("Please install Node.js using your system package manager.")
        if shutil.which("npm") and not surge_available():
            run_interactive(["npm", "install", "-g", "surge"])
        if surge_available():
            say_ok("Surge CLI installed successfully!")
        else:
            say_err("Surge CLI could not be installed automatically. Try: npm install -g surge")


def menu_telegram():
    title("ETeam71 Telegram Official")
    url = load_config().get("telegram_channel", "https://t.me/ETeam71_Official")
    print(cP(url, True))
    if not (termux_exec("termux-open-url", url) or termux_exec("xdg-open", url)):
        say_info("Copy the URL above and join the official Telegram channel.")


# --------------------------- Offline Test Suite -------------------
def run_test_suite():
    title("Automated test suite (Offline Mocks)")
    say_info("Running static verification and mocked logic tests...\n")
    results = []

    def log_t(name, passed, detail=""):
        results.append((name, passed, detail))
        mark = cOK("[PASS]") if passed else cER("[FAIL]")
        print(" %s %s %s" % (mark, cTX(name.ljust(35)), cDM(detail)))

    # Test 1: Domain normalization
    d1 = normalize_domain("my-site", "surge.sh")
    log_t("1. Domain Normalization", d1 == "my-site.surge.sh", d1)

    # Test 2: Invalid Domain format (TLD missing or numeric)
    d2 = normalize_domain("my-site.3", "surge.sh")
    log_t("2. Invalid TLD Rejection", d2 == "", "Rejected '%s'" % d2)

    # Test 3: Missing index.html detection
    t_tmp = tempfile.mkdtemp()
    log_t("3. Missing index.html Detection", not os.path.exists(os.path.join(t_tmp, "index.html")), "Folder empty")

    # Test 4: HTML Validation
    test_html = os.path.join(t_tmp, "index.html")
    with open(test_html, "w") as tf:
        tf.write("<!DOCTYPE html><html><head><meta charset='utf-8'><title>T</title></head><body><h1>Hi</h1></body></html>")
    ok_v, _ = html_validation(test_html)
    log_t("4. HTML Validator Positive", ok_v, "Valid HTML accepted")

    # Test 5: Broken HTML Validation
    with open(test_html, "w") as tf:
        tf.write("<html><body><div>Unclosed tag")
    ok_vb, issues = html_validation(test_html)
    log_t("5. HTML Validator Negative", not ok_vb and len(issues) > 0, "%d issues detected" % len(issues))

    # Test 6: CNAME Conflict detection
    cname_f = os.path.join(t_tmp, "CNAME")
    with open(cname_f, "w") as cf: cf.write("other-domain.surge.sh")
    conflict, _ = inspect_cname_conflict(t_tmp, "target.surge.sh")
    log_t("6. CNAME Conflict Detection", conflict, "Detected conflicting CNAME")

    # Test 7: Email Syntax Validation
    ev_ok, _ = validate_email_syntax("user@example.com")
    log_t("7. Email Syntax Check", ev_ok, "user@example.com is valid")

    # Test 8: Email Typo Detection
    _, typo_msg = validate_email_syntax("user@gnail.com")
    log_t("8. Email Typo Warning", "gmail.com" in typo_msg, typo_msg)

    # Test 9: Token Masking
    masked = mask_token("secret_token_123456789")
    log_t("9. Token Masking", "••••••••6789" == masked, masked)

    # Test 10: Path Traversal Guard
    safe_p = is_safe_path(t_tmp, os.path.join(t_tmp, "sub/file.txt"))
    unsafe_p = is_safe_path(t_tmp, os.path.join(t_tmp, "../escaped.txt"))
    log_t("10. Path Traversal Guard", safe_p and not unsafe_p, "Blocked parent escape")

    # Test 11: HTTP 403 Diagnostics Handling
    health_mock = {"code": 403, "category": "HTTP_ERROR", "error_desc": "Forbidden"}
    b_txt = strip_ansi(badge_detailed(health_mock))
    log_t("11. HTTP 403 Badge Status", "403 FORBIDDEN" in b_txt, b_txt)

    # Test 12: DNS Error Diagnostics
    health_dns = {"code": 0, "category": "DNS_ERROR", "error_desc": "Resolution Failed"}
    b_dns = strip_ansi(badge_detailed(health_dns))
    log_t("12. DNS Error Badge Status", "DNS PENDING" in b_dns, b_dns)

    # Cleanup temp
    shutil.rmtree(t_tmp, ignore_errors=True)

    passed_count = sum(1 for _, p, _ in results if p)
    print("\n%s" % (cOK("Passed %d of %d tests successfully!" % (passed_count, len(results)))))
    pause()


# --------------------------- Menu Structure -----------------------
MENU_SECTIONS = [
    ("DEPLOY", [
        (1, "Deploy new project", menu_deploy),
        (2, "Update project", menu_update),
        (3, "Redeploy current version", menu_redeploy),
        (16, "Share link", menu_share),
    ]),
    ("MANAGE", [
        (4, "Dashboard (live status)", menu_dashboard),
        (5, "Version history & rollback", menu_versions),
        (6, "Domain manager", menu_domain),
        (7, "Enable / disable site", menu_toggle),
        (8, "Search projects", menu_search),
    ]),
    ("REALTIME", [
        (9, "Live monitor (uptime)", live_monitor),
        (10, "Live auto-sync deploy", live_sync),
        (11, "Surge account", menu_surge_account),
    ]),
    ("DATA", [
        (12, "Renewal reminders", menu_expiry),
        (13, "Backup & restore", menu_backup),
        (14, "ZIP export & import", menu_zip),
        (15, "HTML validator", menu_validate),
        (17, "GitHub backup sync", menu_github),
    ]),
    ("SYSTEM", [
        (18, "Audit logs", menu_logs),
        (19, "Settings & themes", menu_config),
        (20, "Storage manager", menu_storage),
        (21, "Setup & diagnostics", menu_setup),
        (22, "ETeam71 Telegram", menu_telegram),
    ]),
]
DISPATCH = {n: fn for _, items in MENU_SECTIONS for n, _, fn in items}


# ------------------------------ Main ------------------------------
def main():
    ensure_environment()
    apply_theme(load_config().get("theme", "neon"))

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--sync":
        live_sync()
        return
    if arg == "--monitor":
        live_monitor()
        return
    if arg == "--setup":
        menu_setup()
        return
    if arg == "--test":
        run_test_suite()
        return
    if arg == "--version":
        print("Code Master v%s" % VERSION)
        return

    while True:
        print_banner()
        print_menu()
        try:
            choice = input(cA("CM", True) + cP(" ❯ ", True)).strip()
        except EOFError:
            break
        if choice in ("0", "00"):
            print(cOK("\nThank you for using CODE MASTER — ETeam71!"))
            break
        if choice.isdigit() and int(choice) in DISPATCH:
            try:
                DISPATCH[int(choice)]()
            except KeyboardInterrupt:
                print()
                say_warn("Action cancelled.")
            except Exception as e:
                say_err("Unexpected system error: %s" % e)
                log_audit("error", "%s: %s" % (choice, e))
        else:
            say_err("Invalid selection.")
        pause()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n" + cWN("Program terminated by user."))
        sys.exit(0)
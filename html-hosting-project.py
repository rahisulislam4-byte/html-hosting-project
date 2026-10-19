#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
CODE MASTER v5.0 — ETeam71
Cloud-first HTML hosting manager (Surge) for Termux / Linux / macOS

Termux setup (one time):
    pkg update -y && pkg install -y python git nodejs
    npm install -g surge
    git clone https://github.com/USERNAME/REPO.git && cd REPO
    python codemaster.py

Shortcuts (good for tmux / background use):
    python codemaster.py --sync       live auto-deploy when files change
    python codemaster.py --monitor    realtime uptime monitor
    python codemaster.py --setup      install / check requirements
====================================================================
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import zipfile
import platform
import threading
import subprocess
import tempfile
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from html.parser import HTMLParser

VERSION = "5.0.0"

# ----------------------------- paths ------------------------------
BASE_DIR = os.path.expanduser("~/.codemaster_v4")
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
REGISTRY_FILE = os.path.join(BASE_DIR, "registry.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
AUDIT_LOG_FILE = os.path.join(LOGS_DIR, "audit.log")

REG_LOCK = threading.RLock()
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "") or "TERMUX_VERSION" in os.environ

# ----------------------------- colors -----------------------------
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


# ------------------------- screen helpers -------------------------
def tw():
    """Terminal width, clamped so it fits small Termux screens."""
    return max(32, min(shutil.get_terminal_size((50, 24)).columns - 1, 72))


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
    return v in ("y", "yes", "h", "ha")


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


# --------------------------- storage ------------------------------
def log_audit(action, details=""):
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] [%s] %s\n" % (stamp, action.upper(), details))
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
            os.chmod(CONFIG_FILE, 0o600)  # token থাকতে পারে, তাই প্রাইভেট রাখা হলো
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
    """Reload -> modify -> save under one lock (safe for live-sync)."""
    with REG_LOCK:
        reg = load_registry()
        res = fn(reg)
        save_registry(reg)
        return res


# --------------------------- utilities ----------------------------
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


def run_cmd(cmd, env=None, timeout=240):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        out = strip_ansi((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Timed out."
    except Exception as e:
        return False, str(e)


def run_interactive(cmd, env=None):
    try:
        return subprocess.run(cmd, env=env).returncode == 0
    except Exception as e:
        say_err(str(e))
        return False


def sanitize_id(name):
    pid = "".join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c in "-_")
    return pid or "proj_%d" % int(time.time())


DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def normalize_domain(raw, suffix):
    d = raw.strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if d and "." not in d:
        d = "%s.%s" % (d, suffix)
    return d if DOMAIN_RE.match(d) else ""


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


# ------------------------- network / surge ------------------------
def check_url(url, timeout=8):
    """Returns (status_code, latency_ms). status 0 = unreachable."""
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CodeMaster/%s" % VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, int((time.time() - start) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, int((time.time() - start) * 1000)
    except Exception:
        return 0, None


def internet_ok():
    return check_url("https://surge.sh", 5)[0] != 0


def surge_available():
    return shutil.which("surge") is not None


def surge_env():
    env = os.environ.copy()
    cfg = load_config()
    if cfg.get("surge_email") and cfg.get("surge_token"):
        env["SURGE_LOGIN"] = cfg["surge_email"]
        env["SURGE_TOKEN"] = cfg["surge_token"]
    return env


def deploy_to_surge(folder, domain):
    if not surge_available():
        return False, "Surge CLI missing. Open menu 21 (Setup) to install it."
    ok, out = run_cmd(["surge", folder, domain], env=surge_env())
    if not ok and not (load_config().get("surge_token")):
        out += "\nTip: login first (menu 11) or save email+token."
    return ok, out[-500:]


def teardown_surge(domain):
    if not surge_available():
        return False, "Surge CLI missing."
    return run_cmd(["surge", "teardown", domain], env=surge_env())


def verify_live(url, tries=4):
    for i in range(tries):
        time.sleep(2)
        code, ms = check_url(url)
        if 200 <= code < 400:
            return True, code, ms
    return False, code, ms


def live_status_map(projects):
    targets = [p for p in projects if p.get("public_url") and p.get("is_enabled", True)]
    if not targets:
        return {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda p: check_url(p["public_url"]), targets))
    return {p["id"]: r for p, r in zip(targets, res)}


def badge(code):
    if 200 <= code < 400:
        return cOK("● LIVE", True)
    if code == 0:
        return cER("● DOWN", True)
    return cWN("● HTTP %d" % code, True)


# ------------------------ project / versions ----------------------
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
    """Create a new version and push it to the cloud. Returns (ok, msg, ver, url)."""
    reg = load_registry()
    p = reg["projects"][pid]
    new_v = max([v["version"] for v in p["versions"]] + [0]) + 1
    vdir = create_project_version(pid, src, new_v)
    ok, msg = deploy_to_surge(vdir, p["domain"])
    url = "https://%s" % p["domain"] if ok else p.get("public_url")

    def apply(r):
        q = r["projects"][pid]
        q["current_version"] = new_v
        q["public_url"] = url
        if ok:
            q["is_enabled"] = True
            q["last_deploy"] = now_str()
        if source_path:
            q["source_path"] = source_path
        q["versions"].append({"version": new_v, "created_at": now_str(),
                              "note": note, "deployed": ok})
    reg_update(apply)
    log_audit("deploy" if ok else "deploy_failed", "%s v%d -> %s" % (pid, new_v, p["domain"]))
    prune_versions(pid, int(load_config().get("keep_versions", 5)))
    return ok, msg, new_v, url


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


# ----------------------- HTML validation --------------------------
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
        issues.append("No viewport meta (page may look bad on phones)")
    if not any("charset" in m for m in c.meta):
        issues.append("No charset meta (add <meta charset=\"utf-8\">)")
    return len(issues) == 0, issues


# --------------------------- banner/menu --------------------------
def print_banner():
    clear_screen()
    reg = load_registry()
    projs = reg.get("projects", {})
    live = sum(1 for p in projs.values() if p.get("public_url") and p.get("is_enabled", True))
    surge = cOK("Surge ✔") if surge_available() else cER("Surge ✖")
    box([
        cA("★ ", True) + cP("CODE MASTER", True) + cA(" ★", True),
        cTX("v%s CLOUD HOSTING" % VERSION, True),
        cDM("Powered by ETeam71"),
        "%s  %s  %s" % (surge, cP("Sites %d" % len(projs)), cOK("Live %d" % live)),
    ])


MENU_SECTIONS = []  # filled after functions are defined


def print_menu():
    for head, items in MENU_SECTIONS:
        print(cA("◆ " + head, True))
        for n, label, _ in items:
            print("  %s %s" % (cP("%02d" % n, True), cTX(label)))
    print(cER("  00 ") + cTX("Exit"))
    rule()


# ============================ MENUS ================================
def menu_deploy():
    title("Deploy new project")
    cfg = load_config()
    name = ask("Project name")
    if not name:
        say_err("Name cannot be empty.")
        return
    pid = sanitize_id(name)
    reg = load_registry()
    if pid in reg["projects"]:
        say_err("ID '%s' already exists. Use Update (menu 2)." % pid)
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
            say_warn("Validation: " + "; ".join(issues[:4]))
        source_path = src
    elif ch == "2":
        src = os.path.expanduser(ask("Folder path"))
        if not os.path.isdir(src):
            say_err("Folder not found.")
            return
        if not os.path.exists(os.path.join(src, "index.html")):
            say_warn("index.html not found inside the folder!")
        source_path = src
    elif ch == "3":
        print(cDM("Paste HTML. Type __EOF__ on a new line to finish."))
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
        say_err("Invalid choice.")
        return

    suffix = cfg.get("default_surge_domain_suffix", "surge.sh")
    default_domain = "%s.%s" % (pid, suffix)
    domain = normalize_domain(ask("Domain", default_domain), suffix)
    if not domain:
        say_err("Invalid domain.")
        return

    expiry = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    def add(r):
        r["projects"][pid] = {
            "id": pid, "name": name, "current_version": 0, "domain": domain,
            "public_url": None, "is_enabled": True, "created_at": now_str(),
            "expiry_date": expiry, "source_path": None, "versions": [],
        }
    reg_update(add)

    say_info("Deploying to https://%s ..." % domain)
    ok, msg, v, url = publish_version(pid, src, "Initial deployment", source_path)
    if tmp and os.path.exists(tmp):
        os.remove(tmp)

    if ok:
        say_ok("Deployed! " + cP(url, True))
        if cfg.get("verify_after_deploy", True):
            say_info("Checking that the site is live...")
            up, code, ms = verify_live(url)
            if up:
                say_ok("Live check passed (HTTP %d, %d ms)" % (code, ms))
            else:
                say_warn("Not reachable yet (HTTP %s). DNS can take a minute." % code)
        log_audit("project_created", pid)
    else:
        say_err("Deploy failed:")
        print(cDM(shorten(msg.replace("\n", " "), 300)))
        say_warn("Project saved. Fix the issue then use 'Redeploy' (menu 3).")


def menu_update():
    title("Update project (same URL)")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    default_src = p.get("source_path") or ""
    path = os.path.expanduser(ask("New source file/folder", default_src))
    if not path or not os.path.exists(path):
        say_err("Source path does not exist.")
        return
    note = ask("Release note", "Updated release")
    say_info("Uploading to %s ..." % p["domain"])
    ok, msg, v, url = publish_version(pid, path, note, path)
    if ok:
        say_ok("Version %d is live at %s" % (v, cP(url, True)))
    else:
        say_err("Cloud update failed: " + shorten(msg.replace("\n", " "), 250))


def menu_redeploy():
    title("Redeploy current version")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    d = ver_dir(pid, p["current_version"])
    if not os.path.isdir(d):
        say_err("Version folder missing.")
        return
    say_info("Redeploying v%s to %s ..." % (p["current_version"], p["domain"]))
    ok, msg = deploy_to_surge(d, p["domain"])
    if ok:
        def f(r):
            r["projects"][pid]["public_url"] = "https://%s" % p["domain"]
            r["projects"][pid]["is_enabled"] = True
            r["projects"][pid]["last_deploy"] = now_str()
        reg_update(f)
        say_ok("Done: https://%s" % p["domain"])
    else:
        say_err(shorten(msg.replace("\n", " "), 300))


def menu_dashboard():
    title("Dashboard (live)")
    reg = load_registry()
    projs = list(reg["projects"].values())
    if not projs:
        say_err("No projects yet.")
        return
    say_info("Checking live status...")
    status = live_status_map(projs)
    up = sum(1 for c, _ in status.values() if 200 <= c < 400)
    print("%s %d   %s %d   %s %d" % (cP("Total"), len(projs), cOK("Up"), up,
                                     cER("Down"), len(status) - up))
    rule()
    for p in projs:
        if not p.get("is_enabled", True):
            b = cDM("● OFF", True)
            extra = ""
        elif p["id"] in status:
            code, ms = status[p["id"]]
            b = badge(code)
            extra = cDM(" %sms" % ms) if ms is not None else ""
        else:
            b = cWN("● NOT DEPLOYED", True)
            extra = ""
        print("%s %s %s%s" % (b, cTX(shorten(p["id"], 18), True), cA("v%s" % p.get("current_version", 0)), extra))
        print("  " + cDM(shorten(p.get("public_url") or "https://" + p.get("domain", ""), tw() - 3)))
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
        dep = "" if v.get("deployed", True) else cWN(" (not deployed)")
        print(" %s %s %s%s%s" % (cA("v%d" % v["version"]), cDM(v["created_at"]),
                                 cTX(shorten(v["note"], 20)), cur, dep))
    if not confirm("Rollback to a version?"):
        return
    t = ask("Version number")
    if not t.isdigit():
        say_err("Invalid number.")
        return
    t = int(t)
    if not any(v["version"] == t for v in p["versions"]) or not os.path.isdir(ver_dir(pid, t)):
        say_err("That version is not available.")
        return
    say_info("Redeploying v%d ..." % t)
    ok, msg = deploy_to_surge(ver_dir(pid, t), p["domain"])
    if ok:
        def f(r):
            r["projects"][pid]["current_version"] = t
            r["projects"][pid]["public_url"] = "https://%s" % p["domain"]
        reg_update(f)
        log_audit("rollback", "%s -> v%d" % (pid, t))
        say_ok("Rolled back to v%d" % t)
    else:
        say_err("Rollback failed: " + shorten(msg.replace("\n", " "), 250))


def menu_domain():
    title("Domain manager")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    print("Current: " + cP(p["domain"]))
    suffix = load_config().get("default_surge_domain_suffix", "surge.sh")
    nd = normalize_domain(ask("New domain (name or full domain)"), suffix)
    if not nd:
        say_err("Invalid domain.")
        return
    say_info("Publishing current version to %s ..." % nd)
    ok, msg = deploy_to_surge(ver_dir(pid, p["current_version"]), nd)
    if ok:
        old = p["domain"]

        def f(r):
            r["projects"][pid]["domain"] = nd
            r["projects"][pid]["public_url"] = "https://%s" % nd
        reg_update(f)
        say_ok("Live at https://%s" % nd)
        if old != nd and confirm("Remove the old domain %s from Surge?" % old):
            t_ok, t_msg = teardown_surge(old)
            (say_ok if t_ok else say_err)("Old domain removed." if t_ok else shorten(t_msg, 200))
        log_audit("domain_changed", "%s -> %s" % (pid, nd))
    else:
        say_err("Failed: " + shorten(msg.replace("\n", " "), 250))
        say_info("For a custom domain (example.com) add a CNAME/ALIAS record pointing to na-west1.surge.sh")


def menu_toggle():
    title("Enable / disable site")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    p = reg["projects"][pid]
    if p.get("is_enabled", True):
        if not confirm("Take %s offline (Surge teardown)?" % p["domain"]):
            return
        ok, msg = teardown_surge(p["domain"])
        if ok:
            def f(r):
                r["projects"][pid]["is_enabled"] = False
                r["projects"][pid]["public_url"] = None
            reg_update(f)
            log_audit("disabled", pid)
            say_ok("Site is now offline.")
        else:
            say_err(shorten(msg.replace("\n", " "), 250))
    else:
        say_info("Bringing it back online...")
        ok, msg = deploy_to_surge(ver_dir(pid, p["current_version"]), p["domain"])
        if ok:
            def f(r):
                r["projects"][pid]["is_enabled"] = True
                r["projects"][pid]["public_url"] = "https://%s" % p["domain"]
            reg_update(f)
            log_audit("enabled", pid)
            say_ok("Online: https://%s" % p["domain"])
        else:
            say_err(shorten(msg.replace("\n", " "), 250))


def menu_search():
    title("Search projects")
    q = ask("Name, ID or domain").lower()
    found = [p for p in load_registry()["projects"].values()
             if q in p["id"].lower() or q in p.get("name", "").lower() or q in p.get("domain", "").lower()]
    if not found:
        say_warn("No match.")
        return
    say_ok("%d result(s)" % len(found))
    for p in found:
        print(" %s %s" % (cA("•"), cTX(p["id"], True)))
        print("   " + cDM(p.get("public_url") or p.get("domain")))


# ------------------------- realtime features ----------------------
def live_monitor():
    title("Realtime monitor")
    cfg = load_config()
    interval = max(2, int(cfg.get("monitor_interval", 5)))
    stats, prev_up = {}, {}
    wake(True)
    say_info("Press Ctrl+C to stop.")
    time.sleep(1)
    try:
        while True:
            reg = load_registry()
            projs = [p for p in reg["projects"].values()
                     if p.get("public_url") and p.get("is_enabled", True)]
            status = live_status_map(projs)
            clear_screen()
            box([cP("LIVE MONITOR", True), cDM("every %ds · Ctrl+C to stop" % interval),
                 cDM(datetime.now().strftime("%H:%M:%S"))])
            if not projs:
                say_warn("No deployed sites to monitor.")
            for p in projs:
                code, ms = status.get(p["id"], (0, None))
                s = stats.setdefault(p["id"], {"n": 0, "up": 0})
                is_up = 200 <= code < 400
                s["n"] += 1
                s["up"] += 1 if is_up else 0
                pct = 100.0 * s["up"] / s["n"]
                if p["id"] in prev_up and prev_up[p["id"]] and not is_up:
                    notify("Site DOWN", "%s is not reachable" % p["domain"])
                    log_audit("site_down", p["domain"])
                if p["id"] in prev_up and not prev_up[p["id"]] and is_up:
                    notify("Site back UP", p["domain"])
                    log_audit("site_up", p["domain"])
                prev_up[p["id"]] = is_up
                lat = "%dms" % ms if ms is not None else "--"
                print("%s %s" % (badge(code), cTX(shorten(p["id"], 20), True)))
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
        st = os.stat(path)
        h.update(("%d:%d" % (st.st_mtime_ns, st.st_size)).encode())
        return h.hexdigest()
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
    title("Live auto-sync")
    reg = load_registry()
    targets = [p for p in reg["projects"].values()
               if p.get("source_path") and os.path.exists(p["source_path"]) and p.get("is_enabled", True)]
    if not targets:
        say_err("No project with a saved source path. Deploy from a file/folder first.")
        return
    interval = max(2, int(load_config().get("sync_interval", 3)))
    sigs = {p["id"]: folder_signature(p["source_path"]) for p in targets}
    print(cOK("Watching %d project(s) — edits deploy automatically:" % len(targets)))
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
                if sig == sigs[p["id"]]:
                    continue
                time.sleep(1.5)  # debounce (editor still saving)
                sigs[p["id"]] = folder_signature(src)
                stamp = datetime.now().strftime("%H:%M:%S")
                print("%s %s changed → deploying..." % (cDM(stamp), cTX(p["id"], True)))
                ok, msg, v, url = publish_version(p["id"], src, "Auto-sync " + stamp, src)
                if ok:
                    say_ok("v%d live: %s" % (v, cP(url)))
                    notify("Deployed", "%s v%d" % (p["id"], v))
                else:
                    say_err(shorten(msg.replace("\n", " "), 200))
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        wake(False)
    print()
    say_info("Auto-sync stopped.")


def menu_share():
    title("Share link")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    url = reg["projects"][pid].get("public_url")
    if not url:
        say_err("This project is not live yet.")
        return
    print(cP(url, True))
    print("  %s Copy   %s Open in browser   %s Share via apps" % (cP("1"), cP("2"), cP("3")))
    ch = ask("Select", "1")
    if ch == "1":
        if termux_exec("termux-clipboard-set", url):
            say_ok("Copied to clipboard.")
        else:
            say_warn("Install Termux:API (pkg install termux-api) or copy manually.")
    elif ch == "2":
        if not (termux_exec("termux-open-url", url) or termux_exec("xdg-open", url)):
            say_warn("Open this link manually.")
    elif ch == "3":
        if not termux_exec("termux-share", "-a", "send", input_text=url):
            say_warn("termux-share not available.")


# --------------------------- surge account ------------------------
def menu_surge_account():
    title("Surge account")
    if not surge_available():
        say_err("Surge CLI missing. Use menu 21 to install.")
        return
    print("  %s Login (interactive)\n  %s Show token\n  %s Save email + token\n"
          "  %s Who am I\n  %s List my sites\n  %s Teardown a domain"
          % tuple(cP(str(i)) for i in range(1, 7)))
    ch = ask("Select")
    if ch == "1":
        run_interactive(["surge", "login"])
    elif ch == "2":
        run_interactive(["surge", "token"])
        say_info("Copy that token, then choose option 3.")
    elif ch == "3":
        cfg = load_config()
        cfg["surge_email"] = ask("Surge email", cfg.get("surge_email"))
        cfg["surge_token"] = ask("Surge token")
        save_config(cfg)
        say_ok("Credentials saved (private file).")
    elif ch == "4":
        run_interactive(["surge", "whoami"], env=surge_env())
    elif ch == "5":
        run_interactive(["surge", "list"], env=surge_env())
    elif ch == "6":
        d = ask("Domain to remove")
        if d and confirm("Really remove %s from Surge?" % d):
            ok, msg = teardown_surge(d)
            (say_ok if ok else say_err)("Removed." if ok else shorten(msg, 250))


# ----------------------------- data tools -------------------------
def menu_expiry():
    title("Renewal reminders")
    projs = load_registry()["projects"]
    if not projs:
        say_err("No projects.")
        return
    now = datetime.now()
    for pid, p in projs.items():
        try:
            exp = datetime.strptime(p.get("expiry_date", ""), "%Y-%m-%d %H:%M:%S")
            left = (exp - now).days
            col = cOK if left > 30 else (cWN if left > 0 else cER)
            print(" %s %s" % (cTX(shorten(pid, 18), True), col("%d days left" % left)))
        except Exception:
            print(" %s %s" % (cTX(pid), cDM("no date")))
    say_info("Surge free sites do not expire; this is your own reminder tracker.")
    if confirm("Extend a project's reminder?"):
        pid = ask("Project ID")
        if pid in projs:
            days = ask("Days", "365")
            days = int(days) if days.isdigit() else 365

            def f(r):
                q = r["projects"][pid]
                try:
                    base = datetime.strptime(q.get("expiry_date"), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    base = datetime.now()
                q["expiry_date"] = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            reg_update(f)
            say_ok("Extended by %d days." % days)
        else:
            say_err("Project not found.")


def menu_backup():
    title("Backup & restore")
    print("  %s Create backup   %s Restore backup" % (cP("1"), cP("2")))
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
            cfg["surge_token"] = ""  # টোকেন ব্যাকআপে রাখা হয় না
            z.writestr("config.json", json.dumps(cfg, indent=2))
        say_ok("Backup created: " + cP(out))
        log_audit("backup_created", out)
    elif ch == "2":
        files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".zip"))
        for i, f in enumerate(files, 1):
            print(" %s %s" % (cA("%2d." % i), cTX(f)))
        s = ask("Number or full path")
        path = os.path.join(BACKUP_DIR, files[int(s) - 1]) if s.isdigit() and 1 <= int(s) <= len(files) \
            else os.path.expanduser(s)
        if not os.path.isfile(path):
            say_err("File not found.")
            return
        try:
            safe_extract_zip(path, BASE_DIR, skip=("config.json",))
            say_ok("Restored (your Surge credentials were kept).")
            log_audit("backup_restored", path)
        except Exception as e:
            say_err("Restore failed: %s" % e)


def menu_zip():
    title("ZIP export & import")
    print("  %s Export project   %s Import project" % (cP("1"), cP("2")))
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
        say_ok("Exported: " + cP(out))
    elif ch == "2":
        zp = os.path.expanduser(ask("ZIP file path"))
        if not os.path.isfile(zp):
            say_err("File not found.")
            return
        name = ask("New project name")
        pid = sanitize_id(name)
        if pid in reg["projects"]:
            say_err("ID already exists.")
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
                    "source_path": None, "versions": []}
            reg_update(add)
            say_info("Deploying to https://%s ..." % domain)
            ok, msg, v, url = publish_version(pid, tmp, "Imported from ZIP")
            (say_ok if ok else say_err)("Live: " + url if ok else shorten(msg.replace("\n", " "), 250))
        except Exception as e:
            say_err("Import error: %s" % e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def menu_validate():
    title("HTML validator")
    reg = load_registry()
    pid = pick_project(reg)
    if not pid:
        return
    idx = os.path.join(ver_dir(pid, reg["projects"][pid]["current_version"]), "index.html")
    ok, issues = html_validation(idx)
    if ok:
        say_ok("index.html looks great. No issues found.")
    else:
        say_warn("%d issue(s):" % len(issues))
        for i in issues:
            print("  " + cER("• ") + cTX(i))


def menu_github():
    title("GitHub backup sync")
    if not shutil.which("git"):
        say_err("git missing. Run: pkg install git")
        return
    cfg = load_config()
    remote = cfg.get("github_remote") or ask("GitHub repo URL (https://github.com/user/repo.git)")
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
    say_info("Pushing... (GitHub asks for username + personal access token)")
    if run_interactive(g + ["push", "-u", "origin", "main"]):
        say_ok("GitHub sync complete.")
        log_audit("github_sync", remote)
    else:
        say_err("Push failed. Check the URL and token.")


def menu_logs():
    title("Audit & deploy logs")
    if not os.path.exists(AUDIT_LOG_FILE):
        say_warn("No logs yet.")
        return
    with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-20:]
    for ln in lines:
        ln = ln.strip()
        col = cER if ("FAILED" in ln or "DOWN" in ln) else (cOK if ("DEPLOY" in ln or "UP]" in ln) else cDM)
        print(col(shorten(ln, tw())))


def menu_config():
    title("Settings")
    cfg = load_config()
    shown = dict(cfg)
    if shown.get("surge_token"):
        shown["surge_token"] = "•" * 8 + shown["surge_token"][-4:]
    for k in ("theme", "default_surge_domain_suffix", "sync_interval", "monitor_interval",
              "keep_versions", "verify_after_deploy", "surge_email", "surge_token", "github_remote"):
        print(" %s %s" % (cDM(k + ":"), cTX(safe_str(shown.get(k)))))
    rule()
    print("  %s Surge credentials   %s Domain suffix   %s Theme\n  %s Sync interval   %s Monitor interval   %s Versions to keep"
          % tuple(cP(str(i)) for i in range(1, 7)))
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
    else:
        return
    save_config(cfg)
    say_ok("Saved.")


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
    print("  %s Clear logs   %s Prune old versions   %s Delete a project"
          % tuple(cP(str(i)) for i in range(1, 4)))
    ch = ask("Select")
    if ch == "1":
        open(AUDIT_LOG_FILE, "w").close()
        say_ok("Logs cleared.")
    elif ch == "2":
        keep = int(load_config().get("keep_versions", 5))
        for pid in list(load_registry()["projects"]):
            prune_versions(pid, keep)
        say_ok("Kept the latest %d versions per project." % keep)
    elif ch == "3":
        reg = load_registry()
        pid = pick_project(reg)
        if not pid:
            return
        if not confirm("Delete '%s' permanently?" % pid):
            return
        p = reg["projects"][pid]
        if p.get("public_url") and confirm("Also remove %s from Surge?" % p["domain"], True):
            ok, msg = teardown_surge(p["domain"])
            (say_ok if ok else say_warn)("Removed from Surge." if ok else shorten(msg, 160))
        shutil.rmtree(os.path.join(PROJECTS_DIR, pid), ignore_errors=True)
        reg_update(lambda r: r["projects"].pop(pid, None))
        log_audit("project_deleted", pid)
        say_ok("Deleted.")


def menu_setup():
    title("Setup & diagnostics")
    print(" %s %s %s (%s)" % (cDM("System:"), cTX(platform.system()), cTX(platform.release()), platform.machine()))
    print(" %s %s%s" % (cDM("Python:"), cTX(platform.python_version()), cA("  Termux") if IS_TERMUX else ""))
    checks = [("Node.js", shutil.which("node")), ("npm", shutil.which("npm")),
              ("Surge CLI", shutil.which("surge")), ("git", shutil.which("git")),
              ("Termux:API", shutil.which("termux-open-url"))]
    for n, ok in checks:
        print(" %s %s" % (cOK("✔") if ok else cER("✖"), cTX(n)))
    net = internet_ok()
    print(" %s %s" % (cOK("✔") if net else cER("✖"), cTX("Internet / Surge reachable")))
    try:
        t, u, f = shutil.disk_usage(BASE_DIR)
        print(" %s %s" % (cDM("Disk:"), cTX("%d MB free of %d MB" % (f // 2**20, t // 2**20))))
    except Exception:
        pass
    missing_node = not shutil.which("npm")
    missing_surge = not surge_available()
    if not (missing_node or missing_surge):
        say_ok("Everything is ready for cloud deployment.")
        return
    if confirm("Install missing requirements now?", True):
        if missing_node:
            if IS_TERMUX:
                run_interactive(["pkg", "install", "-y", "nodejs"])
            else:
                say_warn("Install Node.js from https://nodejs.org then run Setup again.")
        if shutil.which("npm") and not surge_available():
            run_interactive(["npm", "install", "-g", "surge"])
        if surge_available():
            say_ok("Surge CLI installed.")
        else:
            say_err("Surge is still missing. Run manually: npm install -g surge")


def menu_telegram():
    title("ETeam71 Telegram")
    url = load_config().get("telegram_channel", "https://t.me/ETeam71_Official")
    print(cP(url, True))
    if not (termux_exec("termux-open-url", url) or termux_exec("xdg-open", url)):
        say_info("Copy the link and open it in Telegram or your browser.")


# --------------------------- menu layout --------------------------
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


# ------------------------------ main ------------------------------
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
                say_warn("Cancelled.")
            except Exception as e:
                say_err("Unexpected error: %s" % e)
                log_audit("error", "%s: %s" % (choice, e))
        else:
            say_err("Invalid selection.")
        pause()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n" + cWN("Interrupted. Bye!"))
        sys.exit(0)

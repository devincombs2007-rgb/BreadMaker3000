"""Static repository safety scan (runs in CI and before deploy).

Fails if any tracked file could enable real-money trading, contains Kalshi order/portfolio/
request-signing code, or contains anything that looks like a credential.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


SECRET_PATTERNS = [
    (re.compile(r"\bAK[A-Z0-9]{18}\b"), "Alpaca LIVE key id"),
    (re.compile(r"\bPK[A-Z0-9]{18}\b"), "Alpaca paper key id"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GitHub token"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic key"),
    (re.compile(r"(?i)\b(api[_-]?secret|secret[_-]?key|password)\s*[=:]\s*['\"]?[A-Za-z0-9/+]{24,}"), "inline secret"),
]
LIVE_ENDPOINTS = [
    re.compile(r"https?://api\.alpaca\.markets"),
    re.compile(r"(?i)\bPAPER\b\s*[\"']?\s*[:=]\s*False"),
    # Kalshi: this codebase must never place orders, touch portfolios or sign authenticated requests
    re.compile(r"/portfolio/orders|/portfolio/balance|/portfolio/positions"),
    re.compile(r"KALSHI-ACCESS-(KEY|SIGNATURE|TIMESTAMP)"),
    re.compile(r"(?i)\.post\([^)]*kalshi|kalshi[^\n]*\.post\("),
]
SKIP_SUFFIXES = {".png", ".jpg", ".ico", ".feather", ".sqlite", ".gz", ".zip"}


def tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.split()
        return [root / f for f in out]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


def scan(root: Path) -> list[str]:
    problems = []
    for f in tracked_files(root):
        if f.suffix in SKIP_SUFFIXES or not f.is_file() or f.name == "safety_scan.py" or "tests" in f.parts:
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        rel = f.relative_to(root)
        for pat, label in SECRET_PATTERNS:
            if pat.search(text):
                problems.append(f"{rel}: possible {label}")
        if f.suffix in (".py", ".json", ".yml", ".yaml", ".env", ".sh") and not str(rel).startswith("docs/"):
            for pat in LIVE_ENDPOINTS:
                if pat.search(text):
                    problems.append(f"{rel}: live-trading pattern {pat.pattern!r}")
        if f.name == ".env":
            problems.append(f"{rel}: .env must never be committed")
    return problems

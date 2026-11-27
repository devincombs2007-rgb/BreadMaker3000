"""System health checks for the GitHub-Actions deployment (read-only API calls; free).

Checks: latest CI on the default branch succeeded; forecast validation ran recently; forward-log
state artifact is fresh and not expired; Kalshi/Demo cycles are in the expected (disabled) state;
repository visibility vs Kalshi data rules. Returns a list of findings with severity.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx


def _age(ts: str | None, now: datetime) -> timedelta | None:
    if not ts:
        return None
    return now - datetime.fromisoformat(ts.replace("Z", "+00:00"))


INCLUDED_MINUTES = 2000   # GitHub Free: private-repo Actions minutes per month (whole account)


def actions_usage(gh: httpx.Client, repo: str, now: datetime, max_runs: int = 300) -> dict:
    """Billable minutes THIS repository used this month (private repos only; public repos are free).

    Sums each run's billable time (GET /actions/runs/{id}/timing). The free allowance is shared by
    all of the account's private repositories, so this is a lower bound on account usage. Charges
    are only possible if the owner has added a payment method AND set a budget above $0; with no
    budget, GitHub stops the workflows instead (see HOSTING.md)."""
    import os
    included = int(os.environ.get("ACTIONS_INCLUDED_MINUTES", INCLUDED_MINUTES))
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total_ms, page, seen = 0, 1, 0
    while seen < max_runs:
        r = gh.get(f"/repos/{repo}/actions/runs",
                   params={"created": f">={start:%Y-%m-%d}", "per_page": 100, "page": page})
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        for run in runs[: max_runs - seen]:
            t = gh.get(f"/repos/{repo}/actions/runs/{run['id']}/timing")
            if t.status_code == 200:
                total_ms += sum(int(v.get("total_ms", 0)) for v in (t.json().get("billable") or {}).values())
            seen += 1
        if len(runs) < 100:
            break
        page += 1
    minutes = total_ms / 60000
    share = minutes / included if included else 0
    sev = "error" if share >= 0.85 else "warning" if share >= 0.6 else "ok"
    return {"severity": sev, "check": "actions-minutes",
            "detail": f"this repo used ~{minutes:.0f} of {included} included private-repo minutes this month "
                      f"({share:.0%}; {seen} runs counted). At the limit GitHub stops workflows unless a paid budget is set."}


def check(repo: str, token: str, now: datetime | None = None, transport: httpx.BaseTransport | None = None,
          default_branch: str = "main") -> list[dict]:
    now = now or datetime.now(timezone.utc)
    gh = httpx.Client(base_url="https://api.github.com", transport=transport, timeout=30,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    findings: list[dict] = []

    def add(sev, what, detail):
        findings.append({"severity": sev, "check": what, "detail": detail})

    def latest(workflow: str, branch: str | None = default_branch, status: str | None = None):
        params = {"per_page": 1, **({"branch": branch} if branch else {}), **({"status": status} if status else {})}
        r = gh.get(f"/repos/{repo}/actions/workflows/{workflow}/runs", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        return runs[0] if runs else None

    try:
        ci = latest("ci.yml")
        if ci is None:
            add("warning", "ci", "no CI run on the default branch yet")
        elif ci.get("conclusion") not in ("success", None):
            add("error", "ci", f"latest CI on {default_branch} concluded '{ci.get('conclusion')}' ({ci.get('html_url')})")
        else:
            add("ok", "ci", f"latest CI on {default_branch}: {ci.get('conclusion') or ci.get('status')}")

        val = latest("forecast-validation.yml", branch=None, status="success")
        age = _age(val.get("updated_at") if val else None, now)
        if age is None or age > timedelta(days=9):
            add("warning", "validation", "no successful forecast validation in the last 9 days")
        else:
            add("ok", "validation", f"last successful validation {age.days} day(s) ago")

        r = gh.get(f"/repos/{repo}/actions/artifacts", params={"name": "forward-log-state", "per_page": 100})
        r.raise_for_status()
        arts = r.json().get("artifacts", [])
        live = [a for a in arts if not a.get("expired")]
        if not arts:
            add("ok", "forward-log", "no forward-log state yet (first run pending)")
        elif not live:
            add("error", "forward-log", "all forward-log state artifacts EXPIRED: history cannot be restored")
        else:
            newest = max(live, key=lambda a: a["created_at"])
            a_age = _age(newest["created_at"], now)
            sev = "ok" if a_age <= timedelta(days=2) else "warning"
            add(sev, "forward-log", f"newest state {a_age.days} day(s) old, expires {newest.get('expires_at', '?')[:10]}")

        info = gh.get(f"/repos/{repo}")
        info.raise_for_status()
        private = info.json().get("private")
        add("ok", "visibility", "repository is private" if private else
            "repository is public: Kalshi/Demo cycles are refused by design (no Kalshi data may be shared)")
        if private:
            findings.append(actions_usage(gh, repo, now))
    except httpx.HTTPError as exc:
        add("error", "github-api", f"health check could not query GitHub: {exc}")
    return findings


def markdown(findings: list[dict]) -> str:
    icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}
    return "## System health\n\n" + "\n".join(f"- {icon[f['severity']]} **{f['check']}**: {f['detail']}" for f in findings) + "\n"


def worst(findings: list[dict]) -> str:
    order = ["ok", "warning", "error"]
    return max((f["severity"] for f in findings), key=order.index, default="ok")


ALERT_TITLE = "BreadMaker health alert"


def alert_title(workflow: str = "") -> str:
    # One rolling issue PER workflow, so a healthy run of one workflow never closes another's alert.
    return ALERT_TITLE if workflow in ("", "daily-research") else f"{ALERT_TITLE} ({workflow})"


def alert(repo: str, token: str, findings: list[dict], report_url: str = "",
          transport: httpx.BaseTransport | None = None, workflow: str = "") -> str:
    """One rolling issue: open/comment when something is wrong, close when healthy. Returns action taken."""
    title = alert_title(workflow)
    gh = httpx.Client(base_url="https://api.github.com", transport=transport, timeout=30,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    r = gh.get(f"/repos/{repo}/issues", params={"state": "open", "per_page": 100})
    r.raise_for_status()
    existing = next((i for i in r.json() if i.get("title") == title and "pull_request" not in i), None)
    bad = [f for f in findings if f["severity"] != "ok"]
    body = markdown(findings) + (f"\n[Workflow run]({report_url})\n" if report_url else "") + \
        f"\n_Automated by the {workflow or 'daily-research'} workflow (simulation only)._"
    if bad and existing:
        gh.post(f"/repos/{repo}/issues/{existing['number']}/comments", json={"body": body}).raise_for_status()
        return "commented"
    if bad:
        gh.post(f"/repos/{repo}/issues", json={"title": title, "body": body}).raise_for_status()
        return "opened"
    if existing:
        gh.post(f"/repos/{repo}/issues/{existing['number']}/comments",
                json={"body": "All checks healthy again. Closing.\n\n" + body}).raise_for_status()
        gh.patch(f"/repos/{repo}/issues/{existing['number']}", json={"state": "closed", "state_reason": "completed"}).raise_for_status()
        return "closed"
    return "none"


def load_findings(path: str, job_status: str = "success", workflow: str = "") -> list[dict]:
    """Findings for the alert. Never 'nothing to report' just because the report is missing:
    a missing/unreadable health file, or a failed job, is itself an error finding.
    path '-' means this workflow produces no health report; only its job status counts."""
    import json
    name = workflow or "workflow"
    if path == "-":
        findings = [{"severity": "ok", "check": "workflow", "detail": f"{name} completed"}]
    else:
        try:
            with open(path) as fh:
                findings = json.loads(fh.read() or "[]")
            if not isinstance(findings, list) or not findings:
                raise ValueError("health report is empty or not a list")
        except (OSError, ValueError) as exc:
            findings = [{"severity": "error", "check": "health-report",
                         "detail": f"{name} produced no readable health report ({type(exc).__name__}); "
                                   "it failed before the health check ran"}]
    if job_status != "success":
        findings = [f for f in findings if f["check"] != "workflow"] + [
            {"severity": "error", "check": "workflow",
             "detail": f"{name} job status: {job_status or 'unknown'} (a step failed or was cancelled)"}]
    return findings


def main(argv=None) -> int:
    """python -m breadmaker.health alert <findings.json> [report_url] [job_status] [workflow_name]"""
    import os
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "alert":
        print("usage: python -m breadmaker.health alert <findings.json> [report_url] [job_status] [workflow_name]")
        return 2
    url = args[2] if len(args) > 2 else ""
    findings = load_findings(args[1], args[3] if len(args) > 3 else "success", args[4] if len(args) > 4 else "")
    workflow = args[4] if len(args) > 4 else ""
    action = alert(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"], findings, url, workflow=workflow)
    print(f"health alert: {action}; worst severity: {worst(findings)}")
    if os.environ.get("GITHUB_OUTPUT"):          # tells the workflow's fallback alert step it is not needed
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write("posted=true\n")
    return 1 if worst(findings) == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())

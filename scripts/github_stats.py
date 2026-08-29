#!/usr/bin/env python3
"""Self-hosted GitHub Stats + Streak cards as static SVGs.

Replaces github-profile-summary-cards (stats) and streak-stats
(streak) third-party services. Data via GitHub APIs only:
  - REST search/issues        -> all-time PR / issue counts
  - GraphQL user + repos      -> stars, contributed-to, account age
  - contributionsCollection   -> daily counts (paged back to account
                                 creation) for totals and streaks

Env: GH_LOGIN, STAT_OUT (stats.svg), STREAK_OUT (streak.svg)
Icons: assets/icons/octicons/*.svg, inlined into the SVGs.
"""

import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

LOGIN = os.environ.get("GH_LOGIN", "mulhamna")
STAT_OUT = os.environ.get("STAT_OUT", "stats.svg")
STREAK_OUT = os.environ.get("STREAK_OUT", "streak.svg")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
OCTI = os.path.join(REPO_ROOT, "assets", "icons", "octicons")

BG, FG, MUTED, TRACK = "#0d1117", "#c9d1d9", "#8b949e", "#21262d"
BLUE, GREEN, ORANGE = "#58a6ff", "#40c463", "#f0883e"
FONT = "Segoe UI, Ubuntu, Sans-serif"


def gql(query: str, **vars) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in vars.items():
        args += ["-f", f"{k}={v}"]
    for attempt in range(3):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            if data.get("errors"):
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]
        if attempt == 2:
            raise RuntimeError(f"gh api failed: {proc.stderr[:400]}")
    raise RuntimeError("unreachable")


def search_count(q: str) -> int:
    proc = subprocess.run(
        ["gh", "api", f"search/issues?q={q}", "--jq", ".total_count"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:200])
    return int(proc.stdout.strip())


PROFILE_Q = """
query($login:String!){
  user(login:$login){
    createdAt
    repositories(first:100, ownerAffiliations:OWNER, isFork:false){
      nodes{ stargazerCount }
    }
    repositoriesContributedTo(first:1){ totalCount }
    contributionsCollection{
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
    }
  }
}"""
CAL_Q = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from, to:$to){
      contributionCalendar{
        weeks{ contributionDays{ date contributionCount } }
      }
    }
  }
}"""


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT00:00:00Z")


def fetch_all_days(created: datetime) -> dict[str, int]:
    """Daily counts from account creation to now, in <=1y chunks."""
    days: dict[str, int] = {}
    start = created
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    while start < now:
        end = min(start + timedelta(days=360), now)
        data = gql(CAL_Q, login=LOGIN, **{"from": iso(start), "to": iso(end)})
        for w in data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                if d["date"] not in days or d["contributionCount"]:
                    days[d["date"]] = d["contributionCount"]
        start = end + timedelta(days=1)
    return days


def octicon(name: str, x: float, y: float, size: float, color: str) -> str:
    path_file = os.path.join(OCTI, f"{name}.svg")
    content = open(path_file, encoding="utf-8").read()
    m = re.search(r'<path[^>]*\bd="([^"]+)"', content)
    if not m:
        return ""
    s = size / 16.0
    return (f'<path d="{m.group(1)}" fill="{color}" '
            f'transform="translate({x:.1f},{y:.1f}) scale({s:.4f})"/>')


def fmt(n) -> str:
    return f"{int(n):,}"


def main() -> None:
    prof = gql(PROFILE_Q, login=LOGIN)["user"]
    created = datetime.strptime(prof["createdAt"][:10], "%Y-%m-%d")
    stars = sum(r["stargazerCount"]
                for r in prof["repositories"]["nodes"])
    contributed = prof["repositoriesContributedTo"]["totalCount"]
    coll = prof["contributionsCollection"]
    commits_1y = coll["totalCommitContributions"]
    prs_all = search_count(f"author:{LOGIN}+type:pr")
    issues_all = search_count(f"author:{LOGIN}+type:issue")

    print("fetching full history for streaks ...")
    days = fetch_all_days(created)
    ordered = sorted(days.items())
    first_day = ordered[0][0] if ordered else created.strftime("%Y-%m-%d")
    total_all = sum(c for _, c in ordered)

    cur, longest, run = 0, 0, 0
    for _, c in ordered:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    for _, c in reversed(ordered):
        if c > 0:
            cur += 1
        else:
            break

    streak_dates = ""
    for date_s, c in reversed(ordered):
        if c > 0:
            streak_dates = date_s
        elif streak_dates:
            break
    rng = (f"{datetime.strptime(first_day, '%Y-%m-%d'):%b %-d, %Y} - Present")

    print(f"stars={stars} contributed={contributed} commits1y={commits_1y} "
          f"prs={prs_all} issues={issues_all}")
    print(f"total={total_all} current={cur} longest={longest} since={first_day}")

    render_stats(stars, commits_1y, prs_all, issues_all, contributed)
    render_streak(total_all, cur, longest, streak_dates, rng)


def render_stats(stars, commits, prs, issues, contributed) -> None:
    W, H = 340, 200
    rows = [("star-16", "Total Stars", stars),
            ("git-commit-16", "Total Commits", commits),
            ("git-pull-request-16", "Total PRs", prs),
            ("issue-opened-16", "Total Issues", issues),
            ("repo-16", "Contributed to", contributed)]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub stats for {LOGIN}">',
        f'<title>{LOGIN} GitHub stats</title>',
        f'<rect width="{W}" height="{H}" fill="{BG}" rx="6"/>',
        f'<text x="20" y="30" fill="{FG}" font-size="17" font-weight="700" font-family="{FONT}">GitHub Stats</text>',
        f'<line x1="20" y1="42" x2="{W - 20}" y2="42" stroke="{TRACK}"/>',
        octicon("mark-github-16", 268, 118, 52, "#30363d"),
    ]
    y = 64
    for icon, label, val in rows:
        parts.append(octicon(icon, 22, y - 11, 15, MUTED))
        parts.append(f'<text x="44" y="{y}" fill="{FG}" font-size="12.5" '
                     f'font-family="{FONT}">{label}</text>')
        parts.append(f'<text x="222" y="{y}" text-anchor="end" fill="{BLUE}" '
                     f'font-size="13" font-weight="700" font-family="{FONT}">'
                     f'{fmt(val)}</text>')
        y += 26
    parts.append(f'<text x="20" y="{H - 10}" fill="{MUTED}" font-size="8.5" '
                 f'font-family="{FONT}">commits: last 12 months · PRs/issues: all time</text>')
    parts.append("</svg>")
    open(STAT_OUT, "w", encoding="utf-8").write("\n".join(parts))
    print(f"wrote {STAT_OUT}")


def render_streak(total_all, cur, longest, cur_since, rng) -> None:
    W, H = 495, 195
    cx, cy, r = 247, 96, 42
    dash = 2 * math.pi * r
    frac = min(cur / longest, 1.0) if longest else 0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub streak for {LOGIN}">',
        f'<title>{LOGIN} GitHub streak</title>',
        f'<rect width="{W}" height="{H}" fill="{BG}" rx="6"/>',
        # left column
        f'<text x="120" y="78" text-anchor="middle" fill="{BLUE}" font-size="30" font-weight="700" font-family="{FONT}">{fmt(total_all)}</text>',
        f'<text x="120" y="102" text-anchor="middle" fill="{FG}" font-size="13" font-family="{FONT}">Total Contributions</text>',
        f'<text x="120" y="120" text-anchor="middle" fill="{MUTED}" font-size="10" font-family="{FONT}">{rng}</text>',
        # dividers
        f'<line x1="200" y1="30" x2="200" y2="165" stroke="{TRACK}"/>',
        f'<line x1="295" y1="30" x2="295" y2="165" stroke="{TRACK}"/>',
        # middle ring
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{TRACK}" stroke-width="7"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{GREEN}" stroke-width="7" '
        f'stroke-linecap="round" stroke-dasharray="{dash * frac:.1f} {dash:.1f}" '
        f'transform="rotate(-90 {cx} {cy})"/>',
        octicon("flame-16", cx - 8, cy - r - 14, 16, GREEN),
        f'<text x="{cx}" y="{cy + 9}" text-anchor="middle" fill="{FG}" font-size="26" font-weight="700" font-family="{FONT}">{fmt(cur)}</text>',
        f'<text x="{cx}" y="{cy + 32}" text-anchor="middle" fill="{GREEN}" font-size="12" font-weight="600" font-family="{FONT}">Current Streak</text>',
        f'<text x="{cx}" y="{cy + 48}" text-anchor="middle" fill="{MUTED}" font-size="9.5" font-family="{FONT}">'
        f'{datetime.strptime(cur_since, "%Y-%m-%d"):%b %-d, %Y}</text>',
        # right column
        f'<text x="390" y="78" text-anchor="middle" fill="{BLUE}" font-size="30" font-weight="700" font-family="{FONT}">{fmt(longest)}</text>',
        f'<text x="390" y="102" text-anchor="middle" fill="{FG}" font-size="13" font-family="{FONT}">Longest Streak</text>',
        f'<text x="390" y="120" text-anchor="middle" fill="{MUTED}" font-size="10" font-family="{FONT}">{rng}</text>',
        "</svg>",
    ]
    open(STREAK_OUT, "w", encoding="utf-8").write("\n".join(parts))
    print(f"wrote {STREAK_OUT}")


if __name__ == "__main__":
    main()

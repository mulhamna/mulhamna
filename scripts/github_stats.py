#!/usr/bin/env python3
"""Self-hosted GitHub Stats + Streak cards as static SVGs.

Original design (not a clone of any third-party card):
  stats.svg  — icon-chip rows with dotted leaders and bold values
  streak.svg — hero current-streak number, longest/total column,
               and a 52-day activity sparkline along the bottom

Data via GitHub APIs only:
  - REST search/issues        -> all-time PR / issue counts
  - GraphQL user + repos      -> stars, contributed-to
  - contributionsCollection   -> commits (12mo) + daily history
                                 (paged back to account creation)

Env: GH_LOGIN, STAT_OUT (stats.svg), STREAK_OUT (streak.svg)
Icons: assets/icons/octicons/*.svg, inlined into the SVGs.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

LOGIN = os.environ.get("GH_LOGIN", "mulhamna")
STAT_OUT = os.environ.get("STAT_OUT", "stats.svg")
STREAK_OUT = os.environ.get("STREAK_OUT", "streak.svg")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
OCTI = os.path.join(REPO_ROOT, "assets", "icons", "octicons")

BG, FG, MUTED, TRACK = "#0d1117", "#c9d1d9", "#8b949e", "#21262d"
GREEN, BLUE, YELLOW, RED, PURPLE = ("#40c463", "#58a6ff", "#e3b341",
                                    "#f85149", "#bc8cff")
FONT = "Segoe UI, Ubuntu, Sans-serif"

SPARK_DAYS = 52


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


def header(W: float, title: str) -> list[str]:
    return [
        f'<circle cx="26" cy="30" r="3.5" fill="{GREEN}"/>',
        f'<text x="38" y="34.5" fill="{MUTED}" font-size="11.5" '
        f'letter-spacing="2.5" font-family="{FONT}">{title}</text>',
        f'<text x="{W - 24}" y="34.5" text-anchor="end" fill="{TRACK}" '
        f'font-size="10.5" letter-spacing="1" font-family="{FONT}">@{LOGIN}</text>',
    ]


def main() -> None:
    prof = gql(PROFILE_Q, login=LOGIN)["user"]
    created = datetime.strptime(prof["createdAt"][:10], "%Y-%m-%d")
    stars = sum(r["stargazerCount"]
                for r in prof["repositories"]["nodes"])
    contributed = prof["repositoriesContributedTo"]["totalCount"]
    commits_1y = prof["contributionsCollection"]["totalCommitContributions"]
    prs_all = search_count(f"author:{LOGIN}+type:pr")
    issues_all = search_count(f"author:{LOGIN}+type:issue")

    print("fetching full history for streaks ...")
    days = fetch_all_days(created)
    ordered = sorted(days.items())
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
    streak_since = next((d for d, c in reversed(ordered) if c == 0 and d < ordered[-1][0]),
                        ordered[0][0])
    # first day of the current streak:
    idx = len(ordered) - cur
    streak_first = ordered[idx][0] if cur else ordered[-1][0]

    print(f"stars={stars} contributed={contributed} commits1y={commits_1y} "
          f"prs={prs_all} issues={issues_all}")
    print(f"total={total_all} current={cur} longest={longest} "
          f"since={streak_first}")

    render_stats(stars, commits_1y, prs_all, issues_all, contributed)
    render_streak(total_all, cur, longest, streak_first,
                  ordered[-SPARK_DAYS:])


def render_stats(stars, commits, prs, issues, contributed) -> None:
    W, H = 430, 220
    rows = [("star-16", "Total Stars", stars, YELLOW),
            ("git-commit-16", "Total Commits", commits, GREEN),
            ("git-pull-request-16", "Total PRs", prs, BLUE),
            ("issue-opened-16", "Total Issues", issues, RED),
            ("repo-16", "Contributed to", contributed, PURPLE)]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub stats for {LOGIN}">',
        f'<title>{LOGIN} GitHub stats</title>',
        f'<rect width="{W}" height="{H}" fill="{BG}" rx="10"/>',
    ]
    parts += header(W, "GITHUB STATS")
    y0, row_h = 66, 29
    for i, (icon, label, val, chip) in enumerate(rows):
        y = y0 + i * row_h
        parts.append(f'<rect x="24" y="{y - 14.5}" width="20" height="20" '
                     f'rx="6" fill="{chip}"/>')
        parts.append(octicon(icon, 28, y - 10.5, 12, BG))
        parts.append(f'<text x="58" y="{y}" fill="{FG}" font-size="13" '
                     f'font-family="{FONT}">{label}</text>')
        lw = len(label) * 7.3
        parts.append(f'<line x1="{58 + lw + 8:.0f}" y1="{y - 4}" x2="330" '
                     f'y2="{y - 4}" stroke="{TRACK}" stroke-width="1" '
                     f'stroke-dasharray="2 4"/>')
        parts.append(f'<text x="{W - 24}" y="{y}" text-anchor="end" fill="{FG}" '
                     f'font-size="15.5" font-weight="700" '
                     f'font-family="{FONT}">{fmt(val)}</text>')
    parts.append(f'<text x="24" y="{H - 12}" fill="{MUTED}" font-size="8.5" '
                 f'font-family="{FONT}">commits: last 12 months · '
                 f'PRs/issues: all time · stars: own repos</text>')
    parts.append("</svg>")
    open(STAT_OUT, "w", encoding="utf-8").write("\n".join(parts))
    print(f"wrote {STAT_OUT}")


def render_streak(total_all, cur, longest, streak_first, recent) -> None:
    W, H = 430, 220
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="GitHub streak for {LOGIN}">',
        f'<title>{LOGIN} GitHub streak</title>',
        f'<rect width="{W}" height="{H}" fill="{BG}" rx="10"/>',
    ]
    parts += header(W, "STREAKS")
    # hero (left): flame + current streak
    parts.append(octicon("flame-16", 28, 52, 30, GREEN))
    parts.append(f'<text x="28" y="130" fill="{FG}" font-size="46" '
                 f'font-weight="700" font-family="{FONT}">{fmt(cur)}</text>')
    parts.append(f'<text x="28" y="152" fill="{GREEN}" font-size="11" '
                 f'letter-spacing="2" font-family="{FONT}">CURRENT STREAK</text>')
    parts.append(f'<text x="28" y="168" fill="{MUTED}" font-size="10" '
                 f'font-family="{FONT}">'
                 f'{datetime.strptime(streak_first, "%Y-%m-%d"):%b %-d, %Y}'
                 f' - Present</text>')
    # divider
    parts.append(f'<line x1="245" y1="54" x2="245" y2="176" stroke="{TRACK}"/>')
    # right column: longest + total
    parts.append(f'<text x="262" y="78" fill="{MUTED}" font-size="10.5" '
                 f'letter-spacing="1.5" font-family="{FONT}">LONGEST STREAK</text>')
    parts.append(f'<text x="262" y="106" fill="{FG}" font-size="25" '
                 f'font-weight="700" font-family="{FONT}">{fmt(longest)}</text>')
    parts.append(f'<text x="262" y="142" fill="{MUTED}" font-size="10.5" '
                 f'letter-spacing="1.5" font-family="{FONT}">TOTAL CONTRIBUTIONS</text>')
    parts.append(f'<text x="262" y="170" fill="{FG}" font-size="25" '
                 f'font-weight="700" font-family="{FONT}">{fmt(total_all)}</text>')
    # sparkline of the last N days
    if recent:
        maxv = max(c for _, c in recent) or 1
        x0, base, bw, gap = 28, 205, 6, 1
        for j, (date_s, c) in enumerate(recent):
            h = max(2.0, 26.0 * c / maxv)
            x = x0 + j * (bw + gap)
            label = (f'{datetime.strptime(date_s, "%Y-%m-%d"):%b %-d}: '
                     f'{c} contributions')
            parts.append(f'<rect x="{x:.1f}" y="{base - h:.1f}" width="{bw}" '
                         f'height="{h:.1f}" rx="1.5" fill="{GREEN}" '
                         f'fill-opacity="0.85"><title>{label}</title></rect>')
    parts.append("</svg>")
    open(STREAK_OUT, "w", encoding="utf-8").write("\n".join(parts))
    print(f"wrote {STREAK_OUT}")


if __name__ == "__main__":
    main()

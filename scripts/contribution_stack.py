#!/usr/bin/env python3
"""Multi-stack contribution activity chart as a static SVG.

One stacked layer per language ("stack"): shows WHICH weeks were coded
with WHICH stack. Legend below: language icon + name + percentage share
of commits in the window.

Data pipeline (all GitHub APIs, no third-party services):
  1. contributionCalendar -> week boundaries (last 26 weeks)
  2. commitContributionsByRepository -> repos + their language byte shares
  3. per repo: commit history filtered by author since window start,
     bucketed into the same calendar weeks
  4. weekly language values = sum(repo weekly commits * repo lang share)

Env: GH_LOGIN (mulhamna), OUT (activity-graph.svg), WEEKS (26),
     MAX_REPOS (60), MIN_PCT (1.0)
Icons: assets/icons/<slug>.svg (simple-icons, embedded inline).
"""

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

LOGIN = os.environ.get("GH_LOGIN", "mulhamna")
OUT = os.environ.get("OUT", "activity-graph.svg")
WEEKS = int(os.environ.get("WEEKS", "26"))
MAX_REPOS = int(os.environ.get("MAX_REPOS", "60"))
MIN_PCT = float(os.environ.get("MIN_PCT", "1.0"))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
ICON_DIR = os.path.join(REPO_ROOT, "assets", "icons")

BG, FG, MUTED, TRACK = "#0d1117", "#c9d1d9", "#8b949e", "#21262d"
FONT = "Segoe UI, Ubuntu, Sans-serif"

ICON_SLUGS = {
    "Python": "python", "TypeScript": "typescript", "JavaScript": "javascript",
    "Rust": "rust", "Go": "go", "Svelte": "svelte", "HTML": "html5",
    "CSS": "css3", "SCSS": "css3", "Zig": "zig", "Shell": "gnubash",
    "Dockerfile": "docker", "Nix": "nixos", "Lua": "lua", "Vue": "vuedotjs",
    "C": "c", "C++": "cplusplus", "Java": "openjdk", "Swift": "swift",
    "Kotlin": "kotlin", "Ruby": "ruby", "PHP": "php", "Dart": "dart",
    "HCL": "terraform", "Markdown": "markdown", "Jupyter Notebook": "jupyter",
    "Astro": "astro", "C#": "csharp", "Slint": "slint",
}
FALLBACK_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051", "HTML": "#e34c26",
    "CSS": "#663399", "Svelte": "#ff3e00", "Slint": "#ff8b00", "Other": MUTED,
}


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


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


USER_Q = "query($login:String!){ user(login:$login){ id login } }"
CAL_Q = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        weeks{ contributionDays{ date contributionCount } }
      }
    }
  }
}"""
REPOS_Q = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      commitContributionsByRepository(maxRepositories: 100){
        repository{
          nameWithOwner
          isPrivate
          defaultBranchRef{ name }
          languages(first:100, orderBy:{field:SIZE, direction:DESC}){
            totalCount
            edges{ size node{ name color } }
          }
        }
      }
    }
  }
}"""
HIST_Q = """
query($owner:String!,$repo:String!,$userId:ID!,$since:GitTimestamp!,$after:String){
  repository(owner:$owner, name:$repo){
    defaultBranchRef{
      target{
        ... on Commit{
          history(first:100, author:{id:$userId}, since:$since, after:$after){
            pageInfo{ hasNextPage endCursor }
            edges{ node{ committedDate } }
          }
        }
      }
    }
  }
}"""


def fetch_weeks() -> tuple[list[datetime], int]:
    data = gql(CAL_Q, login=LOGIN)
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    boundaries = [datetime.strptime(w["contributionDays"][0]["date"], "%Y-%m-%d")
                  for w in weeks]
    total_days = sum(d["contributionCount"]
                     for w in weeks for d in w["contributionDays"])
    return boundaries[-WEEKS:], total_days


def fetch_repos() -> list[dict]:
    data = gql(REPOS_Q, login=LOGIN)
    entries = data["user"]["contributionsCollection"]["commitContributionsByRepository"]
    repos = []
    for e in entries:
        r = e["repository"]
        if not r["defaultBranchRef"]:
            continue
        langs, total_bytes = {}, 0
        for edge in r["languages"]["edges"]:
            if edge["size"] > 0 and edge["node"]["name"]:
                langs[edge["node"]["name"]] = (
                    edge["size"], edge["node"].get("color") or "")
                total_bytes += edge["size"]
        if total_bytes > 0:
            repos.append({
                "name": r["nameWithOwner"],
                "langs": {n: s / total_bytes for n, (s, _) in langs.items()},
                "colors": {n: c for n, (_, c) in langs.items() if c},
            })
    repos.sort(key=lambda r: -sum(r["langs"].values()))
    return repos[:MAX_REPOS]


def fetch_commit_weeks(repo: dict, user_id: str, since: datetime,
                       week_starts: list[datetime]) -> list[int]:
    """Commits by user on repo default branch since `since`, bucketed per week."""
    owner, name = repo["name"].split("/", 1)
    buckets = [0] * len(week_starts)
    after = None
    while True:
        kw = dict(owner=owner, repo=name, userId=user_id, since=iso(since))
        if after:
            kw["after"] = after
        node = gql(HIST_Q, **kw)["repository"]["defaultBranchRef"]["target"]
        hist = node["history"]
        for edge in hist["edges"]:
            dt = datetime.strptime(edge["node"]["committedDate"][:10], "%Y-%m-%d")
            idx = int((dt - week_starts[0]).days // 7)
            if 0 <= idx < len(buckets):
                buckets[idx] += 1
        if hist["pageInfo"]["hasNextPage"]:
            after = hist["pageInfo"]["endCursor"]
        else:
            break
    return buckets


def stack_data(repos: list[dict], week_starts: list[datetime]) -> tuple[dict, dict]:
    since = week_starts[0]
    weekly_lang: dict[str, list[float]] = defaultdict(
        lambda: [0.0] * len(week_starts))
    colors: dict[str, str] = {}
    for repo in repos:
        try:
            weekly = fetch_commit_weeks(repo, USER_ID, since, week_starts)
        except RuntimeError as exc:
            print(f"  ! skip {repo['name']}: {exc}", file=sys.stderr)
            continue
        for lang, share in repo["langs"].items():
            target = weekly_lang[lang]
            for i, c in enumerate(weekly):
                if c:
                    target[i] += c * share
        colors.update(repo["colors"])
    return dict(weekly_lang), colors


USER_ID = ""


def main() -> None:
    global USER_ID
    USER_ID = gql(USER_Q, login=LOGIN)["user"]["id"]
    week_starts, cal_total = fetch_weeks()
    print(f"window: {week_starts[0]:%Y-%m-%d} .. "
          f"{week_starts[-1] + timedelta(days=6):%Y-%m-%d}")
    repos = fetch_repos()
    print(f"repos: {len(repos)}")

    weekly, colors = stack_data(repos, week_starts)
    weekly = {k: v for k, v in weekly.items() if sum(v) > 0}
    grand = sum(sum(v) for v in weekly.values())
    if grand == 0:
        sys.exit("no commit data in window")

    layers = sorted(weekly.items(), key=lambda kv: -sum(kv[1]))
    main_layers, other = [], [0.0] * len(week_starts)
    for name, vals in layers:
        if sum(vals) / grand >= MIN_PCT / 100.0 or name == layers[0][0]:
            main_layers.append([name, vals])
        else:
            for i, v in enumerate(vals):
                other[i] += v
    if sum(other) > 0:
        main_layers.append(["Other", other])

    render(main_layers, colors, week_starts, grand, cal_total)
    shares = ", ".join(f"{n} {100 * sum(v) / grand:.1f}%"
                       for n, v in main_layers)
    print(f"wrote {OUT}: {grand:.0f} commits | {shares}")


def color_of(name: str, colors: dict) -> str:
    return colors.get(name) or FALLBACK_COLORS.get(name, MUTED)


def icon_embed(name: str, x: float, y: float, size: float, color: str) -> str:
    slug = ICON_SLUGS.get(name)
    if not slug:
        return (f'<circle cx="{x + size / 2}" cy="{y + size / 2}" r="{size / 2}" '
                f'fill="{color}"/>')
    path_file = os.path.join(ICON_DIR, f"{slug}.svg")
    if not os.path.exists(path_file):
        return (f'<circle cx="{x + size / 2}" cy="{y + size / 2}" r="{size / 2}" '
                f'fill="{color}"/>')
    content = open(path_file, encoding="utf-8").read()
    m = re.search(r'<path[^>]*\bd="([^"]+)"', content)
    if not m:
        return (f'<circle cx="{x + size / 2}" cy="{y + size / 2}" r="{size / 2}" '
                f'fill="{color}"/>')
    s = size / 24.0
    return (f'<path d="{m.group(1)}" fill="{color}" '
            f'transform="translate({x:.1f},{y:.1f}) scale({s:.4f})"/>')


def cr_path(pts) -> str:
    d = f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += (f" C {c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f}"
              f" {p2[0]:.1f},{p2[1]:.1f}")
    return d


def render(layers, colors, week_starts, total, cal_total) -> None:
    import math

    n_weeks = len(week_starts)
    W = 900
    legend_cols, row_h = 2, 30
    legend_rows = math.ceil(len(layers) / legend_cols)
    legend_h = 46 + legend_rows * row_h
    H = 330 + legend_h
    LEFT, RIGHT, TOP, BOTTOM = 62, 30, 56, 40
    PW, PH = W - LEFT - RIGHT, 330 - TOP - BOTTOM
    base_y = TOP + PH

    totals = [sum(vals[i] for _, vals in layers) for i in range(n_weeks)]
    y_max = max(totals) or 1
    step = 1
    for mult in (1, 2, 5):
        for k in range(1, 7):
            s = mult * 10 ** k
            if 3 <= y_max / s <= 6:
                step = s
                break
    y_top = math.ceil(y_max / step) * step

    def px(i):
        return LEFT + PW * i / (n_weeks - 1)

    def py(v):
        return base_y - PH * (min(v, y_top) / y_top)

    grid, ylabels = [], []
    n_ticks = y_top // step
    for t in range(n_ticks + 1):
        y = base_y - PH * t / n_ticks
        grid.append(f'<line x1="{LEFT}" y1="{y:.1f}" x2="{W - RIGHT}" y2="{y:.1f}" '
                    f'stroke="{TRACK}" stroke-width="1"/>')
        ylabels.append(f'<text x="{LEFT - 10}" y="{y + 4:.1f}" text-anchor="end" '
                       f'fill="{MUTED}" font-size="12">{int(y_top * t / n_ticks)}</text>')

    xlabels, last_month = [], None
    for i, d in enumerate(week_starts):
        if d.month != last_month:
            last_month = d.month
            xlabels.append(f'<text x="{px(i):.1f}" y="{base_y + 22}" text-anchor="middle" '
                           f'fill="{MUTED}" font-size="12">{d.strftime("%b")}</text>')

    areas = []
    cum = [0.0] * n_weeks
    for name, vals in layers:
        lower = cum[:]
        cum = [cum[i] + vals[i] for i in range(n_weeks)]
        upper_pts = [(px(i), py(cum[i])) for i in range(n_weeks)]
        lower_pts = [(px(i), py(lower[i])) for i in range(n_weeks - 1, -1, -1)]
        d = cr_path(upper_pts) + " L " + " L ".join(
            f"{x:.1f},{y:.1f}" for x, y in lower_pts[1:]) + " Z"
        c = color_of(name, colors)
        op = "0.55" if name == "Other" else "0.82"
        areas.append(
            f'<path d="{d}" fill="{c}" fill-opacity="{op}" '
            f'stroke="{c}" stroke-width="1.2" stroke-opacity="0.9"/>')

    legend = []
    for idx, (name, vals) in enumerate(layers):
        col, row = idx % legend_cols, idx // legend_cols
        x = 40 + col * 430
        y = 330 + 34 + row * row_h
        pct = 100 * sum(vals) / total
        c = color_of(name, colors)
        legend.append(icon_embed(name, x, y - 15, 18, c))
        legend.append(f'<text x="{x + 26}" y="{y}" fill="{FG}" font-size="13" '
                      f'font-family="{FONT}">{name}</text>')
        legend.append(f'<text x="{x + 380}" y="{y}" text-anchor="end" fill="{FG}" '
                      f'font-size="13" font-weight="600" font-family="{FONT}">{pct:.1f}%</text>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Contribution activity by stack for {LOGIN}">
  <title>{total:.0f} commits in the last half year, by stack</title>
  <rect width="{W}" height="{H}" fill="{BG}" rx="6"/>
  {"".join(grid)}
  {"".join(ylabels)}
  {"".join(xlabels)}
  <text x="{LEFT}" y="28" fill="{FG}" font-size="16" font-weight="600" font-family="{FONT}">{total:.0f} commits in the last half year</text>
  <text x="{W - RIGHT}" y="28" text-anchor="end" fill="{MUTED}" font-size="12" font-family="{FONT}">by stack · since {week_starts[0]:%b %Y}</text>
  {"".join(areas)}
  {"".join(legend)}
</svg>
'''
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)


if __name__ == "__main__":
    main()

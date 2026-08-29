#!/usr/bin/env python3
"""Top languages by commit contributions (past year) as a static SVG card.

Same self-hosted pattern as activity_graph.py: GitHub Actions renders the
SVG daily and commits it to the profile repo.

Data: GraphQL contributionsCollection.commitContributionsByRepository ->
per-repo language byte sizes, summed across repos (the same approximation
github-profile-summary-cards uses). The profile README repo is excluded.

Styles (env STYLE): donut (default) | bars
Env: GH_LOGIN, OUT, STYLE, TOP_N (default 6)
"""

import json
import math
import os
import subprocess
import sys
from collections import defaultdict

LOGIN = os.environ.get("GH_LOGIN", "mulhamna")
OUT = os.environ.get("OUT", "top-languages.svg")
STYLE = os.environ.get("STYLE", "donut")
TOP_N = int(os.environ.get("TOP_N", "6"))

BG = "#0d1117"
FG = "#c9d1d9"
MUTED = "#8b949e"
TRACK = "#21262d"
FONT = "Segoe UI, Ubuntu, Sans-serif"

QUERY = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      totalCommitContributions
      commitContributionsByRepository(maxRepositories: 100){
        repository{
          nameWithOwner
          languages(first:100, orderBy:{field:SIZE, direction:DESC}){
            edges{ size node{ name color } }
          }
        }
      }
    }
  }
}"""

FALLBACK_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a",
    "Go": "#00ADD8", "Rust": "#dea584", "Shell": "#89e051", "HTML": "#e34c26",
    "CSS": "#563d7c", "Swift": "#F05138", "Kotlin": "#A97BFF", "Java": "#b07219",
    "C++": "#f34b7d", "C": "#555555", "Zig": "#ec915c", "Vue": "#41b883",
    "Svelte": "#ff3e00", "Dart": "#00B4AB", "PHP": "#4F5D95", "Ruby": "#701516",
    "Lua": "#000080", "Markdown": "#083fa1", "Dockerfile": "#384d54",
    "Nix": "#7e7eff", "HCL": "#844FBA", "SCSS": "#c6538c", "Other": MUTED,
}


def color_of(name: str, colors: dict) -> str:
    return colors.get(name) or FALLBACK_COLORS.get(name, MUTED)


def fetch() -> tuple[dict, dict, int]:
    raw = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"login={LOGIN}"],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(raw)
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    cc = data["data"]["user"]["contributionsCollection"]
    totals: dict[str, int] = defaultdict(int)
    colors: dict[str, str] = {}
    profile_repo = f"{LOGIN}/{LOGIN}".lower()
    for entry in cc["commitContributionsByRepository"]:
        repo = entry["repository"]
        if repo["nameWithOwner"].lower() == profile_repo:
            continue
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if not name or edge["size"] <= 0:
                continue
            totals[name] += edge["size"]
            if edge["node"].get("color"):
                colors[name] = edge["node"]["color"]
    return dict(totals), colors, int(cc["totalCommitContributions"])


def top_slices(totals: dict) -> list[tuple[str, float]]:
    items = sorted(totals.items(), key=lambda kv: -kv[1])
    grand = sum(v for _, v in items)
    if grand == 0:
        return []
    slices = [(n, 100.0 * v / grand) for n, v in items[:TOP_N]]
    rest = items[TOP_N:]
    if rest:
        rv = sum(v for _, v in rest)
        slices.append(("Other", 100.0 * rv / grand))
    return slices


def donut_svg(slices: list[tuple[str, float]], commits: int,
              colors: dict) -> str:
    W, H = 660, 330
    CX, CY, R, THICK = 168, 196, 96, 36
    C = 2 * math.pi * R

    ring, offset = [], 0.0
    for name, pct in slices:
        length = C * pct / 100.0
        if length <= 0:
            continue
        ring.append(
            f'<circle cx="{CX}" cy="{CY}" r="{R}" fill="none" stroke="{color_of(name, colors)}" '
            f'stroke-width="{THICK}" stroke-dasharray="{length - 1.0:.2f} {C - length + 1.0:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {CX} {CY})"/>'
        )
        offset += length

    legend, y = [], 108
    for name, pct in slices:
        legend.append(
            f'<circle cx="330" cy="{y - 4}" r="5" fill="{color_of(name, colors)}"/>'
            f'<text x="344" y="{y}" fill="{FG}" font-size="13" font-family="{FONT}">{name}</text>'
            f'<text x="632" y="{y}" text-anchor="end" fill="{FG}" font-size="13" '
            f'font-weight="600" font-family="{FONT}">{pct:.1f}%</text>'
        )
        y += 27

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Top languages by commit for {LOGIN}">
  <title>Top languages by commit contributions</title>
  <rect width="{W}" height="{H}" fill="{BG}" rx="6"/>
  <text x="30" y="34" fill="{FG}" font-size="16" font-weight="600" font-family="{FONT}">Top Languages by Commit</text>
  <text x="632" y="34" text-anchor="end" fill="{MUTED}" font-size="12" font-family="{FONT}">past year</text>
  {"".join(ring)}
  <text x="{CX}" y="{CY - 2}" text-anchor="middle" fill="{FG}" font-size="22" font-weight="700" font-family="{FONT}">{commits:,}</text>
  <text x="{CX}" y="{CY + 20}" text-anchor="middle" fill="{MUTED}" font-size="11" font-family="{FONT}">commits · 1y</text>
  {"".join(legend)}
</svg>
'''


def bars_svg(slices: list[tuple[str, float]], colors: dict) -> str:
    W, H = 660, 330
    X0, X1 = 150, 552

    rows, y = [], 108
    for name, pct in slices:
        w = max(2.0, (X1 - X0) * pct / 100.0)
        rows.append(
            f'<text x="30" y="{y}" fill="{FG}" font-size="13" font-family="{FONT}">{name}</text>'
            f'<rect x="{X0}" y="{y - 9}" width="{X1 - X0}" height="12" rx="6" fill="{TRACK}"/>'
            f'<rect x="{X0}" y="{y - 9}" width="{w:.1f}" height="12" rx="6" fill="{color_of(name, colors)}"/>'
            f'<text x="632" y="{y}" text-anchor="end" fill="{FG}" font-size="13" '
            f'font-weight="600" font-family="{FONT}">{pct:.1f}%</text>'
        )
        y += 30

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Top languages by commit for {LOGIN}">
  <title>Top languages by commit contributions</title>
  <rect width="{W}" height="{H}" fill="{BG}" rx="6"/>
  <text x="30" y="34" fill="{FG}" font-size="16" font-weight="600" font-family="{FONT}">Top Languages by Commit</text>
  <text x="632" y="34" text-anchor="end" fill="{MUTED}" font-size="12" font-family="{FONT}">past year</text>
  {"".join(rows)}
</svg>
'''


def main() -> None:
    totals, colors, commits = fetch()
    slices = top_slices(totals)
    if not slices:
        sys.exit("no language data returned")

    svg = (bars_svg(slices, colors) if STYLE == "bars"
           else donut_svg(slices, commits, colors))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(svg)
    pretty = ", ".join(f"{n} {p:.1f}%" for n, p in slices)
    print(f"wrote {OUT} [{STYLE}]: commits(1y)={commits:,} | {pretty}")


if __name__ == "__main__":
    main()

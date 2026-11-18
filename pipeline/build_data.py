"""Turf Grade data pipeline.

Downloads this season's weekly player stats from nflverse (licensed CC BY 4.0),
scores every QB, RB, WR, TE, kicker and team defense in PPR, half-PPR and
standard (ESPN default rules), and writes site/data/grades.json for the website.

Uses only the Python standard library so it runs anywhere, including GitHub Actions.
Only the stats file is used: no schedules, images or betting data.
"""
import csv, io, json, os, statistics, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

SOURCE = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_{season}.csv"
OUT = os.path.join(os.path.dirname(__file__), "..", "site", "data", "grades.json")
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
FORMATS = ["ppr", "half", "std"]
MIN_GAMES = 4                      # players need this many games to be graded
WEIGHTS = {"season": 40, "usage": 30, "recent": 20}   # matchup (10) joins once a licensed schedule exists
RECENT_GAMES = 3


def current_season(now):
    return now.year if now.month >= 3 else now.year - 1


def num(row, col):
    v = row.get(col)
    return float(v) if v not in (None, "", "NA") else 0.0


def load_rows(season, path=None):
    if path:
        text = open(path, encoding="utf-8").read()
    else:
        with urllib.request.urlopen(SOURCE.format(season=season), timeout=120) as r:
            text = r.read().decode("utf-8")
    return [r for r in csv.DictReader(io.StringIO(text)) if r.get("season_type") == "REG"]


# ---------- ESPN default scoring ----------

def kicker_points(r):
    made = (3 * (num(r, "fg_made_0_19") + num(r, "fg_made_20_29") + num(r, "fg_made_30_39"))
            + 4 * num(r, "fg_made_40_49") + 5 * num(r, "fg_made_50_59") + 6 * num(r, "fg_made_60_"))
    return made + num(r, "pat_made") - num(r, "fg_missed")


def points_allowed_tier(pa):
    for limit, pts in ((0, 5), (6, 4), (13, 3), (17, 1), (27, 0), (34, -1), (45, -3)):
        if pa <= limit:
            return pts
    return -5


def team_games(rows):
    """Per team and week: points scored, defensive TDs, and D/ST box stats."""
    g = defaultdict(lambda: defaultdict(float))
    for r in rows:
        k = (r["team"], int(r["week"]))
        t = g[k]
        t["opponent"] = r["opponent_team"]
        t["scored"] += (6 * (num(r, "rushing_tds") + num(r, "receiving_tds") + num(r, "def_tds")
                             + num(r, "special_teams_tds") + num(r, "fumble_recovery_tds"))
                        + 3 * num(r, "fg_made") + num(r, "pat_made")
                        + 2 * (num(r, "rushing_2pt_conversions") + num(r, "receiving_2pt_conversions"))
                        + 2 * num(r, "def_safeties"))
        t["def_tds"] += num(r, "def_tds")
        t["st_tds"] += num(r, "special_teams_tds")
        t["sacks"] += num(r, "def_sacks")
        t["ints"] += num(r, "def_interceptions")
        t["fum_rec"] += num(r, "fumble_recovery_opp")
        t["blocks"] += num(r, "def_punt_blocks") + num(r, "def_fg_blocks") + num(r, "def_pat_blocks")
        t["safeties"] += num(r, "def_safeties")
        t["opps"] += num(r, "carries") + num(r, "targets")
        t["qb_plays"] += num(r, "attempts") + num(r, "carries")
    return g


def dst_points(t, opp):
    # ESPN: points the opponent scores on interception or fumble returns don't count against the defense.
    allowed = opp["scored"] - 6 * opp["def_tds"]
    return (t["sacks"] + 2 * (t["ints"] + t["fum_rec"] + t["blocks"] + t["safeties"])
            + 6 * (t["def_tds"] + t["st_tds"]) + points_allowed_tier(allowed))


# ---------- grading ----------

def percentile(v, values):
    return round(100 * sum(x <= v for x in values) / len(values), 1)


def summarise(games, max_week):
    """games: list of {week, ppr, half, std, usage_num, usage_den or usage_value}"""
    games.sort(key=lambda g: g["week"])
    out = {"games": len(games)}
    for f in FORMATS:
        pts = [g[f] for g in games]
        weeks = [None] * max_week
        for g in games:
            weeks[g["week"] - 1] = round(g[f], 1)
        out[f] = {"season": round(statistics.mean(pts), 2),
                  "recent": round(statistics.mean(pts[-RECENT_GAMES:]), 2),
                  "weeks": weeks}
    return out


def grade_pool(players):
    graded = [p for p in players if p["games"] >= MIN_GAMES]
    usage = [p["usage"] for p in graded]
    for f in FORMATS:
        season = [p[f]["season"] for p in graded]
        recent = [p[f]["recent"] for p in graded]
        for p in graded:
            d = p[f]
            d["pct"] = {"season": percentile(d["season"], season),
                        "usage": percentile(p["usage"], usage),
                        "recent": percentile(d["recent"], recent)}
            d["grade"] = round(sum(WEIGHTS[k] * d["pct"][k] for k in WEIGHTS) / sum(WEIGHTS.values()), 1)
    return graded


def build(rows, season):
    max_week = max(int(r["week"]) for r in rows)
    tg = team_games(rows)
    players = {pos: defaultdict(lambda: {"games": []}) for pos in POSITIONS}

    for r in rows:
        pos = r["position"]
        if pos not in ("QB", "RB", "WR", "TE", "K"):
            continue
        t = tg[(r["team"], int(r["week"]))]
        if pos == "K":
            pts = kicker_points(r)
            g = {"ppr": pts, "half": pts, "std": pts, "u": num(r, "fg_att")}
        else:
            std, ppr = num(r, "fantasy_points"), num(r, "fantasy_points_ppr")
            mine = num(r, "attempts") + num(r, "carries") if pos == "QB" else num(r, "carries") + num(r, "targets")
            team_total = t["qb_plays"] if pos == "QB" else t["opps"]
            g = {"ppr": ppr, "half": (std + ppr) / 2, "std": std, "u": mine, "u_den": team_total}
        g["week"] = int(r["week"])
        p = players[pos][r["player_id"]]
        p.update(id=r["player_id"], name=r["player_display_name"], team=r["team"], pos=pos)
        p["games"].append(g)

    for (team, week), t in tg.items():
        opp = tg.get((t["opponent"], week))
        if not opp:
            continue
        pts = dst_points(t, opp)
        p = players["DST"][team]
        p.update(id="DST-" + team, name=team + " D/ST", team=team, pos="DST")
        p["games"].append({"week": week, "ppr": pts, "half": pts, "std": pts, "u": t["sacks"] + t["ints"] + t["fum_rec"]})

    result = {}
    for pos in POSITIONS:
        out = []
        for p in players[pos].values():
            games = p["games"]
            s = summarise(games, max_week)
            if pos in ("K", "DST"):
                usage = sum(g["u"] for g in games) / len(games)          # per game
            else:
                den = sum(g["u_den"] for g in games)
                usage = 100 * sum(g["u"] for g in games) / den if den else 0.0   # share of team plays
            out.append({"id": p["id"], "name": p["name"], "team": p["team"], "usage": round(usage, 2), **s})
        graded = grade_pool(out)
        graded.sort(key=lambda p: -p["ppr"]["grade"])
        result[pos] = graded

    return {
        "season": season,
        "through_week": max_week,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "min_games": MIN_GAMES,
        "weights": WEIGHTS,
        "usage_meaning": {"QB": "share of team pass attempts and carries", "RB": "share of team carries and targets",
                          "WR": "share of team carries and targets", "TE": "share of team carries and targets",
                          "K": "field-goal attempts per game", "DST": "sacks plus takeaways per game"},
        "source": "Player stats from nflverse (github.com/nflverse), licensed CC BY 4.0. Fantasy points for kickers and defenses calculated by Turf Grade with ESPN default scoring.",
        "players": result,
    }


def main():
    season = int(os.environ.get("SEASON") or current_season(datetime.now(timezone.utc)))
    local = sys.argv[1] if len(sys.argv) > 1 else None
    data = build(load_rows(season, local), season)
    new = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    try:
        old = json.load(open(OUT, encoding="utf-8"))
        old["updated"] = data["updated"]
        if json.dumps(old, separators=(",", ":"), ensure_ascii=False) == new:
            print("No change in the stats since the last refresh.")
            return
    except (FileNotFoundError, ValueError):
        pass
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(new)
    counts = ", ".join(f"{pos} {len(v)}" for pos, v in data["players"].items())
    print(f"Wrote grades for the {season} season through week {data['through_week']}: {counts}")


if __name__ == "__main__":
    main()

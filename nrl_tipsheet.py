"""
NRL Betting Tip Sheet Generator
Fetches live odds + player/team stats, calculates edge, generates HTML tip sheet.
Run: python nrl_tipsheet.py
Output: tipsheet_output.html (open in browser or access via GitHub Pages on phone)
"""

import os
import sys
import math
import json
import requests
from datetime import datetime, timezone
from jinja2 import Template
from bs4 import BeautifulSoup
from nrl_tracker import load_db, save_db, save_round_predictions, get_current_bankroll, auto_resolve_from_scores

# ---------------------------------------------------------------------------
# Config — use environment variables (GitHub Actions) or fall back to config.py
# ---------------------------------------------------------------------------
try:
    ODDS_API_KEY = os.environ.get("ODDS_API_KEY") or __import__("config").ODDS_API_KEY
except Exception:
    sys.exit("ERROR: ODDS_API_KEY not found. Add it to config.py or set the ODDS_API_KEY env var.")

ODDS_BASE = "https://api.the-odds-api.com/v4"

# NRL.com internal APIs (power the official NRL website — no key needed)
NRL_COMPETITION_ID = "111"  # NRL Telstra Premiership

OUTPUT_FILE = "tipsheet_output.html"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Positional try-scoring factors for First Try Scorer model
# Higher = more likely to score first. Based on typical NRL positional scoring patterns.
POSITION_FTS_FACTOR = {
    "Fullback": 0.32,
    "Wing": 0.28,
    "Centre": 0.22,
    "Five-Eighth": 0.16,
    "Halfback": 0.14,
    "Lock": 0.12,
    "Hooker": 0.12,
    "Second Row": 0.10,
    "Prop": 0.08,
    "default": 0.14,
}

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def odds_get(path, params=None):
    params = params or {}
    params["apiKey"] = ODDS_API_KEY
    r = requests.get(f"{ODDS_BASE}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def nrl_get(url, params=None):
    """Fetch from NRL.com APIs."""
    r = requests.get(url, params=params or {}, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Round number detection
# ---------------------------------------------------------------------------

def fetch_upcoming_round_number(db=None):
    """
    Detect the upcoming NRL round number by querying the draw API.
    Returns the first round where no fixtures are completed yet.
    Falls back to last recorded round_id + 1 if the API fails.
    """
    season = datetime.now().year
    try:
        draw = nrl_get(
            "https://www.nrl.com/draw/data",
            {"competition": NRL_COMPETITION_ID, "season": season},
        )
        all_rounds = sorted([r["value"] for r in draw.get("filterRounds", [])], key=int)
        for rnd in all_rounds:
            try:
                r_data = nrl_get(
                    "https://www.nrl.com/draw/data",
                    {"competition": NRL_COMPETITION_ID, "season": season, "round": rnd},
                )
                fixtures = r_data.get("fixtures", [])
                completed = [f for f in fixtures if f.get("matchState") in ("FullTime", "Post", "Final")]
                if not completed:
                    return int(rnd)
            except Exception:
                continue
        return int(all_rounds[-1]) + 1 if all_rounds else 1
    except Exception as e:
        print(f"  WARNING: Could not detect round number from draw API: {e}")
        # Fallback: increment from last known round in DB
        if db:
            existing = db.get("rounds", [])
            if existing:
                last_id = sorted(r["round_id"] for r in existing)[-1]
                try:
                    return int(last_id.split("-R")[-1]) + 1
                except Exception:
                    pass
        return 1


# ---------------------------------------------------------------------------
# Step 1: Fetch upcoming NRL fixtures + odds from The Odds API
# ---------------------------------------------------------------------------

def fetch_fixtures_with_odds():
    """Returns list of upcoming NRL games with h2h and spread odds."""
    try:
        data = odds_get(
            "/sports/rugbyleague_nrl/odds",
            {"regions": "au", "markets": "h2h,spreads", "oddsFormat": "decimal", "dateFormat": "iso"},
        )
    except Exception as e:
        print(f"WARNING: Could not fetch odds from The Odds API: {e}")
        return []

    games = []
    for event in data:
        game = {
            "id": event.get("id"),
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "kickoff": event.get("commence_time"),
            "h2h": {},
            "spreads": {},
        }

        for bookie in event.get("bookmakers", []):
            for market in bookie.get("markets", []):
                if market["key"] == "h2h":
                    for outcome in market["outcomes"]:
                        game["h2h"].setdefault(outcome["name"], []).append(outcome["price"])
                elif market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        game["spreads"].setdefault(outcome["name"], {
                            "point": outcome.get("point", 0),
                            "prices": [],
                        })["prices"].append(outcome["price"])

        # Average odds across bookmakers
        for team, prices in game["h2h"].items():
            game["h2h"][team] = round(sum(prices) / len(prices), 2)
        for team, info in game["spreads"].items():
            info["price"] = round(sum(info["prices"]) / len(info["prices"]), 2)
            del info["prices"]

        games.append(game)

    return games


# ---------------------------------------------------------------------------
# Step 2: Fetch NRL team stats from nrl.com ladder
# ---------------------------------------------------------------------------

def fetch_team_stats():
    """Returns dict of team_nickname -> stats using the nrl.com ladder API."""
    season = datetime.now().year
    try:
        data = nrl_get(
            "https://www.nrl.com/ladder/data",
            {"competition": NRL_COMPETITION_ID, "season": season},
        )
        positions = data.get("positions", [])
    except Exception as e:
        print(f"WARNING: Could not fetch ladder from nrl.com: {e}")
        return {}

    team_stats = {}
    for entry in positions:
        nickname = entry.get("teamNickname", "")
        stats = entry.get("stats", {})
        played = int(stats.get("played", 0) or 0)
        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("lost", 0) or 0)
        draws = int(stats.get("drawn", 0) or 0)
        points_for = int(stats.get("points for", 0) or 0)
        points_against = int(stats.get("points against", 0) or 0)
        avg_win_margin = float(stats.get("average winning margin", 0) or 0)
        avg_loss_margin = float(stats.get("average losing margin", 0) or 0)
        win_pct = wins / played if played else 0.5
        avg_margin = (points_for - points_against) / played if played else 0

        # Store under both nickname and full name variants
        next_team = entry.get("next", {})
        team_stats[nickname] = {
            "wins": wins,
            "losses": losses,
            "played": played,
            "win_pct": win_pct,
            "avg_margin": avg_margin,
            "avg_win_margin": avg_win_margin,
            "avg_loss_margin": avg_loss_margin,
            "points_for": points_for,
            "points_against": points_against,
        }

    return team_stats


def _team_key(name, lookup):
    """Match a team name (from Odds API) to a key in a stats dict (nicknames from nrl.com)."""
    if name in lookup:
        return name
    name_lower = name.lower()
    for key in lookup:
        if key.lower() in name_lower or name_lower.endswith(key.lower()):
            return key
    return None


# ---------------------------------------------------------------------------
# Step 2b: Fetch team form stats (home/away splits + recent form) from draw API
# ---------------------------------------------------------------------------

def fetch_team_form_stats():
    """
    Builds per-team form data from completed fixture results using the NRL draw API.
    No match centre calls needed — scores are available in fixture data.
    Returns dict of team_nickname -> form data including home/away records and recent form.
    """
    season = datetime.now().year
    team_results = {}  # nickname -> list of game result dicts (chronological)

    try:
        draw = nrl_get(
            "https://www.nrl.com/draw/data",
            {"competition": NRL_COMPETITION_ID, "season": season},
        )
        all_rounds = [r["value"] for r in draw.get("filterRounds", [])]
    except Exception as e:
        print(f"  Could not fetch draw for form stats: {e}")
        return {}

    for rnd in all_rounds:
        try:
            r_data = nrl_get(
                "https://www.nrl.com/draw/data",
                {"competition": NRL_COMPETITION_ID, "season": season, "round": rnd},
            )
            for f in r_data.get("fixtures", []):
                if f.get("matchState") not in ("FullTime", "Post", "Final"):
                    continue

                home_obj = f.get("homeTeam", {})
                away_obj = f.get("awayTeam", {})
                home_nick = home_obj.get("nickName") or home_obj.get("teamNickname", "")
                away_nick = away_obj.get("nickName") or away_obj.get("teamNickname", "")

                # Try multiple score field names used by nrl.com API
                home_score = (
                    home_obj.get("score")
                    or home_obj.get("teamScore")
                    or f.get("homeScore")
                    or f.get("homeTeamScore")
                )
                away_score = (
                    away_obj.get("score")
                    or away_obj.get("teamScore")
                    or f.get("awayScore")
                    or f.get("awayTeamScore")
                )

                if home_nick and away_nick and home_score is not None and away_score is not None:
                    home_score = int(home_score)
                    away_score = int(away_score)
                    home_margin = home_score - away_score
                    home_win = home_margin > 0

                    team_results.setdefault(home_nick, []).append({
                        "round": rnd,
                        "is_home": True,
                        "score_for": home_score,
                        "score_against": away_score,
                        "margin": home_margin,
                        "win": home_win,
                    })
                    team_results.setdefault(away_nick, []).append({
                        "round": rnd,
                        "is_home": False,
                        "score_for": away_score,
                        "score_against": home_score,
                        "margin": -home_margin,
                        "win": not home_win,
                    })
        except Exception:
            continue

    # Compute derived stats per team
    form_stats = {}
    for nick, results in team_results.items():
        home_games = [r for r in results if r["is_home"]]
        away_games = [r for r in results if not r["is_home"]]

        home_wins = sum(1 for r in home_games if r["win"])
        away_wins = sum(1 for r in away_games if r["win"])

        # Recent form: last 5 games, weights [1,2,3,4,5] oldest→newest
        last5 = results[-5:]
        weights = list(range(1, len(last5) + 1))
        w_total = sum(weights)
        if w_total:
            recent_form_rating = sum(r["win"] * w for r, w in zip(last5, weights)) / w_total
            recent_avg_margin = sum(r["margin"] * w for r, w in zip(last5, weights)) / w_total
        else:
            recent_form_rating = 0.5
            recent_avg_margin = 0.0

        # Home margin advantage vs away (for spread model)
        home_margins = [r["margin"] for r in home_games]
        home_margin_advantage = (sum(home_margins) / len(home_margins)) if home_margins else 3.0

        form_stats[nick] = {
            "home_wins": home_wins,
            "home_played": len(home_games),
            "home_win_pct": home_wins / len(home_games) if home_games else 0.5,
            "away_wins": away_wins,
            "away_played": len(away_games),
            "away_win_pct": away_wins / len(away_games) if away_games else 0.5,
            "recent_form_rating": round(recent_form_rating, 4),
            "recent_avg_margin": round(recent_avg_margin, 2),
            "home_margin_advantage": round(home_margin_advantage, 2),
        }

    print(f"  Built form stats for {len(form_stats)} teams")
    return form_stats


# ---------------------------------------------------------------------------
# Step 3: Fetch NRL player try stats by aggregating match centre data
# ---------------------------------------------------------------------------

def fetch_all_player_try_stats():
    """
    Builds season try-scoring stats by fetching completed match centre data from nrl.com.
    Returns dict of player_name -> {name, position, team, tries, games, try_rate, recent_try_rate}.
    recent_try_rate is based on the last 4 games (blended into model for recency bias).
    """
    season = datetime.now().year
    player_data = {}       # pid -> {name, position, team, tries}
    player_game_ids = {}   # pid -> set of match IDs (deduplicate games played)
    player_game_tries = {} # pid -> list of 0/1 in chronological order (for recent form)

    try:
        draw = nrl_get(
            "https://www.nrl.com/draw/data",
            {"competition": NRL_COMPETITION_ID, "season": season},
        )
        all_rounds = [r["value"] for r in draw.get("filterRounds", [])]
    except Exception as e:
        print(f"  Could not fetch draw: {e}")
        return {}

    completed_matches = []
    for rnd in all_rounds:
        try:
            r_data = nrl_get(
                "https://www.nrl.com/draw/data",
                {"competition": NRL_COMPETITION_ID, "season": season, "round": rnd},
            )
            for f in r_data.get("fixtures", []):
                if f.get("matchState") in ("FullTime", "Post", "Final"):
                    mc_url = f.get("matchCentreUrl", "")
                    if mc_url:
                        completed_matches.append(mc_url)
        except Exception:
            continue

    print(f"  Fetching stats from {len(completed_matches)} completed matches...")

    for mc_url in completed_matches:
        try:
            match_data = nrl_get(f"https://www.nrl.com{mc_url}data")
            match_id = match_data.get("matchId", mc_url)

            # Collect try scorer pids for this match first
            match_try_pids = set()
            for event in match_data.get("timeline", []):
                if event.get("type") == "Try":
                    pid = event.get("playerId")
                    if pid:
                        match_try_pids.add(pid)

            # Register all players from both teams
            for side in ("homeTeam", "awayTeam"):
                team_info = match_data.get(side, {})
                team_name = team_info.get("nickName", "")
                for player in team_info.get("players", []):
                    pid = player.get("profileId") or player.get("playerId")
                    if not pid:
                        continue
                    pname = f"{player.get('firstName', '')} {player.get('lastName', '')}".strip()
                    position = player.get("position", "default")
                    if pid not in player_data:
                        player_data[pid] = {
                            "name": pname,
                            "position": position,
                            "team": team_name,
                            "tries": 0,
                        }
                    player_game_ids.setdefault(pid, set()).add(match_id)
                    scored = 1 if pid in match_try_pids else 0
                    player_data[pid]["tries"] += scored
                    player_game_tries.setdefault(pid, []).append(scored)

        except Exception:
            continue

    # Calculate try rates including recent form (last 4 games)
    result = {}
    for pid, info in player_data.items():
        games = len(player_game_ids.get(pid, set()))
        info["games"] = games
        if games > 0:
            info["try_rate"] = round(info["tries"] / games, 3)
            recent = player_game_tries.get(pid, [])[-4:]
            info["recent_try_rate"] = round(sum(recent) / len(recent), 3) if len(recent) >= 2 else info["try_rate"]
            result[info["name"]] = info

    print(f"  Built try stats for {len(result)} players")
    return result


def fetch_player_try_stats_for_team(team_name, all_player_stats):
    """Return players from a specific team, sorted by try rate."""
    # Extract nickname from full name (e.g. "Canberra Raiders" -> "Raiders")
    nickname = team_name.split()[-1] if team_name else ""
    team_players = [
        {"name": name, **info}
        for name, info in all_player_stats.items()
        if info.get("team", "").lower() in team_name.lower()
        or (nickname and nickname.lower() == info.get("team", "").lower())
    ]
    if not team_players:
        # Fallback: return top try scorers globally (try scorer odds will filter relevance)
        team_players = [{"name": name, **info} for name, info in all_player_stats.items()]
    return sorted(team_players, key=lambda x: x["try_rate"], reverse=True)[:15]


# ---------------------------------------------------------------------------
# Step 4: Fetch player prop odds (try scorers) from The Odds API
# ---------------------------------------------------------------------------

def fetch_try_scorer_odds(event_id):
    """Returns dict of player_name -> {anytime_odds, first_odds} from available bookmakers."""
    try:
        data = odds_get(
            f"/sports/rugbyleague_nrl/events/{event_id}/odds",
            {"regions": "au", "markets": "player_anytime_try_scorer,player_first_try_scorer",
             "oddsFormat": "decimal"},
        )
    except Exception:
        return {}

    player_odds = {}
    for bookie in data.get("bookmakers", []):
        for market in bookie.get("markets", []):
            key = market["key"]
            for outcome in market["outcomes"]:
                pname = outcome["name"]
                price = outcome["price"]
                player_odds.setdefault(pname, {})
                if key == "player_anytime_try_scorer":
                    player_odds[pname].setdefault("anytime_prices", []).append(price)
                elif key == "player_first_try_scorer":
                    player_odds[pname].setdefault("first_prices", []).append(price)

    # Average across bookmakers
    for pname, info in player_odds.items():
        if "anytime_prices" in info:
            info["anytime_odds"] = round(sum(info["anytime_prices"]) / len(info["anytime_prices"]), 2)
            del info["anytime_prices"]
        if "first_prices" in info:
            info["first_odds"] = round(sum(info["first_prices"]) / len(info["first_prices"]), 2)
            del info["first_prices"]

    return player_odds


# ---------------------------------------------------------------------------
# Step 5: Edge & probability calculations
# ---------------------------------------------------------------------------

def implied_prob(decimal_odds):
    """Convert decimal odds to implied probability (raw, not margin-adjusted)."""
    if not decimal_odds or decimal_odds <= 1:
        return 0
    return round(1 / decimal_odds, 4)


def model_h2h_prob(home_team, away_team, team_stats, team_form=None):
    """
    Estimate home team win probability.
    Blends home/away location-specific win% with recent form (last 5 games weighted).
    Home advantage is implicit in home_win_pct rather than a fixed bonus.
    Falls back to season win% + fixed 5% if form data is unavailable.
    """
    team_form = team_form or {}
    home_key = _team_key(home_team, team_stats)
    away_key = _team_key(away_team, team_stats)
    home_ts = team_stats.get(home_key, {})
    away_ts = team_stats.get(away_key, {})
    home_form = team_form.get(home_key, {})
    away_form = team_form.get(away_key, {})

    # Location-specific win% (home team's home record, away team's away record)
    home_base = home_form.get("home_win_pct", home_ts.get("win_pct", 0.5))
    away_base = away_form.get("away_win_pct", away_ts.get("win_pct", 0.5))

    # Recent form rating (last 5 games, recency-weighted)
    home_recent = home_form.get("recent_form_rating", home_base)
    away_recent = away_form.get("recent_form_rating", away_base)

    # Mean-reversion: if a team's season quality is significantly better than recent form,
    # the market tends to over-penalise them. Shift weight back toward season stats
    # proportionally to the slump — up to 20% extra weighting on the season baseline.
    def _blended_strength(season_base, recent):
        slump = max(0.0, season_base - recent)
        extra_season_w = min(0.20, slump * 0.6)
        s_w = 0.5 + extra_season_w
        r_w = 0.5 - extra_season_w
        return s_w * season_base + r_w * recent

    home_strength = _blended_strength(home_base, home_recent)
    away_strength = _blended_strength(away_base, away_recent)

    total = home_strength + away_strength
    if total == 0:
        return 0.5
    # Cap at 0.82 — no team is a reliable 95% chance in NRL, and inflated
    # success rates are misleading when displayed to the user.
    return min(max(round(home_strength / total, 4), 0.05), 0.82)


def model_spread_prob(home_team, away_team, spread_point, team_stats, team_form=None):
    """
    Estimate probability that the home team covers the spread.
    Blends season avg_margin (60%) with recent avg_margin (40%) for better recency.
    Home advantage is derived from actual home margin data rather than a fixed +3.
    Spread_point is from home team's perspective (negative = home favoured).
    """
    team_form = team_form or {}
    home_key = _team_key(home_team, team_stats)
    away_key = _team_key(away_team, team_stats)
    home_ts = team_stats.get(home_key, {})
    away_ts = team_stats.get(away_key, {})
    home_form = team_form.get(home_key, {})
    away_form = team_form.get(away_key, {})

    home_season = home_ts.get("avg_margin", 0)
    away_season = away_ts.get("avg_margin", 0)
    home_recent = home_form.get("recent_avg_margin", home_season)
    away_recent = away_form.get("recent_avg_margin", away_season)

    # 60% season, 40% recent form
    home_eff = 0.6 * home_season + 0.4 * home_recent
    away_eff = 0.6 * away_season + 0.4 * away_recent

    # Home advantage from actual home margin data (default 3 if no data)
    home_advantage = home_form.get("home_margin_advantage", 3.0)
    expected_margin = home_eff - away_eff + home_advantage

    std_dev = 14.0
    z = (expected_margin - (-spread_point)) / std_dev
    prob = 0.5 * (1 + math.erf(z / math.sqrt(2)))

    # Larger handicaps are harder to cover reliably — discount confidence as spread grows.
    # For every point beyond 6.0, reduce the max confidence by 1.2%.
    # e.g. -6.5 → max ~74.3%, -12.5 → max ~67.1%, -16.5 → max ~62.7%
    spread_size = abs(spread_point)
    spread_discount = max(0.0, (spread_size - 6.0) * 0.012)
    max_conf = max(0.62, 0.75 - spread_discount)
    return round(min(max(prob, 0.05), max_conf), 4)


def model_expected_margin(home_team, away_team, team_stats, team_form=None):
    """
    Returns the model's expected points margin from the home team's perspective.
    Positive = home team expected to win; negative = away team expected to win.
    Used to flag '13+ margin' games where a winning margin market may offer better value.
    """
    team_form = team_form or {}
    home_key = _team_key(home_team, team_stats)
    away_key = _team_key(away_team, team_stats)
    home_ts = team_stats.get(home_key, {})
    away_ts = team_stats.get(away_key, {})
    home_form = team_form.get(home_key, {})
    away_form = team_form.get(away_key, {})

    home_season = home_ts.get("avg_margin", 0)
    away_season = away_ts.get("avg_margin", 0)
    home_recent = home_form.get("recent_avg_margin", home_season)
    away_recent = away_form.get("recent_avg_margin", away_season)

    home_eff = 0.6 * home_season + 0.4 * home_recent
    away_eff = 0.6 * away_season + 0.4 * away_recent
    home_advantage = home_form.get("home_margin_advantage", 3.0)
    # Scale by 0.5 to avoid double-counting each team's margin stats — the raw
    # difference over-estimates by roughly 2x since both sides' avg_margin already
    # reflects opponent quality. Result is a realistic expected points margin.
    return round((home_eff - away_eff) * 0.5 + home_advantage, 1)


def model_ats_prob(player):
    """
    Anytime try scorer model probability.
    Blends season try_rate (60%) with recent try_rate from last 4 games (40%).
    """
    recent = player.get("recent_try_rate", player["try_rate"])
    blended = 0.6 * player["try_rate"] + 0.4 * recent
    return round(min(blended, 0.95), 4)


def model_fts_prob(player):
    """
    First try scorer model probability.
    Blends season and recent try rates then applies positional factor.
    """
    recent = player.get("recent_try_rate", player["try_rate"])
    blended = 0.6 * player["try_rate"] + 0.4 * recent
    factor = POSITION_FTS_FACTOR.get(player["position"], POSITION_FTS_FACTOR["default"])
    return round(min(blended * factor, 0.5), 4)


def calc_edge(model_prob, bookie_implied):
    """Edge = model probability minus bookie implied probability."""
    return round(model_prob - bookie_implied, 4)


def edge_label(edge):
    if edge >= 0.10:
        return "STRONG VALUE"
    elif edge >= 0.03:
        return "VALUE"
    elif edge > 0:
        return "MARGINAL"
    else:
        return None


def expected_return(decimal_odds, model_prob, stake=10):
    """Expected return per $stake = odds × model_prob × stake."""
    return round(decimal_odds * model_prob * stake, 2)


def value_score(edge, model_prob):
    """Risk-adjusted score — balances edge with likelihood of winning."""
    return round(edge * model_prob, 4)


# ---------------------------------------------------------------------------
# Step 6: Build per-game analysis
# ---------------------------------------------------------------------------

def analyse_game(game, team_stats, team_form, all_player_stats):
    """Returns a dict of analysed bets for one game."""
    home = game["home_team"]
    away = game["away_team"]

    # Expected margin: positive = home favoured, negative = away favoured
    exp_margin = model_expected_margin(home, away, team_stats, team_form)

    # 13+ Margin Alert: fire only when the bookmaker has set a line of -12.5 or bigger.
    # This uses the bookie spread (reliable) rather than the model's margin estimate
    # (which over-estimates due to avg_margin double-counting).
    big_win_alert = None
    for team, info in game.get("spreads", {}).items():
        point = info.get("point", 0)
        if point <= -12.5:  # team is favoured by 12.5+ pts — genuine blowout game
            big_win_alert = {
                "team": team,
                "margin": abs(point),
                "line": point,
            }
            break

    result = {
        "home_team": home,
        "away_team": away,
        "kickoff": game["kickoff"],
        "h2h_bets": [],
        "spread_bets": [],
        "ats_picks": [],
        "fts_picks": [],
        "best_bet": None,
        "all_value_bets": [],
        "big_win_alert": big_win_alert,
        "exp_margin": exp_margin,
    }

    # --- H2H ---
    for team, odds in game["h2h"].items():
        is_home = (team == home)
        if is_home:
            mp = model_h2h_prob(home, away, team_stats, team_form)
        else:
            mp = round(1 - model_h2h_prob(home, away, team_stats, team_form), 4)
        imp = implied_prob(odds)
        edge = calc_edge(mp, imp)
        label = edge_label(edge)
        bet = {
            "team": team,
            "odds": odds,
            "model_prob": mp,
            "implied_prob": imp,
            "edge": edge,
            "label": label,
            "success_rate": f"{round(mp * 100)}%",
            "exp_return": expected_return(odds, mp),
            "value_score": value_score(edge, mp) if edge > 0 else 0,
            "bet_type": "H2H",
            "description": f"{team} to win",
            "longshot": odds >= 3.5,
        }
        result["h2h_bets"].append(bet)
        if edge > 0:
            result["all_value_bets"].append(bet)

    # --- Spreads ---
    for team, info in game["spreads"].items():
        point = info["point"]
        odds = info["price"]
        is_home = (team == home)
        if is_home:
            mp = model_spread_prob(home, away, point, team_stats, team_form)
        else:
            mp = model_spread_prob(away, home, point, team_stats, team_form)
        imp = implied_prob(odds)
        edge = calc_edge(mp, imp)
        label = edge_label(edge)
        sign = "+" if point > 0 else ""
        bet = {
            "team": team,
            "point": point,
            "odds": odds,
            "model_prob": mp,
            "implied_prob": imp,
            "edge": edge,
            "label": label,
            "success_rate": f"{round(mp * 100)}%",
            "exp_return": expected_return(odds, mp),
            "value_score": value_score(edge, mp) if edge > 0 else 0,
            "bet_type": "Line",
            "description": f"{team} {sign}{point}",
        }
        result["spread_bets"].append(bet)
        if edge > 0:
            result["all_value_bets"].append(bet)

    # --- Try scorer odds from The Odds API ---
    try_odds = fetch_try_scorer_odds(game["id"]) if game.get("id") else {}

    # --- ATS & FTS picks (merge stats + odds) ---
    for side_team in [home, away]:
        players = fetch_player_try_stats_for_team(side_team, all_player_stats)

        for player in players[:15]:  # top 15 by try rate
            pname = player["name"]
            odds_info = try_odds.get(pname, {})

            # Anytime Try Scorer
            ats_odds = odds_info.get("anytime_odds")
            ats_mp = model_ats_prob(player)
            if ats_odds:
                ats_imp = implied_prob(ats_odds)
                ats_edge = calc_edge(ats_mp, ats_imp)
                ats_label = edge_label(ats_edge)
                ats_bet = {
                    "player": pname,
                    "position": player["position"],
                    "try_rate": player["try_rate"],
                    "games": player["games"],
                    "odds": ats_odds,
                    "model_prob": ats_mp,
                    "implied_prob": ats_imp,
                    "edge": ats_edge,
                    "label": ats_label,
                    "success_rate": f"{round(ats_mp * 100)}%",
                    "exp_return": expected_return(ats_odds, ats_mp),
                    "value_score": value_score(ats_edge, ats_mp) if ats_edge > 0 else 0,
                    "bet_type": "ATS",
                    "description": f"{pname} — Anytime Try Scorer",
                }
                if ats_edge > 0:
                    result["ats_picks"].append(ats_bet)
                    result["all_value_bets"].append(ats_bet)

            # First Try Scorer
            fts_odds = odds_info.get("first_odds")
            fts_mp = model_fts_prob(player)
            if fts_odds:
                fts_imp = implied_prob(fts_odds)
                fts_edge = calc_edge(fts_mp, fts_imp)
                fts_label = edge_label(fts_edge)
                fts_bet = {
                    "player": pname,
                    "position": player["position"],
                    "try_rate": player["try_rate"],
                    "games": player["games"],
                    "odds": fts_odds,
                    "model_prob": fts_mp,
                    "implied_prob": fts_imp,
                    "edge": fts_edge,
                    "label": fts_label,
                    "success_rate": f"{round(fts_mp * 100)}%",
                    "exp_return": expected_return(fts_odds, fts_mp),
                    "value_score": value_score(fts_edge, fts_mp) if fts_edge > 0 else 0,
                    "bet_type": "FTS",
                    "description": f"{pname} — First Try Scorer",
                }
                if fts_edge > 0:
                    result["fts_picks"].append(fts_bet)
                    result["all_value_bets"].append(fts_bet)

    # Sort picks by edge descending, keep top 5
    result["ats_picks"] = sorted(result["ats_picks"], key=lambda x: x["edge"], reverse=True)[:5]
    result["fts_picks"] = sorted(result["fts_picks"], key=lambda x: x["edge"], reverse=True)[:5]

    # Best bet this game: prefer H2H (historically +39% ROI vs Line -16% ROI).
    # Only fall back to Line/ATS/FTS when no H2H has positive edge.
    if result["all_value_bets"]:
        h2h_value = [b for b in result["all_value_bets"] if b["bet_type"] == "H2H"]
        if h2h_value:
            result["best_bet"] = max(h2h_value, key=lambda x: x["exp_return"])
        else:
            result["best_bet"] = max(result["all_value_bets"], key=lambda x: x["exp_return"])

    return result


# ---------------------------------------------------------------------------
# Step 7: Round summary — best bets across all games
# ---------------------------------------------------------------------------

def build_round_summary(games_analysis):
    all_bets = []
    all_h2h = []
    for g in games_analysis:
        for bet in g["all_value_bets"]:
            bet["game_label"] = f"{g['home_team']} vs {g['away_team']}"
            all_bets.append(bet)
        for bet in g["h2h_bets"]:
            bet["game_label"] = f"{g['home_team']} vs {g['away_team']}"
            all_h2h.append(bet)

    # Primary: best expected dollar return (maximises return, surfaces longshots)
    best_return = sorted(all_bets, key=lambda x: x["exp_return"], reverse=True)[:5]
    # Secondary: best edge % (most mispriced by bookie)
    best_value = sorted(all_bets, key=lambda x: x["edge"], reverse=True)[:5]
    # Safety: highest model probability (most likely to win, for conservative bettors)
    best_win_rate = sorted(all_bets, key=lambda x: x["model_prob"], reverse=True)[:5]

    # Longshot Watch — H2H underdogs at $3.50+ with positive edge, ranked by expected return
    longshot_watch = [
        b for b in all_h2h
        if b.get("longshot") and b["edge"] > 0
    ]
    longshot_watch = sorted(longshot_watch, key=lambda x: x["exp_return"], reverse=True)[:5]

    return {
        "best_win_rate": best_win_rate,
        "best_value": best_value,
        "best_return": best_return,
        "all_bets": all_bets,
        "longshot_watch": longshot_watch,
    }


# ---------------------------------------------------------------------------
# Step 8: Auto-generate suggested multis
# ---------------------------------------------------------------------------

def build_multis(summary, games_analysis):
    all_bets = summary["all_bets"]
    if not all_bets:
        return {}

    def multi_stats(legs):
        combined_odds = 1.0
        combined_prob = 1.0
        combined_edge = 0.0
        for leg in legs:
            combined_odds *= leg["odds"]
            combined_prob *= leg["model_prob"]
        combined_odds = round(combined_odds, 2)
        combined_prob = round(combined_prob, 4)
        combined_edge = round(combined_prob - (1 / combined_odds), 4)
        exp_ret = round(combined_odds * combined_prob * 10, 2)
        return {
            "legs": legs,
            "combined_odds": combined_odds,
            "combined_prob": combined_prob,
            "combined_edge": combined_edge,
            "exp_return": exp_ret,
            "combined_prob_pct": f"{round(combined_prob * 100)}%",
        }

    def pick_one_per_game(candidates):
        """Select best candidate per game to avoid duplicate legs."""
        seen_games = set()
        selected = []
        for bet in candidates:
            g = bet.get("game_label", "")
            if g not in seen_games:
                seen_games.add(g)
                selected.append(bet)
            if len(selected) >= 4:
                break
        return selected

    # Safety multi — highest model probability bets, one per game
    safety_candidates = sorted(all_bets, key=lambda x: x["model_prob"], reverse=True)
    safety_legs = pick_one_per_game(safety_candidates)

    # Value multi — highest edge bets, one per game
    value_candidates = sorted(all_bets, key=lambda x: x["edge"], reverse=True)
    value_legs = pick_one_per_game(value_candidates)

    # Best of week — best bet from each game (highest value score)
    bow_legs = []
    for g in games_analysis:
        if g["best_bet"]:
            g["best_bet"]["game_label"] = f"{g['home_team']} vs {g['away_team']}"
            bow_legs.append(g["best_bet"])
    bow_legs = bow_legs[:5]

    multis = {}
    if len(safety_legs) >= 2:
        multis["safety"] = multi_stats(safety_legs)
    if len(value_legs) >= 2:
        multis["value"] = multi_stats(value_legs)
    if len(bow_legs) >= 2:
        multis["best_of_week"] = multi_stats(bow_legs)

    return multis


# ---------------------------------------------------------------------------
# Step 9: (No separate team ID map needed — nrl.com data uses nicknames)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Step 10: HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NRL Tip Sheet — Round {{ round_label }}</title>
<style>
  :root {
    --green: #16a34a; --red: #dc2626; --gold: #b45309; --blue: #1d4ed8;
    --bg: #0f172a; --card: #1e293b; --border: #334155; --text: #e2e8f0;
    --muted: #94a3b8; --accent: #38bdf8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; line-height: 1.5; }
  .container { max-width: 900px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 1.6rem; font-weight: 700; color: var(--accent); }
  h2 { font-size: 1.1rem; font-weight: 700; color: var(--accent); margin: 20px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  h3 { font-size: 0.95rem; font-weight: 600; color: var(--muted); margin: 14px 0 6px; text-transform: uppercase; letter-spacing: 0.05em; }
  .header { display: flex; justify-content: space-between; align-items: center; padding: 16px 0 8px; border-bottom: 2px solid var(--accent); margin-bottom: 20px; flex-wrap: wrap; gap: 8px; }
  .generated { color: var(--muted); font-size: 0.8rem; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
  .game-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .game-title { font-size: 1.1rem; font-weight: 700; }
  .kickoff { color: var(--muted); font-size: 0.82rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 6px 8px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); }
  td { padding: 6px 8px; border-bottom: 1px solid #1e293b; }
  tr:last-child td { border-bottom: none; }
  .value-strong { color: #4ade80; font-weight: 700; }
  .value-ok { color: #86efac; }
  .value-marginal { color: #fbbf24; }
  .no-value { color: var(--red); }
  .tick { color: var(--green); }
  .cross { color: var(--red); }
  .best-bet { background: linear-gradient(135deg, #1a2e1a, #1e293b); border: 1px solid var(--green); border-radius: 8px; padding: 12px; margin-top: 12px; }
  .best-bet-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; color: #4ade80; font-weight: 700; margin-bottom: 4px; }
  .best-bet-title { font-size: 1rem; font-weight: 700; }
  .best-bet-meta { font-size: 0.8rem; color: var(--muted); margin-top: 4px; display: flex; gap: 16px; flex-wrap: wrap; }
  .multi-card { border-left: 4px solid; }
  .multi-safety { border-color: #38bdf8; }
  .multi-value { border-color: #a78bfa; }
  .multi-bow { border-color: #fbbf24; }
  .multi-title { font-weight: 700; font-size: 0.95rem; margin-bottom: 8px; }
  .multi-legs { font-size: 0.8rem; color: var(--muted); margin-bottom: 8px; }
  .multi-stats { display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.82rem; }
  .multi-stat { display: flex; flex-direction: column; }
  .multi-stat-label { color: var(--muted); font-size: 0.72rem; }
  .multi-stat-value { font-weight: 700; }
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 20px; }
  .summary-section h3 { margin-top: 0; }
  .summary-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid var(--border); font-size: 0.82rem; }
  .summary-row:last-child { border-bottom: none; }
  .summary-label { flex: 1; }
  .summary-odds { color: var(--accent); font-weight: 600; min-width: 40px; text-align: right; margin-left: 8px; }
  .summary-edge { min-width: 60px; text-align: right; font-weight: 700; }

  /* Cheat sheet */
  details { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 20px; }
  summary { cursor: pointer; font-weight: 700; color: var(--accent); font-size: 1rem; list-style: none; display: flex; align-items: center; gap: 8px; }
  summary::before { content: "▶"; font-size: 0.7rem; transition: transform 0.2s; }
  details[open] summary::before { transform: rotate(90deg); }
  .cheat-table { width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 0.82rem; }
  .cheat-table th { text-align: left; padding: 8px; background: #0f172a; color: var(--accent); }
  .cheat-table td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .cheat-table td:first-child { font-weight: 700; white-space: nowrap; color: #f1f5f9; min-width: 160px; }
  .cheat-table tr:last-child td { border-bottom: none; }

  .label-badge { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 0.7rem; font-weight: 700; }
  .badge-strong { background: #14532d; color: #4ade80; }
  .badge-value { background: #052e16; color: #86efac; }
  .badge-marginal { background: #451a03; color: #fbbf24; }

  .longshot-card { background: linear-gradient(135deg, #1c1a2e, #1e293b); border: 1px solid #7c3aed; border-radius: 10px; padding: 14px; margin-bottom: 14px; }
  .longshot-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .longshot-title { font-weight: 700; font-size: 0.95rem; color: #a78bfa; }
  .longshot-badge { background: #4c1d95; color: #c4b5fd; font-size: 0.7rem; font-weight: 700; padding: 2px 8px; border-radius: 4px; }
  .longshot-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid #2d2a4a; font-size: 0.82rem; }
  .longshot-row:last-child { border-bottom: none; }
  .longshot-team { flex: 1; font-weight: 600; }
  .longshot-game { color: var(--muted); font-size: 0.74rem; }
  .longshot-odds { color: #a78bfa; font-weight: 700; font-size: 1rem; min-width: 50px; text-align: right; }
  .longshot-meta { display: flex; gap: 12px; font-size: 0.76rem; color: var(--muted); margin-top: 2px; flex-wrap: wrap; }
  .longshot-edge { color: #86efac; font-weight: 600; }

  @media (max-width: 600px) {
    .multi-stats { gap: 10px; }
    table { font-size: 0.76rem; }
    th, td { padding: 5px 6px; }
  }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <h1>NRL Tip Sheet</h1>
      <div class="generated">Generated {{ generated_at }} &nbsp;|&nbsp; {{ round_label }}</div>
    </div>
    <div>
      <a href="dashboard.html" style="color: var(--accent); font-size: 0.85rem; text-decoration: none;">📊 Performance Dashboard</a>
    </div>
  </div>

  <!-- CHEAT SHEET -->
  <details>
    <summary>How to Read This Sheet — Glossary</summary>
    <table class="cheat-table">
      <tr><th>Term</th><th>What it means</th></tr>
      <tr><td>Model %</td><td>What our stats model estimates the true probability to be, based on season win rates, points margins, recent form, and home advantage.</td></tr>
      <tr><td>Implied %</td><td>What the bookmaker's odds imply the probability is. Calculated as <strong>1 ÷ decimal odds</strong>. e.g. $4.00 odds = 25% implied probability.</td></tr>
      <tr><td>Edge</td><td>The gap between our model and the bookie. <strong>Positive = value bet</strong> (bookie has underpriced it). e.g. +12% means our model gives it 12% more chance than the bookie does.</td></tr>
      <tr><td>✅ Green tick</td><td>Positive edge — our model thinks the bookie's odds are too generous. Potential value bet.</td></tr>
      <tr><td>❌ Red cross</td><td>Negative edge — the bookie has overpriced this. Generally avoid.</td></tr>
      <tr><td><span class="label-badge badge-strong">STRONG VALUE</span></td><td>Edge greater than +10%. The bookie looks significantly off — strong value bet.</td></tr>
      <tr><td><span class="label-badge badge-value">VALUE</span></td><td>Edge between +3% and +10%. A solid value bet.</td></tr>
      <tr><td><span class="label-badge badge-marginal">MARGINAL</span></td><td>Edge under +3%. Slight positive edge but close to breakeven. Use with caution.</td></tr>
      <tr><td>Success Rate</td><td>Our model's estimated probability of this bet winning. e.g. 62% = wins roughly 6 times out of 10 long-term. This is NOT a guarantee.</td></tr>
      <tr><td>Exp. Return / $10</td><td>Average $ back per $10 staked if the model is correct long-term. e.g. $14.20 = $4.20 average profit per $10. <strong>Does not mean you will win every bet.</strong></td></tr>
      <tr><td>Try Rate / game</td><td>How many tries this player scores per game on average this season. e.g. 0.62 = scores a try in ~62% of games.</td></tr>
      <tr><td>ATS — Anytime Try Scorer</td><td>Bet wins if the player scores a try at any point in the match.</td></tr>
      <tr><td>FTS — First Try Scorer</td><td>Bet wins only if the player scores the <strong>very first try</strong> of the match. Higher odds, lower probability — FTS model weights towards positions that typically score first (Fullbacks, Wingers).</td></tr>
      <tr><td>Line / Handicap</td><td>A points head start or deficit. e.g. Brisbane -7.5 = Brisbane must win by 8+ points. +7.5 = team can lose by up to 7 and still win the bet. <strong>Note:</strong> Model confidence is capped lower for large spreads (e.g. -16.5) — covering a big handicap is much harder than a small one.</td></tr>
      <tr><td>Longshot Watch</td><td>H2H underdogs paying $4.00 or more where the model detects positive edge. Bookmakers may be underpricing the underdog. High risk — review the game context carefully before backing.</td></tr>
      <tr><td>Safety Multi</td><td>Legs chosen for highest win probability. More likely to land — lower combined odds.</td></tr>
      <tr><td>Value Multi</td><td>Legs chosen for highest edge — where bookies look most wrong. Higher odds but less likely to all land.</td></tr>
      <tr><td>Best of Week Multi</td><td>Top recommended pick from each game combined. Balances edge and probability.</td></tr>
      <tr><td>Combined Odds</td><td>All leg odds multiplied together. e.g. $2.00 × $3.50 = $7.00 combined.</td></tr>
      <tr><td>Combined Prob</td><td>All model probabilities multiplied together. The realistic chance of the whole multi landing. e.g. 60% × 45% = 27%.</td></tr>
      <tr><td>Bet365 note</td><td>Odds shown are averaged from Australian bookmakers (Sportsbet, TAB, Ladbrokes). Bet365 player prop odds are not available via our data feed — compare these prices to what you see in your Bet365 app. If Bet365 is higher, that's even better value.</td></tr>
    </table>
  </details>

  <!-- ROUND SUMMARY -->
  {% if summary %}
  <h2>Round Summary — Best Bets This Week</h2>
  <div class="summary-grid">

    <div class="card summary-section">
      <h3>Best Expected Return / $10 ★</h3>
      <p style="color:var(--muted);font-size:0.74rem;margin-bottom:6px;">Ranked by dollar return — surfaces value underdogs, not just safe bets.</p>
      {% for bet in summary.best_return %}
      <div class="summary-row">
        <span class="summary-label">{{ bet.description }}<br><small style="color:#64748b">{{ bet.game_label }}</small></span>
        <span class="summary-odds">${{ bet.odds }}</span>
        <span class="summary-edge value-strong">${{ bet.exp_return }}</span>
      </div>
      {% endfor %}
    </div>

    <div class="card summary-section">
      <h3>Best Value (Most Mispriced)</h3>
      <p style="color:var(--muted);font-size:0.74rem;margin-bottom:6px;">Biggest gap between model and bookie — where the market looks most wrong.</p>
      {% for bet in summary.best_value %}
      <div class="summary-row">
        <span class="summary-label">{{ bet.description }}<br><small style="color:#64748b">{{ bet.game_label }}</small></span>
        <span class="summary-odds">${{ bet.odds }}</span>
        <span class="summary-edge value-strong">+{{ (bet.edge * 100) | round(1) }}%</span>
      </div>
      {% endfor %}
    </div>

    <div class="card summary-section">
      <h3>Safest Bets (Highest Win %)</h3>
      <p style="color:var(--muted);font-size:0.74rem;margin-bottom:6px;">Most likely to win — conservative multi legs.</p>
      {% for bet in summary.best_win_rate %}
      <div class="summary-row">
        <span class="summary-label">{{ bet.description }}<br><small style="color:#64748b">{{ bet.game_label }}</small></span>
        <span class="summary-odds">${{ bet.odds }}</span>
        <span class="summary-edge value-ok">{{ (bet.model_prob * 100) | round | int }}%</span>
      </div>
      {% endfor %}
    </div>

  </div>

  <!-- LONGSHOT WATCH -->
  {% if summary.longshot_watch %}
  <h2>Longshot Watch — Value Underdogs ($3.50+)</h2>
  <p style="color:var(--muted);font-size:0.82rem;margin-bottom:12px;">Big-odds H2H bets where the model detects positive edge. High risk, high reward — the bookie may be underestimating the underdog. Review alongside the game analysis before betting.</p>
  {% for bet in summary.longshot_watch %}
  <div class="longshot-card">
    <div class="longshot-header">
      <span class="longshot-badge">LONGSHOT</span>
      <span class="longshot-title">{{ bet.team }} to win</span>
    </div>
    <div class="longshot-game" style="margin-bottom:8px;color:var(--muted)">{{ bet.game_label }}</div>
    <div class="longshot-row">
      <div>
        <div style="font-weight:700;font-size:1.05rem;color:#a78bfa">${{ bet.odds }}</div>
        <div class="longshot-meta">
          <span>Model: {{ (bet.model_prob * 100) | round(1) }}%</span>
          <span>Implied: {{ (bet.implied_prob * 100) | round(1) }}%</span>
          <span class="longshot-edge">Edge: +{{ (bet.edge * 100) | round(1) }}%</span>
          <span>Exp. return / $10: ${{ bet.exp_return }}</span>
        </div>
      </div>
      <div style="text-align:right">
        {% if bet.label %}<span class="label-badge badge-{% if bet.label == 'STRONG VALUE' %}strong{% elif bet.label == 'VALUE' %}value{% else %}marginal{% endif %}">{{ bet.label }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
  {% endif %}

  <!-- SUGGESTED MULTIS -->
  {% if multis %}
  <h2>Suggested Multis for the Week</h2>
  <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 24px;">

    {% if multis.safety %}
    <div class="card multi-card multi-safety">
      <div class="multi-title">🛡 Safety Multi</div>
      <div class="multi-legs">{% for leg in multis.safety.legs %}• {{ leg.description }}<br>{% endfor %}</div>
      <div class="multi-stats">
        <div class="multi-stat"><span class="multi-stat-label">Combined Odds</span><span class="multi-stat-value">${{ multis.safety.combined_odds }}</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Model Prob</span><span class="multi-stat-value">{{ multis.safety.combined_prob_pct }}</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Edge</span><span class="multi-stat-value {% if multis.safety.combined_edge > 0 %}value-ok{% else %}no-value{% endif %}">{{ "%+.1f" | format(multis.safety.combined_edge * 100) }}%</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Exp. Return / $10</span><span class="multi-stat-value">${{ multis.safety.exp_return }}</span></div>
      </div>
    </div>
    {% endif %}

    {% if multis.value %}
    <div class="card multi-card multi-value">
      <div class="multi-title">💎 Value Multi</div>
      <div class="multi-legs">{% for leg in multis.value.legs %}• {{ leg.description }}<br>{% endfor %}</div>
      <div class="multi-stats">
        <div class="multi-stat"><span class="multi-stat-label">Combined Odds</span><span class="multi-stat-value">${{ multis.value.combined_odds }}</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Model Prob</span><span class="multi-stat-value">{{ multis.value.combined_prob_pct }}</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Edge</span><span class="multi-stat-value {% if multis.value.combined_edge > 0 %}value-ok{% else %}no-value{% endif %}">{{ "%+.1f" | format(multis.value.combined_edge * 100) }}%</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Exp. Return / $10</span><span class="multi-stat-value">${{ multis.value.exp_return }}</span></div>
      </div>
    </div>
    {% endif %}

    {% if multis.best_of_week %}
    <div class="card multi-card multi-bow">
      <div class="multi-title">⭐ Best of Week Multi</div>
      <div class="multi-legs">{% for leg in multis.best_of_week.legs %}• {{ leg.description }}<br>{% endfor %}</div>
      <div class="multi-stats">
        <div class="multi-stat"><span class="multi-stat-label">Combined Odds</span><span class="multi-stat-value">${{ multis.best_of_week.combined_odds }}</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Model Prob</span><span class="multi-stat-value">{{ multis.best_of_week.combined_prob_pct }}</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Edge</span><span class="multi-stat-value {% if multis.best_of_week.combined_edge > 0 %}value-ok{% else %}no-value{% endif %}">{{ "%+.1f" | format(multis.best_of_week.combined_edge * 100) }}%</span></div>
        <div class="multi-stat"><span class="multi-stat-label">Exp. Return / $10</span><span class="multi-stat-value">${{ multis.best_of_week.exp_return }}</span></div>
      </div>
    </div>
    {% endif %}

  </div>
  {% endif %}
  {% endif %}

  <!-- GAME BY GAME -->
  <h2>Game by Game Analysis</h2>

  {% for game in games %}
  <div class="card">
    <div class="game-header">
      <div>
        <div class="game-title">{{ game.home_team }} vs {{ game.away_team }}</div>
        <div class="kickoff">Kickoff: {{ game.kickoff_fmt }}</div>
      </div>
    </div>

    <!-- 13+ MARGIN ALERT — shown at top of card so it's not missed -->
    {% if game.big_win_alert %}
    <div style="background:#1c1a10;border:1px solid #b45309;border-radius:8px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
      <span style="background:#78350f;color:#fcd34d;font-size:0.7rem;font-weight:700;padding:2px 8px;border-radius:4px;">13+ MARGIN ALERT</span>
      <span style="font-size:0.85rem;"><strong>{{ game.big_win_alert.team }}</strong> are priced at <strong>{{ "%+.1f" | format(game.big_win_alert.line) }}</strong> — big line game. Check the <em>winning margin 13+</em> market on Sportsbet/TAB. May pay better than H2H if you expect a blowout.</span>
    </div>
    {% endif %}

    <!-- H2H -->
    {% if game.h2h_bets %}
    <h3>Head-to-Head</h3>
    <table>
      <tr><th>Team</th><th>Odds</th><th>Implied</th><th>Model</th><th>Edge</th><th>Success Rate</th><th>Exp. Return/$10</th></tr>
      {% for bet in game.h2h_bets %}
      <tr>
        <td>{{ bet.team }}</td>
        <td>${{ bet.odds }}</td>
        <td>{{ (bet.implied_prob * 100) | round(1) }}%</td>
        <td>{{ (bet.model_prob * 100) | round(1) }}%</td>
        <td class="{% if bet.edge > 0.10 %}value-strong{% elif bet.edge > 0.03 %}value-ok{% elif bet.edge > 0 %}value-marginal{% else %}no-value{% endif %}">
          {{ "%+.1f" | format(bet.edge * 100) }}%
          {% if bet.edge > 0 %}<span class="tick">✅</span>{% else %}<span class="cross">❌</span>{% endif %}
          {% if bet.label %}<span class="label-badge badge-{% if bet.label == 'STRONG VALUE' %}strong{% elif bet.label == 'VALUE' %}value{% else %}marginal{% endif %}">{{ bet.label }}</span>{% endif %}
        </td>
        <td>{{ bet.success_rate }}</td>
        <td>${{ bet.exp_return }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}

    <!-- SPREADS -->
    {% if game.spread_bets %}
    <h3>Line / Handicap</h3>
    <table>
      <tr><th>Team</th><th>Line</th><th>Odds</th><th>Implied</th><th>Model</th><th>Edge</th><th>Success Rate</th></tr>
      {% for bet in game.spread_bets %}
      <tr>
        <td>{{ bet.team }}</td>
        <td>{{ "%+.1f" | format(bet.point) }}</td>
        <td>${{ bet.odds }}</td>
        <td>{{ (bet.implied_prob * 100) | round(1) }}%</td>
        <td>{{ (bet.model_prob * 100) | round(1) }}%</td>
        <td class="{% if bet.edge > 0.10 %}value-strong{% elif bet.edge > 0.03 %}value-ok{% elif bet.edge > 0 %}value-marginal{% else %}no-value{% endif %}">
          {{ "%+.1f" | format(bet.edge * 100) }}%
          {% if bet.edge > 0 %}<span class="tick">✅</span>{% else %}<span class="cross">❌</span>{% endif %}
          {% if bet.label %}<span class="label-badge badge-{% if bet.label == 'STRONG VALUE' %}strong{% elif bet.label == 'VALUE' %}value{% else %}marginal{% endif %}">{{ bet.label }}</span>{% endif %}
        </td>
        <td>{{ bet.success_rate }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}

    <!-- ATS -->
    {% if game.ats_picks %}
    <h3>Anytime Try Scorer — Top Value Picks</h3>
    <table>
      <tr><th>Player</th><th>Position</th><th>Try Rate</th><th>Odds</th><th>Implied</th><th>Model</th><th>Edge</th><th>Success Rate</th><th>Exp. Return/$10</th></tr>
      {% for pick in game.ats_picks %}
      <tr>
        <td>{{ pick.player }}</td>
        <td>{{ pick.position }}</td>
        <td>{{ pick.try_rate }}/g</td>
        <td>${{ pick.odds }}</td>
        <td>{{ (pick.implied_prob * 100) | round(1) }}%</td>
        <td>{{ (pick.model_prob * 100) | round(1) }}%</td>
        <td class="{% if pick.edge > 0.10 %}value-strong{% elif pick.edge > 0.03 %}value-ok{% else %}value-marginal{% endif %}">
          {{ "%+.1f" | format(pick.edge * 100) }}% <span class="tick">✅</span>
          {% if pick.label %}<span class="label-badge badge-{% if pick.label == 'STRONG VALUE' %}strong{% elif pick.label == 'VALUE' %}value{% else %}marginal{% endif %}">{{ pick.label }}</span>{% endif %}
        </td>
        <td>{{ pick.success_rate }}</td>
        <td>${{ pick.exp_return }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}

    <!-- FTS -->
    {% if game.fts_picks %}
    <h3>First Try Scorer — Top Value Picks</h3>
    <table>
      <tr><th>Player</th><th>Position</th><th>Model Prob</th><th>Odds</th><th>Implied</th><th>Edge</th><th>Success Rate</th><th>Exp. Return/$10</th></tr>
      {% for pick in game.fts_picks %}
      <tr>
        <td>{{ pick.player }}</td>
        <td>{{ pick.position }}</td>
        <td>{{ (pick.model_prob * 100) | round(1) }}%</td>
        <td>${{ pick.odds }}</td>
        <td>{{ (pick.implied_prob * 100) | round(1) }}%</td>
        <td class="{% if pick.edge > 0.10 %}value-strong{% elif pick.edge > 0.03 %}value-ok{% else %}value-marginal{% endif %}">
          {{ "%+.1f" | format(pick.edge * 100) }}% <span class="tick">✅</span>
          {% if pick.label %}<span class="label-badge badge-{% if pick.label == 'STRONG VALUE' %}strong{% elif pick.label == 'VALUE' %}value{% else %}marginal{% endif %}">{{ pick.label }}</span>{% endif %}
        </td>
        <td>{{ pick.success_rate }}</td>
        <td>${{ pick.exp_return }}</td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}

    <!-- BEST BET -->
    {% if game.best_bet %}
    <div class="best-bet">
      <div class="best-bet-label">★ Best Bet This Game</div>
      <div class="best-bet-title">{{ game.best_bet.description }} — ${{ game.best_bet.odds }}</div>
      <div class="best-bet-meta">
        <span>{% if game.best_bet.label %}<span class="label-badge badge-{% if game.best_bet.label == 'STRONG VALUE' %}strong{% elif game.best_bet.label == 'VALUE' %}value{% else %}marginal{% endif %}">{{ game.best_bet.label }}</span>{% endif %}</span>
        <span>Success rate: {{ game.best_bet.success_rate }}</span>
        <span>Exp. return / $10: ${{ game.best_bet.exp_return }}</span>
        <span>Edge: {{ "%+.1f" | format(game.best_bet.edge * 100) }}%</span>
      </div>
    </div>
    {% endif %}

  </div>
  {% else %}
  <div class="card" style="text-align:center; color: var(--muted); padding: 40px;">
    No upcoming NRL games found — check back closer to the round, or verify your API keys in config.py.
  </div>
  {% endfor %}

  <div style="text-align:center; color: var(--muted); font-size: 0.75rem; margin-top: 24px; padding-bottom: 24px;">
    Odds sourced from Australian bookmakers via The Odds API. Stats from nrl.com.<br>
    Bet365 player prop odds not available via free API — compare displayed prices to your Bet365 app.<br>
    This is a statistical model, not financial advice. Gamble responsibly.<br><br>
    <a href="https://siktvp.github.io/nrl-tipsheet/tipsheet_output.html" style="color: var(--accent);">
      📱 siktvp.github.io/nrl-tipsheet
    </a>
  </div>

</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Step 11: Format kickoff time
# ---------------------------------------------------------------------------

def fmt_kickoff(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # Convert UTC to AEST (UTC+10)
        from datetime import timedelta
        aest = dt + timedelta(hours=10)
        return aest.strftime("%A %d %b, %I:%M %p AEST")
    except Exception:
        return iso_str or "TBC"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_completed_round_scores(round_number, season):
    """
    Fetch final scores for all completed games in a given NRL round.
    Returns list of {home_team, away_team, home_score, away_score}.
    """
    scores = []
    try:
        data = nrl_get(
            "https://www.nrl.com/draw/data",
            {"competition": NRL_COMPETITION_ID, "season": season, "round": round_number},
        )
        for f in data.get("fixtures", []):
            if f.get("matchState") not in ("FullTime", "Post", "Final"):
                continue
            home_obj = f.get("homeTeam", {})
            away_obj = f.get("awayTeam", {})
            home_nick = home_obj.get("nickName") or home_obj.get("teamNickname", "")
            away_nick = away_obj.get("nickName") or away_obj.get("teamNickname", "")
            home_score = (home_obj.get("score") or home_obj.get("teamScore")
                          or f.get("homeScore") or f.get("homeTeamScore"))
            away_score = (away_obj.get("score") or away_obj.get("teamScore")
                          or f.get("awayScore") or f.get("awayTeamScore"))
            if home_nick and away_nick and home_score is not None and away_score is not None:
                scores.append({
                    "home_team": home_nick,
                    "away_team": away_nick,
                    "home_score": int(home_score),
                    "away_score": int(away_score),
                })
    except Exception as e:
        print(f"  WARNING: Could not fetch scores for round {round_number}: {e}")
    return scores


def main():
    print("NRL Tip Sheet Generator")
    print("=" * 40)

    print("Loading bet history...")
    db = load_db()
    bankroll = get_current_bankroll(db)
    print(f"  Current bankroll: ${bankroll:.2f}")

    print("Detecting upcoming round number...")
    round_number = fetch_upcoming_round_number(db)
    print(f"  Upcoming round: {round_number}")

    print("Fetching team stats from nrl.com...")
    team_stats = fetch_team_stats()
    print(f"  Found stats for {len(team_stats)} teams")

    print("Fetching team form stats from nrl.com...")
    team_form = fetch_team_form_stats()

    print("Fetching upcoming NRL fixtures + odds...")
    fixtures = fetch_fixtures_with_odds()
    print(f"  Found {len(fixtures)} upcoming games")

    if not fixtures:
        print("No fixtures found — generating tip sheet with no-games message.")

    print("Fetching player try stats from nrl.com...")
    all_player_stats = fetch_all_player_try_stats()

    print("Analysing games...")
    games_analysis = []
    for game in fixtures:
        print(f"  Analysing: {game['home_team']} vs {game['away_team']}")
        analysis = analyse_game(game, team_stats, team_form, all_player_stats)
        analysis["kickoff_fmt"] = fmt_kickoff(game.get("kickoff", ""))
        games_analysis.append(analysis)

    print("Building round summary and multis...")
    summary = build_round_summary(games_analysis) if games_analysis else None
    multis = build_multis(summary, games_analysis) if summary else {}

    round_label = f"Round {round_number} — {datetime.now().strftime('%d %b %Y')}"
    generated_at = datetime.now().strftime("%d %b %Y %I:%M %p")

    # Auto-resolve results for any previous rounds that have completed games
    print("Auto-resolving previous round results from NRL.com...")
    season = datetime.now().year
    resolved_any = False
    for rnd in db.get("rounds", []):
        rr = rnd.get("round_result", {})
        already_resolved = rr.get("resolved_at") is not None
        if already_resolved:
            continue
        has_pending = any(g.get("won") is None for g in rnd.get("games", []) if g.get("recommended_bet"))
        if not has_pending:
            continue
        prev_round_num = rnd.get("round_number")
        if not prev_round_num or prev_round_num >= round_number:
            continue
        scores = fetch_completed_round_scores(prev_round_num, season)
        if scores:
            n = auto_resolve_from_scores(db, rnd["round_id"], scores)
            if n:
                resolved_any = True
    if not resolved_any:
        print("  No pending rounds to resolve.")

    print("Saving round predictions to history_db.json...")
    save_round_predictions(db, season, round_number, games_analysis, bankroll)
    save_db(db)

    print("Rendering HTML...")
    template = Template(HTML_TEMPLATE)
    html = template.render(
        games=games_analysis,
        summary=summary,
        multis=multis,
        round_label=round_label,
        generated_at=generated_at,
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nDone! Open {OUTPUT_FILE} in your browser.")
    print(f"Or visit: https://siktvp.github.io/nrl-tipsheet/tipsheet_output.html")


if __name__ == "__main__":
    main()

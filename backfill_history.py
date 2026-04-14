"""
Backfill history_db.json with Round 5 and Round 6 data recovered from
old tipsheet HTML files and actual NRL scores from the draw API.
"""
import json
from datetime import datetime, timezone

FLAT_STAKE = 10.0

# ─── Round 5 (March 30 tipsheet, games April 3-6) ───────────────────────────
# Actual scores from NRL API round 5
# home_score, away_score
R5_SCORES = {
    "Dolphins_SeaEagles":   (18, 52),
    "Rabbitohs_Bulldogs":   (32, 24),
    "Panthers_Storm":       (50, 10),
    "Dragons_Cowboys":      (0,  32),
    "Titans_Broncos":       (12, 26),
    "Sharks_Warriors":      (36, 22),
    "Knights_Raiders":      (32, 12),
    "Eels_Tigers":          (20, 22),
}

# Model picks from March 30 tipsheet
# (game_id, home, away, bet_type, team, desc, odds, point, home_score, away_score)
R5_GAMES = [
    {
        "game_id": "Dolphins-Manly Warringah Sea Eagles",
        "home_team": "Dolphins",
        "away_team": "Manly Warringah Sea Eagles",
        "kickoff": "2026-04-03T09:00:00Z",  # approx
        "bet_type": "H2H",
        "team": "Manly Warringah Sea Eagles",
        "description": "Manly Warringah Sea Eagles to win",
        "odds": 3.25,
        "point": None,
        "home_score": 18,
        "away_score": 52,
        # Away team won → bet on away (Manly) → WIN
        "won": True,
    },
    {
        "game_id": "South Sydney Rabbitohs-Canterbury Bulldogs",
        "home_team": "South Sydney Rabbitohs",
        "away_team": "Canterbury Bulldogs",
        "kickoff": "2026-04-04T09:30:00Z",
        "bet_type": "Line",
        "team": "South Sydney Rabbitohs",
        "description": "South Sydney Rabbitohs +1.5",
        "odds": 1.85,
        "point": 1.5,
        "home_score": 32,
        "away_score": 24,
        # Rabbitohs home +1.5: home_score + 1.5 vs away. 32+1.5=33.5 > 24 → WIN
        "won": True,
    },
    {
        "game_id": "Penrith Panthers-Melbourne Storm",
        "home_team": "Penrith Panthers",
        "away_team": "Melbourne Storm",
        "kickoff": "2026-04-04T11:35:00Z",
        "bet_type": "Line",
        "team": "Penrith Panthers",
        "description": "Penrith Panthers -7.5",
        "odds": 1.89,
        "point": -7.5,
        "home_score": 50,
        "away_score": 10,
        # Panthers home -7.5: need to win by > 7.5. Won by 40 → WIN
        "won": True,
    },
    {
        "game_id": "St George Illawarra Dragons-North Queensland Cowboys",
        "home_team": "St George Illawarra Dragons",
        "away_team": "North Queensland Cowboys",
        "kickoff": "2026-04-05T09:50:00Z",
        "bet_type": "H2H",
        "team": "North Queensland Cowboys",
        "description": "North Queensland Cowboys to win",
        "odds": 1.83,
        "point": None,
        "home_score": 0,
        "away_score": 32,
        # Away Cowboys won 32-0 → WIN
        "won": True,
    },
    {
        "game_id": "Gold Coast Titans-Brisbane Broncos",
        "home_team": "Gold Coast Titans",
        "away_team": "Brisbane Broncos",
        "kickoff": "2026-04-05T11:30:00Z",
        "bet_type": "Line",
        "team": "Gold Coast Titans",
        "description": "Gold Coast Titans +13.5",
        "odds": 1.93,
        "point": 13.5,
        "home_score": 12,
        "away_score": 26,
        # Titans home +13.5: 12+13.5=25.5 vs 26 → 25.5 < 26 → LOSS
        "won": False,
    },
    {
        "game_id": "Cronulla Sutherland Sharks-New Zealand Warriors",
        "home_team": "Cronulla Sutherland Sharks",
        "away_team": "New Zealand Warriors",
        "kickoff": "2026-04-05T09:00:00Z",
        "bet_type": "Line",
        "team": "New Zealand Warriors",
        "description": "New Zealand Warriors +3.5",
        "odds": 1.91,
        "point": 3.5,
        "home_score": 36,
        "away_score": 22,
        # Warriors away +3.5: 22+3.5=25.5 vs 36 → 25.5 < 36 → LOSS
        "won": False,
    },
    {
        "game_id": "Newcastle Knights-Canberra Raiders",
        "home_team": "Newcastle Knights",
        "away_team": "Canberra Raiders",
        "kickoff": "2026-04-06T04:00:00Z",
        "bet_type": "Line",
        "team": "Newcastle Knights",
        "description": "Newcastle Knights +3.5",
        "odds": 1.90,
        "point": 3.5,
        "home_score": 32,
        "away_score": 12,
        # Knights home +3.5: 32+3.5=35.5 vs 12 → WIN (they won outright)
        "won": True,
    },
    {
        "game_id": "Parramatta Eels-Wests Tigers",
        "home_team": "Parramatta Eels",
        "away_team": "Wests Tigers",
        "kickoff": "2026-04-06T06:00:00Z",
        "bet_type": "Line",
        "team": "Wests Tigers",
        "description": "Wests Tigers +3.5",
        "odds": 1.88,
        "point": 3.5,
        "home_score": 20,
        "away_score": 22,
        # Tigers away +3.5: 22+3.5=25.5 vs 20 → WIN (they won outright)
        "won": True,
    },
]

# ─── Round 6 (April 6 tipsheet, games April 10-13) ──────────────────────────
R6_GAMES = [
    {
        "game_id": "Canterbury Bulldogs-Penrith Panthers",
        "home_team": "Canterbury Bulldogs",
        "away_team": "Penrith Panthers",
        "kickoff": "2026-04-09T09:50:00Z",
        "bet_type": "Line",
        "team": "Penrith Panthers",
        "description": "Penrith Panthers -16.5",
        "odds": 1.89,
        "point": -16.5,
        "home_score": 32,
        "away_score": 16,
        # Panthers away -16.5: need to win by >16.5. Panthers LOST 16-32 → LOSS
        "won": False,
    },
    {
        "game_id": "St George Illawarra Dragons-Manly Warringah Sea Eagles",
        "home_team": "St George Illawarra Dragons",
        "away_team": "Manly Warringah Sea Eagles",
        "kickoff": "2026-04-10T08:00:00Z",
        "bet_type": "H2H",
        "team": "Manly Warringah Sea Eagles",
        "description": "Manly Warringah Sea Eagles to win",
        "odds": 1.56,
        "point": None,
        "home_score": 18,
        "away_score": 28,
        # Manly won 28-18 → WIN
        "won": True,
    },
    {
        "game_id": "Brisbane Broncos-North Queensland Cowboys",
        "home_team": "Brisbane Broncos",
        "away_team": "North Queensland Cowboys",
        "kickoff": "2026-04-10T10:00:00Z",
        "bet_type": "H2H",
        "team": "Brisbane Broncos",
        "description": "Brisbane Broncos to win",
        "odds": 1.73,
        "point": None,
        "home_score": 31,
        "away_score": 35,
        # Broncos lost 31-35 → LOSS
        "won": False,
    },
    {
        "game_id": "South Sydney Rabbitohs-Canberra Raiders",
        "home_team": "South Sydney Rabbitohs",
        "away_team": "Canberra Raiders",
        "kickoff": "2026-04-11T07:30:00Z",
        "bet_type": "Line",
        "team": "South Sydney Rabbitohs",
        "description": "South Sydney Rabbitohs -6.5",
        "odds": 1.89,
        "point": -6.5,
        "home_score": 34,
        "away_score": 36,
        # Rabbitohs -6.5: need to win by >6.5. They LOST 34-36 → LOSS
        "won": False,
    },
    {
        "game_id": "Cronulla Sutherland Sharks-Sydney Roosters",
        "home_team": "Cronulla Sutherland Sharks",
        "away_team": "Sydney Roosters",
        "kickoff": "2026-04-11T09:35:00Z",
        "bet_type": "Line",
        "team": "Cronulla Sutherland Sharks",
        "description": "Cronulla Sutherland Sharks +0.5",
        "odds": 1.90,
        "point": 0.5,
        "home_score": 22,
        "away_score": 34,
        # Sharks +0.5: 22+0.5=22.5 vs 34 → LOSS
        "won": False,
    },
    {
        "game_id": "Melbourne Storm-New Zealand Warriors",
        "home_team": "Melbourne Storm",
        "away_team": "New Zealand Warriors",
        "kickoff": "2026-04-12T04:00:00Z",
        "bet_type": "Line",
        "team": "New Zealand Warriors",
        "description": "New Zealand Warriors +6.5",
        "odds": 1.89,
        "point": 6.5,
        "home_score": 14,
        "away_score": 38,
        # Warriors away +6.5: 38+6.5=44.5 vs 14 → WIN (won outright)
        "won": True,
    },
    {
        "game_id": "Parramatta Eels-Gold Coast Titans",
        "home_team": "Parramatta Eels",
        "away_team": "Gold Coast Titans",
        "kickoff": "2026-04-12T06:00:00Z",
        "bet_type": "H2H",
        "team": "Parramatta Eels",
        "description": "Parramatta Eels to win",
        "odds": 1.57,
        "point": None,
        "home_score": 10,
        "away_score": 52,
        # Eels lost 10-52 → LOSS
        "won": False,
    },
    {
        "game_id": "Wests Tigers-Newcastle Knights",
        "home_team": "Wests Tigers",
        "away_team": "Newcastle Knights",
        "kickoff": "2026-04-13T04:00:00Z",
        "bet_type": "Line",
        "team": "Wests Tigers",
        "description": "Wests Tigers -2.5",
        "odds": 1.90,
        "point": -2.5,
        "home_score": 42,
        "away_score": 22,
        # Tigers -2.5: won by 20 → WIN
        "won": True,
    },
]


def build_game_record(g):
    """Build a history_db game entry from a resolved game dict."""
    won = g["won"]
    odds = g["odds"]
    payout = round(odds * FLAT_STAKE, 2) if won else 0.0
    result = "WIN" if won else "LOSS"

    rec_bet = {
        "bet_type": g["bet_type"],
        "team": g["team"],
        "description": g["description"],
        "odds": odds,
        "model_prob": None,
        "implied_prob": None,
        "edge": None,
        "label": None,
        "value_score": None,
        "point": g["point"],
    }

    return {
        "game_id": g["game_id"],
        "home_team": g["home_team"],
        "away_team": g["away_team"],
        "kickoff": g["kickoff"],
        "recommended_bet": rec_bet,
        "h2h_bets": [],
        "spread_bets": [],
        "ats_picks": [],
        "fts_picks": [],
        "stake": FLAT_STAKE,
        "result": result,
        "won": won,
        "payout": payout,
    }


def build_round(round_id, season, round_number, generated_at, bankroll_start, games_data, resolved_at):
    games = [build_game_record(g) for g in games_data]
    wins = sum(1 for g in games_data if g["won"])
    losses = len(games_data) - wins
    total_staked = FLAT_STAKE * len(games_data)
    total_returned = sum(
        round(g["odds"] * FLAT_STAKE, 2) if g["won"] else 0.0
        for g in games_data
    )
    net_profit = round(total_returned - total_staked, 2)
    bankroll_end = round(bankroll_start + net_profit, 2)

    return {
        "round_id": round_id,
        "season": season,
        "round_number": round_number,
        "generated_at": generated_at,
        "bankroll_start": bankroll_start,
        "stake_per_game": FLAT_STAKE,
        "strategy": "flat_compounding",
        "games": games,
        "round_result": {
            "resolved_at": resolved_at,
            "wins": wins,
            "losses": losses,
            "total_staked": total_staked,
            "total_returned": round(total_returned, 2),
            "net_profit": net_profit,
            "bankroll_end": bankroll_end,
        },
    }


def recompute_lifetime(rounds):
    """Recompute lifetime stats from all resolved rounds."""
    total_rounds = 0
    total_bets = 0
    total_wins = 0
    total_staked = 0.0
    total_returned = 0.0
    bankroll_history = []

    by_label = {k: {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0}
                for k in ("STRONG VALUE", "VALUE", "MARGINAL")}
    by_type = {k: {"bets": 0, "wins": 0, "staked": 0.0, "returned": 0.0}
               for k in ("H2H", "Line", "ATS", "FTS")}
    calibration = [{"bucket": f"{i*10}-{(i+1)*10}%", "bets": 0, "wins": 0} for i in range(10)]

    for rnd in sorted(rounds, key=lambda r: r["generated_at"]):
        rr = rnd.get("round_result", {})
        if rr.get("wins") is None:
            continue
        total_rounds += 1
        total_staked += rr["total_staked"]
        total_returned += rr["total_returned"]
        net = rr["net_profit"]
        bankroll_history.append({
            "round_id": rnd["round_id"],
            "bankroll": rr["bankroll_end"],
            "net_profit": net,
        })
        for g in rnd.get("games", []):
            if g.get("won") is None:
                continue
            total_bets += 1
            if g["won"]:
                total_wins += 1
            stake = g.get("stake", FLAT_STAKE)
            payout = g.get("payout", 0) or 0
            bet = g.get("recommended_bet", {})
            label = bet.get("label")
            btype = bet.get("bet_type", "H2H")
            if label in by_label:
                by_label[label]["bets"] += 1
                by_label[label]["staked"] += stake
                if g["won"]:
                    by_label[label]["wins"] += 1
                    by_label[label]["returned"] += payout
            if btype in by_type:
                by_type[btype]["bets"] += 1
                by_type[btype]["staked"] += stake
                if g["won"]:
                    by_type[btype]["wins"] += 1
                    by_type[btype]["returned"] += payout

    net_profit = round(total_returned - total_staked, 2)
    roi_pct = round(net_profit / total_staked * 100, 1) if total_staked else 0.0
    win_rate = round(total_wins / total_bets * 100, 1) if total_bets else 0.0

    return {
        "total_rounds": total_rounds,
        "total_bets": total_bets,
        "total_wins": total_wins,
        "total_staked": round(total_staked, 2),
        "total_returned": round(total_returned, 2),
        "net_profit": net_profit,
        "roi_pct": roi_pct,
        "win_rate": win_rate,
        "by_label": by_label,
        "by_bet_type": by_type,
        "model_calibration": calibration,
        "bankroll_history": bankroll_history,
    }


# ─── Load existing DB and patch ─────────────────────────────────────────────
with open("history_db.json", encoding="utf-8") as f:
    db = json.load(f)

existing_ids = {r["round_id"] for r in db["rounds"]}

# Fix the current pending round: R27 should be R07 (NRL round 7 = April 16-19)
for rnd in db["rounds"]:
    if rnd["round_id"] == "2026-R27":
        rnd["round_id"] = "2026-R07"
        rnd["round_number"] = 7
        print("Fixed 2026-R27 -> 2026-R07")
        break

# Remove the old dummy R07 entry (the one with empty games and manual results)
db["rounds"] = [r for r in db["rounds"] if not (r["round_id"] == "2026-R07" and r["games"] == [] and r["round_result"]["wins"] == 6)]

# Build backfill rounds
# Starting bankroll = $80 (project starting bankroll)
r5 = build_round(
    round_id="2026-R05",
    season=2026,
    round_number=5,
    generated_at="2026-03-30T22:29:00Z",
    bankroll_start=80.0,
    games_data=R5_GAMES,
    resolved_at="2026-04-06T22:00:00Z",
)

r5_end = r5["round_result"]["bankroll_end"]

r6 = build_round(
    round_id="2026-R06",
    season=2026,
    round_number=6,
    generated_at="2026-04-06T22:30:00Z",
    bankroll_start=r5_end,
    games_data=R6_GAMES,
    resolved_at="2026-04-13T22:00:00Z",
)

r6_end = r6["round_result"]["bankroll_end"]

# Update bankroll_start for the current pending round (R07 upcoming)
for rnd in db["rounds"]:
    if rnd["round_id"] == "2026-R07" and rnd["round_result"]["wins"] is None:
        rnd["bankroll_start"] = r6_end
        rnd["stake_per_game"] = 10.0
        print(f"Updated R07 bankroll_start to ${r6_end}")

# Insert backfill rounds (sorted by round number)
db["rounds"] = [r5, r6] + db["rounds"]
db["rounds"].sort(key=lambda r: r["generated_at"])

# Recompute lifetime
db["lifetime"] = recompute_lifetime(db["rounds"])
db["meta"]["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Print summary
print()
print("=== Round 5 (Apr 3-6) ===")
rr5 = r5["round_result"]
print(f"  {rr5['wins']}W {rr5['losses']}L | staked=${rr5['total_staked']} returned=${rr5['total_returned']} profit=${rr5['net_profit']}")
for g in r5["games"]:
    print(f"  {'WIN' if g['won'] else 'LOSS'} | {g['recommended_bet']['description']} @ ${g['recommended_bet']['odds']} | payout=${g['payout']}")

print()
print("=== Round 6 (Apr 10-13) ===")
rr6 = r6["round_result"]
print(f"  {rr6['wins']}W {rr6['losses']}L | staked=${rr6['total_staked']} returned=${rr6['total_returned']} profit=${rr6['net_profit']}")
for g in r6["games"]:
    print(f"  {'WIN' if g['won'] else 'LOSS'} | {g['recommended_bet']['description']} @ ${g['recommended_bet']['odds']} | payout=${g['payout']}")

print()
print(f"=== Lifetime ===")
lt = db["lifetime"]
print(f"  {lt['total_rounds']} rounds | {lt['total_bets']} bets | {lt['total_wins']}W | ROI {lt['roi_pct']}% | Net ${lt['net_profit']}")

# Save
with open("history_db.json", "w", encoding="utf-8") as f:
    json.dump(db, f, indent=2)
print()
print("history_db.json saved.")

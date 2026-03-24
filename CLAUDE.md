# NRL Tipsheet — Project Context

## What It Does
Generates a weekly NRL betting tipsheet as a static HTML file. Fetches live odds from The Odds API, team/player stats from nrl.com, runs statistical models to find value bets, and outputs `tipsheet_output.html`. Hosted on GitHub Pages, auto-regenerated every Tuesday via GitHub Actions.

## Stack
- **Python 3.12** — main language
- **requests** — HTTP calls
- **jinja2** — HTML templating (template is inline in `nrl_tipsheet.py`)
- **math** (stdlib) — normal distribution for spread model
- **GitHub Actions** — runs Monday 10pm UTC = Tuesday 8am AEST, auto-commits HTML
- **GitHub Pages** — serves the generated HTML

## APIs Used
| API | Purpose | Auth |
|-----|---------|------|
| [The Odds API](https://the-odds-api.com) | Bookmaker odds (H2H, spreads, try scorer props) | `ODDS_API_KEY` env var / `config.py` |
| `nrl.com/ladder/data` | Season ladder (wins, losses, margins) | None (free) |
| `nrl.com/draw/data` | Round fixtures, match states, scores | None (free) |
| `nrl.com{matchCentreUrl}data` | Per-match player data + try timeline | None (free) |

## Key Files
| File | Purpose |
|------|---------|
| `nrl_tipsheet.py` | All logic: data fetching, models, HTML rendering |
| `.github/workflows/generate_tipsheet.yml` | CI/CD — cron schedule + auto-commit |
| `tipsheet_output.html` | Generated output (do not edit manually) |
| `config.py` | Local API key fallback (gitignored, but currently committed — see known issues) |
| `requirements.txt` | Pinned deps: requests, jinja2, beautifulsoup4 |

## Architecture — Key Functions in `nrl_tipsheet.py`
```
main()
├── fetch_team_stats()            # NRL ladder → season win%, avg_margin, etc.
├── fetch_team_form_stats()       # NRL draw → home/away splits, last-5 form
├── fetch_fixtures_with_odds()    # Odds API → upcoming games + H2H/spread odds
├── fetch_all_player_try_stats()  # NRL match centres → per-player try_rate + recent_try_rate
└── for each game:
    └── analyse_game()
        ├── model_h2h_prob()      # Blends home/away win% + recent form rating
        ├── model_spread_prob()   # Blends season + recent avg_margin, normal dist
        ├── fetch_try_scorer_odds()  # Odds API → ATS/FTS odds for this game
        ├── model_ats_prob()      # Blends season + recent try_rate
        └── model_fts_prob()      # try_rate × positional factor (backs score first more)
```

## Model Logic
- **H2H**: `0.5 * home_win_pct_at_home + 0.5 * home_recent_form` vs same for away. Home advantage is implicit in home_win_pct (real data), not a fixed bonus.
- **Spread**: `0.6 * season_avg_margin + 0.4 * recent_avg_margin`, with home advantage derived from actual home margin differential. Normal distribution (σ=14).
- **ATS**: `0.6 * season_try_rate + 0.4 * recent_try_rate` (last 4 games), capped at 0.95.
- **FTS**: ATS blended rate × positional factor (Fullback=0.32 … Prop=0.08), capped at 0.50.
- **Edge**: `model_prob - bookie_implied_prob`. Labels: STRONG VALUE (>8%), VALUE (>4%), MARGINAL (>0%).
- **Recent form weighting**: last 5 games, weights [1,2,3,4,5] oldest→newest.

## NRL.com API Notes
- Competition ID for NRL: `111`
- Draw API: `GET https://www.nrl.com/draw/data?competition=111&season={year}&round={n}`
- Fixture score fields (completed games): `homeTeam.score` / `awayTeam.score`
- Match centre URL from fixture: `fixture["matchCentreUrl"]` → append `data` → full endpoint
- Match states for completed games: `"FullTime"`, `"Post"`, `"Final"`

## Known Issues
1. `config.py` with hardcoded `ODDS_API_KEY` is gitignored but was committed in the initial commit — consider rotating the key.
2. `beautifulsoup4` is in requirements but never used in code.
3. Round number in the page header uses current date, not the actual NRL round number.

## GitHub Actions
- Cron: `0 22 * * 1` (Monday 10pm UTC = Tuesday 8am AEST)
- `workflow_dispatch` available for manual runs from the Actions tab (works from phone)
- Secrets: `ODDS_API_KEY` must be set in repo Settings → Secrets → Actions

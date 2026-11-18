# Turf Grade

Start/sit decisions for fantasy football, settled by the numbers. Compare two players side by side and get a verdict.

## How the grade works

Each player is ranked against everyone at their position with at least 4 games this season, on:

| Factor | Weight |
|---|---|
| Season average (fantasy points per game) | 40% |
| Usage (share of the team's plays; kickers: field-goal attempts; defenses: sacks plus takeaways) | 30% |
| Recent form (last 3 games) | 20% |
| Matchup | 10%, added once a licensed schedule source is in place |

Scoring follows ESPN's defaults in PPR, half-PPR and standard.

## Data

Player stats come from [nflverse](https://github.com/nflverse), licensed CC BY 4.0. Only the stats file is used: no schedules, images, logos or betting data. Fantasy points for kickers and team defenses are calculated here from those stats.

`pipeline/build_data.py` downloads the stats and writes `site/data/grades.json`. A GitHub Action (`.github/workflows/refresh-data.yml`) runs it after the Sunday, Monday and Thursday night games during the season. You can also run it by hand from the repository's **Actions** tab.

Unofficial. Not affiliated with the NFL, its teams or nflverse.

"""
NAQT Scoresheet CSV to QBJ Converter
=====================================
Converts filled NAQT scoresheet CSV files (exported from Google Sheets)
to QBJ format (v2.1.1) compatible with YellowFruit.

CSV Column Layout:
  Left Team:  cols 1-8 (players), 9-11 (bonus parts), 12 (bonus pts), 13 (q total), 14 (cuml score)
  TU Number:  col 15
  Right Team: cols 16-23 (players), 24-26 (bonus parts), 27 (bonus pts), 28 (q total), 29 (cuml score)
  Issues:     col 30

Row Layout:
  Row 0: Event name / Version
  Row 1: Round / Moderator / Room
  Row 2: Team names / "TU Number"
  Row 3: Player names / header labels
  Rows 4-33: Running scores for TU 1-30
  Row 34: Final Score
  Row 35: TUH (tossups heard per player)
  Row 36: No. of 15s per player
  Row 37: No. of 10s per player
  Row 38: No. of -5s per player
  Row 39: TU Points per player
  Row 40: Subtotals

Usage:
    python naqt_csv_to_qbj.py scoresheet.csv [output.qbj]
    python naqt_csv_to_qbj.py game1.csv game2.csv game3.csv  (multiple games → one tournament)
"""

import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional


# ─── Column Constants ─────────────────────────────────────────────────────────

LEFT_PLAYER_COLS = list(range(1, 9))       # cols 1-8  (up to 8 players)
LEFT_BONUS_PARTS_COLS = [9, 10, 11]        # cols 9-11
LEFT_BONUS_PTS_COL = 12                    # col 12
LEFT_Q_TOTAL_COL = 13                      # col 13
LEFT_CUML_SCORE_COL = 14                   # col 14

TU_NUMBER_COL = 15                          # col 15

RIGHT_PLAYER_COLS = list(range(16, 24))    # cols 16-23 (up to 8 players)
RIGHT_BONUS_PARTS_COLS = [24, 25, 26]      # cols 24-26
RIGHT_BONUS_PTS_COL = 27                   # col 27
RIGHT_Q_TOTAL_COL = 28                     # col 28
RIGHT_CUML_SCORE_COL = 29                  # col 29
ISSUES_COL = 30                            # col 30

# Row indices
ROW_EVENT = 0
ROW_ROUND = 1
ROW_TEAMS = 2
ROW_PLAYERS = 3
ROW_TU_START = 4     # TU 1 starts here
ROW_TU_END = 33      # TU 30 ends here (inclusive)
ROW_FINAL_SCORE = 34
ROW_TUH = 35
ROW_15S = 36
ROW_10S = 37
ROW_NEG5S = 38
ROW_TU_POINTS = 39
ROW_SUBTOTALS = 40

# NAQT point values
POWER = 15
GET = 10
NEG = -5
BUZZ_VALUES = {POWER, GET, NEG}


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class PlayerInfo:
    name: str
    col_index: int        # column in CSV
    id: str = ""
    tossups_heard: int = 0
    powers: int = 0
    tens: int = 0
    negs: int = 0

    @property
    def total_points(self) -> int:
        return self.powers * POWER + self.tens * GET + self.negs * NEG


@dataclass
class BuzzEvent:
    """A single buzz on a single tossup."""
    player: PlayerInfo
    value: int            # 15, 10, or -5
    team_side: str        # "left" or "right"


@dataclass
class TossupResult:
    """Question-by-question result for one tossup-bonus cycle."""
    question_number: int
    buzzes: list[BuzzEvent] = field(default_factory=list)
    left_bonus_parts: list[int] = field(default_factory=list)   # per-part points (0 or 10)
    right_bonus_parts: list[int] = field(default_factory=list)
    left_bonus_points: int = 0
    right_bonus_points: int = 0
    notes: str = ""


@dataclass
class GameData:
    """All data from one scoresheet."""
    event_name: str = ""
    round_number: str = ""
    moderator: str = ""
    room: str = ""
    left_team_name: str = ""
    right_team_name: str = ""
    left_players: list[PlayerInfo] = field(default_factory=list)
    right_players: list[PlayerInfo] = field(default_factory=list)
    tossup_results: list[TossupResult] = field(default_factory=list)
    left_final_score: int = 0
    right_final_score: int = 0
    tossups_read: int = 0
    file_path: str = ""


# ─── CSV Parser ───────────────────────────────────────────────────────────────

def safe_int(value: str) -> int:
    """Parse an integer from a string, returning 0 for empty/invalid."""
    value = value.strip()
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return 0


def _find_row_by_label(rows: list[list[str]], label: str, start: int = 0) -> int:
    """Find the first row whose col-0 value matches the label (case-insensitive). Returns -1 if not found."""
    label_lower = label.lower().strip()
    for i in range(start, len(rows)):
        if rows[i] and rows[i][0].strip().lower() == label_lower:
            return i
    return -1


def parse_scoresheet(file_path: str) -> GameData:
    """Parse a single NAQT scoresheet CSV into a GameData object."""
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)

    # Pad rows to ensure minimum column count
    for i in range(len(rows)):
        while len(rows[i]) <= ISSUES_COL:
            rows[i].append('')

    game = GameData(file_path=file_path)

    # ── Dynamically locate key rows by their labels ──
    row_final   = _find_row_by_label(rows, "Final Score")
    row_tuh     = _find_row_by_label(rows, "TUH")
    row_15s     = _find_row_by_label(rows, "No. of 15s")
    row_10s     = _find_row_by_label(rows, "No. of 10s")
    row_neg5s   = _find_row_by_label(rows, "No. of -5s")
    row_tu_pts  = _find_row_by_label(rows, "TU Points")
    row_running = _find_row_by_label(rows, "Running Scores")

    # The tossup data rows start just after "Running Scores" header and end just before "Final Score"
    tu_start = (row_running + 1) if row_running >= 0 else ROW_TU_START
    tu_end   = (row_final - 1) if row_final >= 0 else ROW_TU_END
    # But "Running Scores" row itself may contain TU 1 data (col 15 has the TU number)
    if row_running >= 0 and safe_int(rows[row_running][TU_NUMBER_COL]) > 0:
        tu_start = row_running

    # ── Row 0: Event / Version ──
    # Col 0 is the label "Event", col 1 has the actual event name
    event_label = rows[ROW_EVENT][0].strip()
    if event_label.lower() == 'event':
        game.event_name = rows[ROW_EVENT][1].strip() if len(rows[ROW_EVENT]) > 1 else ""
    else:
        game.event_name = event_label  # In case it's directly in col 0

    # ── Row 1: Round / Moderator / Room ──
    # Col 0 is the label "Round", col 1 has the round number
    round_label = rows[ROW_ROUND][0].strip()
    if round_label.lower() == 'round':
        round_val = rows[ROW_ROUND][1].strip() if len(rows[ROW_ROUND]) > 1 else ""
    else:
        round_val = round_label
    game.round_number = round_val

    # Moderator: scan cols 8-14 for a non-label value
    game.moderator = ""
    for mc in range(8, 15):
        val = rows[ROW_ROUND][mc].strip() if len(rows[ROW_ROUND]) > mc else ""
        if val and val.lower() != 'moderator':
            game.moderator = val
            break

    # Room: scan cols 21-25 for a non-label value
    game.room = ""
    for rc in range(21, 26):
        val = rows[ROW_ROUND][rc].strip() if len(rows[ROW_ROUND]) > rc else ""
        if val and val.lower() != 'room':
            game.room = val
            break

    # ── Row 2: Team Names ──
    # Col 0 is probably "Teams" label, col 1 has the left team name
    teams_label = rows[ROW_TEAMS][0].strip()
    if teams_label.lower() == 'teams':
        game.left_team_name = rows[ROW_TEAMS][1].strip() if len(rows[ROW_TEAMS]) > 1 else ""
    else:
        game.left_team_name = teams_label

    # Right team: search cols 16+ for the first non-empty, non-label value
    for c in range(16, ISSUES_COL + 1):
        val = rows[ROW_TEAMS][c].strip()
        if val and val.lower() not in ('tu number', ''):
            game.right_team_name = val
            break

    # ── Row 3: Player Names ──
    # Labels that can appear in player columns but are NOT player names
    header_labels = {
        'players', 'bonus parts', 'bonus points', 'question total',
        'cuml. score', 'cuml score', 'cumulative score', 'issues',
        'tu number', 'running scores',
    }

    for col in LEFT_PLAYER_COLS:
        name = rows[ROW_PLAYERS][col].strip()
        if name and name.lower() not in header_labels:
            game.left_players.append(PlayerInfo(name=name, col_index=col))

    for col in RIGHT_PLAYER_COLS:
        name = rows[ROW_PLAYERS][col].strip()
        if name and name.lower() not in header_labels:
            game.right_players.append(PlayerInfo(name=name, col_index=col))

    # ── TUH per player ──
    if row_tuh >= 0:
        for player in game.left_players:
            player.tossups_heard = safe_int(rows[row_tuh][player.col_index])
        for player in game.right_players:
            player.tossups_heard = safe_int(rows[row_tuh][player.col_index])

    # ── Summary stats per player ──
    if row_15s >= 0:
        for player in game.left_players + game.right_players:
            player.powers = safe_int(rows[row_15s][player.col_index])
    if row_10s >= 0:
        for player in game.left_players + game.right_players:
            player.tens = safe_int(rows[row_10s][player.col_index])
    if row_neg5s >= 0:
        for player in game.left_players + game.right_players:
            player.negs = safe_int(rows[row_neg5s][player.col_index])

    # ── Final Score ──
    if row_final >= 0:
        # Left score: first non-empty numeric cell in cols 1-14
        for fc in range(1, 15):
            val = rows[row_final][fc].strip()
            if val:
                game.left_final_score = safe_int(val)
                break
        # Right score: first non-empty numeric cell in cols 16-29
        for fc in range(16, 30):
            val = rows[row_final][fc].strip()
            if val:
                game.right_final_score = safe_int(val)
                break

    # ── Question-by-question data ──
    all_players = {p.col_index: (p, "left") for p in game.left_players}
    all_players.update({p.col_index: (p, "right") for p in game.right_players})

    tossups_read = 0
    for row_idx in range(tu_start, min(tu_end + 1, len(rows))):
        row = rows[row_idx]
        tu_num_str = row[TU_NUMBER_COL].strip()
        tu_num = safe_int(tu_num_str)
        if tu_num == 0:
            continue

        # Check if this tossup has any data
        has_data = False
        for col in LEFT_PLAYER_COLS + RIGHT_PLAYER_COLS:
            if row[col].strip():
                has_data = True
                break
        if not has_data:
            for col in [LEFT_BONUS_PTS_COL, RIGHT_BONUS_PTS_COL, LEFT_Q_TOTAL_COL, RIGHT_Q_TOTAL_COL]:
                if row[col].strip():
                    has_data = True
                    break

        if not has_data:
            continue

        tossups_read = max(tossups_read, tu_num)

        result = TossupResult(question_number=tu_num)

        # Find buzzes in player columns
        for col in LEFT_PLAYER_COLS + RIGHT_PLAYER_COLS:
            cell = row[col].strip()
            if not cell:
                continue
            val = safe_int(cell)
            if val in BUZZ_VALUES and col in all_players:
                player, side = all_players[col]
                result.buzzes.append(BuzzEvent(player=player, value=val, team_side=side))

        # Parse bonus parts (values are 1/0 flags meaning correct/incorrect, worth 10 pts each)
        for i, col in enumerate(LEFT_BONUS_PARTS_COLS):
            cell = row[col].strip()
            if cell:
                result.left_bonus_parts.append(safe_int(cell))

        for i, col in enumerate(RIGHT_BONUS_PARTS_COLS):
            cell = row[col].strip()
            if cell:
                result.right_bonus_parts.append(safe_int(cell))

        result.left_bonus_points = safe_int(row[LEFT_BONUS_PTS_COL])
        result.right_bonus_points = safe_int(row[RIGHT_BONUS_PTS_COL])

        # Notes/issues
        if len(row) > ISSUES_COL:
            result.notes = row[ISSUES_COL].strip()

        game.tossup_results.append(result)

    game.tossups_read = tossups_read

    # If TUH wasn't filled per player, estimate from question data
    if all(p.tossups_heard == 0 for p in game.left_players + game.right_players) and tossups_read > 0:
        for p in game.left_players + game.right_players:
            p.tossups_heard = tossups_read

    return game


# ─── QBJ Generator (MODAQ-style single-match format) ─────────────────────────

class NaqtQbjGenerator:
    """Generate a MODAQ-style QBJ match object from a parsed NAQT scoresheet."""

    def __init__(self, game: GameData):
        self.game = game

    def generate(self) -> dict:
        """Generate the flat QBJ match object."""
        game = self.game

        match: dict = {
            "tossups_read": game.tossups_read,
            "match_teams": [
                self._build_match_team(game.left_team_name, game.left_players, "left"),
                self._build_match_team(game.right_team_name, game.right_players, "right"),
            ],
        }

        # Build question-level data
        match_questions = self._build_match_questions()
        if match_questions:
            match["match_questions"] = match_questions

        # Round number
        if game.round_number:
            try:
                match["_round"] = int(game.round_number)
            except ValueError:
                match["_round"] = game.round_number

        # Packet name
        if game.round_number:
            match["packets"] = f"Packet {game.round_number}"

        return match

    def _make_team_obj(self, team_name: str, players: list[PlayerInfo]) -> dict:
        """Create an inline team object with name and players list."""
        return {
            "name": team_name,
            "players": [{"name": p.name} for p in players],
        }

    def _build_match_team(
        self,
        team_name: str,
        players: list[PlayerInfo],
        side: str,
    ) -> dict:
        """Build a QBJ MatchTeam object."""
        game = self.game

        # Calculate bonus points from question data
        bonus_pts = 0
        for tr in game.tossup_results:
            if side == "left":
                bonus_pts += tr.left_bonus_points
            else:
                bonus_pts += tr.right_bonus_points

        # Build match players with only non-zero answer counts
        match_players = []
        for p in players:
            answer_counts = []
            if p.tens > 0:
                answer_counts.append({"answer": {"value": GET}, "number": p.tens})
            if p.powers > 0:
                answer_counts.append({"answer": {"value": POWER}, "number": p.powers})
            if p.negs > 0:
                answer_counts.append({"answer": {"value": NEG}, "number": p.negs})

            mp: dict = {
                "player": {"name": p.name},
                "answer_counts": answer_counts,
                "tossups_heard": p.tossups_heard,
            }
            match_players.append(mp)

        # Build lineup
        lineup = {
            "first_question": 1,
            "players": [{"name": p.name} for p in players],
        }

        match_team: dict = {
            "bonus_points": bonus_pts,
            "lineups": [lineup],
            "match_players": match_players,
            "team": self._make_team_obj(team_name, players),
        }

        return match_team

    def _build_match_questions(self) -> list[dict]:
        """Build question-by-question data."""
        game = self.game
        questions = []

        # Pre-build team objects for reuse
        left_team_obj = self._make_team_obj(game.left_team_name, game.left_players)
        right_team_obj = self._make_team_obj(game.right_team_name, game.right_players)

        for tr in game.tossup_results:
            q: dict = {
                "question_number": tr.question_number,
                "buzzes": [],
            }

            # Build buzz objects
            for buzz in tr.buzzes:
                if buzz.team_side == "left":
                    team_obj = left_team_obj
                else:
                    team_obj = right_team_obj

                buzz_obj: dict = {
                    "player": {"name": buzz.player.name},
                    "team": team_obj,
                    "result": {"value": buzz.value},
                }
                q["buzzes"].append(buzz_obj)

            # Tossup question metadata
            q["tossup_question"] = {
                "parts": 1,
                "type": "tossup",
                "question_number": tr.question_number,
            }

            # Build bonus object
            # The team with the last correct buzz (10 or 15) earned the bonus
            correct_buzzes = [b for b in tr.buzzes if b.value > 0]
            if correct_buzzes:
                winning_side = correct_buzzes[-1].team_side
                parts_flags = tr.left_bonus_parts if winning_side == "left" else tr.right_bonus_parts

                # Build bonus parts: correct = {"controlled_points": 10},
                # missed = {"controlled_points": 0, "bounceback_points": 0}
                bonus_parts = []
                # Ensure we always have 3 parts (NAQT standard)
                while len(parts_flags) < 3:
                    parts_flags.append(0)
                for flag in parts_flags[:3]:
                    if flag:
                        bonus_parts.append({"controlled_points": flag * 10})
                    else:
                        bonus_parts.append({"controlled_points": 0, "bounceback_points": 0})

                q["bonus"] = {
                    "question": {
                        "parts": 3,
                        "type": "bonus",
                        "question_number": tr.question_number,
                    },
                    "parts": bonus_parts,
                }

            questions.append(q)

        return questions


# ─── Main ─────────────────────────────────────────────────────────────────────

def convert(input_paths: list[str], output_path: Optional[str] = None) -> list[str]:
    """Convert NAQT scoresheet CSVs to QBJ files (one QBJ per CSV)."""
    output_paths = []
    for path in input_paths:
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        game = parse_scoresheet(path)

        if output_path and len(input_paths) == 1:
            out = output_path
        else:
            base = os.path.splitext(path)[0]
            out = base + ".qbj"

        generator = NaqtQbjGenerator(game)
        qbj = generator.generate()

        with open(out, 'w', encoding='utf-8') as f:
            json.dump(qbj, f, indent=2, ensure_ascii=False)

        output_paths.append(out)

    return output_paths


def print_summary(games: list[GameData]):
    """Print a summary of parsed data."""
    for i, game in enumerate(games):
        print(f"--- Game {i + 1}: {os.path.basename(game.file_path)} ---")
        print(f"  Event: {game.event_name or '(none)'}")
        print(f"  Round: {game.round_number or '(none)'}")
        if game.moderator:
            print(f"  Moderator: {game.moderator}")
        if game.room:
            print(f"  Room: {game.room}")
        print(f"  {game.left_team_name or '(unnamed)'} vs {game.right_team_name or '(unnamed)'}")
        print(f"  Score: {game.left_final_score} - {game.right_final_score}")
        print(f"  Tossups read: {game.tossups_read}")
        print(f"  Left players:  {', '.join(p.name for p in game.left_players) or '(none)'}")
        print(f"  Right players: {', '.join(p.name for p in game.right_players) or '(none)'}")

        # Per-player stats
        for side_name, players in [("Left", game.left_players), ("Right", game.right_players)]:
            if players:
                for p in players:
                    pts = p.powers * 15 + p.tens * 10 + p.negs * -5
                    print(f"    {p.name}: {p.powers}/{p.tens}/{p.negs} = {pts} pts, TUH={p.tossups_heard}")
        print()


def main():
    if len(sys.argv) < 2:
        print("NAQT Scoresheet CSV to QBJ Converter")
        print()
        print("Usage:")
        print(f"  {sys.argv[0]} <scoresheet.csv> [output.qbj]")
        print(f"  {sys.argv[0]} <game1.csv> <game2.csv> ...")
        print()
        print("Convert filled NAQT scoresheet CSVs to QBJ match files.")
        print("Each CSV produces one QBJ file (same name, .qbj extension).")
        print()
        print("The output QBJ can be imported into YellowFruit via:")
        print("  File > QBJ Schema > Open QBJ Tournament")
        sys.exit(1)

    # Parse arguments
    input_files = []
    output_file = None

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '-o' and i + 1 < len(sys.argv):
            output_file = sys.argv[i + 1]
            i += 2
        else:
            input_files.append(sys.argv[i])
            i += 1

    # If last arg looks like a .qbj file and there are multiple args, use it as output
    if len(input_files) > 1 and input_files[-1].endswith('.qbj'):
        output_file = input_files.pop()

    if not input_files:
        print("Error: No input files specified.", file=sys.stderr)
        sys.exit(1)

    try:
        games = [parse_scoresheet(f) for f in input_files]
        print_summary(games)

        out_paths = convert(input_files, output_file)
        for out in out_paths:
            print(f"QBJ file written to: {out}")
        print()
        print("Import into YellowFruit via:")
        print("  File > QBJ Schema > Open QBJ Tournament")

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

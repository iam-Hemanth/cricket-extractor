"""
src/cricsheet_audit.py
Fact-checking comparison between Match JSON v3.0 and Cricsheet Match JSON.
Compares factual game data (runs, wickets, balls, dismissals, players) rather than field names.
"""
import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    match_id: str
    is_valid: bool
    hard_errors: List[str] = field(default_factory=list)
    soft_warnings: List[str] = field(default_factory=list)
    balls_checked: int = 0
    innings_checked: int = 0


class CricsheetAuditor:
    def __init__(self, people_csv_path: Optional[str] = None):
        self.espn_to_name: Dict[str, str] = {}
        self.cricsheet_to_name: Dict[str, str] = {}
        self.espn_to_cricsheet: Dict[str, str] = {}
        if people_csv_path and Path(people_csv_path).exists():
            self._load_people_registry(people_csv_path)

    def _load_people_registry(self, csv_path: str):
        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                c_id = row.get("identifier")
                name = row.get("name") or row.get("unique_name")
                espn_id = row.get("key_cricinfo")
                if c_id and name:
                    self.cricsheet_to_name[c_id] = name
                if espn_id and name:
                    self.espn_to_name[f"espn_{espn_id}"] = name
                    self.espn_to_name[str(espn_id)] = name
                if espn_id and c_id:
                    self.espn_to_cricsheet[f"espn_{espn_id}"] = c_id
                    self.espn_to_cricsheet[str(espn_id)] = c_id

    def build_match_player_map(self, v3_match: dict, cricsheet_match: dict) -> Dict[str, str]:
        """Build a match-specific player name mapping from v3 and cricsheet playing XIs."""
        match_map = dict(self.espn_to_name)
        # From v3 playing_xi
        for team, players in v3_match.get("playing_xi", {}).items():
            if team.startswith("_") or not isinstance(players, list):
                continue
            for p in players:
                if not isinstance(p, dict):
                    continue
                pid = p.get("player_id")
                pname = p.get("name")
                if pid and pname:
                    match_map[pid] = pname
                    if p.get("espn_id"):
                        match_map[str(p["espn_id"])] = pname


        # From cricsheet info.registry.people
        cs_registry = cricsheet_match.get("info", {}).get("registry", {}).get("people", {})
        for name, cid in cs_registry.items():
            self.cricsheet_to_name[cid] = name

        return match_map

    def audit_match(self, v3_match: dict, cricsheet_match: dict) -> AuditResult:
        mid = v3_match.get("match_id", "unknown")
        result = AuditResult(match_id=mid, is_valid=True)

        player_map = self.build_match_player_map(v3_match, cricsheet_match)

        v3_inns = v3_match.get("innings", [])
        cs_inns = cricsheet_match.get("innings", [])

        if len(v3_inns) != len(cs_inns):
            # Soft warning or hard error? If match was truncated or rain/super-over difference:
            result.soft_warnings.append(
                f"Innings count mismatch: v3 has {len(v3_inns)} vs Cricsheet {len(cs_inns)}"
            )

        # Audit each common innings
        for idx in range(min(len(v3_inns), len(cs_inns))):
            v_inn = v3_inns[idx]
            c_inn = cs_inns[idx]
            self._audit_innings(idx + 1, v_inn, c_inn, player_map, result)

        if result.hard_errors:
            result.is_valid = False

        return result

    def _audit_innings(
        self,
        inn_num: int,
        v_inn: dict,
        c_inn: dict,
        player_map: Dict[str, str],
        result: AuditResult
    ):
        result.innings_checked += 1

        # Flatten deliveries
        v_deliveries: List[dict] = []
        for ov in v_inn.get("overs", []):
            for d in ov.get("deliveries", []):
                v_deliveries.append(d)

        c_deliveries: List[dict] = []
        for ov in c_inn.get("overs", []):
            for d in ov.get("deliveries", []):
                c_deliveries.append(d)

        # 1. Totals Check
        v_total_runs = sum(d.get("runs", {}).get("total", 0) for d in v_deliveries)
        c_total_runs = sum(d.get("runs", {}).get("total", 0) for d in c_deliveries)

        v_total_wkts = sum(len(d.get("wickets", [])) for d in v_deliveries)
        c_total_wkts = sum(len(d.get("wickets", [])) for d in c_deliveries)

        v_legal_balls = sum(1 for d in v_deliveries if d.get("is_legal"))
        c_legal_balls = sum(
            1 for d in c_deliveries
            if not (d.get("extras", {}).get("wides") or d.get("extras", {}).get("noballs"))
        )

        if v_total_runs != c_total_runs:
            result.hard_errors.append(
                f"Inn {inn_num} total runs mismatch: v3={v_total_runs} vs cs={c_total_runs}"
            )
        if v_total_wkts != c_total_wkts:
            result.hard_errors.append(
                f"Inn {inn_num} total wickets mismatch: v3={v_total_wkts} vs cs={c_total_wkts}"
            )
        if v_legal_balls != c_legal_balls:
            result.hard_errors.append(
                f"Inn {inn_num} legal balls mismatch: v3={v_legal_balls} vs cs={c_legal_balls}"
            )

        # 2. Ball-by-ball check
        min_balls = min(len(v_deliveries), len(c_deliveries))
        result.balls_checked += min_balls

        if len(v_deliveries) != len(c_deliveries):
            result.hard_errors.append(
                f"Inn {inn_num} delivery count mismatch: v3={len(v_deliveries)} vs cs={len(c_deliveries)}"
            )

        for b_idx in range(min_balls):
            vd = v_deliveries[b_idx]
            cd = c_deliveries[b_idx]
            b_label = f"Inn {inn_num} Ball {b_idx + 1} (actual {vd.get('actual_delivery')})"

            # Runs check
            v_runs = vd.get("runs", {})
            c_runs = cd.get("runs", {})
            if v_runs.get("batter", 0) != c_runs.get("batter", 0):
                result.hard_errors.append(
                    f"{b_label} batter runs mismatch: v3={v_runs.get('batter')} vs cs={c_runs.get('batter')}"
                )
            if v_runs.get("extras", 0) != c_runs.get("extras", 0):
                result.hard_errors.append(
                    f"{b_label} extras runs mismatch: v3={v_runs.get('extras')} vs cs={c_runs.get('extras')}"
                )
            if v_runs.get("total", 0) != c_runs.get("total", 0):
                result.hard_errors.append(
                    f"{b_label} total runs mismatch: v3={v_runs.get('total')} vs cs={c_runs.get('total')}"
                )

            # Extras type check
            v_ext = vd.get("extras") or {}
            c_ext = cd.get("extras") or {}
            for ext_type in ("wides", "noballs", "byes", "legbyes", "penalty"):
                v_val = v_ext.get(ext_type, 0)
                c_val = c_ext.get(ext_type, 0)
                if v_val != c_val:
                    result.hard_errors.append(
                        f"{b_label} extras[{ext_type}] mismatch: v3={v_val} vs cs={c_val}"
                    )

            # Wickets check
            v_wkts = vd.get("wickets") or []
            c_wkts = cd.get("wickets") or []
            if len(v_wkts) != len(c_wkts):
                result.hard_errors.append(
                    f"{b_label} wicket event mismatch: v3={len(v_wkts)} vs cs={len(c_wkts)}"
                )
            elif v_wkts and c_wkts:
                vw = v_wkts[0]
                cw = c_wkts[0]
                # Compare dismissal kind (normalizing caught and bowled)
                v_kind = vw.get("kind", "").replace("caught and bowled", "caught").lower()
                c_kind = cw.get("kind", "").replace("caught and bowled", "caught").lower()
                if v_kind != c_kind:
                    result.soft_warnings.append(
                        f"{b_label} dismissal kind diff: v3='{vw.get('kind')}' vs cs='{cw.get('kind')}'"
                    )

                # Batter out check
                v_out_name = vw.get("player_out_name") or player_map.get(vw.get("player_out_id"), "")
                c_out_name = cw.get("player_out", "")
                if v_out_name and c_out_name and v_out_name != c_out_name:
                    # Check if surnames or initials match
                    if not self._names_match(v_out_name, c_out_name):
                        result.hard_errors.append(
                            f"{b_label} player out mismatch: v3='{v_out_name}' vs cs='{c_out_name}'"
                        )

    @staticmethod
    def _names_match(n1: str, n2: str) -> bool:
        if n1.lower() == n2.lower():
            return True
        # Check last names
        p1 = n1.split()
        p2 = n2.split()
        if p1 and p2 and p1[-1].lower() == p2[-1].lower():
            return True
        return False

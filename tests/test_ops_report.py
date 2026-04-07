import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.ops_report import parse_cli_args


class OpsReportCliTest(unittest.TestCase):
    def test_defaults_to_overview_and_env_values(self) -> None:
        parsed = parse_cli_args([], "env-token", "45")
        self.assertEqual(parsed["token"], "env-token")
        self.assertEqual(parsed["report_name"], "overview")
        self.assertEqual(parsed["retention_days"], 45)

    def test_report_and_token_can_be_passed_positionally(self) -> None:
        parsed = parse_cli_args(["topology", "cli-token"], None, "30")
        self.assertEqual(parsed["token"], "cli-token")
        self.assertEqual(parsed["report_name"], "topology")
        self.assertEqual(parsed["retention_days"], 30)

    def test_control_room_report_name_is_accepted(self) -> None:
        parsed = parse_cli_args(["control-room", "cli-token"], None, "30")
        self.assertEqual(parsed["token"], "cli-token")
        self.assertEqual(parsed["report_name"], "control-room")
        self.assertEqual(parsed["retention_days"], 30)

    def test_retention_command_accepts_days_without_overriding_env_token(self) -> None:
        parsed = parse_cli_args(["audit-retention-dry-run", "14"], "env-token", "30")
        self.assertEqual(parsed["token"], "env-token")
        self.assertEqual(parsed["report_name"], "audit-retention-dry-run")
        self.assertEqual(parsed["retention_days"], 14)

    def test_retention_command_accepts_token_and_days(self) -> None:
        parsed = parse_cli_args(["audit-retention-apply", "cli-token", "7"], None, "30")
        self.assertEqual(parsed["token"], "cli-token")
        self.assertEqual(parsed["report_name"], "audit-retention-apply")
        self.assertEqual(parsed["retention_days"], 7)


if __name__ == "__main__":
    unittest.main()

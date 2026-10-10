import unittest

from headtrack_pc import fixes

EXE = r"C:\Program Files\HeadTrackPC\HeadTrackPC.exe"


def rule(profiles="Domain,Private,Public", program=EXE, enabled="Yes"):
    return (f"\nRule Name:                            HeadTrack PC\n"
            f"----------------------------------------------------------------------\n"
            f"Enabled:                              {enabled}\n"
            f"Direction:                            In\n"
            f"Profiles:                             {profiles}\n"
            f"Protocol:                             UDP\n"
            f"LocalPort:                            4242,4244\n"
            f"Program:                              {program}\n"
            f"Action:                               Allow\n")


class FirewallRuleTest(unittest.TestCase):
    def test_all_profiles_for_this_program_covers(self):
        self.assertTrue(fixes.rule_covers(rule() + "Ok.\n", EXE))
        self.assertTrue(fixes.rule_covers(rule(program=EXE.upper()), EXE))

    def test_private_only_rule_does_not_cover_a_public_network(self):
        self.assertFalse(fixes.rule_covers(rule(profiles="Domain,Private"), EXE))

    def test_rule_for_another_program_or_disabled_does_not_cover(self):
        self.assertFalse(fixes.rule_covers(rule(program=r"E:\proj\.venv\Scripts\python.exe"), EXE))
        self.assertFalse(fixes.rule_covers(rule(enabled="No"), EXE))
        self.assertFalse(fixes.rule_covers("No rules match the specified criteria.\n", EXE))

    def test_any_matching_rule_among_several_counts(self):
        text = rule(profiles="Private", program=r"D:\old\HeadTrackPC.exe") + rule()
        self.assertTrue(fixes.rule_covers(text, EXE))


if __name__ == "__main__":
    unittest.main()

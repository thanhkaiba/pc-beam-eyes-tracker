import json
import os
import tempfile
import unittest
from dataclasses import replace

from headtrack_pc import profile as prof
from headtrack_pc.mapping import ResponseCurve
from headtrack_pc.pose import HeadPose


class ProfileTest(unittest.TestCase):
    def test_round_trip_with_neutral(self):
        p = replace(prof.DRIVING, neutral_pose=HeadPose(1, 2, 3, 4, 5, 6), source=prof.SourceKind.PHONE)
        q = prof.decode_or_none(prof.encode(p))
        self.assertEqual(p, q)
        self.assertEqual(q.mapping.yaw.curve, ResponseCurve.SOFT)

    def test_unknown_keys_and_missing_fields_use_defaults(self):
        text = json.dumps({"schema_version": 1, "name": "x", "mapping": {"yaw": {"sensitivity": 2.5}}, "future": 1})
        p = prof.decode_or_none(text)
        self.assertEqual(p.name, "x")
        self.assertEqual(p.mapping.yaw.sensitivity, 2.5)
        self.assertEqual(p.mapping.pitch, prof.MappingSettings().pitch)

    def test_invalid_falls_back(self):
        self.assertIsNone(prof.decode_or_none("not json"))
        self.assertIsNone(prof.decode_or_none(json.dumps({"schema_version": 99})))
        self.assertIsNone(prof.decode_or_none(json.dumps({"mapping": {"yaw": {"sensitivity": -1}}})))
        self.assertIsNone(prof.decode_or_none(json.dumps({"neutral_pose": {"yaw": "nan", "pitch": 0, "roll": 0}})))
        self.assertIsNone(prof.decode_or_none(json.dumps([1, 2])))
        self.assertEqual(prof.decode_or_default(None), prof.DEFAULT)
        self.assertIsNone(prof.decode_or_none(json.dumps({"camera": {"index": "zero"}})))

    def test_save_and_load_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "profile.json")
            self.assertEqual(prof.load(path), prof.DEFAULT)
            prof.save(replace(prof.DRIVING, name="saved"), path)
            self.assertEqual(prof.load(path).name, "saved")
            with open(path, "w") as f:
                f.write("{broken")
            self.assertEqual(prof.load(path), prof.DEFAULT)

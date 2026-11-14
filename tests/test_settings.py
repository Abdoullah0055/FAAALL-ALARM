import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from faaall_alarm.settings import LocalSettings, Settings
from faaall_alarm.sms import Mode


class SettingsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "reglages.json"

    def test_roundtrip_and_local_values_take_precedence(self):
        settings = LocalSettings(self.path, {"FAAALL_CONTACT_NAME": "Sophie"})
        self.assertEqual(settings.load().contact_name, "Sophie")
        changed = replace(settings.load(), zone=(5, 10, 80, 95),
                          rest_zone=(10, 20, 40, 60), countdown_seconds=4)
        settings.apply(changed)
        self.assertEqual(settings.load(), changed)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_inputs_not_written(self):
        backend = LocalSettings(self.path, {})
        settings = Settings(camera_index=-1, zone=(5, 10, 80, 95), rest_zone=(0, 20, 40, 60),
                            countdown_seconds=0, volume=101, sms_mode=Mode.LIVE,
                            sender="numéro invalide")
        self.assertEqual(set(backend.validate(settings)), {
            "camera_index", "rest_zone", "countdown_seconds", "volume",
            "sms_mode", "sender", "first_name", "address", "contact_name",
        })
        with self.assertRaises(ValueError):
            backend.apply(settings)
        self.assertFalse(self.path.exists())

    def test_sound_uses_french_and_handles_missing_executable(self):
        backend = LocalSettings(self.path, {})
        settings = Settings(voice_path=r"C:\exemple fictif\espeak-ng.exe", volume=65)
        with patch("faaall_alarm.settings.subprocess.run") as process:
            process.return_value.returncode = 0
            self.assertTrue(backend.test_sound(settings))
            self.assertEqual(process.call_args.args[0][:5], [settings.voice_path, "-v", "fr", "-a", "65"])
            process.side_effect = FileNotFoundError
            self.assertFalse(backend.test_sound(settings))

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loopai.configuration import DEFAULT_CONFIG, load_working_directory_config


class WorkingDirectoryConfigurationTests(unittest.TestCase):
    def test_first_load_creates_default_role_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)

            settings = load_working_directory_config(working_directory)
            config = working_directory / ".loopai" / "config.toml"

            self.assertEqual(config.read_text(encoding="utf-8"), DEFAULT_CONFIG)
            self.assertEqual(settings["coordinator"].model, "gpt-5.6-luna")
            self.assertEqual(
                settings["coordinator"].startup_prompt,
                "请使用中文与用户交互。",
            )
            self.assertEqual(settings["executor"].model, "gpt-5.6-luna")
            self.assertEqual(settings["verifier"].model, "gpt-5.6-luna")
            self.assertEqual(settings["verifier"].reasoning_effort, "medium")

    def test_existing_configuration_is_loaded_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            config_dir = working_directory / ".loopai"
            config_dir.mkdir()
            custom = DEFAULT_CONFIG.replace(
                '[executor]\nmodel = "gpt-5.6-luna"',
                '[executor]\nmodel = "custom-executor"',
            )
            config = config_dir / "config.toml"
            config.write_text(custom, encoding="utf-8")

            settings = load_working_directory_config(working_directory)

            self.assertEqual(settings["executor"].model, "custom-executor")
            self.assertEqual(config.read_text(encoding="utf-8"), custom)

    def test_rejects_unknown_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            config_dir = working_directory / ".loopai"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                DEFAULT_CONFIG + "\n[other]\nmodel = \"x\"\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "Unknown.*sections"):
                load_working_directory_config(working_directory)

    def test_rejects_invalid_reasoning_effort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            config_dir = working_directory / ".loopai"
            config_dir.mkdir()
            (config_dir / "config.toml").write_text(
                DEFAULT_CONFIG.replace(
                    'reasoning_effort = "medium"', 'reasoning_effort = "extreme"'
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported reasoning_effort"):
                load_working_directory_config(working_directory)

    def test_loads_multiline_coordinator_startup_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            config_dir = working_directory / ".loopai"
            config_dir.mkdir()
            custom = DEFAULT_CONFIG.replace(
                'startup_prompt = """请使用中文与用户交互。"""',
                'startup_prompt = """\n请使用中文与用户交互。\n提问保持简洁。\n"""',
            )
            (config_dir / "config.toml").write_text(custom, encoding="utf-8")

            settings = load_working_directory_config(working_directory)

            self.assertEqual(
                settings["coordinator"].startup_prompt,
                "请使用中文与用户交互。\n提问保持简洁。",
            )
            self.assertIsNone(settings["executor"].startup_prompt)

    def test_rejects_non_string_startup_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            working_directory = Path(directory)
            config_dir = working_directory / ".loopai"
            config_dir.mkdir()
            custom = DEFAULT_CONFIG.replace(
                'startup_prompt = """请使用中文与用户交互。"""',
                "startup_prompt = 42",
            )
            (config_dir / "config.toml").write_text(custom, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "startup_prompt must be a string"):
                load_working_directory_config(working_directory)


if __name__ == "__main__":
    unittest.main()

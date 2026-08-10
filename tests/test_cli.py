from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from loopai.cli import _read_multiline_input


class CliInputTests(unittest.TestCase):
    def test_multiline_input_is_submitted_as_one_answer_after_blank_line(self) -> None:
        output = StringIO()
        with patch("builtins.input", side_effect=["first line", "second line", ""]):
            with redirect_stdout(output):
                answer = _read_multiline_input("Question\n\n> ")

        self.assertEqual(answer, "first line\nsecond line")
        self.assertEqual(output.getvalue(), "Question\n\n> ")

    def test_control_command_is_submitted_without_extra_terminator_line(self) -> None:
        output = StringIO()
        with patch("builtins.input", return_value="/cancel") as read_input:
            with redirect_stdout(output):
                answer = _read_multiline_input("Question\n\n> ")

        self.assertEqual(answer, "/cancel")
        self.assertEqual(output.getvalue(), "Question\n\n> ")
        read_input.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

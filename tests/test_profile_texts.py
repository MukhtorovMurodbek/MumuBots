"""Every bot's profile texts fit Telegram's limits (setMyDescription 512, setMyShortDescription 120), in
every language. 2.1.7 went out with three of ConvertBot's over them; Telegram refused them at startup and
kept the old ones."""
import ast
from pathlib import Path

import pytest

LIMITS = {"bot_description": 512, "bot_short_description": 120}
BOTS = Path(__file__).resolve().parents[1] / "bots"


def _texts():
    for path in sorted(BOTS.glob("*_bot/i18n.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in LIMITS:
                    try:
                        yield path.parent.name, key.value, ast.literal_eval(value)
                    except ValueError:
                        continue


TEXTS = list(_texts())


def test_every_public_bot_has_its_profile_texts():
    assert len({bot for bot, _, _ in TEXTS}) == 4
    assert len(TEXTS) >= 4 * 2 * 3


@pytest.mark.parametrize("bot,key,text", TEXTS, ids=[f"{b}:{k}:{t[:12]}" for b, k, t in TEXTS])
def test_fits_telegrams_limit(bot, key, text):
    assert len(text) <= LIMITS[key], f"{bot} {key} is {len(text)} characters: {text!r}"

# This code is part of the TrinityX software suite
# Copyright (C) 2026  ClusterVision Solutions b.v.
# Author: Alex Ninaber
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>

"""Tests for luna.ini loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.lchroot.config import load_api_config
from utils.lchroot.errors import ConfigError

VALID = """\
[API]
USERNAME = luna
PASSWORD = s3cret
ENDPOINT = controller1:7050
PROTOCOL = https
VERIFY_CERTIFICATE = False
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "luna.ini"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_valid_config(tmp_path: Path) -> None:
    cfg = load_api_config(_write(tmp_path, VALID))
    assert cfg.username == "luna"
    assert cfg.base_url == "https://controller1:7050"
    assert cfg.verify_certificate is False


def test_verify_true_is_parsed(tmp_path: Path) -> None:
    cfg = load_api_config(_write(tmp_path, VALID.replace("False", "True")))
    assert cfg.verify_certificate is True


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_api_config(tmp_path / "nope.ini")


def test_missing_key_raises(tmp_path: Path) -> None:
    text = "[API]\nUSERNAME = luna\nENDPOINT = c:1\n"
    with pytest.raises(ConfigError, match="missing keys: PASSWORD"):
        load_api_config(_write(tmp_path, text))


def test_repr_redacts_password(tmp_path: Path) -> None:
    cfg = load_api_config(_write(tmp_path, VALID))
    assert "s3cret" not in repr(cfg)
    assert "<redacted>" in repr(cfg)


def test_password_with_percent_is_not_interpolated(tmp_path: Path) -> None:
    # A '%' in the password must be read verbatim, not treated as ConfigParser
    # interpolation — otherwise it raises InterpolationSyntaxError on access and escapes
    # as a raw traceback instead of a clean error (regression guard, finding D2).
    raw = VALID.replace("s3cret", "pa%%wor%d")
    cfg = load_api_config(_write(tmp_path, raw))
    assert cfg.password == "pa%%wor%d"


def test_malformed_ini_raises_clean_error(tmp_path: Path) -> None:
    # A line with no section / no '=' is a parse error -> clean ConfigError, not a traceback.
    with pytest.raises(ConfigError, match="malformed"):
        load_api_config(_write(tmp_path, "not an ini at all\n"))


def test_no_api_section_reports_missing_keys(tmp_path: Path) -> None:
    # A file with a different section (no [API]) reports the missing required keys.
    with pytest.raises(ConfigError, match="missing keys"):
        load_api_config(_write(tmp_path, "[OTHER]\nX = 1\n"))


def test_protocol_defaults_to_https_when_absent(tmp_path: Path) -> None:
    text = "[API]\nUSERNAME = luna\nPASSWORD = p\nENDPOINT = c:7050\n"
    assert load_api_config(_write(tmp_path, text)).protocol == "https"

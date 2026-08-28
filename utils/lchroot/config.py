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

"""Load and validate luna.ini into a typed, immutable config record.

This is the same file the bash ``lchroot`` parsed by hand. Parse loosely, then
validate, so the friendly error message is the one the user sees (PY-TYPE-005,
Bash->Python gotcha). luna.ini is local and root-owned, so a plain dataclass (not
pydantic) is the right tool here.
"""

import logging
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

log = logging.getLogger(__name__)

LUNA_INI = Path("/trinity/local/luna/utils/config/luna.ini")


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """Validated ``[API]`` settings from luna.ini."""

    username: str
    password: str
    endpoint: str
    protocol: str = "https"
    verify_certificate: bool = False

    @property
    def base_url(self) -> str:
        """Return the base URL of the Luna API."""
        return f"{self.protocol}://{self.endpoint}"

    def __repr__(self) -> str:
        """Redact the password so secrets never reach logs or tracebacks."""
        return (
            f"ApiConfig(username={self.username!r}, endpoint={self.endpoint!r}, "
            f"protocol={self.protocol!r}, verify_certificate={self.verify_certificate!r}, "
            f"password=<redacted>)"
        )


def load_api_config(path: Path = LUNA_INI) -> ApiConfig:
    """Load and validate the ``[API]`` section of luna.ini.

    Args:
        path: location of luna.ini.

    Returns:
        A validated :class:`ApiConfig`.

    Raises:
        ConfigError: if the file is missing, unreadable, malformed, or a required
            key is absent.
    """
    # interpolation=None: luna.ini values are opaque strings (the bash oracle never
    # interpolated). With the default BasicInterpolation a '%' in a value — common in
    # passwords — raises InterpolationSyntaxError on *access*, outside the read_file
    # try/except below, so it would escape as a raw traceback instead of a clean exit 7.
    parser = ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read luna.ini at {path}: {exc}") from exc
    except ConfigParserError as exc:
        raise ConfigError(f"malformed luna.ini at {path}: {exc}") from exc

    api = parser["API"] if parser.has_section("API") else {}
    missing = [key for key in ("USERNAME", "PASSWORD", "ENDPOINT") if key not in api]
    if missing:
        raise ConfigError(f"luna.ini [API] missing keys: {', '.join(missing)}")

    return ApiConfig(
        username=api["USERNAME"],
        password=api["PASSWORD"],
        endpoint=api["ENDPOINT"],
        protocol=api.get("PROTOCOL", "https"),
        verify_certificate=api.get("VERIFY_CERTIFICATE", "false").strip().lower()
        == "true",
    )

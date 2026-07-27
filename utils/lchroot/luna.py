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

"""Luna API client: token auth, HA state, osimage resolution.

The HTTP transport is injected behind a :class:`Transport` Protocol so the client is
fully testable without ``requests`` or a network (PY-AI-001, PY-TEST-002). Responses
are parsed loosely (``.get``) then validated, so our error messages are reachable
(Bash->Python gotcha).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import ApiConfig
from .errors import ImageResolveError, LunaError
from .types import ResolvedImage

log = logging.getLogger(__name__)


class Transport(Protocol):
    """Minimal HTTP transport the Luna client depends on (structural interface)."""

    def post_json(
        self,
        url: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST ``json`` to ``url`` and return the decoded JSON object."""
        ...

    def get_json(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """GET ``url`` and return the decoded JSON object."""
        ...


@dataclass(frozen=True, slots=True)
class HaState:
    """High-availability state of the cluster."""

    enabled: bool
    master: bool


class LunaClient:
    """Talks to the Luna API, mirroring the calls the bash lchroot made."""

    def __init__(self, config: ApiConfig, transport: Transport) -> None:
        """Initialise with validated config and an HTTP transport.

        Args:
            config: validated ``[API]`` settings.
            transport: HTTP transport (real or fake).
        """
        self._cfg = config
        self._transport = transport
        self._token: str | None = None

    @property
    def token(self) -> str:
        """Return a valid API token, fetching and caching it on first use.

        Raises:
            LunaError: if the token endpoint returns no token.
        """
        if self._token is None:
            data = self._transport.post_json(
                f"{self._cfg.base_url}/token",
                json={"username": self._cfg.username, "password": self._cfg.password},
            )
            token = data.get("token")
            if not token or not isinstance(token, str):
                raise LunaError("authentication failed: no token in Luna response")
            self._token = token
        return self._token

    @property
    def _auth(self) -> dict[str, str]:
        return {"x-access-tokens": self.token}

    def ha_state(self) -> HaState:
        """Return the cluster HA state (enabled / is-this-host-the-master)."""
        data = self._transport.get_json(
            f"{self._cfg.base_url}/ha/state", headers=self._auth
        )
        ha = data.get("ha", {})
        return HaState(enabled=bool(ha.get("enabled")), master=bool(ha.get("master")))

    def resolve_image(self, name: str) -> ResolvedImage:
        """Resolve an osimage name to a validated, safe rootfs path.

        Args:
            name: the osimage name.

        Returns:
            The :class:`ResolvedImage`.

        Raises:
            ImageResolveError: if the image is unknown, has no path, or the path is
                unsafe / not a real rootfs.
        """
        data = self._transport.get_json(
            f"{self._cfg.base_url}/config/osimage/{name}", headers=self._auth
        )
        image = data.get("config", {}).get("osimage", {}).get(name)
        if not image:
            raise ImageResolveError(
                f"unknown osimage {name!r} (try `luna osimage list`)"
            )

        raw_path = image.get("path")
        if not raw_path or raw_path == "null":
            raise ImageResolveError(f"osimage {name!r} has no path")
        path = Path(raw_path)
        if path == Path("/") or not path.is_absolute():
            raise ImageResolveError(
                f"refusing unsafe image path {raw_path!r} for {name!r}"
            )
        if not path.is_dir() or not (path / "usr").is_dir():
            raise ImageResolveError(f"image path {path} for {name!r} is not a rootfs")

        self._refuse_if_ambiguous(name, path)

        kernelversion = image.get("kernelversion")
        if not kernelversion or kernelversion == "null":
            log.warning(
                "osimage %s has no kernelversion; uname inside will report the host kernel",
                name,
            )
            kernelversion = None
        return ResolvedImage(name=name, path=path, kernelversion=kernelversion)

    def _refuse_if_ambiguous(self, name: str, path: Path) -> None:
        """Refuse if more than one osimage resolves to ``path`` (ambiguous target).

        Luna should never map two osimages to the same rootfs; if it does, it is a
        misconfiguration and entering would mutate an image under an ambiguous name.
        We cross-check the osimage collection and refuse loudly rather than guess.

        Best-effort (PY-ERR-006): a failed collection fetch must not turn a valid
        resolve into a failure, so a transport/Luna error here is logged and ignored.

        Raises:
            ImageResolveError: if two or more osimages share ``path``.
        """
        try:
            data = self._transport.get_json(
                f"{self._cfg.base_url}/config/osimage", headers=self._auth
            )
        except (LunaError, OSError) as exc:
            log.debug("ambiguity check skipped (collection fetch failed): %s", exc)
            return

        images = data.get("config", {}).get("osimage", {})
        if not isinstance(images, dict):
            return
        target = path.resolve()
        sharing = sorted(
            other
            for other, record in images.items()
            if isinstance(record, dict)
            and record.get("path")
            and record.get("path") != "null"
            and Path(record["path"]).resolve() == target
        )
        if len(sharing) > 1:
            raise ImageResolveError(
                f"ambiguous: {len(sharing)} osimages share path {path} "
                f"({', '.join(sharing)}); this is a luna misconfiguration — refusing "
                f"to enter {name!r} as an ambiguous destructive target"
            )

    def list_images(self) -> list[str]:
        """Return all osimage names known to luna, sorted; empty list on any failure.

        Best-effort by design (PY-ERR-006): this backs shell completion, so a daemon
        outage yields no completions rather than an error — matching luna's own
        ``get_all_names`` behaviour. luna is the authoritative source of images.
        """
        try:
            data = self._transport.get_json(
                f"{self._cfg.base_url}/config/osimage", headers=self._auth
            )
        except (LunaError, OSError) as exc:
            log.debug("list_images failed (ignored): %s", exc)
            return []
        images = data.get("config", {}).get("osimage", {})
        return sorted(images) if isinstance(images, dict) else []

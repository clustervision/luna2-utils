# lchroot ansible connection plugin -- run an image's in-image phase inside the
# lchroot-bwrap sandbox instead of a raw chroot.
#
# This plugin ships WITH lchroot (luna2-utils) rather than as build glue, so a
# `pip install luna2-utils` puts it on disk next to the engine it drives. Point
# Ansible at it with ANSIBLE_CONNECTION_PLUGINS=<this dir> (or an ansible.cfg
# connection_plugins entry) and select it with ansible_connection=lchroot.
#
# TrinityX's image build enters the image via ansible_connection=chroot
# (site/dynamic_hosts). This is a drop-in that executes every command through
# `lchroot --path <image_path> -- sh -c <cmd>`, so package installs, scripts, and
# dracut runs inside the image are sandboxed (no host firmware flash / kernel-
# module load / host-service side effects -- see lchroot ADR 0015/0016) and it is
# the only viable path for cross-arch images (arm64/riscv via qemu_static binfmt;
# a raw chroot cannot run foreign binaries). `ansible_host` (remote_addr) is the
# image path, exactly as the stock chroot plugin expects; put/fetch operate on
# that path directly. Modeled on ansible's community.general chroot plugin.

from __future__ import annotations

import os
import shutil
import subprocess

from ansible.errors import AnsibleError
from ansible.module_utils.common.process import get_bin_path
from ansible.module_utils.common.text.converters import to_bytes, to_native
from ansible.plugins.connection import ConnectionBase
from ansible.utils.display import Display

display = Display()


class Connection(ConnectionBase):
    """Execute inside a TrinityX osimage via lchroot-bwrap."""

    transport = "lchroot"
    has_pipelining = True

    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super().__init__(play_context, new_stdin, *args, **kwargs)
        # remote_addr is the image root path (dynamic_hosts sets ansible_host
        # to the image path for the chroot-style connection).
        self.chroot = self._play_context.remote_addr
        # locate lchroot: prefer the TrinityX python env, then PATH.
        candidates = [
            "/trinity/local/python/bin/lchroot",
            "/usr/local/bin/lchroot",
            "/usr/bin/lchroot",
        ]
        self.lchroot_cmd = next((c for c in candidates if os.path.exists(c)), None)
        if self.lchroot_cmd is None:
            try:
                self.lchroot_cmd = get_bin_path("lchroot")
            except ValueError:
                raise AnsibleError("lchroot not found (install luna2-utils lchroot-bwrap)")

    def _connect(self):
        super()._connect()
        if not self._connected:
            if not os.path.isdir(self.chroot):
                raise AnsibleError("%s is not a directory" % self.chroot)
            display.vvv("ESTABLISH LCHROOT CONNECTION FOR IMAGE: %s" % self.chroot,
                        host=self.chroot)
            self._connected = True

    def _build_argv(self, cmd):
        # lchroot --path <image> --force -- /bin/sh -c <cmd>
        #
        # The sandbox already presents itself as an offline chroot: lchroot sets
        # debian_chroot (-> Ansible is_chroot()=True) + SYSTEMD_OFFLINE=1 and binds an
        # offline `systemctl` shim onto PATH, so the service/systemd modules work with
        # no per-connection env here. See luna2-utils sandbox.py / systemctl-offline.
        return [
            self.lchroot_cmd, "--path", self.chroot, "--force",
            "--", "/bin/sh", "-c", cmd,
        ]

    def exec_command(self, cmd, in_data=None, sudoable=False):
        super().exec_command(cmd, in_data=in_data, sudoable=sudoable)
        argv = self._build_argv(cmd)
        display.vvv("EXEC (lchroot) %s" % cmd, host=self.chroot)
        p = subprocess.Popen(
            [to_bytes(a, errors="surrogate_or_strict") for a in argv],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, stderr = p.communicate(in_data)
        return p.returncode, stdout, stderr

    def _prefix_login_path(self, remote_path):
        if not remote_path.startswith(os.path.sep):
            remote_path = os.path.join(os.path.sep, remote_path)
        return os.path.normpath(remote_path)

    def put_file(self, in_path, out_path):
        super().put_file(in_path, out_path)
        display.vvv("PUT %s TO %s" % (in_path, out_path), host=self.chroot)
        out_path = self._prefix_login_path(out_path)
        dst = os.path.join(self.chroot, out_path.lstrip(os.path.sep))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(to_bytes(in_path), to_bytes(dst))
        except OSError as e:
            raise AnsibleError("failed to transfer file %s to %s: %s"
                               % (in_path, dst, to_native(e)))

    def fetch_file(self, in_path, out_path):
        super().fetch_file(in_path, out_path)
        display.vvv("FETCH %s TO %s" % (in_path, out_path), host=self.chroot)
        in_path = self._prefix_login_path(in_path)
        src = os.path.join(self.chroot, in_path.lstrip(os.path.sep))
        try:
            shutil.copyfile(to_bytes(src), to_bytes(out_path))
        except OSError as e:
            raise AnsibleError("failed to transfer file %s to %s: %s"
                               % (src, out_path, to_native(e)))

    def close(self):
        super().close()
        self._connected = False

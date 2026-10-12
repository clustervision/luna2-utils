"""ldebug: reading a rendered installer, masking, the dump tree and the mutation gate."""

import argparse
import base64
import json
import os
import stat
from pathlib import Path

import pytest

from utils import ldebug

FIXTURES = Path(__file__).parent / "fixtures" / "ldebug"
RENDER = (FIXTURES / "install.sh").read_text()


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()

    def json(self):
        return json.loads(self.text)


class FakeRest:
    """Answers routes from a table and records every call; never touches the network."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, route, auth=True):
        self.calls.append((route, auth))
        return FakeResponse(*self.responses.get(route, (404, '{"message": "not found"}')))

    def json(self, route, auth=True):
        response = self.get(route, auth)
        return response.json() if response.status_code == 200 else None

    def request_log(self):
        return ""


def output(tmp_path, dump=False, show_secrets=False):
    args = argparse.Namespace(raw=False, show_secrets=show_secrets, dump=dump,
                              dump_dir=str(tmp_path), subdir="node001")
    return ldebug.Output(args)


# --- reading the render -----------------------------------------------------

def test_variables_come_from_exports_and_banner():
    variables = ldebug.parse_variables(RENDER)
    assert variables["NODE"] == "node001"
    assert variables["_HOSTNAME"] == '"node001"'
    assert variables["INSTALL_MODE"].startswith("auto")
    assert variables["PROVISION_METHODS"] == "http http"
    assert variables["ROLES"] == ""


def test_names_are_split_from_the_local_lines():
    assert ldebug.parse_names(RENDER, "ROLES") == []
    render = "    local SCRIPTS=$(echo one,two | tr ',' ' ')\n"
    assert ldebug.parse_names(render, "SCRIPTS") == ["one", "two"]


def test_interfaces_resolve_the_installer_variables():
    interfaces = ldebug.parse_interfaces(RENDER)
    assert [iface["name"] for iface in interfaces] == ["BOOTIF", "eth0.1"]
    bootif, vlan = interfaces
    assert bootif["variables"] == {"interface_name": "${LUNA_BOOTIF}", "parent_name": ""}
    assert bootif["destination"].endswith("/Connection_${LUNA_BOOTIF}.nmconnection")
    assert "interface-name=${LUNA_BOOTIF}" in bootif["content"]
    assert vlan["variables"] == {"interface_name": "eth0.1", "parent_name": "${LUNA_BOOTIF}"}
    assert "interface-name=eth0.1" in vlan["content"] and "parent=${LUNA_BOOTIF}" in vlan["content"]
    assert 'config_network_init "eth0.1" "" "${LUNA_BOOTIF}"' in vlan["calls"]
    for iface in interfaces:
        assert "${interface_name}" not in iface["content"] and "${parent_name}" not in iface["content"]


def test_known_boot_nic_replaces_the_dracut_variable():
    bootif, vlan = ldebug.parse_interfaces(RENDER, "ens192")
    assert bootif["variables"]["interface_name"] == "ens192"
    assert "interface-name=ens192" in bootif["content"]
    assert vlan["variables"]["parent_name"] == "ens192" and "parent=ens192" in vlan["content"]
    assert "${LUNA_BOOTIF}" not in bootif["content"] + vlan["content"]


def test_vlan_on_a_bond_with_slaves_resolves_every_block():
    render = (FIXTURES / "change_net_bond.sh").read_text()
    blocks = {iface["name"]: iface for iface in ldebug.parse_interfaces(render)}
    assert set(blocks) == {"BOOTIF", "eth0.1", "eth1", "eth2", "bond0"}
    assert "mode=802.3ad" in blocks["bond0"]["content"] and "interface-name=bond0" in blocks["bond0"]["content"]
    for slave in ("eth1", "eth2"):
        assert "master=bond0" in blocks[slave]["content"] and f"interface-name={slave}" in blocks[slave]["content"]
    assert blocks["eth0.1"]["variables"]["parent_name"] == "bond0" and "parent=bond0" in blocks["eth0.1"]["content"]
    assert blocks["BOOTIF"]["variables"]["interface_name"] == "${LUNA_BOOTIF}"
    for iface in blocks.values():
        assert "${interface_name}" not in iface["content"] and "${parent_name}" not in iface["content"]


def test_rootmnt_resolves_from_the_render():
    render = ('    export rootmnt="/sysroot"\nfunction change_net {\n    interface_name=eth0\n    parent_name=\n'
              '    ######### eth0 #########\n      cat << LUNAEOF > "/lunatmp/interface_${interface_name}.conf"\n'
              '#FILE ${rootmnt}/etc/netplan/${interface_name}_config.yaml\nnetwork:\nLUNAEOF\n}\n')
    (eth0,) = ldebug.parse_interfaces(render)
    assert eth0["destination"] == "/sysroot/etc/netplan/eth0_config.yaml"
    (eth0,) = ldebug.parse_interfaces(render.replace('"/sysroot"', '"${rootmnt:-${NEWROOT:-/sysroot}}"'))
    assert eth0["destination"] == "/sysroot/etc/netplan/eth0_config.yaml"
    assert ldebug.bash_default("plain") == "plain"


def test_boot_nic_comes_from_the_newest_inband_inventory():
    inventory = {"config": {"node": {"node001": {"inventory": [
        {"source": "redfish", "updated": "2026-10-09 10:00:00", "nics": [{"name": "NIC.1", "mac": "00:11:22:33:44:55"}]},
        {"source": "inband", "updated": "2026-10-01 10:00:00", "nics": [{"name": "eno1", "mac": "00:11:22:33:44:55"}]},
        {"source": "inband", "updated": "2026-10-08 10:00:00", "nics": [{"name": "ens192", "mac": "00:11:22:33:44:55"}]},
    ]}}}}
    rest = FakeRest({"config/node/node001/inventory": (200, json.dumps(inventory))})
    assert ldebug.boot_nic(rest, "node001", "00:11:22:33:44:55") == ("ens192", "inband", "2026-10-08 10:00:00")
    assert ldebug.boot_nic(rest, "node001", "aa:bb:cc:dd:ee:ff") == ("", "", "")
    assert ldebug.boot_nic(FakeRest({}), "node001", "00:11:22:33:44:55") == ("", "", "")
    before = len(rest.calls)
    assert ldebug.boot_nic(rest, "node001", "") == ("", "", "")
    assert len(rest.calls) == before


def test_assignments_follow_bash_including_the_bootif_branch_and_mac_lookup():
    env = {"LUNA_BOOTIF": "${LUNA_BOOTIF}"}
    lines = ['if [ -n "${LUNA_BOOTIF}" ]; then', '    interface_name=${LUNA_BOOTIF}', 'else',
             '    interface_name=BOOTIF', 'fi', 'interface_bootif=$interface_name',
             'interface_name=$(get_interface_by_mac "eth1" "aa:bb:cc:dd:ee:01")',
             'parent_name=$interface_bootif']
    ldebug.walk_assignments(lines, env)
    assert env["interface_bootif"] == "${LUNA_BOOTIF}"
    assert env["interface_name"] == "<NIC with MAC aa:bb:cc:dd:ee:01, else eth1>"
    assert env["parent_name"] == "${LUNA_BOOTIF}"
    assert ldebug.resolve_value('$(get_interface_by_mac "non_existing" "aa:bb:cc:dd:ee:02")', env) == "<NIC with MAC aa:bb:cc:dd:ee:02>"
    assert ldebug.resolve_value("bond0", env) == "bond0"


def test_heredoc_and_function_bodies():
    render = "cat << 'LUNAEOF' > /lunatmp/prescript.sh\necho pre\nLUNAEOF\nfunction bmcsetup {\n    USERNAME=\"a\"\n}\n"
    assert ldebug.parse_heredoc(render, "/lunatmp/prescript.sh") == "echo pre"
    assert ldebug.parse_function(render, "bmcsetup") == '    USERNAME="a"\n'
    assert ldebug.parse_function(render, "missing") == ""


def test_inline_inputs_are_decoded_by_destination():
    assert not any(ldebug.parse_inputs(RENDER).values())
    fstab = base64.b64encode(b"nfs:/home /home nfs defaults 0 0").decode()
    layout = base64.b64encode(b'{"disks": []}').decode()
    render = (f'echo "{fstab}" | base64 -d | while read -r MLINE; do\n'
              f'echo "{layout}" | base64 -d > /tmp/install_files/runtime/storage.json\n'
              'echo "" | base64 -d > "/${rootmnt}/etc/exports.d/luna.exports"\n')
    inputs = ldebug.parse_inputs(render)
    assert inputs["fstab"].startswith("nfs:/home")
    assert json.loads(inputs["disklayout"]) == {"disks": []}
    assert inputs["exports"] == ""


# --- masking and decoding ---------------------------------------------------

def test_masking_hides_token_and_passwords_but_keeps_length():
    shown = ldebug.mask_text(RENDER)
    assert "fixture.token.value" not in shown
    assert "fixture-bmc-pw" not in shown
    assert "API_PASSWORD=luna\n" not in shown
    assert f'export LUNA_TOKEN="***len={len("fixture.token.value")}"' in shown
    assert 'if [ "$API_PASSWORD" == "" ]' in shown


def test_json_masking_only_touches_the_named_keys():
    data = {"config": {"secrets": {"group": {"g": [{"name": "k", "content": "abc", "path": "/p"}]}}}}
    masked = ldebug.mask_json(data, keys=("content",))
    assert masked["config"]["secrets"]["group"]["g"][0]["content"] == "***len=3"
    assert masked["config"]["secrets"]["group"]["g"][0]["path"] == "/p"
    assert ldebug.mask_json(data)["config"]["secrets"]["group"]["g"][0]["content"] == "abc"


def test_base64_fields_decode_when_they_are_text():
    unit = base64.b64encode(b"[Unit]\nDescription=x\n").decode()
    binary = base64.b64encode(bytes(range(256))).decode()
    data = {"role": {"r": {"unit": unit, "script": binary, "plain": "hello"}}}
    decoded = ldebug.decode_fields(data)
    assert decoded["role"]["r"]["unit"].startswith("[Unit]")
    assert decoded["role"]["r"]["script"] == binary
    assert decoded["role"]["r"]["plain"] == "hello"


# --- output and dump --------------------------------------------------------

def test_sections_print_indented_and_dump_as_files(tmp_path, capsys):
    out = output(tmp_path, dump=True, show_secrets=True)
    out.section("Pre script", "echo a\necho b", 200, filename="22-prescript.sh")
    out.section("Network BOOTIF", "[connection]", filename="30-network/BOOTIF/Connection_BOOTIF.nmconnection")
    printed = capsys.readouterr().out
    assert "  echo a\n  echo b\n" in printed
    run = Path(out.directory)
    assert run.parent.name == "node001"
    assert stat.S_IMODE(os.stat(run).st_mode) == 0o700
    assert (run / "22-prescript.sh").read_text() == "echo a\necho b\n"
    assert stat.S_IMODE(os.stat(run / "22-prescript.sh").st_mode) == 0o600
    assert (run / "30-network" / "BOOTIF" / "Connection_BOOTIF.nmconnection").read_text() == "[connection]\n"


def test_two_dumps_in_one_second_get_their_own_directories(tmp_path, monkeypatch):
    class FrozenClock:
        @staticmethod
        def now():
            return ldebug.datetime(2026, 10, 12, 18, 46, 10)

    monkeypatch.setattr(ldebug, "datetime", FrozenClock)
    first = output(tmp_path, dump=True)
    second = output(tmp_path, dump=True)
    assert first.directory.endswith("/node001/20261012-184610")
    assert second.directory == first.directory + "-1"
    assert os.path.isdir(second.directory)


def test_failed_status_is_remembered(tmp_path, capsys):
    out = output(tmp_path)
    out.section("x", "", 200)
    assert not out.failed
    out.section("y", "", 404)
    assert out.failed
    assert out.directory is None
    capsys.readouterr()


# --- stages -----------------------------------------------------------------

def test_foreign_mac_is_refused_before_any_request(tmp_path, capsys):
    rest = FakeRest({})
    facts = {"mac": "00:11:22:33:44:55", "provision_interface": "BOOTIF"}
    with pytest.raises(SystemExit):
        ldebug.stage_discover(rest, output(tmp_path), "node001", facts, "aa:bb:cc:dd:ee:ff", False)
    assert rest.calls == []
    assert "--allow-mutation" in capsys.readouterr().err


def test_own_mac_is_discovered_without_a_token(tmp_path, capsys):
    rest = FakeRest({"boot/search/mac/00:11:22:33:44:55": (200, (FIXTURES / "nodeboot.ipxe").read_text())})
    facts = {"mac": "00:11:22:33:44:55", "provision_interface": "BOOTIF"}
    ldebug.stage_discover(rest, output(tmp_path), "node001", facts, "", False)
    assert rest.calls == [("boot/search/mac/00:11:22:33:44:55", False)]
    assert "imgargs kernel" in capsys.readouterr().out


def test_node_without_mac_skips_discovery(tmp_path, capsys):
    rest = FakeRest({})
    facts = {"mac": "", "provision_interface": "BOOTIF"}
    ldebug.stage_discover(rest, output(tmp_path), "node001", facts, "", False)
    assert rest.calls == []
    assert "skipped" in capsys.readouterr().out


def test_install_stage_splits_the_render_and_dumps_it(tmp_path, capsys):
    inventory = {"config": {"node": {"node001": {"inventory": [
        {"source": "inband", "updated": "2026-10-08 10:00:00", "nics": [{"name": "ens192", "mac": "00:11:22:33:44:55"}]}]}}}}
    rest = FakeRest({"boot/install/node001": (200, RENDER),
                     "config/node/node001/inventory": (200, json.dumps(inventory))})
    out = output(tmp_path, dump=True)
    render = ldebug.stage_install(rest, out, "node001", {"imagefile": "", "mac": "00:11:22:33:44:55"})
    assert render == RENDER
    printed = capsys.readouterr().out
    for title in ("Install /boot/install/node001", "Variables", "Pre script", "BMC", "Network BOOTIF", "Network eth0.1"):
        assert title in printed
    assert "fixture.token.value" not in printed
    assert "interface-name=ens192" in printed and "boot NIC ens192 taken from the node's inband inventory" in printed
    run = Path(out.directory)
    dumped = (run / "20-install.sh").read_text()
    assert "fixture.token.value" not in dumped and "***len=" in dumped
    assert (run / "30-network" / "eth0.1" / "Connection_eth0.1.nmconnection").exists()


def test_kickstart_image_uses_the_kickstart_route(tmp_path, capsys):
    rest = FakeRest({"kickstart/install/node001": (200, "#ks\n")})
    assert ldebug.stage_install(rest, output(tmp_path), "node001", {"imagefile": "kickstart", "mac": ""}) == "#ks\n"
    assert rest.calls[0][0] == "kickstart/install/node001"
    capsys.readouterr()


def test_fetch_stage_decodes_each_named_item(tmp_path, capsys):
    script = base64.b64encode(b"echo part\n").decode()
    rest = FakeRest({"boot/scripts/s1": (200, json.dumps({"script": {"s1": {"partscript": script}}}))})
    out = output(tmp_path, dump=True)
    ldebug.stage_fetch(rest, out, "scripts", ["s1"], "boot/scripts", "50")
    printed = capsys.readouterr().out
    assert "Scripts s1 partscript" in printed and "  echo part\n" in printed
    assert (Path(out.directory) / "50-scripts" / "s1" / "partscript").read_text() == "echo part\n"


def test_leaves_walk_dicts_and_lists():
    data = {"profile": {"p": {"files": [{"path": "/a", "content": "x"}]}}}
    assert ldebug.leaves(data) == [(("profile", "p", "files", "0", "path"), "/a"),
                                   (("profile", "p", "files", "0", "content"), "x")]


def test_node_without_secrets_is_not_a_failure(tmp_path, capsys):
    out = output(tmp_path)
    ldebug.stage_secrets(FakeRest({}), out, "node001")
    assert not out.failed and "no secrets for node001" in capsys.readouterr().out
    secrets = {"config": {"secrets": {"group": {"g": [{"name": "k", "content": "abc", "path": "/p"}]}}}}
    ldebug.stage_secrets(FakeRest({"config/secrets/node/node001": (200, json.dumps(secrets))}), out, "node001")
    printed = capsys.readouterr().out
    assert "***len=3" in printed and "abc" not in printed


def test_diff_compares_with_the_newest_earlier_dump_or_a_named_one(tmp_path, capsys):
    def dump(stamp, files):
        directory = tmp_path / "node001" / stamp
        for relative, text in files.items():
            (directory / relative).parent.mkdir(parents=True, exist_ok=True)
            (directory / relative).write_text(text)
        return str(directory)

    first = dump("20261001-100000", {"21-variables.env": "NODE=node001\nROLES=\n", "22-prescript.sh": "echo pre\n",
                                     "requests.log": "GET /boot 200\n", "diff.txt": "old diff\n"})
    second = output(tmp_path)
    second.directory = dump("20261002-100000", {"21-variables.env": "NODE=node001\nROLES=default\n",
                                                "60-roles/default/unit": "[Unit]\n", "requests.log": "GET /boot 200 again\n"})
    ldebug.stage_diff(second, "latest")
    printed = capsys.readouterr().out
    assert "1 changed, 1 added, 1 removed" in printed
    assert "-ROLES=\n" in printed and "+ROLES=default" in printed
    assert "added:   60-roles/default/unit" in printed and "removed: 22-prescript.sh" in printed
    assert "requests.log" not in printed and "diff.txt" not in printed
    written = (Path(second.directory) / "diff.txt").read_text()
    assert "+ROLES=default" in written
    assert ldebug.earlier_dump(second.directory, "latest") == first
    assert ldebug.earlier_dump(second.directory, str(tmp_path / "nowhere")) == ""
    ldebug.stage_diff(second, str(tmp_path / "nowhere"))
    assert "is not a directory" in capsys.readouterr().out
    only = output(tmp_path / "other", dump=True)
    ldebug.stage_diff(only, "latest")
    assert "no earlier dump" in capsys.readouterr().out


def test_diff_implies_dump_in_the_parser():
    args = ldebug.get_parser().parse_args(["boot", "node001", "--diff"])
    assert args.diff == "latest" and not args.dump
    args = ldebug.get_parser().parse_args(["boot", "node001", "--diff", "/var/log/luna/ldebug/node001/x"])
    assert args.diff == "/var/log/luna/ldebug/node001/x"


def test_global_options_work_before_and_after_the_area():
    parse = ldebug.get_parser().parse_args
    after = parse(["boot", "node001", "--dump", "--show-secrets", "-d", "/x", "--no-color"])
    assert after.dump and after.show_secrets and after.dump_dir == "/x" and after.no_color and not after.raw
    before = parse(["--dump", "-d", "/y", "boot", "node001", "--stage", "status"])
    assert before.dump and before.dump_dir == "/y" and not before.show_secrets
    plain = parse(["boot", "node001"])
    assert not plain.dump and plain.dump_dir == ldebug.DUMP_DIR and not plain.verbose
    api = parse(["api", "GET", "/boot", "--no-auth", "--raw"])
    assert api.raw and api.no_auth and not api.dump


def test_parser_builds_without_a_daemon():
    args = ldebug.get_parser().parse_args(["--dump", "boot", "node001", "--stage", "menu,status"])
    assert args.area == "boot" and args.stage == "menu,status" and args.dump
    args = ldebug.get_parser().parse_args(["api", "GET", "/monitor/queue", "--no-auth"])
    assert args.method == "GET" and args.no_auth

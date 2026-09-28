# luna2-utils

Command-line utilities for Luna, the provisioning and cluster management system of
TrinityX. They run on the controllers, beside the `luna` command (luna2-cli), and cover
the jobs an administrator does outside the object model: entering and customising OS
images, node consoles, power, racks and cluster health.

## Features

- Safe customisation of OS images with `lchroot`: a sandbox that protects the controller,
  per-image locking, read-only inspection, and images of other architectures through
  qemu emulation.
- Serial-over-LAN console to any node through its BMC, over IPMI or Redfish, with
  diagnosis of a blank console.
- Power control of nodes, groups and racks.
- Rack management: racks, device placement and elevations, with import and export.
- Cluster health at a glance: power state, install state and Slurm state per node.
- BMC system event log, Redfish boot order and a diagnosis of the TrinityX services.
- Export and import of the cluster configuration and of OS images.
- High-availability master state of the controllers.
- A support report with logs, system and cluster information in one archive.
- Bash completion for `lchroot`, `lconsole` and `lrack`.

## Commands

| Command | Purpose |
|---|---|
| `lchroot` | Enter an OS image in a sandbox to customise or inspect it |
| `lconsole` | Interactive Serial-over-LAN console of a node, over IPMI or Redfish |
| `lpower` | Power status, on, off, reset, cycle and identify, for nodes, a group or a rack |
| `lrack` | Racks and the placement of devices in them, as in the TrinityX Rack View |
| `lcluster` | Health and status of the cluster nodes: power, Luna install state and Slurm |
| `lnode` | List or clear the BMC system event log of nodes |
| `bootutil` | List, read or set the boot order of a node over Redfish |
| `trinity_diagnosis` | Status of the TrinityX services and modules (`trix-diag` when installed with pip) |
| `lmaster` | Show which controller is the HA master, the HA state of all controllers, or make the controller in `luna.ini` master |
| `lexport` | Export and import the cluster configuration or an OS image |
| `lsosreport` | Collect logs, system and cluster information into one archive for support |
| `qemu-static` | Register qemu emulation for a foreign architecture outside `lchroot` |
| `lchroot-legacy` | The previous `lchroot`, kept for compatibility |

Run any command with `--help` for its options.

## lchroot

`lchroot <osimage>` opens a shell inside an OS image; `lchroot <osimage> <command>` runs a
single command there. The image is resolved through the Luna API.

- The image is entered in a bubblewrap sandbox. Its mounts live in a namespace that ends
  with the session, so nothing of the controller's `/dev`, `/proc` or `/sys` is left
  mounted inside the image afterwards.
- One session per image at a time. `--status` shows who holds an image; `--force` stops
  the holder and enters.
- `--ro` enters the image read-only for inspection. `--dry-run` shows the plan and runs
  nothing.
- Images of another architecture are entered through qemu user emulation; `--no-emulate`
  refuses instead.
- On a high-availability pair, write access is only given on the active controller.
- `--path <directory>` enters a directory that is not a Luna image, without contacting
  the daemon.

The design decisions behind lchroot are recorded in [docs/lchroot/](docs/lchroot/).

## Configuration

The utilities read the daemon address and API account from
`/trinity/local/luna/utils/config/luna.ini` (`lrack` also accepts another file through
the `LRACK_INI` environment variable).

**On a TrinityX cluster, the TrinityX installer writes a fresh
`/trinity/local/luna/utils/config/luna.ini` on every run and replaces whatever the file
contained.** Change settings through the TrinityX configuration, not in the file on the
controller. The utilities do not depend on TrinityX: with any Luna daemon, `luna.ini` is
maintained by hand.

---

Luna2 command line utils (bootutil, lchroot, lpower, lcluster).<br />

## Explanation

This project is a part of luna project. Luna2 Utils have all kind of necessary utilities for the luna project. such as:<br />

1. bootutil <br />
2. lchroot <br />
3. lpower <br />
4. lcluster <br />
5. lnode <br />
6. lrack <br />
7. lconsole <br />
8. trinity_diagnosis <br />

After installing the Luna 2 Utils via pip, all those commands will be available for further use.<br />

## Contributing

Please read the [contribution guidelines](Guidelines.rst) before submitting changes, including the legal terms that apply to all contributions.


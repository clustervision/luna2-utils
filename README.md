# Luna 2 Utils

Luna2 command line utils (bootutil, lchroot, lpower, lcluster, lslurm).<br />

## Explanation

This project is a part of luna project. Luna2 Utils have all kind of necessary utilities for the luna project. such as:<br />

1. bootutil <br />
2. lchroot <br />
3. lpower <br />
4. lcluster <br />
5. lslurm <br />
6. limport <br />
7. lnode <br />
8. lrack <br />
9. trinity_diagnosis <br />

After installing the Luna 2 Utils via pip, all those commands will be available for further use.<br />
To use lslurm, kindly locate your installation directory. There is a file called slurm.ini<br />
Slurm.ini file is a configuration of the connection for slurm.<br />

## lrack

`lrack` manages racks and the placement of devices inside them from the command line,
mirroring the TrinityX OOD Rack View. It is a client of the luna2-daemon rack API.

```
lrack list                                  # list racks with utilisation
lrack show [RACK ...]                        # show racks and the devices in them
lrack add RACK [-s 42] [-d ascending] [-m ROOM] [-t SITE]
lrack change RACK [-s ...] [-d ...] [-m ...] [-t ...]
lrack rename RACK NEWNAME
lrack remove RACK
lrack place DEVICE -r RACK [-p U] [-o front|back] [-H U] [-f]   # -p omitted: auto-stack
lrack unplace DEVICE
lrack resize DEVICE -H U
lrack orient DEVICE -o front|back
lrack inventory [configured|unconfigured]
lrack pool                                   # placeable (unconfigured) devices
```

DEVICE accepts a hostlist range, e.g. `node[001-010]`. When `place` is given no
`-p/--position`, devices auto-fill the first free slots (following the rack numbering
order).

`show` scales its detail to the number of racks and the terminal width:

| Racks | View |
|-------|------|
| 1     | full ASCII elevation |
| 2–5   | side-by-side elevations (wrap to bands) |
| >5    | one fill-gauge line per rack + totals |

Named racks (`lrack show a01 a02`) always render as elevations. Force a level with
`-F/--full`, `-s/--summary`, `-M/--map` (per-U heatmap), and tune layout with
`-c/--columns N` and `-w/--width N`.

### Easy syntax

For quick, scriptable changes there is a positional shorthand:

```
lrack node[001-020] in rack01            # place, auto-stacking into free slots
lrack node001 in rack01 at 5 back        # place at U5, orientation back
lrack node001 out                        # unplace
lrack rack01                             # bare rack name: show its elevation
```

### Bulk import / export (JSON)

The whole rack layout (and device inventory) round-trips as JSON:

```
lrack -e                       # export everything to STDOUT
lrack -e layout.json           # export to a file (-f to overwrite)
lrack -e -r rack01 rack01.json # export a single rack
lrack -i layout.json           # import / apply a layout
```

Import is idempotent (existing racks/devices are updated). A placement that exceeds its
rack size is declined with an error and nothing is applied; overlaps need `-f/--force`.

### Command-line completion

Copy `utils/addons/lrack_completion.sh` to `/etc/bash_completion.d/lrack` and start a new
shell. For live rack/device name completion, also enable argcomplete:

```
eval "$(register-python-argcomplete lrack)"
```


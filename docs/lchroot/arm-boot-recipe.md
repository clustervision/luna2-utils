# Boot the packed compute-arm image on an aarch64 host (KVM, headless)

The aarch64 image was built end-to-end via lchroot and packed. To prove it boots,
run it as a VM on **any aarch64 Linux host** (native KVM → fast). This recipe does a
**direct-kernel boot** off a local virtio disk using a *no-luna* initramfs (so it
boots standalone instead of trying to network-provision via luna).

---

## ✅ What actually worked (2026-06-03) — x86, no aarch64 hardware, no container

We had no aarch64 box and `qemu-system-aarch64` is **not packaged on Rocky** (`qemu-kvm` is
host-arch-only; the bundled Fedora qemu is *user-mode*; a Fedora/openSUSE qemu-system *binary*
won't run on Rocky's glibc 2.34). The fix that worked, end to end:

1. **Build `qemu-system-aarch64` from source ON the Rocky box** (so it links Rocky's glibc → runs
   natively). On the controller:
   ```sh
   dnf install -y gcc gcc-c++ make ninja-build python3-pip glib2-devel pixman-devel zlib-devel bzip2
   pip3 install meson
   curl -O https://download.qemu.org/qemu-10.1.5.tar.xz && tar xf qemu-10.1.5.tar.xz && cd qemu-10.1.5
   ./configure --target-list=aarch64-softmmu --disable-docs --disable-werror
   ninja -C build qemu-system-aarch64          # ~10 min on 4 cores; binary at build/qemu-system-aarch64
   strip build/qemu-system-aarch64             # 114 MB -> ~33 MB
   ```
2. **Run the VM on a roomy compute node, not the controller.** The controller `/` was ~94 % full; a
   4.5 GB rootfs disk won't fit and filling a live controller is dangerous. **node001** had 15 GB free
   and is destroyable. Copy `build/qemu-system-aarch64` + the source `pc-bios/` (for `efi-virtio.rom`
   etc.) to `node001:/root/armboot/qemu/`; its runtime deps (glib2, pixman, zlib) are already present.
3. **Boot it (TCG, no KVM cross-arch)** with the standalone initramfs + fresh rootfs. Two gotchas that
   bit us: the source-built qemu had **no slirp** → drop `-netdev user`/`virtio-net` (no NIC needed);
   and a single-target build's `build/pc-bios` is nearly empty → use the **source** `pc-bios` and pass
   `-L`. Working invocation (serial exposed over telnet so you can log in from anywhere):
   ```sh
   /root/armboot/qemu/qemu-system-aarch64 -L /root/armboot/qemu/pc-bios \
     -machine virt -cpu max -smp 2 -m 3072 \
     -kernel vmlinuz-<KVER> -initrd initramfs-standalone.img \
     -append "root=/dev/vda rw console=ttyAMA0 selinux=0 audit=0 rd.timeout=60" \
     -drive file=root.img,if=virtio,format=raw \
     -chardev socket,id=s0,host=0.0.0.0,port=5555,telnet=on,server=on,wait=off,logfile=console.log \
     -serial chardev:s0 -display none -no-reboot
   ```
   **Result:** `Rocky Linux 9.8 … aarch64 … localhost login:` (root / `armtest123`). Attach to the live
   VM from any terminal that can reach the node: `telnet node001 5555`. (`munge` fails at boot — no
   luna-provisioned key in a standalone boot; expected, not an image defect.)

The KVM-aware `armboot-driver.sh` below remains the canonical path for a real **aarch64** host; the
steps above are the **x86/TCG** adaptation that needs no aarch64 hardware and no container.

---

## 0. Artifacts (already built — copy these to the aarch64 host)

Canonical copies on the **controller**:

| File | Path on controller | What |
|---|---|---|
| rootfs tarball | `/trinity/local/luna/files/compute-arm-1780473014.tar.bz2` (2.2 GB) | packed root filesystem |
| kernel | `/trinity/images/compute-arm/boot/vmlinuz-5.14.0-687.12.1.el9_8.aarch64` | aarch64 kernel |
| no-luna initramfs | `/root/armboot-initramfs-standalone.img` | initramfs with luna omitted, virtio+ext4 kept |
| driver script | `/root/armboot-driver.sh` | builds disk + boots + watches console |

(All four are also staged on **node001** under `/root/armboot/` — there the initramfs is
already named `initramfs-standalone.img`.)

Put them in one directory on the aarch64 host, e.g. `/root/armboot/`, with these **exact names**:

```sh
mkdir -p /root/armboot && cd /root/armboot
# from the controller (adjust host):
scp CONTROLLER:/trinity/local/luna/files/compute-arm-1780473014.tar.bz2 .
scp CONTROLLER:/trinity/images/compute-arm/boot/vmlinuz-5.14.0-687.12.1.el9_8.aarch64 .
scp CONTROLLER:/root/armboot-initramfs-standalone.img ./initramfs-standalone.img
scp CONTROLLER:/root/armboot-driver.sh /root/armboot-driver.sh
```

## 1. Install qemu on the aarch64 host

```sh
dnf -y install qemu-kvm qemu-img        # RHEL/Rocky/Alma   (qemu-kvm provides qemu-system-aarch64 natively)
# Debian/Ubuntu:   apt-get install -y qemu-system-arm qemu-utils
ls -l /dev/kvm                           # must exist + be writable for acceleration
```

## 2. Run the driver (builds the disk, boots, watches the console)

```sh
chmod +x /root/armboot-driver.sh
sudo /root/armboot-driver.sh /root/armboot
```

It will:
1. build a 9 GB ext4 disk and extract the rootfs into it;
2. point root at `/dev/vda`, disable SELinux, set a known root password (`armtest123`),
   ensure a serial getty;
3. boot the guest **with KVM** (auto-detected on aarch64) and stream the serial console
   to `/root/armboot/console.log`;
4. print `RESULT: yes` when it sees `login:` / multi-user, or `fail` on a panic/mount error.

**Success looks like:** `RESULT: yes` and a console tail ending in a `compute-arm login:`
prompt. Log in as `root` / `armtest123` and poke around (`systemctl`, `uname -m` → aarch64,
`sinfo`/`slurmd -V`, etc.).

## 3. Manual boot (if you'd rather run qemu yourself)

```sh
# build a disk first (driver step 1–2), then:
qemu-system-aarch64 -machine virt,gic-version=host -cpu host -enable-kvm -smp 4 -m 4096 \
  -kernel  /root/armboot/vmlinuz-5.14.0-687.12.1.el9_8.aarch64 \
  -initrd  /root/armboot/initramfs-standalone.img \
  -append  "root=/dev/vda rw console=ttyAMA0 selinux=0" \
  -drive   file=/root/armboot/root.img,if=virtio,format=raw \
  -netdev  user,id=n0 -device virtio-net-pci,netdev=n0 \
  -nographic                       # Ctrl-A X to quit
```

On an **x86 host** instead, drop `-enable-kvm` and use `-cpu max` (pure TCG — works, just
slow). You need a `qemu-system-aarch64` that runs on Rocky: it is **not packaged** (qemu-kvm is
host-arch-only) and a foreign distro's *binary* won't link against Rocky's glibc 2.34 — so **build it
from source on the Rocky box** (see §"What actually worked" above). This is the path proven on
2026-06-03 with no aarch64 hardware.

## Notes

- **Why a separate initramfs?** The packed image's own initramfs carries the **luna** dracut
  module, which would try to PXE/network-provision. `initramfs-standalone.img` was regenerated
  via `lchroot --path … dracut --omit "luna nfs …"` so it mounts a plain local disk instead.
- **Real provisioning path** (not this test): a node PXE-boots, luna serves the kernel +
  the original (luna) initramfs + the rootfs tarball over the network — no local disk image.
- This recipe is a **functional boot test**, not the production deployment.

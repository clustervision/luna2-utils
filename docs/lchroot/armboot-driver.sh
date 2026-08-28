#!/bin/bash
# Boot the packed TrinityX compute-arm (aarch64) image as a headless VM and watch
# the serial console for a successful userspace boot. Auto-uses KVM on an aarch64
# host (fast) or TCG emulation elsewhere (slow but works).
#
# Needs in $D (default: this script's dir):
#   compute-arm-*.tar.bz2          packed rootfs
#   vmlinuz-<KVER>                 aarch64 kernel
#   initramfs-standalone.img       no-luna initramfs (boots from a local disk)
# Run as root (loop-mount + qemu).  Success = "login:" / multi-user on the console.
set -eu
D="${1:-$(cd "$(dirname "$0")" && pwd)}"
KVER=5.14.0-687.12.1.el9_8.aarch64
TARBALL=$(ls -1 "$D"/compute-arm-*.tar.bz2 | head -1)
DISK="$D/root.img"
CONSOLE="$D/console.log"
ROOTPW="${ROOTPW:-armtest123}"

echo "== [1/4] build ext4 disk from $(basename "$TARBALL") =="
rm -f "$DISK"; truncate -s 9G "$DISK"; mkfs.ext4 -q -F "$DISK"
mkdir -p /mnt/arm; mount -o loop "$DISK" /mnt/arm
tar -xjf "$TARBALL" -C /mnt/arm
echo "   extracted $(du -sh /mnt/arm | cut -f1)"

echo "== [2/4] prep rootfs for standalone disk boot =="
printf '/dev/vda / ext4 defaults 0 1\n' > /mnt/arm/etc/fstab          # local root, no luna/NFS
[ -f /mnt/arm/etc/selinux/config ] && sed -i 's/^SELINUX=.*/SELINUX=disabled/' /mnt/arm/etc/selinux/config || true
HASH=$(openssl passwd -6 "$ROOTPW")                                    # known root pw for the test
sed -i "s#^root:[^:]*:#root:${HASH//#/\\#}:#" /mnt/arm/etc/shadow
ln -sf /usr/lib/systemd/system/serial-getty@.service \
   /mnt/arm/etc/systemd/system/getty.target.wants/serial-getty@ttyAMA0.service 2>/dev/null || true
sync; umount /mnt/arm

echo "== [3/4] boot aarch64 guest, serial -> $CONSOLE =="
: > "$CONSOLE"
QEMU="${QEMU:-qemu-system-aarch64}"
[ -n "${QEMU_PREFIX:-}" ] && export LD_LIBRARY_PATH="$QEMU_PREFIX/usr/lib64:${LD_LIBRARY_PATH:-}"
# Native aarch64 + KVM -> hardware accel; otherwise cross-arch TCG emulation.
if [ "$(uname -m)" = "aarch64" ] && [ -w /dev/kvm ]; then
  MACHINE="-machine virt,gic-version=host -cpu host -enable-kvm -smp 4 -m 4096"
  echo "   acceleration: KVM (native aarch64)"
else
  MACHINE="-machine virt -cpu max -smp 2 -m 3072"
  echo "   acceleration: TCG (slow cross-arch emulation)"
fi
echo "   $($QEMU --version 2>&1 | head -1)"
timeout 1200 $QEMU $MACHINE \
  -kernel "$D/vmlinuz-$KVER" -initrd "$D/initramfs-standalone.img" \
  -append "root=/dev/vda rw console=ttyAMA0 selinux=0 audit=0 systemd.show_status=1 rd.timeout=60" \
  -drive file="$DISK",if=virtio,format=raw \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -display none -serial file:"$CONSOLE" -no-reboot &
QPID=$!

echo "== [4/4] watch console (up to ~15 min) =="
ok=""
for i in $(seq 1 180); do
  sleep 5
  grep -qiE 'login:|Reached target .*(Multi-User|Login)|Startup finished|Welcome to' "$CONSOLE" && { ok=yes; break; }
  grep -qiE 'Kernel panic|Cannot open root|Unable to mount root|dracut: FATAL|Entering emergency mode' "$CONSOLE" && { ok=fail; break; }
  kill -0 $QPID 2>/dev/null || { ok=exited; break; }
done
kill $QPID 2>/dev/null || true; wait $QPID 2>/dev/null || true
echo "== RESULT: ${ok:-timeout}  (root password: $ROOTPW) =="
echo "----- console tail -----"; tail -40 "$CONSOLE"

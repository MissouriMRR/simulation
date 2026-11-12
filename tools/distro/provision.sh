#!/bin/sh
# Runs as root inside the fresh Ubuntu distro that tools/build_distro.py is preparing.
# Installs rootless podman (the same set of packages the development distro runs on) and creates the
# student user. Nothing here refers to any person: the user is called "mrr".
set -eu
export DEBIAN_FRONTEND=noninteractive
USER_NAME=mrr

apt-get update
apt-get install -y --no-install-recommends \
    podman catatonit uidmap slirp4netns passt fuse-overlayfs crun conmon netavark aardvark-dns

if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --comment "MRRSimulator" "$USER_NAME"
fi
passwd -l "$USER_NAME" >/dev/null   # no password: wsl.exe starts commands as this user without logging in

# rootless containers need a range of sub-ids; useradd normally adds them, so only fill in if missing
grep -q "^$USER_NAME:" /etc/subuid || usermod --add-subuids 100000-165535 "$USER_NAME"
grep -q "^$USER_NAME:" /etc/subgid || usermod --add-subgids 100000-165535 "$USER_NAME"

# systemd on (rootless podman uses it for cgroups), and every wsl.exe command runs as the student user
printf '[boot]\nsystemd=true\n\n[user]\ndefault=%s\n' "$USER_NAME" > /etc/wsl.conf

echo "provisioned: $(podman --version), user $(id "$USER_NAME")"

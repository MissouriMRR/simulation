#!/bin/sh
# Runs as root as the last step before the distro is exported: remove caches and anything machine-specific,
# so the payload is small and identical for every student.
set -eu
apt-get clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/cache/debconf/*-old /var/lib/dpkg/*-old
rm -rf /tmp/* /var/tmp/* /root/.cache /root/.bash_history /home/mrr/.cache /home/mrr/.bash_history
rm -rf /var/log/journal/* /var/log/apt/* /var/log/*.log /var/log/alternatives.log 2>/dev/null || true
# a new id is made at the first boot of each installed copy
: > /etc/machine-id
rm -f /var/lib/dbus/machine-id
# leave nothing owned by the build machine's user ids outside the student's own home
chown -R mrr:mrr /home/mrr
echo "cleaned; root filesystem $(df -h / | awk 'NR==2 {print $3}') used"

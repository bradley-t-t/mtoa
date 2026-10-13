#!/bin/sh
# Install the Sunday agent on the cube and make it the only application there.
set -e
cd "$(dirname "$0")/.."
ssh cube 'mkdir -p /data/sunday/lib'
cat cube/sunday_cube.py | ssh cube 'cat > /data/sunday/sunday_cube.py'
# The settings are only installed when missing: the volume buttons write to them. The
# server is the host's LAN address and voice port from the owner's profile.
SERVER="ws://$(python3 server/mtoa_config.py get host.lan_ip "$(python3 server/mtoa_config.py get host.lan_name localhost)"):$(python3 server/mtoa_config.py get host.voice_port 8794)"
sed "s|@VOICE_SERVER@|$SERVER|" cube/config.json | ssh cube '[ -f /data/sunday/config.json ] || cat > /data/sunday/config.json'
for f in cube/lib/*; do cat $f | ssh cube "cat > /data/sunday/lib/$(basename $f)"; done
cat cube/S99sunday | ssh cube 'cat > /etc/init.d/S99sunday; chmod 755 /etc/init.d/S99sunday'
cat cube/adckey_function.sh | ssh cube 'cat > /etc/adckey/adckey_function.sh; chmod 755 /etc/adckey/adckey_function.sh'
ssh cube 'sh -s' < deploy/retire-factory.sh
ssh cube '/etc/init.d/S99sunday restart; sync; sleep 6; tail -5 /tmp/sunday.log'

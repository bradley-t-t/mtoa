# Runs on the cube. Stops and removes the factory application layer: the Home
# Assistant satellite, its ESPHome API, music streaming, OTA updates, Bluetooth
# setup, telnet, mDNS and PulseAudio. Kernel, drivers, Wi-Fi, SSH, the LED ring
# driver and the button daemon stay.
for s in S99ha-speaker S50pulseaudio S41inetd S44bluetooth S50avahi-daemon S05avahi-setup.sh S80dnsmasq; do
    [ -f /etc/init.d/$s ] || continue
    /etc/init.d/$s stop >/dev/null 2>&1
    rm -f /etc/init.d/$s
done
for p in sendspin-client pulseaudio inetd avahi-daemon dnsmasq bluetoothd; do killall -q $p; done
for p in $(ps w | awk '/linux_voice_assistant|netmonitor/ && !/awk/ {print $1}'); do kill $p 2>/dev/null; done
sed -i "/S99ha-speaker/d" /etc/init.d/ntpdate.sh
SP=/usr/lib/python3.11/site-packages
rm -rf $SP/linux_voice_assistant* $SP/thirdreality $SP/aioesphomeapi* $SP/zeroconf* $SP/mpv* $SP/soundcard $SP/SoundCard* \
       $SP/pyopen_wakeword* $SP/sounds $SP/examples \
       /usr/bin/sendspin-client /etc/default/sendspin-client \
       /usr/share/thirdreality/script /usr/share/thirdreality/audio /usr/share/thirdreality/conf \
       /etc/pulse /data/conf/sound.json
# Password logins off: the key in authorized_keys is the only way in.
sed -i 's|^DROPBEAR_ARGS=.*||' /etc/default/dropbear 2>/dev/null
echo 'DROPBEAR_ARGS="-s -g"' > /etc/default/dropbear
sync

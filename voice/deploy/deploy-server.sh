#!/bin/sh
# Push the brain to the host and restart it. Claude runs on the host's own Claude Code
# login (machine.env); speech recognition and the voice are local models. The host is
# VOICE_HOST, or host.ssh in the owner's profile; the units are rendered for the account
# the profile names there (host.user and host.home).
set -e
cd "$(dirname "$0")/.."
profile() { python3 server/mtoa_config.py get "$@"; }
HOST=${VOICE_HOST:-$(profile host.ssh)}
[ -n "$HOST" ] || { echo "no host: set host.ssh in the profile or VOICE_HOST" >&2; exit 1; }
RUSER=$(profile host.user "$(ssh "$HOST" 'id -un')")
RHOME=$(profile host.home "$(ssh "$HOST" 'printf %s "$HOME"')")
UNITS=$(mktemp -d)
trap 'rm -rf "$UNITS"' EXIT
for unit in voice-assistant.service transcripts-sync.service transcripts-sync.timer voice.conf; do
  sed -e "s|@USER@|$RUSER|g" -e "s|@HOME@|$RHOME|g" "deploy/$unit" > "$UNITS/$unit"
done
ssh "$HOST" 'mkdir -p ~/voice-assistant/data ~/voice-assistant/models'
rsync -a deploy/voice-start.sh server/voice_server.py server/transcripts.py server/relay.py server/speech_gate.py server/canvas.py server/repeat_guard.py server/barge.py server/nonspeech.py server/fixtures server/speaker_id.py server/pronounce.py server/mtoa_config.py server/speech_audit.py server/selftest_audit.py server/selftest_other_mic.py server/system_prompt.md server/requirements.txt "$HOST":voice-assistant/
ssh "$HOST" "chmod +x ~/voice-assistant/voice-start.sh ~/voice-assistant/relay.py ~/voice-assistant/transcripts.py ~/voice-assistant/voice_server.py"
ssh "$HOST" 'cd ~/voice-assistant && { [ -d venv ] || python3 -m venv venv; } && venv/bin/pip -q install -r requirements.txt
cd models
R=https://github.com/k2-fsa/sherpa-onnx/releases/download
[ -e silero_vad.onnx ] || curl -sfLO $R/asr-models/silero_vad.onnx
for m in asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8 tts-models/vits-piper-en_US-hfc_female-medium tts-models/kokoro-multi-lang-v1_0; do
  [ -d "$(basename $m)" ] || { curl -sfL $R/$m.tar.bz2 | tar xj; }
done
# The speaker check (speaker_id.py): NeMo TitaNet-small. The release tag is spelled that way.
mkdir -p speaker && [ -e speaker/nemo_en_titanet_small.onnx ] || curl -sfL -o speaker/nemo_en_titanet_small.onnx $R/speaker-recongition-models/nemo_en_titanet_small.onnx'
scp -q "$UNITS/voice-assistant.service" "$UNITS/voice.conf" "$UNITS/transcripts-sync.service" "$UNITS/transcripts-sync.timer" "$HOST":/tmp/
ssh "$HOST" 'sudo install -m644 /tmp/voice-assistant.service /tmp/transcripts-sync.service /tmp/transcripts-sync.timer /etc/systemd/system/ && sudo install -D -m644 /tmp/voice.conf /etc/systemd/system/voice-assistant.service.d/voice.conf && sudo systemctl daemon-reload && sudo systemctl enable -q voice-assistant && sudo systemctl enable -q --now transcripts-sync.timer && sudo systemctl restart voice-assistant'
sleep 8
ssh "$HOST" 'curl -s localhost:8794/health; echo; tail -4 ~/voice-assistant/voice.log'
# The hourly speech audit: reports repeats and stale answers through the routine attention path.
scp -q deploy/speech-audit-run.sh "$HOST":bin/speech-audit-run.sh
ssh "$HOST" 'chmod +x ~/bin/speech-audit-run.sh; crontab -l | grep -q speech-audit-run || { crontab -l; echo "17 * * * *    flock -n $HOME/routine-logs/speech-audit.lock $HOME/bin/speech-audit-run.sh"; } | crontab -'

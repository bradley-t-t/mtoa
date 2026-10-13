#!/bin/sh
# Start the brain with the ElevenLabs key from the credential store in its environment,
# the way the routines get theirs. The key goes from the vault into this one process
# and is written nowhere. If the vault can't be opened, start without it: Sunday then
# speaks with Kokoro alone, which is better than not starting.
APP="$HOME/voice-assistant"
CRED="$HOME/.claude/tools/with-credential.py"
if timeout 30 "$CRED" elevenlabs-api-key=ELEVENLABS_API_KEY -- true 2>/dev/null; then
  exec "$CRED" elevenlabs-api-key=ELEVENLABS_API_KEY -- "$APP/venv/bin/python" -u "$APP/voice_server.py"
fi
echo "$(date '+%F %T') vault unreachable; starting without ElevenLabs" >&2
exec "$APP/venv/bin/python" -u "$APP/voice_server.py"

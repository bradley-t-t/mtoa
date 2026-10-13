#!/bin/sh
# Hardware buttons, as reported by input_eventd. Each press is handed to the
# Sunday agent; nothing here acts on its own.
[ -p /tmp/sunday-keys ] && echo "$1 $2" > /tmp/sunday-keys &

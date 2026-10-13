---
name: draft
description: Draft an email for me to send - the subject and the body, with no signature, every claim in it checked against the source before it is written, and the whole thing read through humanizer before it comes back. Use on /draft, or "draft an email", "write back to them", "reply to this", "put together an email to", "what should I say to". It drafts and stops; nothing is sent, and nothing is placed in the mailbox.
argument-hint: "[who it is to and what it needs to say, or the thread to answer]"
---

# draft

An email I can paste and send. The reply is the draft and nothing else.

## The reply

A subject line, a blank line, the body. No signature, no sign-off block, no name
under the last line - the mail client adds mine and a second one under it reads
as a mistake. No preamble before the subject, no notes after the body, no offer
to change the tone. Where something could not be confirmed, that is the one
thing allowed after the body: a single line naming what it is, so I can settle
it before I send.

The whole draft sits inside one blockquote, so the bar down its left edge
shows where the email starts and where it ends. The subject is the first
line, in bold with `Subject:` in front of it, then a quoted blank line, then
the body in italics - each paragraph wrapped in its own `*...*` so the italics
survive the blank lines between them, and every line of the block prefixed
with `>` including the empty ones. Nothing else in the reply is quoted, so the
bar marks the thing to paste against anything around it. The one line naming
an unconfirmed claim sits below the blockquote, in plain text, so it is never
pasted with the draft.

```
> **Subject:** The subject line
>
> *First paragraph of the body.*
>
> *Second paragraph of the body.*

Could not confirm the delivery date; the thread does not name one.
```

Nothing is sent. Nothing is saved as a draft in Outlook, forwarded, replied to
or scheduled. `/draft` produces text and ends, and putting it in front of
anybody is a separate ask that needs my yes for that send. That holds for
Sam and Chris the same as for a client.

The draft comes back as text in the reply, and stays text until I say
otherwise. No Outlook draft, no artifact, no file, no note - and the same for
every revision that follows, however many rounds it takes. Only an instruction
from me naming where it should go moves it out of the reply.

## Who it is to and what it answers

The argument names the recipient and the point, or the thread on screen is what
is being answered. Read the thread before writing a word of the reply, all of
it, because the question actually asked is usually two messages back from the
last one. Where the recipient is a client, `~/Development/records/` and the
project tracker hold what has been agreed with them, and the draft is written
against that rather than against memory of it.

With no argument and no thread, ask who it is to and what it needs to say, in
one line.

## Everything in it is true

An email is a set of claims with my name on them, and the reader will act on
every one. So each claim the draft makes is checked against the thing it is
about before it is written down, not after:

- **A date, a price, a figure** comes from the record that holds it - Stripe
  read, the database queried, `pricing.js` read, the records directory opened.
  Never from what a figure usually is.
- **What was said before** comes from the thread or the earlier mail, quoted
  back only as it was actually written. Search the mailbox rather than
  reconstruct it.
- **The state of a site, a deploy, a ticket, a fix** comes from the site, the
  deployment, the issue. "It is live" means the page was loaded and the version
  read; "it is fixed" means the fix was found on `main`, not on a branch.
- **A commitment** - a delivery day, a call time, a thing I will do - is only
  written where I have said it or the argument asks for it. The draft never
  promises on my behalf.
- **A name, a role, a company** is spelled as the person spells it, taken from
  their own mail or their site.

A claim that cannot be checked from here does not go in as if it had been. It
comes out, or it goes in as a question to the reader, or it is named in the one
line after the body. A wrong number sent under my name costs more than a
missing one, and a draft that reads well and is wrong is the failure this skill
exists to catch.

This binds hardest on absence and on ease. "We have not heard back", "there
was no invoice for that", "nothing changed on our end" are claims about the
whole mailbox, the whole account, the whole repo, and each is checked the same
way before it is written. The check never appears in the reply; it is what
makes the reply true.

## It reads like me

Once the body is right, run the `humanizer` skill over it before it comes
back - the whole body, as a rewrite, not a scan. It goes out under my name to
somebody who knows how I write, and the stock openers, the inflated claims and
the paired sentences read as a machine to anyone who has had a message from
me before.

Humanizer keeps every claim and adds none, so it cannot undo the checking
above. What it changes is the prose, and what survives is a short, plain
message that says the thing and stops.

No emoji, anywhere in it. No bullet lists in a message under five items. Copy
names who acts: "you will get the plan on Thursday", never "the plan will come
through on Thursday".

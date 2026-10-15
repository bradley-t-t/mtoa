# Staying on Topic

These rules cover how to reply to a request without drifting away from it.

## 1. Find the actual question

- Before writing anything, restate the request to yourself in one sentence.
- Decide what a finished answer looks like. If it's a file, a fix, a yes or no, or a number, produce that thing.
- If the request is ambiguous, ask one short clarifying question. Don't answer three possible versions of it.

## 2. Answer first

- Put the answer in the first sentence.
- For a yes-or-no question, start with "Yes" or "No".
- Add context only when the answer would be wrong or unusable without it.

## 3. The reply shape, nothing more

- Every reply is a bold verdict line and at most four labelled lines - Why, What, Touched, Proof - one line each, in that order, with Proof on every finished verdict. The shape is in `CLAUDE.md`, and ratchet refuses a reply that leaves it.
- A question that one or two sentences answer, in a turn that changed nothing, gets one or two plain sentences instead, with no formatting and nothing beside the answer.
- No headings, extra paragraphs, other labels or sign-offs around it.
- If it doesn't fit the shape, you're answering more than was asked. Cut until it fits.
- A form the user asks for in their own words, such as a table, a draft, code or a long explanation, is the one exception.

## 4. Scope is what was asked, nothing more

- The file, bug, question or section the user named is the whole job.
- Don't refactor, rename, reformat or "improve" anything nobody asked about.
- When something comes out, take it out. Don't swap in a replacement unless asked.
- If you notice an unrelated problem, leave it alone and leave it out of the reply.

## 5. Cut the detours

Leave these out unless the user asked for them:

- Background or history of the topic
- A list of alternatives you considered and rejected
- Your step-by-step reasoning
- A recap of what the user just said
- A summary of what you just said
- Offers to do more ("Let me know if you'd also like...")
- Generic best-practice advice that doesn't change this answer

## 6. Check every sentence

Before you send, ask of each sentence:

1. Does it help answer the question that was asked?
2. Would the user notice if it were gone?

If the answer to both is no, delete it.

## 7. Follow the user's lead in a conversation

- The latest message sets the topic. Don't drag the thread back to an earlier subject.
- Don't bring up earlier work unless the current request depends on it.
- If the user changes topic, change with them completely.

## 8. When you're unsure whether something is on topic

Leave it out. If it matters, the user will ask for it, and a short follow-up costs less than a long answer they have to dig through.

## Quick checklist

- [ ] The first sentence answers the question
- [ ] The reply is in the shape: a verdict line, then Why / What / Touched / Proof lines at most
- [ ] Nothing was changed or discussed that wasn't requested
- [ ] No recap, no reasoning narrative, no rejected options
- [ ] No side issue, aside or note about what was left

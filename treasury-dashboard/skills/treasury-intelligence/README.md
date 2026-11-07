# Treasury intelligence: instruction-only skill

Copy this folder into an agent that supports `SKILL.md`. The host model executes
W1–W4 and event association directly, using its existing search, webpage-reading
and file tools. No repository program, Python, database, service address or
workflow API is required. This folder contains no executable scripts or secrets.

## Setup and use

1. Add the complete `treasury-intelligence/` folder to the host's supported skills
   directory, preserving relative paths. Discovery depends on the host; if it
   does not support automatic discovery, ask the agent to read `SKILL.md` first.
2. Enable the host tools you need. Without web tools, supply article text or
   attachments; without file tools, use a handoff in chat. This folder does not
   install tools or grant network or filesystem permissions.
3. Specify the task, scope, dates and available output directory. Defaults are
   Hapag-Lloyd and Asia/Singapore; explicit user settings take precedence. Keep
   the output directory separate from the installation directory.
4. Check that the result includes a summary, ranked news table, event progress,
   focused explanations, sources and coverage gaps. Markdown is the default;
   add charts only when suitable data supports them.

Example requests:

- “Use treasury-intelligence to analyze the three attached articles without
  browsing, and return a scoring table with explanations.”
- “Use treasury-intelligence to prepare this week's Treasury news brief in
  Singapore time, saving it to my designated report directory.”
- “Read the previous run-record and continue with unfinished articles without
  collecting sources that have already been read.”

## Contents

- [SKILL.md](SKILL.md): entry point, staged loading, state and boundaries.
- `prompts/`: five stage instructions, each with at least three synthetic examples.
  W2 covers all three news categories; W3 covers numeric, directional, factual and
  empty results; E covers creation, progress, corroboration, conflict, deferral,
  resolution and correction.
- `references/`: company context, complete 0–5 scoring anchors, and event, time
  and retrieval rules.
- `templates/`: article, event, run-record and user-report templates.
- [Demo inputs](examples/demo-inputs.md) and [demo report](examples/demo-report.md):
  synthetic inputs and expected output for a manual walkthrough, not real news
  or evaluation results.

Do not load every file into context at once. Follow SKILL.md: read the current
stage, relevant references, and at most five articles or five candidate events.
Save concise records of completed work and retrieve evidence through its references.

## Relationship to the program

The workflow and scoring definitions come from this project, but the prompts are
rewritten as instructions the agent can execute directly. They do not depend on
runtime JSON schemas, database queries, code validation or automated arithmetic.
The v2 scoring anchors, caps and weights are preserved, including R5. Company
information is versioned background already saved in the repository; its webpages
are not necessarily rechecked on every run.

This skill cannot guarantee atomic writes, concurrency safety, automatic checkpoint
recovery or programmatic field validation. Saved records help continuation, but
sources may still be read again. Different hosts and models require their own
semantic evaluation; the program's benchmark accuracy does not carry over to the
skill. During a manual walkthrough, check sources, event classification,
explanations and arithmetic. Reading the entire skill is not a quality pass.

For deterministic checkpoints and a service spanning multiple stages, use the
project's existing HTTP integration. That is a separate execution mode; this skill
does not invoke it implicitly.

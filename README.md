# cortex-sdd

A lean spec-driven development loop for Claude Code, with a knowledge vault that gets smarter every cycle.

Most AI-assisted work starts from a blank page and forgets everything when the session ends. Cortex runs each unit of work as a **cycle** that starts by reading what you already know and ends by writing down what it learned.

```text
explore  ─▶  plan  ─▶  exec  ─▶  review  ─▶  end  ─▶  ingest
   ▲                                                     │
   └───────────────── your knowledge vault ◀─────────────┘
```

## Install

Requires Claude Code 2.1.275 or later, Python 3.9+, git, and an authenticated [GitHub CLI](https://cli.github.com).

```text
/plugin install cortex --marketplace singullar/cortex-sdd
/cortex:install
/cortex:doctor
```

`/cortex:install` creates your vault as a **private** repository from the [cortex-brain](https://github.com/singullar/cortex-brain) template. `/cortex:doctor` checks that everything works and tells you how to fix what doesn't.

## Your first cycle

```text
/cortex:init project:add-rate-limiting repos:~/code/api
```

Open a session in the folder it creates, then run the loop:

| Step | Command | What you get |
|---|---|---|
| Explore | `/cortex:explore` | `EXP.md`: the problem, informed by your vault. No solutions yet |
| Plan | `/cortex:plan` | `PLAN.md` and `EXEC.md`: the approach and a checklist with a validation gate per task |
| Execute | `/cortex:exec` | The work, ending in a draft pull request |
| Review | runs inside `exec`, or `/cortex:review` | Specialized reviewers, auto-fixes, then the PR marked ready for you |
| Close | `/cortex:end` | Verifies merge, tasks and review evidence, then writes `SESSION-SUMMARY.md` |
| Ingest | chained from `end`, or `/cortex:ingest` | What the cycle learned, committed to your vault |

Helpers: `/cortex:status`, `/cortex:handoff`, `/cortex:commit`, `/cortex:commit-push-pr`, `/cortex:new-worktree`.

## How review works

A draft pull request belongs to the machine; a ready one belongs to you. The loop runs your test suite, fans out reviewers (bugs, tests, error handling, security, docs, UX, design system), debates the findings, fixes what is confirmed and marks the PR ready. If it can't converge, the PR stays a draft with a note saying why.

For dedicated reviewers, install the optional `pr-review-toolkit` plugin: `/plugin install pr-review-toolkit@claude-plugins-official`. Without it, general-purpose agents use the same rubric.

## Your vault

The vault is a folder of linked markdown under git: sources, concepts and entities, routed by an `index.md`. See [cortex-brain](https://github.com/singullar/cortex-brain).

Each ingest runs in its own git worktree, commits with a `Cortex-Cycle` trailer naming where the knowledge came from, merges into your branch and pushes. To see which cycle produced what:

```bash
git log --format='%h %(trailers:key=Cortex-Cycle,valueonly)'
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).

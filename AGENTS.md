# Working Style

Be brief and direct. Ask for clarification when requirements are unclear. Tackle one task at a time. If the context window is getting long, suggest writing a memory file and starting a fresh session.

## Collaboration

- Treat this as a collaborative engineering partnership.
- Push back when something seems wrong.
- If you are unsure about any aspect of the task, or lack the relevant knowledge, say so immediately.
- No unneeded flattery or praise.
- Prefer direct, critical answers over agreeable ones.
- Do not drive engagement at the end of a completed task.
- Tell Michael what subagents are going to be doing before using them.
- Surface relevant subagent findings, recommendations, and file diffs in the main conversation before acting.

## Research

- Use web search for up-to-date information rather than guessing.
- If the same question is asked twice, do fresh research instead of repeating the same answer.

## Project Files for LLM agents

| File | Purpose |
|------|---------|
| `AGENTS.md` | Project-specific instructions for Codex |
| `ISSUES.md` | Bug and issue tracker; not for tasks or feature work |
| `.memory/` | Project memory bank - persistent context across sessions |

## Coding Rules

- Never commit by yourself. Michael handles git and GitHub interactions.
- If explicitly asked to commit, generate the command and ask for permission before running it.
- Read a file fully before modifying it unless it does not exist.
- Never rewrite an existing implementation from scratch without explicit permission. Fix; do not replace.
- If scope is unclear, do the smaller interpretation first.
- Stay on task. Do not make unrelated changes.
- Ask Michael before making code or project file changes; do not edit willy-nilly.
- Explain shell commands when asking for permission to run them.
- Match surrounding style and formatting.
- Prefer simple, readable solutions over clever ones.
- Use idiomatic patterns for the language in use.
- Keep names evergreen; avoid `new`, `improved`, `enhanced`, `v2`, and similar names.
- Comments should explain what and why, not change history.
- Never remove comments unless they are demonstrably false.
- Prefer ASCII equivalents.
- Run configured formatters or linters before considering work done.
- Prefer editing existing files over creating new ones unless a new file is genuinely necessary.
- Check for existing patterns or libraries before introducing new ones.
- Prefer the standard library unless a dependency is justified.
- Do not leave debug code, commented-out blocks, or stray logging behind unless Michael added them and wants them kept.
- Fail loudly rather than silently swallowing exceptions.
- Validate at system boundaries.
- Never hardcode credentials, secrets, or environment-specific values.

## Testing

Follow TDD when it makes sense for the project:

1. Write a failing test.
2. Run it to confirm it fails as expected.
3. Write the minimal code to make it pass.
4. Confirm the test passes.
5. Refactor while keeping tests green.

- Only add tests when they make sense for the project.
- Tests must cover the functionality being implemented.
- Never ignore test output or logs.
- Test output must be clean to count as passing.

## Environment Notes

- On this machine, use `uv` instead of `pip` for Python package management.
- `uv` works fine for Michael outside the Codex sandbox. If `uv` fails during a Codex run, treat that as a sandbox or agent-environment issue unless there is direct evidence otherwise.
- Do not switch to `pip` or another package manager because of a Codex-local `uv` failure unless Michael explicitly asks for that change.
- Default shell commands to `login: false` to avoid PowerShell profile side effects unless login behavior is explicitly needed.

## GUI Applications

- Add necessary DPI-aware font and widget scaling options where relevant.
- Do not generate binary assets in code; ask for placeholders first.

## Workflow

- Work in small, testable increments.
- Make the smallest reasonable change that achieves the goal.
- Flag dead code and stale investigations when you see them.
- Update global memory when explicitly asked to remember something unrelated to the current project.

## Project Memory

Maintain a `.memory/` directory in each project as persistent context across sessions.

| File | Purpose |
|------|---------|
| `project-context.md` | Project goal, scope, key constraints |
| `active-context.md` | Current focus, recent decisions, logical next steps |
| `architecture.md` | Patterns, tech choices, system structure |
| `progress.md` | Session journal with decisions, blockers, and tradeoffs |

- Read all `.memory/` files at the start of a task when they exist.
- After completing work, update `active-context.md` and append a timestamped entry to `progress.md`.
- Keep `architecture.md` current when patterns or structure change.
- Use `progress.md` for context, not diffs.
- Keep task tracking in `ISSUES.md`, not in memory files.

## Project Setup Defaults

New projects should include:

- `LICENSE` (MIT, Michael Champanis, current year)
- `README.md`
- `.gitignore`
- `.gitattributes`
- `AGENTS.md`
- `ISSUES.md`
- `ONGOING.md` as a gitignored personal task/note file
- `ETHICS.md` copied from `~/projects/ethics/ETHICS.md`
- `.memory/` directory, gitignored by default
- Any local tool permission directories gitignored by default

## Convenience

- Generate runner scripts such as `.ps1` files for common tasks when appropriate (OS dependent).
- Use an appropriate config format and provide corresponding example files.
- Gitignore any file containing secrets or credentials by default.

WHATS-NEW — cli-refractor branch
=================================

This document summarizes the notable changes made on the `cli-refractor` branch relative to the upstream `main` branch in this fork. It focuses on high-level design changes, new tools and capabilities, refactors, and other improvements introduced in the branch.

Summary (high-level)
- Major CLI / REPL refactor: the REPL loop has been extracted and reorganized (new `src/cai/repl/loop/*` modules) to improve isolation, handle long-running tasks, and simplify resilience and handoffs.
- TUI & UX improvements: many enhancements to the Textual-based TUI including better streaming handling, tool output sanitization/wrapping, toolbar/prompt changes, and fixes for input/Enter key handling.
- New and improved tools: multiple offensive/defensive tools were added or improved (browser/playwright integration, cewl, sqlmap, impacket, ligolo, netexec, cve_search, github PoC search, and others).
- Agent runtime & orchestration: added parallel/isolated execution helpers, shutdown coordination, and improvements to tool execution and handoff logic to avoid event-loop and concurrency issues.
- RAG / knowledge pipeline: added scripts and modules for ingestion, chunking, retrieval, and vector adapters; new `scripts/ingest_vault.py` and knowledge/vault tooling.
- Memory & summarization: updates to memory/compaction logic and summarizer prompts; support for different summarizer models and improved compacting behavior.
- Examples, benchmarks & docs: added/updated examples, benchmark scripts, and documentation entries to cover new usage patterns and local API options.

Category breakdown

1) CLI / REPL
- Extracted REPL loop helpers into `src/cai/repl/loop` and added a `session` abstraction to handle long-running sessions.
- Centralized post-turn orchestration and added `handle_post_turn` and `handle_orphaned_tool_calls` to ensure robust per-turn behavior.
- Improved local-environment initialization and `.env` handling; added `LOCAL_API_BASE` support for a Universal Local API workflow.

2) TUI / UI
- Fixed multiple TUI bugs: Enter key handoff, Enter swallowed by TextArea binding, stream debug handling, and CSS/visual issues.
- Improved tool output formatting: long outputs are wrapped, preview/collapse behavior improved, and progress messages re-routed into the RichLog.
- Removed an unbounded empty-response auto-resubmit loop (prevents stuck re-submissions when model returns empty after tool output).

3) Tools & Integrations
- Browser/Playwright: added a Playwright-based tool and BrowserPreview TUI widget (interactive ARIA element map, screenshots, and VLM sitrep).
- Networking & exploitation tooling: added or improved `impacket` executor, `sqlmap` wrapper, ligolo/netexec helpers, and SMB/CIFS helpers.
- Recon & crawling: new crawler, cewl wordlist generator, DDG-based search replacement for older services, and improved web reconnaissance tools.
- Vulnerability tooling: `cve_search` integration and `github_poc_search` were added to aid discovery and PoC lookups.

4) Agents & Runtime
- Robust agent orchestration: parallel isolation, a shutdown coordinator, better tracing/logging, and more resilient tool-run loops.
- Improved model provider compatibility and fallbacks (e.g., reasoning-parameter retry logic for OpenAI/LiteLLM cases).
- Added agent manager utilities and a `simple_agent_manager` refresh approach for reloads/resumptions.

5) RAG, Knowledge & Memory
- New ingestion scripts and local vault tooling (`scripts/ingest_vault.py`, `src/cai/tools/knowledge/vault.py`).
- Vector DB adapter and retriever pipeline updates for chunking, wakeup indexes, and retriever metrics.
- Memory compaction and summarizer changes to support different support models and better resume behavior.

6) Examples, Benchmarks, Tests, and Docs
- Expanded examples under `examples/` and updated benchmark scripts.
- Added smoke scripts (TUI smoke/config) and multiple example agent patterns.
- Performed a large linter pass (`ruff`) and test adjustments; many style/fix commits.

7) Miscellaneous
- Additions to `pyproject.toml` and `.ruff.toml` to support the branch linting/style.
- New bootstrap and internal components added for metrics, endpoints, and transfer utilities.

Notable file-level highlights (non-exhaustive)
- `src/cai/repl/loop/*` — new REPL loop modules, event loop helpers, and session handling.
- `src/cai/tui/app.py` — many TUI fixes and the empty-response recovery removal to avoid re-submits.
- `src/cai/tools/web/browser.py` & related — Playwright/browser tooling and TUI preview widget.
- `scripts/ingest_vault.py`, `src/cai/tools/knowledge/vault.py` — ingestion and local knowledge tooling.
- `src/cai/sdk/agents/*` — agent-run improvements, tool execution flow fixes, and parallel executor additions.

How this fork improves the project
- Better UX and reliability: the TUI and REPL have been hardened to avoid freeze/stuck conditions and to provide clearer feedback during streaming and tool execution.
- Expanded capability set: multiple new tools and integrations push the project toward a more complete red-team/blue-team toolkit.
- Improved modularity: refactors move the REPL, orchestration, and parallel execution into isolated modules that are easier to maintain and extend.
- Reproducible local workflows: `LOCAL_API_BASE` and bootstrap changes make it easier to run and test the project locally without cloud dependencies.

Next steps and caveats
- This summary is derived from the branch diff and high-level commit messages. For a precise audit, review individual commits or run targeted `git diff`/`git log` for specific subsystems.
- Some additions (new tools and scripts) may require additional runtime dependencies; consult the updated `pyproject.toml` and README changes for install notes.

If you'd like, I can:
- Expand the above into a per-module changelog (split by subsystem) — useful for release notes.
- Produce a concise one-page release note for end-users.

---
Generated on: (branch `cli-refractor`)

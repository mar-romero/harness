# Python CLI distribution contract

Status: draft planning contract for `HARNESS-INSTALL-001`.

This document records the external contract used by the discovery and does not
authorize package publication or target-repository mutation.

## Authoritative sources

- [Creating and packaging command-line tools](https://packaging.python.org/en/latest/guides/creating-command-line-tools/)
- [The packaging flow](https://packaging.python.org/en/latest/flow/)
- [Installing stand-alone command-line tools](https://packaging.python.org/en/latest/guides/installing-stand-alone-command-line-tools/)
- [VCS Support](https://pip.pypa.io/en/latest/topics/vcs-support/)
- [Version specifiers](https://packaging.python.org/en/latest/specifications/version-specifiers/)

## Contract

- A Python distribution may expose a CLI through project metadata and a
  console-script entry point; building an artifact and publishing it are
  separate operations.
- The proposed delivery channel is a private Git distribution. The planning
  does not select a public PyPI or TestPyPI publication.
- A VCS installation builds locally, so the eventual implementation must pin
  an immutable commit or signed release artifact and define authentication and
  credential-redaction behavior before activation.
- A target installation must use an explicit allowlist/manifest. Packaging the
  repository does not imply copying the repository, its history, tasks,
  planning records, runtime state, caches, credentials, or target-owned data.
- Installation, migration, and publication are separate operator actions. Each
  requires deterministic dry-run evidence and an explicit human approval gate
  for external side effects.

## Open contract decisions before implementation

- immutable release identity and digest/provenance format;
- supported Python and operating-system versions;
- private Git authentication without credentials in URLs or logs;
- target Git-worktree validation, ownership state integrity, locking,
  rollback, and symlink/reparse-point handling;
- whether a future public publication is desired.

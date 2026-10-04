# Replay isolation

Each replay has a private bare Git store. It contains only the base commit and its ancestors.
The replay has the exact head tree as staged changes. The head commit and later commits are absent.
Each replay uses a separate worktree, config file, and review output root.

Use `isolation.child_command` for every child launch. It requires Linux bubblewrap. It hides the
source checkout and shared Git store, the full benchmark root, other worktrees at the same location, and live review
outputs. It mounts the current replay and its assets with write access. A private PID namespace closes access through host process roots. It gives the other host
files read access. Child processes inherit this boundary, including classifier and review children.

This is a practical boundary for known local evidence, not a complete security sandbox.
Network access remains available for model calls. Other clones and remote services can contain
future answers. Before each run, the parent must identify extra local answer sources and add them to the replay record `hidden_paths`.
The child task must restrict source reads to the replay and prohibit remote history or answer
retrieval. Run in a dedicated host or container if stronger access control is required.

The parent owns assessment and can inspect all arms. It must send only the frozen task, rules,
config, and current replay evidence to a child. Keep assessments outside replay outputs.

Hydration uses the historical project script when present. Review tracked changes after hydration.
Generated files can affect checks. Record their effect before comparing arms.

The driver uses a private runtime directory for harness session state. Existing credentials and
config files have read-only mounts. Credential contents are not copied into benchmark assets.
The host Cargo and general cache directories remain writable for normal project checks. These
shared caches can affect timing. Record this limit when you compare elapsed time across arms.

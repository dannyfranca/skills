# Code Design Review

Review boundaries, responsibilities, coupling, cohesion, abstractions, and changeability. Repository rules override this baseline. Tool-enforced style is out of scope. These smells are judgement calls, not automatic violations:

- **Mysterious Name** — rename unclear functions, variables, and types.
- **Duplicated Code** — extract a shared shape only when repetition is meaningful.
- **Feature Envy** — move behavior toward the data it primarily uses.
- **Data Clumps** — give repeatedly travelling fields a coherent type.
- **Primitive Obsession** — model domain concepts explicitly when primitives obscure invariants.
- **Repeated Switches** — centralize repeated dispatch logic.
- **Shotgun Surgery** — gather code that changes for one reason.
- **Divergent Change** — split modules that change for unrelated reasons.
- **Speculative Generality** — remove abstractions and hooks without a present requirement.
- **Message Chains** — hide navigation behind an intention-revealing boundary.
- **Middle Man** — remove delegation that adds no policy or abstraction.
- **Refused Bequest** — prefer composition where inheritance contracts do not fit.

Prefer deep, testable modules with small interfaces. Do not demand abstractions merely to shorten files or satisfy a pattern.

# Criticality

Every rule in a process has a **criticality**: how much it matters for that process. It is one of
**Critical**, **High**, **Medium** or **Low**.

Criticality and severity are different things:

- **Severity** is about one finding: is it a warning or an error? That depends on the measurement
  (see [aim for and limits](aim-for-and-limits.md)).
- **Criticality** is about the rule: how bad is a problem of this kind for this process? An
  undercut is Critical for injection molding, because the part can't come out of a simple mold;
  a sharp outside corner is Low, because it mostly adds tooling cost.

The results panel groups findings by criticality, Critical first, so the problems that matter most
are at the top.

The same rule can have a different criticality in different processes. Each rule's page lists its
criticality for every process that uses it, and you can change it in the Process Library.

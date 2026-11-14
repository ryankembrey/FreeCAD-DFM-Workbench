# Aim for and limits

Most rules have two values for each material:

- **Aim for** is what to design for. A measurement on the wrong side of it gives a **warning**: the
  part can probably be made, but it is at risk.
- The **limit** is the line you must not cross. A measurement past it gives an **error**: expect
  the part to fail, or to be much harder or more expensive to make.

The limit's label says which way it works. *At least* is a minimum (a wall thinner than this is an
error); *At most* is a maximum (a wall thicker than this is an error).

For example, a rule with *Aim for* 1.0 mm and *At least* 0.5 mm finds:

| Measured | Result |
| :-- | :-- |
| 1.2 mm | Nothing to report |
| 0.8 mm | Warning: below what to aim for |
| 0.4 mm | Error: below the limit |

## Other kinds of rule

- **Yes or no rules**, such as undercuts or sealed hollows, have no number. When they find the
  problem, they report it as the error or warning the process sets (*If detected*).
- **Relative rules** compare one size with another: a rib's thickness as a percentage of the
  nominal wall, or a boss's height as a multiple of its diameter. Their values read that way, such
  as "60% of nominal wall".
- **Worked-out values**: for FDM printing, the minimum wall and the thinnest printable text are
  worked out from the nozzle diameter you enter when you set up the analysis.

## Where the values come from

Every process ships with values for each material. A material only lists the values that differ;
everything else comes from the process's **Default** material. You can change any of them in the
Process Library.

Each rule's page shows its values, and [where each one comes from](../citations/index.md).

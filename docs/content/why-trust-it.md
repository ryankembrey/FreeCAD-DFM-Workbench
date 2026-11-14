# Why trust it

A DFM tool is only as good as the rules it checks and the numbers behind them. Here is what the
workbench does to earn your trust, and where its limits are.

## Every number has a source

The values each rule checks against come from design guides published by molders, material
suppliers and printer makers. Each value's page shows where it comes from: a published figure, one
worked out from published figures, or engineering judgement where no source gives a number. Values
whose source hasn't been recorded yet say so.

- [How we cite](citations/index.md) explains the kinds of source.
- [Coverage](citations/coverage.md) lists every value and its source.

## Every rule explains itself

Each rule has a diagram of what to avoid and what to aim for, and every finding says why it matters
and how to fix it. Nothing is a black box: the [rule pages](reference/rules/index.md) show exactly
what each rule checks, its values for every material and the messages it gives.

## You are in control

Guides differ, and so do machines, molds and material grades. Every value, criticality and message
can be changed in the [Process Library](guides/process-library.md), and you can add your own
processes and materials. If your molder or printer gives different figures, use theirs.

## What it can't do

The workbench checks geometry against rules. It doesn't simulate how plastic flows, how a print
warps or how a part carries load, and it can't know your machine, your tolerances or your supplier.
Treat its findings as a well-informed review to discuss with whoever makes the part, not a
guarantee.

## Open source

The workbench is open source (LGPL-2.1). Its code, its rules and its values are all
[on GitHub](https://github.com/ryankembrey/FreeCAD-DFM-Workbench), and so are the tests that check
its measurements. If you know a better figure or a source, please
[open an issue](https://github.com/ryankembrey/FreeCAD-DFM-Workbench/issues).

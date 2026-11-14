# Nominal wall

The **nominal wall** is the thickness most of a molded part's walls share. A good molded part keeps
its walls close to it, and many molding rules are measured against it rather than in millimetres:

- a rib's thickness and height, and the spacing between ribs,
- a boss's wall, and the spacing between bosses,
- the fillets at the base of ribs and bosses, and inside corners.

So the same rule suits a thin housing and a thick one. In messages it is the `{nominal}`
placeholder, which also says whether the wall was measured or entered.

## Auto or Manual

The analysis setup has a **Nominal wall** row for processes that use it.

- **Auto** (the default) measures it on your part when the analysis runs. Click the ruler button to
  measure it now and see the value first.
- **Manual** uses the thickness you type. Use it when your part has no single main wall, or when you
  know the wall you designed to.

## How Auto measures it

The workbench looks only at the part's plain walls: it leaves out the faces of bosses, ribs, holes,
fillets and other [recognized features](feature-recognition.md), since those are what is being
compared with the wall. From points on each wall face it measures straight through to the opposite
side. The nominal wall is the middle value of those measurements, weighted by area, so a few thick
or thin spots don't move it.

If a part has no clear main wall, Auto can't find one and the rules that need it say so in the
results. Type the wall in with **Manual** and run again.

# How to make a warehouse map

A step-by-step guide for anyone. You need Excel (or LibreOffice Calc)
and a terminal. No programming.

The whole idea:

> **You draw the warehouse in Excel. You type arrows on the roads to
> say which way robots drive. A checker tells you what is wrong, by
> Excel cell. When the checker is happy, the simulator can run on your
> map.**

---

## 1. What a map is made of

Every Excel cell is one square of floor, **1 m × 1 m**.

| In the cell | Meaning |
|---|---|
| **red fill** | wall — robots can never go here |
| **white**, with arrows | road — robots drive here, one way only |
| `S` | shelf — where items are picked |
| `D` | drop-off — where an order is delivered |
| `C` | charger |
| `H` | holding cell — where a robot waits for a free charger |
| `P` | parking — **one per robot; this is your maximum fleet size** |
| **orange** | the coordinate frame round the outside — leave it alone |

Colours on stations are optional (they just make the sheet easier to
read). Only **red** and **orange** mean something to the program.

Stations are called *terminals*: a robot drives into one, does its job,
and backs out to the road it came from. **Robots never drive through a
station** to get somewhere else.

### Arrows

Type arrows in road cells. They say which way a robot may **leave** that cell.

```text
>  or  →     go right
<  or  ←     go left
^  or  ↑     go up
v  or  ↓     go down
```

Where two roads cross, the cell gets two arrows, for example `>^`
("go right or go up").

Arrows mean what they look like **on screen**. You do not need to think
about coordinates.

---

## 2. The five rules

The checker enforces these. Learn them once and drawing is easy.

**Rule 1 — every road is one-way.** Two neighbouring cells must never
point at each other. That is a head-on collision waiting to happen.

```text
  BAD:   > <        GOOD:   > >
```

**Rule 2 — every road cell needs at least one arrow.** A road with no
arrow is a dead end: a robot that gets there is stuck forever.

**Rule 3 — never draw an arrow into a station or a wall.** Robots enter
stations by themselves from any road next to them. The checker will
refuse an arrow into a shelf and tell you which cell it is.

**Rule 4 — every station must touch a road.** A shelf surrounded by
walls and other shelves can never be reached.

**Rule 5 — from any road you must be able to get to every other road.**
If an area has a way in but no way out (or the reverse), robots get
trapped. The checker calls this *disconnected roads*.

### And one more, for big fleets: no tight loops

Four roads in a 2 × 2 square whose arrows chase each other round:

```text
   v <          four robots, one on each cell, each waiting for the
   > ^          next one to move. Nobody ever moves again.
```

This is called a **rotary**. It obeys all five rules, so the map still
runs — the checker shows it as a *warning*, not an error — but it is
the most common way a busy warehouse locks up. The original V2 map has
33 of them, and one of them caused a real deadlock in testing.

**For a map meant for up to 100 robots, fix every rotary.** Turning one
of the four arrows round is enough.

The easiest way to avoid rotaries: where two roads running opposite
ways sit side by side, cross them with roads that *also* run opposite
ways, arranged so traffic leaves the square rather than circling it.
The example maps (below) are built this way — copy the pattern.

---

## 3. Start your map

Open a terminal in the repository folder and set these two shortcuts
(do this once in every new terminal):

```bash
cd ~/rnd_mrta_ilp_ws
export PY=.pixi/envs/default/bin/python
export PP=src/mrta_core:src/simulation_env:src/task_management
```

### Option A — start from a working example (recommended)

This makes a complete, valid map already sized for your fleet. You
then change it in Excel. Typing every arrow by hand on a 100-robot map
means about 1,400 cells, so this saves hours.

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py example \
    --robots 100 --out src/simulation_env/maps/my_map.xlsx
```

```text
  size       : 40 x 54 cells
  parking    : 100   (the most robots it can run)
  shelves    : 552
  drop-offs  : 36
  chargers   : 24
  holding    : 12
```

Its plan (top of the sheet at the top):

```text
  top edge     chargers C and holding cells H
  ring road    one-way, all the way round, just inside the walls
  middle       repeating units:  up-aisle | shelves | down-aisle
               crossed by two-row "bands" so robots can take shortcuts
  bottom edge  drop-offs D
  left/right   parking P, one per robot
```

`--units 15` makes it wider (more aisles of shelves). See it in the
terminal without opening Excel:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py show \
    --map src/simulation_env/maps/my_map.xlsx
```

A small version of the same design, for 20 robots:

```text
     0123456789012345
   20 ##CCHCCHCCHCCH##      chargers and holding along the top
   19 #v<<<<<<<<<<<<<#      ring road, top part, going left
   18 #v^Sv^Sv^Sv^Sv^#
   17 #v^Sv^Sv^Sv^Sv^#      ^ up-aisle   S shelves   v down-aisle
   ...
   11 P>>>>>>>>>>>>>^P      band, top row, going right
   10 Pv<<<<<<<<<<<<<P      band, bottom row, going left
   ...
    2 #>>>>>>>>>>>>>^#      ring road, bottom part, going right
    1 ##DDDDDDDDDDDD##      drop-offs along the bottom
```

### Option B — start from a blank sheet

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py new \
    --width 40 --height 54 --out src/simulation_env/maps/my_map.xlsx
```

This gives a red wall round the edge and white floor inside. You draw
everything: walls, stations and every arrow.

Keep your map in `src/simulation_env/maps/`, next to W100.

---

## 4. Edit in Excel

Open `my_map.xlsx`. It has two sheets:

- **MAP** — the warehouse. Only this sheet is read.
- **HOW TO EDIT** — a reminder of the symbols and rules.

Things you will typically do:

| To... | Do this |
|---|---|
| add a wall | fill the cell red, delete its text |
| add a shelf | type `S` in a road cell, delete its arrows |
| remove a shelf and make road | delete `S`, type arrows |
| add parking for more robots | type `P` in a cell next to a road |
| add a charger | type `C` in a cell next to a road |
| make the hall bigger | see *Resizing* below |

**Excel tips that save a lot of time**

- Type `>` in one cell, then drag the fill handle (small square,
  bottom-right of the cell) along the row: the whole row gets `>`.
- Select a block and press `Ctrl+D` (fill down) or `Ctrl+R` (fill right).
- Use Find & Replace (`Ctrl+H`) to turn every `>` in a selection into `<`.

**Resizing.** The orange frame holds the coordinates. To add columns,
insert them *inside* the frame and renumber the orange labels along the
top and bottom so they read `1, 2, 3, ...` with no gaps; do the same for
rows (the side labels run from the top count down to 1). It is usually
easier to generate a new example at the size you want and copy your
changes across.

Save as `.xlsx`.

---

## 5. Check it — after every change

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/map_tool.py check \
    --map src/simulation_env/maps/my_map.xlsx --robots 100
```

`--robots 100` also checks there is enough parking for 100 robots.

**A good map:**

```text
  max robots : 100   (one parking cell per robot)

  No problems found.

status                   : OK
```

**A map with mistakes** — every problem names its Excel cell. This is
real output from the 20-robot example after two deliberate mistakes: one
arrow on the bottom ring road turned round, and the arrows deleted from
one aisle cell:

```text
  ERRORS - robots cannot use the map until these are fixed

  1. DEAD END
     at E12 (3, 9)
     road with no arrow out of it. Give it at least one arrow
     pointing to a neighbouring road.

  2. TWO-WAY ROAD
     at E20 (3, 1), F20 (4, 1)
     these two roads point at each other, so two robots can meet
     head-on. Remove one of the two arrows.

  3. DISCONNECTED ROADS
     at E20 (3, 1), F20 (4, 1)
     ...

  8. STATION UNREACHABLE
     at E21 (3, 0)
     dropoff_002 only touches roads that are cut off from the main
     network (see 'disconnected roads').

     ... and 13 more disconnected roads problem(s) like these

status                   : FAIL
```

**One mistake can cause many problems.** Turning a single arrow round
on the ring road (problem 2) broke the loop, so a whole stretch of road
and the drop-offs beside it became unreachable (problems 3–11). Always
fix the **first** errors in the list, especially `TWO-WAY ROAD` and
`DEAD END`, then check again — the rest often disappear.

Go to the cell (`E20`), fix it, save, run the check again. Repeat
until it says **OK** with no warnings.

What each problem means:

| Problem | What happened | Fix |
|---|---|---|
| `TWO-WAY ROAD` | two cells point at each other | delete one of the two arrows |
| `DEAD END` | a road cell has no arrow out | add an arrow toward a neighbouring road |
| `DISCONNECTED ROADS` | an area you can enter but not leave, or the reverse | look where it meets the main roads; there must be a way in **and** a way out |
| `STATION NOT ON A ROAD` | a station touches no road | put a road next to it |
| `STATION UNREACHABLE` | it touches only cut-off roads | fix the disconnected roads first |
| `MISSING STATION` | no shelves, drop-offs, chargers or parking at all | add some |
| `NOT ENOUGH PARKING` | fewer `P` than `--robots` | add `P` cells |
| `ROTARY` (warning) | four roads chasing each other round a square | turn one of the four arrows round |

Errors that stop the file loading at all also name the cell:

```text
The map could not be read:
  the arrow at E9 points into station shelf_004 at F9. Do not draw
  arrows into stations: a robot drives into any station from a
  neighbouring road on its own
```

---

## 6. The final acceptance check

When the quick check says OK, run the full gate once. It plans a real
route between **every pair of stations** — on a 100-robot map that is
over half a million routes, so it takes about 1–2 minutes:

```bash
PYTHONPATH=$PP $PY src/simulation_env/scripts/check_map_topology.py \
    --map src/simulation_env/maps/my_map.xlsx
```

It must end with:

```text
  two-way core edges       : 0
  strongly connected       : PASS
  unreachable legs         : 0
  legs through a terminal  : 0
status                   : OK
```

Your map is ready. Next: [START_HERE.md](START_HERE.md), step 3.

---

## 7. Good design, beyond the rules

The checker makes sure robots *can* use your map. These make it a
*good* one for research:

- **Enough chargers.** About 1 charger per 4 robots keeps charging
  from becoming the bottleneck. Fewer is fine if you want to study
  charger queues.
- **Shortcuts.** Without cross-roads, a robot that just missed its turn
  drives all the way round. Bands every 15–20 rows help.
- **Drop-offs spread out**, not all in one corner, or traffic piles up
  there.
- **Parking near the roads**, so robots leave and return quickly.
- **No rotaries** if you plan to run many robots.
- **Keep the map fixed** once experiments start. Every result is a
  measurement *on this map*; changing it means re-running everything.

## 8. Notes

- Station names (`shelf_001`, ...) are numbered top-to-bottom,
  left-to-right as the sheet appears. Moving or adding a station can
  renumber the others, so **build again** after any station change
  (START_HERE.md, step 3): it regenerates the orders.
- Maps without any arrows (the original V1 and V2 maps) are routed by
  the old built-in rules, which only fit the V2 layout. Every new map
  needs arrows.
- The arrow format can describe anything the old built-in rules could:
  V2 exported with arrows is routed on exactly the same 775 blocked
  moves. See it with `map_tool.py export`.

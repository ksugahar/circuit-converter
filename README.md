# Circuit Converter: SPICE &harr; LTspice &harr; schemdraw

Bidirectional conversion between **SPICE netlists** (`.cir`), **LTspice schematics** (`.asc`), and **[schemdraw](https://schemdraw.readthedocs.io/) diagrams** &mdash; verified on 4,300+ circuits.

```
                schemdraw (.py / PDF)
               ↗  100%     ↖ 100%
  .cir (SPICE) ←——————————→ .cir
    ↕ 99.7%      round-trip     ↕
  .asc (LTspice) ←————→ LTspice sim
```

## Why?

- **Write a netlist, get a schematic.** `.cir` &rarr; schemdraw generates publication-quality circuit diagrams.
- **Draw a circuit, get a netlist.** schemdraw &rarr; `.cir` extracts topology for simulation.
- **Simulate what you design.** `.cir` &rarr; `.asc` creates LTspice schematics with auto-layout.
- **Round-trip without loss.** `.cir` &rarr; schemdraw &rarr; `.cir` preserves node names, directives, and values.

## Installation

```bash
pip install schemdraw spicelib
git clone https://github.com/ksugahar/circuit-converter.git
```

LTspice (optional, for `.asc` simulation): [Download](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html)

## Quick Start

### `.cir` &rarr; schemdraw (netlist to diagram)

```python
from src.cir_to_schemdraw import cir_string_to_schemdraw

netlist = """* Common-Emitter Amplifier
V1 Vcc 0 12
V2 in 0 SINE(0 0.01 1k)
R1 Vcc out 4.7k
R2 Vcc n1 10k
R3 n1 0 2.2k
Q1 out n1 0 2N2222
C1 in n1 1u
.tran 5m
.end"""

script = cir_string_to_schemdraw(netlist, "ce_amplifier")
exec(script)  # Generates ce_amplifier.pdf
```

### schemdraw &rarr; `.cir` (diagram to netlist)

```python
from src.schemdraw_to_cir import schemdraw_script_to_cir

schemdraw_code = '''
import schemdraw
import schemdraw.elements as elm

with schemdraw.Drawing(show=False) as d:
    V1 = d.add(elm.SourceV().up().label("V1\\nAC 1"))
    R1 = d.add(elm.Resistor().right().label("R1\\n1k"))
    d.add(elm.Dot())
    d.push()
    C1 = d.add(elm.Capacitor().down().label("C1\\n1u"))
    d.add(elm.Ground())
    d.pop()
    d.add(elm.Ground().at(V1.start))
'''

netlist = schemdraw_script_to_cir(schemdraw_code, "RC Filter")
print(netlist)
# * RC Filter
# V1 in 0 AC 1
# R1 in out 1k
# C1 out 0 1u
# .end
```

### `.cir` &rarr; `.asc` (netlist to LTspice)

```python
from src.netlist_to_asc import NetlistToAsc

asc = NetlistToAsc().convert_string(netlist)
with open("circuit.asc", "w") as f:
    f.write(asc)
# Open in LTspice and simulate
```

### `.asc` &rarr; `.cir` (LTspice to netlist)

```python
from src.asc_parser import asc_to_cir

asc_to_cir("circuit.asc", "circuit.cir")
```

### Full round-trip

```python
from src.cir_to_schemdraw import cir_string_to_schemdraw
from src.schemdraw_to_cir import schemdraw_script_to_cir

script = cir_string_to_schemdraw(netlist, "my_circuit")
recovered = schemdraw_script_to_cir(script, "My Circuit")
# Node names, directives, and values are preserved
```

## Supported Components

| Category | SPICE | schemdraw element |
|----------|-------|-------------------|
| Resistor | R | `Resistor` |
| Capacitor | C | `Capacitor` |
| Inductor | L | `Inductor2` |
| Voltage source | V | `SourceV` |
| Current source | I | `SourceI` |
| Diode | D | `Diode`, `Schottky`, `Zener`, `LED` |
| NPN BJT | Q | `BjtNpn` |
| PNP BJT | Q | `BjtPnp` |
| N-MOSFET | M | `NFet` |
| P-MOSFET | M | `PFet` |
| N-JFET | J | `JFetN` |
| P-JFET | J | `JFetP` |
| Opamp | X | `Opamp` (auto-detected) |
| VCVS | E | `SourceControlledV` |
| CCCS | F | `SourceControlledI` |
| VCCS | G | `SourceControlledI` |
| CCVS | H | `SourceControlledV` |
| Behavioral | B | `SourceV` |
| Switch | S | `Switch` |
| Transmission line | T | `Coax` |
| Subcircuit | X | `RBox` (generic) |

## Conversion Quality

| Conversion | Pass Rate | Circuits Tested |
|------------|-----------|-----------------|
| `.cir` &rarr; schemdraw (compile + exec) | **100%** | 4,313 |
| `.cir` &harr; schemdraw round-trip | **100%** | node + directive preservation |
| `.asc` &rarr; `.cir` | **99.7%** | 720 |
| `.cir` &rarr; `.asc` | **99.6%** | 481 |
| `.cir` &rarr; `.asc` &rarr; LTspice sim | **96.9%** | 3,999 |

## How It Works

### `.cir` &rarr; schemdraw

1. Parse netlist &rarr; components, nodes, directives
2. Classify: source (vertical left) / series (horizontal) / shunt (vertical to GND)
3. BFS node ordering for left-to-right signal flow
4. Emit schemdraw code with `push()`/`pop()` for branches
5. Embed directives as invisible `Annotate` labels (for lossless round-trip)

### schemdraw &rarr; `.cir`

1. Execute schemdraw script, capture `Drawing` object
2. Read anchor coordinates from all elements
3. Union-Find to merge coincident anchors into electrical nodes
4. `Ground()` positions &rarr; node `0`
5. Map element types to SPICE prefixes, extract labels for names/values

### `.cir` &harr; `.asc`

- **`.cir` &rarr; `.asc`**: Graph-based auto-layout, column assignment, LTspice anchor positioning
- **`.asc` &rarr; `.cir`**: Union-Find wire analysis, `.asy` pin resolution (reads LTspice `lib.zip`)

## Project Structure

```
src/
  cir_to_schemdraw.py      .cir → schemdraw (100% on 4,313 circuits)
  schemdraw_to_cir.py      schemdraw → .cir (Union-Find node extraction)
  netlist_to_asc.py         .cir → .asc (graph-based auto-layout)
  asc_parser.py             .asc → .cir (.asy pin resolution)
  asc_to_schemdraw.py       .asc → schemdraw
  schemdraw_to_ltspice.py   schemdraw → .asc
```

## License

MIT License

## References

- [schemdraw](https://schemdraw.readthedocs.io/) &mdash; Publication-quality circuit diagrams
- [LTspice](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html) &mdash; Free SPICE simulator
- [spicelib](https://github.com/nunobrum/spicelib) &mdash; Python library for LTspice `.raw` files

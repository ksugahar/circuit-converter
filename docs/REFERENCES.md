# References & Related Work

## Primary Reference

### dominc8/netlist_converter
- **Repository**: https://github.com/dominc8/netlist_converter
- **Language**: C++
- **What it does**: SPICE netlist (.cir) to LTspice schematic (.asc) and SVG conversion
- **What we referenced**:
  - The overall architecture of parser → layouter → ASC generator pipeline (our `netlist_to_asc.py` follows the same 3-stage design)
  - The concept of classifying components into source/series/shunt categories for automatic layout
  - BFS-based node ordering for left-to-right signal flow

- **Where we diverged**:
  - Pin offset tables derived from actual `.asy` symbol files with verified rotation transforms, instead of hardcoded values
  - FLAG-based connectivity with stub wires instead of long-distance wire routing, to avoid T-junction cross-connections
  - Component overlap and terminal conflict resolution with iterative shift algorithm
  - `.raw` waveform comparison as the verification standard (not just compilation success)

## Other Related Work

### Schemato (Nov 2024)
- **Paper**: https://arxiv.org/html/2411.13899v2
- **Approach**: Fine-tuned LLM for netlist-to-schematic conversion
- **Metric**: 76% Compilation Success Rate (CSR) — whether generated .asc is syntactically valid
- **Comparison**: Our project achieves 81.9% on a stricter metric — .raw waveform equivalence (electrical correctness, not just syntax)

### netlistsvg
- **Repository**: https://github.com/nturley/netlistsvg
- **Focus**: JSON netlist to SVG, digital circuits
- **Routing**: Uses ElkJS (Sugiyama/layered graph layout)
- **Limitation**: No SPICE support, no simulation verification

### f18m/netlist-viewer
- **Repository**: https://github.com/f18m/netlist-viewer
- **Status**: Unmaintained since 2010
- **Limitation**: Only subcircuits, requires uppercase input

## Key Design Decisions

### Why FLAG-based connectivity instead of wire routing?

LTspice interprets wire crossings as connections when a wire endpoint lies on another wire segment (T-junction). Long wire routes inevitably create unintended T-junctions in dense circuits. Our approach:

1. Each pin gets a short stub wire (16px)
2. FLAG (net label) placed at stub endpoint
3. Same-name FLAGs are electrically connected by LTspice
4. T-junction avoidance: stub direction chosen to avoid landing on existing wire segments

This trades visual simplicity for electrical correctness — the generated .asc looks different from hand-drawn schematics but simulates identically.

### Why .raw waveform comparison?

Prior tools validate only:
- Compilation success (does LTspice open the file?)
- Component count match
- Node name preservation

We validate **simulation equivalence**: the original .asc and the round-trip .asc must produce identical .raw waveform data (rtol=1e-3, atol=1e-6). This is the strongest possible verification — it proves the circuit is electrically equivalent, not just structurally similar.

### Pin offset verification methodology

Instead of manually measuring pin positions, we:
1. Read PIN coordinates from `.asy` symbol files in LTspice's `lib.zip`
2. Apply rotation/mirror transforms verified against 44,585 component instances
3. Achieved 99.7% pin-to-wire-endpoint match rate across all symbol types

## Verification Results

| Metric | This Project | Schemato (SOTA) | dominc8 |
|--------|-------------|-----------------|---------|
| Test circuits | 174 | ~100 | 4 |
| Verification level | .raw waveform match | Compilation + GED | Visual |
| Pass rate | **81.9%** (strict) | 76% (CSR only) | N/A |
| Verified DB | 113 circuits | — | — |

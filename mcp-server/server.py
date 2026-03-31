#!/usr/bin/env python3
"""MCP server providing schemdraw, PyLTSpice, and LTSpice knowledge for Claude Code.

Static tools (10): API reference for schemdraw, PyLTSpice, LTSpice formats.
Dynamic tools (8): circuit DB search, LTSpice examples, LTSpice applications (3999 circuits),
simulation execution, result analysis, parametric sweep, netlist↔schemdraw bidirectional conversion.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("schemdraw-ltspice-circuit-lab")

# --- Paths ---
_SERVER_DIR = Path(__file__).parent
_REPO_ROOT = _SERVER_DIR.parent
_DB_DIR = _REPO_ROOT / "tests" / "db"
_TEXTBOOK_DIR = _REPO_ROOT / "textbook"
_DB_PATH = _TEXTBOOK_DIR / "circuit_db.json"
_CIRCUITS_DIR = _TEXTBOOK_DIR / "circuits"
_EXAMPLES_DIR = _DB_DIR / "ltspice_examples"
_EXAMPLES_CATALOG = _EXAMPLES_DIR / "catalog.json"
_APPLICATIONS_DIR = _DB_DIR / "ltspice_applications"
_APPLICATIONS_CATALOG = _APPLICATIONS_DIR / "catalog.json"


def _find_ltspice() -> str | None:
    """Auto-detect LTspice executable."""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "ADI" / "LTspice" / "LTspice.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ADI" / "LTspice" / "LTspice.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "LTC" / "LTspiceXVII" / "XVIIx64.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _load_db() -> dict:
    """Load circuit_db.json."""
    return json.loads(_DB_PATH.read_text(encoding="utf-8"))


# ============================================================
# Dynamic Tool 1: Circuit DB Search
# ============================================================
@mcp.tool()
def search_circuits(
    query: str = "",
    circuit_type: str = "",
    status: str = "verified",
    limit: int = 20,
) -> str:
    """Search the circuit database (229 textbook circuits).

    Args:
        query: Free-text search in figure name, description, components (case-insensitive).
        circuit_type: Filter by circuit_type field (e.g. 'DC', 'AC', 'resonance', 'filter').
        status: Filter by status: 'verified', 'skipped', 'all'. Default 'verified'.
        limit: Max results to return (default 20).

    Returns:
        Matching circuits with figure, page, type, description, and netlist.
    """
    db = _load_db()
    results = []
    q_lower = query.lower()
    ct_lower = circuit_type.lower()

    for e in db["entries"]:
        if status != "all" and e["status"] != status:
            continue
        if ct_lower and ct_lower not in e.get("circuit_type", "").lower():
            continue
        if q_lower:
            searchable = f"{e['figure']} {e.get('description','')} {e.get('components','')}".lower()
            if q_lower not in searchable:
                continue
        results.append(e)
        if len(results) >= limit:
            break

    if not results:
        return f"No circuits found matching query='{query}', type='{circuit_type}', status='{status}'."

    lines = [f"Found {len(results)} circuit(s):\n"]
    for e in results:
        lines.append(f"### {e['figure']} (p.{e['page']}) — {e.get('circuit_type','')}")
        lines.append(f"**Components:** {e.get('components','')}")
        lines.append(f"**Description:** {e.get('description','')}")
        lines.append(f"**Status:** {e['status']}")
        if e.get("cir_file"):
            lines.append(f"**File:** {e['cir_file']}")
        if e.get("netlist") and e["netlist"].strip() not in ("", "* SKIP - conceptual diagram"):
            lines.append(f"```spice\n{e['netlist']}\n```")
        lines.append("")
    return "\n".join(lines)


# ============================================================
# Dynamic Tool 2: Search LTSpice Educational Examples
# ============================================================
@mcp.tool()
def search_ltspice_examples(
    query: str = "",
    category: str = "",
    limit: int = 20,
) -> str:
    """Search LTSpice built-in Educational examples (93 pre-simulated circuits).

    Categories: oscillator, filter, amplifier, op-amp, power, RF, analysis, other.

    Args:
        query: Free-text search in example name (case-insensitive). E.g. 'colpits', 'PLL', 'transformer'.
        category: Filter by category. E.g. 'oscillator', 'filter', 'amplifier'.
        limit: Max results (default 20).

    Returns:
        Matching examples with name, category, plot type, and available traces.
        Use run_simulation(cir_file='...') or read_simulation_results(raw_file='...') with the returned paths.
    """
    if not _EXAMPLES_CATALOG.exists():
        return "ERROR: Examples catalog not found. Run batch simulation first."

    catalog = json.loads(_EXAMPLES_CATALOG.read_text(encoding="utf-8"))
    q_lower = query.lower()
    cat_lower = category.lower()

    results = []
    for item in catalog:
        if cat_lower and cat_lower not in [c.lower() for c in item.get("categories", [])]:
            continue
        if q_lower and q_lower not in item["name"].lower():
            continue
        results.append(item)
        if len(results) >= limit:
            break

    if not results:
        return f"No examples found matching query='{query}', category='{category}'."

    lines = [f"Found {len(results)} LTSpice Educational example(s):\n"]
    for item in results:
        cats = ", ".join(item.get("categories", []))
        lines.append(f"### {item['name']} [{cats}]")
        lines.append(f"**Plot type:** {item['plot_type']} ({item['n_traces']} traces)")
        lines.append(f"**Raw file:** ltspice_examples/{item['name']}.raw")
        lines.append(f"**ASC file:** ltspice_examples/{item['name']}.asc")
        # Show key traces (voltages and currents, skip internal)
        traces = item.get("traces", [])
        if traces:
            display = [t for t in traces if t.lower() not in ("time", "frequency")][:10]
            lines.append(f"**Traces:** {', '.join(display)}")
        lines.append("")
    return "\n".join(lines)


# ============================================================
# Dynamic Tool 2b: Search LTSpice Application Circuits
# ============================================================
@mcp.tool()
def search_ltspice_applications(
    query: str = "",
    category: str = "",
    sim_status: str = "ok",
    limit: int = 20,
) -> str:
    """Search LTSpice Application circuits (3999 ADI component demo circuits).

    Covers ADC, DAC, op-amp, comparator, power, switch, instrumentation-amp,
    isolator, LED-driver, and more.

    Args:
        query: Free-text search in circuit name (case-insensitive). E.g. 'AD8221', 'LT3080', 'ADA4930'.
        category: Filter by category: op-amp, ADC, DAC, power, switch, comparator,
                  instrumentation-amp, isolator, LED-driver, regulator, analog.
        sim_status: Filter: 'ok' (simulated successfully), 'fail', 'all'. Default 'ok'.
        limit: Max results (default 20).

    Returns:
        Matching circuits with name, categories, plot type, and available traces.
        Use run_simulation(cir_file='ltspice_applications/<name>.asc') to simulate.
    """
    if not _APPLICATIONS_CATALOG.exists():
        return "ERROR: Applications catalog not found. Run build_applications_catalog.py first."

    catalog = json.loads(_APPLICATIONS_CATALOG.read_text(encoding="utf-8"))
    q_lower = query.lower()
    cat_lower = category.lower()

    results = []
    for item in catalog:
        # Filter by sim status
        if sim_status == "ok" and not item.get("sim_ok", False):
            continue
        if sim_status == "fail" and item.get("sim_ok", True):
            continue

        # Filter by category
        if cat_lower and cat_lower not in [c.lower() for c in item.get("categories", [])]:
            continue

        # Free-text search
        if q_lower and q_lower not in item["name"].lower():
            continue

        results.append(item)
        if len(results) >= limit:
            break

    if not results:
        return f"No applications found matching query='{query}', category='{category}', sim_status='{sim_status}'."

    # Count totals for context
    total = len(catalog)
    total_ok = sum(1 for i in catalog if i.get("sim_ok"))

    lines = [f"Found {len(results)} application circuit(s) (from {total_ok}/{total} total):\n"]
    for item in results:
        cats = ", ".join(item.get("categories", []))
        lines.append(f"### {item['name']} [{cats}]")
        if item.get("plot_type"):
            lines.append(f"**Plot type:** {item['plot_type']} ({item.get('n_traces', 0)} traces)")
        lines.append(f"**ASC file:** ltspice_applications/{item['asc_file']}")
        if item.get("raw_file"):
            lines.append(f"**Raw file:** ltspice_applications/{item['raw_file']}")
        traces = item.get("traces", [])
        if traces:
            display = [t for t in traces if t.lower() not in ("time", "frequency")][:10]
            if display:
                lines.append(f"**Traces:** {', '.join(display)}")
        lines.append("")
    return "\n".join(lines)


# ============================================================
# Dynamic Tool 3: Run LTSpice Simulation
# ============================================================
@mcp.tool()
def run_simulation(
    netlist: str = "",
    cir_file: str = "",
    timeout_sec: int = 30,
) -> str:
    """Run an LTSpice simulation and return trace summary.

    Provide EITHER a netlist string OR a path to an existing .cir/.asc file.

    Args:
        netlist: SPICE netlist content to simulate (creates temp file).
        cir_file: Path to existing .cir or .asc file (relative to tests/db/ or absolute).
        timeout_sec: Simulation timeout in seconds (default 30).

    Returns:
        List of available traces with min/max/mean values, or error message.
    """
    ltspice = _find_ltspice()
    if not ltspice:
        return "ERROR: LTspice not found on this system."

    # Resolve .cir file path
    if cir_file:
        cir_path = Path(cir_file)
        if not cir_path.is_absolute():
            cir_path = _DB_DIR / cir_file
        if not cir_path.exists():
            return f"ERROR: File not found: {cir_path}"
        work_dir = cir_path.parent
    elif netlist:
        work_dir = _CIRCUITS_DIR
        cir_path = work_dir / "_mcp_temp_sim.cir"
        cir_path.write_text(netlist, encoding="utf-8")
    else:
        return "ERROR: Provide either 'netlist' or 'cir_file'."

    # Run LTSpice
    try:
        result = subprocess.run(
            [ltspice, "-b", "-Run", str(cir_path)],
            capture_output=True, timeout=timeout_sec,
            cwd=str(work_dir),
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: Simulation timed out after {timeout_sec}s."
    except Exception as e:
        return f"ERROR: {e}"

    # Check for .raw output
    raw_path = cir_path.with_suffix(".raw")
    op_raw_path = cir_path.with_suffix(".op.raw")
    actual_raw = None
    if raw_path.exists():
        actual_raw = raw_path
    elif op_raw_path.exists():
        actual_raw = op_raw_path

    if not actual_raw:
        # Check log for errors
        log_path = cir_path.with_suffix(".log")
        if log_path.exists():
            for enc in ("utf-16-le", "utf-8"):
                try:
                    log_text = log_path.read_text(encoding=enc)
                    break
                except UnicodeDecodeError:
                    log_text = ""
            error_lines = [l for l in log_text.split("\n") if "error" in l.lower()]
            return f"ERROR: Simulation failed.\n" + "\n".join(error_lines[:5])
        return "ERROR: No .raw output generated."

    # Parse results
    try:
        from spicelib import RawRead
        r = RawRead(str(actual_raw), verbose=False)
        traces = r.get_trace_names()
        props = r.get_raw_property()
        plot_type = props.get("Plotname", "Unknown")

        lines = [f"**Simulation OK** — {plot_type}"]
        lines.append(f"**Raw file:** {actual_raw.name}")
        lines.append(f"**Traces ({len(traces)}):**\n")

        for tname in traces:
            t = r.get_trace(tname)
            w = t.get_wave()
            if tname.lower() == "time" or tname.lower() == "frequency":
                lines.append(f"- **{tname}**: {len(w)} points, {w[0]:.6g} to {w[-1]:.6g}")
            else:
                lines.append(f"- **{tname}**: min={w.real.min():.6g}, max={w.real.max():.6g}, mean={w.real.mean():.6g}")

        return "\n".join(lines)
    except Exception as e:
        return f"Simulation completed (raw file exists) but parse error: {e}"


# ============================================================
# Dynamic Tool 3: Read Simulation Results
# ============================================================
@mcp.tool()
def read_simulation_results(
    raw_file: str,
    traces: str = "",
    time_range: str = "",
    downsample: int = 200,
) -> str:
    """Read waveform data from an LTSpice .raw file.

    Args:
        raw_file: Path to .raw file (relative to tests/db/ or absolute).
        traces: Comma-separated trace names to read (default: all). E.g. 'V(out),I(R1)'.
        time_range: Optional 'start,end' in seconds to clip data. E.g. '0,0.01'.
        downsample: Max data points per trace (default 200, for readability).

    Returns:
        Waveform data as text table with statistics.
    """
    raw_path = Path(raw_file)
    if not raw_path.is_absolute():
        raw_path = _DB_DIR / raw_file
    if not raw_path.exists():
        return f"ERROR: File not found: {raw_path}"

    try:
        from spicelib import RawRead
        import numpy as np

        r = RawRead(str(raw_path), verbose=False)
        all_traces = r.get_trace_names()
        props = r.get_raw_property()

        # Select traces
        if traces:
            selected = [t.strip() for t in traces.split(",")]
            # Validate
            missing = [t for t in selected if t not in all_traces]
            if missing:
                return f"ERROR: Traces not found: {missing}\nAvailable: {all_traces}"
        else:
            selected = all_traces

        # Get time/frequency axis
        axis_name = all_traces[0] if all_traces else "time"
        axis_trace = r.get_trace(axis_name)
        axis_data = axis_trace.get_wave().real

        # Apply time range filter
        mask = np.ones(len(axis_data), dtype=bool)
        if time_range:
            parts = time_range.split(",")
            if len(parts) == 2:
                t_start, t_end = float(parts[0]), float(parts[1])
                mask = (axis_data >= t_start) & (axis_data <= t_end)

        lines = [f"**{props.get('Plotname', 'Simulation')} Results**"]
        lines.append(f"**File:** {raw_path.name}")
        lines.append(f"**Total points:** {len(axis_data)}, filtered: {mask.sum()}\n")

        # Statistics for each trace
        lines.append("| Trace | Min | Max | Mean | RMS |")
        lines.append("|-------|-----|-----|------|-----|")
        for tname in selected:
            if tname == axis_name:
                continue
            t = r.get_trace(tname)
            w = t.get_wave()[mask].real
            if len(w) == 0:
                continue
            rms = np.sqrt(np.mean(w**2))
            lines.append(f"| {tname} | {w.min():.6g} | {w.max():.6g} | {w.mean():.6g} | {rms:.6g} |")

        # Downsampled waveform data
        filtered_axis = axis_data[mask]
        n = len(filtered_axis)
        if n > downsample:
            indices = np.linspace(0, n - 1, downsample, dtype=int)
        else:
            indices = np.arange(n)

        lines.append(f"\n**Waveform data** ({len(indices)} points):\n")
        # Header
        header = [axis_name] + [t for t in selected if t != axis_name]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")

        for i in indices:
            row = [f"{filtered_axis[i]:.6g}"]
            for tname in selected:
                if tname == axis_name:
                    continue
                t = r.get_trace(tname)
                w = t.get_wave()[mask].real
                row.append(f"{w[i]:.6g}")
            lines.append("| " + " | ".join(row) + " |")

        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


# ============================================================
# Dynamic Tool 4: Parametric Analysis
# ============================================================
@mcp.tool()
def parametric_sweep(
    netlist: str,
    param_name: str,
    values: str,
    measure_trace: str,
    timeout_sec: int = 60,
) -> str:
    """Run a parametric sweep: vary one component value and measure results.

    Args:
        netlist: Base SPICE netlist (must contain the parameter to sweep).
        param_name: Component name to vary (e.g. 'R1', 'C1') or .param name.
        values: Comma-separated values to sweep. E.g. '100,1k,10k,100k'.
        measure_trace: Trace name to measure (e.g. 'V(out)', 'I(R1)').
        timeout_sec: Total timeout for all simulations (default 60).

    Returns:
        Table of parameter value vs. trace statistics (min/max/mean/RMS).
    """
    ltspice = _find_ltspice()
    if not ltspice:
        return "ERROR: LTspice not found."

    value_list = [v.strip() for v in values.split(",")]
    if not value_list:
        return "ERROR: No values provided."

    import re
    import numpy as np

    lines = [f"**Parametric Sweep:** {param_name} = [{', '.join(value_list)}]"]
    lines.append(f"**Measuring:** {measure_trace}\n")
    lines.append(f"| {param_name} | Min | Max | Mean | RMS | Peak-Peak |")
    lines.append("|---|---|---|---|---|---|")

    work_dir = _CIRCUITS_DIR
    for val in value_list:
        # Substitute parameter value in netlist
        modified = netlist
        # Try .param substitution first
        param_pattern = rf"(\.param\s+{re.escape(param_name)}\s*=\s*)\S+"
        if re.search(param_pattern, modified, re.IGNORECASE):
            modified = re.sub(param_pattern, rf"\g<1>{val}", modified, flags=re.IGNORECASE)
        else:
            # Try component value substitution (e.g. "R1 1 2 1k" → "R1 1 2 10k")
            comp_pattern = rf"({re.escape(param_name)}\s+\S+\s+\S+\s+)\S+"
            modified = re.sub(comp_pattern, rf"\g<1>{val}", modified, flags=re.IGNORECASE)

        # Write temp file
        cir_path = work_dir / f"_mcp_sweep_{param_name}_{val}.cir"
        cir_path.write_text(modified, encoding="utf-8")

        try:
            subprocess.run(
                [ltspice, "-b", "-Run", str(cir_path)],
                capture_output=True, timeout=timeout_sec,
                cwd=str(work_dir),
            )

            raw_path = cir_path.with_suffix(".raw")
            op_raw = cir_path.with_suffix(".op.raw")
            actual = raw_path if raw_path.exists() else (op_raw if op_raw.exists() else None)

            if actual:
                from spicelib import RawRead
                r = RawRead(str(actual), verbose=False)
                if measure_trace in r.get_trace_names():
                    w = r.get_trace(measure_trace).get_wave().real
                    rms = np.sqrt(np.mean(w**2))
                    pp = w.max() - w.min()
                    lines.append(f"| {val} | {w.min():.6g} | {w.max():.6g} | {w.mean():.6g} | {rms:.6g} | {pp:.6g} |")
                else:
                    lines.append(f"| {val} | — | — | — | — | trace not found |")
            else:
                lines.append(f"| {val} | — | — | — | — | no output |")
        except subprocess.TimeoutExpired:
            lines.append(f"| {val} | — | — | — | — | timeout |")
        except Exception as e:
            lines.append(f"| {val} | — | — | — | — | error: {str(e)[:50]} |")
        finally:
            # Cleanup temp files
            for suffix in (".cir", ".raw", ".op.raw", ".log", ".net"):
                p = cir_path.with_suffix(suffix)
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

    return "\n".join(lines)


# ============================================================
# Dynamic Tool 6: Netlist to schemdraw Script
# ============================================================
@mcp.tool()
def netlist_to_schemdraw(
    netlist: str,
    name: str = "circuit",
) -> str:
    """Convert a SPICE netlist to a runnable schemdraw Python script.

    Generates a Python script that draws the circuit using schemdraw.
    Supports R, C, L, V, I, D, BJT (NPN/PNP), MOSFET, JFET, opamp.

    Args:
        netlist: SPICE netlist text (with .end). E.g. 'V1 in 0 AC 1\\nR1 in out 1k\\nC1 out 0 1u\\n.ac dec 20 1 100k\\n.end'
        name: Circuit name for the output file (default 'circuit').

    Returns:
        Runnable Python script that generates a PDF schematic.
        Execute it with: exec(result) or save as .py and run.
    """
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "src"))
    try:
        from cir_to_schemdraw import CirToSchemdraw
        converter = CirToSchemdraw()
        script = converter.convert_string(netlist, name)
        return script
    except Exception as e:
        return f"ERROR: {e}"


# ============================================================
# Dynamic Tool 7: schemdraw Script to Netlist
# ============================================================
@mcp.tool()
def schemdraw_to_netlist(
    script: str,
    title: str = "circuit",
) -> str:
    """Convert a schemdraw Python script to a SPICE netlist.

    Takes a schemdraw script (with Drawing context), executes it,
    extracts the circuit topology from element anchors, and generates
    a SPICE netlist.

    Supports R, C, L, V, I, D, BJT, MOSFET, JFET, Opamp.

    Args:
        script: schemdraw Python script text (must create a Drawing).
        title: Title for the netlist (default 'circuit').

    Returns:
        SPICE netlist text (.cir format) ready for LTspice simulation.
        Can be passed directly to run_simulation(netlist=result).
    """
    import sys as _sys
    _sys.path.insert(0, str(_REPO_ROOT / "src"))
    try:
        from schemdraw_to_cir import schemdraw_script_to_cir
        netlist = schemdraw_script_to_cir(script, title)
        return netlist
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def schemdraw_tips() -> str:
    """Return tips, API reference, and gotchas for schemdraw v0.22 (latest, released 2025-11-30).

    Covers: element creation, placement, labels, anchors, coordinate system,
    direction control, styling, and common pitfalls.
    """
    return """
schemdraw v0.22 API Reference & Tips
======================================

VERSION: 0.22 is the latest (released 2025-11-30). Requires Python 3.9+.

INSTALLATION
------------
pip install schemdraw
pip install schemdraw[matplotlib]   # with matplotlib backend

IMPORT
------
import schemdraw
import schemdraw.elements as elm

CREATING A DRAWING
------------------
Context manager (preferred, v0.18+) -- elements auto-added:
    with schemdraw.Drawing(show=False) as d:
        d.config(unit=3, font='Times New Roman')
        elm.Resistor().right().label('R1')
        elm.Capacitor().down().label('C1')

Explicit add (still works):
    d = schemdraw.Drawing()
    R1 = d.add(elm.Resistor().right())
    d.save('circuit.pdf')

Drawing config options:
    d.config(
        unit=3,              # default element length (default 3)
        inches_per_unit=0.5, # output scaling
        fontsize=12,
        font='Times New Roman',
        color='black',
        lw=2,                # line width
    )

SAVING
------
d.save('file.svg')   # SVG
d.save('file.pdf')   # PDF
d.save('file.png', dpi=300)  # PNG

COMMON ELEMENTS
---------------
Passive:
  elm.Resistor()    (IEEE zigzag) / elm.ResistorIEC() (box)
  elm.Capacitor()   / elm.Capacitor2() (curved plate)
  elm.Inductor()    / elm.Inductor2() (loopy style)
  elm.Potentiometer(), elm.RBox(), elm.Fuse(), elm.Memristor()

Sources:
  elm.SourceV()     -- voltage source (circle with +/-)
  elm.SourceI()     -- current source (circle with arrow)
  elm.SourceSin()   -- AC source (sine wave)
  elm.SourceControlledV(), elm.SourceControlledI()  -- dependent (diamond)
  elm.Battery(), elm.BatteryCell()

Semiconductors:
  elm.Diode(), elm.Zener(), elm.Schottky(), elm.LED()
  elm.BjtNpn(), elm.BjtPnp()
  elm.NFet(), elm.PFet(), elm.JFetN(), elm.JFetP()

Opamp & ICs:
  elm.Opamp()   -- anchors: in1, in2, out, vs, vd
  elm.Ic(), elm.Ic555()

Lines & Connections:
  elm.Line()        -- plain wire
  elm.Wire(shape)   -- multi-segment: '-', '-|', '|-', 'n', 'c', 'z', 'N'
  elm.Dot()         -- connection dot
  elm.Ground(), elm.GroundChassis(), elm.GroundSignal()
  elm.Vdd(), elm.Vss()
  elm.NoConnect()

Annotations:
  elm.Label(), elm.Tag(), elm.Annotate()
  elm.CurrentLabel(), elm.CurrentLabelInline()
  elm.LoopCurrent()
  elm.Gap()          -- labeled open terminal pair

DIRECTION AND PLACEMENT
------------------------
Direction methods (also accept length arg):
  .right(), .left(), .up(), .down()
  .right(5)   -- go right with length=5
  .theta(45)  -- arbitrary angle

Explicit length:
  .length(5)

Positioning:
  .at(position)      -- place start at a specific point or anchor
  .to(position)      -- stretch to reach endpoint (may go diagonal)
  .tox(anchor)       -- extend horizontally to match x-coordinate
  .toy(anchor)       -- extend vertically to match y-coordinate
  .endpoints(start, end)  -- set exact start and end points

Anchor alignment:
  .anchor('name')    -- align named anchor to current position
  .drop('name')      -- after placing, move cursor to this anchor

Drawing cursor:
  .hold()            -- place without advancing cursor
  .dot()             -- connection dot at end
  .idot()            -- connection dot at start

LABELS
------
  elm.Resistor().label('R1')                         # default: top
  elm.Resistor().label('100k', loc='bottom')
  elm.Capacitor().label('C1', loc='top').label('10nF', loc='bottom')

loc values: 'top' (default), 'bottom', 'left', 'right', 'center'

Label parameters:
  ofst       -- offset from default (float or (x,y) tuple)
  fontsize   -- override font size
  color      -- label color
  rotate     -- True (rotate with element) or float (angle)

Multiple labels as list (for +/- annotations):
  elm.Resistor().label(['+', '$v_o$', '-'], loc='bottom')

ANCHORS
-------
Two-terminal elements: start, center, end
  R = elm.Resistor().right()
  R.start  -> Point(0, 0)
  R.end    -> Point(3, 0)

Multi-terminal elements:
  Opamp: in1, in2, out, vs, vd, center
  BJT:   base, collector, emitter
  MOSFET: gate, drain, source

Access: Q.base or Q['base'] (index notation v0.21+)

COORDINATE SYSTEM
-----------------
- Origin at (0,0), default direction: right
- Standard Cartesian: x right, y up
- Default unit: 3.0 (1 unit lead + 1 unit body + 1 unit lead)
- d.here -- current cursor position as Point(x, y)
- d.theta -- current direction angle

Push/pop:
  d.push()   # save position+direction
  d.pop()    # restore
  # or: with d.hold(): ...

Move cursor:
  d.move(dx=..., dy=...)

ORIENTATION CONTROL
-------------------
  .flip()      -- mirror perpendicular to direction
  .reverse()   -- swap start and end
  .scale(factor)

STYLING
-------
  elm.Resistor().color('blue')
  elm.Capacitor().fill('lightblue')
  elm.Line().style('--')         # dashed
  elm.Resistor().zorder(5)

Global style:
  elm.style(elm.STYLE_IEC)   # European box-style
  elm.style(elm.STYLE_IEEE)  # US zigzag (default)

CRITICAL GOTCHAS
----------------
1. INSTANTIATE before adding:
   CORRECT: d.add(elm.Dot())
   WRONG:   d.add(elm.Dot)    # passes class, not instance

2. add_label() DOES NOT EXIST in v0.22.
   Use chained .label() instead.

3. Label loc is RELATIVE TO DRAWING DIRECTION, not absolute.
   For vertical elements, 'left'/'right' can be counter-intuitive.

4. Non-interactive backend (required for scripts/servers):
   import matplotlib
   matplotlib.use('Agg')
   # Put this BEFORE importing schemdraw

5. Context manager auto-adds elements (v0.18+):
   Inside `with schemdraw.Drawing()`, no d.add() needed.

COMPLETE EXAMPLE: RC Low-Pass Filter
-------------------------------------
import schemdraw
import schemdraw.elements as elm

with schemdraw.Drawing(show=False) as d:
    d.config(unit=3, font='Times New Roman')
    V1 = elm.SourceV().up().label('V1\\nAC 1', loc='left')
    R1 = elm.Resistor().right().label('R1\\n10k')
    elm.Dot()
    elm.Label().label('out', loc='right')
    C1 = elm.Capacitor().down().label('C1\\n10n', loc='bottom')
    elm.Line().left().to(V1.start)
    elm.Ground()
    d.save('rc_lowpass.pdf')
"""


@mcp.tool()
def pyltspice_api() -> str:
    """Return PyLTSpice / spicelib API reference for programmatic LTSpice control.

    Covers: AscEditor for .asc file creation/editing, SimRunner for running
    simulations, RawRead for reading results, and key data classes.
    """
    return """
PyLTSpice / spicelib API Reference
====================================

VERSION: PyLTSpice v5.4+ wraps spicelib v1.4+.
The actual classes live in spicelib; PyLTSpice re-exports them.

IMPORT
------
from PyLTSpice import AscEditor, SpiceEditor, SimRunner, RawRead
# or equivalently:
from spicelib import AscEditor, SpiceEditor, SimRunner, RawRead

KEY DATA CLASSES (from spicelib.editor.base_schematic)
-----------------------------------------------------
Point(X, Y)              -- coordinate on schematic canvas
Line(V1: Point, V2: Point)  -- wire or graphical line
Text(coord, text, size, type)  -- label, directive, or comment
TextTypeEnum             -- .DIRECTIVE, .COMMENT, .LABEL, .ATTRIBUTE
ERotation                -- R0, R90, R180, R270, M0, M90, M180, M270
SchematicComponent       -- .reference, .symbol, .position, .rotation, .attributes

1. EDITING .ASC FILES WITH AscEditor
=====================================

Opening an existing file:
    editor = AscEditor("./circuit.asc")

IMPORTANT: AscEditor requires an existing file. To create from scratch,
write the minimal ASC text first:
    with open('new.asc', 'w') as f:
        f.write('Version 4\\nSHEET 1 880 680\\n')
    editor = AscEditor('new.asc')

Modifying component values:
    editor.set_component_value('R1', '4.7k')
    editor.set_component_value('C1', 100e-9)     # auto-converts to eng notation
    editor.set_component_value('R2', 2000)

Getting component info:
    val = editor.get_component_value('R1')         # string '4.7k'
    fval = editor.get_component_floatvalue('R1')   # float
    info = editor.get_component_info('R1')         # dict
    params = editor.get_component_parameters('R1')

Listing components:
    all_refs = editor.get_components()             # ['R1', 'C1', 'V1', ...]
    resistors = editor.get_components('R')         # only R-prefix
    caps_res = editor.get_components('RC')         # R and C prefix

Changing component models:
    editor.set_element_model('D1', '1N4148')
    editor.set_element_model('V3', "SINE(0 1 3k 0 0 0)")

Component position/rotation:
    from spicelib.editor.base_schematic import Point, ERotation
    pos, rot = editor.get_component_position('R1')
    editor.set_component_position('R1', Point(200, 300), ERotation.R90)

Adding a component programmatically:
    from spicelib.editor.base_schematic import SchematicComponent, Point, ERotation
    comp = SchematicComponent(editor, "SYMBOL res 200 300 R0")
    comp.symbol = 'res'
    comp.position = Point(200, 300)
    comp.rotation = ERotation.R0
    comp.reference = 'R2'
    comp.attributes['Value'] = '10k'
    editor.add_component(comp)

Removing a component:
    editor.remove_component('R2')

Adding wires (no add_wire() method -- use wires list directly):
    from spicelib.editor.base_schematic import Line, Point
    wire = Line(Point(0, 192), Point(128, 192))
    editor.wires.append(wire)
    editor.updated = True

Adding net labels / flags:
    from spicelib.editor.base_schematic import Text, TextTypeEnum
    flag = Text(coord=Point(192, 192), text='out', type=TextTypeEnum.LABEL)
    editor.labels.append(flag)
    editor.updated = True

Simulation directives:
    editor.add_instruction('.tran 10m')
    editor.add_instruction('.ac dec 100 1 100k')
    editor.add_instructions('.meas TRAN vout_max MAX V(out)',
                            '.step param R1 1k 10k 1k')

Parameters:
    editor.set_parameter('freq', '1k')
    editor.set_parameters(R_val=1000, C_val=1e-6)
    # NOTE: use set_parameter(), NOT add_instruction('.param ...')

Removing instructions:
    editor.remove_instruction('.tran 10m')
    editor.remove_Xinstruction(r'\\.step.*')   # regex-based

Saving:
    editor.save_netlist('./output.asc')    # must be .asc extension

Scaling/transforming:
    editor.scale(offset_x=100, offset_y=50, scale_x=2.0, scale_y=2.0)

Subcircuit access:
    sub = editor.get_subcircuit('XU1')
    editor.set_component_value('XU1:C2', 20e-12)  # ':' separator

2. RUNNING SIMULATIONS WITH SimRunner
=======================================

from PyLTSpice import SimRunner
from spicelib.simulators.ltspice_simulator import LTspice

runner = SimRunner(output_folder='./temp')

Run from .asc file:
    runner.run('./circuit.asc')

Create netlist, modify, run:
    LTspice.create_netlist('./circuit.asc')    # produces .net
    netlist = SpiceEditor('./circuit.net')
    netlist.set_component_value('R1', '10k')
    runner.run(netlist)

Run with AscEditor:
    editor = AscEditor('./circuit.asc')
    editor.set_component_value('R1', '10k')
    runner.run(editor)

Wait and iterate:
    runner.wait_completion(timeout=120)
    for raw_file, log_file in runner:
        print(f"Raw: {raw_file}, Log: {log_file}")

Synchronous run:
    raw_path, log_path = runner.run_now('./circuit.asc', timeout=60)

Batch sweep:
    for r_val in ['1k', '4.7k', '10k']:
        netlist.set_component_value('R1', r_val)
        runner.run(netlist)
    runner.wait_completion()
    runner.file_cleanup()

LTspice executable: auto-detected at C:/Program Files/ADI/LTspice/LTspice.exe.
Custom: LTspice.create_from(path)

3. READING RESULTS WITH RawRead
=================================

from PyLTSpice import RawRead

raw = RawRead('./simulation.raw')

List traces:
    raw.get_trace_names()   # ['time', 'V(out)', 'I(R1)', ...]

Get waveform data:
    wave = raw.get_wave('V(out)', step=0)   # numpy array
    time = raw.get_axis(step=0)
    time = raw.get_time_axis(step=0)

Stepped simulations:
    steps = raw.get_steps()
    for step_idx in range(len(steps)):
        t = raw.get_axis(step=step_idx)
        v = raw.get_wave('V(out)', step=step_idx)

Export:
    raw.to_csv('./results.csv')
    raw.to_excel('./results.xlsx')
    df = raw.to_dataframe()

KEY NOTES
---------
- AscEditor requires an existing file -- cannot create blank .asc from scratch
- No add_wire() method -- append Line objects to editor.wires directly
- Labels append to editor.labels directly
- set_parameter() for .param, NOT add_instruction('.param ...')
- Unique sim instructions (.tran, .ac, .dc) auto-replaced on add_instruction()
- LTspice 17+ uses UTF-16 LE encoding; older uses ASCII/UTF-8
- File encoding is auto-detected
"""


@mcp.tool()
def ltspice_anchor_points() -> str:
    """Return LTSpice symbol anchor point reference (measured values).

    Shows how symbol placement coordinates relate to terminal positions.
    Essential for programmatic ASC file generation.
    """
    return """
LTSpice Symbol Anchor Points (Measured)
=========================================

All coordinates in LTSpice pixels. Grid size = 16px.
sym(x, y) = SYMBOL placement coordinate in ASC file.

RESISTOR (res)
--------------
R0  (vertical):   sym(x,y) -> upper terminal (x+16, y),    lower terminal (x+16, y+64)
R90 (horizontal): sym(x,y) -> left terminal  (x-64, y+16), right terminal (x, y+16)
                  NOTE: R90 anchor is at the RIGHT terminal side
R180:             sym(x,y) -> lower terminal (x+16, y+64), upper terminal (x+16, y)
R270:             sym(x,y) -> right terminal (x+64, y+16), left terminal  (x, y+16)

INDUCTOR (ind)
--------------
R0  (vertical):   sym(x,y) -> upper terminal (x+16, y-16), lower terminal (x+16, y+96)
R90 (horizontal): sym(x,y) -> left terminal  (x-96, y+16), right terminal (x, y+16)
                  NOTE: R90 anchor is at the RIGHT terminal side
R180:             sym(x,y) -> upper terminal (x+16, y+112), lower terminal (x+16, y)
R270:             sym(x,y) -> right terminal (x+96, y+16), left terminal  (x, y+16)

CAPACITOR (cap)
---------------
R0  (vertical):   sym(x,y) -> upper terminal (x+16, y),    lower terminal (x+16, y+64)
R90 (horizontal): sym(x,y) -> left terminal  (x-64, y+16), right terminal (x, y+16)
                  NOTE: R90 anchor is at the RIGHT terminal side
R180:             sym(x,y) -> lower terminal (x+16, y+64), upper terminal (x+16, y)
R270:             sym(x,y) -> right terminal (x+64, y+16), left terminal  (x, y+16)

CURRENT SOURCE (current)
-------------------------
R0:   sym(x,y) -> upper terminal (x+16, y),     lower terminal (x+16, y+96)
R180: sym(x,y) -> upper terminal (x, y-64),     lower terminal (x, y+64)

VOLTAGE SOURCE (voltage)
-------------------------
R0:   sym(x,y) -> upper terminal(+) (x, y),     lower terminal(-) (x, y+96)
R180: sym(x,y) -> upper terminal(+) (x, y+96),  lower terminal(-) (x, y)

SYMBOL SIZES (width x height in LTSpice px)
--------------------------------------------
res:     R0 (32x64),  R90 (64x32)
ind:     R0 (32x112), R90 (112x32)
cap:     R0 (32x64),  R90 (64x32)
current: R0 (32x96),  R180 (32x96)
voltage: R0 (32x96),  R180 (32x96)

COORDINATE SYSTEM
-----------------
- LTSpice Y-axis points DOWNWARD (opposite of schemdraw)
- Grid snapping: round to nearest multiple of 16
- Scale factor: schemdraw 1 unit = 64 LTSpice pixels
"""


@mcp.tool()
def ltspice_asc_format() -> str:
    """Return detailed LTSpice .asc file format specification.

    Covers all ASC file elements: WIRE, FLAG, IOPIN, SYMBOL, WINDOW,
    SYMATTR, TEXT, and graphical elements.
    """
    return """
LTSpice .asc File Format Specification
========================================

An ASC file is plain-text (UTF-8 or UTF-16 LE for LTspice 17+).
Version can be "Version 4" or "Version 4.1" (newer LTspice).

HEADER
------
Version 4
SHEET 1 <width> <height>

Typical sheet sizes: 880x680 (small), 1600x680 (medium), 2844x1336 (large)

ELEMENTS
--------

WIRE x1 y1 x2 y2
  Connects two points. Coordinates in LTSpice pixels.
  Grid: 16px. All coordinates should be multiples of 16.
  Wires are always axis-aligned (horizontal or vertical).
  Wires connecting at the same coordinate are electrically joined.
  Negative coordinates are valid and commonly used.

FLAG x y <netname>
  Net label at position (x,y). netname "0" = ground.
  Multiple FLAG 0 entries allowed for multiple ground connections.
  Any other name creates a named net (used for power: +V, -V, Vdd;
  signals: OUT, IN, etc.)
  Example: FLAG 192 192 0       (ground)
           FLAG 192 64 out      (net label "out")
           FLAG 144 -1168 +V    (positive supply rail)

IOPIN x y <direction>
  I/O port marker. Must follow a FLAG line.
  direction: In, Out, BiDir
  Used mainly in hierarchical designs.
  Example: IOPIN 192 64 Out

SYMBOL <symbolname> x y <rotation>
  Component placement. symbolname is relative to sym/ directory.
  Built-in symbols (no path prefix): res, cap, ind, current, voltage
  Subdirectory symbols use backslash:
    opamps\\AD711, References\\AD590, SpecialFunctions\\LTC6905
  Top-level custom symbols: LT1021-7, AD820

  Rotation: R0, R90, R180, R270 (normal)
            M0, M90, M180, M270 (mirrored)
  R0 = vertical (default), R90 = horizontal (rotated 90 deg clockwise)
  M = mirrored version of corresponding R rotation

WINDOW <num> x y <alignment> <size>
  Display positioning for component attributes.
  num: 0=InstName, 3=Value, 123=Value2/SpiceLine, 39=SpiceLine2
  alignment: Left, Right, Top, VBottom, VTop
  size: font size. 0 = hidden, 2 = normal
  Setting position to (0,0) with size 0 effectively hides the window.
  Negative offsets are valid for repositioning.
  Each WINDOW belongs to the preceding SYMBOL.

  Common patterns from real LTSpice files:
    WINDOW 0 0 56 VBottom 2     (for R90 resistors - InstName)
    WINDOW 3 32 56 VTop 2       (for R90 resistors - Value)
    WINDOW 123 0 0 Left 0       (hide SpiceLine)
    WINDOW 39 0 0 Left 0        (hide SpiceLine2)
    WINDOW 0 24 80 Left 2       (for R180 current source - InstName)
    WINDOW 3 24 0 Left 2        (for R180 current source - Value)

SYMATTR <attribute> <value>
  Component attribute. Must follow SYMBOL line (after WINDOW lines).
  Attributes: InstName, Value, Value2, SpiceModel, SpiceLine,
              SpiceLine2, Prefix, Def_Sub
  Value2 used for continuation of long parameters:
    SYMATTR Value PULSE(-1 1 5u
    SYMATTR Value2 1n 1n 5u 10u)
  SpiceLine for subcircuit parameters:
    SYMATTR SpiceLine Gain=1
  Examples:
    SYMATTR InstName R1
    SYMATTR Value 10k
    SYMATTR SpiceModel 1N4148

TEXT x y <alignment> <size> !<directive>
TEXT x y <alignment> <size> ;<comment>
  Simulation directive (! prefix) or comment (; prefix).
  alignment: Left, Right, Center, Top, Bottom
  size: font size (typically 2)
  \\n encodes literal newlines for multi-line text.
  Examples:
    TEXT 0 512 Left 2 !.ac dec 100 1 1Meg
    TEXT 0 544 Left 2 !.tran 10m
    TEXT 584 80 Left 2 !.dc V1 0 10 1m\\n.temp -55 25 150
    TEXT 0 576 Left 2 ;This is a comment

  Common SPICE directives:
    .tran <tstop> [startup]
    .ac dec <npoints> <fstart> <fstop>
    .dc <source> <start> <stop> <step>
    .noise V(out) V1 dec 100 1 100k
    .op
    .meas TRAN <name> <function> <expression>
    .step param <name> <start> <stop> <step>
    .param <name>=<value>
    .temp <temp1> [temp2] ...

  Common source value formats:
    AC 1                              (AC analysis source)
    SINE(offset amplitude frequency)
    PULSE(V1 V2 Tdelay Trise Tfall Ton Tperiod)
    PWL(t1 v1 t2 v2 ...)

GRAPHICAL ELEMENTS (optional)
-----------------------------
LINE Normal x1 y1 x2 y2 [style]
RECTANGLE Normal x1 y1 x2 y2 [style]
CIRCLE Normal x1 y1 x2 y2 [style]
ARC Normal x1 y1 x2 y2 x3 y3 x4 y4 [style]

COMPLETE MINIMAL EXAMPLE (RC lowpass from converter)
-----------------------------------------------------
Version 4
SHEET 1 1600 680
WIRE 0 192 0 384
WIRE 0 192 128 192
WIRE 128 192 192 192
WIRE 192 192 192 256
WIRE 192 256 192 384
WIRE 192 384 0 384
FLAG 0 384 0
FLAG 192 192 out
IOPIN 192 192 Out
SYMBOL current 0 320 R180
WINDOW 0 24 80 Left 2
WINDOW 3 24 0 Left 2
SYMATTR InstName I1
SYMATTR Value AC 1
SYMBOL res 192 176 R90
WINDOW 0 0 56 VBottom 2
WINDOW 3 32 56 VTop 2
SYMATTR InstName R1
SYMATTR Value 1k
SYMBOL cap 176 192 R0
SYMATTR InstName C1
SYMATTR Value 1u
TEXT 0 512 Left 2 !.ac dec 100 1 100k

COMPLETE EXAMPLE (inverting op-amp from AD820.asc)
---------------------------------------------------
Version 4
SHEET 1 1240 700
WIRE 432 -1216 416 -1216
WIRE 528 -1216 512 -1216
WIRE 544 -1216 528 -1216
WIRE 640 -1216 624 -1216
WIRE 528 -1120 528 -1216
WIRE 544 -1120 528 -1120
WIRE 640 -1104 640 -1216
WIRE 640 -1104 608 -1104
WIRE 704 -1104 640 -1104
WIRE 544 -1088 432 -1088
WIRE 432 -1072 432 -1088
WIRE 432 -976 432 -992
SYMBOL voltage 144 -1168 R0
WINDOW 123 0 0 Left 2
WINDOW 39 0 0 Left 2
SYMATTR InstName V1
SYMATTR Value 15
SYMBOL voltage 432 -1088 R0
WINDOW 123 24 146 Left 2
WINDOW 39 24 125 Left 2
SYMATTR InstName Vin
SYMATTR Value SINE(0 1 10K)
SYMBOL res 528 -1232 R90
WINDOW 0 0 56 VBottom 2
WINDOW 3 32 56 VTop 2
SYMATTR InstName R1
SYMATTR Value 10K
SYMBOL res 640 -1232 R90
WINDOW 0 0 56 VBottom 2
WINDOW 3 32 56 VTop 2
SYMATTR InstName R2
SYMATTR Value 10K
SYMBOL AD820 576 -1168 R0
SYMATTR InstName U1
FLAG 144 -1168 +V
FLAG 432 -976 0
FLAG 704 -1104 OUT
FLAG 576 -1136 +V
FLAG 576 -1072 -V
TEXT 688 -1000 Left 2 !.tran 1m

NOTE: Op-amp power pins connected via FLAG net labels (+V, -V),
      not by explicit wires -- this is the standard LTSpice pattern.

DIRECTION MAPPING (schemdraw -> LTSpice)
-----------------------------------------
schemdraw right (dx>0) -> LTSpice R90
schemdraw left  (dx<0) -> LTSpice R270
schemdraw up    (dy>0) -> LTSpice R0   (Y-axis inverted!)
schemdraw down  (dy<0) -> LTSpice R180

VALUE FORMATTING (SI prefixes)
-------------------------------
1e12 -> 1T       1e-3  -> 1m
1e9  -> 1G       1e-6  -> 1u
1e6  -> 1Meg     1e-9  -> 1n
1e3  -> 1k       1e-12 -> 1p
1    -> 1        1e-15 -> 1f

ASC FILE ORDERING CONVENTION
------------------------------
Standard element ordering in real LTSpice files:
  1. Version header
  2. SHEET declaration
  3. All WIRE lines
  4. All FLAG lines (ground and net labels)
  5. IOPIN lines (if any, after corresponding FLAG)
  6. SYMBOL + WINDOW + SYMATTR blocks (each component together)
  7. TEXT lines (directives and comments)
"""


@mcp.tool()
def schemdraw_to_ltspice_guide() -> str:
    """Return guide for converting schemdraw circuits to LTSpice ASC format.

    Covers the SchemdrawToLTSpice converter class usage, coordinate conversion,
    and the complete workflow from schemdraw drawing to .asc file.
    """
    return """
schemdraw to LTSpice ASC Conversion Guide
============================================

OVERVIEW
--------
Use the SchemdrawToLTSpice class from schemdraw_to_ltspice.py to convert
schemdraw circuit descriptions into LTSpice .asc files.

COORDINATE CONVERSION
---------------------
schemdraw: Y-axis UP,   units are abstract (typically 1 unit = 1 element length)
LTSpice:   Y-axis DOWN, units are pixels (grid = 16px)

Formula:
  lx = sx * scale                          (default scale = 64)
  ly = y_offset - sy * scale + y_offset    (default y_offset = 192)
Then snap to nearest 16px grid.

BASIC USAGE
-----------
    from schemdraw_to_ltspice import SchemdrawToLTSpice

    conv = SchemdrawToLTSpice(scale=64, y_offset=192)

    # Add components using schemdraw coordinates (start, end tuples)
    conv.add_resistor('R1', 100, (0, 3), (3, 3))       # 100 ohm
    conv.add_inductor('L1', 1e-3, (3, 3), (3, 0))      # 1 mH
    conv.add_capacitor('C1', 1e-6, (0, 3), (0, 0))     # 1 uF
    conv.add_current_source('I1', 'AC 1', (0, 0), (0, 3))
    conv.add_voltage_source('V1', 'AC 1', (0, 0), (0, 3))

    # Add wires, ground, labels
    conv.add_wire((0, 0), (3, 0))
    conv.add_ground((0, 0))
    conv.add_label((3, 3), 'out', is_output=True)

    # Add SPICE directives
    conv.add_spice_directive(0, 512, '.ac dec 100 1 1Meg')

    # Generate and save
    conv.save_asc('output.asc', sheet_width=1600, sheet_height=680)

EXTRACTING COORDINATES FROM SCHEMDRAW ELEMENTS
-----------------------------------------------
After drawing with schemdraw, extract element positions:

    d = schemdraw.Drawing()
    R1 = d.add(elm.Resistor().right().label('R1'))
    start = (R1.absanchors['start'][0], R1.absanchors['start'][1])
    end   = (R1.absanchors['end'][0],   R1.absanchors['end'][1])

Or use the helper function:
    from schemdraw_to_ltspice import convert_schemdraw_element
    convert_schemdraw_element(conv, R1, 'R1', 100)

DIRECTION MAPPING
-----------------
schemdraw direction -> LTSpice rotation:
  right (dx > 0) -> R90
  left  (dx < 0) -> R270
  up    (dy > 0) -> R0    (remember: schemdraw Y is up)
  down  (dy < 0) -> R180

VALUE FORMATTING
----------------
Automatic SI prefix conversion:
  1e6  -> 1Meg     1e-3 -> 1m
  1e3  -> 1k       1e-6 -> 1u
  1    -> 1        1e-9 -> 1n
                   1e-12 -> 1p

ASC FILE FORMAT
---------------
The generated .asc file contains:
  Version 4
  SHEET 1 <width> <height>
  WIRE x1 y1 x2 y2          (connections)
  FLAG x y <name>            (net labels / ground)
  IOPIN x y Out              (output pins)
  SYMBOL <type> x y <rot>    (component placement)
  WINDOW ...                 (display settings)
  SYMATTR InstName <name>    (component name)
  SYMATTR Value <value>      (component value)
  TEXT x y Left 2 !<cmd>     (SPICE directives)

COMPLETE WORKFLOW EXAMPLE
--------------------------
import schemdraw
import schemdraw.elements as elm
from schemdraw_to_ltspice import SchemdrawToLTSpice

# Step 1: Create schemdraw circuit
with schemdraw.Drawing(show=False) as d:
    d.config(unit=3)
    I1 = elm.SourceI().up().label('I1')
    R1 = elm.Resistor().right().label('R1')
    elm.Dot()
    d.push()
    L1 = elm.Inductor2().down().label('L1')
    d.pop()
    elm.Line().right()
    C1 = elm.Capacitor().down().label('C1')
    elm.Line().left().to(I1.start)
    elm.Ground()
    d.save('circuit.pdf')

# Step 2: Extract positions and build converter
conv = SchemdrawToLTSpice()
# ... add components with extracted coordinates ...
conv.save_asc('circuit.asc')

# Step 3 (optional): Run simulation with PyLTSpice
from PyLTSpice import SimRunner, RawRead
runner = SimRunner(output_folder='./temp')
raw_path, log_path = runner.run_now('circuit.asc', timeout=60)
raw = RawRead(raw_path)
print(raw.get_trace_names())

CONVERTER API REFERENCE (SchemdrawToLTSpice class)
---------------------------------------------------
Constructor:
  SchemdrawToLTSpice(scale=64, y_offset=192)
    scale: 1 schemdraw unit = 64 LTSpice pixels
    y_offset: baseline for Y-axis inversion

Component methods (schemdraw coordinates):
  add_resistor(name, value_float, start_tuple, end_tuple)
  add_inductor(name, value_float, start_tuple, end_tuple)
  add_capacitor(name, value_float, start_tuple, end_tuple)
  add_current_source(name, value_str, start_tuple, end_tuple)
  add_voltage_source(name, value_str, start_tuple, end_tuple)

Connection methods:
  add_wire(start_tuple, end_tuple)
  add_ground(pos_tuple)
  add_label(pos_tuple, name, is_output=bool)

Directive methods:
  add_spice_directive(x_pixel, y_pixel, directive_str)

Output:
  save_asc(filename, sheet_width=1600, sheet_height=400)
  generate_asc(sheet_width=1600, sheet_height=400) -> str

Helper function:
  convert_schemdraw_element(converter, element, name, value)
    Extracts start/end from schemdraw element and calls appropriate method.
    Supported types: Resistor, Inductor, Inductor2, Capacitor,
                     Capacitor2, SourceI, SourceV, Line

IMPORTANT: Sources use string values ('AC 1'), passives use float (1e3).
IMPORTANT: For sources, start = negative terminal, end = positive terminal.

TIPS
----
- Always add a ground (FLAG ... 0) -- LTSpice requires it.
- Zero-length wires are automatically filtered out.
- Duplicate wires are automatically removed.
- For R90 components, the symbol anchor is at the RIGHT terminal.
  The converter handles this internally.
- sheet_height should be large enough to fit all components.
  Typical: 400-680 for simple circuits.
- Use PyLTSpice's AscEditor to further modify generated .asc files
  (e.g., adding simulation directives, changing values).
- Coordinate system: schemdraw (0,0) maps to LTSpice (0, 384) by default.
  schemdraw unit 3 up maps to LTSpice y=192.
- Use d.push()/d.pop() in schemdraw for branching circuits (pi/T filters).
- For parallel components, use elm.Dot() at junction, d.push(), branch
  component, d.pop(), continue on main path.
"""


@mcp.tool()
def ltspice_advanced_components() -> str:
    """Return reference for advanced LTSpice component types beyond basic passives.

    Covers: transistors (BJT, JFET, MOSFET, IGBT), diodes, op-amps,
    behavioral sources, coupled inductors, digital, switches, crystals,
    and advanced analysis directives. Based on real Educational examples.
    """
    return """
LTSpice Advanced Components & Patterns (from Educational Examples)
===================================================================

TRANSISTORS
-----------
BJT NPN:
  SYMBOL npn x y R0           (or NPN -- case-insensitive)
  SYMATTR InstName Q1
  SYMATTR Value 2N3904

BJT PNP:
  SYMBOL pnp x y M180         (common: M180 for standard orientation)
  WINDOW 0 60 68 Left 2
  WINDOW 3 64 28 Left 2
  SYMATTR InstName Q2
  SYMATTR Value 2N3906

JFET N-channel:
  SYMBOL njf x y R0           (also NJF)
  SYMATTR InstName J1
  SYMATTR Value 2N5484

MOSFET:
  SYMBOL nmos x y R0          (N-channel enhancement)
  SYMBOL pmos x y M180        (P-channel)
  SYMATTR InstName M1
  SYMATTR Value IRFP240

IGBT:
  SYMBOL misc\\nigbt x y R0
  SYMATTR InstName Z1
  SYMATTR Prefix Z

DIODES
------
Standard diode:
  SYMBOL diode x y R0
  SYMATTR InstName D1
  SYMATTR Value 1N4148

  R180 (flipped): WINDOW 0 24 72 Left 2 / WINDOW 3 24 0 Left 2

Zener diode:
  SYMBOL zener x y M180
  SYMATTR InstName D1
  SYMATTR Value 6.3V

Schottky diode:
  SYMBOL schottky x y R0
  SYMATTR InstName D1
  SYMATTR Value 1N5818

OP-AMPS
-------
Ideal (no supply pins):
  SYMBOL OPAMPS\\OPAMP x y R0
  SYMATTR InstName U1
  (Requires .include opamp.sub)

UniversalOpamp2 (built-in, no supply needed):
  SYMBOL opamps\\UniversalOpamp2 x y R0
  SYMATTR InstName U1

Real models (need +V/-V supply via FLAG nets):
  SYMBOL opamps\\LT1001 x y R0        (or Opamps\\LT1001)
  SYMBOL AD820 x y R0
  Power: FLAG sx sy+32 +V / FLAG sx sy-32 -V  (offsets from symbol pos)

BEHAVIORAL / CONTROLLED SOURCES
---------------------------------
VCVS (Voltage-Controlled Voltage Source):
  SYMBOL e x y R0
  SYMATTR InstName E1
  SYMATTR Value 1                    (gain)
  SYMATTR Value Laplace=1./(1+.0005*s)**3   (Laplace transfer function)

VCCS (Voltage-Controlled Current Source):
  SYMBOL g x y R0
  SYMATTR InstName G1
  SYMATTR Value {2/R1}               (parameterized)

Behavioral Voltage Source:
  SYMBOL bv x y R0
  SYMATTR InstName B1
  SYMATTR Value V=exp(time-7)        (arbitrary expression)

Behavioral Current Source:
  SYMBOL bi2 x y R0
  SYMATTR InstName B1
  SYMATTR Value I={Cjo}/(1+max(V(bias),-.5*{Vj})/{Vj})**{m}

COUPLED INDUCTORS / TRANSFORMERS
---------------------------------
Use ind2 elements with K coupling statement:

  SYMBOL ind2 x1 y1 R0              (winding 1)
  SYMATTR InstName L1
  SYMATTR Value 100u
  SYMATTR Type ind

  SYMBOL ind2 x2 y2 M0              (winding 2)
  SYMATTR InstName L2
  SYMATTR Value 900u
  SYMATTR Type ind

  TEXT x y alignment 2 !K1 L1 L2 1   (coupling coefficient = 1)

For 3+ windings: !K1 L1 L2 L3 1

CRYSTAL:
  SYMBOL MISC\\XTAL x y R90
  SYMATTR InstName Y1
  SYMATTR Value 0.25p
  SYMATTR SpiceLine Rser=0.1 Lser=0.001 Cpar=5e-011

SWITCHES
--------
Voltage-controlled switch:
  SYMBOL sw x y M180
  SYMATTR InstName S1
  SYMATTR Value MYSW
  TEXT x y Left 2 !.model MYSW SW(Ron=1 Roff=1Meg Vt=.5 Vh=-.4)

DIGITAL ELEMENTS
-----------------
  SYMBOL Digital\\XOR x y R0
  SYMBOL DIGITAL\\SCHMTBUF x y R0
  SYMBOL Digital\\dflop x y M0

SPECIAL / MISC
--------------
  SYMBOL Misc\\jumper x y R0          (test point / jumper)
  SYMBOL SpecialFunctions\\sample x y R0  (sample & hold)
  SYMBOL SpecialFunctions\\MODULATE x y R0 (modulator)
  SYMBOL POWERPRODUCTS\\LT1184F x y R0    (power IC)

GRAPHICAL ANNOTATIONS
----------------------
  RECTANGLE Normal x1 y1 x2 y2 2     (box around circuit section)
  LINE Normal x1 y1 x2 y2            (annotation line)
  DATAFLAG x y ""                     (data readout point)

ADVANCED ANALYSIS DIRECTIVES
------------------------------
DC sweep:
  .dc V1 0 10 1m
  .dc V1 0 15 10m I1 20u 100u 20u     (nested sweep)

Noise analysis:
  .noise V(out) V1 oct 10 1K 100K

S-parameter / Network analysis:
  .net V(out) V1 Rout=50 Rin=50
  .net I(Rout) V4                      (auto-detect impedances)

Fourier analysis:
  .four 1K V(out)

Monte Carlo:
  .step param X 0 20 1                 (cycle MC runs)
  Component: {mc(1n, tol)}             (random tolerance)
  .param tol=.05

Parameter sweep:
  .step param X list 1 10 100 1K       (list values)
  .step oct param V 1m 1.44 2          (octave steps)

Simulation options:
  .options method=trap
  .options maxstep=.0125u
  .options plotwinsize=0 numdgt=15

Include / Model:
  .include opamp.sub
  .model NP NPN(BF=125 Cje=.5p Cjc=.5p Rb=500)
  .model PN LPNP(BF=25 Cje=.3p Cjc=1.5p Rb=250)

Subcircuit (.subckt) in TEXT directive:
  TEXT x y Left 2 !.subckt MYCOMP T1 T2\\n...\\n.ends MYCOMP

PARAMETERIZATION
-----------------
Use {} braces for expressions:
  SYMATTR Value {6*R}
  SYMATTR Value {mc(1n, tol)}
  .param f0=1k Q=0.5
  .param L1=R1*Q/(2*pi*f0)
  .param C1=1/(L1*(2*pi*f0)**2)

Functions: mc(val,tol), flat(x), gauss(x)

WINDOW PATTERNS BY ROTATION (comprehensive)
---------------------------------------------
R0  (vertical, default):   usually no WINDOW override needed
R90 (horizontal):
  res:  WINDOW 0 0 56 VBottom 2 / WINDOW 3 32 56 VTop 2
  cap:  WINDOW 0 0 32 VBottom 2 / WINDOW 3 32 32 VTop 2
  ind:  (uses R270 convention below)
R270 (horizontal inductor):
        WINDOW 0 32 56 VTop 2 / WINDOW 3 5 56 VBottom 2
  or:   WINDOW 0 32 56 VTop 2 / WINDOW 3 4 56 VBottom 2
M0  (mirrored vertical):
  g:    WINDOW 0 -10 9 Right 2 / WINDOW 3 -15 96 Right 2
  ind:  WINDOW 0 -2 30 Right 2 / WINDOW 3 -2 59 Right 2
M90 (mirrored horizontal):
  current: WINDOW 0 -32 40 VBottom 2 / WINDOW 3 32 40 VTop 2
M180 (mirrored, flipped):
  res:  WINDOW 0 36 76 Left 2 / WINDOW 3 36 40 Left 2
  pnp:  WINDOW 0 60 68 Left 2 / WINDOW 3 64 28 Left 2
  cap:  WINDOW 0 24 56 Left 2 / WINDOW 3 24 8 Left 2
R180 (flipped):
  current: WINDOW 0 24 80 Left 2 / WINDOW 3 24 0 Left 2
  diode:   WINDOW 0 24 72 Left 2 / WINDOW 3 24 0 Left 2

Hiding windows: WINDOW 123 0 0 Left 0 / WINDOW 39 0 0 Left 0
"""


@mcp.tool()
def spice_netlist_format() -> str:
    """Return SPICE netlist (.cir/.net/.sp) format reference.

    Covers: netlist syntax, component lines, subcircuits, and how to
    map netlist elements to LTSpice .asc schematic elements for
    automated .cir to .asc conversion.
    """
    return """
SPICE Netlist (.cir/.net/.sp) Format Reference
================================================

A SPICE netlist is a plain-text file describing a circuit.

BASIC STRUCTURE
---------------
* Title line (first line is always the title)
R1 node1 node2 value           ; Resistor
C1 node1 node2 value           ; Capacitor
L1 node1 node2 value           ; Inductor
V1 node+ node- value           ; Voltage source
I1 node+ node- value           ; Current source
D1 anode cathode model         ; Diode
Q1 C B E model                 ; BJT (Collector Base Emitter)
M1 D G S B model               ; MOSFET (Drain Gate Source Bulk)
J1 D G S model                 ; JFET
X1 node1 node2 ... subckt_name ; Subcircuit instance
.model name type(params)        ; Model definition
.subckt name node1 node2 ...    ; Subcircuit definition
.ends                           ; End subcircuit
.end                            ; End of netlist

COMPONENT LINE FORMAT
---------------------
First character determines type:
  R = Resistor       R<name> <n+> <n-> <value>
  C = Capacitor      C<name> <n+> <n-> <value> [IC=<v>]
  L = Inductor       L<name> <n+> <n-> <value> [IC=<i>]
  V = Voltage src    V<name> <n+> <n-> <value_or_spec>
  I = Current src    I<name> <n+> <n-> <value_or_spec>
  D = Diode          D<name> <anode> <cathode> <model>
  Q = BJT            Q<name> <C> <B> <E> [<S>] <model>
  M = MOSFET         M<name> <D> <G> <S> <B> <model>
  J = JFET           J<name> <D> <G> <S> <model>
  X = Subcircuit     X<name> <nodes...> <subckt_name>
  E = VCVS           E<name> <n+> <n-> <nc+> <nc-> <gain>
  F = CCCS           F<name> <n+> <n-> <vname> <gain>
  G = VCCS           G<name> <n+> <n-> <nc+> <nc-> <gain>
  H = CCVS           H<name> <n+> <n-> <vname> <gain>
  K = Coupling       K<name> L1 L2 <coefficient>
  * = Comment line

Node "0" or "GND" = ground reference.

SOURCE SPECIFICATIONS
---------------------
DC:     V1 n+ n- 5
AC:     V1 n+ n- AC 1
SINE:   V1 n+ n- SINE(offset amplitude frequency)
PULSE:  V1 n+ n- PULSE(V1 V2 Tdelay Trise Tfall Ton Tperiod)
PWL:    V1 n+ n- PWL(t1 v1 t2 v2 ...)

ANALYSIS COMMANDS
-----------------
.tran <tstop>
.ac dec <npts> <fstart> <fstop>
.dc <src> <start> <stop> <step>
.op
.noise V(out) Vin dec <npts> <fstart> <fstop>
.param <name>=<value>
.include <filename>

MAPPING: NETLIST -> ASC
========================

Component type -> ASC SYMBOL:
  R -> res
  C -> cap
  L -> ind (or ind2 for coupled)
  V -> voltage
  I -> current
  D -> diode
  Q (NPN) -> npn
  Q (PNP) -> pnp
  M (NMOS) -> nmos
  M (PMOS) -> pmos
  J (NJF) -> njf
  J (PJF) -> pjf
  X -> subcircuit symbol name (from .subckt)
  E -> e (VCVS)
  G -> g (VCCS)
  K -> TEXT directive (!K L1 L2 coefficient)

Node -> coordinate mapping strategy:
  1. Assign each unique node a (x, y) coordinate
  2. Place components between their nodes
  3. Generate WIRE elements to connect
  4. Add FLAG 0 for ground nodes
  5. Add FLAG name for labeled nodes

AUTOMATIC PLACEMENT ALGORITHM
-------------------------------
For .cir -> .asc conversion, a placement strategy is needed:

1. Parse netlist into components and nodes
2. Build adjacency graph (which nodes connect to which)
3. Assign grid positions to nodes:
   - Ground node (0) at bottom center
   - Source nodes at left
   - Output nodes at right
   - Internal nodes placed to minimize wire crossings
4. Place components between their nodes:
   - Determine orientation (R0/R90) from node positions
   - Calculate symbol position from terminal positions
5. Generate wires connecting components to nodes
6. Add FLAGS for ground and named nets
7. Add SPICE directives as TEXT elements

EXAMPLE: Simple RC netlist -> ASC
----------------------------------
Input (.cir):
  * RC Lowpass Filter
  V1 in 0 AC 1
  R1 in out 1k
  C1 out 0 1u
  .ac dec 100 1 100k
  .end

Output (.asc):
  Version 4
  SHEET 1 880 680
  WIRE 0 192 0 384
  WIRE 0 192 192 192
  WIRE 192 192 192 256
  WIRE 192 384 0 384
  FLAG 0 384 0
  FLAG 0 192 in
  FLAG 192 192 out
  IOPIN 192 192 Out
  SYMBOL voltage 0 192 R0
  SYMATTR InstName V1
  SYMATTR Value AC 1
  SYMBOL res 192 176 R90
  WINDOW 0 0 56 VBottom 2
  WINDOW 3 32 56 VTop 2
  SYMATTR InstName R1
  SYMATTR Value 1k
  SYMBOL cap 176 192 R0
  SYMATTR InstName C1
  SYMATTR Value 1u
  TEXT 0 512 Left 2 !.ac dec 100 1 100k
"""


@mcp.tool()
def schemdraw_elements_catalog() -> str:
    """Return the complete schemdraw v0.22 element catalog.

    ~180 element classes organized by category with all anchor names.
    Use this when you need to know what elements are available and
    what anchors they provide.
    """
    return """
schemdraw v0.22 Complete Element Catalog
==========================================

IMPORT: import schemdraw.elements as elm

TWO-TERMINAL ELEMENTS (Element2Term)
--------------------------------------
All have anchors: start, center, end, istart, iend
All support: .to(), .tox(), .toy(), .length(), .endpoints(),
             .dot(), .idot(), .shift()

Resistors:
  Resistor (=ResistorIEEE), ResistorIEC (box), ResistorVar,
  Thermistor, Photoresistor, Rshunt (v1,v2 anchors),
  RBox, RBoxVar, PotBox, PhotoresistorBox
  Potentiometer (+tap anchor), PotentiometerIEEE, PotentiometerIEC

Capacitors:
  Capacitor (polar=False), Capacitor2 (curved plate, polar=False),
  CapacitorVar, CapacitorTrim, Crystal

Inductors:
  Inductor (arcs), Inductor2 (loopy, +NE,NW,SE,SW anchors)

Diodes:
  Diode, Schottky, DiodeTunnel, DiodeShockley, Zener, DiodeTVS,
  Varactor, LED (arrows), LED2, Photodiode (arrows),
  Diac, Triac (+gate), SCR (+gate)

Sources:
  Source, SourceV (+/-), SourceI (arrow), SourceSin, SourcePulse,
  SourceSquare, SourceTriangle, SourceRamp
  SourceControlled (diamond), SourceControlledV, SourceControlledI
  NOTE: All sources default theta=90 (vertical)

Batteries:
  BatteryCell, Battery, BatteryDouble (+tap)

Meters:
  MeterV, MeterI, MeterA, MeterOhm, MeterArrow, Lamp, Lamp2,
  Solar, Neon

Switches:
  Switch (action='open'/'close'), Button, SwitchReed
  SwitchSpdt (a,b,c), SwitchSpdt2, SwitchDpst (p1,p2,t1,t2),
  SwitchDpdt, SwitchRotary, SwitchDIP

Fuses:
  Fuse (=FuseUS), FuseIEEE, FuseIEC, Breaker

Other 2-terminal:
  Memristor, Memristor2, Josephson, CPE, SparkGap,
  Nullator, Norator, CurrentMirror, VoltageMirror

Lines & Wires:
  Line (arrow=), DataBusLine, Arrow (double=False), Arrowhead
  Wire(shape) -- shapes: '-', '-|', '|-', 'z', 'N', 'n', 'c'
    Wire supports .to(), .delta(), .dot(), .idot(), k= for bend

MULTI-TERMINAL ELEMENTS
--------------------------

Op-Amp:
  Opamp(sign=True, leads=False)
  Anchors: in1(+), in2(-), out, vd, vs, n1, n2, n1a, n2a, center

BJT Transistors (3-terminal):
  BjtNpn, BjtPnp (circle=False)
  Anchors: base, collector, emitter, center
  BjtPnp2c (+C2 anchor)
  Inline: BjtNpn2, BjtPnp2 (start,end + base,collector,emitter)
  Specialty: NpnSchottky, PnpSchottky, NpnPhoto, PnpPhoto, IgbtN, IgbtP

FET Transistors (3-terminal):
  NFet, PFet (bulk=False)
  Anchors: gate, drain, source, center (+bulk)
  NMos, PMos (aliases for NFet/PFet with different style)
  Inline: NFet2, PFet2, NMos2, PMos2 (start,end + gate,drain,source)
  JFetN, JFetP, JFet2, JFetN2, JFetP2
  AnalogNFet, AnalogPFet, AnalogBiasedFet, Hemt

ONE-TERMINAL ELEMENTS
-----------------------
  Ground (lead=True), GroundSignal, GroundChassis
  Antenna, AntennaLoop, AntennaLoop2
  Vdd, Vss, NoConnect
  NOTE: drop=(0,0), theta=0 by default -- cursor doesn't move

LABELS & ANNOTATIONS
---------------------
  Dot (radius=.075, open=False), DotDotDot
  Label, Tag (width=1.5, height=.625)
  Gap (for voltage labels)
  Rect (corner1, corner2)
  CurrentLabel, CurrentLabelInline, VoltageLabelArc, ZLabel
  LoopCurrent (elm_list, direction='cw'), LoopArrow
  Arc2, Arc3, ArcZ, ArcN, ArcLoop, Annotate
  Encircle, EncircleBox (elm_list, padx, pady)

CONNECTORS
-----------
  OrthoLines, RightLines, BusConnect, BusLine
  Header (rows,cols,style='round'/'square'/'screw',numbering='lr'/'ud'/'ccw')
  Jumper, Terminal, Plug, Jack, CoaxConnect
  DB9/DE9, DB25, DA15, DC37, DD50

INTEGRATED CIRCUITS
--------------------
  Ic(size, pins=[IcPin(...)], slant=0)
    IcPin(name, pin, side='L'/'R'/'T'/'B', pos, slot, invert)
  IcDIP (pins=8, notch=True)
  Multiplexer (demux=False)
  VoltageRegulator (in,out,gnd)
  DFlipFlop (D,CLK,Q,Qbar,PRE,CLR)
  JKFlipFlop (J,K,CLK,Q,Qbar)
  Ic555, SevenSegment

TRANSFORMER
-----------
  Transformer(t1=4, t2=4, core=True, loop=False, align='center')
  Anchors: p1,p2 (primary), s1,s2 (secondary), tapP/tapS

CABLES
------
  Coax, Triax

COMPOUND ELEMENTS (ElementCompound)
-------------------------------------
  Optocoupler (anode,cathode,collector,emitter)
  Relay (switch='spst'/'spdt'/'dpst'/'dpdt')
  Wheatstone (vout=False)
  Rectifier (fill=False)

TWO-PORT NETWORKS
------------------
  TwoPort, VoltageTransactor, TransimpedanceTransactor,
  CurrentTransactor, TransadmittanceTransactor, Nullor, VMCMPair
  Anchors: in_p, in_n, out_p, out_n, center

VACUUM TUBES
------------
  TubeDiode, Triode, Tetrode, Pentode
  VacuumTube, DualVacuumTube, NixieTube

MISC
-----
  Speaker, Mic, Motor, AudioJack
  OutletA through OutletL (international outlets)

DSP MODULE (import schemdraw.dsp as dsp)
------------------------------------------
  dsp.Box, dsp.Circle, dsp.Square, dsp.Amp, dsp.OscillatorBox
  dsp.Filter(response='lp'/'hp'/'bp'/'notch')
  dsp.Adc, dsp.Dac, dsp.Demod, dsp.Mixer
  dsp.Speaker, dsp.Mic, dsp.Antenna, dsp.Oscillator
  dsp.Sum, dsp.SumSigma, dsp.SumPoint
  dsp.Arrow, dsp.Line, dsp.Dot, dsp.Arrowhead

LOGIC MODULE (import schemdraw.logic.logic as logic)
------------------------------------------------------
  logic.And, logic.Nand, logic.Or, logic.Nor, logic.Xor, logic.Xnor
  logic.Buf, logic.Not, logic.NotNot
  logic.Tristate, logic.Tgate
  logic.Schmitt, logic.SchmittNot, logic.SchmittAnd, logic.SchmittNand
  logic.Table, logic.Kmap, logic.TimingDiagram

FLOW MODULE (import schemdraw.flow as flow)
---------------------------------------------
  flow.Box, flow.RoundBox, flow.Subroutine, flow.Data, flow.Start
  flow.Decision, flow.Connect, flow.Process
  flow.State, flow.StateEnd
  flow.Arrow, flow.Line, flow.Wire, flow.Arc2, flow.ArcLoop

STYLE SWITCHING
----------------
  elm.style(elm.STYLE_IEEE)  -- US zigzag resistors (default)
  elm.style(elm.STYLE_IEC)   -- European box resistors
"""


@mcp.tool()
def schemdraw_examples() -> str:
    """Return schemdraw v0.22 circuit examples from the official gallery.

    Complete working code examples for common circuit patterns.
    All use v0.22 context manager syntax.
    """
    return """
schemdraw v0.22 Circuit Examples (from official gallery)
=========================================================

ALL EXAMPLES USE v0.22 SYNTAX: with schemdraw.Drawing() as d:

EXAMPLE 1: RC Filter (Simplest)
---------------------------------
import schemdraw
import schemdraw.elements as elm

with schemdraw.Drawing() as d:
    elm.Resistor().label('100K')
    elm.Capacitor().down().label('0.1uF', loc='bottom')
    elm.Line().left()
    elm.Ground()
    elm.SourceV().up().label('10V')

EXAMPLE 2: Inverting Op-Amp
-----------------------------
with schemdraw.Drawing() as d:
    op = elm.Opamp(leads=True)
    elm.Line().down(d.unit/4).at(op.in2)
    elm.Ground(lead=False)
    elm.Resistor().at(op.in1).left().idot().label('$R_{in}$', loc='bot')
    elm.Line().up(d.unit/2).at(op.in1)
    elm.Resistor().tox(op.out).label('$R_f$')
    elm.Line().toy(op.out).dot()
    elm.Line().right(d.unit/4).at(op.out).label('$v_o$', 'right')

EXAMPLE 3: Non-Inverting Op-Amp
---------------------------------
with schemdraw.Drawing() as d:
    op = elm.Opamp(leads=True)
    elm.Line().at(op.out).length(.75)
    elm.Line().up().at(op.in1).length(1.5).dot()
    with d.hold():
        elm.Resistor().left().label('$R_1$')
        elm.Ground()
    elm.Resistor().tox(op.out).label('$R_f$')
    elm.Line().toy(op.out).dot()
    elm.Resistor().left().at(op.in2).label('$R_2$')
    elm.SourceV().down().reverse().label('$v_{in}$')
    elm.Line().right().dot()
    elm.Resistor().up().label('$R_3$').hold()
    elm.Line().tox(op.out)

EXAMPLE 4: RLC with Current Loop Labels
------------------------------------------
with schemdraw.Drawing() as d:
    d.config(unit=5)
    V1 = elm.SourceV().label('20V')
    R1 = elm.Resistor().right().label('400$\\Omega$').dot()
    R2 = elm.Resistor().down().label('100$\\Omega$').dot().hold()
    L1 = elm.Line().right()
    I1 = elm.SourceI().down().label('1A')
    L2 = elm.Line().tox(V1.start)
    elm.LoopCurrent([R1,R2,L2,V1], pad=1.25).label('$I_1$')
    elm.LoopCurrent([R1,I1,L2,R2], pad=1.25).label('$I_2$')

EXAMPLE 5: Parallel Components (push/pop)
--------------------------------------------
with schemdraw.Drawing() as d:
    d.config(unit=3)
    I1 = elm.SourceI().up().label('I1')
    elm.Line().right(1.5)
    elm.Dot()
    d.push()
    elm.Inductor2().down().label('L1', loc='left')
    elm.Dot()
    d.pop()
    elm.Line().right(1.5)
    elm.Dot()
    elm.Capacitor().down().label('C1')
    elm.Line().left().to(I1.start)
    elm.Ground()

EXAMPLE 5b: Parallel Components (with d.hold())
--------------------------------------------------
with schemdraw.Drawing() as d:
    d.config(unit=3)
    I1 = elm.SourceI().up().label('I1')
    elm.Line().right(1.5)
    elm.Dot()
    with d.hold():
        elm.Inductor2().down().label('L1', loc='left')
        elm.Dot()
    elm.Line().right(1.5)
    elm.Dot()
    elm.Capacitor().down().label('C1')
    elm.Line().left().to(I1.start)
    elm.Ground()

EXAMPLE 6: Discharging Cap with SPDT Switch
----------------------------------------------
with schemdraw.Drawing() as d:
    V1 = elm.SourceV().label('5V')
    elm.Line().right(d.unit*.75)
    S1 = elm.SwitchSpdt2(action='close').up().anchor('b')
    elm.Line().right(d.unit*.75).at(S1.c)
    elm.Resistor().down().label('100$\\Omega$').label(['+','$v_o$','-'], loc='bot')
    elm.Line().to(V1.start)
    elm.Capacitor().at(S1.a).toy(V1.start).label('1$\\mu$F').dot()

EXAMPLE 7: BJT Power Supply
------------------------------
with schemdraw.Drawing() as d:
    d.config(inches_per_unit=.5, unit=3)
    D = elm.Rectifier()
    elm.Line().left(d.unit*1.5).at(D.N).dot(open=True).idot()
    elm.Line().left(d.unit*1.5).at(D.S).dot(open=True).idot()
    G = elm.Gap().toy(D.N).label(['-', 'AC IN', '+'])
    top = elm.Line().right(d.unit*3).at(D.E).idot()
    Q2 = elm.BjtNpn(circle=True).up().anchor('collector').label('Q2')
    elm.Line().down(d.unit/2).at(Q2.base)
    Q2b = elm.Dot()
    elm.Line().left(d.unit/3)
    Q1 = elm.BjtNpn(circle=True).up().anchor('emitter').label('Q1')
    elm.Line().at(Q1.collector).toy(top.center).dot()

EXAMPLE 8: 5-Transistor OTA (AnalogFet)
------------------------------------------
with schemdraw.Drawing() as d:
    Q1 = elm.AnalogNFet().anchor('source').theta(0).reverse()
    elm.Line().down(0.5)
    elm.Ground()
    elm.Line().left(1).at(Q1.drain)
    Q2 = elm.AnalogNFet().anchor('source').theta(0).reverse()
    elm.Dot().at(Q1.drain)
    elm.Line().right(1)
    Q3 = elm.AnalogNFet().anchor('source').theta(0)
    Q4 = elm.AnalogPFet().anchor('drain').at(Q2.drain).theta(0)
    Q5 = elm.AnalogPFet().anchor('drain').at(Q3.drain).theta(0).reverse()
    elm.Line().right().at(Q4.gate).to(Q5.gate)
    elm.Dot().at((Q4.gate[0]+Q5.gate[0])/2, (Q4.gate[1]+Q5.gate[1])/2)
    elm.Line().down().toy(Q4.drain)
    elm.Line().left().tox(Q4.drain)
    elm.Dot()
    elm.Line().right().at(Q4.source).to(Q5.source)
    elm.Vdd()
    elm.Tag().at(Q2.gate).label('In+').left()
    elm.Tag().at(Q3.gate).label('In-').right()

EXAMPLE 9: Transformer
-------------------------
with schemdraw.Drawing() as d:
    elm.Line().dot()
    T = elm.Transformer(t1=4, t2=8, core=True).label('4:8', loc='top')
    elm.Line().at(T.s1).dot()
    elm.Line().at(T.p2).dot()
    elm.Line().at(T.s2).dot()

EXAMPLE 10: Digital Half Adder
---------------------------------
import schemdraw.logic.logic as logic

with schemdraw.Drawing() as d:
    d.config(unit=0.5)
    S = logic.Xor().label('S', 'right')
    logic.Line().left(d.unit*2).at(S.in1).idot().label('A', 'left')
    B = logic.Line().left().at(S.in2).dot()
    logic.Line().left().label('B', 'left')
    logic.Line().down(d.unit*3).at(S.in1)
    C = logic.And().right().anchor('in1').label('C', 'right')
    logic.Wire('|-').at(B.end).to(C.in2)

EXAMPLE 11: Superheterodyne Receiver (DSP)
---------------------------------------------
import schemdraw.dsp as dsp

with schemdraw.Drawing() as d:
    d.config(fontsize=12)
    dsp.Antenna()
    dsp.Line().right(d.unit/4)
    dsp.Filter(response='bp').fill('thistle').anchor('W').label('RF filter', 'bottom')
    dsp.Line().length(d.unit/4)
    dsp.Amp().fill('lightblue').label('LNA')
    dsp.Line().length(d.unit/3)
    mix = dsp.Mixer().fill('navajowhite').label('Mixer')
    dsp.Line().at(mix.S).down(d.unit/3)
    dsp.Oscillator().right().anchor('N').fill('navajowhite').label('LO', 'right')
    dsp.Line().at(mix.E).right(d.unit/3)
    dsp.Filter(response='bp').fill('thistle').label('IF filter', 'bottom')
    dsp.Line().right(d.unit/4)
    dsp.Amp().fill('lightblue').label('IF amp')
    dsp.Line().length(d.unit/4)
    dsp.Demod().fill('navajowhite').label('Demod', 'bottom')
    dsp.Arrow().right(d.unit/3)

EXAMPLE 12: Reusable Sub-Circuit (ElementDrawing)
----------------------------------------------------
with schemdraw.Drawing(show=False) as d1:
    elm.Resistor()
    with d1.hold():
        elm.Capacitor().down()
        elm.Line().left()

with schemdraw.Drawing() as d2:
    for i in range(3):
        elm.ElementDrawing(d1)

EXAMPLE 13: Custom Compound Element
--------------------------------------
class RC(elm.ElementCompound):
    def setup(self):
        self.r = self.add(elm.Resistor().length(1).scale(.5))
        self.c = self.add(elm.Capacitor().length(1).scale(.5))
        self.elmparams['drop'] = self.c.end

KEY PATTERNS FOR CIRCUIT BUILDING
------------------------------------
1. Source on left, vertical: elm.SourceV().up().label('V1')
2. Series right:  elm.Resistor().right().label('R1')
3. Shunt down:    elm.Capacitor().down().label('C1')
4. Return wire:   elm.Line().left().to(source.start)
5. Ground:        elm.Ground()
6. Branch:        d.push() / d.pop()  OR  with d.hold():
7. Parallel:      elm.Dot() -> d.push() -> branch -> d.pop() -> continue
8. Connect:       elm.Line().to(target.anchor)
9. Extend:        elm.Line().tox(ref) / elm.Line().toy(ref)
10. Output dot:   elm.Dot().label('out', 'right')
"""


@mcp.tool()
def schemdraw_internals() -> str:
    """Return schemdraw v0.22 internal mechanisms and advanced usage.

    Covers: Drawing class API, Element class API, coordinate system,
    drawing stack, transform, segments, custom elements, and backends.
    """
    return """
schemdraw v0.22 Internals & Advanced Usage
=============================================

DRAWING CLASS API
------------------
Drawing(canvas=None, file=None, show=True, transparent=False, dpi=72)

Properties:
  d.here    -- (x,y) current cursor position
  d.theta   -- current direction angle (degrees)

Methods:
  d.add(element) / d += element    -- add element, returns it
  d.push() / d.pop()               -- save/restore cursor (here+theta)
  with d.hold():                   -- context manager push/pop (preferred)
  d.move(dx=0, dy=0)              -- move cursor by delta
  d.move_from(ref, dx=0, dy=0)    -- move relative to reference point
  d.set_anchor(name)               -- define named anchor at current pos
  d.container(cornerradius=.3)     -- context manager to draw box around elements
  d.config(unit, inches_per_unit, fontsize, font, color, lw, ls, fill,
           bgcolor, margin, mathfont)
  d.draw(show=True) -> Figure
  d.save(fname, transparent=True, dpi=72)
  d.get_imagedata(fmt='svg') -> bytes
  d.get_bbox() -> BBox(xmin,ymin,xmax,ymax)
  d.interactive(True)              -- live matplotlib mode
  d.undo()                         -- remove last element

ELEMENT BASE CLASS API (all elements)
--------------------------------------
Direction:  .up(), .down(), .left(), .right(), .theta(angle)
Position:   .at(xy_or_(element,'anchor')), .anchor('name'), .drop('name')
Size:       .scale(f), .scalex(f), .scaley(f), .length(l)
Mirror:     .flip() (vertical), .reverse() (horizontal)
Style:      .color(c), .fill(c), .gradient_fill(c1,c2), .linestyle(ls),
            .linewidth(lw), .style(color,fill,ls,lw), .zorder(z)
Label:      .label(text, loc, ofst, halign, valign, rotate, fontsize,
                   font, color, href, decoration)
Other:      .hold(), .get_bbox()

Element2Term ADDITIONAL API (2-terminal elements)
---------------------------------------------------
  .to(xy)                 -- set endpoint
  .tox(x_or_element)      -- extend to x-coordinate
  .toy(y_or_element)      -- extend to y-coordinate
  .endpoints(start, end)  -- set both endpoints
  .dot(open=False)        -- connection dot at end
  .idot(open=False)       -- connection dot at start
  .shift(fraction)        -- shift body along leads (-1 to 1)
  .up/down/left/right(length=None)  -- direction + optional length

COORDINATE SYSTEM
------------------
  Origin: (0,0) at drawing start
  X: increases rightward
  Y: increases upward
  Default unit: 3.0 (= 1 lead + 1 body + 1 lead for 2-terminal)
  Angles: 0=right, 90=up, 180=left, 270=down

DRAWING STACK MECHANISM (Context Manager)
-------------------------------------------
  with schemdraw.Drawing() as d:
      elm.Resistor()     # auto-added to d (no d.add() needed)
      elm.Capacitor()    # previous element gets added when new one created

  Internally: Drawing.__enter__() pushes to stack, Element.__init__()
  registers with stack, previous unplaced element gets placed first.
  Fluent methods (.up(), .label()) modify element BEFORE placement.

LABEL DETAILS
--------------
  loc values: 'top'/'T', 'bottom'/'bot'/'B', 'left'/'lft'/'L',
              'right'/'rgt'/'R', 'center', or any anchor name
  List labels: ['−', 'V', '+'] → evenly spaced along element edge
  Math text: '$R_f$', '$\\\\Omega$' (use raw strings r'...')
  Auto-rotation: labels stay readable (top/bottom swap at 90-270 deg)

CUSTOM ELEMENTS
-----------------

Method 1: ElementDrawing (wrap a Drawing as reusable element)
  with schemdraw.Drawing(show=False) as sub:
      elm.Resistor()
      elm.Capacitor().down()
  with schemdraw.Drawing() as d:
      elm.ElementDrawing(sub)     # place the sub-circuit

Method 2: ElementCompound (mini-Drawing inside Element)
  class MyComp(elm.ElementCompound):
      def setup(self):
          r = self.add(elm.Resistor())
          c = self.add(elm.Capacitor().at(r.end).down())
          self.anchors['input'] = r.start
          self.anchors['output'] = c.end

Method 3: Custom from Segments (low-level)
  class MyElm(elm.Element):
      def __init__(self, **kwargs):
          super().__init__(**kwargs)
          self.segments.append(Segment([(0,0),(1,0),(1,1)]))
          self.segments.append(SegmentCircle((0.5,0.5), 0.2))
          self.anchors['pin1'] = (0, 0)
          self.anchors['pin2'] = (1, 1)

SEGMENT TYPES
--------------
  Segment(path, color, lw, ls, fill, arrow)
  SegmentCircle(center, radius, ...)
  SegmentArc(center, width, height, theta1, theta2, arrow)
  SegmentText(pos, label, align, fontsize, font)
  SegmentPoly(verts, closed, cornerradius)
  SegmentBezier(points, arrow)
  SegmentPath(*commands)     -- SVG path-like
  Arrow specifiers: '->', '<-', '<->', '-o', '|->'
  Gap sentinel: (math.nan, math.nan) -- pen-up in path

BACKENDS
---------
  schemdraw.use('matplotlib')  -- full raster+vector, interactive
  schemdraw.use('svg')         -- SVG only, no dependencies

THEMES
-------
  schemdraw.theme('default')   -- black on white
  schemdraw.theme('dark')      -- white on black
  Others: solarizedd, solarizedl, onedork, oceans16, monokai,
          gruvboxl, gruvboxd, grade3, chesterish

GLOBAL CONFIG
--------------
  schemdraw.config(unit=3.0, inches_per_unit=0.5, lblofst=0.1,
                   fontsize=14, font='sans-serif', color='black',
                   lw=2, ls='-', fill=None, bgcolor=None, margin=0.1)

STYLE SWITCHING (IEEE vs IEC)
-------------------------------
  elm.style(elm.STYLE_IEEE)   -- US: zigzag resistors (default)
  elm.style(elm.STYLE_IEC)    -- EU: box resistors

CLASS-LEVEL DEFAULTS
---------------------
  elm.Resistor.defaults['color'] = 'blue'
  flow.Box.defaults['fill'] = '#eeffff'

IMPORTANT GOTCHAS
------------------
1. Sources default to theta=90 (vertical) -- unlike passives (theta=0)
2. Ground/Vdd drop=(0,0) -- cursor stays after placing
3. Opamp in1=non-inverting(+), in2=inverting(-)
4. .flip()=vertical mirror, .reverse()=horizontal mirror
5. Context manager auto-adds -- no d.add() needed inside 'with'
6. .tox()/.toy() auto-set direction based on target position
7. Access anchor (R.end) on unplaced element triggers placement
8. Element['anchor'] works same as Element.anchor (dict access)
9. ElementCompound.setup() pauses drawing stack (sub-elements not added to main)
10. Non-interactive: import matplotlib; matplotlib.use('Agg')
"""


if __name__ == "__main__":
    mcp.run()

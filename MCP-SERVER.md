# MCP Server: AI-Assisted Circuit Design

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives LLMs the ability to **search 5,000+ circuits**, **run LTspice simulations**, and **generate schematics** &mdash; all through natural language.

## Setup

Add to your Claude Code or Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "circuit-lab": {
      "command": "python",
      "args": ["mcp-server/server.py"],
      "cwd": "/path/to/circuit-converter"
    }
  }
}
```

### Requirements

- Python 3.9+
- `pip install schemdraw spicelib`
- LTspice installed (for simulation tools)

## Dynamic Tools (8)

### Circuit Search

| Tool | Description | Example |
|------|-------------|---------|
| `search_circuits` | Search 229 textbook circuits by type/keyword | `search_circuits(query="lowpass", circuit_type="filter")` |
| `search_ltspice_examples` | Search 100 LTspice Educational examples by category | `search_ltspice_examples(category="oscillator")` |
| `search_ltspice_applications` | Search 3,999 ADI application circuits | `search_ltspice_applications(query="LT3080", category="power")` |

Categories for `search_ltspice_applications`: `op-amp` (1,214), `power` (2,500), `analog` (754), `LED-driver` (246), `switch` (142), `DAC` (64), `isolator` (41), `ADC` (12), `comparator` (5), `instrumentation-amp` (14).

### Simulation

| Tool | Description | Example |
|------|-------------|---------|
| `run_simulation` | Run LTspice from netlist or file | `run_simulation(netlist="V1 in 0 AC 1\nR1 in out 1k\n...")` |
| `read_simulation_results` | Read `.raw` waveform data with statistics | `read_simulation_results(raw_file="ltspice_applications/AD8221.raw")` |
| `parametric_sweep` | Sweep a component value across multiple runs | `parametric_sweep(netlist, "R1", "1k,10k,100k", "V(out)")` |

### Conversion

| Tool | Description | Example |
|------|-------------|---------|
| `netlist_to_schemdraw` | Generate schemdraw Python script from netlist | `netlist_to_schemdraw(netlist, "my_circuit")` |
| `schemdraw_to_netlist` | Extract SPICE netlist from schemdraw script | `schemdraw_to_netlist(script, "My Circuit")` |

## Static Tools (10)

API reference and best practices for:

| Tool | Content |
|------|---------|
| `schemdraw_tips` | schemdraw v0.22 API reference, element types, anchors, gotchas |
| `schemdraw_elements_quick_ref` | Element constructors and parameters |
| `schemdraw_placement_patterns` | Series, parallel, push/pop, anchors |
| `schemdraw_style_guide` | Labels, colors, fonts, line styles |
| `ltspice_format_reference` | `.asc` file format specification |
| `ltspice_netlist_reference` | SPICE netlist syntax and directives |
| `ltspice_simulation_guide` | Simulation types (.tran, .ac, .dc, .noise) |
| `ltspice_tips` | Common pitfalls and best practices |
| `spice_quick_ref` | SPICE component syntax reference |
| `workflow_guide` | End-to-end design workflow |

## LLM Workflow

With these tools, an LLM can autonomously execute a complete circuit design cycle:

### 1. Search &rarr; Design &rarr; Simulate &rarr; Optimize &rarr; Draw

```
User: "Design a 1kHz lowpass filter with -40dB/decade rolloff"

LLM:
  1. search_ltspice_applications("lowpass filter")     → find reference circuits
  2. Write netlist: V1, R1, C1, C2 (2nd order)         → design the circuit
  3. run_simulation(netlist)                            → verify frequency response
  4. read_simulation_results(raw_file, traces="V(out)") → check cutoff frequency
  5. parametric_sweep(netlist, "C1", "1n,10n,100n,1u")  → optimize component values
  6. netlist_to_schemdraw(final_netlist)                → generate PDF schematic
```

### 2. Reverse Engineering

```
User: "What does this LTspice circuit do?"

LLM:
  1. Read the .asc file
  2. Extract netlist (asc_to_cir)
  3. run_simulation → read_simulation_results
  4. Analyze waveforms and explain the circuit behavior
  5. netlist_to_schemdraw → clean schematic for documentation
```

### 3. Parametric Optimization

```
User: "Find the best R1 value for maximum gain without clipping"

LLM:
  1. parametric_sweep(netlist, "R1", "100,1k,10k,100k", "V(out)")
  2. Compare peak-to-peak values across sweep
  3. Recommend optimal value with reasoning
```

## Circuit Database

| Collection | Circuits | Sim Pass Rate |
|------------|----------|---------------|
| ADI Applications | 3,999 | 96.9% |
| LTspice Educational | 100 | 93% |
| GitHub repositories | 720 | 86.9% |
| Textbook (local only) | 229 | - |
| **Total** | **~5,000** | |

All circuits are searchable by name, category, and free text. Simulation results (`.raw` files) are available for immediate analysis.

## Architecture

```
mcp-server/server.py
  ├── FastMCP framework
  ├── Dynamic tools (8)
  │   ├── 3 search tools → tests/db/ catalogs (JSON)
  │   ├── 3 simulation tools → LTspice CLI (-b flag)
  │   └── 2 conversion tools → src/ converters
  └── Static tools (10) → inline reference text
```

The server auto-detects LTspice installation on Windows (ADI/LTspice or LTC/LTspiceXVII).

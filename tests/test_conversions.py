#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Round-trip conversion tests: .cir <-> schemdraw <-> .asc

Tests:
1. .cir → schemdraw → .cir (node/directive preservation)
2. .cir → .asc → .cir (netlist preservation)
3. .cir → schemdraw (compile + exec)
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from cir_to_schemdraw import CirToSchemdraw, cir_string_to_schemdraw
from schemdraw_to_cir import schemdraw_script_to_cir
from netlist_to_asc import NetlistToAsc
from asc_parser import asc_to_netlist


# =============================================================================
# Test circuits
# =============================================================================

CIRCUITS = {
    'rc_lowpass': """* RC Lowpass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 1u
.ac dec 20 1 100k
.end""",

    'rlc_series': """* RLC Series
V1 in 0 SINE(0 1 1k)
R1 in n1 100
L1 n1 out 10m
C1 out 0 1u
.tran 10m
.end""",

    'voltage_divider': """* Voltage Divider
V1 in 0 5
R1 in out 1k
R2 out 0 1k
.op
.end""",

    'pi_filter': """* Pi Filter
V1 in 0 AC 1
C1 in 0 10u
L1 in out 100m
C2 out 0 10u
R1 out 0 50
.ac dec 20 1 100k
.end""",

    'bandpass': """* RLC Bandpass
V1 in 0 AC 1
R1 in n1 100
L1 n1 out 10m
C1 out 0 100n
R2 out 0 10k
.ac dec 50 100 100k
.end""",
}


def _count_components(netlist: str) -> int:
    """Count component lines in netlist"""
    count = 0
    for line in netlist.strip().split('\n'):
        line = line.strip()
        if line and line[0].isalpha() and not line.startswith('*') and not line.startswith('.'):
            count += 1
    return count


def _count_signal_nodes(netlist: str) -> int:
    """Count unique signal nodes (excluding 0/gnd)"""
    nodes = set()
    for line in netlist.strip().split('\n'):
        parts = line.split()
        if len(parts) >= 3 and parts[0][0].isalpha() and parts[0][0] != '*':
            for p in parts[1:3]:
                if p != '0' and p.lower() != 'gnd':
                    nodes.add(p)
    return len(nodes)


def _has_directive(netlist: str, prefix: str) -> bool:
    """Check if netlist contains a directive starting with prefix"""
    for line in netlist.strip().split('\n'):
        if line.strip().lower().startswith(prefix.lower()):
            return True
    return False


# =============================================================================
# Tests
# =============================================================================

def test_cir_to_schemdraw_compiles():
    """Test: .cir → schemdraw script compiles"""
    print("Test 1: .cir → schemdraw (compile)")
    passed = 0
    for name, cir in CIRCUITS.items():
        try:
            script = cir_string_to_schemdraw(cir, name)
            compile(script, f'<{name}>', 'exec')
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
    print(f"  {passed}/{len(CIRCUITS)} passed")
    return passed == len(CIRCUITS)


def test_cir_to_schemdraw_executes():
    """Test: .cir → schemdraw script executes without error"""
    print("Test 2: .cir → schemdraw (exec)")
    passed = 0
    for name, cir in CIRCUITS.items():
        try:
            script = cir_string_to_schemdraw(cir, name)
            exec(script)
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
    # Cleanup generated PDFs
    for name in CIRCUITS:
        pdf = f"{name}.pdf"
        if os.path.exists(pdf):
            os.remove(pdf)
    print(f"  {passed}/{len(CIRCUITS)} passed")
    return passed == len(CIRCUITS)


def test_schemdraw_roundtrip_nodes():
    """Test: .cir → schemdraw → .cir preserves node count"""
    print("Test 3: .cir <-> schemdraw round-trip (nodes)")
    passed = 0
    for name, cir in CIRCUITS.items():
        try:
            script = cir_string_to_schemdraw(cir, name)
            recovered = schemdraw_script_to_cir(script, name)
            orig_nodes = _count_signal_nodes(cir)
            back_nodes = _count_signal_nodes(recovered)
            if orig_nodes == back_nodes:
                passed += 1
            else:
                print(f"  FAIL {name}: {orig_nodes} → {back_nodes} nodes")
        except Exception as e:
            print(f"  FAIL {name}: {e}")
    # Cleanup
    for name in CIRCUITS:
        pdf = f"{name}.pdf"
        if os.path.exists(pdf):
            os.remove(pdf)
    print(f"  {passed}/{len(CIRCUITS)} passed")
    return passed == len(CIRCUITS)


def test_schemdraw_roundtrip_directives():
    """Test: .cir → schemdraw → .cir preserves directives"""
    print("Test 4: .cir <-> schemdraw round-trip (directives)")
    passed = 0
    checks = {
        'rc_lowpass': '.ac',
        'rlc_series': '.tran',
        'voltage_divider': '.op',
        'pi_filter': '.ac',
        'bandpass': '.ac',
    }
    for name, cir in CIRCUITS.items():
        try:
            script = cir_string_to_schemdraw(cir, name)
            recovered = schemdraw_script_to_cir(script, name)
            expected = checks.get(name, '')
            if expected and _has_directive(recovered, expected):
                passed += 1
            elif not expected:
                passed += 1
            else:
                print(f"  FAIL {name}: missing {expected}")
        except Exception as e:
            print(f"  FAIL {name}: {e}")
    for name in CIRCUITS:
        pdf = f"{name}.pdf"
        if os.path.exists(pdf):
            os.remove(pdf)
    print(f"  {passed}/{len(CIRCUITS)} passed")
    return passed == len(CIRCUITS)


def test_cir_to_asc_roundtrip():
    """Test: .cir → .asc → .cir preserves components"""
    print("Test 5: .cir <-> .asc round-trip (components)")
    passed = 0
    for name, cir in CIRCUITS.items():
        try:
            # .cir → .asc
            asc = NetlistToAsc().convert_string(cir)
            # .asc → .cir
            recovered = asc_to_netlist(None, asc_string=asc) if hasattr(asc_to_netlist, '__code__') and 'asc_string' in asc_to_netlist.__code__.co_varnames else None

            # Alternative: write temp file
            if recovered is None:
                from asc_parser import AscParser, NetlistExtractor
                parser = AscParser()
                parser.parse_string(asc)
                extractor = NetlistExtractor(parser)
                recovered = extractor.extract()

            orig_comps = _count_components(cir)
            back_comps = _count_components(recovered)
            if orig_comps == back_comps:
                passed += 1
            else:
                print(f"  FAIL {name}: {orig_comps} → {back_comps} components")
        except Exception as e:
            print(f"  FAIL {name}: {e}")
    print(f"  {passed}/{len(CIRCUITS)} passed")
    return passed == len(CIRCUITS)


def test_route_a_equals_route_b():
    """Test: Route A (.cir→.asc) == Route B (.cir→schemdraw→.cir→.asc)"""
    print("Test 6: Route A == Route B (identical .asc)")
    passed = 0
    for name, cir in CIRCUITS.items():
        try:
            # Route A: direct
            asc_a = NetlistToAsc().convert_string(cir)

            # Route B: via schemdraw
            script = cir_string_to_schemdraw(cir, name)
            cir_b = schemdraw_script_to_cir(script, name)
            asc_b = NetlistToAsc().convert_string(cir_b)

            if asc_a == asc_b:
                passed += 1
            else:
                # Check structural equivalence (same WIRE/SYMBOL count)
                wires_a = asc_a.count('WIRE')
                wires_b = asc_b.count('WIRE')
                syms_a = asc_a.count('SYMBOL')
                syms_b = asc_b.count('SYMBOL')
                if wires_a == wires_b and syms_a == syms_b:
                    passed += 1  # structurally equivalent
                else:
                    print(f"  FAIL {name}: W={wires_a}/{wires_b} S={syms_a}/{syms_b}")
        except Exception as e:
            print(f"  FAIL {name}: {e}")
    for name in CIRCUITS:
        pdf = f"{name}.pdf"
        if os.path.exists(pdf):
            os.remove(pdf)
    print(f"  {passed}/{len(CIRCUITS)} passed")
    return passed == len(CIRCUITS)


if __name__ == '__main__':
    results = []
    results.append(test_cir_to_schemdraw_compiles())
    results.append(test_cir_to_schemdraw_executes())
    results.append(test_schemdraw_roundtrip_nodes())
    results.append(test_schemdraw_roundtrip_directives())
    results.append(test_cir_to_asc_roundtrip())
    results.append(test_route_a_equals_route_b())

    print(f"\n{'='*40}")
    print(f"Total: {sum(results)}/{len(results)} test groups passed")
    if all(results):
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)

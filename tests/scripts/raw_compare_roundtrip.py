#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
.raw 波形比較ラウンドトリップテスト

Flow:
  1. 元 .asc → LTspice sim → original.raw
  2. .asc → .cir → .asc (round-trip)
  3. round-trip .asc → LTspice sim → roundtrip.raw
  4. 両 .raw をトレースごとに数値比較

Usage:
    python raw_compare_roundtrip.py                  # passive-only (Educational)
    python raw_compare_roundtrip.py --all-edu         # 全 Educational
    python raw_compare_roundtrip.py --file X.asc      # 単体
"""

import sys
import os
import json
import argparse
import shutil
import subprocess
import time
import numpy as np
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))
from asc_parser import asc_to_netlist, asc_to_cir, classify_asc
from netlist_to_asc import NetlistToAsc

EXAMPLES_DIR = Path(__file__).parent.parent.parent / 'examples' / 'ltspice_bundled'
RESULTS_DIR = Path(__file__).parent.parent / 'db' / 'raw_compare'
VERIFIED_DIR = Path(__file__).parent.parent / 'db' / 'verified'
FAILED_DIR = Path(__file__).parent.parent / 'db' / 'failed'

# --- LTspice runner (from batch_roundtrip_github.py) ---

def find_ltspice():
    candidates = [
        Path(os.environ.get('PROGRAMFILES', '')) / 'ADI' / 'LTspice' / 'LTspice.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'ADI' / 'LTspice' / 'LTspice.exe',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _kill_proc_tree(pid):
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def run_ltspice(ltspice_exe, file_path, timeout=60):
    file_path = Path(file_path).resolve()
    log_path = file_path.with_suffix('.log')
    raw_path = file_path.with_suffix('.raw')

    for p in [log_path, raw_path]:
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        proc = subprocess.Popen(
            [ltspice_exe, '-b', str(file_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(file_path.parent),
            startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_proc_tree(proc.pid)
        return None, 'timeout'
    except Exception as e:
        return None, f'exec error: {e}'

    if raw_path.exists():
        return str(raw_path), 'OK'
    op_raw = file_path.with_suffix('.op.raw')
    if op_raw.exists():
        return str(op_raw), 'OK (op)'
    return None, 'no .raw output'


# --- .raw comparison ---

def compare_raw_files(raw_orig, raw_rt, rtol=1e-3, atol=1e-6):
    """Compare two .raw files trace by trace. Returns dict with results."""
    from spicelib import RawRead

    r1 = RawRead(raw_orig, verbose=False)
    r2 = RawRead(raw_rt, verbose=False)

    traces1 = set(r1.get_trace_names())
    traces2 = set(r2.get_trace_names())

    common = sorted(traces1 & traces2)
    only_orig = sorted(traces1 - traces2)
    only_rt = sorted(traces2 - traces1)

    trace_results = {}
    all_pass = True

    for name in common:
        d1 = np.abs(r1.get_trace(name).get_wave())
        d2 = np.abs(r2.get_trace(name).get_wave())

        # Length mismatch: resample shorter to longer via interp
        if len(d1) != len(d2):
            max_len = max(len(d1), len(d2))
            if len(d1) < max_len:
                d1 = np.interp(np.linspace(0, 1, max_len),
                               np.linspace(0, 1, len(d1)), d1)
            else:
                d2 = np.interp(np.linspace(0, 1, max_len),
                               np.linspace(0, 1, len(d2)), d2)

        match = np.allclose(d1, d2, rtol=rtol, atol=atol)
        if not match:
            # Compute max relative error for diagnostics
            denom = np.maximum(np.abs(d1), atol)
            max_rel_err = float(np.max(np.abs(d1 - d2) / denom))
        else:
            max_rel_err = 0.0

        trace_results[name] = {
            'match': match,
            'max_rel_err': max_rel_err,
            'n_points': len(d1),
        }
        if not match:
            all_pass = False

    return {
        'all_pass': all_pass,
        'common_traces': len(common),
        'only_in_original': only_orig,
        'only_in_roundtrip': only_rt,
        'traces': trace_results,
    }


# --- Round-trip pipeline ---

def run_one(asc_path, ltspice_exe, work_dir):
    """Run full round-trip comparison for one .asc file."""
    asc_path = Path(asc_path).resolve()
    name = asc_path.stem
    case_dir = work_dir / name
    case_dir.mkdir(parents=True, exist_ok=True)

    result = {'name': name, 'source': str(asc_path)}

    # Step 1: Simulate original
    orig_copy = case_dir / f'{name}_orig.asc'
    shutil.copy2(asc_path, orig_copy)
    # Copy any .lib/.sub/.asy from source dir
    for ext in ('*.lib', '*.sub', '*.asy', '*.wav', '*.txt'):
        for f in asc_path.parent.glob(ext):
            dst = case_dir / f.name
            if not dst.exists():
                shutil.copy2(f, dst)

    raw_orig, msg1 = run_ltspice(ltspice_exe, orig_copy)
    if not raw_orig:
        result['status'] = 'orig_sim_fail'
        result['error'] = msg1
        return result

    # Step 2: .asc → .cir
    try:
        cir_text = asc_to_cir(str(asc_path))
    except Exception as e:
        result['status'] = 'asc_to_cir_fail'
        result['error'] = str(e)
        return result

    cir_path = case_dir / f'{name}.cir'
    cir_path.write_text(cir_text, encoding='utf-8')

    # Step 3: .cir → .asc (round-trip)
    try:
        converter = NetlistToAsc()
        rt_asc_text = converter.convert_string(cir_text)
    except Exception as e:
        result['status'] = 'cir_to_asc_fail'
        result['error'] = str(e)
        return result

    rt_asc_path = case_dir / f'{name}_rt.asc'
    rt_asc_path.write_text(rt_asc_text, encoding='utf-8')

    # Step 3.5: Check for overlapping components (causes LTspice GUI dialog)
    sym_positions = []
    for line in rt_asc_text.split('\n'):
        if line.startswith('SYMBOL '):
            parts = line.split()
            if len(parts) >= 4:
                sym_positions.append((parts[1], int(parts[2]), int(parts[3])))
    if len(sym_positions) != len(set(sym_positions)):
        from collections import Counter
        dupes = [k for k, v in Counter(sym_positions).items() if v > 1]
        result['status'] = 'overlap'
        result['error'] = f'Duplicate components at: {dupes[:3]}'
        return result

    # Step 4: Simulate round-trip
    raw_rt, msg2 = run_ltspice(ltspice_exe, rt_asc_path)
    if not raw_rt:
        result['status'] = 'rt_sim_fail'
        result['error'] = msg2
        return result

    # Step 5: Compare .raw files
    try:
        cmp = compare_raw_files(raw_orig, raw_rt)
        result['status'] = 'pass' if cmp['all_pass'] else 'waveform_mismatch'
        result['comparison'] = cmp
    except Exception as e:
        result['status'] = 'compare_fail'
        result['error'] = str(e)

    return result


def save_to_verified(result, case_dir, asc_path):
    """PASSした回路を verified DB に保存

    3点セット: original.asc, converted.cir, original.raw
    + meta.json（回路メタデータ）
    """
    name = result['name']
    dest = VERIFIED_DIR / name
    dest.mkdir(parents=True, exist_ok=True)

    # 3点セットをコピー
    src_asc = case_dir / f'{name}_orig.asc'
    src_cir = case_dir / f'{name}.cir'
    src_raw = case_dir / f'{name}_orig.raw'
    if not src_raw.exists():
        src_raw = case_dir / f'{name}_orig.op.raw'

    for src, dst_name in [(src_asc, 'original.asc'),
                          (src_cir, 'converted.cir'),
                          (src_raw, 'original.raw')]:
        if src.exists():
            shutil.copy2(src, dest / dst_name)

    # 分類情報を取得
    info = classify_asc(str(asc_path))

    # メタデータ
    cmp = result.get('comparison', {})
    meta = {
        'name': name,
        'source': str(asc_path),
        'source_dir': asc_path.parent.name,
        'verified_at': datetime.now().isoformat(),
        'symbol_types': info.get('symbol_types', []),
        'num_symbols': info.get('num_symbols', 0),
        'passive_only': info.get('passive_only', False),
        'common_traces': cmp.get('common_traces', 0),
        'trace_names': sorted(cmp.get('traces', {}).keys()),
        'rtol': 1e-3,
        'atol': 1e-6,
    }

    (dest / 'meta.json').write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')

    return dest


def move_to_failed(name, result, work_dir, asc_path):
    """失敗した回路を failed/ に移動（デバッグ用）"""
    case_dir = work_dir / name
    if not case_dir.exists():
        return

    dest = FAILED_DIR / name
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(case_dir), str(dest))

    # 失敗理由を保存
    info = classify_asc(str(asc_path))
    fail_meta = {
        'name': name,
        'source': str(asc_path),
        'status': result['status'],
        'error': result.get('error', ''),
        'tested_at': datetime.now().isoformat(),
        'symbol_types': info.get('symbol_types', []),
        'num_symbols': info.get('num_symbols', 0),
        'passive_only': info.get('passive_only', False),
    }
    if 'comparison' in result:
        cmp = result['comparison']
        failed_traces = [k for k, v in cmp.get('traces', {}).items() if not v['match']]
        fail_meta['failed_traces'] = failed_traces[:10]
        fail_meta['common_traces'] = cmp.get('common_traces', 0)

    (dest / 'fail_info.json').write_text(
        json.dumps(fail_meta, indent=2, ensure_ascii=False), encoding='utf-8')


def rebuild_catalog():
    """verified/ 内の全 meta.json から catalog.json を再構築"""
    if not VERIFIED_DIR.exists():
        return

    catalog = []
    for meta_path in sorted(VERIFIED_DIR.glob('*/meta.json')):
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            catalog.append({
                'name': meta['name'],
                'source_dir': meta.get('source_dir', ''),
                'passive_only': meta.get('passive_only', False),
                'num_symbols': meta.get('num_symbols', 0),
                'common_traces': meta.get('common_traces', 0),
                'verified_at': meta.get('verified_at', ''),
            })
        except Exception:
            pass

    catalog_path = VERIFIED_DIR / 'catalog.json'
    catalog_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'\nCatalog: {len(catalog)} verified circuits -> {catalog_path}')


TWO_TERMINAL_TYPES = {'res', 'cap', 'ind', 'ind2', 'polcap', 'voltage',
                       'current', 'diode', 'schottky', 'zener', 'led',
                       'battery'}


def collect_targets(args):
    """Collect .asc files based on CLI args."""
    if args.file:
        return [Path(args.file)]

    targets = []

    # Educational
    edu_dir = EXAMPLES_DIR / 'Educational'
    if edu_dir.exists():
        for f in sorted(edu_dir.glob('*.asc')):
            try:
                info = classify_asc(str(f))
                types = set(info.get('symbol_types', []))
                if info.get('num_symbols', 0) > 0 and types:
                    if args.all_edu:
                        targets.append(f)
                    elif types.issubset(TWO_TERMINAL_TYPES):
                        targets.append(f)
            except Exception:
                pass

    # GitHub repos (2-terminal only)
    if args.github or args.all:
        repos_dir = Path(__file__).parent.parent.parent / 'examples' / 'github_repos'
        if repos_dir.exists():
            for f in sorted(repos_dir.rglob('*.asc')):
                try:
                    info = classify_asc(str(f))
                    types = set(info.get('symbol_types', []))
                    if (info.get('num_symbols', 0) > 0 and types
                            and types.issubset(TWO_TERMINAL_TYPES)):
                        targets.append(f)
                except Exception:
                    pass

    # Batch limit
    if args.batch and args.batch < len(targets):
        targets = targets[:args.batch]

    return targets


def main():
    parser = argparse.ArgumentParser(description='.raw比較ラウンドトリップ')
    parser.add_argument('--file', help='単体テスト対象 .asc')
    parser.add_argument('--all-edu', action='store_true', help='全Educational回路')
    parser.add_argument('--github', action='store_true', help='GitHub repos回路も含む')
    parser.add_argument('--all', action='store_true', help='全ソース')
    parser.add_argument('--batch', type=int, help='最大テスト件数')
    parser.add_argument('--rtol', type=float, default=1e-3, help='相対許容差')
    parser.add_argument('--atol', type=float, default=1e-6, help='絶対許容差')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    ltspice_exe = find_ltspice()
    if not ltspice_exe:
        print('ERROR: LTspice not found')
        sys.exit(1)
    print(f'LTspice: {ltspice_exe}')

    targets = collect_targets(args)
    print(f'Targets: {len(targets)} circuits\n')

    if args.dry_run:
        for f in targets:
            print(f'  {f.name}')
        return

    work_dir = RESULTS_DIR
    work_dir.mkdir(parents=True, exist_ok=True)

    results = []
    counts = {'pass': 0, 'fail': 0, 'skip': 0}

    for i, asc_path in enumerate(targets, 1):
        name = asc_path.stem
        print(f'[{i}/{len(targets)}] {name}: ', end='', flush=True)

        r = run_one(asc_path, ltspice_exe, work_dir)
        results.append(r)

        if r['status'] == 'pass':
            counts['pass'] += 1
            cmp = r['comparison']
            print(f"PASS ({cmp['common_traces']} traces)")
            # Verified DB に保存
            case_dir = work_dir / name
            save_to_verified(r, case_dir, asc_path)
        elif r['status'] == 'waveform_mismatch':
            counts['fail'] += 1
            cmp = r['comparison']
            failed = [k for k, v in cmp['traces'].items() if not v['match']]
            print(f"MISMATCH: {', '.join(failed[:3])}")
            move_to_failed(name, r, work_dir, asc_path)
        else:
            counts['skip'] += 1
            print(f"{r['status']}: {r.get('error', '?')}")
            move_to_failed(name, r, work_dir, asc_path)

    # Summary
    total = counts['pass'] + counts['fail']
    print(f'\n{"="*60}')
    print(f'RAW COMPARE RESULTS: {counts["pass"]}/{total} pass'
          f' ({counts["pass"]/total*100:.1f}%)' if total else 'No comparable results')
    if counts['skip']:
        print(f'Skipped (sim/convert fail): {counts["skip"]}')
    print(f'{"="*60}')

    # Verified catalog 再構築
    if counts['pass'] > 0:
        rebuild_catalog()

    # Save report
    report_path = RESULTS_DIR / 'report.json'
    report = {
        'timestamp': datetime.now().isoformat(),
        'counts': counts,
        'results': results,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                           encoding='utf-8')
    print(f'\nReport: {report_path}')


if __name__ == '__main__':
    main()

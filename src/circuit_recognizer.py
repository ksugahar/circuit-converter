#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回路図認識モジュール

Claude CLI (サブスク認証) を利用して:
1. 画像が回路図かどうかを判定
2. 回路図からSPICEネットリストを抽出
3. 抽出したネットリストを .cir / .asc / .py に変換

Claude Codeのサブスクリプションで動作（APIキー不要）。
"""

import subprocess
import json
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

# 自モジュールのインポートパス
sys.path.insert(0, str(Path(__file__).parent))


def _call_claude(prompt: str, model: str = 'sonnet',
                 allowed_tools: str = 'Read',
                 timeout: int = 120) -> str:
    """Claude CLIを呼び出す"""
    env = os.environ.copy()
    env.pop('CLAUDECODE', None)  # ネスト防止を回避

    # Windows: npm globalのclaude CLIのフルパスを使う
    claude_cmd = os.path.expandvars(
        r'%APPDATA%\npm\claude.cmd')
    if not os.path.exists(claude_cmd):
        claude_cmd = 'claude'  # PATHから探す

    # ファイルパスがプロンプト内にあれば、そのディレクトリも追加
    import re
    paths_in_prompt = re.findall(r'[A-Za-z]:[/\\][\w/\\.\\-]+', prompt)
    add_dirs = [str(Path(os.getcwd()).resolve())]
    for p in paths_in_prompt:
        parent = str(Path(p).parent.resolve())
        if parent not in add_dirs:
            add_dirs.append(parent)

    cmd = [claude_cmd, '-p', prompt, '--model', model,
           '--allowedTools', 'Read Glob',
           '--dangerously-skip-permissions']
    for d in add_dirs:
        cmd.extend(['--add-dir', d])

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=env, encoding='utf-8')

    if result.returncode != 0:
        raise RuntimeError(f'Claude CLI error: {result.stderr}')

    return result.stdout.strip()


# =============================================================================
# 1. 回路図判定
# =============================================================================

def is_circuit_diagram(image_path: str, model: str = 'haiku') -> Tuple[bool, str]:
    """画像が回路図かどうかを判定する

    Returns:
        (is_circuit: bool, description: str)
    """
    prompt = (f"Read the file at {image_path} and determine if this image "
              f"is an electrical/electronic circuit diagram (schematic). "
              f"Start your answer with YES or NO, then briefly describe what you see.")

    try:
        response = _call_claude(prompt, model=model)
        # **YES** や *YES* などのマークダウン装飾を除去
        first_word = response.strip().split()[0].upper()
        first_word = first_word.strip('*_.,!:;')
        is_circuit = first_word == 'YES'
        description = response.strip()
        return (is_circuit, description)
    except RuntimeError as e:
        return (False, f'Error: {e}')


# =============================================================================
# 2. 回路図からネットリスト抽出
# =============================================================================

def classify_and_extract(image_path: str,
                          classify_model: str = 'haiku',
                          extract_model: str = 'sonnet') -> Tuple[bool, str, str]:
    """回路図判定（haiku）+ ネットリスト抽出（sonnet）

    Returns:
        (is_circuit: bool, description: str, netlist: str)
    """
    # Step 1: 判定（haiku = 安い・速い）
    is_circuit, description = is_circuit_diagram(image_path,
                                                  model=classify_model)

    if not is_circuit:
        return (False, description, '')

    # Step 2: ネットリスト抽出（sonnet = 賢い）
    # 「ネットリストだけ出力」を強制するため、system promptで役割を固定
    extract_prompt = (
        f"Read the file at {image_path}. "
        f"This circuit diagram must be converted to a SPICE netlist. "
        f"Reply with ONLY the netlist lines. "
        f"Format: component_name node+ node- value. "
        f"First line starts with *. Last line is .end. "
        f"Example: * Title\\nR1 in out 1k\\nC1 out 0 1u\\n.end"
    )

    response = _call_claude(extract_prompt, model=extract_model, timeout=180)

    # ネットリスト抽出: コードブロックや余計なテキストを除去
    netlist = _extract_netlist_text(response)

    return (is_circuit, description, netlist)


def _extract_netlist_text(response: str) -> str:
    """応答テキストからSPICEネットリスト部分を抽出"""
    lines = response.strip().split('\n')
    netlist_lines = []
    in_netlist = False
    in_code_block = False

    for line in lines:
        clean = line.strip()

        # コードブロックの開始/終了
        if clean.startswith('```'):
            in_code_block = not in_code_block
            continue

        # ネットリスト開始: * で始まる行
        if clean.startswith('*') and not in_netlist:
            in_netlist = True

        # コンポーネント行（R, C, L, V, I, D, Q, M, .で始まる）
        if not in_netlist and clean and clean[0] in 'RCLVIDQMrcldiqm.':
            in_netlist = True

        if in_netlist:
            netlist_lines.append(clean)
            if clean.lower() == '.end':
                break

    result = '\n'.join(netlist_lines)

    # .end がなければ追加
    if not result.lower().endswith('.end'):
        result += '\n.end'

    return result


def extract_netlist_from_image(image_path: str,
                                model: str = 'sonnet') -> str:
    """回路図画像からSPICEネットリストを抽出する（classify_and_extractの簡易版）"""
    is_circuit, desc, netlist = classify_and_extract(image_path, model)
    if not is_circuit:
        raise ValueError(f'Not a circuit diagram: {desc}')
    return netlist


# =============================================================================
# 3. 画像→全フォーマット変換パイプライン
# =============================================================================

def convert_image_to_all(image_path: str, output_dir: str = None,
                          model: str = 'sonnet') -> dict:
    """回路図画像から .cir, .asc, .py を全て生成する

    Returns:
        {'cir': str, 'asc': str, 'py': str, 'is_circuit': bool, ...}
    """
    image_path = str(Path(image_path).resolve())
    name = Path(image_path).stem

    if output_dir is None:
        output_dir = str(Path(image_path).parent)

    result = {
        'source': image_path,
        'name': name,
        'is_circuit': False,
        'description': '',
        'cir': '',
        'asc': '',
        'py': '',
        'error': '',
    }

    # Step 1: 回路図判定
    is_circuit, desc = is_circuit_diagram(image_path, model='haiku')
    result['is_circuit'] = is_circuit
    result['description'] = desc

    if not is_circuit:
        result['error'] = 'Not a circuit diagram'
        return result

    # Step 2: ネットリスト抽出
    try:
        netlist = extract_netlist_from_image(image_path, model=model)
        result['cir'] = netlist
    except Exception as e:
        result['error'] = f'Netlist extraction failed: {e}'
        return result

    # Step 3: .cir 保存
    cir_path = Path(output_dir) / f'{name}.cir'
    cir_path.write_text(netlist, encoding='utf-8')

    # Step 4: .cir → .asc 変換
    try:
        from netlist_to_asc import NetlistToAsc
        converter = NetlistToAsc()
        asc_content = converter.convert_string(netlist)
        result['asc'] = asc_content

        asc_path = Path(output_dir) / f'{name}.asc'
        asc_path.write_text(asc_content, encoding='utf-8')
    except Exception as e:
        result['error'] = f'ASC conversion failed: {e}'

    # Step 5: .cir → .py 変換 (ASC経由)
    try:
        if result['asc']:
            from asc_to_schemdraw import AscToSchemdraw
            asc_path = Path(output_dir) / f'{name}.asc'
            sd_converter = AscToSchemdraw()
            py_content = sd_converter.convert_file(
                str(asc_path),
                str(Path(output_dir) / f'{name}.gen.py'))
            result['py'] = py_content
    except Exception as e:
        result['error'] = f'Schemdraw conversion failed: {e}'

    return result


# =============================================================================
# テスト
# =============================================================================

if __name__ == '__main__':
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        # テスト: 手元のPDFを判定
        image_path = str(Path(__file__).parent.parent /
            'examples/00_converter/01_rc_lowpass/test_rc_lowpass.pdf')

    print(f'=== Circuit Recognition Test ===')
    print(f'Input: {image_path}')
    print()

    # 判定 + 抽出を1回で
    print('Classifying and extracting...')
    is_circuit, desc, netlist = classify_and_extract(image_path)
    print(f'  Is circuit: {is_circuit}')
    print(f'  Description: {desc}')
    print()

    if is_circuit and netlist:
        print('Extracted netlist:')
        for line in netlist.split('\n'):
            print(f'  {line}')
    elif is_circuit:
        print('Circuit detected but netlist extraction failed.')

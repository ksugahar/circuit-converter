#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SPICE Netlist (.cir) to LTSpice ASC (.asc) Converter

SPICEネットリストを解析し、自動レイアウトを行い、
LTSpice .ascスキーマティックファイルを生成する。

参考: dominc8/netlist_converter (C++/OGDF)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from enum import Enum
import re
import math


# =============================================================================
# 1. NetlistParser - ネットリスト解析
# =============================================================================

class ComponentType(Enum):
    """コンポーネント種別"""
    RESISTOR = 'R'
    CAPACITOR = 'C'
    INDUCTOR = 'L'
    VOLTAGE = 'V'
    CURRENT = 'I'
    DIODE = 'D'
    BJT = 'Q'
    MOSFET = 'M'
    JFET = 'J'
    SUBCIRCUIT = 'X'
    VCVS = 'E'
    CCCS = 'F'
    VCCS = 'G'
    CCVS = 'H'
    BEHAVIORAL = 'B'
    SWITCH = 'S'
    COUPLED = 'K'
    TLINE = 'T'
    GROUND = 'GND'
    NET_NODE = 'NET'


@dataclass
class Component:
    """パースされたコンポーネント"""
    name: str                    # R1, C1, V1 etc.
    comp_type: ComponentType
    node_pos: str                # 正端子ノード名
    node_neg: str                # 負端子ノード名
    value: str                   # 値（文字列のまま保持）
    raw_line: str = ''           # 元のネットリスト行


@dataclass
class SpiceDirective:
    """SPICEディレクティブ（.tran, .ac, .param 等）"""
    text: str


class NetlistParser:
    """SPICEネットリスト (.cir) パーサー"""

    # コンポーネントの先頭文字 → ComponentType
    TYPE_MAP = {
        'R': ComponentType.RESISTOR,
        'C': ComponentType.CAPACITOR,
        'L': ComponentType.INDUCTOR,
        'V': ComponentType.VOLTAGE,
        'I': ComponentType.CURRENT,
        'D': ComponentType.DIODE,
        'Q': ComponentType.BJT,
        'M': ComponentType.MOSFET,
        'J': ComponentType.JFET,
        'X': ComponentType.SUBCIRCUIT,
        'E': ComponentType.VCVS,
        'F': ComponentType.CCCS,
        'G': ComponentType.VCCS,
        'H': ComponentType.CCVS,
        'B': ComponentType.BEHAVIORAL,
        'S': ComponentType.SWITCH,
        'K': ComponentType.COUPLED,
        'T': ComponentType.TLINE,
    }

    def __init__(self):
        self.components: List[Component] = []
        self.directives: List[SpiceDirective] = []
        self.title: str = ''

    def parse_file(self, filepath: str) -> 'NetlistParser':
        """ファイルからネットリストをパース"""
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return self.parse_lines(lines)

    def parse_string(self, text: str) -> 'NetlistParser':
        """文字列からネットリストをパース"""
        lines = text.strip().split('\n')
        return self.parse_lines(lines)

    def parse_lines(self, lines: List[str]) -> 'NetlistParser':
        """行リストからネットリストをパース"""
        self.components = []
        self.directives = []
        self.title = ''

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # 最初の非空行はタイトル
            if i == 0 and not line.startswith('.') and not line.startswith('*'):
                first_char = line[0].upper()
                if first_char not in self.TYPE_MAP:
                    self.title = line
                    continue

            # コメント行
            if line.startswith('*'):
                continue

            # インラインコメント除去
            if ';' in line:
                comment_pos = line.index(';')
                # 文字列リテラル内でないか簡易チェック
                line = line[:comment_pos].strip()
                if not line:
                    continue

            # ディレクティブ行
            if line.startswith('.'):
                directive = line.lower()
                if directive == '.end':
                    break
                self.directives.append(SpiceDirective(text=line))
                continue

            # K文（結合定数）はディレクティブとして扱う
            if line[0].upper() == 'K':
                self.directives.append(SpiceDirective(text=line))
                continue

            # コンポーネント行をパース
            comp = self._parse_component_line(line)
            if comp:
                self.components.append(comp)

        return self

    def _parse_component_line(self, line: str) -> Optional[Component]:
        """コンポーネント行をパース"""
        parts = line.split()
        if len(parts) < 3:
            return None

        name = parts[0]
        first_char = name[0].upper()

        if first_char not in self.TYPE_MAP:
            return None

        comp_type = self.TYPE_MAP[first_char]

        # 3端子素子: Q C B E model / M D G S B model / J D G S model
        if comp_type in (ComponentType.BJT, ComponentType.JFET):
            if len(parts) >= 5:
                # Q name C B E model
                node_pos = parts[1]  # Collector/Drain
                node_neg = parts[3]  # Emitter/Source
                value = parts[4] if len(parts) > 4 else ''  # model name only
            else:
                return None
        elif comp_type == ComponentType.MOSFET:
            if len(parts) >= 6:
                # M name D G S B model
                node_pos = parts[1]  # Drain
                node_neg = parts[3]  # Source
                value = parts[5] if len(parts) > 5 else ''  # model name only
            else:
                return None
        elif comp_type == ComponentType.COUPLED:
            # K文はコンポーネントではなくディレクティブとして扱う
            return None
        elif comp_type == ComponentType.SUBCIRCUIT:
            # X name node1 node2 ... subckt_name
            if len(parts) >= 4:
                node_pos = parts[1]
                node_neg = parts[2]
                value = parts[-1]  # 最後がサブサーキット名
            else:
                return None
        else:
            # 2端子素子: name node+ node- value
            node_pos = parts[1]
            node_neg = parts[2]
            if len(parts) >= 4:
                value = ' '.join(parts[3:])
            else:
                value = ''

        return Component(
            name=name,
            comp_type=comp_type,
            node_pos=node_pos,
            node_neg=node_neg,
            value=value,
            raw_line=line,
        )

    def get_all_nodes(self) -> Set[str]:
        """全ユニークノード名を取得"""
        nodes = set()
        for comp in self.components:
            nodes.add(comp.node_pos)
            nodes.add(comp.node_neg)
        return nodes

    def get_ground_nodes(self) -> Set[str]:
        """グランドノード（'0' or 'gnd'）を取得"""
        nodes = self.get_all_nodes()
        return {n for n in nodes if n == '0' or n.lower() == 'gnd'}

    def get_signal_nodes(self) -> Set[str]:
        """信号ノード（グランド以外）を取得"""
        return self.get_all_nodes() - self.get_ground_nodes()


# =============================================================================
# 2. CircuitLayouter - グラフベースの自動レイアウト
# =============================================================================

@dataclass
class NodePosition:
    """ノードの座標"""
    x: int = 0
    y: int = 0


@dataclass
class PlacedComponent:
    """配置済みコンポーネント"""
    component: Component
    x: int = 0              # LTSpiceシンボル座標
    y: int = 0              # LTSpiceシンボル座標
    rotation: str = 'R0'    # R0, R90, R180, R270
    terminal1: Tuple[int, int] = (0, 0)  # 正端子座標
    terminal2: Tuple[int, int] = (0, 0)  # 負端子座標


# アンカーポイントオフセット（シンボル座標→端子座標）
# asc_parser.py の正規テーブルを使用（.asy実測値ベース）
from asc_parser import TERMINAL_OFFSETS as _ASC_OFFSETS

# ComponentType → asc_parser シンボル名のマッピング
_TYPE_TO_SYM = {
    ComponentType.RESISTOR: 'res',
    ComponentType.CAPACITOR: 'cap',
    ComponentType.INDUCTOR: 'ind',
    ComponentType.VOLTAGE: 'voltage',
    ComponentType.CURRENT: 'current',
    ComponentType.DIODE: 'diode',
}

ANCHOR_OFFSETS = {}
for ct, sym_name in _TYPE_TO_SYM.items():
    if sym_name in _ASC_OFFSETS:
        ANCHOR_OFFSETS[ct] = _ASC_OFFSETS[sym_name]

# 端子間距離（pin1-pin2 の距離、R0方向）
COMPONENT_SPAN = {}
for ct, sym_name in _TYPE_TO_SYM.items():
    offs = _ASC_OFFSETS.get(sym_name, {}).get('R0')
    if offs:
        p1, p2 = offs
        COMPONENT_SPAN[ct] = abs(p2[1] - p1[1])

GRID = 16  # LTSpiceグリッド


def snap(val: float) -> int:
    """グリッドスナップ"""
    return int(round(val / GRID) * GRID)


def calc_symbol_placement(comp_type: ComponentType, rotation: str,
                           t1: Tuple[int, int], t2: Tuple[int, int]):
    """端子座標からシンボル配置位置を逆算

    t1 = node_pos 端子の目標座標
    t2 = node_neg 端子の目標座標
    off1, off2 = anchor からの pin1, pin2 オフセット

    anchor = t1 - off1 （pin1 を t1 に合わせる）
    """
    offsets = ANCHOR_OFFSETS.get(comp_type, {}).get(rotation)
    if offsets is None:
        return (t1[0], t1[1], t1, t2)

    off1, off2 = offsets

    # pin1 を t1 に合わせてアンカー位置を逆算
    sym_x = snap(t1[0] - off1[0])
    sym_y = snap(t1[1] - off1[1])

    actual_t1 = (sym_x + off1[0], sym_y + off1[1])
    actual_t2 = (sym_x + off2[0], sym_y + off2[1])

    return (sym_x, sym_y, actual_t1, actual_t2)


class CircuitLayouter:
    """回路の自動レイアウト

    実際のLTSpice回路パターンに基づく配置:
    - ソース(V/I)は左側に縦配置（上が正端子）
    - 直列素子は上段を右へ水平配置
    - 分路素子は上段ノードから下のGNDへ縦配置
    - GNDは最下段
    """

    # ノード間隔
    H_SPACING = 192   # 水平ノード間隔
    V_SPACING = 192   # 垂直ノード間隔（上段→GND）
    TOP_Y = 192       # 上段のY座標
    GND_Y = 384       # GND段のY座標

    def __init__(self):
        self.node_positions: Dict[str, NodePosition] = {}
        self.placed_components: List[PlacedComponent] = []

    def layout(self, parser: NetlistParser) -> 'CircuitLayouter':
        """レイアウトを実行"""
        self.node_positions = {}
        self.placed_components = []

        ground_nodes = parser.get_ground_nodes()
        if not parser.components:
            return self

        # Step 1: コンポーネントを分類
        sources = []         # V/Iソース
        series_comps = []    # 直列素子（両端がシグナルノード）
        shunt_comps = []     # 分路素子（一端がGND）

        for comp in parser.components:
            is_source = comp.comp_type in (ComponentType.VOLTAGE,
                                            ComponentType.CURRENT)
            pos_is_gnd = comp.node_pos in ground_nodes
            neg_is_gnd = comp.node_neg in ground_nodes

            if is_source:
                sources.append(comp)
            elif pos_is_gnd or neg_is_gnd:
                shunt_comps.append(comp)
            else:
                series_comps.append(comp)

        # Step 2: ノード座標を決定
        self._assign_positions(parser, sources, series_comps, shunt_comps,
                               ground_nodes)

        # Step 3: 並列コンポーネントの水平オフセット計算
        # 同じ2ノード間に複数のコンポーネントがある場合、横にずらす
        parallel_offsets = self._calc_parallel_offsets(
            parser, sources, ground_nodes)

        # Step 4: コンポーネントを配置
        for comp in parser.components:
            offset = parallel_offsets.get(comp.name, 0)
            placed = self._place_component(comp, ground_nodes, offset)
            self.placed_components.append(placed)

        # Step 5: 重複解消 — 同一座標の部品を水平にずらす
        self._resolve_overlaps()

        return self

    def _resolve_overlaps(self):
        """部品重複と端子衝突を解消

        1. 同一座標のシンボルを水平にずらす
        2. 異なるノードの端子が同一座標にならないよう調整
        """
        for _ in range(20):
            # Phase 1: シンボル位置の重複
            occupied: Dict[Tuple[int, int], List[int]] = {}
            for i, pc in enumerate(self.placed_components):
                key = (pc.x, pc.y)
                occupied.setdefault(key, []).append(i)

            has_overlap = False
            for key, indices in occupied.items():
                if len(indices) <= 1:
                    continue
                has_overlap = True
                for rank, idx in enumerate(indices[1:], 1):
                    shift = rank * self.H_SPACING
                    self._shift_component(idx, shift)

            # Phase 2: 端子座標の衝突（異なるノードが同一座標）
            term_owner: Dict[Tuple[int, int], Tuple[str, int]] = {}  # coord -> (node, comp_idx)
            shift_set: Set[int] = set()
            for i, pc in enumerate(self.placed_components):
                if i in shift_set:
                    continue
                comp = pc.component
                for term, node in [(pc.terminal1, comp.node_pos),
                                   (pc.terminal2, comp.node_neg)]:
                    if term in term_owner:
                        existing_node, existing_idx = term_owner[term]
                        if existing_node != node:
                            has_overlap = True
                            shift_set.add(i)
                            break
                    else:
                        term_owner[term] = (node, i)

            # 衝突する部品を既存の全端子座標から離れた位置にシフト
            all_terms = set(term_owner.keys())
            for idx in sorted(shift_set):
                pc = self.placed_components[idx]
                # 右方向に空き位置を探す
                shift = self.H_SPACING
                while True:
                    new_t1 = (pc.terminal1[0] + shift, pc.terminal1[1])
                    new_t2 = (pc.terminal2[0] + shift, pc.terminal2[1])
                    if new_t1 not in all_terms and new_t2 not in all_terms:
                        break
                    shift += self.H_SPACING
                self._shift_component(idx, shift)
                # 新端子を登録
                pc2 = self.placed_components[idx]
                all_terms.add(pc2.terminal1)
                all_terms.add(pc2.terminal2)

            if not has_overlap:
                break

    def _shift_component(self, idx: int, shift: int):
        """部品を水平にシフト"""
        pc = self.placed_components[idx]
        self.placed_components[idx] = PlacedComponent(
            component=pc.component,
            x=pc.x + shift,
            y=pc.y,
            rotation=pc.rotation,
            terminal1=(pc.terminal1[0] + shift, pc.terminal1[1]),
            terminal2=(pc.terminal2[0] + shift, pc.terminal2[1]),
        )

    def _assign_positions(self, parser, sources, series_comps, shunt_comps,
                           ground_nodes):
        """ノード位置を割り当て"""
        # ソースの信号ノードを集める
        source_signal_nodes = set()
        for src in sources:
            if src.node_pos not in ground_nodes:
                source_signal_nodes.add(src.node_pos)
            if src.node_neg not in ground_nodes:
                source_signal_nodes.add(src.node_neg)

        # 直列チェーンを構築（ソースの信号ノードから右へ）
        # まず全信号ノードの順序を決める
        ordered_nodes = self._order_signal_nodes(
            parser, sources, series_comps, ground_nodes)

        # 信号ノードを上段に水平配置
        x = 0
        for node in ordered_nodes:
            self.node_positions[node] = NodePosition(x=x, y=self.TOP_Y)
            x += self.H_SPACING

        # GNDノード：接続されている信号ノードのX座標の最小値
        for gnd in ground_nodes:
            # GNDに直接繋がっている信号ノードのX座標を集める
            connected_x = []
            for comp in parser.components:
                other_node = None
                if comp.node_neg == gnd and comp.node_pos in self.node_positions:
                    other_node = comp.node_pos
                elif comp.node_pos == gnd and comp.node_neg in self.node_positions:
                    other_node = comp.node_neg

                if other_node:
                    connected_x.append(self.node_positions[other_node].x)

            if connected_x:
                gnd_x = min(connected_x)
            else:
                gnd_x = 0

            self.node_positions[gnd] = NodePosition(x=gnd_x, y=self.GND_Y)

    def _order_signal_nodes(self, parser, sources, series_comps,
                             ground_nodes) -> List[str]:
        """信号ノードを左から右の順序で並べる

        ソースの正端子から始めて、直列接続を辿る。
        """
        ordered = []
        visited = set()

        # ソースの信号側ノードを開始点にする
        start_nodes = []
        for src in sources:
            if src.node_pos not in ground_nodes:
                start_nodes.append(src.node_pos)
            elif src.node_neg not in ground_nodes:
                start_nodes.append(src.node_neg)

        if not start_nodes:
            # ソースがない場合、最初のコンポーネントのノードから
            if parser.components:
                comp = parser.components[0]
                if comp.node_pos not in ground_nodes:
                    start_nodes.append(comp.node_pos)
                if comp.node_neg not in ground_nodes:
                    start_nodes.append(comp.node_neg)

        # BFSで信号ノードを辿る
        queue = list(start_nodes)
        for node in queue:
            if node in visited or node in ground_nodes:
                continue
            visited.add(node)
            ordered.append(node)

            # このノードに接続された他の信号ノードを探す
            for comp in parser.components:
                neighbor = None
                if comp.node_pos == node and comp.node_neg not in ground_nodes:
                    neighbor = comp.node_neg
                elif comp.node_neg == node and comp.node_pos not in ground_nodes:
                    neighbor = comp.node_pos

                if neighbor and neighbor not in visited:
                    queue.append(neighbor)

        # 残りの信号ノード（到達できなかったもの）
        all_signal = parser.get_signal_nodes()
        for node in sorted(all_signal):
            if node not in visited and node not in ground_nodes:
                ordered.append(node)

        return ordered

    def _calc_parallel_offsets(self, parser, sources,
                                ground_nodes) -> Dict[str, int]:
        """並列コンポーネントの水平オフセットを計算"""
        offsets: Dict[str, int] = {}

        # 同じノードペアを共有するコンポーネントをグループ化
        node_pair_groups: Dict[Tuple[str, str], List[Component]] = {}
        for comp in parser.components:
            # ソートしたノードペア（順序無関係に同じペアを検出）
            pair = tuple(sorted([comp.node_pos, comp.node_neg]))
            node_pair_groups.setdefault(pair, []).append(comp)

        for pair, comps in node_pair_groups.items():
            if len(comps) <= 1:
                continue

            # 並列: ソースを除いた素子にオフセットを付ける
            non_source = [c for c in comps
                         if c.comp_type not in (ComponentType.VOLTAGE,
                                                 ComponentType.CURRENT)]
            source = [c for c in comps
                     if c.comp_type in (ComponentType.VOLTAGE,
                                         ComponentType.CURRENT)]

            # ソースはオフセットなし
            all_to_offset = source + non_source
            n = len(all_to_offset)
            for i, comp in enumerate(all_to_offset):
                offsets[comp.name] = i * self.H_SPACING

        return offsets

    def _place_component(self, comp: Component,
                          ground_nodes: Set[str],
                          h_offset: int = 0) -> PlacedComponent:
        """コンポーネントを端子ノード間に配置"""
        pos_node = self.node_positions.get(comp.node_pos, NodePosition(0, 0))
        neg_node = self.node_positions.get(comp.node_neg, NodePosition(0, 0))

        is_source = comp.comp_type in (ComponentType.VOLTAGE,
                                        ComponentType.CURRENT)
        pos_is_gnd = comp.node_pos in ground_nodes
        neg_is_gnd = comp.node_neg in ground_nodes

        # 並列オフセット適用
        ox = h_offset

        # 分路素子: GND側の座標を信号側ノードの真下に調整
        if neg_is_gnd and not is_source:
            t1 = (pos_node.x + ox, pos_node.y)
            t2 = (pos_node.x + ox, self.GND_Y)
        elif pos_is_gnd and not is_source:
            t1 = (neg_node.x + ox, self.GND_Y)
            t2 = (neg_node.x + ox, neg_node.y)
        elif is_source:
            # ソース: 信号ノードを上、GNDを下に固定
            if neg_is_gnd:
                signal_node = pos_node
                t1 = (signal_node.x + ox, self.TOP_Y)
                t2 = (signal_node.x + ox, self.GND_Y)
            elif pos_is_gnd:
                signal_node = neg_node
                t1 = (signal_node.x + ox, self.GND_Y)
                t2 = (signal_node.x, self.TOP_Y)
            else:
                t1 = (pos_node.x, pos_node.y)
                t2 = (neg_node.x, neg_node.y)
        else:
            t1 = (pos_node.x, pos_node.y)
            t2 = (neg_node.x, neg_node.y)

        # 方向と回転を決定
        dx = t2[0] - t1[0]
        dy = t2[1] - t1[1]

        if is_source:
            # ソースは常に垂直配置
            if comp.comp_type == ComponentType.VOLTAGE:
                # 電圧源: R0 = 正端子(上) → 負端子(下)
                rotation = 'R0'
            else:
                # 電流源: R180 = 矢印上向き（負端子下→正端子上）
                rotation = 'R180'
        elif abs(dx) > abs(dy):
            # 水平配置
            rotation = 'R90' if dx > 0 else 'R270'
        elif abs(dy) > 0:
            # 垂直配置
            rotation = 'R0' if dy > 0 else 'R180'
        else:
            rotation = 'R0'

        # シンボル位置を計算
        sym_x, sym_y, actual_t1, actual_t2 = calc_symbol_placement(
            comp.comp_type, rotation, t1, t2)

        return PlacedComponent(
            component=comp,
            x=sym_x,
            y=sym_y,
            rotation=rotation,
            terminal1=actual_t1,
            terminal2=actual_t2,
        )


# =============================================================================
# 3. AscGenerator - .ascファイル生成
# =============================================================================

class AscGenerator:
    """LTSpice .ascファイルジェネレータ"""

    # コンポーネント種別 → LTSpiceシンボル名
    SYMBOL_MAP = {
        ComponentType.RESISTOR: 'res',
        ComponentType.CAPACITOR: 'cap',
        ComponentType.INDUCTOR: 'ind',
        ComponentType.VOLTAGE: 'voltage',
        ComponentType.CURRENT: 'current',
        ComponentType.DIODE: 'diode',
        ComponentType.BJT: 'npn',
        ComponentType.MOSFET: 'nmos',
        ComponentType.JFET: 'njf',
        ComponentType.VCVS: 'e',
        ComponentType.VCCS: 'g',
        ComponentType.CCCS: 'f',
        ComponentType.CCVS: 'h',
        ComponentType.BEHAVIORAL: 'bv',
        ComponentType.SWITCH: 'sw',
        ComponentType.TLINE: 'tline',
    }

    # 回転別WINDOW設定
    WINDOW_CONFIGS = {
        'res': {
            'R90':  [('WINDOW 0 0 56 VBottom 2',), ('WINDOW 3 32 56 VTop 2',)],
            'R270': [('WINDOW 0 32 56 VTop 2',), ('WINDOW 3 0 56 VBottom 2',)],
        },
        'cap': {
            'R90':  [('WINDOW 0 0 32 VBottom 2',), ('WINDOW 3 32 32 VTop 2',)],
            'R270': [('WINDOW 0 0 32 VBottom 2',), ('WINDOW 3 32 32 VTop 2',)],
        },
        'ind': {
            'R270': [('WINDOW 0 32 56 VTop 2',), ('WINDOW 3 5 56 VBottom 2',)],
        },
        'current': {
            'R180': [('WINDOW 0 24 80 Left 2',), ('WINDOW 3 24 0 Left 2',)],
        },
    }

    def __init__(self):
        self.lines: List[str] = []

    def generate(self, layouter: CircuitLayouter, parser: NetlistParser,
                 sheet_width: int = 0, sheet_height: int = 0) -> str:
        """ASCファイル内容を生成"""
        self.lines = []

        placed = layouter.placed_components
        node_positions = layouter.node_positions

        # シートサイズの自動計算
        if sheet_width == 0 or sheet_height == 0:
            sw, sh = self._calc_sheet_size(placed, node_positions)
            if sheet_width == 0:
                sheet_width = sw
            if sheet_height == 0:
                sheet_height = sh

        # ヘッダ
        self.lines.append('Version 4')
        self.lines.append(f'SHEET 1 {sheet_width} {sheet_height}')

        # ワイヤ生成
        wires = self._generate_wires(placed, node_positions, parser)
        for w in wires:
            self.lines.append(f'WIRE {w[0]} {w[1]} {w[2]} {w[3]}')

        # 全端子にFLAGを配置（ワイヤレス接続）
        node_terminal_map: Dict[str, List[Tuple[int, int]]] = {}
        for pc in placed:
            comp = pc.component
            node_terminal_map.setdefault(comp.node_pos, []).append(pc.terminal1)
            node_terminal_map.setdefault(comp.node_neg, []).append(pc.terminal2)

        ground_nodes = parser.get_ground_nodes()

        for node_name, terminals in node_terminal_map.items():
            unique_pts = list(dict.fromkeys(terminals))
            if node_name in ground_nodes:
                flag_name = '0'
            else:
                flag_name = node_name

            for pt in unique_pts:
                self.lines.append(f'FLAG {pt[0]} {pt[1]} {flag_name}')

        # シンボル（コンポーネント）
        for pc in placed:
            self._write_symbol(pc)

        # ディレクティブ（.xxx および K文）
        directive_y = sheet_height - 100
        for i, directive in enumerate(parser.directives):
            text = directive.text
            if text.startswith('.') or text[0].upper() == 'K':
                self.lines.append(
                    f'TEXT 0 {directive_y + i * 32} Left 2 !{text}'
                )

        return '\n'.join(self.lines)

    def _write_symbol(self, pc: PlacedComponent):
        """コンポーネントシンボルを書き出す"""
        comp = pc.component
        sym_name = self.SYMBOL_MAP.get(comp.comp_type, 'res')

        self.lines.append(f'SYMBOL {sym_name} {pc.x} {pc.y} {pc.rotation}')

        # WINDOW設定
        win_config = self.WINDOW_CONFIGS.get(sym_name, {}).get(pc.rotation)
        if win_config:
            for win_lines in win_config:
                for wl in win_lines:
                    self.lines.append(wl)

        self.lines.append(f'SYMATTR InstName {comp.name}')
        if comp.value:
            self.lines.append(f'SYMATTR Value {comp.value}')

    def _generate_wires(self, placed: List[PlacedComponent],
                         node_positions: Dict[str, NodePosition],
                         parser: NetlistParser) -> List[Tuple[int, int, int, int]]:
        """ワイヤ（配線）を生成 — FLAGベース方式

        ワイヤ交差による短絡を防ぐため、長距離ワイヤは使わない。
        各端子はFLAG（ネットラベル）経由で接続する。
        ワイヤはシンボルピンからFLAG配置位置までの短い接続のみ。
        """
        # ワイヤは不要 — 全接続はFLAGで行う
        return []

    def _make_orthogonal_wires(self, x1: int, y1: int,
                                x2: int, y2: int
                                ) -> List[Tuple[int, int, int, int]]:
        """2点間の直交ワイヤを生成（L字ルーティング）"""
        wires = []

        if x1 == x2 and y1 == y2:
            return wires  # 長さ0

        if x1 == x2 or y1 == y2:
            # 既に直線
            wires.append((x1, y1, x2, y2))
        else:
            # L字ルーティング：まず水平、次に垂直
            wires.append((x1, y1, x2, y1))
            wires.append((x2, y1, x2, y2))

        return wires

    def _find_label_nodes(self, parser: NetlistParser) -> List[str]:
        """ラベルを付けるべきノードを特定

        - グランド以外で、名前が数字のみでないノード
        - 複数のコンポーネントが接続するノード
        """
        ground = parser.get_ground_nodes()
        node_count: Dict[str, int] = {}
        for comp in parser.components:
            node_count[comp.node_pos] = node_count.get(comp.node_pos, 0) + 1
            node_count[comp.node_neg] = node_count.get(comp.node_neg, 0) + 1

        labels = []
        for node_name in sorted(parser.get_signal_nodes()):
            if node_name in ground:
                continue
            # 名前が意味のある文字列（数字だけでない）の場合ラベル付け
            if not node_name.isdigit():
                labels.append(node_name)
            # または接続数が3以上（ジャンクション）の場合
            elif node_count.get(node_name, 0) >= 3:
                labels.append(node_name)

        return labels

    def _calc_sheet_size(self, placed: List[PlacedComponent],
                          node_positions: Dict[str, NodePosition]
                          ) -> Tuple[int, int]:
        """シートサイズを自動計算"""
        max_x = 400
        max_y = 400
        min_x = 0
        min_y = 0

        for pc in placed:
            max_x = max(max_x, pc.x + 200, pc.terminal1[0] + 100,
                       pc.terminal2[0] + 100)
            max_y = max(max_y, pc.y + 200, pc.terminal1[1] + 100,
                       pc.terminal2[1] + 100)
            min_x = min(min_x, pc.x, pc.terminal1[0], pc.terminal2[0])
            min_y = min(min_y, pc.y, pc.terminal1[1], pc.terminal2[1])

        for pos in node_positions.values():
            max_x = max(max_x, pos.x + 200)
            max_y = max(max_y, pos.y + 200)

        # ディレクティブ用スペース
        max_y += 150

        # 最小サイズ
        max_x = max(max_x, 880)
        max_y = max(max_y, 680)

        return (snap(max_x), snap(max_y))

    def _snap(self, val: float) -> int:
        """グリッドスナップ"""
        return snap(val)


# =============================================================================
# 4. NetlistToAsc - 統合クラス
# =============================================================================

class NetlistToAsc:
    """ネットリスト → ASC変換の統合インターフェース"""

    def __init__(self):
        self.parser = NetlistParser()
        self.layouter = CircuitLayouter()
        self.generator = AscGenerator()

    def convert_file(self, input_path: str, output_path: str = None) -> str:
        """.cirファイルを.ascファイルに変換"""
        self.parser.parse_file(input_path)
        self.layouter.layout(self.parser)
        asc_content = self.generator.generate(self.layouter, self.parser)

        if output_path is None:
            output_path = input_path.rsplit('.', 1)[0] + '.asc'

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(asc_content)

        print(f'ASC saved: {output_path}')
        print(f'  Components: {len(self.parser.components)}')
        print(f'  Nodes: {len(self.parser.get_all_nodes())}')
        print(f'  Directives: {len(self.parser.directives)}')

        return asc_content

    def convert_string(self, netlist: str, output_path: str = None) -> str:
        """ネットリスト文字列を.ascに変換"""
        self.parser.parse_string(netlist)
        self.layouter.layout(self.parser)
        asc_content = self.generator.generate(self.layouter, self.parser)

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(asc_content)
            print(f'ASC saved: {output_path}')

        return asc_content


# =============================================================================
# テスト用
# =============================================================================

if __name__ == '__main__':
    # テスト1: 簡単なRC回路
    netlist_rc = """\
* RC Lowpass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 1u
.ac dec 100 1 100k
.end
"""

    print("=== Test 1: RC Lowpass ===")
    converter = NetlistToAsc()
    asc = converter.convert_string(netlist_rc)
    print(asc)
    print()

    # テスト2: RLC直列回路
    netlist_rlc = """\
* RLC Series Circuit
I1 0 in AC 1
R1 in mid 100
L1 mid out 10m
C1 out 0 1u
.ac dec 100 10 100k
.end
"""

    print("=== Test 2: RLC Series ===")
    asc2 = converter.convert_string(netlist_rlc)
    print(asc2)
    print()

    # テスト3: 並列LC回路
    netlist_plc = """\
* Parallel LC Tank
I1 0 top AC 1
L1 top 0 1m
C1 top 0 100n
.ac dec 100 1k 100k
.end
"""

    print("=== Test 3: Parallel LC ===")
    asc3 = converter.convert_string(netlist_plc)
    print(asc3)

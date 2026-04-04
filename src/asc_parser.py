#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LTSpice ASC Parser & Netlist Extractor

.ascファイルを解析し、SPICEネットリスト(.cir)を生成する。
ラウンドトリップ試験（.cir → .asc → .cir）の逆変換側。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Set, Optional
from pathlib import Path
import re
import zipfile
import math


# =============================================================================
# ASCデータ構造
# =============================================================================

@dataclass
class AscWire:
    """WIRE要素"""
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class AscFlag:
    """FLAG要素（ネットラベル・GND）"""
    x: int
    y: int
    name: str   # '0' = GND, otherwise net label


@dataclass
class AscSymbol:
    """SYMBOL要素（コンポーネント）"""
    symbol_type: str    # res, cap, ind, voltage, current, npn, etc.
    x: int
    y: int
    rotation: str       # R0, R90, R180, R270, M0, ...
    inst_name: str = ''
    value: str = ''
    value2: str = ''
    spice_model: str = ''
    spice_line: str = ''
    windows: List[str] = field(default_factory=list)
    symbol_path: str = ''  # full path from SYMBOL line (e.g. 'Opamps\\opamp')


@dataclass
class AscText:
    """TEXT要素（ディレクティブ・コメント）"""
    x: int
    y: int
    text: str
    is_directive: bool = False  # ! prefix


# =============================================================================
# 共通端子オフセットテンプレート
# =============================================================================
# .asy シンボルファイルの PIN 座標を基準に、回転/ミラー変換で全方向を生成。
# 変換行列 (実測検証済み):
#   R0:  (x, y)     R90: (-y, x)    R180: (-x, -y)   R270: (y, -x)
#   M0:  (-x, y)    M90: (y, x)     M180: (x, -y)    M270: (-y, -x)


def _make_offsets(pins):
    """R0のピン座標タプルから全8回転のオフセット辞書を生成"""
    tf = {
        'R0':   lambda x, y: (x, y),
        'R90':  lambda x, y: (-y, x),
        'R180': lambda x, y: (-x, -y),
        'R270': lambda x, y: (y, -x),
        'M0':   lambda x, y: (-x, y),
        'M90':  lambda x, y: (y, x),
        'M180': lambda x, y: (x, -y),
        'M270': lambda x, y: (-y, -x),
    }
    return {rot: tuple(f(px, py) for px, py in pins)
            for rot, f in tf.items()}


# .asy PIN 座標 (R0方向)
# res.asy:     PIN 16 16, PIN 16 96
# cap.asy:     PIN 16 0,  PIN 16 64
# ind.asy:     PIN 16 16, PIN 16 96
# ind2.asy:    PIN 16 16, PIN 16 96
# polcap.asy:  PIN 16 0,  PIN 16 64
# voltage.asy: PIN 0 16,  PIN 0 96
# current.asy: PIN 0 0,   PIN 0 80
# battery.asy: PIN 0 16,  PIN 0 96
# diode.asy:   PIN 16 0,  PIN 16 64
# npn.asy:     PIN 64 0,  PIN 0 48,  PIN 64 96  (C, B, E)
# pnp.asy:     PIN 64 0,  PIN 0 48,  PIN 64 96  (C, B, E)
# nmos.asy:    PIN 48 0,  PIN 0 80,  PIN 48 96  (D, G, S)
# pmos.asy:    PIN 48 0,  PIN 0 80,  PIN 48 96  (D, G, S)

_2T_RES = _make_offsets(((16, 16), (16, 96)))
_2T_CAP = _make_offsets(((16, 0), (16, 64)))
_2T_IND = _make_offsets(((16, 16), (16, 96)))
_2T_VOLTAGE = _make_offsets(((0, 16), (0, 96)))
_2T_CURRENT = _make_offsets(((0, 0), (0, 80)))
_2T_BATTERY = _make_offsets(((0, 16), (0, 96)))
_2T_DIODE = _make_offsets(((16, 0), (16, 64)))

_3T_NPN = _make_offsets(((64, 0), (0, 48), (64, 96)))
_3T_PNP = _make_offsets(((64, 0), (0, 48), (64, 96)))
_3T_NMOS = _make_offsets(((48, 0), (0, 80), (48, 96)))
_3T_PMOS = _make_offsets(((48, 0), (0, 80), (48, 96)))

# JFET: NPN と同じピン配置
_3T_NJF = _3T_NPN
_3T_PJF = _3T_PNP

# アンカーポイントオフセット（シンボル座標 → 端子座標）
TERMINAL_OFFSETS = {
    # 受動素子 (2端子)
    'res':      _2T_RES,
    'cap':      _2T_CAP,
    'ind':      _2T_IND,
    'ind2':     _2T_IND,
    'polcap':   _2T_CAP,

    # ソース (2端子)
    'voltage':  _2T_VOLTAGE,
    'current':  _2T_CURRENT,
    'battery':  _2T_BATTERY,

    # ダイオード系 (2端子)
    'diode':    _2T_DIODE,
    'schottky': _2T_DIODE,
    'zener':    _2T_DIODE,
    'led':      _2T_DIODE,
    'varactor': _2T_DIODE,
    'tvs':      _2T_DIODE,

    # スイッチ (2端子 + 制御)
    'sw':       _2T_CAP,

    # 制御ソース (2端子出力 + 2端子制御) — 出力端子のみ
    'e':        _2T_VOLTAGE,    # VCVS
    'e2':       _2T_VOLTAGE,
    'g':        _2T_VOLTAGE,    # VCCS
    'g2':       _2T_VOLTAGE,
    'f':        _2T_VOLTAGE,    # CCCS
    'h':        _2T_VOLTAGE,    # CCVS
    'bv':       _2T_VOLTAGE,    # Behavioral voltage
    'bi':       _2T_CURRENT,    # Behavioral current
    'bi2':      _2T_CURRENT,

    # クリスタル (2端子)
    'xtal':     _2T_CAP,

    # 伝送線路 (4端子→2端子近似)
    'tline':    _2T_CAP,
}

# 3端子素子のオフセット（別テーブル: C/D, B/G, E/S の3点）
TERMINAL_OFFSETS_3 = {
    'npn':   _3T_NPN,
    'pnp':   _3T_PNP,
    'nmos':  _3T_NMOS,
    'pmos':  _3T_PMOS,
    'njf':   _3T_NJF,
    'pjf':   _3T_PJF,
    'nigbt': _3T_NPN,
}

# シンボルタイプ → SPICEプレフィックス
SYMBOL_TO_SPICE = {
    'res': 'R', 'cap': 'C', 'ind': 'L', 'ind2': 'L',
    'polcap': 'C',
    'voltage': 'V', 'current': 'I', 'battery': 'V',
    'diode': 'D', 'schottky': 'D', 'zener': 'D', 'led': 'D',
    'varactor': 'D', 'tvs': 'D',
    'npn': 'Q', 'pnp': 'Q',
    'nmos': 'M', 'pmos': 'M',
    'njf': 'J', 'pjf': 'J',
    'nigbt': 'Z',
    'e': 'E', 'e2': 'E', 'g': 'G', 'g2': 'G',
    'f': 'F', 'h': 'H',
    'bv': 'B', 'bi': 'B', 'bi2': 'B',
    'sw': 'S',
    'xtal': 'X',
    'tline': 'T',
}

# 2端子素子シンボルタイプ（ラウンドトリップ対応可能なもの）
TWO_TERMINAL_SYMBOLS = {
    'res', 'cap', 'ind', 'ind2', 'polcap',
    'voltage', 'current', 'battery',
    'diode', 'schottky', 'zener', 'led', 'varactor', 'tvs',
    'sw', 'xtal', 'tline',
    'e', 'e2', 'g', 'g2', 'f', 'h', 'bv', 'bi', 'bi2',
}

# 3端子素子シンボルタイプ
THREE_TERMINAL_SYMBOLS = {
    'npn', 'pnp', 'nmos', 'pmos', 'njf', 'pjf', 'nigbt',
}

# 受動素子のみ（旧定義 — 後方互換）
PASSIVE_SYMBOLS = {'res', 'cap', 'ind', 'ind2', 'voltage', 'current'}

# ラウンドトリップ可能な全シンボルタイプ
SUPPORTED_SYMBOLS = TWO_TERMINAL_SYMBOLS | THREE_TERMINAL_SYMBOLS


# =============================================================================
# LTspice標準ライブラリ検索パス
# =============================================================================

# LTspice lib.zip の場所候補
_LTSPICE_LIB_ZIP_CANDIDATES = [
    Path(r'C:/Program Files/ADI/LTspice/lib.zip'),
    Path(r'C:/Program Files (x86)/ADI/LTspice/lib.zip'),
]

def _find_ltspice_lib_zip() -> Optional[Path]:
    """LTspice lib.zip を探す"""
    import os
    for p in _LTSPICE_LIB_ZIP_CANDIDATES:
        if p.is_file():
            return p
    # LOCALAPPDATA
    local = os.environ.get('LOCALAPPDATA', '')
    if local:
        p = Path(local) / 'Programs' / 'ADI' / 'LTspice' / 'lib.zip'
        if p.is_file():
            return p
    return None


# =============================================================================
# .asy ファイルパーサー (Improvement A)
# =============================================================================

@dataclass
class AsyPin:
    """PIN definition from .asy file"""
    x: int
    y: int
    direction: str  # LEFT, RIGHT, TOP, BOTTOM, NONE
    name: str = ''
    spice_order: int = 0


class AsyParser:
    """Parse .asy files to extract pin definitions and ordering."""

    # Cache: symbol_path -> list of AsyPin (sorted by SpiceOrder)
    _cache: Dict[str, Optional[List[AsyPin]]] = {}
    _zip_file: Optional[zipfile.ZipFile] = None
    _zip_path: Optional[Path] = None

    @classmethod
    def _get_zip(cls) -> Optional[zipfile.ZipFile]:
        """Get (or open) the LTspice lib.zip"""
        zip_path = _find_ltspice_lib_zip()
        if zip_path is None:
            return None
        if cls._zip_file is None or cls._zip_path != zip_path:
            try:
                cls._zip_file = zipfile.ZipFile(str(zip_path), 'r')
                cls._zip_path = zip_path
            except Exception:
                return None
        return cls._zip_file

    @classmethod
    def parse_asy_text(cls, text: str) -> Optional[List[AsyPin]]:
        """Parse .asy file content, return pins sorted by SpiceOrder."""
        pins: List[AsyPin] = []
        current_pin: Optional[AsyPin] = None

        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('PIN '):
                if current_pin is not None:
                    pins.append(current_pin)
                parts = line.split()
                current_pin = AsyPin(
                    x=int(parts[1]) if len(parts) > 1 else 0,
                    y=int(parts[2]) if len(parts) > 2 else 0,
                    direction=parts[3] if len(parts) > 3 else 'NONE',
                )
            elif line.startswith('PINATTR ') and current_pin is not None:
                parts = line.split(' ', 2)
                if len(parts) >= 3:
                    if parts[1] == 'PinName':
                        current_pin.name = parts[2]
                    elif parts[1] == 'SpiceOrder':
                        try:
                            current_pin.spice_order = int(parts[2])
                        except ValueError:
                            pass

        if current_pin is not None:
            pins.append(current_pin)

        if not pins:
            return None

        # Sort by SpiceOrder (0 means unspecified, put at end)
        pins.sort(key=lambda p: (p.spice_order == 0, p.spice_order))
        return pins

    @classmethod
    def find_and_parse(cls, symbol_path: str,
                       search_dirs: Optional[List[Path]] = None
                       ) -> Optional[List[AsyPin]]:
        """Find and parse .asy for a given symbol path.

        symbol_path: e.g. 'Opamps\\\\opamp', 'res', 'misc\\\\DIAC'
        search_dirs: additional directories to search (e.g. .asc file's dir)
        """
        cache_key = symbol_path + '|' + str(search_dirs or [])
        if cache_key in cls._cache:
            return cls._cache[cache_key]

        # Normalize path separators
        norm_path = symbol_path.replace('\\', '/')

        # 1. Search in provided directories
        if search_dirs:
            for d in search_dirs:
                d = Path(d)
                # Try exact path
                asy_file = d / (norm_path + '.asy')
                if asy_file.is_file():
                    result = cls._parse_file(asy_file)
                    if result:
                        cls._cache[cache_key] = result
                        return result
                # Try just the basename
                base = norm_path.split('/')[-1]
                asy_file = d / (base + '.asy')
                if asy_file.is_file():
                    result = cls._parse_file(asy_file)
                    if result:
                        cls._cache[cache_key] = result
                        return result

        # 2. Search in lib.zip
        zf = cls._get_zip()
        if zf is not None:
            # Try lib/sym/<path>.asy
            zip_path = f'lib/sym/{norm_path}.asy'
            try:
                result = cls._parse_zip_entry(zf, zip_path)
                if result:
                    cls._cache[cache_key] = result
                    return result
            except KeyError:
                pass
            # Try case-insensitive search for the basename
            base = norm_path.split('/')[-1].lower()
            for name in zf.namelist():
                if name.lower().endswith(f'/{base}.asy') and name.startswith('lib/sym/'):
                    try:
                        result = cls._parse_zip_entry(zf, name)
                        if result:
                            cls._cache[cache_key] = result
                            return result
                    except Exception:
                        pass

        cls._cache[cache_key] = None
        return None

    @classmethod
    def _parse_zip_entry(cls, zf: zipfile.ZipFile,
                          entry_name: str) -> Optional[List[AsyPin]]:
        """Parse a .asy file from a zip entry, handling encoding."""
        raw = zf.read(entry_name)
        # Try encodings in order
        for enc in ('utf-8', 'utf-16-le', 'utf-16-be', 'ascii', 'latin-1'):
            try:
                text = raw.decode(enc)
                if text.startswith('\ufeff'):
                    text = text[1:]
                # Quick sanity check: should contain 'PIN' if valid
                if 'PIN ' in text:
                    return cls.parse_asy_text(text)
                elif 'Version' in text and 'PIN' not in text:
                    # Valid .asy but no PINs
                    return None
            except (UnicodeDecodeError, UnicodeError):
                continue
        return None

    @classmethod
    def _parse_file(cls, path: Path) -> Optional[List[AsyPin]]:
        """Parse a .asy file from disk."""
        for enc in ('utf-8', 'utf-16-le', 'ascii', 'latin-1'):
            try:
                text = path.read_text(encoding=enc)
                if text.startswith('\ufeff'):
                    text = text[1:]
                return cls.parse_asy_text(text)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return None

    @classmethod
    def get_terminal_offsets(cls, symbol_path: str, rotation: str,
                             search_dirs: Optional[List[Path]] = None
                             ) -> Optional[Tuple[Tuple[int, int], ...]]:
        """Get terminal positions (offsets from symbol origin) in SpiceOrder.

        Returns absolute offsets after applying rotation/mirror transform.
        """
        pins = cls.find_and_parse(symbol_path, search_dirs)
        if pins is None:
            return None

        # Apply rotation/mirror to pin coordinates
        offsets = []
        for pin in pins:
            if pin.spice_order == 0:
                continue  # Skip pins without SpiceOrder
            ox, oy = cls._transform_point(pin.x, pin.y, rotation)
            offsets.append((ox, oy))

        if not offsets:
            # If no SpiceOrder defined, use all pins in order
            for pin in pins:
                ox, oy = cls._transform_point(pin.x, pin.y, rotation)
                offsets.append((ox, oy))

        return tuple(offsets) if offsets else None

    @staticmethod
    def _transform_point(x: int, y: int, rotation: str) -> Tuple[int, int]:
        """Transform a point by LTspice rotation/mirror.

        LTspice rotations: R0, R90, R180, R270, M0, M90, M180, M270
        In LTspice coordinate system (Y increases downward):
        - R90 = 90 degrees clockwise
        - M = mirror about Y axis (negate X), then apply rotation
        """
        mirror = rotation.startswith('M')
        angle = int(rotation[1:]) if len(rotation) > 1 else 0

        if mirror:
            x = -x

        if angle == 0:
            return (x, y)
        elif angle == 90:
            return (-y, x)
        elif angle == 180:
            return (-x, -y)
        elif angle == 270:
            return (y, -x)
        return (x, y)


# =============================================================================
# ASCパーサー
# =============================================================================

class AscParser:
    """LTSpice .ascファイルパーサー"""

    def __init__(self, asy_search_dirs: Optional[List[Path]] = None):
        self.wires: List[AscWire] = []
        self.flags: List[AscFlag] = []
        self.symbols: List[AscSymbol] = []
        self.texts: List[AscText] = []
        self.sheet_width: int = 880
        self.sheet_height: int = 680
        self.version: str = '4'
        self.asy_search_dirs: List[Path] = list(asy_search_dirs or [])
        self.source_dir: Optional[Path] = None  # directory of the .asc file

    def parse_file(self, filepath: str) -> 'AscParser':
        """ファイルからASCをパース"""
        path = Path(filepath)
        self.source_dir = path.parent
        # Add source directory to .asy search path
        if self.source_dir not in self.asy_search_dirs:
            self.asy_search_dirs.insert(0, self.source_dir)
        # LTSpice 17+ はUTF-16 LE、それ以前はUTF-8/ASCII
        for encoding in ['utf-8', 'utf-16-le', 'ascii', 'latin-1']:
            try:
                text = path.read_text(encoding=encoding)
                if text.startswith('\ufeff'):
                    text = text[1:]  # BOM除去
                if 'Version' in text or 'SHEET' in text:
                    break
            except (UnicodeDecodeError, UnicodeError):
                continue
        else:
            raise ValueError(f"Cannot decode {filepath}")

        return self.parse_string(text)

    def parse_string(self, text: str) -> 'AscParser':
        """文字列からASCをパース"""
        self.wires = []
        self.flags = []
        self.symbols = []
        self.texts = []

        lines = text.split('\n')
        current_symbol: Optional[AscSymbol] = None

        for line in lines:
            line = line.rstrip()
            if not line:
                continue

            if line.startswith('Version '):
                self.version = line.split(' ', 1)[1].strip()

            elif line.startswith('SHEET '):
                parts = line.split()
                if len(parts) >= 4:
                    self.sheet_width = int(parts[2])
                    self.sheet_height = int(parts[3])

            elif line.startswith('WIRE '):
                parts = line.split()
                if len(parts) >= 5:
                    self.wires.append(AscWire(
                        x1=int(parts[1]), y1=int(parts[2]),
                        x2=int(parts[3]), y2=int(parts[4])
                    ))

            elif line.startswith('FLAG '):
                parts = line.split()
                if len(parts) >= 4:
                    self.flags.append(AscFlag(
                        x=int(parts[1]), y=int(parts[2]),
                        name=' '.join(parts[3:])
                    ))

            elif line.startswith('SYMBOL '):
                # 前のシンボルを確定
                if current_symbol:
                    self.symbols.append(current_symbol)

                parts = line.split()
                sym_type = parts[1] if len(parts) > 1 else ''
                # パス区切りを統一（backslash → 最後の部分のみ）
                sym_base = sym_type.split('\\')[-1].lower()

                current_symbol = AscSymbol(
                    symbol_type=sym_base,
                    x=int(parts[2]) if len(parts) > 2 else 0,
                    y=int(parts[3]) if len(parts) > 3 else 0,
                    rotation=parts[4] if len(parts) > 4 else 'R0',
                    symbol_path=sym_type,  # keep full path for .asy lookup
                )

            elif line.startswith('WINDOW ') and current_symbol:
                current_symbol.windows.append(line)

            elif line.startswith('SYMATTR ') and current_symbol:
                parts = line.split(' ', 2)
                attr_name = parts[1] if len(parts) > 1 else ''
                attr_value = parts[2] if len(parts) > 2 else ''

                if attr_name == 'InstName':
                    current_symbol.inst_name = attr_value
                elif attr_name == 'Value':
                    current_symbol.value = attr_value
                elif attr_name == 'Value2':
                    current_symbol.value2 = attr_value
                elif attr_name == 'SpiceModel':
                    current_symbol.spice_model = attr_value
                elif attr_name == 'SpiceLine':
                    current_symbol.spice_line = attr_value

            elif line.startswith('TEXT '):
                parts = line.split(' ', 5)
                if len(parts) >= 6:
                    raw_text = parts[5] if len(parts) > 5 else ''
                    is_dir = raw_text.startswith('!')
                    if is_dir:
                        raw_text = raw_text[1:]
                    self.texts.append(AscText(
                        x=int(parts[1]), y=int(parts[2]),
                        text=raw_text,
                        is_directive=is_dir,
                    ))
                elif len(parts) >= 5:
                    raw_text = parts[4] if len(parts) > 4 else ''
                    is_dir = raw_text.startswith('!')
                    if is_dir:
                        raw_text = raw_text[1:]
                    self.texts.append(AscText(
                        x=int(parts[1]), y=int(parts[2]),
                        text=raw_text,
                        is_directive=is_dir,
                    ))

        # 最後のシンボルを確定
        if current_symbol:
            self.symbols.append(current_symbol)

        return self

    def get_component_terminals(self, sym: AscSymbol) -> Optional[Tuple[Tuple[int,int], ...]]:
        """シンボルの端子座標を計算（2端子、3端子、.asy、または推定）"""
        # 2端子素子（ハードコードテーブル）
        offsets = TERMINAL_OFFSETS.get(sym.symbol_type, {}).get(sym.rotation)
        if offsets is not None:
            off1, off2 = offsets
            t1 = (sym.x + off1[0], sym.y + off1[1])
            t2 = (sym.x + off2[0], sym.y + off2[1])
            return (t1, t2)

        # 3端子素子（ハードコードテーブル）
        offsets3 = TERMINAL_OFFSETS_3.get(sym.symbol_type, {}).get(sym.rotation)
        if offsets3 is not None:
            return tuple(
                (sym.x + off[0], sym.y + off[1]) for off in offsets3
            )

        # .asy ファイルからピン定義を取得 (Improvement A)
        asy_offsets = AsyParser.get_terminal_offsets(
            sym.symbol_path or sym.symbol_type,
            sym.rotation,
            search_dirs=self.asy_search_dirs if self.asy_search_dirs else None
        )
        if asy_offsets is not None:
            return tuple(
                (sym.x + off[0], sym.y + off[1]) for off in asy_offsets
            )

        # 未知のシンボル → ワイヤ端点から端子位置を推定
        return self._estimate_terminals(sym)

    def _estimate_terminals(self, sym: AscSymbol,
                             search_radius: int = 160
                             ) -> Optional[Tuple[Tuple[int,int], ...]]:
        """ワイヤ端点からシンボルの端子位置を推定"""
        nearby = []
        for w in self.wires:
            for wx, wy in [(w.x1, w.y1), (w.x2, w.y2)]:
                dist = abs(wx - sym.x) + abs(wy - sym.y)
                if dist <= search_radius:
                    nearby.append((wx, wy, dist))

        if not nearby:
            return None

        # 距離でソートし、重複除去
        seen = set()
        unique = []
        for wx, wy, d in sorted(nearby, key=lambda x: x[2]):
            if (wx, wy) not in seen:
                seen.add((wx, wy))
                unique.append((wx, wy))

        if len(unique) >= 2:
            return tuple(unique[:max(len(unique), 8)])  # 最大8ピン
        elif len(unique) == 1:
            return (unique[0],)

        return None

    def get_passive_symbols(self) -> List[AscSymbol]:
        """受動素子（R,C,L,V,I）のシンボルのみ返す"""
        return [s for s in self.symbols if s.symbol_type in PASSIVE_SYMBOLS]

    def has_only_passives(self) -> bool:
        """受動素子のみで構成されているか"""
        for s in self.symbols:
            if s.symbol_type not in PASSIVE_SYMBOLS:
                return False
        return True

    def has_only_supported(self) -> bool:
        """サポートされた素子のみで構成されているか"""
        for s in self.symbols:
            if s.symbol_type not in SUPPORTED_SYMBOLS:
                return False
        return True

    def get_symbol_types(self) -> Set[str]:
        """全シンボルタイプを返す"""
        return {s.symbol_type for s in self.symbols}


# =============================================================================
# ネットリスト抽出器
# =============================================================================

class NetlistExtractor:
    """ASCからSPICEネットリストを抽出する

    ワイヤ接続を辿って各コンポーネントの端子がどのノードに
    繋がっているかを特定し、SPICEネットリストを生成する。
    """

    def __init__(self, asc: AscParser):
        self.asc = asc
        # 座標 → ノード名のマッピング
        self.coord_to_node: Dict[Tuple[int,int], str] = {}
        self._node_counter = 0

    def extract(self) -> str:
        """SPICEネットリストを生成"""
        # Step 1: ワイヤで接続された座標をグループ化（同一ネット）
        net_groups = self._build_net_groups()

        # Step 2: FLAGからノード名を割り当て
        self._assign_flag_names(net_groups)

        # Step 3: 全座標にノード名を割り当て
        self._assign_node_names(net_groups)

        # Step 4: コンポーネントのネットリスト行を生成
        lines = []
        lines.append('* Extracted from ASC')

        for sym in self.asc.symbols:
            spice_line = self._symbol_to_spice(sym)
            if spice_line:
                lines.append(spice_line)

        # Step 5: ディレクティブ (Improvement B & E)
        # TEXT directives in .asc use literal \n as line separator.
        # We must restore them as real newlines. Multi-line .subckt blocks,
        # .model, .param, .lib, .include, .ic, continuation lines (+) are
        # all preserved.
        for text in self.asc.texts:
            if text.is_directive:
                # Split on literal \n (which appears as \\n in the parsed text)
                sub_lines = text.text.split('\\n')
                for sub in sub_lines:
                    sub = sub.strip()
                    if sub:
                        lines.append(sub)
            elif text.text.strip().startswith('.'):
                # Non-directive TEXT that looks like a SPICE directive
                # (sometimes users forget the ! prefix)
                sub_lines = text.text.split('\\n')
                for sub in sub_lines:
                    sub = sub.strip()
                    if sub and sub.startswith('.'):
                        lines.append(sub)

        lines.append('.end')
        return '\n'.join(lines)

    def _build_net_groups(self) -> List[Set[Tuple[int,int]]]:
        """ワイヤで接続された座標をUnion-Findでグループ化

        LTSpiceのワイヤはコンポーネント端子を通り抜ける形で配線される。
        端子位置でワイヤを分割（切断）してからUnion-Findを実行する。
        """
        # Step 1: 全端子座標を集める
        terminal_coords: Set[Tuple[int,int]] = set()
        for sym in self.asc.symbols:
            terms = self.asc.get_component_terminals(sym)
            if terms:
                for t in terms:
                    terminal_coords.add(t)

        # Step 2: コンポーネントの端子ペア（内部接続）を集める
        # 隣接する端子間をペアとして登録
        internal_pairs: Set[Tuple[Tuple[int,int], Tuple[int,int]]] = set()
        for sym in self.asc.symbols:
            terms = self.asc.get_component_terminals(sym)
            if terms and len(terms) >= 2:
                for i in range(len(terms)):
                    for j in range(i + 1, len(terms)):
                        internal_pairs.add((terms[i], terms[j]))
                        internal_pairs.add((terms[j], terms[i]))

        # Step 3: ワイヤを端子位置で分割
        split_wires = []
        for w in self.asc.wires:
            segments = self._split_wire_at_terminals(w, terminal_coords)
            split_wires.extend(segments)

        # Step 4: コンポーネント内部を通るセグメントを除外
        # セグメントの両端がともに同一コンポーネントの端子ペアなら除外
        external_wires = []
        for seg in split_wires:
            p1 = (seg[0], seg[1])
            p2 = (seg[2], seg[3])
            if (p1, p2) in internal_pairs:
                continue  # コンポーネント内部のワイヤ
            external_wires.append(seg)
        split_wires = external_wires

        # (snap tolerance already applied in _build_net_groups)

        # Step 3: Union-Find
        parent: Dict[Tuple[int,int], Tuple[int,int]] = {}

        def find(c):
            if c not in parent:
                parent[c] = c
            while parent[c] != c:
                parent[c] = parent[parent[c]]
                c = parent[c]
            return c

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # 全座標を集める
        all_coords: Set[Tuple[int,int]] = set()
        for w in split_wires:
            all_coords.add((w[0], w[1]))
            all_coords.add((w[2], w[3]))
        for f in self.asc.flags:
            all_coords.add((f.x, f.y))
        all_coords.update(terminal_coords)

        # 分割済みワイヤ端点を結合
        for w in split_wires:
            union((w[0], w[1]), (w[2], w[3]))

        # FLAG座標がワイヤ上にあるかチェック
        for f in self.asc.flags:
            fc = (f.x, f.y)
            for w in split_wires:
                wire = AscWire(w[0], w[1], w[2], w[3])
                if self._point_on_wire(f.x, f.y, wire):
                    union(fc, (w[0], w[1]))
                    break

        # Step 5: 端子座標をワイヤ端点にスナップ（近傍探索）
        # 3端子素子の端子オフセットは概算なので、余裕をもたせる
        SNAP_TOLERANCE = 64
        wire_endpoints = set()
        for seg in split_wires:
            wire_endpoints.add((seg[0], seg[1]))
            wire_endpoints.add((seg[2], seg[3]))
        for f in self.asc.flags:
            wire_endpoints.add((f.x, f.y))

        for coord in list(terminal_coords):
            if coord in wire_endpoints:
                continue
            best = None
            best_dist = SNAP_TOLERANCE + 1
            for wp in wire_endpoints:
                dist = abs(coord[0] - wp[0]) + abs(coord[1] - wp[1])
                if dist < best_dist:
                    best_dist = dist
                    best = wp
            if best is not None and best_dist <= SNAP_TOLERANCE:
                union(coord, best)

        # グループ化
        groups: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {}
        for c in all_coords:
            root = find(c)
            groups.setdefault(root, set()).add(c)

        return list(groups.values())

    @staticmethod
    def _split_wire_at_terminals(w: AscWire,
                                  terminals: Set[Tuple[int,int]]
                                  ) -> List[Tuple[int,int,int,int]]:
        """ワイヤを端子位置で分割する

        ワイヤ上に端子がある場合、その位置で切断して
        複数のセグメントに分割する。
        """
        # ワイヤ上にある端子を探す
        on_wire = []
        for tx, ty in terminals:
            # 水平ワイヤ
            if w.y1 == w.y2 == ty:
                min_x = min(w.x1, w.x2)
                max_x = max(w.x1, w.x2)
                if min_x < tx < max_x:  # 端点は除外（厳密に中間のみ）
                    on_wire.append((tx, ty))
            # 垂直ワイヤ
            elif w.x1 == w.x2 == tx:
                min_y = min(w.y1, w.y2)
                max_y = max(w.y1, w.y2)
                if min_y < ty < max_y:
                    on_wire.append((tx, ty))

        if not on_wire:
            return [(w.x1, w.y1, w.x2, w.y2)]

        # 端点と中間点をソートしてセグメントに分割
        points = [(w.x1, w.y1)] + on_wire + [(w.x2, w.y2)]

        if w.y1 == w.y2:
            # 水平: X座標でソート
            points.sort(key=lambda p: p[0])
        else:
            # 垂直: Y座標でソート
            points.sort(key=lambda p: p[1])

        segments = []
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            if p1 != p2:
                segments.append((p1[0], p1[1], p2[0], p2[1]))

        return segments

    @staticmethod
    def _point_on_wire(px: int, py: int, w: AscWire) -> bool:
        """点(px,py)がワイヤw上にあるかチェック（端点含む）"""
        # 水平ワイヤ
        if w.y1 == w.y2 == py:
            min_x = min(w.x1, w.x2)
            max_x = max(w.x1, w.x2)
            return min_x <= px <= max_x
        # 垂直ワイヤ
        if w.x1 == w.x2 == px:
            min_y = min(w.y1, w.y2)
            max_y = max(w.y1, w.y2)
            return min_y <= py <= max_y
        return False

    def _assign_flag_names(self, net_groups: List[Set[Tuple[int,int]]]):
        """FLAGの位置からノード名を割り当て"""
        for flag in self.asc.flags:
            coord = (flag.x, flag.y)
            # このFLAGが所属するグループを探す
            for group in net_groups:
                if coord in group:
                    name = flag.name
                    for c in group:
                        self.coord_to_node[c] = name
                    break

    def _assign_node_names(self, net_groups: List[Set[Tuple[int,int]]]):
        """名前未割り当てのグループに自動名を付ける"""
        for group in net_groups:
            # 既に名前があるか
            existing_name = None
            for c in group:
                if c in self.coord_to_node:
                    existing_name = self.coord_to_node[c]
                    break

            if existing_name is None:
                self._node_counter += 1
                existing_name = f'n{self._node_counter}'

            for c in group:
                if c not in self.coord_to_node:
                    self.coord_to_node[c] = existing_name

    def _get_node_at(self, coord: Tuple[int,int]) -> str:
        """座標のノード名を取得"""
        return self.coord_to_node.get(coord, f'?{coord}')

    def _symbol_to_spice(self, sym: AscSymbol) -> Optional[str]:
        """シンボルをSPICEコンポーネント行に変換"""
        terms = self.asc.get_component_terminals(sym)
        if terms is None or len(terms) == 0:
            return None

        prefix = SYMBOL_TO_SPICE.get(sym.symbol_type)
        if prefix is None:
            prefix = 'X'

        name = sym.inst_name or f'{prefix}?'
        value = sym.value or ''
        model = sym.spice_model or value

        if len(terms) > 3:
            # 多端子素子（サブサーキット/IC）
            nodes = [self._get_node_at(t) for t in terms]
            model = sym.spice_model or sym.symbol_type
            return f'{name} {" ".join(nodes)} {model}'.strip()

        elif len(terms) == 3:
            # 3端子素子 (BJT, MOSFET, JFET)
            node1 = self._get_node_at(terms[0])  # C/D
            node2 = self._get_node_at(terms[1])  # B/G
            node3 = self._get_node_at(terms[2])  # E/S

            if sym.symbol_type in ('npn', 'pnp'):
                # Q C B E model
                return f'{name} {node1} {node2} {node3} {model}'.strip()
            elif sym.symbol_type in ('nmos', 'pmos'):
                # M D G S B model (B=S for simplicity)
                return f'{name} {node1} {node2} {node3} {node3} {model}'.strip()
            elif sym.symbol_type in ('njf', 'pjf'):
                # J D G S model
                return f'{name} {node1} {node2} {node3} {model}'.strip()
            elif sym.symbol_type in ('nigbt',):
                return f'{name} {node1} {node2} {node3} {model}'.strip()
            else:
                return f'{name} {node1} {node2} {node3} {value}'.strip()

        elif len(terms) == 1:
            # 1端子（電源ピン等） — スキップまたは接地
            node1 = self._get_node_at(terms[0])
            return f'{name} {node1} {value}'.strip()

        elif len(terms) == 2:
            node1 = self._get_node_at(terms[0])
            node2 = self._get_node_at(terms[1])

            if sym.symbol_type in ('voltage', 'current', 'battery'):
                return f'{name} {node1} {node2} {value}'.strip()
            elif sym.symbol_type in ('res', 'cap', 'ind', 'ind2', 'polcap'):
                return f'{name} {node1} {node2} {value}'.strip()
            elif sym.symbol_type in ('diode', 'schottky', 'zener', 'led',
                                      'varactor', 'tvs'):
                return f'{name} {node1} {node2} {model}'.strip()
            elif sym.symbol_type in ('e', 'e2', 'g', 'g2'):
                # 制御ソース: 出力端子のみ。制御端子は別途。
                return f'{name} {node1} {node2} {value}'.strip()
            elif sym.symbol_type in ('bv', 'bi', 'bi2'):
                return f'{name} {node1} {node2} {value}'.strip()
            elif sym.symbol_type in ('sw',):
                return f'{name} {node1} {node2} {model}'.strip()
            else:
                return f'{name} {node1} {node2} {value}'.strip()

        return None


# =============================================================================
# 便利関数
# =============================================================================

def asc_to_netlist(filepath: str, asy_search_dirs: Optional[List[Path]] = None) -> str:
    """ASCファイルからネットリストを抽出"""
    parser = AscParser(asy_search_dirs=asy_search_dirs)
    parser.parse_file(filepath)
    extractor = NetlistExtractor(parser)
    return extractor.extract()


def asc_to_cir(asc_path: str, cir_path: str = None,
               asy_search_dirs: Optional[List[Path]] = None) -> str:
    """ASCファイルを.cirファイルに変換"""
    netlist = asc_to_netlist(asc_path, asy_search_dirs=asy_search_dirs)
    if cir_path is None:
        cir_path = str(Path(asc_path).with_suffix('.cir'))
    with open(cir_path, 'w', encoding='utf-8') as f:
        f.write(netlist)
    return netlist


def classify_asc(filepath: str) -> dict:
    """ASCファイルを解析して分類情報を返す"""
    parser = AscParser()
    try:
        parser.parse_file(filepath)
    except Exception as e:
        return {'error': str(e), 'parseable': False}

    sym_types = parser.get_symbol_types()
    passive_only = parser.has_only_passives()

    supported_only = all(st in SUPPORTED_SYMBOLS for st in sym_types)

    return {
        'parseable': True,
        'num_symbols': len(parser.symbols),
        'num_wires': len(parser.wires),
        'num_flags': len(parser.flags),
        'num_texts': len(parser.texts),
        'symbol_types': sorted(sym_types),
        'passive_only': passive_only,
        'supported_only': supported_only,
        'has_directives': any(t.is_directive for t in parser.texts),
    }


# =============================================================================
# テスト
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        print(f"Parsing: {filepath}")
        print(asc_to_netlist(filepath))
    else:
        # テスト: 既存の変換例のASCをパース
        test_asc = r"s:\LTSpice\01_GitHub\examples\00_converter\01_rc_lowpass\test_rc_lowpass.asc"
        print(f"=== Parsing reference ASC ===")
        print(f"File: {test_asc}")
        netlist = asc_to_netlist(test_asc)
        print(netlist)

        print(f"\n=== Classification ===")
        info = classify_asc(test_asc)
        for k, v in info.items():
            print(f"  {k}: {v}")

"""Accent fold tieng Viet ve ASCII thuong + tach token.

Day la noi DUY NHAT trong repo biet ve 'đ' (docs/code-standards.md:42,
docs/system-architecture.md Components). Ham thuan tuy, khong biet gi ve HTTP.
"""

import re
import unicodedata

# unicodedata.normalize("NFD", ...) tach dau thanh cac ky tu category Mn
# roi rac, nhung KHONG dung toi 'đ'/'Đ' - hai ky tu nay khong thuoc category
# Mn (chung la chu cai rieng trong Unicode, khong phai chu cai + dau ket
# hop). Neu chi bo Mn ma khong map rieng 'đ' -> 'd', ket qua fold van con
# sot 'đ', hong tham lang 8/20 san pham trong data/products.json (vi du
# "Ngăn đá" fold ra "ngan đa" thay vi "ngan da", nguoi dung go "ngan da"
# nhan 0 ket qua ma khong co loi nao bao). Phai map rieng SAU buoc bo Mn.
_D_STROKE_MAP = {"đ": "d", "Đ": "d"}

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def fold(s: str) -> str:
    """Chuan hoa chuoi tieng Viet ve ASCII thuong.

    Thu tu bat buoc (docs/system-architecture.md:73): NFD -> bo Mn -> map
    'đ'/'Đ' -> 'd' -> lowercase. Buoc map 'đ' phai dung SAU buoc bo Mn vi
    'đ' khong thuoc category Mn nen khong bi NFD dung toi.
    """
    decomposed = unicodedata.normalize("NFD", s)
    without_marks = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    with_d_stroke_mapped = "".join(
        _D_STROKE_MAP.get(char, char) for char in without_marks
    )
    return with_d_stroke_mapped.lower()


def tokenize(s: str) -> set[str]:
    """Fold roi tach token theo [a-z0-9]+.

    Tra ve set, khong phai list - thu tu token khong mang nghia, buoc khop o
    phase 4 la phep so tap hop.
    """
    return set(_TOKEN_PATTERN.findall(fold(s)))

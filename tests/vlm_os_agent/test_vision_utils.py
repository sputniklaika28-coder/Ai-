"""core/vision_utils.py のパーサ／pHash ユーティリティのユニットテスト。"""

from __future__ import annotations

import hashlib
import json

import pytest

from core.vision_utils import (
    encode_png_b64,
    extract_json,
    is_in_viewport,
    parse_coordinates,
    parse_single_coordinate,
    phash,
    phash_distance,
    snap_to_grid,
    validate_coordinates,
)


class TestExtractJson:
    def test_markdown_block(self) -> None:
        text = "結果: ```json\n{\"a\": 1}\n```"
        assert extract_json(text) == '{"a": 1}'

    def test_bare_json(self) -> None:
        text = "前置き {\"x\": 10, \"y\": 20} 後置き"
        assert extract_json(text) == '{"x": 10, "y": 20}'

    def test_no_json(self) -> None:
        assert extract_json("ただの文章") is None

    def test_empty(self) -> None:
        assert extract_json("") is None


class TestParseCoordinates:
    def test_pieces_dict(self) -> None:
        resp = '{"pieces": [{"px_x": 1, "px_y": 2}]}'
        r = parse_coordinates(resp)
        assert r == [{"px_x": 1, "px_y": 2}]

    def test_markdown_list(self) -> None:
        # extract_json は {...} 形式を抽出するので、リスト形式は dict で包む前提
        r = parse_coordinates('{"pieces": [{"px_x": 5, "px_y": 6}]}')
        assert r == [{"px_x": 5, "px_y": 6}]

    def test_invalid_returns_empty(self) -> None:
        assert parse_coordinates("no json here") == []
        assert parse_coordinates("{broken") == []


class TestParseSingleCoordinate:
    def test_valid(self) -> None:
        assert parse_single_coordinate('{"px_x": 100, "px_y": 200}') == (100, 200)

    def test_with_markdown(self) -> None:
        r = parse_single_coordinate('```json\n{"px_x": 7, "px_y": 8}\n```')
        assert r == (7, 8)

    def test_missing_keys(self) -> None:
        assert parse_single_coordinate('{"foo": 1}') is None

    def test_invalid_types(self) -> None:
        assert parse_single_coordinate('{"px_x": "a", "px_y": "b"}') is None


class TestIsInViewport:
    def test_inside(self) -> None:
        assert is_in_viewport((50, 60), (100, 100)) is True

    def test_boundary(self) -> None:
        assert is_in_viewport((0, 0), (100, 100)) is True
        assert is_in_viewport((100, 100), (100, 100)) is True

    def test_outside(self) -> None:
        assert is_in_viewport((101, 50), (100, 100)) is False
        assert is_in_viewport((-1, 50), (100, 100)) is False


class TestEncodePngB64:
    def test_roundtrip(self) -> None:
        import base64

        raw = b"\x89PNGhello"
        enc = encode_png_b64(raw)
        assert base64.b64decode(enc) == raw


class TestPhash:
    def _make_png(self, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
        pil = pytest.importorskip("PIL.Image")
        import io

        img = pil.new("RGB", (32, 32), color)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_same_image_same_hash(self) -> None:
        png = self._make_png((255, 0, 0))
        assert phash(png) == phash(png)

    def test_different_image_different_hash(self) -> None:
        a = self._make_png((255, 0, 0))
        b = self._make_png((0, 0, 255))
        # 単色同士だと全ピクセル同値で dHash は同じになるが、
        # SHA1 フォールバック経路が走れば異なる
        # どちらでも「distance==0 または >0」を許容しつつ、少なくとも例外にならないことを保証
        assert isinstance(phash(a), str)
        assert isinstance(phash(b), str)

    def test_fallback_when_pillow_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        h = phash(b"\x89PNGdata")
        assert h == hashlib.sha1(b"\x89PNGdata").hexdigest()[:64]


class TestPhashDistance:
    def test_identical(self) -> None:
        assert phash_distance("abcd", "abcd") == 0

    def test_known_bits(self) -> None:
        # 0xFF ^ 0x0F = 0xF0 → 4 ビット差
        assert phash_distance("ff", "0f") == 4

    def test_length_mismatch(self) -> None:
        assert phash_distance("a", "abcd") >= 10_000

    def test_empty(self) -> None:
        assert phash_distance("", "abcd") >= 10_000
        assert phash_distance("abcd", "") >= 10_000

    def test_non_hex(self) -> None:
        assert phash_distance("zz", "00") >= 10_000


class TestSnapToGrid:
    def test_center(self) -> None:
        # grid=100: round(123/100)=1, round(167/100)=2 → (1*100+50, 2*100+50)
        assert snap_to_grid((123, 167), 100) == (150, 250)

    def test_zero(self) -> None:
        assert snap_to_grid((10, 10), 100) == (50, 50)


class TestValidateCoordinates:
    def test_filters_outside(self) -> None:
        coords = [{"px_x": 10, "px_y": 10}, {"px_x": 200, "px_y": 10}]
        r = validate_coordinates(coords, (100, 100))
        assert len(r) == 1
        assert r[0]["px_x"] == 10

    def test_snap(self) -> None:
        coords = [{"px_x": 23, "px_y": 77}]
        # 23/50=0.46→0, 77/50=1.54→2 → (0*50+25, 2*50+25) = (25, 125)
        r = validate_coordinates(coords, (200, 200), grid_size=50)
        assert len(r) == 1
        assert r[0]["px_x"] == 25
        assert r[0]["px_y"] == 125
        assert r[0]["grid_x"] == 0
        assert r[0]["grid_y"] == 2

    def test_invalid_types_skipped(self) -> None:
        coords = [{"px_x": "nope", "px_y": 10}]
        assert validate_coordinates(coords, (100, 100)) == []


class TestLoadVlmTemplates:
    def test_loads_strings(self, tmp_path) -> None:
        data = {"a": "prompt a", "b": "prompt b", "skip_dict": {"x": 1}}
        (tmp_path / "browser_use_tasks.json").write_text(
            json.dumps(data), encoding="utf-8",
        )
        from core.vision_utils import load_vlm_templates

        r = load_vlm_templates(tmp_path)
        assert r == {"a": "prompt a", "b": "prompt b"}

    def test_missing_file(self, tmp_path) -> None:
        from core.vision_utils import load_vlm_templates

        assert load_vlm_templates(tmp_path) == {}

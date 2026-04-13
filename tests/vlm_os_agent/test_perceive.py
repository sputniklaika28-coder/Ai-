"""perceive.py のユニットテスト。"""

from __future__ import annotations

import io

import pytest


def _sample_png(size: int = 64) -> bytes:
    pytest.importorskip("PIL.Image")
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # 複数のクリック候補矩形（輪郭がはっきりした要素）
    draw.rectangle([(10, 10), (30, 30)], fill=(0, 0, 0))
    draw.rectangle([(35, 35), (55, 55)], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestAnnotate:
    def test_passthrough_none(self) -> None:
        from addons.vlm_os_agent.perceive import annotate

        src = _sample_png()
        result = annotate(src, backend="none")
        assert result.annotated_png == src
        assert result.marks == []

    def test_unknown_backend_falls_back(self) -> None:
        from addons.vlm_os_agent.perceive import annotate

        src = _sample_png()
        result = annotate(src, backend="something_weird")
        assert result.annotated_png == src
        assert result.marks == []

    def test_cv_backend_requires_cv2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenCV 未導入環境でフォールバック。"""
        from addons.vlm_os_agent import perceive

        def bad_import() -> None:
            raise perceive.PerceiveUnavailable("mocked")

        monkeypatch.setattr(perceive, "_annotate_cv", lambda *a, **kw: bad_import())
        src = _sample_png()
        result = perceive.annotate(src, backend="cv")
        assert result.annotated_png == src
        assert result.marks == []


class TestCvBackend:
    def test_detects_rectangles(self) -> None:
        cv2 = pytest.importorskip("cv2")
        pytest.importorskip("numpy")
        pytest.importorskip("PIL.Image")
        _ = cv2  # ensure linter happy

        from addons.vlm_os_agent.perceive import annotate, find_mark

        src = _sample_png()
        result = annotate(src, backend="cv", max_marks=10)
        assert len(result.marks) >= 1
        # 重ね絵のバイト列が変わっていること
        assert result.annotated_png != src
        # find_mark で ID 逆引きが通ること
        first = result.marks[0]
        assert find_mark(result.marks, first.id) is first
        assert find_mark(result.marks, 999) is None

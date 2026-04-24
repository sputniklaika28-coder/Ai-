# ================================
# ファイル: core/char_maker.py
# 旧 core/char_maker.py は addons/tactical_exorcist/char_maker.py に統合された。
# このファイルは後方互換のためのエントリポイントシムとして残している。
#
# 直接起動:
#   python core/char_maker.py
#
# 上記は addons/tactical_exorcist/char_maker.py::TacticalExorcistCharMaker を
# そのまま起動する。`world_setting_compressed.txt` とリファレンスシート
# `configs/reference_character.json` をプロンプトに丸ごと注入する新方式のみを
# 使用する（旧 VTTCharMakerApp は完全廃止）。
# ================================

from __future__ import annotations

import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from addons.tactical_exorcist.char_maker import TacticalExorcistCharMaker


# 後方互換: 旧クラス名 VTTCharMakerApp を参照しているコードのためのエイリアス
VTTCharMakerApp = TacticalExorcistCharMaker


if __name__ == "__main__":
    app = TacticalExorcistCharMaker()
    app.mainloop()

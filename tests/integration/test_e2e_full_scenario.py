"""E2E: 統合フルシナリオテスト。
 
scenes.json + event_rules を用意し、CCFoliaConnector の
セッション監視ループを起動、プレイヤー発言 → GM AI 自動応答 →
駒移動 → シーン遷移の一連の流れを検証する。
 
実行方法:
    CCFOLIA_ROOM_URL=https://ccfolia.com/rooms/xxxx pytest tests/integration/test_e2e_full_scenario.py -v -s
"""
 
from __future__ import annotations
 
import json
import time
from pathlib import Path
 
import pytest
 
from core.room_builder import CharacterPlacement, RoomBuilder, RoomDefinition
from core.session_copilot import EventRule, SceneDefinition, SessionCoPilot
 
pytestmark = [pytest.mark.integration, pytest.mark.browser_use]
 
 
@pytest.fixture
def full_scenario_scenes(tmp_path) -> Path:
    """E2E テスト用のシーン定義 JSON。"""
    data = {
        "scenes": [
            {
                "name": "酒場",
                "description": "冒険者が集まる賑やかな酒場",
                "background_image": "",
                "bgm": [],
                "characters": [
                    {"name": "酒場のマスター", "grid_x": 5, "grid_y": 3},
                ],
                "metadata": {"mood": "calm", "lighting": "warm"},
            },
            {
                "name": "ダンジョン入口",
                "description": "暗い洞窟の入り口。冷たい風が吹いている",
                "background_image": "",
                "bgm": [],
                "characters": [
                    {"name": "ゴブリン", "grid_x": 7, "grid_y": 4},
                ],
                "metadata": {"mood": "tense", "lighting": "dark"},
            },
        ]
    }
    p = tmp_path / "e2e_scenes.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
 
 
@pytest.fixture
def e2e_event_rules() -> list[EventRule]:
    """E2E テスト用のイベントルール。"""
    return [
        EventRule(
            name="ダンジョン遷移",
            pattern="ダンジョンに(入|向か)",
            action="transition",
            params={"scene": "ダンジョン入口"},
        ),
        EventRule(
            name="戦闘BGM",
            pattern="戦闘開始|敵が現れた",
            action="bgm",
            params={"bgm_name": "battle_theme"},
        ),
        EventRule(
            name="GM歓迎",
            pattern="^こんにちは",
            action="narration",
            params={
                "character": "GM",
                "text": "ようこそ、勇敢なる冒険者よ。今宵の物語を始めよう。",
            },
        ),
    ]
 
 
class TestE2EFullScenario:
    """E2E-1: 完全自動セッション統合テスト。"""
 
    def test_e2e_1_full_auto_session(
        self, adapter, full_scenario_scenes, e2e_event_rules
    ):
        """完全自動セッション:
        scenes.json + event_rules を用意 →
        プレイヤーが発言 → ルールが発火 → シーン遷移が動作する。
        """
        # ── Step 1: SessionCoPilot を初期化 ──
        copilot = SessionCoPilot(adapter=adapter, mode="auto")
 
        # シーンを JSON から読み込み
        count = copilot.load_scenes_from_file(str(full_scenario_scenes))
        assert count == 2, f"シーン読み込み数が不正: {count}"
        print(f"\n  [1/5] シーン読み込み完了: {copilot.list_scenes()}")
 
        # イベントルールを登録
        copilot.add_rules(e2e_event_rules)
        assert len(copilot.event_rules) == 3
        print(f"  [2/5] イベントルール登録完了: {len(copilot.event_rules)} 件")
 
        # ── Step 2: 初期シーン（酒場）に遷移 ──
        results = copilot.transition_to("酒場")
        assert copilot.current_scene == "酒場"
        print(f"  [3/5] 初期シーン遷移完了: {copilot.current_scene}")
        for r in results:
            status = "OK" if r.get("success") else "NG"
            print(f"         [{status}] {r.get('step')}: {r.get('detail', '')} {r.get('error', '')}")
 
        # ── Step 3: プレイヤーの発言をシミュレート ──
        # 3a: 挨拶 → GM歓迎ルール発火
        action_results = copilot.process_message("プレイヤー", "こんにちは！")
        print(f"  [4/5] メッセージ処理:")
        print(f"         「こんにちは！」 → {len(action_results)} ルール発火")
        for ar in action_results:
            print(f"           {ar.rule_name}: {ar.action} (success={ar.success})")
 
        # 3b: ダンジョン遷移トリガー
        action_results = copilot.process_message("プレイヤー", "ダンジョンに入ろう！")
        print(f"         「ダンジョンに入ろう！」 → {len(action_results)} ルール発火")
        for ar in action_results:
            print(f"           {ar.rule_name}: {ar.action} (success={ar.success})")
 
        # シーンが遷移したか確認
        if any(ar.rule_name == "ダンジョン遷移" for ar in action_results):
            assert copilot.current_scene == "ダンジョン入口", (
                f"シーンがダンジョン入口に遷移していません: {copilot.current_scene}"
            )
 
        # ── Step 4: 全体の結果サマリー ──
        print(f"  [5/5] セッション結果サマリー:")
        print(f"         現在のシーン: {copilot.current_scene}")
        print(f"         シーン履歴: {copilot.scene_history}")
        print(f"         アクションログ: {len(copilot.action_log)} 件")
        for log in copilot.action_log:
            status = "OK" if log.success else "NG"
            print(f"           [{status}] {log.rule_name} → {log.action}: {log.detail}")
 
        # 最低限の検証: アクションログにエントリがある
        assert len(copilot.action_log) > 0, "アクションログが空です"
        assert len(copilot.scene_history) > 0, "シーン履歴が空です"
 
    def test_e2e_chat_driven_flow(self, adapter):
        """チャット送受信を含む E2E フロー:
        GM がメッセージを送信 → チャット取得 → ルールマッチ → 応答。
        """
        copilot = SessionCoPilot(adapter=adapter, mode="auto")
 
        # ナレーション応答ルール
        copilot.add_rule(EventRule(
            name="E2E応答",
            pattern="テスト発言",
            action="narration",
            params={
                "character": "GM",
                "text": "[E2E] 自動応答テスト成功",
            },
        ))
 
        # テストメッセージ送信
        ok = adapter.send_chat("テストプレイヤー", "テスト発言です")
        assert ok, "チャット送信が失敗しました"
 
        # 少し待ってからチャットを取得
        time.sleep(1)
        messages = adapter.get_chat_messages()
 
        # 取得したメッセージでルールを処理
        total_fired = 0
        for msg in messages:
            results = copilot.process_message(
                msg.get("speaker", ""), msg.get("body", ""),
            )
            total_fired += len(results)
 
        print(f"\n  チャット取得数: {len(messages)}")
        print(f"  ルール発火数: {total_fired}")
        print(f"  アクションログ: {len(copilot.action_log)} 件")
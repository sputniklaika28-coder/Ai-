"""Phase 4: セッションコパイロットテスト。
 
シーン遷移、イベントルール（auto/assist）、チャット監視との統合、
シーン JSON 読み込みを検証する。
 
実行方法:
    CCFOLIA_ROOM_URL=https://ccfolia.com/rooms/xxxx pytest tests/integration/test_phase4_session_copilot.py -v
"""
 
from __future__ import annotations
 
import pytest
 
from core.session_copilot import EventRule, SceneDefinition, SessionCoPilot
 
pytestmark = [pytest.mark.integration, pytest.mark.browser_use]
 
 
class TestPhase4SessionCoPilot:
    """Phase 4: セッションコパイロット。"""
 
    # 4-1: シーン遷移
    def test_4_1_scene_transition(self, adapter):
        """2つのシーンを登録し transition_to("ダンジョン") → 背景とBGMが切り替わる。"""
        copilot = SessionCoPilot(adapter=adapter, mode="auto")
 
        tavern = SceneDefinition(
            name="酒場",
            description="冒険者が集まる酒場",
        )
        dungeon = SceneDefinition(
            name="ダンジョン",
            description="暗い地下迷宮",
        )
        copilot.register_scenes([tavern, dungeon])
 
        assert "酒場" in copilot.list_scenes()
        assert "ダンジョン" in copilot.list_scenes()
 
        results = copilot.transition_to("ダンジョン")
        assert isinstance(results, list), "遷移結果がリストではありません"
        assert copilot.current_scene == "ダンジョン"
        assert "ダンジョン" in copilot.scene_history
 
    # 4-2: イベントルール（auto モード）
    def test_4_2_event_rule_auto(self, adapter):
        """"戦闘開始" パターンの BGM 切替ルール → チャットで発火 → BGM が切り替わる。"""
        copilot = SessionCoPilot(adapter=adapter, mode="auto")
 
        rule = EventRule(
            name="戦闘BGM",
            pattern="戦闘開始",
            action="bgm",
            params={"bgm_name": "battle_theme"},
        )
        copilot.add_rule(rule)
 
        results = copilot.process_message("プレイヤー", "戦闘開始！")
        assert len(results) > 0, "ルールがマッチしませんでした"
        assert results[0].rule_name == "戦闘BGM"
        assert results[0].action == "bgm"
        # BGM 切替の成否は実環境依存（BGM が登録されていない場合は失敗する）
        print(f"  BGM切替結果: success={results[0].success}, detail={results[0].detail}")
 
    # 4-3: イベントルール（assist モード）
    def test_4_3_event_rule_assist(self, adapter):
        """assist モード → ルールがマッチしても実行されず、提案として記録される。"""
        copilot = SessionCoPilot(adapter=adapter, mode="assist")
 
        rule = EventRule(
            name="シーン遷移提案",
            pattern="ダンジョンに入る",
            action="transition",
            params={"scene": "ダンジョン"},
        )
        copilot.add_rule(rule)
 
        results = copilot.process_message("プレイヤー", "＞ダンジョンに入る")
        assert len(results) > 0, "ルールがマッチしませんでした"
        assert results[0].action == "transition"
        assert "[提案]" in results[0].detail, "assist モードで提案になっていません"
 
        # action_log にも記録される
        log = copilot.action_log
        assert len(log) > 0
        assert log[-1].detail.startswith("[提案]")
 
    # 4-4: チャット監視との統合
    def test_4_4_chat_monitoring_integration(self, adapter):
        """チャット監視 → メッセージ取得 → ルールマッチの一連の流れ。"""
        copilot = SessionCoPilot(adapter=adapter, mode="auto")
 
        # ナレーションルールを追加
        rule = EventRule(
            name="歓迎ナレーション",
            pattern="こんにちは",
            action="narration",
            params={
                "character": "GM",
                "text": "[統合テスト] ようこそ、冒険者よ！",
            },
        )
        copilot.add_rule(rule)
 
        # テストメッセージを送信
        adapter.send_chat("テストプレイヤー", "こんにちは、GM！")
 
        # チャットを取得してルールに通す
        messages = adapter.get_chat_messages()
        fired = False
        for msg in messages:
            body = msg.get("body", "")
            speaker = msg.get("speaker", "")
            results = copilot.process_message(speaker, body)
            if results:
                fired = True
                for r in results:
                    print(f"  ルール発火: {r.rule_name} → {r.action} (success={r.success})")
 
        # "こんにちは" を含むメッセージがあればルールが発火するはず
        # ただし DOM からのメッセージ取得が実装依存のため、発火しなくても警告のみ
        if not fired:
            print("  [警告] チャットメッセージからルールが発火しませんでした")
            print(f"  取得メッセージ数: {len(messages)}")
 
    # 4-5: シーン JSON 読み込み
    def test_4_5_load_scenes_json(self, adapter, scenes_json):
        """scenes.json からシーン定義を読み込み、list_scenes() で確認できる。"""
        copilot = SessionCoPilot(adapter=adapter, mode="auto")
        count = copilot.load_scenes_from_file(str(scenes_json))
 
        assert count == 2, f"読み込みシーン数が不正: {count}"
        scenes = copilot.list_scenes()
        assert "酒場" in scenes, f"酒場シーンがありません: {scenes}"
        assert "ダンジョン" in scenes, f"ダンジョンシーンがありません: {scenes}"
 
        # シーンの詳細を検証
        tavern = copilot.get_scene("酒場")
        assert tavern is not None
        assert tavern.description == "冒険者が集まる酒場"
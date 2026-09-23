import core.harness as harness


class _AlwaysRefuses:
    class chat:
        class completions:
            @staticmethod
            def parse(**kwargs):
                class _Msg:
                    refusal = "판정 불가"
                    parsed = None
                class _Choice:
                    message = _Msg()
                class _Completion:
                    choices = [_Choice()]
                return _Completion()


def test_judge_faithfulness_fail_open_reports_no_score():
    result = harness.judge_faithfulness(_AlwaysRefuses(), context="문맥", answer="답변")
    assert result["pass"] is True
    assert result["score"] is None  # main.py는 이 None으로 verified=False를 판단한다

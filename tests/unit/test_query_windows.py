from code_harness.application.context.query_windows import (
    render_windows,
    select_query_windows,
)


def test_select_query_windows_prefers_commit_and_rollback_regions() -> None:
    content = "".join(
        [
            "public Double criarEnvelope(ISession session) {\n",
            "    // setup params\n",
            "    setParam(1);\n",
            "    setParam(2);\n",
            "    setParam(3);\n",
            "    setParam(4);\n",
            "    setParam(5);\n",
            "    setParam(6);\n",
            "    setParam(7);\n",
            "    setParam(8);\n",
            "    execute();\n",
            "    session.commit();\n",
            "    return value;\n",
            "}\n",
        ]
    )

    windows = select_query_windows(
        content=content,
        start_line=1,
        end_line=14,
        query="criarEnvelope commit rollback",
    )
    rendered, start, end = render_windows(content, windows)

    assert "criarEnvelope" in rendered
    assert "session.commit()" in rendered
    assert start == 1
    assert end >= 12

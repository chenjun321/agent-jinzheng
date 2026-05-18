from app.document.text_normalizer import TextNormalizer


class DummySettings:
    values = {
        "document.text_cleanup.enabled": True,
        "document.text_cleanup.merge_orphan_lines": True,
        "document.text_cleanup.merge_wrapped_lines": True,
        "document.text_cleanup.orphan_line_max_chars": 3,
        "document.text_cleanup.short_line_max_chars": 24,
        "document.text_cleanup.quote_noise_line_max_chars": 12,
    }

    def cfg(self, path: str, default=None):
        return self.values.get(path, default)


def test_removes_unwanted_spaces_between_chinese_characters():
    result = TextNormalizer(DummySettings()).normalize_page_text("中 华 人 民 共 和 国 国 家 标 准")

    assert result.text == "中华人民共和国国家标准"
    assert result.stats["cjk_space_fixes"] > 0


def test_normalizes_standard_numbers_dates_and_dashes():
    text = "GB / T 1568 一 2008\n2008-09-22 发 布 2009-05-01 实 施\nICS 21. 120. 30"
    result = TextNormalizer(DummySettings()).normalize_page_text(text)

    assert "GB/T 1568-2008" in result.text
    assert "2008-09-22 发布 2009-05-01 实施" in result.text
    assert "ICS 21.120.30" in result.text
    assert result.stats["standard_number_fixes"] > 0


def test_keeps_table_separators_with_multiple_spaces():
    text = "项目  名称  公差\n键 宽  b  10 mm"
    result = TextNormalizer(DummySettings()).normalize_page_text(text)

    assert "项目  名称  公差" in result.text
    assert "键宽  b  10 mm" in result.text


def test_merges_short_orphan_chinese_line():
    result = TextNormalizer(DummySettings()).normalize_page_text("键 技术 条\n件")

    assert result.text == "键技术条件"
    assert result.stats["orphan_line_merges"] == 1


def test_merges_short_wrapped_continuation_line():
    result = TextNormalizer(DummySettings()).normalize_page_text("合格判定\n数 Ac")

    assert "合格判定数 Ac" in result.text
    assert result.stats["wrapped_line_merges"] == 1


def test_removes_stray_quotes_between_chinese_characters():
    result = TextNormalizer(DummySettings()).normalize_page_text("键 ”技术条件")

    assert result.text == "键技术条件"
    assert result.stats["quote_noise_fixes"] == 1


def test_keeps_normal_quotes_in_long_body_lines():
    text = "本标准在范围中增加 “ 除花键外的各种键 ” 的说明"
    result = TextNormalizer(DummySettings()).normalize_page_text(text)

    assert "增加“除花键外的各种键”" in result.text

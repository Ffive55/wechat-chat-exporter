"""Inspect real browser-printed PDFs; pypdf is a verification-only dependency."""
from pypdf import PdfReader


def verify_pdf_content(path):
    reader = PdfReader(path)
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    compact = ''.join(text.split())
    for token in ('开始边界', '结束边界', '长消息开始', '长消息结束',
                  '视频号真实内容验证', '视频号回退标题验证', '直播真实内容验证',
                  '小程序真实内容验证', '礼物', '被引用的原始消息'):
        assert token in compact, f'PDF 缺少正文：{token}'
    for number in range(1, 106):
        assert f'第{number:03d}行' in compact, f'PDF 长消息缺少第 {number} 行'
    assert '请升级' not in compact and '不支持展示' not in compact, 'PDF 包含升级占位'
    links = []
    for page in reader.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        assert abs(width - 595.28) < 2 and abs(height - 841.89) < 2, (width, height)
        for reference in page.get('/Annots', []):
            annotation = reference.get_object()
            action = annotation.get('/A')
            if action:
                action = action.get_object()
                if action.get('/URI'):
                    links.append(str(action['/URI']))
    assert not any('support.weixin.qq.com' in url for url in links), links
    assert 'https://example.com/video?key=fixture&token=test' in links, links
    return {'pages': len(reader.pages), 'characters': len(text), 'links': links}

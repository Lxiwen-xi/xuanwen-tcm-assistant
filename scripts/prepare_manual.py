"""保留原始 PDF；将已记录的重点校对写入检索文本。不是临床审校工具。"""
import hashlib
import json
from pathlib import Path
import re
import sys
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.storage import Store

SOURCES = {
    'four': 'https://www.samr.gov.cn/bzjss/bzjd/art/2022/art_d96f4e909dd04ed082b20b5fdf87e84a.html',
    'eight': 'https://higher.smartedu.cn/course/69e7eca36c1cfb58c38c7f46',
    'aconite': 'https://www.cmro.gov.hk/html/gb/useful_information/public_health/pamphlet/The_safe_use_of_Chinese_medicines_containing_aconitum_alkaloids.html',
    'adverse': 'https://www.cmro.gov.hk/html/gb/useful_information/public_health/pamphlet/Adverse_Reactions_of_CM.html',
    'angong': 'https://www.ihchina.cn/project_details/14822.html',
    'diet': 'https://www.niddk.nih.gov/health-information/kidney-disease/chronic-kidney-disease-ckd/healthy-eating-adults-chronic-kidney-disease',
}


def prepare(pdf_path):
    reader = PdfReader(pdf_path)
    pages = [(p.extract_text() or '') for p in reader.pages]
    changes = []

    def replace(page, pattern, new, issue, source):
        updated, count = re.subn(pattern, new, pages[page - 1])
        if count != 1:
            raise ValueError(f'第{page}页校对定位不唯一：{issue}；匹配{count}处')
        pages[page - 1] = updated
        changes.append(dict(page=page, issue=issue, source=SOURCES[source]))

    replace(17, r'表热属实属阳、里寒属虚属阴',
        '表、热、实通常归阳，里、寒、虚通常归阴；各纲是不同维度，不能认为表证必为实证或里证必为虚证',
        '澄清八纲是不同维度，修订原概括中容易误读的语句', 'eight')
    replace(23, r'附子等有毒药须久煎\s*口尝无麻',
        '附子等有毒中药的炮制、煎煮及使用须遵从执业中医师与药师指导，不得自行尝药判断安全',
        '移除面向普通用户的尝药判定表述', 'aconite')
    replace(27, r'须炮制、先煎久煎至口尝无麻',
        '须由专业人员指导炮制、煎煮与使用，不得自行尝药判断安全',
        '修订附子条目中的同类表述', 'aconite')
    replace(34, r'生用研粉嚼服；平和；无特殊禁忌。',
        '仅作传统用药知识列举；本手册未完整列出禁忌及相互作用，不能视为对所有人安全；寄生虫感染应经确诊和规范治疗。',
        '移除未经核实的绝对安全说法及自行使用指引；不判定此药必有某项禁忌', 'adverse')
    replace(42, r'朱砂、珍珠、郁金、冰片等（凉开）',
        '朱砂、雄黄、珍珠、郁金、冰片等（凉开；需核对具体药品说明书，不能据此自行配药）',
        '成分列举补充雄黄；原表为简列，不是完整产品说明书', 'angong')
    replace(71, r'慢病（高血压、糖尿病、冠心病、肾病、肝病、痛风等）：中医调护须服从相应现代医学基础治疗与膳\s*食要求（限盐、限糖、限嘌呤、限蛋白等），不得用食疗替代。',
        '不同慢性病的膳食要求不同，应依具体疾病、肾功能、是否透析、营养状态及治疗方案制定；不能对所有慢病统一限制蛋白质、糖或嘌呤。食疗不得替代规范治疗。',
        '澄清并列限制项目不能直接应用于所有慢病患者', 'diet')

    # 封面、目录和 Agent 操作建议不作医学知识检索；保留原页码。
    excluded = {1, 2, 3, 4, 72, 73, 77}
    sections = []
    for n, page in enumerate(pages, 1):
        if n in excluded:
            continue
        page = re.sub(r'^中医智能问诊知识手册（Agent 知识检索用）\s*第\s*\d+\s*页\s*', '', page).strip()
        # 第74页的后半部分是术语；急症处理操作段不进入资料问答。
        if n == 74:
            page = page[page.index('第40章 常用中医术语'):]
        sections.append(f'【第{n}页】\n' + page)
    content = '\n\n'.join(sections)
    return content, dict(checked_at='2026-11-03', review='关键条目事实核对；其余未逐条医学审校',
        changes=changes, sources=SOURCES, excluded_pages=sorted(excluded), indexed_pages=len(sections),
        note='传统理论条目按中医知识体系阅读；教材式表述不等于疗效获得临床验证。')


def main():
    folder = ROOT / 'data' / 'reference_documents'
    pdf = folder / '中医智能问诊知识手册.pdf'
    original_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    content, audit = prepare(pdf)
    manifest = json.loads((folder / 'manual-import.json').read_text(encoding='utf-8'))
    store = Store(ROOT / 'data' / 'qiwen.db')
    doc = next(d for d in store.documents() if d['id'] == manifest['document_id'])
    (folder / 'manual-corrected.txt').write_text(content, encoding='utf-8')
    audit['original_sha256'] = original_hash
    audit['indexed_text_sha256'] = hashlib.sha256(content.encode()).hexdigest()
    (folder / 'manual-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    doc.update(title='中医智能问诊知识手册（关键条目校对版）', content=content,
        version='v1.1 · 关键条目校对/其余待核验 · 仅知识查询 · 未经临床审核', active=0)
    # 校对先落库但保持停用；测试通过后再由现有启用 API 生效。
    store.put_document(doc)
    manifest.update(active=False, status='关键条目校对版已准备，等待软件验证', audit='manual-audit.json')
    (folder / 'manual-import.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == original_hash
    print(json.dumps(dict(changes=len(audit['changes']), pages=audit['indexed_pages'],
        chars=len(content), original_unchanged=True, active=False), ensure_ascii=False))


if __name__ == '__main__':
    main()

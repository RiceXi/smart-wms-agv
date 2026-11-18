from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "仓储WMS与AGV调度开发计划与分工.docx"

BLACK = RGBColor(0, 0, 0)
DARK_BLUE = "24475F"
LIGHT_BLUE = "EAF2F7"
WHITE = "FFFFFF"
BORDER = "D9D9D9"
TEXT = RGBColor(31, 41, 55)
MUTED = RGBColor(75, 85, 99)


def set_run_font(run, name="Microsoft YaHei", size=10.5, bold=None, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)


def style_paragraph(paragraph, size=10.5, bold=False, color=TEXT, line=1.25):
    paragraph.paragraph_format.line_spacing = line
    paragraph.paragraph_format.space_after = Pt(6)
    for run in paragraph.runs:
        set_run_font(run, size=size, bold=bold, color=color)


def add_text(doc, text, size=10.5, bold=False, color=TEXT, align=None, after=6):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = 1.25
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)
    return paragraph


def add_heading(doc, text, level=1):
    paragraph = doc.add_heading(level=level)
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    paragraph.paragraph_format.space_after = Pt(6)
    run = paragraph.add_run(text)
    set_run_font(run, size=16 if level == 1 else 12.5, bold=True, color=BLACK)
    return paragraph


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=90, start=120, bottom=90, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def remove_paragraph_borders(paragraph):
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is not None:
        p_pr.remove(borders)


def remove_style_borders(style):
    p_pr = style._element.pPr
    if p_pr is not None:
        borders = p_pr.find(qn("w:pBdr"))
        if borders is not None:
            p_pr.remove(borders)


def set_table_borders(table, color=BORDER, size="6"):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), size)
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_repeat_table_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def prevent_row_split(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)


def set_cell_width(cell, width_inches):
    cell.width = Inches(width_inches)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(width_inches * 1440)))
    tc_w.set(qn("w:type"), "dxa")


def add_table(doc, headers, rows, widths, font_size=9.2, header_size=9.2):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_table_borders(table)

    header = table.rows[0]
    set_repeat_table_header(header)
    prevent_row_split(header)
    for idx, text in enumerate(headers):
        cell = header.cells[idx]
        set_cell_width(cell, widths[idx])
        set_cell_shading(cell, DARK_BLUE)
        set_cell_margins(cell)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_after = Pt(0)
        run = paragraph.add_run(text)
        set_run_font(run, size=header_size, bold=True, color=RGBColor(255, 255, 255))

    for row_idx, values in enumerate(rows):
        row = table.add_row()
        prevent_row_split(row)
        for col_idx, text in enumerate(values):
            cell = row.cells[col_idx]
            set_cell_width(cell, widths[col_idx])
            set_cell_margins(cell)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if row_idx % 2 == 1:
                set_cell_shading(cell, LIGHT_BLUE)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.1
            paragraph.alignment = (
                WD_ALIGN_PARAGRAPH.LEFT if col_idx in (1, 2, 3) else WD_ALIGN_PARAGRAPH.CENTER
            )
            run = paragraph.add_run(str(text))
            set_run_font(run, size=font_size, bold=False, color=TEXT)

    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def add_page_number_footer(section):
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(4)

    run = paragraph.add_run("SmartWMS  |  ")
    set_run_font(run, size=8, color=MUTED)

    field_begin = OxmlElement("w:fldChar")
    field_begin.set(qn("w:fldCharType"), "begin")
    instr_text = OxmlElement("w:instrText")
    instr_text.set(qn("xml:space"), "preserve")
    instr_text.text = " PAGE "
    field_end = OxmlElement("w:fldChar")
    field_end.set(qn("w:fldCharType"), "end")
    run._r.append(field_begin)
    run._r.append(instr_text)
    run._r.append(field_end)


def configure_document(doc):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.72)
    section.bottom_margin = Inches(0.72)
    section.left_margin = Inches(0.78)
    section.right_margin = Inches(0.78)
    add_page_number_footer(section)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = styles[style_name]
        remove_style_borders(style)
        style.font.name = "Microsoft YaHei"
        style.font.color.rgb = BLACK
        style.font.bold = True
        rpr = style._element.get_or_add_rPr()
        rfonts = rpr.rFonts
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        rfonts.set(qn("w:ascii"), "Microsoft YaHei")
        rfonts.set(qn("w:hAnsi"), "Microsoft YaHei")
        rfonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    doc.core_properties.title = "仓储 WMS 与 AGV 调度系统开发计划与分工"
    doc.core_properties.subject = "三人协作开发基线与工作分解"
    doc.core_properties.author = "SmartWMS 项目组"
    doc.core_properties.keywords = "WMS, AGV, MAPF, 微服务, 开发计划, 分工"


def add_cover(doc):
    for _ in range(4):
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(0)

    kicker = add_text(
        doc,
        "SMARTWMS  /  TEAM DEVELOPMENT BASELINE",
        size=9,
        bold=True,
        color=MUTED,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=18,
    )
    kicker.paragraph_format.space_before = Pt(18)

    title = doc.add_paragraph(style="Title")
    remove_paragraph_borders(title)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(14)
    title_run = title.add_run("仓储 WMS 与 AGV 调度系统开发计划与分工")
    set_run_font(title_run, size=26, bold=True, color=BLACK)

    subtitle = add_text(
        doc,
        "三人协作开发基线",
        size=13,
        bold=False,
        color=MUTED,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=20,
    )
    subtitle.paragraph_format.space_after = Pt(20)

    scope = add_text(
        doc,
        "覆盖 WMS 业务、任务中心、多 AGV 时空路径、tick 仿真、实时可视化与分析服务",
        size=10.5,
        color=TEXT,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=36,
    )

    meta = doc.add_table(rows=4, cols=2)
    meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta.autofit = False
    set_table_borders(meta, color=WHITE, size="0")
    meta_rows = [
        ("项目阶段", "M0 契约基线"),
        ("团队", "成员一 RiceXi  /  成员二 Zsource-Fuyi  /  成员三 KingSure000"),
        ("仓库模式", "单仓库、个人集成分支、契约先行"),
        ("文档版本", "0.1"),
    ]
    meta_widths = [1.25, 5.1]
    for row_idx, (label, value) in enumerate(meta_rows):
        for col_idx, text in enumerate((label, value)):
            cell = meta.cell(row_idx, col_idx)
            set_cell_width(cell, meta_widths[col_idx])
            set_cell_margins(cell, top=80, bottom=80, start=80, end=80)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.alignment = (
                WD_ALIGN_PARAGRAPH.RIGHT if col_idx == 0 else WD_ALIGN_PARAGRAPH.LEFT
            )
            run = paragraph.add_run(text)
            set_run_font(
                run,
                size=9.5,
                bold=col_idx == 0,
                color=MUTED if col_idx == 0 else TEXT,
            )

    add_text(
        doc,
        "本文件用于冻结三人职责、代码归属、接口交接、里程碑退出条件和发布门禁。",
        size=9.5,
        color=MUTED,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        after=0,
    )


def build_document():
    doc = Document()
    configure_document(doc)
    add_cover(doc)
    doc.add_page_break()

    add_heading(doc, "1  项目目标与交付边界")
    add_text(
        doc,
        "系统在本机完成“出库订单 -> 拣选任务 -> AGV 指派 -> 时空路径规划 -> tick 仿真 -> 实时地图 -> 指标分析”的闭环。演示使用模拟订单、地图和车辆，不连接真实仓储设备。",
    )
    add_text(
        doc,
        "开发按可运行里程碑推进。只有当前里程碑满足退出条件并通过联合验证后，才进入下一个里程碑的新功能。业务状态、调度结果和地图表现必须使用同一份后端事实。",
    )
    add_heading(doc, "1.1 交付优先级", level=2)
    add_table(
        doc,
        ["等级", "交付内容", "完成要求"],
        [
            ("P0 演示阻断", "订单闭环、无冲突多车调度、死锁恢复、实时地图、持久化", "30 台目标、20 fps、30 分钟无永久卡死"),
            ("P1 课程技术", "Kafka、Redis、网关、双 dispatch、监控、部署", "在演示环境真实运行并留存证据"),
            ("P2 加分能力", "检索、离线分析、对象存储、Serverless、3D", "不得挤占 P0/P1，不得伪造结果"),
        ],
        [1.15, 2.8, 2.95],
        font_size=9.0,
    )

    add_heading(doc, "2  团队分工")
    add_text(
        doc,
        "分工按端到端技术所有权划分。每名成员对一条主链路负责，同时承担对应质量门禁和跨角色交接。",
    )
    add_table(
        doc,
        ["成员", "主角色", "核心职责", "质量责任"],
        [
            ("成员一\nRiceXi", "集成与平台负责人", "gateway、auth、wms、task、Compose、CI、契约合并", "可启动、可观测、事务与状态一致"),
            ("成员二\nZsource-Fuyi", "调度与算法负责人", "dispatch、sim、时空 A*、预约、死锁、充电、仿真", "路径正确、确定性、性能与规模"),
            ("成员三\nKingSure000", "前端与演示负责人", "web、push、analytics、Canvas、回放、端到端测试", "实时体验、状态一致、演示可复现"),
        ],
        [1.18, 1.45, 3.05, 1.82],
        font_size=8.9,
    )

    doc.add_page_break()
    add_heading(doc, "2.1 代码与接口归属", level=2)
    add_table(
        doc,
        ["对象", "最终负责人", "评审要求", "交接产物"],
        [
            ("地图、tick、AGV 状态", "成员二", "成员一、成员三确认", "地图 schema、状态枚举、固定 seed"),
            ("API 与事件 envelope", "成员一", "至少两名成员批准", "OpenAPI、JSON Schema、错误码"),
            ("任务状态机", "成员一", "成员二确认重派语义", "状态表、非法流转用例、事件示例"),
            ("WebSocket 协议", "成员三", "成员一确认网关与会话", "快照、增量、重连和告警消息"),
            ("共享测试场景", "成员二", "成员一、成员三复用", "地图、订单、AGV、seed、预期事件"),
        ],
        [1.75, 1.15, 2.3, 2.7],
        font_size=8.9,
    )

    add_heading(doc, "3  开发里程碑")
    add_table(
        doc,
        ["里程碑", "主目标", "主责", "退出条件"],
        [
            ("M0 契约基线", "冻结地图、任务、AGV、API 与事件契约", "成员一", "三人批准契约；骨架可构建；样例场景可解析"),
            ("M1 单 AGV 闭环", "一个订单由一台 AGV 完成", "成员二", "订单、任务、路径、仿真和前端状态一致；固定 seed 可重放"),
            ("M2 多 AGV 正确性", "不重格、不穿边、不永久卡死", "成员二", "10 台固定场景通过；死锁检测与回退可复现"),
            ("M3 全栈集成", "核心服务通过统一入口联动", "成员一", "一条命令启动；剧本 A 不使用 mock 数据完成"),
            ("M4 规模与可靠性", "30 台规模与持续运行", "成员二", "30 分钟无永久死锁；500 场景成功率不低于 95%"),
            ("M5 实时体验", "实时地图、回放、断线恢复", "成员三", "30 台不低于 20 fps；重连后状态一致"),
            ("M6 技术能力补齐", "消息、双实例、观测、部署", "成员一", "Kafka、Grafana、SkyWalking、kind、Ansible 可运行"),
            ("M7 发布封版", "回归、演示、文档和发布包", "成员三", "P0/P1 全通过；连续两次完整演示成功"),
        ],
        [1.15, 2.35, 0.78, 3.02],
        font_size=8.6,
    )

    add_heading(doc, "3.1 关键依赖", level=2)
    add_text(
        doc,
        "地图、状态和事件契约必须先冻结；随后完成单 AGV 与顶点/边预约，再进入多车冲突、死锁、30 台规模、实时地图和发布封版。WMS 与前端可以在契约冻结后并行。",
    )

    doc.add_page_break()
    add_heading(doc, "4  核心工作包")
    add_table(
        doc,
        ["编号", "工作包", "负责人", "主要产物", "验收摘要"],
        [
            ("WP-01", "契约与仓库基线", "成员一", "OpenAPI、事件 schema、服务骨架、CI", "schema lint 与服务构建通过"),
            ("WP-02", "地图与 tick 仿真", "成员二", "地图、运动、电量、装卸、固定 seed", "单 AGV 路径确定可重放"),
            ("WP-03", "WMS 主数据与订单", "成员一", "SKU、库位、库存、出库订单", "库存事务和重复释放测试通过"),
            ("WP-04", "任务状态机", "成员一", "任务池、优先级、重派、取消、超时", "非法流转拒绝，命令幂等"),
            ("WP-05", "多 AGV 路径", "成员二", "时空 A*、顶点/边预约、等待与绕行", "冲突场景与相向穿边通过"),
            ("WP-06", "死锁与充电", "成员二", "等待图、回退、充电队列和充电位", "环检测和恢复事件可复现"),
            ("WP-07", "前端与推送", "成员三", "Canvas、WebSocket、轨迹、热力、回放", "30 台 20 fps，重连一致"),
            ("WP-08", "分析与观测", "成员三", "吞吐、耗时、利用率、Grafana", "指标与原始事件可对账"),
            ("WP-09", "部署与发布", "成员一", "Compose、kind、Ansible、启动脚本", "干净环境按文档启动"),
            ("WP-10", "验收与演示", "成员三", "固定剧本、性能数据、演示脚本", "连续两次完整演示成功"),
        ],
        [0.65, 1.45, 0.72, 2.55, 2.08],
        font_size=8.3,
    )

    add_heading(doc, "5  协作方式与交付门禁")
    add_heading(doc, "5.1 分支与评审", level=2)
    add_text(
        doc,
        "main 始终保持可构建、可启动。成员使用 dev/member-N 集成分支和短功能分支，通过 PR 合并到 main。契约先合并，代码后合并；一个 PR 只处理一个可验证目标。",
    )
    add_table(
        doc,
        ["事项", "规则"],
        [
            ("直接推送", "禁止直接向 main 推送"),
            ("共享契约", "schema 校验通过，至少两名成员批准"),
            ("跨目录修改", "先说明影响，再由归属人评审"),
            ("测试", "新增行为必须有对应单测、集成或端到端测试"),
            ("性能", "附机器、规模、seed、命令和原始结果"),
            ("运行数据", "不得提交数据库、轨迹原件、密钥和大体积媒体"),
        ],
        [1.55, 5.35],
        font_size=9.0,
    )

    add_heading(doc, "5.2 里程碑集成", level=2)
    add_text(
        doc,
        "达到里程碑退出条件时，三人从最新 main 合入个人分支，用固定 seed 启动完整场景，记录失败用例、冲突、死锁和性能数据。未关闭的 P0/P1 缺陷进入下一里程碑唯一最高优先级修复项。",
    )

    add_heading(doc, "5.3 契约变更", level=2)
    add_text(
        doc,
        "新增可选字段默认向后兼容；删除字段、改变字段含义或改变枚举语义必须升版本。破坏性变更需提交 ADR、迁移期和兼容测试。任何服务不得自行改变共享字段含义来适配本模块。",
    )

    add_heading(doc, "6  验收与发布")
    add_table(
        doc,
        ["指标", "目标值", "证据"],
        [
            ("AGV 规模", "至少 30 台同时运行", "固定高峰场景与回放"),
            ("前端帧率", "2D Canvas 持续不低于 20 fps", "浏览器性能采样"),
            ("冲突", "无同格、无相向穿边", "预约测试与日志"),
            ("死锁", "30 分钟无永久卡死", "soak test 和恢复事件"),
            ("冲突消解", "500 场景成功率不低于 95%", "批测原始结果"),
            ("任务指派延迟", "服务端 P95 小于 200 ms", "压测报告"),
            ("服务数量", "至少 6 个容器化微服务", "Compose 服务清单与健康检查"),
            ("可观测", "Grafana 与 SkyWalking 可用", "面板与 trace 截图索引"),
        ],
        [1.6, 3.0, 2.3],
        font_size=8.9,
    )

    add_heading(doc, "6.1 封版条件", level=2)
    for text in (
        "P0 全部通过，P1 全部通过或有无争议的明确限制说明。",
        "从干净环境按 README 和部署文档启动成功。",
        "固定场景连续两次完整演示成功，不依赖现场手工修复。",
        "接口、事件、地图、状态机和部署说明与实现一致。",
        "已知缺陷有等级、影响、规避方式和负责人。",
    ):
        paragraph = doc.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(text)
        set_run_font(run, size=10.2, color=TEXT)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build_document()

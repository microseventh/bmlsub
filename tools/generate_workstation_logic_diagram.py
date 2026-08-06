#!/usr/bin/env python3
"""Generate the multi-page Workstation implementation logic diagram."""

from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


PALETTE = {
    "process": ("#dae8fc", "#6c8ebf"),
    "success": ("#d5e8d4", "#82b366"),
    "decision": ("#fff2cc", "#d6b656"),
    "io": ("#ffe6cc", "#d79b00"),
    "state": ("#e1d5e7", "#9673a6"),
    "error": ("#f8cecc", "#b85450"),
    "neutral": ("#f5f5f5", "#666666"),
}


class Page:
    def __init__(self, mxfile: ET.Element, name: str, width: int, height: int,
                 subtitle: str) -> None:
        diagram = ET.SubElement(mxfile, "diagram", {"name": name, "id": name[:24]})
        model = ET.SubElement(diagram, "mxGraphModel", {
            "dx": "1422", "dy": "794", "grid": "1", "gridSize": "10",
            "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1",
            "fold": "1", "page": "1", "pageScale": "1",
            "pageWidth": str(width), "pageHeight": str(height), "math": "0",
            "shadow": "0", "background": "#ffffff",
        })
        self.root = ET.SubElement(model, "root")
        ET.SubElement(self.root, "mxCell", {"id": "0"})
        ET.SubElement(self.root, "mxCell", {"id": "1", "parent": "0"})
        self.counter = 2
        self.node(
            "title", name, 30, 20, width - 60, 50, "neutral",
            detail=subtitle, font_size=18,
        )
        self.legend(width - 600, 80)

    def ident(self, prefix: str) -> str:
        value = f"{prefix}-{self.counter}"
        self.counter += 1
        return value

    @staticmethod
    def text(title: str, detail: str = "", ref: str = "") -> str:
        parts = [f"<b>{title}</b>"]
        if detail:
            parts.append(detail)
        if ref:
            # Use CSS pixels instead of the legacy HTML `font size` attribute;
            # Draw.io's renderer treats large legacy values inconsistently.
            parts.append(f"<span style=\"font-size:10px;color:#666666\">{ref}</span>")
        return "<br>".join(parts)

    def node(self, key: str, title: str, x: int, y: int, width: int, height: int,
             kind: str = "process", *, detail: str = "", ref: str = "",
             parent: str = "1", font_size: int = 12) -> str:
        node_id = self.ident(key)
        fill, stroke = PALETTE[kind]
        shape = "rounded=1;arcSize=8;"
        if kind == "decision":
            shape = "rhombus;"
        elif kind == "success":
            shape = "ellipse;"
        elif kind == "io":
            shape = "shape=parallelogram;perimeter=parallelogramPerimeter;"
        style = (
            f"{shape}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
            f"fontColor=#1f2937;fontSize={font_size};align=center;verticalAlign=middle;"
            "spacing=8;strokeWidth=1.5;"
        )
        cell = ET.SubElement(self.root, "mxCell", {
            "id": node_id, "value": self.text(title, detail, ref), "style": style,
            "vertex": "1", "parent": parent,
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": str(width), "height": str(height),
            "as": "geometry",
        })
        return node_id

    def store(self, key: str, title: str, x: int, y: int, width: int, height: int,
              *, detail: str = "", ref: str = "", parent: str = "1") -> str:
        node_id = self.ident(key)
        cell = ET.SubElement(self.root, "mxCell", {
            "id": node_id, "value": self.text(title, detail, ref),
            "style": (
                "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;"
                "fillColor=#d5e8d4;strokeColor=#82b366;fontColor=#1f2937;"
                "fontSize=12;spacing=8;strokeWidth=1.5;"
            ),
            "vertex": "1", "parent": parent,
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": str(width), "height": str(height),
            "as": "geometry",
        })
        return node_id

    def lane(self, key: str, title: str, x: int, y: int, width: int, height: int,
             fill: str = "#f8fafc") -> str:
        lane_id = self.ident(key)
        cell = ET.SubElement(self.root, "mxCell", {
            "id": lane_id, "value": title,
            "style": (
                "swimlane;startSize=34;horizontal=1;html=1;container=1;pointerEvents=0;"
                f"fillColor={fill};swimlaneFillColor=#ffffff;strokeColor=#94a3b8;"
                "fontStyle=1;fontSize=13;rounded=0;collapsible=0;"
            ),
            "vertex": "1", "parent": "1",
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": str(width), "height": str(height),
            "as": "geometry",
        })
        return lane_id

    def edge(self, source: str, target: str, label: str = "", *, dashed: bool = False,
             color: str = "#64748b", points: tuple[tuple[int, int], ...] = ()) -> str:
        edge_id = self.ident("edge")
        style = (
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;"
            f"html=1;strokeColor={color};strokeWidth=1.5;endArrow=blockThin;endFill=1;"
            "fontSize=11;fontColor=#334155;labelBackgroundColor=#ffffff;"
        )
        if dashed:
            style += "dashed=1;dashPattern=6 4;"
        cell = ET.SubElement(self.root, "mxCell", {
            "id": edge_id, "value": label, "style": style,
            "edge": "1", "parent": "1", "source": source, "target": target,
        })
        geometry = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
        if points:
            array = ET.SubElement(geometry, "Array", {"as": "points"})
            for x, y in points:
                ET.SubElement(array, "mxPoint", {"x": str(x), "y": str(y)})
        return edge_id

    def legend(self, x: int, y: int) -> None:
        labels = (
            ("process", "执行步骤"), ("decision", "条件/选择"),
            ("io", "输入/外部动作"), ("state", "状态/产物"),
            ("error", "阻断/失败"), ("success", "完成/交接"),
        )
        legend_id = self.ident("legend")
        cell = ET.SubElement(self.root, "mxCell", {
            "id": legend_id, "value": "图例",
            "style": (
                "swimlane;startSize=24;html=1;container=1;pointerEvents=0;"
                "fillColor=#ffffff;strokeColor=#94a3b8;fontStyle=1;fontSize=11;collapsible=0;"
            ),
            "vertex": "1", "parent": "1",
        })
        ET.SubElement(cell, "mxGeometry", {
            "x": str(x), "y": str(y), "width": "570", "height": "58", "as": "geometry",
        })
        for index, (kind, label) in enumerate(labels):
            fill, stroke = PALETTE[kind]
            swatch = ET.SubElement(self.root, "mxCell", {
                "id": self.ident("swatch"), "value": label,
                "style": (
                    f"rounded=1;html=1;fillColor={fill};strokeColor={stroke};"
                    "fontSize=10;align=center;verticalAlign=middle;"
                ),
                "vertex": "1", "parent": legend_id,
            })
            ET.SubElement(swatch, "mxGeometry", {
                "x": str(8 + index * 93), "y": "29", "width": "84", "height": "20",
                "as": "geometry",
            })


def overview(mxfile: ET.Element) -> None:
    p = Page(mxfile, "01 总控与阶段判定", 2200, 1280,
             "workstation start 的系列检查、单集发现、状态识别、确认边界与动作分派")
    start = p.node("start", "bmlsub workstation start", 60, 130, 250, 70, "success",
                   ref="cli.py:_workstation_start:1237")
    root = p.node("root", "解析系列根目录", 390, 130, 240, 70,
                  detail="当前目录 / --series-root", ref="start.py:resolve_series_root:27")
    series = p.node("series", "检查系列工作区", 710, 130, 260, 70,
                    detail="series.json、繁体名、数字单集目录", ref="start.py:inspect_series_workspace:48")
    ready = p.node("ready", "系列可用？", 1050, 125, 210, 90, "decision")
    init = p.node("init", "创建/提升系列配置", 1030, 280, 250, 80, "io",
                  detail="问答、模板、繁化重试", ref="cli.py:1237-1333; series.py")
    episode = p.node("episode", "选择 episode_id", 1340, 130, 240, 70,
                     detail="启动目录、唯一单集、TTY 选择或参数", ref="cli.py:1334-1357")
    inspect = p.node("inspect", "识别单集阶段", 1650, 130, 250, 70,
                     ref="start.py:inspect_episode_stage:94")
    manifest = p.node("manifest", "存在 manifest.json？", 1970, 120, 190, 90, "decision")

    registered = p.node("registered", "验证登记状态", 1750, 300, 260, 80,
                        detail="SQLite Artifact 当前性 + summary + manifest",
                        ref="start.py:_inspect_registered_state:261")
    physical = p.node("physical", "检查物理文件", 1750, 440, 260, 80,
                      detail="视频、正式 ASS、参考字幕、顶层字体",
                      ref="start.py:_inspect_physical_state:362")
    phase = p.node("phase", "detected_phase", 1410, 410, 220, 100, "decision",
                   detail="preprocess / human_handoff / local_production / publish / complete / blocked")

    preprocess = p.node("preprocess", "run_preprocess", 1060, 620, 240, 75,
                        ref="preprocess.py:run_preprocess:78")
    handoff = p.node("handoff", "人工翻译、校对、字体收集", 1350, 620, 270, 75, "success",
                     detail="自动流程在此暂停", ref="start.py:physical:418-425")
    delivery = p.node("delivery", "run_delivery", 1680, 620, 230, 75,
                      ref="delivery.py:run_delivery:308")
    publish_hint = p.node("publish_hint", "提示 start delivery", 1960, 620, 200, 75, "io",
                          detail="普通 start 不直接发布", ref="cli.py:1371-1379")
    publish = p.node("publish", "run_publish", 1770, 790, 230, 75, "io",
                     detail="外部动作需单独确认", ref="publish.py:run_publish:124")
    complete = p.node("complete", "complete", 2070, 790, 100, 75, "success",
                      detail="无下一动作")
    blocked = p.node("blocked", "needs_review / blocked", 1380, 790, 260, 75, "error",
                     detail="歧义、缺输入、人工交接")
    confirm = p.node("confirm", "本地执行已确认？", 750, 610, 220, 95, "decision",
                     ref="start.py:execute_recommended_action:139")
    waiting = p.node("waiting", "awaiting_confirmation", 750, 790, 220, 70, "state")

    rebuild = p.node("rebuild", "workstation rebuild", 60, 1010, 250, 70, "io",
                     detail="preprocess / delivery / 单步骤", ref="start.py:plan_rebuild:204")
    force = p.node("force", "确认后 force=True", 390, 1010, 240, 70, "decision",
                   detail="保留历史，禁止外部发布", ref="start.py:run_rebuild:224")
    status = p.node("status", "workstation status", 750, 1010, 240, 70, "state",
                    detail="读取 config + manifest + summary", ref="state.py:load_status:324")

    for a, b, label in (
        (start, root, ""), (root, series, ""), (series, ready, ""),
        (ready, episode, "是"), (ready, init, "否"), (init, series, "修复后重检"),
        (episode, inspect, ""), (inspect, manifest, ""),
        (manifest, registered, "是"), (manifest, physical, "否"),
        (registered, phase, ""), (physical, phase, ""),
        (phase, confirm, "preprocess / local_production"),
        (confirm, waiting, "否"), (confirm, preprocess, "run_preprocess"),
        (confirm, delivery, "run_delivery"), (phase, handoff, "human_handoff"),
        (phase, publish_hint, "publish"), (publish_hint, publish, "start delivery + 确认"),
        (phase, complete, "complete"), (phase, blocked, "blocked / ambiguous"),
        (rebuild, force, ""), (force, preprocess, "target=preprocess"),
        (force, delivery, "target=delivery/step"),
    ):
        p.edge(a, b, label)


def preprocess_page(mxfile: ET.Element) -> None:
    p = Page(mxfile, "02 预处理 Preprocess", 2400, 1620,
             "plan → 视频登记 → 参考字幕 → 音频 → Whisper jobs → 文本导出 → 状态汇总")
    setup_lane = p.lane("setup", "规划与源视频", 30, 150, 440, 1370, "#eff6ff")
    ref_lane = p.lane("refs", "参考字幕轨", 500, 150, 470, 1370, "#fff7ed")
    audio_lane = p.lane("audio", "日语音频", 1000, 150, 420, 1370, "#f0fdf4")
    trans_lane = p.lane("trans", "转录任务", 1450, 150, 620, 1370, "#faf5ff")
    state_lane = p.lane("state", "状态与出口", 2100, 150, 270, 1370, "#f8fafc")

    plan = p.node("plan", "plan_preprocess", 30, 60, 360, 80, "process", parent=setup_lane,
                  detail="发现视频、选择策略、生成 expected_steps", ref="preprocess.py:35-75")
    plan_ok = p.node("plan_ok", "plan 成功？", 100, 190, 220, 90, "decision", parent=setup_lane)
    plan_fail = p.node("plan_fail", "写 inspect_video 阻断步骤", 70, 330, 280, 80, "error",
                       parent=setup_lane, detail="select_source_video", ref="preprocess.py:130-138")
    config = p.node("config", "持久化 config / phase plan", 50, 460, 320, 80, "state",
                    parent=setup_lane, detail="preprocess policy + expected steps", ref="preprocess.py:99-129")
    video = p.node("video", "preprocess.inspect_video", 50, 590, 320, 90, "process",
                   parent=setup_lane, detail="register_video + VIDEO_PURPOSES", ref="preprocess.py:140-153")
    tracks = p.node("tracks", "list_media_tracks", 50, 740, 320, 75, "process",
                    parent=setup_lane, detail="读取音频/字幕轨元数据", ref="preprocess.py:155-157")

    ref_select = p.node("ref_select", "选择参考字幕", 70, 70, 320, 100, "decision", parent=ref_lane,
                        detail="explicit / all_matching / unique；过滤文本 codec",
                        ref="preprocess.py:_select_reference_tracks:423")
    ref_review = p.node("ref_review", "needs_review", 90, 230, 280, 80, "error", parent=ref_lane,
                        detail="找不到、explicit 缺轨、unique 歧义", ref="preprocess.py:162-169")
    ref_extract = p.node("ref_extract", "extract_reference_subtitles", 70, 370, 320, 90, "process",
                         parent=ref_lane, detail="一次提取所有已选流", ref="preprocess.py:188-197")
    ref_publish = p.node("ref_publish", "publish_reference_subtitles", 70, 520, 320, 100, "process",
                         parent=ref_lane, detail=".en.s<index> + primary .en；哈希验证",
                         ref="preprocess.py:462-536")
    ref_manifest = p.node("ref_manifest", "写 reference_selection", 70, 690, 320, 100, "state",
                          parent=ref_lane, detail="轨列表、primary、Artifact/交付路径", ref="preprocess.py:210-255")

    audio_select = p.node("audio_select", "选择日语音频", 60, 100, 300, 100, "decision", parent=audio_lane,
                          detail="显式 index；否则语言唯一或 default 唯一", ref="preprocess.py:_select_audio_track:548")
    audio_review = p.node("audio_review", "needs_review", 80, 270, 260, 80, "error", parent=audio_lane,
                          detail="select_audio_track", ref="preprocess.py:260-272")
    audio_extract = p.node("audio_extract", "preprocess.extract_audio", 50, 420, 320, 100, "process",
                           parent=audio_lane, detail="mode=both", ref="preprocess.py:273-287")
    archive = p.store("archive", "归档音频 MKA", 50, 600, 140, 90, parent=audio_lane,
                      detail="generated.audio.archive")
    wav = p.store("wav", "转录 WAV", 220, 600, 140, 90, parent=audio_lane,
                  detail="mono 16k PCM")
    audio_manifest = p.node("audio_manifest", "写音频 Artifact IDs", 60, 760, 300, 80, "state",
                            parent=audio_lane, ref="preprocess.py:284-291")

    policy = p.node("policy", "transcription_policy", 190, 70, 240, 100, "decision", parent=trans_lane,
                    detail="none / quick / full / custom", ref="preprocess.py:_transcription_policy:411")
    no_trans = p.node("no_trans", "不创建转录步骤", 190, 230, 240, 75, "neutral", parent=trans_lane)
    each_job = p.node("each_job", "for job in whisper_jobs", 170, 360, 280, 80, "process", parent=trans_lane,
                      ref="preprocess.py:298-320")
    direct = p.node("direct", "direct · medium", 30, 520, 250, 95, "process", parent=trans_lane,
                    detail="整段调用；mlx-community/whisper-medium-mlx",
                    ref="models.py:transcription_jobs_for_mode:109")
    chunked = p.node("chunked", "chunked · v3 turbo", 320, 520, 250, 95, "process", parent=trans_lane,
                     detail="240s 块 / 5s overlap；逐块重置上下文",
                     ref="transcription/core.py:_transcribe_chunked:359")
    trans_fail = p.node("trans_fail", "失败 / retryable？", 190, 680, 240, 95, "decision", parent=trans_lane)
    resume = p.node("resume", "resume_preprocess", 30, 830, 230, 75, "error", parent=trans_lane,
                    detail="写 next_action 后返回")
    export = p.node("export", "export_transcript_text.<job>", 300, 830, 270, 90, "process", parent=trans_lane,
                    detail="每个非空 segment 一行", ref="text_export.py:45-107")
    transcript_store = p.store("transcript_store", "JSON + TXT + chunks", 180, 1000, 260, 100,
                               parent=trans_lane, detail="Artifact IDs 按 job 归档")
    trans_manifest = p.node("trans_manifest", "写 transcript 映射", 170, 1170, 280, 85, "state",
                            parent=trans_lane, ref="preprocess.py:370-384")

    step_files = p.store("step_files", "steps/*.json", 45, 130, 180, 90, parent=state_lane)
    manifest_store = p.store("manifest_store", "manifest.json", 45, 300, 180, 90, parent=state_lane)
    summary = p.node("summary", "refresh_summary", 35, 480, 200, 80, "state", parent=state_lane,
                     ref="state.py:refresh_summary:283")
    final = p.node("final", "preprocess status", 35, 650, 200, 100, "decision", parent=state_lane,
                   detail="参考字幕失败会提升为 needs_review")
    handoff = p.node("handoff", "人工交接", 35, 850, 200, 75, "success", parent=state_lane)

    for a, b, label in (
        (plan, plan_ok, ""), (plan_ok, plan_fail, "否"), (plan_ok, config, "是"),
        (config, video, ""), (video, tracks, "成功/复用"),
        (tracks, ref_select, "字幕轨"), (ref_select, ref_review, "无唯一可用选择"),
        (ref_select, ref_extract, "已选择"), (ref_extract, ref_publish, "成功/复用"),
        (ref_publish, ref_manifest, "成功/复用"),
        (tracks, audio_select, "音频轨"), (audio_select, audio_review, "歧义/缺失"),
        (audio_select, audio_extract, "唯一"), (audio_extract, archive, "产物 1"),
        (audio_extract, wav, "产物 2"), (archive, audio_manifest, ""), (wav, audio_manifest, ""),
        (audio_manifest, policy, ""), (policy, no_trans, "none"), (policy, each_job, "quick/full/custom"),
        (each_job, direct, "job.mode=direct"), (each_job, chunked, "job.mode=chunked"),
        (direct, trans_fail, ""), (chunked, trans_fail, ""), (trans_fail, resume, "失败"),
        (trans_fail, export, "成功/复用"), (export, transcript_store, ""),
        (transcript_store, trans_manifest, ""), (ref_manifest, manifest_store, ""),
        (audio_manifest, manifest_store, ""), (trans_manifest, manifest_store, ""),
        (video, step_files, "step snapshot"), (ref_extract, step_files, "step snapshot"),
        (audio_extract, step_files, "step snapshot"), (export, step_files, "step snapshot"),
        (step_files, summary, ""), (manifest_store, summary, ""), (summary, final, ""),
        (final, handoff, "succeeded"),
    ):
        p.edge(a, b, label)


def delivery_page(mxfile: ET.Element) -> None:
    p = Page(mxfile, "03 翻译交接与本地生产", 2600, 1720,
             "translation.validate_delivery → 字幕快照/繁化 → ASS 分析 → 三类成品 → Torrent")
    input_lane = p.lane("input", "翻译交付验证", 30, 150, 470, 1470, "#fff7ed")
    subtitle_lane = p.lane("subtitle", "字幕准备", 530, 150, 520, 1470, "#faf5ff")
    product_lane = p.lane("product", "ProductionRequest 执行", 1080, 150, 900, 1470, "#eff6ff")
    release_lane = p.lane("release", "成品与 Torrent", 2010, 150, 560, 1470, "#f0fdf4")

    plan = p.node("plan", "plan_delivery_execution", 55, 60, 360, 85, "process", parent=input_lane,
                  detail="只读计划：范围、输入、Profile、目标、remaining", ref="delivery.py:53-146")
    validate = p.node("validate", "translation.validate_delivery", 55, 200, 360, 95, "decision", parent=input_lane,
                      detail="视频 + CHS ASS + 可选 CHT ASS + 顶层字体 + release names",
                      ref="delivery.py:190-273")
    invalid = p.node("invalid", "needs_review / failed", 80, 360, 310, 85, "error", parent=input_lane,
                     detail="select_translation_inputs / provide_subtitle_and_fonts")
    register = p.node("register", "登记输入 Artifacts", 55, 520, 360, 105, "process", parent=input_lane,
                      detail="视频(缺时)、CHS、可选 CHT、逐字体", ref="delivery.py:235-266")
    persist = p.node("persist", "保存 delivery config", 70, 690, 330, 80, "state", parent=input_lane,
                     detail="保留 preprocess 配置", ref="delivery.py:_persist_delivery_config:43")

    chs_snapshot = p.node("chs_snapshot", "delivery.snapshot_chs_subtitle", 80, 70, 360, 90, "process",
                          parent=subtitle_lane, detail="事务复制至 workstation/delivery/subtitles",
                          ref="delivery.py:349-362")
    has_cht = p.node("has_cht", "存在正式 CHT？", 150, 230, 220, 95, "decision", parent=subtitle_lane)
    cht_snapshot = p.node("cht_snapshot", "snapshot_cht_subtitle", 25, 390, 220, 90, "process", parent=subtitle_lane,
                          detail="人工 CHT 优先")
    convert = p.node("convert", "generate_cht_subtitle", 275, 390, 220, 90, "process", parent=subtitle_lane,
                     detail="Taiwan 转换服务", ref="delivery.py:383-397")
    publish_cht = p.node("publish_cht", "publish_cht_subtitle", 130, 550, 260, 90, "process", parent=subtitle_lane,
                         detail="人工版保留；生成版复制到顶层", ref="delivery.py:400-429")
    analyze = p.node("analyze", "validate_subtitles_fonts", 80, 720, 360, 105, "process", parent=subtitle_lane,
                     detail="分别 analyze_ass(CHS/CHT) + font-report；诊断不阻断生产",
                     ref="delivery.py:431-455")

    selection = p.node("selection", "DeliverySelection", 300, 60, 300, 100, "decision", parent=product_lane,
                       detail="full / mkv / mp4 / custom", ref="models.py:DeliverySelection:166")
    hevc = p.node("hevc", "delivery.encode_hevc", 60, 250, 250, 100, "process", parent=product_lane,
                  detail="encode · hevc-10bit → intermediate MKV", ref="delivery.py:461-465")
    chs = p.node("chs", "encode_hardsub_chs", 330, 250, 250, 100, "process", parent=product_lane,
                 detail="hardsub · h264-chs + CHS + fonts", ref="delivery.py:466-470")
    cht = p.node("cht", "encode_hardsub_cht", 600, 250, 250, 100, "process", parent=product_lane,
                 detail="hardsub · h264-cht + CHT + fonts", ref="delivery.py:471-476")
    request = p.node("request", "create + execute ProductionRequest", 250, 450, 400, 100, "process",
                     parent=product_lane, detail="每个请求失败即停止；成功/复用写 step",
                     ref="delivery.py:478-502")
    mux = p.node("mux", "delivery.mux_subtitles", 250, 620, 400, 105, "process", parent=product_lane,
                 detail="HEVC + CHS + CHT + fonts → mkv-subtitle", ref="delivery.py:508-533")
    product_manifest = p.node("product_manifest", "写 products manifest", 280, 800, 340, 90, "state",
                              parent=product_lane, detail="3 个正式成品 + HEVC intermediate", ref="delivery.py:534-541")

    product_files = p.store("product_files", "正式成品", 100, 100, 360, 120, parent=release_lane,
                            detail="mp4_chs / mp4_cht / mkv_hevc")
    want_torrent = p.node("want_torrent", "create_torrents？", 160, 310, 240, 95, "decision", parent=release_lane)
    torrents = p.node("torrents", "delivery.create_torrents", 90, 480, 380, 100, "process", parent=release_lane,
                      detail="按 selection.products 逐个创建", ref="delivery.py:542-564")
    torrent_files = p.store("torrent_files", "Torrent Artifacts", 110, 650, 340, 100, parent=release_lane)
    complete = p.node("complete", "全部 3 成品 + 3 Torrent？", 130, 830, 300, 100, "decision", parent=release_lane,
                      ref="delivery.py:565-587")
    success = p.node("success", "succeeded", 70, 1010, 180, 75, "success", parent=release_lane)
    partial = p.node("partial", "partial", 310, 1010, 180, 75, "state", parent=release_lane,
                     detail="所选范围完成，但全量未齐")

    for a, b, label in (
        (plan, validate, "用户确认计划后"), (validate, invalid, "输入不完整/歧义"),
        (validate, register, "有效"), (register, persist, ""), (persist, chs_snapshot, ""),
        (chs_snapshot, has_cht, ""), (has_cht, cht_snapshot, "是"), (has_cht, convert, "否"),
        (cht_snapshot, publish_cht, "保留人工版"), (convert, publish_cht, "复制生成版"),
        (publish_cht, analyze, ""), (analyze, selection, ""),
        (selection, hevc, "含 mkv_hevc"), (selection, chs, "含 mp4_chs"),
        (selection, cht, "含 mp4_cht"), (hevc, request, ""), (chs, request, ""), (cht, request, ""),
        (request, mux, "mkv_hevc 分支"), (request, product_manifest, "MP4 分支"),
        (mux, product_manifest, ""), (product_manifest, product_files, ""),
        (product_files, want_torrent, ""), (want_torrent, torrents, "是"),
        (want_torrent, complete, "否"), (torrents, torrent_files, ""),
        (torrent_files, complete, ""), (complete, success, "全量齐"), (complete, partial, "未齐"),
    ):
        p.edge(a, b, label)


def publish_page(mxfile: ET.Element) -> None:
    p = Page(mxfile, "04 外部发布 Publish", 2700, 1700,
             "严格批次顺序：全部 R2 → 全部 VPS → 全部 qB → 全部 Anibt；每产品可再次确认")
    gate_lane = p.lane("gate", "发布计划与确认", 30, 150, 480, 1450, "#fff7ed")
    r2_lane = p.lane("r2", "批次 1 · R2", 540, 150, 500, 1450, "#eff6ff")
    remote_lane = p.lane("remote", "批次 2 · VPS", 1070, 150, 500, 1450, "#f0fdf4")
    qb_lane = p.lane("qb", "批次 3 · qB", 1600, 150, 500, 1450, "#faf5ff")
    anibt_lane = p.lane("anibt", "批次 4 · Anibt / Nyaa", 2130, 150, 540, 1450, "#fef2f2")

    plan = p.node("plan", "plan_publish", 55, 70, 370, 95, "process", parent=gate_lane,
                  detail="3 成品 + 3 Torrent + Artifact 当前性 + 配置 + publication identity",
                  ref="publish.py:plan_publish:26")
    ready = p.node("ready", "发布就绪？", 130, 230, 220, 95, "decision", parent=gate_lane)
    blocked = p.node("blocked", "publish_not_ready", 80, 390, 320, 80, "error", parent=gate_lane,
                     detail="complete_delivery_and_publish_configuration")
    global_confirm = p.node("global_confirm", "外部发布总确认？", 110, 550, 260, 95, "decision", parent=gate_lane,
                            ref="publish.py:148-155")
    awaiting = p.node("awaiting", "awaiting_confirmation", 100, 710, 280, 75, "state", parent=gate_lane)
    products = p.node("products", "构造 3 个产品上下文", 65, 860, 350, 90, "process", parent=gate_lane,
                      detail="content + torrent + registered receipts", ref="publish.py:156-184")
    per_item = p.node("per_item", "confirm_item(stage, product)？", 95, 1020, 290, 95, "decision", parent=gate_lane,
                      detail="TTY 可逐项暂停；-y 自动接受")

    r2_loop = p.node("r2_loop", "for 每个 product", 130, 90, 240, 75, "process", parent=r2_lane)
    r2_pair = p.node("r2_pair", "content + torrent", 130, 230, 240, 75, "decision", parent=r2_lane)
    upload = p.node("upload", "publish.upload_r2", 70, 380, 360, 105, "io", parent=r2_lane,
                    detail="bucket/object_key/content_type/access + R2 credential",
                    ref="publish.py:186-224")
    r2_receipt = p.store("r2_receipt", "R2 receipt IDs", 100, 560, 300, 90, parent=r2_lane,
                         detail="publish.r2[product:label]")
    r2_fail = p.node("r2_fail", "失败即返回", 120, 720, 260, 75, "error", parent=r2_lane)

    remote_loop = p.node("remote_loop", "for 每个 product", 130, 90, 240, 75, "process", parent=remote_lane)
    remote_pair = p.node("remote_pair", "content + torrent", 130, 230, 240, 75, "decision", parent=remote_lane)
    pull = p.node("pull", "publish.pull_remote", 70, 380, 360, 105, "io", parent=remote_lane,
                  detail="SSH + rclone，从 R2 拉到 remote_target",
                  ref="publish.py:226-263")
    remote_receipt = p.store("remote_receipt", "Remote receipt IDs", 100, 560, 300, 90, parent=remote_lane,
                             detail="publish.remote[product:label]")
    remote_fail = p.node("remote_fail", "失败即返回", 120, 720, 260, 75, "error", parent=remote_lane)

    qb_loop = p.node("qb_loop", "for 每个 product", 130, 90, 240, 75, "process", parent=qb_lane)
    seed = p.node("seed", "publish.seed_qbittorrent", 70, 260, 360, 120, "io", parent=qb_lane,
                  detail="torrent + content + 2 remote receipts；隧道/WebUI；校验并启动",
                  ref="publish.py:265-300")
    qb_receipt = p.store("qb_receipt", "qB receipt ID", 100, 470, 300, 90, parent=qb_lane,
                         detail="publish.qb[product]")
    qb_fail = p.node("qb_fail", "失败即返回", 120, 640, 260, 75, "error", parent=qb_lane)

    anibt_loop = p.node("anibt_loop", "for 每个 product", 150, 90, 240, 75, "process", parent=anibt_lane)
    profile = p.node("profile", "构建 Anibt Profile", 100, 240, 340, 120, "process", parent=anibt_lane,
                     detail="格式/字幕/语言/1080p/大小/notes；可选 Nyaa 1_4 + trackers",
                     ref="publish.py:_anibt_profile:345")
    publish = p.node("publish", "publish.anibt", 100, 440, 340, 100, "io", parent=anibt_lane,
                     detail="上传 Torrent + Anibt credential", ref="publish.py:302-330")
    anibt_receipt = p.store("anibt_receipt", "Anibt receipt ID", 120, 620, 300, 90, parent=anibt_lane,
                            detail="publish.anibt[product]")
    summary = p.node("summary", "refresh_summary", 120, 790, 300, 80, "state", parent=anibt_lane)
    done = p.node("done", "publish status", 160, 960, 220, 75, "success", parent=anibt_lane)

    for a, b, label in (
        (plan, ready, ""), (ready, blocked, "否"), (ready, global_confirm, "是"),
        (global_confirm, awaiting, "否"), (global_confirm, products, "是"),
        (products, per_item, ""), (per_item, r2_loop, "upload_r2 已确认"),
        (r2_loop, r2_pair, ""), (r2_pair, upload, "逐项"),
        (upload, r2_receipt, "成功/复用"), (upload, r2_fail, "失败"),
        (r2_receipt, remote_loop, "全部 6 项完成"), (remote_loop, remote_pair, ""),
        (remote_pair, pull, "逐项"), (pull, remote_receipt, "成功/复用"),
        (pull, remote_fail, "失败"), (remote_receipt, qb_loop, "全部 6 项完成"),
        (qb_loop, seed, "逐产品"), (seed, qb_receipt, "成功/复用"), (seed, qb_fail, "失败"),
        (qb_receipt, anibt_loop, "全部 3 项完成"), (anibt_loop, profile, "逐产品"),
        (profile, publish, ""), (publish, anibt_receipt, "成功/复用"),
        (anibt_receipt, summary, "全部 3 项完成"), (summary, done, ""),
    ):
        p.edge(a, b, label)


def reliability_page(mxfile: ET.Element) -> None:
    p = Page(mxfile, "05 Stage 与 Artifact 可靠性内核", 2500, 1600,
             "每个 Pipeline 操作共享 StageRunner 生命周期；文件由 ArtifactWriter 事务提交")
    stage_lane = p.lane("stage", "StageRunner.run", 30, 150, 1250, 1350, "#eff6ff")
    writer_lane = p.lane("writer", "ArtifactBatchWriter.write", 1310, 150, 1160, 1350, "#f0fdf4")

    create = p.node("create", "create_run + create_stage", 450, 60, 350, 80, "process", parent=stage_lane,
                    detail="pending；保存 input/parameter/tool fingerprints", ref="stage_runner.py:62-82")
    inputs = p.node("inputs", "逐个验证输入 Artifact", 450, 190, 350, 90, "decision", parent=stage_lane,
                    detail="存在、validation_status、size/mtime/hash 当前", ref="stage_runner.py:83-100")
    input_fail = p.node("input_fail", "标 stale + fail stage/run", 50, 340, 330, 85, "error", parent=stage_lane,
                       detail="input_missing + diagnostic")
    force = p.node("force", "force？", 500, 340, 250, 95, "decision", parent=stage_lane)
    reusable = p.node("reusable", "查找可复用 Stage", 850, 340, 330, 90, "process", parent=stage_lane,
                      detail="episode + stage + 三指纹 + succeeded", ref="sqlite_store.py:find_reusable_stage:800")
    artifacts_ok = p.node("artifacts_ok", "旧产物仍当前？", 890, 500, 250, 95, "decision", parent=stage_lane)
    reuse = p.node("reuse", "skip 当前 Stage；返回旧 Artifacts", 820, 660, 360, 90, "success", parent=stage_lane,
                   detail="reused=True + reused_stage diagnostic")
    stale = p.node("stale", "旧 Stage 标 stale", 850, 810, 360, 80, "state", parent=stage_lane,
                   detail="artifact_stale diagnostic")
    running = p.node("running", "mark_stage_running + adapter", 420, 520, 390, 90, "process", parent=stage_lane,
                     ref="stage_runner.py:143-155")
    outcome = p.node("outcome", "Adapter outcome", 470, 680, 290, 100, "decision", parent=stage_lane,
                     detail="succeeded / skipped / needs_review / exception")
    register = p.node("register", "登记返回的 Artifacts", 400, 850, 430, 85, "state", parent=stage_lane,
                      detail="必须属于当前 run_id/stage_id")
    finish = p.node("finish", "完成 Stage + Run", 420, 1010, 390, 90, "success", parent=stage_lane,
                    detail="统一 StageResult + duration")
    review = p.node("review", "needs_review", 50, 850, 280, 80, "state", parent=stage_lane)
    exception = p.node("exception", "规范化异常并失败", 50, 1010, 280, 90, "error", parent=stage_lane,
                       detail="BmlsubError / input / external retryable / unexpected")

    specs = p.node("specs", "校验 targets", 390, 60, 350, 80, "decision", parent=writer_lane,
                   detail="非空、唯一、位于 workspace 内", ref="writer.py:106-117")
    temp = p.node("temp", "为每个目标创建临时路径", 390, 190, 350, 80, "process", parent=writer_lane)
    produce = p.node("produce", "producer(temporary)", 390, 320, 350, 80, "process", parent=writer_lane)
    validate_temp = p.node("validate_temp", "fsync + 验证全部临时文件", 370, 450, 390, 90, "decision", parent=writer_lane,
                           detail="缺文件或 validator 失败则不提交")
    backup = p.node("backup", "备份既有目标", 390, 600, 350, 80, "process", parent=writer_lane,
                    detail="os.replace → backups")
    commit = p.node("commit", "原子替换并 fsync 目录", 390, 730, 350, 85, "process", parent=writer_lane)
    validate_final = p.node("validate_final", "再次验证正式目标", 420, 870, 290, 90, "decision", parent=writer_lane)
    record = p.store("record", "创建 VALID ArtifactRecord", 380, 1020, 370, 105, parent=writer_lane,
                     detail="uuid + size + mtime + hash + source/parameter fp")
    rollback = p.node("rollback", "回滚", 40, 730, 280, 100, "error", parent=writer_lane,
                      detail="删除已提交目标；恢复备份；清理临时文件", ref="writer.py:_restore:176")

    for a, b, label in (
        (create, inputs, ""), (inputs, input_fail, "无效"), (inputs, force, "有效"),
        (force, running, "是"), (force, reusable, "否"), (reusable, artifacts_ok, "找到"),
        (reusable, running, "未找到"), (artifacts_ok, reuse, "是"), (artifacts_ok, stale, "否"),
        (stale, running, "重新执行"), (running, outcome, ""), (outcome, register, "succeeded/skipped"),
        (register, finish, ""), (outcome, review, "ReviewRequired"), (outcome, exception, "异常"),
        (specs, temp, "有效"), (temp, produce, ""), (produce, validate_temp, ""),
        (validate_temp, backup, "全部有效"), (validate_temp, rollback, "失败"),
        (backup, commit, ""), (commit, validate_final, ""), (validate_final, record, "有效"),
        (validate_final, rollback, "失败"), (record, register, "adapter 返回"),
    ):
        p.edge(a, b, label)


def state_page(mxfile: ET.Element) -> None:
    p = Page(mxfile, "06 状态文件与数据流", 2500, 1550,
             "SQLite 是执行账本；JSON 快照服务于人类检查、恢复、计划隔离与阶段识别")
    producers = p.lane("producers", "执行器", 30, 150, 460, 1280, "#eff6ff")
    ledger = p.lane("ledger", "SQLiteJobStore", 520, 150, 520, 1280, "#faf5ff")
    snapshots = p.lane("snapshots", "workstation/state JSON", 1070, 150, 760, 1280, "#fff7ed")
    consumers = p.lane("consumers", "读取与阶段判断", 1860, 150, 610, 1280, "#f0fdf4")

    operations = p.node("operations", "Pipeline / Workstation steps", 60, 90, 340, 90, "process", parent=producers,
                        detail="preprocess / translation / delivery / publish")
    fingerprints = p.node("fingerprints", "计算三类指纹", 80, 260, 300, 90, "process", parent=producers,
                          detail="input / parameter / tool")
    stage_result = p.node("stage_result", "StageResult / payload", 70, 430, 320, 90, "state", parent=producers)
    manifest_update = p.node("manifest_update", "update_manifest", 80, 600, 300, 80, "state", parent=producers,
                             detail="深度合并，不抹除其它 section")

    db = p.store("db", "state.sqlite3", 100, 90, 320, 120, parent=ledger,
                 detail="runs / stages / artifacts / stage_inputs / production_requests / events")
    transitions = p.node("transitions", "受控状态迁移", 90, 290, 340, 100, "decision", parent=ledger,
                         detail="pending → running → succeeded/failed/skipped/needs_review/stale")
    current = p.node("current", "Artifact 当前性", 110, 480, 300, 90, "decision", parent=ledger,
                     detail="VALID + path + size/mtime + 可选 hash")
    reusable = p.node("reuse", "复用索引", 110, 650, 300, 85, "state", parent=ledger,
                      detail="stage_name + 三指纹 + finished_at")

    config = p.store("config", "config.json", 60, 80, 260, 90, parent=snapshots,
                     detail="系列继承后的 preprocess/delivery/publish 配置")
    plans = p.store("plans", "<phase>-plan.json", 400, 80, 270, 90, parent=snapshots,
                    detail="policy + expected_steps + plan_id")
    steps = p.store("steps", "steps/<step>.json", 60, 260, 260, 100, parent=snapshots,
                    detail="状态、时间、SQLite IDs、I/O、诊断、next_action")
    artifacts = p.store("artifacts", "artifacts/<id>.json", 400, 260, 270, 100, parent=snapshots,
                        detail="Artifact 可读快照")
    manifest = p.store("manifest", "manifest.json", 60, 470, 260, 110, parent=snapshots,
                       detail="source / preprocess / subtitles / fonts / products / torrents / publish")
    summary = p.store("summary", "summary.json", 400, 470, 270, 110, parent=snapshots,
                      detail="四阶段聚合状态")
    phase_status = p.node("phase_status", "_phase_status 优先级", 150, 680, 430, 120, "decision", parent=snapshots,
                          detail="failed → needs_review → awaiting_confirmation → blocked → succeeded/skipped → running",
                          ref="state.py:_phase_status:339")

    status_cmd = p.node("status_cmd", "workstation status", 110, 90, 360, 85, "io", parent=consumers,
                        detail="读取 config + manifest + summary")
    inspect = p.node("inspect", "inspect_episode_stage", 110, 260, 360, 95, "process", parent=consumers,
                     detail="优先 registered；否则 physical_files")
    verify = p.node("verify", "验证 manifest 引用", 90, 450, 400, 110, "decision", parent=consumers,
                    detail="SQLite 可读？Artifact ID 存在且文件仍当前？")
    phase = p.node("phase", "识别下一阶段", 120, 660, 340, 110, "decision", parent=consumers,
                   detail="preprocess / human_handoff / local_production / publish / complete / blocked")
    resume = p.node("resume", "恢复/重跑", 140, 870, 300, 90, "success", parent=consumers,
                    detail="按当前 plan 隔离历史步骤；Stage 可复用")

    for a, b, label in (
        (operations, fingerprints, ""), (fingerprints, db, "StageRunner"),
        (db, transitions, "事务写入"), (transitions, current, ""), (current, reusable, ""),
        (stage_result, steps, "pipeline_payload_step / result_step"),
        (stage_result, artifacts, "输出 Artifact"), (manifest_update, manifest, ""),
        (operations, config, "初始化/更新"), (operations, plans, "write_phase_plan"),
        (steps, summary, "refresh_summary"), (plans, summary, "只聚合 expected_steps"),
        (summary, phase_status, ""), (config, status_cmd, ""), (manifest, status_cmd, ""),
        (summary, status_cmd, ""), (manifest, inspect, ""), (summary, inspect, ""),
        (db, verify, ""), (manifest, verify, "引用 IDs"), (verify, phase, "证据"),
        (phase_status, phase, "聚合状态"), (phase, resume, "recommended_action"),
        (reusable, resume, "有效历史 Stage"),
    ):
        p.edge(a, b, label)


def build(output: Path) -> None:
    mxfile = ET.Element("mxfile", {
        "host": "Electron", "agent": "bmlsub-workstation-diagram-generator",
        "version": "31.1.5", "type": "device", "compressed": "false",
    })
    overview(mxfile)
    preprocess_page(mxfile)
    delivery_page(mxfile)
    publish_page(mxfile)
    reliability_page(mxfile)
    state_page(mxfile)
    ET.indent(mxfile, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(mxfile).write(output, encoding="utf-8", xml_declaration=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--output", type=Path,
        default=Path("docs/diagrams/bmlsub-workstation-logic.drawio"),
    )
    args = parser.parse_args()
    build(args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()

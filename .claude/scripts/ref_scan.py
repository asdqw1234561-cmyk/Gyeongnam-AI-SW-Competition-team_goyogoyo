"""references/ 자료 변화 감지·텍스트 추출·색인 관리 (개발 Agent 전용, 앱과 무관).

사용법 (저장소 루트에서):
    python .claude/scripts/ref_scan.py status            # 새 파일·변경·삭제·분석 대기 목록
    python .claude/scripts/ref_scan.py extract           # 새 파일·변경 파일만 텍스트 추출
    python .claude/scripts/ref_scan.py extract --all     # 전부 다시 추출
    python .claude/scripts/ref_scan.py mark <경로> --category 대회필수요건,평가기준 --summary "한 줄 요약" [--used-in docs/agent/X.md]
    python .claude/scripts/ref_scan.py render            # docs/agent/REFERENCE_INDEX.md 다시 생성

- 원본(references/)은 읽기만 한다. 추출 결과는 .agent_state/extracted/ (git 제외, 언제든 재생성 가능).
- 색인 .agent_state/reference_index.json 이 원본 해시·추출 상태·분석 요약의 단일 기준이다.
  REFERENCE_INDEX.md 는 이 json 에서 생성되는 사람용 보기다(직접 수정하지 않는다).
- 글자가 거의 없는 PDF 페이지(이미지 슬라이드)는 PNG로 렌더링해 두고, Agent가 Read 도구로 이미지를 직접 본다.
- 선택 의존성: pymupdf(PDF), olefile(HWP) - requirements-dev.txt. 없으면 해당 형식은 'unsupported'로 남는다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REF_DIR = ROOT / "references"
STATE_DIR = ROOT / ".agent_state"
INDEX_PATH = STATE_DIR / "reference_index.json"
EXTRACT_DIR = STATE_DIR / "extracted"
INDEX_MD = ROOT / "docs" / "agent" / "REFERENCE_INDEX.md"

IGNORED_NAMES = {"README.md", ".gitkeep", "Thumbs.db", ".DS_Store"}
IMAGE_PAGE_MIN_CHARS = 30   # 이보다 글자가 적은 PDF 페이지는 이미지로 보고 렌더링
RENDER_DPI = 80             # 슬라이드 글자를 읽을 수 있는 최소 수준(파일 크기 절약)
CSV_PREVIEW_ROWS = 200

CATEGORIES = [
    "대회필수요건", "평가기준", "주제제약", "현재아이디어", "사용자문제",
    "데이터", "기술요구사항", "제출요구사항", "발표시연요구사항", "참고아이디어",
]


# ---------------------------------------------------------------- 색인

def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "files": {}}


def save_index(index: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def scan_files() -> list[Path]:
    if not REF_DIR.exists():
        return []
    return sorted(p for p in REF_DIR.rglob("*") if p.is_file() and p.name not in IGNORED_NAMES)


def diff(index: dict) -> dict:
    """디스크와 색인을 비교한다. 해시만 계산하므로 빠르다(hook에서 사용)."""
    on_disk = {_rel(p): p for p in scan_files()}
    known = index.get("files", {})
    result = {"new": [], "changed": [], "removed": [], "pending_extract": [], "pending_analysis": []}
    for rel, path in on_disk.items():
        entry = known.get(rel)
        if entry is None:
            result["new"].append(rel)
        elif entry.get("sha256") != _sha256(path):
            result["changed"].append(rel)
        elif entry.get("status") == "new":
            result["pending_extract"].append(rel)
        elif entry.get("status") == "extracted":
            result["pending_analysis"].append(rel)
    result["removed"] = [rel for rel in known if rel not in on_disk]
    return result


# ---------------------------------------------------------------- 추출기

def _extract_pdf(path: Path, out_dir: Path) -> dict:
    try:
        import pymupdf  # noqa: PLC0415
    except ImportError:
        pymupdf = None
    if pymupdf is None:
        if shutil.which("pdftotext"):
            txt = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True).stdout
            text = txt.decode("utf-8", errors="replace")
            pages = text.split("\f")
            body = "\n".join(f"===== p{i} =====\n{t}" for i, t in enumerate(pages, 1) if t.strip())
            return {"method": "pdftotext(한글 폰트가 깨질 수 있음 - pymupdf 설치 권장)", "pages": len(pages),
                    "text": body, "image_pages": []}
        return {"method": "unsupported", "error": "pymupdf 미설치 (pip install -r requirements-dev.txt)"}

    doc = pymupdf.open(str(path))
    parts, image_pages = [], []
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        if len(text) < IMAGE_PAGE_MIN_CHARS:
            png = out_dir / f"p{i:02d}.png"
            page.get_pixmap(dpi=RENDER_DPI).save(str(png))
            image_pages.append(_rel(png))
            parts.append(f"===== p{i} ===== [이미지 페이지 - {_rel(png)} 를 Read 도구로 직접 확인]\n{text}")
        else:
            parts.append(f"===== p{i} =====\n{text}")
    return {"method": "pymupdf", "pages": doc.page_count, "text": "\n".join(parts), "image_pages": image_pages}


_HWP_INLINE_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _hwp_para_text(raw: bytes) -> str:
    t = raw.decode("utf-16le", errors="replace")
    out, j = [], 0
    while j < len(t):
        c = ord(t[j])
        if c < 32:
            if c in (10, 13):
                out.append("\n")
            elif c == 9:
                out.append("\t")
            j += 8 if c in _HWP_INLINE_CTRL else 1  # 확장/인라인 컨트롤은 16바이트(8문자) 차지
            continue
        out.append(t[j])
        j += 1
    return "".join(out).strip()


def _extract_hwp(path: Path, out_dir: Path) -> dict:
    try:
        import olefile  # noqa: PLC0415
    except ImportError:
        return {"method": "unsupported", "error": "olefile 미설치 (pip install -r requirements-dev.txt)"}
    ole = olefile.OleFileIO(str(path))
    compressed = bool(ole.openstream("FileHeader").read()[36] & 1)
    sections = sorted((s for s in ole.listdir() if s[0] == "BodyText"), key=lambda s: int(s[1][7:]))
    parts = []
    for sec in sections:
        data = ole.openstream(sec).read()
        if compressed:
            data = zlib.decompress(data, -15)
        paras, i = [], 0
        while i + 4 <= len(data):
            header = struct.unpack_from("<I", data, i)[0]
            tag, size = header & 0x3FF, (header >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", data, i)[0]
                i += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                text = _hwp_para_text(data[i:i + size])
                if text:
                    paras.append(text)
            i += size
        parts.append(f"===== {sec[1]} (HWP는 쪽 번호가 없어 절·항목 제목으로 인용) =====\n" + "\n".join(paras))
    return {"method": "olefile(hwp5)", "pages": None, "text": "\n".join(parts), "image_pages": []}


def _xml_texts(xml: str, para_tag: str, text_tag: str) -> list[str]:
    paras = []
    for para in re.findall(rf"<{para_tag}[ >].*?</{para_tag}>", xml, flags=re.S):
        texts = re.findall(rf"<{text_tag}(?: [^>]*)?>([^<]*)</{text_tag}>", para)
        line = "".join(texts).strip()
        if line:
            paras.append(line)
    return paras


def _extract_docx(path: Path, out_dir: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    return {"method": "docx(zip)", "pages": None, "text": "\n".join(_xml_texts(xml, "w:p", "w:t")), "image_pages": []}


def _extract_pptx(path: Path, out_dir: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        parts = []
        for n, name in enumerate(slides, 1):
            xml = z.read(name).decode("utf-8", errors="replace")
            parts.append(f"===== slide {n} =====\n" + "\n".join(_xml_texts(xml, "a:p", "a:t")))
    return {"method": "pptx(zip)", "pages": len(slides), "text": "\n".join(parts), "image_pages": []}


def _read_text_file(path: Path) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_text(path: Path, out_dir: Path) -> dict:
    return {"method": "text", "pages": None, "text": _read_text_file(path), "image_pages": []}


def _extract_csv(path: Path, out_dir: Path) -> dict:
    rows = list(csv.reader(io.StringIO(_read_text_file(path))))
    preview = "\n".join(",".join(r) for r in rows[:CSV_PREVIEW_ROWS])
    note = f"[전체 {len(rows)}행 중 앞 {min(len(rows), CSV_PREVIEW_ROWS)}행 - 전체는 원본 CSV를 직접 분석]"
    return {"method": "csv", "pages": None, "rows": len(rows), "text": f"{note}\n{preview}", "image_pages": []}


EXTRACTORS = {
    ".pdf": _extract_pdf, ".hwp": _extract_hwp, ".docx": _extract_docx, ".pptx": _extract_pptx,
    ".txt": _extract_text, ".md": _extract_text, ".csv": _extract_csv,
}


def _out_dir_for(rel: str) -> Path:
    stem = re.sub(r"[^\w.-]+", "_", Path(rel).stem)[:60]
    return EXTRACT_DIR / f"{stem}_{hashlib.sha1(rel.encode()).hexdigest()[:8]}"


def extract_one(rel: str) -> dict:
    path = ROOT / rel
    out_dir = _out_dir_for(rel)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    extractor = EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        info = {"method": "unsupported", "error": f"{path.suffix} 형식 추출기 없음 - Read 도구로 직접 확인"}
    else:
        try:
            info = extractor(path, out_dir)
        except Exception as exc:  # 손상 파일 등: 색인에 오류로 남기고 계속 진행
            info = {"method": "error", "error": f"{type(exc).__name__}: {exc}"}
    entry = {
        "sha256": _sha256(path),
        "size": path.stat().st_size,
        "kind": path.suffix.lower().lstrip("."),
        "extract_method": info.get("method"),
        "pages": info.get("pages"),
        "image_pages": info.get("image_pages", []),
        "extracted_at": _now(),
    }
    if "text" in info:
        txt_path = out_dir / "text.txt"
        txt_path.write_text(info["text"], encoding="utf-8")
        entry["extracted_text"] = _rel(txt_path)
        entry["chars"] = len(info["text"])
    if info.get("error"):
        entry["error"] = info["error"]
    return entry


# ---------------------------------------------------------------- 명령

def cmd_status(index: dict, as_json: bool) -> None:
    d = diff(index)
    if as_json:
        print(json.dumps(d, ensure_ascii=False))
        return
    total = len(scan_files())
    print(f"references/ 파일 {total}개 · 색인 {len(index.get('files', {}))}개")
    labels = {"new": "새 파일", "changed": "변경됨(재분석 필요)", "removed": "삭제됨",
              "pending_extract": "추출 대기", "pending_analysis": "추출 완료·분석 대기"}
    clean = True
    for key, label in labels.items():
        for rel in d[key]:
            print(f"  [{label}] {rel}")
            clean = False
    if clean:
        print("  변화 없음 - 모든 자료가 분석 완료 상태")


def cmd_extract(index: dict, all_files: bool) -> None:
    d = diff(index)
    targets = [_rel(p) for p in scan_files()] if all_files else d["new"] + d["changed"] + d["pending_extract"]
    files = index.setdefault("files", {})
    for rel in d["removed"]:
        files[rel]["status"] = "removed"
    for rel in targets:
        prev = files.get(rel, {})
        entry = extract_one(rel)
        # 내용이 같으면 기존 분석 결과를 유지, 바뀌었으면 분석을 다시 하도록 상태를 되돌린다.
        same = prev.get("sha256") == entry["sha256"] and prev.get("status") == "analyzed"
        for key in ("categories", "summary", "used_in", "analyzed_at"):
            if same and key in prev:
                entry[key] = prev[key]
        entry["status"] = "analyzed" if same else ("error" if entry.get("error") and "extracted_text" not in entry else "extracted")
        if prev.get("sha256") and prev["sha256"] != entry["sha256"]:
            entry["previous_sha256"] = prev["sha256"]
        files[rel] = entry
        imgs = f", 이미지 페이지 {len(entry['image_pages'])}개" if entry.get("image_pages") else ""
        err = f" ⚠ {entry['error']}" if entry.get("error") else ""
        print(f"[{entry['status']}] {rel} -> {entry.get('extracted_text', '-')} ({entry['extract_method']}{imgs}){err}")
    if not targets:
        print("추출할 새 자료 없음")
    save_index(index)
    render_md(index)


def cmd_mark(index: dict, rel: str, categories: str, summary: str, used_in: str | None) -> None:
    rel = Path(rel).as_posix()
    files = index.get("files", {})
    if rel not in files:
        sys.exit(f"색인에 없는 경로: {rel} (먼저 extract)")
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    unknown = [c for c in cats if c not in CATEGORIES]
    if unknown:
        sys.exit(f"알 수 없는 범주 {unknown} - 사용 가능: {', '.join(CATEGORIES)}")
    entry = files[rel]
    entry.update({"status": "analyzed", "categories": cats, "summary": summary, "analyzed_at": _now()})
    if used_in:
        entry["used_in"] = sorted(set(entry.get("used_in", [])) | {u.strip() for u in used_in.split(",")})
    save_index(index)
    render_md(index)
    print(f"[analyzed] {rel}")


def render_md(index: dict) -> None:
    lines = [
        "# 참고자료 색인 (자동 생성)",
        "",
        "> 이 파일은 `.agent_state/reference_index.json`에서 `python .claude/scripts/ref_scan.py render`로 생성된다. 직접 수정하지 않는다.",
        "> 원본은 `references/`(읽기 전용), 추출 텍스트는 `.agent_state/extracted/`(git 제외, `extract --all`로 재생성).",
        "> 자료 우선순위: 공식 공고·운영규정 > 공식 사업설명회 > 공식 교육자료 > 사용자 확정 방향 > 실제 코드·데이터 > 기존 기획 문서 > 기타.",
        "",
        "| 파일 | 상태 | 형식·분량 | 범주 | 요약 | 반영 문서 |",
        "|---|---|---|---|---|---|",
    ]
    for rel, e in sorted(index.get("files", {}).items()):
        size = f"{e.get('kind', '')}"
        if e.get("pages"):
            size += f" · {e['pages']}쪽"
        if e.get("image_pages"):
            size += f" (이미지 {len(e['image_pages'])}쪽)"
        lines.append("| `{}` | {} | {} | {} | {} | {} |".format(
            rel, e.get("status", "-"), size, ", ".join(e.get("categories", [])) or "-",
            (e.get("summary") or e.get("error") or "-").replace("|", "/"),
            ", ".join(f"`{u}`" for u in e.get("used_in", [])) or "-"))
    INDEX_MD.parent.mkdir(parents=True, exist_ok=True)
    INDEX_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("extract")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("mark")
    p.add_argument("path")
    p.add_argument("--category", required=True, help=", ".join(CATEGORIES))
    p.add_argument("--summary", required=True)
    p.add_argument("--used-in")
    sub.add_parser("render")
    args = parser.parse_args()

    index = load_index()
    if args.cmd == "status":
        cmd_status(index, args.json)
    elif args.cmd == "extract":
        cmd_extract(index, args.all)
    elif args.cmd == "mark":
        cmd_mark(index, args.path, args.category, args.summary, args.used_in)
    elif args.cmd == "render":
        render_md(index)


if __name__ == "__main__":
    main()

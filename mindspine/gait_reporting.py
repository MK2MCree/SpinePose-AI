"""Keep versioned downloadable reports consistent with local multi-track results.

The upstream renderer only understands one subject. Its provenance signature
is retained, but our adapter decorates selection and multi-subject views before
publishing the JSON, printable HTML and ZIP together.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import zipfile


def prepare_report_view(job: dict, cache: dict) -> dict:
    from sconet_video.reporting import prepare_report_view as upstream_prepare, SAFE_NAME
    from sconet_video.interpretation import render_report_html as upstream_renderer
    from mindspine.gait_pipeline import decorate_analysis, render_report_html
    report = upstream_prepare(job, cache)
    if not report.get("multi_subject") and not report.get("requires_target_selection"):
        return report
    report = decorate_analysis(report)
    output = Path(job["_output"])
    # No runtime paths, secrets or unchecked HTML are inserted by the renderer.
    html = render_report_html(report, upstream_renderer)
    json_tmp = output / "report_v2.json.tmp"
    html_tmp = output / "report.html.tmp"
    json_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    html_tmp.write_text(html, encoding="utf-8")
    json_tmp.replace(output / "report_v2.json")
    html_tmp.replace(output / "report.html")
    bundle_tmp = output / "report_v2.zip.tmp"
    with zipfile.ZipFile(bundle_tmp, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(set(report.get("artifacts", {}).values()) - {"report_v2.zip"}):
            if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
                continue
            path = output / name
            if path.is_file() and not path.is_symlink() and path.resolve().parent == output.resolve():
                bundle.write(path, name)
    bundle_tmp.replace(output / "report_v2.zip")
    cache[job["id"]] = (report["original_report_sha256"], deepcopy(report))
    return report

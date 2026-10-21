#!/usr/bin/env python3
"""Analyze revert commits in llvm-project and export overall stats & 1D scatter plot.

Usage:
  python3 analysis/analyze_reverts.py [--since "6 months ago"] [--out-dir analysis]
"""

import argparse
import csv
import hashlib
import html
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
from typing import Any


def git_output(repo: Path, args: list[str]) -> str:
  return subprocess.check_output(
      ["git", "-C", str(repo)] + args,
      text=True,
      stderr=subprocess.DEVNULL,
  )


def strip_tags_and_pr(title: str) -> str:
  """Strip leading [tag] prefixes and trailing (#PR) suffixes for fuzzy title matching."""
  s = re.sub(r"\s*\(#\d+\)\s*$", "", title.strip())
  s = re.sub(r"^(?:\[[^\]]+\]\s*:?\s*)+", "", s)
  return s.strip().strip("\"'").strip().lower()


def is_doc_file(path: str) -> bool:
  if "/docs/" in path or path.startswith("docs/"):
    return True
  if path.endswith(".md") or path.endswith(".rst"):
    return True
  return False


def format_duration(seconds: float) -> str:
  days = seconds / 86400.0
  hours = seconds / 3600.0
  mins = seconds / 60.0
  if days >= 2:
    return f"{days:.2f} days ({hours:.1f} hrs)"
  if hours >= 1:
    return f"{hours:.2f} hrs ({mins:.1f} mins)"
  return f"{mins:.1f} mins ({seconds:.0f} secs)"


def compute_stats(deltas: list[int]) -> dict[str, Any]:
  if not deltas:
    return {
        "count": 0,
        "median_seconds": 0,
        "p90_seconds": 0,
        "average_seconds": 0,
        "max_seconds": 0,
        "median_human": "N/A",
        "p90_human": "N/A",
        "average_human": "N/A",
        "max_human": "N/A",
    }
  sorted_d = sorted(deltas)
  pos90 = (len(sorted_d) - 1) * 0.9
  lo90, hi90 = int(math.floor(pos90)), int(math.ceil(pos90))
  p90 = sorted_d[lo90] * (1.0 - (pos90 - lo90)) + sorted_d[hi90] * (pos90 - lo90)
  med = statistics.median(sorted_d)
  avg = statistics.mean(sorted_d)
  mx = sorted_d[-1]
  return {
      "count": len(deltas),
      "median_seconds": round(med, 2),
      "p90_seconds": round(p90, 2),
      "average_seconds": round(avg, 2),
      "max_seconds": mx,
      "median_human": format_duration(med),
      "p90_human": format_duration(p90),
      "average_human": format_duration(avg),
      "max_human": format_duration(mx),
  }


def deterministic_jitter(seed_str: str, half_span: float) -> float:
  h = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)
  norm = (h / 0xFFFFFFFF) * 2.0 - 1.0
  return norm * half_span


def generate_svg_scatter(
    out_path: Path,
    code_reverts: list[dict[str, Any]],
    overall_stats: dict[str, Any],
) -> None:
  """Generate a standalone 1D scatter-plot SVG for all code-related reverts."""
  width, height = 1104, 270
  pad_l, pad_r = 190, 40
  plot_w = width - pad_l - pad_r

  # Major labeled ticks: start at 0, then 4x progression from 15m up to ~683d, ending around 2000d
  labeled_ticks = [
      (0, "0"),
      (15 * 60, "15m"),
      (60 * 60, "1h"),
      (4 * 3600, "4h"),
      (16 * 3600, "16h"),
      (64 * 3600, "2.7d"),
      (256 * 3600, "11d"),
      (1024 * 3600, "43d"),
      (4096 * 3600, "171d"),
      (16384 * 3600, "683d"),
      (2000 * 86400, "2000d"),
  ]

  t_lin = 15.0 * 60  # Linear threshold (0 to 15m)
  t_max = 2000.0 * 86400  # End of scale (~2000d)
  # Number of 4x steps from 15m to 683d is 8 steps
  # The final interval from 683d (16384h) to 2000d (48000h) is a fractional step:
  num_log_steps = 8
  last_step_fraction = (math.log10(t_max) - math.log10(16384 * 3600)) / math.log10(4)
  total_steps = 1.0 + num_log_steps + last_step_fraction
  step_w = plot_w / total_steps

  def x_pos(sec: float) -> float:
    if sec <= 0:
      return pad_l
    if sec <= t_lin:
      return pad_l + (sec / t_lin) * step_w
    s = min(sec, t_max)
    log_ratio = (math.log10(s) - math.log10(t_lin)) / math.log10(4)
    return pad_l + (1.0 + log_ratio) * step_w

  # Unlabeled evenly-divided ticks (4 ticks between each labeled interval):
  # 1) Between 0 and 15m: 4 evenly-divided ticks (3m, 6m, 9m, 12m)
  unlabeled_ticks = [(k * 15 * 60) / 5.0 for k in range(1, 5)]
  # 2) Between each 4x labeled interval (from 15m to 683d): 4 evenly-divided ticks
  # (Skipped for the final interval from 683d to 2000d)
  for sec, _ in labeled_ticks[1:-2]:
    for k in range(1, 5):
      unlabeled_ticks.append(sec * (1.0 + 0.6 * k))

  y_band = 140
  band_h = 100
  y_top = y_band - band_h // 2
  y_bottom = y_band + band_h // 2

  svg = [
      f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="system-ui, -apple-system, sans-serif">',
      f'<rect width="{width}" height="{height}" rx="12" fill="#ffffff" stroke="#e2e8f0" stroke-width="1"/>',
      f'<text x="24" y="36" fill="#0f172a" font-size="16" font-weight="600">Time-to-Revert 1D Scatter Plot — All Code-Related Reverts (Past 6 Months)</text>',
      f'<text x="24" y="56" fill="#475569" font-size="12">N = {overall_stats["count"]} code-related reverts • Median: {overall_stats["median_human"]} • P90: {overall_stats["p90_human"]} • Average: {overall_stats["average_human"]}</text>',
      # Band background
      f'<rect x="{pad_l}" y="{y_top}" width="{plot_w}" height="{band_h}" rx="8" fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>',
      # Band labels
      f'<text x="24" y="{y_band + 5}" fill="#0284c7" font-size="13" font-weight="600">All Code Reverts</text>',
  ]

  # 1. Unlabeled intermediate gridlines (faint vertical lines across band)
  for sec in unlabeled_ticks:
    xp = x_pos(sec)
    svg.append(
        f'<line x1="{xp:.1f}" y1="{y_top}" x2="{xp:.1f}" y2="{y_bottom}" stroke="#f1f5f9" stroke-width="1"/>'
    )

  # 2. Major gridlines (dashed vertical lines across band for labeled ticks)
  for sec, _ in labeled_ticks:
    xp = x_pos(sec)
    svg.append(
        f'<line x1="{xp:.1f}" y1="{y_top}" x2="{xp:.1f}" y2="{y_bottom}" stroke="#8496ad" stroke-dasharray="3,3" stroke-width="1"/>'
    )

  # 3. Plot points for All Code Reverts
  for r in code_reverts:
    xp = x_pos(r["delta_seconds"])
    yp = y_band + deterministic_jitter(r["revert_sha"] + "_all", band_h * 0.35)
    title_attr = html.escape(
        f'{r["revert_sha"][:8]}: {format_duration(r["delta_seconds"])} — {r["revert_subject"]}'
    )
    svg.append(
        f'<circle cx="{xp:.1f}" cy="{yp:.1f}" r="3.6" fill="#0ea5e9" fill-opacity="0.55" stroke="#0369a1" stroke-width="0.6"><title>{title_attr}</title></circle>'
    )

  # 4. Summary stats lines: Median (purple dashed), P90 (red dashed), and Mean (green dotted)
  b1_med_x = x_pos(overall_stats["median_seconds"])
  b1_p90_x = x_pos(overall_stats["p90_seconds"])
  b1_avg_x = x_pos(overall_stats["average_seconds"])

  med_txt = (
      f"Med {overall_stats['median_seconds'] / 3600:.1f}h"
      if overall_stats["median_seconds"] < 86400
      else f"Med {overall_stats['median_seconds'] / 86400:.1f}d"
  )
  p90_txt = (
      f"P90 {overall_stats['p90_seconds'] / 86400:.1f}d"
      if overall_stats["p90_seconds"] >= 86400
      else f"P90 {overall_stats['p90_seconds'] / 3600:.1f}h"
  )
  avg_txt = (
      f"Avg {overall_stats['average_seconds'] / 86400:.1f}d"
      if overall_stats["average_seconds"] >= 86400
      else f"Avg {overall_stats['average_seconds'] / 3600:.1f}h"
  )

  svg.append(
      f'<line x1="{b1_med_x:.1f}" y1="{y_top}" x2="{b1_med_x:.1f}" y2="{y_bottom}" stroke="#7e22ce" stroke-width="2" stroke-dasharray="5,3"/>'
  )
  svg.append(
      f'<text x="{b1_med_x:.1f}" y="{y_top - 6}" fill="#7e22ce" font-size="12" font-weight="600" text-anchor="middle">{med_txt}</text>'
  )
  svg.append(
      f'<line x1="{b1_p90_x:.1f}" y1="{y_top}" x2="{b1_p90_x:.1f}" y2="{y_bottom}" stroke="#dc2626" stroke-width="2" stroke-dasharray="5,3"/>'
  )
  svg.append(
      f'<text x="{b1_p90_x - 5:.1f}" y="{y_top - 6}" fill="#dc2626" font-size="12" font-weight="600" text-anchor="end">{p90_txt}</text>'
  )
  svg.append(
      f'<line x1="{b1_avg_x:.1f}" y1="{y_top}" x2="{b1_avg_x:.1f}" y2="{y_bottom}" stroke="#059669" stroke-width="2" stroke-dasharray="2,2"/>'
  )
  svg.append(
      f'<text x="{b1_avg_x + 5:.1f}" y="{y_top - 6}" fill="#059669" font-size="12">{avg_txt}</text>'
  )

  # 5. Unlabeled tick marks (top and bottom border of band)
  for sec in unlabeled_ticks:
    xp = x_pos(sec)
    svg.append(
        f'<line x1="{xp:.1f}" y1="{y_bottom}" x2="{xp:.1f}" y2="{y_bottom + 4}" stroke="#94a3b8" stroke-width="1"/>'
    )
    svg.append(
        f'<line x1="{xp:.1f}" y1="{y_top - 4}" x2="{xp:.1f}" y2="{y_top}" stroke="#94a3b8" stroke-width="1"/>'
    )

  # 6. Major labeled tick marks & labels
  for sec, label in labeled_ticks:
    xp = x_pos(sec)
    svg.append(
        f'<line x1="{xp:.1f}" y1="{y_bottom}" x2="{xp:.1f}" y2="{y_bottom + 6}" stroke="#475569" stroke-width="1.2"/>'
    )
    svg.append(
        f'<line x1="{xp:.1f}" y1="{y_top - 6}" x2="{xp:.1f}" y2="{y_top}" stroke="#475569" stroke-width="1.2"/>'
    )
    svg.append(
        f'<text x="{xp:.1f}" y="{y_bottom + 19}" fill="#475569" font-size="12" text-anchor="middle">{label}</text>'
    )

  # Axis Title
  svg.append(
      f'<text x="{pad_l + plot_w / 2:.1f}" y="{height - 14}" fill="#475569" font-size="12" text-anchor="middle">Time from Original Commit to Revert (Log Scale)</text>'
  )

  svg.append("</svg>")
  out_path.write_text("\n".join(svg) + "\n")


def collect_revert_records(
    repo: Path, since: str = "6 months ago", lookback: str = "3 years ago"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
  """Query git log and return (all_grep_commits, strict_reverts)."""
  all_log = git_output(
      repo,
      ["log", f"--since={lookback}", "--pretty=format:%H|%ct|%at|%s"],
  ).splitlines()

  sha_map: dict[str, tuple[str, int, int, str]] = {}
  pr_map: dict[str, tuple[str, int, int, str]] = {}
  title_map: dict[str, list[tuple[str, int, int, str]]] = {}
  pr_end_re = re.compile(r"^(.*?)\s*\(#(\d+)\)\s*$")

  for line in all_log:
    if not line.strip():
      continue
    sha, ct_s, at_s, subj = line.split("|", 3)
    info = (sha, int(ct_s), int(at_s), subj)
    sha_map[sha] = info
    m = pr_end_re.match(subj)
    if m:
      base_title, pr_num = m.group(1).strip(), m.group(2)
      pr_map[pr_num] = info
      title_map.setdefault(base_title.lower(), []).append(info)
    title_map.setdefault(subj.strip().lower(), []).append(info)
    norm_title = strip_tags_and_pr(subj)
    if norm_title:
      title_map.setdefault(norm_title, []).append(info)

  def find_by_sha_prefix(prefix: str) -> tuple[str, int, int, str] | None:
    if prefix in sha_map:
      return sha_map[prefix]
    for k, v in sha_map.items():
      if k.startswith(prefix):
        return v
    try:
      out = git_output(
          repo, ["log", "-1", "--format=%H|%ct|%at|%s", prefix]
      ).strip()
    except Exception:
      out = ""
    if out:
      s_sha, s_ct, s_at, s_subj = out.split("|", 3)
      return (s_sha, int(s_ct), int(s_at), s_subj)
    return None

  raw_out = git_output(
      repo,
      [
          "log",
          "-m",
          "--first-parent",
          f"--since={since}",
          "--grep=Revert",
          "--pretty=format:COMMIT_START%n%H%n%ct%n%at%n%ci%n%s%n%b%nCOMMIT_END",
          "--name-only",
      ],
  )
  raw_commits = raw_out.split("COMMIT_START\n")[1:]

  sha_re = re.compile(
      r"(?:[Tt]his reverts commit|[Rr]everts? commit|[Tt]his reverts|[Rr]everts|[Cc]ommit|accidentally committed in|- )\s*([0-9a-f]{7,40})"
  )
  explicit_pr_re = re.compile(
      r"(?:[Rr]everts?|[Rr]evert of|[Bb]roken by)\s+(?:llvm/llvm-project#|#|PR #|https://github\.com/llvm/llvm-project/pull/)(\d{5,6})"
  )
  any_pr_re = re.compile(
      r"(?:[Rr]everts?|[Bb]roken by|pull/|llvm/llvm-project#|PR #|#)(\d{5,6})"
  )
  phab_re = re.compile(r"\b(D\d{5,6})\b")
  subj_revert_re = re.compile(
      r"^(?:\[[^\]]+\]\s*)*[Rr]everts?\s+[\"\x27]?(.+?)[\"\x27]?\s*(?:\(#\d+\))?\s*$"
  )

  all_grep_commits = []
  strict_reverts = []

  for rc in raw_commits:
    parts = rc.split("COMMIT_END\n")
    lines = parts[0].splitlines()
    sha = lines[0]
    ct = int(lines[1])
    at = int(lines[2])
    ci = lines[3]
    subj = lines[4]
    body = "\n".join(lines[5:])
    files = list(
        dict.fromkeys(f.strip() for f in parts[1].splitlines() if f.strip())
    )

    is_reland = bool(
        re.match(
            r"^(?:\[[^\]]+\]\s*)*(?:[Rr]eland|[Rr]e-land|[Rr]eapply|Partially [Rr]eapply|revert/reapply)\b",
            subj,
        )
    )
    is_revert_subj = bool(
        re.match(
            r"^(?:\[[^\]]+\]\s*)*(?:[Rr]evert|[Rr]everting|This reverts)\b",
            subj,
        )
    )

    orig = None
    for m in sha_re.finditer(body + "\n" + subj):
      cand = find_by_sha_prefix(m.group(1))
      if cand and cand[0] != sha and cand[1] <= ct:
        orig = cand
        break
    if not orig:
      m_exp = explicit_pr_re.search(body)
      if m_exp and m_exp.group(1) in pr_map:
        cand = pr_map[m_exp.group(1)]
        if cand[0] != sha and cand[1] <= ct:
          orig = cand
    if not orig:
      for m in any_pr_re.finditer(subj + "\n" + body):
        p = m.group(1)
        if p in pr_map:
          cand = pr_map[p]
          if cand[0] != sha and cand[1] <= ct:
            orig = cand
            break
    if not orig and is_revert_subj:
      m_title = subj_revert_re.match(subj)
      if m_title:
        inner = m_title.group(1).strip().strip("\"'").strip()
        inner_no_pr = re.sub(r"\s*\(#\d+\)\s*$", "", inner).strip()
        norm_inner = strip_tags_and_pr(inner)
        for key in [inner.lower(), inner_no_pr.lower(), norm_inner]:
          if key and key in title_map:
            cands = [c for c in title_map[key] if c[0] != sha and c[1] <= ct]
            if cands:
              orig = max(cands, key=lambda c: c[1])
              break
    if not orig and is_revert_subj:
      m_phab = phab_re.search(body + "\n" + subj)
      if m_phab:
        d_id = m_phab.group(1)
        phab_lines = git_output(
            repo,
            [
                "log",
                "-n",
                "5",
                f"--grep={d_id}",
                "--format=%H|%ct|%at|%s",
                f"{sha}~1",
            ],
        ).splitlines()
        if phab_lines:
          s_sha, s_ct, s_at, s_subj = phab_lines[0].split("|", 3)
          orig = (s_sha, int(s_ct), int(s_at), s_subj)
    if not orig and is_revert_subj and files:
      hist_lines = git_output(
          repo,
          ["log", "-n", "10", "--format=%H|%ct|%at|%s", f"{sha}~1", "--"]
          + files[:5],
      ).splitlines()
      for hl in hist_lines:
        s_sha, s_ct, s_at, s_subj = hl.split("|", 3)
        if "revert" not in s_subj.lower():
          orig = (s_sha, int(s_ct), int(s_at), s_subj)
          break

    is_code_related = any(not is_doc_file(f) for f in files)
    record = {
        "revert_sha": sha,
        "revert_timestamp": ct,
        "revert_date": ci,
        "revert_subject": subj,
        "is_strict_revert": is_revert_subj and not is_reland,
        "is_reland": is_reland,
        "is_code_related": is_code_related,
        "files": files,
        "orig_sha": orig[0] if orig else None,
        "orig_timestamp": orig[1] if orig else None,
        "orig_subject": orig[3] if orig else None,
        "delta_seconds": (ct - orig[1]) if orig else None,
        "delta_hours": round((ct - orig[1]) / 3600.0, 2) if orig else None,
        "delta_days": round((ct - orig[1]) / 86400.0, 2) if orig else None,
    }
    all_grep_commits.append(record)
    if record["is_strict_revert"] and orig is not None:
      strict_reverts.append(record)

  return all_grep_commits, strict_reverts


def main() -> None:
  default_repo = Path(__file__).resolve().parent.parent
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--repo",
      type=Path,
      default=default_repo,
      help="Path to llvm-project git repository",
  )
  parser.add_argument(
      "--since",
      type=str,
      default="6 months ago",
      help="Git --since window (default: '6 months ago')",
  )
  parser.add_argument(
      "--lookback",
      type=str,
      default="3 years ago",
      help="Lookback window for indexing original commits (default: '3 years ago')",
  )
  parser.add_argument(
      "--out-dir",
      type=Path,
      default=default_repo / "analysis",
      help="Directory to write output files into",
  )
  args = parser.parse_args()
  repo: Path = args.repo.resolve()
  out_dir: Path = args.out_dir.resolve()
  out_dir.mkdir(parents=True, exist_ok=True)

  all_grep_commits, strict_reverts = collect_revert_records(
      repo, since=args.since, lookback=args.lookback
  )

  # Compute overall code-related revert stats
  code_reverts = [r for r in strict_reverts if r["is_code_related"]]
  code_deltas = [r["delta_seconds"] for r in code_reverts]
  overall_code_stats = compute_stats(code_deltas)
  max_code_rec = max(code_reverts, key=lambda r: r["delta_seconds"])

  # Write CSV, JSON, and SVG outputs to out_dir
  with open(out_dir / "revert_commits.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "revert_sha",
        "revert_date",
        "revert_timestamp",
        "revert_subject",
        "is_strict_revert",
        "is_code_related",
        "orig_sha",
        "orig_timestamp",
        "orig_subject",
        "delta_seconds",
        "delta_hours",
        "delta_days",
        "num_files",
    ])
    for r in all_grep_commits:
      writer.writerow([
          r["revert_sha"],
          r["revert_date"],
          r["revert_timestamp"],
          r["revert_subject"],
          r["is_strict_revert"],
          r["is_code_related"],
          r["orig_sha"] or "",
          r["orig_timestamp"] or "",
          r["orig_subject"] or "",
          r["delta_seconds"] if r["delta_seconds"] is not None else "",
          r["delta_hours"] if r["delta_hours"] is not None else "",
          r["delta_days"] if r["delta_days"] is not None else "",
          len(r["files"]),
      ])

  scatter_points = []
  for r in code_reverts:
    scatter_points.append({
        "sha": r["revert_sha"][:10],
        "orig_sha": r["orig_sha"][:10] if r["orig_sha"] else "",
        "date": r["revert_date"][:10],
        "subj": r["revert_subject"],
        "sec": r["delta_seconds"],
        "human": format_duration(r["delta_seconds"]),
        "j1": round(deterministic_jitter(r["revert_sha"] + "_all", 1.0), 4),
    })

  with open(out_dir / "revert_scatter_data.json", "w") as f:
    json.dump(scatter_points, f, indent=2)

  generate_svg_scatter(
      out_dir / "time_to_revert_scatter.svg",
      code_reverts,
      overall_code_stats,
  )

  summary_data = {
      "since": args.since,
      "total_grep_revert_commits": len(all_grep_commits),
      "total_strict_reverts": len(strict_reverts),
      "overall_code_related_reverts": {
          **overall_code_stats,
          "max_commit": {
              "revert_sha": max_code_rec["revert_sha"],
              "revert_subject": max_code_rec["revert_subject"],
              "orig_sha": max_code_rec["orig_sha"],
              "orig_subject": max_code_rec["orig_subject"],
          },
      },
  }
  with open(out_dir / "summary.json", "w") as f:
    json.dump(summary_data, f, indent=2)

  md_lines = [
      f"# LLVM Revert History Analysis (`--since=\"{args.since}\"`)",
      "",
      f"- **Total commits matching `--grep=Revert`**: {len(all_grep_commits)}",
      f"- **Strict revert commits (excluding Reland/Reapply)**: {len(strict_reverts)}",
      f"- **Code-related strict revert commits (excluding doc-only)**: {len(code_reverts)}",
      "",
      "## Time from Original Commit to Revert — All Code-Related Reverts",
      "",
      "| Metric | Value |",
      "| :--- | :--- |",
      f"| **Revert Count** | {overall_code_stats['count']} |",
      f"| **Median** | **{overall_code_stats['median_human']}** |",
      f"| **90th Percentile (P90)** | **{overall_code_stats['p90_human']}** |",
      f"| **Average (Mean)** | **{overall_code_stats['average_human']}** |",
      f"| **Maximum** | **{overall_code_stats['max_human']}** (`{max_code_rec['revert_sha'][:10]}` reverting `{max_code_rec['orig_sha'][:10]}`) |",
  ]

  (out_dir / "summary.md").write_text("\n".join(md_lines) + "\n")
  print(f"Wrote main revert analysis outputs to {out_dir}")


if __name__ == "__main__":
  main()

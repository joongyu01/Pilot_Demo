"""Share bot: turn a "설정 공유" issue into a preset the demo can load.

  python tools/share_settings.py --event $GITHUB_EVENT_PATH --site <pages url> \
      --out shared/presets --comment comment.md

The issue body is only ever read from the event file, never through a shell.
Only settings the web lets a user edit (shared/schema.json, built from the
deployed upstream code) are kept; everything else in a backup is dropped.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

LABELS = {
  "nickname": "닉네임",
  "car": "차종",
  "date": "날짜",
  "settings": "설정 JSON",
  "memo": "메모",
}
NO_RESPONSE = "_No response_"
MAX_SETTINGS_BYTES = 40_000
NICK_MAX = 20
MEMO_MAX = 200
CONTROL = re.compile(r"[\x00-\x1f\x7f<>`]")


def parse_issue_form(body: str) -> dict:
  """GitHub renders an issue form as '### <label>' sections."""
  fields: dict[str, str] = {}
  parts = re.split(r"^###\s+(.+?)\s*$", body or "", flags=re.M)
  for label, content in zip(parts[1::2], parts[2::2]):
    for key, want in LABELS.items():
      if label.strip().startswith(want):
        value = content.strip()
        fields[key] = "" if value == NO_RESPONSE else value
  return fields


def normalize(value):
  if isinstance(value, bool):
    return int(value)
  if isinstance(value, (int, float)):
    return int(value) if float(value).is_integer() else float(value)
  text = str(value).strip()
  if text.lower() in ("true", "false"):
    return int(text.lower() == "true")
  try:
    number = float(text)
  except ValueError:
    return text
  return int(number) if number.is_integer() else number


def clean_settings(raw: dict, schema: dict) -> tuple[dict, list, list]:
  """Returns (kept non-default values as device strings, dropped keys, invalid keys)."""
  settings = schema["settings"]
  kept, dropped, invalid = {}, [], []
  for key, value in raw.items():
    meta = settings.get(key)
    if meta is None:
      dropped.append(key)
      continue
    v = normalize(value)
    lo, hi = meta.get("min"), meta.get("max")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
      if not isinstance(v, (int, float)) or not lo <= v <= hi:
        invalid.append(f"{key}={value}")
        continue
    if v == normalize(meta.get("default")):
      continue
    kept[key] = str(v)
  return dict(sorted(kept.items())), sorted(dropped), invalid


def parse_settings_text(text: str) -> dict:
  text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
  if len(text.encode()) > MAX_SETTINGS_BYTES:
    raise ValueError(f"설정 JSON이 너무 큽니다 ({len(text.encode())} bytes)")
  data = json.loads(text)
  if isinstance(data, dict) and isinstance(data.get("values"), dict):
    data = data["values"]
  if not isinstance(data, dict):
    raise ValueError("설정 JSON은 {\"키\": 값} 형태의 객체여야 합니다")
  return data


def check_date(text: str) -> str:
  text = text.strip()
  m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", text)
  if not m:
    raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다")
  try:
    day = datetime.date(int(m[1]), int(m[2]), int(m[3]))
  except ValueError:
    raise ValueError(f"'{text}'은(는) 없는 날짜입니다") from None
  today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
  if not datetime.date(2019, 1, 1) <= day <= today + datetime.timedelta(days=1):
    raise ValueError("날짜가 범위를 벗어났습니다")
  return day.isoformat()


def check_car(text: str, cars: list) -> str:
  name = text.strip()
  if name in cars:
    return name
  folded = {c.casefold(): c for c in cars}
  if name.casefold() in folded:
    return folded[name.casefold()]
  raise ValueError(f"차종 '{name}'이(가) 데모 차량 목록에 없습니다. 목록의 이름을 그대로 써 주세요")


def check_text(text: str, limit: int, label: str, required: bool) -> str:
  value = re.sub(r"\s+", " ", CONTROL.sub("", text or "")).strip()
  if required and not value:
    raise ValueError(f"{label}을(를) 입력해 주세요")
  if len(value) > limit:
    raise ValueError(f"{label}은(는) {limit}자 이하여야 합니다")
  return value


def load_json(site: str, path: str):
  if site.startswith("http"):
    with urllib.request.urlopen(site.rstrip("/") + "/" + path, timeout=30) as r:
      return json.load(r)
  with open(os.path.join(site, path), encoding="utf-8") as f:
    return json.load(f)


def build_preset(issue: dict, schema: dict, meta: dict) -> tuple[dict, dict]:
  fields = parse_issue_form(issue.get("body") or "")
  errors: list[str] = []
  report: dict = {}

  def attempt(fn, *args):
    try:
      return fn(*args)
    except ValueError as e:
      errors.append(str(e))
      return None

  nickname = attempt(check_text, fields.get("nickname", ""), NICK_MAX, "닉네임", True)
  car = attempt(check_car, fields.get("car", ""), schema["cars"])
  date = attempt(check_date, fields.get("date", ""))
  memo = attempt(check_text, fields.get("memo", ""), MEMO_MAX, "메모", False) or ""
  raw = None
  try:
    raw = parse_settings_text(fields.get("settings", ""))
  except (ValueError, json.JSONDecodeError) as e:
    errors.append(f"설정 JSON을 읽지 못했습니다: {e}")

  values, dropped, invalid = ({}, [], []) if raw is None else clean_settings(raw, schema)
  report.update(dropped=dropped, invalid=invalid, received=0 if raw is None else len(raw))
  if raw is not None and not values:
    errors.append("기본값과 다른 설정이 하나도 없습니다")
  report["errors"] = errors
  if errors:
    return {}, report

  now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
  preset = {
    "id": issue["number"],
    "nickname": nickname,
    "car": car,
    "date": date,
    "memo": memo,
    "author": (issue.get("user") or {}).get("login", ""),
    "issue": issue.get("html_url", ""),
    "createdAt": issue.get("created_at", now),
    "updatedAt": now,
    "upstream": meta.get("commit", ""),
    "values": values,
  }
  return preset, report


def render_comment(preset: dict, report: dict, site: str) -> str:
  if report["errors"]:
    lines = ["❌ **공유하지 못했습니다.** 이슈 본문을 고치면(Edit) 봇이 다시 확인합니다.", ""]
    lines += [f"- {e}" for e in report["errors"]]
  else:
    lines = [
      f"✅ **공개됐습니다.** 1~3분 뒤 [데모]({site})의 `DEMO` → **설정 불러오기(web)** 목록에 나타납니다.",
      "",
      "| 항목 | 값 |", "|---|---|",
      f"| 닉네임 | {preset['nickname']} |",
      f"| 차종 | {preset['car']} |",
      f"| 날짜 | {preset['date']} |",
      f"| 공유된 설정 | 기본값과 다른 {len(preset['values'])}개 (받은 키 {report['received']}개) |",
      "",
      "본문을 고치면 같은 항목이 갱신됩니다. 내리고 싶으면 이 이슈에 댓글로 알려 주세요.",
    ]
  if report.get("dropped"):
    lines += ["", f"<details><summary>웹 설정이 아니라서 뺀 키 {len(report['dropped'])}개</summary>", "",
              ", ".join(f"`{k}`" for k in report["dropped"]), "</details>"]
  if report.get("invalid"):
    lines += ["", f"<details><summary>허용 범위를 벗어나 뺀 값 {len(report['invalid'])}개</summary>", "",
              ", ".join(f"`{k}`" for k in report["invalid"]), "</details>"]
  return "\n".join(lines) + "\n"


def main() -> None:
  ap = argparse.ArgumentParser()
  ap.add_argument("--event", required=True)
  ap.add_argument("--site", required=True, help="deployed demo URL (or a local _site path)")
  ap.add_argument("--out", required=True)
  ap.add_argument("--comment", required=True)
  args = ap.parse_args()

  with open(args.event, encoding="utf-8") as f:
    issue = json.load(f)["issue"]
  schema = load_json(args.site, "shared/schema.json")
  meta = load_json(args.site, "_demo/meta.json")

  preset, report = build_preset(issue, schema, meta)
  with open(args.comment, "w", encoding="utf-8") as f:
    f.write(render_comment(preset, report, args.site if args.site.startswith("http") else ""))

  result = "invalid"
  if preset:
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"{preset['id']}.json"), "w", encoding="utf-8", newline="\n") as f:
      json.dump(preset, f, ensure_ascii=False, indent=2)
      f.write("\n")
    result = "published"
  out = os.environ.get("GITHUB_OUTPUT")
  if out:
    with open(out, "a", encoding="utf-8") as f:
      f.write(f"result={result}\n")
  print(result, json.dumps({k: v for k, v in report.items() if k != "dropped"}, ensure_ascii=False))


if __name__ == "__main__":
  main()

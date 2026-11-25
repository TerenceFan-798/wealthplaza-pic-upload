"""批量上传图片素材并发布 —— 中信金控 统一运营管理平台 / 物料管理 / 图片素材管理

用法:
    python publish_assets.py --folder "E:\\物料\\11月\\后管配置图" --prefix "财富节二阶段"
    python publish_assets.py --folder ... --prefix ... --dry-run      # 只列出计划，不动浏览器

流程(每个文件):
    1. 点击工具栏「上传」
    2. 填写 图片名称 = <prefix>-<原文件名>
    3. 注入图片到上传控件(绕过 CDP 文件注入限制)，等待自动识别尺寸
    4. 点击「提交保存」
    5. 在列表首行找到该记录，点击「发布」

已在列表中存在的同名记录会被跳过，可安全重跑。
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time

ENV = dict(os.environ, BSK_AUTO_START="0")
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif"}
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


def bsk(*args, timeout=90):
    p = subprocess.run(["bsk", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV, timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def sync():
    """返回 (vom 文本, ok)。"""
    rc, out = bsk("sync", "--json")
    try:
        d = json.loads(out[out.find("{"):])
        return d.get("vom", ""), True
    except Exception:
        return out, False


def find_ref(vom, pattern):
    m = re.search(pattern, vom)
    return f"@e{m.group(1)}" if m else None


def find_row_ref(vom, name, action):
    """在包含 name 的那一行（代码块）里找 action 按钮的引用。"""
    lines = vom.splitlines()
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("row ") and name in ln and "checkbox" not in ln:
            idx = i
            break
    if idx is None:
        return None
    for ln in lines[idx + 1:]:
        if ln.strip().startswith("row "):
            break
        m = re.search(rf"@e(\d+) button \"{action}\"", ln)
        if m:
            return f"@e{m.group(1)}"
    return None


def wait_for(predicate, label, timeout=25.0, interval=0.6):
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        vom, ok = sync()
        last = vom
        if ok and predicate(vom):
            return vom
        time.sleep(interval)
    raise TimeoutError(f"等待超时: {label}")


def already_uploaded(vom, name):
    m = re.search(re.escape(name) + r"\s+\d+\*\d+", vom)
    return bool(m)


def click(ref, what="元素"):
    rc, out = bsk("click", ref)
    if rc != 0:
        raise RuntimeError(f"点击{what}失败: {out}")


RETRYABLE = ("DOM Error", "Nothing was sent", "ref was not found", "no longer",
             "observation ref", "user has control")


def click_resolved(resolver, what, tries=5):
    """sync → 解析 ref → 点击；页面重绘导致 ref 失效时重新解析重试。"""
    last = ""
    for _ in range(tries):
        vom, ok = sync()
        ref = resolver(vom) if ok else None
        if not ref:
            time.sleep(0.4)
            last = last or "未找到目标"
            continue
        rc, out = bsk("click", ref)
        if rc == 0:
            return vom
        last = out
        if not any(k in out for k in RETRYABLE):
            raise RuntimeError(f"点击{what}失败: {out}")
        time.sleep(0.5)
    raise RuntimeError(f"点击{what}失败: {last}")


def open_dialog():
    """点击「上传」并确认弹窗出现。"""
    for _ in range(3):
        vom, ok = sync()
        if ok and 'dialog "上传图片素材"' in vom:
            return vom
        click_resolved(lambda v: find_ref(v, r'@e(\d+) button "上传"$')
                       or find_ref(v, r'@e(\d+) button "上传"'), "上传")
        for _ in range(10):
            time.sleep(0.5)
            v, ok = sync()
            if ok and 'dialog "上传图片素材"' in v:
                return v
    raise RuntimeError("点击上传后弹窗未出现")


def close_dialog_if_open():
    try:
        vom, ok = sync()
        if ok and 'dialog "上传图片素材"' in vom:
            click_resolved(lambda v: find_ref(v, r'@e(\d+) button "关闭窗口"'), "关闭窗口", tries=2)
    except Exception:
        pass


def upload_one(path, name):
    ext = os.path.splitext(path)[1].lower()
    data = base64.b64encode(open(path, "rb").read()).decode()

    # 1-2. 点击「上传」并等弹窗
    open_dialog()

    # 3. 填写名称（ref 失效时重新定位）
    for _ in range(5):
        vom, ok = sync()
        name_ref = find_ref(vom, r'@e(\d+) textbox "\* 图片名称"') if ok else None
        if not name_ref:
            time.sleep(0.4)
            continue
        rc, out = bsk("fill", name_ref, "--value", name)
        if rc == 0:
            break
        if not any(k in out for k in RETRYABLE):
            raise RuntimeError(f"填写名称失败: {out}")
        time.sleep(0.4)
    else:
        raise RuntimeError("填写名称失败：无法定位输入框")

    # 4. 注入图片文件
    js = (
        "(()=>{"
        "const inp=document.querySelector('.el-upload__input');"
        "if(!inp) throw new Error('no file input');"
        f"const s='{data}';"
        "const b=atob(s);const u=new Uint8Array(b.length);"
        "for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);"
        f"const f=new File([u],{json.dumps(os.path.basename(path), ensure_ascii=False)},"
        f"{{type:'{MIME.get(ext, 'image/png')}'}});"
        "const dt=new DataTransfer();dt.items.add(f);"
        "inp.files=dt.files;"
        "inp.dispatchEvent(new Event('change',{bubbles:true}));"
        "return f.size;})()"
    )
    injected = False
    for _ in range(5):
        vom, ok = sync()
        nr = find_ref(vom, r'@e(\d+) textbox "\* 图片名称"') if ok else None
        if not nr:
            time.sleep(0.4)
            continue
        expect = json.dumps([{"target": nr, "field": "value", "equals": name}],
                            ensure_ascii=False)
        rc, out = bsk("evaluate", js, "--expect", expect)
        if rc == 0 or "evaluate threw" in out:
            injected = True
            break
        time.sleep(0.5)
    if not injected:
        raise RuntimeError(f"注入图片失败: {out}")

    # 5. 等尺寸自动识别
    wait_for(lambda v: re.search(r'textbox "\* 图片尺寸".*\[filled\]', v), "图片尺寸识别")

    # 6. 提交保存
    click_resolved(lambda v: find_ref(v, r'@e(\d+) button "提交保存"'), "提交保存")

    # 7. 等弹窗关闭 + 新行出现
    vom = wait_for(lambda v: 'dialog "上传图片素材"' not in v and already_uploaded(v, name),
                   "列表出现新记录", timeout=30)

    # 8. 发布新行
    click_resolved(lambda v: find_row_ref(v, name, "发布"), "发布")

    # 9. 确认已发布
    wait_for(lambda v: find_row_ref(v, name, "发布") is None, "状态变为已发布", timeout=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start-at", default=None, help="从该文件名开始（可断点续跑）")
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.folder)
                   if os.path.splitext(f)[1].lower() in IMG_EXT)
    if args.start_at:
        i = files.index(args.start_at)
        files = files[i:]
    print(f"待处理 {len(files)} 个文件，前缀「{args.prefix}」")

    if args.dry_run:
        for f in files:
            print(f"  {f}  ->  {args.prefix}-{os.path.splitext(f)[0]}")
        return

    done, failed = [], []
    for i, f in enumerate(files, 1):
        stem = os.path.splitext(f)[0]
        name = f"{args.prefix}-{stem}"
        path = os.path.join(args.folder, f)
        print(f"[{i}/{len(files)}] {name} ...", flush=True)
        try:
            vom, ok = sync()
            if ok and already_uploaded(vom, name):
                print("  已存在，跳过")
                done.append((f, "skipped"))
                continue
            upload_one(path, name)
            print("  完成")
            done.append((f, "ok"))
        except Exception as e:
            print(f"  失败: {e}")
            close_dialog_if_open()
            failed.append((f, str(e)))
            print("  已停止，避免重复写入。修复后可用 --start-at 从该文件续跑。")
            break

    print("\n=== 汇总 ===")
    for f, st in done:
        print(f"  {f}: {st}")
    if failed:
        for f, e in failed:
            print(f"  {f}: FAILED - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

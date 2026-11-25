# wealthplaza-pic-upload

批量上传图片素材到中信金控「统一运营管理平台」的图片素材管理页，并自动发布。

脚本通过 [browser-skill](https://www.npmjs.com/) 的 `bsk` CLI 驱动本机已登录的 Chrome，
不接触任何账号密码，也不需要调用站点接口。

## 每个文件的处理流程

1. 点击工具栏「上传」
2. 填写「图片名称」= `<前缀>-<原文件名>`
3. 把图片写入上传控件，等待站点自动识别尺寸
4. 点击「提交保存」
5. 在列表中找到这条新记录，点击「发布」

## 环境要求

- Windows + Chrome（已安装 browser-skill 扩展，且已在目标站点登录）
- `bsk` CLI 可用
- Python 3.8+

## 用法

```bash
# 先空跑，确认命名清单
python publish_assets.py --folder "E:\物料\11月\后管配置图" --prefix "财富节二阶段" --dry-run

# 正式执行
python publish_assets.py --folder "E:\物料\11月\后管配置图" --prefix "财富节二阶段"

# 中途失败后断点续跑
python publish_assets.py --folder "..." --prefix "..." --start-at "中信信托.png"
```

执行前请先在浏览器里打开图片素材管理页并确认已登录；脚本作用在当前活动标签页上。

## 安全设计

- **幂等**：列表里已存在同名记录时自动跳过，可反复重跑
- **失败即停**：任一步失败立即中止，避免重复提交；提示 `--start-at` 续跑
- **不猜测写入**：每次点击前重新观察页面、重新定位元素，引用失效则重试
- 默认不带凭据，不加 `--yes` 之类跳过确认的开关

## 已适配的页面细节

- 站点用 Element Plus，上传控件是 `input.el-upload__input`（隐藏）。
  常规 CDP 文件注入会被 Chrome 拒绝（`-32000 Not allowed`），
  脚本改为在页面内构造 `File` + `DataTransfer` 并派发 `change`，站点可正常接收并自动填尺寸。
- 弹窗打开时页面结构只暴露弹窗层，列表校验需在弹窗关闭后进行。
- 列表状态流转：`创建` → `发布`；未发布行操作列有 `查看 / 编辑 / 发布`。

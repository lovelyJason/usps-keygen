# USPS 批量注册助手 v2.1.0

这是一个 PySide6 + Playwright 桌面工具，用 CSV 管理多条 USPS 注册任务，并接入
`velydora-mail-otp` 自动完成邮箱验证。

支持的主流程：

- 批量导入个人账号和商业账号资料
- 顺序或并发执行，并发模式支持 1-5 个独立浏览器线程
- 从 TXT 导入代理并按任务顺序绑定，支持直接拖入表格
- 空邮箱自动生成唯一的 `@velydora.com` 地址
- 自动邮箱使用纯随机字母数字，不包含业务名、日期或批次号
- 自动等待数字验证码或 USPS 一次性邮箱验证链接
- CSV 最后一列记录状态，默认跳过失败项，并在重启时自动加载上次 CSV
- 每条任务和每次重试使用全新临时浏览器 profile 与随机浏览器指纹
- 自动填写 USPS 账号、地址、联系人和安全问题
- 逐条记录成功、失败阶段、错误原因和最终页面
- 停止、重试未完成项、结果导出和崩溃后检查点恢复

USPS 的 CAPTCHA、身份核验拒绝、限流和服务故障会显示为真实失败，不会被替换成成功。

## Windows EXE

仓库推送到 `main` 后，GitHub Actions 会自动执行 Ruff、完整测试和 Windows 打包。

构建完成后，在仓库的 Actions 页面打开最新一次 `Build Windows EXE`，下载
`USPSBatchRegistration-v2.1.0-Windows-x64` Artifact。解压后运行：

```text
USPSBatchRegistration-v2.1.0-Windows-x64\USPSBatchRegistration-v2.1.0.exe
```

压缩包已包含 Playwright Chromium，无需另外安装浏览器。也可以在 Actions 页面手动运行
`workflow_dispatch`；推送 `v*` 标签时会同时创建 GitHub Release。

## Windows 10/11 安装

在 `code` 目录打开 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

CMD：

```cmd
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

## 使用

1. 在“API Token”处粘贴 Token，或用“载入 Token 文件”选择仅包含 Token 的文本文件。
2. 点击“测试邮箱连接”。这个检查会访问需鉴权的邮件列表接口，错误 Token 无法通过。
3. 点击“生成 CSV 模板”，填写每一行。`email` 留空会生成纯随机唯一邮箱。
4. 点击“导入 CSV”，确认表格中的邮箱、用户名和账号类型。
5. 点击“导入代理 TXT”或把 TXT 拖入表格，代理按行顺序绑定。
6. 选择顺序模式，或选择并发模式并设置 1-5 个独立浏览器线程。
7. “后台浏览器（无头模式）”默认选中；排查 USPS 页面问题时取消勾选。
8. 勾选任务并点击“开始所选”。单条失败不会中断其他任务。
9. 点击“导出结果”。默认不导出密码、代理凭据和安全问题答案。

运行中的邮箱地址和结果会保存到：

```text
%USERPROFILE%\.usps-registration-mvp\batch-checkpoint.json
```

检查点内容使用本机密钥加密；Windows 下密钥由当前用户的 DPAPI 保护。清空任务会删除检查点和失败截图；不含注册数据的本机密钥会保留供后续批次复用。

成功导入 CSV 后会记住文件路径。下次启动自动加载该 CSV；“跳过失败项”默认选中，`status` 为 `failed` 或 `失败` 的行不会进入表格。任务完成后状态会回写原 CSV。

## CSV 字段

```text
account_type,email,proxy,username,password,first_name,last_name,company,address1,address2,
city,state,zip_code,phone,security_answer1,security_answer2,status
```

- `account_type`：`Business Account` 或 `Personal Account`
- `email`：可留空；手工填写时必须使用界面中配置的邮箱域名
- `proxy`：可留空；支持 `host:port:user:password` 或带协议代理 URL
- `company`：商业账号必填
- `state`：两位州缩写，例如 `FL`
- `password`：8-50 位，必须同时包含大写字母、小写字母和数字
- 两个安全问题答案不能为空且不能相同
- `status`：必须位于最后一列；程序自动写入 `success`、`failed` 或 `stopped`

模板第二行是格式示例，地址和恢复答案必须替换后再运行。

自动或手工重试会切换到同域名下的新邮箱地址，避免上一尝试的延迟邮件被复用。最终提交或 OTP 提交结果不确定时，任务会要求人工核对，不会自动重放注册。

## 测试

```powershell
python -m pip install pytest
python -m pytest -q
```

开发环境使用 `uv` 时：

```powershell
uv sync --group dev
uv run ruff check .
uv run python -m pytest -q
```

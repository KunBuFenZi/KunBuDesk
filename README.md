# RustDesk 自定义客户端编译

仓库：[KunBuFenZi/KunBuDesk](https://github.com/KunBuFenZi/KunBuDesk) · [开始编译](https://github.com/KunBuFenZi/KunBuDesk/actions/workflows/custom-client.yml) · [同步稳定版](https://github.com/KunBuFenZi/KunBuDesk/actions/workflows/sync-upstream.yml)

填写服务器信息和 App 名称，在自己的 GitHub Actions 编译 Windows、Linux、macOS、Android 客户端。直接使用 [RustDesk 官方源码](https://github.com/rustdesk/rustdesk)，不需要 Console 或第三方构建服务。

## 最快使用方法

1. 打开本仓库的 **Actions → Build custom RustDesk → Run workflow**。
2. 填写下面的参数。
3. `platform` 选择平台，`arch` 选择架构；两个都选 `all` 会编译全部支持的平台和架构。
4. 点击 **Run workflow**，等待完成。
5. 在该次运行页面底部 **Artifacts** 下载 `client-*`。解压后就是安装包，同时包含版本信息和校验文件。

| 参数 | 填写说明 |
|---|---|
| `app_name` | 例如 `MyDesk`。1–32 个英文字母、数字、下划线或连字符，首字符为字母；暂不支持空格和中文名称。 |
| `id_server` | ID 服务器，例如 `rd.example.com` 或 `rd.example.com:21116`。不加 `http://`、`https://` 或路径。 |
| `relay_server` | 中继服务器，例如 `rd.example.com:21117`；可留空，使用客户端的中继发现逻辑。 |
| `api_server` | API 地址，例如 `https://rd.example.com`；普通开源 RustDesk Server 没有 API 时留空。 |
| `key` | 服务端 `id_ed25519.pub` 文件中的 Base64 **公钥**，不是私钥。 |
| `platform` | `all`、`windows`、`linux`、`linux-wayland`、`macos`、`android`。 |
| `arch` | `all`、`x86_64`、`arm64`、`x86`、`armv7`。 |

ID Server 和 Key 必须提供。IPv6 用 `[2001:db8::1]:21116` 形式。参数通过环境变量交给脚本，经过校验后写入源码，不作为命令执行。

默认 App 名称为 `KunBuDesk`。想只填一次：编辑 [`config/client.json`](config/client.json) 保存 App 名称和服务器信息。Actions 中留空的字段会使用该文件的值。要清空已保存的中继/API 地址，请同时清空配置文件中的对应字段。

## 支持的平台和安装包

| 平台 | 架构 | 产物 |
|---|---|---|
| Windows | x86_64 / ARM64 | 便携 EXE、MSI |
| Windows | x86（32 位） | Sciter 版便携 EXE |
| Linux | x86_64 / ARM64 | DEB、RPM、AppImage |
| Linux Wayland 实验版 | x86_64 | 官方 unattended-wayland / DRM 版 DEB |
| Linux | ARMv7（armhf） | Sciter 版 DEB |
| macOS | x86_64（Intel）/ ARM64（Apple Silicon） | DMG，内含自定义名称的 `.app` |
| Android | ARMv7 / ARM64 / x86_64 | 分架构 APK |
| Android | 通用 | 同时包含上述三种架构的 APK；选择 Android + all 或 all + all 时生成 |

`arm64` 对应官方构建中的 `aarch64`。不支持的组合（例如 macOS + ARMv7）会在构建开始前报错。官方工具链、运行环境和平台补丁均来自锁定稳定版的官方工作流。

选择 `platform=linux` 或 `all`，并选择 `arch=all` 或 `x86_64`，会同时构建普通 Linux 版本和 Wayland 实验版。只想编译实验版时，选择 `platform=linux-wayland`，`arch=x86_64` 或 `all`。

Wayland 实验版复用官方 `build-rustdesk-linux-drm` 流程，用于测试 Wayland 下的无人值守屏幕捕获。下载产物为 **`client-linux-wayland-x86_64`**，内含 `KunBuDesk-unattended-wayland-版本-x86_64.deb`；若更改 App 名称，文件名前缀也相应变化。构建会保留官方对 DRM 特性和 `libdrmtap` 的检查，`build-info.json` 也会标记为实验版。当前官方只提供该实验版的 x86_64 DEB，没有 ARM64/ARMv7 或对应的 RPM/AppImage 实验包；实际效果取决于桌面环境和显卡驱动。

App 名称用于客户端名称、窗口标题、安装包文件名、Windows MSI 产品名、macOS 应用包名和 Android 应用标签。图标、Logo 和内部包标识沿用官方；Linux 的命令、软件包名和服务仍为 `rustdesk`，避免服务管理与安装脚本不一致。这是应用名称定制，并不包含完整的企业品牌替换或修改 Android applicationId。

## 自动同步官方稳定版

**Actions → Sync official stable RustDesk**：每天北京时间 **11:00** 检查一次，也可手动运行。GitHub 定时任务可能延迟。

发现新稳定版后：

- 下载官方新版本源码和对应的 `hbb_common` 子模块。
- 同步官方 Windows/Linux/macOS/Android 构建步骤、版本参数和架构配置。
- 检查所有构建模板以及 App 名称、服务器配置的源码注入位置。
- 检查通过后自动提交更新到默认分支；保留 `config/client.json` 和自定义脚本。
- 上游结构不兼容时让同步任务失败，保留仓库现有可用版本，便于查看日志后修复。

只跟随正式稳定版，不跟随 master、nightly 或预发布版本。当前锁定版本见 [`upstream/lock.json`](upstream/lock.json)。同步完成后下一次手动编译使用新版本；同步本身不会自动启动全部平台编译。

不需要个人 Token。同步使用 GitHub 提供的 `GITHUB_TOKEN`，只写入 `upstream/` 中的版本锁和官方构建模板； `.github/workflows/` 中的调度工作流不需要自动改写。

若仓库设置了禁止直接提交的分支保护规则，需允许同步机器人提交，否则同步提交步骤会失败。公开仓库的 GitHub 定时任务可能在长期无活动后停用，可在 Actions 重新启用。

## 签名和下载

- Windows 产物默认没有商业代码签名证书。
- macOS 应用使用 ad-hoc 签名，没有 Apple Developer ID 公证；首次打开可能需要在系统设置中允许。
- Android 已配置固定发布证书，所有分架构 APK 和通用 APK、后续版本均复用同一份 keystore。证书只生成一次，编译流程不会重新生成；服务器地址、公钥和 App 显示名称的修改不会更换签名证书。

| Android Secret | 内容 |
|---|---|
| `ANDROID_SIGNING_KEY` | 固定发布 keystore 文件的 Base64 内容 |
| `ANDROID_ALIAS` | keystore alias |
| `ANDROID_KEY_STORE_PASSWORD` | keystore 密码 |
| `ANDROID_KEY_PASSWORD` | key 密码 |

本仓库的四项 Android Secrets 已保存，无需每次填写。只要选中 Android，工作流会在耗时编译之前检查 Secrets 和证书；缺少配置或证书指纹与 [`config/android-signing.json`](config/android-signing.json) 不符时直接失败，不会回退到临时调试签名。最终 APK 还会经过 Android SDK 的签名验证，只有固定证书签出的 APK 才会上传。`build-info.json` 记录其 `android_certificate_sha256`。

私钥和密码不进入 Git、源码包或 Actions Artifact；构建期间的 keystore 放在临时目录，用完删除。电脑上的一次性备份保存在仓库目录的 `.signing/` 中，包含 `KunBuDesk-android-release.jks`、`credentials.json` 和备份说明，已被 Git 和源码打包流程排除。请把整个目录另行安全备份；GitHub Secrets 无法读回原值，丢失私钥和密码会影响后续覆盖升级。恢复时沿用备份原值，**不要生成新证书替换**。

覆盖升级还要求 Android applicationId 保持一致和版本号符合系统要求。本仓库沿用官方 applicationId，新证书与官方 RustDesk 的签名不同，因此无法直接覆盖官方客户端；首次切换需先备份配置再卸载原客户端。以后本仓库各版本沿用固定签名。

`Validate builder` 会用这份固定证书签一个临时测试 APK，验证签名和打包链路，测试包用完删除。它不代表完整 RustDesk 已编译成功。

每个安装包 Artifact 包含 `build-info.json` 和 `SHA256SUMS.txt`。Artifact 保留 30 天；需要长期保存请下载备份。产物目前通过 Actions Artifact 下载，不自动创建公开 Release。

## 运行与费用

第一次构建会下载 Rust、Flutter、Android NDK 和 C/C++ 依赖，耗时较长；官方流程使用缓存加速后续构建。全部平台会启动多个任务，可以先选择一个平台、一个架构验证。

本仓库使用标准 GitHub hosted runners，包括原生 ARM runner。私有仓库消耗账号的 Actions 分钟额度；公开仓库的标准 runner 通常免费，以 [GitHub 当前规则](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 为准。本仓库不会购买 runner 或调整账号的付费设置。

## 结构和验证

```text
.github/workflows/custom-client.yml   手动编译入口
.github/workflows/sync-upstream.yml   每日稳定版同步
.github/workflows/validate.yml        仓库验证
config/client.json                   可选的默认客户端配置
upstream/lock.json                   官方版本、源码提交、子模块提交和模板校验值
upstream/*.yml                       同步的官方构建模板，不直接注册为工作流
scripts/                             参数校验、源码定制、模板适配、产物打包、上游同步
tests/                               参数、架构选择与产物校验测试
```

工作流在运行时把官方步骤转为本地 composite action，使用锁定提交的官方源码构建。这样同步源代码和官方构建模板不需要具有工作流写入权限的额外 Token。

本地验证：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/validate.py
```

`Validate builder` 还会检查工作流语法，并在线验证运行时生成的 composite action 能正常执行。模板及源码注入检查不等于各平台已成功完成整机编译，最终以 `Build custom RustDesk` 的运行结果为准。

## 开源许可证

RustDesk 按 AGPL-3.0 授权，本仓库保留官方版权和许可证通知。每次构建同时上传 `client-corresponding-source`，包括修改后的源码、子模块、配置和构建脚本。分发客户端时，请同时提供对应源码并履行许可证要求。
